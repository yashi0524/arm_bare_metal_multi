#!/usr/bin/env python3
"""Iris_connect.py — connect to a running FVP_Base_RevC-2xAEMvA started
with an Iris server (e.g. `make run-base-debug`, port 7100) and expose
cpu0/cpu1 as ready-to-use iris.debug targets, halted and waiting.

Run interactively so cpu0/cpu1 stay live in your session:
    ipython3 -i script/Iris_connect.py

Or import from your own script:
    from Iris_connect import model, cpu0, cpu1

`synchronous=True` is required for cpu0.step()/cpu1.step() (single-
instruction stepping) to exist at all — see ../README.md.
"""
import os
import sys

IRIS_PYTHON_PATH = os.path.expanduser(
    "~/work/0_simulator/arm_fvp/Base_RevC_AEMvA_11.32_19/Iris/Python"
)
sys.path.insert(0, IRIS_PYTHON_PATH)

from iris.debug.Model import NewNetworkModel  # noqa: E402

HOST = "localhost"
PORT = 7100
SIM_NAME = "FVP_Base_RevC_2xAEMvA"

model = NewNetworkModel(host=HOST, port=PORT, timeoutInMs=10000, synchronous=True)
cpu0 = model.get_target(f"component.{SIM_NAME}.cluster0.cpu0")
cpu1 = model.get_target(f"component.{SIM_NAME}.cluster0.cpu1")
CPUS = (cpu0, cpu1)


def halt_all(cpus=CPUS):
    """Halt every core in `cpus` with one call. There's no bulk primitive in
    the Iris API itself — set_execution_state() is per-target — this just
    loops for you."""
    for cpu in cpus:
        cpu.set_execution_state(False)


def resume_all(cpus=CPUS):
    """Set every core in `cpus` to the 'running' state with one call.

    WARNING: on this synchronous connection (synchronous=True, needed for
    step()), this alone does NOT make execution actually progress —
    verified: PC stayed frozen for 1.5 real seconds with
    get_execution_state() reporting True the whole time. SyncTarget has no
    free-run RPC at all; step() is the only thing that advances it. Use
    step_all() below for real forward progress. resume_all() only matters
    if you specifically need the execution-state flag itself set (e.g.
    before disconnecting so the core doesn't read as freshly-halted next
    time) — see 'Closing the session safely' in ../doc/how_to_debug.md."""
    for cpu in cpus:
        cpu.set_execution_state(True)


def step_all(count=100000, unit="instruction", cpus=CPUS):
    """Advance every core in `cpus` by `count` steps, one call each — the
    real way to make execution progress on this synchronous connection.
    Blocking; returns once every core has completed its steps."""
    for cpu in cpus:
        cpu.step(count=count, unit=unit)


def dump_mem(cpu, address, count=16, memory_space=None):
    """Read `count` bytes and print them as a clean hex dump — cpu.read_memory()
    on its own returns a bytearray, whose repr (e.g. bytearray(b'\\xa0\\x008\\xd5'))
    is noisy to read directly in ipython."""
    data = cpu.read_memory(address, memory_space=memory_space, size=1, count=count)
    for offset in range(0, len(data), 16):
        row = data[offset : offset + 16]
        print(f"{address + offset:#010x}  {row.hex(' ')}")


if __name__ == "__main__":
    halt_all()
    print(f"cpu0 PC = {hex(cpu0.get_pc())}")
    print(f"cpu1 PC = {hex(cpu1.get_pc())}")
    print("cpu0/cpu1 halted and ready. model.release() when done.")
