# Deploy YGO Training Console to Fly.io

The dashboard and all training artifacts (jobs, policies, game logs, EDOPro cache) live on a **Fly volume** mounted at `/data`.

## What gets stored on the volume

| Path on volume | Contents |
|----------------|----------|
| `/data/jobs/` | Job metadata, logs, reports |
| `/data/bots/` | Per-bot learned policies |
| `/data/learned-policy.json` | Global learned weights |
| `/data/custom-decks/` | Imported `.ydk` decks |
| `/data/human-duels/` | Human replay catalog |
| `/data/cards.json` | Card name cache for deck UI |
| `/data/bracket/` | Yearly bracket / loop outputs |
| `/data/edopro-home/` | EDOPro scripts + `cards.cdb` (bootstrapped once) |
| `/data/edopro-build/` | Git clone cache for bootstrap script |

Application code and format packs ship in the Docker image; only runtime data uses the volume.

## Prerequisites

- [Fly CLI](https://fly.io/docs/hands-on/install-flyctl/) installed and logged in (`fly auth login`)
- Fly account with billing enabled (volumes require a paid plan)

## First-time deploy

```powershell
# From repo root — pick a unique app name
fly apps create ygotrainingbot-yourname

# Edit fly.toml: set app = "ygotrainingbot-yourname"

# Create the persistent volume (once per app, same region as fly.toml)
fly volumes create ygotrain_data --size 10 --region iad

# Deploy
fly deploy
```

Open the app:

```powershell
fly open
```

## Environment variables

| Variable | Default on Fly | Purpose |
|----------|----------------|---------|
| `YGOTRAIN_DATA_DIR` | `/data` | Root for all persistent training data |
| `PORT` | `8765` | HTTP port (set by Fly) |
| `YGOTRAIN_EDOPRO_BUILD_DIR` | `/data/edopro-build` | Cache EDOPro git clones across restarts |

Local dev is unchanged unless you set `YGOTRAIN_DATA_DIR` — then jobs/policies use that directory instead of `.ygotrain/`.

## Migrate existing local data

From your machine (adjust paths):

```powershell
# Example: sync .ygotrain and data/ to the Fly volume via SFTP
fly ssh sftp shell

# Then on the SFTP prompt, upload into /data:
# put -r .ygotrain/jobs /data/jobs
# put -r .ygotrain/bots /data/bots
# put .ygotrain/learned-policy.json /data/learned-policy.json
# put -r .ygotrain/edopro-home /data/edopro-home
# put -r data/human-duels /data/human-duels
```

Or use `fly ssh console` and `tar`/`rsync` if you prefer.

## Scaling and ops notes

- **Memory**: `fly.toml` requests 2 GB RAM. WASM duels are CPU-heavy; bump to 4 GB if jobs OOM.
- **First job** after a fresh deploy bootstraps EDOPro (~5–10 min) unless `/data/edopro-home/cards.cdb` already exists from a prior run or migration.
- **No auth** on the dashboard API today. Do not expose publicly without adding auth (Fly SSO, basic token, or private networking via `flycast`).
- **Concurrent jobs** run in background threads; avoid starting many heavy jobs at once on a single machine.

## Useful commands

```powershell
fly logs              # tail dashboard / job output
fly ssh console       # shell on the machine
fly volumes list      # check volume attachment
fly scale memory 4096 # increase RAM
```

## Local vs Fly layout

| Local (default) | Fly (`YGOTRAIN_DATA_DIR=/data`) |
|-----------------|----------------------------------|
| `.ygotrain/jobs` | `/data/jobs` |
| `.ygotrain/bots` | `/data/bots` |
| `data/human-duels` | `/data/human-duels` |
| `data/dashboard-bracket-*` | `/data/bracket/dashboard-bracket-*` |
