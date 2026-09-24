"""No-LLM feature audit over the 20 case-pack cases (Task 14).

Runs only the deterministic layer -- behavior profile, card testing, CNP
burst, strict out-of-region, recurrence tier, direct device network,
evidence families and the simulator outcome a verification request would
get -- and writes an auditable report. No LLM call, no graph write.

    python -m scripts.feature_audit                    # graph source (TigerGraph MCP)
    python -m scripts.feature_audit --source csv       # the benchmark CSVs directly
    python -m scripts.feature_audit --out runs/feature-audit

`--source csv` reads the same provided benchmark files the graph was loaded
from (transactions.csv, identity.csv, closed_cases_history.csv,
case_pack.csv) and reproduces the card_window / device_network shapes, so
the audit can run when the graph workspace is unavailable; the two sources
should agree row for row. It does not read any external dataset.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

import pandas as pd  # noqa: E402

from src.agent.episode import build_episode  # noqa: E402
from src.agent.features import (  # noqa: E402
    compute_behavior_profile,
    compute_evidence_families,
    describe_findings,
    detect_card_testing,
    detect_cnp_burst,
    detect_out_of_region,
    detect_recurring_charge,
    evaluate_device_network,
)
from src.agent.simulator import simulate_customer_validation  # noqa: E402
from src.run.dataset_index import _default_data_dir  # noqa: E402
from src.schema.card_ids import build_card_id_map, card_id_for  # noqa: E402
from src.schema.derive_entities import device_id_for  # noqa: E402

# Detector regression facts from the data audit. These are properties of
# the DETECTORS on real data (what must and must not fire), not target
# labels: none of them says whether a case is fraud.
REGRESSION_FACTS: dict[str, list[tuple[str, Any]]] = {
    "HHG-001": [("flagged_channel", "in_person"), ("online_48h", 0), ("amount_ratio_approx", 0.92),
                ("region_prior", ("444.0", 10)), ("cnp_documented", False), ("oor", False)],
    "HHG-003": [("flagged_channel", "in_person"), ("cnp_documented", False), ("recurrence_not_strong", True)],
    "HHG-007": [("flagged_channel", "in_person"), ("region_prior", ("264.0", 2221)), ("cnp_documented", False), ("oor", False)],
    "HHG-009": [("flagged_channel", "online"), ("oor", False)],
    "HHG-012": [("flagged_channel", "in_person"), ("online_48h", 0), ("region_prior", ("494.0", 21)),
                ("cnp_documented", False), ("oor", False)],
    "HHG-013": [("cnp_documented", False), ("cnp_has_no_in_person", True)],
    "HHG-014": [("cnp_documented", False), ("network_corroborated", False)],
    "HHG-015": [("flagged_channel", "online"), ("oor", False), ("cnp_documented", False)],
    "HHG-018": [("flagged_channel", "in_person"), ("region_prior", ("126.0", 565)), ("cnp_documented", False),
                ("oor", False), ("recurrence_not_strong", True)],
    "HHG-019": [("flagged_channel", "online"), ("oor", False), ("network_corroborated", True)],
    "HHG-020": [("flagged_channel", "online"), ("is_new_device", True), ("product_class", "unseen"),
                ("not_decisive_alone", True)],
}


def _check(fact: str, expected: Any, r: dict) -> tuple[bool, Any]:
    p, s = r["behavior_profile"], r["signals"]
    if fact == "flagged_channel":
        actual = p["flagged_channel"]
    elif fact == "online_48h":
        actual = len(p["online_txn_ids_48h"])
    elif fact == "amount_ratio_approx":
        actual = p["amount_ratio"]
        return actual is not None and abs(actual - expected) < 0.02, actual
    elif fact == "region_prior":
        actual = (p["flagged_region"], p["flagged_region_prior_count"])
    elif fact == "cnp_documented":
        actual = s["cnp_burst"]["documented_burst"]
    elif fact == "cnp_has_no_in_person":
        actual = not (set(s["cnp_burst"]["online_txn_ids"]) & set(p["card_present_txn_ids_48h"]))
    elif fact == "oor":
        actual = s["out_of_region"]["fired"]
    elif fact == "recurrence_not_strong":
        actual = s["recurrence"]["tier"] != "strong"
    elif fact == "network_corroborated":
        actual = s["device_network"]["corroborated"]
    elif fact == "is_new_device":
        actual = p["is_new_device"]
    elif fact == "product_class":
        actual = p["product_class"]
    elif fact == "not_decisive_alone":
        # new device + product novelty: at most two families and no strong signal
        actual = not r["families"]["strong_suspicious"]
    else:
        raise ValueError(fact)
    return actual == expected, actual


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------
class CsvSource:
    def __init__(self, data_dir: Path) -> None:
        cols = ["TransactionID", "TransactionAmt", "ProductCD", "addr1", "customer_id", "ts", "channel", "risk_score"]
        tx = pd.read_csv(data_dir / "transactions.csv", usecols=cols, dtype={"TransactionID": str, "addr1": str})
        idn = pd.read_csv(
            data_dir / "identity.csv",
            usecols=["TransactionID", "id_15", "id_23", "id_30", "id_31", "id_33", "DeviceInfo"], dtype=str,
        ).fillna("")
        idn["device_id"] = [
            device_id_for(a, b, c, d) if (a or b or c) else ""
            for a, b, c, d in zip(idn.DeviceInfo, idn.id_30, idn.id_31, idn.id_33)
        ]
        idn["device_label"] = [
            " | ".join(x for x in (a, b, c, d) if x) for a, b, c, d in zip(idn.DeviceInfo, idn.id_30, idn.id_31, idn.id_33)
        ]
        self.tx = tx.merge(idn[["TransactionID", "id_15", "id_23", "device_id", "device_label"]], on="TransactionID", how="left")
        case_pack = pd.read_csv(data_dir / "case_pack.csv")
        closed = pd.read_csv(data_dir / "closed_cases_history.csv", dtype=str)
        self.card_map = build_card_id_map(case_pack, closed)
        links = []
        for r in closed.itertuples():
            for t in str(r.txn_ids).split("|"):
                if t and t != "nan":
                    links.append((t, r.case_id, r.outcome, r.closed_at))
        self.case_links = pd.DataFrame(links, columns=["TransactionID", "case_id", "outcome", "closed_at"])

    def _card(self, customer_id: str) -> str:
        return card_id_for(customer_id, self.card_map)

    async def card_window(self, row: dict) -> list[dict]:
        customer = row["card_id"].split("-K")[0]
        w = self.tx[(self.tx.customer_id == customer) & (self.tx.ts <= str(row["opened_at"]))]
        return [
            {"id": r.TransactionID, "ts": r.ts, "TransactionAmt": r.TransactionAmt, "channel": r.channel,
             "ProductCD": r.ProductCD, "addr1": r.addr1, "id_15": r.id_15, "id_23": r.id_23, "risk_score": r.risk_score}
            for r in w.itertuples()
        ]

    async def device_network(self, row: dict, flagged_ts: str) -> dict:
        f = self.tx[self.tx.TransactionID == str(row["flagged_txn_id"])]
        if f.empty or not isinstance(f.iloc[0].device_id, str) or not f.iloc[0].device_id:
            return {}
        dev = f.iloc[0].device_id
        cutoff = str(row["opened_at"])
        d = self.tx[(self.tx.device_id == dev) & (self.tx.ts <= cutoff)]
        start = (datetime.strptime(flagged_ts, "%Y-%m-%d %H:%M:%S") - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
        win = d[d.ts >= start]
        fraud = d.merge(self.case_links[self.case_links.outcome == "confirmed_fraud"], on="TransactionID")
        return {
            "device_profile_id": dev,
            "device_profile_label": f.iloc[0].device_label,
            "total_distinct_cards": int(d.customer_id.nunique()),
            "txns": [
                {"txn_id": r.TransactionID, "ts": r.ts, "amount": r.TransactionAmt, "risk_score": r.risk_score,
                 "customer_id": r.customer_id, "card_id": self._card(r.customer_id)}
                for r in win.itertuples()
            ],
            "fraud_cases": [
                {"case_id": r.case_id, "outcome": r.outcome, "txn_id": r.TransactionID, "txn_ts": r.ts,
                 "closed_at": r.closed_at, "card_id": self._card(r.customer_id)}
                for r in fraud.itertuples()
            ],
        }


class GraphSource:
    def __init__(self, tg) -> None:
        self.tg = tg

    async def card_window(self, row: dict) -> list[dict]:
        from src.agent.graph_flow import CARD_WINDOW_LOOKBACK_HOURS
        from src.graph.queries import card_window

        return await card_window(
            self.tg, row["card_id"], hours=CARD_WINDOW_LOOKBACK_HOURS,
            reference_txn_id=str(row["flagged_txn_id"]), cutoff_ts=str(row["opened_at"]),
        )

    async def device_network(self, row: dict, flagged_ts: str) -> dict:
        from src.graph.queries import device_network

        return await device_network(self.tg, str(row["flagged_txn_id"]), flagged_ts, str(row["opened_at"]))


# --------------------------------------------------------------------------
async def audit_case(source, row: dict) -> dict:
    flagged = str(row["flagged_txn_id"])
    cutoff = str(row["opened_at"])
    window = await source.card_window(row)
    profile = compute_behavior_profile(window, flagged, cutoff)
    ct = detect_card_testing(window, flagged, cutoff)
    cnp = detect_cnp_burst(window, flagged, cutoff, profile.baseline_online_per_48h)
    region = detect_out_of_region(window, flagged, cutoff, profile)
    recurrence = detect_recurring_charge(window, flagged, cutoff)
    device = await source.device_network(row, profile.flagged_ts) if profile.flagged_channel == "online" else {}
    if device:
        device["flagged_amount"] = profile.flagged_amount
    network = evaluate_device_network(device, row["card_id"], profile.flagged_ts, cutoff)
    statement = None
    if row.get("trigger_type") == "customer_report":
        statement = "disputes_recurring" if recurrence.tier == "strong" else "denies"
    families_pre = compute_evidence_families(profile, ct, cnp, region, network)
    families = compute_evidence_families(profile, ct, cnp, region, network, customer_statement=statement)
    profile = describe_findings(profile, ct, cnp, region, recurrence, network, families)
    episode = build_episode(window, flagged, cutoff, card_testing=ct, cnp=cnp, region=region)
    sim = None
    if row.get("trigger_type") != "customer_report":
        sim = simulate_customer_validation(profile, families_pre, ct, cnp, region, network).model_dump()
    return {
        "case_id": row["case_id"],
        "trigger_type": row.get("trigger_type"),
        "window_rows": len(window),
        "behavior_profile": profile.model_dump(),
        "signals": {
            "card_testing": ct.model_dump(), "cnp_burst": {**cnp.model_dump(), "temporal_signal": cnp.temporal_signal},
            "out_of_region": region.model_dump(), "recurrence": recurrence.model_dump(),
            "device_network": {**network.model_dump(), "matching_txns_48h": network.matching_txns_48h[:20]},
        },
        "customer_statement": statement,
        "families_before_statement": families_pre.model_dump(),
        "families": families.model_dump(),
        "episode": episode.as_dict(),
        "simulated_response_if_asked": sim,
    }


def _fmt(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def render_markdown(results: list[dict], source: str, checks: dict[str, list[dict]]) -> str:
    lines = [
        "# Task 14 feature audit (no LLM)",
        "",
        f"Source: `{source}`. Generated {datetime.utcnow():%Y-%m-%d %H:%M:%S} UTC. Deterministic layer only: "
        "no LLM call, no graph write. Baselines use strictly-prior transactions; the risk score is context only.",
        "",
        "## Summary",
        "",
        "| Case | Trigger | Chan | Prod | Hist | Amt ratio / pct / class | Product | Region (prior, class) | Online 48h | Card test | CNP | OOR | Recurrence | Device | Suspicious families | Benign families | Sim. response |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        p, s, f = r["behavior_profile"], r["signals"], r["families"]
        cnp = s["cnp_burst"]
        cnp_txt = (
            "doc 2-4" if cnp["documented_burst"] else "high-vol" if cnp["high_volume_online"] else "-"
        ) + (f" ({cnp['online_count']}, {'temporal' if cnp['temporal_signal'] else 'routine/none'})" if cnp["flagged_online"] else " (n/a)")
        dev = s["device_network"]
        dev_txt = (
            "corroborated" if dev["corroborated"] else "generic" if dev["generic_profile"]
            else "candidate" if dev["meaningful_match"] else "none" if dev["available"] else "n/a"
        ) + (f" ({dev['total_distinct_cards']} cards)" if dev["available"] else "")
        region = (
            f"{p['flagged_region']} ({p['flagged_region_prior_count']}, {p['region_class']})"
            if p["flagged_region"] else f"n/a ({p['region_class']})"
        )
        sim = (r["simulated_response_if_asked"] or {}).get("response", "on file: " + str(r["customer_statement"]))
        lines.append(
            f"| {r['case_id']} | {r['trigger_type']} | {p['flagged_channel']} | {p['flagged_product']} | {p['history_count']} | "
            f"{_fmt(p['amount_ratio'])} / {_fmt(p['amount_percentile'])} / {p['amount_class']} | "
            f"{p['product_class']} ({p['product_prior_count']}) | {region} | {len(p['online_txn_ids_48h'])} | "
            f"{'FIRED' if s['card_testing']['fired'] else '-'} | {cnp_txt} | {'FIRED' if s['out_of_region']['fired'] else '-'} | "
            f"{s['recurrence']['tier']} | {dev_txt} | {', '.join(f['suspicious']) or '-'} | {', '.join(f['benign']) or '-'} | {sim} |"
        )
    passed = sum(1 for c in checks.values() for x in c if x["ok"])
    total = sum(len(c) for c in checks.values())
    lines += [
        "",
        f"## Detector regression facts: {passed}/{total} hold",
        "",
        "Properties the detectors must satisfy on real data (from the data audit). They are not labels.",
        "",
        "| Case | Fact | Expected | Actual | OK |",
        "|---|---|---|---|---|",
    ]
    for case_id, items in checks.items():
        for x in items:
            lines.append(f"| {case_id} | {x['fact']} | {x['expected']} | {x['actual']} | {'yes' if x['ok'] else '**NO**'} |")
    lines += ["", "## Per-case detail", ""]
    for r in results:
        p, s, f = r["behavior_profile"], r["signals"], r["families"]
        lines += [
            f"### {r['case_id']} ({r['trigger_type']})",
            "",
            f"- Profile: {p['history_count']} prior txns over {_fmt(p['history_span_days'])} days; median ${_fmt(p['amount_median'])}; "
            f"flagged ${_fmt(p['flagged_amount'])} = {_fmt(p['amount_ratio'])}x, percentile {_fmt(p['amount_percentile'])} ({p['amount_class']}); "
            f"ProductCD {p['flagged_product']} {p['product_prior_count']} prior (share {_fmt(p['product_prior_share'])}, {p['product_class']}); "
            f"home region {p['home_region']} ({p['home_region_count']}, share {_fmt(p['home_region_share'])}); "
            f"device {p['device_status'] or 'n/a'}, proxy {p['proxy_type'] or 'none'}; risk score (context only) {_fmt(p['risk_score_context_only'])}.",
            f"- Card testing: {s['card_testing']['reason']}",
            f"- CNP: {s['cnp_burst']['reason']} (excluded in-person rows: {len(s['cnp_burst']['excluded_in_person_ids'])})",
            f"- Out-of-region: {s['out_of_region']['reason']}",
            f"- Recurrence: {s['recurrence']['tier']} -- {s['recurrence']['reason']}",
            f"- Device network: {s['device_network']['reason']}",
            f"- Families: suspicious {f['suspicious'] or '[]'}; benign {f['benign'] or '[]'}; strong {f['strong_suspicious'] or '[]'}; single_signal {f['single_signal']}",
            f"- Episode: {r['episode']['basis']} -> {r['episode']['txn_ids']} (${r['episode']['exposure_usd']})",
        ]
        if r["simulated_response_if_asked"]:
            lines.append(f"- Simulated response if verification is requested: {r['simulated_response_if_asked']['text']}")
        lines.append("")
    return "\n".join(lines)


async def run_audit(source_name: str, out_dir: Path, data_dir: Path | None) -> int:
    base = data_dir or _default_data_dir()
    cases = pd.read_csv(base / "case_pack.csv").to_dict(orient="records")
    results: list[dict] = []
    if source_name == "csv":
        source = CsvSource(base)
        for row in cases:
            results.append(await audit_case(source, row))
    else:
        from src.tg_client import INVESTIGATION_ALLOWED_TOOLS, TigerGraphMCP

        async with TigerGraphMCP(allowed_tools=INVESTIGATION_ALLOWED_TOOLS) as tg:
            source = GraphSource(tg)
            for row in cases:
                results.append(await audit_case(source, row))

    checks: dict[str, list[dict]] = {}
    by_id = {r["case_id"]: r for r in results}
    for case_id, facts in REGRESSION_FACTS.items():
        checks[case_id] = []
        for fact, expected in facts:
            ok, actual = _check(fact, expected, by_id[case_id])
            checks[case_id].append({"fact": fact, "expected": expected, "actual": actual, "ok": ok})

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "feature_audit.json").write_text(
        json.dumps({"source": source_name, "cases": results, "regression_checks": checks}, indent=2, default=str),
        encoding="utf-8",
    )
    (out_dir / "FEATURE_AUDIT.md").write_text(render_markdown(results, source_name, checks), encoding="utf-8")
    failed = [(c, x) for c, items in checks.items() for x in items if not x["ok"]]
    print(f"Audited {len(results)} cases from {source_name}; regression facts: "
          f"{sum(len(v) for v in checks.values()) - len(failed)}/{sum(len(v) for v in checks.values())} hold.")
    for c, x in failed:
        print(f"  FAILED {c}: {x['fact']} expected {x['expected']} actual {x['actual']}")
    return 0 if not failed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=["graph", "csv"], default="graph")
    parser.add_argument("--out", default="runs/feature-audit")
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args()
    return asyncio.run(run_audit(args.source, Path(args.out), Path(args.data_dir) if args.data_dir else None))


if __name__ == "__main__":
    raise SystemExit(main())
