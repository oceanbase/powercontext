# Prepared-context budget allocation report

- Manifest: `powercontext.prepared-context-budget.synthetic.v1` (schema `powercontext.prepared-context-budget-experiment.v1`, contract `2026-11-06`)
- Manifest SHA-256: `f66d20fde6356dee53d63790dbb4f5936f8979e51f1f01ee10452cde80068078`
- Definitions SHA-256: `1892d202e9d5d9bda732912991609ecf68224ee096b48bd7269fa746bd3aa5ec`
- Arms: baseline = production Builder; candidate = evaluation-only `waterfill-v1`

## Decision

**improvement_not_established**

- Primary cases: 14
- Mean coverage delta: -0.026493 (must be strictly greater than 0.05)
- Top-item truncation loss above 0.25: `default-large-early-then-small`; `default-long-citations`; `default-mixed-families`; `default-multibyte-and-controls`; `default-short-complete-items`; `assembly-large-early-then-small`; `assembly-controls-and-line-endings`; `assembly-four-sections`
- Critical fact regressions: `default-large-early-then-small: atomic-memory/d1-m1: FACT-D1-M1-TAIL`; `assembly-large-early-then-small: atomic-memory/a1-m1: FACT-A1-M1-TAIL`
- Failed validity checks: none
- Reason: mean coverage delta -0.026493 is not greater than 0.05
- Reason: top-item truncation loss above 0.25 in 8 primary case(s)
- Reason: 2 critical fact regression(s); 0 allowed

## Cases

| Case | Role | Path | max_bytes | Baseline bytes | Candidate bytes | Baseline coverage | Candidate coverage | Delta | Top-item loss | Facts (declared / baseline / candidate) | Fact regressions |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| `default-large-early-then-small` | primary (critical) | default | 3000 | 3000 | 3000 | 0.4741 | 0.7747 | +0.3007 | 0.7811 | 9 / 4 / 8 | 1 |
| `default-all-fit-control` | primary (critical) | default | 8000 | 2094 | 2094 | 1.0000 | 1.0000 | +0.0000 | 0.0000 | 5 / 5 / 5 | 0 |
| `default-infeasible-512` | primary | default | 512 | 0 | 0 | 0.0000 | 0.0000 | +0.0000 | 0.0000 | 16 / 0 / 0 | 0 |
| `default-infeasible-640` | primary | default | 640 | 640 | 640 | 0.0347 | 0.0347 | +0.0000 | 0.0000 | 16 / 1 / 1 | 0 |
| `default-long-citations` | primary | default | 2000 | 1580 | 2000 | 0.4935 | 0.2278 | -0.2657 | 0.2909 | 6 / 2 / 3 | 0 |
| `default-mixed-families` | primary | default | 2600 | 2600 | 2600 | 0.6257 | 0.2093 | -0.4165 | 0.5500 | 10 / 5 / 8 | 0 |
| `default-multibyte-and-controls` | primary | default | 1600 | 1591 | 1600 | 0.4704 | 0.2611 | -0.2092 | 0.8744 | 5 / 2 / 5 | 0 |
| `default-short-complete-items` | primary (critical) | default | 1500 | 1500 | 1500 | 0.1132 | 0.6451 | +0.5319 | 0.7392 | 8 / 1 / 6 | 0 |
| `default-entry-cap` | primary | default | 8000 | 2340 | 2340 | 0.7763 | 0.7763 | +0.0000 | 0.0000 | 12 / 8 / 8 | 0 |
| `default-over-quota` | primary | default | 8000 | 3404 | 3404 | 0.6869 | 0.6869 | +0.0000 | 0.0000 | 4 / 3 / 3 | 0 |
| `assembly-large-early-then-small` | primary (critical) | assembly | 3000 | 2952 | 3000 | 0.5857 | 0.7802 | +0.1944 | 0.5995 | 8 / 4 / 7 | 1 |
| `assembly-controls-and-line-endings` | primary | assembly | 1400 | 1399 | 1400 | 0.4944 | 0.2939 | -0.2005 | 0.7378 | 4 / 2 / 4 | 0 |
| `assembly-infeasible-640` | primary | assembly | 640 | 640 | 640 | 0.0519 | 0.0519 | +0.0000 | 0.0000 | 8 / 1 / 1 | 0 |
| `assembly-four-sections` | primary | assembly | 2400 | 2234 | 2400 | 0.4690 | 0.1630 | -0.3060 | 0.8457 | 10 / 3 / 8 | 0 |
| `sensitivity-default-large-last` | sensitivity | default | 3000 | 3000 | 3000 | 0.9289 | 0.9289 | +0.0000 | 0.0000 | 9 / 8 / 8 | 0 |
| `sensitivity-assembly-large-last` | sensitivity | assembly | 3000 | 3000 | 3000 | 0.8390 | 0.9217 | +0.0827 | 0.0000 | 8 / 6 / 7 | 0 |

