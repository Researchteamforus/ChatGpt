#!/usr/bin/env python3
"""Consolidate 30 SRMA search workstreams into an auditable package."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path("downloaded")
OUT = Path("final_search_package")
OUT.mkdir(exist_ok=True)


def text(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()


def doi(v: Any) -> str:
    s = text(v).lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    s = re.sub(r"^doi:\s*", "", s)
    return s.rstrip(".,; ")


def title(v: Any) -> str:
    s = unicodedata.normalize("NFKD", text(v)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()

records = []
logs = []
for p in sorted(ROOT.rglob("records.csv")):
    try:
        df = pd.read_csv(p, dtype=str, keep_default_na=False)
        df["Artifact_Path"] = str(p.parent)
        records.append(df)
    except Exception as exc:
        logs.append({"status":"failed_to_read_records","path":str(p),"error":repr(exc)})
for p in sorted(ROOT.rglob("search_log.json")):
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        flat = {
            "Task_ID": obj.get("task",{}).get("task_id"),
            "Source": obj.get("task",{}).get("source"),
            "Query_Group": obj.get("task",{}).get("group"),
            "Method": obj.get("task",{}).get("method"),
            "Formal_Protocol_Search": obj.get("formal_protocol_search"),
            "Query": obj.get("task",{}).get("query"),
            "Started_UTC": obj.get("started_utc"),
            "Finished_UTC": obj.get("finished_utc"),
            "Status": obj.get("status"),
            "Reported_Hits": obj.get("reported_hits"),
            "Exported_Records": obj.get("exported_records"),
            "Error": obj.get("error"),
            "Artifact_Path": str(p.parent),
        }
        logs.append(flat)
    except Exception as exc:
        logs.append({"Status":"failed_to_read_log","Artifact_Path":str(p),"Error":repr(exc)})

all_df = pd.concat(records, ignore_index=True) if records else pd.DataFrame()
log_df = pd.DataFrame(logs)

if not all_df.empty:
    all_df["DOI_norm"] = all_df.get("DOI", "").map(doi)
    all_df["PMID_norm"] = all_df.get("PMID", "").map(lambda x: re.sub(r"\D", "", text(x)))
    all_df["Title_norm"] = all_df.get("Title", "").map(title)
    all_df["Dedup_Key"] = ""
    for idx, row in all_df.iterrows():
        if row["DOI_norm"]:
            key = "doi:" + row["DOI_norm"]
        elif row["PMID_norm"]:
            key = "pmid:" + row["PMID_norm"]
        elif text(row.get("Source_ID")) and text(row.get("Source")):
            key = "sid:" + text(row.get("Source")) + ":" + text(row.get("Source_ID"))
        else:
            key = "title:" + row["Title_norm"]
        all_df.at[idx, "Dedup_Key"] = key
    all_df["Duplicate_Group_Size"] = all_df.groupby("Dedup_Key")["Dedup_Key"].transform("size")
    source_rank = {"PubMed/MEDLINE":0,"Europe PMC":1,"OpenAlex":2,"WHO IRIS":3,"WHO Global Index Medicus/IMSEAR":4,"BanglaJOL":5,"DGHS EPI/CES archive":6,"Institutional repositories":7}
    all_df["_rank"] = all_df["Source"].map(source_rank).fillna(99)
    all_df["_abstract_len"] = all_df.get("Abstract", "").map(lambda x: len(text(x)))
    ordered = all_df.sort_values(["Dedup_Key","_rank","_abstract_len"], ascending=[True,True,False])
    master = ordered.drop_duplicates("Dedup_Key", keep="first").copy()
    prov = ordered.groupby("Dedup_Key").agg(
        Sources=("Source", lambda s: "; ".join(sorted(set(map(text, s))))),
        Task_IDs=("Task_ID", lambda s: "; ".join(sorted(set(map(text, s))))),
        Query_Groups=("Query_Group", lambda s: "; ".join(sorted(set(map(text, s))))),
        Record_Count=("Dedup_Key", "size"),
    ).reset_index()
    master = master.merge(prov, on="Dedup_Key", how="left")
    duplicate_log = ordered[ordered["Duplicate_Group_Size"] > 1].copy()
    all_df.drop(columns=["_rank","_abstract_len"], errors="ignore").to_csv(OUT/"all_raw_union.csv", index=False, encoding="utf-8-sig")
    master.drop(columns=["_rank","_abstract_len"], errors="ignore").to_csv(OUT/"exact_deduplicated_master.csv", index=False, encoding="utf-8-sig")
    duplicate_log.drop(columns=["_rank","_abstract_len"], errors="ignore").to_csv(OUT/"exact_duplicate_audit.csv", index=False, encoding="utf-8-sig")
else:
    master = pd.DataFrame(); duplicate_log = pd.DataFrame()

log_df.to_csv(OUT/"search_log.csv", index=False, encoding="utf-8-sig")
failed = log_df[log_df.get("Status", pd.Series(dtype=str)).astype(str) != "completed"] if not log_df.empty else pd.DataFrame()
failed.to_csv(OUT/"failed_or_incomplete_tasks.csv", index=False, encoding="utf-8-sig")

summary = pd.DataFrame([
    {"Metric":"Expected workstreams","Value":30},
    {"Metric":"Logs received","Value":len(log_df)},
    {"Metric":"Completed workstreams","Value":int((log_df.get("Status", pd.Series(dtype=str)) == "completed").sum()) if not log_df.empty else 0},
    {"Metric":"Failed/incomplete workstreams","Value":len(failed)},
    {"Metric":"Raw exported records","Value":len(all_df)},
    {"Metric":"Exact-deduplicated records","Value":len(master)},
    {"Metric":"Exact duplicate rows","Value":len(all_df)-len(master)},
    {"Metric":"Screening decisions made","Value":0},
])
summary.to_csv(OUT/"summary.csv", index=False, encoding="utf-8-sig")

with pd.ExcelWriter(OUT/"SRMA_Prospective_Search_Audit.xlsx", engine="openpyxl") as xw:
    summary.to_excel(xw, sheet_name="Summary", index=False)
    log_df.to_excel(xw, sheet_name="Search Log", index=False)
    if not master.empty: master.drop(columns=["_rank","_abstract_len"], errors="ignore").to_excel(xw, sheet_name="Exact Dedup Master", index=False)
    if not duplicate_log.empty: duplicate_log.drop(columns=["_rank","_abstract_len"], errors="ignore").to_excel(xw, sheet_name="Duplicate Audit", index=False)
    failed.to_excel(xw, sheet_name="Failures", index=False)

readme = f"""# SRMA Bangladesh prospective search package

