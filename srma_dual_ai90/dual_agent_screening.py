#!/usr/bin/env python3
"""Dual AI-agent provisional screening for the SAMA Bangladesh review.

The script never writes into human reviewer decision fields. It creates separate
AI recommendation columns for Kapashia_AI_Agent and Mizan_AI_Agent, then builds
human-verification files with blank final-decision fields.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Iterable

AGENTS = {
    "kapashia": "Kapashia_AI_Agent_v1_recall_sensitive",
    "mizan": "Mizan_AI_Agent_v1_protocol_strict",
}
HUMAN_FIELDS = {
    "decision", "human_title_abstract_decision", "exclusion_reason_code",
    "primary_exclusion_code", "reviewer_notes", "review_date",
    "human_final_decision", "human_final_exclusion_code", "human_final_notes",
}
BLANK_EQUIV = {"", "not reviewed", "not_reviewed", "pending", "not assessed", "not_assessed", "na", "n/a"}
BD_TERMS = (
    "bangladesh", "bangladeshi", "dhaka", "chattogram", "chittagong", "sylhet",
    "rajshahi", "rangpur", "khulna", "barishal", "barisal", "mymensingh",
    "cox's bazar", "coxs bazar", "rohingya", "icddr,b", "icddrb",
)
FOREIGN_TERMS = (
    " india", "indian ", "pakistan", "nepal", "sri lanka", "ethiopia", "nigeria",
    "kenya", "uganda", "tanzania", "ghana", "zambia", "malawi", "rwanda",
    "south africa", "china", "chinese", "japan", "indonesia", "philippines",
    "vietnam", "thailand", "myanmar", "united states", " usa ", "united kingdom",
    "australia", "canada", "brazil", "mexico", "bangalore", "delhi", "mumbai",
)
CHILD_TERMS = (
    "child", "children", "childhood", "infant", "infants", "under-five", "under five",
    "under 5", "u5", "pediatric", "paediatric", "neonate", "newborn", "toddler",
    "12-23 months", "12–23 months", "0-23 months", "0–23 months", "school-age",
)
ADULT_ONLY_TERMS = (
    "adult", "elderly", "older adults", "pregnant women", "healthcare worker",
    "health care worker", "medical student", "university student", "physician",
)
VAX_TERMS = (
    "immunization", "immunisation", "immunized", "immunised", "vaccination", "vaccine",
    "vaccinated", "epi", "expanded programme on immunization", "expanded program on immunization",
    "measles", "bcg", "dpt", "dtp", "pentavalent", "polio", "opv", "ipv", "rotavirus",
    "pneumococcal", "pcv", "hepatitis b", "hib", "rubella", "zero-dose", "zero dose",
)
PROGRAM_OUTCOME_TERMS = (
    "coverage", "uptake", "timeliness", "timely", "dropout", "drop-out", "completion",
    "complete immun", "fully immun", "partially immun", "incomplete immun", "status",
    "determinant", "factor", "barrier", "facilitator", "hesitancy", "acceptance",
    "inequal", "inequit", "disparit", "missed", "zero-dose", "zero dose", "access",
    "utilization", "utilisation", "prevalence", "rate", "programme", "program performance",
)
ORIGINAL_STUDY_TERMS = (
    "cross-sectional", "cross sectional", "survey", "cohort", "case-control", "case control",
    "trial", "randomized", "randomised", "secondary analysis", "dhs", "mics", "methods:",
    "methodology:", "participants", "sample size", "we conducted", "data were collected",
)
INELIGIBLE_DOC_TERMS = (
    "systematic review", "scoping review", "narrative review", "a review", "review article",
    "editorial", "commentary", "letter to the editor", "study protocol", "protocol for",
    "conference abstract", "perspective", "opinion", "bibliometric",
)
IMMUNOGENICITY_ONLY_TERMS = (
    "immunogenicity", "seroconversion", "antibody titre", "antibody titer", "vaccine efficacy",
    "vaccine safety", "adverse event", "reactogenicity",
)

def norm(value: object) -> str:
    text = html.unescape(str(value or "")).lower()
    text = re.sub(r"\s+", " ", text)
    return f" {text.strip()} "

def contains_any(text: str, terms: Iterable[str]) -> bool:
    return any(t in text for t in terms)

def occurrences(text: str, terms: Iterable[str]) -> int:
    return sum(text.count(t) for t in terms)

def get_value(row: dict[str, str], *names: str) -> str:
    low = {k.lower(): v for k, v in row.items()}
    for n in names:
        if n.lower() in low:
            return str(low[n.lower()] or "")
    return ""

def record_id(row: dict[str, str], idx: int) -> str:
    return get_value(row, "Record_ID", "Supplementary_Record_ID", "record_id", "id") or f"ROW-{idx+1:06d}"

def human_nonblank(row: dict[str, str]) -> list[str]:
    bad = []
    for k, v in row.items():
        if k.lower().strip() in HUMAN_FIELDS and norm(v).strip() not in BLANK_EQUIV:
            bad.append(k)
    return bad

def features(row: dict[str, str]) -> dict[str, object]:
    title = norm(get_value(row, "Title", "candidate_title"))
    abstract = norm(get_value(row, "Abstract", "candidate_abstract"))
    text = title + " " + abstract
    bd = contains_any(text, BD_TERMS)
    foreign = contains_any(text, FOREIGN_TERMS)
    child = contains_any(text, CHILD_TERMS)
    adult_only = contains_any(text, ADULT_ONLY_TERMS) and not child
    vax_title = contains_any(title, VAX_TERMS)
    vax_count = occurrences(text, VAX_TERMS)
    vax = vax_count > 0
    outcome = contains_any(text, PROGRAM_OUTCOME_TERMS)
    original = contains_any(text, ORIGINAL_STUDY_TERMS)
    ineligible_doc = contains_any(title, INELIGIBLE_DOC_TERMS) or contains_any(text, INELIGIBLE_DOC_TERMS)
    immunogenicity_only = contains_any(text, IMMUNOGENICITY_ONLY_TERMS) and not outcome
    multicountry = bd and foreign
    substantive_vax = vax_title or vax_count >= 2 or outcome
    info_sparse = len(title.strip()) < 10 or (not abstract.strip() and len(title.strip()) < 70)
    return {
        "title": title.strip(), "abstract": abstract.strip(), "text": text,
        "bd": bd, "foreign": foreign, "multicountry": multicountry,
        "child": child, "adult_only": adult_only, "vax": vax,
        "vax_title": vax_title, "vax_count": vax_count, "substantive_vax": substantive_vax,
        "outcome": outcome, "original": original, "ineligible_doc": ineligible_doc,
        "immunogenicity_only": immunogenicity_only, "info_sparse": info_sparse,
    }

def rec(decision: str, code: str, confidence: str, rationale: str, f: dict[str, object]) -> dict[str, str]:
    return {
        "AI_Recommendation": decision,
        "AI_Reason_Code": code,
        "AI_Confidence": confidence,
        "AI_Rationale": rationale,
        "AI_Geography_Signal": "Bangladesh" if f["bd"] else ("Non-Bangladesh" if f["foreign"] else "Unknown"),
        "AI_Child_Population_Signal": "Yes" if f["child"] else ("No/adult-only" if f["adult_only"] else "Unknown"),
        "AI_Immunization_Focus_Signal": "Yes" if f["substantive_vax"] else ("Mention-only" if f["vax"] else "No"),
        "AI_Programme_Outcome_Signal": "Yes" if f["outcome"] else "No/unclear",
        "AI_Original_Study_Signal": "Yes" if f["original"] else "No/unclear",
    }

def recommend(row: dict[str, str], profile: str) -> dict[str, str]:
    f = features(row)
    if f["ineligible_doc"]:
        return rec("Exclude", "TA-DOC", "High", "The record appears to be a review, protocol, editorial, commentary, or other non-primary document.", f)
    if f["foreign"] and not f["bd"]:
        return rec("Exclude", "TA-GEO", "High", "An explicit non-Bangladesh setting is present and no Bangladesh-specific evidence is apparent.", f)
    if f["multicountry"]:
        return rec("Unclear", "TA-MIX", "Medium", "Bangladesh and another geography are mentioned; separable Bangladesh data require human verification.", f)
    if f["adult_only"]:
        return rec("Exclude", "TA-POP", "High", "The population appears adult-only and no eligible child population is apparent.", f)
    if not f["vax"]:
        return rec("Exclude", "TA-VAX", "High" if f["title"] else "Medium", "Routine childhood vaccination or immunization is not a substantive topic.", f)
    if f["immunogenicity_only"]:
        return rec("Exclude", "TA-OUT", "Medium", "The record focuses on immunogenicity, efficacy, or safety rather than programme coverage, timeliness, dropout, determinants, inequality, or access.", f)
    if profile == "kapashia":
        if f["bd"] and f["child"] and f["substantive_vax"] and f["outcome"]:
            return rec("Include", "", "High" if f["original"] else "Medium", "Bangladesh, children, immunization focus, and a programme-relevant outcome are apparent.", f)
        if f["bd"] and f["child"] and f["substantive_vax"]:
            return rec("Unclear", "TA-OUT", "Medium", "The core geography, population, and vaccination topic are present, but the eligible programme outcome is not explicit.", f)
        if f["bd"] and f["substantive_vax"] and not f["child"]:
            return rec("Unclear", "TA-POP", "Medium", "Bangladesh vaccination evidence is present, but the child population is not explicit; retain for human verification.", f)
        if not f["bd"] and f["child"] and f["substantive_vax"] and f["outcome"]:
            return rec("Unclear", "TA-GEO", "Medium", "The record may be relevant, but a Bangladesh-specific setting is not explicit.", f)
        if f["info_sparse"]:
            return rec("Unclear", "TA-NOI", "Low", "Insufficient title/abstract information for a reliable machine recommendation.", f)
        if not f["outcome"]:
            return rec("Exclude", "TA-OUT", "Medium", "No programme-relevant immunization outcome is apparent.", f)
        return rec("Unclear", "TA-NOI", "Low", "Potential relevance remains, but one or more eligibility elements are insufficiently reported.", f)
    if not f["bd"]:
        return rec("Exclude", "TA-GEO", "Medium", "Bangladesh-specific evidence is not explicit under the strict protocol profile.", f)
    if not f["child"]:
        return rec("Exclude", "TA-POP", "Medium", "An eligible child population is not explicit under the strict protocol profile.", f)
    if not f["substantive_vax"]:
        return rec("Exclude", "TA-VAX", "High", "Vaccination is incidental or not a substantive focus.", f)
    if not f["outcome"]:
        return rec("Exclude", "TA-OUT", "Medium", "No eligible coverage, timeliness, dropout, determinant, inequality, access, or programme outcome is apparent.", f)
    if not f["original"]:
        return rec("Unclear", "TA-DES", "Medium", "All core concepts are present, but eligible primary-study design is not explicit.", f)
    return rec("Include", "", "High", "All strict title/abstract eligibility signals are present.", f)

def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def write_csv(path: Path, rows: Iterable[dict[str, object]], fields: list[str] | None = None) -> None:
    rows = [dict(r) for r in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields, seen = [], set()
        for r in rows:
            for k in r:
                if k not in seen:
                    fields.append(k); seen.add(k)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def locate(root: Path, pattern: str) -> list[Path]:
    return sorted(root.rglob(pattern))

def copy_inputs(src: Path, out: Path) -> dict[str, int]:
    out.mkdir(parents=True, exist_ok=True)
    groups = {
        "calibration": locate(src, "CAL_*_Kapashia.csv"),
        "main": locate(src, "MAIN_POSTCAL_*_Kapashia.csv"),
        "supplementary": locate(src, "SUP_POSTCAL_*_Kapashia.csv"),
    }
    counts = {}
    for group, files in groups.items():
        if len(files) != 10:
            raise SystemExit(f"Expected 10 {group} source files, found {len(files)}")
        dest = out / group
        dest.mkdir(exist_ok=True)
        total = 0
        for i, p in enumerate(files, 1):
            rows = read_csv(p)
            for r in rows:
                bad = human_nonblank(r)
                if bad:
                    raise SystemExit(f"Protected human fields are nonblank in {p}: {bad[:3]}")
            target = dest / f"{group}_{i:02d}.csv"
            write_csv(target, rows)
            total += len(rows)
        counts[group] = total
    if counts != {"calibration": 1000, "main": 7433, "supplementary": 3732}:
        raise SystemExit(f"Unexpected record counts: {counts}")
    manifest = []
    for p in sorted(out.rglob("*.csv")):
        manifest.append({"file": str(p.relative_to(out)), "rows": len(read_csv(p)), "sha256": sha256(p)})
    write_csv(out / "input_manifest.csv", manifest)
    (out / "prepare_summary.json").write_text(json.dumps({**counts, "human_decisions_detected": 0}, indent=2), encoding="utf-8")
    return counts

def cmd_prepare(args: argparse.Namespace) -> None:
    copy_inputs(Path(args.input), Path(args.out))

def source_file(prepared: Path, group: str, batch: int) -> Path:
    return prepared / group / f"{group}_{batch:02d}.csv"

def cmd_screen(args: argparse.Namespace) -> None:
    prepared, out = Path(args.prepared), Path(args.out)
    group, profile, batch = args.group, args.profile, int(args.batch)
    rows = read_csv(source_file(prepared, group, batch))
    agent_name = AGENTS[profile]
    output = []
    for row in rows:
        base = dict(row)
        base["Source_Human_Reviewer_Template"] = base.get("Reviewer", "")
        base["Reviewer"] = ""
        ai = recommend(row, profile)
        base.update({"AI_Agent": agent_name, "AI_Profile": profile, **ai,
                     "AI_Provisional_Only": "Yes", "Human_Verification_Required": "Yes"})
        output.append(base)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{group}_{batch:02d}_{profile}_ai.csv"
    write_csv(target, output)
    counts = Counter(r["AI_Recommendation"] for r in output)
    (out / "summary.json").write_text(json.dumps({"group": group, "batch": batch, "profile": profile,
        "agent": agent_name, "rows": len(output), "recommendations": dict(counts),
        "human_decisions_written": 0}, indent=2), encoding="utf-8")

def find_agent_file(root: Path, group: str, batch: int, profile: str) -> Path:
    hits = list(root.rglob(f"{group}_{batch:02d}_{profile}_ai.csv"))
    if len(hits) != 1:
        raise SystemExit(f"Expected one {group} batch {batch} {profile} output, found {len(hits)}")
    return hits[0]

def cmd_compare(args: argparse.Namespace) -> None:
    inp, out = Path(args.input), Path(args.out)
    group, batch = args.group, int(args.batch)
    ka = read_csv(find_agent_file(inp, group, batch, "kapashia"))
    mi = read_csv(find_agent_file(inp, group, batch, "mizan"))
    km = {record_id(r, i): r for i, r in enumerate(ka)}
    mm = {record_id(r, i): r for i, r in enumerate(mi)}
    if set(km) != set(mm):
        raise SystemExit(f"Agent record-ID mismatch in {group} batch {batch}")
    merged = []
    for rid in km:
        k, m = km[rid], mm[rid]
        kd, md = k["AI_Recommendation"], m["AI_Recommendation"]
        agree = kd == md
        priority = "High" if (not agree or "Unclear" in (kd, md) or "Low" in (k["AI_Confidence"], m["AI_Confidence"])) else "Routine"
        base = {key: value for key, value in k.items() if not key.startswith("AI_") and key not in {"Human_Verification_Required", "Source_Human_Reviewer_Template"}}
        for field in list(base):
            if field.lower().strip() in HUMAN_FIELDS:
                base[field] = ""
        base.update({
            "Kapashia_AI_Recommendation": kd, "Kapashia_AI_Reason_Code": k["AI_Reason_Code"],
            "Kapashia_AI_Confidence": k["AI_Confidence"], "Kapashia_AI_Rationale": k["AI_Rationale"],
            "Mizan_AI_Recommendation": md, "Mizan_AI_Reason_Code": m["AI_Reason_Code"],
            "Mizan_AI_Confidence": m["AI_Confidence"], "Mizan_AI_Rationale": m["AI_Rationale"],
            "AI_Agreement": "Yes" if agree else "No",
            "AI_Consensus_Recommendation": kd if agree else "Disagreement",
            "Human_Verification_Priority": priority,
            "Human_Final_Decision": "", "Human_Final_Exclusion_Code": "", "Human_Final_Notes": "",
            "Human_Verifier": "", "Human_Verification_Date": "", "Verification_Status": "Not reviewed",
        })
        merged.append(base)
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / f"{group}_{batch:02d}_human_verification.csv", merged)
    (out / "summary.json").write_text(json.dumps({"group": group, "batch": batch, "rows": len(merged),
        "agreements": sum(r["AI_Agreement"] == "Yes" for r in merged),
        "disagreements": sum(r["AI_Agreement"] == "No" for r in merged),
        "high_priority_human_checks": sum(r["Human_Verification_Priority"] == "High" for r in merged),
        "human_decisions_written": 0}, indent=2), encoding="utf-8")

def cmd_gate(args: argparse.Namespace) -> None:
    inp, out = Path(args.input), Path(args.out)
    expected = {"calibration": 1000, "main": 7433, "supplementary": 3732}
    groups = args.groups.split(",")
    out.mkdir(parents=True, exist_ok=True)
    summary = {}
    for group in groups:
        files = sorted(inp.rglob(f"{group}_*_human_verification.csv"))
        if len(files) != 10:
            raise SystemExit(f"Expected 10 comparison files for {group}, found {len(files)}")
        rows = []
        for p in files:
            rows.extend(read_csv(p))
        if len(rows) != expected[group]:
            raise SystemExit(f"Expected {expected[group]} {group} rows, found {len(rows)}")
        ids = [record_id(r, i) for i, r in enumerate(rows)]
        if len(ids) != len(set(ids)):
            raise SystemExit(f"Duplicate IDs in {group} verification output")
        for r in rows:
            if r.get("Human_Final_Decision", "").strip() or r.get("Human_Final_Exclusion_Code", "").strip():
                raise SystemExit(f"Human final field populated in {group}")
        write_csv(out / f"{group}_dual_ai_human_verification_master.csv", rows)
        summary[group] = {"rows": len(rows), "agreement": sum(r["AI_Agreement"] == "Yes" for r in rows),
            "disagreement": sum(r["AI_Agreement"] == "No" for r in rows),
            "high_priority": sum(r["Human_Verification_Priority"] == "High" for r in rows)}
    (out / "gate_summary.json").write_text(json.dumps({"status": "PASS", "groups": summary,
        "human_decisions_written": 0}, indent=2), encoding="utf-8")

def cmd_consolidate(args: argparse.Namespace) -> None:
    inp, out = Path(args.input), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    masters = {}
    for group in ("calibration", "main", "supplementary"):
        hits = list(inp.rglob(f"{group}_dual_ai_human_verification_master.csv"))
        if len(hits) != 1:
            raise SystemExit(f"Missing unique {group} master: {len(hits)}")
        target = out / f"{group}_dual_ai_human_verification_master.csv"
        shutil.copy2(hits[0], target)
        rows = read_csv(target)
        masters[group] = {"rows": len(rows), "agreement": sum(r["AI_Agreement"] == "Yes" for r in rows),
            "disagreement": sum(r["AI_Agreement"] == "No" for r in rows),
            "high_priority": sum(r["Human_Verification_Priority"] == "High" for r in rows)}
    manifest = []
    for p in sorted(out.glob("*.csv")):
        manifest.append({"file": p.name, "rows": len(read_csv(p)), "sha256": sha256(p)})
    write_csv(out / "artifact_manifest.csv", manifest)
    final = {"pipeline": "SAMA dual AI-agent provisional title-abstract screening",
        "agents": list(AGENTS.values()),
        "status": "PROVISIONAL_AI_SCREENING_READY_FOR_SINGLE_HUMAN_VERIFICATION",
        "groups": masters, "human_title_abstract_decisions_completed": 0,
        "reporting_rule": "AI recommendations must not be reported as independent human screening. Human verification is required for every record."}
    (out / "final_summary.json").write_text(json.dumps(final, indent=2), encoding="utf-8")
    (out / "README.md").write_text(
        "# Dual AI-agent provisional screening package\n\n"
        "Kapashia_AI_Agent and Mizan_AI_Agent independently generated provisional title/abstract recommendations.\n"
        "The outputs are decision support only. Human_Final_Decision fields are blank and must be completed by the named human reviewers.\n"
        "These AI outputs cannot be reported as independent human screening in the manuscript.\n", encoding="utf-8")

def main() -> None:
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare"); p.add_argument("--input", required=True); p.add_argument("--out", required=True); p.set_defaults(func=cmd_prepare)
    p = sub.add_parser("screen"); p.add_argument("--prepared", required=True); p.add_argument("--out", required=True); p.add_argument("--group", choices=["calibration", "main", "supplementary"], required=True); p.add_argument("--profile", choices=["kapashia", "mizan"], required=True); p.add_argument("--batch", required=True); p.set_defaults(func=cmd_screen)
    p = sub.add_parser("compare"); p.add_argument("--input", required=True); p.add_argument("--out", required=True); p.add_argument("--group", choices=["calibration", "main", "supplementary"], required=True); p.add_argument("--batch", required=True); p.set_defaults(func=cmd_compare)
    p = sub.add_parser("gate"); p.add_argument("--input", required=True); p.add_argument("--out", required=True); p.add_argument("--groups", required=True); p.set_defaults(func=cmd_gate)
    p = sub.add_parser("consolidate"); p.add_argument("--input", required=True); p.add_argument("--out", required=True); p.set_defaults(func=cmd_consolidate)
    args = ap.parse_args(); args.func(args)

if __name__ == "__main__":
    main()
