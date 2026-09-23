from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from src.tg_client import TigerGraphMCP

GRAPH_NAME = "FraudInvestigation"

# --------------------------------------------------------------------------
# Why this file looks nothing like the task-10 brief's draft
# --------------------------------------------------------------------------
#
# The brief's draft used bare `INTERPRET QUERY () FOR GRAPH ... { ... }`
# (no formal CREATE/INSTALL) with either a WHERE-clause primary-key filter
# (`WHERE c.card_id == "..."`) or, per the brief's own fallback note, a
# `VERTEX<Type>` query PARAMETER passed through `tg.gsql` as a single command
# string. Both were tried live against this server before writing any query
# below, and both fail, for two separate reasons that stack on top of each
# other:
#
# 1. WHERE-clause primary-key filtering fails exactly as predicted for every
#    vertex type except Transaction: `Card`, `DeviceProfile`, `ClosedCase`
#    (confirmed live here too -- `Cases.case_id` throws the same
#    "indicates no valid vertex type ... refers to a primary_id, which is
#    not directly usable" error TYP-158) all lack
#    `primary_id_as_attribute`. `Customer`/`BillingRegion`/`FraudCase` are
#    assumed to share this (identical plain PRIMARY_ID declaration in
#    src/schema/build_schema.py) per the task's own instruction, and never
#    needed a WHERE-clause probe here because every query below avoids
#    filtering by primary-key attribute entirely (see point 3).
#
# 2. Separately and more fundamentally: **`INTERPRET QUERY` on this server
#    does not accept parameters at all**, confirmed live with the exact
#    error `Semantic Check Fails: Run interpreted query doesn't support
#    parameter.` -- posting `INTERPRET QUERY (VERTEX<Card> input_card) FOR
#    GRAPH ... { Start = {input_card}; ... }` through `tg.gsql` (the single
#    "command" string channel the brief specified) compiles but refuses to
#    execute. This means the brief's own suggested fix (a typed
#    `VERTEX<Type>` parameter) cannot be delivered through an interpreted
#    query on this server, no matter how the query body is written --
#    contrary to the brief's framing that only the WHERE-clause needed
#    fixing. Every query below is therefore a real, named, formally
#    installed GSQL query object (`CREATE OR REPLACE QUERY ... INSTALL
#    QUERY ...`, run via `tigergraph__run_installed_query`/`tg.run_installed_
#    query`) -- the exact mechanism Task 8.5's `connected_components.py`
#    already established for this server, not the brief's "interpreted
#    queries are fine, no install ceremony needed" assumption. Query install
#    takes ~20-30s the first time (confirmed live, see `_ensure_installed`
#    below); this cost is paid at most once per query per process, not once
#    per call, via a module-level cache -- once installed, GSQL persists the
#    query server-side across process restarts too, so redundant installs
#    are also gsql-side no-ops (though the ~20-30s round trip through
#    `tg.gsql` for the CREATE-OR-REPLACE re-issue in a *new* process is not
#    free; see `_ensure_installed`'s docstring for how the cache handles this).
#
# 3. **No edge in Task 4's schema has a declared `REVERSE_EDGE`** (Task 8's
#    finding), and unlike Task 8.5's finding that this didn't matter for a
#    forward-only traversal, several of this task's queries genuinely need
#    to go from the "one" side (a known DeviceProfile/BillingRegion vertex)
#    back to the "many" side (its Transactions). This task's own framing
#    suggested the native backward pattern-arrow (`<-(EdgeName)-`) as the
#    fix -- **tested live and confirmed to be a hard GSQL PARSE error on
#    this server** (`no viable alternative at input 'from Devs:d <-('`),
#    not merely a semantic one. The actual confirmed-working pattern
#    (identical to Task 8's own resolution, re-verified here) is: seed from
#    the "many" side's FULL vertex set (e.g. `{Transaction.*}`), traverse
#    the edge FORWARD in its declared direction, and filter the target
#    endpoint against the known vertex/vertex-set -- `WHERE target ==
#    paramVertex` for a single known vertex, or `WHERE target IN
#    @@setAccum` for a set collected earlier in the same query (GSQL
#    rejects `IN` against a plain vertex-set *variable* -- confirmed live,
#    `TYP-8027: Vertex set variable '...' is not supported for IN/NOT IN
#    clause` -- so every "join against an earlier step's results" below
#    goes through a `SetAccum<VERTEX<T>>` accumulator, never the raw
#    SELECT-produced vertex-set variable).
#
# 4. This dialect still does not support multi-hop chained FROM-clause
#    patterns (Task 8.5's finding, re-confirmed implicitly by every query
#    below being written as sequential single-hop SELECTs).
#
# 5. Primary IDs are never referenced with `alias.card_id`/`alias.case_id`
#    etc. in any SELECT/WHERE/PRINT below (would hit finding #1) -- `v_id`
#    in the JSON response (present automatically, confirmed live, regardless
#    of `primary_id_as_attribute`) is used instead wherever a primary id is
#    needed in the returned data.
#
# --------------------------------------------------------------------------
# Two findings from task-10-review.md's fix round (investigated, not code
# changes to the queries themselves)
# --------------------------------------------------------------------------
#
# 6. **A live "deprecated parameter format" warning on every VERTEX<T> call
#    cannot actually be fixed from this file.** pyTigerGraph's client warns
#    that plain string values for VERTEX<T> params (`{"input_card": "..."}`)
#    are deprecated in favor of a 1-tuple (`{"input_card": ("...",)}`), and
#    recommends the 1-tuple form. Tried both a Python tuple AND a Python list
#    for every VERTEX param, live, before assuming this was a one-line fix:
#    the warning fires identically either way. Root cause: `tg.run_installed_
#    query` goes through the tigergraph-mcp MCP server over JSON-RPC (see
#    `tg_client.py`), and JSON has no tuple type distinct from an array --
#    whatever Python object is sent here is serialized to a JSON array and
#    reconstructed as a plain `list` on the MCP server's side, where
#    pyTigerGraph's own `isinstance(value, tuple)` check (inside the
#    tigergraph-mcp server process, not this codebase) always sees a `list`,
#    never a `tuple`, no matter what this file sends. Fixing this for real
#    would require a change to the tigergraph-mcp server's own tool
#    implementation, out of this task's (and this repo's) scope. Confirmed
#    live this is harmless today: the deprecated path still returns correct,
#    verified-accurate results (falls back to a GET-based request) for every
#    query in this file.
#
# 7. **A real, reproducible concurrency race** in `CREATE OR REPLACE QUERY
#    ... INSTALL QUERY ...` when two processes install the same query name
#    concurrently -- see `_run_installed_query`'s docstring for the confirmed
#    failure mode and the retry mitigation applied to every call site below.