## Sensitivity

Reordering cases are preregistered, reported here and excluded from the decision.

| Case | Reorders | Reference delta | Reordered delta | Reference top-item loss | Reordered top-item loss |
| --- | --- | ---: | ---: | ---: | ---: |
| `sensitivity-default-large-last` | `default-large-early-then-small` | 0.3007 | 0.0000 | 0.7811 | 0.0000 |
| `sensitivity-assembly-large-last` | `assembly-large-early-then-small` | 0.1944 | 0.0827 | 0.5995 | 0.0000 |

## Limitations

- The experiment measures allocation coverage of fixed synthetic candidates under one renderer. It does not measure answer quality, downstream model behavior, or real recall improvement.
- A retained truncated prefix is credited at most half of a complete item; a prefix is never treated as a complete fact.
- include_code preparations are outside the comparison; code evidence keeps its own reserved share.

## Items

Bytes are UTF-8 body bytes; `display` is the complete display body, `delivered` includes the ellipsis.

### `default-large-early-then-small`

Inputs SHA-256: `6f0d29468e378811b156c926d5f75ddc1bbd30ea35001de3baecc420343c9023`

| Pos | Kind | Artifact | Rank | Display | Baseline | Delivered | Retained | Candidate | Delivered | Retained |
| ---: | --- | --- | ---: | ---: | --- | ---: | ---: | --- | ---: | ---: |
| 1 | atomic-memory | `d1-m1` | 1 | 1900 | full | 1900 | 1.000 | truncated | 419 | 0.219 |
| 2 | atomic-memory | `d1-m2` | 2 | 150 | full | 150 | 1.000 | full | 150 | 1.000 |
| 3 | atomic-memory | `d1-m3` | 3 | 150 | truncated | 149 | 0.973 | full | 150 | 1.000 |
| 4 | atomic-memory | `d1-m4` | 4 | 150 | dropped_no_fitting_truncation | 0 | 0.000 | full | 150 | 1.000 |
| 5 | atomic-memory | `d1-m5` | 5 | 150 | dropped_no_fitting_truncation | 0 | 0.000 | full | 150 | 1.000 |
| 6 | atomic-memory | `d1-m6` | 6 | 150 | dropped_no_fitting_truncation | 0 | 0.000 | full | 150 | 1.000 |
| 7 | atomic-memory | `d1-m7` | 7 | 150 | dropped_no_fitting_truncation | 0 | 0.000 | full | 150 | 1.000 |
| 8 | atomic-memory | `d1-m8` | 8 | 150 | dropped_no_fitting_truncation | 0 | 0.000 | full | 150 | 1.000 |

### `default-all-fit-control`

Inputs SHA-256: `80b8e2d7003eec3bd72f4ac1ba51ada4673a970f52a8bafdd5583c8081c15f1f`

