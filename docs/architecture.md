# Ygotrainingbot architecture

## Product goal

Build a Yu-Gi-Oh! coach/opponent that learns by repeatedly playing through
individual sets and card pools. The system should discover strong lines, niche
interactions, and common mistakes, then expose those findings as training
matches and coaching feedback.

## Core loop

1. **Load a card pool** from a set, format, or custom experiment definition.
2. **Generate decks or scenarios** for the pool being studied.
3. **Play many duels** through a rules-complete simulator.
4. **Record traces** containing decisions, visible game state, outcomes, and
   notable interactions.
5. **Train agents** from those traces so future opponents improve.
6. **Distill coaching notes** that explain what mattered and how a player could
   choose better lines.

## System boundaries

```text
Card/set data -> Experiment planner -> Duel simulator -> Trace store
                                      -> Agent trainer -> Coach insights
```

### Card and set data

The project should use structured card metadata rather than scraped text. Early
sources can include public card databases, curated fixtures, and simulator deck
exports. Long term, every experiment should be reproducible from a pinned set
definition plus a rules/banlist snapshot.

### Duel simulator

The learning system depends on a small simulator protocol instead of embedding
rules logic directly. Yu-Gi-Oh! has too many timing, chain, and card-specific
interactions for an ad hoc engine. The first concrete backend boundary is an
EDOPro-core-compatible JSON-lines gateway behind `DuelSimulator`.

### Agents

Agents receive a visible game state and return a legal action. The first useful
agents can be deterministic baselines and scripted policies. More advanced
versions can train from self-play, replay imitation, or search over simulator
rollouts.

### Coaching

Coaching is derived from traces rather than raw wins and losses. A coach note
should reference:

- the scenario where the decision happened,
- the action that was taken,
- one or more better alternatives when available,
- why the alternative matters,
- the confidence or evidence behind the recommendation.

## Near-term milestones

1. Create typed domain models for cards, sets, actions, game states, match
   results, and coaching recommendations.
2. Add a simple deterministic simulator fixture so the learning pipeline can be
   tested before a full duel engine exists.
3. Integrate current card data ingestion and static set mining.
4. Connect a production EDOPro-core-compatible simulator gateway or replay parser.
5. Add batch experiments for set-by-set exploration and trace analysis.
6. Persist training traces and benchmark agent versions against fixed baselines.
