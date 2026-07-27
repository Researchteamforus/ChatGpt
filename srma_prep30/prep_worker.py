#!/usr/bin/env python3
"""Generate one auditable SRMA preparation artifact per workstream.

These artifacts prepare screening, extraction, appraisal, synthesis, and
submission infrastructure. They do not claim that screening, extraction,
risk-of-bias assessment, or duplicate review has been completed.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

PROJECT = "Childhood immunization in Bangladesh systematic review"
PROSPERO = "CRD420261461557"
LEAD = "Md. Mizanoor Rahman"
SECOND = "Kapashia Binte Giash"
AFFILIATION = "Department of Statistics, Mawlana Bhashani Science and Technology University"

TASKS = {
1: ("01_eligibility_framework.md", "Eligibility framework", """# Eligibility framework

## Population
Children in Bangladesh, principally infancy through under-five years, and caregivers/households when the measured outcome concerns childhood immunization.

## Exposure/intervention
Routine childhood vaccination, EPI service delivery, vaccination access, outreach, reminders, catch-up, missed opportunities, determinants, or inequalities.

## Outcomes
Coverage, antigen-specific uptake, full/basic vaccination, timeliness, delay, valid vaccination, dropout, zero-dose, under-immunization, missed opportunities, barriers, determinants, and programme effects.

## Eligible designs
Cross-sectional surveys, cohorts, surveillance analyses, trials/quasi-experiments, mixed-methods and qualitative studies, theses, and official government/agency reports with usable Bangladesh data.

## Exclusions
No Bangladesh-specific data; adult-only vaccination; vaccine immunogenicity/safety without service/coverage outcome; clinical case series without immunization outcome; editorials/protocols with no results; duplicate report versions retained only as linked companion reports.

## Governance
Final decisions must be documented record by record. No independent duplicate screening may be claimed unless both named reviewers actually complete and document it.
"""),
2: ("02_screening_manual.md", "Screening manual", """# Screening manual

1. Read title and abstract without inferring unreported details.
2. Include when eligibility is plausible or unclear; exclude only when a rule is explicit.
3. Use one primary exclusion reason at full text.
4. Link preprints, conference abstracts, theses, reports, and journal papers that may represent the same study.
5. Preserve Bangladesh subgroup reports even when the parent study is multinational.
6. Do not use prior-version inclusion as proof of eligibility in the prospective rerun.
7. Escalate uncertainty about population age, vaccination outcome, study design, or duplicate lineage.

Decision labels: Include / Exclude / Unclear / Duplicate-report candidate.
"""),
3: ("03_title_abstract_screening_form.csv", "Title-abstract screening form", "Record_ID,Title,Year,Source,Reviewer,Decision,Population_relevant,Immunization_relevant,Bangladesh_data,Empirical_results,Duplicate_report_candidate,Reason_or_note,Decision_date\n"),
4: ("04_full_text_screening_form.csv", "Full-text screening form", "Record_ID,Report_ID,Study_family_ID,Citation,Reviewer,Full_text_obtained,Population_eligible,Exposure_or_intervention_eligible,Outcome_eligible,Design_eligible,Bangladesh_specific_data,Decision,Primary_exclusion_reason,Secondary_note,Page_evidence,Decision_date\n"),
5: ("05_exclusion_reason_dictionary.md", "Exclusion-reason dictionary", """# Full-text exclusion reasons

Use exactly one primary reason:

1. Wrong country/no separable Bangladesh data
2. Wrong population/age group
3. Wrong vaccination topic or exposure
4. No eligible coverage, timeliness, dropout, zero-dose, determinant, barrier, or programme outcome
5. Ineligible publication type with no empirical results
6. Protocol/editorial/commentary only
7. Duplicate report with no unique usable data
8. Insufficient methods or results to verify eligibility
9. Full text unobtainable after documented attempts
10. Outside registered date or language limits, when applicable
"""),
6: ("06_report_family_linkage_rules.md", "Report-family linkage rules", """# Study and report-family linkage

Assign a Study_family_ID when two reports share a sample frame, field dates, survey wave, intervention, authors, setting, and outcome structure.

Retain separate Report_IDs for:
- preprint and version of record;
- conference abstract/poster and full paper;
- thesis and derived journal article;
- government summary and complete survey report;
- secondary analyses from the same BDHS/EPI dataset.

