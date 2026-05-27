# Ygotrainingbot

Ygotrainingbot is the foundation for a Yu-Gi-Oh! learning bot: an engine-aware
opponent and coach that studies the game by repeatedly playing through card
sets, surfacing niche interactions, and turning those discoveries into training
feedback for players.

The project starts with a deliberately small core:

- **Set exploration**: model sealed/product-like card pools and run repeatable
  experiments across them.
- **Simulation hooks**: keep the duel engine boundary explicit so we can plug in
  a rules-complete simulator without coupling learning code to one backend.
- **Agent interfaces**: define how opponents choose actions, record outcomes,
  and improve from match history.
- **Coaching outputs**: convert game traces into actionable notes such as
  misplays, alternative lines, and matchup-specific interaction warnings.

See [docs/architecture.md](docs/architecture.md) for the current system design, [docs/training-roadmap.md](docs/training-roadmap.md) for the path from immediate static training to full self-play, [docs/edopro-integration.md](docs/edopro-integration.md) for connecting an EDOPro-core-compatible headless runner, and [docs/iphone-format-training.md](docs/iphone-format-training.md) for launching format training from an iPhone, and [docs/format-packs.md](docs/format-packs.md) for banlists and topping deck-list packs, and [docs/dashboard.md](docs/dashboard.md) for the web dashboard.

## Development

This repository uses Python for the first implementation pass.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
pytest
```

Start the current-card static training loop with:

```bash
ygotrain fetch-cards --cache data/cards.json
ygotrain train-static --cache data/cards.json
```

If the console script is not on `PATH`, use `python3 -m ygotrainingbot.cli` with the same subcommands.

`fetch-cards` refreshes the local cache from the current public card database. `train-static` groups those cards by set and mines archetypes, effect signals, and likely interaction candidates that later simulator runs should verify.

Bootstrap an EDOPro-compatible data directory with:

```bash
scripts/bootstrap_edopro_home.sh /tmp/ygotrain/edopro-home
```

Validate an EDOPro install or data directory with:

```bash
python3 -m ygotrainingbot.cli check-edopro --root /path/to/ProjectIgnis
```

Run one connected duel through the included ocgcore WASM gateway with:

```bash
cd gateways/edopro-ocgcore
npm install
cd ../..
python3 -m ygotrainingbot.cli edopro-play-once \
  --gateway-command "node gateways/edopro-ocgcore/gateway.mjs --edopro-home /path/to/edopro-home"
```

Run repeated gameplay training with:

```bash
python3 -m ygotrainingbot.cli edopro-train \
  --gateway-command "node gateways/edopro-ocgcore/gateway.mjs --edopro-home /path/to/edopro-home --max-decisions 40" \
  --games 25 \
  --output data/edopro-training-report.json
```

Train a named format config with:

```bash
python3 -m ygotrainingbot.cli train-format \
  --config configs/formats/starter-normal.json \
  --edopro-home /path/to/edopro-home \
  --games 25 \
  --max-decisions 40 \
  --output data/format-training-report.json
```

If you only have an iPhone, use the **Train Yu-Gi-Oh Format** GitHub Actions workflow and download the `format-training-report` artifact when the run finishes.

## Current status

The code in `src/ygotrainingbot` now includes the `DuelSimulator` boundary plus an EDOPro JSON-lines gateway adapter. A production gateway still needs to wrap an EDOPro-core-compatible headless runner and expose legal actions to the bot.

## Format packs

Train all deck matchups in a format pack with:

```bash
python3 -m ygotrainingbot.cli train-format-pack \
  --pack configs/format-packs/goat-2005.json \
  --edopro-home /path/to/edopro-home \
  --games-per-matchup 5 \
  --max-decisions 60 \
  --output data/goat-training-report.json
```

Initial packs include Goat 2005 and Edison 2010 representative topping-style deck shells with banlist metadata.

## Web dashboard

Start the mobile-friendly dashboard with:

```bash
ygotrain-dashboard --host 0.0.0.0 --port 8765
```

Use the forwarded/public URL from your iPhone to launch jobs, watch logs, and open training reports.
