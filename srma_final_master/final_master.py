#!/usr/bin/env python3
"""Parallel final-master preparation for the Bangladesh childhood immunization SRMA.

Computational prioritisation, retrieval discovery, provenance QA, and blank reviewer
forms only. It never creates human screening, eligibility, extraction, or RoB decisions.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, re, shutil, time, unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

PROSPERO = "CRD420261461557"
TRIAGE_AGENTS = 100
REVIEW_AGENTS = 20
RETRIEVAL_AGENTS = 20
QA_AGENTS = 10
FULLTEXT_AGENTS = 10

CHILD = {"child","children","childhood","infant","infants","newborn","newborns","toddler","toddlers","under five","under-five","pediatric","paediatric"}
VAX = {"vaccin","immuniz","immunis","epi","expanded programme on immunization","expanded program on immunization"}
OUTCOME = {"coverage","uptake","timely","timeliness","delay","dropout","zero dose","zero-dose","unvaccinated","incomplete","fully vaccinated","fully immunized","fully immunised","determinant","factor","barrier","inequality","inequity","access","service delivery","outreach","missed opportunity","defaulter","reminder"}
NEGATIVE_GEO = {"india","pakistan","nepal","sri lanka","afghanistan","china","ethiopia","nigeria","kenya","uganda","ghana"}


def now(): return datetime.now(timezone.utc).isoformat()
def nt(v: Any): return re.sub(r"\s+", " ", str(v or "")).strip()
def norm_doi(v: Any):
    x=nt(v).lower(); x=re.sub(r"^https?://(?:dx\.)?doi\.org/", "", x); x=re.sub(r"^doi:\s*", "", x); return x.rstrip(".,; )]")
def norm_title(v: Any):
    x=unicodedata.normalize("NFKD",nt(v)).encode("ascii","ignore").decode().lower(); return re.sub(r"[^a-z0-9]+"," ",x).strip()
def read_csv(path: Path):
    with path.open(encoding="utf-8-sig",newline="") as f: return list(csv.DictReader(f))
def write_csv(path: Path, rows: Iterable[dict[str,Any]], fields=None):
    rows=list(rows); path.parent.mkdir(parents=True,exist_ok=True)
    fields=fields or (list(rows[0]) if rows else [])
    with path.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore")
        if fields: w.writeheader(); w.writerows({k:r.get(k,"") for k in fields} for r in rows)
def find_one(root: Path, name: str):
    hits=list(root.rglob(name))
    if not hits: raise FileNotFoundError(f"{name} not found under {root}")
    return max(hits,key=lambda p:p.stat().st_size)
def rr(rows,n):
    out=[[] for _ in range(n)]
    for i,r in enumerate(rows): out[i%n].append(r)
    return out
def contains_any(text, terms): return any(t in text for t in terms)


def prepare(args):
    oa,screen,down,out=Path(args.oa),Path(args.screen),Path(args.downstream),Path(args.out)
    if out.exists(): shutil.rmtree(out)
    out.mkdir(parents=True)
    new=read_csv(find_one(oa,"openalex_new_records_for_machine_triage.csv"))
    logs=read_csv(find_one(oa,"openalex_final_search_log.csv"))
    existing=read_csv(find_one(screen,"screening_enrichment_master_1129.csv"))
    fulltext=read_csv(find_one(down,"fulltext_machine_evidence.csv"))
    blank_ft=read_csv(find_one(down,"blank_fulltext_screening_form.csv"))
    blank_ex=read_csv(find_one(down,"blank_extraction_rob_form.csv"))
    assert len(new)==54119, len(new); assert len(existing)==1129; assert len(logs)==58; assert len(fulltext)==422
    small=out/"small"; small.mkdir()
    write_csv(small/"existing_master_1129.csv",existing)
    write_csv(small/"openalex_search_log_58.csv",logs)
    write_csv(small/"fulltext_evidence_422.csv",fulltext)
    write_csv(small/"blank_fulltext_422.csv",blank_ft)
    write_csv(small/"blank_extraction_422.csv",blank_ex)
    for i,rows in enumerate(rr(new,TRIAGE_AGENTS),1): write_csv(out/"triage_shards"/f"triage_{i:03d}.csv",rows,list(new[0]))
    for i,rows in enumerate(rr(logs,QA_AGENTS),1): write_csv(out/"qa_shards"/f"qa_{i:02d}.csv",rows,list(logs[0]))
    rank={"Strong protocol signal":0,"Partial protocol signal":1,"Weak/uncertain protocol signal":2}
    fulltext.sort(key=lambda r:(rank.get(r.get("Machine_Evidence_Status",""),9), r.get("Integrated_ID","")))
    ft_batches=rr(fulltext,FULLTEXT_AGENTS)
    for i,rows in enumerate(ft_batches,1):
        ids={r.get("Integrated_ID","") for r in rows}
        write_csv(out/"fulltext_shards"/f"evidence_{i:02d}.csv",rows,list(fulltext[0]))
        write_csv(out/"fulltext_shards"/f"screen_{i:02d}.csv",[r for r in blank_ft if r.get("Integrated_ID","") in ids])
        write_csv(out/"fulltext_shards"/f"extract_{i:02d}.csv",[r for r in blank_ex if r.get("Integrated_ID","") in ids])
    summary={"prospero":PROSPERO,"new_openalex_records":len(new),"existing_master":len(existing),"search_units":len(logs),"fulltext_rows":len(fulltext),"agents_planned":TRIAGE_AGENTS+REVIEW_AGENTS+RETRIEVAL_AGENTS+QA_AGENTS+FULLTEXT_AGENTS,"formal_human_decisions":0,"generated_utc":now()}
    (out/"prepare_summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps(summary))


def triage_agent(args):
    root,out,agent=Path(args.input),Path(args.out),int(args.agent); out.mkdir(parents=True,exist_ok=True)
    rows=read_csv(find_one(root,f"triage_{agent:03d}.csv")); result=[]
    for r in rows:
        title=nt(r.get("Title")); abstract=nt(r.get("Abstract")); text=(title+" "+abstract).lower()
        bd="bangladesh" in text or "bangladeshi" in text
        child=contains_any(text,CHILD); vax=contains_any(text,VAX); outcome=contains_any(text,OUTCOME)
        neg=[g for g in NEGATIVE_GEO if g in text]
        score=0
        score += 5 if bd else 0; score += 3 if child else 0; score += 4 if vax else 0; score += 2 if outcome else 0
        score += 1 if abstract else 0; score += 1 if norm_doi(r.get("DOI")) else 0
        if neg and not bd: score-=3
        dtype=nt(r.get("Document_Type")).lower()
        if dtype in {"editorial","letter","paratext","reference-entry"}: score-=2
        if bd and child and vax and outcome: priority="High priority"
        elif bd and child and vax: priority="High priority"
        elif (bd and vax) or (child and vax and outcome): priority="Unclear—review"
        elif vax and (bd or child): priority="Unclear—review"
        elif not bd and neg: priority="Low priority—non-Bangladesh signal"
        else: priority="Low priority—weak protocol signal"
        key_doi="doi:"+norm_doi(r.get("DOI")) if norm_doi(r.get("DOI")) else ""
        key_title="title:"+norm_title(title)+"|"+nt(r.get("Year")) if norm_title(title) else ""
        result.append({**r,"Agent":agent,"Bangladesh_Flag":"Yes" if bd else "No","Child_Flag":"Yes" if child else "No","Vaccination_Flag":"Yes" if vax else "No","Outcome_Flag":"Yes" if outcome else "No","Negative_Geography_Signals":"; ".join(neg),"Machine_Priority":priority,"Machine_Score":score,"Machine_Evidence":"; ".join([x for x,b in [("Bangladesh",bd),("child",child),("vaccination",vax),("outcome",outcome)] if b]),"DOI_Key":key_doi,"Title_Year_Key":key_title,"Human_Title_Abstract_Decision":"","Human_Reviewer":"","Human_Review_Date":"","Human_Notes":""})
    write_csv(out/f"triage_{agent:03d}.csv",result)
    (out/"summary.json").write_text(json.dumps({"agent":agent,"records":len(result),"priority_counts":Counter(r["Machine_Priority"] for r in result),"human_decisions":0},indent=2),encoding="utf-8")


def consolidate_triage(args):
    root,prep,out=Path(args.input),Path(args.prepared),Path(args.out); out.mkdir(parents=True,exist_ok=True)
    rows=[]
    for p in sorted(root.rglob("triage_*.csv")): rows.extend(read_csv(p))
    assert len(rows)==54119,len(rows)
    existing=read_csv(find_one(prep,"existing_master_1129.csv"))
    pri=Counter(r["Machine_Priority"] for r in rows)
    rows.sort(key=lambda r:(-int(r.get("Machine_Score") or 0),r.get("Record_ID","")))
    high=[r for r in rows if r["Machine_Priority"]=="High priority"]
    unclear=[r for r in rows if r["Machine_Priority"]=="Unclear—review"]
    low=[r for r in rows if r["Machine_Priority"].startswith("Low priority")]
    fam=defaultdict(list)
    for r in rows:
        k=r.get("DOI_Key") or r.get("Title_Year_Key")
        if k: fam[k].append(r)
    dup=[]
    for k,grp in fam.items():
        if len(grp)>1:
            for r in grp: dup.append({"Candidate_Key":k,"Group_Size":len(grp),"Record_ID":r["Record_ID"],"Title":r.get("Title",""),"Year":r.get("Year",""),"DOI":r.get("DOI",""),"Machine_Recommendation":"Manual duplicate-family adjudication","Human_Adjudication":"Not reviewed","Human_Reviewer":"","Human_Notes":""})
    combined=[]
    for e in existing:
        combined.append({"Master_Set":"Prior verified 1,129 master","Record_ID":e.get("Master_Record_ID") or e.get("Record_ID"),"Title":e.get("Enriched_Title") or e.get("Title") or e.get("Original_Title",""),"Abstract":e.get("Enriched_Abstract") or e.get("Abstract") or e.get("Original_Abstract",""),"Year":e.get("Year",""),"DOI":e.get("DOI",""),"PMID":e.get("PMID",""),"URL":e.get("URL",""),"Machine_Priority":e.get("Machine_Triage","Carried forward"),"Human_Title_Abstract_Decision":"","Human_Reviewer":"","Human_Review_Date":"","Human_Notes":""})
    for r in rows:
        combined.append({"Master_Set":"Prospective OpenAlex new record","Record_ID":r["Record_ID"],"Title":r.get("Title",""),"Abstract":r.get("Abstract",""),"Year":r.get("Year",""),"DOI":r.get("DOI",""),"PMID":r.get("PMID",""),"URL":r.get("URL",""),"Machine_Priority":r.get("Machine_Priority",""),"Human_Title_Abstract_Decision":"","Human_Reviewer":"","Human_Review_Date":"","Human_Notes":""})
    write_csv(out/"openalex_new_machine_triage_54119.csv",rows)
    write_csv(out/"openalex_high_priority.csv",high); write_csv(out/"openalex_unclear_review.csv",unclear); write_csv(out/"openalex_low_priority.csv",low)
    write_csv(out/"probable_duplicate_family_candidates.csv",dup)
    write_csv(out/"combined_discovery_master_55248.csv",combined)
    review_pool=high+unclear
    for i,chunk in enumerate(rr(review_pool,REVIEW_AGENTS),1): write_csv(out/"downstream_shards"/f"review_{i:02d}.csv",chunk,list(rows[0]))
    top=(high+unclear)[:1000]
    for i,chunk in enumerate(rr(top,RETRIEVAL_AGENTS),1): write_csv(out/"downstream_shards"/f"retrieval_{i:02d}.csv",chunk,list(rows[0]))
    summary={"prospero":PROSPERO,"new_records":len(rows),"combined_master":len(combined),"priority_counts":dict(pri),"review_pool":len(review_pool),"probable_duplicate_candidate_rows":len(dup),"top_retrieval_queue":len(top),"formal_human_screening":0,"generated_utc":now()}
    (out/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps(summary))


def review_agent(args):
    root,out,agent=Path(args.input),Path(args.out),int(args.agent); out.mkdir(parents=True,exist_ok=True)
    rows=read_csv(find_one(root,f"review_{agent:02d}.csv")); common=[]
    for i,r in enumerate(rows,1): common.append({"Batch":f"OA-TA-{agent:02d}","Batch_Order":i,"Record_ID":r.get("Record_ID",""),"Title":r.get("Title",""),"Abstract":r.get("Abstract",""),"Year":r.get("Year",""),"DOI":r.get("DOI",""),"PMID":r.get("PMID",""),"Machine_Priority":r.get("Machine_Priority",""),"Decision":"","Primary_Exclusion_Reason":"","Reviewer_Notes":"","Review_Date":""})
    write_csv(out/f"OA_TA_{agent:02d}_Mizan.csv",[{**r,"Reviewer":"Md. Mizanoor Rahman","Review_Status":"Not reviewed"} for r in common])
    write_csv(out/f"OA_TA_{agent:02d}_Kapashia.csv",[{**r,"Reviewer":"Kapashia Binte Giash","Review_Status":"Not reviewed"} for r in common])
    write_csv(out/f"OA_TA_{agent:02d}_Reconciliation.csv",[{"Batch":r["Batch"],"Record_ID":r["Record_ID"],"Reviewer1_Decision":"","Reviewer2_Decision":"","Agreement":"","Final_Decision":"","Adjudicator":"","Resolution_Notes":""} for r in common])


def retrieval_agent(args):
    root,out,agent=Path(args.input),Path(args.out),int(args.agent); out.mkdir(parents=True,exist_ok=True)
    rows=read_csv(find_one(root,f"retrieval_{agent:02d}.csv")); results=[]
    import requests
    s=requests.Session(); s.headers.update({"User-Agent":f"SRMA-Bangladesh/{PROSPERO} (st19009@mbstu.ac.bd)"})
    for r in rows:
        doi=norm_doi(r.get("DOI")); pmid=nt(r.get("PMID")); candidates=[]; errors=[]
        if r.get("URL"): candidates.append(("OpenAlex/landing",r["URL"]))
        if doi:
            candidates.append(("DOI",f"https://doi.org/{quote(doi,safe='/')}"))
            try:
                u=s.get(f"https://api.unpaywall.org/v2/{quote(doi,safe='')}",params={"email":"st19009@mbstu.ac.bd"},timeout=25)
                if u.ok:
                    best=(u.json().get("best_oa_location") or {}); url=best.get("url_for_pdf") or best.get("url")
                    if url: candidates.insert(0,("Unpaywall",url))
            except Exception as e: errors.append("Unpaywall:"+type(e).__name__)
        if pmid or doi:
            try:
                q=f"EXT_ID:{pmid}" if pmid else f'DOI:"{doi}"'
                e=s.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",params={"query":q,"format":"json","pageSize":3},timeout=25)
                if e.ok:
                    hits=((e.json().get("resultList") or {}).get("result") or [])
                    if hits and hits[0].get("pmcid"): candidates.insert(0,("Europe PMC",f"https://europepmc.org/articles/{hits[0]['pmcid']}?pdf=render"))
            except Exception as ex: errors.append("EuropePMC:"+type(ex).__name__)
        seen=[]
        for src,url in candidates:
            if url and url not in [x[1] for x in seen]: seen.append((src,url))
        results.append({"Agent":agent,"Record_ID":r.get("Record_ID",""),"Title":r.get("Title",""),"DOI":r.get("DOI",""),"PMID":r.get("PMID",""),"Machine_Priority":r.get("Machine_Priority",""),"Candidate_Routes":" | ".join(f"{s}:{u}" for s,u in seen),"Candidate_Count":len(seen),"Lookup_Errors":"; ".join(errors),"PDF_Retrieved":"No","Human_Verification":"Not reviewed","Human_Notes":""})
        time.sleep(0.08)
    write_csv(out/f"retrieval_{agent:02d}.csv",results)


def qa_agent(args):
    root,out,agent=Path(args.input),Path(args.out),int(args.agent); out.mkdir(parents=True,exist_ok=True)
    rows=read_csv(find_one(root,f"qa_{agent:02d}.csv")); audits=[]
    for r in rows:
        reported=int(r.get("Reported_Hits") or 0); exported=int(r.get("Exported_Records") or 0); status=r.get("Status","")
        audits.append({**r,"Agent":agent,"Count_Reconciled":"Yes" if status=="completed" and reported==exported else "No","Difference":reported-exported,"QA_Flag":"OK" if status=="completed" and reported==exported else "Investigate","Human_QA_Reviewer":"","Human_QA_Notes":""})
    write_csv(out/f"qa_{agent:02d}.csv",audits)


def fulltext_agent(args):
    root,out,agent=Path(args.input),Path(args.out),int(args.agent); out.mkdir(parents=True,exist_ok=True)
    ev=read_csv(find_one(root,f"evidence_{agent:02d}.csv")); sc=read_csv(find_one(root,f"screen_{agent:02d}.csv")); ex=read_csv(find_one(root,f"extract_{agent:02d}.csv"))
    by={r.get("Integrated_ID",""):r for r in ev}; enriched=[]
    for r in sc:
        e=by.get(r.get("Integrated_ID",""),{})
        enriched.append({**r,"Machine_Evidence_Status":e.get("Machine_Evidence_Status",""),"Suggested_Design":e.get("Suggested_Design",""),"Bangladesh_Flag":e.get("Bangladesh_Flag",""),"Child_Flag":e.get("Child_Flag",""),"Vaccination_Flag":e.get("Vaccination_Flag",""),"Outcome_Flag":e.get("Outcome_Flag",""),"Evidence_Snippet":e.get("Evidence_Snippet",""),"Reviewer":"","Decision":"","Primary_Exclusion_Reason":"","Review_Date":"","Reviewer_Notes":""})
    write_csv(out/f"FT_{agent:02d}_Screening_Blank.csv",enriched)
    write_csv(out/f"FT_{agent:02d}_Extraction_RoB_Blank.csv",ex)


def final_consolidate(args):
    root,triage,out=Path(args.input),Path(args.triage),Path(args.out); out.mkdir(parents=True,exist_ok=True)
    for p in triage.glob("*.csv"): shutil.copy2(p,out/p.name)
    for p in triage.glob("*.json"): shutil.copy2(p,out/p.name)
    review_dir=out/"reviewer_title_abstract_batches"; retrieval_dir=out/"new_record_retrieval_routes"; qa_dir=out/"search_qa"; ft_dir=out/"fulltext_reviewer_batches"
    for d in [review_dir,retrieval_dir,qa_dir,ft_dir]: d.mkdir(exist_ok=True)
    for p in root.rglob("OA_TA_*.csv"): shutil.copy2(p,review_dir/p.name)
    retr=[]
    for p in root.rglob("retrieval_*.csv"): retr.extend(read_csv(p))
    write_csv(retrieval_dir/"top1000_retrieval_routes.csv",retr)
    qa=[]
    for p in root.rglob("qa_*.csv"): qa.extend(read_csv(p))
    write_csv(qa_dir/"openalex_search_unit_qa.csv",qa)
    for p in root.rglob("FT_*.csv"): shutil.copy2(p,ft_dir/p.name)
    tri=json.loads((triage/"summary.json").read_text(encoding="utf-8"))
    summary={"prospero":PROSPERO,"parallel_agents":TRIAGE_AGENTS+REVIEW_AGENTS+RETRIEVAL_AGENTS+QA_AGENTS+FULLTEXT_AGENTS,"new_records_triaged":tri["new_records"],"combined_discovery_master":tri["combined_master"],"reviewer_batches":REVIEW_AGENTS,"retrieval_route_rows":len(retr),"search_qa_rows":len(qa),"fulltext_batches":FULLTEXT_AGENTS,"formal_human_screening_completed":0,"fulltext_decisions_completed":0,"extractions_completed":0,"rob_completed":0,"generated_utc":now()}
    (out/"final_summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    (out/"README.md").write_text("# SRMA final-master preparation\n\nMachine prioritisation, retrieval routes, provenance QA, and blank reviewer forms only. No human screening, eligibility, extraction, or risk-of-bias completion is claimed.\n",encoding="utf-8")
    print(json.dumps(summary))


def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="cmd",required=True)
    x=sub.add_parser("prepare"); x.add_argument("--oa",required=True); x.add_argument("--screen",required=True); x.add_argument("--downstream",required=True); x.add_argument("--out",required=True); x.set_defaults(func=prepare)
    x=sub.add_parser("triage-agent"); x.add_argument("--agent",type=int,required=True,choices=range(1,TRIAGE_AGENTS+1)); x.add_argument("--input",required=True); x.add_argument("--out",required=True); x.set_defaults(func=triage_agent)
    x=sub.add_parser("consolidate-triage"); x.add_argument("--input",required=True); x.add_argument("--prepared",required=True); x.add_argument("--out",required=True); x.set_defaults(func=consolidate_triage)
    for name,fn,n in [("review-agent",review_agent,REVIEW_AGENTS),("retrieval-agent",retrieval_agent,RETRIEVAL_AGENTS),("qa-agent",qa_agent,QA_AGENTS),("fulltext-agent",fulltext_agent,FULLTEXT_AGENTS)]:
        x=sub.add_parser(name); x.add_argument("--agent",type=int,required=True,choices=range(1,n+1)); x.add_argument("--input",required=True); x.add_argument("--out",required=True); x.set_defaults(func=fn)
    x=sub.add_parser("final-consolidate"); x.add_argument("--input",required=True); x.add_argument("--triage",required=True); x.add_argument("--out",required=True); x.set_defaults(func=final_consolidate)
    a=p.parse_args(); a.func(a)
if __name__=="__main__": main()
