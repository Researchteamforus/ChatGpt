#!/usr/bin/env python3
from __future__ import annotations
import csv, json
from pathlib import Path

GROUPS = ("calibration", "main", "supplementary")
REVIEWERS = {
    "Kapashia": "Kapashia Binte Giash",
    "Mizan": "Md. Mizanoor Rahman",
}

def read_csv(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def write_csv(path: Path, rows):
    rows = list(rows); path.parent.mkdir(parents=True, exist_ok=True)
    fields=[]; seen=set()
    for r in rows:
        for k in r:
            if k not in seen: fields.append(k); seen.add(k)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w=csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)

def build_for(row, label):
    other = "Mizan" if label == "Kapashia" else "Kapashia"
    out = {}
    for k,v in row.items():
        if k.startswith(other + "_AI_"):
            continue
        if k in {"AI_Agreement", "AI_Consensus_Recommendation", "Human_Verification_Priority",
                 "Human_Final_Decision", "Human_Final_Exclusion_Code", "Human_Final_Notes",
                 "Human_Verifier", "Human_Verification_Date", "Verification_Status"}:
            continue
        out[k]=v
    out["Assigned_Human_Reviewer"] = REVIEWERS[label]
    out["AI_Suggestion_Is_Provisional"] = "Yes"
    out["Human_Decision"] = ""
    out["Human_Exclusion_Code"] = ""
    out["Human_Notes"] = ""
    out["Human_Review_Date"] = ""
    out["Human_Verification_Status"] = "Not reviewed"
    return out

def main():
    import argparse
    ap=argparse.ArgumentParser(); ap.add_argument("--package", required=True); args=ap.parse_args()
    root=Path(args.package)
    summary={}
    for group in GROUPS:
        src=root/f"{group}_dual_ai_human_verification_master.csv"
        rows=read_csv(src)
        for label in REVIEWERS:
            blinded=[build_for(r,label) for r in rows]
            write_csv(root/f"{group}_{label}_blinded_human_verification.csv", blinded)
        summary[group]={"rows_per_reviewer":len(rows),"reviewers":list(REVIEWERS.values())}
    (root/"blinded_verification_summary.json").write_text(json.dumps({
        "status":"BLINDED_SINGLE_PASS_HUMAN_VERIFICATION_FILES_READY",
        "groups":summary,
        "instruction":"Each human reviewer must verify every assigned record once without opening the other reviewer's file."
    },indent=2),encoding="utf-8")

if __name__ == "__main__": main()