| Pos | Kind | Artifact | Rank | Display | Baseline | Delivered | Retained | Candidate | Delivered | Retained |
| ---: | --- | --- | ---: | ---: | --- | ---: | ---: | --- | ---: | ---: |
| 1 | atomic-memory | `d2-m1` | 1 | 200 | full | 200 | 1.000 | full | 200 | 1.000 |
| 2 | atomic-memory | `d2-m2` | 2 | 200 | full | 200 | 1.000 | full | 200 | 1.000 |
| 3 | atomic-memory | `d2-m3` | 3 | 200 | full | 200 | 1.000 | full | 200 | 1.000 |
| 4 | atomic-memory | `d2-m4` | 4 | 200 | full | 200 | 1.000 | full | 200 | 1.000 |
| 5 | atomic-memory | `d2-m5` | 5 | 200 | full | 200 | 1.000 | full | 200 | 1.000 |

### `default-infeasible-512`

Inputs SHA-256: `9ac2c2b67a13b49fa754f94dae77a0762bdaf0cd12577512d427fb2620015390`

| Pos | Kind | Artifact | Rank | Display | Baseline | Delivered | Retained | Candidate | Delivered | Retained |
| ---: | --- | --- | ---: | ---: | --- | ---: | ---: | --- | ---: | ---: |
| 1 | atomic-memory | `d3-m01` | 1 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 2 | atomic-memory | `d3-m02` | 2 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 3 | atomic-memory | `d3-m03` | 3 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 4 | atomic-memory | `d3-m04` | 4 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 5 | atomic-memory | `d3-m05` | 5 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 6 | atomic-memory | `d3-m06` | 6 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 7 | atomic-memory | `d3-m07` | 7 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 8 | atomic-memory | `d3-m08` | 8 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 9 | atomic-memory | `d3-m09` | 9 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 10 | atomic-memory | `d3-m10` | 10 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 11 | atomic-memory | `d3-m11` | 11 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 12 | atomic-memory | `d3-m12` | 12 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 13 | atomic-memory | `d3-m13` | 13 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 14 | atomic-memory | `d3-m14` | 14 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 15 | atomic-memory | `d3-m15` | 15 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 16 | atomic-memory | `d3-m16` | 16 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |

### `default-infeasible-640`

Inputs SHA-256: `bfc741d8d2405b90750b35c663dba47be63c53c010203af32d9cc84dbf59c7d9`

| Pos | Kind | Artifact | Rank | Display | Baseline | Delivered | Retained | Candidate | Delivered | Retained |
| ---: | --- | --- | ---: | ---: | --- | ---: | ---: | --- | ---: | ---: |
| 1 | atomic-memory | `d4-m01` | 1 | 300 | truncated | 130 | 0.423 | truncated | 130 | 0.423 |
| 2 | atomic-memory | `d4-m02` | 2 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 3 | atomic-memory | `d4-m03` | 3 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 4 | atomic-memory | `d4-m04` | 4 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 5 | atomic-memory | `d4-m05` | 5 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 6 | atomic-memory | `d4-m06` | 6 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 7 | atomic-memory | `d4-m07` | 7 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 8 | atomic-memory | `d4-m08` | 8 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 9 | atomic-memory | `d4-m09` | 9 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 10 | atomic-memory | `d4-m10` | 10 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 11 | atomic-memory | `d4-m11` | 11 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 12 | atomic-memory | `d4-m12` | 12 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 13 | atomic-memory | `d4-m13` | 13 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 14 | atomic-memory | `d4-m14` | 14 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 15 | atomic-memory | `d4-m15` | 15 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 16 | atomic-memory | `d4-m16` | 16 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |

### `default-long-citations`

Inputs SHA-256: `6b73adc70545f34d33c9d7220fe3aca863a83abc887c29fca7539cfa07debf80`

