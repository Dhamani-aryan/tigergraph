# Task 14 feature audit (no LLM)

Source: `csv`. Generated 2026-09-24 15:57:23 UTC. Deterministic layer only: no LLM call, no graph write. Baselines use strictly-prior transactions; the risk score is context only.

## Summary

| Case | Trigger | Chan | Prod | Hist | Amt ratio / pct / class | Product | Region (prior, class) | Online 48h | Card test | CNP | OOR | Recurrence | Device | Suspicious families | Benign families | Sim. response |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| HHG-001 | risk_score | in_person | W | 358 | 0.92 / 0.46 / normal | established (356) | 444.0 (10, established) | 0 | - | - (n/a) | - | none | n/a | - | behavioral_consistency, geographic_consistency | confirmed_legitimate |
| HHG-002 | risk_score | online | C | 35 | 6.18 / 1.00 / extreme | established (35) | n/a (not_applicable) | 0 | - | - (1, routine/none) | - | none | n/a | behavioral_anomaly | - | no_reply |
| HHG-003 | customer_report | in_person | W | 985 | 0.72 / 0.28 / normal | established (936) | 330.0 (42, established) | 0 | - | - (n/a) | - | candidate | n/a | customer_statement | behavioral_consistency, geographic_consistency | on file: denies |
| HHG-004 | customer_report | online | C | 209 | 4.63 / 0.99 / extreme | established (209) | n/a (not_applicable) | 1 | - | doc 2-4 (2, routine/none) | - | none | none (6 cards) | behavioral_anomaly, identity_anomaly, customer_statement | - | on file: denies |
| HHG-005 | risk_score | online | R | 90 | 0.85 / 0.23 / normal | established (6) | 330.0 (85, not_applicable) | 0 | - | - (1, routine/none) | - | none | generic (110 cards) | identity_anomaly | behavioral_consistency | confirmed_legitimate |
| HHG-006 | customer_report | online | C | 199 | 7.66 / 0.96 / elevated | established (3) | 264.0 (28, not_applicable) | 3 | - | doc 2-4 (4, temporal) | - | none | generic (494 cards) | temporal_pattern, identity_anomaly, customer_statement | - | on file: denies |
| HHG-007 | risk_score | in_person | W | 2432 | 1.45 / 0.71 / normal | established (2216) | 264.0 (2221, established) | 0 | - | - (n/a) | - | strong | n/a | - | behavioral_consistency, geographic_consistency | confirmed_legitimate |
| HHG-008 | customer_report | online | C | 900 | 1.41 / 0.68 / normal | established (899) | n/a (not_applicable) | 13 | - | high-vol (14, routine/none) | - | strong | generic (125 cards) | - | behavioral_consistency, customer_confirmation | on file: disputes_recurring |
| HHG-009 | customer_report | online | S | 46 | 0.60 / 0.30 / normal | established (39) | 203.0 (1, not_applicable) | 0 | - | - (1, routine/none) | - | none | n/a | customer_statement | behavioral_consistency | on file: denies |
| HHG-010 | risk_score | online | R | 33 | 14.50 / 1.00 / extreme | established (9) | 469.0 (24, not_applicable) | 0 | - | - (1, routine/none) | - | none | generic (185 cards) | behavioral_anomaly, identity_anomaly | - | denies |
| HHG-011 | customer_report | online | C | 10335 | 4.59 / 0.97 / elevated | established (10334) | n/a (not_applicable) | 63 | - | high-vol (62, routine/none) | - | none | corroborated (3 cards) | identity_anomaly, direct_network_corroboration, customer_statement | - | on file: denies |
| HHG-012 | risk_score | in_person | W | 913 | 0.63 / 0.25 / normal | established (852) | 494.0 (21, established) | 0 | - | - (n/a) | - | candidate | n/a | - | behavioral_consistency, geographic_consistency | confirmed_legitimate |
| HHG-013 | risk_score | online | C | 1439 | 0.60 / 0.15 / normal | rare (1) | n/a (not_applicable) | 0 | - | - (1, routine/none) | - | none | generic (59 cards) | identity_anomaly | - | no_reply |
| HHG-014 | analyst_request | online | C | 71 | 1.34 / 0.79 / normal | rare (1) | 191.0 (36, not_applicable) | 0 | - | - (1, routine/none) | - | none | generic (44 cards) | identity_anomaly | - | no_reply |
| HHG-015 | risk_score | online | R | 68 | 6.00 / 1.00 / extreme | established (23) | 327.0 (0, not_applicable) | 0 | - | - (1, routine/none) | - | none | none (6 cards) | behavioral_anomaly, identity_anomaly | - | denies |
| HHG-016 | customer_report | online | C | 48 | 1.58 / 0.75 / normal | established (48) | n/a (not_applicable) | 0 | - | - (1, routine/none) | - | none | generic (143 cards) | identity_anomaly, customer_statement | behavioral_consistency | on file: denies |
| HHG-017 | risk_score | online | R | 52 | 0.50 / 0.21 / normal | established (29) | 204.0 (5, not_applicable) | 2 | - | doc 2-4 (3, temporal) | - | none | generic (164 cards) | temporal_pattern, identity_anomaly | behavioral_consistency | no_reply |
| HHG-018 | customer_report | in_person | W | 5864 | 0.45 / 0.18 / normal | established (5373) | 126.0 (565, established) | 4 | - | - (n/a) | - | candidate | n/a | customer_statement | behavioral_consistency, geographic_consistency | on file: denies |
| HHG-019 | risk_score | online | R | 220 | 0.85 / 0.40 / normal | established (13) | 264.0 (5, not_applicable) | 0 | - | - (1, routine/none) | - | candidate | corroborated (3 cards) | identity_anomaly, direct_network_corroboration | behavioral_consistency | denies |
| HHG-020 | risk_score | online | R | 83 | 1.16 / 0.59 / normal | unseen (0) | 264.0 (83, not_applicable) | 0 | - | - (1, routine/none) | - | none | generic (232 cards) | behavioral_anomaly, identity_anomaly | - | no_reply |

