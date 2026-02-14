# =============================================================================
# DYNAMIC IMPORTS FROM DATA FILES
# =============================================================================
# Import move sets extracted from moves.json
from constants_pkg.move_flags import (
    SOUND_MOVES,
    POWDER_MOVES,
    BULLET_MOVES,
    WIND_MOVES,
    CONTACT_MOVES,
    GRASS_TYPE_MOVES,
    DARK_TYPE_MOVES,
    SELF_STAT_DROP_MOVES,
    ALL_STATUS_MOVES,
)

# Import Pokemon sets extracted from pokedex.json
from constants_pkg.pokemon_abilities import (
    POKEMON_COMMONLY_CONTRARY,
    POKEMON_COMMONLY_SAP_SIPPER,
    POKEMON_COMMONLY_STURDY,
    POKEMON_COMMONLY_DISGUISE,
    POKEMON_COMMONLY_SOUNDPROOF,
    POKEMON_COMMONLY_BULLETPROOF,
    POKEMON_COMMONLY_OVERCOAT,
    POKEMON_COMMONLY_EARTH_EATER,
    POKEMON_COMMONLY_JUSTIFIED,
    POKEMON_COMMONLY_STEAM_ENGINE,
    POKEMON_COMMONLY_WIND_RIDER,
    POKEMON_COMMONLY_WELL_BAKED_BODY,
    POKEMON_COMMONLY_STAT_DROP_IMMUNE,
    POKEMON_COMMONLY_MIRROR_ARMOR,
    POKEMON_COMMONLY_FLUFFY,
    POKEMON_COMMONLY_DRY_SKIN,
    POKEMON_WITH_PRANKSTER,
    POKEMON_WITH_INTIMIDATE,
    POKEMON_COMMONLY_AIR_BALLOON,
    POKEMON_COMMONLY_SUPREME_OVERLORD,
)

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

