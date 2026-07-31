#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import html
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests

USER_AGENT = (
    "Mozilla/5.0 (compatible; SRMA-Bangladesh-Child-Immunization/1.0; "
    "+systematic-review-retrieval)"
)
MAX_BYTES = 30 * 1024 * 1024

def safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value or "record")
    return value[:120] or "record"

def find_pdf_meta(text: str, base_url: str) -> str:
    patterns = [
        r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url["\']',
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.I)
        if m:
            return urljoin(base_url, html.unescape(m.group(1)))
    return ""

def retrieve(session: requests.Session, url: str, output_dir: Path, record_id: str) -> dict:
    result = {
        "automated_result": "No URL",
        "final_url": "",
        "content_type": "",
        "saved_file": "",
        "http_status": "",
        "notes": "",
    }
    if not url:
        return result
    try:
        response = session.get(url, timeout=35, allow_redirects=True, stream=True)
        result["http_status"] = str(response.status_code)
        result["final_url"] = response.url
        ctype = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
        result["content_type"] = ctype
        if response.status_code >= 400:
            result["automated_result"] = "HTTP error"
            result["notes"] = f"Status {response.status_code}"
            return result

        is_pdf = ctype == "application/pdf" or response.url.lower().split("?")[0].endswith(".pdf")
        if is_pdf:
            filename = safe_name(record_id) + ".pdf"
            path = output_dir / filename
            total = 0
            with path.open("wb") as handle:
                for chunk in response.iter_content(1024 * 128):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_BYTES:
                        path.unlink(missing_ok=True)
                        result["automated_result"] = "PDF too large"
                        result["notes"] = f"Exceeded {MAX_BYTES} bytes"
                        return result
                    handle.write(chunk)
            result["automated_result"] = "PDF saved"
            result["saved_file"] = filename
            return result

        body = response.content[:2_000_000].decode(response.encoding or "utf-8", errors="replace")
        meta_pdf = find_pdf_meta(body, response.url)
        if meta_pdf and meta_pdf != response.url:
            second = session.get(meta_pdf, timeout=35, allow_redirects=True, stream=True)
            ctype2 = (second.headers.get("content-type") or "").split(";")[0].strip().lower()
            if second.status_code < 400 and (
                ctype2 == "application/pdf" or second.url.lower().split("?")[0].endswith(".pdf")
            ):
                filename = safe_name(record_id) + ".pdf"
                path = output_dir / filename
                total = 0
                with path.open("wb") as handle:
                    for chunk in second.iter_content(1024 * 128):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > MAX_BYTES:
                            path.unlink(missing_ok=True)
                            result["automated_result"] = "PDF too large"
                            result["notes"] = f"Exceeded {MAX_BYTES} bytes"
                            return result
                        handle.write(chunk)
                result["automated_result"] = "PDF saved from metadata"
                result["saved_file"] = filename
                result["final_url"] = second.url
                result["content_type"] = ctype2
                return result

        result["automated_result"] = "Landing page reached"
        result["notes"] = "No openly exposed PDF detected; manual/library retrieval may be required."
        return result
    except Exception as exc:
        result["automated_result"] = "Request failed"
        result["notes"] = f"{type(exc).__name__}: {exc}"
        return result

def load_agent_rows(manifest_dir: Path, agent: str) -> list[dict]:
    rows = []
    for path in sorted(manifest_dir.glob("manifest_group_*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("agent_id") == agent:
                    rows.append(row)
    return rows

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest-dir", required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = load_agent_rows(Path(args.manifest_dir), args.agent)

    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.8",
    })

    results = []
    for i, row in enumerate(rows, start=1):
        result = retrieve(session, row.get("resolver_url", ""), output_dir, row["integrated_id"])
        results.append({**row, **result})
        if i < len(rows):
            time.sleep(1.0)

    out_csv = output_dir / f"{args.agent}_retrieval_results.csv"
    fields = list(results[0].keys()) if results else [
        "agent_id", "integrated_id", "resolver_url", "automated_result", "notes"
    ]
    with out_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)

if __name__ == "__main__":
    main()
