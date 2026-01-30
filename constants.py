from enum import StrEnum


class BattleType(StrEnum):
    STANDARD_BATTLE = "standard_battle"
    BATTLE_FACTORY = "battle_factory"
    RANDOM_BATTLE = "random_battle"


NO_TEAM_PREVIEW_GENS = {"gen1", "gen2", "gen3", "gen4"}

START_STRING = "|start"
RQID = "rqid"
TEAM_PREVIEW_POKE = "poke"
START_TEAM_PREVIEW = "clearpoke"

MOVES = "moves"
ABILITIES = "abilities"
ITEMS = "items"
COUNT = "count"
SETS = "sets"

UNKNOWN_ITEM = "unknownitem"

# a lookup for the opponent's name given the bot's name
# this has to do with the Pokemon-Showdown PROTOCOL
ID_LOOKUP = {"p1": "p2", "p2": "p1"}

FORCE_SWITCH = "forceSwitch"
REVIVING = "reviving"
WAIT = "wait"
TRAPPED = "trapped"
MAYBE_TRAPPED = "maybeTrapped"
ITEM = "item"

CONDITION = "condition"
DISABLED = "disabled"
PP = "pp"

SELF = "self"

DO_NOTHING_MOVE = "splash"

ID = "id"
BASESTATS = "baseStats"
NAME = "name"
STATUS = "status"
TYPES = "types"
TYPE = "type"
WEIGHT = "weightkg"

SIDE = "side"
POKEMON = "pokemon"
FNT = "fnt"

SWITCH_STRING = "switch"
WIN_STRING = "|win|"
TIE_STRING = "|tie"
CHAT_STRING = "|c|"
TIME_LEFT = "Time left:"
DETAILS = "details"
IDENT = "ident"
TERA_TYPE = "teraType"

MEGA_EVOLVE_GENERATIONS = ["gen6", "gen7"]
CAN_MEGA_EVO = "canMegaEvo"
CAN_ULTRA_BURST = "canUltraBurst"
CAN_DYNAMAX = "canDynamax"
CAN_TERASTALLIZE = "canTerastallize"
CAN_Z_MOVE = "canZMove"
ZMOVE = "zmove"
ULTRA_BURST = "ultra"
MEGA = "mega"

ACTIVE = "active"

PRIORITY = "priority"
STATS = "stats"
BOOSTS = "boosts"

HITPOINTS = "hp"
ATTACK = "attack"
DEFENSE = "defense"
SPECIAL_ATTACK = "special-attack"
SPECIAL_DEFENSE = "special-defense"
SPEED = "speed"
ACCURACY = "accuracy"
EVASION = "evasion"

ABILITY = "ability"
REQUEST_DICT_ABILITY = ABILITY

MAX_BOOSTS = 6

STAT_ABBREVIATION_LOOKUPS = {
    "atk": ATTACK,
    "def": DEFENSE,
    "spa": SPECIAL_ATTACK,
    "spd": SPECIAL_DEFENSE,
    "spe": SPEED,
    "accuracy": ACCURACY,
    "evasion": EVASION,
}

HIDDEN_POWER = "hiddenpower"
HIDDEN_POWER_TYPE_STRING_INDEX = -1
HIDDEN_POWER_ACTIVE_MOVE_BASE_DAMAGE_STRING = "60"

PHYSICAL = "physical"
SPECIAL = "special"
CATEGORY = "category"

DAMAGING_CATEGORIES = [PHYSICAL, SPECIAL]

VOLATILE_STATUS = "volatileStatus"
LOCKED_MOVE = "lockedmove"

# Side-Effects
REFLECT = "reflect"
LIGHT_SCREEN = "lightscreen"
AURORA_VEIL = "auroraveil"
SAFEGUARD = "safeguard"
MIST = "mist"
TAILWIND = "tailwind"
STICKY_WEB = "stickyweb"
WISH = "wish"
FUTURE_SIGHT = "futuresight"
HEALING_WISH = "healingwish"

# weather
RAIN = "raindance"
SUN = "sunnyday"
SAND = "sandstorm"
HAIL = "hail"
SNOW = "snowscape"
DESOLATE_LAND = "desolateland"
HEAVY_RAIN = "primordialsea"

HAIL_OR_SNOW = {HAIL, SNOW}

