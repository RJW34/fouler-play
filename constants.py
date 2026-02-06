# constants.py — backward-compatible shim
# The actual definitions live in constants_pkg/ (core.py, strategy.py).
# This file re-exports everything so `import constants` keeps working.

# Re-export everything from constants_pkg (core, strategy, abilities, move flags, etc.)
from constants_pkg import *  # noqa: F401,F403