| Pos | Kind | Artifact | Rank | Display | Baseline | Delivered | Retained | Candidate | Delivered | Retained |
| ---: | --- | --- | ---: | ---: | --- | ---: | ---: | --- | ---: | ---: |
| 1 | atomic-memory | `d5-memory-with-a-deliberately-long-artifact-identifier-0000000000000000000000000000000000001` | 1 | 220 | full | 220 | 1.000 | truncated | 159 | 0.709 |
| 2 | atomic-memory | `d5-memory-with-a-deliberately-long-artifact-identifier-0000000000000000000000000000000000002` | 2 | 220 | full | 220 | 1.000 | truncated | 158 | 0.705 |
| 3 | atomic-memory | `d5-memory-with-a-deliberately-long-artifact-identifier-0000000000000000000000000000000000003` | 3 | 220 | dropped_no_fitting_truncation | 0 | 0.000 | truncated | 158 | 0.705 |
| 4 | atomic-memory | `d5-memory-with-a-deliberately-long-artifact-identifier-0000000000000000000000000000000000004` | 4 | 220 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 5 | atomic-memory | `d5-memory-with-a-deliberately-long-artifact-identifier-0000000000000000000000000000000000005` | 5 | 220 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 6 | atomic-memory | `d5-memory-with-a-deliberately-long-artifact-identifier-0000000000000000000000000000000000006` | 6 | 220 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |

### `default-mixed-families`

Inputs SHA-256: `a71d1cd6bc1d5b08d894f9edaf07d6f97a0b9fcd03218377e04f27a70d8294d9`

| Pos | Kind | Artifact | Rank | Display | Baseline | Delivered | Retained | Candidate | Delivered | Retained |
| ---: | --- | --- | ---: | ---: | --- | ---: | ---: | --- | ---: | ---: |
| 1 | atomic-memory | `d6-m1` | 1 | 300 | full | 300 | 1.000 | truncated | 138 | 0.450 |
| 2 | topic-memory | `d6-t1` | 2 | 478 | full | 478 | 1.000 | truncated | 133 | 0.272 |
| 3 | experience | `d6-e1` | 3 | 183 | full | 183 | 1.000 | truncated | 133 | 0.710 |
| 4 | atomic-memory | `d6-m2` | 4 | 300 | full | 300 | 1.000 | truncated | 133 | 0.433 |
| 5 | topic-memory | `d6-t2` | 5 | 444 | truncated | 234 | 0.520 | truncated | 133 | 0.293 |
| 6 | experience | `d6-e2` | 6 | 173 | dropped_no_fitting_truncation | 0 | 0.000 | truncated | 133 | 0.751 |
| 7 | atomic-memory | `d6-m3` | 7 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | truncated | 133 | 0.433 |
| 8 | topic-memory | `d6-t3` | 8 | 446 | dropped_no_fitting_truncation | 0 | 0.000 | truncated | 133 | 0.291 |
| 9 | atomic-memory | `d6-m4` | 9 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | entry_limit | 0 | 0.000 |

### `default-multibyte-and-controls`

Inputs SHA-256: `b38e99d1d7eceb01cf11205bbf64c52d338166d995c1d740f165dbf884ec7103`

| Pos | Kind | Artifact | Rank | Display | Baseline | Delivered | Retained | Candidate | Delivered | Retained |
| ---: | --- | --- | ---: | ---: | --- | ---: | ---: | --- | ---: | ---: |
| 1 | atomic-memory | `d7-m1` | 1 | 900 | full | 900 | 1.000 | truncated | 116 | 0.126 |
| 2 | atomic-memory | `d7-m2` | 2 | 400 | dropped_no_fitting_truncation | 0 | 0.000 | truncated | 114 | 0.278 |
| 3 | atomic-memory | `d7-m3` | 3 | 182 | dropped_no_fitting_truncation | 0 | 0.000 | truncated | 115 | 0.615 |
| 4 | atomic-memory | `d7-m4` | 4 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | truncated | 113 | 0.367 |
| 5 | atomic-memory | `d7-m5` | 5 | 35 | full | 35 | 1.000 | full | 35 | 1.000 |

### `default-short-complete-items`

Inputs SHA-256: `dd0fe48d9faef733b9d9834797fa2030c6a8ebe21c7fe112e4bc92810383ddec`

