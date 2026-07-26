#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import re
import time
import traceback
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

UA = "Mozilla/5.0 SRMA-Bangladesh-Open-Source-Completion/4.0"
TIMEOUT = 180

BANGJ_QUERIES = [
    "Bangladesh immunization child",
    "Bangladesh vaccination coverage",
    "EPI Bangladesh child",
    "zero-dose Bangladesh",
    "vaccination dropout Bangladesh",
    "vaccination timeliness Bangladesh",
]

GIM_QUERIES = [
    'tw:(Bangladesh) AND tw:(immunization OR immunisation OR vaccination) AND tw:(child OR children OR infant OR infants)',
    'tw:(Bangladesh) AND tw:(vaccination coverage OR immunization coverage OR immunisation coverage)',
    'tw:(Bangladesh) AND tw:(zero-dose OR "zero dose" OR dropout OR incomplete vaccination)',
    'tw:(Bangladesh) AND tw:(vaccination timeliness OR delayed vaccination OR schedule adherence)',
    'tw:(Bangladesh) AND tw:(EPI OR "expanded programme on immunization")',
]

IRIS_ITEMS = [
    ("NEW-CORR-013", "4e7db731-8996-433a-a04c-a873907edfb4", "Expanded programme on Immunization (EPI) factsheet 2024: Bangladesh"),
    ("NEW-CORR-015", "22f253f9-a42d-4143-8199-1bfad3cf559a", "Bangladesh factsheet 2020: expanded programme on Immunization (EPI)"),
    ("NEW-CORR-016", "cbbf3bde-d318-4462-8eb4-3d94ac7062ee", "Expanded programme on Immunization (EPI) factsheet 2019: Bangladesh"),
    ("NEW-CORR-018", "c4ed9c2a-9d6d-45a7-a4ab-e0bb020cac7b", "Expanded programme on immunization (EPI): Bangladesh 2021 Factsheet"),
    ("NEW-CORR-022", "03f3ba2c-ca6f-41c8-89a7-34818519ea50", "The impact of the Global Polio Eradication Initiative on the financing of routine immunization"),
    ("NEW-CORR-024", "58bce329-25b9-414e-a279-e412a9c751b2", "Use of tetanus toxoid for the prevention of neonatal tetanus"),
    ("NEW-CORR-027", "a8a96533-4365-4301-9791-f949967ca3fe", "Post-Introduction Evaluation of Pneumococcal Conjugated and Inactivated Poliomyelitis Vaccines"),
    ("NEW-CORR-028", "2d66b328-f725-4153-8c84-a651095ba6a9", "Management of poliomyelitis eradication in Myanmar and Bangladesh border areas"),
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def make_session() -> requests.Session:
    retry = Retry(
        total=8,
        connect=8,
        read=8,
        status=8,
        backoff_factor=2,
        status_forcelist=[408, 425, 429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.headers.update({"User-Agent": UA, "Accept": "text/html,application/json,*/*"})
    return session


def write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if columns is None:
        columns, seen = [], set()
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    columns.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def save_response(response: requests.Response, base: Path) -> Path:
    base.parent.mkdir(parents=True, exist_ok=True)
    content_type = response.headers.get("content-type", "").lower()
    suffix = ".json" if "json" in content_type or response.text.lstrip().startswith(("{", "[")) else ".html"
    path = base.with_suffix(suffix)
    path.write_bytes(response.content)
    return path


def meta_value(soup: BeautifulSoup, names: list[str]) -> str:
    targets = {name.lower() for name in names}
    for tag in soup.find_all("meta"):
        key = clean(tag.get("name") or tag.get("property")).lower()
        if key in targets:
            return clean(tag.get("content"))
    return ""


def run_banglajol(session: requests.Session, out: Path) -> dict:
    base_urls = [
        "https://www.banglajol.info/index.php/index/search/search",
        "https://www.banglajol.info/index.php/index/search",
    ]
    raw = out / "BanglaJOL" / "raw"
    links: dict[str, set[str]] = {}
    logs: list[dict] = []
    errors: list[dict] = []

    for qnum, query in enumerate(BANGJ_QUERIES, 1):
        search_id = f"BANGLAJOL-V4-{qnum:02d}"
        found: list[str] = []
        for endpoint in base_urls:
            endpoint_success = False
            for page in range(1, 101):
                try:
                    response = session.get(endpoint, params={"query": query, "page": page}, timeout=TIMEOUT)
                    saved = save_response(response, raw / search_id / f"endpoint_{base_urls.index(endpoint)+1}_page_{page:04d}")
                    if response.status_code >= 400:
                        errors.append({"Search_ID": search_id, "Endpoint": endpoint, "Page": page, "Status": response.status_code, "Error": "HTTP error"})
                        break
                    soup = BeautifulSoup(response.text, "html.parser")
                    page_links = []
                    for anchor in soup.find_all("a", href=True):
                        href = urljoin(response.url, anchor["href"]).split("#")[0]
                        if re.search(r"/article/view/\d+", href):
                            page_links.append(href)
                    page_links = list(dict.fromkeys(page_links))
                    new_links = [link for link in page_links if link not in found]
                    if not new_links:
                        if page == 1:
                            continue
                        break
                    endpoint_success = True
                    found.extend(new_links)
                    for link in new_links:
                        links.setdefault(link, set()).add(search_id)
                    time.sleep(1.0)
                except Exception as exc:
                    errors.append({"Search_ID": search_id, "Endpoint": endpoint, "Page": page, "Status": "", "Error": repr(exc)})
                    break
            if endpoint_success:
                break
        logs.append({"Search_ID": search_id, "Exact_Query": query, "Execution_UTC": now(), "Article_Links": len(found)})

    records: list[dict] = []
    for index, (url, search_ids) in enumerate(sorted(links.items()), 1):
        try:
            response = session.get(url, timeout=TIMEOUT)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            article_id = re.search(r"/article/view/(\d+)", url)
            date = meta_value(soup, ["citation_publication_date", "dc.date"])
            year_match = re.search(r"\b(18|19|20)\d{2}\b", date)
            authors = [clean(tag.get("content")) for tag in soup.find_all("meta", attrs={"name": "citation_author"}) if clean(tag.get("content"))]
            records.append({
                "Source_Record_ID": f"BANGLAJOL_{article_id.group(1) if article_id else index}",
                "Source": "BanglaJOL",
                "Search_IDs": "; ".join(sorted(search_ids)),
                "Title": meta_value(soup, ["citation_title", "dc.title", "og:title"]),
                "Authors": "; ".join(authors),
                "Publication_Year": year_match.group(0) if year_match else "",
                "Publication_Date": date,
                "Journal": meta_value(soup, ["citation_journal_title", "dc.source"]),
                "DOI": meta_value(soup, ["citation_doi"]),
                "Abstract": meta_value(soup, ["dc.description", "description", "og:description"]),
                "Keywords": meta_value(soup, ["citation_keywords", "dc.subject"]),
                "Landing_Page_URL": url,
                "PDF_URL": meta_value(soup, ["citation_pdf_url"]),
            })
        except Exception as exc:
            errors.append({"Search_ID": "; ".join(sorted(search_ids)), "Endpoint": url, "Page": "article", "Status": "", "Error": repr(exc)})

    write_csv(out / "BanglaJOL" / "BanglaJOL_v4_Search_Log.csv", logs)
    write_csv(out / "BanglaJOL" / "BanglaJOL_v4_Records.csv", records)
    write_csv(out / "BanglaJOL" / "BanglaJOL_v4_Errors.csv", errors)
    return {"records": len(records), "queries": len(logs), "errors": len(errors)}


def run_gim(session: requests.Session, out: Path) -> dict:
    endpoint = "https://pesquisa.bvsalud.org/gim/"
    raw = out / "WHO_GIM_IMSEAR" / "raw"
    links: dict[str, set[str]] = {}
    logs: list[dict] = []
    errors: list[dict] = []

    for qnum, query in enumerate(GIM_QUERIES, 1):
        search_id = f"GIM-IMSEAR-V4-{qnum:02d}"
        found: list[str] = []
        for page in range(1, 101):
            try:
                response = session.get(endpoint, params={"lang": "en", "q": query, "page": page, "count": 50}, timeout=TIMEOUT)
                saved = save_response(response, raw / search_id / f"page_{page:04d}")
                if response.status_code >= 400:
                    errors.append({"Search_ID": search_id, "Page": page, "Status": response.status_code, "Error": "HTTP error"})
                    break
                soup = BeautifulSoup(response.text, "html.parser")
                page_links = []
                for anchor in soup.find_all("a", href=True):
                    href = urljoin(response.url, anchor["href"]).split("#")[0]
                    if "/gim/resource/" in href:
                        page_links.append(href)
                page_links = list(dict.fromkeys(page_links))
                new_links = [link for link in page_links if link not in found]
                if not new_links:
                    if page == 1:
                        break
                    break
                found.extend(new_links)
                for link in new_links:
                    links.setdefault(link, set()).add(search_id)
                time.sleep(0.75)
            except Exception as exc:
                errors.append({"Search_ID": search_id, "Page": page, "Status": "", "Error": repr(exc)})
                break
        logs.append({"Search_ID": search_id, "Exact_Query": query, "Execution_UTC": now(), "Resource_Links": len(found)})

    records: list[dict] = []
    for index, (url, search_ids) in enumerate(sorted(links.items()), 1):
        try:
            response = session.get(url, timeout=TIMEOUT)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            page_text = clean(soup.get_text(" ", strip=True))
            title = clean((soup.find("h1") or soup.find("h2") or soup.title).get_text(" ", strip=True)) if (soup.find("h1") or soup.find("h2") or soup.title) else ""
            resource_match = re.search(r"/resource/[^/]+/([^/?]+)", url)
            is_imsear = "IMSEAR" in page_text or (resource_match and resource_match.group(1).startswith("sea-"))
            if not is_imsear:
                continue
            year_match = re.search(r"(?:Year|Ano de publicação|Publication year)\s*:?\s*((?:18|19|20)\d{2})", page_text, re.I)
            abstract = ""
            for label in ["Abstract", "Resumo", "Resumen"]:
                node = soup.find(string=re.compile(rf"^{label}$", re.I))
                if node:
                    container = node.parent.find_next()
                    abstract = clean(container.get_text(" ", strip=True)) if container else ""
                    if abstract:
                        break
            records.append({
                "Source_Record_ID": resource_match.group(1) if resource_match else f"GIM_{index}",
                "Source": "WHO Global Index Medicus / IMSEAR",
                "Search_IDs": "; ".join(sorted(search_ids)),
                "Title": title,
                "Publication_Year": year_match.group(1) if year_match else "",
                "Abstract_or_Page_Snippet": abstract or page_text[:2000],
                "Landing_Page_URL": url,
                "IMSEAR_Confirmed": "Yes",
            })
        except Exception as exc:
            errors.append({"Search_ID": "; ".join(sorted(search_ids)), "Page": url, "Status": "", "Error": repr(exc)})

    write_csv(out / "WHO_GIM_IMSEAR" / "GIM_IMSEAR_v4_Search_Log.csv", logs)
    write_csv(out / "WHO_GIM_IMSEAR" / "GIM_IMSEAR_v4_Records.csv", records)
    write_csv(out / "WHO_GIM_IMSEAR" / "GIM_IMSEAR_v4_Errors.csv", errors)
    return {"records": len(records), "queries": len(logs), "errors": len(errors)}


def embedded_items(data: dict, preferred: list[str]) -> list[dict]:
    embedded = data.get("_embedded", {}) if isinstance(data, dict) else {}
    for key in preferred:
        value = embedded.get(key)
        if isinstance(value, list):
            return value
    for value in embedded.values():
        if isinstance(value, list):
            return value
    return []


def metadata_text(metadata: dict, keys: list[str]) -> str:
    for key in keys:
        values = metadata.get(key, []) if isinstance(metadata, dict) else []
        if isinstance(values, list):
            found = [clean(value.get("value")) for value in values if isinstance(value, dict) and clean(value.get("value"))]
            if found:
                return "; ".join(found)
    return ""


def run_iris_retrieval(session: requests.Session, out: Path) -> dict:
    base = "https://iris.who.int/server/api"
    raw = out / "WHO_IRIS_Missing_8" / "raw"
    pdf_dir = out / "WHO_IRIS_Missing_8" / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    errors: list[dict] = []

    for candidate_id, item_uuid, expected_title in IRIS_ITEMS:
        try:
            item_response = session.get(f"{base}/core/items/{item_uuid}", timeout=TIMEOUT)
            save_response(item_response, raw / candidate_id / "item")
            item_response.raise_for_status()
            item = item_response.json()
            metadata = item.get("metadata", {})

            bundle_response = session.get(f"{base}/core/bundles/search/byItem", params={"uuid": item_uuid, "size": 100}, timeout=TIMEOUT)
            save_response(bundle_response, raw / candidate_id / "bundles")
            bundle_response.raise_for_status()
            bundles = embedded_items(bundle_response.json(), ["bundles"])

            bitstream_rows = []
            for bundle in bundles:
                bundle_uuid = clean(bundle.get("uuid"))
                if not bundle_uuid:
                    continue
                bits_response = session.get(f"{base}/core/bitstreams/search/byBundle", params={"uuid": bundle_uuid, "size": 100}, timeout=TIMEOUT)
                save_response(bits_response, raw / candidate_id / f"bitstreams_{bundle_uuid}")
                if bits_response.status_code >= 400:
                    continue
                for bitstream in embedded_items(bits_response.json(), ["bitstreams"]):
                    bit_uuid = clean(bitstream.get("uuid"))
                    name = clean(bitstream.get("name"))
                    mime = clean(bitstream.get("metadata", {}).get("dc.format.mimetype", [{}])[0].get("value") if isinstance(bitstream.get("metadata"), dict) else "")
                    content_url = f"{base}/core/bitstreams/{bit_uuid}/content" if bit_uuid else ""
                    downloaded_file = ""
                    if bit_uuid and (name.lower().endswith(".pdf") or "pdf" in mime.lower()):
                        file_response = session.get(content_url, timeout=TIMEOUT)
                        if file_response.status_code < 400:
                            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", name or f"{candidate_id}.pdf")
                            target = pdf_dir / f"{candidate_id}_{safe_name}"
                            target.write_bytes(file_response.content)
                            downloaded_file = str(target.relative_to(out))
                    bitstream_rows.append({"name": name, "mime": mime, "content_url": content_url, "downloaded_file": downloaded_file})

            rows.append({
                "Candidate_ID": candidate_id,
                "Item_UUID": item_uuid,
                "Expected_Title": expected_title,
                "Retrieved_Title": metadata_text(metadata, ["dc.title"]) or clean(item.get("name")),
                "Publication_Date": metadata_text(metadata, ["dc.date.issued", "dc.date"]),
                "Authors": metadata_text(metadata, ["dc.contributor.author", "dc.creator"]),
                "Description_or_Abstract": metadata_text(metadata, ["dc.description.abstract", "dc.description"]),
                "Subjects": metadata_text(metadata, ["dc.subject"]),
                "Landing_Page_URL": metadata_text(metadata, ["dc.identifier.uri"]) or f"https://iris.who.int/items/{item_uuid}/full",
                "Bitstream_Count": len(bitstream_rows),
                "Downloaded_PDFs": "; ".join(bit["downloaded_file"] for bit in bitstream_rows if bit["downloaded_file"]),
                "Bitstream_Details_JSON": json.dumps(bitstream_rows, ensure_ascii=False),
                "Retrieval_Status": "Completed",
            })
        except Exception as exc:
            errors.append({"Candidate_ID": candidate_id, "Item_UUID": item_uuid, "Error": repr(exc), "Traceback": traceback.format_exc()})
            rows.append({"Candidate_ID": candidate_id, "Item_UUID": item_uuid, "Expected_Title": expected_title, "Retrieval_Status": "Failed"})

    write_csv(out / "WHO_IRIS_Missing_8" / "WHO_IRIS_Missing_8_Metadata_and_PDF_Register.csv", rows)
    write_csv(out / "WHO_IRIS_Missing_8" / "WHO_IRIS_Missing_8_Errors.csv", errors)
    return {"items": len(rows), "successful": sum(row.get("Retrieval_Status") == "Completed" for row in rows), "errors": len(errors)}


def main() -> int:
    out = Path.cwd() / f"SRMA_OPEN_SOURCE_COMPLETION_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    out.mkdir()
    session = make_session()
    results, errors = {}, []

    for name, function in [
        ("BanglaJOL", run_banglajol),
        ("WHO GIM / IMSEAR", run_gim),
        ("WHO IRIS missing 8", run_iris_retrieval),
    ]:
        try:
            results[name] = function(session, out)
            print(name, results[name], flush=True)
        except Exception as exc:
            errors.append({"Workstream": name, "Error": repr(exc), "Traceback": traceback.format_exc()})
            print(name, "FAILED", repr(exc), flush=True)

    summary = {"Execution_UTC": now(), "Results": results, "Top_Level_Errors": errors}
    (out / "SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if errors:
        (out / "ERRORS.json").write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest = []
    for path in sorted(out.rglob("*")):
        if path.is_file():
            manifest.append({
                "Relative_Path": str(path.relative_to(out)),
                "Size_Bytes": path.stat().st_size,
                "SHA256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
    write_csv(out / "FILE_INTEGRITY_MANIFEST.csv", manifest)

    zip_path = out.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in out.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(out))
    print("UPLOAD:", zip_path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
