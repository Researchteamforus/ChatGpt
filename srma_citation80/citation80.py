#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

import requests

UA = "SAMA-Bangladesh-SRMA/1.0 (systematic-review-support; contact via repository)"
TIMEOUT = 25
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
BANGLA_RE = re.compile(r"[\u0980-\u09FF]")

TITLE_KEYS = ("title", "record_title", "article_title", "work_title")
ABSTRACT_KEYS = ("abstract", "abstract_text", "summary")
DOI_KEYS = ("doi", "DOI")
ID_KEYS = ("record_id", "stable_record_id", "id", "openalex_id", "source_record_id")
YEAR_KEYS = ("year", "publication_year", "pub_year")
PRIORITY_KEYS = ("priority", "machine_priority", "fulltext_priority", "triage", "machine_triage")
ROUTE_KEYS = ("pdf_url", "best_pdf_url", "open_access_url", "landing_url", "url")
DECISION_KEYS = (
    "reviewer_decision", "decision", "r1_decision", "r2_decision",
    "reviewer_1_decision", "reviewer_2_decision", "include_exclude"
)

def text(v: Any) -> str:
    return "" if v is None else str(v).strip()

def first(row: dict[str, Any], keys: Iterable[str]) -> str:
    for k in keys:
        if k in row and text(row[k]):
            return text(row[k])
    lower = {str(k).lower(): k for k in row}
    for k in keys:
        kk = lower.get(k.lower())
        if kk is not None and text(row.get(kk)):
            return text(row.get(kk))
    return ""

def norm_doi(v: str) -> str:
    v = text(v).lower()
    v = v.replace("https://doi.org/", "").replace("http://doi.org/", "").replace("doi:", "").strip()
    m = DOI_RE.search(v)
    return m.group(0).rstrip(".,;)]}") if m else ""

def norm_title(v: str) -> str:
    v = unicodedata.normalize("NFKC", text(v)).lower()
    v = re.sub(r"<[^>]+>", " ", v)
    v = re.sub(r"[^\w\u0980-\u09FF]+", " ", v, flags=re.UNICODE)
    return re.sub(r"\s+", " ", v).strip()

def stable_id(row: dict[str, Any]) -> str:
    existing = first(row, ID_KEYS)
    if existing:
        return existing
    doi = norm_doi(first(row, DOI_KEYS))
    if doi:
        return "doi:" + doi
    basis = "|".join([norm_title(first(row, TITLE_KEYS)), first(row, YEAR_KEYS)])
    return "hash:" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]

def read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except (UnicodeDecodeError, csv.Error):
        return []

def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        keys: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for k in row:
                if k not in seen:
                    seen.add(k)
                    keys.append(k)
        fieldnames = keys or ["empty"]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

def iter_csvs(root: Path) -> Iterable[Path]:
    yield from root.rglob("*.csv")

def candidate_rows(root: Path) -> tuple[list[dict[str, str]], dict[str, int]]:
    chosen: dict[str, dict[str, str]] = {}
    files = 0
    rows_seen = 0
    for p in iter_csvs(root):
        rows = read_csv(p)
        if not rows:
            continue
        files += 1
        for row in rows:
            rows_seen += 1
            title = first(row, TITLE_KEYS)
            doi = norm_doi(first(row, DOI_KEYS))
            if not title and not doi:
                continue
            rid = stable_id(row)
            existing = chosen.get(rid)
            if existing is None or len(first(row, ABSTRACT_KEYS)) > len(first(existing, ABSTRACT_KEYS)):
                copy = dict(row)
                copy["_source_file"] = str(p)
                copy["_stable_id"] = rid
                copy["_doi_norm"] = doi
                copy["_title_norm"] = norm_title(title)
                chosen[rid] = copy
    return list(chosen.values()), {"csv_files": files, "rows_seen": rows_seen, "candidate_records": len(chosen)}