# Hazards
STEALTH_ROCK = "stealthrock"
SPIKES = "spikes"
TOXIC_SPIKES = "toxicspikes"

TYPECHANGE = "typechange"

FIRST_TURN_MOVES = {"fakeout", "firstimpression"}

WEIGHT_BASED_MOVES = {
    "heavyslam",
    "heatcrash",
    "lowkick",
    "grassknot",
}

SPEED_BASED_MOVES = {"gyroball", "electroball"}

COURT_CHANGE_SWAPS = {
    "spikes",
    "toxicspikes",
    "stealthrock",
    "stickyweb",
    "lightscreen",
    "reflect",
    "auroraveil",
    "tailwind",
}

TRICK_ROOM = "trickroom"
GRAVITY = "gravity"

ELECTRIC_TERRAIN = "electricterrain"
GRASSY_TERRAIN = "grassyterrain"
MISTY_TERRAIN = "mistyterrain"
PSYCHIC_TERRAIN = "psychicterrain"

# switch-out moves
SWITCH_OUT_MOVES = {
    "uturn",
    "voltswitch",
    "partingshot",
    "teleport",
    "flipturn",
    "chillyreception",
    "shedtail",
}

# volatile statuses
CONFUSION = "confusion"
LEECH_SEED = "leechseed"
SUBSTITUTE = "substitute"
TAUNT = "taunt"
ROOST = "roost"
PROTECT = "protect"
BANEFUL_BUNKER = "banefulbunker"
SILK_TRAP = "silktrap"
ENDURE = "endure"
SPIKY_SHIELD = "spikyshield"
DYNAMAX = "dynamax"
SLOW_START = "slowstart"
TERASTALLIZE = "terastallize"
TRANSFORM = "transform"
YAWN = "yawn"
PARTIALLY_TRAPPED = "partiallytrapped"

PROTECT_VOLATILE_STATUSES = [PROTECT, BANEFUL_BUNKER, SPIKY_SHIELD, SILK_TRAP, ENDURE]

TAUNT_DURATION_INCREMENT_END_OF_TURN = {"gen3", "gen4"}

# non-volatile statuses
SLEEP = "slp"
BURN = "brn"
FROZEN = "frz"
PARALYZED = "par"
POISON = "psn"
TOXIC = "tox"
TOXIC_COUNT = "toxic_count"
NON_VOLATILE_STATUSES = {SLEEP, BURN, FROZEN, PARALYZED, POISON, TOXIC}

IMMUNE_TO_POISON_ABILITIES = {"immunity", "pastelveil"}

ASSAULT_VEST = "assaultvest"
HEAVY_DUTY_BOOTS = "heavydutyboots"
LEFTOVERS = "leftovers"
BLACK_SLUDGE = "blacksludge"
LIFE_ORB = "lifeorb"
CHOICE_SCARF = "choicescarf"
CHOICE_BAND = "choiceband"
CHOICE_SPECS = "choicespecs"
CHOICE_ITEMS = {CHOICE_BAND, CHOICE_SPECS, CHOICE_SCARF}

# Moves that boost offensive stats (Attack, Special Attack, or both)
# Unaware ignores these boosts when calculating damage taken
OFFENSIVE_STAT_BOOST_MOVES = {
    # Pure Attack boosters
    "swordsdance",
    "bellydrum",
    "howl",
    "meditate",
    "sharpen",
    "honeclaws",
    # Pure Special Attack boosters
    "nastyplot",
    "tailglow",
    # Mixed/Both offensive stat boosters
    "workup",
    "growth",
    "dragondance",
    "quiverdance",
    "shellsmash",
    "shiftgear",
    "victorydance",
    "filletaway",
    "tidyup",
    "clangoroussoul",
    "noretreat",
    "geomancy",
    # Moves that also boost defensive stats but primarily used offensively
    "calmmind",
    "bulkup",
    "coil",
    "curse",
}

# Pokemon whose most common competitive ability is Unaware
# Used when the opponent's ability hasn't been revealed yet
POKEMON_COMMONLY_UNAWARE = {
    "dondozo",      # Unaware is ability 0 (primary)
    "clefable",     # Unaware is hidden ability but very common competitively
    "clodsire",     # Unaware is hidden ability but common competitively
    "quagsire",     # Unaware is hidden ability but common competitively
    "skeledirge",   # Unaware is hidden ability, sometimes used
    "pyukumuku",    # Unaware is ability 0 (primary)
    "swoobat",      # Unaware is ability 0 (primary)
}