_INSTALLED_QUERIES: set[str] = set()


async def _ensure_installed(tg: TigerGraphMCP, query_name: str, create_and_install_gsql: str) -> None:
    """Install (or confirm already-installed) a named GSQL query, exactly
    once per process. GSQL install is idempotent and persists server-side
    across process restarts, but each `CREATE OR REPLACE QUERY ... INSTALL
    QUERY ...` round trip still costs ~20-30s live (confirmed repeatedly
    during this task's development) even when nothing changed, so this
    module-level set avoids paying that cost more than once per query per
    running process -- Task 12 is expected to call these functions many
    times per case-processing run, not once.

    The tigergraph-mcp `gsql` tool call itself always reports
    `success: true` (it's a successful HTTP round trip) even when the GSQL
    *content* failed to install -- a `CREATE QUERY` with a syntax/semantic
    error comes back as a normal 200 response whose `data.result` text says
    "Saved as draft query ... Query installation failed!". So a plain
    success-envelope check is not enough; the response text itself has to
    be inspected for the server's own installation-outcome message.
    """
    if query_name in _INSTALLED_QUERIES:
        return
    result = await tg.gsql(create_and_install_gsql)
    text = str(result.get("data", {}).get("result", "")) if isinstance(result, dict) else str(result)
    if "Query installation finished." not in text or "Successfully created queries" not in text:
        raise RuntimeError(f"Failed to install GSQL query '{query_name}': {text}")
    _INSTALLED_QUERIES.add(query_name)


async def _run_installed_query(tg: TigerGraphMCP, query_name: str, params: dict[str, Any]) -> Any:
    """Thin wrapper around `tg.run_installed_query` that retries once on a
    specific, live-reproduced concurrency race: `_ensure_installed` has no
    cross-process locking around `CREATE OR REPLACE QUERY ... INSTALL QUERY
    ...`, so two processes with cold `_INSTALLED_QUERIES` caches racing on
    the SAME query name (e.g. two Task 12 workers, or a test run overlapping
    a manual probe) can leave that query's REST endpoint transiently
    *disabled* -- confirmed live (task-10-review.md): a concurrent install
    of `card_window` from a second process produced
    `REST-1005: Query endpoint '/query/FraudInvestigation/card_window' is
    disabled, please make sure all its sub-queries are installed and enabled
    with same signature.` on `run_installed_query`, and a bare retry moments
    later (once the racing install finished) succeeded cleanly with no other
    change. This does not eliminate the race at its source (that would need
    a real cross-process install lock, e.g. a dedicated coordination vertex
    or an external mutex -- out of scope for this fix, and not needed if
    Task 12 pre-warms all 8 queries once before spawning any parallel
    workers, which the module-level docstring above already recommends);
    it turns the one confirmed failure mode into a short, self-healing
    retry instead of a hard, unhandled RuntimeError reaching Task 12's
    evidence-gathering node.
    """
    try:
        return await tg.run_installed_query(query_name, params)
    except RuntimeError as e:
        if "is disabled" in str(e) or "REST-1005" in str(e):
            await asyncio.sleep(3)
            return await tg.run_installed_query(query_name, params)
        raise


