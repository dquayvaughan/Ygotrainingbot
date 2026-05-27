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

See [docs/architecture.md](docs/architecture.md) for the current system design.

## Development

This repository uses Python for the first implementation pass.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
pytest
```

## Current status

The code in `src/ygotrainingbot` is an initial domain scaffold, not a complete
duel engine. The next major milestone is connecting a real Yu-Gi-Oh! simulator
or replay source behind the `DuelSimulator` protocol.
