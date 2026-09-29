// test.s — bare-metal AArch64 PSCI CPU_ON test for Arm FVP
//
// A real PSCI CPU_ON handshake, not a shortcut: cpu0 boots at EL3 (this
// FVP's default reset state — verified via SCR_EL3), installs a minimal
// VBAR_EL3 exception vector table, drops to EL1 via ERET, then issues a
// genuine `smc` with the standard PSCI_CPU_ON (SMC64) function ID. The
// EL3 handler — acting as the tiniest possible firmware, standing in for
// real Arm Trusted Firmware — does exactly what real PSCI firmware does:
// writes the OS-supplied entry point into a mailbox, then powers the
// target core on via the platform's real power controller (PWRC, the
// documented Arm/TF-A FVP register block at physical 0x1c100000; PPONR at
// +0x4 takes the target's raw MPIDR-style affinity value), and ERETs back.
//
// Two things were verified empirically before trusting them here (see
// arm_test_env.md):
//   - cpu1 does NOT boot by default on Base Platform RevC. `-C
//     pctl.startup` defaults to only cluster0.cpu0 — every other core is
//     genuinely powered off, not just WFE-parked, confirmed by cpu1's PC
//     staying frozen even under direct Iris single-stepping.
//   - PPONR (0x1c100004) is real and correctly documented, but only
//     responds to a genuine CPU-issued store — Iris's own debug
//     write_memory() backdoor cannot reach it (fails with a hard error);
//     it took injecting real code and single-stepping cpu0 through an
//     actual `str` to confirm the address, at which point cpu1's PC
//     visibly moved off 0x80000000 for the first time.
//   - cpu1's own reset vector (RVBAR) defaults to 0x0, independent of
//     cpu0's — `-a`/`--image` only sets the primary core's initial PC via
//     the ELF entry point. RVBAR is fixed at launch time via `-C
//     cluster0.cpu1.RVBAR=<secondary_entry's address>` (see Makefile —
//     the address is read back from `nm` after building, not guessed).
//
// Once powered on, cpu1 needs no WFE/SEV polling loop at all: the EL3
// handler writes the mailbox *before* powering the core on, so by the
// time cpu1 starts fetching from RVBAR the entry address is already
// there waiting to be read.

.global _start
_start:
    // Only the real primary (Aff1 == 0 — Base Platform RevC reflects core
    // number in MPIDR_EL1's Aff1 field, not Aff0; verified via
    // pctl.Affinity-shifted=1 and live MPIDR reads) runs the PSCI-
    // initiator code below. On Base Platform RevC this check is almost
    // redundant anyway — cpu1 is powered off at boot and won't fetch this
    // instruction at all until the PSCI call below powers it on.
    // Note: verified this guard does NOT distinguish cores on Foundation
    // Platform — its cpu1 auto-boots to this same address but reads
    // Aff1==0 too (different affinity encoding), so both cores run the
    // primary path there and both print "PRIMARY...". Harmless (no crash,
    // no PWRC on Foundation Platform to actually mis-power anything), but
    // real — Foundation Platform's `run` target is a cpu0-code-path
    // sanity check only, not a demonstration of working PSCI semantics.
    mrs  x0, mpidr_el1
    ubfx x0, x0, #8, #8
    cbnz x0, spin_forever

    // Install the vector table and drop from EL3 (this core's reset
    // state) to EL1 so the PSCI call below crosses a real privilege
    // boundary, the same one real OS software crosses with `smc`.
    adr  x0, vector_table
    msr  vbar_el3, x0

    mov  x0, #0x3c5            // SPSR: EL1h, DAIF all masked
    msr  spsr_el3, x0
    adr  x0, el1_entry
    msr  elr_el3, x0
    eret

el1_entry:
    // Genuine PSCI CPU_ON (SMC64 function ID 0xC4000003): X1 = target
    // core's affinity (cpu1's raw MPIDR_EL1 value, read back via Iris —
    // see arm_test_env.md), X2 = entry point for the released core,
    // X3 = context id (unused here).
    movz x0, #0x0003
    movk x0, #0xc400, lsl #16
    mov  x1, #0x0100
    movk x1, #0x8100, lsl #16
    adr  x2, secondary_task
    mov  x3, #0
    smc  #0

    adr  x1, msg_primary
    mov  w0, #0x04              // SYS_WRITE0
    hlt  #0xf000

primary_loop:
    b    primary_loop

spin_forever:
    b    spin_forever

// --- EL3 vector table (VBAR_EL3) ---
// 16 entries x 0x80 bytes, 2KB-aligned per the architecture. Only the
// "Synchronous exception from a lower EL, AArch64" entry (offset 0x400)
// is real — that's the one `smc` from EL1 lands on. Every other entry is
// an inert spin: nothing in this controlled test ever raises them.
.balign 2048
vector_table:
.rept 8
    b    .
    .balign 0x80
.endr

smc_handler:                    // offset 0x400: Sync, Lower EL, AArch64
    // Minimal firmware: acts as PSCI_CPU_ON's implementation. Does not
    // preserve caller-saved registers per real SMCCC — acceptable here
    // since this test's EL1 side never uses them again after the call.
    mov  x4, x1                 // target affinity, from the caller's X1
    adr  x5, mailbox
    str  x2, [x5]                // caller's X2 (entry point) -> mailbox
    dsb  sy
    mov  x6, #0x0000
    movk x6, #0x1c10, lsl #16    // x6 = 0x1c100000 (PWRC base, verified)
    str  w4, [x6, #4]            // PPONR: power the target core on
    mov  x0, #0                  // PSCI_SUCCESS
    eret

    .balign 0x80
.rept 7
    b    .
    .balign 0x80
.endr

// --- Secondary core's fixed reset vector (cluster0.cpu1.RVBAR) ---
// No WFE/SEV polling needed: by the time cpu1 is powered on, the EL3
// handler above has already written the mailbox.
secondary_entry:
    adr  x1, mailbox
    ldr  x2, [x1]
    br   x2

secondary_task:
    adr  x1, msg_secondary
    mov  w0, #0x04
    hlt  #0xf000

secondary_loop:
    b    secondary_loop

.align 3
mailbox:
    .quad 0

msg_primary:
    .asciz "PRIMARY CORE (cpu0) BOOTED, RELEASED SECONDARY VIA PSCI CPU_ON\n"
msg_secondary:
    .asciz "SECONDARY CORE (cpu1) RELEASED VIA PSCI CPU_ON\n"
