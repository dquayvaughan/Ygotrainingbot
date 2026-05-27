# Training dashboard

The dashboard is a mobile-friendly web app for starting and monitoring training
runs without editing commands by hand.

## Start it

From a machine or cloud environment where the repo is checked out:

```bash
python3 -m pip install -e ".[dev]"
ygotrain-dashboard --host 0.0.0.0 --port 8765
```

If the console script is not on `PATH`, use:

```bash
python3 -m ygotrainingbot.dashboard --host 0.0.0.0 --port 8765
```

Open the forwarded/public URL from your iPhone. The dashboard lets you:

- pick a format pack,
- choose games per matchup,
- choose max decisions per game,
- start a background training job,
- view logs,
- open the JSON report when the job completes.

## What happens when a job starts

The dashboard stores job data under `.ygotrain/jobs/`. If needed, it will:

1. install the Node gateway dependencies with `npm ci`,
2. bootstrap EDOPro-compatible data under `/tmp/ygotrain/edopro-home`,
3. run `python3 -m ygotrainingbot.cli train-format-pack`,
4. write logs and a report for the dashboard to display.

## iPhone access options

The dashboard needs to be running somewhere reachable by your phone:

- a cloud dev environment with port forwarding,
- a home server on your Wi-Fi,
- a small VPS,
- a tunneled local machine.

If you do not have a reachable machine, use the GitHub Actions workflow instead:
GitHub repo -> **Actions** -> **Train Yu-Gi-Oh Format** -> **Run workflow**.
