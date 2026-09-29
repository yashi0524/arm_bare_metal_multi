# Makefile — bare-metal AArch64 PSCI CPU_ON test for Arm FVP
# Assembles src/test.s, loads at 0x80000000, runs under Foundation_Platform
# (cpu0-only sanity check, no debug interface) or Base Platform RevC
# (FVP_Base_RevC-2xAEMvA; run-base-debug additionally starts an Iris server
# so the real PSCI CPU_ON handshake can be caught live with a breakpoint/
# watchpoint — see src/test.s's header and arm_test_env.md's Iris section
# for the debug API).
# `source script/0_env_var_setup.sh` first to put both FVPs (and ipython3) on PATH.
# Sibling of ../bare_metal (single-core smoke test) — same build recipe,
# same two FVPs, this one brings up a second core for real via PSCI instead.

# --- Tools ---
# Host here is arm64 itself, so these are native (not cross-)compiling —
# aarch64-linux-gnu-as/-ld are just explicit about the target either way.
AS := aarch64-linux-gnu-as
LD := aarch64-linux-gnu-ld

# --- Paths ---
SRC_DIR := src
SRC     := $(SRC_DIR)/test.s
OUT_DIR := test/output
OBJ     := $(OUT_DIR)/test.o
TARGET  := $(OUT_DIR)/test.elf

# --- Load address ---
# Common bare-metal RAM base for both FVPs' -a/--image (no OS) — this is
# cpu0's entry point only; `-a`/`--image` sets nothing for other cores.
LOAD_ADDR := 0x80000000

# --- Secondary core's reset vector ---
# cluster0.cpu1.RVBAR defaults to 0x0, independent of cpu0's -a-supplied
# entry point (verified empirically — see src/test.s's header). Read back
# from `nm test/output/test.elf | grep secondary_entry` after building;
# update this if src/test.s changes enough to shift that symbol.
SECONDARY_RVBAR := 0x80001000

# --- Build Rules ---
all: $(TARGET)

# -Ttext-segment (with -n) pins the ELF's single PT_LOAD segment's own
# VirtAddr to exactly LOAD_ADDR — see ../bare_metal/Makefile for why this
# matters (header-padding underflow on Base Platform RevC otherwise).
$(TARGET): $(SRC) | $(OUT_DIR)
	$(AS) $(SRC) -o $(OBJ)
	$(LD) -Ttext=$(LOAD_ADDR) -Ttext-segment=$(LOAD_ADDR) -n $(OBJ) -o $(TARGET)

$(OUT_DIR):
	mkdir -p $(OUT_DIR)

# --- Run under Foundation Platform ---
# Sanity check only for cpu0's own EL3/vector-table/SMC-handler code —
# Foundation Platform has no Iris and no PWRC, and its cpu1 auto-boots to
# the same -a entry point reading the same MPIDR Aff1 as cpu0 (verified —
# see src/test.s's header), so BOTH cores run the primary path and print
# "PRIMARY..." here; that's expected on this platform, not a bug.
# --cores=2 so there's a second core present at all.
run: $(TARGET)
	Foundation_Platform --image=$(TARGET) --cores=2 --uart0-outfile=-

# --- Run under Base Platform RevC (no debug server) ---
# cluster0.cpu1.RVBAR: see SECONDARY_RVBAR above — cpu1 boots powered off
# by default (`-C pctl.startup`, unset here, defaults to cluster0.cpu0
# only) and is powered on for real by cpu0's PSCI CPU_ON call in
# src/test.s, not by any launch-time parameter.
run-base: $(TARGET)
	FVP_Base_RevC-2xAEMvA -a $(TARGET) \
	  -C bp.secure_memory=0 \
	  -C bp.pl011_uart0.out_file=- \
	  -C cluster0.has_arm_v9-0=1 -C cluster0.has_sve=1 \
	  -C cluster1.has_arm_v9-0=1 -C cluster1.has_sve=1 \
	  -C cluster0.cpu1.RVBAR=$(SECONDARY_RVBAR)

# --- Run under Base Platform RevC, with Iris debug server ---
# Same as run-base, plus -I to start the Iris server so a debug client
# (e.g. ipython3 + iris.debug, see arm_test_env.md) can attach and watch
# the PSCI CPU_ON handshake live — e.g. a breakpoint on smc_handler, or a
# memory watchpoint (add_bpt_mem) on the PPONR write or the mailbox store.
# The model starts running immediately (no -R), so halt cpu0 right after
# connecting if you need to catch this from the very first instruction.
run-base-debug: $(TARGET)
	FVP_Base_RevC-2xAEMvA -a $(TARGET) \
	  -C bp.secure_memory=0 \
	  -C bp.pl011_uart0.out_file=- \
	  -C cluster0.has_arm_v9-0=1 -C cluster0.has_sve=1 \
	  -C cluster1.has_arm_v9-0=1 -C cluster1.has_sve=1 \
	  -C cluster0.cpu1.RVBAR=$(SECONDARY_RVBAR) \
	  -I --iris-port 7100

clean:
	rm -f $(OBJ) $(TARGET)

.PHONY: all run run-base run-base-debug clean
