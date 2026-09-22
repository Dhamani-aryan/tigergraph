from __future__ import annotations

import asyncio
import csv
from pathlib import Path

import pandas as pd

from src.schema.card_ids import build_card_id_map, card_id_for
from src.schema.columns import generate_attrs
from src.tg_client import TigerGraphMCP

GRAPH_NAME = "FraudInvestigation"

# --- Deviation from the brief's literal code: how files actually get loaded ---
#
# The brief's original design assumed `tg.gsql()` could run a single
# declarative `CREATE LOADING JOB ... DEFINE FILENAME f1 = "transactions.csv"
# ... RUN LOADING JOB ... END` block outright. Empirically, against this
# Savanna-hosted TigerGraph 4.2.5 instance, that does NOT work:
#
#   * `tg.gsql()` (-> pyTigerGraph's `gsql()`) POSTs raw GSQL text to
#     `/gsql/v1/statements` on the GSQL server. `DEFINE FILENAME` with any
#     path is resolved against the GSQL SERVER's OWN filesystem, not the
#     machine running this script. A live probe with a relative path
#     ("_probe_tiny.csv") resolved server-side to
#     ".../4.2.5/dev/gdk/gsql/_probe_tiny.csv" and was rejected as being
#     inside a "sensitive directory". A made-up absolute path
#     ("/tmp/placeholder.csv") failed differently, but just as fatally, at
#     CREATE time: "File or directory '/tmp/placeholder.csv' does not
#     exist!" -- CREATE LOADING JOB validates the file's existence against
#     the SERVER filesystem immediately, not just at RUN time. There is no
#     way to hand this server a path on this machine.
#
# The correct mechanism (confirmed live with a tiny probe job before running
# this against the real 708MB file) is:
#
#   1. `DEFINE FILENAME <tag>;` with NO path at all -- GSQL's "runtime data"
#      mode, where the file's bytes are supplied later via a REST call
#      instead of a path.
#   2. Reference columns POSITIONALLY ($0, $1, ...), not by header name
#      ($"ColName"). Named refs need a header row GSQL can sniff, but the
#      upload path used below strips the header (see point 3), and even
#      with `DEFINE HEADER` declared explicitly, `USING header="<name>"`
#      only accepts the literal strings "true"/"false" over this path --
#      `header="true"` against headerless uploaded data is what
#      pyTigerGraph's own `runLoadingJobWithData` docstring warns "may not
#      be enough". `header="false"` + positional refs was the only
#      combination that worked in the live probe.
#   3. Execute via the `tigergraph__run_loading_job_with_data` MCP tool
#      (wraps pyTigerGraph's `runLoadingJobWithData`, which POSTs the given
#      bytes to `/ddl/{graph}`) -- not `tg.gsql()`. This is the only path
#      that actually uploads local bytes to the server for a loading job.
#
# Both CSVs turn out to have exactly one data row per physical line: zero
# quote characters appear anywhere in transactions.csv, and
# closed_cases_history.csv's quoted fields (commas inside analyst_notes
# etc.) never contain an embedded newline -- `wc -l` matches (1 header +
# expected row count) exactly for both files. So batching by raw text lines
# (skipping the header line, no CSV re-parsing/re-quoting on our side) is
# safe and avoids any risk of reformatting values through a parse/rewrite
# round-trip -- important given 393 of Transaction's columns are typed
# DOUBLE. transactions.csv's total absence of quote characters means plain
# `separator=","` positional parsing is correct there. But
# closed_cases_history.csv DOES have comma-containing quoted fields (2,324
# of its 5,565 rows), and GSQL's own CSV parser does NOT infer quoting by
# default -- confirmed live the hard way: the first real run of
# closed_cases_loading_job_gsql without `quote="DOUBLE"` silently
# mis-parsed/dropped 346 of 5,565 rows (5,219 ClosedCase vertices loaded
# instead of 5,565, caught by Step 4's count check), because a comma inside
# an unquoted-per-GSQL analyst_notes/actions_taken field shifted every
# later column's position for that row. Fixed by adding `quote="DOUBLE"` to
# closed_cases_loading_job_gsql's USING clauses (transactions.csv doesn't
# need this, since it has zero quote characters to begin with).
#
# Card/OWNS/MADE are unaffected by any of this file-parsing discussion --
# they were never meant to be file-loaded (see load_cards_and_made_edges
# below). They do, however, have their own live-discovered deviation from
# the brief: raw `tg.gsql("INSERT INTO VERTEX/EDGE ...")` statements don't
# work on this server at all (see the note inside load_cards_and_made_edges
# for the confirmed error and the `add_nodes`/`add_edges` MCP tools used
# instead).


