#!/usr/bin/env python3
"""Reproducible supplementary literature search for CRD420261461557.

This script queries public scholarly APIs (OpenAlex, Europe PMC, Crossref and
DOAJ where available), standardises metadata, removes duplicate records, and
writes CSV/XLSX outputs suitable for screening. It supplements—not replaces—
the protocol-specified database searches that require direct platform access.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

RECORD_ID = "CRD420261461557"
DEFAULT_QUERY = (
    '(immunization OR vaccination OR "zero-dose" OR "zero dose" OR EPI) '
    'AND (child OR children OR childhood OR infant) AND Bangladesh'
)
COLUMNS = [
    "Database",
    "Title",
    "Authors",
    "Year",
    "Journal/Source",
    "DOI",
    "PMID",
    "Citations",
    "Abstract",
    "Record URL",
    "Search Query",
    "Retrieved UTC",
]


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalise_doi(value: Any) -> str:
    doi = clean_text(value).lower()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
    doi = re.sub(r"^doi:\s*", "", doi)
    return doi.rstrip(" .")


def title_key(value: Any) -> str:
    text = clean_text(value).lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def year_from_parts(item: dict[str, Any]) -> str:
    for key in ("published-print", "published-online", "published", "issued", "created"):
        parts = item.get(key, {}).get("date-parts", [])
        if parts and parts[0]:
            return str(parts[0][0])
    return ""


class LiteratureExtractor:
    def __init__(self, query: str, timeout: int = 45) -> None:
        self.query = query
        self.timeout = timeout
        self.retrieved_utc = datetime.now(timezone.utc).isoformat()
        self.records: list[dict[str, Any]] = []
        self.errors: list[dict[str, str]] = []
        self.session = requests.Session()
        retries = Retry(
            total=4,
            connect=4,
            read=4,
            backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retries))
        self.session.headers.update(
            {
                "User-Agent": (
                    "CRD420261461557-literature-extractor/1.0 "
                    "(GitHub: Researchteamforus/ChatGpt)"
                ),
                "Accept": "application/json",
            }
        )

    def request_json(self, source: str, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("API response was not a JSON object")
            return payload
        except Exception as exc:  # noqa: BLE001 - preserve source-level failures
            message = f"{type(exc).__name__}: {exc}"
            self.errors.append({"source": source, "error": message})
            print(f"[{source}] ERROR: {message}", file=sys.stderr)
            return {}

    def add_record(self, database: str, **kwargs: Any) -> None:
        title = clean_text(kwargs.get("title"))
        if not title:
            return
        self.records.append(
            {
                "Database": database,
                "Title": title,
                "Authors": clean_text(kwargs.get("authors")),
                "Year": clean_text(kwargs.get("year")),
                "Journal/Source": clean_text(kwargs.get("journal")),
                "DOI": normalise_doi(kwargs.get("doi")),
                "PMID": clean_text(kwargs.get("pmid")),
                "Citations": kwargs.get("citations", ""),
                "Abstract": clean_text(kwargs.get("abstract")),
                "Record URL": clean_text(kwargs.get("url")),
                "Search Query": self.query,
                "Retrieved UTC": self.retrieved_utc,
            }
        )

    def fetch_openalex(self, max_results: int) -> None:
        source = "OpenAlex"
        print(f"[{source}] Searching up to {max_results} records")
        cursor = "*"
        fetched = 0
        search_text = "childhood immunization vaccination zero-dose EPI Bangladesh"
        while fetched < max_results and cursor:
            page_size = min(100, max_results - fetched)
            payload = self.request_json(
                source,
                "https://api.openalex.org/works",
                {
                    "search": search_text,
                    "per-page": page_size,
                    "cursor": cursor,
                    "sort": "relevance_score:desc",
                    "select": (
                        "id,doi,title,publication_year,primary_location,authorships,"
                        "cited_by_count,abstract_inverted_index,open_access"
                    ),
                },
            )
            items = payload.get("results", [])
            if not items:
                break
            for item in items:
                inverted = item.get("abstract_inverted_index") or {}
                positioned = sorted(
                    (position, word)
                    for word, positions in inverted.items()
                    for position in positions
                )
                abstract = " ".join(word for _, word in positioned)
                authors = ", ".join(
                    clean_text(authorship.get("author", {}).get("display_name"))
                    for authorship in item.get("authorships", [])[:20]
                    if authorship.get("author", {}).get("display_name")
                )
                location = item.get("primary_location") or {}
                source_info = location.get("source") or {}
                open_access = item.get("open_access") or {}
                self.add_record(
                    source,
                    title=item.get("title"),
                    authors=authors,
                    year=item.get("publication_year"),
                    journal=source_info.get("display_name"),
                    doi=item.get("doi"),
                    citations=item.get("cited_by_count", 0),
                    abstract=abstract,
                    url=open_access.get("oa_url") or item.get("id"),
                )
            fetched += len(items)
            cursor = payload.get("meta", {}).get("next_cursor")
            time.sleep(0.2)
        print(f"[{source}] Retrieved {fetched} raw records")

    def fetch_europe_pmc(self, max_results: int) -> None:
        source = "Europe PMC"
        print(f"[{source}] Searching up to {max_results} records")
        cursor = "*"
        fetched = 0
        while fetched < max_results and cursor:
            page_size = min(1000, max_results - fetched)
            payload = self.request_json(
                source,
                "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                {
                    "query": self.query,
                    "format": "json",
                    "resultType": "core",
                    "pageSize": page_size,
                    "cursorMark": cursor,
                },
            )
            items = payload.get("resultList", {}).get("result", [])
            if not items:
                break
            for item in items:
                pmid = item.get("pmid") or item.get("id") or ""
                record_url = f"https://europepmc.org/article/MED/{pmid}" if pmid else ""
                self.add_record(
                    source,
                    title=item.get("title"),
                    authors=item.get("authorString"),
                    year=item.get("pubYear"),
                    journal=item.get("journalTitle"),
                    doi=item.get("doi"),
                    pmid=pmid,
                    citations=item.get("citedByCount", 0),
                    abstract=item.get("abstractText"),
                    url=record_url,
                )
            fetched += len(items)
            next_cursor = payload.get("nextCursorMark")
            cursor = next_cursor if next_cursor and next_cursor != cursor else ""
            time.sleep(0.2)
        print(f"[{source}] Retrieved {fetched} raw records")

    def fetch_crossref(self, max_results: int) -> None:
        source = "Crossref"
        print(f"[{source}] Searching up to {max_results} records")
        cursor = "*"
        fetched = 0
        while fetched < max_results and cursor:
            rows = min(1000, max_results - fetched)
            payload = self.request_json(
                source,
                "https://api.crossref.org/works",
                {
                    "query.bibliographic": self.query,
                    "filter": "type:journal-article,type:proceedings-article,type:dissertation",
                    "rows": rows,
                    "cursor": cursor,
                    "cursor-max": rows,
                    "select": (
                        "DOI,title,author,published-print,published-online,published,issued,"
                        "created,container-title,is-referenced-by-count,abstract,URL"
                    ),
                },
            )
            message = payload.get("message", {})
            items = message.get("items", [])
            if not items:
                break
            for item in items:
                authors = ", ".join(
                    " ".join(filter(None, [clean_text(a.get("given")), clean_text(a.get("family"))]))
                    for a in item.get("author", [])[:20]
                )
                titles = item.get("title") or []
                journals = item.get("container-title") or []
                self.add_record(
                    source,
                    title=titles[0] if titles else "",
                    authors=authors,
                    year=year_from_parts(item),
                    journal=journals[0] if journals else "",
                    doi=item.get("DOI"),
                    citations=item.get("is-referenced-by-count", 0),
                    abstract=item.get("abstract"),
                    url=item.get("URL"),
                )
            fetched += len(items)
            next_cursor = message.get("next-cursor")
            cursor = next_cursor if next_cursor and next_cursor != cursor else ""
            time.sleep(0.3)
        print(f"[{source}] Retrieved {fetched} raw records")

    def fetch_doaj(self, max_results: int) -> None:
        source = "DOAJ"
        print(f"[{source}] Searching up to {max_results} records")
        query = 'bibjson.title:(immunization OR vaccination OR "zero dose") AND bibjson.title:Bangladesh'
        payload = self.request_json(
            source,
            f"https://doaj.org/api/search/articles/{quote(query, safe='():\"')}",
            {"page": 1, "pageSize": min(max_results, 100)},
        )
        items = payload.get("results", [])
        for result in items[:max_results]:
            item = result.get("bibjson", {})
            identifiers = item.get("identifier", [])
            doi = next(
                (identifier.get("id") for identifier in identifiers if identifier.get("type") == "doi"),
                "",
            )
            links = item.get("link", [])
            record_url = next((link.get("url") for link in links if link.get("url")), "")
            authors = ", ".join(
                clean_text(author.get("name"))
                for author in item.get("author", [])[:20]
                if author.get("name")
            )
            self.add_record(
                source,
                title=item.get("title"),
                authors=authors,
                year=item.get("year"),
                journal=item.get("journal", {}).get("title"),
                doi=doi,
                abstract=item.get("abstract"),
                url=record_url,
            )
        print(f"[{source}] Retrieved {min(len(items), max_results)} raw records")

    def deduplicate(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        raw = pd.DataFrame(self.records, columns=COLUMNS)
        if raw.empty:
            return raw, raw
        ranked = raw.copy()
        ranked["_doi_key"] = ranked["DOI"].map(normalise_doi)
        ranked["_title_key"] = ranked["Title"].map(title_key)
        ranked["_completeness"] = (
            ranked[["DOI", "PMID", "Abstract", "Record URL"]]
            .fillna("")
            .astype(str)
            .ne("")
            .sum(axis=1)
        )
        ranked = ranked.sort_values(
            ["_completeness", "Citations"], ascending=[False, False], kind="stable"
        )
        seen_doi: set[str] = set()
        seen_title: set[str] = set()
        keep: list[bool] = []
        for row in ranked.itertuples(index=False):
            doi_key = getattr(row, "_doi_key")
            title_value = getattr(row, "_title_key")
            duplicate = bool(doi_key and doi_key in seen_doi) or bool(
                title_value and title_value in seen_title
            )
            keep.append(not duplicate)
            if not duplicate:
                if doi_key:
                    seen_doi.add(doi_key)
                if title_value:
                    seen_title.add(title_value)
        unique = ranked.loc[keep, COLUMNS].sort_values(
            ["Year", "Title"], ascending=[False, True], kind="stable"
        )
        return raw, unique.reset_index(drop=True)

    def run(self, max_per_source: int, output_dir: Path) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        self.fetch_openalex(max_per_source)
        self.fetch_europe_pmc(max_per_source)
        self.fetch_crossref(max_per_source)
        self.fetch_doaj(min(max_per_source, 100))
        raw, unique = self.deduplicate()

        raw_csv = output_dir / f"{RECORD_ID}_raw_records.csv"
        unique_csv = output_dir / f"{RECORD_ID}_deduplicated_records.csv"
        workbook = output_dir / f"{RECORD_ID}_literature_search.xlsx"
        metadata_path = output_dir / f"{RECORD_ID}_run_metadata.json"

        raw.to_csv(raw_csv, index=False, encoding="utf-8-sig")
        unique.to_csv(unique_csv, index=False, encoding="utf-8-sig")
        source_counts = Counter(raw.get("Database", pd.Series(dtype=str)).tolist())
        summary = pd.DataFrame(
            [
                {"Metric": "PROSPERO record", "Value": RECORD_ID},
                {"Metric": "Run timestamp (UTC)", "Value": self.retrieved_utc},
                {"Metric": "Search query", "Value": self.query},
                {"Metric": "Raw records", "Value": len(raw)},
                {"Metric": "Unique records", "Value": len(unique)},
                {"Metric": "Duplicates removed", "Value": len(raw) - len(unique)},
                *[
                    {"Metric": f"Raw records: {source}", "Value": count}
                    for source, count in sorted(source_counts.items())
                ],
            ]
        )
        errors_df = pd.DataFrame(self.errors, columns=["source", "error"])
        with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
            unique.to_excel(writer, sheet_name="Deduplicated", index=False)
            raw.to_excel(writer, sheet_name="Raw records", index=False)
            summary.to_excel(writer, sheet_name="Run summary", index=False)
            errors_df.to_excel(writer, sheet_name="API errors", index=False)

        metadata = {
            "prospero_record": RECORD_ID,
            "repository": "Researchteamforus/ChatGpt",
            "retrieved_utc": self.retrieved_utc,
            "query": self.query,
            "max_per_source": max_per_source,
            "raw_records": len(raw),
            "unique_records": len(unique),
            "duplicates_removed": len(raw) - len(unique),
            "source_counts": dict(sorted(source_counts.items())),
            "errors": self.errors,
            "outputs": [path.name for path in (raw_csv, unique_csv, workbook)],
            "scope_note": (
                "Public-API supplementary search. It does not substitute for direct Google Scholar "
                "or Scopus searching specified in the protocol."
            ),
        }
        metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(metadata, indent=2, ensure_ascii=False))
        if unique.empty:
            raise RuntimeError("All API searches completed without any extractable records")
        return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--max-per-source", type=int, default=100)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs") / RECORD_ID)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    extractor = LiteratureExtractor(args.query)
    extractor.run(max_per_source=max(1, args.max_per_source), output_dir=args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
