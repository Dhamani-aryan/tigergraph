from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# Why this module exists (Task 14 evidence-classification fix, 2026-09-24)
# --------------------------------------------------------------------------
# The all-fraud diagnostic batch (runs/diagnostic-all-fraud/) showed that
# every one of the 20 cases was pushed to fraud by detectors that did not
# mean what the README says they mean: a "CNP burst" built from in-person
# rows, an "out-of-region" signal on online transactions and on regions the
# card had used hundreds of times, a recurrence check that matched any
# similar amount, a device check that treated a 20-card fingerprint
# collision as a ring, and a simulator that turned the model's own
# suspicion into a customer denial.
#
# Everything here is computed from `card_window` (and, for the network
# signal, the device_network query) BEFORE any LLM call, is deterministic,
# and is written into the trace so every number can be audited. Missing data
# is always reported as unavailable -- it never becomes a fraud signal.
#
# Baselines use only transactions strictly before the flagged transaction.
# The flagged transaction's own risk_score is alert context only and is never
# an evidence family.

STABLE_HISTORY_MIN_TXNS = 20
EXTREME_AMOUNT_RATIO = 3.0
EXTREME_AMOUNT_PERCENTILE = 0.99
NORMAL_AMOUNT_RATIO = 2.0
NORMAL_AMOUNT_PERCENTILE = 0.95
ESTABLISHED_PRODUCT_MIN_COUNT = 3
ESTABLISHED_PRODUCT_MIN_SHARE = 0.01
ESTABLISHED_REGION_MIN_IN_PERSON = 3
EPISODE_WINDOW_HOURS = 48.0
# Burst baseline: prior online activity rate, measured over history that
# ends before the suspected 48-hour episode, needs at least this much span
# to be meaningful.
BURST_BASELINE_MIN_SPAN_DAYS = 7.0

CARD_TESTING_SMALL_AMOUNT_USD = 5.0
CARD_TESTING_MIN_SMALL_AUTHS = 3
CARD_TESTING_WINDOW_HOURS = 1.0
CARD_TESTING_FOLLOWUP_HOURS = 6.0
CARD_TESTING_LARGER_PURCHASE_USD = 20.0
R5_BLOCK_CLEARED_PURCHASE_USD = 100.0

CNP_BURST_MIN_TXNS = 2
CNP_BURST_MAX_TXNS = 4

OUT_OF_REGION_HOME_MIN_TXNS = 3
OUT_OF_REGION_HOME_MIN_SHARE = 0.40
OUT_OF_REGION_TRIP_LOOKBACK_DAYS = 7
OUT_OF_REGION_TRIP_MIN_DAYS = 2

RECURRING_MIN_DAYS = 25
RECURRING_MAX_DAYS = 35
RECURRING_SECOND_MIN_DAYS = 55
RECURRING_SECOND_MAX_DAYS = 70
RECURRING_THIRD_MIN_DAYS = 85
RECURRING_THIRD_MAX_DAYS = 105
RECURRING_MIN_TOLERANCE_USD = 0.50
RECURRING_TOLERANCE_SHARE = 0.01
RECURRING_MAX_COLLISION_RATE = 0.01
RECURRING_MIN_SAME_CHANNEL_PRODUCT_TXNS = 20

DEVICE_PROFILE_MAX_CARDS = 20
DEVICE_ACTIVITY_WINDOW_HOURS = 48.0
DEVICE_CONFIRMED_FRAUD_LOOKBACK_DAYS = 30
DEVICE_COORDINATION_WINDOW_HOURS = 6.0
DEVICE_NEAR_IDENTICAL_AMOUNT_USD = 1.0
DEVICE_NEAR_IDENTICAL_AMOUNT_SHARE = 0.02
DEVICE_HIGH_RISK_SCORE = 0.70
DEVICE_SIMILAR_AMOUNT_SHARE = 0.25

FAMILY_BEHAVIORAL = "behavioral_anomaly"
FAMILY_TEMPORAL = "temporal_pattern"
FAMILY_IDENTITY = "identity_anomaly"
FAMILY_GEOGRAPHIC = "geographic_anomaly"
FAMILY_NETWORK = "direct_network_corroboration"
FAMILY_CUSTOMER = "customer_statement"
FAMILY_PRIOR_CASE = "closely_matched_prior_case"
EVIDENCE_FAMILIES = (
    FAMILY_BEHAVIORAL, FAMILY_TEMPORAL, FAMILY_IDENTITY, FAMILY_GEOGRAPHIC,
    FAMILY_NETWORK, FAMILY_CUSTOMER, FAMILY_PRIOR_CASE,
)
# The same families read in the benign direction (each family is counted in
# at most one direction per case).
BENIGN_BEHAVIORAL = "behavioral_consistency"
BENIGN_IDENTITY = "identity_consistency"
BENIGN_GEOGRAPHIC = "geographic_consistency"
BENIGN_CUSTOMER = "customer_confirmation"
BENIGN_PRIOR_CASE = "closely_matched_cleared_case"

# A vector hit only counts as a "closely matched" prior case below this
# cosine distance. Measured on this dataset: generic trigger prose lands
# every ClosedCase at ~0.17-0.19, so anything near that band is topical
# similarity, not a matched case.
CLOSE_PRIOR_CASE_MAX_DISTANCE = 0.10


