#!/usr/bin/env python3
"""Consolidate all 100 metadata-enrichment and provisional-triage agents."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

EXPECTED_RECORDS = 1129
EXPECTED_AGENTS = 100


def read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows, fields):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    root, out = Path(args.input), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    files = sorted(root.rglob("screening_enrichment_agent_*.csv"))
    if len(files) != EXPECTED_AGENTS:
        raise RuntimeError(f"Expected {EXPECTED_AGENTS} agent CSVs, found {len(files)}")

    rows = []
    completion = []
    for path in files:
        batch = read_csv(path)
        rows.extend(batch)
        completion.append({"Agent_File": path.name, "Records": len(batch), "SHA256": sha256(path)})

    ids = [r.get("Master_Record_ID", "") for r in rows]
    if len(rows) != EXPECTED_RECORDS:
        raise RuntimeError(f"Expected {EXPECTED_RECORDS} records, found {len(rows)}")
    if len(set(ids)) != EXPECTED_RECORDS or "" in set(ids):
        duplicates = [k for k, v in Counter(ids).items() if v > 1 or not k]
        raise RuntimeError(f"Record ID uniqueness failure: {duplicates[:20]}")

    rows.sort(key=lambda r: r["Master_Record_ID"])
    fields = list(rows[0].keys())
    master = out / "screening_enrichment_master_1129.csv"
    write_csv(master, rows, fields)

    groups = defaultdict(list)
    for row in rows:
        groups[row.get("Identity_Key", "")].append(row)
    duplicate_rows = []
    for key, group in sorted(groups.items()):
        if key and len(group) > 1:
            for row in group:
                duplicate_rows.append({
                    "Identity_Key": key,
                    "Duplicate_Group_Size": len(group),
                    "Master_Record_ID": row["Master_Record_ID"],
                    "Title": row.get("Enriched_Title", ""),
                    "DOI": row.get("DOI", ""),
                    "PMID": row.get("PMID", ""),
                    "Sources": row.get("Sources", ""),
                })
    dup_fields = ["Identity_Key", "Duplicate_Group_Size", "Master_Record_ID", "Title", "DOI", "PMID", "Sources"]
    write_csv(out / "exact_duplicate_candidates.csv", duplicate_rows, dup_fields)

    triage_rank = {"Likely include": 0, "Unclear": 1, "Likely exclude": 2}
    queue = sorted(
        rows,
        key=lambda r: (
            triage_rank.get(r.get("Machine_Triage", ""), 9),
            0 if r.get("Abstract_Available_After") == "No" else 1,
            -int(r.get("Machine_Confidence") or 0),
            r.get("Master_Record_ID", ""),
        ),
    )
    write_csv(out / "priority_human_screening_queue.csv", queue, fields)
    write_csv(out / "agent_completion_audit.csv", completion, ["Agent_File", "Records", "SHA256"])

    triage_counts = Counter(r.get("Machine_Triage", "") for r in rows)
    enrichment_counts = Counter(r.get("Enrichment_Status", "") for r in rows)
    summary = {
        "records": len(rows),
        "agents": len(files),
        "triage_counts": dict(triage_counts),
        "enrichment_status": dict(enrichment_counts),
        "abstracts_before": sum(r.get("Abstract_Available_Before") == "Yes" for r in rows),
        "abstracts_after": sum(r.get("Abstract_Available_After") == "Yes" for r in rows),
        "records_with_metadata_change": sum(r.get("Metadata_Changed") == "Yes" for r in rows),
        "duplicate_candidate_groups": sum(1 for g in groups.values() if len(g) > 1),
        "duplicate_candidate_records": len(duplicate_rows),
        "formal_human_screening_completed": 0,
        "governance": "Machine-assisted enrichment and provisional triage only. Human reviewer fields remain blank/not reviewed.",
        "master_sha256": sha256(master),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out / "README.md").write_text(
        "# SRMA 100-agent metadata enrichment and provisional triage\n\n"
        "PROSPERO: CRD420261461557\n\n"
        "This package contains computational metadata enrichment, exact-identity duplicate candidates, "
        "and a protocol-grounded provisional screening queue for 1,129 verified prospective-search records. "
        "It must not be described as independent human screening or final eligibility assessment.\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
