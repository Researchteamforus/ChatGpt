#!/usr/bin/env python3
"""Post-160 parallel preparation for the Bangladesh childhood immunization SRMA.

This program performs metadata enrichment, duplicate-family evidence preparation,
lawful open-PDF acquisition, topic/design mapping, PRISMA identification QA, and
blank reviewer calibration preparation. It never creates human screening,
eligibility, extraction, or risk-of-bias decisions.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import shutil
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

PROSPERO = "CRD420261461557"
EMAIL = "st19009@mbstu.ac.bd"
METADATA_AGENTS = 50
DUPLICATE_AGENTS = 30
RETRIEVAL_AGENTS = 30
TOPIC_AGENTS = 20
PRISMA_AGENTS = 10
CALIBRATION_AGENTS = 10
EXPECTED_REVIEW_POOL = 8433
EXPECTED_DUPLICATE_ROWS = 806
EXPECTED_RETRIEVAL_ROWS = 1000
EXPECTED_COMBINED_MASTER = 55248
EXPECTED_SEARCH_UNITS = 58


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def nt(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def norm_doi(value: Any) -> str:
    text = nt(value).lower()
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text)
    text = re.sub(r"^doi:\s*", "", text)
    return text.rstrip(".,; )]")


def norm_title(value: Any) -> str:
    text = unicodedata.normalize("NFKD", nt(value)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def strip_markup(value: Any) -> str:
    text = html.unescape(nt(value))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str] | None = None) -> None:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or (list(rows[0]) if rows else [])
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        if fields:
            writer.writeheader()
            writer.writerows({key: row.get(key, "") for key in fields} for row in rows)


def find_one(root: Path, name: str) -> Path:
    hits = list(root.rglob(name))
    if not hits:
        raise FileNotFoundError(f"{name} not found under {root}")
    return max(hits, key=lambda path: path.stat().st_size)


def round_robin(rows: list[dict[str, str]], n: int) -> list[list[dict[str, str]]]:
    shards = [[] for _ in range(n)]
    for index, row in enumerate(rows):
        shards[index % n].append(row)
    return shards


def split_grouped(groups: list[list[dict[str, str]]], n: int) -> list[list[dict[str, str]]]:
    shards = [[] for _ in range(n)]
    loads = [0] * n
    for group in sorted(groups, key=len, reverse=True):
        target = min(range(n), key=lambda idx: loads[idx])
        shards[target].extend(group)
        loads[target] += len(group)
    return shards


def stable_order(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(rows, key=lambda row: hashlib.sha256(nt(row.get("Record_ID")).encode()).hexdigest())


def prepare(args: argparse.Namespace) -> None:
    root, out = Path(args.input), Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    high = read_csv(find_one(root, "openalex_high_priority.csv"))
    unclear = read_csv(find_one(root, "openalex_unclear_review.csv"))
    duplicates = read_csv(find_one(root, "probable_duplicate_family_candidates.csv"))
    combined = read_csv(find_one(root, "combined_discovery_master_55248.csv"))
    routes = read_csv(find_one(root, "top1000_retrieval_routes.csv"))
    search_qa = read_csv(find_one(root, "openalex_search_unit_qa.csv"))

    review_pool = high + unclear
    assert len(review_pool) == EXPECTED_REVIEW_POOL, len(review_pool)
    assert len(duplicates) == EXPECTED_DUPLICATE_ROWS, len(duplicates)
    assert len(combined) == EXPECTED_COMBINED_MASTER, len(combined)
    assert len(routes) == EXPECTED_RETRIEVAL_ROWS, len(routes)
    assert len(search_qa) == EXPECTED_SEARCH_UNITS, len(search_qa)

    combined_by_id = {row.get("Record_ID", ""): row for row in combined}
    duplicate_enriched = []
    for row in duplicates:
        master = combined_by_id.get(row.get("Record_ID", ""), {})
        duplicate_enriched.append({
            **row,
            "Master_Set": master.get("Master_Set", ""),
            "Abstract": master.get("Abstract", ""),
            "PMID": master.get("PMID", ""),
            "URL": master.get("URL", ""),
            "Machine_Priority": master.get("Machine_Priority", ""),
        })

    for index, shard in enumerate(round_robin(review_pool, METADATA_AGENTS), 1):
        write_csv(out / "metadata_shards" / f"metadata_{index:02d}.csv", shard, list(review_pool[0]))

    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in duplicate_enriched:
        grouped[row.get("Candidate_Key", "")].append(row)
    for index, shard in enumerate(split_grouped(list(grouped.values()), DUPLICATE_AGENTS), 1):
        write_csv(out / "duplicate_shards" / f"duplicate_{index:02d}.csv", shard, list(duplicate_enriched[0]))

    for index, shard in enumerate(round_robin(routes, RETRIEVAL_AGENTS), 1):
        write_csv(out / "retrieval_shards" / f"retrieval_{index:02d}.csv", shard, list(routes[0]))

    for index, shard in enumerate(round_robin(review_pool, TOPIC_AGENTS), 1):
        write_csv(out / "topic_shards" / f"topic_{index:02d}.csv", shard, list(review_pool[0]))

    for index, shard in enumerate(round_robin(combined, PRISMA_AGENTS), 1):
        write_csv(out / "prisma_shards" / f"records_{index:02d}.csv", shard, list(combined[0]))
    for index, shard in enumerate(round_robin(search_qa, PRISMA_AGENTS), 1):
        write_csv(out / "prisma_shards" / f"search_{index:02d}.csv", shard, list(search_qa[0]))

    high_ordered = stable_order(high)
    unclear_ordered = stable_order(unclear)
    for index in range(1, CALIBRATION_AGENTS + 1):
        batch = high_ordered[(index - 1) * 40:index * 40] + unclear_ordered[(index - 1) * 60:index * 60]
        batch = stable_order(batch)
        assert len(batch) == 100, (index, len(batch))
        write_csv(out / "calibration_shards" / f"calibration_{index:02d}.csv", batch, list(review_pool[0]))

    summary = {
        "prospero": PROSPERO,
        "review_pool_records": len(review_pool),
        "duplicate_candidate_rows": len(duplicates),
        "duplicate_candidate_groups": len(grouped),
        "retrieval_route_rows": len(routes),
        "combined_master_rows": len(combined),
        "search_units": len(search_qa),
        "agents_planned": METADATA_AGENTS + DUPLICATE_AGENTS + RETRIEVAL_AGENTS + TOPIC_AGENTS + PRISMA_AGENTS + CALIBRATION_AGENTS,
        "formal_human_decisions": 0,
        "generated_utc": now(),
    }
    (out / "prepare_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


def epmc_lookup(session, doi: str, pmid: str) -> dict[str, str]:
    if not doi and not pmid:
        return {}
    query = f"EXT_ID:{pmid}" if pmid else f'DOI:"{doi}"'
    response = session.get(
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
        params={"query": query, "format": "json", "pageSize": 3, "resultType": "core"},
        timeout=30,
    )
    response.raise_for_status()
    hits = ((response.json().get("resultList") or {}).get("result") or [])
    if not hits:
        return {}
    hit = hits[0]
    return {
        "title": nt(hit.get("title")),
        "abstract": nt(hit.get("abstractText")),
        "authors": nt(hit.get("authorString")),
        "journal": nt(hit.get("journalTitle")),
        "year": nt(hit.get("pubYear")),
        "pmid": nt(hit.get("pmid")),
        "pmcid": nt(hit.get("pmcid")),
        "oa": nt(hit.get("isOpenAccess")),
    }


def crossref_lookup(session, doi: str) -> dict[str, str]:
    if not doi:
        return {}
    response = session.get(f"https://api.crossref.org/works/{quote(doi, safe='')}", timeout=30)
    response.raise_for_status()
    message = response.json().get("message") or {}
    authors = []
    for author in message.get("author") or []:
        name = " ".join(filter(None, [nt(author.get("given")), nt(author.get("family"))])).strip()
        if name:
            authors.append(name)
    year = ""
    for field in ["published-print", "published-online", "issued"]:
        parts = ((message.get(field) or {}).get("date-parts") or [])
        if parts and parts[0]:
            year = nt(parts[0][0])
            break
    title = nt((message.get("title") or [""])[0])
    journal = nt((message.get("container-title") or [""])[0])
    return {
        "title": title,
        "abstract": strip_markup(message.get("abstract")),
        "authors": "; ".join(authors),
        "journal": journal,
        "year": year,
        "doi": norm_doi(message.get("DOI")),
    }


def metadata_agent(args: argparse.Namespace) -> None:
    root, out, agent = Path(args.input), Path(args.out), int(args.agent)
    out.mkdir(parents=True, exist_ok=True)
    rows = read_csv(find_one(root, f"metadata_{agent:02d}.csv"))
    import requests

    session = requests.Session()
    session.headers.update({"User-Agent": f"SRMA-Bangladesh/{PROSPERO} ({EMAIL})", "Accept": "application/json"})
    output = []
    for row in rows:
        doi = norm_doi(row.get("DOI"))
        pmid = nt(row.get("PMID"))
        original_abstract = nt(row.get("Abstract"))
        epmc: dict[str, str] = {}
        crossref: dict[str, str] = {}
        errors = []
        needs_lookup = not original_abstract or not nt(row.get("PMID")) or not nt(row.get("PMCID"))
        if needs_lookup and (doi or pmid):
            try:
                epmc = epmc_lookup(session, doi, pmid)
            except Exception as exc:  # noqa: BLE001
                errors.append("EuropePMC:" + type(exc).__name__)
        if doi and (not original_abstract or not nt(row.get("Authors")) or not nt(row.get("Journal_or_Institution"))):
            try:
                crossref = crossref_lookup(session, doi)
            except Exception as exc:  # noqa: BLE001
                errors.append("Crossref:" + type(exc).__name__)
        abstracts = [original_abstract, epmc.get("abstract", ""), crossref.get("abstract", "")]
        enriched_abstract = max(abstracts, key=len)
        sources = []
        if enriched_abstract and enriched_abstract == epmc.get("abstract", ""):
            sources.append("Europe PMC abstract")
        elif enriched_abstract and enriched_abstract == crossref.get("abstract", ""):
            sources.append("Crossref abstract")
        elif enriched_abstract:
            sources.append("OpenAlex/original abstract")
        output.append({
            **row,
            "Enriched_Title": nt(row.get("Title")) or epmc.get("title", "") or crossref.get("title", ""),
            "Enriched_Abstract": enriched_abstract,
            "Enriched_Authors": nt(row.get("Authors")) or epmc.get("authors", "") or crossref.get("authors", ""),
            "Enriched_Journal": nt(row.get("Journal_or_Institution")) or epmc.get("journal", "") or crossref.get("journal", ""),
            "Enriched_Year": nt(row.get("Year")) or epmc.get("year", "") or crossref.get("year", ""),
            "Enriched_DOI": doi or crossref.get("doi", ""),
            "Enriched_PMID": pmid or epmc.get("pmid", ""),
            "Enriched_PMCID": nt(row.get("PMCID")) or epmc.get("pmcid", ""),
            "Open_Access_Metadata_Flag": epmc.get("oa", ""),
            "Metadata_Enrichment_Source": "; ".join(sources),
            "Metadata_Enrichment_Errors": "; ".join(errors),
            "Abstract_Available_After_Enrichment": "Yes" if enriched_abstract else "No",
            "Human_Title_Abstract_Decision": "",
            "Human_Reviewer": "",
            "Human_Review_Date": "",
            "Human_Notes": "",
        })
        time.sleep(0.05)
    write_csv(out / f"metadata_{agent:02d}.csv", output)
    (out / "summary.json").write_text(json.dumps({
        "agent": agent,
        "records": len(output),
        "abstracts_available": sum(row["Abstract_Available_After_Enrichment"] == "Yes" for row in output),
        "human_decisions": 0,
    }, indent=2), encoding="utf-8")


def duplicate_agent(args: argparse.Namespace) -> None:
    root, out, agent = Path(args.input), Path(args.out), int(args.agent)
    out.mkdir(parents=True, exist_ok=True)
    rows = read_csv(find_one(root, f"duplicate_{agent:02d}.csv"))
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[row.get("Candidate_Key", "")].append(row)
    output = []
    for key, group in groups.items():
        dois = {norm_doi(row.get("DOI")) for row in group if norm_doi(row.get("DOI"))}
        titles = {norm_title(row.get("Title")) for row in group if norm_title(row.get("Title"))}
        years = {nt(row.get("Year")) for row in group if nt(row.get("Year"))}
        if key.startswith("doi:") and len(dois) == 1:
            assessment = "High-confidence duplicate family"
            evidence = "Shared normalized DOI"
        elif key.startswith("title:") and len(titles) == 1 and len(years) <= 1:
            assessment = "Likely duplicate family"
            evidence = "Shared normalized title and publication year"
        else:
            assessment = "Manual duplicate adjudication required"
            evidence = "Candidate identity evidence is incomplete or discordant"
        canonical = max(group, key=lambda row: (
            bool(nt(row.get("PMID"))),
            bool(norm_doi(row.get("DOI"))),
            len(nt(row.get("Abstract"))),
            bool(nt(row.get("URL"))),
            nt(row.get("Record_ID")),
        )).get("Record_ID", "")
        for row in group:
            output.append({
                **row,
                "Agent": agent,
                "Machine_Duplicate_Assessment": assessment,
                "Machine_Duplicate_Evidence": evidence,
                "Recommended_Canonical_Record": canonical,
                "Metadata_Completeness_Score": sum(bool(nt(row.get(field))) for field in ["Title", "Abstract", "Year", "DOI", "PMID", "URL"]),
                "Human_Adjudication": "Not reviewed",
                "Human_Reviewer": "",
                "Human_Review_Date": "",
                "Human_Notes": "",
            })
    write_csv(out / f"duplicate_{agent:02d}.csv", output)
    (out / "summary.json").write_text(json.dumps({"agent": agent, "groups": len(groups), "rows": len(output), "formal_adjudications": 0}, indent=2), encoding="utf-8")


def parse_routes(value: str) -> list[tuple[str, str]]:
    parsed = []
    for item in nt(value).split(" | "):
        if ":http" not in item:
            continue
        source, url_tail = item.split(":http", 1)
        url = "http" + url_tail
        if url and url not in [existing[1] for existing in parsed]:
            parsed.append((source.strip(), url.strip()))
    priority = {"Europe PMC": 0, "Unpaywall": 1, "OpenAlex/landing": 2, "DOI": 3}
    return sorted(parsed, key=lambda pair: priority.get(pair[0], 9))


def retrieve_pdf(session, url: str, max_bytes: int = 12 * 1024 * 1024) -> tuple[bytes | None, str, str]:
    try:
        response = session.get(url, timeout=45, allow_redirects=True, stream=True)
        status = str(response.status_code)
        if response.status_code >= 400:
            return None, status, "HTTP error"
        chunks = []
        total = 0
        for chunk in response.iter_content(65536):
            if not chunk:
                continue
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                return None, status, "Exceeded 12 MB safety limit"
        content = b"".join(chunks)
        content_type = nt(response.headers.get("Content-Type")).lower()
        if not content.startswith(b"%PDF") and "application/pdf" not in content_type:
            return None, status, "Response was not a PDF"
        if not content.startswith(b"%PDF"):
            marker = content.find(b"%PDF")
            if marker >= 0:
                content = content[marker:]
            else:
                return None, status, "PDF signature missing"
        return content, status, "Verified PDF signature"
    except Exception as exc:  # noqa: BLE001
        return None, "", type(exc).__name__


def retrieval_agent(args: argparse.Namespace) -> None:
    root, out, agent = Path(args.input), Path(args.out), int(args.agent)
    out.mkdir(parents=True, exist_ok=True)
    pdf_dir = out / "pdfs"
    pdf_dir.mkdir()
    rows = read_csv(find_one(root, f"retrieval_{agent:02d}.csv"))
    import requests

    session = requests.Session()
    session.headers.update({"User-Agent": f"SRMA-Bangladesh/{PROSPERO} ({EMAIL})"})
    output = []
    successes = 0
    bytes_saved = 0
    for row in rows:
        record_id = nt(row.get("Record_ID"))
        result = {
            **row,
            "Agent": agent,
            "PDF_Retrieved": "No",
            "Retrieved_From": "",
            "Final_URL": "",
            "HTTP_Status": "",
            "PDF_SHA256": "",
            "PDF_Bytes": "",
            "Saved_File": "",
            "Retrieval_Note": "No verified open PDF found",
            "Human_Verification": "Not reviewed",
            "Human_Notes": "",
        }
        if successes < 12 and bytes_saved < 100 * 1024 * 1024:
            for source, url in parse_routes(row.get("Candidate_Routes", "")):
                content, status, note = retrieve_pdf(session, url)
                result["HTTP_Status"] = status
                result["Retrieval_Note"] = note
                if content:
                    digest = hashlib.sha256(content).hexdigest()
                    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", record_id or digest[:16])
                    path = pdf_dir / f"{safe_id}.pdf"
                    path.write_bytes(content)
                    result.update({
                        "PDF_Retrieved": "Yes",
                        "Retrieved_From": source,
                        "Final_URL": url,
                        "PDF_SHA256": digest,
                        "PDF_Bytes": len(content),
                        "Saved_File": f"pdfs/{path.name}",
                        "Retrieval_Note": "Verified open PDF acquired",
                    })
                    successes += 1
                    bytes_saved += len(content)
                    break
                time.sleep(0.05)
        output.append(result)
    write_csv(out / f"retrieval_manifest_{agent:02d}.csv", output)
    (out / "summary.json").write_text(json.dumps({
        "agent": agent,
        "records": len(output),
        "verified_pdfs_retrieved": successes,
        "bytes_saved": bytes_saved,
        "human_verifications": 0,
    }, indent=2), encoding="utf-8")


TOPICS = {
    "coverage_uptake": ["coverage", "uptake", "fully vaccinated", "fully immunized", "fully immunised", "complete vaccination"],
    "timeliness_delay": ["timeliness", "timely", "delay", "delayed", "age appropriate", "age-appropriate", "schedule adherence"],
    "dropout_zero_dose": ["dropout", "drop out", "zero dose", "zero-dose", "unvaccinated", "incomplete vaccination", "under-vaccinated"],
    "determinants_inequality": ["determinant", "factor", "barrier", "inequality", "inequity", "socioeconomic", "maternal", "caregiver", "geographic"],
    "programme_service_delivery": ["service delivery", "outreach", "missed opportunity", "defaulter", "reminder", "intervention", "programme", "program"],
}
DESIGNS = {
    "randomized_trial": ["randomized", "randomised", "cluster trial", "controlled trial"],
    "quasi_experimental": ["quasi-experimental", "difference-in-differences", "interrupted time series", "before and after"],
    "cohort": ["cohort", "longitudinal", "follow-up", "follow up"],
    "case_control": ["case-control", "case control"],
    "cross_sectional": ["cross-sectional", "cross sectional", "survey"],
    "qualitative": ["qualitative", "focus group", "in-depth interview", "thematic analysis"],
    "systematic_review": ["systematic review", "meta-analysis", "meta analysis"],
}


def topic_agent(args: argparse.Namespace) -> None:
    root, out, agent = Path(args.input), Path(args.out), int(args.agent)
    out.mkdir(parents=True, exist_ok=True)
    rows = read_csv(find_one(root, f"topic_{agent:02d}.csv"))
    output = []
    for row in rows:
        text = (nt(row.get("Title")) + " " + nt(row.get("Abstract"))).lower()
        topics = [label for label, terms in TOPICS.items() if any(term in text for term in terms)]
        designs = [label for label, terms in DESIGNS.items() if any(term in text for term in terms)]
        output.append({
            **row,
            "Agent": agent,
            "Machine_Topic_Labels": "; ".join(topics) if topics else "unclassified",
            "Machine_Design_Hints": "; ".join(designs) if designs else "not identifiable from title/abstract",
            "Potential_Primary_Evidence": "No" if "systematic_review" in designs else "Unclear—human review required",
            "Human_Topic_Validation": "Not reviewed",
            "Human_Design_Validation": "Not reviewed",
            "Human_Reviewer": "",
            "Human_Notes": "",
        })
    write_csv(out / f"topic_{agent:02d}.csv", output)
    (out / "summary.json").write_text(json.dumps({"agent": agent, "records": len(output), "topic_counts": Counter(label for row in output for label in row["Machine_Topic_Labels"].split("; "))}, indent=2), encoding="utf-8")


def prisma_agent(args: argparse.Namespace) -> None:
    root, out, agent = Path(args.input), Path(args.out), int(args.agent)
    out.mkdir(parents=True, exist_ok=True)
    records = read_csv(find_one(root, f"records_{agent:02d}.csv"))
    search = read_csv(find_one(root, f"search_{agent:02d}.csv"))
    audits = []
    for row in records:
        stable_id = bool(norm_doi(row.get("DOI")) or nt(row.get("PMID")) or nt(row.get("URL")) or nt(row.get("Record_ID")))
        flags = []
        if not nt(row.get("Title")):
            flags.append("missing title")
        if not nt(row.get("Abstract")):
            flags.append("missing abstract")
        if not nt(row.get("Year")):
            flags.append("missing year")
        if not stable_id:
            flags.append("missing stable identifier")
        audits.append({
            **row,
            "Agent": agent,
            "Stable_Identifier_Available": "Yes" if stable_id else "No",
            "Metadata_QA_Flags": "; ".join(flags),
            "Identification_Record_Status": "Computationally catalogued",
            "Human_Screening_Status": "Not reviewed",
        })
    search_output = []
    for row in search:
        search_output.append({
            **row,
            "Agent": agent,
            "Provisional_PRISMA_Use": "Identification/search accounting only",
            "Human_Search_QA": "Not reviewed",
            "Human_QA_Notes": "",
        })
    write_csv(out / f"record_audit_{agent:02d}.csv", audits)
    write_csv(out / f"search_audit_{agent:02d}.csv", search_output)
    summary = {
        "agent": agent,
        "record_rows": len(audits),
        "search_units": len(search_output),
        "missing_abstract": sum("missing abstract" in row["Metadata_QA_Flags"] for row in audits),
        "master_set_counts": Counter(row.get("Master_Set", "") for row in audits),
        "formal_screening": 0,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


def calibration_agent(args: argparse.Namespace) -> None:
    root, out, agent = Path(args.input), Path(args.out), int(args.agent)
    out.mkdir(parents=True, exist_ok=True)
    rows = read_csv(find_one(root, f"calibration_{agent:02d}.csv"))
    blind = []
    key = []
    for index, row in enumerate(rows, 1):
        common = {
            "Calibration_Batch": f"CAL-{agent:02d}",
            "Batch_Order": index,
            "Record_ID": row.get("Record_ID", ""),
            "Title": row.get("Title", ""),
            "Abstract": row.get("Abstract", ""),
            "Year": row.get("Year", ""),
            "DOI": row.get("DOI", ""),
            "Decision": "",
            "Primary_Exclusion_Reason": "",
            "Reviewer_Notes": "",
            "Review_Date": "",
        }
        blind.append(common)
        key.append({"Calibration_Batch": common["Calibration_Batch"], "Record_ID": common["Record_ID"], "Machine_Priority_Admin_Key": row.get("Machine_Priority", "")})
    write_csv(out / f"CAL_{agent:02d}_Mizan_Blind.csv", [{**row, "Reviewer": "Md. Mizanoor Rahman", "Review_Status": "Not reviewed"} for row in blind])
    write_csv(out / f"CAL_{agent:02d}_Kapashia_Blind.csv", [{**row, "Reviewer": "Kapashia Binte Giash", "Review_Status": "Not reviewed"} for row in blind])
    write_csv(out / f"CAL_{agent:02d}_Reconciliation_Blank.csv", [{
        "Calibration_Batch": row["Calibration_Batch"],
        "Record_ID": row["Record_ID"],
        "Reviewer1_Decision": "",
        "Reviewer2_Decision": "",
        "Agreement": "",
        "Final_Decision": "",
        "Adjudicator": "",
        "Resolution_Notes": "",
    } for row in blind])
    write_csv(out / f"CAL_{agent:02d}_Admin_Key.csv", key)
    (out / "summary.json").write_text(json.dumps({"agent": agent, "records": len(rows), "reviewer1_completed": 0, "reviewer2_completed": 0}, indent=2), encoding="utf-8")


def consolidate(args: argparse.Namespace) -> None:
    root, out = Path(args.input), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    metadata = []
    for path in root.rglob("metadata_*.csv"):
        metadata.extend(read_csv(path))
    duplicates = []
    for path in root.rglob("duplicate_*.csv"):
        duplicates.extend(read_csv(path))
    retrieval = []
    for path in root.rglob("retrieval_manifest_*.csv"):
        retrieval.extend(read_csv(path))
    topics = []
    for path in root.rglob("topic_*.csv"):
        topics.extend(read_csv(path))
    record_audits = []
    for path in root.rglob("record_audit_*.csv"):
        record_audits.extend(read_csv(path))
    search_audits = []
    for path in root.rglob("search_audit_*.csv"):
        search_audits.extend(read_csv(path))

    assert len(metadata) == EXPECTED_REVIEW_POOL, len(metadata)
    assert len(duplicates) == EXPECTED_DUPLICATE_ROWS, len(duplicates)
    assert len(retrieval) == EXPECTED_RETRIEVAL_ROWS, len(retrieval)
    assert len(topics) == EXPECTED_REVIEW_POOL, len(topics)
    assert len(record_audits) == EXPECTED_COMBINED_MASTER, len(record_audits)
    assert len(search_audits) == EXPECTED_SEARCH_UNITS, len(search_audits)

    write_csv(out / "review_pool_metadata_enriched_8433.csv", metadata)
    write_csv(out / "duplicate_family_machine_qc_806.csv", duplicates)
    write_csv(out / "lawful_pdf_acquisition_manifest_1000.csv", retrieval)
    write_csv(out / "review_pool_topic_design_map_8433.csv", topics)
    write_csv(out / "prisma_record_metadata_audit_55248.csv", record_audits)
    write_csv(out / "prisma_search_unit_audit_58.csv", search_audits)

    calibration_dir = out / "reviewer_calibration_batches"
    calibration_dir.mkdir()
    calibration_files = list(root.rglob("CAL_*.csv"))
    for path in calibration_files:
        shutil.copy2(path, calibration_dir / path.name)
    mizan_rows = sum(len(read_csv(path)) for path in calibration_dir.glob("*_Mizan_Blind.csv"))
    kapashia_rows = sum(len(read_csv(path)) for path in calibration_dir.glob("*_Kapashia_Blind.csv"))
    assert mizan_rows == 1000 and kapashia_rows == 1000, (mizan_rows, kapashia_rows)

    priority_counts = Counter(row.get("Machine_Priority", "") for row in metadata)
    topic_counts = Counter(label for row in topics for label in row.get("Machine_Topic_Labels", "").split("; ") if label)
    summary = {
        "prospero": PROSPERO,
        "parallel_agents": METADATA_AGENTS + DUPLICATE_AGENTS + RETRIEVAL_AGENTS + TOPIC_AGENTS + PRISMA_AGENTS + CALIBRATION_AGENTS,
        "review_pool_metadata_rows": len(metadata),
        "abstracts_available_after_enrichment": sum(row.get("Abstract_Available_After_Enrichment") == "Yes" for row in metadata),
        "review_pool_priority_counts": dict(priority_counts),
        "duplicate_candidate_rows_qc_prepared": len(duplicates),
        "verified_open_pdfs_acquired": sum(row.get("PDF_Retrieved") == "Yes" for row in retrieval),
        "retrieval_manifest_rows": len(retrieval),
        "topic_counts": dict(topic_counts),
        "prisma_record_audit_rows": len(record_audits),
        "prisma_search_units": len(search_audits),
        "calibration_rows_per_reviewer": mizan_rows,
        "formal_human_screening_completed": 0,
        "duplicate_adjudications_completed": 0,
        "fulltext_eligibility_decisions_completed": 0,
        "generated_utc": now(),
    }
    (out / "final_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out / "README.md").write_text(
        "# SRMA post-160 parallel package\n\n"
        "This package contains machine-assisted metadata enrichment, duplicate-family evidence, lawful open-PDF acquisition manifests, topic/design mapping, PRISMA identification QA, and blank calibration forms.\n\n"
        "It does **not** claim human title/abstract screening, duplicate adjudication, full-text eligibility, extraction, or risk-of-bias completion. Retrieved PDF bytes remain in the separate per-agent GitHub artifacts; the consolidated package contains their verified manifest.\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("prepare")
    command.add_argument("--input", required=True)
    command.add_argument("--out", required=True)
    command.set_defaults(func=prepare)
    for name, function, count in [
        ("metadata-agent", metadata_agent, METADATA_AGENTS),
        ("duplicate-agent", duplicate_agent, DUPLICATE_AGENTS),
        ("retrieval-agent", retrieval_agent, RETRIEVAL_AGENTS),
        ("topic-agent", topic_agent, TOPIC_AGENTS),
        ("prisma-agent", prisma_agent, PRISMA_AGENTS),
        ("calibration-agent", calibration_agent, CALIBRATION_AGENTS),
    ]:
        command = sub.add_parser(name)
        command.add_argument("--agent", type=int, required=True, choices=range(1, count + 1))
        command.add_argument("--input", required=True)
        command.add_argument("--out", required=True)
        command.set_defaults(func=function)
    command = sub.add_parser("consolidate")
    command.add_argument("--input", required=True)
    command.add_argument("--out", required=True)
    command.set_defaults(func=consolidate)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
