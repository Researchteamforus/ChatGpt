#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

import retrieve_lane as base


def map_row(row: dict[str, str]) -> dict[str, str]:
    url = (row.get("u") or "").strip()
    previous_final = (row.get("f") or "").strip()
    doi = ""
    match = re.search(r"(?:doi\.org/)?(10\.\d{4,9}/[^\s?#]+)", url, flags=re.I)
    if match:
        doi = match.group(1).rstrip(".,;)")
    lane_num = int(row.get("l") or 0)
    return {
        "lane_num": str(lane_num),
        "lane_id": f"PDF-AGENT-{lane_num:04d}",
        "queue_order": "",
        "integrated_id": (row.get("i") or "").strip(),
        "source_record_id": "",
        "title": (row.get("t") or "").strip(),
        "doi": doi,
        "pmid": "",
        "article_url": "",
        "fulltext_url": "",
        "resolver_url": url,
        "retrieval_route": "1000-lane open-PDF rerun",
        "priority": "",
        "previous_result_category": "",
        "previous_automated_result": "",
        "previous_http_status": "",
        "previous_final_url": previous_final,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--lane", type=int, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    mapped_rows = []
    with open(args.manifest, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            mapped_rows.append(map_row(row))

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    mapped_manifest = output_dir / "mapped_manifest.csv"
    fields = list(mapped_rows[0].keys())
    with mapped_manifest.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(mapped_rows)

    sys.argv = [
        sys.argv[0],
        "--manifest", str(mapped_manifest),
        "--lane", str(args.lane),
        "--output", str(output_dir),
    ]
    base.main()


if __name__ == "__main__":
    main()
