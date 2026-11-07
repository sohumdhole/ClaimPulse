# Methodology

Every figure on this page is rendered from `outputs/audit/audit.json`. Each audit entry names the step and the artefact that produced it.

## 1. Data and provenance

| Log | File | Cases | Events | MD5 matches 4TU | Counts match published |
|---|---|---|---|---|---|
| Domestic Declarations (DD) | `DomesticDeclarations.xes.gz` | 10,500 | 56,437 | yes | yes |
| International Declarations (ID) | `InternationalDeclarations.xes.gz` | 6,449 | 72,151 | yes | yes |
| Request For Payment (RFP) | `RequestForPayment.xes.gz` | 6,886 | 36,796 | yes | yes |

`outputs/audit/manifest.json` records byte size, MD5, SHA-256, DOI, landing page and licence (CC BY-NC 4.0) for each file. The ingest step refuses to run if an MD5 differs from the published value.

Observed attributes:
- **Event level:** activity, timestamp, resource and role. Resources are anonymised to `STAFF MEMBER` or `SYSTEM`.
- **Domestic trace level:** amount, budget number and declaration number.
- **International trace level:** the linked travel permit with its organisational unit, project and requested budget, plus trip start and end events.
- **Request-for-payment trace level:** organisational unit, project, cost type and requested amount.

## 2. Warehouse layers and data quality

- **Bronze**: a lossless long-format copy of every trace attribute and event (23,835 cases, 165,384 events).
- **Silver**: timestamps parsed to UTC (local time kept as Europe/Amsterdam), activities decomposed into document type, action and acting role, data-quality rules applied (23,554 cases, 164,714 events).
- **Gold**: `gold.claim`, one row per claim (23,554 claims), and `gold.transition`, one row per hand-off between consecutive claim or payment steps (107,656 hand-offs).

| Rule | Action | Check | Hits |
|---|---|---|---|
| DQ01 | Quarantine event | Timestamp cannot be parsed | 0 |
| DQ02 | Quarantine event | Duplicate event id within the same case | 0 |
| DQ03 | Quarantine event | Exact duplicate of case, activity and timestamp | 15 |
| DQ04 | Quarantine case | No claim submission by the employee | 281 |
| DQ05 | Quarantine case | Amount missing or negative | 0 |
| DQ06 | Quarantine case | Payment handled before first submission | 0 |
| DQ07 | Flag | Acting role recorded as MISSING | 300 |
| DQ08 | Flag | Timestamps out of order within the trace | 0 |
| DQ09 | Flag | Event dated after the log's claim-processing horizon (planned trip) | 4 |
| DQ10 | Flag | Payment requested but never handled | 4 |
| DQ11 | Flag | Amount equal to zero | 513 |
| DQ12 | Flag | Permit-level events shared with another declaration of the same permit | 1,068 |

A design note on DQ02. My first version checked event ids across the whole log and flagged thousands of duplicates. Inspection showed these were permit, trip and reminder events, which the publisher copies into every declaration linked to the same travel permit. They are legitimate, so the rule was rescoped to duplicates within a case, and permit sharing is reported as the DQ12 flag instead.

Right-censoring guard: claims submitted within 90 days of their log's last processing event would be excluded from outcome KPIs. In this extraction no claim falls in that window (0 claims). All claims were submitted between January 2017 and December 2018, and the logs run until 17 June 2019 (DD), 20 August 2019 (ID) and 08 August 2019 (RFP).

## 3. Definitions

Full KPI dictionary: [reporting_requirements.md](reporting_requirements.md). The core definitions:

- **Submission**: `SUBMITTED by EMPLOYEE` on the claim document. The first submission starts the clock.
- **Organisational rejection**: `REJECTED by` any reviewing role (administration, pre-approver, budget owner, supervisor, director, or MISSING). `REJECTED by EMPLOYEE` is the employee closing or withdrawing after a rejection, so it is not counted as an organisational rejection.
- **Outcome**: PAID if `Payment Handled` exists; REJECTED_CLOSED if not paid and the last claim event is a rejection; otherwise OPEN.
- **Cycle time**: calendar days from first submission to the last `Payment Handled`.
- **Analytical SLA**: a paid claim breaches when its cycle exceeds 14 days. The data contains no TU/e service level, so this is my threshold. 79.9% of paid domestic claims meet it, and `outputs/kpis/sla_sensitivity.csv` shows breach rates at 7, 14, 21 and 30 days.
- **First time right**: paid, never rejected and never resubmitted.
- **Problem claim** (model target): rejected at least once, OR not paid, OR paid after the SLA.

## 4. Operational KPIs

