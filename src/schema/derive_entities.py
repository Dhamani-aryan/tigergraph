from __future__ import annotations

import csv
import hashlib

from src.tg_client import TigerGraphMCP

GRAPH_NAME = "FraudInvestigation"


def device_id_for(device_info: str, os_: str, browser: str, screen: str) -> str:
    raw = f"{device_info}|{os_}|{browser}|{screen}"
    return "D" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


async def load_device_profiles(tg: TigerGraphMCP, identity_csv_path: str) -> int:
    """Loads DeviceProfile vertices (deduped by device_id_for's hash of
    DeviceInfo/id_30/id_31/id_33) and FROM_DEVICE edges from Transaction to
    DeviceProfile, straight from identity.csv's per-row device fingerprint
    columns. Rows with no device signal at all (blank DeviceInfo/id_30/id_31)
    are skipped -- there is nothing to link. Returns the number of
    identity.csv rows that produced a DeviceProfile + FROM_DEVICE edge (not
    the number of distinct DeviceProfile vertices, since many transactions
    legitimately share one device fingerprint)."""
    inserted = 0
    with open(identity_csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        batch: list[tuple[str, str, str, str, str, str]] = []
        for row in reader:
            device_info = row.get("DeviceInfo") or ""
            os_ = row.get("id_30") or ""
            browser = row.get("id_31") or ""
            screen = row.get("id_33") or ""
            if not device_info and not os_ and not browser:
                continue
            device_id = device_id_for(device_info, os_, browser, screen)
            batch.append((device_id, device_info, os_, browser, screen, row["TransactionID"]))
            if len(batch) >= 500:
                await _flush_device_batch(tg, batch)
                inserted += len(batch)
                batch = []
        if batch:
            await _flush_device_batch(tg, batch)
            inserted += len(batch)
    return inserted


async def _flush_device_batch(
    tg: TigerGraphMCP, batch: list[tuple[str, str, str, str, str, str]]
) -> None:
    # Task 7 confirmed live against this server: raw tg.gsql("INSERT INTO
    # VERTEX/EDGE ...") is rejected outright by the /gsql/v1/statements
    # endpoint (the parser's "expecting one of" list never includes "insert").
    # Use the same tigergraph__add_nodes/add_edges MCP tools Task 7 established
    # instead -- REST++ batch upsert, not raw GSQL INSERT.
    await tg.call(
        "tigergraph__add_nodes",
        {
            "vertex_type": "DeviceProfile",
            "vertex_id": "device_id",
            "vertices": [
                {
                    "device_id": device_id,
                    "device_info": device_info,
                    "os": os_,
                    "browser": browser,
                    "screen": screen,
                }
                for device_id, device_info, os_, browser, screen, _ in batch
            ],
        },
    )
    await tg.call(
        "tigergraph__add_edges",
        {
            "edge_type": "FROM_DEVICE",
            "edges": [
                {
                    "source_type": "Transaction",
                    "source_id": str(txn_id),
                    "target_type": "DeviceProfile",
                    "target_id": device_id,
                }
                for device_id, _, _, _, _, txn_id in batch
            ],
        },
    )


async def load_closed_case_multi_edges(tg: TigerGraphMCP, closed_cases_csv_path: str) -> None:
    """Loads INVOLVES (ClosedCase -> Transaction) and CONNECTED_TO
    (ClosedCase -> Card) edges from closed_cases_history.csv's
    pipe-separated txn_ids/connected_card_ids fields. See the module's
    docstring-equivalent note below on why INVOLVES and CONNECTED_TO are
    always flushed as two separate add_edges calls, never combined."""
    with open(closed_cases_csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        involves_batch: list[dict] = []
        connected_batch: list[dict] = []
        for row in reader:
            case_id = row["case_id"]
            for txn_id in (row.get("txn_ids") or "").split("|"):
                if txn_id:
                    involves_batch.append(
                        {
                            "source_type": "ClosedCase",
                            "source_id": case_id,
                            "target_type": "Transaction",
                            "target_id": txn_id,
                        }
                    )
            for card_id in (row.get("connected_card_ids") or "").split("|"):
                if card_id:
                    connected_batch.append(
                        {
                            "source_type": "ClosedCase",
                            "source_id": case_id,
                            "target_type": "Card",
                            "target_id": card_id,
                        }
                    )
            # add_edges requires every edge in one batch to share the same
            # source/target vertex types -- flush INVOLVES (ClosedCase ->
            # Transaction) and CONNECTED_TO (ClosedCase -> Card) separately,
            # never mixed, even though the source loop above is interleaved
            # per CSV row.
            if len(involves_batch) >= 500:
                await tg.call("tigergraph__add_edges", {"edge_type": "INVOLVES", "edges": involves_batch})
                involves_batch = []
            if len(connected_batch) >= 500:
                await tg.call("tigergraph__add_edges", {"edge_type": "CONNECTED_TO", "edges": connected_batch})
                connected_batch = []
        if involves_batch:
            await tg.call("tigergraph__add_edges", {"edge_type": "INVOLVES", "edges": involves_batch})
        if connected_batch:
            await tg.call("tigergraph__add_edges", {"edge_type": "CONNECTED_TO", "edges": connected_batch})