def score_priority(row: dict[str, str]) -> tuple[int, int, str]:
    blob = " ".join([
        first(row, PRIORITY_KEYS), first(row, TITLE_KEYS), first(row, ABSTRACT_KEYS)
    ]).lower()
    score = 0
    if any(x in blob for x in ("high", "likely include", "include", "priority 1")): score += 10
    if "bangladesh" in blob or "bangladeshi" in blob: score += 6
    if any(x in blob for x in ("immuniz", "vaccin", "zero-dose", "zero dose")): score += 5
    if first(row, DOI_KEYS): score += 2
    if first(row, ABSTRACT_KEYS): score += 1
    year = first(row, YEAR_KEYS)
    return (-score, -len(first(row, ABSTRACT_KEYS)), year)

def partition(rows: list[dict[str, str]], n: int) -> list[list[dict[str, str]]]:
    n = max(1, n)
    return [rows[i::n] for i in range(n)]

def compact(row: dict[str, str]) -> dict[str, str]:
    return {
        "record_id": stable_id(row),
        "title": first(row, TITLE_KEYS),
        "abstract": first(row, ABSTRACT_KEYS),
        "doi": norm_doi(first(row, DOI_KEYS)),
        "year": first(row, YEAR_KEYS),
        "priority_source": first(row, PRIORITY_KEYS),
        "source_file": row.get("_source_file", ""),
    }

def prepare(args: argparse.Namespace) -> None:
    src = Path(args.input)
    out = Path(args.out)
    all_rows, audit = candidate_rows(src)
    all_rows.sort(key=score_priority)

    high = all_rows[: min(1200, len(all_rows))]
    unresolved = []
    for r in high:
        route = first(r, ROUTE_KEYS)
        blob = " ".join(str(v) for v in r.values()).lower()
        if not route or "unresolved" in blob or "not retrieved" in blob:
            unresolved.append(r)
    if len(unresolved) < 520:
        unresolved = high[: min(520, len(high))]
    else:
        unresolved = unresolved[:520]

    review_pool = all_rows[: min(8433, len(all_rows))]
    known_dois = sorted({norm_doi(first(r, DOI_KEYS)) for r in all_rows if norm_doi(first(r, DOI_KEYS))})
    known_titles = sorted({norm_title(first(r, TITLE_KEYS)) for r in all_rows if norm_title(first(r, TITLE_KEYS))})

    write_json(out / "known_index.json", {
        "known_dois": known_dois,
        "known_title_hashes": [hashlib.sha256(t.encode()).hexdigest() for t in known_titles],
        "candidate_count": len(all_rows),
    })

    specs = [
        ("backward", high, 20),
        ("forward", high, 20),
        ("repository", unresolved, 15),
        ("normalize", review_pool, 10),
        ("correspondence", unresolved[:250], 5),
    ]
    for name, rows, n in specs:
        for i, shard in enumerate(partition(rows, n), 1):
            write_csv(out / f"{name}_shards" / f"{name}_{i:02d}.csv", [compact(r) for r in shard])

    screening_files: list[Path] = []
    for p in src.rglob("*.csv"):
        name = p.name.lower()
        if any(k in name for k in ("reviewer", "screening_batch", "calibration")):
            screening_files.append(p)
    for i, group in enumerate(partition([{"path": str(p)} for p in screening_files], 10), 1):
        write_csv(out / "workload_shards" / f"workload_{i:02d}.csv", group, ["path"])

    summary = {
        **audit,
        "high_priority_seeds": len(high),
        "unresolved_route_seeds": len(unresolved),
        "review_pool_records": len(review_pool),
        "known_dois": len(known_dois),
        "screening_files_found": len(screening_files),
        "agents": {"backward": 20, "forward": 20, "repository": 15, "normalize": 10, "workload": 10, "correspondence": 5},
        "governance": "Machine-assisted supplementary-search and operational preparation only; no eligibility decision.",
    }
    write_json(out / "prepare_summary.json", summary)

def get_json(url: str, params: dict[str, Any] | None = None, tries: int = 3) -> dict[str, Any]:
    for attempt in range(tries):
        try:
            r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=TIMEOUT)
            if r.status_code == 429:
                time.sleep(2 + attempt * 2)
                continue
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == tries - 1:
                return {}
            time.sleep(1 + attempt)
    return {}

