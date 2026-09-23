# Task 3 Report: Card ID Derivation

## Status
**DONE**

## Commit(s)
- `8303f6d` feat: card_id derivation from case reference data

## Test Summary
All 4 tests pass (100%): `test_known_customers_use_referenced_suffix`, `test_connected_card_ids_are_parsed_into_the_map`, `test_unreferenced_customer_defaults_to_k1`, `test_referenced_customer_uses_map_not_default`.

## Details
Implemented card ID derivation logic as specified:
- Created `src/schema/__init__.py` (empty package marker)
- Created `src/schema/card_ids.py` with two functions:
  - `build_card_id_map()`: Maps customer_id → card_id from case_pack_df and closed_cases_df, extracting customer IDs from the `connected_card_ids` pipe-delimited field
  - `card_id_for()`: Retrieves card_id for a customer, defaulting to `{customer_id}-K1` if not in the map
- Created `tests/test_card_ids.py` with comprehensive test coverage

## Concerns
None. All code transcribed verbatim from brief, tests pass, commit created locally as specified.
