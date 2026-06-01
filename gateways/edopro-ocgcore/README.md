# EDOPro ocgcore gateway

JSON-lines bridge between Python training code and `ocgcore-wasm`.

## Lua prelude (required)

Card scripts call globals such as `GetID()` defined in EDOPro core Lua files. The gateway **must** load these before any duel:

- `script/constant.lua`
- `script/utility.lua`

If either file is missing, duel startup fails fast with a clear error. Bootstrap with:

```bash
bash scripts/bootstrap_edopro_home.sh /path/to/edopro-home
```

Or point `--edopro-home` at a full ProjectIgnis install (`C:/ProjectIgnis`).

## Smoke test

```bash
cd gateways/edopro-ocgcore
npm run smoke -- C:/ProjectIgnis vanilla
npm run smoke -- C:/ProjectIgnis frog
npm run smoke-pack -- ../../.ygotrain/edopro-home
```

`smoke-pack-duel.mjs` runs a short duel with real format-pack decks (Kashtira vs Primite by default).

A healthy run reports `"runtime_errors": 0` and `"prelude_loaded": ["constant.lua", "utility.lua"]` in `script_stats`.

## Training quality flags

From Python (`run-yearly-bracket`):

- LP-only endings: default on
- Clean scripts: default on (use `--allow-script-errors` to disable)
- `--max-decisions` is enforced in the gateway