Do not deduplicate different analyses merely because they use the same national dataset. Avoid double counting the same participants/outcomes in a single meta-analysis.
"""),
7: ("07_screening_calibration_worksheet.csv", "Screening calibration worksheet", "Round,Record_ID,Lead_decision,Second_reviewer_decision,Agreement,Conflict_type,Resolution,Rule_clarified,Resolved_by,Date\n"),
8: ("08_conflict_resolution_log.csv", "Conflict-resolution log", "Conflict_ID,Stage,Record_ID_or_Study_family_ID,Lead_position,Second_reviewer_position,Evidence_reviewed,Final_resolution,Decision_rule,Resolver,Date\n"),
9: ("09_search_transfer_checklist.md", "Search-to-screening transfer checklist", """# Search-to-screening transfer checklist

- Freeze raw exports and checksum files.
- Record database/source, exact query, date, and hit count.
- Import all formal search results before deduplication.
- Preserve source provenance after deduplication.
- Reconcile previously verified Scopus package separately.
- Mark subscription databases not executed; do not imply completion.
- Create immutable pre-screening master and sequential Record_ID.
- Verify title, year, DOI/PMID and source fields on a sample.
- Record exact and fuzzy duplicates separately.
- Export screening-ready CSV/XLSX without eligibility decisions.
"""),
10: ("10_prisma_accounting_schema.csv", "PRISMA accounting schema", "Stage,Source_or_reason,Count,Definition,Verified_by,Verification_date,Notes\nIdentification,Database records,0,All records exported from formal databases,,,\nIdentification,Other-method records,0,Registers/websites/citation searching/repositories,,,\nDeduplication,Exact duplicates removed,0,DOI PMID source-ID or exact normalized-title duplicates,,,\nDeduplication,Fuzzy duplicates resolved,0,Manually verified probable duplicates,,,\nScreening,Title-abstract records screened,0,Unique records receiving a documented decision,,,\nScreening,Title-abstract exclusions,0,Records excluded before full text,,,\nRetrieval,Reports sought,0,Reports requested or downloaded,,,\nRetrieval,Reports not retrieved,0,After documented retrieval attempts,,,\nEligibility,Full-text reports assessed,0,Reports with documented full-text decision,,,\nEligibility,Full-text exclusions,0,Sum by primary reason,,,\nIncluded,Studies included,0,Unique study families,,,\nIncluded,Reports included,0,All reports representing included studies,,,\n"),
11: ("11_study_characteristics_dictionary.md", "Study-characteristics dictionary", """# Study characteristics

Extract: Study_family_ID; Report_ID; authors; year; publication type; funding; setting; administrative division/district; urban/rural/camp; study design; field dates; sampling frame; sampling method; sample size; child age; caregiver definition; vaccine schedule reference; data source; survey weighting; clustering; inclusion/exclusion criteria; response rate; and analysis population.

