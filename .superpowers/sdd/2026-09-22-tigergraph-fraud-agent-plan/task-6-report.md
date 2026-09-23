# Task 6 Report: Answer-JSON Pydantic schemas

## Status
DONE

## Commit
`1e9f6fe` — "feat: Pydantic schemas for the answer JSON format" (branch `tigergraph-fraud-agent`, local only, not pushed)

Files added:
- `src/agent/__init__.py` (empty)
- `src/agent/schemas.py`
- `tests/test_schemas.py`

## Test summary
`pytest tests/test_schemas.py -v` → 3 passed. Full suite `pytest -q` → 24 passed, 0 failed (no regressions in existing policy/schema/tg_client tests).

## Cross-check against README "Answer Format"
Read the full "Answer Format" section (top-level fields, Part 1 `case`, Part 2 `sar`, Part 3 `next_best_actions`, `pattern` enum values, and the worked JSON example) directly from `README.md` and compared field-by-field against the brief's code. No discrepancies found — the brief's field list, types, and enum values (`pattern`, `status`, `verdict`, `evidence.source`, `route`, `evidence_requests.type`) match the README's own text exactly, including:
- `pattern`: `card_testing | card_not_present_fraud | card_not_present_new_device | out_of_region_use | account_takeover | undocumented | none`
- `status`: `open | closed_fraud | closed_legitimate | escalated`
- `verdict`: `fraud | legitimate | uncertain`
- `evidence.source`: `graph | document | customer | external`
- `route`: `auto | L1 | L2`
- `evidence_requests.type`: `customer_validation | step_up_auth | analyst_info`
- `action` (inside `next_best_actions.initial`/`final`) is correctly left as a plain `str`, not a `Literal` — the README says only "from the policy," with no enum listed in the Answer Format section itself.

Implemented `src/agent/schemas.py` and `tests/test_schemas.py` verbatim per the brief (Step 3/Step 1), since it already matched the README ground truth; no adjustments were needed.

## Concerns
None. Brief's field list was a perfect match to the README's Answer Format section — no gaps found.