## Detector regression facts: 39/39 hold

Properties the detectors must satisfy on real data (from the data audit). They are not labels.

| Case | Fact | Expected | Actual | OK |
|---|---|---|---|---|
| HHG-001 | flagged_channel | in_person | in_person | yes |
| HHG-001 | online_48h | 0 | 0 | yes |
| HHG-001 | amount_ratio_approx | 0.92 | 0.918 | yes |
| HHG-001 | region_prior | ('444.0', 10) | ('444.0', 10) | yes |
| HHG-001 | cnp_documented | False | False | yes |
| HHG-001 | oor | False | False | yes |
| HHG-003 | flagged_channel | in_person | in_person | yes |
| HHG-003 | cnp_documented | False | False | yes |
| HHG-003 | recurrence_not_strong | True | True | yes |
| HHG-007 | flagged_channel | in_person | in_person | yes |
| HHG-007 | region_prior | ('264.0', 2221) | ('264.0', 2221) | yes |
| HHG-007 | cnp_documented | False | False | yes |
| HHG-007 | oor | False | False | yes |
| HHG-009 | flagged_channel | online | online | yes |
| HHG-009 | oor | False | False | yes |
| HHG-012 | flagged_channel | in_person | in_person | yes |
| HHG-012 | online_48h | 0 | 0 | yes |
| HHG-012 | region_prior | ('494.0', 21) | ('494.0', 21) | yes |
| HHG-012 | cnp_documented | False | False | yes |
| HHG-012 | oor | False | False | yes |
| HHG-013 | cnp_documented | False | False | yes |
| HHG-013 | cnp_has_no_in_person | True | True | yes |
| HHG-014 | cnp_documented | False | False | yes |
| HHG-014 | network_corroborated | False | False | yes |
| HHG-015 | flagged_channel | online | online | yes |
| HHG-015 | oor | False | False | yes |
| HHG-015 | cnp_documented | False | False | yes |
| HHG-018 | flagged_channel | in_person | in_person | yes |
| HHG-018 | region_prior | ('126.0', 565) | ('126.0', 565) | yes |
| HHG-018 | cnp_documented | False | False | yes |
| HHG-018 | oor | False | False | yes |
| HHG-018 | recurrence_not_strong | True | True | yes |
| HHG-019 | flagged_channel | online | online | yes |
| HHG-019 | oor | False | False | yes |
| HHG-019 | network_corroborated | True | True | yes |
| HHG-020 | flagged_channel | online | online | yes |
| HHG-020 | is_new_device | True | True | yes |
| HHG-020 | product_class | unseen | unseen | yes |
| HHG-020 | not_decisive_alone | True | True | yes |

