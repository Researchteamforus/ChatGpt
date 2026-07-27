#!/usr/bin/env python3
"""Protocol-grounded title/abstract triage for the SRMA Bangladesh review.

This is machine-assisted prioritisation only. Outputs MUST NOT be represented as
independent human screening or final eligibility decisions. Human reviewers must
complete the corresponding reviewer decision fields while blinded.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path
from typing import Dict, List, Tuple

PROSPERO = "CRD420261461557"
N_WORKERS = 30

FIELDS = [
    "Order", "Batch_ID", "Master_Record_ID", "Sources", "Title", "Abstract", "Year",
    "DOI", "PMID", "Document_Type", "Machine_Triage", "Machine_Primary_Reason",
    "Machine_Confidence", "Machine_Evidence_Snippet", "Needs_Full_Text",
    "Human_R1_Decision", "Human_R1_Approval", "Human_R1_Primary_Reason", "Human_R1_Notes"
]

COUNTRY = re.compile(r"\bbangladesh(?:i)?\b|\bdhaka\b|\bcox'?s bazar\b|\bsylhet\b|\bchattogram\b|\bchittagong\b|\bbarishal\b|\brangpur\b|\bmymensingh\b|\brajshahi\b|\bkhulna\b|\bhaor\b|\brohingya\b", re.I)
CHILD = re.compile(r"\bchild(?:ren|hood)?\b|\binfant(?:s)?\b|\bunder[- ]?five\b|\b12\s*[–-]\s*23\s*month|\bnewborn|\btoddler|\bcaregiver|\bparent", re.I)
ROUTINE = re.compile(r"routine immuni[sz]ation|expanded program(?:me)? on immuni[sz]ation|\bEPI\b|vaccination coverage|immuni[sz]ation coverage|fully immuni[sz]ed|full(?:y)? vaccin|complete immuni[sz]ation|basic vaccination|zero[- ]dose|under[- ]immuni[sz]ed|incomplete immuni[sz]ation|dropout|timeliness|timely vaccin|delayed vaccin|missed opportunit|defaulter|service delivery|vaccination uptake|immuni[sz]ation uptake", re.I)
OUTCOME = re.compile(r"coverage|uptake|timeliness|timely|delay|invalid dose|dropout|zero[- ]dose|under[- ]immuni[sz]|incomplete|partially vaccin|missed opportunit|barrier|determinant|factor|inequalit|inequit|access|service readiness|service delivery|outreach|stock[- ]?out|defaulter|reminder|knowledge|attitude|practice", re.I)

WRONG_VACCINE = re.compile(r"\bCOVID[- ]?19\b|coronavirus|SARS[- ]?CoV|\bHPV\b|human papillomavirus|rabies|dog vaccination|canine|maternal vaccin|pregnan(?:cy|t)|adult vaccination|influenza vaccination in (?:adult|elderly)|typhoid conjugate vaccine trial|cholera vaccine trial|Japanese encephalitis vaccine policy", re.I)
LAB_ONLY = re.compile(r"immunogenicity|seroprevalence|serology|antibod(?:y|ies)|immune response|efficacy trial|vaccine efficacy|safety trial|adverse event|laboratory|molecular detection|genetic diversity|biological response|dose[- ]finding", re.I)
NONEMPIRICAL = re.compile(r"systematic review|scoping review|narrative review|review article|protocol|editorial|commentary|opinion|perspective|news report|letter to the editor|conference abstract", re.I)
MODELLING = re.compile(r"mathematical model|modelling study|modeling study|simulation|cost[- ]effectiveness|economic evaluation|deaths averted|optimal vaccine policy", re.I)
OUTBREAK_ONLY = re.compile(r"outbreak investigation|outbreak response|mass campaign|supplementary immuni[sz]ation|catch[- ]up campaign|measles outbreak|diphtheria outbreak", re.I)
MULTICOUNTRY = re.compile(r"multi[- ]?countr|countries|south asia|south and southeast asia|global|worldwide|low[- ] and middle[- ]income countries", re.I)
PRIMARY = re.compile(r"cross[- ]sectional|survey|cohort|case[- ]control|qualitative|mixed[- ]methods|randomi[sz]ed|quasi[- ]experimental|before[- ]and[- ]after|programme evaluation|program evaluation|secondary analysis|demographic and health survey|multiple indicator cluster survey|facility assessment|surveillance analysis", re.I)


def norm(v: object) -> str:
    return re.sub(r"\s+", " ", "" if v is None else str(v)).strip()


def find_master(root: Path) -> Path:
    candidates = list(root.rglob("exact_deduplicated_master.csv"))
    if not candidates:
        candidates = list(root.rglob("*deduplicated*master*.csv"))
    if not candidates:
        raise FileNotFoundError("No exact-deduplicated master CSV found in downloaded search artifact")
    return sorted(candidates, key=lambda p: p.stat().st_size, reverse=True)[0]


def evidence(text: str, patterns: List[re.Pattern], limit: int = 360) -> str:
    for pat in patterns:
        m = pat.search(text)
        if m:
            start = max(0, m.start() - 90); end = min(len(text), m.end() + 220)
            return text[start:end][:limit]
    return text[:limit]


def classify(title: str, abstract: str, doc_type: str) -> Tuple[str, str, int, str, str]:
    t = norm(title); a = norm(abstract); d = norm(doc_type)
    blob = f"{t}. {a}. {d}"
    has_country = bool(COUNTRY.search(blob))
    has_child = bool(CHILD.search(blob))
    has_routine = bool(ROUTINE.search(blob))
    has_outcome = bool(OUTCOME.search(blob))
    has_primary = bool(PRIMARY.search(blob))
    abstract_missing = len(a) < 40

    # High-specificity protocol exclusions.
    if WRONG_VACCINE.search(blob) and not has_routine:
        return "Likely exclude", "Non-routine, adult, adolescent, maternal, animal, or unrelated vaccine scope", 96, evidence(blob, [WRONG_VACCINE]), "No"
    if LAB_ONLY.search(blob) and not has_outcome:
        return "Likely exclude", "Clinical efficacy, immunogenicity, laboratory, dosing, or safety evidence without eligible routine-immunization outcome", 95, evidence(blob, [LAB_ONLY]), "No"
    if NONEMPIRICAL.search(blob) and not has_primary:
        return "Likely exclude", "Ineligible publication type without clear primary empirical data", 94, evidence(blob, [NONEMPIRICAL]), "No"
    if MODELLING.search(blob) and not has_primary and not has_outcome:
        return "Likely exclude", "Modelling or simulated evidence without separately extractable empirical routine-immunization findings", 93, evidence(blob, [MODELLING]), "No"
    if OUTBREAK_ONLY.search(blob) and not has_routine:
        return "Likely exclude", "Outbreak-response or supplementary campaign evidence without eligible routine-immunization outcome", 92, evidence(blob, [OUTBREAK_ONLY]), "No"
    if not has_country and len(blob) > 140:
        return "Likely exclude", "No Bangladesh-specific evidence identified in title or abstract", 90, evidence(blob, [COUNTRY]), "No"
    if MULTICOUNTRY.search(blob) and has_country and not re.search(r"Bangladesh.{0,180}(coverage|estimate|result|odds|prevalence|percentage|factor)", blob, re.I | re.S):
        return "Unclear", "Multicountry report; Bangladesh-specific results require verification", 72, evidence(blob, [MULTICOUNTRY, COUNTRY]), "Yes"

    # High-specificity likely inclusion.
    if has_country and has_routine and has_outcome and (has_child or re.search(r"caregiver|parent|health worker|facility|programme|program", blob, re.I)):
        conf = 96 if has_primary else 88
        reason = "Bangladesh-specific routine childhood immunization population/process and eligible outcome are explicit"
        return "Likely include", reason, conf, evidence(blob, [ROUTINE, OUTCOME, COUNTRY]), "No" if has_primary else "Yes"
    if has_country and has_child and has_outcome and re.search(r"vaccin|immuni[sz]", blob, re.I):
        return "Likely include", "Bangladesh childhood vaccination evidence with an eligible coverage, timeliness, dropout, zero-dose, service, equity, or determinant outcome", 88, evidence(blob, [OUTCOME, COUNTRY]), "Yes" if abstract_missing else "No"

    # Conservative unresolved states.
    if abstract_missing:
        return "Unclear", "Abstract missing or too limited for safe title-only decision", 55, t[:360], "Yes"
    if has_country and re.search(r"vaccin|immuni[sz]", blob, re.I):
        return "Unclear", "Bangladesh vaccination record but routine-childhood outcome or eligible study design is not sufficiently clear", 65, evidence(blob, [COUNTRY, ROUTINE, OUTCOME]), "Yes"
    return "Likely exclude", "No clear Bangladesh-specific routine childhood immunization relevance", 82, evidence(blob, [COUNTRY, ROUTINE, OUTCOME]), "No"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", type=int, required=True)
    ap.add_argument("--input", default="search_input")
    ap.add_argument("--out", default="screen_outputs")
    args = ap.parse_args()
    if not (1 <= args.worker <= N_WORKERS):
        raise SystemExit("worker must be 1..30")

    master = find_master(Path(args.input))
    with master.open(encoding="utf-8-sig", newline="") as f:
        records = list(csv.DictReader(f))
    records.sort(key=lambda r: (norm(r.get("Record_ID") or r.get("Master_Record_ID")), norm(r.get("Title"))))
    n = len(records); size = math.ceil(n / N_WORKERS)
    start = (args.worker - 1) * size; end = min(n, start + size)
    batch = records[start:end]

    out_dir = Path(args.out) / f"worker_{args.worker:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    output: List[Dict[str, str]] = []
    counts = {"Likely include": 0, "Likely exclude": 0, "Unclear": 0}
    for local_i, r in enumerate(batch, start=start + 1):
        title = norm(r.get("Title")); abstract = norm(r.get("Abstract")); doc = norm(r.get("Document_Type"))
        triage, reason, conf, snippet, needs_ft = classify(title, abstract, doc)
        counts[triage] += 1
        output.append({
            "Order": str(local_i),
            "Batch_ID": f"SCREEN-{args.worker:02d}",
            "Master_Record_ID": norm(r.get("Record_ID") or r.get("Master_Record_ID")),
            "Sources": norm(r.get("Sources") or r.get("Source")),
            "Title": title, "Abstract": abstract, "Year": norm(r.get("Year")),
            "DOI": norm(r.get("DOI")), "PMID": norm(r.get("PMID")), "Document_Type": doc,
            "Machine_Triage": triage, "Machine_Primary_Reason": reason,
            "Machine_Confidence": str(conf), "Machine_Evidence_Snippet": snippet,
            "Needs_Full_Text": needs_ft, "Human_R1_Decision": "", "Human_R1_Approval": "Not reviewed",
            "Human_R1_Primary_Reason": "", "Human_R1_Notes": ""
        })
    csv_path = out_dir / f"screening_batch_{args.worker:02d}.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader(); w.writerows(output)
    log = {
        "prospero": PROSPERO, "worker": args.worker, "total_master_records": n,
        "start_index": start + 1 if batch else None, "end_index": end if batch else None,
        "records_triaged": len(batch), "counts": counts,
        "governance": "Machine-assisted provisional triage only; not independent human screening or final eligibility."
    }
    (out_dir / "summary.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
    print(json.dumps(log))

if __name__ == "__main__":
    main()