# Penalty multiplier for offensive stat-boosting moves when facing Unaware
# 0.1 means the move's weight is reduced to 10% of its original value
UNAWARE_BOOST_PENALTY = 0.1

# =============================================================================
# ABILITY-BASED MOVE PENALTIES
# These abilities make certain move categories ineffective or counterproductive
# =============================================================================

# Pokemon that commonly have Guts (status boosts Attack) or similar abilities
# Using status moves on these Pokemon backfires badly
POKEMON_COMMONLY_GUTS = {
    "conkeldurr",   # Guts is very common
    "obstagoon",    # Guts is primary competitive ability
    "ursaring",     # Guts
    "raticate",     # Guts
    "swellow",      # Guts
    "heracross",    # Guts
    "luxray",       # Guts
    "throh",        # Guts
    "timburr",      # Guts
    "gurdurr",      # Guts
}

POKEMON_COMMONLY_MARVEL_SCALE = {
    "milotic",      # Marvel Scale is common (or Competitive)
    "dragonair",    # Marvel Scale hidden ability
}

POKEMON_COMMONLY_QUICK_FEET = {
    "ursaring",     # Can also be Guts
    "jolteon",      # Quick Feet hidden ability
}

# Combined set: Pokemon where status moves backfire
POKEMON_STATUS_BACKFIRES = (
    POKEMON_COMMONLY_GUTS
    | POKEMON_COMMONLY_MARVEL_SCALE
    | POKEMON_COMMONLY_QUICK_FEET
)

# Poison Heal: Toxic/Poison actually heals them
POKEMON_COMMONLY_POISON_HEAL = {
    "gliscor",      # Poison Heal is the standard competitive ability
    "breloom",      # Poison Heal is very common
}

# Abilities that grant immunity to specific move types
# and sometimes boost stats when hit by that type

# Water Absorb / Dry Skin / Storm Drain: Water moves heal or boost
POKEMON_COMMONLY_WATER_IMMUNE = {
    "vaporeon",     # Water Absorb
    "lapras",       # Water Absorb
    "quagsire",     # Water Absorb (or Unaware)
    "poliwrath",    # Water Absorb
    "mantine",      # Water Absorb
    "jellicent",    # Water Absorb
    "seismitoad",   # Water Absorb
    "gastrodon",    # Storm Drain (also boosts SpA)
    "cradily",      # Storm Drain
    "lumineon",     # Storm Drain
    "toxicroak",    # Dry Skin (heals from water)
    "jynx",         # Dry Skin
    "heliolisk",    # Dry Skin
}

# Volt Absorb / Lightning Rod / Motor Drive: Electric moves heal or boost
POKEMON_COMMONLY_ELECTRIC_IMMUNE = {
    "jolteon",      # Volt Absorb
    "lanturn",      # Volt Absorb
    "thundurus",    # Volt Absorb (hidden ability, sometimes used)
    "zeraora",      # Volt Absorb
    "raichu",       # Lightning Rod (Alolan)
    "marowak",      # Lightning Rod (Alolan especially)
    "togedemaru",   # Lightning Rod
    "electivire",   # Motor Drive (boosts Speed)
    "pachirisu",    # Volt Absorb
}

# Flash Fire: Fire moves boost their Fire-type attacks
POKEMON_COMMONLY_FLASH_FIRE = {
    "heatran",      # Flash Fire is extremely common
    "chandelure",   # Flash Fire
    "arcanine",     # Flash Fire (one of two abilities)
    "ninetales",    # Flash Fire
    "flareon",      # Flash Fire
    "houndoom",     # Flash Fire
    "typhlosion",   # Flash Fire
    "rapidash",     # Flash Fire
    "camerupt",     # Can have Solid Rock instead, but Flash Fire common
    "centiskorch",  # Flash Fire
}

