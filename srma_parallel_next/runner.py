#!/usr/bin/env python3
"""Parallel SRMA next-stage audit preparation.

This program performs computational audit and queue preparation only. It does not
constitute human title/abstract screening, full-text eligibility assessment,
duplicate adjudication, data extraction, or risk-of-bias assessment.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

PROSPERO = "CRD420261461557"
DUP_AGENTS = 15
PDF_AGENTS = 20
UNRESOLVED_AGENTS = 20
SCREEN_BATCH_AGENTS = 10


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str] | None = None) -> None:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if fields:
            w.writeheader()
            for row in rows:
                w.writerow({k: row.get(k, "") for k in fields})


def find_one(root: Path, name: str) -> Path:
    hits = list(root.rglob(name))
    if not hits:
        raise FileNotFoundError(f"{name} not found under {root}")
    return max(hits, key=lambda p: p.stat().st_size)


def norm_text(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def norm_doi(v: Any) -> str:
    x = norm_text(v).lower()
    x = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", x)
    x = re.sub(r"^doi:\s*", "", x)
    return x.rstrip(".,; )]")


def norm_title(v: Any) -> str:
    x = unicodedata.normalize("NFKD", norm_text(v)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", x).strip()


def split_round_robin(rows: list[dict[str, str]], n: int) -> list[list[dict[str, str]]]:
    out = [[] for _ in range(n)]
    for i, row in enumerate(rows):
        out[i % n].append(row)
    return out


def completeness(row: dict[str, str]) -> int:
    fields = ["Enriched_Title", "Enriched_Abstract", "Authors", "Year", "Journal_or_Institution", "DOI", "PMID", "PMCID", "URL"]
    return sum(bool(norm_text(row.get(k))) for k in fields) + min(5, len(norm_text(row.get("Enriched_Abstract"))) // 500)


def prepare(args: argparse.Namespace) -> None:
    screen_root, pdf_root, out = Path(args.screen_dir), Path(args.pdf_dir), Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    small = out / "small_inputs"
    small.mkdir()

    screen_master_path = find_one(screen_root, "screening_enrichment_master_1129.csv")
    queue_path = find_one(screen_root, "priority_human_screening_queue.csv")
    dup_path = find_one(screen_root, "exact_duplicate_candidates.csv")
    screen_summary = find_one(screen_root, "summary.json")
    pdf_index_path = find_one(pdf_root, "retrieved_pdf_index.csv")
    retrieval_path = find_one(pdf_root, "retrieval_results_1000_lanes.csv")
    unresolved_path = find_one(pdf_root, "unresolved_records.csv")
    pdf_summary = find_one(pdf_root, "consolidation_summary.json")

    for p in [screen_master_path, queue_path, dup_path, screen_summary, pdf_index_path, retrieval_path, unresolved_path, pdf_summary]:
        shutil.copy2(p, small / p.name)

    master = read_csv(screen_master_path)
    queue = read_csv(queue_path)
    dups = read_csv(dup_path)
    pdf_index = read_csv(pdf_index_path)
    retrieval = read_csv(retrieval_path)
    unresolved = read_csv(unresolved_path)
    assert len(master) == 1129, len(master)
    assert len(pdf_index) == 422, len(pdf_index)
    assert len(unresolved) == 935, len(unresolved)
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in dups:
        groups[row["Identity_Key"]].append(row)
    assert len(groups) == 15, len(groups)

    group_map = []
    for i, key in enumerate(sorted(groups), 1):
        group_map.append({"Agent": i, "Identity_Key": key, "Group_Size": len(groups[key])})
    write_csv(small / "duplicate_group_map.csv", group_map)

    unresolved_shards = split_round_robin(unresolved, UNRESOLVED_AGENTS)
    for i, rows in enumerate(unresolved_shards, 1):
        write_csv(small / "unresolved_shards" / f"unresolved_{i:02d}.csv", rows, list(unresolved[0]))

    triage_rank = {"Likely include": 0, "Unclear": 1, "Likely exclude": 2}
    queue.sort(key=lambda r: (triage_rank.get(r.get("Machine_Triage", ""), 9), -int(r.get("Machine_Confidence") or 0), r.get("Master_Record_ID", "")))
    chunks = [[] for _ in range(SCREEN_BATCH_AGENTS)]
    for i, row in enumerate(queue):
        chunks[i % SCREEN_BATCH_AGENTS].append(row)
    for i, rows in enumerate(chunks, 1):
        write_csv(small / "screen_batches" / f"screen_batch_{i:02d}.csv", rows, list(queue[0]))

    retrieval_by_id = {r["integrated_id"]: r for r in retrieval}
    pdf_shards = split_round_robin(pdf_index, PDF_AGENTS)
    copied_files: set[str] = set()
    for i, rows in enumerate(pdf_shards, 1):
        shard = out / "pdf_shards" / f"shard_{i:02d}"
        (shard / "pdfs").mkdir(parents=True)
        manifest = []
        for row in rows:
            joined = dict(retrieval_by_id.get(row["integrated_id"], {}))
            joined.update(row)
            rel = row["consolidated_pdf"]
            src = pdf_root / rel
            if not src.exists():
                hits = list(pdf_root.rglob(Path(rel).name))
                if not hits:
                    raise FileNotFoundError(rel)
                src = hits[0]
            dest = shard / "pdfs" / src.name
            if not dest.exists():
                shutil.copy2(src, dest)
            joined["shard_pdf"] = f"pdfs/{dest.name}"
            manifest.append(joined)
            copied_files.add(row["actual_sha256"])
        write_csv(shard / "manifest.csv", manifest)

    summary = {
        "generated_utc": now(), "prospero": PROSPERO,
        "screen_records": len(master), "duplicate_groups": len(groups), "duplicate_candidate_rows": len(dups),
        "pdf_record_rows": len(pdf_index), "unique_pdf_hashes": len(copied_files), "unresolved_records": len(unresolved),
        "pdf_agents": PDF_AGENTS, "duplicate_agents": DUP_AGENTS, "unresolved_agents": UNRESOLVED_AGENTS,
        "screen_batch_agents": SCREEN_BATCH_AGENTS,
        "governance": "Computational audit preparation only; no formal human screening or final decisions."
    }
    (out / "prepare_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary))


def duplicate_agent(args: argparse.Namespace) -> None:
    root, out, agent = Path(args.input), Path(args.out), int(args.agent)
    out.mkdir(parents=True, exist_ok=True)
    groups = read_csv(find_one(root, "duplicate_group_map.csv"))
    key = next(r["Identity_Key"] for r in groups if int(r["Agent"]) == agent)
    candidates = [r for r in read_csv(find_one(root, "exact_duplicate_candidates.csv")) if r["Identity_Key"] == key]
    master = read_csv(find_one(root, "screening_enrichment_master_1129.csv"))
    by_id = {r["Master_Record_ID"]: r for r in master}
    dois = {norm_doi(r.get("DOI")) for r in candidates if norm_doi(r.get("DOI"))}
    pmids = {norm_text(r.get("PMID")) for r in candidates if norm_text(r.get("PMID"))}
    titles = {norm_title(r.get("Title")) for r in candidates if norm_title(r.get("Title"))}
    if len(dois) == 1 and dois:
        assessment, reason = "High-confidence exact duplicate candidate", "All records share the same normalized DOI"
    elif len(pmids) == 1 and pmids:
        assessment, reason = "High-confidence exact duplicate candidate", "All records share the same PMID"
    elif len(titles) == 1 and titles:
        assessment, reason = "Likely duplicate candidate", "All records share the same normalized title"
    else:
        assessment, reason = "Manual adjudication required", "Identity evidence is not fully concordant"
    canonical = max(candidates, key=lambda r: (completeness(by_id.get(r["Master_Record_ID"], {})), r["Master_Record_ID"]))["Master_Record_ID"]
    rows = []
    for row in candidates:
        m = by_id.get(row["Master_Record_ID"], {})
        rows.append({
            **row,
            "Machine_Duplicate_Assessment": assessment,
            "Machine_Reason": reason,
            "Recommended_Canonical_Record": canonical,
            "Metadata_Completeness_Score": completeness(m),
            "Abstract_Length": len(norm_text(m.get("Enriched_Abstract"))),
            "Human_Adjudication": "Not reviewed",
            "Human_Reviewer": "",
            "Human_Notes": "",
        })
    write_csv(out / f"duplicate_group_{agent:02d}_audit.csv", rows)
    (out / "summary.json").write_text(json.dumps({"agent": agent, "identity_key": key, "records": len(rows), "assessment": assessment, "canonical_recommendation": canonical, "formal_adjudication": 0}, indent=2), encoding="utf-8")


def token_overlap(a: str, b: str) -> float:
    aa = {x for x in norm_title(a).split() if len(x) > 3}
    bb = {x for x in norm_title(b).split() if len(x) > 3}
    return len(aa & bb) / max(1, len(aa))


def pdf_agent(args: argparse.Namespace) -> None:
    root, out, agent = Path(args.input), Path(args.out), int(args.agent)
    out.mkdir(parents=True, exist_ok=True)
    manifest = read_csv(find_one(root, "manifest.csv"))
    from pypdf import PdfReader
    rows = []
    for row in manifest:
        pdf = root / row["shard_pdf"]
        status, reason, pages, text_len, extracted_doi, pdf_title = "Needs manual verification", "", 0, 0, "", ""
        try:
            reader = PdfReader(str(pdf))
            pages = len(reader.pages)
            text = "\n".join((reader.pages[i].extract_text() or "") for i in range(min(3, pages)))[:20000]
            text_len = len(text)
            meta = reader.metadata or {}
            pdf_title = norm_text(getattr(meta, "title", "") or meta.get("/Title", ""))
            dois = re.findall(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", text, flags=re.I)
            extracted_doi = norm_doi(dois[0]) if dois else ""
            expected_doi = norm_doi(row.get("doi"))
            expected_title = norm_text(row.get("title"))
            if expected_doi and expected_doi in {norm_doi(x) for x in dois}:
                status, reason = "Verified by DOI", "Expected DOI appears in extracted PDF text"
            elif expected_title and token_overlap(expected_title, text[:5000]) >= 0.60:
                status, reason = "Likely match by title", "At least 60% of informative title tokens appear in first-page text"
            elif row.get("duplicate_pdf_content", "").lower() == "true":
                status, reason = "Duplicate byte content; mapping review needed", "The same PDF byte stream is linked to multiple records"
            elif text_len < 100:
                status, reason = "Needs OCR/manual verification", "PDF opened but yielded very little extractable text"
            else:
                status, reason = "Needs manual verification", "PDF is readable but metadata evidence is insufficient for automated confirmation"
        except Exception as exc:
            status, reason = "Unreadable PDF", f"{type(exc).__name__}: {str(exc)[:180]}"
        rows.append({
            "Agent": agent, "Integrated_ID": row.get("integrated_id", ""), "Expected_Title": row.get("title", ""),
            "Expected_DOI": row.get("doi", ""), "Expected_PMID": row.get("pmid", ""), "PDF_File": row.get("shard_pdf", ""),
            "Actual_SHA256": row.get("actual_sha256", ""), "Page_Count": pages, "Extracted_Text_Length": text_len,
            "PDF_Metadata_Title": pdf_title, "First_Extracted_DOI": extracted_doi,
            "Machine_Linkage_Status": status, "Machine_Linkage_Reason": reason,
            "Human_Verification": "Not reviewed", "Human_Reviewer": "", "Human_Notes": "",
        })
    write_csv(out / f"pdf_qc_{agent:02d}.csv", rows)
    counts = Counter(r["Machine_Linkage_Status"] for r in rows)
    (out / "summary.json").write_text(json.dumps({"agent": agent, "records": len(rows), "statuses": counts, "formal_full_text_screening": 0}, indent=2), encoding="utf-8")


def query_europepmc(row: dict[str, str]) -> tuple[str, str, str]:
    try:
        import requests
        query = ""
        if norm_text(row.get("pmid")):
            query = f'EXT_ID:{norm_text(row.get("pmid"))}'
        elif norm_doi(row.get("doi")):
            query = f'DOI:"{norm_doi(row.get("doi"))}"'
        if not query:
            return "", "", ""
        r = requests.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search", params={"query": query, "format": "json", "pageSize": 3}, timeout=30)
        r.raise_for_status()
        results = ((r.json().get("resultList") or {}).get("result") or [])
        if not results:
            return "", "", ""
        hit = results[0]
        pmcid = norm_text(hit.get("pmcid"))
        full = norm_text(hit.get("isOpenAccess")).lower() == "y" or bool(pmcid)
        url = f"https://europepmc.org/articles/{pmcid}?pdf=render" if pmcid else ""
        return pmcid, "Open-access candidate" if full else "Metadata match only", url
    except Exception as exc:
        return "", f"Europe PMC lookup error: {type(exc).__name__}", ""


def unresolved_agent(args: argparse.Namespace) -> None:
    root, out, agent = Path(args.input), Path(args.out), int(args.agent)
    out.mkdir(parents=True, exist_ok=True)
    shard = find_one(root, f"unresolved_{agent:02d}.csv")
    records = read_csv(shard)
    master = read_csv(find_one(root, "screening_enrichment_master_1129.csv"))
    by_doi = {norm_doi(r.get("DOI")): r for r in master if norm_doi(r.get("DOI"))}
    by_pmid = {norm_text(r.get("PMID")): r for r in master if norm_text(r.get("PMID"))}
    rank = {"Likely include": "High", "Unclear": "Medium", "Likely exclude": "Low"}
    output = []
    for row in records:
        m = by_doi.get(norm_doi(row.get("doi"))) or by_pmid.get(norm_text(row.get("pmid"))) or {}
        pmcid, epmc_status, epmc_url = query_europepmc(row)
        attempts = []
        try:
            attempts = json.loads(row.get("attempt_log_json") or "[]")
        except Exception:
            pass
        codes = sorted({str(a.get("http_status")) for a in attempts if a.get("http_status")})
        if epmc_url:
            route = "Retry Europe PMC open-access PDF candidate"
        elif "403" in codes:
            route = "Publisher access blocked; use institutional library, author repository, or correspondence"
        elif norm_doi(row.get("doi")):
            route = "DOI available; check publisher, Crossref links, institutional library, and author repository"
        elif norm_text(row.get("pmid")):
            route = "PMID available; check PubMed/PMC and institutional library"
        else:
            route = "Identifier enrichment required before manual retrieval"
        output.append({
            "Agent": agent, "Integrated_ID": row.get("integrated_id", ""), "Title": row.get("title", ""),
            "DOI": row.get("doi", ""), "PMID": row.get("pmid", ""), "Prior_Status": row.get("retrieval_status", ""),
            "Observed_HTTP_Statuses": ";".join(codes), "Matched_Master_Record_ID": m.get("Master_Record_ID", ""),
            "Machine_Triage": m.get("Machine_Triage", "Unmatched"), "Manual_Retrieval_Priority": rank.get(m.get("Machine_Triage", ""), "Unranked"),
            "Europe_PMC_Status": epmc_status, "Discovered_PMCID": pmcid, "Candidate_Open_PDF_URL": epmc_url,
            "Recommended_Next_Route": route, "Retrieval_Completed": "No", "Human_Retrieval_Notes": "",
        })
        time.sleep(0.05)
    write_csv(out / f"unresolved_queue_{agent:02d}.csv", output)
    (out / "summary.json").write_text(json.dumps({"agent": agent, "records": len(output), "open_pdf_candidates": sum(bool(r["Candidate_Open_PDF_URL"]) for r in output), "retrieval_completed": 0}, indent=2), encoding="utf-8")


def batch_agent(args: argparse.Namespace) -> None:
    root, out, agent = Path(args.input), Path(args.out), int(args.agent)
    out.mkdir(parents=True, exist_ok=True)
    rows = read_csv(find_one(root, f"screen_batch_{agent:02d}.csv"))
    common = []
    for i, row in enumerate(rows, 1):
        common.append({
            "Batch": f"TA-{agent:02d}", "Batch_Order": i, "Master_Record_ID": row.get("Master_Record_ID", ""),
            "Title": row.get("Enriched_Title") or row.get("Title") or row.get("Original_Title", ""),
            "Abstract": row.get("Enriched_Abstract") or row.get("Abstract") or row.get("Original_Abstract", ""),
            "Year": row.get("Year", ""), "DOI": row.get("DOI", ""), "PMID": row.get("PMID", ""),
            "Decision": "", "Primary_Exclusion_Reason": "", "Reviewer_Notes": "", "Review_Date": "",
        })
    r1 = [{**r, "Reviewer": "Md. Mizanoor Rahman", "Review_Status": "Not reviewed"} for r in common]
    r2 = [{**r, "Reviewer": "Kapashia Binte Giash", "Review_Status": "Not reviewed"} for r in common]
    write_csv(out / f"TA_{agent:02d}_Reviewer1_Mizan.csv", r1)
    write_csv(out / f"TA_{agent:02d}_Reviewer2_Kapashia.csv", r2)
    write_csv(out / f"TA_{agent:02d}_Reconciliation_Blank.csv", [{
        "Batch": r["Batch"], "Master_Record_ID": r["Master_Record_ID"], "Reviewer1_Decision": "", "Reviewer2_Decision": "",
        "Agreement": "", "Final_Decision": "", "Adjudicator": "", "Resolution_Notes": ""
    } for r in common])
    (out / "summary.json").write_text(json.dumps({"agent": agent, "records": len(common), "reviewer1_completed": 0, "reviewer2_completed": 0}, indent=2), encoding="utf-8")


def consolidate(args: argparse.Namespace) -> None:
    root, out = Path(args.input), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    patterns = {
        "duplicate_audit_recommendations.csv": "duplicate_group_*_audit.csv",
        "pdf_record_linkage_qc.csv": "pdf_qc_*.csv",
        "unresolved_manual_retrieval_queue.csv": "unresolved_queue_*.csv",
    }
    counts = {}
    for name, pat in patterns.items():
        files = sorted(root.rglob(pat))
        all_rows = []
        for p in files:
            all_rows.extend(read_csv(p))
        write_csv(out / name, all_rows)
        counts[name] = len(all_rows)

    screen_dir = out / "title_abstract_screening_batches"
    screen_dir.mkdir(exist_ok=True)
    for p in root.rglob("TA_*.csv"):
        shutil.copy2(p, screen_dir / p.name)

    assert counts["duplicate_audit_recommendations.csv"] == 56, counts
    assert counts["pdf_record_linkage_qc.csv"] == 422, counts
    assert counts["unresolved_manual_retrieval_queue.csv"] == 935, counts
    r1 = sum(len(read_csv(p)) for p in screen_dir.glob("*Reviewer1*.csv"))
    r2 = sum(len(read_csv(p)) for p in screen_dir.glob("*Reviewer2*.csv"))
    assert r1 == 1129 and r2 == 1129, (r1, r2)
    summary = {
        "generated_utc": now(), "prospero": PROSPERO,
        "parallel_agents": DUP_AGENTS + PDF_AGENTS + UNRESOLVED_AGENTS + SCREEN_BATCH_AGENTS,
        "duplicate_candidate_rows_audited": counts["duplicate_audit_recommendations.csv"],
        "pdf_record_rows_qc_prepared": counts["pdf_record_linkage_qc.csv"],
        "unresolved_records_prioritized": counts["unresolved_manual_retrieval_queue.csv"],
        "reviewer1_screening_rows_prepared": r1, "reviewer2_screening_rows_prepared": r2,
        "formal_human_screening_completed": 0, "formal_duplicate_adjudications_completed": 0,
        "full_text_eligibility_decisions_completed": 0,
        "governance": "All outputs are machine-assisted audit or blank human-review templates; no human decisions are claimed."
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out / "README.md").write_text(
        "# SRMA parallel next-stage preparation\n\n"
        "This package contains machine-assisted duplicate evidence, PDF-to-record linkage QC, unresolved full-text retrieval prioritisation, and blank independent title/abstract screening templates.\n\n"
        "It does **not** claim formal human screening, duplicate adjudication, full-text eligibility decisions, extraction, or risk-of-bias assessment.\n",
        encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    x = sub.add_parser("prepare"); x.add_argument("--screen-dir", required=True); x.add_argument("--pdf-dir", required=True); x.add_argument("--out", required=True); x.set_defaults(func=prepare)
    for name, fn, max_n in [("duplicate-agent", duplicate_agent, DUP_AGENTS), ("pdf-agent", pdf_agent, PDF_AGENTS), ("unresolved-agent", unresolved_agent, UNRESOLVED_AGENTS), ("batch-agent", batch_agent, SCREEN_BATCH_AGENTS)]:
        x = sub.add_parser(name); x.add_argument("--agent", type=int, required=True, choices=range(1, max_n + 1)); x.add_argument("--input", required=True); x.add_argument("--out", required=True); x.set_defaults(func=fn)
    x = sub.add_parser("consolidate"); x.add_argument("--input", required=True); x.add_argument("--out", required=True); x.set_defaults(func=consolidate)
    args = p.parse_args(); args.func(args)


if __name__ == "__main__":
    main()