Distinguish national household surveys, EPI Coverage Evaluation Surveys, facility studies, surveillance cohorts, refugee/camp studies, and intervention evaluations.
"""),
12: ("12_coverage_extraction_form.csv", "Coverage extraction form", "Study_family_ID,Report_ID,Outcome_ID,Antigen_or_series,Coverage_definition,Age_window,Numerator,Denominator,Estimate,Measure,Lower_CI,Upper_CI,Weighted,Survey_design_accounted,Card_or_recall,Geography,Year_of_measurement,Page_table_figure,Notes\n"),
13: ("13_timeliness_extraction_form.csv", "Timeliness extraction form", "Study_family_ID,Report_ID,Outcome_ID,Vaccine_dose,Schedule_source,Eligible_age_start,Eligible_age_end,Timely_definition,Delay_definition,Numerator,Denominator,Estimate,Measure,Lower_CI,Upper_CI,Time_to_event_method,Page_table_figure,Notes\n"),
14: ("14_zero_dose_dropout_form.csv", "Zero-dose and dropout extraction form", "Study_family_ID,Report_ID,Outcome_ID,Outcome_type,Operational_definition,Starting_antigen,Ending_antigen,Numerator,Denominator,Estimate,Measure,Lower_CI,Upper_CI,Age_group,Geography,Survey_year,Page_table_figure,Notes\n"),
15: ("15_determinants_extraction_form.csv", "Determinants extraction form", "Study_family_ID,Report_ID,Analysis_ID,Outcome,Determinant,Reference_category,Comparison_category,Adjusted,Effect_measure,Estimate,Lower_CI,Upper_CI,P_value,Covariates,Model,Survey_design_accounted,Missing_data_method,Page_table_figure,Notes\n"),
16: ("16_programme_intervention_form.csv", "Programme/intervention extraction form", "Study_family_ID,Report_ID,Intervention,Comparator,Design,Unit_of_allocation,Clusters,Participants,Baseline_period,Followup_period,Outcome,Effect_measure,Estimate,Lower_CI,Upper_CI,Adjusted,Contamination,Implementation_fidelity,Page_table_figure,Notes\n"),
17: ("17_survey_design_form.csv", "Survey-design extraction form", "Study_family_ID,Report_ID,Data_source,Survey_wave,Sampling_stages,Strata,PSU,Weights_used,Finite_population_correction,Domain_analysis,Variance_estimator,Design_effect_reported,Effective_sample_size,Nonresponse_adjustment,Notes\n"),
18: ("18_effect_estimate_form.csv", "Effect-estimate extraction form", "Study_family_ID,Report_ID,Analysis_ID,Outcome,Contrast,Effect_measure,Estimate,SE,Lower_CI,Upper_CI,P_value,N_events,N_total,Adjusted,Adjustment_set,Preferred_for_synthesis,Reason_for_preference,Notes\n"),
19: ("19_missing_data_form.csv", "Missing-data extraction form", "Study_family_ID,Report_ID,Variable_or_outcome,Missing_n,Missing_percent,Reason_reported,Complete_case,Imputation_method,Number_of_imputations,Sensitivity_analysis,Attrition_n,Attrition_percent,Handling_in_effect_estimate,Notes\n"),
20: ("20_study_overlap_matrix.csv", "Study-overlap matrix", "Study_family_ID,Report_ID,Dataset_or_cohort,Survey_wave,Geography,Field_dates,Age_range,Outcome_family,Sample_size,Potential_overlap_with,Overlap_basis,Double_count_risk,Resolution\n"),
21: ("21_jbi_analytical_cross_sectional_rob.csv", "JBI analytical cross-sectional appraisal", "Study_family_ID,Report_ID,Reviewer,Inclusion_criteria_defined,Setting_and_subjects_described,Exposure_valid_reliable,Outcome_objective_standard,Confounders_identified,Confounder_control,Outcome_valid_reliable,Analysis_appropriate,Overall_judgement,Support_for_judgement,Date\n"),
22: ("22_jbi_prevalence_rob.csv", "JBI prevalence appraisal", "Study_family_ID,Report_ID,Reviewer,Sample_frame_appropriate,Sampling_appropriate,Sample_size_adequate,Subjects_setting_described,Coverage_sufficient,Condition_measurement_valid,Measurement_standard,Analysis_appropriate,Response_rate_adequate,Overall_judgement,Support_for_judgement,Date\n"),
23: ("23_robins_i_intervention_template.csv", "ROBINS-I intervention template", "Study_family_ID,Report_ID,Reviewer,Confounding,Selection_into_study,Intervention_classification,Deviations_from_intervention,Missing_data,Outcome_measurement,Selective_reporting,Overall_risk,Support_for_judgement,Date\n"),
24: ("24_casp_qualitative_template.csv", "CASP qualitative template", "Study_family_ID,Report_ID,Reviewer,Clear_aims,Qualitative_method_appropriate,Design_appropriate,Recruitment_appropriate,Data_collection_appropriate,Researcher_relationship_considered,Ethics_considered,Analysis_rigorous,Findings_clear,Research_value,Overall_judgement,Notes,Date\n"),
25: ("25_government_report_appraisal.csv", "Government-report appraisal", "Study_family_ID,Report_ID,Reviewer,Issuing_body,Survey_objective_clear,Sampling_frame_described,Probability_sampling,Questionnaire_or_measure_definition,Fieldwork_quality_control,Weighting_described,Nonresponse_reported,Variance_or_CI_reported,Definitions_consistent,Selective_reporting_concern,Overall_judgement,Notes,Date\n"),
26: ("26_meta_analysis_skeleton.R", "Meta-analysis R skeleton", """# Prospective synthesis skeleton; do not insert unverified prior results.
library(readr)
library(dplyr)
library(metafor)

