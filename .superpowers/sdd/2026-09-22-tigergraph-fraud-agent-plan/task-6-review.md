# Task 6 Review: Answer-JSON Pydantic schemas

## Verdict

- **Spec compliance: ✅ PASS** — every field the README's "Answer Format" section documents is present in `src/agent/schemas.py` with the correct name, correct type/shape, and (for every enum-like field) the correct closed set of literal values, no more and no fewer.
- **Code quality: ✅ PASS** — small, clean, idiomatic Pydantic v2 models; no defects found. One low-severity design note below (not a defect).
- **Tests: ✅ CONFIRMED** — I ran them myself, output below. They match the report exactly.

## What I actually checked

1. Read the task brief (`task-6-brief.md`) in full.
2. Read the implementer's report (`task-6-report.md`) in full.
3. Read the diff (`review-517da8c..1e9f6fe.diff`) and confirmed it touches exactly three files: `src/agent/__init__.py` (0 bytes, confirmed on disk), `src/agent/schemas.py`, `tests/test_schemas.py` — no scope creep.
4. Read the **entire "Answer Format" section of `README.md`** myself, directly (lines 289–434: top-level table, Part 1 `case` table, Part 2 `sar` table, Part 3 `next_best_actions` table, the `pattern` values line, and the full worked `### Example` JSON block), plus the surrounding "Notes" section (lines 436–443) and the policy/actions section (lines ~203–263) for cross-reference on the `action` field. I did not rely on the implementer's or the brief's paraphrase of the README — I read the source table cell by cell against the model field by field.
5. Read `src/agent/schemas.py` in full on disk.
6. Ran `.venv\Scripts\pytest tests/test_schemas.py -v` myself (not just trusted the report) — 3 passed. Also ran the full suite (`pytest -q`) — 24 passed, matching the report's claim of no regressions.
7. Confirmed HEAD is at the claimed commit `1e9f6fe` and the worktree is clean (`git status --short` empty).

## Field-by-field audit (README vs. `schemas.py`)

### Top level (`AnswerFile`)

| README field | README type | Model field | Verdict |
|---|---|---|---|
| `case_id` | string | `case_id: str` | ✅ |
| `case` | object | `case: CaseRecord` | ✅ |
| `evidence_requests` | list, "Empty if you asked for nothing" | `evidence_requests: list[EvidenceRequestRecord] = []` | ✅ |
| `next_best_actions` | object | `next_best_actions: NextBestActionSet` (required) | ✅ |
| `sar` | object | `sar: SAR` (required) | ✅ |
| `stop_reason` | string | `stop_reason: str` | ✅ |
| `tool_calls` | int | `tool_calls: int` | ✅ |
| `tokens` | int | `tokens: int` | ✅ |
| `latency_s` | number | `latency_s: float` | ✅ |

### Part 1: `case` (`CaseRecord`)