def agent_input(args: argparse.Namespace, prefix: str) -> list[dict[str, str]]:
    p = Path(args.input) / f"{prefix}_{int(args.agent):02d}.csv"
    if not p.exists():
        matches = list(Path(args.input).rglob(f"{prefix}_{int(args.agent):02d}.csv"))
        if not matches:
            raise FileNotFoundError(p)
        p = matches[0]
    return read_csv(p)

def backward_agent(args: argparse.Namespace) -> None:
    rows = agent_input(args, "backward")
    out_rows: list[dict[str, Any]] = []
    for seed in rows:
        doi = norm_doi(seed.get("doi", ""))
        if not doi:
            out_rows.append({**seed, "status": "no_doi", "reference_doi": "", "reference_title": ""})
            continue
        data = get_json(f"https://api.crossref.org/works/{quote(doi, safe='')}")
        msg = data.get("message", {}) if isinstance(data, dict) else {}
        refs = msg.get("reference", []) if isinstance(msg, dict) else []
        if not refs:
            out_rows.append({**seed, "status": "no_structured_references", "reference_doi": "", "reference_title": ""})
        for ref in refs[:100]:
            out_rows.append({
                "seed_record_id": seed.get("record_id", ""),
                "seed_doi": doi,
                "seed_title": seed.get("title", ""),
                "reference_doi": norm_doi(text(ref.get("DOI"))),
                "reference_title": text(ref.get("article-title") or ref.get("journal-title")),
                "reference_year": text(ref.get("year")),
                "status": "candidate_reference",
                "human_eligibility_decision": "",
            })
    write_csv(Path(args.out) / f"backward_{int(args.agent):02d}.csv", out_rows)
    write_json(Path(args.out) / "summary.json", {"agent": int(args.agent), "seeds": len(rows), "output_rows": len(out_rows)})

def openalex_work_by_doi(doi: str) -> dict[str, Any]:
    if not doi:
        return {}
    return get_json(f"https://api.openalex.org/works/https://doi.org/{quote(doi, safe=':/')}")

def forward_agent(args: argparse.Namespace) -> None:
    rows = agent_input(args, "forward")
    out_rows: list[dict[str, Any]] = []
    for seed in rows:
        doi = norm_doi(seed.get("doi", ""))
        work = openalex_work_by_doi(doi)
        wid = text(work.get("id")) if work else ""
        if not wid:
            out_rows.append({**seed, "status": "openalex_seed_not_resolved", "citing_work_id": ""})
            continue
        short_id = wid.rsplit("/", 1)[-1]
        data = get_json("https://api.openalex.org/works", params={
            "filter": f"cites:{short_id}",
            "per-page": 50,
            "select": "id,doi,title,publication_year,primary_location",
        })
        results = data.get("results", []) if isinstance(data, dict) else []
        if not results:
            out_rows.append({**seed, "status": "no_forward_citations_found", "citing_work_id": ""})
        for w in results:
            loc = w.get("primary_location") or {}
            out_rows.append({
                "seed_record_id": seed.get("record_id", ""),
                "seed_doi": doi,
                "citing_work_id": text(w.get("id")),
                "citing_doi": norm_doi(text(w.get("doi"))),
                "citing_title": text(w.get("title")),
                "citing_year": text(w.get("publication_year")),
                "landing_page_url": text(loc.get("landing_page_url")) if isinstance(loc, dict) else "",
                "pdf_url": text(loc.get("pdf_url")) if isinstance(loc, dict) else "",
                "status": "candidate_forward_citation",
                "human_eligibility_decision": "",
            })
    write_csv(Path(args.out) / f"forward_{int(args.agent):02d}.csv", out_rows)
    write_json(Path(args.out) / "summary.json", {"agent": int(args.agent), "seeds": len(rows), "output_rows": len(out_rows)})

