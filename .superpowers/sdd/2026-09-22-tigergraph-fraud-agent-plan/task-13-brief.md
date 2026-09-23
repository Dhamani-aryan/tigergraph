## Task 13: SAR narrative generation

**Files:**
- Modify: `src/run/run_case.py`
- Create: `src/agent/sar_writer.py`
- Test: `tests/test_sar_writer.py`

**Interfaces:**
- Produces: `async write_sar_narrative(case_row: dict, case_record: dict) -> str`. `run_single_case` (Task 12) calls this only when `sar_info["sar_file"]` is true, replacing the empty-string placeholder from Task 12 Step 3.

- [ ] **Step 1: Write `src/agent/sar_writer.py`**

```python
from __future__ import annotations

from pydantic import BaseModel

from src.agent.llm import generate_structured


class SARNarrativeOutput(BaseModel):
    narrative: str


async def write_sar_narrative(case_row: dict, case_summary: dict) -> str:
    prompt = (
        "Write a suspicious activity report narrative, six to twelve sentences, covering "
        "who (customer, cards, merchants, devices), what happened, when (dates), where "
        "(locations, channels), how it was carried out, and why it is suspicious. Base it "
        "only on these facts -- do not invent details:\n\n"
        f"Customer: {case_row['customer_id']}, Card: {case_row['card_id']}\n"
        f"Pattern: {case_summary['pattern']}\n"
        f"Fraud probability: {case_summary['fraud_probability']}\n"
        f"Evidence claims: {case_summary['evidence_claims']}\n"
        f"Trigger: {case_row.get('trigger_text', '')}\n"
    )
    result = await generate_structured(prompt, SARNarrativeOutput)
    return result.narrative
```

- [ ] **Step 2: Write `tests/test_sar_writer.py`**

```python
import pytest

from src.agent.sar_writer import write_sar_narrative


@pytest.mark.asyncio
async def test_narrative_mentions_key_facts():
    case_row = {
        "customer_id": "C04570",
        "card_id": "C04570-K1",
        "trigger_text": "Real-time model scored transaction 3450629 ($100.09, online) at 0.57.",
    }
    case_summary = {
        "pattern": "card_testing",
        "fraud_probability": 0.86,
        "evidence_claims": ["Three small authorizations then a larger purchase"],
    }
    narrative = await write_sar_narrative(case_row, case_summary)
    assert len(narrative) > 100
    assert "C04570" in narrative or "card" in narrative.lower()
```

- [ ] **Step 3: Run**

```bash
.venv\Scripts\pytest tests/test_sar_writer.py -v
```

Expected: passes; a weak/short narrative here is a real quality signal worth tuning the prompt for, since the SAR narrative is explicitly "the one place to be complete" per the README.

- [ ] **Step 4: Wire it into `run_case.py`** — modify the `SAR(...)` construction in `src/run/run_case.py` (Task 12 Step 3):

```python
from src.agent.sar_writer import write_sar_narrative

# ... inside run_single_case, replace the SAR(...) block with:
narrative = ""
if sar_info["sar_file"]:
    narrative = await write_sar_narrative(case_row, assessment)

sar = SAR(
    file=sar_info["sar_file"],
    reason=sar_info["sar_reason"],
    narrative=narrative,
    subjects=[case_row["customer_id"], case_row["card_id"]] if sar_info["sar_file"] else [],
    total_amount_usd=case_record.exposure_usd if sar_info["sar_file"] else 0.0,
    activity_dates=[] if not sar_info["sar_file"] else [str(case_row["opened_at"])[:10]] * 2,
)
```

- [ ] **Step 5: Re-run the Task 12 end-to-end test to confirm nothing broke**

```bash
.venv\Scripts\pytest tests/test_run_case_hhg017.py -v
```

Expected: still passes.

- [ ] **Step 6: Commit**

```bash
git add src/agent/sar_writer.py tests/test_sar_writer.py src/run/run_case.py
git commit -m "feat: SAR narrative generation, wired into the case runner"
```

---

