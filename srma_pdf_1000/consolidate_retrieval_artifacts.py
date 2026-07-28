#!/usr/bin/env python3
"""Consolidate the 1,000-lane SRMA open-PDF retrieval artifacts.

This script does not make eligibility decisions. It combines retrieval audit rows,
checks record coverage, verifies saved PDFs, removes byte-identical PDF copies,
and exports unresolved records for manual/legal retrieval routes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

EXPECTED_RECORDS = 1357


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return value.strip("._-") or "record"


def write_csv(path: Path, rows: Iterable[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-records", type=int, default=EXPECTED_RECORDS)
    args = parser.parse_args()

    source_root = args.input.resolve()
    output_root = args.output.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    pdf_output = output_root / "pdfs"
    pdf_output.mkdir(parents=True, exist_ok=True)

    result_files = sorted(source_root.rglob("*_results.csv"))
    if not result_files:
        raise RuntimeError(f"No lane result CSV files found under {source_root}")

    rows: list[dict[str, str]] = []
    field_order: list[str] = []
    row_source: dict[tuple[str, str], Path] = {}

    for result_file in result_files:
        with result_file.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for field in reader.fieldnames or []:
                if field not in field_order:
                    field_order.append(field)
            for row in reader:
                normalized = {key: (value or "") for key, value in row.items()}
                rows.append(normalized)
                key = (normalized.get("integrated_id", ""), normalized.get("lane_num", ""))
                row_source[key] = result_file.parent

    rows.sort(key=lambda r: (int(r.get("lane_num") or 0), r.get("integrated_id", "")))
    integrated_ids = [row.get("integrated_id", "").strip() for row in rows]
    id_counts = Counter(integrated_ids)
    blank_ids = id_counts.get("", 0)
    duplicate_ids = {key: count for key, count in id_counts.items() if key and count > 1}

    if len(rows) != args.expected_records:
        raise AssertionError(f"Expected {args.expected_records} rows, found {len(rows)}")
    if blank_ids:
        raise AssertionError(f"Found {blank_ids} rows without integrated_id")
    if len(id_counts) != args.expected_records:
        raise AssertionError(
            f"Expected {args.expected_records} unique integrated IDs, found {len(id_counts)}; "
            f"duplicates={len(duplicate_ids)}"
        )

    status_counts = Counter(row.get("retrieval_status", "") or "Missing status" for row in rows)
    method_counts = Counter(row.get("winning_method", "") or "None" for row in rows)

    retrieved_rows: list[dict[str, str]] = []
    unresolved_rows: list[dict[str, str]] = []
    pdf_index: list[dict[str, str]] = []
    missing_pdf_rows: list[dict[str, str]] = []
    hash_to_records: dict[str, list[str]] = defaultdict(list)
    hash_to_destination: dict[str, str] = {}

    for row in rows:
        status = row.get("retrieval_status", "")
        if status != "PDF saved":
            unresolved_rows.append(row)
            continue

        retrieved_rows.append(row)
        integrated_id = row.get("integrated_id", "")
        saved_file = Path(row.get("saved_file", "")).name
        source_dir = row_source[(integrated_id, row.get("lane_num", ""))]
        candidates = list(source_dir.rglob(saved_file)) if saved_file else []
        source_pdf = candidates[0] if candidates else None

        if source_pdf is None or not source_pdf.is_file():
            missing = dict(row)
            missing["verification_issue"] = "Declared PDF was not present in downloaded artifact"
            missing_pdf_rows.append(missing)
            continue

        actual_hash = sha256_file(source_pdf)
        actual_size = source_pdf.stat().st_size
        declared_hash = row.get("sha256", "").lower()
        declared_size = row.get("size_bytes", "")
        hash_matches = str(bool(declared_hash and declared_hash == actual_hash)).lower()
        size_matches = str(bool(declared_size and declared_size.isdigit() and int(declared_size) == actual_size)).lower()

        hash_to_records[actual_hash].append(integrated_id)
        if actual_hash in hash_to_destination:
            destination_name = hash_to_destination[actual_hash]
            duplicate_content = "true"
        else:
            extension = source_pdf.suffix.lower() or ".pdf"
            destination_name = f"{safe_name(integrated_id)}_{actual_hash[:12]}{extension}"
            shutil.copy2(source_pdf, pdf_output / destination_name)
            hash_to_destination[actual_hash] = destination_name
            duplicate_content = "false"

        pdf_index.append(
            {
                "integrated_id": integrated_id,
                "lane_num": row.get("lane_num", ""),
                "retrieval_status": status,
                "winning_method": row.get("winning_method", ""),
                "winning_url": row.get("winning_url", ""),
                "source_artifact_file": str(source_pdf.relative_to(source_root)),
                "consolidated_pdf": f"pdfs/{destination_name}",
                "actual_size_bytes": str(actual_size),
                "actual_sha256": actual_hash,
                "declared_sha256_matches": hash_matches,
                "declared_size_matches": size_matches,
                "duplicate_pdf_content": duplicate_content,
            }
        )

    duplicate_hash_rows: list[dict[str, str]] = []
    for digest, record_ids in sorted(hash_to_records.items()):
        if len(record_ids) > 1:
            duplicate_hash_rows.append(
                {
                    "sha256": digest,
                    "record_count": str(len(record_ids)),
                    "integrated_ids": " | ".join(sorted(record_ids)),
                    "consolidated_pdf": f"pdfs/{hash_to_destination[digest]}",
                }
            )

    write_csv(output_root / "retrieval_results_1000_lanes.csv", rows, field_order)
    write_csv(output_root / "retrieved_records.csv", retrieved_rows, field_order)
    write_csv(output_root / "unresolved_records.csv", unresolved_rows, field_order)
    write_csv(
        output_root / "retrieved_pdf_index.csv",
        pdf_index,
        [
            "integrated_id",
            "lane_num",
            "retrieval_status",
            "winning_method",
            "winning_url",
            "source_artifact_file",
            "consolidated_pdf",
            "actual_size_bytes",
            "actual_sha256",
            "declared_sha256_matches",
            "declared_size_matches",
            "duplicate_pdf_content",
        ],
    )
    write_csv(
        output_root / "duplicate_pdf_hashes.csv",
        duplicate_hash_rows,
        ["sha256", "record_count", "integrated_ids", "consolidated_pdf"],
    )
    missing_fields = field_order + ["verification_issue"]
    write_csv(output_root / "missing_declared_pdfs.csv", missing_pdf_rows, missing_fields)

    status_rows = [
        {"retrieval_status": status, "record_count": str(count)}
        for status, count in sorted(status_counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    write_csv(output_root / "status_summary.csv", status_rows, ["retrieval_status", "record_count"])

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "expected_records": args.expected_records,
        "lane_result_files": len(result_files),
        "total_rows": len(rows),
        "unique_integrated_ids": len(id_counts),
        "retrieved_record_rows": len(retrieved_rows),
        "verified_pdf_rows": len(pdf_index),
        "unique_pdf_files": len(hash_to_destination),
        "unresolved_records": len(unresolved_rows),
        "declared_pdfs_missing_from_artifacts": len(missing_pdf_rows),
        "duplicate_integrated_ids": duplicate_ids,
        "duplicate_pdf_hash_groups": len(duplicate_hash_rows),
        "retrieval_statuses": dict(status_counts),
        "winning_methods": dict(method_counts),
        "source_workflow_run_ids": [30331196758, 30331196777, 30331196764, 30331196762],
        "formal_screening_decisions": 0,
    }
    (output_root / "consolidation_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    summary_md = [
        "# 1,000-lane open-PDF retrieval consolidation",
        "",
        f"- Target records: **{args.expected_records}**",
        f"- Lane result files found: **{len(result_files)}**",
        f"- Unique records consolidated: **{len(id_counts)}**",
        f"- Records reporting PDF saved: **{len(retrieved_rows)}**",
        f"- Saved PDFs verified in artifacts: **{len(pdf_index)}**",
        f"- Unique PDF byte streams retained: **{len(hash_to_destination)}**",
        f"- Unresolved records: **{len(unresolved_rows)}**",
        f"- Missing declared PDF files: **{len(missing_pdf_rows)}**",
        "",
        "## Retrieval statuses",
        "",
    ]
    summary_md.extend(f"- {status}: {count}" for status, count in status_counts.most_common())
    summary_md.extend(
        [
            "",
            "This package records retrieval only. No title/abstract screening, full-text eligibility decision,",
            "risk-of-bias judgement, or duplicate-review claim is made by this workflow.",
            "",
        ]
    )
    (output_root / "README.md").write_text("\n".join(summary_md), encoding="utf-8")

    if missing_pdf_rows:
        raise AssertionError(f"{len(missing_pdf_rows)} rows declared a saved PDF that was absent")

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
