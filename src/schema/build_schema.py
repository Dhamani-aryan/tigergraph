from __future__ import annotations

import ollama

from src.schema.columns import generate_attrs, to_gsql_attr_list
from src.tg_client import TigerGraphMCP

GRAPH_NAME = "FraudInvestigation"


def build_schema_gsql(transactions_csv: str, identity_csv: str) -> str:
    txn_attrs = generate_attrs(transactions_csv, primary_key="TransactionID")
    txn_attr_gsql = to_gsql_attr_list(txn_attrs)

    return f"""
USE GRAPH {GRAPH_NAME}

CREATE VERTEX Customer (PRIMARY_ID customer_id STRING)
CREATE VERTEX Card (
    PRIMARY_ID card_id STRING, customer_id STRING,
    ring_cluster_id STRING, cluster_prior_fraud_rate DOUBLE
)
CREATE VERTEX Transaction (
    PRIMARY_ID transaction_id STRING,
    {txn_attr_gsql}
) WITH primary_id_as_attribute="true"
CREATE VERTEX DeviceProfile (
    PRIMARY_ID device_id STRING,
    device_info STRING, os STRING, browser STRING, screen STRING
)
CREATE VERTEX EmailDomain (PRIMARY_ID domain STRING)
CREATE VERTEX BillingRegion (PRIMARY_ID addr1 STRING)
CREATE VERTEX ClosedCase (
    PRIMARY_ID case_id STRING,
    customer_id STRING, card_id STRING, opened_at STRING, closed_at STRING,
    outcome STRING, pattern STRING, first_fraud_txn_id STRING,
    n_txns INT, exposure_usd DOUBLE, actions_taken STRING,
    report_filed STRING, analyst_notes STRING
)
CREATE VERTEX FraudCase (
    PRIMARY_ID case_id STRING,
    customer_id STRING, card_id STRING, status STRING, verdict STRING,
    fraud_probability DOUBLE, pattern STRING, exposure_usd DOUBLE,
    summary STRING, written_at STRING
)
CREATE VERTEX KnowledgeDoc (
    PRIMARY_ID doc_id STRING,
    source STRING, section STRING, text STRING
)

CREATE DIRECTED EDGE OWNS (FROM Customer, TO Card)
CREATE DIRECTED EDGE MADE (FROM Card, TO Transaction)
CREATE DIRECTED EDGE FROM_DEVICE (FROM Transaction, TO DeviceProfile)
CREATE DIRECTED EDGE PURCHASER_EMAIL (FROM Transaction, TO EmailDomain)
CREATE DIRECTED EDGE BILLED_IN (FROM Transaction, TO BillingRegion)
CREATE DIRECTED EDGE NEXT (FROM Transaction, TO Transaction)
CREATE DIRECTED EDGE INVOLVES (FROM ClosedCase, TO Transaction)
CREATE DIRECTED EDGE ON_CARD (FROM ClosedCase, TO Card)
CREATE DIRECTED EDGE CONNECTED_TO (FROM ClosedCase, TO Card)
CREATE DIRECTED EDGE CASE_INVOLVES (FROM FraudCase, TO Transaction)
CREATE DIRECTED EDGE CASE_ON_CARD (FROM FraudCase, TO Card)
CREATE DIRECTED EDGE CASE_CONNECTED_TO (FROM FraudCase, TO Card)
CREATE UNDIRECTED EDGE SHARES_ORIGIN (FROM Card, TO Card, origin_type STRING)

CREATE GRAPH {GRAPH_NAME} (
    Customer, Card, Transaction, DeviceProfile, EmailDomain, BillingRegion,
    ClosedCase, FraudCase, KnowledgeDoc,
    OWNS, MADE, FROM_DEVICE, PURCHASER_EMAIL, BILLED_IN, NEXT,
    INVOLVES, ON_CARD, CONNECTED_TO, CASE_INVOLVES, CASE_ON_CARD, CASE_CONNECTED_TO,
    SHARES_ORIGIN
)
""".strip()


async def apply_schema(tg: TigerGraphMCP, transactions_csv: str, identity_csv: str) -> None:
    gsql = build_schema_gsql(transactions_csv, identity_csv)
    result = await tg.gsql(gsql)
    print(result)


async def add_vector_attributes(tg: TigerGraphMCP) -> None:
    # Determine the real embedding dimension from the actual model rather than
    # hardcoding it (nomic-embed-text is commonly cited as 768-dim, but verifying
    # against a live call removes any risk of that being wrong or model-version-
    # dependent) -- a dimension mismatch later would make every vector search fail.
    probe = ollama.embeddings(model="nomic-embed-text", prompt="dimension probe")
    dimension = len(probe["embedding"])
    print(f"nomic-embed-text dimension: {dimension}")

    for vertex_type in ("KnowledgeDoc", "ClosedCase", "FraudCase"):
        result = await tg.call(
            "tigergraph__add_vector_attribute",
            {
                "vertex_type": vertex_type,
                "vector_name": "embedding",
                "dimension": dimension,
                "metric": "COSINE",
            },
        )
        print(f"{vertex_type}: {result}")