# Levitate: Ground moves do nothing
POKEMON_COMMONLY_LEVITATE = {
    "gengar",       # Only in older gens (gen 1-6)
    "hydreigon",    # Levitate
    "latios",       # Levitate
    "latias",       # Levitate
    "bronzong",     # Levitate (or Heatproof)
    "rotom",        # Levitate (all forms except fan)
    "rotomwash",    # Levitate
    "rotomheat",    # Levitate
    "rotommow",     # Levitate
    "rotomfrost",   # Levitate
    "weezing",      # Levitate (Galarian)
    "weezinggalar", # Levitate
    "eelektross",   # Levitate (famous for no weaknesses)
    "cresselia",    # Levitate
    "uxie",         # Levitate
    "mesprit",      # Levitate
    "azelf",        # Levitate
    "mismagius",    # Levitate
    "flygon",       # Levitate
    "claydol",      # Levitate
    "vikavolt",     # Levitate
    "tatsugiri",    # Storm Drain, but worth noting
}

# Magic Bounce: Status moves bounce back to the user
POKEMON_COMMONLY_MAGIC_BOUNCE = {
    "hatterene",    # Magic Bounce is common competitive set
    "espeon",       # Magic Bounce hidden ability, often used
    "xatu",         # Magic Bounce
    "diancie",      # Magic Bounce (Mega)
}

# Competitive: Stat drops give +2 SpA
POKEMON_COMMONLY_COMPETITIVE = {
    "milotic",      # Competitive or Marvel Scale
    "gothitelle",   # Competitive (Shadow Tag banned, so Competitive used)
    "wigglytuff",   # Competitive
    "lopunny",      # Can have Competitive (non-Mega)
}

# Defiant: Stat drops give +2 Atk
POKEMON_COMMONLY_DEFIANT = {
    "bisharp",      # Defiant is standard
    "kingambit",    # Defiant is standard
    "thundurus",    # Defiant
    "braviary",     # Defiant
    "passimian",    # Defiant
    "falinks",      # Defiant
    "pawniard",     # Defiant
    "primeape",     # Defiant
    "annihilape",   # Defiant
    "mankey",       # Defiant
    "gallade",      # Defiant (hidden)
}

# Combined: Pokemon where stat-lowering moves backfire
POKEMON_STAT_DROP_BACKFIRES = POKEMON_COMMONLY_COMPETITIVE | POKEMON_COMMONLY_DEFIANT

# =============================================================================
# MOVE CATEGORIES FOR PENALTIES
# =============================================================================

# Status moves that inflict non-volatile status conditions
# These backfire against Guts/Marvel Scale/Quick Feet
STATUS_INFLICTING_MOVES = {
    # Burn
    "willowisp",
    "scald",        # 30% chance
    "searingshot",
    "inferno",
    "sacredfire",
    "burningjealousy",
    # Paralysis
    "thunderwave",
    "stunspore",
    "glare",
    "nuzzle",
    "zapcannon",
    "bodyslam",     # 30% chance
    # Sleep
    "spore",
    "sleeppowder",
    "hypnosis",
    "sing",
    "grasswhistle",
    "lovelykiss",
    "yawn",
    "darkvoid",
    "relicsong",
    # Poison (not including Toxic - handled separately for Poison Heal)
    "poisonpowder",
    "poisongas",
    "toxic",
    "toxicspikes",
    "poisonfang",
    "gunkshot",
    "poisonjab",
    "sludgebomb",
    "sludgewave",
    "crosspoison",
    "poisontail",
    # Freeze (rare but included)
    "icebeam",      # 10% chance
    "blizzard",     # 10% chance
}

# Moves that are specifically Toxic/Poison (for Poison Heal check)
TOXIC_POISON_MOVES = {
    "toxic",
    "poisonpowder",
    "poisongas",
    "toxicspikes",
    "poisonfang",
    "banefulbunker",
}

# Water-type moves (for Water Absorb/Storm Drain/Dry Skin)
WATER_TYPE_MOVES = {
    "aquajet",
    "aquatail",
    "brine",
    "bubblebeam",
    "bubble",
    "crabhammer",
    "dive",
    "flipturn",
    "hydrocannon",
    "hydropump",
    "jetpunch",
    "liquidation",
    "muddywater",
    "originpulse",
    "razorshell",
    "scald",
    "snipeshot",
    "sparklingaria",
    "steamroller",
    "surf",
    "surgingstrikes",
    "waterfall",
    "watergun",
    "waterpledge",
    "waterpulse",
    "watershuriken",
    "waterspout",
    "wavecrash",
    "whirlpool",
}

