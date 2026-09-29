#!/bin/bash

if [ -z "$PATH_BACK" ]; then
    export PATH_BACK="$PATH"
fi

# 1. Path to your toolchain (uncomment for the Makefile's `run` target)
#export PATH=$HOME/work/0_simulator/arm_fvp/Foundation_Platform_11.32_19/bin:$PATH

# 2. Base Platform RevC (Iris debug support, configurable Armv9/SVE — see Makefile's run-base/run-base-debug targets)
export PATH=$HOME/work/0_simulator/arm_fvp/Base_RevC_AEMvA_11.32_19/bin:$PATH_BACK

# 3. ipython3 (interactive iris.debug sessions — no root/pip on this host, installed via
# apt-get download + dpkg-deb -x into this user-local prefix; see arm_test_env.md)
export PATH=$HOME/.local/ipython-tools/usr/bin:$PATH
export PYTHONPATH=$HOME/.local/ipython-tools/usr/lib/python3/dist-packages:$PYTHONPATH