| KPI | DD | ID | RFP | All |
|---|---|---|---|---|
| Claims | 10,365 | 6,375 | 6,814 | 23,554 |
| Paid | 96.9% | 97.1% | 92.6% | 95.7% |
| Rejected at least once | 12.6% | 24.7% | 15.7% | 16.7% |
| Closed as rejected | 3.1% | 2.9% | 7.2% | 4.2% |
| Resubmitted | 9.8% | 22.0% | 9.0% | 12.9% |
| First time right | 87.4% | 75.3% | 84.2% | 83.2% |
| Median cycle (days) | 7.4 | 10.2 | 9.0 | 8.8 |
| P90 cycle (days) | 20.2 | 27.3 | 22.1 | 23.0 |
| Median submission to final approval (days) | 1.6 | 3.6 | 2.1 | 2.0 |
| Median final approval to payment (days) | 5.3 | 5.4 | 5.9 | 5.4 |
| SLA breach (paid claims) | 20.1% | 34.3% | 25.7% | 25.6% |

**Scope change.** 2017 covers two departments and 2018 the whole university:
- DD volume moves from 2,205 claims to 8,160.
- DD SLA breach moves from 16.6% to 21.0%.
- ID SLA breach moves from 25.7% to 36.6%.

These are reported per year in `outputs/kpis/kpi_by_year.csv`.

Effect of extra steps on the median cycle of paid claims:
- **Budget-owner approval:** 11.4 days with it, 7.3 without.
- **Director approval:** 13.5 days with it, 8.4 without.
- **A rejection:** 15.1 days with one, 8.0 without.

These are descriptive differences, not causal effects.

Rejections by role: 4,566 organisational rejection events in total, of which Administration raised 73.1% (`outputs/kpis/rejections_by_role.csv`).

## 5. Process mining

**Event log.** Claim and payment events per claim. Permit and trip events are used only by the compliance rules.

**Variants.**

| | DD | ID | RFP |
|---|---|---|---|
| Distinct variants | 97 | 115 | 88 |
| Share of claims in the top variant | 44.6% | 36.3% | 44.2% |
| Share in the top five variants | 90.7% | 80.6% | 88.6% |
| Variants needed to cover nine in ten claims | 5 | 9 | 6 |
| Variants seen only once | 45 | 54 | 41 |

Top domestic variant: `SUBMITTED by EMPLOYEE > APPROVED by ADMINISTRATION > FINAL_APPROVED by SUPERVISOR > Request Payment > Payment Handled`.

**Discovery.** I ran the inductive miner with a noise threshold on each log and measured it by token replay:

| | DD | ID | RFP |
|---|---|---|---|
| Fitness | 0.968 | 0.972 | 0.973 |
| Precision | 0.336 | 0.429 | 0.387 |

The models replay the log well, but precision is low: they allow behaviour that never occurs. I use them to visualise the flow (`outputs/figures/pm_discovered_*.png`), not as a normative model.

**Bottlenecks.** Waiting time is measured on each hand-off between consecutive steps (`outputs/mining/transition_waits.csv`):
- The largest single hand-off is Request Payment to Payment Handled for Domestic declarations, at 12.1% of all waiting time.
- 51.8% of all waiting time falls after final approval: final approval to Request Payment, plus Request Payment to Payment Handled.
- Median Request Payment to Payment Handled is 3.2 days for DD, 3.2 for ID and 3.2 for RFP.
- 99.6% of 22,537 payments land on Thursdays and Mondays, and 98.7% at 17:00 local time, which shows a batch payment run.

**Conformance.** The reference model encodes the intended process from the dataset description: submission, then travel administration approval, then an optional budget-owner approval (skipped when budget owner and supervisor are the same person), then either a supervisor final approval or a supervisor approval followed by a director final approval, then Request Payment and Payment Handled (`outputs/figures/pm_reference_model.png`). Alignment-based conformance gives, per claim, whether it fits exactly and its alignment fitness:

| | DD | ID | RFP | All |
|---|---|---|---|---|
| Claims fitting exactly | 68.4% | 57.5% | 70.4% | 66.0% |
| Mean alignment fitness | 0.926 | 0.893 | 0.904 | |

**Business compliance rules** explain the deviations in plain terms (`outputs/mining/compliance_rules.csv`):

| Rule | DD | ID | RFP |
|---|---|---|---|
| R1 paid without a final approval | 1 | 0 | 1 |
| R2 paid with no administration or pre-approver check | 1,463 (14.1%) | 745 (11.7%) | 625 (9.2%) |
| R2b paid with pre-approver instead of administration | 659 (6.4%) | 544 (8.5%) | 376 (5.5%) |
| R3 administration check after final approval | 3 | 1 | 0 |
| R4 payment requested before final approval | 0 | 0 | 0 |
| R5 no approved travel permit before submission | not applicable | 438 (6.9%) | not applicable |
| R6 permit approved after the trip started | not applicable | 1,115 (17.5%) | not applicable |
| R7 declared more than 60 days after the trip | not applicable | 321 (5.0%) | not applicable |
| R8 resubmitted at least once | 1,019 (9.8%) | 1,405 (22.0%) | 611 (9.0%) |

