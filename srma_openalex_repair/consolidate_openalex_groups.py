#!/usr/bin/env python3
"""Consolidate five parallel OpenAlex SRMA query artifacts."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from run_openalex_repair import FIELDS, norm_doi, norm_text, write_csv

GROUPS = ["coverage", "timeliness", "dropout", "determinants", "programme"]


def norm_title(value: str) -> str:
    value = unicodedata.normalize("NFKD", norm_text(value)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def exact_key(row: dict[str, str]) -> str:
    if row.get("Source_ID"):
        return "openalex:" + row["Source_ID"].lower()
    if row.get("DOI"):
        return "doi:" + norm_doi(row["DOI"])
    return "title:" + norm_title(row.get("Title", "")) + "|" + row.get("Year", "")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="downloaded")
    parser.add_argument("--out", default="openalex_repair_output")
    args = parser.parse_args()

    root = Path(args.input)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, str]] = []
    logs: list[dict[str, str]] = []
    for group in GROUPS:
        candidates = list(root.glob(f"**/OpenAlex-{group}/records.csv")) + list(root.glob(f"**/{group}/records.csv"))
        log_candidates = list(root.glob(f"**/OpenAlex-{group}/search_log.csv")) + list(root.glob(f"**/{group}/search_log.csv"))
        if not candidates:
            candidates = [p for p in root.glob("**/records.csv") if group in str(p).lower()]
        if not log_candidates:
            log_candidates = [p for p in root.glob("**/search_log.csv") if group in str(p).lower()]
        if not candidates or not log_candidates:
            raise FileNotFoundError(f"Missing artifact for {group}")
        rows.extend(read_csv(candidates[0]))
        logs.extend(read_csv(log_candidates[0]))

    unique: dict[str, dict[str, str]] = {}
    provenance: dict[str, set[str]] = {}
    duplicates: list[dict[str, str]] = []
    for row in rows:
        key = exact_key(row)
        if key not in unique:
            unique[key] = dict(row)
            provenance[key] = set(filter(None, row.get("Query_Group", "").split("; ")))
        else:
            provenance[key].update(filter(None, row.get("Query_Group", "").split("; ")))
            duplicates.append({
                "Exact_Key": key,
                "Kept_Record_ID": unique[key]["Record_ID"],
                "Removed_Record_ID": row["Record_ID"],
                "Title": row.get("Title", ""),
                "Removed_Query_Group": row.get("Query_Group", ""),
            })
            if len(row.get("Abstract", "")) > len(unique[key].get("Abstract", "")):
                kept_group = unique[key].get("Query_Group", "")
                unique[key] = dict(row)
                unique[key]["Query_Group"] = kept_group

    unique_rows = list(unique.values())
    for row in unique_rows:
        row["Query_Group"] = "; ".join(sorted(provenance[exact_key(row)]))
    unique_rows.sort(key=lambda item: (norm_title(item.get("Title", "")), item.get("Year", ""), item.get("Record_ID", "")))

    write_csv(out / "openalex_all_query_rows.csv", rows, FIELDS)
    write_csv(out / "openalex_exact_unique_master.csv", unique_rows, FIELDS)
    write_csv(out / "openalex_exact_duplicate_audit.csv", duplicates,
              ["Exact_Key", "Kept_Record_ID", "Removed_Record_ID", "Title", "Removed_Query_Group"])
    write_csv(out / "openalex_search_log.csv", logs, list(logs[0]))

    summary = {
        "queries": len(GROUPS),
        "queries_completed": sum(row.get("Status") == "completed" for row in logs),
        "queries_failed": sum(row.get("Status") != "completed" for row in logs),
        "api_key_used": all(str(row.get("API_Key_Used", "")).lower() == "true" for row in logs),
        "reported_hits_sum_not_deduplicated": sum(int(row.get("Reported_Hits") or 0) for row in logs),
        "exported_query_rows": len(rows),
        "exact_unique_records": len(unique_rows),
        "exact_duplicate_rows_removed": len(rows) - len(unique_rows),
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "formal_screening_decisions": 0,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if summary["queries_failed"]:
        raise SystemExit("One or more OpenAlex groups failed")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
