#!/usr/bin/env python3
"""Corrected OpenAlex search for the Bangladesh childhood immunization SRMA.

PROSPERO: CRD420261461557

This script fixes the prior OpenAlex failures by:
- using `per_page=100` rather than the invalid `per-page=200`;
- removing wildcard syntax from the stemmed `search` parameter;
- keeping each Boolean query below the OpenAlex URL-length limit;
- using cursor pagination until all returned records are exhausted;
- reading the API key only from OPENALEX_API_KEY and never printing it.

The script performs discovery only. It does not make formal screening decisions.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

PROSPERO_ID = "CRD420261461557"
EMAIL = os.getenv("OPENALEX_MAILTO", "st19009@mbstu.ac.bd")
API_KEY = os.getenv("OPENALEX_API_KEY", "").strip()
OUT = Path(os.getenv("OPENALEX_OUT", "openalex_repair_output"))
OUT.mkdir(parents=True, exist_ok=True)

CORE = (
    'Bangladesh AND '
    '(immunization OR immunisation OR vaccination OR '
    '"expanded programme on immunization" OR '
    '"expanded program on immunization" OR EPI) AND '
    '(infant OR infants OR child OR children OR childhood OR newborn OR newborns OR '
    '"under five" OR under-five OR toddler OR toddlers)'
)

QUERIES = {
    "coverage": CORE + ' AND (coverage OR uptake OR "full vaccination" OR '
        '"complete vaccination" OR "fully vaccinated" OR "fully immunized" OR '
        '"fully immunised" OR "antigen-specific")',
    "timeliness": CORE + ' AND (timeliness OR timely OR delayed OR delay OR '
        '"age-appropriate" OR "age appropriate" OR invalid OR schedule OR adherence)',
    "dropout": CORE + ' AND (dropout OR "drop out" OR "zero dose" OR zero-dose OR '
        'unvaccinated OR incomplete OR partial OR "under vaccinated" OR '
        '"under-vaccinated" OR "under immunized" OR "under-immunized")',
    "determinants": CORE + ' AND (determinant OR determinants OR factor OR factors OR '
        'barrier OR barriers OR inequality OR inequalities OR inequity OR inequities OR '
        'socioeconomic OR maternal OR caregiver OR geographic OR access OR '
        '"health service" OR "health services")',
    "programme": CORE + ' AND ("missed opportunity" OR "missed opportunities" OR '
        '"service delivery" OR readiness OR outreach OR defaulter OR reminder OR '
        'reminders OR intervention OR interventions OR programme OR programmes OR '
        'program OR programs)',
}

FIELDS = [
    "Record_ID", "Source", "Task_ID", "Query_Group", "Source_ID", "Title",
    "Abstract", "Authors", "Year", "Journal_or_Institution", "DOI", "PMID",
    "PMCID", "URL", "Document_Type", "Language", "Retrieved_UTC", "Raw_File",
    "OpenAlex_Relevance_Score", "OpenAlex_Cited_By_Count", "Open_Access_Status",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def norm_doi(value: Any) -> str:
    value = norm_text(value).lower()
    value = re.sub(r"^https?://(dx\.)?doi\.org/", "", value)
    value = re.sub(r"^doi:\s*", "", value)
    return value.rstrip(".,; )]")


def norm_title(value: Any) -> str:
    value = unicodedata.normalize("NFKD", norm_text(value)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def reconstruct_abstract(index: Any) -> str:
    if not isinstance(index, dict):
        return ""
    pairs: list[tuple[int, str]] = []
    for word, positions in index.items():
        for pos in positions or []:
            try:
                pairs.append((int(pos), str(word)))
            except (TypeError, ValueError):
                continue
    return " ".join(word for _, word in sorted(pairs))


def canonical_record(work: dict[str, Any], task_id: int, group: str, raw_file: str) -> dict[str, Any]:
    ids = work.get("ids") or {}
    authors = "; ".join(
        norm_text((authorship.get("author") or {}).get("display_name"))
        for authorship in work.get("authorships") or []
        if norm_text((authorship.get("author") or {}).get("display_name"))
    )
    location = work.get("primary_location") or {}
    source = location.get("source") or {}
    title = norm_text(work.get("display_name") or work.get("title"))
    doi = norm_doi(ids.get("doi") or work.get("doi"))
    seed = "|".join(["OpenAlex", norm_text(work.get("id")), doi, norm_title(title)])
    record_id = "SRMA-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]
    return {
        "Record_ID": record_id,
        "Source": "OpenAlex",
        "Task_ID": str(task_id),
        "Query_Group": group,
        "Source_ID": norm_text(work.get("id")),
        "Title": title,
        "Abstract": reconstruct_abstract(work.get("abstract_inverted_index")),
        "Authors": authors,
        "Year": norm_text(work.get("publication_year")),
        "Journal_or_Institution": norm_text(source.get("display_name")),
        "DOI": doi,
        "PMID": norm_text(ids.get("pmid")).replace("https://pubmed.ncbi.nlm.nih.gov/", "").strip("/"),
        "PMCID": norm_text(ids.get("pmcid")).replace("https://www.ncbi.nlm.nih.gov/pmc/articles/", "").strip("/"),
        "URL": norm_text(location.get("landing_page_url") or work.get("id")),
        "Document_Type": norm_text(work.get("type")),
        "Language": norm_text(work.get("language")),
        "Retrieved_UTC": now_iso(),
        "Raw_File": raw_file,
        "OpenAlex_Relevance_Score": norm_text(work.get("relevance_score")),
        "OpenAlex_Cited_By_Count": norm_text(work.get("cited_by_count")),
        "Open_Access_Status": norm_text((work.get("open_access") or {}).get("oa_status")),
    }


def sanitized_error(exc: Exception) -> str:
    text = repr(exc)
    if API_KEY:
        text = text.replace(API_KEY, "[REDACTED]")
    text = re.sub(r"api_key=[^&'\"\s]+", "api_key=[REDACTED]", text)
    return text


def request_page(session: requests.Session, query: str, cursor: str) -> requests.Response:
    params = {
        "search": query,
        "per_page": 100,
        "cursor": cursor,
        "mailto": EMAIL,
    }
    if API_KEY:
        params["api_key"] = API_KEY
    last_error: Exception | None = None
    for attempt in range(1, 8):
        try:
            response = session.get("https://api.openalex.org/works", params=params, timeout=90)
            if response.status_code in {429, 500, 502, 503, 504}:
                wait = int(response.headers.get("Retry-After", min(60, 2 ** attempt)))
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(min(60, 2 ** attempt))
    raise RuntimeError(sanitized_error(last_error or RuntimeError("unknown request failure")))


def exact_key(row: dict[str, Any]) -> str:
    if row["Source_ID"]:
        return "openalex:" + row["Source_ID"].lower()
    if row["DOI"]:
        return "doi:" + row["DOI"].lower()
    return "title:" + norm_title(row["Title"]) + "|" + row["Year"]


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def main() -> None:
    session = requests.Session()
    session.headers.update({
        "User-Agent": f"SRMA-Bangladesh/{PROSPERO_ID} ({EMAIL})",
        "Accept": "application/json",
    })

    all_rows: list[dict[str, Any]] = []
    search_log: list[dict[str, Any]] = []

    for offset, (group, query) in enumerate(QUERIES.items(), start=6):
        group_dir = OUT / f"task_{offset:02d}_OpenAlex_{group}"
        group_dir.mkdir(parents=True, exist_ok=True)
        started = now_iso()
        rows: list[dict[str, Any]] = []
        cursor = "*"
        reported_hits = 0
        page = 0
        status = "completed"
        error = ""
        try:
            while cursor:
                page += 1
                response = request_page(session, query, cursor)
                payload = response.json()
                raw_name = f"raw_{page:04d}.json"
                (group_dir / raw_name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                if page == 1:
                    reported_hits = int((payload.get("meta") or {}).get("count") or 0)
                results = payload.get("results") or []
                for work in results:
                    rows.append(canonical_record(work, offset, group, raw_name))
                next_cursor = (payload.get("meta") or {}).get("next_cursor")
                if not results or not next_cursor or next_cursor == cursor:
                    break
                cursor = next_cursor
                time.sleep(0.08)
        except Exception as exc:  # noqa: BLE001
            status = "failed"
            error = sanitized_error(exc)

        write_csv(group_dir / "records.csv", rows, FIELDS)
        query_audit = {
            "Task_ID": offset,
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
        (group_dir / "search_log.json").write_text(json.dumps(query_audit, indent=2), encoding="utf-8")
        search_log.append(query_audit)
        all_rows.extend(rows)

    unique: dict[str, dict[str, Any]] = {}
    provenance: dict[str, set[str]] = {}
    duplicate_audit: list[dict[str, Any]] = []
    for row in all_rows:
        key = exact_key(row)
        if key not in unique:
            unique[key] = dict(row)
            provenance[key] = {row["Query_Group"]}
        else:
            provenance[key].add(row["Query_Group"])
            duplicate_audit.append({
                "Exact_Key": key,
                "Kept_Record_ID": unique[key]["Record_ID"],
                "Removed_Record_ID": row["Record_ID"],
                "Title": row["Title"],
                "Removed_Query_Group": row["Query_Group"],
            })
            if len(row["Abstract"]) > len(unique[key]["Abstract"]):
                retained_group = unique[key]["Query_Group"]
                unique[key] = dict(row)
                unique[key]["Query_Group"] = retained_group

    unique_rows = list(unique.values())
    for row in unique_rows:
        row["Query_Group"] = "; ".join(sorted(provenance[exact_key(row)]))
    unique_rows.sort(key=lambda item: (norm_title(item["Title"]), item["Year"], item["Record_ID"]))

    write_csv(OUT / "openalex_all_query_rows.csv", all_rows, FIELDS)
    write_csv(OUT / "openalex_exact_unique_master.csv", unique_rows, FIELDS)
    write_csv(
        OUT / "openalex_exact_duplicate_audit.csv",
        duplicate_audit,
        ["Exact_Key", "Kept_Record_ID", "Removed_Record_ID", "Title", "Removed_Query_Group"],
    )
    write_csv(
        OUT / "openalex_search_log.csv",
        search_log,
        ["Task_ID", "Source", "Query_Group", "Query", "Search_Parameter", "Per_Page",
         "Cursor_Paging", "API_Key_Used", "Started_UTC", "Finished_UTC", "Status",
         "Reported_Hits", "Exported_Records", "Pages", "Error"],
    )

    summary = {
        "prospero_id": PROSPERO_ID,
        "api_key_used": bool(API_KEY),
        "queries": len(QUERIES),
        "queries_completed": sum(item["Status"] == "completed" for item in search_log),
        "queries_failed": sum(item["Status"] != "completed" for item in search_log),
        "reported_hits_sum_not_deduplicated": sum(int(item["Reported_Hits"]) for item in search_log),
        "exported_query_rows": len(all_rows),
        "exact_unique_records": len(unique_rows),
        "exact_duplicate_rows_removed": len(all_rows) - len(unique_rows),
        "completed_utc": now_iso(),
        "formal_screening_decisions": 0,
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if summary["queries_failed"]:
        raise SystemExit("One or more OpenAlex queries failed; inspect openalex_search_log.csv")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
