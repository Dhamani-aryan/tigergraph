from __future__ import annotations

from collections import defaultdict
from typing import Any

from src.tg_client import TigerGraphMCP

GRAPH_NAME = "FraudInvestigation"

# Degree cap applied to every DeviceProfile/BillingRegion/EmailDomain "origin
# group" (the set of distinct Cards that share that device fingerprint /
# billing region / purchaser email domain) before it is allowed to contribute
# SHARES_ORIGIN edges. Confirmed live (see task-8.5-report.md) that without
# this cap, ALL THREE dimensions are dominated by coarse-categorical
# collisions, not genuine shared origins -- e.g. one BillingRegion had 2,006
# distinct cards, one EmailDomain ("gmail.com") had 8,933, and 523/9,705
# DeviceProfiles had more than 20 distinct cards (max 842). 20 matches the
# brief's suggested 15-20 range and is applied uniformly across all three
# dimensions since the collision problem was confirmed live to be worse for
# region/email than for device, not better.
DEFAULT_CAP_DEGREE = 20

# --------------------------------------------------------------------------
# build_shares_origin
# --------------------------------------------------------------------------
#
# Live-confirmed deviations from the brief's literal GSQL (see
# task-8.5-report.md "GSQL syntax findings" for the full trail):
#
# 1. This server's GSQL dialect does NOT support chaining more than one hop
#    in a single FROM-clause path expression (`A -(E1)-> B -(E2)-> C` fails
#    to parse -- "mismatched input '->'" -- even with `SYNTAX v2` on the
#    query). Confirmed by isolated probes: a single hop (`Cards:c -(MADE)->
#    Transaction:t`) parses and returns the exact expected count (590,742);
#    the same query with a second chained hop fails to parse. Every
#    multi-hop traversal below is therefore written as a sequence of
#    separate SELECT statements (one hop each), matching classic GSQL v1
#    idiom, not the brief's single chained-arrow FROM clause.
#
# 2. The brief's `reverse_FROM_DEVICE`/`reverse_MADE`/etc. backward-traversal
#    is unnecessary here (Task 8's finding was about reading a *specific*
#    vertex's neighbors from the "one" side; this query needs the opposite
#    direction anyway): traversing FORWARD from Card (`Card -(MADE)->
#    Transaction -(FROM_DEVICE)-> DeviceProfile`, all declared FROM/TO
#    directions) reaches every DeviceProfile/BillingRegion/EmailDomain
#    without any reverse edge at all.
#
# 3. `WHERE c1.card_id != c2.card_id` (brief's literal draft) would hit
#    Task 8's confirmed `primary_id_as_attribute` gap (Card was not declared
#    with that flag, so `c.card_id` is not a queryable attribute). Avoided
#    entirely here: cards are paired via vertex-identity comparison
#    (`c1 != c2`, comparing the VERTEX values themselves, not a string
#    attribute) and inserted into SHARES_ORIGIN using the vertex aliases
#    directly (`INSERT INTO EDGE SHARES_ORIGIN VALUES (c1, c2, "device")`),
#    never a string card_id.
#
# 4. `ACCUM INSERT INTO EDGE SHARES_ORIGIN VALUES(...)` (an insert *inside* an
#    installed query body) is confirmed live to actually persist edges on
#    this server -- unlike the bare top-level `tg.gsql("INSERT INTO ...")`
#    Task 7 found rejected. One gotcha confirmed live: immediately after a
#    RUN, `tigergraph__get_edge_count` can report 0 (stale cached stats);
#    `SELECT count(*) FROM Card-(SHARES_ORIGIN)-Card` and
#    `tigergraph__get_edges` both showed the real, correct count right away.
#    Don't trust `get_edge_count` as a freshness check after this query.
#
# Grouping strategy: rather than a per-pair self-join (which this server's
# GSQL can't express in one FROM clause anyway -- see point 1), each
# DeviceProfile/BillingRegion/EmailDomain accumulates the SetAccum<VERTEX
# <Card>> of every Card that reaches it, then a POST-ACCUM step generates
# all pairs within that set (skipped entirely if the set exceeds
# capDegree) via a nested FOREACH and inserts SHARES_ORIGIN for each pair.
BUILD_SHARES_ORIGIN_GSQL = f"""
USE GRAPH {GRAPH_NAME}
CREATE OR REPLACE QUERY build_shares_origin(INT capDegree={DEFAULT_CAP_DEGREE}) FOR GRAPH {GRAPH_NAME} {{
    SetAccum<VERTEX<Card>> @card;
    SetAccum<VERTEX<Card>> @cardSet;
    SumAccum<INT> @@devicePairs;
    SumAccum<INT> @@regionPairs;
    SumAccum<INT> @@emailPairs;
    SumAccum<INT> @@deviceGroupsUsed;
    SumAccum<INT> @@regionGroupsUsed;
    SumAccum<INT> @@emailGroupsUsed;
    SumAccum<INT> @@deviceGroupsSkippedOverCap;
    SumAccum<INT> @@regionGroupsSkippedOverCap;
    SumAccum<INT> @@emailGroupsSkippedOverCap;

    Cards = {{Card.*}};
    Txns = SELECT t FROM Cards:c -(MADE)-> Transaction:t
           ACCUM t.@card += c;

    // --- device leg: Card -(MADE)-> Transaction -(FROM_DEVICE)-> DeviceProfile ---
    Devs = SELECT d FROM Txns:t -(FROM_DEVICE)-> DeviceProfile:d
           ACCUM d.@cardSet += t.@card;
    Devs = SELECT d FROM Devs:d
           POST-ACCUM
               IF d.@cardSet.size() > capDegree THEN
                   @@deviceGroupsSkippedOverCap += 1
               ELSE IF d.@cardSet.size() >= 2 THEN
                   @@deviceGroupsUsed += 1,
                   FOREACH c1 IN d.@cardSet DO
                       FOREACH c2 IN d.@cardSet DO
                           IF c1 != c2 THEN
                               INSERT INTO EDGE SHARES_ORIGIN VALUES (c1, c2, "device"),
                               @@devicePairs += 1
                           END
                       END
                   END
               END;

    // --- region leg: Card -(MADE)-> Transaction -(BILLED_IN)-> BillingRegion ---
    Regions = SELECT r FROM Txns:t -(BILLED_IN)-> BillingRegion:r
           ACCUM r.@cardSet += t.@card;
    Regions = SELECT r FROM Regions:r
           POST-ACCUM
               IF r.@cardSet.size() > capDegree THEN
                   @@regionGroupsSkippedOverCap += 1
               ELSE IF r.@cardSet.size() >= 2 THEN
                   @@regionGroupsUsed += 1,
                   FOREACH c1 IN r.@cardSet DO
                       FOREACH c2 IN r.@cardSet DO
                           IF c1 != c2 THEN
                               INSERT INTO EDGE SHARES_ORIGIN VALUES (c1, c2, "region"),
                               @@regionPairs += 1
                           END
                       END
                   END
               END;

    // --- email leg: Card -(MADE)-> Transaction -(PURCHASER_EMAIL)-> EmailDomain ---
    Emails = SELECT e FROM Txns:t -(PURCHASER_EMAIL)-> EmailDomain:e
           ACCUM e.@cardSet += t.@card;
    Emails = SELECT e FROM Emails:e
           POST-ACCUM
               IF e.@cardSet.size() > capDegree THEN
                   @@emailGroupsSkippedOverCap += 1
               ELSE IF e.@cardSet.size() >= 2 THEN
                   @@emailGroupsUsed += 1,
                   FOREACH c1 IN e.@cardSet DO
                       FOREACH c2 IN e.@cardSet DO
                           IF c1 != c2 THEN
                               INSERT INTO EDGE SHARES_ORIGIN VALUES (c1, c2, "email"),
                               @@emailPairs += 1
                           END
                       END
                   END
               END;

    PRINT @@devicePairs, @@regionPairs, @@emailPairs;
    PRINT @@deviceGroupsUsed, @@regionGroupsUsed, @@emailGroupsUsed;
    PRINT @@deviceGroupsSkippedOverCap, @@regionGroupsSkippedOverCap, @@emailGroupsSkippedOverCap;
}}
""".strip()