# Electric-type moves (for Volt Absorb/Lightning Rod/Motor Drive)
ELECTRIC_TYPE_MOVES = {
    "boltstrike",
    "charge",
    "chargebeam",
    "discharge",
    "eerieimpulse",
    "electricterrain",
    "electrify",
    "electroball",
    "electroweb",
    "fusionbolt",
    "nuzzle",
    "overdrive",
    "paralyzinggas",
    "pikapapow",
    "plasmafists",
    "risingvoltage",
    "shockwave",
    "spark",
    "thunder",
    "thunderbolt",
    "thundercage",
    "thunderfang",
    "thunderpunch",
    "thundershock",
    "thunderwave",
    "volttackle",
    "voltswitch",
    "wildcharge",
    "zapcannon",
    "zingzap",
}

# Fire-type moves (for Flash Fire)
FIRE_TYPE_MOVES = {
    "blazekick",
    "blastburn",
    "blueflare",
    "burningjealousy",
    "burnup",
    "ember",
    "eruption",
    "fierydance",
    "fireblast",
    "firefang",
    "firelash",
    "firepledge",
    "firepunch",
    "firespin",
    "flameburst",
    "flamecharge",
    "flamethrower",
    "flamewheel",
    "flareblitz",
    "fusionflare",
    "heatcrash",
    "heatwave",
    "inferno",
    "incinerate",
    "lavaplume",
    "magmastorm",
    "mindblown",
    "mysticalfire",
    "overheat",
    "pyroball",
    "sacredfire",
    "searingshot",
    "shelltrap",
    "vcreate",
}

# Ground-type moves (for Levitate)
GROUND_TYPE_MOVES = {
    "boneclub",
    "bonemerang",
    "bonerush",
    "bulldoze",
    "dig",
    "drillrun",
    "earthpower",
    "earthquake",
    "fissure",
    "groundpulse",
    "highhorsepower",
    "landswrath",
    "magnitude",
    "mudshot",
    "mudslap",
    "precipiceblades",
    "sandattack",
    "sandtomb",
    "scorchingsands",
    "spikes",
    "stealthrock",  # Not actually Ground damage but sets hazard
    "stompingtantrum",
    "thousandarrows",
    "thousandwaves",
}

# Status moves that Magic Bounce reflects (hazards, status, etc.)
MAGIC_BOUNCE_REFLECTED_MOVES = {
    # Hazards
    "stealthrock",
    "spikes",
    "toxicspikes",
    "stickyweb",
    # Status inflicting
    "toxic",
    "willowisp",
    "thunderwave",
    "spore",
    "sleeppowder",
    "hypnosis",
    "sing",
    "glare",
    "stunspore",
    "poisonpowder",
    "yawn",
    # Stat lowering
    "memento",
    "partingshot",
    "defog",
    "scaryface",
    "nobleroar",
    "tearfullook",
    # Other status
    "taunt",
    "torment",
    "encore",
    "disable",
    "attract",
    "leechseed",
    "embargo",
    "healblock",
    "telekinesis",
    "confuseray",
    "supersonic",
    "sweetkiss",
    "flatter",
    "swagger",
}

# Moves that lower opponent's stats (for Competitive/Defiant)
STAT_LOWERING_MOVES = {
    # Direct stat lowering
    "icywind",
    "bulldoze",
    "rocksmash",
    "rocktomb",
    "electroweb",
    "strugglebug",
    "snarl",
    "partingshot",
    "memento",
    "nobleroar",
    "playrough",
    "moonblast",
    "seedflare",
    "shadowball",
    "energyball",
    "earthpower",
    "psychic",
    "focusblast",
    "flashcannon",
    "acidspray",
    "acid",
    "lunge",
    "breakingswipe",
    "tickle",
    "charm",
    "featherdance",
    "babydolleyes",
    "captivate",
    "confide",
    "eerieimpulse",
    "metalsound",
    "faketears",
    "scaryface",
    "stringshot",
    "tearfullook",
    "venomdrench",
    # Sticky Web (Speed drop on switch)
    "stickyweb",
    # Defog clears hazards but triggers Defiant
    "defog",
}

# Note: Intimidate is handled separately since it's an ability trigger, not a move
# But switching in Pokemon with Intimidate against Defiant/Competitive is also bad

