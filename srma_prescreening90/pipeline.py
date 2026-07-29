#!/usr/bin/env python3
"""Three-gate pre-screening readiness pipeline for the SAMA Bangladesh review.

This program prepares operational and audit materials only. It never fills human
screening, adjudication, full-text eligibility, extraction, RoB, or GRADE fields.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Iterable

DECISION_TOKENS = (
    "decision", "eligibility", "include_exclude", "screening_result",
    "adjudication", "rob_judgement", "grade_judgement"
)
REVIEWERS = ("Md. Mizanoor Rahman", "Kapashia Binte Giash")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: Iterable[dict], fields: list[str] | None = None) -> None:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = []
        seen = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    fields.append(key); seen.add(key)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


def all_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


def locate(root: Path, name: str) -> Path | None:
    hits = list(root.rglob(name))
    return hits[0] if hits else None


def nonblank_decisions(rows: list[dict[str, str]]) -> int:
    count = 0
    for row in rows:
        for key, value in row.items():
            kl = key.lower().strip()
            if any(t in kl for t in DECISION_TOKENS) and str(value or "").strip():
                count += 1
    return count


def csv_profile(path: Path) -> dict:
    try:
        rows = read_csv(path)
        cols = list(rows[0].keys()) if rows else []
        return {
            "path": str(path), "rows": len(rows), "columns": len(cols),
            "decision_cells_nonblank": nonblank_decisions(rows),
            "sha256": sha256(path), "read_error": ""
        }
    except Exception as exc:  # audit should record, not hide, malformed files
        return {
            "path": str(path), "rows": -1, "columns": -1,
            "decision_cells_nonblank": -1, "sha256": sha256(path),
            "read_error": type(exc).__name__ + ": " + str(exc)
        }


def split_even(rows: list[dict], n: int) -> list[list[dict]]:
    return [rows[i::n] for i in range(n)]


def cmd_prepare(args: argparse.Namespace) -> None:
    src, out = Path(args.input), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    files = all_files(src)
    manifest = []
    for p in files:
        rel = p.relative_to(src)
        entry = {"relative_path": str(rel), "bytes": p.stat().st_size, "sha256": sha256(p)}
        if p.suffix.lower() == ".csv": entry.update(csv_profile(p))
        else: entry.update({"rows": "", "columns": "", "decision_cells_nonblank": "", "read_error": ""})
        manifest.append(entry)
    write_csv(out / "source_freeze_manifest.csv", manifest)

    fulltext = locate(src, "fulltext_master.csv")
    fulltext_rows = read_csv(fulltext) if fulltext else []
    duplicates = locate(src, "duplicates_master.csv")
    duplicate_rows = read_csv(duplicates) if duplicates else []

    reviewer_files = []
    for p in files:
        if p.suffix.lower() != ".csv":
            continue
        low = str(p).lower()
        reviewer = ""
        if "mizan" in low or "mizanoor" in low: reviewer = REVIEWERS[0]
        elif "kapashia" in low: reviewer = REVIEWERS[1]
        elif "reviewer" in low or "screen" in low or "calibration" in low:
            reviewer = "Unassigned/administrative"
        if reviewer:
            prof = csv_profile(p)
            reviewer_files.append({
                "reviewer": reviewer, "relative_path": str(p.relative_to(src)),
                "rows": prof["rows"], "decision_cells_nonblank": prof["decision_cells_nonblank"],
                "sha256": prof["sha256"]
            })
    write_csv(out / "reviewer_file_inventory.csv", reviewer_files)
    write_csv(out / "fulltext_seed.csv", fulltext_rows)
    write_csv(out / "duplicate_seed.csv", duplicate_rows)

    tasks1 = []
    for a in range(1, 31):
        stream = "freeze_integrity" if a <= 10 else "eligibility_taxonomy" if a <= 20 else "calibration_launch_readiness"
        tasks1.append({"agent": a, "stream": stream})
    tasks2 = []
    for a in range(1, 31):
        stream = "duplicate_pdf_linkage" if a <= 15 else "route_status_and_fulltext_schedule"
        tasks2.append({"agent": a, "stream": stream})
    tasks3 = []
    for a in range(1, 31):
        stream = "mizan_launch_qa" if a <= 10 else "kapashia_launch_qa" if a <= 20 else "reconciliation_launch_control"
        tasks3.append({"agent": a, "stream": stream})
    write_csv(out / "phase1_tasks.csv", tasks1)
    write_csv(out / "phase2_tasks.csv", tasks2)
    write_csv(out / "phase3_tasks.csv", tasks3)

    summary = {
        "files_frozen": len(files), "csv_files": sum(p.suffix.lower()==".csv" for p in files),
        "fulltext_seed_rows": len(fulltext_rows), "duplicate_seed_rows": len(duplicate_rows),
        "reviewer_files": len(reviewer_files),
        "human_screening_decisions_recorded": 0,
        "governance": "All outputs are pre-screening operational materials only."
    }
    (out / "prepare_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


def task_for(prepared: Path, phase: int, agent: int) -> str:
    rows = read_csv(prepared / f"phase{phase}_tasks.csv")
    return next(r["stream"] for r in rows if int(r["agent"]) == agent)


def cmd_phase1(args: argparse.Namespace) -> None:
    prepared, out, agent = Path(args.input), Path(args.out), int(args.agent)
    out.mkdir(parents=True, exist_ok=True)
    stream = task_for(prepared, 1, agent)
    if stream == "freeze_integrity":
        rows = read_csv(prepared / "source_freeze_manifest.csv")
        shard = split_even(rows, 10)[agent - 1]
        result = []
        for r in shard:
            ok = not r.get("read_error") and str(r.get("decision_cells_nonblank", "0")) in ("", "0")
            result.append({"agent": agent, "file": r["relative_path"], "sha256": r["sha256"],
                           "freeze_status": "PASS" if ok else "REVIEW", "human_decision_added": ""})
        write_csv(out / f"freeze_integrity_{agent:02d}.csv", result)
    elif stream == "eligibility_taxonomy":
        codes = [
            ("E01", "Not conducted in Bangladesh"), ("E02", "Population outside childhood immunization scope"),
            ("E03", "No eligible immunization outcome"), ("E04", "Ineligible publication or study design"),
            ("E05", "Duplicate report of same study"), ("E06", "Protocol/editorial/commentary only"),
            ("E07", "Insufficient title/abstract information; retain as Unclear"),
            ("E08", "Non-human or laboratory study"), ("E09", "Outside prespecified date/language rule"),
            ("E10", "Other protocol-defined reason; explanation required")
        ]
        subset = [codes[(agent - 11) % len(codes)]]
        result = [{"agent": agent, "candidate_code": c, "candidate_definition": d,
                   "protocol_confirmation_required": "Yes", "approved_by_human_reviewer": ""} for c,d in subset]
        write_csv(out / f"taxonomy_{agent:02d}.csv", result)
    else:
        inv = read_csv(prepared / "reviewer_file_inventory.csv")
        shard = split_even(inv, 10)[agent - 21]
        result = []
        for r in shard:
            blank = str(r.get("decision_cells_nonblank", "0")) == "0"
            result.append({"agent": agent, "reviewer": r["reviewer"], "file": r["relative_path"],
                           "rows": r["rows"], "blank_decision_check": "PASS" if blank else "FAIL",
                           "launch_authorized_by_reviewer": ""})
        write_csv(out / f"calibration_readiness_{agent:02d}.csv", result)
    (out / "summary.json").write_text(json.dumps({"agent": agent, "stream": stream}, indent=2), encoding="utf-8")


def gather_csv(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.csv") if "tasks" not in p.name)


def cmd_gate1(args: argparse.Namespace) -> None:
    inp, out = Path(args.input), Path(args.out); out.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in gather_csv(inp):
        rows.extend(read_csv(p))
    failures = sum(1 for r in rows if any(str(v).upper() == "FAIL" for v in r.values()))
    if failures:
        raise SystemExit(f"Gate 1 failed: {failures} explicit failures")
    write_csv(out / "gate1_audit_master.csv", rows)
    (out / "gate1_summary.json").write_text(json.dumps({"gate": 1, "status": "PASS", "rows": len(rows),
        "human_decisions_recorded": 0}, indent=2), encoding="utf-8")


def choose_id(row: dict[str, str], idx: int) -> str:
    for key in ("record_id", "source_record_id", "id", "openalex_id", "doi", "title"):
        if row.get(key): return row[key]
    return f"ROW-{idx+1:06d}"


def cmd_phase2(args: argparse.Namespace) -> None:
    prepared, out, agent = Path(args.prepared), Path(args.out), int(args.agent)
    out.mkdir(parents=True, exist_ok=True)
    stream = task_for(prepared, 2, agent)
    ft = read_csv(prepared / "fulltext_seed.csv")
    dups = read_csv(prepared / "duplicate_seed.csv")
    if stream == "duplicate_pdf_linkage":
        shard = split_even(ft, 15)[agent - 1]
        dup_text = " ".join(" ".join(r.values()) for r in dups).lower()
        result = []
        for i, r in enumerate(shard):
            rid = choose_id(r, i)
            text = " ".join(str(v) for v in r.values()).lower()
            result.append({"agent": agent, "record_id": rid,
                "machine_duplicate_evidence_present": "Yes" if rid.lower() in dup_text else "No/unknown",
                "pdf_or_route_signal_present": "Yes" if any(x in text for x in ("http", "pdf", "pmc", "doi")) else "No/unknown",
                "human_duplicate_adjudication": "", "human_fulltext_verification": ""})
        write_csv(out / f"duplicate_pdf_linkage_{agent:02d}.csv", result)
    else:
        shard = split_even(ft, 15)[agent - 16]
        result = []
        for i, r in enumerate(shard):
            text = " ".join(str(v) for v in r.values()).lower()
            status = "route_or_pdf_signal" if any(x in text for x in ("http", "pdf", "pmc", "doi")) else "manual_route_search_required"
            result.append({"agent": agent, "record_id": choose_id(r, i), "operational_status": status,
                           "proposed_queue": "Full-text readiness", "formal_fulltext_decision": ""})
        write_csv(out / f"fulltext_schedule_{agent:02d}.csv", result)
    (out / "summary.json").write_text(json.dumps({"agent": agent, "stream": stream}, indent=2), encoding="utf-8")


def cmd_gate2(args: argparse.Namespace) -> None:
    inp, out = Path(args.input), Path(args.out); out.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in gather_csv(inp): rows.extend(read_csv(p))
    if nonblank_decisions(rows):
        raise SystemExit("Gate 2 failed: a protected human-decision field is nonblank")
    write_csv(out / "gate2_linkage_schedule_master.csv", rows)
    (out / "gate2_summary.json").write_text(json.dumps({"gate": 2, "status": "PASS", "rows": len(rows),
        "human_decisions_recorded": 0}, indent=2), encoding="utf-8")


def cmd_phase3(args: argparse.Namespace) -> None:
    prepared, out, agent = Path(args.prepared), Path(args.out), int(args.agent)
    out.mkdir(parents=True, exist_ok=True)
    stream = task_for(prepared, 3, agent)
    inv = read_csv(prepared / "reviewer_file_inventory.csv")
    reviewer = REVIEWERS[0] if stream == "mizan_launch_qa" else REVIEWERS[1] if stream == "kapashia_launch_qa" else "Adjudication administrator"
    selected = [r for r in inv if reviewer.split()[0].lower() in r.get("reviewer", "").lower()]
    if not selected: selected = inv
    shard_idx = (agent - 1) % 10
    shard = split_even(selected, 10)[shard_idx] if selected else []
    result = []
    for batch_no, r in enumerate(shard, 1):
        result.append({"agent": agent, "stream": stream, "reviewer": reviewer,
                       "batch_file": r["relative_path"], "expected_rows": r["rows"],
                       "checksum": r["sha256"], "launch_status": "READY_FOR_HUMAN_REVIEW",
                       "reviewer_decision": "", "adjudication_decision": ""})
    if not result:
        result.append({"agent": agent, "stream": stream, "reviewer": reviewer,
                       "batch_file": "TO_BE_ASSIGNED", "expected_rows": "", "checksum": "",
                       "launch_status": "REQUIRES_ASSIGNMENT", "reviewer_decision": "", "adjudication_decision": ""})
    write_csv(out / f"launch_control_{agent:02d}.csv", result)
    (out / "summary.json").write_text(json.dumps({"agent": agent, "stream": stream}, indent=2), encoding="utf-8")


def cmd_consolidate(args: argparse.Namespace) -> None:
    inp, out = Path(args.input), Path(args.out); out.mkdir(parents=True, exist_ok=True)
    p1 = list((inp / "gate1").rglob("gate1_audit_master.csv"))
    p2 = list((inp / "gate2").rglob("gate2_linkage_schedule_master.csv"))
    p3_files = sorted((inp / "phase3").rglob("launch_control_*.csv"))
    if not p1 or not p2 or len(p3_files) != 30:
        raise SystemExit(f"Missing gate outputs: gate1={len(p1)}, gate2={len(p2)}, phase3={len(p3_files)}")
    launch_rows = []
    for p in p3_files: launch_rows.extend(read_csv(p))
    if nonblank_decisions(launch_rows):
        raise SystemExit("Final gate failed: protected decision fields are nonblank")
    shutil.copy2(p1[0], out / "gate1_data_freeze_and_taxonomy_audit.csv")
    shutil.copy2(p2[0], out / "gate2_duplicate_pdf_and_fulltext_schedule.csv")
    write_csv(out / "gate3_reviewer_launch_control.csv", launch_rows)
    summary = {
        "pipeline": "SRMA 90-Agent Three-Gate Pre-Screening Readiness",
        "gate1_agents": 30, "gate2_agents": 30, "gate3_agents": 30,
        "status": "PRE_SCREENING_OPERATIONAL_PACKAGE_READY",
        "reviewers": list(REVIEWERS), "human_title_abstract_screening_decisions": 0,
        "human_duplicate_adjudications": 0, "formal_fulltext_decisions": 0,
        "extraction_completed": 0, "risk_of_bias_completed": 0,
        "next_human_action": "Review and approve the eligibility taxonomy, then complete blinded calibration batches."
    }
    (out / "final_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out / "README.md").write_text(
        "# Three-gate pre-screening readiness package\n\n"
        "Gate 1 freezes and audits sources, candidate exclusion codes, and calibration files.\n"
        "Gate 2 prepares duplicate/PDF linkage and full-text scheduling evidence.\n"
        "Gate 3 prepares reviewer launch and reconciliation controls.\n\n"
        "No human screening or eligibility decision has been completed by this workflow.\n",
        encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare"); p.add_argument("--input", required=True); p.add_argument("--out", required=True); p.set_defaults(func=cmd_prepare)
    p = sub.add_parser("phase1-agent"); p.add_argument("--agent", required=True); p.add_argument("--input", required=True); p.add_argument("--out", required=True); p.set_defaults(func=cmd_phase1)
    p = sub.add_parser("gate1"); p.add_argument("--input", required=True); p.add_argument("--out", required=True); p.set_defaults(func=cmd_gate1)
    p = sub.add_parser("phase2-agent"); p.add_argument("--agent", required=True); p.add_argument("--prepared", required=True); p.add_argument("--gate1", required=True); p.add_argument("--out", required=True); p.set_defaults(func=cmd_phase2)
    p = sub.add_parser("gate2"); p.add_argument("--input", required=True); p.add_argument("--out", required=True); p.set_defaults(func=cmd_gate2)
    p = sub.add_parser("phase3-agent"); p.add_argument("--agent", required=True); p.add_argument("--prepared", required=True); p.add_argument("--gate2", required=True); p.add_argument("--out", required=True); p.set_defaults(func=cmd_phase3)
    p = sub.add_parser("consolidate"); p.add_argument("--input", required=True); p.add_argument("--out", required=True); p.set_defaults(func=cmd_consolidate)
    args = ap.parse_args(); args.func(args)

if __name__ == "__main__": main()
