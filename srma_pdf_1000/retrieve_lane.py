#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import random
import re
import time
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote, urljoin

import requests

UA = (
    "Mozilla/5.0 (compatible; SRMA-Bangladesh-Child-Immunization/2.0; "
    "+https://github.com/Researchteamforus/ChatGpt)"
)
MAX_BYTES = 40 * 1024 * 1024
MIN_PDF_BYTES = 1024


def clean_doi(value: str) -> str:
    value = (value or "").strip()
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value, flags=re.I)
    value = re.sub(r"^doi:\s*", "", value, flags=re.I)
    return value.strip().rstrip(".,;)")


def normalize_title(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", " ", (value or "").lower())
    return " ".join(value.split())


def title_match(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value or "record")[:150]


def add_candidate(candidates: list[tuple[str, str]], url: str, method: str) -> None:
    url = (url or "").strip()
    if not url or not url.lower().startswith(("http://", "https://")):
        return
    if any(existing == url for existing, _ in candidates):
        return
    candidates.append((url, method))


def metadata_pdf_urls(text: str, base_url: str) -> list[str]:
    patterns = [
        r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url["\']',
        r'<meta[^>]+name=["\']pdf_url["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+property=["\']og:pdf["\'][^>]+content=["\']([^"\']+)["\']',
        r'<link[^>]+type=["\']application/pdf["\'][^>]+href=["\']([^"\']+)["\']',
    ]
    urls = []
    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.I):
            url = urljoin(base_url, html.unescape(match))
            if url not in urls:
                urls.append(url)
    for match in re.findall(r'href=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']', text, flags=re.I):
        url = urljoin(base_url, html.unescape(match))
        if url not in urls:
            urls.append(url)
    return urls[:12]


def fetch_json(session: requests.Session, url: str, params: dict | None = None) -> dict | None:
    try:
        response = session.get(url, params=params, timeout=25, headers={"Accept": "application/json"})
        if response.status_code == 200:
            return response.json()
    except Exception:
        return None
    return None


def discovery_candidates(session: requests.Session, row: dict) -> list[tuple[str, str]]:
    candidates = []
    for field, method in [
        ("fulltext_url", "manifest_fulltext_url"),
        ("resolver_url", "manifest_resolver_url"),
        ("article_url", "manifest_article_url"),
        ("previous_final_url", "previous_final_url"),
    ]:
        add_candidate(candidates, row.get(field, ""), method)

    doi = clean_doi(row.get("doi", ""))
    pmid = (row.get("pmid", "") or "").strip()
    title = row.get("title", "") or ""

    if doi:
        add_candidate(candidates, f"https://doi.org/{doi}", "doi_resolver")

        openalex = fetch_json(session, f"https://api.openalex.org/works/https://doi.org/{quote(doi, safe='')}")
        if openalex:
            locations = []
            for key in ("best_oa_location", "primary_location"):
                locations.append(openalex.get(key) or {})
            locations.extend(openalex.get("locations") or [])
            for location in locations:
                add_candidate(candidates, location.get("pdf_url") or "", "openalex_pdf")
                add_candidate(candidates, location.get("landing_page_url") or "", "openalex_landing")

        crossref = fetch_json(session, f"https://api.crossref.org/works/{quote(doi, safe='')}")
        if crossref:
            message = crossref.get("message") or {}
            for link in message.get("link") or []:
                add_candidate(candidates, link.get("URL") or "", "crossref_link")
            for relation in (message.get("relation") or {}).values():
                for related in relation or []:
                    add_candidate(candidates, related.get("id") or "", "crossref_relation")

        europepmc = fetch_json(
            session,
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={"query": f'DOI:"{doi}"', "format": "json", "pageSize": 3},
        )
        if europepmc:
            for item in ((europepmc.get("resultList") or {}).get("result") or []):
                pmcid = item.get("pmcid") or ""
                if pmcid:
                    add_candidate(candidates, f"https://europepmc.org/articles/{pmcid}?pdf=render", "europepmc_pdf")
                    add_candidate(candidates, f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/", "pmc_pdf")

    if pmid:
        europepmc = fetch_json(
            session,
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={"query": f'EXT_ID:{pmid}', "format": "json", "pageSize": 3},
        )
        if europepmc:
            for item in ((europepmc.get("resultList") or {}).get("result") or []):
                pmcid = item.get("pmcid") or ""
                if pmcid:
                    add_candidate(candidates, f"https://europepmc.org/articles/{pmcid}?pdf=render", "europepmc_pmid_pdf")
                    add_candidate(candidates, f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/", "pmc_pmid_pdf")

    if title:
        openalex_search = fetch_json(
            session,
            "https://api.openalex.org/works",
            params={"search": title[:250], "per-page": 5},
        )
        if openalex_search:
            for work in openalex_search.get("results") or []:
                if title_match(title, work.get("title") or "") < 0.90:
                    continue
                locations = [work.get("best_oa_location") or {}, work.get("primary_location") or {}]
                locations.extend(work.get("locations") or [])
                for location in locations:
                    add_candidate(candidates, location.get("pdf_url") or "", "openalex_title_pdf")
                    add_candidate(candidates, location.get("landing_page_url") or "", "openalex_title_landing")
                break

    return candidates[:28]


def save_pdf_response(response: requests.Response, output_dir: Path, integrated_id: str) -> tuple[bool, str, int, str]:
    filename = safe_name(integrated_id) + ".pdf"
    path = output_dir / filename
    total = 0
    digest = hashlib.sha256()
    with path.open("wb") as handle:
        for chunk in response.iter_content(1024 * 128):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_BYTES:
                path.unlink(missing_ok=True)
                return False, "", total, "PDF exceeded size limit"
            digest.update(chunk)
            handle.write(chunk)
    if total < MIN_PDF_BYTES:
        path.unlink(missing_ok=True)
        return False, "", total, "File too small to be a valid PDF"
    with path.open("rb") as handle:
        prefix = handle.read(5)
    if prefix != b"%PDF-":
        path.unlink(missing_ok=True)
        return False, "", total, "Downloaded content did not have a PDF header"
    return True, filename, total, digest.hexdigest()


def attempt_url(session: requests.Session, url: str, method: str, output_dir: Path, integrated_id: str, visited: set[str]) -> dict:
    result = {
        "url": url,
        "method": method,
        "status": "",
        "http_status": "",
        "final_url": "",
        "content_type": "",
        "saved_file": "",
        "size_bytes": "",
        "sha256": "",
        "notes": "",
    }
    if not url or url in visited:
        result["status"] = "skipped_duplicate_url"
        return result
    visited.add(url)
    try:
        response = session.get(url, timeout=35, allow_redirects=True, stream=True)
        result["http_status"] = str(response.status_code)
        result["final_url"] = response.url
        content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
        result["content_type"] = content_type
        if response.status_code >= 400:
            result["status"] = "http_error"
            result["notes"] = f"HTTP {response.status_code}"
            return result

        prefix = b""
        try:
            prefix = response.raw.read(5, decode_content=True)
        except Exception:
            prefix = b""
        is_pdf = (
            content_type == "application/pdf"
            or response.url.lower().split("?")[0].endswith(".pdf")
            or prefix == b"%PDF-"
        )
        if is_pdf:
            response.close()
            response = session.get(url, timeout=35, allow_redirects=True, stream=True)
            ok, filename, size, detail = save_pdf_response(response, output_dir, integrated_id)
            result["size_bytes"] = str(size)
            if ok:
                result["status"] = "pdf_saved"
                result["saved_file"] = filename
                result["sha256"] = detail
            else:
                result["status"] = "invalid_pdf"
                result["notes"] = detail
            return result

        response.close()
        html_response = session.get(url, timeout=35, allow_redirects=True)
        page_text = html_response.text[:3_000_000]
        pdf_urls = metadata_pdf_urls(page_text, html_response.url)
        if not pdf_urls:
            result["status"] = "landing_page_only"
            result["notes"] = "No explicit open PDF link found in page metadata or HTML"
            return result
        result["status"] = "landing_page_with_pdf_candidates"
        result["notes"] = " | ".join(pdf_urls[:5])
        result["discovered_pdf_urls"] = pdf_urls
        return result
    except Exception as exc:
        result["status"] = "request_failed"
        result["notes"] = f"{type(exc).__name__}: {exc}"
        return result


def retrieve_record(session: requests.Session, row: dict, output_dir: Path) -> dict:
    candidates = discovery_candidates(session, row)
    attempts = []
    visited: set[str] = set()
    queue = list(candidates)
    while queue and len(attempts) < 40:
        url, method = queue.pop(0)
        attempt = attempt_url(session, url, method, output_dir, row["integrated_id"], visited)
        discovered = attempt.pop("discovered_pdf_urls", [])
        attempts.append(attempt)
        if attempt["status"] == "pdf_saved":
            return {
                **row,
                "retrieval_status": "PDF saved",
                "winning_method": method,
                "winning_url": attempt["final_url"] or url,
                "saved_file": attempt["saved_file"],
                "size_bytes": attempt["size_bytes"],
                "sha256": attempt["sha256"],
                "attempt_count": len(attempts),
                "attempt_log_json": json.dumps(attempts, ensure_ascii=False),
            }
        for pdf_url in discovered:
            if pdf_url not in visited:
                queue.insert(0, (pdf_url, method + ":html_pdf_link"))
        time.sleep(1.2)

    statuses = [attempt["status"] for attempt in attempts]
    if "landing_page_only" in statuses or "landing_page_with_pdf_candidates" in statuses:
        final_status = "Landing page reached—no open PDF saved"
    elif "http_error" in statuses:
        final_status = "HTTP/access failure—manual route needed"
    elif attempts:
        final_status = "Automated retrieval failed"
    else:
        final_status = "No candidate URL found"
    return {
        **row,
        "retrieval_status": final_status,
        "winning_method": "",
        "winning_url": "",
        "saved_file": "",
        "size_bytes": "",
        "sha256": "",
        "attempt_count": len(attempts),
        "attempt_log_json": json.dumps(attempts, ensure_ascii=False),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--lane", type=int, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(args.manifest, encoding="utf-8-sig", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if int(row["lane_num"]) == args.lane]

    session = requests.Session()
    session.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/pdf;q=0.9,application/json;q=0.8,*/*;q=0.7",
    })
    random.seed(args.lane)
    time.sleep(random.uniform(0.5, 25.0))

    results = []
    for row in rows:
        results.append(retrieve_record(session, row, output_dir))
        time.sleep(random.uniform(1.0, 3.0))

    result_path = output_dir / f"PDF-AGENT-{args.lane:04d}_results.csv"
    fields = list(results[0].keys()) if results else ["lane_num", "retrieval_status"]
    with result_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)

    summary = {
        "lane": args.lane,
        "records": len(results),
        "pdfs_saved": sum(row["retrieval_status"] == "PDF saved" for row in results),
        "statuses": {
            status: sum(row["retrieval_status"] == status for row in results)
            for status in sorted(set(row["retrieval_status"] for row in results))
        },
    }
    (output_dir / f"PDF-AGENT-{args.lane:04d}_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
