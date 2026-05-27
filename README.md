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

See [docs/architecture.md](docs/architecture.md) for the current system design, [docs/training-roadmap.md](docs/training-roadmap.md) for the path from immediate static training to full self-play, and [docs/edopro-integration.md](docs/edopro-integration.md) for connecting an EDOPro-core-compatible headless runner.

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

Validate an EDOPro install or data directory with:

```bash
python3 -m ygotrainingbot.cli check-edopro --root /path/to/ProjectIgnis
```

Run one connected duel through an EDOPro-core-compatible JSON-lines gateway with:

```bash
python3 -m ygotrainingbot.cli edopro-play-once \
  --gateway-command "node /path/to/edopro-gateway.js"
```

## Current status

The code in `src/ygotrainingbot` now includes the `DuelSimulator` boundary plus an EDOPro JSON-lines gateway adapter. A production gateway still needs to wrap an EDOPro-core-compatible headless runner and expose legal actions to the bot.
