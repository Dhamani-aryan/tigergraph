# Running the Task 14 batch (for a teammate with their own Groq key)

**Why you're doing this:** Naman's Groq account hit its daily token quota
(200,000 tokens/day, shared across the whole org — rotating API keys on the
*same* Groq account doesn't reset it) partway through generating the 20
answer files. The code is done and two real calibration bugs were found and
fixed today; it just needs a account with quota to finish the run.

## 1. Get the code

```bash
git clone https://github.com/Namans12/tigergraph.git
cd tigergraph
git checkout tigergraph-fraud-agent
git pull
```

Confirm you're on commit `7dbca76` or later (`git log --oneline -1`).

## 2. Get the dataset

`transactions.csv`, `identity.csv`, `closed_cases_history.csv`, `case_pack.csv`
are gitignored (too large / benchmark integrity) — you already have these
from the hackathon dataset download. Place all four **directly in the repo
root** (`tigergraph/`, the folder you just cloned — not inside any
subfolder).

## 3. Set up Python

Python 3.10 or 3.11. From the repo root:

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Mac/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

## 4. Configure `.env`

Copy `.env.example` to `.env` in the repo root and fill in:

- `TG_HOST`, `TG_USERNAME`, `TG_PASSWORD` — **ask Naman for these directly**
  (same shared TigerGraph Savanna workspace — the data is already loaded
  there, so you're connecting to the existing graph, not building a new one).
- `GROQ_API_KEY` — **your own Groq key, from your own account** (not
  Naman's — that's the whole point). Free tier is fine:
  https://console.groq.com/keys
- Leave `GROQ_MODEL=openai/gpt-oss-120b` and the rest as in `.env.example`.

## 5. Sanity-check the connection (optional but recommended)

```bash
python -c "
import asyncio
from dotenv import load_dotenv
load_dotenv()
from src.tg_client import TigerGraphMCP
async def main():
    async with TigerGraphMCP() as tg:
        print('TigerGraph OK:', bool(await tg.gsql('HELP')))
asyncio.run(main())
"
```

## 6. Run the batch

```bash
python -m scripts.run_batch
```

This runs all 20 cases through the LangGraph investigation flow, writes
`cases/HHG-001.json` … `cases/HHG-020.json`, `runs/latest/batch_summary.json`
(an aggregate summary), and `runs/latest/traces/HHG-001.trace.json` … (one
per case — powers the UI's Investigation/Graph tabs; a trace failing never
costs a case its already-valid answer file). It prints progress per case
(`--- HHG-001 ---`, then `wrote cases\HHG-001.json (...)`, then
`wrote runs\latest\traces\HHG-001.trace.json`).

**Make sure you're on commit `c486dea` or later** (`git log --oneline -1`)
before running — that's when trace files were added. If you already pulled
an earlier commit and started a run, it's fine to `git pull` now and finish
with the newer code; you'll just be missing traces for cases already
written before you pulled (rerun those specific `--case-ids` afterward if
you want traces for everything).

**Expect ~4-5 minutes per case** (mostly graph queries, a couple of LLM
calls), so the full run is roughly **1.5-2 hours**. It's fine to leave it
running unattended.

**If a case fails** (rate limit, a transient TigerGraph hiccup), the script
keeps going — failures are recorded in `runs/latest/batch_summary.json`'s
`errors` field, not fatal. Once it finishes, check which case_ids are
missing from `cases/`, then rerun just those:

```bash
python -m scripts.run_batch --case-ids HHG-011,HHG-013 --merge
```

`--merge` keeps the already-succeeded cases in the summary and only
replaces the ones you're rerunning — it won't redo work or drop results.

**If you get killed/interrupted partway through:** verify the python
process actually exited before rerunning (`Get-Process python` /
`ps aux | grep python`) — a "stopped" background job doesn't always
kill the underlying process, and two runs hitting TigerGraph/Groq at once
will cause new failures. Kill any stray one manually if it's still there.

## 7. Validate before sending back

```bash
python -m src.run.validate_outputs cases --traces runs/latest/traces
```

Reports structural and semantic validation separately; both should print `20/20 files passed.`
Structural validation alone is not enough: the semantic pass catches contradictions such as
`closed_fraud` with R7 actions, CNP evidence citing in-person rows, or connected cards taken from a
generic device collision. Before any live run, `python -m scripts.feature_audit` (no LLM) shows every
deterministic signal per case. If anything fails, paste the output back
— don't try to hand-fix a JSON file.

## 8. Sanity-check the verdict distribution

```bash
python -c "
import json
from pathlib import Path
for f in sorted(Path('cases').glob('HHG-*.json')):
    d = json.load(open(f, encoding='utf-8'))['case']
    print(f'{f.stem}: {d[\"verdict\"]:10s} prob={d[\"fraud_probability\"]:.2f} pattern={d[\"pattern\"]}')
"
```

You should see a real mix of `fraud`/`uncertain`/`legitimate` — **not**
every single case scoring `fraud` at 0.7+. (That exact failure mode is what
the two bugs fixed today caused; if you somehow still see it, stop and ping
Naman rather than pushing.)

## 9. Push it back

```bash
git add -f cases/ runs/
git commit -m "Task 14: full 20-case batch run"
git push origin tigergraph-fraud-agent
```

(`cases/` and `runs/` are gitignored right now specifically because no
clean full run had completed yet — `-f` force-adds them past the
gitignore rule, which is expected and correct once your run is the real,
final one.)

Then let Naman know it's done.
