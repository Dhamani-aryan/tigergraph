# Manual case checkpoint: HHG-017

Per the README's own advice ("Investigate one case by hand, before writing any agent
code") and Task 8 Step 4. This is a by-hand investigation run against the live graph
after Task 8's `DeviceProfile`/`FROM_DEVICE`/`INVOLVES`/`CONNECTED_TO` load, using the
`tigergraph-mcp` tools directly — no agent code involved.

**Case:** `HHG-017` — `risk_score` trigger, opened 2016-11-12 00:46:24. Trigger text:
"Real-time model scored transaction 3450629 ($100.09, online) at 0.57. Review and
decide." Flagged transaction `3450629`, card `C04570-K1`, customer `C04570`.

## Load validation (go/no-go, part 1)

Before investigating, confirmed the Task 8 load actually landed correctly:

| Check | Expected (from CSV) | Actual (graph) |
|---|---|---|
| `DeviceProfile` vertices | — (deduped) | 9,705 |
| `FROM_DEVICE` edges | 144,432 identity.csv rows − 3,648 with no device signal at all = 140,784 | 140,784 |
| `INVOLVES` edges | sum of `txn_ids` pipe-fields across closed_cases_history.csv = 14,955 | 14,955 |
| `CONNECTED_TO` edges | sum of `connected_card_ids` pipe-fields = 92 | 92 |
| `ON_CARD` edges (Task 7, unaffected) | 5,565 | 5,565 |

