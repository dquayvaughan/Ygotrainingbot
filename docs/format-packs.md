# Format packs, banlists, and topping deck lists

Format packs live in `configs/format-packs/*.json`. They are designed for
training a bot against multiple representative or topping-style deck lists from
the same format.

A pack contains:

- format name and description,
- default games and decision cap,
- banlist metadata (`forbidden`, `limited`, `semi_limited` card IDs),
- one or more named decks with EDOPro card IDs.

Current starter packs:

- `configs/format-packs/goat-2005.json`
- `configs/format-packs/edison-2010.json`

The included banlists are metadata for analysis and reporting. The gateway does
not enforce banlist legality yet; deck validation/enforcement is the next step.

## Train a whole format pack

```bash
scripts/bootstrap_edopro_home.sh /tmp/ygotrain/edopro-home
npm ci --prefix gateways/edopro-ocgcore
python3 -m ygotrainingbot.cli train-format-pack \
  --pack configs/format-packs/goat-2005.json \
  --edopro-home /tmp/ygotrain/edopro-home \
  --games-per-matchup 5 \
  --max-decisions 60 \
  --output data/goat-training-report.json
```

The trainer runs every deck into every other deck, including mirrors. The report
contains per-matchup stats plus aggregate tags for actions like normal summon,
attack, chain, zone selection, and phase movement.

## Adding more topping deck lists

Add another deck object to a format pack:

```json
{
  "name": "Deck name and event finish",
  "archetype": "Archetype",
  "source": "Where this list came from",
  "main": [1184620, 1184620]
}
```

`main` must contain at least 40 EDOPro card IDs. Side and extra decks are not
used by the gateway yet, but the schema can be extended for them when side-deck
and extra-deck decisions are implemented.

Current limitation: format-pack smoke training prioritizes safe phase actions for the baseline agent. The legal action list still includes summons, attacks, activations, and other choices, but smarter agents are needed before the bot should aggressively play complex topping decks.
