#!/usr/bin/env python3
"""Human-screening execution preparation for the Bangladesh childhood immunization SRMA.

This program creates guidance, candidate decision-taxonomy materials, neutral reviewer
CSV batches, and blank adjudication dashboards. It does not make title/abstract,
duplicate, full-text, extraction, risk-of-bias, or GRADE decisions.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

PROSPERO = "CRD420261461557"
REVIEWERS = ["Md. Mizanoor Rahman", "Kapashia Binte Giash"]
AGENTS_PER_STREAM = 10
EXPECTED_POOL = 8433
EXPECTED_MASTER = 55248
CALIBRATION_N = 1000


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str] | None = None) -> None:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or (list(rows[0]) if rows else [])
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if fields:
            w.writeheader()
            for row in rows:
                w.writerow({k: row.get(k, "") for k in fields})


def find_one(root: Path, name: str) -> Path:
    hits = list(root.rglob(name))
    if not hits:
        raise FileNotFoundError(f"{name} not found under {root}")
    return max(hits, key=lambda p: p.stat().st_size)


def rr(rows: list[dict[str, str]], n: int) -> list[list[dict[str, str]]]:
    out = [[] for _ in range(n)]
    for i, row in enumerate(rows):
        out[i % n].append(row)
    return out


def stable_key(row: dict[str, str]) -> str:
    seed = "|".join([row.get("Record_ID", ""), row.get("DOI", ""), row.get("Title", "")])
    return hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest()


def normalise_row(row: dict[str, str]) -> dict[str, str]:
    return {
        "Record_ID": row.get("Record_ID", ""),
        "Title": row.get("Title", ""),
        "Abstract": row.get("Abstract", ""),
        "Year": row.get("Year", ""),
        "DOI": row.get("DOI", ""),
        "PMID": row.get("PMID", ""),
        "URL": row.get("URL", ""),
        "Machine_Priority_Admin_Only": row.get("Machine_Priority", ""),
        "Decision": "",
        "Exclusion_Reason_Code": "",
        "Reviewer_Notes": "",
        "Review_Date": "",
        "Review_Status": "Not reviewed",
    }


def prepare(args: argparse.Namespace) -> None:
    final_root = Path(args.final160)
    readiness_root = Path(args.readiness)
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    high = read_csv(find_one(final_root, "openalex_high_priority.csv"))
    unclear = read_csv(find_one(final_root, "openalex_unclear_review.csv"))
    combined = read_csv(find_one(final_root, "combined_discovery_master_55248.csv"))
    pool = high + unclear
    assert len(pool) == EXPECTED_POOL, len(pool)
    assert len(combined) == EXPECTED_MASTER, len(combined)

    pool.sort(key=lambda r: (-int(r.get("Machine_Score") or 0), stable_key(r)))
    clean = [normalise_row(r) for r in pool]
    write_csv(out / "small" / "review_pool_8433.csv", clean)

    # Ten complete screening batches. Machine priority is retained only as an
    # administrative field and can be removed before blinded reviewer delivery.
    for i, chunk in enumerate(rr(clean, AGENTS_PER_STREAM), 1):
        write_csv(out / "export_shards" / f"review_pool_{i:02d}.csv", chunk, list(clean[0]))

    # Deterministic stratified calibration sample: all records are still formally
    # unreviewed; this is only a reviewer-training and agreement-testing package.
    high_clean = [normalise_row(r) for r in high]
    unclear_clean = [normalise_row(r) for r in unclear]
    high_clean.sort(key=stable_key)
    unclear_clean.sort(key=stable_key)
    n_high = min(len(high_clean), CALIBRATION_N // 2)
    calibration = high_clean[:n_high] + unclear_clean[: CALIBRATION_N - n_high]
    calibration.sort(key=stable_key)
    assert len(calibration) == CALIBRATION_N, len(calibration)
    for i, chunk in enumerate(rr(calibration, AGENTS_PER_STREAM), 1):
        write_csv(out / "adjudication_shards" / f"calibration_{i:02d}.csv", chunk, list(calibration[0]))

    # Preserve a manifest of successful upstream readiness material without
    # treating its templates as completed assessments.
    readiness_files = []
    for p in readiness_root.rglob("*"):
        if p.is_file():
            readiness_files.append({"Relative_Path": str(p.relative_to(readiness_root)), "Size_Bytes": p.stat().st_size})
    write_csv(out / "small" / "readiness_file_manifest.csv", readiness_files)

    summary = {
        "prospero": PROSPERO,
        "combined_discovery_master": len(combined),
        "human_review_pool": len(clean),
        "calibration_sample": len(calibration),
        "agents_planned": 4 * AGENTS_PER_STREAM,
        "formal_human_screening_completed": 0,
        "duplicate_adjudications_completed": 0,
        "full_text_decisions_completed": 0,
        "generated_utc": now(),
    }
    (out / "prepare_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


HANDBOOK_TOPICS = [
    ("Scope and governance", "Use the registered review question and documented protocol. Machine labels are administrative aids, never eligibility decisions."),
    ("Geography", "Record whether the study contains Bangladesh-specific evidence. Mixed-country studies require a separable Bangladesh result or explicit protocol handling."),
    ("Population", "Record whether the evidence concerns children within the protocol age range. Mixed-age samples require transparent handling."),
    ("Vaccination domain", "Confirm that routine childhood vaccination or immunization is a substantive study focus rather than a passing mention."),
    ("Eligible outcomes", "Assess coverage, uptake, timeliness, dropout or zero-dose, determinants, inequalities, and programme or service-delivery outcomes against the protocol."),
    ("Publication and study type", "Do not infer eligibility from document type alone. Use the protocol to handle reports, conference records, reviews, editorials, and primary studies."),
    ("Insufficient information", "At title/abstract stage, uncertainty should normally remain eligible for full-text checking rather than being converted into a confident exclusion."),
    ("Duplicate and companion reports", "Flag related reports without deleting them during screening. Link reports to an underlying study after evidence review."),
    ("Independent review and reconciliation", "Each reviewer records a decision independently. Reconciliation occurs only after both decisions are locked."),
    ("Audit trail", "Retain reviewer identity, date, reason code, notes, version, and any adjudication. Never overwrite a prior decision without a logged correction."),
]


def handbook_agent(args: argparse.Namespace) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    agent = int(args.agent)
    title, guidance = HANDBOOK_TOPICS[agent - 1]
    text = f"# Screening handbook section {agent:02d}: {title}\n\n{guidance}\n\n"
    text += "## Required documentation\n\n- Decision: Include / Exclude / Unclear, using the agreed project vocabulary.\n- Reason code: required for an exclusion.\n- Notes: factual and concise; do not copy machine priority as a reviewer decision.\n- Date and reviewer identity: required.\n\n"
    text += "## Governance\n\nThis section is operational guidance only. It does not record a human eligibility decision.\n"
    (out / f"handbook_{agent:02d}.md").write_text(text, encoding="utf-8")


TAXONOMY = [
    ("TA-GEO", "Potentially wrong geography", "No Bangladesh-specific evidence is apparent."),
    ("TA-POP", "Potentially wrong population", "The population does not appear to include protocol-eligible children."),
    ("TA-VAX", "Potentially wrong topic", "Routine childhood vaccination/immunization is not a substantive focus."),
    ("TA-OUT", "Potentially wrong outcome", "No protocol-relevant coverage, timeliness, dropout, determinant, inequality, or programme outcome is apparent."),
    ("TA-DOC", "Potentially ineligible document type", "The record may not report eligible original evidence; confirm against the protocol."),
    ("TA-DES", "Potentially ineligible design", "The study design may fall outside protocol eligibility; do not infer solely from machine hints."),
    ("TA-DUP", "Possible duplicate or companion report", "Keep linked for study-level adjudication; this is not automatically an exclusion."),
    ("TA-NOI", "Insufficient information", "The title/abstract is insufficient; normally retain for full-text assessment."),
    ("TA-MIX", "Mixed population/geography/outcome", "Eligibility depends on whether protocol-relevant results are separable."),
    ("TA-OTH", "Other protocol-based reason", "A reviewer must provide a specific note tied to the protocol."),
]


def taxonomy_agent(args: argparse.Namespace) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    agent = int(args.agent)
    code, label, definition = TAXONOMY[agent - 1]
    row = {
        "Code": code,
        "Candidate_Label": label,
        "Operational_Definition": definition,
        "Stage": "Title/abstract candidate taxonomy",
        "Requires_Protocol_Confirmation": "Yes",
        "Can_Be_Assigned_By_Machine": "No",
        "Human_Reviewer_Field": "Blank",
        "Final_Adjudication_Field": "Blank",
    }
    write_csv(out / f"taxonomy_{agent:02d}.csv", [row])
    (out / f"taxonomy_{agent:02d}.md").write_text(
        f"# {code}: {label}\n\n{definition}\n\nThis is a candidate operational code and must be confirmed against the registered protocol before use.\n",
        encoding="utf-8",
    )


def export_agent(args: argparse.Namespace) -> None:
    root = Path(args.input)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    agent = int(args.agent)
    rows = read_csv(find_one(root, f"review_pool_{agent:02d}.csv"))
    blind_fields = ["Batch", "Batch_Order", "Record_ID", "Title", "Abstract", "Year", "DOI", "PMID", "URL", "Reviewer", "Decision", "Exclusion_Reason_Code", "Reviewer_Notes", "Review_Date", "Review_Status"]
    for reviewer in REVIEWERS:
        prepared = []
        for i, r in enumerate(rows, 1):
            prepared.append({
                "Batch": f"TA-EXEC-{agent:02d}",
                "Batch_Order": i,
                "Record_ID": r.get("Record_ID", ""),
                "Title": r.get("Title", ""),
                "Abstract": r.get("Abstract", ""),
                "Year": r.get("Year", ""),
                "DOI": r.get("DOI", ""),
                "PMID": r.get("PMID", ""),
                "URL": r.get("URL", ""),
                "Reviewer": reviewer,
                "Decision": "",
                "Exclusion_Reason_Code": "",
                "Reviewer_Notes": "",
                "Review_Date": "",
                "Review_Status": "Not reviewed",
            })
        slug = "Mizan" if reviewer.startswith("Md.") else "Kapashia"
        write_csv(out / f"TA_EXEC_{agent:02d}_{slug}.csv", prepared, blind_fields)
    write_csv(out / f"TA_EXEC_{agent:02d}_Admin_Key.csv", [{"Record_ID": r.get("Record_ID", ""), "Machine_Priority_Admin_Only": r.get("Machine_Priority_Admin_Only", "")} for r in rows])


def adjudication_agent(args: argparse.Namespace) -> None:
    root = Path(args.input)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    agent = int(args.agent)
    rows = read_csv(find_one(root, f"calibration_{agent:02d}.csv"))
    dashboard = []
    for i, r in enumerate(rows, 1):
        dashboard.append({
            "Calibration_Batch": f"CAL-{agent:02d}",
            "Batch_Order": i,
            "Record_ID": r.get("Record_ID", ""),
            "Title": r.get("Title", ""),
            "Reviewer1_Decision": "",
            "Reviewer1_Reason": "",
            "Reviewer2_Decision": "",
            "Reviewer2_Reason": "",
            "Agreement": "",
            "Conflict_Type": "",
            "Final_Decision": "",
            "Final_Reason": "",
            "Adjudicator": "",
            "Resolution_Notes": "",
            "Resolution_Date": "",
        })
    write_csv(out / f"CAL_{agent:02d}_Adjudication_Blank.csv", dashboard)
    qa = [
        {"Check": "Both reviewers completed every assigned row", "Status": "Not checked", "Notes": ""},
        {"Check": "Reviewer decisions were locked before reconciliation", "Status": "Not checked", "Notes": ""},
        {"Check": "Every exclusion has a reason code", "Status": "Not checked", "Notes": ""},
        {"Check": "Conflicts were resolved and documented", "Status": "Not checked", "Notes": ""},
        {"Check": "Agreement statistics were calculated only after decisions", "Status": "Not checked", "Notes": ""},
    ]
    write_csv(out / f"CAL_{agent:02d}_QA_Checklist.csv", qa)


def consolidate(args: argparse.Namespace) -> None:
    root = Path(args.input)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name in ["handbook", "taxonomy", "reviewer_batches", "adjudication"]:
        (out / name).mkdir(exist_ok=True)
    for p in root.rglob("handbook_*.md"):
        shutil.copy2(p, out / "handbook" / p.name)
    for p in root.rglob("taxonomy_*.*"):
        shutil.copy2(p, out / "taxonomy" / p.name)
    for p in root.rglob("TA_EXEC_*.csv"):
        shutil.copy2(p, out / "reviewer_batches" / p.name)
    for p in root.rglob("CAL_*.csv"):
        shutil.copy2(p, out / "adjudication" / p.name)

    mizan = sum(len(read_csv(p)) for p in (out / "reviewer_batches").glob("*_Mizan.csv"))
    kapashia = sum(len(read_csv(p)) for p in (out / "reviewer_batches").glob("*_Kapashia.csv"))
    calibration = sum(len(read_csv(p)) for p in (out / "adjudication").glob("*_Adjudication_Blank.csv"))
    assert mizan == EXPECTED_POOL, mizan
    assert kapashia == EXPECTED_POOL, kapashia
    assert calibration == CALIBRATION_N, calibration

    taxonomy_rows = []
    for p in sorted((out / "taxonomy").glob("taxonomy_*.csv")):
        taxonomy_rows.extend(read_csv(p))
    write_csv(out / "candidate_title_abstract_reason_codebook.csv", taxonomy_rows)
    summary = {
        "prospero": PROSPERO,
        "parallel_agents": 40,
        "reviewer1_rows_prepared": mizan,
        "reviewer2_rows_prepared": kapashia,
        "calibration_adjudication_rows_prepared": calibration,
        "handbook_sections": len(list((out / "handbook").glob("*.md"))),
        "candidate_reason_codes": len(taxonomy_rows),
        "formal_human_screening_completed": 0,
        "formal_adjudications_completed": 0,
        "generated_utc": now(),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out / "README.md").write_text(
        "# SRMA screening execution package\n\nOperational guidance, candidate reason taxonomy, blinded reviewer batches, and blank adjudication dashboards. No human screening or eligibility decisions are claimed.\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    x = sub.add_parser("prepare")
    x.add_argument("--final160", required=True)
    x.add_argument("--readiness", required=True)
    x.add_argument("--out", required=True)
    x.set_defaults(func=prepare)
    for name, fn in [("handbook-agent", handbook_agent), ("taxonomy-agent", taxonomy_agent), ("export-agent", export_agent), ("adjudication-agent", adjudication_agent)]:
        x = sub.add_parser(name)
        x.add_argument("--agent", type=int, required=True, choices=range(1, AGENTS_PER_STREAM + 1))
        x.add_argument("--input", required=False, default=".")
        x.add_argument("--out", required=True)
        x.set_defaults(func=fn)
    x = sub.add_parser("consolidate")
    x.add_argument("--input", required=True)
    x.add_argument("--out", required=True)
    x.set_defaults(func=consolidate)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