## Per-case detail

### HHG-001 (risk_score)

- Profile: 358 prior txns over 151.21 days; median $83.94; flagged $77.07 = 0.92x, percentile 0.46 (normal); ProductCD W 356 prior (share 0.99, established); home region 204.0 (34, share 0.10); device n/a, proxy none; risk score (context only) 0.61.
- Card testing: no qualifying sequence contains the flagged transaction
- CNP: flagged transaction channel is in_person, not online (excluded in-person rows: 8)
- Out-of-region: region 444.0 already appears 10 time(s) in this card's prior history; it is not a region the cardholder has no history in
- Recurrence: none -- no same-channel, same-ProductCD, amount-matching transaction 25-35 days earlier
- Device network: no device record (in-person or missing identity)
- Families: suspicious []; benign ['behavioral_consistency', 'geographic_consistency']; strong []; single_signal True
- Episode: flagged_only -> ['3514030'] ($77.07)
- Simulated response if verification is requested: Simulated assumption: customer confirms they made this $77.07 purchase. Rule: confirm -- every behavioral feature is consistent with the card's own history. Metrics: history 358 prior txns; amount 0.918x median $83.94 (percentile 0.4553, normal); ProductCD W established (356 prior); channel in_person; region 444.0 established.

### HHG-002 (risk_score)

- Profile: 35 prior txns over 141.94 days; median $47.29; flagged $292.36 = 6.18x, percentile 1.00 (extreme); ProductCD C 35 prior (share 1.00, established); home region None (0, share n/a); device n/a, proxy none; risk score (context only) 0.79.
- Card testing: no qualifying sequence contains the flagged transaction
- CNP: only the flagged online transaction within 48h (excluded in-person rows: 0)
- Out-of-region: flagged transaction is online; out-of-region requires card-present use
- Recurrence: none -- no same-channel, same-ProductCD, amount-matching transaction 25-35 days earlier
- Device network: no device record (in-person or missing identity)
- Families: suspicious ['behavioral_anomaly']; benign []; strong []; single_signal True
- Episode: flagged_only -> ['3478782'] ($292.36)
- Simulated response if verification is requested: Simulated assumption: customer did not reply within 24 hours. Rule: no reply -- profile is ambiguous: not fully consistent with history (normal amount) but only 1 suspicious family (behavioral_anomaly) and no strong signal. Metrics: history 35 prior txns; amount 6.182x median $47.29 (percentile 1.0, extreme); ProductCD C established (35 prior); channel online; device unknown.

### HHG-003 (customer_report)

- Profile: 985 prior txns over 156.94 days; median $67.92; flagged $49.00 = 0.72x, percentile 0.28 (normal); ProductCD W 936 prior (share 0.95, established); home region 299.0 (110, share 0.12); device n/a, proxy none; risk score (context only) 0.40.
- Card testing: no qualifying sequence contains the flagged transaction
- CNP: flagged transaction channel is in_person, not online (excluded in-person rows: 12)
- Out-of-region: region 330.0 already appears 42 time(s) in this card's prior history; it is not a region the cardholder has no history in
- Recurrence: candidate -- amount band recurs outside the monthly slots in 4.4% of same-channel/ProductCD history (41 of 936): collision-prone, not a distinct charge
- Device network: no device record (in-person or missing identity)
- Families: suspicious ['customer_statement']; benign ['behavioral_consistency', 'geographic_consistency']; strong []; single_signal True
- Episode: flagged_only -> ['3530164'] ($49.0)

### HHG-004 (customer_report)

