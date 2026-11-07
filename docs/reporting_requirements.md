# Reporting requirements and KPI dictionary

This is written as a business reporting requirements document for a claims operations audience. It describes what the reporting must answer, for whom, at what grain and how often, and it defines every KPI exactly as the code computes it. Current values are rendered from `outputs/audit/audit.json`.

## 1. Purpose and audience

| Audience | Question the reporting must answer | Product |
|---|---|---|
| Operations lead | Are claims flowing within service level, and where is work waiting? | Excel Dashboard, Power BI Overview and Bottlenecks pages |
| Team leads and reviewers | Which claims submitted today are likely to go wrong and need attention first? | RAG monitor (Excel RAG_Monitor, Power BI Early Warning page) |
| Process owner | Does the process run as designed? Which deviations are systemic? | Power BI Conformance page, `outputs/mining/compliance_rules.csv` |
| Finance and payments | How much of cycle time is the payment run, and what would a change in run frequency affect? | Power BI Bottlenecks page, payment batch profile |
| Unit managers | How does my unit compare on rejections, rework and SLA? | Excel Org_Scorecard, Power BI Units page |

## 2. Scope and grain

- **Claims in scope**: Domestic Declarations, International Declarations and Requests for Payment. Travel permits and prepaid travel costs are out of scope as claims, but permit events feed the international compliance rules.
- **Grain**: one row per claim (`fact_claim`), one row per hand-off between consecutive claim steps (`fact_transition`).
- **Time attribution**: a claim belongs to the month of its first submission, in Europe/Amsterdam local time.
- **Exclusions**: quarantined cases (281, no employee submission) and right-censored claims (0 in this extraction).
- **Refresh**: the pipeline is a full rebuild. In production this would run daily after the event extract, with the RAG scores refreshed whenever claims are submitted.

## 3. KPI dictionary

| KPI | Definition | Numerator | Denominator | Unit | Direction | Current value (all logs) | Source |
|---|---|---|---|---|---|---|---|
| Claims | Claims first submitted in the period | count of claims | | count | context | 23,554 | `gold.claim` |
| Paid rate | Claims with a Payment Handled event | paid claims | claims | % | higher is better | 95.7% | `kpi_by_log.csv` |
| Rejection rate | Claims rejected at least once by a reviewing role (not the employee) | claims with an organisational rejection | claims | % | lower is better | 16.7% | `kpi_by_log.csv` |
| Closed as rejected | Not paid and last claim event is a rejection | rejected-closed claims | claims | % | lower is better | 4.2% | `kpi_by_log.csv` |
| Resubmission rate (rework) | Claims submitted more than once | claims with more than one submission | claims | % | lower is better | 12.9% | `kpi_by_log.csv` |
| First time right | Paid, never rejected, never resubmitted | first-time-right claims | claims | % | higher is better | 83.2% | `kpi_by_log.csv` |
| Median cycle time | First submission to last Payment Handled, paid claims | | | days | lower is better | 8.8 | `kpi_by_log.csv` |
| P90 cycle time | Ninetieth percentile of the same | | | days | lower is better | 23.0 | `kpi_by_log.csv` |
| Approval time | First submission to first final approval, median | | | days | lower is better | 2.0 | `kpi_by_log.csv` |
| Payment time | First final approval to last Payment Handled, median | | | days | lower is better | 5.4 | `kpi_by_log.csv` |
| SLA breach rate | Paid claims whose cycle exceeds the SLA (14 days, analytical) | paid claims over SLA | paid claims | % | lower is better | 25.6% | `kpi_by_log.csv` |
| Hand-off waiting time | Time between two consecutive claim or payment steps | | | days | lower is better | see bottlenecks | `transition_waits.csv` |
| Conformance rate | Claims whose trace fits the reference approval model exactly (alignment fitness of one) | fitting claims | claims | % | higher is better | 66.0% | `alignment_fitness_by_case.csv` |
| Compliance rule hits | Claims violating each business rule R1 to R8 | claims hitting the rule | claims | count and % | lower is better | see methodology | `compliance_rules.csv` |
| Risk score | Calibrated probability that a claim becomes a problem claim, scored at first submission | | | probability | context | | `rag_monitor_claims.csv` |
| RAG band | RED (riskiest 10.0%), AMBER (next 20.0%), GREEN (rest), cut-offs fixed on validation | | | band | context | | `rag_monitor_claims.csv` |
| RED precision | Observed problem rate among RED claims | RED problem claims | RED claims | % | higher is better | 77.3% | `rag_band_summary.csv` |

## 4. Status rules

- **SLA status per month and unit** (Excel Dashboard): GREEN below the AMBER threshold, AMBER from the AMBER threshold, RED from the RED threshold. All three thresholds are editable inputs, so the business can set them.
- **Scorecard deviation**: a unit is shaded when its rejection or SLA breach rate differs from the all-claims rate by more than five percentage points. This is set as a conditional format in the Excel pack.
- Status colours always appear with a text label (RED, AMBER, GREEN), so they never rely on colour alone.

## 5. Data quality requirements

- Source files must match their published MD5, otherwise the build stops.
- Parsed case and event counts must match the published counts. The result is recorded in the manifest.
- Quarantine rules DQ01 to DQ06 remove records. Flag rules DQ07 to DQ12 keep records and expose a flag. Hit counts are published in `outputs/kpis/dq_rule_results.csv`.
- Every KPI table excludes quarantined and censored claims.

## 6. Known gaps a production version would need

- A real, agreed SLA and RAG thresholds from the business.
- Resource-level data (who reviewed what), to measure workload and individual queues. The source anonymises people.
- Organisational unit for domestic claims, which are reported by budget number only.
- Payment-run calendar as master data. It is inferred here from timestamps.
- Ongoing model monitoring: monthly calibration and band precision checks, with a retraining trigger.