All four counts match the source CSVs exactly (computed independently in Python
against the raw files, not derived from the loader's own log). The load is complete
and correct.

## Raw transaction

`SELECT * FROM Transaction WHERE transaction_id == "3450629"` returns exactly one
vertex. Key attributes: `TransactionAmt` 100.09, `ts` 2016-11-11 23:46:24,
`channel` online, `risk_score` 0.57, `customer_id` C04570, `card1` 20749/`card4`
mastercard/`card6` credit, `addr1` 204.0/`addr2` 87.0 (home country),
`P_emaildomain`/`R_emaildomain` both `anonymous.com`, `ProductCD` R. This matches
`case_pack.csv`'s trigger text exactly ($100.09, online, 0.57) — the schema and
loader reproduce the case pack faithfully.

The corresponding `identity.csv` row (joined on `TransactionID`) has `id_15`
("Found", i.e. **not** a New device for this account), `id_23`
`IP_PROXY:HIDDEN`, and device `Windows / Windows 10 / chrome 65.0 / 1920x1080`.

## Card history: `Card`–`(MADE)`→`Transaction` for `C04570-K1`

**Note on query mechanics:** the brief's suggested ad-hoc query
(`SELECT * FROM Card-(MADE)->Transaction WHERE Card.card_id == "C04570-K1"`) does
**not** run as written — `Card` (unlike `Transaction`) was not declared with
`WITH primary_id_as_attribute="true"` in Task 4's schema, so `Card.card_id` is not a
queryable attribute and the ad-hoc GSQL parser rejects it ("Vertex 'Card' does not
contain the attribute 'card_id'"). Worked around by using the
`tigergraph__get_neighbors` MCP tool (REST++, addresses vertices by their real
primary ID directly) instead. This is a real, load-bearing finding — see
"Schema/query gaps" below.

Card `C04570-K1` has **59 transactions** from 2016-07-17 to 2016-12-25, `TransactionAmt`
ranging $34.01–$2,892.12, across product codes W/R/H/S, and across at least a dozen
different `addr1` billing regions (204, 225, 264, 299, 310, 315, 325, 327, 330, 387,
420, 441, 469, 126, 123). This is an active, long-lived card with a wide legitimate
purchase history, not a newly-opened or dormant one.

The three transactions immediately around the flagged one:

| txn | ts | amount | risk_score | addr1 |
|---|---|---|---|---|
| 3450436 | 2016-11-11 22:36:50 | $100.09 | 0.05 | 204.0 |
| 3450503 | 2016-11-11 22:58:57 | $99.96 | 0.10 | 204.0 |
| **3450629** | **2016-11-11 23:46:24** | **$100.09** | **0.57** | **204.0** |

Three ~$100 online purchases in the same billing region within about 70 minutes,
the first and third for the *identical* amount ($100.09). **This is not the
`card_testing` pattern** (README/Policy R5: three or more small, typically sub-$5,
authorizations followed by one larger purchase that clears) — there is no tiny
"testing" charge here at all, just three purchases of comparable, non-trivial size.
`addr1` 204.0 is also not a new region for this card: it recurs at least 4 other
times in the card's history (7/28, 7/29, 10/27). This looks more like repeated
legitimate purchase attempts (e.g. retried checkouts) than a fraud signature, but
three same-size charges in one evening on a card whose normal cadence is roughly
one purchase every few days is still worth asking the customer about.

## Prior closed case on this exact card

`ClosedCase` `CC-1383` → `ON_CARD` → `Card` `C04570-K1` (confirmed via
`get_node_edges` from the `ClosedCase` side — see note below) and → `INVOLVES` →
`Transaction` `3118964`. From `closed_cases_history.csv`: **`outcome: cleared`**,
`pattern: none`, analyst note: *"model scored a $99.98 transaction at 0.91.
Cardholder confirmed travel to the billing region in question. Alert cleared."*
(2016-07-29). This is the *only* closed case touching this customer or card, and it
was a false alarm. That is a meaningful prior: this customer's alert history is
0-for-1 on confirmed fraud, and the one prior alert cleared on simple customer
verification — evidence leaning toward legitimacy, not against it.

## Device profile

The flagged transaction's device fingerprint (`Windows` / `Windows 10` /
`chrome 65.0` / `1920x1080`, hashed to `device_id` `Dec9ef04aa023`) is **not** a
rare or distinguishing fingerprint: an installed GSQL query (see below) found
**621 other transactions from 299 distinct customers** sharing this exact
device-profile hash. A common OS+browser+screen-resolution combination collides
across hundreds of unrelated customers, because `DeviceProfile`'s dedup key
(`DeviceInfo`/`id_30`/`id_31`/`id_33`) captures a device *category*, not a unique
physical device. Sharing this device profile with 299 other customers is not
meaningful "shared origin" evidence — it is noise. (Contrast: a rare fingerprint
like a specific phone model shared by only two cards, as in the README's own
illustrative example, would be real signal. This case's device isn't that.)

No `CONNECTED_TO` edge exists for `C04570-K1` in either direction — it has never
appeared as a connected card on any closed case.

## Schema/query gaps found during this checkpoint (important for Tasks 8.5–10)

Two real, load-bearing findings surfaced while running these by-hand queries,
neither of which is a data problem — both are about how to *query* this schema
correctly on this server:

1. **`Card` (and `DeviceProfile`) don't expose their primary ID as a filterable
   attribute.** Only `Transaction` was created `WITH primary_id_as_attribute="true"`
   (Task 4). Ad-hoc `tg.gsql("SELECT ... WHERE Card.card_id == ...")` fails for any
   vertex type without that flag. Workaround used throughout this checkpoint: the
   `tigergraph__get_neighbors`/`get_node`/`get_node_edges` MCP tools, which address
   vertices by real primary ID via REST++ directly, bypassing GSQL's attribute
   requirement entirely.

2. **No `REVERSE_EDGE` was declared on any directed edge in Task 4's schema**
   (`FROM_DEVICE`, `MADE`, `BILLED_IN`, `PURCHASER_EMAIL`, etc. — confirmed by
   grepping `src/schema/build_schema.py`). Two consequences, both confirmed live:
   - The generic `tigergraph__get_node_edges`/`get_node_degree` MCP tools only see a
     vertex's **outgoing** edges. Calling them on `DeviceProfile`/`Card` (which are
     only ever the *target* of `FROM_DEVICE`/`ON_CARD`/`CONNECTED_TO`) always
     reports 0 edges/degree — not because the edges don't exist, but because
     those tools never look at incoming edges. (Verified: `get_node_edges` from the
     `Transaction` side, and from the `ClosedCase` side, correctly finds the same
     edges the `DeviceProfile`/`Card`-side query reported as missing.)
   - **Task 8.5's planned `build_shares_origin()` query, as written in the brief,
     will not install** — it references `reverse_FROM_DEVICE`, `reverse_MADE`,
     `reverse_BILLED_IN`, and `reverse_PURCHASER_EMAIL`, none of which exist
     (confirmed live: `CREATE QUERY` referencing `reverse_FROM_DEVICE` fails with
     `SEM-40: reverse_FROM_DEVICE is not a valid edge type`). GSQL's undirected
     pattern arrow (`-(FROM_DEVICE)-`, no `->`) also does **not** implicitly reverse
     a directed edge on this server — it still requires the source-vertex type
     compatible with the edge's declared `FROM` side.
   - **The correct workaround**, confirmed live end-to-end (installed a probe query,
     got back an exact count matching an independent Python count of the CSV: 1,424
     transactions for a test device, and 621/299 for this case's device): seed the
     query from the "many" side's full vertex set (e.g. `{Transaction.*}`), traverse
     the edge in its declared forward direction, and filter the target endpoint with
     `WHERE targetAlias == paramVertex` (a `VERTEX<T>` query parameter compared
     directly — not `to_vertex()`, which is rejected in ad-hoc top-level `WHERE`
     clauses on this server, and not an attribute comparison, since these vertex
     types have no `primary_id_as_attribute`). Whoever picks up Task 8.5 needs to
     rewrite `BUILD_SHARES_ORIGIN_GSQL` using this pattern instead of
     `reverse_FROM_DEVICE`/`reverse_MADE`/`reverse_BILLED_IN`/`reverse_PURCHASER_EMAIL`.

Neither finding blocks Task 8 itself (the vertices/edges are loaded correctly, as the
count validation above shows) — they are query-authoring gotchas for the tasks that
come next, exactly the kind of thing this checkpoint step is meant to catch before
more code gets built on a wrong assumption.

## A note on the README's illustrative JSON example

The Answer Format section's worked JSON example happens to use `"case_id": "HHG-017"`
as its label, but its actual content (transactions `T0412877`/`T0412878`/etc., cards
`C00377-K1`/`C00877-K1`) does not correspond to this dataset's real `HHG-017` row in
`case_pack.csv` at all — this dataset's transaction IDs are purely numeric (e.g.
`3450629`), never `T`-prefixed, and no such cards exist on this case. That JSON block
is a format illustration only, not ground truth for the real case. The verdict below
is derived independently from the live graph, not reverse-engineered from that example
(per this task's own instruction not to fabricate findings to match an expected
answer).