def _print_results(run_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Unwrap a `tg.run_installed_query(...)` envelope down to the list of
    PRINT-statement results.

    Confirmed live (this task): `tg.run_installed_query` returns the same
    `{"success", "operation", "data", ...}` envelope as `tg.gsql` (Task 2's
    finding, reused here) -- NOT a bare list. The actual PRINT outputs live
    at `data["result"]`, a list with one entry per PRINT statement in
    source order, e.g. for a query with two PRINT statements:
    `[{"first_print_alias": [...]}, {"second_print_alias": [...]}]`. Each
    vertex-set PRINT entry is itself a list of
    `{"v_id": ..., "v_type": ..., "attributes": {"Alias.field": value, ...}}`
    dicts (confirmed live against known HHG-017/CC-1383/RING-C00001-K1
    fixtures throughout this task's development -- see task-10-report.md).
    """
    if not isinstance(run_result, dict):
        return []
    return run_result.get("data", {}).get("result", []) or []


def _flatten_vertices(vertex_list: list[dict[str, Any]], prefix: str) -> list[dict[str, Any]]:
    """Turn a PRINT'd vertex list (`{"v_id", "v_type", "attributes": {"Alias.x": v}}`)
    into a flat `[{"id": v_id, "x": v, ...}]` list, stripping the GSQL
    alias prefix (e.g. "Txns.") from each attribute key so callers (and the
    LLM prompt this feeds in Task 12) see plain field names."""
    flattened: list[dict[str, Any]] = []
    for entry in vertex_list:
        row: dict[str, Any] = {"id": entry.get("v_id")}
        for key, value in entry.get("attributes", {}).items():
            plain_key = key.split(".", 1)[1] if "." in key else key
            row[plain_key] = value
        flattened.append(row)
    return flattened


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------
# card_window
# --------------------------------------------------------------------------
CARD_WINDOW_GSQL = f"""
USE GRAPH {GRAPH_NAME}
CREATE OR REPLACE QUERY card_window(VERTEX<Card> input_card) FOR GRAPH {GRAPH_NAME} {{
    Start = {{input_card}};
    Txns = SELECT t FROM Start-(MADE)->Transaction:t;
    PRINT Txns[Txns.transaction_id, Txns.ts, Txns.TransactionAmt, Txns.addr1,
               Txns.channel, Txns.risk_score, Txns.customer_id] AS transactions;
}}
INSTALL QUERY card_window
""".strip()


async def card_window(
    tg: TigerGraphMCP,
    card_id: str,
    hours: float = 2.0,
    reference_txn_id: str | None = None,
    cutoff_ts: str | None = None,
) -> list[dict]:
    """The card's transactions, windowed to `hours` around an anchor point.

    **Temporal leakage fix (2026-09-23):** `cutoff_ts` (pass the case's
    `opened_at`) caps the window so it never includes anything the bank
    could not have seen when the case was opened. Before this fix, the
    default `hours=48` symmetric window around the flagged transaction could
    -- and on this dataset, for 13 of the 20 case-pack rows, DID -- include
    transactions that happened *after* the case was opened (up to 61 for
    HHG-018), which is future information relative to the investigation.
    `opened_at` is only 1-6 hours after the flagged transaction across the
    whole case pack, so this cap is the difference between a real 48h
    forward window and the true (much narrower) window an analyst actually
    had. `cutoff_ts` is optional (kept so this function still has a sane
    standalone default) but every real call site (`gather_evidence_node`,
    the `wider_card_window` follow-up tool) now always passes it.

    **Fixed after live task-10-review.md finding (Important, live-demonstrated
    defect):** the original version (no `reference_txn_id` parameter) always
    anchored on the card's own MOST RECENT transaction. Task 12's actual
    planned caller (`gather_evidence_node`) calls `card_window(tg, card_id,
    hours=48)` unconditionally, on every case, as the very first piece of
    evidence -- with no reference timestamp, because the interface didn't
    expose one. Live-reproduced against this project's own canonical fixture
    (HHG-017 / `C04570-K1`): with the old "anchor on latest" behavior,
    `card_window(tg, "C04570-K1", hours=48)` returns 2 transactions, both from
    2016-12-25, and SILENTLY EXCLUDES the actual flagged transaction
    (`3450629`, 2016-11-11 23:46:24) -- because this card has ~44 days of
    routine activity after the flagged one. The failure is silent and
    plausible-looking (two real transactions, no error), and would recur for
    every case where the flagged transaction isn't the card's most recent
    activity, which is plausibly the common case, not the rare one.

    `reference_txn_id`, when given, anchors the window on THAT transaction's
    own timestamp instead, `hours` before AND after it (symmetric), matching
    what a caller passing the actual flagged transaction needs; when omitted,
    or when the given id isn't found among this card's own transactions (e.g.
    wrong card/typo), falls back to the original "hours before the card's own
    latest transaction" behavior for backward compatibility. Task 12 is
    expected to pass the case's own flagged/reference transaction id here
    once its call site is updated (tracked separately, not part of this fix)
    -- this function's signature and behavior are ready for that now.

    `card_window`'s GSQL itself is unchanged: it returns the card's FULL
    transaction history (a single VERTEX<Card>-parameterized forward MADE
    traversal, confirmed live: exactly 59 rows for C04570-K1, matching Task
    8's independently-verified count), and all windowing (anchor selection +
    the hours cutoff) happens in Python, since `ts` is a plain STRING
    attribute (no DATETIME arithmetic available server-side without extra
    parsing ceremony this task doesn't need at this data scale, typically
    dozens of rows per card).
    """
    await _ensure_installed(tg, "card_window", CARD_WINDOW_GSQL)
    result = await _run_installed_query(tg, "card_window", {"input_card": card_id})
    print_results = _print_results(result)
    raw_txns = print_results[0]["transactions"] if print_results else []
    txns = _flatten_vertices(raw_txns, "Txns")

    parsed = [(t, _parse_ts(t.get("ts"))) for t in txns]
    valid = [(t, ts) for t, ts in parsed if ts is not None]
    if not valid:
        return txns

    cutoff_dt = _parse_ts(cutoff_ts) if cutoff_ts else None
    # Applied to EVERY branch below, not just the reference-anchored one --
    # a cutoff means "nothing after this point", full stop, regardless of
    # which anchor selected the window.
    if cutoff_dt is not None:
        valid = [(t, ts) for t, ts in valid if ts <= cutoff_dt]
        if not valid:
            return []

    reference_ts = None
    if reference_txn_id is not None:
        matches = [ts for t, ts in valid if t.get("id") == reference_txn_id]
        if matches:
            reference_ts = matches[0]

    if reference_ts is not None:
        low = reference_ts - timedelta(hours=hours)
        high = reference_ts + timedelta(hours=hours)
        if cutoff_dt is not None and high > cutoff_dt:
            high = cutoff_dt
        return [t for t, ts in valid if low <= ts <= high]

    # Fallback: no reference given (or it wasn't found on this card) --
    # original one-directional "hours before the card's own latest
    # transaction" behavior, kept for backward compatibility. `valid` is
    # already cutoff-filtered above, so "latest" here means "latest
    # on-or-before cutoff", not the card's true latest transaction.
    latest = max(ts for _, ts in valid)
    window_cutoff = latest - timedelta(hours=hours)
    return [t for t, ts in valid if ts >= window_cutoff]


# --------------------------------------------------------------------------
# customer_cards
# --------------------------------------------------------------------------
CUSTOMER_CARDS_GSQL = f"""
USE GRAPH {GRAPH_NAME}
CREATE OR REPLACE QUERY customer_cards(VERTEX<Customer> input_customer) FOR GRAPH {GRAPH_NAME} {{
    Start = {{input_customer}};
    Cards = SELECT c FROM Start-(OWNS)->Card:c;
    PRINT Cards[Cards.ring_cluster_id, Cards.cluster_prior_fraud_rate] AS cards;
}}
INSTALL QUERY customer_cards
""".strip()


async def customer_cards(tg: TigerGraphMCP, customer_id: str) -> list[dict]:
    """Every card owned by this customer, with each card's ring-clustering
    output (Task 8.5) attached. `Customer` has never been probed for
    `primary_id_as_attribute` before this task (per the task's own
    instruction to assume the gap); avoided entirely here the same way
    `card_window` avoids it for `Card` -- seed from the known VERTEX<Customer>
    parameter, no WHERE-clause primary-key filter at all."""
    await _ensure_installed(tg, "customer_cards", CUSTOMER_CARDS_GSQL)
    result = await _run_installed_query(tg, "customer_cards", {"input_customer": customer_id})
    print_results = _print_results(result)
    raw_cards = print_results[0]["cards"] if print_results else []
    return _flatten_vertices(raw_cards, "Cards")


# --------------------------------------------------------------------------
# device_neighbors
# --------------------------------------------------------------------------
DEVICE_NEIGHBORS_GSQL = f"""
USE GRAPH {GRAPH_NAME}
CREATE OR REPLACE QUERY device_neighbors(VERTEX<Transaction> input_txn, STRING cutoff_ts) FOR GRAPH {GRAPH_NAME} {{
    SetAccum<VERTEX<DeviceProfile>> @@device;
    SetAccum<VERTEX<Transaction>> @@sameDeviceTxns;

    Start = {{input_txn}};
    DevStep = SELECT d FROM Start-(FROM_DEVICE)->DeviceProfile:d
              ACCUM @@device += d;

    // Temporal leakage fix (2026-09-23): `t.ts <= cutoff_ts` excludes any
    // transaction that happened after the case was opened. `ts` is a fixed-
    // width "YYYY-MM-DD HH:MM:SS" STRING, so lexicographic <= is exactly
    // chronological <= -- no DATETIME cast needed. Before this fix, another
    // card could show up as a "shared device" match purely because it used
    // the same device fingerprint WEEKS after this case's cutoff, which is
    // not evidence the investigator could have had.
    AllTxns = {{Transaction.*}};
    SameDeviceTxns = SELECT t FROM AllTxns:t -(FROM_DEVICE)-> DeviceProfile:d2
                      WHERE d2 IN @@device AND t.ts <= cutoff_ts
                      ACCUM @@sameDeviceTxns += t;

    AllCards = {{Card.*}};
    SharedCards = SELECT c FROM AllCards:c -(MADE)-> Transaction:t2
                  WHERE t2 IN @@sameDeviceTxns
                  LIMIT 300;

    PRINT @@sameDeviceTxns.size() AS n_shared_transactions;
    PRINT SharedCards[SharedCards.ring_cluster_id, SharedCards.cluster_prior_fraud_rate] AS shared_cards;
}}
INSTALL QUERY device_neighbors
""".strip()


async def device_neighbors(tg: TigerGraphMCP, transaction_id: str, cutoff_ts: str) -> list[dict]:
    """Other Cards that share this transaction's device fingerprint, using
    only device activity on or before `cutoff_ts` (the case's `opened_at`).

    The brief's draft chains `Transaction -(FROM_DEVICE)-> DeviceProfile
    <-(FROM_DEVICE)- Transaction <-(MADE)- Card` in one FROM clause with a
    `reverse_`-style implicit backward walk; that fails outright (both the
    multi-hop chaining and the native `<-(EdgeName)-` backward arrow are
    confirmed live parse errors on this server -- see the module docstring).
    Rewritten as: get the one DeviceProfile the input transaction points to
    (forward, trivial), then forward-seed-and-filter twice more (all
    Transactions -> DeviceProfile, keep ones matching AND on/before cutoff;
    all Cards -> those Transactions, keep ones matching). Verified live
    against HHG-017's known device fingerprint before the temporal filter
    was added: 621 shared transactions / up to 300 (LIMIT-capped) shared
    cards, matching Task 8's independently-confirmed 621/299 exactly (299 <
    the 300 cap, so nothing was actually truncated for that fixture); the
    cutoff can only shrink that count, never grow it.

    `cutoff_ts` is required, not optional, precisely because the un-bounded
    version was the finding: every real caller has a case's `opened_at`
    available, and there is no legitimate reason to call this without it.
    """
    await _ensure_installed(tg, "device_neighbors", DEVICE_NEIGHBORS_GSQL)
    result = await _run_installed_query(
        tg, "device_neighbors", {"input_txn": transaction_id, "cutoff_ts": cutoff_ts}
    )
    print_results = _print_results(result)
    raw_cards = print_results[1]["shared_cards"] if len(print_results) > 1 else []
    return _flatten_vertices(raw_cards, "SharedCards")


# --------------------------------------------------------------------------
# region_neighbors
# --------------------------------------------------------------------------
REGION_NEIGHBORS_GSQL = f"""
USE GRAPH {GRAPH_NAME}
CREATE OR REPLACE QUERY region_neighbors(VERTEX<BillingRegion> input_region, STRING cutoff_ts) FOR GRAPH {GRAPH_NAME} {{
    // Temporal leakage fix (2026-09-23): `t.ts <= cutoff_ts` is now part of
    // the WHERE clause GSQL evaluates BEFORE `LIMIT 500`, not a client-side
    // filter applied after. This also fixes the previously-documented
    // "LIMIT truncates before the time filter can run" defect for free --
    // filtering first means the 500 returned rows are the region's 500
    // most-relevant (on-or-before cutoff) rows, not an arbitrary pre-cutoff
    // mix of past and future activity.
    AllTxns = {{Transaction.*}};
    RegionTxns = SELECT t FROM AllTxns:t -(BILLED_IN)-> BillingRegion:b
                 WHERE b == input_region AND t.ts <= cutoff_ts
                 LIMIT 500;
    PRINT RegionTxns[RegionTxns.transaction_id, RegionTxns.ts, RegionTxns.TransactionAmt,
                      RegionTxns.channel, RegionTxns.risk_score, RegionTxns.customer_id] AS transactions;
}}
INSTALL QUERY region_neighbors
""".strip()


async def region_neighbors(
    tg: TigerGraphMCP,
    addr1: str,
    cutoff_ts: str,
    txn_ts: str | None = None,
    window_days: float | None = None,
) -> list[dict]:
    """Other transactions billed in the same region as `addr1`, on or before
    `cutoff_ts` (the case's `opened_at`).

    `BillingRegion` was never explicitly probed for `primary_id_as_attribute`
    before this task; avoided the same way as `card_window`/`customer_cards`
    -- `addr1` is passed as a `VERTEX<BillingRegion>` query parameter
    (GSQL resolves it from the primary-id string automatically, confirmed
    live, same as `VERTEX<Card>`), never as a WHERE-clause attribute filter.
    `BillingRegion` is the "one" side of `Transaction -(BILLED_IN)->
    BillingRegion`, so reading its transactions needs the same
    forward-seed-from-the-many-side-and-filter-the-target workaround as
    `device_neighbors` (single accumulator-free `WHERE b == input_region`
    here since there's only one known region, not a set).

    Server-side `LIMIT 500` guards against the coarse `BillingRegion`
    collisions Task 8.5 found (max 2,006 cards sharing one region) blowing
    up the response; `cutoff_ts` is now applied in the SAME GSQL WHERE
    clause, before that limit (see the query docstring above -- this
    replaces the old client-side-only `txn_ts`/`window_days` narrowing,
    which ran too late to prevent the LIMIT-before-filter defect). `txn_ts`/
    `window_days`, when both given, still further narrow that (now
    cutoff-safe) set down to a specific window in Python, same
    string-timestamp-parsing approach as `card_window`.
    """
    await _ensure_installed(tg, "region_neighbors", REGION_NEIGHBORS_GSQL)
    result = await _run_installed_query(
        tg, "region_neighbors", {"input_region": addr1, "cutoff_ts": cutoff_ts}
    )
    print_results = _print_results(result)
    raw_txns = print_results[0]["transactions"] if print_results else []
    txns = _flatten_vertices(raw_txns, "RegionTxns")

    if txn_ts is None or window_days is None:
        return txns

    anchor = _parse_ts(txn_ts)
    if anchor is None:
        return txns
    window = timedelta(days=window_days)
    filtered = []
    for t in txns:
        ts = _parse_ts(t.get("ts"))
        if ts is not None and abs(ts - anchor) <= window:
            filtered.append(t)
    return filtered


# --------------------------------------------------------------------------
# closed_case_lookup
# --------------------------------------------------------------------------
CLOSED_CASE_BY_CARD_GSQL = f"""
USE GRAPH {GRAPH_NAME}
CREATE OR REPLACE QUERY closed_case_lookup_by_card(VERTEX<Card> input_card) FOR GRAPH {GRAPH_NAME} {{
    SetAccum<VERTEX<ClosedCase>> @@cases;

    AllCases1 = {{ClosedCase.*}};
    OnCard = SELECT cc FROM AllCases1:cc -(ON_CARD)-> Card:c
             WHERE c == input_card
             ACCUM @@cases += cc;

    AllCases2 = {{ClosedCase.*}};
    Connected = SELECT cc FROM AllCases2:cc -(CONNECTED_TO)-> Card:c
                WHERE c == input_card
                ACCUM @@cases += cc;

    Cases = {{@@cases}};
    PRINT Cases[Cases.outcome, Cases.pattern, Cases.exposure_usd, Cases.analyst_notes] AS closed_cases;
}}
INSTALL QUERY closed_case_lookup_by_card
""".strip()

CLOSED_CASE_BY_DEVICE_GSQL = f"""
USE GRAPH {GRAPH_NAME}
CREATE OR REPLACE QUERY closed_case_lookup_by_device(VERTEX<DeviceProfile> input_device) FOR GRAPH {GRAPH_NAME} {{
    SetAccum<VERTEX<Transaction>> @@deviceTxns;
    AllTxns = {{Transaction.*}};
    DeviceTxns = SELECT t FROM AllTxns:t -(FROM_DEVICE)-> DeviceProfile:d
                 WHERE d == input_device
                 ACCUM @@deviceTxns += t;

    SetAccum<VERTEX<ClosedCase>> @@cases;
    AllCases = {{ClosedCase.*}};
    Involved = SELECT cc FROM AllCases:cc -(INVOLVES)-> Transaction:t2
               WHERE t2 IN @@deviceTxns
               ACCUM @@cases += cc;

    Cases = {{@@cases}};
    PRINT Cases[Cases.outcome, Cases.pattern, Cases.exposure_usd, Cases.analyst_notes] AS closed_cases;
}}
INSTALL QUERY closed_case_lookup_by_device
""".strip()

CLOSED_CASE_BY_REGION_GSQL = f"""
USE GRAPH {GRAPH_NAME}
CREATE OR REPLACE QUERY closed_case_lookup_by_region(VERTEX<BillingRegion> input_region) FOR GRAPH {GRAPH_NAME} {{
    SetAccum<VERTEX<Transaction>> @@regionTxns;
    AllTxns = {{Transaction.*}};
    RegionTxns = SELECT t FROM AllTxns:t -(BILLED_IN)-> BillingRegion:b
                 WHERE b == input_region
                 ACCUM @@regionTxns += t;

    SetAccum<VERTEX<ClosedCase>> @@cases;
    AllCases = {{ClosedCase.*}};
    Involved = SELECT cc FROM AllCases:cc -(INVOLVES)-> Transaction:t2
               WHERE t2 IN @@regionTxns
               ACCUM @@cases += cc;

    Cases = {{@@cases}};
    PRINT Cases[Cases.outcome, Cases.pattern, Cases.exposure_usd, Cases.analyst_notes] AS closed_cases;
}}
INSTALL QUERY closed_case_lookup_by_region
""".strip()


async def closed_case_lookup(
    tg: TigerGraphMCP,
    card_id: str | None = None,
    device_id: str | None = None,
    addr1: str | None = None,
) -> list[dict]:
    """Closed cases connected to a card (via ON_CARD or CONNECTED_TO), a
    device fingerprint (via a shared-device Transaction set, then INVOLVES),
    or a billing region (via a shared-region Transaction set, then
    INVOLVES) -- any combination of the three may be supplied; results are
    the union across whichever are given (duplicates possible if the same
    case matches more than one criterion; not de-duplicated here since
    Task 12's evidence-gathering step is expected to reason over each
    signal separately).

    `ClosedCase` (confirmed live in this task -- `Cases.case_id` throws
    the same primary_id-not-attribute error as `Card`) and `DeviceProfile`/
    `BillingRegion` (assumed per the task's instruction) all share the
    `primary_id_as_attribute` gap; every branch here uses the same
    forward-seed-from-ClosedCase(or Transaction)-and-filter-by-VERTEX-
    membership pattern established by `device_neighbors`/`region_neighbors`,
    never a WHERE-clause attribute filter on any of these three types.
    Verified live against the known HHG-017/`C04570-K1` fixture (returns
    exactly `CC-1383`, outcome `cleared`, matching the manual checkpoint
    doc) and the case's own flagged device (returns several unrelated
    `confirmed_fraud` cases -- expected: Task 8's checkpoint already
    established this device fingerprint is a coarse category shared by 299
    customers, not a meaningful ring signal for this specific card).

    No `cutoff_ts` parameter here, unlike `card_window`/`device_neighbors`/
    `region_neighbors`: confirmed against the actual data (2026-09-23) that
    every row in `closed_cases_history.csv` closes by 2016-11-06, and every
    row in `case_pack.csv` opens from 2016-11-12 onward -- so a closed case
    is, by construction, always fully in the past relative to any case this
    function is called for. Revisit if the dataset changes.
    """
    results: list[dict] = []
    if card_id:
        await _ensure_installed(tg, "closed_case_lookup_by_card", CLOSED_CASE_BY_CARD_GSQL)
        result = await _run_installed_query(tg, "closed_case_lookup_by_card", {"input_card": card_id})
        print_results = _print_results(result)
        raw = print_results[0]["closed_cases"] if print_results else []
        results.extend(_flatten_vertices(raw, "Cases"))
    if device_id:
        await _ensure_installed(tg, "closed_case_lookup_by_device", CLOSED_CASE_BY_DEVICE_GSQL)
        result = await _run_installed_query(tg, "closed_case_lookup_by_device", {"input_device": device_id})
        print_results = _print_results(result)
        raw = print_results[0]["closed_cases"] if print_results else []
        results.extend(_flatten_vertices(raw, "Cases"))
    if addr1:
        await _ensure_installed(tg, "closed_case_lookup_by_region", CLOSED_CASE_BY_REGION_GSQL)
        result = await _run_installed_query(tg, "closed_case_lookup_by_region", {"input_region": addr1})
        print_results = _print_results(result)
        raw = print_results[0]["closed_cases"] if print_results else []
        results.extend(_flatten_vertices(raw, "Cases"))
    return results


# --------------------------------------------------------------------------
# ring_membership
# --------------------------------------------------------------------------
RING_MEMBERSHIP_GSQL = f"""
USE GRAPH {GRAPH_NAME}
CREATE OR REPLACE QUERY ring_membership(VERTEX<Card> input_card) FOR GRAPH {GRAPH_NAME} {{
    Start = {{input_card}};
    PRINT Start[Start.ring_cluster_id, Start.cluster_prior_fraud_rate] AS card;
}}
INSTALL QUERY ring_membership
""".strip()


async def ring_membership(tg: TigerGraphMCP, card_id: str) -> dict:
    """O(1) lookup against the ring_cluster_id/cluster_prior_fraud_rate
    attributes written by Task 8.5's connected-components pass -- this is
    the graph-algorithm output, not a live traversal.

    The brief's draft filters `WHERE card_id == "..."` (the exact
    `primary_id_as_attribute` gap this whole task works around) even though,
    unlike the other functions here, no filter is actually needed: the
    caller already knows the one card they want, so this seeds directly
    from a `VERTEX<Card>` parameter and prints its two attributes -- no
    WHERE clause, no full-collection scan, no accumulator.

    Confirmed live re: Task 2's tg_client.py envelope-wrapping note (the
    brief flagged this as unverified and asked whoever implements it to
    check the real shape by hand): `tg.run_installed_query(...)` returns
    `{"success": ..., "data": {"result": [{"card": [{"v_id": ...,
    "attributes": {"Start.ring_cluster_id": ..., "Start.cluster_prior_fraud_rate":
    ...}}]}]}}` -- i.e. `result["data"]["result"][0]["card"]` is the row list,
    matching `_print_results`/`_flatten_vertices` above exactly (no further
    unwrapping needed beyond what those two helpers already do). Verified
    against the known 24-member ring fixture from Task 8.5
    (`C03528-K1` -> `ring_cluster_id: "RING-C00001-K1"`,
    `cluster_prior_fraud_rate: 0.8469...`, matching task-8.5-report.md's
    numbers exactly).
    """
    await _ensure_installed(tg, "ring_membership", RING_MEMBERSHIP_GSQL)
    result = await _run_installed_query(tg, "ring_membership", {"input_card": card_id})
    print_results = _print_results(result)
    raw = print_results[0]["card"] if print_results else []
    rows = _flatten_vertices(raw, "Start")
    return rows[0] if rows else {}


# --- Bounded agentic follow-up round (Task 12 step 2a) -------------------------
# A small, fixed menu of the SAME query functions above, re-parameterized, exposed
# to the LLM as real function-calling tools. The LLM may call at most one of these
# after seeing the deterministic evidence pass; this is genuine tool selection, not
# prose-parsing, but capped to one call so a rate-limited/small model can't loop.

FOLLOWUP_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "wider_region_check",
            "description": "Check for other transactions in the same billing region over a wider window than the default pass.",
            "parameters": {
                "type": "object",
                "properties": {"addr1": {"type": "string"}},
                "required": ["addr1"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "closed_case_lookup_by_region",
            "description": "Look up closed cases connected to a billing region rather than a specific card.",
            "parameters": {
                "type": "object",
                "properties": {"addr1": {"type": "string"}},
                "required": ["addr1"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wider_card_window",
            "description": "Re-run the card transaction window with a longer lookback (e.g. 7 days instead of 48 hours) when the default window looks incomplete.",
            "parameters": {
                "type": "object",
                "properties": {
                    "card_id": {"type": "string"},
                    "hours": {"type": "number"},
                    "reference_txn_id": {
                        "type": "string",
                        "description": "Optional: anchor the window on this transaction's own timestamp instead of the card's most recent transaction.",
                    },
                },
                "required": ["card_id", "hours"],
            },
        },
    },
]


async def dispatch_followup_tool(
    tg: TigerGraphMCP, name: str, arguments: dict, cutoff_ts: str
) -> list[dict] | dict:
    """`cutoff_ts` (the case's `opened_at`) is required here too -- a
    follow-up call is still part of the SAME investigation and must not see
    anything the deterministic first pass wasn't allowed to see either."""
    if name == "wider_region_check":
        # No time-WINDOW narrowing for the "wider" follow-up (still no
        # txn_ts/window_days, so region_neighbors returns its full
        # LIMIT-capped set rather than the default pass's narrower window)
        # -- but the cutoff itself is never optional.
        return await region_neighbors(tg, arguments["addr1"], cutoff_ts=cutoff_ts)
    if name == "closed_case_lookup_by_region":
        return await closed_case_lookup(tg, addr1=arguments["addr1"])
    if name == "wider_card_window":
        return await card_window(
            tg,
            arguments["card_id"],
            hours=arguments.get("hours", 168),
            reference_txn_id=arguments.get("reference_txn_id"),
            cutoff_ts=cutoff_ts,
        )
    raise ValueError(f"Unknown follow-up tool: {name}")
