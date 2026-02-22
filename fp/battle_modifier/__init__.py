# fp/battle_modifier/__init__.py
# Backward-compatible re-exports.
# All existing `from fp.battle_modifier import X` imports continue to work.

from fp.battle_modifier._common import *  # noqa: F401,F403
from fp.battle_modifier.switching import *  # noqa: F401,F403
from fp.battle_modifier.damage import *  # noqa: F401,F403
from fp.battle_modifier.status import *  # noqa: F401,F403
from fp.battle_modifier.field import *  # noqa: F401,F403
from fp.battle_modifier.items import *  # noqa: F401,F403
from fp.battle_modifier.dispatcher import *  # noqa: F401,F403