def _read_header(csv_path: str | Path) -> list[str]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        return next(csv.reader(f))


def transactions_loading_job_gsql(transactions_csv_path: str) -> str:
    header = _read_header(transactions_csv_path)
    col_index = {name: i for i, name in enumerate(header)}
    txn_attrs = generate_attrs(transactions_csv_path, primary_key="TransactionID")
    pk_idx = col_index["TransactionID"]
    email_idx = col_index["P_emaildomain"]
    addr_idx = col_index["addr1"]
    cust_idx = col_index["customer_id"]
    # $N -> attribute mapping, positional (see module note above for why).
    attr_positions = ",\n        ".join(f"${col_index[name]}" for name, _ in txn_attrs)
    return f'''
USE GRAPH {GRAPH_NAME}
CREATE LOADING JOB load_transactions FOR GRAPH {GRAPH_NAME} {{
    DEFINE FILENAME f1;
    LOAD f1 TO VERTEX Customer VALUES (${cust_idx}) USING header="false", separator=",";
    LOAD f1 TO VERTEX Transaction VALUES (
        ${pk_idx},
        {attr_positions}
    ) USING header="false", separator=",";
    LOAD f1 TO VERTEX EmailDomain VALUES (${email_idx}) USING header="false", separator=",";
    LOAD f1 TO VERTEX BillingRegion VALUES (${addr_idx}) USING header="false", separator=",";
    LOAD f1 TO EDGE PURCHASER_EMAIL VALUES (${pk_idx} Transaction, ${email_idx} EmailDomain) USING header="false", separator=",";
    LOAD f1 TO EDGE BILLED_IN VALUES (${pk_idx} Transaction, ${addr_idx} BillingRegion) USING header="false", separator=",";
}}
'''.strip()


def closed_cases_loading_job_gsql(closed_cases_csv_path: str) -> str:
    header = _read_header(closed_cases_csv_path)
    col_index = {name: i for i, name in enumerate(header)}
    # Same set as the brief's original named mapping, minus the pipe-separated
    # txn_ids/connected_card_ids columns (handled row-by-row in Task 8), now
    # referenced positionally.
    case_cols = [
        "case_id", "customer_id", "card_id", "opened_at", "closed_at",
        "outcome", "pattern", "first_fraud_txn_id", "n_txns",
        "exposure_usd", "actions_taken", "report_filed", "analyst_notes",
    ]
    case_values = ", ".join(f"${col_index[c]}" for c in case_cols)
    case_idx = col_index["case_id"]
    card_idx = col_index["card_id"]
    return f'''
USE GRAPH {GRAPH_NAME}
CREATE LOADING JOB load_closed_cases FOR GRAPH {GRAPH_NAME} {{
    DEFINE FILENAME f2;
    LOAD f2 TO VERTEX ClosedCase VALUES ({case_values}) USING header="false", separator=",", quote="DOUBLE";
    LOAD f2 TO EDGE ON_CARD VALUES (${case_idx} ClosedCase, ${card_idx} Card) USING header="false", separator=",", quote="DOUBLE";
}}
'''.strip()


