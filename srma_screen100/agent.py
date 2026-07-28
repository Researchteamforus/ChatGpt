#!/usr/bin/env python3
"""100-lane metadata enrichment and protocol-grounded provisional triage.

This is computational prioritisation only. It does not constitute independent
human screening or a final eligibility decision.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import re
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import quote

import requests

from srma_screen30.screen_worker import classify, norm

N_AGENTS = 100
PROSPERO = "CRD420261461557"
USER_AGENT = "SRMA-Bangladesh-Metadata-Enrichment/1.0 (mailto:st19009@mbstu.ac.bd)"

FIELDS = [
    "Agent_ID", "Agent_Record_Order", "Master_Record_ID", "Source", "Sources",
    "Task_ID", "Task_IDs", "Query_Group", "Query_Groups", "Source_ID",
    "Original_Title", "Enriched_Title", "Original_Abstract", "Enriched_Abstract",
    "Abstract_Available_Before", "Abstract_Available_After", "Authors", "Year",
    "Journal_or_Institution", "DOI", "PMID", "PMCID", "URL", "Document_Type",
    "Language", "Enrichment_Attempted", "Enrichment_Status", "Enrichment_Source",
    "Metadata_Changed", "Identity_Key", "Machine_Triage", "Machine_Primary_Reason",
    "Machine_Confidence", "Machine_Evidence_Snippet", "Needs_Full_Text",
    "Human_R1_Decision", "Human_R1_Approval", "Human_R1_Primary_Reason",
    "Human_R1_Notes", "Governance_Note"
]

TAG_RE = re.compile(r"<[^>]+>")
DOI_RE = re.compile(r"(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.I)
NONALNUM = re.compile(r"[^a-z0-9]+")


def clean_markup(value: Any) -> str:
    text = html.unescape(norm(value))
    text = TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_doi(value: Any) -> str:
    doi = DOI_RE.sub("", norm(value)).strip().lower()
    return doi.rstrip(".,;:) ]}")


def title_key(value: Any) -> str:
    return NONALNUM.sub("", norm(value).lower())


def identity_key(row: Dict[str, str]) -> str:
    doi = normalize_doi(row.get("DOI"))
    if doi:
        return f"doi:{doi}"
    pmid = norm(row.get("PMID"))
    if pmid:
        return f"pmid:{pmid}"
    pmcid = norm(row.get("PMCID"))
    if pmcid:
        return f"pmcid:{pmcid.lower()}"
    return f"title:{title_key(row.get('Enriched_Title') or row.get('Original_Title'))}"


def first(values: Any) -> str:
    if isinstance(values, list) and values:
        return norm(values[0])
    return norm(values)


def crossref_year(item: Dict[str, Any]) -> str:
    for key in ("published-print", "published-online", "issued", "created"):
        parts = ((item.get(key) or {}).get("date-parts") or [])
        if parts and parts[0]:
            return norm(parts[0][0])
    return ""


def crossref_authors(item: Dict[str, Any]) -> str:
    out = []
    for person in item.get("author") or []:
        name = " ".join(x for x in [norm(person.get("given")), norm(person.get("family"))] if x)
        if name:
            out.append(name)
    return "; ".join(out)


def request_json(session: requests.Session, url: str, *, params: Optional[dict] = None, timeout: int = 20) -> Optional[dict]:
    try:
        response = session.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except Exception:
        return None


def europe_pmc_lookup(session: requests.Session, row: Dict[str, str]) -> Optional[Tuple[str, Dict[str, str]]]:
    pmid, pmcid, doi = norm(row.get("PMID")), norm(row.get("PMCID")), normalize_doi(row.get("DOI"))
    queries = []
    if pmid:
        queries.append(f"EXT_ID:{pmid}")
    if pmcid:
        queries.append(f"PMCID:{pmcid}")
    if doi:
        queries.append(f'DOI:"{doi}"')
    for query in queries:
        data = request_json(
            session,
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={"query": query, "format": "json", "pageSize": 1, "resultType": "core"},
        )
        results = (((data or {}).get("resultList") or {}).get("result") or [])
        if not results:
            continue
        item = results[0]
        full_text_urls = ((item.get("fullTextUrlList") or {}).get("fullTextUrl") or [])
        return "Europe PMC", {
            "Title": clean_markup(item.get("title")),
            "Abstract": clean_markup(item.get("abstractText")),
            "Authors": norm(item.get("authorString")),
            "Year": norm(item.get("pubYear")),
            "Journal_or_Institution": norm(item.get("journalTitle")),
            "DOI": normalize_doi(item.get("doi")),
            "PMID": norm(item.get("pmid")),
            "PMCID": norm(item.get("pmcid")),
            "URL": norm((full_text_urls[0] or {}).get("url")) if full_text_urls else "",
        }
    return None


def crossref_lookup(session: requests.Session, row: Dict[str, str]) -> Optional[Tuple[str, Dict[str, str]]]:
    doi = normalize_doi(row.get("DOI"))
    title = norm(row.get("Title"))
    item: Optional[Dict[str, Any]] = None
    if doi:
        data = request_json(session, f"https://api.crossref.org/works/{quote(doi, safe='')}")
        item = ((data or {}).get("message") or None)
    elif title:
        data = request_json(
            session,
            "https://api.crossref.org/works",
            params={"query.title": title, "rows": 1, "mailto": "st19009@mbstu.ac.bd"},
        )
        items = (((data or {}).get("message") or {}).get("items") or [])
        if items:
            candidate = items[0]
            candidate_title = first(candidate.get("title"))
            similarity = SequenceMatcher(None, title_key(title), title_key(candidate_title)).ratio()
            if similarity >= 0.92:
                item = candidate
    if not item:
        return None
    return "Crossref", {
        "Title": clean_markup(first(item.get("title"))),
        "Abstract": clean_markup(item.get("abstract")),
        "Authors": crossref_authors(item),
        "Year": crossref_year(item),
        "Journal_or_Institution": clean_markup(first(item.get("container-title"))),
        "DOI": normalize_doi(item.get("DOI")),
        "PMID": "",
        "PMCID": "",
        "URL": norm(item.get("URL")),
    }


def merge_metadata(base: Dict[str, str], candidate: Dict[str, str]) -> Tuple[Dict[str, str], bool]:
    merged = dict(base)
    changed = False
    for key in ("Title", "Abstract", "Authors", "Year", "Journal_or_Institution", "DOI", "PMID", "PMCID", "URL"):
        current = norm(merged.get(key))
        incoming = norm(candidate.get(key))
        if not current and incoming:
            merged[key] = incoming
            changed = True
        elif key == "Abstract" and len(incoming) > len(current) + 80:
            merged[key] = incoming
            changed = True
    return merged, changed


def enrich(session: requests.Session, row: Dict[str, str]) -> Tuple[Dict[str, str], str, str, bool]:
    needs = len(norm(row.get("Abstract"))) < 80 or not normalize_doi(row.get("DOI")) or not norm(row.get("PMID"))
    if not needs:
        return dict(row), "not_needed", "", False
    current = dict(row)
    sources = []
    changed_any = False
    for resolver in (europe_pmc_lookup, crossref_lookup):
        result = resolver(session, current)
        if result:
            source, candidate = result
            current, changed = merge_metadata(current, candidate)
            if changed:
                sources.append(source)
                changed_any = True
        time.sleep(0.10)
    if changed_any:
        return current, "enriched", "; ".join(sources), True
    return current, "attempted_no_change", "", False


def read_records(path: Path) -> list[Dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", type=int, required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if not 1 <= args.agent <= N_AGENTS:
        raise SystemExit("agent must be 1..100")

    records = read_records(Path(args.input))
    records.sort(key=lambda r: (norm(r.get("Record_ID")), norm(r.get("Title"))))
    batch = records[args.agent - 1 :: N_AGENTS]
    out_dir = Path(args.out) / f"agent_{args.agent:03d}"
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    output = []
    counts = {"Likely include": 0, "Likely exclude": 0, "Unclear": 0}
    enriched_count = 0
    before_abstracts = 0
    after_abstracts = 0

    for order, row in enumerate(batch, start=1):
        original = dict(row)
        before_abs = len(norm(original.get("Abstract"))) >= 40
        before_abstracts += int(before_abs)
        enriched, status, source, changed = enrich(session, original)
        enriched_count += int(changed)
        after_abs = len(norm(enriched.get("Abstract"))) >= 40
        after_abstracts += int(after_abs)
        triage, reason, confidence, snippet, needs_ft = classify(
            norm(enriched.get("Title")), norm(enriched.get("Abstract")), norm(enriched.get("Document_Type"))
        )
        counts[triage] += 1
        combined = {
            "Agent_ID": f"SCREEN100-{args.agent:03d}",
            "Agent_Record_Order": str(order),
            "Master_Record_ID": norm(original.get("Record_ID")),
            "Source": norm(original.get("Source")),
            "Sources": norm(original.get("Sources")),
            "Task_ID": norm(original.get("Task_ID")),
            "Task_IDs": norm(original.get("Task_IDs")),
            "Query_Group": norm(original.get("Query_Group")),
            "Query_Groups": norm(original.get("Query_Groups")),
            "Source_ID": norm(original.get("Source_ID")),
            "Original_Title": norm(original.get("Title")),
            "Enriched_Title": norm(enriched.get("Title")),
            "Original_Abstract": norm(original.get("Abstract")),
            "Enriched_Abstract": norm(enriched.get("Abstract")),
            "Abstract_Available_Before": "Yes" if before_abs else "No",
            "Abstract_Available_After": "Yes" if after_abs else "No",
            "Authors": norm(enriched.get("Authors")),
            "Year": norm(enriched.get("Year")),
            "Journal_or_Institution": norm(enriched.get("Journal_or_Institution")),
            "DOI": normalize_doi(enriched.get("DOI")),
            "PMID": norm(enriched.get("PMID")),
            "PMCID": norm(enriched.get("PMCID")),
            "URL": norm(enriched.get("URL")),
            "Document_Type": norm(enriched.get("Document_Type")),
            "Language": norm(enriched.get("Language")),
            "Enrichment_Attempted": "No" if status == "not_needed" else "Yes",
            "Enrichment_Status": status,
            "Enrichment_Source": source,
            "Metadata_Changed": "Yes" if changed else "No",
            "Machine_Triage": triage,
            "Machine_Primary_Reason": reason,
            "Machine_Confidence": str(confidence),
            "Machine_Evidence_Snippet": snippet,
            "Needs_Full_Text": needs_ft,
            "Human_R1_Decision": "",
            "Human_R1_Approval": "Not reviewed",
            "Human_R1_Primary_Reason": "",
            "Human_R1_Notes": "",
            "Governance_Note": "Machine-assisted provisional triage only; not independent human screening or final eligibility.",
        }
        combined["Identity_Key"] = identity_key(combined)
        output.append(combined)

    csv_path = out_dir / f"screening_enrichment_agent_{args.agent:03d}.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(output)

    summary = {
        "prospero": PROSPERO,
        "agent": args.agent,
        "records_assigned": len(batch),
        "abstracts_before": before_abstracts,
        "abstracts_after": after_abstracts,
        "records_enriched": enriched_count,
        "triage_counts": counts,
        "governance": "Computational enrichment and provisional triage only; formal human screening remains required.",
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
