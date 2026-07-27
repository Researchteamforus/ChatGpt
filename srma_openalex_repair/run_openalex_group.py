#!/usr/bin/env python3
"""Run one corrected OpenAlex SRMA query group with full cursor pagination."""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
from pathlib import Path

from run_openalex_repair import (
    API_KEY,
    EMAIL,
    FIELDS,
    PROSPERO_ID,
    QUERIES,
    canonical_record,
    now_iso,
    request_page,
    sanitized_error,
    write_csv,
)

TASK_IDS = {
    "coverage": 6,
    "timeliness": 7,
    "dropout": 8,
    "determinants": 9,
    "programme": 10,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", required=True, choices=sorted(QUERIES))
    parser.add_argument("--out", default="openalex_group_output")
    args = parser.parse_args()

    group = args.group
    task_id = TASK_IDS[group]
    query = QUERIES[group]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    raw_dir = out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    import requests

    session = requests.Session()
    session.headers.update({
        "User-Agent": f"SRMA-Bangladesh/{PROSPERO_ID} ({EMAIL})",
        "Accept": "application/json",
    })

    started = now_iso()
    rows: list[dict] = []
    cursor = "*"
    page = 0
    reported_hits = 0
    status = "completed"
    error = ""

    try:
        while cursor:
            page += 1
            response = request_page(session, query, cursor)
            payload = response.json()
            raw_name = f"raw_{page:04d}.json"
            (raw_dir / raw_name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            if page == 1:
                reported_hits = int((payload.get("meta") or {}).get("count") or 0)
                print(f"{group}: reported_hits={reported_hits}, api_key_used={bool(API_KEY)}", flush=True)
            results = payload.get("results") or []
            for work in results:
                rows.append(canonical_record(work, task_id, group, f"raw/{raw_name}"))
            print(f"{group}: page={page}, exported={len(rows)}", flush=True)
            next_cursor = (payload.get("meta") or {}).get("next_cursor")
            if not results or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
            time.sleep(0.08)
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        error = sanitized_error(exc)
        print(f"{group}: failed: {error}", flush=True)

    write_csv(out / "records.csv", rows, FIELDS)
    log = {
        "Task_ID": task_id,
        "Source": "OpenAlex",
        "Query_Group": group,
        "Query": query,
        "Search_Parameter": "search",
        "Per_Page": 100,
        "Cursor_Paging": True,
        "API_Key_Used": bool(API_KEY),
        "Started_UTC": started,
        "Finished_UTC": now_iso(),
        "Status": status,
        "Reported_Hits": reported_hits,
        "Exported_Records": len(rows),
        "Pages": page,
        "Error": error,
    }
    (out / "search_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
    with (out / "search_log.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(log))
        writer.writeheader()
        writer.writerow(log)

    if status != "completed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
