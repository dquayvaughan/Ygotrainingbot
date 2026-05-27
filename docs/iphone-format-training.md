# Training a format from iPhone

You can start format training without a local computer by using GitHub Actions
from Safari or the GitHub mobile app.

## Run the default starter format

1. Open the repository on GitHub.
2. Tap **Actions**.
3. Tap **Train Yu-Gi-Oh Format**.
4. Tap **Run workflow**.
5. Leave `format_config` as `configs/formats/starter-normal.json`.
6. Set `games` and `max_decisions`.
7. Tap **Run workflow**.

When it finishes, open the workflow run and download the
`format-training-report` artifact. That JSON file contains the number of games,
traced decisions, win/draw counts, and action tags the bot saw.

## What a format config contains

Format configs live in `configs/formats/*.json`:

```json
{
  "name": "starter-normal",
  "description": "Short explanation of what this format trains.",
  "games": 25,
  "max_decisions": 40,
  "deck_a": [1184620, 1184620],
  "deck_b": [3134241, 3134241]
}
```

`deck_a` and `deck_b` must each contain at least 40 EDOPro card IDs. The
workflow bootstraps EDOPro-compatible scripts and databases, starts the included
ocgcore gateway, then runs those decks against each other.

## Expanding to a real format

To train a real historical or current format, add another JSON file under
`configs/formats/` with representative deck lists for that format. Examples:

- Edison
- Goat
- current Advanced
- a single archetype mirror
- a sealed pool from one set

The next useful automation is a deck generator that converts set-mining output
into multiple format configs automatically.
