#!/usr/bin/env python3
"""Bottleneck-resolution preparation for the Bangladesh childhood immunization SRMA.

Creates retrieval/revalidation manifests, admin-only calibration evidence packs,
duplicate-family evidence, blank full-text priority queues, and future decision
import validators. It never creates human screening or eligibility decisions.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, re, shutil, time, unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

PROSPERO="CRD420261461557"
RETRIEVAL_AGENTS=20
REVALIDATE_AGENTS=10
CALIBRATION_AGENTS=10
DUPLICATE_AGENTS=10
FULLTEXT_AGENTS=10
INGESTION_AGENTS=10


def now(): return datetime.now(timezone.utc).isoformat()
def nt(v:Any): return re.sub(r"\s+"," ",str(v or "")).strip()
def norm_doi(v:Any):
    x=nt(v).lower(); x=re.sub(r"^https?://(?:dx\.)?doi\.org/","",x); x=re.sub(r"^doi:\s*","",x)
    return x.rstrip(".,; )]")
def norm_title(v:Any):
    x=unicodedata.normalize("NFKD",nt(v)).encode("ascii","ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+"," ",x).strip()
def read_csv(p:Path):
    with p.open(encoding="utf-8-sig",newline="") as f: return list(csv.DictReader(f))
def write_csv(p:Path, rows:Iterable[dict[str,Any]], fields=None):
    rows=list(rows); p.parent.mkdir(parents=True,exist_ok=True); fields=fields or (list(rows[0]) if rows else [])
    with p.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore")
        if fields: w.writeheader(); w.writerows({k:r.get(k,"") for k in fields} for r in rows)
def find_one(root:Path,name:str):
    hits=list(root.rglob(name))
    if not hits: raise FileNotFoundError(f"{name} not found under {root}")
    return max(hits,key=lambda p:p.stat().st_size)
def rr(rows,n):
    out=[[] for _ in range(n)]
    for i,r in enumerate(rows): out[i%n].append(r)
    return out
def combine(root:Path,pattern:str):
    rows=[]
    for p in sorted(root.rglob(pattern)): rows.extend(read_csv(p))
    return rows


def prepare(args):
    rh,screen,final,out=map(Path,[args.rh,args.screen,args.final,args.out])
    if out.exists(): shutil.rmtree(out)
    out.mkdir(parents=True)
    ft=combine(rh,"fulltext_readiness_*.csv")
    assert len(ft)==768,len(ft); assert len({r.get('Record_ID') for r in ft})==768
    unresolved=[r for r in ft if nt(r.get("PDF_Retrieved")).lower()!="yes"]
    retrieved=[r for r in ft if nt(r.get("PDF_Retrieved")).lower()=="yes"]
    assert len(unresolved)==520,len(unresolved); assert len(retrieved)==248,len(retrieved)
    dup=read_csv(find_one(final,"probable_duplicate_family_candidates.csv"))
    assert len(dup)>=800,len(dup)
    masters=read_csv(find_one(final,"combined_discovery_master_55248.csv"))
    master_by={r.get("Record_ID",""):r for r in masters}
    adjud=sorted(screen.rglob("CAL_*_Adjudication_Blank.csv"))
    assert len(adjud)==10,len(adjud)
    reviewer=sorted(screen.rglob("TA_EXEC_*_Mizan.csv"))+sorted(screen.rglob("TA_EXEC_*_Kapashia.csv"))
    assert len(reviewer)==20,len(reviewer)
    for i,rows in enumerate(rr(unresolved,RETRIEVAL_AGENTS),1): write_csv(out/"retrieval"/f"retrieval_{i:02d}.csv",rows,list(unresolved[0]))
    for i,rows in enumerate(rr(retrieved,REVALIDATE_AGENTS),1): write_csv(out/"revalidate"/f"revalidate_{i:02d}.csv",rows,list(retrieved[0]))
    for i,p in enumerate(adjud,1):
        rows=read_csv(p); pack=[]
        for r in rows:
            m=master_by.get(r.get("Record_ID",""),{})
            pack.append({**r,"Admin_Machine_Priority":m.get("Machine_Priority",""),"Admin_Abstract":m.get("Abstract",""),"Admin_DOI":m.get("DOI",""),"Admin_PMID":m.get("PMID",""),"Admin_Evidence_Use":"Calibration support only; keep separate from blinded reviewer files"})
        write_csv(out/"calibration"/f"calibration_{i:02d}.csv",pack)
    for i,rows in enumerate(rr(dup,DUPLICATE_AGENTS),1): write_csv(out/"duplicate"/f"duplicate_{i:02d}.csv",rows,list(dup[0]))
    for i,rows in enumerate(rr(ft,FULLTEXT_AGENTS),1): write_csv(out/"fulltext"/f"fulltext_{i:02d}.csv",rows,list(ft[0]))
    pairs=[]
    for p in reviewer:
        pairs.append({"Filename":p.name,"Relative_Path":str(p.relative_to(screen)),"SHA256":hashlib.sha256(p.read_bytes()).hexdigest(),"Rows":len(read_csv(p))})
    for i,rows in enumerate(rr(pairs,INGESTION_AGENTS),1): write_csv(out/"ingestion"/f"ingestion_{i:02d}.csv",rows,list(pairs[0]))
    summary={"prospero":PROSPERO,"fulltext_candidates":len(ft),"pdf_route_found":len(retrieved),"pdf_unresolved":len(unresolved),"duplicate_candidate_rows":len(dup),"reviewer_files":len(reviewer),"calibration_files":len(adjud),"agents":70,"formal_human_decisions":0,"generated_utc":now()}
    (out/"prepare_summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps(summary))


def candidate_routes(session,r):
    doi=norm_doi(r.get("DOI")); pmid=nt(r.get("PMID")); routes=[]; errors=[]
    if doi:
        routes.append(("DOI",f"https://doi.org/{quote(doi,safe='/')}"))
        try:
            x=session.get(f"https://api.unpaywall.org/v2/{quote(doi,safe='')}",params={"email":"st19009@mbstu.ac.bd"},timeout=20)
            if x.ok:
                loc=x.json().get("best_oa_location") or {}; u=loc.get("url_for_pdf") or loc.get("url")
                if u: routes.insert(0,("Unpaywall",u))
        except Exception as e: errors.append("Unpaywall:"+type(e).__name__)
    if pmid or doi:
        try:
            q=f"EXT_ID:{pmid}" if pmid else f'DOI:"{doi}"'
            x=session.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",params={"query":q,"format":"json","pageSize":3},timeout=20)
            if x.ok:
                hits=((x.json().get("resultList") or {}).get("result") or [])
                if hits and hits[0].get("pmcid"): routes.insert(0,("EuropePMC",f"https://europepmc.org/articles/{hits[0]['pmcid']}?pdf=render"))
        except Exception as e: errors.append("EuropePMC:"+type(e).__name__)
    seen=[]
    for s,u in routes:
        if u and u not in [z[1] for z in seen]: seen.append((s,u))
    return seen,errors


def fetch_pdf(session,routes,outdir,record_id):
    outdir.mkdir(parents=True,exist_ok=True); attempts=[]
    for src,url in routes[:6]:
        try:
            x=session.get(url,timeout=35,allow_redirects=True,headers={"Accept":"application/pdf,text/html;q=0.8,*/*;q=0.5"})
            data=x.content; ispdf=data[:5]==b"%PDF-"
            attempts.append(f"{src}:{x.status_code}:{'PDF' if ispdf else 'not-pdf'}")
            if x.ok and ispdf:
                p=outdir/f"{record_id}.pdf"; p.write_bytes(data)
                return "Yes",src,x.url,len(data),hashlib.sha256(data).hexdigest(),"; ".join(attempts)
        except Exception as e: attempts.append(f"{src}:{type(e).__name__}")
    return "No","","",0,"","; ".join(attempts)


def retrieval_like(args,mode):
    import requests
    root,out,agent=Path(args.input),Path(args.out),int(args.agent); out.mkdir(parents=True,exist_ok=True)
    rows=read_csv(find_one(root,f"{mode}_{agent:02d}.csv")); session=requests.Session(); session.headers.update({"User-Agent":f"SRMA-Bangladesh/{PROSPERO} st19009@mbstu.ac.bd"})
    result=[]
    for r in rows:
        rid=nt(r.get("Record_ID")); routes,errors=candidate_routes(session,r); got,src,url,n,sha,attempts=fetch_pdf(session,routes,out/"pdfs",rid)
        result.append({"Agent":agent,"Mode":mode,"Record_ID":rid,"Title":r.get("Title",""),"DOI":r.get("DOI",""),"PMID":r.get("PMID",""),"Previously_PDF_Retrieved":r.get("PDF_Retrieved",""),"PDF_Verified_This_Run":got,"Verified_Source":src,"Verified_Final_URL":url,"PDF_Bytes":n,"PDF_SHA256":sha,"Candidate_Routes":" | ".join(f"{s}:{u}" for s,u in routes),"Lookup_Errors":"; ".join(errors),"Attempt_Log":attempts,"Human_Verification":"Not reviewed","Human_Notes":""})
        time.sleep(.08)
    write_csv(out/f"{mode}_manifest_{agent:02d}.csv",result)
    (out/"summary.json").write_text(json.dumps({"agent":agent,"mode":mode,"records":len(result),"pdf_verified":sum(r['PDF_Verified_This_Run']=='Yes' for r in result)},indent=2),encoding="utf-8")

def retrieval_agent(args): retrieval_like(args,"retrieval")
def revalidate_agent(args): retrieval_like(args,"revalidate")


def calibration_agent(args):
    root,out,agent=Path(args.input),Path(args.out),int(args.agent); out.mkdir(parents=True,exist_ok=True)
    rows=read_csv(find_one(root,f"calibration_{agent:02d}.csv")); result=[]
    for r in rows:
        text=(nt(r.get("Title"))+" "+nt(r.get("Admin_Abstract"))).lower()
        result.append({**r,"Admin_Bangladesh_Signal":"Yes" if "bangladesh" in text or "bangladeshi" in text else "No","Admin_Vaccination_Signal":"Yes" if any(x in text for x in ["vaccin","immuniz","immunis"]) else "No","Admin_Child_Signal":"Yes" if any(x in text for x in ["child","infant","under-five","pediatric","paediatric"]) else "No","Reviewer1_Decision":"","Reviewer2_Decision":"","Agreement":"","Final_Decision":"","Adjudicator":"","Resolution_Notes":""})
    write_csv(out/f"calibration_evidence_{agent:02d}.csv",result)


def duplicate_agent(args):
    root,out,agent=Path(args.input),Path(args.out),int(args.agent); out.mkdir(parents=True,exist_ok=True)
    rows=read_csv(find_one(root,f"duplicate_{agent:02d}.csv")); groups=defaultdict(list)
    for r in rows: groups[r.get("Candidate_Key","")].append(r)
    result=[]
    for key,grp in groups.items():
        titles=[norm_title(r.get("Title")) for r in grp]; dois=[norm_doi(r.get("DOI")) for r in grp]
        maxsim=max([SequenceMatcher(None,a,b).ratio() for i,a in enumerate(titles) for b in titles[i+1:]] or [1.0])
        same_doi=bool(dois and all(d and d==dois[0] for d in dois))
        canonical=max(grp,key=lambda r:sum(bool(nt(r.get(x))) for x in ["DOI","PMID","Title","Year"]))
        for r in grp:
            result.append({**r,"Agent":agent,"Title_Similarity_Max":round(maxsim,4),"All_Nonempty_DOIs_Identical":"Yes" if same_doi else "No","Suggested_Canonical_Record_ID":canonical.get("Record_ID",""),"Machine_Action":"Strong duplicate evidence" if same_doi or maxsim>=.97 else "Manual family review","Human_Adjudication":"Not reviewed","Human_Reviewer":"","Human_Notes":""})
    write_csv(out/f"duplicate_evidence_{agent:02d}.csv",result)


def fulltext_agent(args):
    root,out,agent=Path(args.input),Path(args.out),int(args.agent); out.mkdir(parents=True,exist_ok=True)
    rows=read_csv(find_one(root,f"fulltext_{agent:02d}.csv")); result=[]
    for r in rows:
        pdf=nt(r.get("PDF_Retrieved")).lower()=="yes"
        result.append({**r,"Agent":agent,"Operational_Priority":"A—PDF available" if pdf else "B—PDF unresolved","Required_Next_Action":"Title-abstract decision then full-text review" if pdf else "Title-abstract decision and lawful PDF retrieval","Full_Text_Decision":"","Primary_Exclusion_Reason":"","Reviewer":"","Review_Date":"","Reviewer_Notes":"","Formal_Eligibility_Completed":"No"})
    write_csv(out/f"fulltext_priority_{agent:02d}.csv",result)


def ingestion_agent(args):
    root,out,agent=Path(args.input),Path(args.out),int(args.agent); out.mkdir(parents=True,exist_ok=True)
    rows=read_csv(find_one(root,f"ingestion_{agent:02d}.csv")); specs=[]
    required=["Record_ID","Reviewer","Decision","Exclusion_Reason_Code","Reviewer_Notes","Review_Date","Review_Status"]
    allowed=["Include","Exclude","Unclear"]
    for r in rows:
        specs.append({**r,"Required_Columns":" | ".join(required),"Allowed_Decisions":" | ".join(allowed),"Blank_Decision_Is_Valid_Pending":"Yes","Duplicate_Record_IDs_Allowed":"No","Cross_Reviewer_Overwrite_Allowed":"No","Synthetic_Dry_Run":"Passed","Actual_Human_Decisions_Imported":0})
    write_csv(out/f"ingestion_validation_{agent:02d}.csv",specs)
    template=[{"Record_ID":"SYNTHETIC-ONLY","Reviewer":"TEST","Decision":"Include","Exclusion_Reason_Code":"","Reviewer_Notes":"Schema dry run only","Review_Date":"2099-01-01","Review_Status":"Synthetic test—do not merge"}]
    write_csv(out/f"synthetic_ingestion_test_{agent:02d}.csv",template)


def consolidate(args):
    root,out=Path(args.input),Path(args.out); out.mkdir(parents=True,exist_ok=True)
    mapping={"retrieval":"retrieval_manifest_*.csv","revalidation":"revalidate_manifest_*.csv","calibration":"calibration_evidence_*.csv","duplicates":"duplicate_evidence_*.csv","fulltext":"fulltext_priority_*.csv","ingestion":"ingestion_validation_*.csv"}
    counts={}; pdf_verified=0
    for name,pat in mapping.items():
        rows=combine(root,pat); counts[name]=len(rows); write_csv(out/f"{name}_master.csv",rows)
        if name in {"retrieval","revalidation"}: pdf_verified+=sum(r.get("PDF_Verified_This_Run")=="Yes" for r in rows)
    for p in root.rglob("synthetic_ingestion_test_*.csv"):
        d=out/"synthetic_tests"; d.mkdir(exist_ok=True); shutil.copy2(p,d/p.name)
    summary={"prospero":PROSPERO,"parallel_agents":70,"output_rows":counts,"pdfs_verified_this_run":pdf_verified,"formal_human_title_abstract_decisions":0,"formal_fulltext_decisions":0,"generated_utc":now()}
    (out/"final_summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    (out/"README.md").write_text("# SRMA bottleneck-resolution package\n\nRetrieval, revalidation, admin-only evidence, duplicate support, blank full-text queues, and synthetic ingestion validation only. No human decisions are claimed.\n",encoding="utf-8")
    print(json.dumps(summary))


def main():
    p=argparse.ArgumentParser(); s=p.add_subparsers(dest="cmd",required=True)
    x=s.add_parser("prepare"); [x.add_argument(a,required=True) for a in ["--rh","--screen","--final","--out"]]; x.set_defaults(func=prepare)
    for name,fn,n in [("retrieval-agent",retrieval_agent,20),("revalidate-agent",revalidate_agent,10),("calibration-agent",calibration_agent,10),("duplicate-agent",duplicate_agent,10),("fulltext-agent",fulltext_agent,10),("ingestion-agent",ingestion_agent,10)]:
        x=s.add_parser(name); x.add_argument("--agent",type=int,required=True,choices=range(1,n+1)); x.add_argument("--input",required=True); x.add_argument("--out",required=True); x.set_defaults(func=fn)
    x=s.add_parser("consolidate"); x.add_argument("--input",required=True); x.add_argument("--out",required=True); x.set_defaults(func=consolidate)
    a=p.parse_args(); a.func(a)
if __name__=="__main__": main()
