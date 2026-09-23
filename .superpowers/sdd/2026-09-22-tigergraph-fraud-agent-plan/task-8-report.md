# Task 8 Report: Derived entities (DeviceProfile) + pipe-separated edges + manual case checkpoint

## Status: DONE_WITH_CONCERNS

## Commit

`621ec99` — `feat: derived entities (DeviceProfile, multi-edges) and manual case
checkpoint`, on branch `tigergraph-fraud-agent`, local only (not pushed). Adds:
- `src/schema/derive_entities.py`
- `scripts/derive_entities.py`
- `tests/test_derive_entities.py` (not required by the brief's file list, added to
  mirror `tests/test_loading_jobs.py`'s existing convention and de-risk the live run)
- `docs/manual-case-checkpoint.md`

Working tree clean after commit (`git status --short` empty).

## What ran, live, against the graph

`PYTHONPATH=. .venv\Scripts\python scripts\derive_entities.py` — completed
successfully (exit 0). Loaded:
- **140,784** `FROM_DEVICE` edges + deduped `DeviceProfile` vertices (**9,705**
  distinct fingerprints) from `identity.csv`'s 144,432 rows — 3,648 rows had no
  device signal (blank `DeviceInfo`/`id_30`/`id_31`) and were correctly skipped,
  matching an independent Python count of the raw CSV exactly.
- **14,955** `INVOLVES` edges and **92** `CONNECTED_TO` edges from
  `closed_cases_history.csv`'s pipe-separated `txn_ids`/`connected_card_ids`
  fields — both counts verified against an independent Python sum over the raw
  CSV, exact match.

`tigergraph__add_nodes`/`tigergraph__add_edges` were used throughout (never raw
`tg.gsql("INSERT ...")`), matching Task 7's confirmed pattern. `INVOLVES` and
`CONNECTED_TO` were always flushed in separate batches, never mixed in one
`add_edges` call, per the brief's noted API constraint.

One deviation from the brief's literal invocation: running
`.venv\Scripts\python scripts\derive_entities.py` directly from the repo root fails
with `ModuleNotFoundError: No module named 'src'` (no `src`-relative import path
without it). Ran with `PYTHONPATH=.` prefixed instead, consistent with this repo's
existing convention (`pytest.ini` sets `pythonpath = .`, and Task 7's report used
the same `PYTHONPATH=.` prefix for its own commands).

## Manual checkpoint: HHG-017

Full write-up in `docs/manual-case-checkpoint.md`. Summary:

**Did it reproduce a `card_testing` pattern? No — and that's a real finding, not a
gap.** Card `C04570-K1`'s 59-transaction history shows three ~$100 online purchases
in one 70-minute window around the flagged transaction, but no sub-$5 "testing"
charges precede them — the defining feature of `card_testing` (Policy R5) is absent.
The shared device fingerprint (Windows 10 / Chrome 65 / 1920x1080) turned out, on
querying, to be shared by 621 transactions across 299 distinct customers — a common
device-category collision, not a meaningful ring signal. The customer's one prior
closed case on this exact card (`CC-1383`) was `cleared`, not confirmed fraud. My
by-hand verdict: not fraud-confirmed, single weak signal (risk score 0.57), Policy R1
applies — recommend `VERIFY_WITH_CUSTOMER` + `CREATE_CASE`, not an immediate block.

I also found that the task brief's framing of HHG-017 as having "ground truth" from
the README's worked JSON example doesn't actually hold: that example's `case_id`
happens to say `"HHG-017"`, but its transaction IDs (`T0412877` etc.) and card IDs
(`C00377-K1`, `C00877-K1`) don't exist anywhere in this dataset (real transaction IDs
here are purely numeric) — it's a generic format illustration, not this case's actual
ground truth. I reported my independently-derived verdict rather than reverse-fitting
it to that example, per this task's explicit instruction not to fabricate findings.

**Go/no-go: Go**, with two documented, workaround-confirmed query-mechanics gaps for
whoever does Task 8.5/9/10 next (both fully written up in the checkpoint doc, not
just flagged):
1. `Card`/`DeviceProfile` lack `primary_id_as_attribute`, so ad-hoc
   `WHERE Card.card_id == ...` (as literally shown in the brief's own Step 4 snippet)
   fails — worked around via the `tigergraph__get_neighbors`/`get_node`/
   `get_node_edges` MCP tools instead, which address by real primary ID over REST++.
2. No edge in Task 4's schema has a declared `REVERSE_EDGE`. This breaks two things
   confirmed live: (a) the generic `get_node_edges`/`get_node_degree` MCP tools only
   see outgoing edges, so calling them on `DeviceProfile`/`Card` (edge targets)
   always reports 0, even when the edge exists (verified from the correct side); (b)
   Task 8.5's planned `build_shares_origin()` query, exactly as drafted in the brief,
   will fail to install — it references `reverse_FROM_DEVICE`/`reverse_MADE`/
   `reverse_BILLED_IN`/`reverse_PURCHASER_EMAIL`, none of which exist
   (`SEM-40: reverse_FROM_DEVICE is not a valid edge type`, confirmed live). The
   confirmed-working alternative (verified end-to-end with an installed probe query
   whose result count matched an independent CSV count exactly, twice): seed from
   the "many" side's full vertex set, traverse the edge forward, and filter the
   target with `WHERE targetAlias == paramVertex` using a `VERTEX<T>` query
   parameter — not `reverse_X` edges, and not `to_vertex()` (rejected in ad-hoc
   top-level `WHERE` clauses on this server).

## Verification

- `PYTHONPATH=. .venv\Scripts\python -m pytest tests/` — 33/33 passed (29 prior +
  4 new in `tests/test_derive_entities.py`, covering `device_id_for`'s determinism,
  the "no device signal → skip" branch, and that `INVOLVES`/`CONNECTED_TO` are
  never mixed in one `add_edges` batch).
- Live vertex/edge counts (`tigergraph__get_vertex_count`/`get_edge_count`) cross-
  checked against independent Python counts over the raw CSVs for all four new/
  affected edge types — exact match on every one.
- Manual checkpoint queries run live against the real graph (not mocked), including
  three installed-GSQL probe queries used to validate the reverse-traversal
  workaround (all dropped again afterward — graph left with only the intended
  Task 8 artifacts, no leftover debug queries).

## Concerns (why DONE_WITH_CONCERNS, not DONE)

1. The brief's Step 4 example query (`Card.card_id == "C04570-K1"`) does not run as
   written on this server — documented in the checkpoint doc with the working
   alternative, but anyone copy-pasting the brief's literal snippet will hit the
   same `Semantic Check Fails` error I did.
2. Task 8.5's `build_shares_origin()` GSQL, as drafted in the brief, will not
   install on this server (confirmed live) because of the missing `REVERSE_EDGE`
   declarations. This isn't something Task 8 should silently fix (it's Task 4's
   schema and Task 8.5's query code, both out of this task's stated scope), but
   it's a known, live-confirmed blocker for the next task in the plan, not a
   hypothetical one — flagged prominently in the checkpoint doc.
3. HHG-017 does not confirm as `card_testing`/`fraud` the way the task's framing
   context suggested it should. I'm confident this is because that framing
   conflated the README's generic illustrative JSON example with this case's real
   ground truth (they share only the string `"HHG-017"` and nothing else), not
   because the schema or load is broken — the load-validation counts all match
   exactly, and the graph answered every query I asked it sensibly. But since I
   can't independently confirm the exam's actual answer key, I'm flagging this as
   a concern rather than asserting it's certainly resolved.
