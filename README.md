# arm_bare_metal_multi — PSCI CPU_ON test

Bare-metal AArch64 test that brings up a second CPU core for real, via a
genuine PSCI `CPU_ON` handshake (SMC64 function ID `0xC4000003`) — not a
shortcut like a raw `WFE`/`SEV` spin-table. Sibling of
[`../bare_metal`](../bare_metal) (single-core smoke test); same build
recipe, same two FVPs.

See `src/test.s`'s header comment for the full design rationale and the
empirical findings that shaped it (cpu1 boots powered off by default on
Base Platform RevC, its reset vector defaults to `0x0` independent of
cpu0's, and the power controller only responds to a genuine CPU-issued
store, not Iris's debug memory-write backdoor).

## What it does

1. `cpu0` boots at EL3 (this FVP's actual reset state), installs a minimal
   `VBAR_EL3` exception vector table, and drops to EL1 via `ERET`.
2. From EL1, `cpu0` issues a real `smc` carrying the standard PSCI
   `CPU_ON` function ID, `cpu1`'s affinity, and `cpu1`'s intended entry
   point (`secondary_task`).
3. The EL3 handler (a stand-in for real firmware, e.g. Arm Trusted
   Firmware) writes that entry point into a shared mailbox, then powers
   `cpu1` on for real via the platform's power controller (PWRC,
   `0x1c100000`; `PPONR` at `+0x4`), and `ERET`s back to `cpu0`.
4. `cpu1`, once powered on, starts fetching from its fixed reset vector
   (`cluster0.cpu1.RVBAR`, set at launch — see `SECONDARY_RVBAR` in
   `Makefile`), reads the now-already-written mailbox, and branches
   straight to `secondary_task`. No polling loop needed — the mailbox is
   written *before* the core is powered on.
5. Each core prints a distinct semihosting message once it's running.

## Usage

```bash
source script/0_env_var_setup.sh   # puts both FVPs + ipython3 on PATH
make                                 # assembles src/test.s -> test/output/test.elf
```

| Target | FVP | Iris debug server | Purpose |
|---|---|---|---|
| `make run` | Foundation_Platform | no (none available) | `cpu0`'s own EL3/vector-table/SMC-handler code path only — see caveat below |
| `make run-base` | FVP_Base_RevC-2xAEMvA | no | Full PSCI CPU_ON handshake, functional check |
| `make run-base-debug` | FVP_Base_RevC-2xAEMvA | yes, port 7100 | Same as `run-base`, plus `-I --iris-port 7100` so a debug client (e.g. `ipython3` + `iris.debug`) can attach and watch the handshake live |
| `make run-base-bug` | FVP_Base_RevC-2xAEMvA | yes, port 7100 | Deliberately reproduces a real boot failure (`cpu1` never comes up) for debugging practice — see [`doc/boot_fail_practice.md`](doc/boot_fail_practice.md) |
| `make clean` | — | — | Removes `test/output/test.o`/`test.elf` |

## Expected result

**`make run-base`** (and `make run-base-debug`, functionally identical
plus the debug server) — both lines print, in this order:

```
PRIMARY CORE (cpu0) BOOTED, RELEASED SECONDARY VIA PSCI CPU_ON
SECONDARY CORE (cpu1) RELEASED VIA PSCI CPU_ON
```

then both cores loop forever (`primary_loop`/`secondary_loop`) — the
simulator has to be killed (`Ctrl+C`, or a wrapping `timeout`) to exit;
that's expected, not a hang/crash. A trailing `Segmentation fault (core
dumped)` after the signal-15 termination message is the simulator's own
shutdown noise from being killed this way, not a fault in the test image
— the real output above will already have printed before it.

**`make run`** (Foundation Platform) — prints the *same* line twice:

```
PRIMARY CORE (cpu0) BOOTED, RELEASED SECONDARY VIA PSCI CPU_ON
PRIMARY CORE (cpu0) BOOTED, RELEASED SECONDARY VIA PSCI CPU_ON
```

This is expected on this platform, not a bug: Foundation Platform has no
Iris and no power controller, and its `cpu1` auto-boots to the same `-a`
entry point reading the same `MPIDR_EL1` Aff1 value as `cpu0` (verified —
see `src/test.s`'s header), so *both* cores run the primary path
independently. Use `run` only as a quick sanity check that `cpu0`'s own
EL3/SMC code assembles and runs without faulting — not as evidence the
PSCI handshake itself works; that's what `run-base` demonstrates.

## Debugging the handshake live

`make run-base-debug` starts the model with its Iris server listening on
`localhost:7100`. From a separate terminal:

```bash
source script/0_env_var_setup.sh
ipython3 -i script/Iris_connect.py
```

This connects, halts both cores, and leaves `model`/`cpu0`/`cpu1` live in
your `ipython3` session (see `script/Iris_connect.py`). It also loads
`script/iris_targets.py`: `run_to()`, `show_regs()`, `run_until_uart()` and
`uart_tail()`, on the same connection. To use the UART ones, pass the FVP's
stdout log as an argument (`ipython3 -i script/Iris_connect.py build_debug.log`,
with `make run-base-debug > build_debug.log 2>&1`):

```python
>>> run_to("smc_handler"); show_regs()                     # PSCI CPU_ON being serviced
>>> cpu0.step(count=1)                                     # by hand
>>> run_to("secondary_task", cpu=cpu1, interleave=True)    # cpu1 after power-on
>>> run_until_uart("SECONDARY CORE")
``` For the debug
options landscape and the full Iris API, see
[`doc/how_to_debug.md`](doc/how_to_debug.md).

Non-interactive check of `step_interleaved()` against this test (start
from a freshly launched model; exit status 0 = PASS):

```bash
make run-base-debug > build_debug.log 2>&1 &    # FVP stdout (semihosting) -> log
python3 script/1_iris_verify_interleaved.py build_debug.log
```

Useful things to catch live:
- A breakpoint on `smc_handler` (`cpu0.add_bpt_prog(<address>)`) — halts
  exactly when the PSCI call is serviced.
- A memory watchpoint (`cpu0.add_bpt_mem(<mailbox address>, on_write=True)`)
  on the mailbox — halts exactly when the entry point is published, before
  `cpu1` is even powered on.
- `cpu1.get_pc()` before/after — **don't read the value itself as the
  reset vector**: while `cpu1` is powered off (and even right after the
  `PPONR` write, before `cpu1` is stepped) it reads `0x80000000` — the
  ELF entry point — not `cluster0.cpu1.RVBAR`. Verified: once stepped
  (`cpu1.step(count=50)`) it genuinely starts fetching from RVBAR
  (`secondary_entry`, `0x80001000`) and lands on `secondary_loop`. The
  real signal is whether the PC *moves* when stepped, not what it reads
  while parked.
- Semihosting output (`hlt #0xf000`) prints in the FVP's own terminal
  (terminal 1), not in `ipython3` — see "Print debugging" in
  [`doc/how_to_debug.md`](doc/how_to_debug.md).

Get exact addresses via `aarch64-linux-gnu-nm test/output/test.elf`
(e.g. `smc_handler`, `mailbox`, `secondary_task`).