# Good as Gold: Blocks ALL status moves (cannot be hit by them)
POKEMON_COMMONLY_GOOD_AS_GOLD = {
    "gholdengo",    # Good as Gold is Gholdengo's only ability
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
# Pure status moves that ONLY inflict a status condition (no damage).
# These are completely wasted if the opponent already has a status.
PURE_STATUS_MOVES = {
    "toxic",
    "willowisp",
    "thunderwave",
    "stunspore",
    "glare",
    "poisonpowder",
    "poisongas",
    "spore",
    "sleeppowder",
    "hypnosis",
    "sing",
    "grasswhistle",
    "lovelykiss",
    "yawn",
    "darkvoid",
}

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
# Contact moves are dynamically loaded from moves.json via move_flags.py

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


# =============================================================================
# PENALTY BOOST VALUES (for opportunities, not penalties)
# =============================================================================

# Boost multiplier for moves that are extra effective
ABILITY_BOOST_LIGHT = 1.2   # 20% bonus (e.g., Fire vs Dry Skin)
ABILITY_BOOST_MEDIUM = 1.3  # 30% bonus (e.g., Fire vs Fluffy)


# =============================================================================
# DAMAGING GROUND MOVES (for Levitate/Earth Eater/Air Balloon checks)
# Excludes hazards like Stealth Rock and Spikes
# =============================================================================

DAMAGING_GROUND_MOVES = {
    "boneclub",
    "bonemerang",
    "bonerush",
    "bulldoze",
    "dig",
    "drillrun",
    "earthpower",
    "earthquake",
    "fissure",
    "headlongrush",
    "highhorsepower",
    "landswrath",
    "magnitude",
    "mudshot",
    "mudslap",
    "precipiceblades",
    "sandtomb",
    "scorchingsands",
    "stompingtantrum",
    "thousandarrows",
    "thousandwaves",
}


# =============================================================================
# WEATHER AND TERRAIN CONSTANTS
# =============================================================================

# Weather conditions that affect move effectiveness
WEATHER_RAIN = {"raindance", "primordialsea"}
WEATHER_SUN = {"sunnyday", "desolateland"}
WEATHER_EXTREME_RAIN = {"primordialsea"}  # Fire moves fail completely
WEATHER_EXTREME_SUN = {"desolateland"}    # Water moves fail completely

# Terrain conditions
TERRAIN_PSYCHIC = {"psychicterrain"}

# Priority moves affected by Psychic Terrain (blocked on grounded targets)
# This uses PRIORITY_MOVES already defined above


# =============================================================================
# PRANKSTER INTERACTION
# =============================================================================

# Prankster status moves fail against Dark-type Pokemon (Gen 7+)
# Uses ALL_STATUS_MOVES from move_flags.py for comprehensive check


# =============================================================================
# ABILITIES BYPASSED BY MOLD BREAKER
# These abilities are ignored when attacker has Mold Breaker/Teravolt/Turboblaze
# =============================================================================

MOLD_BREAKER_BYPASSED_ABILITIES = {
    # Type immunities
    "voltabsorb",
    "waterabsorb",
    "flashfire",
    "lightningrod",
    "motordrive",
    "stormdrain",
    "dryskin",
    "sapsipper",
    "levitate",
    "eartheater",
    # Damage reduction
    "sturdy",
    "fluffy",
    "thickfat",
    "multiscale",
    "shadowshield",
    "filter",
    "solidrock",
    "prismarmor",
    # Move category immunities
    "soundproof",
    "bulletproof",
    "overcoat",
    # Other defensive abilities
    "unaware",  # Our boosts still count if we have Mold Breaker
    "wonderguard",
}

# These abilities are NOT bypassed by Mold Breaker
MOLD_BREAKER_IGNORED_ABILITIES = {
    "contrary",      # Affects their own stat changes, not our attack
    "mirrorarmor",   # Reflects back, different mechanic
    "disguise",      # Form change, not damage prevention
    "magicbounce",   # Reflects moves, not damage prevention
    "goodasgold",    # Blocks status, but Mold Breaker doesn't help here
}


# =============================================================================
# GROUNDED CHECK (for Psychic Terrain priority blocking)
# =============================================================================

# Pokemon with these abilities are not grounded
UNGROUNDED_ABILITIES = {
    "levitate",
}

# Items that make Pokemon ungrounded
UNGROUNDED_ITEMS = {
    "airballoon",
}

# Types that are naturally ungrounded
UNGROUNDED_TYPES = {
    "flying",
}


# =============================================================================
# PHASE 1.1: POSITIVE BOOSTS EXPANSION
# =============================================================================

# Protect-like moves (opponent used last turn = free setup opportunity)
PROTECT_MOVES = {
    "protect",
    "detect",
    "kingsshield",
    "banefulbunker",
    "spikyshield",
    "obstruct",
    "silktrap",
    "burningbulwark",
    "maxguard",
}

# Boost values for opportunity exploitation
BOOST_CHOICE_LOCKED_RESIST = 1.4   # +40% setup when choice-locked into resisted move
BOOST_CHOICE_LOCKED_IMMUNE = 1.5   # +50% setup when choice-locked into immune move
BOOST_PROTECT_PUNISH = 1.3         # +30% setup/status after opponent Protect
BOOST_OPPONENT_STATUSED = 1.2      # +20% setup when opponent is statused
BOOST_LOW_HP_PRIORITY = 1.15       # +15% priority when opponent <25% HP


# =============================================================================
# PHASE 1.2: TRICK ROOM AWARENESS
# =============================================================================

# Speed-boosting moves (bad under Trick Room)
SPEED_BOOSTING_MOVES = {
    "agility",
    "autotomize",
    "rockpolish",
    "tailwind",
    "dragondance",
    "shiftgear",
    "quiverdance",
    "geomancy",
    "flamecharge",
    "rapidspin",  # also boosts speed in gen 8+
}

# Trick Room move itself
TRICK_ROOM_MOVES = {"trickroom"}

# Penalty/boost values for Trick Room
PENALTY_SPEED_BOOST_IN_TR = 0.5    # -50% speed boosts under TR
BOOST_SETUP_SLOW_IN_TR = 1.3       # +30% setup for slow Pokemon under TR


# =============================================================================
# PHASE 1.3: SCREENS AWARENESS
# =============================================================================

# Screen-breaking moves
SCREEN_BREAKING_MOVES = {
    "brickbreak",
    "psychicfangs",
    "ragingbull",
    "defog",  # Also removes screens
}

# Screen moves (for detection)
SCREEN_SETTING_MOVES = {
    "reflect",
    "lightscreen",
    "auroraveil",
}

# Boost values for screens
BOOST_SETUP_VS_SCREENS = 1.3       # +30% setup when screens reduce damage to us
BOOST_SCREEN_BREAKER = 1.3         # +30% screen-breaking moves when screens up


# =============================================================================
# PHASE 1.4: WEATHER/TERRAIN SYNERGIES - SPEED DOUBLING ABILITIES
# =============================================================================

# Pokemon with Swift Swim (2x speed in Rain)
POKEMON_WITH_SWIFT_SWIM = {
    "kingdra",
    "kabutops",
    "omastar",
    "ludicolo",
    "swampert",  # Mega
    "qwilfish",
    "beartic",
    "barraskewda",
    "floatzel",
    "golduck",
    "huntail",
    "gorebyss",
    "luvdisc",
    "relicanth",
    "seaking",
    "basculin",
    "basculegion",
    "araquanid",  # technically Water Bubble but similar
}

# Pokemon with Sand Rush (2x speed in Sand)
POKEMON_WITH_SAND_RUSH = {
    "excadrill",
    "sandslash",  # Alolan
    "sandslashalola",
    "stoutland",
    "dracozolt",
    "lycanroc",  # Midday
    "lycanrocmidday",
}

# Pokemon with Slush Rush (2x speed in Snow/Hail)
POKEMON_WITH_SLUSH_RUSH = {
    "sandslashalola",
    "beartic",
    "arctozolt",
    "alolansandslash",
    "cetitan",
}

# Pokemon with Chlorophyll (2x speed in Sun)
POKEMON_WITH_CHLOROPHYLL = {
    "venusaur",
    "vileplume",
    "bellossom",
    "victreebel",
    "exeggutor",
    "tangrowth",
    "leafeon",
    "lilligant",
    "sawsbuck",
    "whimsicott",  # Usually Prankster but can have this
    "sunflora",
    "shiftry",
    "tropius",
    "cherrim",
    "jumpluff",
}

# Abilities that double speed in weather
SWIFT_SWIM_ABILITIES = {"swiftswim"}
SAND_RUSH_ABILITIES = {"sandrush"}
SLUSH_RUSH_ABILITIES = {"slushrush"}
CHLOROPHYLL_ABILITIES = {"chlorophyll"}

# Weather conditions (expanded)
WEATHER_SAND = {"sandstorm"}
WEATHER_SNOW = {"snow", "hail"}

# Terrain boost values
BOOST_TERRAIN_MOVE = 1.3           # +30% for moves boosted by terrain
BOOST_WEATHER_SPEED_ADVANTAGE = 1.25  # +25% when we have weather speed advantage

# Terrain-boosted move types
TERRAIN_ELECTRIC_BOOSTS = {"electric"}  # Electric Terrain boosts Electric moves
TERRAIN_GRASSY_BOOSTS = {"grass"}       # Grassy Terrain boosts Grass moves
TERRAIN_PSYCHIC_BOOSTS = {"psychic"}    # Psychic Terrain boosts Psychic moves

# Moves weakened by Grassy Terrain
GRASSY_TERRAIN_WEAKENED = {
    "earthquake",
    "bulldoze",
    "magnitude",
}


# =============================================================================
# PHASE 2.1: SWITCH EVALUATION SYSTEM
# =============================================================================

# Pokemon with Intimidate (penalize switching these in vs Defiant/Competitive)
POKEMON_WITH_INTIMIDATE_COMMON = {
    "gyarados",
    "landorustherian",
    "incineroar",
    "arcanine",
    "salamence",
    "krookodile",
    "luxray",
    "mawile",
    "hitmontop",
    "staraptor",
    "scrafty",
    "mightyena",
    "masquerain",
    "tauros",
}

# Intimidate ability names
INTIMIDATE_ABILITIES = {"intimidate"}

# Switch penalty/boost values
PENALTY_SWITCH_INTO_HAZARDS_PER_LAYER = 0.92  # -8% per effective hazard layer
PENALTY_SWITCH_INTIMIDATE_VS_DEFIANT = 0.3    # -70% for Intimidate vs Defiant/Competitive
PENALTY_SWITCH_LOW_HP = 0.6                    # -40% for switching to <25% HP Pokemon
PENALTY_SWITCH_WEAK_TO_OPPONENT = 0.7          # -30% for switching to type-weak Pokemon

BOOST_SWITCH_RESISTS_STAB = 1.3               # +30% for switching to STAB resister
BOOST_SWITCH_HAS_RECOVERY = 1.15              # +15% for Pokemon with recovery moves
BOOST_SWITCH_COUNTERS = 1.4                   # +40% for hard counters
BOOST_SWITCH_UNAWARE_VS_SETUP = 1.6           # +60% for Unaware vs boosted attackers

# Recovery moves for switch evaluation
POKEMON_RECOVERY_MOVES = {
    "recover",
    "softboiled",
    "roost",
    "moonlight",
    "morningsun",
    "synthesis",
    "shoreup",
    "slackoff",
    "milkdrink",
    "healorder",
    "rest",
    "wish",
    "strengthsap",
    "leechseed",
    "drainpunch",
    "gigadrain",
    "hornleech",
    "drainingkiss",
    "oblivionwing",
    "paraboliccharge",
}


# =============================================================================
# PHASE 2.2: ENTRY HAZARD CALCULUS
# =============================================================================

# Hazard removal moves
HAZARD_REMOVAL_MOVES = {
    "defog",
    "rapidspin",
    "courtchange",
    "tidyup",
    "mortalspin",
}

# Hazard setting moves
HAZARD_SETTING_MOVES = {
    "stealthrock",
    "spikes",
    "toxicspikes",
    "stickyweb",
}

# Pokemon commonly weak to Stealth Rock (4x or key Pokemon)
POKEMON_SR_4X_WEAK = {
    # Fire/Flying
    "charizard",
    "moltres",
    "talonflame",
    "ho-oh",
    # Bug/Flying
    "butterfree",
    "masquerain",
    "yanmega",
    "volcarona",
    "frosmoth",
    # Ice/Flying
    "articuno",
    "delibird",
    # Bug/Fire
    "volcarona",
    "centiskorch",
    # Fire types (2x)
    "blaziken",
    "infernape",
    "cinderace",
    "arcanine",
}

# Pokemon that commonly run Heavy-Duty Boots
POKEMON_COMMONLY_HDB = {
    "volcarona",
    "moltres",
    "talonflame",
    "dragonite",
    "weavile",
    "tornadustherian",
    "zapdos",
    "mandibuzz",
    "corviknight",
    "pelipper",
}

# Hazard calculus boost/penalty values
BOOST_SET_HAZARDS_NO_HAZARDS = 1.4            # +40% Stealth Rock when none up
BOOST_SET_HAZARDS_SR_WEAK_OPP = 1.5           # +50% SR when opponent has SR-weak Pokemon
PENALTY_SET_HAZARDS_ALREADY_UP = 0.2          # -80% when hazards already up
BOOST_REMOVE_HAZARDS_HEAVY = 1.4              # +40% Defog/Spin when we have heavy hazards
PENALTY_REMOVE_HAZARDS_NONE = 0.1             # -90% Defog/Spin when no hazards to remove
PENALTY_HAZARDS_VS_REMOVAL = 0.7              # -30% hazard moves when opponent has revealed removal


# =============================================================================
# PHASE 2.3: TERA PREDICTION
# =============================================================================

# Common defensive Tera types (used to survive hits)
DEFENSIVE_TERA_TYPES = {
    "steel",    # Many resistances
    "fairy",    # Dragon immunity
    "ghost",    # Normal/Fighting immunity
    "water",    # Few weaknesses
    "flying",   # Ground immunity
    "poison",   # Fairy resistance for fairies
}

# Common offensive Tera types (used for STAB coverage)
OFFENSIVE_TERA_TYPES = {
    "fire",
    "electric",
    "ice",
    "ground",
    "fighting",
    "ghost",
    "dark",
    "stellar",  # Gen 9 special Tera
}

# Tera boost values
BOOST_COVERAGE_VS_LIKELY_TERA = 1.2           # +20% coverage moves when opponent might Tera
BOOST_STAY_FOR_TERA = 1.15                    # +15% staying in if Tera could save us


# =============================================================================
# PHASE 3.1: WIN CONDITION AWARENESS
# =============================================================================

# Penalty/boost values for win condition protection
PENALTY_RISKY_WITH_WINCON = 0.6               # -40% risky plays when active is wincon
BOOST_SAFE_WITH_WINCON = 1.3                  # +30% safe plays when active is wincon
PENALTY_SACK_ONLY_CHECK = 0.5                 # -50% trading when we're the only check
BOOST_REVENGE_KILL_THREAT = 1.4               # +40% revenge killing opponent wincon

# Moves considered "risky" (high risk, could lose the Pokemon)
RISKY_MOVES = {
    "explosion",
    "selfdestruct",
    "mistyexplosion",
    "mindblown",
    "steelbeam",
    "chloroblast",
    "finalgambit",
    "memento",
    "healingwish",
    "lunardance",
    "destinybond",
}

# Moves considered "safe" (low commitment)
SAFE_MOVES = {
    "uturn",
    "voltswitch",
    "flipturn",
    "partingshot",
    "teleport",
    "batonpass",
    "protect",
    "detect",
    "substitute",
}

# Pre-orb safety (Poison Heal + Toxic Orb)
# When Toxic Orb hasn't activated yet, avoid letting opponents remove it or status us first.
PRE_ORB_STATUS_THREAT_MIN_PROB = 0.2  # Minimum inferred status threat to trigger adjustment
BOOST_PRE_ORB_PROTECT = 1.35          # Up to +35% weight for Protect vs status threats
PENALTY_PRE_ORB_NONPROTECT = 0.85     # Up to -15% weight for non-Protect vs status threats
# Knock Off / item removal is MORE catastrophic than status (item gone forever = no Poison Heal)
BOOST_PRE_ORB_PROTECT_KNOCK = 1.8     # +80% weight for Protect when opponent likely has Knock Off
PENALTY_PRE_ORB_NONPROTECT_KNOCK = 0.6  # -40% weight for attacking into Knock Off threat
BOOST_PRE_ORB_SWITCH_KNOCK = 1.5      # +50% weight for switching out to absorb Knock Off
# Item-removal moves that deny Toxic Orb activation
ITEM_REMOVAL_MOVES = {"knockoff", "trick", "switcheroo", "corrosivegas"}


# =============================================================================
# PHASE 3.2: PP TRACKING
# =============================================================================

# Boost values for PP-based decisions
BOOST_STALL_NO_RECOVERY_PP = 1.4              # +40% stall when opponent recovery exhausted
BOOST_DEFENSIVE_LOW_PP = 1.3                  # +30% defensive when opponent STAB low PP


# =============================================================================
# PHASE 3.3: MOMENTUM TRACKING
# =============================================================================

# Momentum thresholds
MOMENTUM_STRONG_POSITIVE = 5.0
MOMENTUM_POSITIVE = 1.0
MOMENTUM_NEGATIVE = -1.0
MOMENTUM_STRONG_NEGATIVE = -5.0

# Momentum-based boost values
BOOST_AGGRESSIVE_STRONG_MOMENTUM = 1.3        # +30% aggressive when strong momentum
BOOST_PRESSURE_MOMENTUM = 1.2                 # +20% pressure when positive momentum
BOOST_PIVOT_NEGATIVE_MOMENTUM = 1.2           # +20% pivots when negative momentum
BOOST_HIGHRISK_DESPERATE = 1.3                # +30% high-risk when very behind

# =============================================================================
# PHASE PROACTIVE: SETUP SWEEP PREVENTION (2026-02-08)
# =============================================================================
# Penalties for staying in against boosted threats
PENALTY_STAY_VS_SETUP_SWEEPER = 0.15         # 85% penalty for non-switch vs setup threat
PENALTY_PASSIVE_VS_BOOSTED = 0.25            # 75% penalty for passive moves vs boosted opponent
BOOST_SWITCH_VS_BOOSTED = 1.6                # +60% boost for switching vs boosted opponent
BOOST_PHAZE_VS_BOOSTED = 1.8                 # +80% boost for phazing boosted opponent
BOOST_REVENGE_VS_BOOSTED = 1.5               # +50% boost for priority/revenge vs boosted opponent

# Aggressive moves (benefit from momentum)
AGGRESSIVE_MOVES = {
    "swordsdance",
    "nastyplot",
    "dragondance",
    "quiverdance",
    "shellsmash",
    "bellydrum",
    "bulkup",
    "calmmind",
    "agility",
}


# =============================================================================
# PHASE 4.1: ENDGAME SOLVER
# =============================================================================

# Endgame thresholds
ENDGAME_MAX_POKEMON = 3  # Consider endgame when both sides have <= 3 Pokemon
ENDGAME_SOLVE_DEPTH = 4  # Max depth for minimax search