# =============================================================================
# PENALTY VALUES
# =============================================================================

# Heavy penalty (0.1) - Move is almost always wrong
ABILITY_PENALTY_SEVERE = 0.1

# Medium penalty (0.3) - Move is usually wrong but occasionally justified
ABILITY_PENALTY_MEDIUM = 0.3

# Light penalty (0.5) - Move is somewhat suboptimal
ABILITY_PENALTY_LIGHT = 0.5


# =============================================================================
# MOLD BREAKER ABILITIES (ignore opponent's defensive abilities)
# =============================================================================

MOLD_BREAKER_ABILITIES = {
    "moldbreaker",
    "teravolt",
    "turboblaze",
}


# =============================================================================
# FOCUS SASH DETECTION
# =============================================================================

# Pokemon that commonly hold Focus Sash (frail leads, suicide leads, etc.)
POKEMON_COMMONLY_FOCUS_SASH = {
    # Common suicide leads
    "azelf",
    "froslass",
    "garchomp",   # lead sets
    "greninja",
    "hawlucha",
    "landorustherian",  # sometimes lead
    "lycanrocdusk",
    "mamoswine",
    "smeargle",
    # Frail setup sweepers
    "alakazam",
    "breloom",
    "dugtrio",
    "gengar",
    "lucario",
    "mimikyu",     # technically Disguise but similar principle
    "ribombee",
    "sneasler",
    "weavile",
    # Common endgame sash users
    "cinderace",
    "dragapult",
    "ironvaliant",
    "kingambit",
}

# Multi-hit moves that break through Focus Sash
MULTI_HIT_MOVES = {
    "armthrust",
    "barrage",
    "bonerush",
    "bonemerang",
    "bulletseed",
    "cometpunch",
    "doublehit",
    "doublekick",
    "doubleironbash",
    "dualchop",
    "dualwingbeat",
    "furyattack",
    "furyswipes",
    "geargrind",
    "iciclespear",
    "mudshot",
    "pinmissile",
    "populationbomb",
    "rockblast",
    "scaleshot",
    "surgingstrikes",
    "tailslap",
    "technoblast",
    "triplekick",
    "tripleaxel",
    "twinbeam",
    "twineedle",
    "watershuriken",
}

# Priority moves (useful to finish off after Sash)
PRIORITY_MOVES = {
    "accelerock",
    "aquajet",
    "bulletpunch",
    "extremespeed",
    "fakeout",
    "feint",
    "firstimpression",
    "grappleghost",
    "iceshard",
    "jetpunch",
    "machpunch",
    "quickattack",
    "shadowsneak",
    "suckerpunch",
    "vacuumwave",
    "watershuriken",
}


# =============================================================================
# SETUP VS PHAZERS
# =============================================================================

# Phazing moves that force switches (wasting setup boosts)
PHAZING_MOVES = {
    "roar",
    "whirlwind",
    "dragontail",
    "circlethrow",
    "yawn",  # forces switch or sleep
}

# Setup/boosting moves that are wasted if phazed out
SETUP_MOVES = {
    # Physical boosts
    "swordsdance",
    "dragondance",
    "bellydrum",
    "bulkup",
    "howl",
    "honeclaws",
    "shiftgear",
    "victorydance",
    "tidyup",
    # Special boosts
    "nastyplot",
    "calmmind",
    "tailglow",
    "quiverdance",
    "geomancy",
    "torchsong",
    # Mixed/other
    "shellsmash",
    "growth",
    "workup",
    "coil",
    "curse",  # non-ghost
    "irondefense",
    "amnesia",
    "agility",
    "autotomize",
    "rockpolish",
    "cottonguard",
    "cosmicpower",
    "stockpile",
    "acupressure",
    "minimize",
    "doubleteam",
}


# =============================================================================
# SUBSTITUTE AWARENESS
# =============================================================================

# Status-only moves that fail against Substitute (non-damaging)
STATUS_ONLY_MOVES = {
    "toxic",
    "willowisp",
    "thunderwave",
    "spore",
    "sleeppowder",
    "hypnosis",
    "sing",
    "glare",
    "stunspore",
    "poisonpowder",
    "yawn",
    "leechseed",
    "taunt",
    "encore",
    "disable",
    "attract",
    "confuseray",
    "swagger",
    "flatter",
    "torment",
    "spite",
    "grudge",
    "nightmare",
    "perishsong",
    "curse",  # ghost variant
    "superfang",  # goes through sub actually, but included for reference
}

