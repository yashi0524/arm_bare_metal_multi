"""iris_targets.py — reusable "get the model to X" functions on top of
Iris_connect's connection, for this project's `make run-base-debug` model
(bare-metal PSCI CPU_ON test, src/test.s). Ported from
../../arm_linux_multicore/script/iris_targets.py; the Linux/UEFI-only
helpers (step_to_init, find_efi_stub) don't apply here.

Interactive (mix with manual stepping freely; nothing here disconnects):

    make run-base-debug > build_debug.log 2>&1 &          # terminal 1
    ipython3 -i script/Iris_connect.py build_debug.log      # terminal 2
    # (Iris_connect.py, run as __main__, does `from iris_targets import *`
    #  and set_uart_log(<its argument>) for you)
    >>> run_to("smc_handler"); show_regs()                # cpu0 services PSCI CPU_ON
    #   (from reset, at EL3; from EL1 use space="Secure Monitor")
    >>> cpu0.step(count=1); show_regs()                   # by hand
    >>> run_to("secondary_task", cpu=cpu1, interleave=True)   # cpu1 after power-on
    >>> run_until_uart("SECONDARY CORE")                  # both cores, until the message
    >>> model.release()                                   # when done

Targets are symbols from test/output/test.elf (nm) or addresses. Every
function continues from wherever the model is now; none restarts it.

Stepping caveats (verified 2026-11-23, see doc/how_to_debug.md):
- each cpu.step() runs only that core. cpu1 is powered off until cpu0's
  smc_handler writes PPONR, so to reach cpu1 code, step both cores
  (interleave=True / run_until_uart, which use step_interleaved());
- breakpoints are per memory space, and spaces are per exception level:
  `Secure Monitor` = `S` (EL3), `Hyp` (EL2), `Guest` = `N` (EL1/EL0, also
  for this test's secure EL1 code). Default = the core's current space.
  Verified here: run_to("smc_handler") from EL1 (e.g. after el1_entry) is
  NOT REACHED with the default space; use space="Secure Monitor". (From
  reset, at EL3, the default works, and an el1_entry breakpoint set at EL3
  did fire at EL1, so it's not symmetric.)
- a step that gets no answer (Iris client E_timeout, 10 s) raises
  StepTimeout; the core is then free-running, so restart the model.
"""
import os
import subprocess
import time

from Iris_connect import model, cpu0, cpu1, CPUS, halt_all, step_interleaved, dump_mem  # noqa: F401
import iris.error  # on sys.path once Iris_connect is imported

__all__ = ["model", "cpu0", "cpu1", "CPUS", "halt_all", "step_interleaved", "dump_mem",
           "set_uart_log", "uart_tail", "resolve", "run_to", "show_regs",
           "run_until_uart", "StepTimeout"]

HERE = os.path.dirname(os.path.abspath(__file__))
ELF = os.path.join(HERE, "..", "test", "output", "test.elf")

UART_LOG = None   # FVP stdout log (UART + semihosting); set with set_uart_log()


class StepTimeout(RuntimeError):
    """A step() got no answer: the core is probably free-running. Restart the model."""


def set_uart_log(path):
    """Tell the UART-aware functions where the FVP's stdout is being written."""
    global UART_LOG
    UART_LOG = path


def _log_text(log=None):
    path = log or UART_LOG
    if not path:
        raise ValueError("no UART log: call set_uart_log(path) or pass log=...")
    with open(path, errors="replace") as f:
        return f.read()


def uart_tail(n=4, prefixes=None, log=None):
    """Print (and return) the last n non-empty lines of the FVP's stdout
    (semihosting output lands here), optionally only those starting with one
    of `prefixes`."""
    lines = [l for l in _log_text(log).splitlines() if l.strip()]
    if prefixes:
        lines = [l for l in lines if l.startswith(tuple(prefixes))]
    lines = lines[-n:]
    print("UART:", *lines, sep="\n  ", flush=True)
    return lines


