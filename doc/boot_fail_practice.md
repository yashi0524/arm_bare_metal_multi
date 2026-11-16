# Boot-fail debugging practice

A deliberately broken variant of the PSCI CPU_ON test — `cpu0` boots and
prints normally, `cpu1` never does. Practice finding out why, live over
Iris, using exactly the workflow from `how_to_debug.md`.

## Setup

Terminal 1 — launch it:
```bash
source script/0_env_var_setup.sh
make run-base-bug
```
It'll just sit there listening (`Info: ... terminal_0: Listening for
serial connection on port 5000` etc.) — **that's expected, not a hang**.
With `-I` and no `-R`, the model never runs on its own; nothing happens
until a debug client connects and drives it (see the caveat in
`../Makefile`'s `run-base-debug` target — same applies here).

Terminal 2 — connect and drive it:
```bash
source script/0_env_var_setup.sh
ipython3 -i script/Iris_connect.py
```
Then, in `ipython3`:
```python
step_all(count=50000)
hex(cpu0.get_pc())
hex(cpu1.get_pc())
```

## What you should see

`cpu0` lands on `primary_loop` — it booted, ran its whole EL3→EL1→SMC
sequence, and printed its message, completely normally. `cpu1` lands
somewhere that is **not** `secondary_loop`, `secondary_task`, or
anywhere near `0x80000000` at all — and never printed its message.

That's the exercise: **one core came up clean, the other didn't — find
out why, using only Iris.** Everything you need is in `how_to_debug.md`'s
cheat sheet (registers, memory, breakpoints, single-step) plus what's
already in `script/Iris_connect.py`.

Useful starting moves, roughly in order of how you'd actually approach
this live:
- Where exactly is `cpu1` stuck? `cpu1.get_pc()` — check it against
  `aarch64-linux-gnu-nm test/output/test.elf` output. Is it inside any
  function you recognize, or somewhere with no symbol at all?
- If it's somewhere unlabeled, what's actually mapped there?
  `dump_mem(cpu1, <its PC>, 16)` — what does the code there look like?
- Single-instruction step `cpu1` a few times (`cpu1.step(1)`) and watch
  `get_pc()` between each — is it making forward progress, or looping?
- Compare against `cpu0`'s registers/state at the equivalent point in its
  own boot — what's different about how `cpu1` got here?
- `cpu1.read_register("MPIDR_EL1")` — sanity-check which core you're
  actually looking at.

## If you're stuck and short on time

<details>
<summary>Hint 1 — where to look</summary>

`cpu1`'s landing address isn't inside any function in `src/test.s` at
all — it's a very low physical address, nowhere near `0x80000000`.
That's a strong clue about *when* in the boot sequence this went wrong:
before `cpu1` ever reached any of our code, or after?
</details>

<details>
<summary>Hint 2 — what controls a core's very first fetch</summary>

Compare the `run-base-bug` target in `../Makefile` against the working
`run-base-debug` target right above it. One `-C` parameter is missing.
What does it control, and why would a *secondary* core (not the one
`-a`/`--image` configures) need it?
</details>

<details>
<summary>Full answer</summary>

`run-base-bug` omits `-C cluster0.cpu1.RVBAR=0x80001000`. `cpu1`'s reset
vector (RVBAR) defaults to `0x0`, independent of `cpu0`'s `-a`-supplied
entry point — `-a`/`--image` only ever configures the primary core.
Once PSCI CPU_ON powers `cpu1` on, it starts fetching from address `0x0`
— all-zero memory, which decodes as `udf #0` (undefined instruction).
That raises a synchronous exception; `cpu1`'s `VBAR_EL3` was also never
set (only `cpu0` sets its own), so it defaults to `0` too, and the
exception vectors to `0x0 + 0x200` (the architected "Synchronous
exception, Current EL, SPx" offset) — which is *also* unmapped, so it
double-faults and gets stuck there. `cpu1.get_pc()` reads `0x200`.

The fix (already in the real `run-base-debug`/`run-base` targets):
`-C cluster0.cpu1.RVBAR=0x80001000`, pointing it at `secondary_entry`
instead of the architectural default.
</details>

## Cleanup when done

`pkill -f FVP_Base_RevC` to stop the simulator. No code was changed for
this exercise — `src/test.s` is untouched; the bug is purely a missing
launch-time parameter in `run-base-bug`.
