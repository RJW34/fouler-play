# Fouler-Play ![umbreon](https://play.pokemonshowdown.com/sprites/xyani/umbreon.gif)

A smarter Pokemon battle bot - a fork of [foul-play](https://github.com/pmariglia/foul-play) with enhanced decision-making.

Fouler-Play can play single battles in all generations, though dynamax and z-moves are not currently supported.

## What's Different?

This fork adds an **ability-aware penalty system** that prevents the bot from making obviously bad decisions that the original bot's MCTS engine doesn't handle correctly.

### Problems Fixed

| Scenario | Original Bot | Fouler-Play |
|----------|--------------|-------------|
| Facing Dondozo (Unaware) | Uses Swords Dance | Avoids stat-boosting moves |
| Facing Gliscor (Poison Heal) | Uses Toxic | Avoids poison moves |
| Facing Conkeldurr (Guts) | Uses Will-O-Wisp | Avoids status moves |
| Facing Heatran (Flash Fire) | Uses Flamethrower | Avoids Fire moves |
| Facing Hatterene (Magic Bounce) | Uses Stealth Rock | Avoids reflected moves |
| Facing Kingambit (Defiant) | Uses Icy Wind | Avoids stat-lowering moves |
| Facing Rotom-Wash (Levitate) | Uses Earthquake | Avoids Ground moves |
| Facing Vaporeon (Water Absorb) | Uses Surf | Avoids Water moves |

See [CLAUDE.md](CLAUDE.md) for full technical details and remaining work items.

## Python version

Requires Python 3.11+.

## Getting Started

### Configuration

Command-line arguments are used to configure the bot.

Use `python run.py --help` to see all options.

### Running Locally

**1. Clone**

```bash
git clone https://github.com/YOUR_USERNAME/fouler-play.git
```

**2. Install Requirements**

```bash
pip install -r requirements.txt
```

Note: Requires Rust to be installed on your machine to build the poke-engine.

**3. Run**

```bash
python run.py \
  --websocket-uri wss://sim3.psim.us/showdown/websocket \
  --ps-username 'My Username' \
  --ps-password sekret \
  --bot-mode search_ladder \
  --pokemon-format gen9randombattle
```

### Running with Docker

**1. Build the Docker image**

```shell
make docker
```

or for a specific generation:

```shell
make docker GEN=gen4
```

**2. Run the Docker Image**

```bash
docker run --rm --network host fouler-play:latest \
  --websocket-uri wss://sim3.psim.us/showdown/websocket \
  --ps-username 'My Username' \
  --ps-password sekret \
  --bot-mode search_ladder \
  --pokemon-format gen9randombattle
```

## Engine

This project uses [poke-engine](https://github.com/pmariglia/poke-engine) to search through battles.
See [the engine docs](https://poke-engine.readthedocs.io/en/latest/) for more information.

The engine must be built from source, so you must have Rust installed on your machine.

### Re-Installing the Engine

It is common to want to re-install the engine for different generations of Pokemon.

`pip` will use cached .whl artifacts when installing packages and cannot detect the `--config-settings` flag that was used to build the engine.

The following command will ensure that the engine is re-installed properly:

```shell
pip uninstall -y poke-engine && pip install -v --force-reinstall --no-cache-dir poke-engine --config-settings="build-args=--features poke-engine/<GENERATION> --no-default-features"
```

Or using the Makefile:

```shell
make poke_engine GEN=<generation>
```

For example, to re-install the engine for generation 4:

```shell
make poke_engine GEN=gen4
```

## Credits

- Original project: [pmariglia/foul-play](https://github.com/pmariglia/foul-play)
- Battle engine: [pmariglia/poke-engine](https://github.com/pmariglia/poke-engine)