- Profile: 209 prior txns over 175.08 days; median $27.73; flagged $128.33 = 4.63x, percentile 0.99 (extreme); ProductCD C 209 prior (share 1.00, established); home region None (0, share n/a); device New, proxy none; risk score (context only) 0.34.
- Card testing: no qualifying sequence contains the flagged transaction
- CNP: 2 online transactions inside one 48h window containing the flagged one (documented 2-4 burst); card baseline 2.40 online per 48h -> within routine volume (excluded in-person rows: 0)
- Out-of-region: flagged transaction is online; out-of-region requires card-present use
- Recurrence: none -- no same-channel, same-ProductCD, amount-matching transaction 25-35 days earlier
- Device network: 5 other card(s) used this profile, none within 48h of the flagged transaction
- Families: suspicious ['behavioral_anomaly', 'identity_anomaly', 'customer_statement']; benign []; strong ['extreme_amount_with_identity_anomaly']; single_signal False
- Episode: flagged_only -> ['3583227'] ($128.33)

### HHG-005 (risk_score)

- Profile: 90 prior txns over 158.23 days; median $117.01; flagged $100.07 = 0.85x, percentile 0.23 (normal); ProductCD R 6 prior (share 0.07, established); home region 330.0 (68, share 0.99); device New, proxy none; risk score (context only) 0.54.
- Card testing: no qualifying sequence contains the flagged transaction
- CNP: only the flagged online transaction within 48h (excluded in-person rows: 0)
- Out-of-region: flagged transaction is online; out-of-region requires card-present use
- Recurrence: none -- no same-channel, same-ProductCD, amount-matching transaction 25-35 days earlier
- Device network: generic device profile: 110 distinct cards on/before cutoff (> 20); contextual only
- Families: suspicious ['identity_anomaly']; benign ['behavioral_consistency']; strong []; single_signal True
- Episode: flagged_only -> ['3523199'] ($100.07)
- Simulated response if verification is requested: Simulated assumption: customer confirms they made this $100.07 purchase. Rule: confirm -- every behavioral feature is consistent with the card's own history. A device marked New does not block confirmation on its own. Metrics: history 90 prior txns; amount 0.855x median $117.01 (percentile 0.2333, normal); ProductCD R established (6 prior); channel online; device New.

### HHG-006 (customer_report)

- Profile: 199 prior txns over 139.18 days; median $62.93; flagged $482.12 = 7.66x, percentile 0.96 (elevated); ProductCD C 3 prior (share 0.02, established); home region 264.0 (26, share 0.13); device New, proxy none; risk score (context only) 0.25.
- Card testing: no qualifying sequence contains the flagged transaction
- CNP: 4 online transactions inside one 48h window containing the flagged one (documented 2-4 burst); card baseline 0.03 online per 48h -> above routine volume (excluded in-person rows: 3)
- Out-of-region: flagged transaction is online; out-of-region requires card-present use
- Recurrence: none -- no same-channel, same-ProductCD, amount-matching transaction 25-35 days earlier
- Device network: generic device profile: 494 distinct cards on/before cutoff (> 20); contextual only
- Families: suspicious ['temporal_pattern', 'identity_anomaly', 'customer_statement']; benign []; strong []; single_signal False
- Episode: documented_cnp_burst -> ['3476602', '3476633', '3476665', '3476682'] ($1906.07)

### HHG-007 (risk_score)

- Profile: 2432 prior txns over 154.99 days; median $77.03; flagged $111.92 = 1.45x, percentile 0.71 (normal); ProductCD W 2216 prior (share 0.91, established); home region 264.0 (2050, share 0.93); device n/a, proxy none; risk score (context only) 0.87.
- Card testing: no qualifying sequence contains the flagged transaction
- CNP: flagged transaction channel is in_person, not online (excluded in-person rows: 26)
- Out-of-region: region 264.0 already appears 2221 time(s) in this card's prior history; it is not a region the cardholder has no history in
- Recurrence: strong -- exactly one amount match (+/-$1.12) 32 days earlier, same channel and ProductCD; band collision rate 0.1% of 2216 prior
- Device network: no device record (in-person or missing identity)
- Families: suspicious []; benign ['behavioral_consistency', 'geographic_consistency']; strong []; single_signal True
- Episode: flagged_only -> ['3514948'] ($111.92)
- Simulated response if verification is requested: Simulated assumption: customer confirms they made this $111.92 purchase. Rule: confirm -- every behavioral feature is consistent with the card's own history. Metrics: history 2432 prior txns; amount 1.453x median $77.03 (percentile 0.7081, normal); ProductCD W established (2216 prior); channel in_person; region 264.0 established.

