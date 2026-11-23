#!/usr/bin/env python3
"""Iris_connect.py — connect to a running FVP_Base_RevC-2xAEMvA started
with an Iris server (e.g. `make run-base-debug`, port 7100) and expose
cpu0/cpu1 as ready-to-use iris.debug targets, halted and waiting.

Run interactively so cpu0/cpu1 stay live in your session; this also loads
iris_targets (run_to(), show_regs(), run_until_uart(), ...) on the same
connection, and the optional argument is the FVP stdout log for the UART ones:
    ipython3 -i script/Iris_connect.py [build_debug.log]
    >>> run_to("smc_handler"); show_regs()

Or import from your own script:
    from Iris_connect import model, cpu0, cpu1

`synchronous=True` is required for cpu0.step()/cpu1.step() (single-
instruction stepping) to exist at all — see ../README.md.
"""
import os
import sys

# When run as `ipython3 -i script/Iris_connect.py` this file is __main__, so a
# later `import Iris_connect` (e.g. from iris_targets) would load it AGAIN and
# open a second Iris connection. Register this module under its own name first.
sys.modules.setdefault("Iris_connect", sys.modules[__name__])
# ...and make this script's folder importable (iris_targets.py lives here),
# whatever the current directory is.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
    """Advance every core in `cpus` by `count` steps, one call each, one
    core after the other. Blocking; returns once every core has completed
    its steps.

    NOTE: each cpu.step() runs ONLY that core — the others stay frozen
    while it runs (verified 2026-11-23 in ../arm_linux_multicore:
    stepping cpu0 alone from reset to Linux /init left cpu1 at PC 0x0
    with zero instructions retired, and Linux reported "CPU1: failed to
    come online" after its 5 s timeout).
    So one big step_all() runs cpu0 for `count`, THEN cpu1 for `count` —
    fine for a simple handshake, but not for code where the cores must
    make progress together (Linux SMP bring-up). Use step_interleaved()
    for that."""
    for cpu in cpus:
        cpu.step(count=count, unit=unit)


def step_interleaved(count, chunk=1_000_000, unit="instruction", cpus=CPUS):
    """Advance every core in `cpus` by `count` steps, round-robin in
    `chunk`-sized slices, so the cores make progress together (roughly
    lockstep, to `chunk` granularity). Needed for Linux SMP boot; useful
    here for anything where both cores must run at once (shared-memory
    polling, spinlocks) rather than one-after-the-other.

    Powered-off cores need care (verified 2026-11-23 on cpu1 before
    PSCI CPU_ON, both here and in ../arm_linux_multicore): cpu.step() on
    them returns at once without executing anything (PC stays put —
    0x80000000 here, 0x0 there; the instruction counter ticks by 1 per call),
    BUT the next step() on another core is then swallowed — it returns
    with zero progress. Strict alternation would leave cpu0 stuck forever,
    so each step is re-issued once if that core's instruction count did
    not move."""
    done = 0
    while done < count:
        n = min(chunk, count - done)
        for cpu in cpus:
            before = cpu.get_instruction_count()
            cpu.step(count=n, unit=unit)
            if cpu.get_instruction_count() == before:  # swallowed step
                cpu.step(count=n, unit=unit)
        done += n


def dump_mem(cpu, address, count=16, memory_space=None):
    """Read `count` bytes and print them as a clean hex dump — cpu.read_memory()
    on its own returns a bytearray, whose repr (e.g. bytearray(b'\\xa0\\x008\\xd5'))
    is noisy to read directly in ipython."""
    data = cpu.read_memory(address, memory_space=memory_space, size=1, count=count)
    for offset in range(0, len(data), 16):
        row = data[offset : offset + 16]
        print(f"{address + offset:#010x}  {row.hex(' ')}")


if __name__ == "__main__":
    # Interactive session: also load the "get to X" helpers (run_to,
    # show_regs, run_until_uart, uart_tail, ...). Only here, not at module
    # level: iris_targets itself imports this module, and scripts that
    # `import Iris_connect` don't need them.
    from iris_targets import *  # noqa: F401,F403
    if len(sys.argv) > 1:       # optional: the FVP stdout log, for the UART helpers
        set_uart_log(sys.argv[1])  # noqa: F405
    halt_all()
    print(f"cpu0 PC = {hex(cpu0.get_pc())}")
    print(f"cpu1 PC = {hex(cpu1.get_pc())}")
    print("cpu0/cpu1 halted and ready. iris_targets loaded: run_to(), show_regs(), "
          "run_until_uart(), uart_tail()"
          + ("" if len(sys.argv) > 1 else "; call set_uart_log(path) for the UART ones")
          + ". model.release() when done.")
