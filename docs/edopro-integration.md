# EDOPro integration

EDOPro is the right rules engine family for this project, but the public EDOPro
client is primarily a graphical application. The bot connects through a
headless gateway process that uses EDOPro/ocgcore-compatible data and streams
legal decision points over JSON lines.

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

Run one connected duel with:

```bash
python3 -m ygotrainingbot.cli edopro-play-once \
  --gateway-command "node /path/to/edopro-gateway.js"
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