| README field | README type | Model field | Verdict |
|---|---|---|---|
| `status` | `open`\|`closed_fraud`\|`closed_legitimate`\|`escalated` | `Status = Literal["open","closed_fraud","closed_legitimate","escalated"]` | ✅ exact match, 4 values |
| `verdict` | `fraud`\|`legitimate`\|`uncertain` | `Verdict = Literal["fraud","legitimate","uncertain"]` | ✅ exact match, 3 values |
| `fraud_probability` | number 0–1 | `fraud_probability: float` | ✅ type matches (see note below on the 0–1 range not being enforced — not a spec violation, README doesn't require validation, just documents the meaning) |
| `pattern` | enum, see below | `Pattern` literal | ✅ — checked separately below |
| `pattern_description` | "Required when `pattern` is `undocumented`... Otherwise `\"\"`" | `pattern_description: str = ""` | ✅ correctly a defaultable string, NOT incorrectly forced non-empty. Cross-field conditional requirement isn't schema-enforceable without a validator and the brief didn't ask for one — structurally correct |
| `affected_txn_ids` | list of strings, "Empty if legitimate" | `list[str] = []` | ✅ |
| `first_suspicious_txn_id` | string or `""` | `first_suspicious_txn_id: str = ""` | ✅ correctly optional/defaultable, not forced non-empty |
| `connected_card_ids` | list of strings | `list[str] = []` | ✅ |
| `connected_device_profiles` | list of strings | `list[str] = []` | ✅ |
| `exposure_usd` | number | `exposure_usd: float = 0.0` | ✅ |
| `evidence` | list of objects: `claim`, `source`, `ref`, `entity_ids` | `list[Evidence] = []`, `Evidence{claim: str, source: EvidenceSource, ref: str, entity_ids: list[str]=[]}` | ✅ all 4 sub-fields present, correct types |
| `similar_prior_cases` | list of strings, "Empty if none" | `list[str] = []` | ✅ |
| `summary` | string | `summary: str` (required) | ✅ README gives no default/optional language, correctly required |
| `written_to_graph` | boolean | `written_to_graph: bool` (required) | ✅ |
| `graph_case_id` | string or `""` | `graph_case_id: str = ""` | ✅ correctly optional/defaultable, not forced non-empty |

**`Evidence.source` enum**: README lists `graph`\|`document`\|`customer`\|`external`. Model: `EvidenceSource = Literal["graph","document","customer","external"]`. ✅ exact match, 4 values, no extras.

### `pattern` enum — checked with extra care per the ask

README's own "`pattern` values" line (line 360): `card_testing · card_not_present_fraud · card_not_present_new_device · out_of_region_use · account_takeover · undocumented · none`

Model:
```python
Pattern = Literal[
    "card_testing",
    "card_not_present_fraud",
    "card_not_present_new_device",
    "out_of_region_use",
    "account_takeover",
    "undocumented",
    "none",
]
```
✅ Exactly 7 values, in the same order, including both `undocumented` and `none`. No extra or renamed values. This is the field the task explicitly flagged as highest-risk and it is correct.

### Part 2: `sar` (`SAR`)

| README field | README type | Model field | Verdict |
|---|---|---|---|
| `file` | boolean | `file: bool` (required) | ✅ |
| `reason` | string | `reason: str` (required) | ✅ |
| `narrative` | string, required when `file` true, else `""` | `narrative: str = ""` | ✅ correctly defaultable, not forced non-empty |
| `subjects` | list of strings | `subjects: list[str] = []` | ✅ |
| `total_amount_usd` | number | `total_amount_usd: float = 0.0` | ✅ |
| `activity_dates` | list of **two** strings | `activity_dates: list[str] = []` | ✅ structurally sound — see note |

**"If `file` is false" defaults** (README line 348: narrative `""`, subjects `[]`, total_amount_usd `0`, activity_dates `[]`): I confirmed nothing in `SAR` forces these to be non-empty when `file=False`. All four fields have permissive defaults/types that allow the false-case shape. The brief's own `test_legitimate_verdict_shape` test exercises exactly this (`sar.file=False` with all-empty companions) and it passes. ✅

Note on `activity_dates`: README says "list of two strings," which is a stricter constraint (fixed length 2) than the model's plain `list[str]`. A `tuple[str, str]` would be more literal to "two strings" but would conflict with the documented empty-list case when `file` is false, so under-constraining here (like `pattern_description`/`narrative`) is the correct trade-off given the brief's scope (field name/type/enum matching, not cross-field length validation). Not a defect.

### Part 3: `next_best_actions` (`NextBestActionSet`, `ActionEntry`)

| README field | README type | Model field | Verdict |
|---|---|---|---|
| `initial` | list of `{action, route, reason}` | `initial: list[ActionEntry]` (required) | ✅ |
| `final` | same shape | `final: list[ActionEntry]` (required) | ✅ |
| `what_changed` | string | `what_changed: str` (required) | ✅ |
| `action` (inside entries) | "from the policy" (no enum given in Answer Format section) | `action: str` | ✅ per Answer-Format section text |
| `route` (inside entries) | `auto`\|`L1`\|`L2` | `Route = Literal["auto","L1","L2"]` | ✅ exact match, 3 values |
| `reason` (inside entries) | string | `reason: str` (required) | ✅ |

**Low-severity observation (not a defect):** elsewhere in the README (the policy/actions tables around lines 203–225), `action` values are in fact a closed, enumerable set (`ALLOW_TRANSACTION`, `MONITOR_CARD`, `MONITOR_CONNECTED_CARDS`, `WARN_CUSTOMER`, `VERIFY_WITH_CUSTOMER`, `STEP_UP_AUTH`, `GENERATE_REPORT`, `CREATE_CASE`, `ESCALATE_TO_ANALYST`, `CLOSE_NO_FRAUD`, `DECLINE_TRANSACTION`, `BLOCK_CARD`, `BLOCK_ALL_CARDS`, `FILE_REPORT` — 14 values). The Answer Format section itself (what the task brief scoped this audit to) only says "from the policy" with no enum listed there, and the brief's Step 3 code explicitly specifies `action: str`. The implementer followed the brief exactly and its own reasoning for not narrowing to a `Literal` is defensible (the grading-relevant section doesn't define the enum inline, and over-constraining could reject a valid-but-differently-worded action instead of scoring it). This is worth a future task's attention (e.g., a later task that wires actions to the policy) but is not a spec violation of Task 6's scope, and I would not block on it.