# --------------------------------------------------------------------------
# Row normalization
# --------------------------------------------------------------------------
def parse_ts(ts: Any) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.strptime(str(ts), "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def normalize_channel(value: Any) -> str | None:
    """`online` / `in_person`, or None for missing/unknown -- never guessed.
    A missing channel is not online (so it can never enter a CNP set) and
    not in-person (so it can never fire out-of-region)."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("online", "in_person"):
        return text
    return None


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("nan", "none", "null"):
        return None
    return text


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


@dataclass(frozen=True)
class Txn:
    id: str
    ts: datetime
    amount: float | None
    channel: str | None
    product: str | None
    region: str | None
    id_15: str | None
    id_23: str | None
    risk_score: float | None


def normalize_rows(window: list[dict[str, Any]], cutoff_ts: str | None = None) -> list[Txn]:
    """Card-window dicts -> sorted Txn rows. Rows with no parseable timestamp
    are dropped (they cannot be placed in time); rows after `cutoff_ts` are
    dropped (future information)."""
    cutoff = parse_ts(cutoff_ts) if cutoff_ts else None
    rows: list[Txn] = []
    for t in window:
        ts = parse_ts(t.get("ts"))
        if ts is None or t.get("id") in (None, ""):
            continue
        if cutoff is not None and ts > cutoff:
            continue
        product = _clean_str(t.get("ProductCD"))
        rows.append(Txn(
            id=str(t["id"]),
            ts=ts,
            amount=_float(t.get("TransactionAmt")),
            channel=normalize_channel(t.get("channel")),
            product=product.upper() if product else None,
            region=_clean_str(t.get("addr1")),
            id_15=_clean_str(t.get("id_15")),
            id_23=_clean_str(t.get("id_23")),
            risk_score=_float(t.get("risk_score")),
        ))
    rows.sort(key=lambda r: (r.ts, r.id))
    return rows


def _within(a: datetime, b: datetime, hours: float) -> bool:
    return abs(a - b) <= timedelta(hours=hours)


# --------------------------------------------------------------------------
# Behavior profile
# --------------------------------------------------------------------------
AmountClass = Literal["extreme", "normal", "elevated", "unavailable"]
ProductClass = Literal["established", "rare", "unseen", "unavailable"]
RegionClass = Literal["established", "seen", "new", "unavailable", "not_applicable"]


class BehaviorProfile(BaseModel):
    flagged_txn_id: str
    flagged_found: bool
    flagged_ts: str = ""
    flagged_amount: float | None = None
    risk_score_context_only: float | None = None
    history_count: int = 0
    history_span_days: float | None = None
    stable_history: bool = False
    amount_median: float | None = None
    amount_ratio: float | None = None
    amount_percentile: float | None = None
    amount_class: AmountClass = "unavailable"
    flagged_product: str | None = None
    product_prior_count: int = 0
    product_prior_share: float | None = None
    product_class: ProductClass = "unavailable"
    flagged_channel: str | None = None
    channel_prior_count: int = 0
    flagged_region: str | None = None
    flagged_region_prior_count: int = 0
    flagged_region_prior_in_person_count: int = 0
    region_class: RegionClass = "unavailable"
    home_region: str | None = None
    home_region_count: int = 0
    home_region_share: float | None = None
    in_person_with_region_count: int = 0
    online_txn_ids_48h: list[str] = Field(default_factory=list)
    card_present_txn_ids_48h: list[str] = Field(default_factory=list)
    is_new_device: bool | None = None
    device_status: str | None = None
    is_proxy: bool | None = None
    proxy_type: str | None = None
    recurring_band_count: int = 0
    recurring_band_denominator: int = 0
    recurring_band_frequency: float | None = None
    baseline_online_per_48h: float | None = None
    independent_evidence_families: list[str] = Field(default_factory=list)
    benign_families: list[str] = Field(default_factory=list)
    suspicious_findings: list[str] = Field(default_factory=list)
    benign_findings: list[str] = Field(default_factory=list)
    unavailable_findings: list[str] = Field(default_factory=list)


def recurring_tolerance(amount: float) -> float:
    return max(RECURRING_MIN_TOLERANCE_USD, RECURRING_TOLERANCE_SHARE * abs(amount))


def _find_flagged(rows: list[Txn], flagged_txn_id: str) -> Txn | None:
    return next((r for r in rows if r.id == str(flagged_txn_id)), None)


def _prior(rows: list[Txn], flagged: Txn) -> list[Txn]:
    return [r for r in rows if r.ts < flagged.ts and r.id != flagged.id]


def compute_behavior_profile(
    window: list[dict[str, Any]], flagged_txn_id: str, cutoff_ts: str | None = None
) -> BehaviorProfile:
    rows = normalize_rows(window, cutoff_ts)
    flagged = _find_flagged(rows, flagged_txn_id)
    if flagged is None:
        return BehaviorProfile(
            flagged_txn_id=str(flagged_txn_id), flagged_found=False,
            unavailable_findings=["flagged transaction not present in card_window"],
        )

    prior = _prior(rows, flagged)
    n = len(prior)
    p = BehaviorProfile(
        flagged_txn_id=flagged.id,
        flagged_found=True,
        flagged_ts=flagged.ts.strftime("%Y-%m-%d %H:%M:%S"),
        flagged_amount=flagged.amount,
        risk_score_context_only=flagged.risk_score,
        history_count=n,
        history_span_days=round((flagged.ts - prior[0].ts).total_seconds() / 86400, 2) if prior else None,
        stable_history=n >= STABLE_HISTORY_MIN_TXNS,
        flagged_product=flagged.product,
        flagged_channel=flagged.channel,
        flagged_region=flagged.region,
    )

    # -- amount --
    amounts = [r.amount for r in prior if r.amount is not None]
    if amounts:
        p.amount_median = round(statistics.median(amounts), 2)
    if flagged.amount is not None and amounts:
        if p.amount_median and p.amount_median > 0:
            p.amount_ratio = round(flagged.amount / p.amount_median, 3)
        p.amount_percentile = round(sum(1 for a in amounts if a < flagged.amount) / len(amounts), 4)
    if p.stable_history and p.amount_ratio is not None and p.amount_percentile is not None:
        if p.amount_ratio >= EXTREME_AMOUNT_RATIO and p.amount_percentile >= EXTREME_AMOUNT_PERCENTILE:
            p.amount_class = "extreme"
        elif p.amount_ratio <= NORMAL_AMOUNT_RATIO and p.amount_percentile <= NORMAL_AMOUNT_PERCENTILE:
            p.amount_class = "normal"
        else:
            p.amount_class = "elevated"

    # -- product --
    if flagged.product is not None:
        with_product = [r for r in prior if r.product is not None]
        p.product_prior_count = sum(1 for r in with_product if r.product == flagged.product)
        if with_product:
            p.product_prior_share = round(p.product_prior_count / len(with_product), 4)
        if p.stable_history and p.product_prior_share is not None:
            if p.product_prior_count == 0:
                p.product_class = "unseen"
            elif (p.product_prior_count >= ESTABLISHED_PRODUCT_MIN_COUNT
                  and p.product_prior_share >= ESTABLISHED_PRODUCT_MIN_SHARE):
                p.product_class = "established"
            else:
                p.product_class = "rare"

    # -- channel --
    if flagged.channel is not None:
        p.channel_prior_count = sum(1 for r in prior if r.channel == flagged.channel)

    # -- region --
    in_person_regions = [r.region for r in prior if r.channel == "in_person" and r.region is not None]
    p.in_person_with_region_count = len(in_person_regions)
    if in_person_regions:
        counts = Counter(in_person_regions)
        home, home_count = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        p.home_region = home
        p.home_region_count = home_count
        p.home_region_share = round(home_count / len(in_person_regions), 4)
    if flagged.region is not None:
        p.flagged_region_prior_count = sum(1 for r in prior if r.region == flagged.region)
        p.flagged_region_prior_in_person_count = sum(
            1 for r in prior if r.region == flagged.region and r.channel == "in_person"
        )
    if flagged.channel != "in_person":
        p.region_class = "not_applicable"
    elif flagged.region is None:
        p.region_class = "unavailable"
    elif p.flagged_region_prior_in_person_count >= ESTABLISHED_REGION_MIN_IN_PERSON:
        p.region_class = "established"
    elif p.flagged_region_prior_count > 0:
        p.region_class = "seen"
    else:
        p.region_class = "new"

    # -- 48h neighbourhood (both sides of the flagged txn, never past cutoff) --
    for r in rows:
        if r.id == flagged.id or not _within(r.ts, flagged.ts, EPISODE_WINDOW_HOURS):
            continue
        if r.channel == "online":
            p.online_txn_ids_48h.append(r.id)
        elif r.channel == "in_person":
            p.card_present_txn_ids_48h.append(r.id)

    # -- identity (online only; in-person rows have no identity record) --
    if flagged.channel == "online":
        p.device_status = flagged.id_15
        if flagged.id_15 == "New":
            p.is_new_device = True
        elif flagged.id_15 == "Found":
            p.is_new_device = False
        if flagged.id_23 is not None:
            p.proxy_type = flagged.id_23
            p.is_proxy = any(k in flagged.id_23.upper() for k in ("ANONYMOUS", "HIDDEN"))

    # -- recurring amount-band frequency (same channel + ProductCD) --
    if flagged.amount is not None and flagged.channel and flagged.product:
        tol = recurring_tolerance(flagged.amount)
        same_cp = [r for r in prior if r.channel == flagged.channel and r.product == flagged.product and r.amount is not None]
        p.recurring_band_denominator = len(same_cp)
        p.recurring_band_count = sum(1 for r in same_cp if abs(r.amount - flagged.amount) <= tol)
        if same_cp:
            p.recurring_band_frequency = round(p.recurring_band_count / len(same_cp), 4)

    # -- online burst baseline, excluding the suspected 48h episode --
    episode_start = flagged.ts - timedelta(hours=EPISODE_WINDOW_HOURS)
    base_rows = [r for r in prior if r.ts < episode_start]
    if base_rows:
        span_h = (episode_start - base_rows[0].ts).total_seconds() / 3600
        if span_h >= BURST_BASELINE_MIN_SPAN_DAYS * 24:
            online_base = sum(1 for r in base_rows if r.channel == "online")
            p.baseline_online_per_48h = round(online_base / (span_h / EPISODE_WINDOW_HOURS), 4)

    return p


# --------------------------------------------------------------------------
# Card testing (README pattern 1, R5) -- exact sequence only
# --------------------------------------------------------------------------
class CardTestingResult(BaseModel):
    fired: bool = False
    reason: str = ""
    txn_ids: list[str] = Field(default_factory=list)
    timestamps: list[str] = Field(default_factory=list)
    amounts: list[float] = Field(default_factory=list)
    small_auth_ids: list[str] = Field(default_factory=list)
    larger_purchase_id: str = ""
    larger_purchase_amount: float = 0.0
    purchase_over_100_cleared: bool = False


def detect_card_testing(
    window: list[dict[str, Any]], flagged_txn_id: str, cutoff_ts: str | None = None
) -> CardTestingResult:
    """>=3 online authorizations under $5 inside one rolling hour, then an
    ONLINE purchase >= $20 within 6 hours of the last small auth. Fires only
    when the flagged transaction is one of the rows in that sequence -- an
    unrelated sequence elsewhere on the card is not attached to this case."""
    rows = normalize_rows(window, cutoff_ts)
    flagged = _find_flagged(rows, flagged_txn_id)
    if flagged is None:
        return CardTestingResult(reason="flagged transaction not in window")
    online = [r for r in rows if r.channel == "online" and r.amount is not None]
    small = [r for r in online if 0 <= r.amount < CARD_TESTING_SMALL_AMOUNT_USD]
    best: CardTestingResult | None = None
    for i in range(len(small)):
        j = i
        while j + 1 < len(small) and small[j + 1].ts - small[i].ts <= timedelta(hours=CARD_TESTING_WINDOW_HOURS):
            j += 1
        cluster = small[i : j + 1]
        if len(cluster) < CARD_TESTING_MIN_SMALL_AUTHS:
            continue
        end = cluster[-1].ts
        followups = [
            r for r in online
            if end < r.ts <= end + timedelta(hours=CARD_TESTING_FOLLOWUP_HOURS)
            and r.amount >= CARD_TESTING_LARGER_PURCHASE_USD
        ]
        if not followups:
            continue
        if flagged in followups:
            purchases = followups[: followups.index(flagged) + 1]
        else:
            purchases = followups[:1]
        seq = cluster + purchases
        if flagged not in seq:
            continue
        larger = purchases[-1]
        best = CardTestingResult(
            fired=True,
            reason=(
                f"{len(cluster)} online authorizations under ${CARD_TESTING_SMALL_AMOUNT_USD:.0f} within "
                f"{(cluster[-1].ts - cluster[0].ts).total_seconds() / 60:.0f} minutes, then an online "
                f"${larger.amount:.2f} purchase {(larger.ts - end).total_seconds() / 3600:.1f}h later; "
                "the flagged transaction is part of this sequence."
            ),
            txn_ids=[r.id for r in seq],
            timestamps=[r.ts.strftime("%Y-%m-%d %H:%M:%S") for r in seq],
            amounts=[round(r.amount, 2) for r in seq],
            small_auth_ids=[r.id for r in cluster],
            larger_purchase_id=larger.id,
            larger_purchase_amount=round(larger.amount, 2),
            purchase_over_100_cleared=any(r.amount > R5_BLOCK_CLEARED_PURCHASE_USD for r in purchases),
        )
        break
    return best or CardTestingResult(reason="no qualifying sequence contains the flagged transaction")


# --------------------------------------------------------------------------
# CNP burst (README patterns 2-3) -- online rows only
# --------------------------------------------------------------------------
class CnpBurstResult(BaseModel):
    flagged_online: bool = False
    online_txn_ids: list[str] = Field(default_factory=list)  # complete qualifying set, incl. flagged
    online_count: int = 0
    excluded_in_person_ids: list[str] = Field(default_factory=list)
    documented_burst: bool = False  # complete set has 2-4 online txns
    high_volume_online: bool = False  # > 4 online txns: NOT the documented burst
    baseline_online_per_48h: float | None = None
    exceeds_baseline: bool | None = None
    reason: str = ""

    @property
    def temporal_signal(self) -> bool:
        """A temporal family member: the documented burst unless the card's
        own baseline shows this volume is routine; high volume only with
        baseline evidence that it is unusual."""
        if self.documented_burst:
            return self.exceeds_baseline is not False
        if self.high_volume_online:
            return self.exceeds_baseline is True
        return False


def detect_cnp_burst(
    window: list[dict[str, Any]], flagged_txn_id: str, cutoff_ts: str | None = None,
    baseline_online_per_48h: float | None = None,
) -> CnpBurstResult:
    rows = normalize_rows(window, cutoff_ts)
    flagged = _find_flagged(rows, flagged_txn_id)
    if flagged is None:
        return CnpBurstResult(reason="flagged transaction not in window")
    nearby = [r for r in rows if _within(r.ts, flagged.ts, EPISODE_WINDOW_HOURS)]
    in_person = [r.id for r in nearby if r.channel == "in_person" and r.id != flagged.id]
    if flagged.channel != "online":
        return CnpBurstResult(
            flagged_online=False, excluded_in_person_ids=in_person,
            reason=f"flagged transaction channel is {flagged.channel or 'unknown'}, not online",
        )
    # The README burst is "two to four within 48 hours": the densest 48-hour
    # window that CONTAINS the flagged transaction, not +/-48h around it
    # (which could stretch a "burst" over 96 hours).
    around = [r for r in nearby if r.channel == "online"]
    online = [flagged]
    for start in around:
        if start.ts > flagged.ts:
            break
        end = start.ts + timedelta(hours=EPISODE_WINDOW_HOURS)
        if end < flagged.ts:
            continue
        candidate = [r for r in around if start.ts <= r.ts <= end]
        if len(candidate) > len(online):
            online = candidate
    n = len(online)
    result = CnpBurstResult(
        flagged_online=True,
        online_txn_ids=[r.id for r in online],
        online_count=n,
        excluded_in_person_ids=in_person,
        documented_burst=CNP_BURST_MIN_TXNS <= n <= CNP_BURST_MAX_TXNS,
        high_volume_online=n > CNP_BURST_MAX_TXNS,
        baseline_online_per_48h=baseline_online_per_48h,
    )
    if baseline_online_per_48h is not None and n >= CNP_BURST_MIN_TXNS:
        result.exceeds_baseline = n >= 2 * baseline_online_per_48h + 1
    if result.documented_burst:
        result.reason = f"{n} online transactions inside one 48h window containing the flagged one (documented 2-4 burst)"
    elif result.high_volume_online:
        result.reason = (
            f"{n} online transactions inside one 48h window: high-volume online activity, not the documented 2-4 burst"
        )
    else:
        result.reason = "only the flagged online transaction within 48h"
    if result.exceeds_baseline is not None:
        result.reason += (
            f"; card baseline {baseline_online_per_48h:.2f} online per 48h -> "
            f"{'above' if result.exceeds_baseline else 'within'} routine volume"
        )
    return result


# --------------------------------------------------------------------------
# Strict out-of-region (README pattern 4)
# --------------------------------------------------------------------------
class RegionSignal(BaseModel):
    fired: bool = False
    reason: str = ""
    flagged_channel: str | None = None
    flagged_region: str | None = None
    home_region: str | None = None
    flagged_region_prior_count: int = 0
    flagged_region_prior_in_person_count: int = 0
    home_region_count: int = 0
    home_region_share: float | None = None
    in_person_with_region_count: int = 0
    home_activity_ids_48h: list[str] = Field(default_factory=list)
    trip_candidate: bool = False
    evidence_ids: list[str] = Field(default_factory=list)
    episode_ids: list[str] = Field(default_factory=list)


def detect_out_of_region(
    window: list[dict[str, Any]], flagged_txn_id: str, cutoff_ts: str | None = None,
    profile: BehaviorProfile | None = None,
) -> RegionSignal:
    rows = normalize_rows(window, cutoff_ts)
    flagged = _find_flagged(rows, flagged_txn_id)
    if flagged is None:
        return RegionSignal(reason="flagged transaction not in window")
    profile = profile or compute_behavior_profile(window, flagged_txn_id, cutoff_ts)
    sig = RegionSignal(
        flagged_channel=flagged.channel,
        flagged_region=flagged.region,
        home_region=profile.home_region,
        flagged_region_prior_count=profile.flagged_region_prior_count,
        flagged_region_prior_in_person_count=profile.flagged_region_prior_in_person_count,
        home_region_count=profile.home_region_count,
        home_region_share=profile.home_region_share,
        in_person_with_region_count=profile.in_person_with_region_count,
    )
    if flagged.channel != "in_person":
        sig.reason = f"flagged transaction is {flagged.channel or 'unknown channel'}; out-of-region requires card-present use"
        return sig
    if flagged.region is None:
        sig.reason = "flagged transaction has no billing region (addr1 missing)"
        return sig
    if profile.flagged_region_prior_count > 0:
        sig.reason = (
            f"region {flagged.region} already appears {profile.flagged_region_prior_count} time(s) in this card's "
            "prior history; it is not a region the cardholder has no history in"
        )
        return sig
    if profile.home_region is None:
        sig.reason = "no home region can be established from prior in-person history"
        return sig
    if profile.home_region_count < OUT_OF_REGION_HOME_MIN_TXNS:
        sig.reason = f"home region {profile.home_region} has only {profile.home_region_count} prior in-person transaction(s)"
        return sig
    if (profile.home_region_share or 0) < OUT_OF_REGION_HOME_MIN_SHARE:
        sig.reason = f"home region {profile.home_region} is only {profile.home_region_share:.0%} of prior in-person activity"
        return sig
    home_48h = [
        r for r in rows
        if r.id != flagged.id and r.channel == "in_person" and r.region == profile.home_region
        and _within(r.ts, flagged.ts, EPISODE_WINDOW_HOURS)
    ]
    sig.home_activity_ids_48h = [r.id for r in home_48h]
    if not home_48h:
        sig.reason = f"no in-person home-region ({profile.home_region}) activity within 48h of the flagged transaction"
        return sig
    # Trip check: several days of in-person purchases away from home just
    # before the flagged one, with no home activity interleaved, is a
    # trip/life-event candidate, not a cloned card.
    lookback = [
        r for r in rows
        if r.channel == "in_person" and r.region is not None
        and flagged.ts - timedelta(days=OUT_OF_REGION_TRIP_LOOKBACK_DAYS) <= r.ts <= flagged.ts
    ]
    last_home = max((r.ts for r in lookback if r.region == profile.home_region and r.ts <= flagged.ts), default=None)
    away = [r for r in lookback if r.region != profile.home_region and (last_home is None or r.ts > last_home)]
    away_days = {r.ts.date() for r in away}
    if len(away_days) >= OUT_OF_REGION_TRIP_MIN_DAYS:
        sig.trip_candidate = True
        sig.reason = (
            f"{len(away_days)} days of continuous away-from-home in-person purchases without interleaved "
            "home activity: trip/life-event candidate, not cloned-card evidence"
        )
        return sig
    sig.fired = True
    sig.reason = (
        f"card-present use in region {flagged.region} (0 prior transactions there) while home region "
        f"{profile.home_region} ({profile.home_region_count} prior in-person, {profile.home_region_share:.0%}) "
        f"shows in-person activity within 48h ({len(home_48h)} transaction(s))"
    )
    sig.evidence_ids = [flagged.id] + sig.home_activity_ids_48h
    sig.episode_ids = [
        r.id for r in rows
        if r.channel == "in_person" and r.region == flagged.region and _within(r.ts, flagged.ts, EPISODE_WINDOW_HOURS)
    ]
    return sig


# --------------------------------------------------------------------------
# Recurring charge (R7)
# --------------------------------------------------------------------------
RecurrenceTier = Literal["none", "candidate", "strong"]
RECURRENCE_PROXY_NOTE = (
    "ProductCD, channel and amount are proxies for 'same merchant': this dataset has no merchant-name column."
)


class RecurrenceResult(BaseModel):
    tier: RecurrenceTier = "none"
    reason: str = ""
    tolerance_usd: float = 0.0
    monthly_match_ids: list[str] = Field(default_factory=list)
    second_cycle_match_ids: list[str] = Field(default_factory=list)
    same_channel_product_prior_count: int = 0
    band_count: int = 0
    band_share: float | None = None
    collision_count: int = 0
    collision_rate: float | None = None
    proxy_note: str = RECURRENCE_PROXY_NOTE


def detect_recurring_charge(
    window: list[dict[str, Any]], flagged_txn_id: str, cutoff_ts: str | None = None
) -> RecurrenceResult:
    """Collision-rate check: the amount band (flagged amount +/- tolerance,
    same channel and ProductCD) must be rare on this card outside the
    recurrence slots themselves (~30/60/90 days back). The slot matches are
    excluded from the collision numerator because they are the hypothesised
    subscription, not coincidences; without that exclusion a genuine monthly
    charge could only ever be 'strong' on cards with 100+ same-product
    transactions. Both the raw band share and the collision rate are
    reported."""
    rows = normalize_rows(window, cutoff_ts)
    flagged = _find_flagged(rows, flagged_txn_id)
    if flagged is None:
        return RecurrenceResult(reason="flagged transaction not in window")
    if flagged.amount is None or flagged.amount <= 0 or not flagged.channel or not flagged.product:
        return RecurrenceResult(reason="flagged amount/channel/ProductCD unavailable")
    tol = recurring_tolerance(flagged.amount)
    same_cp = [
        r for r in _prior(rows, flagged)
        if r.channel == flagged.channel and r.product == flagged.product and r.amount is not None
    ]
    band = [r for r in same_cp if abs(r.amount - flagged.amount) <= tol]

    def gap_days(r: Txn) -> float:
        return (flagged.ts - r.ts).total_seconds() / 86400

    monthly = [r for r in band if RECURRING_MIN_DAYS <= gap_days(r) <= RECURRING_MAX_DAYS]
    second = [r for r in band if RECURRING_SECOND_MIN_DAYS <= gap_days(r) <= RECURRING_SECOND_MAX_DAYS]
    third = [r for r in band if RECURRING_THIRD_MIN_DAYS <= gap_days(r) <= RECURRING_THIRD_MAX_DAYS]
    slot_ids = {r.id for r in monthly + second + third}
    collisions = [r for r in band if r.id not in slot_ids]
    res = RecurrenceResult(
        tolerance_usd=round(tol, 2),
        monthly_match_ids=[r.id for r in monthly],
        second_cycle_match_ids=[r.id for r in second],
        same_channel_product_prior_count=len(same_cp),
        band_count=len(band),
        band_share=round(len(band) / len(same_cp), 4) if same_cp else None,
        collision_count=len(collisions),
        collision_rate=round(len(collisions) / len(same_cp), 4) if same_cp else None,
    )
    if not monthly:
        res.reason = "no same-channel, same-ProductCD, amount-matching transaction 25-35 days earlier"
        return res
    res.tier = "candidate"
    if len(monthly) != 1:
        res.reason = f"{len(monthly)} matches in the 25-35 day window: coincidental, not one recurring charge"
    elif len(same_cp) < RECURRING_MIN_SAME_CHANNEL_PRODUCT_TXNS:
        res.reason = (
            f"only {len(same_cp)} prior same-channel/ProductCD transactions; not enough baseline "
            "to rule out coincidence"
        )
    elif (res.collision_rate or 0) > RECURRING_MAX_COLLISION_RATE:
        res.reason = (
            f"amount band recurs outside the monthly slots in {res.collision_rate:.1%} of same-channel/"
            f"ProductCD history ({len(collisions)} of {len(same_cp)}): collision-prone, not a distinct charge"
        )
    else:
        res.tier = "strong"
        res.reason = (
            f"exactly one amount match (+/-${tol:.2f}) {gap_days(monthly[0]):.0f} days earlier, same channel and "
            f"ProductCD; band collision rate {res.collision_rate:.1%} of {len(same_cp)} prior"
            + (f"; second-cycle match {second[0].id}" if second else "")
        )
    return res


# --------------------------------------------------------------------------
# Direct shared-device evidence (R6)
# --------------------------------------------------------------------------
class DeviceNetworkResult(BaseModel):
    available: bool = False
    device_profile_id: str = ""
    device_profile_label: str = ""
    total_distinct_cards: int = 0
    other_card_count: int = 0
    generic_profile: bool = False
    other_cards_48h: list[str] = Field(default_factory=list)
    matching_txns_48h: list[dict[str, Any]] = Field(default_factory=list)
    meaningful_match: bool = False
    corroborated: bool = False
    corroboration_basis: str = ""
    corroborated_card_ids: list[str] = Field(default_factory=list)
    corroborating_txn_ids: list[str] = Field(default_factory=list)
    confirmed_fraud_case_ids: list[str] = Field(default_factory=list)
    reason: str = ""


def _near_identical(a: float, b: float) -> bool:
    return abs(a - b) <= max(DEVICE_NEAR_IDENTICAL_AMOUNT_USD, DEVICE_NEAR_IDENTICAL_AMOUNT_SHARE * max(abs(a), abs(b)))


def _similar(a: float, b: float) -> bool:
    return abs(a - b) <= DEVICE_SIMILAR_AMOUNT_SHARE * max(abs(a), abs(b))


def evaluate_device_network(
    device_data: dict[str, Any] | None, subject_card_id: str, flagged_ts: str, cutoff_ts: str,
) -> DeviceNetworkResult:
    """`device_data` is `queries.device_network`'s result (or the CSV
    audit's equivalent): the flagged transaction's device profile, how many
    distinct cards used it on/before cutoff, and every transaction on it in
    [flagged-48h, cutoff] with card/customer/amount/risk, plus confirmed-fraud
    ClosedCases involving the profile."""
    if not device_data or not device_data.get("device_profile_id"):
        return DeviceNetworkResult(reason="no device record (in-person or missing identity)")
    fts, cut = parse_ts(flagged_ts), parse_ts(cutoff_ts)
    total = int(device_data.get("total_distinct_cards") or 0)
    res = DeviceNetworkResult(
        available=True,
        device_profile_id=str(device_data.get("device_profile_id")),
        device_profile_label=str(device_data.get("device_profile_label") or ""),
        total_distinct_cards=total,
        other_card_count=max(total - 1, 0),  # the flagged txn is on this profile, so the subject card is in `total`
        generic_profile=total > DEVICE_PROFILE_MAX_CARDS,
    )
    if fts is None or cut is None:
        res.reason = "flagged/cutoff timestamp unavailable"
        return res
    txns = []
    for t in device_data.get("txns") or []:
        ts = parse_ts(t.get("ts"))
        card = t.get("card_id")
        if ts is None or not card or card == subject_card_id or ts > cut:
            continue
        if not _within(ts, fts, DEVICE_ACTIVITY_WINDOW_HOURS):
            continue
        txns.append({**t, "_ts": ts, "_amt": _float(t.get("amount")), "_risk": _float(t.get("risk_score"))})
    txns.sort(key=lambda t: t["_ts"])
    res.other_cards_48h = sorted({t["card_id"] for t in txns})
    res.matching_txns_48h = [
        {k: v for k, v in t.items() if not k.startswith("_")} for t in txns
    ]
    if res.other_card_count == 0:
        res.reason = "no other card has used this device profile"
        return res
    if res.generic_profile:
        res.reason = (
            f"generic device profile: {total} distinct cards on/before cutoff (> {DEVICE_PROFILE_MAX_CARDS}); "
            "contextual only"
        )
        return res
    # A: confirmed fraud on another card tied to this device in the relevant period.
    fraud_cases = []
    for c in device_data.get("fraud_cases") or []:
        ts = parse_ts(c.get("txn_ts"))
        if (c.get("outcome") == "confirmed_fraud" and c.get("card_id") and c["card_id"] != subject_card_id
                and ts is not None and fts - timedelta(days=DEVICE_CONFIRMED_FRAUD_LOOKBACK_DAYS) <= ts <= cut
                and (parse_ts(c.get("closed_at")) is None or parse_ts(c.get("closed_at")) <= cut)):
            fraud_cases.append(c)
    if fraud_cases:
        res.meaningful_match = True
        res.corroborated = True
        res.corroboration_basis = "confirmed_fraud_on_other_card"
        res.confirmed_fraud_case_ids = sorted({c["case_id"] for c in fraud_cases if c.get("case_id")})
        res.corroborated_card_ids = sorted({c["card_id"] for c in fraud_cases})
        res.corroborating_txn_ids = sorted({str(c["txn_id"]) for c in fraud_cases if c.get("txn_id")})
        res.reason = (
            f"confirmed-fraud closed case(s) {', '.join(res.confirmed_fraud_case_ids)} on other card(s) "
            f"used this device profile within {DEVICE_CONFIRMED_FRAUD_LOOKBACK_DAYS} days"
        )
        return res

    if not txns:
        res.reason = f"{res.other_card_count} other card(s) used this profile, none within 48h of the flagged transaction"
        return res
    res.meaningful_match = True

    # B: >= 2 other cards with tightly coordinated transactions on the device.
    # B1 anchors on the flagged transaction itself (the episode window is the
    # short window): >= 2 other cards at near-identical amounts to it, or
    # high-risk other-card transactions at matching amounts.
    flagged_amt = _float(device_data.get("flagged_amount"))
    if flagged_amt is not None:
        near_flagged = [t for t in txns if t["_amt"] is not None and _near_identical(t["_amt"], flagged_amt)]
        risky_flagged = [
            t for t in txns
            if t["_amt"] is not None and t["_risk"] is not None and t["_risk"] >= DEVICE_HIGH_RISK_SCORE
            and _similar(t["_amt"], flagged_amt)
        ]
        for basis, members in (("near_identical_to_flagged", near_flagged),
                               ("high_risk_matching_flagged", risky_flagged)):
            cards = {t["card_id"] for t in members}
            if len(cards) >= 2:
                res.corroborated = True
                res.corroboration_basis = basis
                res.corroborated_card_ids = sorted(cards)
                res.corroborating_txn_ids = [str(t["txn_id"]) for t in members]
                res.reason = (
                    f"{len(cards)} other cards on this {total}-card device profile transacted within 48h of the "
                    f"flagged ${flagged_amt:.2f} "
                    + ("at near-identical amounts" if basis == "near_identical_to_flagged"
                       else "with risk scores >= 0.70 at matching amounts")
                    + ": " + ", ".join(f"{t['card_id']} ${t['_amt']:.2f} at {t['_ts']:%Y-%m-%d %H:%M}" for t in members)
                )
                return res
    # B2: >= 2 other cards coordinated with each other inside a short window.
    for anchor in txns:
        group = [
            t for t in txns
            if abs(t["_ts"] - anchor["_ts"]) <= timedelta(hours=DEVICE_COORDINATION_WINDOW_HOURS)
            and t["_amt"] is not None and anchor["_amt"] is not None
        ]
        near = [t for t in group if _near_identical(t["_amt"], anchor["_amt"])]
        risky = [
            t for t in group
            if t["_risk"] is not None and t["_risk"] >= DEVICE_HIGH_RISK_SCORE and _similar(t["_amt"], anchor["_amt"])
        ]
        for basis, members in (("near_identical_amounts", near), ("high_risk_matching_timing_amount", risky)):
            cards = {t["card_id"] for t in members}
            if basis == "high_risk_matching_timing_amount" and not (
                anchor["_risk"] is not None and anchor["_risk"] >= DEVICE_HIGH_RISK_SCORE
            ):
                continue
            if len(cards) >= 2:
                res.corroborated = True
                res.corroboration_basis = basis
                res.corroborated_card_ids = sorted(cards)
                res.corroborating_txn_ids = [str(t["txn_id"]) for t in members]
                res.reason = (
                    f"{len(cards)} other cards transacted on this {total}-card device profile within "
                    f"{DEVICE_COORDINATION_WINDOW_HOURS:.0f}h of each other "
                    + ("at near-identical amounts" if basis == "near_identical_amounts"
                       else "with high risk scores and matching timing/amounts")
                )
                return res
    res.reason = (
        f"{len(res.other_cards_48h)} other card(s) on this {total}-card profile within 48h, but no confirmed "
        "fraud and no tightly coordinated activity: network candidate only"
    )
    return res


# --------------------------------------------------------------------------
# Independent evidence families
# --------------------------------------------------------------------------
class EvidenceFamilies(BaseModel):
    suspicious: list[str] = Field(default_factory=list)
    benign: list[str] = Field(default_factory=list)
    strong_suspicious: list[str] = Field(default_factory=list)
    detail: dict[str, str] = Field(default_factory=dict)
    single_signal: bool = True

    @property
    def independent_evidence_count(self) -> int:
        return len(self.suspicious)


def compute_evidence_families(
    profile: BehaviorProfile,
    card_testing: CardTestingResult,
    cnp: CnpBurstResult,
    region: RegionSignal,
    network: DeviceNetworkResult,
    *,
    customer_statement: Literal["denies", "confirms", "disputes_recurring"] | None = None,
    matched_prior_case: dict[str, Any] | None = None,
) -> EvidenceFamilies:
    """Each family is counted once however many detector rows support it.
    The bank's risk score, raw cluster priors, generic closed-case existence
    and LLM prose are never families."""
    fam = EvidenceFamilies()

    def add(kind: str, name: str, why: str) -> None:
        target = fam.suspicious if kind == "suspicious" else fam.benign
        if name not in target:
            target.append(name)
        fam.detail[f"{kind}:{name}"] = why

    behavioral_anomaly = profile.amount_class == "extreme" or profile.product_class == "unseen"
    if behavioral_anomaly:
        bits = []
        if profile.amount_class == "extreme":
            bits.append(f"amount {profile.amount_ratio}x median, percentile {profile.amount_percentile}")
        if profile.product_class == "unseen":
            bits.append(f"ProductCD {profile.flagged_product} never used in {profile.history_count} prior transactions")
        add("suspicious", FAMILY_BEHAVIORAL, "; ".join(bits))
    elif profile.amount_class == "normal" and profile.product_class == "established":
        add("benign", BENIGN_BEHAVIORAL,
            f"normal amount ({profile.amount_ratio}x median) on an established ProductCD {profile.flagged_product}")

    if card_testing.fired:
        add("suspicious", FAMILY_TEMPORAL, card_testing.reason)
    elif cnp.temporal_signal:
        add("suspicious", FAMILY_TEMPORAL, cnp.reason)

    if profile.flagged_channel == "online":
        if profile.is_new_device or profile.is_proxy:
            why = []
            if profile.is_new_device:
                why.append("device marked New for this account (id_15)")
            if profile.is_proxy:
                why.append(f"anonymizing proxy ({profile.proxy_type})")
            add("suspicious", FAMILY_IDENTITY, "; ".join(why))
        elif profile.is_new_device is False and profile.is_proxy is False:
            add("benign", BENIGN_IDENTITY, "device already Found for this account, no anonymizing proxy")

    if region.fired:
        add("suspicious", FAMILY_GEOGRAPHIC, region.reason)
    elif profile.region_class == "established":
        add("benign", BENIGN_GEOGRAPHIC,
            f"region {profile.flagged_region} has {profile.flagged_region_prior_in_person_count} prior in-person uses")

    if network.corroborated:
        add("suspicious", FAMILY_NETWORK, network.reason)

    if customer_statement == "denies":
        add("suspicious", FAMILY_CUSTOMER, "customer states they did not make the transaction")
    elif customer_statement in ("confirms", "disputes_recurring"):
        add("benign", BENIGN_CUSTOMER, "customer confirmed the transaction" if customer_statement == "confirms"
            else "disputed charge matches the card's own strong recurring pattern")

    if matched_prior_case:
        fraud_case = matched_prior_case.get("outcome") == "confirmed_fraud"
        add("suspicious" if fraud_case else "benign", FAMILY_PRIOR_CASE if fraud_case else BENIGN_PRIOR_CASE,
            f"{matched_prior_case.get('id')} ({matched_prior_case.get('outcome')}) at distance "
            f"{matched_prior_case.get('distance')}")

    strong = []
    if card_testing.fired:
        strong.append("exact_card_testing")
    if region.fired:
        strong.append("strict_out_of_region")
    if cnp.temporal_signal and behavioral_anomaly:
        strong.append("behaviorally_anomalous_cnp_burst")
    if network.corroborated:
        strong.append("direct_shared_origin")
    if profile.amount_class == "extreme" and FAMILY_IDENTITY in fam.suspicious:
        strong.append("extreme_amount_with_identity_anomaly")
    fam.strong_suspicious = strong
    fam.single_signal = len(fam.suspicious) <= 1
    return fam


def describe_findings(
    profile: BehaviorProfile, card_testing: CardTestingResult, cnp: CnpBurstResult,
    region: RegionSignal, recurrence: RecurrenceResult, network: DeviceNetworkResult,
    families: EvidenceFamilies,
) -> BehaviorProfile:
    """Fill the profile's human-readable finding lists and family names so
    the trace carries one audit-ready object."""
    sus, ben, na = [], [], []
    if not profile.stable_history:
        na.append(f"only {profile.history_count} prior transactions (< {STABLE_HISTORY_MIN_TXNS}): amount/product baselines unavailable")
    if profile.amount_class == "extreme":
        sus.append(f"extreme amount: {profile.amount_ratio}x median ${profile.amount_median}, percentile {profile.amount_percentile}")
    elif profile.amount_class == "normal":
        ben.append(f"normal amount: {profile.amount_ratio}x median ${profile.amount_median}, percentile {profile.amount_percentile}")
    elif profile.amount_class == "elevated":
        sus.append(f"elevated but not extreme amount: {profile.amount_ratio}x median, percentile {profile.amount_percentile}")
    if profile.product_class == "unseen":
        sus.append(f"ProductCD {profile.flagged_product} unseen in prior history")
    elif profile.product_class == "rare":
        sus.append(f"ProductCD {profile.flagged_product} rare ({profile.product_prior_count} prior, share {profile.product_prior_share})")
    elif profile.product_class == "established":
        ben.append(f"ProductCD {profile.flagged_product} established ({profile.product_prior_count} prior, share {profile.product_prior_share})")
    if profile.region_class == "established":
        ben.append(f"region {profile.flagged_region} established ({profile.flagged_region_prior_in_person_count} prior in-person)")
    elif profile.region_class == "unavailable":
        na.append("flagged in-person transaction has no billing region")
    if profile.flagged_channel == "online":
        if profile.is_new_device:
            sus.append("device marked New for this account (id_15) -- not proof: people buy new phones")
        elif profile.is_new_device is False:
            ben.append("device Found for this account (id_15)")
        else:
            na.append("device newness unavailable")
        if profile.is_proxy:
            sus.append(f"anonymizing proxy ({profile.proxy_type})")
    if card_testing.fired:
        sus.append(card_testing.reason)
    if cnp.flagged_online:
        (sus if cnp.temporal_signal else na).append(f"CNP: {cnp.reason}")
    if region.fired:
        sus.append(f"out-of-region: {region.reason}")
    if recurrence.tier != "none":
        (ben if recurrence.tier == "strong" else na).append(f"recurrence {recurrence.tier}: {recurrence.reason}")
    if network.available:
        (sus if network.corroborated else na).append(f"device network: {network.reason}")
    profile.suspicious_findings = sus
    profile.benign_findings = ben
    profile.unavailable_findings = [*profile.unavailable_findings, *na]
    profile.independent_evidence_families = list(families.suspicious)
    profile.benign_families = list(families.benign)
    return profile
