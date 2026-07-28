#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    source_dir = Path(args.source_dir)
    rows = []
    for path in sorted(source_dir.glob("manifest_group_*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows.extend(csv.DictReader(handle))

    if len(rows) != 1771:
        raise RuntimeError(f"Expected 1771 source rows, found {len(rows)}")

    output_fields = [
        "lane_num", "lane_id", "queue_order", "integrated_id", "source_record_id",
        "title", "doi", "pmid", "article_url", "fulltext_url", "resolver_url",
        "retrieval_route", "priority", "previous_result_category",
        "previous_automated_result", "previous_http_status", "previous_final_url"
    ]

    prepared = []
    for index, row in enumerate(sorted(rows, key=lambda r: int(r.get("queue_order") or 0))):
        lane_num = (index % 1000) + 1
        prepared.append({
            "lane_num": lane_num,
            "lane_id": f"PDF-AGENT-{lane_num:04d}",
            "queue_order": row.get("queue_order", ""),
            "integrated_id": row.get("integrated_id", ""),
            "source_record_id": row.get("source_record_id", ""),
            "title": row.get("title", ""),
            "doi": row.get("doi", ""),
            "pmid": row.get("pmid", ""),
            "article_url": row.get("article_url", ""),
            "fulltext_url": row.get("fulltext_url", ""),
            "resolver_url": row.get("resolver_url", ""),
            "retrieval_route": row.get("retrieval_route", ""),
            "priority": row.get("priority", ""),
            "previous_result_category": "",
            "previous_automated_result": "",
            "previous_http_status": "",
            "previous_final_url": "",
        })

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=output_fields)
        writer.writeheader()
        writer.writerows(prepared)

    counts = {lane: 0 for lane in range(1, 1001)}
    for row in prepared:
        counts[int(row["lane_num"])] += 1
    if min(counts.values()) != 1 or max(counts.values()) != 2:
        raise RuntimeError("Unexpected lane balance")
    print(f"Prepared {len(prepared)} records across 1000 lanes")


if __name__ == "__main__":
    main()
