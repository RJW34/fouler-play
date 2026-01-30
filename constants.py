# constants.py — backward-compatible shim
# The actual definitions live in constants_pkg/ (core.py, strategy.py).
# This file re-exports everything so `import constants` keeps working.

from constants_pkg.core import *  # noqa: F401,F403
from constants_pkg.strategy import *  # noqa: F401,F403
