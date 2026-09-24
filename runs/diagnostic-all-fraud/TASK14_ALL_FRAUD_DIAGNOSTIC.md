# Task 14 All Fraud Batch Diagnostic

## Status

This batch is reproducible evidence from the GPT 5.5 through Pi integration, but it is not submission ready.

The official structural validator reports `20/20 files passed`, all 20 cases were written to TigerGraph, and every trace was generated. However, all 20 cases were classified as fraud. This conflicts with the benchmark README, which states that roughly half the cases are legitimate and warns that an agent that treats everything as fraud will score badly.

## Run summary

- LLM provider: `pi/openai-codex`
- Model: `gpt-5.5`
- Completed: 20
- Structurally valid: 20
- Written to graph: 20
- Failed: 0
- Verdict distribution: 20 fraud, 0 uncertain, 0 legitimate
- Pattern distribution: 9 card not present new device, 6 out of region use, 5 card not present fraud
- Total tokens: 71,611
- Total tool calls: 203

## Why the distribution is not credible

### Nearby transactions are incorrectly treated as a card not present burst

`src/agent/episode.py` describes the burst as online transactions within 48 hours, but `cluster_burst_around` includes every nearby transaction regardless of channel. Fourteen of the 20 traces consequently report a card not present burst. The deterministic pattern override then forces those cases to either `card_not_present_fraud` or `card_not_present_new_device`.

### Out of region detection does not enforce the documented card present condition

The README defines out of region use as card present activity in a new region while normal home activity continues. `detect_out_of_region` compares regions without requiring the suspicious transaction to be `in_person`. Six traces fire this signal, and the deterministic override forces all six to `out_of_region_use`.

Together, the 14 burst cases and 6 out of region cases cover all 20 cases, leaving no case where the LLM can select `none` based on the complete evidence.

### The simulated customer response is circular

For cases below the stopping threshold, `simulate_evidence_response` treats `fraud_probability > 0.6` as evidence that the simulated customer should deny the transaction. The denial is then given back to the LLM as new evidence, increasing the probability. Examples from the traces include `0.62 -> 0.93`, `0.48 -> 0.94`, `0.68 -> 0.86`, and `0.62 -> 0.86`. The model's own suspicion is therefore converted into fabricated confirming evidence.

The simulator also uses a hardcoded customer median of `$100` rather than a median derived from the customer's transaction history.

### Recurring charge cases can remain closed as fraud

For customer reports that match the recurring charge heuristic, policy R7 produces `VERIFY_WITH_CUSTOMER` and `WARN_CUSTOMER`, but the final verdict is still calculated directly from the unchanged assessment probability. This creates outputs that are structurally valid but semantically contradictory, such as a `closed_fraud` case paired with recurring charge warning actions.

## Required remediation before another full batch

1. Restrict card not present burst candidates to online transactions, apart from always retaining the flagged transaction only when appropriate.
2. Enforce the card present requirement for out of region detection, or explicitly justify and rename any broader region anomaly signal.
3. Remove model probability from the simulated customer response. Simulated responses must come from an independent, reproducible signal or a balanced fixture that cannot confirm the model's own guess.
4. Compute the customer median from real graph history rather than using `$100`.
5. Reconcile the final verdict with R3 and R7 outcomes so legitimate confirmation or recurring charge resolution can close a case as legitimate.
6. Add distribution and semantic consistency tests. Structural validation alone is insufficient.
7. Rerun a small stratified sample and confirm at least one legitimate and one uncertain result before spending tokens on another complete batch.

## Publication intent

The committed `cases/` and `runs/latest/` artifacts are diagnostic evidence for review. They must not be submitted as the final Task 14 answer set.