async def _run_job_from_csv(
    tg: TigerGraphMCP,
    csv_path: str,
    job_name: str,
    file_tag: str,
    chunk_rows: int = 20_000,
    timeout_ms: int = 600_000,
) -> int:
    """Streams csv_path (skipping its header line) to an already-CREATEd
    loading job in batches of chunk_rows raw text lines, via the
    tigergraph__run_loading_job_with_data MCP tool -- the only mechanism
    that actually uploads local bytes to this server (see module note
    above). Text-mode file reading normalizes \\r\\n/\\n so chunks are always
    joined with a plain \\n, matching the job's default EOL. Returns the
    total number of data lines sent."""

    async def _send(lines: list[str]) -> None:
        # Live-observed failure mode worth guarding against: a `RUN` sent
        # immediately after (re)creating the loading job once came back with
        # `success: True` and a plausible-looking response, but the server
        # had actually parsed zero valid lines (fileLevel.validLine == 0,
        # every vertex/edge type's validObject == 0) -- a silent no-op that
        # only `get_vertex_count` after the fact would have caught. A retry
        # of the exact same call/data a few seconds later loaded every row
        # correctly, so this looks like eventual-consistency lag on the
        # server's loading-job catalog rather than a data problem. Since
        # `tg.call()` only raises on an outright tool failure (not on a
        # "successful" call that quietly loaded nothing), check the reported
        # validLine count ourselves and raise so this can't fail silently.
        payload = {
            "data": "\n".join(lines) + "\n",
            "file_tag": file_tag,
            "job_name": job_name,
            "separator": ",",
            "timeout": timeout_ms,
            "size_limit": 200_000_000,
        }
        for attempt in (1, 2):
            result = await tg.call("tigergraph__run_loading_job_with_data", payload)
            try:
                stats = result["data"]["result"][0]["statistics"]["parsingStatistics"]
                valid_lines = stats["fileLevel"]["validLine"]
            except (KeyError, IndexError, TypeError):
                valid_lines = None  # unexpected response shape -- don't block on it, just skip the check
            if valid_lines != 0 or not lines:
                return
            if attempt == 1:
                print(f"  {job_name}: sent {len(lines)} rows but server reported validLine=0 -- retrying once")
                await asyncio.sleep(3)
        raise RuntimeError(
            f"{job_name}: sent {len(lines)} rows but server reported validLine=0 twice in a row "
            f"(silent no-op load, not just a one-off lag). Full response: {result}"
        )

    total = 0
    batch: list[str] = []
    with open(csv_path, "r", encoding="utf-8") as f:
        f.readline()  # discard header -- see module note on why it must be stripped
        for line in f:
            line = line.rstrip("\n").rstrip("\r")
            if not line:
                continue
            batch.append(line)
            if len(batch) >= chunk_rows:
                await _send(batch)
                total += len(batch)
                print(f"  {job_name}: {total} rows loaded so far")
                batch = []
    if batch:
        await _send(batch)
        total += len(batch)
    print(f"{job_name}: finished, {total} rows loaded total")
    return total