### HHG-008 (customer_report)

- Profile: 900 prior txns over 170.89 days; median $39.38; flagged $55.68 = 1.41x, percentile 0.68 (normal); ProductCD C 899 prior (share 1.00, established); home region 299.0 (1, share 1.00); device Found, proxy none; risk score (context only) 0.38.
- Card testing: no qualifying sequence contains the flagged transaction
- CNP: 14 online transactions inside one 48h window: high-volume online activity, not the documented 2-4 burst; card baseline 10.49 online per 48h -> within routine volume (excluded in-person rows: 0)
- Out-of-region: flagged transaction is online; out-of-region requires card-present use
- Recurrence: strong -- exactly one amount match (+/-$0.56) 31 days earlier, same channel and ProductCD; band collision rate 0.6% of 899 prior
- Device network: generic device profile: 125 distinct cards on/before cutoff (> 20); contextual only
- Families: suspicious []; benign ['behavioral_consistency', 'customer_confirmation']; strong []; single_signal True
- Episode: flagged_only -> ['3558054'] ($55.68)

### HHG-009 (customer_report)

- Profile: 46 prior txns over 176.65 days; median $49.97; flagged $30.02 = 0.60x, percentile 0.30 (normal); ProductCD S 39 prior (share 0.85, established); home region 441.0 (4, share 0.67); device Found, proxy none; risk score (context only) 0.28.
- Card testing: no qualifying sequence contains the flagged transaction
- CNP: only the flagged online transaction within 48h (excluded in-person rows: 0)
- Out-of-region: flagged transaction is online; out-of-region requires card-present use
- Recurrence: none -- no same-channel, same-ProductCD, amount-matching transaction 25-35 days earlier
- Device network: no device record (in-person or missing identity)
- Families: suspicious ['customer_statement']; benign ['behavioral_consistency']; strong []; single_signal True
- Episode: flagged_only -> ['3581141'] ($30.02)

### HHG-010 (risk_score)

- Profile: 33 prior txns over 148.64 days; median $68.98; flagged $1000.03 = 14.50x, percentile 1.00 (extreme); ProductCD R 9 prior (share 0.27, established); home region 469.0 (11, share 0.65); device New, proxy none; risk score (context only) 0.90.
- Card testing: no qualifying sequence contains the flagged transaction
- CNP: only the flagged online transaction within 48h (excluded in-person rows: 0)
- Out-of-region: flagged transaction is online; out-of-region requires card-present use
- Recurrence: none -- no same-channel, same-ProductCD, amount-matching transaction 25-35 days earlier
- Device network: generic device profile: 185 distinct cards on/before cutoff (> 20); contextual only
- Families: suspicious ['behavioral_anomaly', 'identity_anomaly']; benign []; strong ['extreme_amount_with_identity_anomaly']; single_signal False
- Episode: flagged_only -> ['3506725'] ($1000.03)
- Simulated response if verification is requested: Simulated assumption: customer states they did not make this $1000.03 purchase and still has the card. Rule: deny -- 2 independent suspicious families (behavioral_anomaly, identity_anomaly) including strong signal(s) extreme_amount_with_identity_anomaly. Metrics: history 33 prior txns; amount 14.497x median $68.98 (percentile 1.0, extreme); ProductCD R established (9 prior); channel online; device New.

### HHG-011 (customer_report)