x <- read_csv("verified_effect_estimates.csv", show_col_types = FALSE)
stopifnot(all(c("Study_family_ID", "yi", "sei", "Outcome_family") %in% names(x)))

run_meta <- function(dat) {
  dat <- dat %>% filter(is.finite(yi), is.finite(sei), sei > 0)
  if (nrow(dat) < 2) return(NULL)
  rma(yi = yi, sei = sei, data = dat, method = "REML", test = "knha")
}

results <- x %>% group_split(Outcome_family) %>% lapply(run_meta)
# Add prediction intervals, influence diagnostics and forest plots only after verification.
"""),
27: ("27_heterogeneity_subgroup_plan.md", "Heterogeneity and subgroup plan", """# Heterogeneity plan

Use random-effects synthesis only for sufficiently comparable definitions, populations, and time periods. Report tau-squared, I-squared, Q, and prediction intervals where estimable.

Prespecified explanatory groupings may include:
- national vs subnational/facility/camp;
- urban vs rural;
- survey era and vaccine schedule era;
- card-only vs card-plus-recall ascertainment;
- age group;
- antigen/series definition;
- survey-weighted vs unweighted analysis;
- official CES/BDHS vs independent primary study.

Avoid subgroup meta-analysis with sparse cells. Treat subgroup findings as exploratory unless prespecified and adequately powered.
"""),
28: ("28_sensitivity_analysis_plan.md", "Sensitivity-analysis plan", """# Sensitivity analyses

1. Remove high-risk/critically appraised studies.
2. Restrict to nationally representative probability samples.
3. Restrict to card-confirmed vaccination.
4. Separate recall-inclusive estimates.
5. Retain one estimate per overlapping study family.
6. Compare REML with alternative tau-squared estimators when k permits.
7. Use Hartung-Knapp adjustment where appropriate.
8. Leave-one-out and influence diagnostics.
9. Exclude estimates with reconstructed denominators or unclear definitions.
10. Compare prior-version provisional results only as a labelled historical benchmark, never as prospective rerun results.
"""),
29: ("29_grade_evidence_profile.csv", "GRADE evidence profile", "Outcome,Studies,Participants,Study_design,Risk_of_bias,Inconsistency,Indirectness,Imprecision,Publication_bias,Other_considerations,Certainty,Relative_effect,Absolute_effect,Plain_language_summary,Judgement_notes\n"),
30: ("30_submission_package_checklist.md", "Submission-package checklist", """# Submission and reviewer-audit package

## Main files
- Manuscript with current verified results
- Title page and author affiliations
- Cover letter
- Highlights/key messages if required
- PRISMA 2020 checklist
- PRISMA flow diagram
- Registered protocol citation and deviations table

## Supplementary files
- Full reproducible search strategies and dated logs
- Database/source counts and deduplication audit
- Screening decision export and full-text exclusion log
- Study/report-family linkage table
- Extraction workbook and data dictionary
- Risk-of-bias judgements with support
- Analysis-ready dataset and R scripts
- Forest/funnel/influence plots as applicable
- GRADE evidence profiles

## Integrity statements
Prior-version results must remain clearly labelled provisional until replaced by verified post-registration results. Do not claim post-registration searches, duplicate screening, extraction, or appraisal unless documented.
"""),
}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--task", type=int, required=True)
    p.add_argument("--out", default="prep_outputs")
    args = p.parse_args()
    if args.task not in TASKS:
        raise SystemExit("task must be 1-30")
    filename, title, body = TASKS[args.task]
    out = Path(args.out) / f"task_{args.task:02d}"
    out.mkdir(parents=True, exist_ok=True)
    header = ""
    if filename.endswith(".md"):
        header = f"> Project: {PROJECT}  \n> PROSPERO: {PROSPERO}  \n> Reviewers: {LEAD} (lead); {SECOND} (second reviewer)  \n> Affiliation: {AFFILIATION}  \n> Status: preparation template; no completed review activity is implied.\n\n"
    (out / filename).write_text(header + body, encoding="utf-8")
    log = {
        "task": args.task,
        "title": title,
        "file": filename,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "prospero": PROSPERO,
        "claims_completed_screening": False,
        "claims_duplicate_review": False,
        "claims_completed_extraction": False,
        "claims_completed_risk_of_bias": False,
    }
    (out / "task_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
    print(json.dumps(log))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
