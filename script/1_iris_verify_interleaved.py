#!/usr/bin/env python3
"""1_iris_verify_interleaved.py — check Iris_connect.step_interleaved()
against this project's PSCI CPU_ON test, under `make run-base-debug`.

    make run-base-debug > build_debug.log 2>&1 &     # terminal 1 (FVP stdout -> log)
    python3 script/1_iris_verify_interleaved.py build_debug.log

Must start from a freshly launched model (cpu0 at _start, cpu1 powered off).
1. Reproduces the "swallowed step": cpu1.step() while cpu1 is powered off,
   then cpu0.step() makes zero progress once.
2. Drives the test with step_interleaved() in 4-instruction slices until
   cpu0 is in primary_loop and cpu1 in secondary_loop, printing each slice
   in which cpu1 moves.
3. Checks both cores keep progressing and both semihosting messages
   reached the FVP's stdout (the log file).
Exit status 0 = PASS, 1 = FAIL. Verified 2026-11-23 (PASS).
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from Iris_connect import model, cpu0, cpu1, halt_all, step_interleaved  # noqa: E402

if len(sys.argv) != 2:
    sys.exit(__doc__)
LOG = sys.argv[1]
ELF = os.path.join(HERE, "..", "test", "output", "test.elf")


def symbols(elf):
    out = subprocess.run(["aarch64-linux-gnu-nm", elf], check=True,
                         capture_output=True, text=True).stdout
    return {name: int(addr, 16) for addr, _, name in
            (line.split() for line in out.splitlines() if len(line.split()) == 3)}


sym = symbols(ELF)
PRIMARY_LOOP, SECONDARY_LOOP = sym["primary_loop"], sym["secondary_loop"]
failures = []


def st(tag):
    print(f"{tag}: cpu0 PC={cpu0.get_pc():#x} ic={cpu0.get_instruction_count()} | "
          f"cpu1 PC={cpu1.get_pc():#x} ic={cpu1.get_instruction_count()}", flush=True)


halt_all()
st("start")
if cpu0.get_instruction_count() != 0:
    failures.append("model is not fresh (cpu0 already ran) — restart make run-base-debug")

# --- 1. swallowed-step quirk, cpu1 powered off ---
pc1 = cpu1.get_pc()
ic0 = cpu0.get_instruction_count()
cpu1.step(count=10)
st("after cpu1.step(10) [cpu1 off]")
if cpu1.get_pc() != pc1:
    failures.append("cpu1 moved while it should be powered off")
cpu0.step(count=3)
a = cpu0.get_instruction_count() - ic0
cpu0.step(count=3)
b = cpu0.get_instruction_count() - ic0 - a
print(f"cpu0.step(3) right after: +{a} insn; next cpu0.step(3): +{b} insn", flush=True)
if (a, b) != (0, 3):
    print("  note: swallowed-step quirk did not reproduce as (0, 3)", flush=True)

# --- 2. run the test with step_interleaved, 4-instruction slices ---
prev1 = cpu1.get_pc()
done = False
for i in range(200):
    p0, p1 = cpu0.get_pc(), cpu1.get_pc()
    i0, i1 = cpu0.get_instruction_count(), cpu1.get_instruction_count()
    step_interleaved(4, chunk=4)
    n0, n1 = cpu0.get_pc(), cpu1.get_pc()
    d0 = cpu0.get_instruction_count() - i0
    d1 = cpu1.get_instruction_count() - i1
    if n1 != prev1 or i < 3:
        print(f"slice {i:3d}: cpu0 {p0:#x}->{n0:#x} (+{d0}) | "
              f"cpu1 {p1:#x}->{n1:#x} (+{d1})", flush=True)
    prev1 = n1
    if n0 == PRIMARY_LOOP and n1 == SECONDARY_LOOP:
        print(f"both cores in their final loops after slice {i}", flush=True)
        done = True
        break
if not done:
    failures.append("cores did not reach primary_loop/secondary_loop in 200 slices")

# --- 3. both cores keep progressing; both messages printed ---
i0, i1 = cpu0.get_instruction_count(), cpu1.get_instruction_count()
step_interleaved(100, chunk=10)
d0, d1 = cpu0.get_instruction_count() - i0, cpu1.get_instruction_count() - i1
print(f"final step_interleaved(100): cpu0 +{d0}, cpu1 +{d1}", flush=True)
if (d0, d1) != (100, 100):
    failures.append(f"expected +100/+100 after the final interleaved step, got +{d0}/+{d1}")
st("end")
time.sleep(0.5)  # let the FVP flush its stdout to the log
out = open(LOG, errors="replace").read()
for m in ("PRIMARY CORE (cpu0) BOOTED", "SECONDARY CORE (cpu1) RELEASED"):
    found = m in out
    print(f"semihosting '{m}...': {'FOUND' if found else 'MISSING'}", flush=True)
    if not found:
        failures.append(f"missing semihosting message: {m}")

model.release()
print("released", flush=True)
if failures:
    print("FAIL:\n  " + "\n  ".join(failures), flush=True)
    sys.exit(1)
print("PASS", flush=True)
