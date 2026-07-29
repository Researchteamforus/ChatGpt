#!/usr/bin/env python3
"""Year-bounded parallel OpenAlex discovery for the Bangladesh childhood immunization SRMA.

Discovery only. No screening or eligibility decisions are made.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, os, re, time, unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROSPERO = "CRD420261461557"
EMAIL = os.getenv("OPENALEX_MAILTO", "st19009@mbstu.ac.bd")
API_KEY = os.getenv("OPENALEX_API_KEY", "").strip()
CORE = ('Bangladesh AND (immunization OR immunisation OR vaccination OR "expanded programme on immunization" OR '
        '"expanded program on immunization" OR EPI) AND (infant OR infants OR child OR children OR childhood OR newborn OR newborns OR '
        '"under five" OR under-five OR toddler OR toddlers)')
QUERIES = {
    "coverage": CORE + ' AND (coverage OR uptake OR "full vaccination" OR "complete vaccination" OR "fully vaccinated" OR "fully immunized" OR "fully immunised" OR "antigen-specific")',
    "timeliness": CORE + ' AND (timeliness OR timely OR delayed OR delay OR "age-appropriate" OR "age appropriate" OR invalid OR schedule OR adherence)',
    "dropout": CORE + ' AND (dropout OR "drop out" OR "zero dose" OR zero-dose OR unvaccinated OR incomplete OR partial OR "under vaccinated" OR "under-vaccinated" OR "under immunized" OR "under-immunized")',
    "determinants": CORE + ' AND (determinant OR determinants OR factor OR factors OR barrier OR barriers OR inequality OR inequalities OR inequity OR inequities OR socioeconomic OR maternal OR caregiver OR geographic OR access OR "health service" OR "health services")',
    "programme": CORE + ' AND ("missed opportunity" OR "missed opportunities" OR "service delivery" OR readiness OR outreach OR defaulter OR reminder OR reminders OR intervention OR interventions OR programme OR programmes OR program OR programs)',
}
FIELDS = ["Record_ID","Source","Task_ID","Query_Group","Year_Band","Source_ID","Title","Abstract","Authors","Year","Journal_or_Institution","DOI","PMID","PMCID","URL","Document_Type","Language","Retrieved_UTC","OpenAlex_Relevance_Score","OpenAlex_Cited_By_Count","Open_Access_Status"]
SELECT = ",".join(["id","display_name","publication_year","type","language","cited_by_count","relevance_score","doi","ids","authorships","primary_location","open_access","abstract_inverted_index"])
TASK_IDS = {"coverage":6,"timeliness":7,"dropout":8,"determinants":9,"programme":10}

def now(): return datetime.now(timezone.utc).isoformat()
def nt(v): return re.sub(r"\s+"," ",str(v or "")).strip()
def nd(v):
    x=nt(v).lower(); x=re.sub(r"^https?://(?:dx\.)?doi\.org/","",x); x=re.sub(r"^doi:\s*","",x); return x.rstrip(".,; )]")
def title_key(v):
    x=unicodedata.normalize("NFKD",nt(v)).encode("ascii","ignore").decode().lower(); return re.sub(r"[^a-z0-9]+"," ",x).strip()
def abstract(idx):
    if not isinstance(idx,dict): return ""
    p=[]
    for w,poses in idx.items():
        for pos in poses or []:
            try:p.append((int(pos),str(w)))
            except:pass
    return " ".join(w for _,w in sorted(p))
def record(work, group, band):
    ids=work.get("ids") or {}; loc=work.get("primary_location") or {}; src=loc.get("source") or {}
    title=nt(work.get("display_name") or work.get("title")); doi=nd(ids.get("doi") or work.get("doi")); oid=nt(work.get("id"))
    rid="SRMA-"+hashlib.sha256(f"OpenAlex|{oid}|{doi}|{title_key(title)}".encode()).hexdigest()[:20]
    return {"Record_ID":rid,"Source":"OpenAlex","Task_ID":TASK_IDS[group],"Query_Group":group,"Year_Band":band,"Source_ID":oid,"Title":title,
            "Abstract":abstract(work.get("abstract_inverted_index")),"Authors":"; ".join(nt((a.get("author") or {}).get("display_name")) for a in work.get("authorships") or [] if nt((a.get("author") or {}).get("display_name"))),
            "Year":nt(work.get("publication_year")),"Journal_or_Institution":nt(src.get("display_name")),"DOI":doi,
            "PMID":nt(ids.get("pmid")).replace("https://pubmed.ncbi.nlm.nih.gov/","").strip("/"),"PMCID":nt(ids.get("pmcid")).replace("https://www.ncbi.nlm.nih.gov/pmc/articles/","").strip("/"),
            "URL":nt(loc.get("landing_page_url") or oid),"Document_Type":nt(work.get("type")),"Language":nt(work.get("language")),"Retrieved_UTC":now(),
            "OpenAlex_Relevance_Score":nt(work.get("relevance_score")),"OpenAlex_Cited_By_Count":nt(work.get("cited_by_count")),"Open_Access_Status":nt((work.get("open_access") or {}).get("oa_status"))}
def write(path, rows, fields=None):
    rows=list(rows); path.parent.mkdir(parents=True,exist_ok=True); fields=fields or (list(rows[0]) if rows else [])
    with path.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore")
        if fields:w.writeheader(); w.writerows({k:r.get(k,"") for k in fields} for r in rows)
def request(session, query, cursor, start, end):
    params={"search":query,"per_page":100,"cursor":cursor,"mailto":EMAIL,"select":SELECT,
            "filter":f"from_publication_date:{start}-01-01,to_publication_date:{end}-12-31"}
    if API_KEY: params["api_key"]=API_KEY
    last=None
    for attempt in range(1,7):
        try:
            r=session.get("https://api.openalex.org/works",params=params,timeout=60)
            if r.status_code in {429,500,502,503,504}:
                time.sleep(int(r.headers.get("Retry-After",min(30,2**attempt)))); continue
            r.raise_for_status(); return r
        except Exception as e:
            last=e; time.sleep(min(30,2**attempt))
    raise RuntimeError(f"{type(last).__name__}: {str(last)[:300]}")
def run(args):
    import requests
    out=Path(args.out); out.mkdir(parents=True,exist_ok=True); band=f"{args.start}-{args.end}"; group=args.group
    s=requests.Session(); s.headers.update({"User-Agent":f"SRMA-Bangladesh/{PROSPERO} ({EMAIL})","Accept":"application/json"})
    rows=[]; cursor="*"; pages=0; reported=0; status="completed"; error=""; started=now()
    try:
        while cursor:
            r=request(s,QUERIES[group],cursor,args.start,args.end); payload=r.json(); pages+=1
            if pages==1:
                reported=int((payload.get("meta") or {}).get("count") or 0)
                print(f"{group} {band}: reported={reported} key={bool(API_KEY)}",flush=True)
                if reported>9500: raise RuntimeError("Year band exceeds 9,500 hits and requires a finer split")
            results=payload.get("results") or []
            rows.extend(record(x,group,band) for x in results)
            print(f"{group} {band}: page={pages} exported={len(rows)}",flush=True)
            nxt=(payload.get("meta") or {}).get("next_cursor")
            if not results or not nxt or nxt==cursor: break
            cursor=nxt; time.sleep(0.12 if not API_KEY else 0.05)
    except Exception as e:
        status="failed"; error=f"{type(e).__name__}: {str(e)[:500]}"
    write(out/"records.csv",rows,FIELDS)
    log={"PROSPERO":PROSPERO,"Group":group,"Year_Band":band,"Start_Year":args.start,"End_Year":args.end,"Query":QUERIES[group],"Status":status,"Reported_Hits":reported,"Exported_Records":len(rows),"Pages":pages,"API_Key_Used":bool(API_KEY),"Started_UTC":started,"Finished_UTC":now(),"Error":error}
    write(out/"search_log.csv",[log],list(log)); (out/"search_log.json").write_text(json.dumps(log,indent=2),encoding="utf-8")
    if status!="completed": raise SystemExit(2)
def read(path):
    with path.open(encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))
def consolidate(args):
    root,out=Path(args.input),Path(args.out); out.mkdir(parents=True,exist_ok=True)
    logs=[]; rows=[]
    for p in root.rglob("search_log.csv"): logs.extend(read(p))
    for p in root.rglob("records.csv"): rows.extend(read(p))
    expected=55
    if len(logs)!=expected: raise SystemExit(f"Expected {expected} search logs, found {len(logs)}")
    failed=[x for x in logs if x.get("Status")!="completed"]
    unique={}; provenance=defaultdict(set); duplicate=[]
    for r in rows:
        key=("openalex:"+r["Source_ID"].lower()) if r.get("Source_ID") else (("doi:"+nd(r.get("DOI"))) if nd(r.get("DOI")) else "title:"+title_key(r.get("Title"))+"|"+r.get("Year",""))
        provenance[key].add(r.get("Query_Group","")+"@"+r.get("Year_Band",""))
        if key not in unique: unique[key]=dict(r)
        else:
            duplicate.append({"Exact_Key":key,"Kept_Record_ID":unique[key]["Record_ID"],"Removed_Record_ID":r["Record_ID"],"Title":r.get("Title","")})
            if len(r.get("Abstract",""))>len(unique[key].get("Abstract","")): unique[key]=dict(r)
    uniques=list(unique.values())
    for r in uniques:
        key=("openalex:"+r["Source_ID"].lower()) if r.get("Source_ID") else (("doi:"+nd(r.get("DOI"))) if nd(r.get("DOI")) else "title:"+title_key(r.get("Title"))+"|"+r.get("Year",""))
        r["Query_Group"]="; ".join(sorted(provenance[key]))
    write(out/"openalex_bounded_all_rows.csv",rows,FIELDS); write(out/"openalex_bounded_exact_unique.csv",uniques,FIELDS); write(out/"openalex_bounded_duplicates.csv",duplicate); write(out/"openalex_bounded_search_log.csv",logs,list(logs[0]))
    summary={"prospero":PROSPERO,"agents_expected":expected,"agents_completed":len(logs)-len(failed),"agents_failed":len(failed),"reported_hits_sum_not_deduplicated":sum(int(x.get("Reported_Hits") or 0) for x in logs),"exported_rows":len(rows),"exact_unique_records":len(uniques),"exact_duplicate_rows":len(rows)-len(uniques),"formal_screening_decisions":0,"completed_utc":now()}
    (out/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    if failed: raise SystemExit(f"{len(failed)} bounded agents failed; package retained for diagnosis")
    print(json.dumps(summary,indent=2))
def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="cmd",required=True)
    x=sub.add_parser("run"); x.add_argument("--group",choices=sorted(QUERIES),required=True); x.add_argument("--start",type=int,required=True); x.add_argument("--end",type=int,required=True); x.add_argument("--out",required=True); x.set_defaults(func=run)
    x=sub.add_parser("consolidate"); x.add_argument("--input",required=True); x.add_argument("--out",required=True); x.set_defaults(func=consolidate)
    a=p.parse_args(); a.func(a)
if __name__=="__main__":main()
