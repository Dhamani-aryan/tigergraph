from __future__ import annotations

import csv
from pathlib import Path

# Columns whose GSQL type should be STRING even though they look numeric or are
# sparsely populated categoricals, per the README's column-group descriptions.
_STRING_COLUMNS = {
    "TransactionID",
    "ProductCD",
    "card4",
    "card6",
    "addr1",
    "addr2",
    "P_emaildomain",
    "R_emaildomain",
    "customer_id",
    "channel",
    *(f"M{i}" for i in range(1, 10)),
    *(f"id_{i:02d}" for i in (12, 15, 16, 23, 27, 28, 29, 30, 31, 33, 34, 35, 36, 37, 38)),
    "DeviceType",
    "DeviceInfo",
}


def generate_attrs(csv_path: str | Path, primary_key: str) -> list[tuple[str, str]]:
    """Read just the header row and yield (attr_name, gsql_type) pairs.
    primary_key is excluded (it becomes the vertex's PRIMARY_ID, not a listed attr).
    """
    with open(csv_path, newline="", encoding="utf-8") as f:
        header = next(csv.reader(f))

    attrs: list[tuple[str, str]] = []
    for col in header:
        if col == primary_key:
            continue
        gsql_type = "STRING" if col in _STRING_COLUMNS else "DOUBLE"
        attrs.append((col, gsql_type))
    return attrs


def to_gsql_attr_list(attrs: list[tuple[str, str]]) -> str:
    return ",\n    ".join(f"{name} {gsql_type}" for name, gsql_type in attrs)