# Moves/abilities that bypass Substitute
INFILTRATOR_BYPASS = {"infiltrator"}


# =============================================================================
# CONTACT MOVES VS ROCKY HELMET / IRON BARBS / ROUGH SKIN
# =============================================================================

# Contact moves that trigger Rocky Helmet, Iron Barbs, Rough Skin
CONTACT_MOVES = {
    # Common physical attacks
    "closecombat",
    "drainpunch",
    "facade",
    "firstimpression",
    "fakeout",
    "gigaimpact",
    "hammerarm",
    "hijumpkick",
    "irontail",
    "karatechop",
    "knockoff",
    "lastresort",
    "lowkick",
    "lowsweep",
    "machpunch",
    "megakick",
    "megapunch",
    "poweruppunch",
    "quickattack",
    "rapidspin",
    "return",
    "reversal",
    "revenge",
    "seismictoss",
    "slam",
    "strength",
    "submission",
    "superpower",
    "tackle",
    "takedown",
    "vitalthrow",
    "wakeupslap",
    # Flying/Fairy/Dragon physical
    "aerialace",
    "airslash",  # not contact!
    "bravebird",
    "dualwingbeat",
    "fly",
    "hurricane",  # not contact!
    "pluck",
    "skyattack",
    "wingattack",
    "acrobatics",
    "playrough",
    "dracometeor",  # not contact!
    "dragonascent",
    "dragonclaw",
    "dragontail",
    "outrage",
    # Dark/Ghost physical
    "assurance",
    "bite",
    "crunch",
    "darkestlariat",
    "fling",
    "knockoff",
    "nightslash",
    "payback",
    "pursuit",
    "suckerpunch",
    "thief",
    "wickedblow",
    "astonish",
    "lick",
    "phantomforce",
    "poltergeist",
    "shadowclaw",
    "shadowforce",
    "shadowpunch",
    "shadowsneak",
    "spectralthief",
    # Steel/Rock/Ground physical
    "bulletpunch",
    "flashcannon",  # not contact!
    "heavyslam",
    "ironhead",
    "meteormash",
    "steelbeam",  # not contact!
    "earthquake",  # not contact!
    "headsmash",
    "rockslide",  # not contact!
    "rockthrow",
    "smackdown",
    "stealthrock",  # not contact!
    "stoneedge",  # not contact!
    # Fire/Ice physical
    "fireblast",  # not contact!
    "firepunch",
    "flamecharge",
    "flamewheel",
    "flareblitz",
    "fusionflare",  # not contact!
    "heatwave",  # not contact!
    "icefang",
    "icehammer",
    "icepunch",
    "iceshard",
    "iciclecrash",
    "iciclespear",
    # Water/Grass/Electric physical
    "aquajet",
    "aquatail",
    "crabhammer",
    "liquidation",
    "waterfall",
    "bulldoze",
    "hammerarm",
    "leafblade",
    "powerwhip",
    "seedbomb",
    "volttackle",
    "wildcharge",
    "thunderpunch",
    "spark",
    "fusionbolt",
    # Bug physical
    "attackorder",
    "bugbite",
    "leechlife",
    "megahorn",
    "pinmissile",
    "xscissor",
    "uturn",
}

# Pokemon that commonly have Iron Barbs (takes 1/8 HP from contact moves)
POKEMON_COMMONLY_IRON_BARBS = {
    "ferrothorn",
    "toedscool",
    "toedscruel",
}

# Pokemon that commonly have Rough Skin (takes 1/8 HP from contact moves)
POKEMON_COMMONLY_ROUGH_SKIN = {
    "garchomp",
    "carvanha",
    "sharpedo",
    "sandaconda",
    "dondozo",  # sometimes
}

# Rocky Helmet is an item, not an ability, so we infer from common holders
POKEMON_COMMONLY_ROCKY_HELMET = {
    "ferrothorn",
    "skarmory",
    "corviknight",
    "garchomp",
    "hippowdon",
    "tangrowth",
    "toxapex",
    "landorustherian",
    "ting-lu",
    "clodsire",
}