| Pos | Kind | Artifact | Rank | Display | Baseline | Delivered | Retained | Candidate | Delivered | Retained |
| ---: | --- | --- | ---: | ---: | --- | ---: | ---: | --- | ---: | ---: |
| 1 | atomic-memory | `d8-m1` | 1 | 1200 | truncated | 991 | 0.823 | truncated | 104 | 0.084 |
| 2 | atomic-memory | `d8-m2` | 2 | 32 | dropped_below_min_bytes | 0 | 0.000 | full | 32 | 1.000 |
| 3 | atomic-memory | `d8-m3` | 3 | 30 | dropped_below_min_bytes | 0 | 0.000 | full | 30 | 1.000 |
| 4 | atomic-memory | `d8-m4` | 4 | 29 | dropped_below_min_bytes | 0 | 0.000 | full | 29 | 1.000 |
| 5 | atomic-memory | `d8-m5` | 5 | 34 | dropped_below_min_bytes | 0 | 0.000 | full | 34 | 1.000 |
| 6 | atomic-memory | `d8-m6` | 6 | 32 | dropped_below_min_bytes | 0 | 0.000 | full | 32 | 1.000 |
| 7 | atomic-memory | `d8-m7` | 7 | 29 | dropped_below_min_bytes | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |

### `default-entry-cap`

Inputs SHA-256: `42daf2ec2d928a9aa8d4debd54778066541d44ab0dbe4bb91476f5551a6da95b`

| Pos | Kind | Artifact | Rank | Display | Baseline | Delivered | Retained | Candidate | Delivered | Retained |
| ---: | --- | --- | ---: | ---: | --- | ---: | ---: | --- | ---: | ---: |
| 1 | atomic-memory | `d9-m01` | 1 | 100 | full | 100 | 1.000 | full | 100 | 1.000 |
| 2 | atomic-memory | `d9-m02` | 2 | 100 | full | 100 | 1.000 | full | 100 | 1.000 |
| 3 | atomic-memory | `d9-m03` | 3 | 100 | full | 100 | 1.000 | full | 100 | 1.000 |
| 4 | atomic-memory | `d9-m04` | 4 | 100 | full | 100 | 1.000 | full | 100 | 1.000 |
| 5 | atomic-memory | `d9-m05` | 5 | 100 | full | 100 | 1.000 | full | 100 | 1.000 |
| 6 | atomic-memory | `d9-m06` | 6 | 100 | full | 100 | 1.000 | full | 100 | 1.000 |
| 7 | atomic-memory | `d9-m07` | 7 | 100 | full | 100 | 1.000 | full | 100 | 1.000 |
| 8 | atomic-memory | `d9-m08` | 8 | 100 | full | 100 | 1.000 | full | 100 | 1.000 |
| 9 | atomic-memory | `d9-m09` | 9 | 100 | entry_limit | 0 | 0.000 | entry_limit | 0 | 0.000 |
| 10 | atomic-memory | `d9-m10` | 10 | 100 | entry_limit | 0 | 0.000 | entry_limit | 0 | 0.000 |
| 11 | atomic-memory | `d9-m11` | 11 | 100 | entry_limit | 0 | 0.000 | entry_limit | 0 | 0.000 |
| 12 | atomic-memory | `d9-m12` | 12 | 100 | entry_limit | 0 | 0.000 | entry_limit | 0 | 0.000 |

### `default-over-quota`

Inputs SHA-256: `7898b82f155accc150e507ba8eab48a1a1aa34681521dd71e201af5fa0f6bc68`

| Pos | Kind | Artifact | Rank | Display | Baseline | Delivered | Retained | Candidate | Delivered | Retained |
| ---: | --- | --- | ---: | ---: | --- | ---: | ---: | --- | ---: | ---: |
| 1 | atomic-memory | `d10-m1` | 1 | 3000 | truncated | 2000 | 0.666 | truncated | 2000 | 0.666 |
| 2 | atomic-memory | `d10-m2` | 2 | 300 | full | 300 | 1.000 | full | 300 | 1.000 |
| 3 | atomic-memory | `d10-m3` | 3 | 300 | full | 300 | 1.000 | full | 300 | 1.000 |

### `assembly-large-early-then-small`

Inputs SHA-256: `bc7766a4de352fc6d6a3f704a95c5b1cd9239555f210a4767746d4bfb9b03933`