R5 counts international claims whose permit has no final approval anywhere in the trace. The R2 rule shows that the published "administration first" description is not universal: many claims go straight to a supervisor. A process owner should confirm whether that path is policy or a gap.

## 6. Early-warning model

**Question.** At the moment a claim is first submitted, how likely is it to become a problem claim?

**Features.** 21 features, each known at first submission: Domestic declaration; International declaration; Request for payment; Claim amount (log); Zero amount; Submission hour; Submission weekday; Submission month; Submission day of month; Claims submitted in prior 7 days (same log); Org unit: claims closed in prior 180 days (log); Org unit: problem rate, prior 180 days; Budget: claims closed in prior 180 days (log); Budget: problem rate, prior 180 days; Days from trip end to submission; Trip length (days); Permit final-approved before submission; Permit approved after trip start; Permit rejections before submission; Amount / permit requested budget; Cost type. Org-unit and budget history are point-in-time:
- They use only claims that were already closed before this claim's submission.
- They look back over a rolling 180-day window.
- They are shrunk toward the training base rate with a pseudo-count of 20.

Permit features use only permit events dated before submission.

**Split.** Temporal, by first submission:

| Set | Period | Claims | Problem rate |
|---|---|---|---|
| Train | before 01 April 2018 | 8,830 | 27.6% |
| Validation | 01 April 2018 to 01 July 2018 | 5,081 | 33.6% |
| Test (unseen) | from 01 July 2018 | 9,643 | 40.8% |

**Model selection.**
- LightGBM over a fixed grid of 27 configurations, selected on validation ROC AUC (0.653).
- Selected configuration: 7 leaves, minimum 20 claims per leaf, 100 trees.
- Isotonic calibration fitted on validation.
- Training is deterministic: fixed seeds, single thread, and a fully ordered feature query.

**Test results** (`outputs/model/model_metrics.json`):

| Metric | Value |
|---|---|
| ROC AUC (95.0% bootstrap CI, 1,000 resamples) | 0.694 (0.683 to 0.704) |
| Baseline: log type only | 0.594 |
| Baseline: logistic regression, same features | 0.641 |
| PR AUC (base rate 40.8%) | 0.634 |
| Brier score, calibrated model / constant base rate | 0.222 / 0.259 |
| Problem rate in the riskiest tenth / lift | 77.5% / 1.90 |
| ROC AUC by log: DD / ID / RFP | 0.707 / 0.628 / 0.629 |
| Same score against: rejection / not paid / SLA breach among paid | 0.671 / 0.751 / 0.678 |

**Explanation.** SHAP values from a tree explainer on the test set (`outputs/model/shap_importance.csv`). The top drivers are: Claim amount (log); Submission month; Org unit: claims closed in prior 180 days (log). Each scored claim carries its top positive drivers in `outputs/model/rag_monitor_claims.csv`.

**RAG view.** The cut-offs are fixed on the validation period: RED is the riskiest 10.0% and AMBER the next 20.0%, using raw score cut-offs of 0.415 and 0.317. On the test period:

| Band | Claims | Share | Observed problem rate | Share of all problems |
|---|---|---|---|---|
| RED | 881 | 9.1% | 77.3% | 17.3% |
| AMBER | 1,934 | 20.1% | 56.7% | 27.9% |
| GREEN | 6,828 | 70.8% | 31.6% | 54.8% |

**Honest assessment.** The model is a moderate triage signal. It clearly beats the naive baseline, and RED claims are far more likely to go wrong than GREEN ones. But the problem rate rises from 27.6% in training to 40.8% in test, so:
- calibrated probabilities under-predict in test (`outputs/model/calibration_test.csv`);
- band shares drift from their design targets.

It should prioritise review effort, never auto-reject. It would need recalibration on recent data before live use.

**Development disclosure.** The test period was looked at more than once while building the model, and the changes made were:
1. An early version used *cumulative* org-unit history counts. These grow mechanically over time, so test claims sat outside the training range, and the test calibration table exposed it. I replaced them with a rolling window.
2. Early stopping chose very few trees, and the coarse scores created ties at the RAG cut-offs. I replaced it with the fixed grid above and set cut-offs on the raw score.
3. Training was made deterministic.

The protocol was then locked, run once, and the result reported as is. Because the test set influenced these design choices, the test metrics are slightly optimistic.

## 7. Reproducibility

`PYTHONPATH=src python -m claimpulse.pipeline` rebuilds everything from the three raw files, and `pytest` checks:
- data contracts on the gold layer;
- independent recomputation of headline KPIs from the published CSVs;
- that every document matches a fresh render from the audit record;
- that template text contains no free-standing numbers;
- a mutation check that a changed audit value is detected;
- the absence of em and en dashes;
- that no raw data is in the repository.