def _guarded(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except iris.error.IrisError as e:
        if "timeout" in str(e).lower():
            raise StepTimeout(f"{getattr(fn, '__name__', fn)} got no answer ({e}). The core is "
                              "probably free-running. Restart the model.") from e
        raise


def resolve(target, elf=ELF):
    """Address for `target`: an int, a hex string like "0x80000c00", or a
    symbol name looked up with nm in test/output/test.elf."""
    if isinstance(target, int):
        return target
    if target.lower().startswith("0x"):
        return int(target, 16)
    out = subprocess.run(["aarch64-linux-gnu-nm", elf], check=True,
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        f = line.split()
        if len(f) == 3 and f[2] == target:
            return int(f[0], 16)
    raise ValueError(f"{target} not found in {elf}")


def run_to(target, space=None, step=1_000_000, cpu=None, interleave=False,
           max_instructions=100_000_000):
    """Step until `cpu` (default cpu0) has PC == target, using a breakpoint on
    that cpu in memory space `space` (default: its current space). With
    interleave=True every core is stepped round-robin in `step`-sized slices
    (needed to reach cpu1 code: cpu1 only runs after cpu0 powers it on).
    Continues from the current state. Returns True if reached; the
    breakpoint is removed either way and the cores are left halted."""
    cpu = cpu or cpu0
    addr = resolve(target)
    halt_all()
    if cpu.get_pc() == addr:
        print(f"already at {addr:#x}", flush=True)
        return True
    try:
        bpt = cpu.add_bpt_prog(addr, memory_space=space)
    except ValueError as e:
        raise ValueError(f"{e}; memory spaces: {sorted(cpu.memory_spaces_by_name)}") from e
    hit = False
    try:
        for _ in range(max(1, max_instructions // step)):
            if interleave:
                _guarded(step_interleaved, step, chunk=step)
            else:
                _guarded(cpu.step, count=step)       # a hit breakpoint ends the step early
            if cpu.get_pc() == addr:
                hit = True
                break
    finally:
        try:
            cpu.remove_bpt(bpt.number)
        except Exception:  # noqa: BLE001 — connection may be busy after StepTimeout
            pass
    name = target if isinstance(target, str) else hex(addr)
    print(f"{'HIT' if hit else 'NOT REACHED'} {name} ({addr:#x}) on {cpu.instance_name.split('.')[-1]}"
          f" (space {space or 'current'}{', interleaved' if interleave else ''}): "
          f"cpu0 PC={cpu0.get_pc():#x} cpu1 PC={cpu1.get_pc():#x}", flush=True)
    return hit


DEFAULT_REGS = ("X0", "X1", "X2", "X3", "X4", "X5", "X6", "X30", "SP",
                "ELR_EL3", "SPSR_EL3", "SCR_EL3", "VBAR_EL3", "MPIDR_EL1")


def show_regs(regs=DEFAULT_REGS, cpu=None, code_bytes=16):
    """Print (and return as a dict) registers by name, plus the bytes at PC.
    (read_all_registers() is keyed by numeric IDs, and CPSR doesn't exist on
    this model, hence reading by name.)"""
    cpu = cpu or cpu0
    vals = {"PC": cpu.get_pc()}
    print(f"{cpu.instance_name.split('.')[-1]} PC = {vals['PC']:#x}   "
          f"ic={cpu.get_instruction_count()}", flush=True)
    for r in regs:
        try:
            vals[r] = cpu.read_register(r)
            print(f"  {r:10s}= {vals[r]:#x}", flush=True)
        except ValueError:
            print(f"  {r:10s}: (not available)", flush=True)
    if code_bytes:
        print("  bytes at PC:", flush=True)
        dump_mem(cpu, vals["PC"], code_bytes)
    return vals


def run_until_uart(text, chunk=1000, slice_=10, max_instructions=1_000_000,
                   settle=0.1, log=None):
    """Step both cores interleaved until `text` appears in the FVP's stdout
    (semihosting messages land there). Only output produced AFTER this call
    starts counts. `chunk` instructions per core between log checks; this
    test is only ~130 instructions before both cores sit in their final
    loops, so the default 1000 normally sees both messages in one chunk.
    Returns True if seen."""
    halt_all()
    start = len(_log_text(log))
    total = 0
    while total < max_instructions:
        _guarded(step_interleaved, chunk, chunk=slice_)
        total += chunk
        time.sleep(settle)            # semihosting output reaches the log asynchronously
        if text in _log_text(log)[start:]:
            print(f"seen '{text}' after +{total} insn/core: cpu0 PC={cpu0.get_pc():#x} "
                  f"cpu1 PC={cpu1.get_pc():#x}", flush=True)
            return True
    print(f"'{text}' not seen after {total} insn/core", flush=True)
    return False
