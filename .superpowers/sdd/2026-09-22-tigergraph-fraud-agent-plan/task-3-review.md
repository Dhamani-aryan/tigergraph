# Task 3 Review: Card ID Derivation

**Date:** 2026-09-22  
**Reviewer:** Claude Code (Haiku 4.5)  
**Commit:** `8303f6d` feat: card_id derivation from case reference data

---

## Executive Summary

✅ **SPEC COMPLIANCE: PASS**  
✅ **CODE QUALITY: PASS**

The implementation correctly fulfills all requirements from the task brief. All 4 tests pass with actual execution. No issues identified.

---

## Specification Compliance

### Files Created
All 3 files specified in the brief were created correctly:

1. ✅ `src/schema/__init__.py` - Empty package marker present
2. ✅ `src/schema/card_ids.py` - Core implementation
3. ✅ `tests/test_card_ids.py` - Comprehensive test suite

### Implementation (`src/schema/card_ids.py`)

**Exact Match Verification:** The implementation matches the brief's specification **byte-for-byte**.

#### Function 1: `build_card_id_map(case_pack_df, closed_cases_df) -> dict[str, str]`

Logic verified:
- ✅ Iterates `case_pack_df` and adds all customer_id → card_id mappings
- ✅ Iterates `closed_cases_df` and adds customer_id → card_id mappings from direct rows
- ✅ Parses `connected_card_ids` pipe-delimited field correctly using `split("|")`
- ✅ Extracts customer_id from each card_id via `split("-K")[0]`
- ✅ Uses `setdefault()` to preserve direct customer entries over connected IDs (correct priority)
- ✅ Trusts -K suffix from reference data (does NOT derive it)
- ✅ Returns `dict[str, str]` as specified

#### Function 2: `card_id_for(customer_id, card_map) -> str`

Logic verified:
- ✅ Looks up customer_id in the map
- ✅ Defaults to `f"{customer_id}-K1"` for unmapped customers (only fallback behavior)
- ✅ Never derives suffix any other way
- ✅ Returns `str` as specified

### Test Coverage

All 4 tests specified in the brief are present and match exactly:

#### Test 1: `test_known_customers_use_referenced_suffix()`
Tests that customers with direct entries in reference data use their assigned card_ids:
- ✅ `card_map["C08623"] == "C08623-K2"` (from case_pack_df)
- ✅ `card_map["C09933"] == "C09933-K2"` (from case_pack_df)
- ✅ `card_map["C03528"] == "C03528-K1"` (from closed_cases_df)

**Result:** PASSED

#### Test 2: `test_connected_card_ids_are_parsed_into_the_map()`
Tests that customers from the `connected_card_ids` field are correctly parsed:
- ✅ `card_map["C00255"] == "C00255-K1"` (from connected_card_ids)
- ✅ `card_map["C01935"] == "C01935-K1"` (from connected_card_ids)
- ✅ `card_map["C03551"] == "C03551-K2"` (from connected_card_ids)

**Result:** PASSED

#### Test 3: `test_unreferenced_customer_defaults_to_k1()`
Tests that unmapped customers default to -K1 suffix:
- ✅ `card_id_for("C99999", card_map) == "C99999-K1"`

**Result:** PASSED

#### Test 4: `test_referenced_customer_uses_map_not_default()`
Tests that mapped customers use the map, not the default:
- ✅ `card_id_for("C08623", card_map) == "C08623-K2"`

**Result:** PASSED

### Test Execution

```
============================= test session starts =============================
platform win32 -- Python 3.10.5, pytest-9.1.1, pluggy-1.6.0
collected 4 items

tests/test_card_ids.py::test_known_customers_use_referenced_suffix PASSED [ 25%]
tests/test_card_ids.py::test_connected_card_ids_are_parsed_into_the_map PASSED [ 50%]
tests/test_card_ids.py::test_unreferenced_customer_defaults_to_k1 PASSED [ 75%]
tests/test_card_ids.py::test_referenced_customer_uses_map_not_default PASSED [100%]

============================== 4 passed in 0.43s ==============================
```

**Outcome:** All 4 tests pass as expected.

### Spec Requirement: Suffix Derivation

The brief explicitly requires:
> "trusts the -K suffix from case_pack.csv/closed_cases_history.csv reference data, defaults to -K1 only for unreferenced customers — do not accept an implementation that derives the suffix any other way"

✅ **Verified:** The implementation:
- Does NOT attempt to derive the suffix from transaction data
- Does NOT use heuristics or patterns to generate suffixes
- Only trusts the -K suffix directly provided in reference data
- Defaults to -K1 as a fallback only for unmapped customers

---

## Code Quality Analysis

### Type Annotations
✅ Present and correct throughout:
- Function signatures properly annotated
- Return types explicit: `-> dict[str, str]` and `-> str`
- Parameter types clear: `pd.DataFrame`
- Uses PEP 563 future annotations for forward compatibility

### Documentation
✅ Docstring present and explains the data modeling decision:
- Clarifies that card1 is 1:1 with customer_id in transactions.csv
- Explains that -K suffix is per-customer labeling, not evidence of multiple cards
- Provides context for why trusting reference data is the correct approach

### Error Handling
✅ Appropriate defensive coding:
- `row.get("connected_card_ids")` safely handles missing field
- `isinstance(connected, str)` checks type before string operations
- Empty string check prevents parsing empty values
- `setdefault()` prevents accidental overwrites

### Code Style
✅ Clean and readable:
- Clear variable names (customer_id, card_id, card_map, connected)
- Logical flow: direct entries first, then connected IDs
- No unnecessary complexity
- Follows Python conventions

### Test Quality
✅ Tests are well-structured:
- Helper functions `_case_pack_df()` and `_closed_cases_df()` provide clear, reusable test data
- Assertions are specific and meaningful
- No magic strings or numbers
- Tests are independent (each rebuilds card_map)
- No side effects (no filesystem or network calls)

### Performance Considerations
✅ Acceptable for this use case:
- `iterrows()` is not optimal for large datasets, but acceptable for reference data which is small
- No unnecessary operations or loops
- Linear time complexity in dataset size

### Constraints Compliance
✅ Pure Python, no prohibited dependencies:
- Only uses pandas (already in project dependencies)
- No TigerGraph calls
- No network or database dependencies
- Fully testable in isolation

---

## Findings Summary

### Critical Issues
None

### High Severity Issues
None

### Medium Severity Issues
None

### Low Severity Issues
None

### Code Quality Observations
None. The implementation is clean, correct, and well-tested.

---

## Verdict

| Aspect | Result | Notes |
|--------|--------|-------|
| Files Created | ✅ | All 3 files present and correct |
| Implementation Logic | ✅ | Exact match to brief specification |
| Test Coverage | ✅ | All 4 tests present and passing |
| Spec Compliance | ✅ | Trusts reference data, defaults to -K1 only |
| Code Quality | ✅ | Clean, documented, well-tested |
| Type Safety | ✅ | Proper annotations throughout |
| Error Handling | ✅ | Defensive coding appropriate for input validation |
| Constraints | ✅ | Pure Python, no prohibited dependencies |

**SPECIFICATION COMPLIANCE: ✅ PASS**

**CODE QUALITY: ✅ PASS**

---

## Approval

This task is **READY TO MERGE**. The implementation meets all specified requirements with no issues identified. The code is of production quality.

Dependency: Task 4's schema/loading can safely depend on the exports from this task (`build_card_id_map` and `card_id_for`).
