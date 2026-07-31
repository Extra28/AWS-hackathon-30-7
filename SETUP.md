# Setup and run

Working prototype for the UOB cash replenishment challenge. Python + SQLite
backend, HTML dashboard, ML-driven scheduling.

## 1. Install

Your Python 3.12 is reachable as `py` (plain `python` hits the Microsoft Store
stub and will fail).

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Dependencies (pinned in `requirements.txt`):

```
numpy==1.26.4
pandas==2.2.2
scikit-learn==1.5.1
Flask==3.0.3
```

SQLite needs nothing extra; it ships with Python.

Verify:

```powershell
.\.venv\Scripts\python.exe -c "import numpy,pandas,sklearn,flask;print('ok')"
```

## 2. Build the database and plan

```powershell
.\.venv\Scripts\python.exe run_pipeline.py
```

This runs six stages and prints progress:

1. create the SQLite schema
2. generate 350 ATMs, demand history, hourly telemetry, fault events
3. train the quantile demand model and score it against a seasonal-naive baseline
4. compute refill windows and build the schedule
5. validate the schedule against every constraint
6. simulate the planner against two baseline policies and print a comparison table

Takes roughly one to three minutes. Add `--skip-sim` to stop after planning,
or `--seed 7` for a different random draw.

## 3. Start the dashboard

```powershell
.\.venv\Scripts\python.exe app.py
```

Open <http://127.0.0.1:5000>.

Six tabs: **Day plan** (visits grouped by priority window, with a replan panel),
**Fleet risk** (all 350 ATMs by urgency), **Benchmark** (policy comparison),
**Model & checks** (forecast accuracy and constraint checks), **Cartridge advice**,
and **Ask** (plain-language queries over the plan). Click any ATM id to open a
detail drawer with its balance history, forecast, and projected trajectory
against the 55% and 25% levels.

## How the pieces fit

```
config.json          all tunable parameters
run_pipeline.py      orchestration
app.py               Flask API + static file serving
src/schema.sql       SQLite DDL
src/db.py            connection helpers
src/config.py        config load + bounds validation
src/generate.py      fleet, demand, telemetry, faults      (Req 1-4)
src/forecast.py      quantile ML + walk-forward backtest    (Req 5)
src/scheduler.py     refill windows, schedule, loads        (Req 6,7,8,10)
src/simulate.py      ad hoc trips, baselines, metrics, validation (Req 9,11,12,13)
web/                 dashboard, no build step, no CDN
```

## Where the AI sits

The ML model is the decision driver. A pooled gradient-boosting model
(`HistGradientBoostingRegressor` with quantile loss) forecasts each ATM's daily
demand at the 50th, 90th and 95th percentiles. The 90th percentile forecast is
what determines every refill window: when an ATM will cross 55% (window opens)
and when it will cross 25% (window closes). Those windows determine what gets
scheduled, on which day, and in which priority band.

A deterministic layer then assembles the schedule from those windows. That split
is deliberate: the forecast is genuinely predictive and carries the intelligence,
while the assembly step is auditable and reproducible. A language model could
generate a plan directly but could not be held to a trip cap or a band split, and
you would lose the ability to explain any single decision from stored numbers.

Every scheduling decision is written to SQL with a plain-language reason, which is
what the dashboard and the Ask tab read back.

## Prototype scope, honestly stated

Deliberate simplifications, all reversible via `config.json`:

- **180 days of history, 30 days of hourly telemetry, 30-day simulation.** The
  spec asks for 24 months; that is roughly 6.1M telemetry rows and a database
  near a gigabyte. Raise `history_days`, `telemetry_days` and `sim_days` to scale
  up. Note the demand history is stored daily with intraday shares, while
  telemetry is hourly for the recent window only.
- **Constraints are best-effort, not guaranteed.** The scheduler is a greedy
  construction with a load-relief pass, not an exact solver. `src/simulate.py`
  re-reads the stored schedule and reports every constraint independently, so any
  deviation is visible on the Model & checks tab rather than hidden. Expect the
  priority-mix check to flag days where visits were promoted to P0 for cash
  safety, which is the intended tradeoff.
- **The Ask tab is rule-based**, not an LLM. Answers are read from SQL so they are
  always traceable. `api_ask` in `app.py` is where a hosted model would slot in.
- **Singapore holiday dates are approximate** for the lunar and Islamic
  festivals. They only need to be plausible for synthetic data.
- **No authentication.** `app.py` binds to `127.0.0.1` and is a local dashboard.
  Do not expose it on a network without adding auth.

## AWS mapping, if you take it further

S3 for history and model artifacts, SageMaker for training, Step Functions plus
Lambda for the nightly ingest to forecast to optimise to publish pipeline,
EventBridge Scheduler as the trigger, DynamoDB for live ATM state, Bedrock for
the explanation layer, Amplify plus API Gateway for the dashboard.

Do not reach for Amazon Forecast: AWS
[closed it to new customers on 29 July 2024](https://aws.amazon.com/blogs/machine-learning/transition-your-amazon-forecast-usage-to-amazon-sagemaker-canvas/)
and is not adding features, so a fresh hackathon account cannot provision it.
SageMaker, or SageMaker Canvas for a no-code path, is the replacement.
*Content was rephrased for compliance with licensing restrictions.*
