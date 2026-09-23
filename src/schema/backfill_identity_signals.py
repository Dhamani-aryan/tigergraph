from __future__ import annotations

import csv

from src.tg_client import TigerGraphMCP

GRAPH_NAME = "FraudInvestigation"

# --------------------------------------------------------------------------
# Why this exists (2026-09-23 fix)
# --------------------------------------------------------------------------
# `identity.csv` was only ever read for DeviceProfile's own columns
# (DeviceInfo/id_30/id_31/id_33, see derive_entities.py's load_device_profiles).
# `id_15` (the README's own glossary: "id_15 (device New/Found)") was never
# loaded onto anything -- not Transaction, not DeviceProfile, not an edge --
# so it was simply unavailable to the agent. This was masked rather than
# caught: `graph_flow.py`'s `evidence_request_node` computed `is_new_device`
# from `shared_device` (a device-SHARING signal) instead, which is a
# different fact -- a device can be shared across many cards (fraud ring)
# without being NEW to this particular account, and vice versa. Confirmed
# live in review: HHG-017's own case conflated these two.
#
# id_15/id_23/id_34/DeviceType are all per-TRANSACTION facts (a device
# fingerprint can be "New" on one transaction and not on another for the
# same account), so they belong on Transaction, not on the deduped
# DeviceProfile vertex -- adding them there instead would incorrectly imply
# one fixed New/Found value per device fingerprint.
#
# `ALTER VERTEX ... ADD ATTRIBUTE` cannot be run as a bare top-level
# statement on this server -- confirmed live: a plain `ALTER VERTEX
# Transaction ADD ATTRIBUTE (...)` is a hard GSQL parse error ("Was
# expecting: in ..."), the same class of "brief's draft syntax doesn't work
# on this server" finding as every other query in this codebase (see
# src/graph/queries.py's module docstring). This server's GSQL 4.2.5 dialect
# requires wrapping schema changes in a named `SCHEMA_CHANGE JOB` -- and,
# confirmed live on a second attempt, a per-GRAPH job is itself rejected for
# this specific vertex ("Cannot alter shared vertex 'Transaction'! Please
# use global schema change job instead."), because Transaction was declared
# at global scope (`CREATE VERTEX Transaction (...)`, no `FOR GRAPH`, in
# src/schema/build_schema.py -- Task 4's original design, unrelated to this
# fix), not scoped to one graph. A `GLOBAL SCHEMA_CHANGE JOB` (no `FOR
# GRAPH`, no `USE GRAPH` needed) is therefore the only form that works here.
#
# Idempotent by catch: re-running this against an already-altered schema
# fails with a "already exists"/duplicate-attribute style message, which is
# treated as "already done" rather than a real error, matching this file's
# other idempotent backfills (e.g. `backfill_stub_card_customer_ids`).
ALTER_TRANSACTION_GSQL = """
CREATE GLOBAL SCHEMA_CHANGE JOB add_identity_signals_to_transaction {
    ALTER VERTEX Transaction ADD ATTRIBUTE (
        id_15 STRING DEFAULT "",
        id_23 STRING DEFAULT "",
        id_34 STRING DEFAULT "",
        device_type STRING DEFAULT ""
    );
}
RUN GLOBAL SCHEMA_CHANGE JOB add_identity_signals_to_transaction
""".strip()


async def alter_transaction_schema(tg: TigerGraphMCP) -> None:
    result = await tg.gsql(ALTER_TRANSACTION_GSQL)
    text = str(result.get("data", {}).get("result", "")) if isinstance(result, dict) else str(result)
    lowered = text.lower()
    if "already" in lowered or "successfully" in lowered or "finished" in lowered:
        return
    raise RuntimeError(f"ALTER VERTEX Transaction (schema change job) failed: {text}")


async def backfill_identity_signals(tg: TigerGraphMCP, identity_csv_path: str) -> int:
    """Streams identity.csv and upserts id_15/id_23/id_34/DeviceType onto the
    matching Transaction vertex (already created by load_transactions).
    `add_nodes` on an existing vertex only sets the attributes given here --
    the same partial-upsert pattern `backfill_stub_card_customer_ids` already
    relies on -- so this cannot clobber any other Transaction attribute.
    Returns the number of identity.csv rows processed (144,432 expected;
    in-person/no-identity-record transactions simply keep the "" default)."""
    processed = 0
    batch: list[dict] = []
    with open(identity_csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            batch.append(
                {
                    "transaction_id": row["TransactionID"],
                    "id_15": row.get("id_15") or "",
                    "id_23": row.get("id_23") or "",
                    "id_34": row.get("id_34") or "",
                    "device_type": row.get("DeviceType") or "",
                }
            )
            if len(batch) >= 1000:
                await tg.call(
                    "tigergraph__add_nodes",
                    {"vertex_type": "Transaction", "vertex_id": "transaction_id", "vertices": batch},
                )
                processed += len(batch)
                batch = []
    if batch:
        await tg.call(
            "tigergraph__add_nodes",
            {"vertex_type": "Transaction", "vertex_id": "transaction_id", "vertices": batch},
        )
        processed += len(batch)
    return processed