# --------------------------------------------------------------------------
# label_propagation_cc
# --------------------------------------------------------------------------
#
# Live-confirmed deviation from the brief's literal GSQL: Card lacks
# `primary_id_as_attribute` (same Task 8 finding as above), and unlike
# build_shares_origin's WHERE clause (fixable via vertex-identity comparison),
# label propagation genuinely needs the primary id as a STRING VALUE to seed
# and compare labels -- `c.ring_cluster_id = c.card_id` in the brief's draft
# has no vertex-identity workaround. Live investigation (WebSearch against
# current GSQL docs, a TigerGraph forum thread, and direct probes against
# this server) confirmed there is NO GSQL expression that converts a VERTEX
# to its primary-id STRING without primary_id_as_attribute: `c.ring_cluster_id
# = c` and `c.ring_cluster_id = "" + c` both fail with "no type can be
# inferred"; the documented vertex functions (getvid, to_vertex, elementId,
# getAttr) either go the wrong direction or return an internal id, never the
# primary key string. The TigerGraph forum's own answer to this exact
# question: either add primary_id_as_attribute at CREATE time (not possible
# here without dropping/recreating Card, which would cascade-delete every
# edge touching it -- far too destructive for this task) or fall back to
# "external tools like pyTigerGraph... it will include the vertex ids."
#
# The fix used here keeps the actual label-propagation ALGORITHM (the
# min-label WHILE loop) running entirely inside GSQL as a real installed
# query, using `getvid(c)` (documented, works on any vertex regardless of
# primary_id_as_attribute) as the per-vertex label instead of a card_id
# string -- getvid returns TigerGraph's internal numeric vertex id, which is
# just as valid a "propagate the minimum label" seed as card_id would have
# been, since only relative ordering/equality matters for connected
# components, not the label's specific value. Only the unavoidable last step
# -- mapping each card's final internal-id label back to an actual
# ring_cluster_id STRING -- happens in Python, using the same
# tigergraph__add_nodes REST++ upsert pattern already established in
# src/schema/loading_jobs.py and src/schema/derive_entities.py, because
# GSQL's PRINT/JSON serialization (unlike its expression language) DOES
# include each vertex's real primary id (`v_id`) automatically -- confirmed
# live: `PRINT Cards[Cards.@minLabel]` returns e.g.
# `{"v_id": "C07660-K1", ..., "attributes": {"Cards.@minLabel": 220201130}}`.
#
# Change-detection: MinAccum only ever decreases, so round-over-round change
# is detected by snapshotting each vertex's label into a second accumulator
# (@prevLabel) before propagating, then comparing after. Converged in 7
# rounds against the live graph (13,574 Cards, tens of thousands of
# SHARES_ORIGIN edges) in well under a second -- LIMIT 25 is a safety cap,
# never actually hit.
#
# IMPORTANT caveat carried into the Python post-processing (documented in
# full in task-8.5-report.md): after a SELECT step traverses
# `Cards:c -(SHARES_ORIGIN)- Card:nbr`, GSQL's `Cards` variable is
# reassigned to only the vertices that matched that traversal -- i.e. only
# Cards with at least one SHARES_ORIGIN edge survive into the final PRINT.
# Cards with zero SHARES_ORIGIN edges (the large majority -- 9,940 of
# 13,574) never appear in this query's output at all and must be assigned
# their own singleton ring_cluster_id separately in Python.
LABEL_PROPAGATION_CC_GSQL = f"""
USE GRAPH {GRAPH_NAME}
CREATE OR REPLACE QUERY label_propagation_cc() FOR GRAPH {GRAPH_NAME} {{
    MinAccum<INT> @minLabel;
    MaxAccum<INT> @prevLabel;
    SumAccum<INT> @@numChanged;
    SumAccum<INT> @@rounds;

    Cards = {{Card.*}};
    Cards = SELECT c FROM Cards:c
            POST-ACCUM c.@minLabel = getvid(c);

    WHILE TRUE LIMIT 25 DO
        Cards = SELECT c FROM Cards:c
                POST-ACCUM c.@prevLabel = c.@minLabel;
        Cards = SELECT c FROM Cards:c -(SHARES_ORIGIN)- Card:nbr
                ACCUM c.@minLabel += nbr.@minLabel;
        @@numChanged = 0;
        Cards = SELECT c FROM Cards:c
                POST-ACCUM
                    IF c.@minLabel < c.@prevLabel THEN
                        @@numChanged += 1
                    END;
        @@rounds += 1;
        IF @@numChanged == 0 THEN
            BREAK;
        END;
    END;

    PRINT @@rounds;
    PRINT Cards[Cards.@minLabel];
}}
""".strip()