- Profile: 10335 prior txns over 180.14 days; median $28.58; flagged $131.30 = 4.59x, percentile 0.97 (elevated); ProductCD C 10334 prior (share 1.00, established); home region None (0, share n/a); device New, proxy none; risk score (context only) 0.39.
- Card testing: no qualifying sequence contains the flagged transaction
- CNP: 62 online transactions inside one 48h window: high-volume online activity, not the documented 2-4 burst; card baseline 115.38 online per 48h -> within routine volume (excluded in-person rows: 0)
- Out-of-region: flagged transaction is online; out-of-region requires card-present use
- Recurrence: none -- no same-channel, same-ProductCD, amount-matching transaction 25-35 days earlier
- Device network: 2 other cards transacted on this 3-card device profile within 6h of each other at near-identical amounts
- Families: suspicious ['identity_anomaly', 'direct_network_corroboration', 'customer_statement']; benign []; strong ['direct_shared_origin']; single_signal False
- Episode: flagged_only -> ['3583368'] ($131.3)

### HHG-012 (risk_score)

- Profile: 913 prior txns over 168.60 days; median $49.08; flagged $30.91 = 0.63x, percentile 0.25 (normal); ProductCD W 852 prior (share 0.93, established); home region 325.0 (116, share 0.14); device n/a, proxy none; risk score (context only) 0.55.
- Card testing: no qualifying sequence contains the flagged transaction
- CNP: flagged transaction channel is in_person, not online (excluded in-person rows: 12)
- Out-of-region: region 494.0 already appears 21 time(s) in this card's prior history; it is not a region the cardholder has no history in
- Recurrence: candidate -- 3 matches in the 25-35 day window: coincidental, not one recurring charge
- Device network: no device record (in-person or missing identity)
- Families: suspicious []; benign ['behavioral_consistency', 'geographic_consistency']; strong []; single_signal True
- Episode: flagged_only -> ['3553342'] ($30.91)
- Simulated response if verification is requested: Simulated assumption: customer confirms they made this $30.91 purchase. Rule: confirm -- every behavioral feature is consistent with the card's own history. Metrics: history 913 prior txns; amount 0.63x median $49.08 (percentile 0.2486, normal); ProductCD W established (852 prior); channel in_person; region 494.0 established.

### HHG-013 (risk_score)

- Profile: 1439 prior txns over 159.49 days; median $59.09; flagged $35.66 = 0.60x, percentile 0.15 (normal); ProductCD C 1 prior (share 0.00, rare); home region 264.0 (1220, share 0.86); device New, proxy none; risk score (context only) 0.76.
- Card testing: no qualifying sequence contains the flagged transaction
- CNP: only the flagged online transaction within 48h (excluded in-person rows: 25)
- Out-of-region: flagged transaction is online; out-of-region requires card-present use
- Recurrence: none -- no same-channel, same-ProductCD, amount-matching transaction 25-35 days earlier
- Device network: generic device profile: 59 distinct cards on/before cutoff (> 20); contextual only
- Families: suspicious ['identity_anomaly']; benign []; strong []; single_signal True
- Episode: flagged_only -> ['3526826'] ($35.66)
- Simulated response if verification is requested: Simulated assumption: customer did not reply within 24 hours. Rule: no reply -- profile is ambiguous: not fully consistent with history (established ProductCD) but only 1 suspicious family (identity_anomaly) and no strong signal. Metrics: history 1439 prior txns; amount 0.603x median $59.09 (percentile 0.1466, normal); ProductCD C rare (1 prior); channel online; device New.

### HHG-014 (analyst_request)

- Profile: 71 prior txns over 140.87 days; median $55.92; flagged $74.96 = 1.34x, percentile 0.79 (normal); ProductCD C 1 prior (share 0.01, rare); home region 272.0 (35, share 0.51); device New, proxy IP_PROXY:ANONYMOUS; risk score (context only) 0.05.
- Card testing: no qualifying sequence contains the flagged transaction
- CNP: only the flagged online transaction within 48h (excluded in-person rows: 2)
- Out-of-region: flagged transaction is online; out-of-region requires card-present use
- Recurrence: none -- no same-channel, same-ProductCD, amount-matching transaction 25-35 days earlier
- Device network: generic device profile: 44 distinct cards on/before cutoff (> 20); contextual only
- Families: suspicious ['identity_anomaly']; benign []; strong []; single_signal True
- Episode: flagged_only -> ['3478561'] ($74.96)
- Simulated response if verification is requested: Simulated assumption: customer did not reply within 24 hours. Rule: no reply -- profile is ambiguous: not fully consistent with history (established ProductCD, no anonymizing proxy) but only 1 suspicious family (identity_anomaly) and no strong signal. Metrics: history 71 prior txns; amount 1.34x median $55.92 (percentile 0.7887, normal); ProductCD C rare (1 prior); channel online; device New.

