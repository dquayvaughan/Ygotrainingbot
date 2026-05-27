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

See [docs/architecture.md](docs/architecture.md) for the current system design and [docs/training-roadmap.md](docs/training-roadmap.md) for the path from immediate static training to full self-play.

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

`fetch-cards` refreshes the local cache from the current public card database. `train-static` groups those cards by set and mines archetypes, effect signals, and likely interaction candidates that later simulator runs should verify.

## Current status

The code in `src/ygotrainingbot` is an initial domain scaffold, not a complete
duel engine. The next major milestone is connecting a real Yu-Gi-Oh! simulator
or replay source behind the `DuelSimulator` protocol.