| Pos | Kind | Artifact | Rank | Display | Baseline | Delivered | Retained | Candidate | Delivered | Retained |
| ---: | --- | --- | ---: | ---: | --- | ---: | ---: | --- | ---: | ---: |
| 1 | atomic-memory | `a1-m1` | 1 | 1900 | full | 1900 | 1.000 | truncated | 764 | 0.401 |
| 2 | atomic-memory | `a1-m2` | 2 | 150 | full | 150 | 1.000 | full | 150 | 1.000 |
| 3 | atomic-memory | `a1-m3` | 3 | 150 | full | 150 | 1.000 | full | 150 | 1.000 |
| 4 | atomic-memory | `a1-m4` | 4 | 150 | dropped_no_fitting_truncation | 0 | 0.000 | full | 150 | 1.000 |
| 5 | atomic-memory | `a1-m5` | 5 | 150 | dropped_no_fitting_truncation | 0 | 0.000 | full | 150 | 1.000 |
| 6 | atomic-memory | `a1-m6` | 6 | 150 | dropped_no_fitting_truncation | 0 | 0.000 | full | 150 | 1.000 |
| 7 | experience | `a1-e1` | 1 | 183 | dropped_no_fitting_truncation | 0 | 0.000 | full | 183 | 1.000 |

### `assembly-controls-and-line-endings`

Inputs SHA-256: `391945926fe015cfee954ef91b0cec6ebd2e50564af31d86f54008fa3af2bbd1`

| Pos | Kind | Artifact | Rank | Display | Baseline | Delivered | Retained | Candidate | Delivered | Retained |
| ---: | --- | --- | ---: | ---: | --- | ---: | ---: | --- | ---: | ---: |
| 1 | atomic-memory | `a2-m1` | 1 | 267 | full | 267 | 1.000 | truncated | 73 | 0.262 |
| 2 | atomic-memory | `a2-m2` | 2 | 432 | truncated | 368 | 0.845 | truncated | 71 | 0.157 |
| 3 | atomic-memory | `a2-m3` | 3 | 122 | dropped_no_fitting_truncation | 0 | 0.000 | truncated | 72 | 0.566 |
| 4 | atomic-memory | `a2-m4` | 4 | 26 | dropped_no_fitting_truncation | 0 | 0.000 | full | 26 | 1.000 |

### `assembly-infeasible-640`

Inputs SHA-256: `7960458af30d40aff0c152b08205fd1ed19094e940756936be4a17580048e76d`

| Pos | Kind | Artifact | Rank | Display | Baseline | Delivered | Retained | Candidate | Delivered | Retained |
| ---: | --- | --- | ---: | ---: | --- | ---: | ---: | --- | ---: | ---: |
| 1 | atomic-memory | `a3-m1` | 1 | 300 | truncated | 126 | 0.410 | truncated | 126 | 0.410 |
| 2 | atomic-memory | `a3-m2` | 2 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 3 | atomic-memory | `a3-m3` | 3 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 4 | atomic-memory | `a3-m4` | 4 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 5 | atomic-memory | `a3-m5` | 5 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 6 | atomic-memory | `a3-m6` | 6 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 7 | atomic-memory | `a3-m7` | 7 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |
| 8 | atomic-memory | `a3-m8` | 8 | 300 | dropped_no_fitting_truncation | 0 | 0.000 | dropped_minimum_infeasible | 0 | 0.000 |

### `assembly-four-sections`

Inputs SHA-256: `e996105f32ead0a09138d8182dde67e23252c8dca26a009fc37fb038f953a345`