### `evidence_requests` entries (`EvidenceRequestRecord`)

| README field | README type | Model field | Verdict |
|---|---|---|---|
| `type` | `customer_validation`\|`step_up_auth`\|`analyst_info` | `EvidenceRequestType = Literal["customer_validation","step_up_auth","analyst_info"]` | ✅ exact match, 3 values |
| `asked_after_step` | int | `asked_after_step: int` | ✅ |
| `assumed_response` | string | `assumed_response: str` | ✅ |

## README worked example — round-trip verified

I confirmed the README's own `### Example` JSON block (lines 366–434) is what `tests/test_schemas.py`'s `README_EXAMPLE` constant reproduces (the brief transcribes it, dropping only the second `evidence` array entry and shortening two narrative-adjacent strings, which are immaterial to schema validation since they're free-text `str` fields). This means the test is validating against the real README ground truth, not a hand-rolled fixture, and it passes.

## Test results (run myself)

```
$ .venv\Scripts\pytest tests/test_schemas.py -v
tests/test_schemas.py::test_readme_example_parses PASSED                 [ 33%]
tests/test_schemas.py::test_round_trip_json PASSED                       [ 66%]
tests/test_schemas.py::test_legitimate_verdict_shape PASSED              [100%]
============================== 3 passed in 0.03s ==============================

$ .venv\Scripts\pytest -q
........................                                                 [100%]
24 passed in 4.78s
```

Matches the report's claims exactly (3 passed for the targeted file, 24 passed for the full suite, no regressions).

## Findings by severity

- **Critical:** none.
- **Major:** none.
- **Minor:** none — the `activity_dates` "list of two strings" vs. plain `list[str]` is a deliberate, correct under-constraint given the conditional empty-list case, not a bug.
- **Low / informational:**
  1. `fraud_probability` is typed `float` with no `ge=0, le=1` constraint, so the model will accept out-of-range values. README documents it as "number 0–1" descriptively, not as a hard schema rule, and the brief didn't ask for range validation — flagging only as a possible future hardening, not a scoring risk (bad values would still fail scoring/grading downstream, just not at parse time).
  2. `action` inside `ActionEntry` is `str` rather than a `Literal` of the 14 policy-defined action names found elsewhere in the README (outside the Answer Format section). Matches the brief exactly; worth revisiting if a later task wants stricter validation against the policy.

## Scope / process check

- Diff touches exactly the 3 files the brief specifies (`src/agent/__init__.py`, `src/agent/schemas.py`, `tests/test_schemas.py`) — no unrelated changes.
- `src/agent/__init__.py` is 0 bytes as claimed.
- HEAD is at the claimed commit `1e9f6fe`, working tree clean.
- Implementer's report is accurate and does not overclaim; its README cross-check summary matches what I independently found.

## Overall

Task 6 is complete and correct. Every field name, type/shape, and enum in the README's "Answer Format" section — including the highest-risk `pattern` 7-value enum, and the optional/defaultable treatment of `pattern_description`, `first_suspicious_txn_id`, `graph_case_id`, and the SAR "if file is false" fields — is faithfully represented in `src/agent/schemas.py`. No changes required.
