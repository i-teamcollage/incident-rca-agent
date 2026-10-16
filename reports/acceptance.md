# Incident RCA agent: acceptance test

| Criterion | Target | Measured | 95% interval | Result |
|---|---|---|---|---|
| Root cause correct (right cause and right pipeline) | ≥ 75% | 84.0% | 65.3–93.6% | PASS |
| RCAs posted as confident that are correct | ≥ 90% | 100.0% | 79.6–100.0% | PASS |
| Claims traced to a real source | ≥ 85% | 85.8% | 78.2–91.1% | PASS |

25 made-up test cases, ministral-3:8b on Intel Xeon Processor @ 2.80GHz, 4 cores, 16 GB, recorded 2026-10-16, median 459.6 s per item.

All criteria passed.

## Informational, no threshold

- Posted as confident: 15 of 25 (60.0%)
- Tool calls per incident: median 4, mean 4.7, max 8 (the first call, get_alert, is made by the code)
- Minutes per incident: median 7.7, max 15.7, from the recorded run on the machine named above, without a graphics card (the client build's 30 to 60 seconds were measured on server hardware)
- Claims grounded: 97 of 113
- RCAs that could not be read, even after a retry: 0

## Per failure type

| Failure type | Incidents | Root cause correct |
|---|---|---|
| API rate limit (HTTP 429) | 3 | 3 |
| bad config deploy | 2 | 2 |
| daylight-saving bug | 3 | 2 |
| disk full | 3 | 3 |
| DNS failure | 2 | 2 |
| duplicate keys | 3 | 2 |
| expired credentials | 2 | 2 |
| late upstream data | 2 | 0 |
| out of memory (exit 137) | 2 | 2 |
| upstream schema change | 3 | 3 |

## Per incident

| Incident | Answer key | Agent | Result | Posted as | Claims grounded | Tool calls |
|---|---|---|---|---|---|---|
| t-001 | bad config deploy in fct_orders | bad config deploy in fct_orders | correct | confident | 4 of 4 | 4 |
| t-002 | daylight-saving bug in ing_events | duplicate keys in src_events_stream | wrong | needs an engineer | 3 of 4 | 6 |
| t-003 | daylight-saving bug in fct_sessions | daylight-saving bug in fct_sessions | correct | confident | 5 of 5 | 4 |
| t-004 | expired credentials in src_partner_sftp | expired credentials in src_partner_sftp | correct | confident | 4 of 4 | 4 |
| t-005 | disk full in ing_shipments | disk full in ing_shipments | correct | confident | 5 of 5 | 4 |
| t-006 | upstream schema change in ing_ad_spend | upstream schema change in ing_ad_spend | correct | confident | 5 of 5 | 4 |
| t-007 | upstream schema change in ing_ad_spend | upstream schema change in ing_ad_spend | correct | needs an engineer | 1 of 4 | 4 |
| t-008 | late upstream data in src_shop_db | DNS failure in stg_payments | wrong | needs an engineer | 4 of 4 | 6 |
| t-009 | upstream schema change in ing_shipments | upstream schema change in ing_shipments | correct | confident | 5 of 5 | 4 |
| t-010 | duplicate keys in ing_events | duplicate keys in stg_sessions | wrong | needs an engineer | 3 of 4 | 4 |
| t-011 | late upstream data in src_events_stream | late upstream data in fct_orders | wrong | needs an engineer | 5 of 5 | 7 |
| t-012 | duplicate keys in stg_customers | duplicate keys in stg_customers | correct | needs an engineer | 3 of 4 | 8 |
| t-013 | duplicate keys in ing_events | duplicate keys in ing_events | correct | confident | 5 of 5 | 6 |
| t-014 | API rate limit (HTTP 429) in src_ads_api | API rate limit (HTTP 429) in src_ads_api | correct | needs an engineer | 3 of 5 | 4 |
| t-015 | out of memory (exit 137) in fct_orders | out of memory (exit 137) in fct_orders | correct | confident | 4 of 5 | 4 |
| t-016 | disk full in fct_sessions | disk full in fct_sessions | correct | confident | 5 of 5 | 4 |
| t-017 | expired credentials in src_partner_sftp | expired credentials in src_partner_sftp | correct | confident | 5 of 5 | 4 |
| t-018 | out of memory (exit 137) in mart_customer_ltv | out of memory (exit 137) in mart_customer_ltv | correct | confident | 4 of 5 | 4 |
| t-019 | API rate limit (HTTP 429) in src_ads_api | API rate limit (HTTP 429) in src_ads_api | correct | confident | 4 of 4 | 4 |
| t-020 | bad config deploy in ing_payments | bad config deploy in ing_payments | correct | confident | 4 of 4 | 4 |
| t-021 | DNS failure in src_payments_api | DNS failure in src_payments_api | correct | needs an engineer | 2 of 5 | 6 |
| t-022 | daylight-saving bug in ing_events | daylight-saving bug in ing_events | correct | confident | 4 of 4 | 4 |
| t-023 | DNS failure in exp_crm_segments | DNS failure in exp_crm_segments | correct | needs an engineer | 2 of 4 | 6 |
| t-024 | API rate limit (HTTP 429) in src_payments_api | API rate limit (HTTP 429) in src_payments_api | correct | needs an engineer | 3 of 4 | 4 |
| t-025 | disk full in stg_sessions | disk full in stg_sessions | correct | confident | 5 of 5 | 4 |

## Incidents with a red herring

- t-001 (misleading metric): correct, confident
- t-003 (deploy on alert pipeline): correct, confident
- t-005 (deploy on alert pipeline): correct, confident
- t-006 (misleading metric): correct, confident
- t-007 (deploy on alert pipeline): correct, needs an engineer
- t-008 (failure in other pipeline): wrong, needs an engineer
- t-010 (failure in other pipeline): wrong, needs an engineer
- t-013 (failure in other pipeline): correct, confident
- t-014 (misleading metric): correct, needs an engineer
