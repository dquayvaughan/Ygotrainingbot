# Training roadmap

This project can start learning immediately from current card data, but there
are two different levels of "training" to keep separate.

## Immediate training: static card and set mining

The first training pass does not need a duel engine. It downloads current card
metadata, groups cards by set, and mines each set for:

- common effect signals such as search, negate, graveyard, banish, chain, and
  special summon,
- archetype density,
- likely card-pair interaction candidates that deserve simulator verification.

Run it with:

```bash
ygotrain fetch-cards --cache data/cards.json
ygotrain train-static --cache data/cards.json --json
```

This lets the bot grow with new releases because every fresh `fetch-cards` run
uses the latest card database snapshot available from the upstream source.

## Next additions for real self-play

Static training can tell the bot where to look. To become a chess-bot-like
opponent, the system still needs these pieces:

1. **Rules-complete simulator adapter**
   - Implement `DuelSimulator` against an existing Yu-Gi-Oh! engine or replay
     runner.
   - Preserve chain links, timing windows, public/private zones, and legal
     actions at every decision point.
2. **Format snapshots**
   - Store banlists, rules revisions, and card database snapshots by date.
   - Tie every experiment to a specific snapshot so old discoveries remain
     reproducible.
3. **Deck/scenario generation**
   - Build sealed, draft, archetype, meta, rogue, and custom scenario decks from
     each set or release window.
   - Generate focused puzzles from mined interaction candidates.
4. **Trace storage**
   - Persist every decision, legal action list, outcome, and simulator version.
   - Make traces queryable by set, card, archetype, matchup, and mistake type.
5. **Learning policies**
   - Start with scripted baselines and imitation from replay logs.
   - Add self-play/search policies once simulator throughput is reliable.
6. **Evaluation**
   - Track win rate, blunder rate, exploitability against fixed baselines, and
     coaching accuracy on held-out replays.
7. **Coach UX**
   - Turn traces into "why this mattered" explanations with replay snippets,
     alternative lines, and confidence scores.

## Release coverage strategy

To grow up to current Yu-Gi-Oh! releases, each batch should be pinned to:

- card database cache,
- set list and release grouping,
- banlist,
- simulator/rules version,
- agent version,
- generated deck/scenario seed.

That gives the project a continuous ladder: mine the latest release statically,
generate scenarios from the mined signals, verify them in simulation, train
agents on the resulting traces, and promote only improvements that beat fixed
benchmarks.
