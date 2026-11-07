# Power BI build guide

This guide builds a five-page report from the star-schema CSVs in `outputs/powerbi/`. Run the pipeline first: `fact_transition.csv` is event-level, so it is generated locally and not shipped in the repo. Every "check" value below comes from the audit record. If your visual shows a different number, a relationship or filter is wrong.

## 1. Load the data

Get Data > Text/CSV, load each file:

| Table | Grain | Key |
|---|---|---|
| `fact_claim` | one row per claim (23,554 rows) | `claim_key` |
| `fact_transition` | one row per hand-off between consecutive claim steps (107,656 rows) | `claim_key` + order |
| `dim_date` | one row per calendar day | `date_key` |
| `dim_log` | the three logs | `log_code` |
| `dim_org` | org units, plus domestic budgets (38 rows) | `org_code` |
| `dim_step` | process steps (21 rows) | `step_key` |
| `dim_rag` | RAG bands with sort order and colour | `rag` |

In Power Query, set the following data types:

| Columns | Type |
|---|---|
| `cycle_days`, `approval_days`, `payment_days`, `amount`, `alignment_fitness`, `risk_score`, `wait_hours` | Decimal |
| all `_key` and flag columns | Whole number |
| `dim_date[date]` | Date |

Optionally, load `outputs/mining/compliance_rules.csv` and unpivot every column except `log` and `claims` to get a long `rule / claims_hit` table.

## 2. Model view (relationships)

| From (many) | To (one) | Active | Cross filter |
|---|---|---|---|
| `fact_claim[submit_date_key]` | `dim_date[date_key]` | yes | single |
| `fact_claim[payment_date_key]` | `dim_date[date_key]` | no (used by `USERELATIONSHIP`) | single |
| `fact_claim[log_code]` | `dim_log[log_code]` | yes | single |
| `fact_claim[org_code]` | `dim_org[org_code]` | yes | single |
| `fact_claim[rag]` | `dim_rag[rag]` | yes | single |
| `fact_transition[claim_key]` | `fact_claim[claim_key]` | yes | single |
| `fact_transition[from_step_key]` | `dim_step[step_key]` | yes | single |
| `fact_transition[to_step_key]` | `dim_step[step_key]` | no | single |

Then:
1. Mark `dim_date` as the date table on `date`.
2. Sort `dim_date[month]` by `month_no`, `dim_date[weekday]` by `weekday_no`, and `dim_rag[rag]` by `rag_order`.
3. Hide all `_key` columns from report view.

## 3. Measures

Create a `_Measures` table and paste the measures from `outputs/powerbi/measures.dax`. Create the three what-if parameters described at the top of that file: SLA Days (default 14), Amber Threshold (default 20.0%) and Red Threshold (default 30.0%).

## 4. Pages

### Page 1: Overview
- **Slicers:** `dim_log[log_name]`, `dim_date[year]`, SLA Days parameter.
- **Cards:** Claims, Paid Rate, Rejection Rate, First Time Right Rate, Median Cycle Days, SLA Breach Rate, SLA Status. Apply the conditional font colour from the field value `SLA Status Colour`.
- **Line chart:** x `dim_date[year_month]`, y Claims, legend `dim_log[log_name]`.
- **Line chart:** x `dim_date[year_month]`, y SLA Breach Rate, legend `dim_log[log_name]`.
- **Text box:** "2017 covers two departments; 2018 the whole university."
- **Check (no filters):** Claims 23,554, Paid Rate 95.7%, Rejection Rate 16.7%, Median Cycle Days 8.8, SLA Breach Rate 25.6% at 14 days.

### Page 2: Bottlenecks
- **Bar chart:** y `fact_transition[handoff]`, x Total Wait Days, top ten by Total Wait Days. Tooltip: Median Wait Days, Handoffs.
- **Matrix:** rows `dim_log[log_name]`, `fact_transition[handoff]`; values Median Wait Days, Wait Share.
- **Column chart:** x `dim_date[weekday]`, y Payments by Payment Date. This shows the batch payment run.
- **Cards:** Median Approval Days, Median Payment Days.
- **Check:** most payments fall on Thursdays and Mondays. Median Approval Days 2.0, Median Payment Days 5.4.

### Page 3: Units
- **Matrix:** rows `dim_org[org_code]` filtered to `org_type = "Organisational unit"`. Values: Claims, Rejection Rate, Resubmission Rate, SLA Breach Rate, Median Cycle Days, SLA Status. Visual filter: Claims at or above 50.
- **Conditional formatting:** background colour scale on Rejection Rate and SLA Breach Rate; font colour on SLA Status from `SLA Status Colour`.
- **Scatter:** x Rejection Rate, y SLA Breach Rate, size Claims, details `org_code`.
- **Check:** with 50+ claims, SLA Breach Rate ranges from 13.0% to 54.7% across unit and log combinations (use `dim_log` as a second row level).

### Page 4: Conformance
- **Card:** Conformance Rate. Check: 66.0%.
- **Clustered bar:** y `dim_log[log_name]`, x Conformance Rate. Check: DD 68.4%, ID 57.5%, RFP 70.4%.
- **Histogram:** alignment fitness, as a column chart on `fact_claim[alignment_fitness]`, binned (for example in steps of one twentieth).
- **Table:** the compliance rules table, if loaded.
- **Image:** `outputs/figures/pm_reference_model.png` (intended process) and `outputs/figures/pm_discovered_DD.png`.

### Page 5: Early warning
- **Page filter:** `fact_claim[rag]` is not "NOT SCORED".
- **Cards:** Scored Claims, RED Precision, GREEN Problem Rate, Problem Capture RED AMBER.
- **Column chart:** x `dim_rag[rag]`, y Problem Rate, with data colours matching `dim_rag[rag_hex]`.
- **Stacked column:** x `dim_date[year_month]`, y Claims, legend `dim_rag[rag]`.
- **Table:** `case_id`, `log_code`, `risk_score`, `top_risk_drivers`, filtered to RED and sorted by `risk_score` descending. This is the reviewers' work queue.
- **Check:** Scored Claims 9,643, RED Precision 77.3%, GREEN Problem Rate 31.6%, Problem Capture RED AMBER 45.2%.

## 5. Finishing

- Theme: neutral background. Use the RAG colours only for status, always next to the RAG text.
- Add a footer text box: "Source: BPI Challenge 2020, 4TU.ResearchData, CC BY-NC 4.0. University expense claims; analytical SLA."
- Publish with row-level security only if units should see only their own rows (role filter on `dim_org[org_code]`).