# --------------------------------------------------------------------------
# cluster_fraud_rate
# --------------------------------------------------------------------------
#
# Runs AFTER ring_cluster_id has been written onto every Card (by Python,
# via REST++ -- see write_ring_cluster_ids below). Once populated,
# ring_cluster_id is an ordinary STRING attribute (not a primary id), so it
# is freely dot-accessible/writable in GSQL with no primary_id_as_attribute
# concern. Live-confirmed deviation from the brief: GSQL on this server does
# not support the ternary `cond ? a : b` operator ("mismatched input '?'");
# rewritten as IF/ELSE. `MapAccum<STRING, SumAccum<INT>>.get(key)` (nested
# map read) works as drafted -- confirmed by a live install of this exact
# pattern.
CLUSTER_FRAUD_RATE_GSQL = f"""
USE GRAPH {GRAPH_NAME}
CREATE OR REPLACE QUERY cluster_fraud_rate() FOR GRAPH {GRAPH_NAME} {{
    MapAccum<STRING, SumAccum<INT>> @@clusterFraud;
    MapAccum<STRING, SumAccum<INT>> @@clusterTotal;

    ClosedCards = SELECT c FROM ClosedCase:cc -(ON_CARD)-> Card:c
                  ACCUM
                      @@clusterTotal += (c.ring_cluster_id -> 1),
                      IF cc.outcome == "confirmed_fraud" THEN
                          @@clusterFraud += (c.ring_cluster_id -> 1)
                      END;

    AllCards = {{Card.*}};
    AllCards = SELECT c FROM AllCards:c
               POST-ACCUM
                   FLOAT total = @@clusterTotal.get(c.ring_cluster_id),
                   FLOAT fraud = @@clusterFraud.get(c.ring_cluster_id),
                   IF total > 0 THEN
                       c.cluster_prior_fraud_rate = fraud / total
                   ELSE
                       c.cluster_prior_fraud_rate = 0.0
                   END;
}}
""".strip()


