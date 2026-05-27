# EDOPro integration

EDOPro is the right rules engine family for this project, but the public EDOPro
client is primarily a graphical application. The bot connects through a
headless gateway process that uses EDOPro/ocgcore-compatible data and streams
legal decision points over JSON lines.

Bootstrap an EDOPro-compatible data directory with:

```bash
scripts/bootstrap_edopro_home.sh /tmp/ygotrain/edopro-home
```

## Validate a local EDOPro directory

Point the bot at an EDOPro install or extracted data directory:

```bash
export EDOPRO_HOME=/path/to/ProjectIgnis
python3 -m ygotrainingbot.cli check-edopro
```

Or pass paths directly:

```bash
python3 -m ygotrainingbot.cli check-edopro --root /path/to/ProjectIgnis
```

The validator checks for the standard script/deck directories and a card
database source such as `cards.cdb` or `expansions/`.

## Headless gateway protocol

The bot starts a gateway command and sends:

```json
{"type":"start_duel","players":["bot-a","bot-b"]}
```

The gateway responds with legal decision states:

```json
{
  "type": "state",
  "state": {
    "state_id": "turn-1-main-open",
    "turn": 1,
    "active_player": "bot-a",
    "summary": "Main Phase 1 open game state",
    "legal_actions": [
      {"action_id": "normal-summon-123", "label": "Normal Summon Aleister"}
    ],
    "public_zones": {
      "bot-a.field": [],
      "bot-b.field": ["set-card"]
    }
  }
}
```

The bot chooses an action with its configured agent and replies:

```json
{"type":"action","state_id":"turn-1-main-open","agent":"bot-a","action_id":"normal-summon-123"}
```

When the duel is over, the gateway sends:

```json
{"type":"result","winner":"bot-a","loser":"bot-b","turns":4,"tags":["edopro"]}
```

Run one connected duel with the included ocgcore WASM gateway:

```bash
cd gateways/edopro-ocgcore
npm install
cd ../..
python3 -m ygotrainingbot.cli edopro-play-once \
  --gateway-command "node gateways/edopro-ocgcore/gateway.mjs --edopro-home /path/to/edopro-home"
```

Run repeated gameplay training and save a report:

```bash
python3 -m ygotrainingbot.cli edopro-train \
  --gateway-command "node gateways/edopro-ocgcore/gateway.mjs --edopro-home /path/to/edopro-home --max-decisions 40" \
  --games 25 \
  --output data/edopro-training-report.json
```

## What the gateway must own

The gateway should be responsible for:

- loading EDOPro card databases, scripts, banlists, and decks,
- creating duel rooms or core instances,
- exposing every legal action at each decision point,
- applying the selected action back to the engine,
- emitting final results and enough state metadata for trace analysis.

The Python bot remains responsible for:

- choosing actions,
- storing traces,
- training policies,
- generating coach feedback,
- benchmarking agent versions.

## Quick local bootstrap used by this agent

For the current smoke run, the EDOPro resources were assembled under `/tmp/ygotrain/edopro-home` from public script/database repositories, then exercised with:

```bash
python3 -m ygotrainingbot.cli edopro-train \
  --gateway-command "node gateways/edopro-ocgcore/gateway.mjs --edopro-home /tmp/ygotrain/edopro-home --max-decisions 20" \
  --games 5 \
  --output /tmp/ygotrain/edopro-training-report.json
```

The included gateway currently uses simple normal-monster starter decks by default. The next improvement is passing generated deck lists from the static set miner into the gateway.