def repository_agent(args: argparse.Namespace) -> None:
    rows = agent_input(args, "repository")
    out_rows: list[dict[str, Any]] = []
    for seed in rows:
        doi = norm_doi(seed.get("doi", ""))
        routes: list[tuple[str, str, str]] = []
        if doi:
            routes.append(("doi_resolver", f"https://doi.org/{doi}", "resolver"))
            w = openalex_work_by_doi(doi)
            for loc in (w.get("locations") or [])[:20] if w else []:
                if not isinstance(loc, dict):
                    continue
                src = loc.get("source") or {}
                routes.append((
                    text(src.get("display_name") if isinstance(src, dict) else "OpenAlex location"),
                    text(loc.get("pdf_url") or loc.get("landing_page_url")),
                    "openalex_location",
                ))
            epmc = get_json("https://www.ebi.ac.uk/europepmc/webservices/rest/search", params={
                "query": f'DOI:"{doi}"', "format": "json", "pageSize": 5
            })
            for item in ((epmc.get("resultList") or {}).get("result") or []):
                pmcid = text(item.get("pmcid"))
                if pmcid:
                    routes.append(("Europe PMC", f"https://europepmc.org/articles/{pmcid}", "pmc"))
        seen = set()
        if not routes:
            out_rows.append({**seed, "route_status": "no_machine_route_found", "candidate_url": ""})
        for source, url, rtype in routes:
            if not url or url in seen:
                continue
            seen.add(url)
            out_rows.append({
                **seed,
                "route_source": source,
                "route_type": rtype,
                "candidate_url": url,
                "route_status": "candidate_route_requires_validation",
                "copyright_note": "Use only lawful access routes; no bypassing access controls.",
            })
    write_csv(Path(args.out) / f"repository_{int(args.agent):02d}.csv", out_rows)
    write_json(Path(args.out) / "summary.json", {"agent": int(args.agent), "seeds": len(rows), "route_rows": len(out_rows)})

def normalize_agent(args: argparse.Namespace) -> None:
    rows = agent_input(args, "normalize")
    out_rows = []
    for r in rows:
        title = text(r.get("title"))
        abstract = text(r.get("abstract"))
        blob = title + " " + abstract
        lang = "Bangla_or_mixed" if BANGLA_RE.search(blob) else "Latin_script_or_unknown"
        out_rows.append({
            **r,
            "title_nfkc": unicodedata.normalize("NFKC", title),
            "title_search_normalized": norm_title(title),
            "abstract_nfkc": unicodedata.normalize("NFKC", abstract),
            "script_flag": lang,
            "translation_required": "Yes" if BANGLA_RE.search(blob) else "No/Unknown",
            "human_verified_translation": "",
        })
    write_csv(Path(args.out) / f"normalize_{int(args.agent):02d}.csv", out_rows)
    write_json(Path(args.out) / "summary.json", {"agent": int(args.agent), "records": len(rows), "bangla_or_mixed": sum(r["translation_required"]=="Yes" for r in out_rows)})

def workload_agent(args: argparse.Namespace) -> None:
    rows = agent_input(args, "workload")
    output = []
    for item in rows:
        p = Path(item.get("path", ""))
        data = read_csv(p) if p.exists() else []
        ids = [stable_id(r) for r in data]
        decision_nonblank = 0
        found_decision_cols: set[str] = set()
        for r in data:
            lower = {k.lower(): k for k in r}
            for key in DECISION_KEYS:
                if key in lower:
                    found_decision_cols.add(lower[key])
                    if text(r.get(lower[key])):
                        decision_nonblank += 1
        output.append({
            "file": str(p),
            "rows": len(data),
            "unique_record_ids": len(set(ids)),
            "duplicate_rows": max(0, len(ids)-len(set(ids))),
            "decision_columns": "|".join(sorted(found_decision_cols)),
            "nonblank_decision_cells": decision_nonblank,
            "blank_handoff_ok": "Yes" if decision_nonblank == 0 else "No—review",
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "",
        })
    write_csv(Path(args.out) / f"workload_{int(args.agent):02d}.csv", output)
    write_json(Path(args.out) / "summary.json", {"agent": int(args.agent), "files": len(rows), "files_with_nonblank_decisions": sum(x["blank_handoff_ok"]!="Yes" for x in output)})