### HHG-015 (risk_score)

- Profile: 68 prior txns over 137.69 days; median $99.96; flagged $599.94 = 6.00x, percentile 1.00 (extreme); ProductCD R 23 prior (share 0.34, established); home region 299.0 (4, share 0.33); device New, proxy none; risk score (context only) 0.77.
- Card testing: no qualifying sequence contains the flagged transaction
- CNP: only the flagged online transaction within 48h (excluded in-person rows: 3)
- Out-of-region: flagged transaction is online; out-of-region requires card-present use
- Recurrence: none -- no same-channel, same-ProductCD, amount-matching transaction 25-35 days earlier
- Device network: 5 other card(s) used this profile, none within 48h of the flagged transaction
- Families: suspicious ['behavioral_anomaly', 'identity_anomaly']; benign []; strong ['extreme_amount_with_identity_anomaly']; single_signal False
- Episode: flagged_only -> ['3464869'] ($599.94)
- Simulated response if verification is requested: Simulated assumption: customer states they did not make this $599.94 purchase and still has the card. Rule: deny -- 2 independent suspicious families (behavioral_anomaly, identity_anomaly) including strong signal(s) extreme_amount_with_identity_anomaly. Metrics: history 68 prior txns; amount 6.002x median $99.96 (percentile 1.0, extreme); ProductCD R established (23 prior); channel online; device New.

### HHG-016 (customer_report)

- Profile: 48 prior txns over 158.15 days; median $37.75; flagged $59.67 = 1.58x, percentile 0.75 (normal); ProductCD C 48 prior (share 1.00, established); home region None (0, share n/a); device New, proxy none; risk score (context only) 0.37.
- Card testing: no qualifying sequence contains the flagged transaction
- CNP: only the flagged online transaction within 48h (excluded in-person rows: 0)
- Out-of-region: flagged transaction is online; out-of-region requires card-present use
- Recurrence: none -- no same-channel, same-ProductCD, amount-matching transaction 25-35 days earlier
- Device network: generic device profile: 143 distinct cards on/before cutoff (> 20); contextual only
- Families: suspicious ['identity_anomaly', 'customer_statement']; benign ['behavioral_consistency']; strong []; single_signal False
- Episode: flagged_only -> ['3534820'] ($59.67)

### HHG-017 (risk_score)

- Profile: 52 prior txns over 117.23 days; median $199.97; flagged $100.09 = 0.50x, percentile 0.21 (normal); ProductCD R 29 prior (share 0.56, established); home region 325.0 (8, share 0.47); device Found, proxy IP_PROXY:HIDDEN; risk score (context only) 0.57.
- Card testing: no qualifying sequence contains the flagged transaction
- CNP: 3 online transactions inside one 48h window containing the flagged one (documented 2-4 burst); card baseline 0.57 online per 48h -> above routine volume (excluded in-person rows: 0)
- Out-of-region: flagged transaction is online; out-of-region requires card-present use
- Recurrence: none -- no same-channel, same-ProductCD, amount-matching transaction 25-35 days earlier
- Device network: generic device profile: 164 distinct cards on/before cutoff (> 20); contextual only
- Families: suspicious ['temporal_pattern', 'identity_anomaly']; benign ['behavioral_consistency']; strong []; single_signal False
- Episode: documented_cnp_burst -> ['3450436', '3450503', '3450629'] ($300.14)
- Simulated response if verification is requested: Simulated assumption: customer did not reply within 24 hours. Rule: no reply -- profile is ambiguous: not fully consistent with history (no anonymizing proxy) but only 2 suspicious families (temporal_pattern, identity_anomaly) and no strong signal. Metrics: history 52 prior txns; amount 0.501x median $199.97 (percentile 0.2115, normal); ProductCD R established (29 prior); channel online; device Found.