| Pos | Kind | Artifact | Rank | Display | Baseline | Delivered | Retained | Candidate | Delivered | Retained |
| ---: | --- | --- | ---: | ---: | --- | ---: | ---: | --- | ---: | ---: |
| 1 | profile | `a4-profile` | 1 | 700 | full | 700 | 1.000 | truncated | 111 | 0.154 |
| 2 | topic-memory | `a4-t1` | 1 | 287 | full | 287 | 1.000 | truncated | 108 | 0.366 |
| 3 | topic-memory | `a4-t2` | 2 | 287 | full | 287 | 1.000 | truncated | 108 | 0.366 |
| 4 | topic-memory | `a4-t3` | 3 | 289 | family_limit | 0 | 0.000 | family_limit | 0 | 0.000 |
| 5 | atomic-memory | `a4-m1` | 1 | 200 | dropped_below_min_bytes | 0 | 0.000 | truncated | 108 | 0.525 |
| 6 | atomic-memory | `a4-m2` | 2 | 200 | dropped_below_min_bytes | 0 | 0.000 | truncated | 108 | 0.525 |
| 7 | atomic-memory | `a4-m3` | 3 | 200 | dropped_below_min_bytes | 0 | 0.000 | truncated | 108 | 0.525 |
| 8 | atomic-memory | `a4-m4` | 4 | 200 | dropped_below_min_bytes | 0 | 0.000 | family_limit | 0 | 0.000 |
| 9 | experience | `a4-e1` | 1 | 183 | dropped_below_min_bytes | 0 | 0.000 | truncated | 108 | 0.574 |
| 10 | experience | `a4-e2` | 2 | 173 | dropped_below_min_bytes | 0 | 0.000 | truncated | 108 | 0.607 |

### `sensitivity-default-large-last`

Inputs SHA-256: `c07ebcd45331d109c14dea3742a00af25cbcd18da56dc80847ad5fa589ac76c0`

| Pos | Kind | Artifact | Rank | Display | Baseline | Delivered | Retained | Candidate | Delivered | Retained |
| ---: | --- | --- | ---: | ---: | --- | ---: | ---: | --- | ---: | ---: |
| 1 | atomic-memory | `d1-m2` | 1 | 150 | full | 150 | 1.000 | full | 150 | 1.000 |
| 2 | atomic-memory | `d1-m3` | 2 | 150 | full | 150 | 1.000 | full | 150 | 1.000 |
| 3 | atomic-memory | `d1-m4` | 3 | 150 | full | 150 | 1.000 | full | 150 | 1.000 |
| 4 | atomic-memory | `d1-m5` | 4 | 150 | full | 150 | 1.000 | full | 150 | 1.000 |
| 5 | atomic-memory | `d1-m6` | 5 | 150 | full | 150 | 1.000 | full | 150 | 1.000 |
| 6 | atomic-memory | `d1-m7` | 6 | 150 | full | 150 | 1.000 | full | 150 | 1.000 |
| 7 | atomic-memory | `d1-m8` | 7 | 150 | full | 150 | 1.000 | full | 150 | 1.000 |
| 8 | atomic-memory | `d1-m1` | 8 | 1900 | truncated | 419 | 0.219 | truncated | 419 | 0.219 |

### `sensitivity-assembly-large-last`

Inputs SHA-256: `bfb0fc5f1aad7c0558a227dd8cee2319fef9a9100de3c9a5ae95f3108d7a8e3d`

| Pos | Kind | Artifact | Rank | Display | Baseline | Delivered | Retained | Candidate | Delivered | Retained |
| ---: | --- | --- | ---: | ---: | --- | ---: | ---: | --- | ---: | ---: |
| 1 | atomic-memory | `a1-m2` | 1 | 150 | full | 150 | 1.000 | full | 150 | 1.000 |
| 2 | atomic-memory | `a1-m3` | 2 | 150 | full | 150 | 1.000 | full | 150 | 1.000 |
| 3 | atomic-memory | `a1-m4` | 3 | 150 | full | 150 | 1.000 | full | 150 | 1.000 |
| 4 | atomic-memory | `a1-m5` | 4 | 150 | full | 150 | 1.000 | full | 150 | 1.000 |
| 5 | atomic-memory | `a1-m6` | 5 | 150 | full | 150 | 1.000 | full | 150 | 1.000 |
| 6 | atomic-memory | `a1-m1` | 6 | 1900 | truncated | 1110 | 0.583 | truncated | 764 | 0.401 |
| 7 | experience | `a1-e1` | 1 | 183 | dropped_no_fitting_truncation | 0 | 0.000 | full | 183 | 1.000 |
