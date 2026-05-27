# Training a format from iPhone

You can start format training without a local computer by using GitHub Actions
from Safari or the GitHub mobile app.

## Run the default starter format

1. Open the repository on GitHub.
2. Tap **Actions**.
3. Tap **Train Yu-Gi-Oh Format**.
4. Tap **Run workflow**.
5. Leave `format_pack` as `configs/format-packs/goat-2005.json` or switch it to `configs/format-packs/edison-2010.json`.
6. Set `games_per_matchup` and `max_decisions`.
7. Tap **Run workflow**.

When it finishes, open the workflow run and download the
`format-training-report` artifact. That JSON file contains the number of games,
traced decisions, win/draw counts, and action tags the bot saw.

## What a format pack contains

Format packs live in `configs/format-packs/*.json` and include banlist metadata plus multiple decks. Single-match configs still live in `configs/formats/*.json`:

```json
{
  "name": "goat-2005",
  "games": 25,
  "max_decisions": 60,
  "banlist": {
    "forbidden": [],
    "limited": [],
    "semi_limited": []
  },
  "decks": [
    {
      "name": "Deck name and event finish",
      "archetype": "Goat Control",
      "source": "Tournament or archive source",
      "main": [1184620, 1184620]
    }
  ]
}
```

Each deck `main` list must contain at least 40 EDOPro card IDs. The workflow
bootstraps EDOPro-compatible scripts and databases, starts the included ocgcore
gateway, then runs every deck against every other deck in the pack.

## Expanding to a real format

To train a real historical or current format, add another JSON file under
`configs/formats/` with representative deck lists for that format. Examples:

- Edison
- Goat
- current Advanced
- a single archetype mirror
- a sealed pool from one set

The next useful automation is a deck generator that converts set-mining output
into multiple format packs automatically, plus banlist enforcement before each
training run.

Current limitation: the default baseline agent uses conservative phase actions first so historical decks run reliably while richer response handlers are added for complex combo lines.
