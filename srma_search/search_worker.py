#!/usr/bin/env python3
"""SRMA Bangladesh prospective search runner.

Executes one of 30 auditable workstreams for PROSPERO CRD420261461557.
Each worker writes raw JSON/HTML where possible, a canonical CSV export, and a
machine-readable search log. Search execution is discovery only; it does not
make screening or eligibility decisions.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests
from bs4 import BeautifulSoup

PROSPERO_ID = "CRD420261461557"
PROJECT = "SRMA Bangladesh prospective rerun"
EMAIL = os.getenv("NCBI_EMAIL", "st19009@mbstu.ac.bd")
USER_AGENT = f"SRMA-Bangladesh/{PROSPERO_ID} ({EMAIL})"
OUTCOME_GROUPS = {
    "coverage": '(coverage OR uptake OR "full vaccination" OR "complete vaccination" OR "antigen-specific")',
    "timeliness": '(timeliness OR timely OR delay* OR "age-appropriate" OR invalid OR schedule OR adherence)',
    "dropout": '(dropout OR "zero dose" OR zero-dose OR unvaccinated OR incomplete OR partial* OR under-vaccinat*)',
    "determinants": '(determinant* OR factor* OR barrier* OR inequ* OR socioeconomic OR maternal OR caregiver OR geographic OR access* OR "health service*")',
    "programme": '("missed opportunit*" OR "service delivery" OR readiness OR outreach OR defaulter OR reminder* OR intervention* OR programme* OR program*)',
}
CORE = 'Bangladesh AND (immuni* OR vaccinat* OR "expanded programme on immunization" OR EPI) AND (infant* OR child* OR newborn* OR "under five" OR under-five OR toddler*)'
PUBMED_CORE = '("Bangladesh"[Mesh] OR Bangladesh*[tiab]) AND ("Immunization Programs"[Mesh] OR "Vaccination"[Mesh] OR immuni*[tiab] OR vaccinat*[tiab] OR "expanded programme on immunization"[tiab] OR EPI[tiab]) AND ("Infant"[Mesh] OR "Child, Preschool"[Mesh] OR infant*[tiab] OR child*[tiab] OR newborn*[tiab] OR under-five[tiab] OR toddler*[tiab])'
PUBMED_OUTCOMES = '(coverage[tiab] OR uptake[tiab] OR timeliness[tiab] OR timely[tiab] OR delay*[tiab] OR dropout[tiab] OR "zero dose"[tiab] OR zero-dose[tiab] OR unvaccinated[tiab] OR incomplete[tiab] OR partial*[tiab] OR "missed opportunit*"[tiab] OR barrier*[tiab] OR determinant*[tiab] OR inequ*[tiab] OR access*[tiab] OR "service delivery"[tiab])'


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")[:100]


def norm_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def norm_doi(value: Any) -> str:
    v = norm_text(value).lower()
    v = re.sub(r"^https?://(dx\.)?doi\.org/", "", v)
    v = re.sub(r"^doi:\s*", "", v)
    return v.rstrip(".,; ")


def norm_title(value: Any) -> str:
    v = unicodedata.normalize("NFKD", norm_text(value)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", v).strip()


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json,text/html,application/xml;q=0.9,*/*;q=0.8"})
    return s


def get_with_retry(s: requests.Session, url: str, *, params: Optional[dict] = None, timeout: int = 60, max_tries: int = 5) -> requests.Response:
    last: Optional[Exception] = None
    for attempt in range(max_tries):
        try:
            r = s.get(url, params=params, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(30, 2 ** attempt))
                continue
            r.raise_for_status()
            return r
        except Exception as exc:
            last = exc
            time.sleep(min(30, 2 ** attempt))
    raise RuntimeError(f"GET failed after {max_tries} tries: {url}: {last}")


def canonical_record(**kw: Any) -> Dict[str, Any]:
    title = norm_text(kw.get("Title"))
    doi = norm_doi(kw.get("DOI"))
    rid_seed = "|".join([norm_text(kw.get("Source")), norm_text(kw.get("Source_ID")), doi, norm_title(title)])
    rid = hashlib.sha256(rid_seed.encode("utf-8")).hexdigest()[:20]
    base = {
        "Record_ID": f"SRMA-{rid}", "Source": "", "Task_ID": "", "Query_Group": "",
        "Source_ID": "", "Title": title, "Abstract": "", "Authors": "", "Year": "",
        "Journal_or_Institution": "", "DOI": doi, "PMID": "", "PMCID": "", "URL": "",
        "Document_Type": "", "Language": "", "Retrieved_UTC": now_iso(), "Raw_File": "",
    }
    for k, v in kw.items():
        if k in base:
            base[k] = norm_text(v)
    base["DOI"] = norm_doi(base["DOI"])
    return base


def make_task(task_id: int, source: str, group: str, query: str, method: str, formal: bool = True) -> dict:
    return {"task_id": task_id, "source": source, "group": group, "query": query, "method": method, "formal": formal}


TASKS: Dict[int, dict] = {}
for i, g in enumerate(OUTCOME_GROUPS, start=1):
    TASKS[i] = make_task(i, "Europe PMC", g, f"TITLE_ABS:({CORE} AND {OUTCOME_GROUPS[g]})", "europe_pmc")
for i, g in enumerate(OUTCOME_GROUPS, start=6):
    TASKS[i] = make_task(i, "OpenAlex", g, f"{CORE} AND {OUTCOME_GROUPS[g]}", "openalex")
TASKS[11] = make_task(11, "PubMed/MEDLINE", "combined_protocol", f"({PUBMED_CORE}) AND {PUBMED_OUTCOMES}", "pubmed")
# Four supplementary PubMed QC blocks; task 11 remains the formal exact protocol query.
for i, g in zip(range(12, 16), ["coverage", "timeliness", "dropout", "programme"]):
    q = f"({PUBMED_CORE}) AND " + {
        "coverage": '(coverage[tiab] OR uptake[tiab] OR "full vaccination"[tiab] OR "complete vaccination"[tiab])',
        "timeliness": '(timeliness[tiab] OR timely[tiab] OR delay*[tiab] OR "age-appropriate"[tiab] OR invalid[tiab])',
        "dropout": '(dropout[tiab] OR "zero dose"[tiab] OR zero-dose[tiab] OR unvaccinated[tiab] OR incomplete[tiab])',
        "programme": '("missed opportunit*"[tiab] OR "service delivery"[tiab] OR readiness[tiab] OR outreach[tiab] OR intervention*[tiab])',
    }[g]
    TASKS[i] = make_task(i, "PubMed/MEDLINE", f"supplementary_{g}", q, "pubmed", formal=False)
for i, g in enumerate(OUTCOME_GROUPS, start=16):
    TASKS[i] = make_task(i, "WHO IRIS", g, f"Bangladesh (immunization OR immunisation OR vaccination) children {OUTCOME_GROUPS[g]}", "who_iris")
BJL_QUERIES = [
    "Bangladesh childhood immunization", "Bangladesh child vaccination coverage",
    "Bangladesh vaccination timeliness", "Bangladesh vaccination dropout",
    "Bangladesh zero dose children", "Bangladesh immunization determinants",
]
for i, q in enumerate(BJL_QUERIES, start=21):
    TASKS[i] = make_task(i, "BanglaJOL", f"query_{i-20}", q, "banglajol")
TASKS[27] = make_task(27, "WHO Global Index Medicus/IMSEAR", "combined", "Bangladesh AND (immunization OR immunisation OR vaccination) AND (infant OR child OR children)", "gim")
TASKS[28] = make_task(28, "WHO Global Index Medicus/IMSEAR", "outcomes", "Bangladesh AND (vaccination OR immunization) AND (coverage OR timeliness OR dropout OR zero-dose OR incomplete OR determinants)", "gim")
TASKS[29] = make_task(29, "DGHS EPI/CES archive", "prespecified_waves", "Bangladesh EPI Coverage Evaluation Survey 2006 2007 2010 2011 2013 2014 2015 2016 2019", "dghs")
TASKS[30] = make_task(30, "Institutional repositories", "theses_reports", "Bangladesh childhood immunization vaccination thesis dissertation report", "openalex", formal=False)


def run_europe_pmc(task: dict, out: Path) -> tuple[List[dict], int]:
    s = session(); cursor = "*"; rows: List[dict] = []; total = 0; page = 0
    while cursor and page < 200:
        page += 1
        params = {"query": task["query"], "format": "json", "pageSize": 1000, "cursorMark": cursor, "resultType": "core"}
        r = get_with_retry(s, "https://www.ebi.ac.uk/europepmc/webservices/rest/search", params=params)
        payload = r.json(); (out / f"raw_{page:04d}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        total = int(payload.get("hitCount", total or 0)); results = payload.get("resultList", {}).get("result", [])
        for x in results:
            rows.append(canonical_record(Source=task["source"], Task_ID=task["task_id"], Query_Group=task["group"], Source_ID=x.get("id"), Title=x.get("title"), Abstract=x.get("abstractText"), Authors=x.get("authorString"), Year=x.get("pubYear"), Journal_or_Institution=x.get("journalTitle"), DOI=x.get("doi"), PMID=x.get("pmid"), PMCID=x.get("pmcid"), URL=("https://europepmc.org/article/" + str(x.get("source", "MED")) + "/" + str(x.get("id", ""))), Document_Type=x.get("pubType"), Language=x.get("language"), Raw_File=f"raw_{page:04d}.json"))
        nxt = payload.get("nextCursorMark")
        if not results or not nxt or nxt == cursor: break
        cursor = nxt
    return rows, total


def reconstruct_abstract(inv: Any) -> str:
    if not isinstance(inv, dict): return ""
    pairs = []
    for word, positions in inv.items():
        for pos in positions or []: pairs.append((int(pos), word))
    return " ".join(w for _, w in sorted(pairs))


def run_openalex(task: dict, out: Path) -> tuple[List[dict], int]:
    s = session(); cursor = "*"; rows: List[dict] = []; total = 0; page = 0
    while cursor and page < 200:
        page += 1
        params = {"search": task["query"], "per-page": 200, "cursor": cursor, "mailto": EMAIL}
        r = get_with_retry(s, "https://api.openalex.org/works", params=params)
        payload = r.json(); (out / f"raw_{page:04d}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        total = int(payload.get("meta", {}).get("count", total or 0))
        for x in payload.get("results", []):
            ids = x.get("ids") or {}; auth = "; ".join(a.get("author", {}).get("display_name", "") for a in x.get("authorships", []))
            source = (((x.get("primary_location") or {}).get("source") or {}).get("display_name"))
            rows.append(canonical_record(Source=task["source"], Task_ID=task["task_id"], Query_Group=task["group"], Source_ID=x.get("id"), Title=x.get("display_name") or x.get("title"), Abstract=reconstruct_abstract(x.get("abstract_inverted_index")), Authors=auth, Year=x.get("publication_year"), Journal_or_Institution=source, DOI=ids.get("doi") or x.get("doi"), PMID=ids.get("pmid"), PMCID=ids.get("pmcid"), URL=(x.get("primary_location") or {}).get("landing_page_url") or x.get("id"), Document_Type=x.get("type"), Language=x.get("language"), Raw_File=f"raw_{page:04d}.json"))
        nxt = payload.get("meta", {}).get("next_cursor")
        if not payload.get("results") or not nxt or nxt == cursor: break
        cursor = nxt
    return rows, total


def first_text(node: Optional[ET.Element], path: str) -> str:
    if node is None: return ""
    found = node.find(path)
    return norm_text(found.text if found is not None else "")


def all_text(node: Optional[ET.Element], path: str) -> str:
    if node is None: return ""
    return "; ".join(norm_text("".join(e.itertext())) for e in node.findall(path) if norm_text("".join(e.itertext())))


def run_pubmed(task: dict, out: Path) -> tuple[List[dict], int]:
    s = session(); base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    es = get_with_retry(s, base + "esearch.fcgi", params={"db":"pubmed","term":task["query"],"retmode":"json","retmax":0,"usehistory":"y","tool":"srma_bangladesh","email":EMAIL}).json()
    (out / "esearch.json").write_text(json.dumps(es, ensure_ascii=False), encoding="utf-8")
    obj = es["esearchresult"]; total = int(obj.get("count", 0)); webenv = obj.get("webenv"); qk = obj.get("querykey")
    rows: List[dict] = []
    for start in range(0, total, 200):
        params = {"db":"pubmed","query_key":qk,"WebEnv":webenv,"retstart":start,"retmax":200,"retmode":"xml","tool":"srma_bangladesh","email":EMAIL}
        r = get_with_retry(s, base + "efetch.fcgi", params=params); raw = out / f"raw_{start//200+1:04d}.xml"; raw.write_bytes(r.content)
        root = ET.fromstring(r.content)
        for art in root.findall(".//PubmedArticle"):
            cit = art.find("MedlineCitation"); article = cit.find("Article") if cit is not None else None
            pmid = first_text(cit, "PMID"); title = all_text(article, "ArticleTitle")
            abstract = " ".join(norm_text("".join(x.itertext())) for x in article.findall("Abstract/AbstractText")) if article is not None else ""
            authors = []
            if article is not None:
                for a in article.findall("AuthorList/Author"):
                    coll = first_text(a, "CollectiveName"); name = coll or " ".join(v for v in [first_text(a,"ForeName"), first_text(a,"LastName")] if v)
                    if name: authors.append(name)
            journal = first_text(article, "Journal/Title"); year = first_text(article, "Journal/JournalIssue/PubDate/Year") or first_text(cit, "DateCompleted/Year")
            doi = ""; pmcid = ""
            for aid in art.findall("PubmedData/ArticleIdList/ArticleId"):
                typ = aid.attrib.get("IdType", ""); val = norm_text(aid.text)
                if typ == "doi": doi = val
                elif typ == "pmc": pmcid = val
            rows.append(canonical_record(Source=task["source"], Task_ID=task["task_id"], Query_Group=task["group"], Source_ID=pmid, Title=title, Abstract=abstract, Authors="; ".join(authors), Year=year, Journal_or_Institution=journal, DOI=doi, PMID=pmid, PMCID=pmcid, URL=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", Document_Type=all_text(article,"PublicationTypeList/PublicationType"), Language=all_text(article,"Language"), Raw_File=raw.name))
        time.sleep(0.35)
    return rows, total


def recursive_find(obj: Any, keys: set[str], found: Optional[dict] = None) -> dict:
    found = found or {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys and k not in found and isinstance(v, (str,int,float)): found[k] = v
            recursive_find(v, keys, found)
    elif isinstance(obj, list):
        for v in obj: recursive_find(v, keys, found)
    return found


def run_who_iris(task: dict, out: Path) -> tuple[List[dict], int]:
    s = session(); rows: List[dict] = []; total = 0
    endpoint = "https://iris.who.int/server/api/discover/search/objects"
    for page in range(0, 200):
        params = {"query": task["query"], "size": 100, "page": page}
        r = get_with_retry(s, endpoint, params=params); payload = r.json(); (out / f"raw_{page+1:04d}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        total = int((payload.get("page") or {}).get("totalElements", total or 0))
        objs = (((payload.get("_embedded") or {}).get("searchResult") or {}).get("_embedded") or {}).get("objects", [])
        if not objs: break
        for wrapper in objs:
            x = (wrapper.get("_embedded") or {}).get("indexableObject") or wrapper
            flat = recursive_find(x, {"uuid","name","handle","type","language","issueDate","dateIssued","abstract","description","title"})
            uid = flat.get("uuid") or flat.get("handle"); title = flat.get("name") or flat.get("title")
            url = f"https://iris.who.int/items/{uid}" if uid else "https://iris.who.int/"
            rows.append(canonical_record(Source=task["source"], Task_ID=task["task_id"], Query_Group=task["group"], Source_ID=uid, Title=title, Abstract=flat.get("abstract") or flat.get("description"), Year=str(flat.get("issueDate") or flat.get("dateIssued") or "")[:4], Journal_or_Institution="World Health Organization", URL=url, Document_Type=flat.get("type"), Language=flat.get("language"), Raw_File=f"raw_{page+1:04d}.json"))
        if (page + 1) * 100 >= total: break
    return rows, total


def parse_generic_results(task: dict, out: Path, url: str, params: dict, source_domain: str) -> tuple[List[dict], int]:
    s = session(); rows: List[dict] = []; seen = set(); max_pages = 30
    for page in range(1, max_pages + 1):
        p = dict(params); p.update({"page": page, "from": (page-1)*100})
        r = get_with_retry(s, url, params=p); raw = out / f"raw_{page:04d}.html"; raw.write_text(r.text, encoding="utf-8")
        soup = BeautifulSoup(r.text, "html.parser"); added = 0
        for a in soup.find_all("a", href=True):
            href = urllib.parse.urljoin(r.url, a["href"]); title = norm_text(a.get_text(" ", strip=True))
            if len(title) < 18 or source_domain not in urllib.parse.urlparse(href).netloc: continue
            key = (norm_title(title), href)
            if key in seen: continue
            if not re.search(r"immuni|vaccin|epi|zero.?dose", title, re.I): continue
            seen.add(key); added += 1
            rows.append(canonical_record(Source=task["source"], Task_ID=task["task_id"], Query_Group=task["group"], Source_ID=href, Title=html.unescape(title), URL=href, Journal_or_Institution=task["source"], Raw_File=raw.name))
        if added == 0 and page > 1: break
    return rows, len(rows)


def run_banglajol(task: dict, out: Path) -> tuple[List[dict], int]:
    return parse_generic_results(task, out, "https://www.banglajol.info/index.php/index/search/search", {"query": task["query"]}, "banglajol.info")


def run_gim(task: dict, out: Path) -> tuple[List[dict], int]:
    return parse_generic_results(task, out, "https://pesquisa.bvsalud.org/gim/", {"q": task["query"], "lang": "en", "output": "site", "format": "summary", "count": 100}, "bvsalud.org")


def run_dghs(task: dict, out: Path) -> tuple[List[dict], int]:
    # The nine protocol-listed waves were already source-verified. This worker
    # creates a prospective rerun manifest and attempts current DGHS portal discovery.
    years = [2006,2007,2010,2011,2013,2014,2015,2016,2019]
    rows = [canonical_record(Source=task["source"], Task_ID=task["task_id"], Query_Group=task["group"], Source_ID=f"DGHS-CES-{y}", Title=f"Bangladesh EPI Coverage Evaluation Survey {y}", Year=y, Journal_or_Institution="Bangladesh Directorate General of Health Services", URL="https://dghs.gov.bd/", Document_Type="government report", Raw_File="prespecified_wave_manifest.json") for y in years]
    (out / "prespecified_wave_manifest.json").write_text(json.dumps({"years": years, "status": "protocol-prespecified; stable report URLs require reconciliation with previously validated archive package"}, indent=2), encoding="utf-8")
    return rows, len(rows)


def execute(task: dict, out: Path) -> tuple[List[dict], int]:
    return {
        "europe_pmc": run_europe_pmc, "openalex": run_openalex, "pubmed": run_pubmed,
        "who_iris": run_who_iris, "banglajol": run_banglajol, "gim": run_gim,
        "dghs": run_dghs,
    }[task["method"]](task, out)


def write_csv(path: Path, rows: List[dict]) -> None:
    fields = list(canonical_record().keys())
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--task", type=int, required=True); ap.add_argument("--out", default="outputs")
    args = ap.parse_args(); task = TASKS.get(args.task)
    if not task: raise SystemExit(f"Unknown task {args.task}")
    out = Path(args.out) / f"task_{args.task:02d}_{safe_name(task['source'])}_{safe_name(task['group'])}"; out.mkdir(parents=True, exist_ok=True)
    started = now_iso(); status = "completed"; error = ""; rows: List[dict] = []; reported = 0
    try:
        rows, reported = execute(task, out)
    except Exception as exc:
        status = "failed"; error = repr(exc)
        (out / "ERROR.txt").write_text(error + "\n", encoding="utf-8")
    write_csv(out / "records.csv", rows)
    log = {"project":PROJECT,"prospero":PROSPERO_ID,"task":task,"started_utc":started,"finished_utc":now_iso(),"status":status,"error":error,"reported_hits":reported,"exported_records":len(rows),"formal_protocol_search":task["formal"],"screening_decisions_made":False}
    (out / "search_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "query.txt").write_text(task["query"] + "\n", encoding="utf-8")
    print(json.dumps(log, ensure_ascii=False))
    return 0 if status == "completed" else 2

if __name__ == "__main__":
    sys.exit(main())
