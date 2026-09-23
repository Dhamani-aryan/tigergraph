## Task 3: Card ID derivation (customer_id → card_id mapping)

This is its own task because it's a genuine, non-obvious data-modeling decision (see Global Constraints) that Task 4's schema/loading depends on, and it's fully testable without touching TigerGraph at all.

**Files:**
- Create: `src/schema/__init__.py` (empty)
- Create: `src/schema/card_ids.py`
- Test: `tests/test_card_ids.py`

**Interfaces:**
- Produces: `build_card_id_map(case_pack_df, closed_cases_df) -> dict[str, str]` mapping `customer_id -> card_id` (e.g. `"C08623" -> "C08623-K2"`), and `card_id_for(customer_id, card_map) -> str` (falls back to `f"{customer_id}-K1"` for unmapped customers). Task 7's `load_cards_and_made_edges` imports both — this is what resolves the correct `card_id` before any `Card`/`OWNS`/`MADE` data is created, not a post-hoc patch (see Task 7's note on why order matters here).

- [ ] **Step 1: Write the failing test**

`tests/test_card_ids.py`:

```python
import pandas as pd

from src.schema.card_ids import build_card_id_map, card_id_for


def _case_pack_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"case_id": "HHG-003", "card_id": "C08623-K2", "customer_id": "C08623"},
            {"case_id": "HHG-007", "card_id": "C09933-K2", "customer_id": "C09933"},
        ]
    )


def _closed_cases_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "case_id": "CC-2649",
                "customer_id": "C03528",
                "card_id": "C03528-K1",
                "connected_card_ids": "C00255-K1|C01935-K1|C03551-K2",
            }
        ]
    )


def test_known_customers_use_referenced_suffix():
    card_map = build_card_id_map(_case_pack_df(), _closed_cases_df())
    assert card_map["C08623"] == "C08623-K2"
    assert card_map["C09933"] == "C09933-K2"
    assert card_map["C03528"] == "C03528-K1"


def test_connected_card_ids_are_parsed_into_the_map():
    card_map = build_card_id_map(_case_pack_df(), _closed_cases_df())
    assert card_map["C00255"] == "C00255-K1"
    assert card_map["C01935"] == "C01935-K1"
    assert card_map["C03551"] == "C03551-K2"


def test_unreferenced_customer_defaults_to_k1():
    card_map = build_card_id_map(_case_pack_df(), _closed_cases_df())
    assert card_id_for("C99999", card_map) == "C99999-K1"


def test_referenced_customer_uses_map_not_default():
    card_map = build_card_id_map(_case_pack_df(), _closed_cases_df())
    assert card_id_for("C08623", card_map) == "C08623-K2"
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv\Scripts\pytest tests/test_card_ids.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.schema.card_ids'`.

- [ ] **Step 3: Implement**

`src/schema/card_ids.py`:

```python
from __future__ import annotations

import pandas as pd


def build_card_id_map(
    case_pack_df: pd.DataFrame, closed_cases_df: pd.DataFrame
) -> dict[str, str]:
    """Map customer_id -> card_id, trusting the -K suffix given in the reference
    files. card1 is 1:1 with customer_id across the whole transactions.csv (verified
    empirically), so there is no second distinguishable card to derive from raw
    transaction data; the -K suffix is per-customer labeling assigned in the
    reference data, not evidence of multiple physical cards.
    """
    card_map: dict[str, str] = {}

    for _, row in case_pack_df.iterrows():
        card_map[row["customer_id"]] = row["card_id"]

    for _, row in closed_cases_df.iterrows():
        card_map[row["customer_id"]] = row["card_id"]
        connected = row.get("connected_card_ids")
        if isinstance(connected, str) and connected:
            for card_id in connected.split("|"):
                customer_id = card_id.split("-K")[0]
                card_map.setdefault(customer_id, card_id)

    return card_map


def card_id_for(customer_id: str, card_map: dict[str, str]) -> str:
    return card_map.get(customer_id, f"{customer_id}-K1")
```

- [ ] **Step 4: Run to verify it passes**

```bash
.venv\Scripts\pytest tests/test_card_ids.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/schema/__init__.py src/schema/card_ids.py tests/test_card_ids.py
git commit -m "feat: card_id derivation from case reference data"
```

---

