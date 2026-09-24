# Provenance: Task 14 consolidated 20-case output

This branch (`fix/task14-evidence-classification`) previously had 10 of the 20
required cases, produced by the GPT-5.5/Pi investigation run:

```
HHG-001, HHG-008, HHG-009, HHG-010, HHG-011, HHG-013, HHG-014, HHG-017, HHG-019, HHG-020
```

The remaining 10 cases were imported from Naman's independently completed run
on `Namans12/tigergraph:tigergraph-fraud-agent` (post-calibration-fix
codebase, commit `a8bb493`), executed locally against Ollama rather than
Groq/GPT-5.5:

```
HHG-002, HHG-003, HHG-004, HHG-005, HHG-006, HHG-007, HHG-012, HHG-015, HHG-016, HHG-018
```

- **LLM backend:** Ollama `qwen3:4b-instruct` (local), embeddings via Ollama
  `nomic-embed-text`
- **Agent code commit:** `a8bb493` on `Namans12/tigergraph:tigergraph-fraud-agent`
  (includes the 5 calibration fixes from `TASK14_ALL_FRAUD_DIAGNOSTIC.md`)
- **TigerGraph workspace:** same shared Savanna instance used by the rest of
  the project
- **Source files (before copy):** `cases_backup/<id>.json` and
  `runs_backup/latest/traces/<id>.trace.json` in that worktree

These 10 files were copied as-is into `cases-smoke-fix/` and
`runs/smoke-fix/traces/` without modification. `runs/smoke-fix/batch_summary.json`
was regenerated from the actual 20 on-disk case files (not fabricated).

## Known issue in the imported (Ollama-backend) subset

The quick output validator (`python -m src.run.validate_outputs cases-smoke-fix`,
answer-only mode, no `--traces`) flags 5 semantic violations, all in the
Ollama-generated cases:

- `HHG-002`, `HHG-005`, `HHG-012`, `HHG-015`: simulated evidence response not
  labeled `"Simulated assumption:"`
- `HHG-003`: verdict `legitimate` paired with pattern `account_takeover`
  (expected `none`)
- `HHG-012`: verdict `legitimate` paired with pattern `out_of_region_use`
  (expected `none`), plus the labeling issue above

These are real output-quality gaps in the local Ollama backend's generation
(likely instruction-following weaker than GPT-5.5/Pi on these two specific
formatting/consistency rules), not consolidation errors. Flagging here rather
than silently passing — worth a follow-up fix if time allows, but per
instructions no case was rerun or modified to fix these.
