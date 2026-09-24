from __future__ import annotations

from datetime import datetime
from typing import Any

from src.agent.features import (  # noqa: F401 -- re-exported for existing callers
    CardTestingResult,
    CnpBurstResult,
    RecurrenceResult,
    RegionSignal,
    compute_behavior_profile,
    detect_card_testing,
    detect_cnp_burst,
    detect_out_of_region,
    detect_recurring_charge,
    parse_ts,
)

# --------------------------------------------------------------------------
# Episode assembly
# --------------------------------------------------------------------------
# The flagged transaction is where the alert fired, not necessarily the whole
# episode (README). Which rows belong to the same suspicious episode is
# decided here from the structured detector results in features.py:
#
#   1. exact card-testing sequence that contains the flagged transaction;
#   2. strict out-of-region: the card-present rows in the new region within
#      48h (on or before cutoff);
#   3. the documented 2-4 online CNP burst, only when it is not routine for
#      this card's own baseline;
#   4. otherwise the flagged transaction alone.
#
# In-person rows can never enter a CNP episode, and a > 4 online window is
# never truncated to "the four nearest" and relabelled as the documented
# burst. A legitimate final verdict discards the episode entirely
# (run_case.py): empty affected_txn_ids, zero exposure.


class Episode:
    def __init__(
        self,
        txn_ids: list[str],
        first_txn_id: str,
        exposure_usd: float,
        detected_pattern: str | None,
        first_date: str = "",
        last_date: str = "",
        basis: str = "flagged_only",
    ) -> None:
        self.txn_ids = txn_ids
        self.first_txn_id = first_txn_id
        self.exposure_usd = exposure_usd
        # "card_testing" | "out_of_region" | "cnp_burst" | None
        self.detected_pattern = detected_pattern
        self.first_date = first_date
        self.last_date = last_date
        self.basis = basis

    def as_dict(self) -> dict[str, Any]:
        return {
            "txn_ids": self.txn_ids,
            "first_txn_id": self.first_txn_id,
            "exposure_usd": self.exposure_usd,
            "detected_pattern": self.detected_pattern,
            "first_date": self.first_date,
            "last_date": self.last_date,
            "basis": self.basis,
        }


def build_episode(
    window: list[dict[str, Any]],
    flagged_txn_id: str,
    cutoff_ts: str | None = None,
    *,
    card_testing: CardTestingResult | None = None,
    cnp: CnpBurstResult | None = None,
    region: RegionSignal | None = None,
) -> Episode:
    flagged_txn_id = str(flagged_txn_id)
    if card_testing is None:
        card_testing = detect_card_testing(window, flagged_txn_id, cutoff_ts)
    if cnp is None or region is None:
        profile = compute_behavior_profile(window, flagged_txn_id, cutoff_ts)
        if cnp is None:
            cnp = detect_cnp_burst(window, flagged_txn_id, cutoff_ts, profile.baseline_online_per_48h)
        if region is None:
            region = detect_out_of_region(window, flagged_txn_id, cutoff_ts, profile)

    if card_testing.fired:
        txn_ids, detected, basis = card_testing.txn_ids, "card_testing", "card_testing_sequence"
    elif region.fired:
        txn_ids, detected, basis = region.episode_ids or [flagged_txn_id], "out_of_region", "strict_out_of_region"
    elif cnp.documented_burst and cnp.temporal_signal:
        txn_ids, detected, basis = cnp.online_txn_ids, "cnp_burst", "documented_cnp_burst"
    else:
        txn_ids, detected, basis = [flagged_txn_id], None, "flagged_only"
    if flagged_txn_id not in txn_ids:
        txn_ids = [*txn_ids, flagged_txn_id]

    by_id = {str(t["id"]): t for t in window if t.get("id") is not None}
    rows = [by_id[tid] for tid in dict.fromkeys(txn_ids) if tid in by_id]
    if not rows:
        rows = [{"id": flagged_txn_id, "ts": None, "TransactionAmt": 0.0}]
    ordered = sorted(
        ((r, parse_ts(r.get("ts"))) for r in rows), key=lambda pair: pair[1] or datetime.max
    )
    ids = [str(r["id"]) for r, _ in ordered]
    dated = [ts for _, ts in ordered if ts is not None]
    exposure = round(sum(abs(float(r.get("TransactionAmt") or 0)) for r, _ in ordered), 2)
    return Episode(
        txn_ids=ids,
        first_txn_id=ids[0],
        exposure_usd=exposure,
        detected_pattern=detected,
        first_date=dated[0].strftime("%Y-%m-%d") if dated else "",
        last_date=dated[-1].strftime("%Y-%m-%d") if dated else "",
        basis=basis,
    )