PROSPERO: CRD420261461557

This package consolidates 30 GitHub Actions search workstreams. It records exact queries, UTC execution times, raw source responses, structured exports, failures, and conservative exact deduplication. It does not claim title/abstract screening, full-text screening, extraction, or risk-of-bias decisions.

## Run summary

- Logs received: {len(log_df)}/30
- Completed workstreams: {int((log_df.get('Status', pd.Series(dtype=str)) == 'completed').sum()) if not log_df.empty else 0}
- Failed/incomplete workstreams: {len(failed)}
- Raw exported records: {len(all_df)}
- Exact-deduplicated records: {len(master)}

Subscription-only Embase and Web of Science are outside this public-API run unless institutional credentials/access are supplied. Scopus is maintained in the separately verified Scopus audit package and must be reconciled with this output before the final PRISMA freeze.
"""
(OUT/"README.md").write_text(readme, encoding="utf-8")
manifest = []
for p in sorted(OUT.iterdir()):
    if p.is_file():
        manifest.append({"file":p.name,"bytes":p.stat().st_size,"sha256":hashlib.sha256(p.read_bytes()).hexdigest()})
pd.DataFrame(manifest).to_csv(OUT/"file_manifest_sha256.csv", index=False)

with zipfile.ZipFile("SRMA_Prospective_Search_Package.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for p in sorted(OUT.iterdir()):
        if p.is_file(): z.write(p, arcname=p.name)
print(summary.to_string(index=False))
