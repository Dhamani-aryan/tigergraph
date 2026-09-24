from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from src.schema.card_ids import build_card_id_map, card_id_for

# Answer-quality fix (2026-09-23): README rule "Every ID in your answer files
# must exist in this dataset" was never actually checked anywhere. This
# module builds the real, dataset-backed ID universe once (transaction ids,
# card ids -- including every override/backfilled one, exactly as
# src/schema/loading_jobs.py's load_cards_and_made_edges built them -- closed
# case ids, customer ids), so validate_outputs.py can catch a hallucinated
# ID before it ships in a submitted answer file.


def _default_data_dir() -> Path:
    """The CSVs are gitignored and live one level above every worktree
    (shared across worktrees, per this repo's `.worktrees/` convention --
    see .gitignore). Checks the current working directory first (so running
    from the outer `tigergraph/` folder, as scripts/load_data.py already
    assumes, just works), then falls back to the fixed relative position
    from this file's own location."""
    cwd_candidate = Path.cwd()
    if (cwd_candidate / "case_pack.csv").exists():
        return cwd_candidate
    # src/run/dataset_index.py -> run -> src -> <worktree root> -> .worktrees -> tigergraph
    fixed_candidate = Path(__file__).resolve().parents[4]
    return fixed_candidate


@dataclass(frozen=True)
class DatasetIndex:
    transaction_ids: frozenset[str]
    card_ids: frozenset[str]
    customer_ids: frozenset[str]
    closed_case_ids: frozenset[str]
    # Task 14 semantic validation: channel per transaction and each case's
    # flagged transaction, so channel rules (CNP evidence cites online rows
    # only; out-of-region never on an online flagged transaction) can be
    # checked against the dataset itself. Empty when the source CSV lacks
    # the column (e.g. small synthetic fixtures).
    txn_channel: dict[str, str] = field(default_factory=dict, hash=False, compare=False)
    case_flagged_txn: dict[str, str] = field(default_factory=dict, hash=False, compare=False)

    def channel_of(self, txn_id: str) -> str | None:
        return self.txn_channel.get(str(txn_id))

    def has_transaction(self, txn_id: str) -> bool:
        return txn_id in self.transaction_ids

    def has_card(self, card_id: str) -> bool:
        return card_id in self.card_ids

    def has_customer(self, customer_id: str) -> bool:
        return customer_id in self.customer_ids

    def has_closed_case(self, case_id: str) -> bool:
        return case_id in self.closed_case_ids


_CACHED_INDEX: DatasetIndex | None = None


def load_dataset_index(data_dir: str | Path | None = None) -> DatasetIndex:
    """Builds (and caches for the process) the full valid-ID universe.
    Reading transactions.csv's TransactionID/customer_id columns is the only
    genuinely large read here (~590k rows, two columns) -- still a few
    seconds, not minutes."""
    global _CACHED_INDEX
    if _CACHED_INDEX is not None and data_dir is None:
        return _CACHED_INDEX

    base = Path(data_dir) if data_dir is not None else _default_data_dir()
    txns_path = base / "transactions.csv"
    case_pack_path = base / "case_pack.csv"
    closed_cases_path = base / "closed_cases_history.csv"

    header = pd.read_csv(txns_path, nrows=0).columns
    usecols = ["TransactionID", "customer_id"] + (["channel"] if "channel" in header else [])
    txns = pd.read_csv(txns_path, usecols=usecols)
    transaction_ids = frozenset(txns["TransactionID"].astype(str))
    txn_channel = (
        dict(zip(txns["TransactionID"].astype(str), txns["channel"].astype(str).str.strip().str.lower()))
        if "channel" in txns.columns else {}
    )
    customer_ids = frozenset(txns["customer_id"].astype(str).unique())

    case_pack_df = pd.read_csv(case_pack_path)
    closed_cases_df = pd.read_csv(closed_cases_path)
    closed_case_ids = frozenset(closed_cases_df["case_id"].astype(str))

    # Exactly the map load_cards_and_made_edges built at load time (Task 7),
    # so "every card this pipeline could ever have created" is the valid set
    # -- not just the -K1 default.
    override_map = build_card_id_map(case_pack_df, closed_cases_df)
    card_ids = {card_id_for(cid, override_map) for cid in customer_ids}
    card_ids |= set(override_map.values())

    index = DatasetIndex(
        transaction_ids=transaction_ids,
        card_ids=frozenset(card_ids),
        customer_ids=customer_ids,
        closed_case_ids=closed_case_ids,
        txn_channel=txn_channel,
        case_flagged_txn=dict(zip(case_pack_df["case_id"].astype(str), case_pack_df["flagged_txn_id"].astype(str))),
    )
    if data_dir is None:
        _CACHED_INDEX = index
    return index