def correspondence_agent(args: argparse.Namespace) -> None:
    rows = agent_input(args, "correspondence")
    output = []
    for r in rows:
        doi = norm_doi(r.get("doi", ""))
        output.append({
            "record_id": r.get("record_id", ""),
            "title": r.get("title", ""),
            "doi": doi,
            "year": r.get("year", ""),
            "contact_target_type": "Corresponding author or institutional repository",
            "contact_details": "",
            "request_status": "Not contacted",
            "request_date": "",
            "response_status": "",
            "lawful_copy_received": "",
            "notes": "",
        })
    write_csv(Path(args.out) / f"correspondence_{int(args.agent):02d}.csv", output)
    write_json(Path(args.out) / "summary.json", {"agent": int(args.agent), "queue_rows": len(output), "emails_sent": 0})

def consolidate(args: argparse.Namespace) -> None:
    root = Path(args.input)
    out = Path(args.out)
    prefixes = ("backward_", "forward_", "repository_", "normalize_", "workload_", "correspondence_")
    collected: dict[str, list[dict[str, str]]] = defaultdict(list)
    for p in root.rglob("*.csv"):
        for prefix in prefixes:
            if p.name.startswith(prefix):
                collected[prefix.rstrip("_")].extend(read_csv(p))
                break

    known = {}
    for p in root.rglob("known_index.json"):
        try:
            known = json.loads(p.read_text(encoding="utf-8"))
            break
        except Exception:
            pass
    known_dois = set(known.get("known_dois", []))
    known_hashes = set(known.get("known_title_hashes", []))

    supplementary = []
    for source_name in ("backward", "forward"):
        for r in collected.get(source_name, []):
            doi = norm_doi(r.get("reference_doi") or r.get("citing_doi") or "")
            title = text(r.get("reference_title") or r.get("citing_title") or "")
            title_hash = hashlib.sha256(norm_title(title).encode()).hexdigest() if title else ""
            if not doi and not title:
                continue
            supplementary.append({
                "supplementary_source": source_name,
                "seed_record_id": r.get("seed_record_id", ""),
                "candidate_doi": doi,
                "candidate_title": title,
                "candidate_year": r.get("reference_year") or r.get("citing_year") or "",
                "already_in_known_master": "Yes" if (doi and doi in known_dois) or (title_hash and title_hash in known_hashes) else "No/Uncertain",
                "machine_relevance_status": "Not assessed",
                "human_screening_decision": "",
            })
    dedup: dict[str, dict[str, str]] = {}
    for r in supplementary:
        key = r["candidate_doi"] or norm_title(r["candidate_title"])
        if key and key not in dedup:
            dedup[key] = r

    write_csv(out / "supplementary_citation_candidates.csv", list(dedup.values()))
    for name, rows in collected.items():
        write_csv(out / f"{name}_master.csv", rows)
    summary = {
        "agents_expected": 80,
        "workstreams": {k: len(v) for k, v in collected.items()},
        "supplementary_candidates_before_dedup": len(supplementary),
        "supplementary_candidates_after_dedup": len(dedup),
        "new_or_uncertain_candidates": sum(r["already_in_known_master"] == "No/Uncertain" for r in dedup.values()),
        "human_screening_decisions_recorded": 0,
        "formal_eligibility_decisions_recorded": 0,
        "governance": "All outputs are supplementary-search candidates or operational support and require documented human review.",
    }
    write_json(out / "summary.json", summary)
    (out / "README.md").write_text(
        "# SRMA Citation, Route and Screening QA Package\n\n"
        "This package contains machine-assisted backward/forward citation candidates, lawful route maps, "
        "metadata normalization, workload QA and blank correspondence queues. It does not contain formal "
        "screening or eligibility decisions.\n", encoding="utf-8"
    )

def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd in ("prepare", "backward-agent", "forward-agent", "repository-agent", "normalize-agent",
                "workload-agent", "correspondence-agent", "consolidate"):
        p = sub.add_parser(cmd)
        p.add_argument("--input", required=True)
        p.add_argument("--out", required=True)
        if cmd not in ("prepare", "consolidate"):
            p.add_argument("--agent", required=True, type=int)
    args = ap.parse_args()
    {
        "prepare": prepare,
        "backward-agent": backward_agent,
        "forward-agent": forward_agent,
        "repository-agent": repository_agent,
        "normalize-agent": normalize_agent,
        "workload-agent": workload_agent,
        "correspondence-agent": correspondence_agent,
        "consolidate": consolidate,
    }[args.cmd](args)

if __name__ == "__main__":
    main()
