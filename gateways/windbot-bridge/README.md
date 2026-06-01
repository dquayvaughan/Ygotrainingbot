# WindBot bridge for Ygotrainingbot

EDOPro **Local AI** and **server rooms** spawn [WindBot Ignite](https://github.com/ProjectIgnis/windbot),
which speaks the YGOPro room protocol—not the JSON `gateway.mjs` protocol used for training.

This folder is for a **WindBot executor** that forwards each bot decision to
`ygotrain edopro-bot-serve` (Python HTTP API on port **8765**).

## Build (Windows)

1. Clone [ProjectIgnis/windbot](https://github.com/ProjectIgnis/windbot) or use the
   WindBot-Ignite copy shipped with your EDOPro install.
2. Add `YgotrainingbotExecutor.cs` from this folder into `Game/AI/Decks/` (or build
   as a SampleExecutor-style DLL if you use ExecutorBase loading).
3. Add your bot deck as `Decks/AI_<DeckName>.ydk` inside the WindBot project.
4. Register in `bots.json`:

   ```json
   {
     "name": "Ygotrainingbot",
     "deck": "Ygotrainingbot",
     "difficulty": 3,
     "masterRules": [4, 5]
   }
   ```

5. Build Release, copy outputs next to EDOPro’s WindBot binaries (or replace the
   executor DLL in the `executors` folder if using dynamic loading).

## Runtime

Terminal 1 — policy server:

```powershell
$env:PYTHONPATH = "src"
python -m ygotrainingbot.cli edopro-bot-serve --policy data/learned-policy.json --learn-after-duel
```

Terminal 2 — EDOPro: host a room, **Add AI player** → **Ygotrainingbot**.

Or manual WindBot:

```powershell
WindBot.exe Name=Ygotrainingbot Deck=Ygotrainingbot Host=127.0.0.1 Port=7911
```

## HTTP contract

See `docs/edopro-play-vs-bot.md`. The executor must:

1. `POST /v1/start` when the duel begins (human name, format, deck meta).
2. `POST /v1/decide` whenever WindBot needs a choice—serialize legal options as
   `{action_id, label, tags}`.
3. `POST /v1/finish` when the duel ends (`winner`, `loser`, `turns`).

`YgotrainingbotExecutor.cs` in this folder is a **starter template** showing HTTP calls;
you still need to map WindBot callbacks (`OnSelectCard`, chain prompts, etc.) to
`legal_actions` lists.

## Interim without building C#

Use EDOPro’s **Feelin’ Lucky** deck engine with your `.ydk` in the deck dropdown.
The bot will not use learned weights. After the duel, transcribe key decisions into
JSON and upload via dashboard **Human replays**, or play via the headless gateway
(`edopro-play-once` with a human stdin agent—planned CLI).