### HHG-018 (customer_report)

- Profile: 5864 prior txns over 148.54 days; median $86.92; flagged $39.08 = 0.45x, percentile 0.18 (normal); ProductCD W 5373 prior (share 0.92, established); home region 325.0 (4444, share 0.83); device n/a, proxy none; risk score (context only) 0.48.
- Card testing: no qualifying sequence contains the flagged transaction
- CNP: flagged transaction channel is in_person, not online (excluded in-person rows: 92)
- Out-of-region: region 126.0 already appears 565 time(s) in this card's prior history; it is not a region the cardholder has no history in
- Recurrence: candidate -- 9 matches in the 25-35 day window: coincidental, not one recurring charge
- Device network: no device record (in-person or missing identity)
- Families: suspicious ['customer_statement']; benign ['behavioral_consistency', 'geographic_consistency']; strong []; single_signal True
- Episode: flagged_only -> ['3491361'] ($39.08)

### HHG-019 (risk_score)

- Profile: 220 prior txns over 151.70 days; median $116.92; flagged $99.92 = 0.85x, percentile 0.40 (normal); ProductCD R 13 prior (share 0.06, established); home region 325.0 (44, share 0.27); device New, proxy none; risk score (context only) 0.90.
- Card testing: no qualifying sequence contains the flagged transaction
- CNP: only the flagged online transaction within 48h (excluded in-person rows: 1)
- Out-of-region: flagged transaction is online; out-of-region requires card-present use
- Recurrence: candidate -- only 13 prior same-channel/ProductCD transactions; not enough baseline to rule out coincidence
- Device network: 2 other cards on this 3-card device profile transacted within 48h of the flagged $99.92 at near-identical amounts: C11309-K1 $100.06 at 2016-11-29 17:45, C06224-K2 $100.00 at 2016-12-01 18:00
- Families: suspicious ['identity_anomaly', 'direct_network_corroboration']; benign ['behavioral_consistency']; strong ['direct_shared_origin']; single_signal False
- Episode: flagged_only -> ['3503878'] ($99.92)
- Simulated response if verification is requested: Simulated assumption: customer states they did not make this $99.92 purchase and still has the card. Rule: deny -- 2 independent suspicious families (identity_anomaly, direct_network_corroboration) including strong signal(s) direct_shared_origin. Metrics: history 220 prior txns; amount 0.855x median $116.92 (percentile 0.4, normal); ProductCD R established (13 prior); channel online; device New.

### HHG-020 (risk_score)

- Profile: 83 prior txns over 150.11 days; median $107.90; flagged $125.08 = 1.16x, percentile 0.59 (normal); ProductCD R 0 prior (share 0.00, unseen); home region 264.0 (79, share 1.00); device New, proxy none; risk score (context only) 0.52.
- Card testing: no qualifying sequence contains the flagged transaction
- CNP: only the flagged online transaction within 48h (excluded in-person rows: 0)
- Out-of-region: flagged transaction is online; out-of-region requires card-present use
- Recurrence: none -- no same-channel, same-ProductCD, amount-matching transaction 25-35 days earlier
- Device network: generic device profile: 232 distinct cards on/before cutoff (> 20); contextual only
- Families: suspicious ['behavioral_anomaly', 'identity_anomaly']; benign []; strong []; single_signal False
- Episode: flagged_only -> ['3509359'] ($125.08)
- Simulated response if verification is requested: Simulated assumption: customer did not reply within 24 hours. Rule: no reply -- profile is ambiguous: not fully consistent with history (established ProductCD) but only 2 suspicious families (behavioral_anomaly, identity_anomaly) and no strong signal. Metrics: history 83 prior txns; amount 1.159x median $107.9 (percentile 0.5904, normal); ProductCD R unseen (0 prior); channel online; device New.
