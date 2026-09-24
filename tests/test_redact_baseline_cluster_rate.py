from __future__ import annotations

from src.agent.graph_flow import _redact_baseline_cluster_rate


def test_redacts_dict_rate_below_threshold():
    # The exact real-world shape: the known 3,565-card supercluster sitting
    # at the dataset baseline (~0.847), which every case in the batch was
    # citing as if it were meaningful evidence.
    result = _redact_baseline_cluster_rate(
        {"ring_cluster_id": "RING-C00001-K1", "cluster_prior_fraud_rate": 0.85}
    )
    assert result["cluster_prior_fraud_rate"] is None
    assert result["ring_cluster_id"] is None


def test_keeps_dict_rate_at_or_above_threshold():
    result = _redact_baseline_cluster_rate(
        {"ring_cluster_id": "RING-REAL", "cluster_prior_fraud_rate": 0.97}
    )
    assert result["cluster_prior_fraud_rate"] == 0.97
    assert result["ring_cluster_id"] == "RING-REAL"


def test_redacts_within_a_list_of_cards():
    # device_neighbors' shared_cards shape.
    result = _redact_baseline_cluster_rate([
        {"id": "C001-K1", "ring_cluster_id": "RING-A", "cluster_prior_fraud_rate": 0.847},
        {"id": "C002-K1", "ring_cluster_id": "RING-B", "cluster_prior_fraud_rate": 0.97},
    ])
    assert result[0]["cluster_prior_fraud_rate"] is None
    assert result[1]["cluster_prior_fraud_rate"] == 0.97


def test_leaves_other_fields_and_none_rate_untouched():
    result = _redact_baseline_cluster_rate({"id": "C001-K1", "ring_cluster_id": None, "cluster_prior_fraud_rate": None})
    assert result == {"id": "C001-K1", "ring_cluster_id": None, "cluster_prior_fraud_rate": None}


def test_handles_non_dict_non_list_passthrough():
    assert _redact_baseline_cluster_rate("plain string") == "plain string"
    assert _redact_baseline_cluster_rate(42) == 42