## By-hand verdict

- **Pattern:** does not match any of the five known patterns. Specifically not
  `card_testing` (no sub-$5 probing charges before a larger one — see the table
  above), not `card_not_present_new_device` (`id_15` says "Found", not "New"), and
  the shared device profile is a false positive from fingerprint collision, not a
  real ring signal.
- **Fraud probability (by hand):** ~0.25–0.35. The only signal is the risk score
  (0.57, and the README explicitly warns "above 0.7, most flagged transactions turn
  out to be legitimate" and scores are "often wrong in both directions") plus three
  same-region, same-order-of-magnitude purchases in one evening. Weighing against
  fraud: no tiny-then-large burst, no new/rare device, no historical fraud on this
  card or customer (the one prior alert on this exact card was cleared, not
  confirmed), and 59 transactions of broadly consistent behavior.
- **Policy application:** this rests on a single signal (risk score, still below
  0.70) — **R1** applies: verify before blocking. Recommended action:
  `VERIFY_WITH_CUSTOMER` (route `auto`), plus `CREATE_CASE` (Sec 3a: open a case once
  the investigation starts / evidence is requested, even before a verdict). No
  `FILE_REPORT` at this stage — exposure ($100.09, or up to ~$300 if all three
  November 11 transactions are treated as one episode) is under $1,000 and there is
  no genuine shared-device/region/ring signal to invoke R6.
- **If the customer denies the transaction:** R2 — `BLOCK_CARD` (`L1`, exposure
  ≤ $2,500) and `CREATE_CASE`; no `FILE_REPORT` unless exposure is later found to
  exceed $1,000 or a real (not fingerprint-collision) connected-fraud link turns up.
- **If the customer confirms:** R3 — `CLOSE_NO_FRAUD`, consistent with this
  customer's one prior alert also clearing on confirmation.

## Go/no-go conclusion

**Go.** The schema loads correctly (all counts validated against source CSVs), the
graph is traversable end-to-end for a real investigation (transaction → card history,
card → prior closed cases, transaction → device → other transactions/customers), and
the query results are sensible and support a defensible by-hand verdict. The two
query-mechanics gaps above (no `primary_id_as_attribute` on `Card`/`DeviceProfile`,
no `REVERSE_EDGE` declarations) are real but have confirmed workarounds — they need
to be carried forward into Task 8.5's and Task 9/10's query design, not fixed by
re-loading data.