async def load_cards_and_made_edges(
    tg: TigerGraphMCP, transactions_csv_path: str, case_pack_csv: str, closed_cases_csv: str
) -> dict[str, str]:
    """Creates every Card vertex and its OWNS edge with the CORRECT resolved card_id
    (via Task 3's build_card_id_map/card_id_for), then streams transactions.csv once
    to wire MADE edges using that same resolved card_id per row -- so no card is ever
    created under the wrong ID in the first place. Returns the full customer_id ->
    card_id map (13,500ish entries) for logging/verification."""
    case_pack_df = pd.read_csv(case_pack_csv)
    closed_cases_df = pd.read_csv(closed_cases_csv)
    override_map = build_card_id_map(case_pack_df, closed_cases_df)

    unique_customers = pd.read_csv(transactions_csv_path, usecols=["customer_id"])["customer_id"].unique()
    full_map = {cid: card_id_for(cid, override_map) for cid in unique_customers}

    # Phase 1: Card vertices + OWNS edges, straight from the small in-memory map --
    # no need to touch the 708MB transactions file for this part.
    #
    # Deviation from the brief's literal code: the brief used raw
    # `tg.gsql('INSERT INTO VERTEX/EDGE ... VALUES(...)')` statements for this.
    # Confirmed live that this server's `/gsql/v1/statements` endpoint (which
    # `tg.gsql()` posts to) does NOT accept "INSERT" as a top-level statement at
    # all -- a bare single `INSERT INTO VERTEX Card VALUES(...)` was rejected
    # with a parser error whose "Was expecting one of" list includes "upsert",
    # "use", "select", etc. but never "insert", with or without a `USE GRAPH`/
    # `BEGIN...END` wrapper. The correct mechanism on this server is the
    # `tigergraph__add_nodes`/`tigergraph__add_edges` MCP tools, which go
    # through pyTigerGraph's `upsertVertices`/`upsertEdges` (the REST++ batch
    # upsert endpoint) instead -- confirmed live with a tiny real probe before
    # rewriting this to run against the full ~13,500-customer map.
    #
    # Card's schema (Task 4) declares 4 attributes (card_id PK, customer_id,
    # ring_cluster_id, cluster_prior_fraud_rate) but only the first two are
    # supplied here -- ring_cluster_id/cluster_prior_fraud_rate are meant to
    # stay unset until Task 8.5's connected-components pass writes them.
    card_items = list(full_map.items())
    for i in range(0, len(card_items), 1000):
        batch = card_items[i : i + 1000]
        await tg.call(
            "tigergraph__add_nodes",
            {
                "vertex_type": "Card",
                "vertex_id": "card_id",
                "vertices": [
                    {"card_id": card_id, "customer_id": customer_id} for customer_id, card_id in batch
                ],
            },
        )
        await tg.call(
            "tigergraph__add_edges",
            {
                "edge_type": "OWNS",
                "edges": [
                    {
                        "source_type": "Customer",
                        "source_id": customer_id,
                        "target_type": "Card",
                        "target_id": card_id,
                    }
                    for customer_id, card_id in batch
                ],
            },
        )
    print(f"Created {len(full_map)} Card vertices with resolved card_id (OWNS edges included)")

    # Phase 2: MADE edges. This is the one part that has to stream the full file,
    # since transactions.csv only has customer_id per row, never the resolved card_id.
    made_batch: list[dict] = []
    edge_count = 0

    async def _flush_made(batch: list[dict]) -> None:
        await tg.call("tigergraph__add_edges", {"edge_type": "MADE", "edges": batch})

    for chunk in pd.read_csv(transactions_csv_path, usecols=["TransactionID", "customer_id"], chunksize=50_000):
        for txn_id, customer_id in zip(chunk["TransactionID"], chunk["customer_id"]):
            card_id = full_map[customer_id]
            made_batch.append(
                {
                    "source_type": "Card",
                    "source_id": card_id,
                    "target_type": "Transaction",
                    # TransactionID is STRING-typed in the schema but pandas infers it
                    # as int64 from the CSV -- cast explicitly so the upsert targets
                    # the same string primary key load_transactions already created.
                    "target_id": str(txn_id),
                }
            )
            if len(made_batch) >= 1000:
                await _flush_made(made_batch)
                edge_count += len(made_batch)
                if edge_count % 50_000 == 0:
                    print(f"  MADE edges: {edge_count} so far")
                made_batch = []
    if made_batch:
        await _flush_made(made_batch)
        edge_count += len(made_batch)
    print(f"Created {edge_count} MADE edges")

    return full_map


async def run_all_loading_jobs(
    tg: TigerGraphMCP, transactions_csv: str, closed_cases_csv: str, case_pack_csv: str
) -> None:
    # Order matters: Transaction vertices must exist before Phase 2's MADE edges
    # reference them, and Card vertices must exist before closed_cases_loading_job_gsql's
    # ON_CARD edge references them.
    print(await tg.gsql(transactions_loading_job_gsql(transactions_csv)))
    await _run_job_from_csv(tg, transactions_csv, "load_transactions", "f1", chunk_rows=20_000)
    await load_cards_and_made_edges(tg, transactions_csv, case_pack_csv, closed_cases_csv)
    print(await tg.gsql(closed_cases_loading_job_gsql(closed_cases_csv)))
    await _run_job_from_csv(tg, closed_cases_csv, "load_closed_cases", "f2", chunk_rows=10_000)
