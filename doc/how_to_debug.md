# How to debug this project

## Debug options landscape

What's actually available for debugging on this host, and why the raw
`iris.debug` client (below) is the path this project uses:

- **No GDB-remote or CADI on Foundation Platform's CLI** — `-I`, `-S`, `--plugin` all rejected.
- **Iris genuinely works on Base Platform RevC** — `-I` starts a real server; a Python client (`iris.debug`, shipped in the install) gets a genuine JTAG-equivalent session: halt/resume, register/memory read-write, breakpoints, single-step — see the cheat sheet below. It's driven from an interactive prompt (`ipython3`/`python3 -i`), not just scripted — typing `cpu.step(1)` and seeing the result is exactly like a GDB session, just in Python syntax.
- **`ipython3`** isn't preinstalled here and there's no `pip`/root sudo — resolved via `apt-get download` (no root needed) + `dpkg-deb -x` into a user-local prefix, already wired into `../script/0_env_var_setup.sh`.
- **Real GDB-remote-to-bare-metal exists** (`--allow-debug-plugin --plugin GDBRemoteConnection.so`) but isn't bundled with the free AEM FVP download — it's in the separately-licensed "TPIP" add-on.
- **OS-hosted GDB** (`gdbserver` in a booted Linux guest) is real but needs a running OS — not applicable to raw bare-metal.
- **Arm Debugger (VS Code extension)** is installed and can attach to an already-running Iris session (`connect_existing_model`), and genuinely connects with the right `--cdb-entry` config — but stops at licensing: the free Keil MDK Community license covers launching it, not obtaining a debug license for an Armv8-A/Armv9 target (Keil MDK is scoped to Cortex-M). A real Cortex-A/Armv9 session through it needs a paid Arm Development Studio-class UBL product.
- **Net result:** the raw Python `iris.debug` client is the actually-usable free path — `../script/Iris_connect.py` gives you exactly that, ready to go.

## Iris debug API cheat sheet

Arm doesn't publish a consolidated Iris debug API reference — the
`iris.debug` module's own docstrings are the only real documentation.
Everything below was verified hands-on against a running FVP as part of
this project (see `README.md`), not copied from an official cheat sheet
(none exists).

### Connecting

```python
from iris.debug.Model import NewNetworkModel
model = NewNetworkModel(host="localhost", port=7100, timeoutInMs=10000, synchronous=True)
cpu0 = model.get_target("component.<SimName>.cluster0.cpu0")
model.release()   # when done
```
- `synchronous=True` is **required** for `step()` to exist at all (only
  `SyncTarget` has it; the default async target doesn't).
- Target names: `component.<SimName>.<path>` — list them all with
  `model.get_target_info()` (a generator of
  `TargetInfo(instance_name, target_name, ...)`).

### Closing the session safely

A halted core stays halted across `model.release()` — verified: it does
**not** auto-resume just because the debug client disconnected. So:

1. If you want the core(s) to keep running after you disconnect,
   `resume_all()` first (see below — one call, all cores). **Skip this**
   if you're going to restart the simulator anyway (the usual case, and
   the default workflow for this project) — there's nothing to "clean up"
   by resuming a process you're about to kill.
2. `model.release()` — cleanly disconnects from the FVP's Iris server
   (rather than just dropping the socket).
3. Then exit `ipython3`/`python3` (`Ctrl+D`, or `exit`) — it'll ask
   "Do you really want to exit ([y]/n)?"; `y` (or Enter) confirms.

This only closes the debug *client*. The FVP simulator itself
(`FVP_Base_RevC-2xAEMvA`) keeps running wherever `make run-base-debug`
was launched — `Ctrl+C` there, or `pkill -f FVP_Base_RevC` from another
shell, to stop that too.

### Execution control

| Call | Notes |
|---|---|
| `cpu.set_execution_state(False)` / `(True)` | halt / resume flag — **real bool**, not `"stop"`/`"run"`. **On a synchronous connection this does NOT make execution progress** — verified: PC stayed frozen for 1.5 real seconds with the flag reporting `True` the whole time. `SyncTarget` has no free-run RPC at all; only `step()` advances it |
| `cpu.get_execution_state()` | returns bool (just the flag above, not "is it actually moving") |
| `cpu.step(count=1, unit='instruction')` | single/multi-step — sync model only, and the *only* real way to advance a synchronous target. A large `count` works fine as a practical "run" — e.g. `cpu.step(count=100000)` drove an entire PSCI CPU_ON handshake to completion in one call in this project |
| `cpu.get_pc()` | current PC |
| `cpu.get_instruction_count()` | retired-instruction counter (per-target, not cumulative from reset) |
| `resume_all()` / `halt_all()` / `step_all()` (from `../script/Iris_connect.py`) | one call each to set/step every core at once — no bulk primitive exists in the Iris API itself; these just loop `(cpu0, cpu1)` for you. **Use `step_all()` to actually make progress** — `resume_all()` alone only sets the flag, per the caveat above |

Quick summary, since the distinction above is easy to miss:

| Want to... | Use |
|---|---|
| Actually advance the CPU(s) | `step_all(count=100000)` |
| Just set the running/halted flag (no forward progress) | `resume_all()` / `halt_all()` |

### Registers

| Call | Notes |
|---|---|
| `cpu.read_register(name)` | e.g. `"X1"`, `"PC"`, `"MPIDR_EL1"`, `"SCR_EL3"` |
| `cpu.write_register(name, value)` | |
| `cpu.read_all_registers()` | dict of everything |
| `cpu.has_register(name)` | check before reading something arch-conditional |

### Memory

| Call | Notes |
|---|---|
| `cpu.read_memory(addr, size=1, count=1, memory_space=None)` | returns `bytearray`, always `size*count` long |
| `cpu.write_memory(addr, data, size=1, count=None)` | `data` can be an int if `count=1`, else bytes/bytearray |
| `cpu.memory_spaces_by_name` | dict — `'Physical Memory (Secure)'`/`'SP'`, `'...(Non Secure)'`/`'NP'`, `'Current'`, etc. |

`read_memory()` returns a `bytearray`, whose default repr in ipython is
noisy (`bytearray(b'\xa0\x008\xd5')`). For raw hex, either call `.hex()`
directly on the result (`cpu.read_memory(addr, size=1, count=4).hex()` →
`'a00038d5'`), or use `dump_mem(cpu, address, count=16)` from
`../script/Iris_connect.py` for a multi-row `hexdump`-style view with
offsets.

Gotcha we hit directly: some real hardware peripherals (e.g. this FVP's
power controller, see `../src/test.s`) silently **reject**
`write_memory()` — the debug backdoor can't reach them — but a genuine
CPU-issued `str` (single-stepped) works fine. If a write mysteriously
fails on a peripheral address, that's why.

### Breakpoints / watchpoints

| Call | Notes |
|---|---|
| `cpu.add_bpt_prog(addr)` | instruction breakpoint |
| `cpu.add_bpt_mem(addr, on_read=True, on_write=True)` | memory watchpoint |
| `cpu.add_bpt_reg(name, on_read=True, on_write=True)` | register watchpoint — Arm's own docstring says "no Arm models support this as of 2013," untested here |
| `cpu.remove_bpt(bpt.number)` | **note the `.number`** — passing the `Breakpoint` object itself raises `ValueError` |
| `cpu.get_hit_breakpoints()` | generator, valid after a run stops |

### Other

- `cpu.disassemble(address, count=1)` — exists, not exercised by us yet.
- `model.get_target_info()` — enumerate every component (buses, caches,
  GIC, peripherals — not just CPUs).
