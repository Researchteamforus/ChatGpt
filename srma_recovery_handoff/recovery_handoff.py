#!/usr/bin/env python3
"""Recover partial SRMA artifacts and prepare auditable reviewer handoff.

This script never creates human screening, eligibility, extraction, RoB, or GRADE
judgements. It inventories files, validates blank reviewer materials, creates
future-ingestion rules, and records pending dependencies.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, re, shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

PROSPERO = "CRD420261461557"
AGENTS = 10


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def nt(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str] | None = None) -> None:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or (list(rows[0]) if rows else [])
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if fields:
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in fields})


def rr(items: list[Any], n: int) -> list[list[Any]]:
    out = [[] for _ in range(n)]
    for i, item in enumerate(items):
        out[i % n].append(item)
    return out


def files(root: Path) -> list[Path]:
    allowed = {".csv", ".json", ".md", ".txt", ".r", ".py"}
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in allowed)


def inventory(path: Path, base: Path, source: str) -> dict[str, Any]:
    count: Any = ""
    headers = ""
    status = "not_applicable"
    if path.suffix.lower() == ".csv":
        rows = read_csv(path)
        count = len(rows)
        headers = " | ".join(rows[0].keys()) if rows else ""
        status = "parsed" if rows else "empty_or_header_only"
    elif path.suffix.lower() == ".json":
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            count = len(obj) if isinstance(obj, (list, dict)) else 1
            status = "parsed"
        except Exception:
            status = "parse_error"
    return {
        "Source_Workstream": source,
        "Relative_Path": str(path.relative_to(base)),
        "Filename": path.name,
        "Bytes": path.stat().st_size,
        "SHA256": sha256(path),
        "Row_or_Item_Count": count,
        "Headers": headers,
        "Parse_Status": status,
    }


def prepare(args: argparse.Namespace) -> None:
    roots = {
        "Final160": Path(args.final160),
        "Screen40": Path(args.screen40),
        "Ready100": Path(args.ready100),
        "Partial150": Path(args.partial150),
        "Partial60": Path(args.partial60),
    }
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    inv: list[dict[str, Any]] = []
    for label, root in roots.items():
        for p in files(root):
            inv.append(inventory(p, root, label))
    for i, shard in enumerate(rr(inv, AGENTS), 1):
        write_csv(out / "recovery_shards" / f"inventory_{i:02d}.csv", shard)

    handoff = []
    for p in roots["Screen40"].rglob("*.csv"):
        low = p.name.lower()
        if any(k in low for k in ("mizan", "kapashia", "screen", "calibration", "reconciliation", "adjudication", "review")):
            rows = read_csv(p)
            handoff.append({
                "Absolute_Path": str(p),
                "Relative_Path": str(p.relative_to(roots["Screen40"])),
                "Filename": p.name,
                "Rows": len(rows),
                "Headers": " | ".join(rows[0].keys()) if rows else "",
                "SHA256": sha256(p),
            })
    for i, shard in enumerate(rr(handoff, AGENTS), 1):
        write_csv(out / "handoff_shards" / f"handoff_{i:02d}.csv", shard)

    ft_rows: list[dict[str, Any]] = []
    seen = set()
    for source in ("Final160", "Partial150"):
        root = roots[source]
        for p in root.rglob("*.csv"):
            if not any(k in p.name.lower() for k in ("fulltext", "full_text", "retrieval_manifest", "evidence")):
                continue
            for r in read_csv(p):
                rid = nt(r.get("Integrated_ID") or r.get("Record_ID") or r.get("Master_Record_ID"))
                key = (rid, nt(r.get("DOI")), nt(r.get("Title")))
                if not any(key) or key in seen:
                    continue
                seen.add(key)
                ft_rows.append({
                    "Source": source,
                    "Source_File": str(p.relative_to(root)),
                    "Record_ID": rid,
                    "Title": nt(r.get("Title") or r.get("Enriched_Title")),
                    "DOI": nt(r.get("DOI")),
                    "PMID": nt(r.get("PMID")),
                    "PDF_Retrieved": nt(r.get("PDF_Retrieved") or r.get("Retrieval_Status")),
                    "Machine_Evidence_Status": nt(r.get("Machine_Evidence_Status") or r.get("Machine_Priority")),
                    "Human_Full_Text_Decision": "",
                    "Primary_Exclusion_Reason": "",
                    "Reviewer": "",
                    "Review_Date": "",
                    "Reviewer_Notes": "",
                })
    for i, shard in enumerate(rr(ft_rows, AGENTS), 1):
        write_csv(out / "fulltext_shards" / f"fulltext_{i:02d}.csv", shard)

    stages = [
        {"Stage": "Search and identification", "Status": "Computationally prepared; human QA pending where flagged", "Human_Completed": "No"},
        {"Stage": "Title-abstract screening", "Status": "Reviewer files ready; decisions blank", "Human_Completed": "No"},
        {"Stage": "Duplicate adjudication", "Status": "Candidate evidence prepared; adjudication pending", "Human_Completed": "No"},
        {"Stage": "Full-text screening", "Status": "Forms/evidence prepared; decisions blank", "Human_Completed": "No"},
        {"Stage": "Data extraction", "Status": "Codebook/forms prepared; extraction blank", "Human_Completed": "No"},
        {"Stage": "Risk of bias", "Status": "Framework/forms prepared; judgements blank", "Human_Completed": "No"},
        {"Stage": "Synthesis/GRADE", "Status": "Plans/templates prepared; results/judgements pending", "Human_Completed": "No"},
    ]
    for i in range(1, AGENTS + 1):
        write_csv(out / "freeze_shards" / f"freeze_inventory_{i:02d}.csv", inv[(i - 1)::AGENTS])
        write_csv(out / "freeze_shards" / f"stage_ledger_{i:02d}.csv", stages)

    summary = {
        "prospero": PROSPERO,
        "inventory_files": len(inv),
        "handoff_files": len(handoff),
        "fulltext_candidate_rows": len(ft_rows),
        "agents_planned": 50,
        "human_screening_decisions_created": 0,
        "generated_utc": now(),
    }
    (out / "prepare_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary))


def recovery_agent(args: argparse.Namespace) -> None:
    root, out, agent = Path(args.input), Path(args.out), int(args.agent)
    out.mkdir(parents=True, exist_ok=True)
    rows = read_csv(root / "recovery_shards" / f"inventory_{agent:02d}.csv")
    result = []
    for r in rows:
        issues = []
        if r.get("Parse_Status") == "parse_error":
            issues.append("parse_error")
        if int(r.get("Bytes") or 0) == 0:
            issues.append("zero_bytes")
        if r.get("Filename", "").lower().endswith(".csv") and not r.get("Headers"):
            issues.append("missing_headers_or_empty")
        result.append({**r, "Recovery_QA": "Investigate" if issues else "OK", "Issue": "; ".join(issues)})
    write_csv(out / f"recovery_{agent:02d}.csv", result)
    (out / "summary.json").write_text(json.dumps({"agent": agent, "files": len(rows), "issues": sum(r["Recovery_QA"] != "OK" for r in result)}, indent=2), encoding="utf-8")


def handoff_agent(args: argparse.Namespace) -> None:
    root, out, agent = Path(args.input), Path(args.out), int(args.agent)
    out.mkdir(parents=True, exist_ok=True)
    refs = read_csv(root / "handoff_shards" / f"handoff_{agent:02d}.csv")
    audits = []
    for ref in refs:
        rows = read_csv(Path(ref["Absolute_Path"]))
        headers = list(rows[0]) if rows else []
        decisions = [h for h in headers if h.lower() in {"decision", "final_decision", "human_title_abstract_decision", "reviewer_decision"}]
        reviewers = [h for h in headers if "reviewer" in h.lower()]
        leakage = [h for h in headers if any(k in h.lower() for k in ("machine_priority", "machine_score", "machine_triage"))]
        populated = sum(1 for r in rows for c in decisions if nt(r.get(c)))
        ids = [nt(r.get("Record_ID") or r.get("Integrated_ID")) for r in rows]
        duplicate_ids = len([x for x in ids if x]) - len(set(x for x in ids if x))
        audits.append({
            **ref,
            "Decision_Columns": " | ".join(decisions),
            "Reviewer_Columns": " | ".join(reviewers),
            "Potential_Machine_Priority_Leakage": " | ".join(leakage),
            "Populated_Human_Decisions": populated,
            "Duplicate_ID_Count": duplicate_ids,
            "Handoff_QA": "Investigate" if populated or duplicate_ids or leakage else "OK",
        })
    write_csv(out / f"handoff_qa_{agent:02d}.csv", audits)


def ingestion_agent(args: argparse.Namespace) -> None:
    root, out, agent = Path(args.input), Path(args.out), int(args.agent)
    out.mkdir(parents=True, exist_ok=True)
    refs = read_csv(root / "handoff_shards" / f"handoff_{agent:02d}.csv")
    specs = []
    for ref in refs:
        specs.append({
            "Source_File": ref.get("Relative_Path", ""),
            "Required_Record_ID": "Record_ID or Integrated_ID",
            "Allowed_Decisions": "Include | Exclude | Unclear",
            "Exclusion_Reason_Required_When": "Decision=Exclude",
            "Reviewer_Name_Required": "Yes",
            "Review_Date_Required": "Yes",
            "Unknown_Record_ID_Action": "Reject row and report",
            "Duplicate_Record_ID_Action": "Reject duplicate within reviewer file",
            "Current_Status": "Blank template—awaiting human completion",
        })
    write_csv(out / f"ingestion_spec_{agent:02d}.csv", specs)
    (out / f"validator_{agent:02d}.json").write_text(json.dumps({
        "agent": agent,
        "checks": [
            "identifier belongs to frozen review pool",
            "decision is Include/Exclude/Unclear",
            "exclusion reason supplied when excluded",
            "reviewer and date populated",
            "one decision per reviewer per record",
            "source checksum recorded before ingestion",
        ],
        "human_decisions_created": 0,
    }, indent=2), encoding="utf-8")


def fulltext_agent(args: argparse.Namespace) -> None:
    root, out, agent = Path(args.input), Path(args.out), int(args.agent)
    out.mkdir(parents=True, exist_ok=True)
    rows = read_csv(root / "fulltext_shards" / f"fulltext_{agent:02d}.csv")
    for r in rows:
        r["Queue_Status"] = "Awaiting title-abstract decision or full-text verification"
        r["Formal_Eligibility_Completed"] = "No"
    write_csv(out / f"fulltext_readiness_{agent:02d}.csv", rows)


def freeze_agent(args: argparse.Namespace) -> None:
    root, out, agent = Path(args.input), Path(args.out), int(args.agent)
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / f"freeze_manifest_{agent:02d}.csv", read_csv(root / "freeze_shards" / f"freeze_inventory_{agent:02d}.csv"))
    write_csv(out / f"stage_ledger_{agent:02d}.csv", read_csv(root / "freeze_shards" / f"stage_ledger_{agent:02d}.csv"))
    (out / "summary.json").write_text(json.dumps({"agent": agent, "all_human_stages_pending": True}, indent=2), encoding="utf-8")


def consolidate(args: argparse.Namespace) -> None:
    root, out = Path(args.input), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    targets = {
        "recovery_": out / "partial_output_recovery",
        "handoff_qa_": out / "reviewer_handoff_qa",
        "ingestion_spec_": out / "decision_ingestion_validation",
        "validator_": out / "decision_ingestion_validation",
        "fulltext_readiness_": out / "fulltext_readiness",
        "freeze_manifest_": out / "evidence_freeze",
        "stage_ledger_": out / "evidence_freeze",
    }
    counts = Counter()
    for d in set(targets.values()):
        d.mkdir(exist_ok=True)
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        for prefix, dest_dir in targets.items():
            if p.name.lower().startswith(prefix):
                dest = dest_dir / p.name
                if dest.exists():
                    dest = dest_dir / f"{p.parent.name}_{p.name}"
                shutil.copy2(p, dest)
                counts[dest_dir.name] += 1
                break
    summary = {
        "prospero": PROSPERO,
        "parallel_agents": 50,
        "output_file_counts": dict(counts),
        "formal_human_title_abstract_screening_completed": 0,
        "formal_fulltext_screening_completed": 0,
        "data_extractions_completed": 0,
        "risk_of_bias_judgements_completed": 0,
        "generated_utc": now(),
    }
    (out / "final_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out / "README.md").write_text(
        "# SRMA recovery and reviewer handoff\n\nRecovered computational artifacts, reviewer-batch QA, future-ingestion rules, blank full-text queues, and evidence-freeze ledgers. No human screening or scientific judgement is claimed.\n",
        encoding="utf-8",
    )
    print(json.dumps(summary))


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    x = sub.add_parser("prepare")
    for name in ("final160", "screen40", "ready100", "partial150", "partial60", "out"):
        x.add_argument(f"--{name}", required=True)
    x.set_defaults(func=prepare)
    for name, fn in (
        ("recovery-agent", recovery_agent),
        ("handoff-agent", handoff_agent),
        ("ingestion-agent", ingestion_agent),
        ("fulltext-agent", fulltext_agent),
        ("freeze-agent", freeze_agent),
    ):
        x = sub.add_parser(name)
        x.add_argument("--agent", required=True, type=int, choices=range(1, AGENTS + 1))
        x.add_argument("--input", required=True)
        x.add_argument("--out", required=True)
        x.set_defaults(func=fn)
    x = sub.add_parser("consolidate")
    x.add_argument("--input", required=True)
    x.add_argument("--out", required=True)
    x.set_defaults(func=consolidate)
    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