async def _install(tg: TigerGraphMCP, gsql: str, query_name: str) -> None:
    result = await tg.gsql(f"{gsql}\nINSTALL QUERY {query_name}")
    print(result)


def _extract_print_results(run_result: dict[str, Any]) -> list[Any]:
    return run_result["data"]["result"]


async def build_shares_origin(tg: TigerGraphMCP, cap_degree: int = DEFAULT_CAP_DEGREE) -> dict[str, Any]:
    await _install(tg, BUILD_SHARES_ORIGIN_GSQL, "build_shares_origin")
    result = await tg.run_installed_query("build_shares_origin", {"capDegree": cap_degree})
    print(result)
    return result


def _group_touched_cards(touched_cards: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Pure grouping logic factored out of run_label_propagation for unit
    testing: takes the raw `Cards` list from label_propagation_cc's PRINT
    output (each entry {"v_id": ..., "attributes": {"Cards.@minLabel": N}})
    and returns {ring_cluster_id: [card_id, ...]}, one entry per connected
    component, canonically named "RING-" + the lexicographically smallest
    card_id in the component (deterministic/reproducible across re-runs)."""
    label_groups: dict[int, list[str]] = defaultdict(list)
    for entry in touched_cards:
        card_id = entry["v_id"]
        label = entry["attributes"]["Cards.@minLabel"]
        label_groups[label].append(card_id)

    ring_groups: dict[str, list[str]] = {}
    for members in label_groups.values():
        members_sorted = sorted(members)
        ring_id = f"RING-{members_sorted[0]}"
        ring_groups[ring_id] = members_sorted
    return ring_groups


async def run_label_propagation(tg: TigerGraphMCP) -> dict[str, list[str]]:
    """Installs and runs label_propagation_cc, then computes the final
    ring_cluster_id grouping in Python (canonical id = "RING-" + the
    lexicographically smallest card_id in the group, for determinism/
    reproducibility across re-runs). Cards untouched by any SHARES_ORIGIN
    edge (absent from the query's output -- see the module docstring above)
    are fetched separately and each assigned a singleton "RING-<own card_id>"
    cluster, so every Card in the graph ends up with a ring_cluster_id.

    Returns {ring_cluster_id: [card_id, ...]} for every card in the graph.
    """
    await _install(tg, LABEL_PROPAGATION_CC_GSQL, "label_propagation_cc")
    run_result = await tg.run_installed_query("label_propagation_cc", {})
    print_results = _extract_print_results(run_result)
    # print_results[0] == {"@@rounds": N}, print_results[1] == {"Cards": [...]}
    rounds = print_results[0].get("@@rounds")
    print(f"label_propagation_cc converged after {rounds} round(s)")
    touched_cards = print_results[1]["Cards"]

    ring_groups = _group_touched_cards(touched_cards)
    touched_card_ids: set[str] = {cid for members in ring_groups.values() for cid in members}

    # Cards with zero SHARES_ORIGIN edges never appear in touched_cards --
    # fetch the full Card population and give each untouched card its own
    # singleton ring.
    all_cards_result = await tg.call("tigergraph__get_nodes", {"vertex_type": "Card", "limit": 50000})
    all_card_ids = {node["v_id"] for node in all_cards_result["data"]["vertices"]}
    untouched = all_card_ids - touched_card_ids
    for card_id in untouched:
        ring_groups[f"RING-{card_id}"] = [card_id]

    total_cards = sum(len(v) for v in ring_groups.values())
    print(
        f"ring grouping: {len(ring_groups)} rings covering {total_cards} cards "
        f"({len(touched_card_ids)} touched by SHARES_ORIGIN, {len(untouched)} singleton)"
    )
    return ring_groups


async def write_ring_cluster_ids(tg: TigerGraphMCP, ring_groups: dict[str, list[str]]) -> None:
    """Batch-writes ring_cluster_id onto every Card via the REST++
    add_nodes upsert (same mechanism as src/schema/loading_jobs.py's
    backfill_stub_card_customer_ids), since GSQL itself cannot compute
    these STRING values (see run_label_propagation's docstring)."""
    batch: list[dict[str, Any]] = []
    for ring_id, card_ids in ring_groups.items():
        for card_id in card_ids:
            batch.append({"card_id": card_id, "ring_cluster_id": ring_id})
            if len(batch) >= 500:
                await tg.call(
                    "tigergraph__add_nodes",
                    {"vertex_type": "Card", "vertex_id": "card_id", "vertices": batch},
                )
                batch = []
    if batch:
        await tg.call(
            "tigergraph__add_nodes",
            {"vertex_type": "Card", "vertex_id": "card_id", "vertices": batch},
        )


async def run_connected_components(tg: TigerGraphMCP, cap_degree: int = DEFAULT_CAP_DEGREE) -> None:
    await build_shares_origin(tg, cap_degree)
    ring_groups = await run_label_propagation(tg)
    await write_ring_cluster_ids(tg, ring_groups)


async def compute_cluster_fraud_rates(tg: TigerGraphMCP) -> None:
    await _install(tg, CLUSTER_FRAUD_RATE_GSQL, "cluster_fraud_rate")
    result = await tg.run_installed_query("cluster_fraud_rate", {})
    print(result)
