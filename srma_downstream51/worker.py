#!/usr/bin/env python3
"""Machine-assisted downstream preparation for CRD420261461557.

No output is a formal human screening, eligibility, extraction, or RoB decision.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, re, shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

PROSPERO="CRD420261461557"
EMAIL="st19009@mbstu.ac.bd"

def now(): return datetime.now(timezone.utc).isoformat()
def nt(v): return re.sub(r"\s+"," ",str(v or "")).strip()
def nd(v):
    x=nt(v).lower(); x=re.sub(r"^https?://(?:dx\.)?doi\.org/","",x); x=re.sub(r"^doi:\s*","",x)
    return x.rstrip(".,; )]")
def read(p):
    with Path(p).open(encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))
def write(p,rows,fields=None):
    rows=list(rows); p=Path(p); p.parent.mkdir(parents=True,exist_ok=True); fields=fields or (list(rows[0]) if rows else [])
    with p.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore")
        if fields:w.writeheader(); w.writerows({k:r.get(k,"") for k in fields} for r in rows)
def find(root,name):
    hits=list(Path(root).rglob(name))
    if not hits: raise FileNotFoundError(name)
    return max(hits,key=lambda p:p.stat().st_size)
def assigned(rows,agent,n): return [r for i,r in enumerate(rows) if i%n==agent-1]

DOI_RX=re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+",re.I)
BD=re.compile(r"\bbangladesh(?:i)?\b|\bdhaka\b|chattogram|chittagong|sylhet|khulna|rajshahi|rangpur|mymensingh|barishal|rohingya",re.I)
CHILD=re.compile(r"child(?:ren|hood)?|infant|under[- ]?five|newborn|toddler|caregiver",re.I)
VAX=re.compile(r"immuni[sz]|vaccin|\bEPI\b|expanded program",re.I)
OUTCOME=re.compile(r"coverage|uptake|timeliness|delay|dropout|zero[- ]dose|incomplete|under[- ]immun|barrier|determinant|inequalit|access|service delivery|missed opportunit|defaulter",re.I)
DESIGNS=[("Trial",re.compile(r"randomi[sz]ed|controlled trial|cluster trial",re.I)),("Cohort",re.compile(r"cohort|longitudinal|follow[- ]?up",re.I)),("Cross-sectional",re.compile(r"cross[- ]sectional|household survey|demographic and health survey|multiple indicator cluster survey",re.I)),("Qualitative",re.compile(r"qualitative|focus group|in-depth interview|thematic",re.I)),("Programme/facility evaluation",re.compile(r"programme evaluation|program evaluation|facility assessment|service readiness",re.I))]

def fulltext(args):
    from pypdf import PdfReader
    links=read(find(args.next,"pdf_record_linkage_qc.csv")); byhash=defaultdict(list)
    for r in links: byhash[nt(r.get("Actual_SHA256")) or nt(r.get("PDF_File"))].append(r)
    keys=sorted(byhash); chosen=set(k for i,k in enumerate(keys) if i%10==args.agent-1)
    pdfs={p.name:p for p in Path(args.pdf).rglob("*.pdf")}; out=[]; cache={}
    for k in chosen:
        for r in byhash[k]:
            name=Path(nt(r.get("PDF_File"))).name; p=pdfs.get(name); info={"Error":"PDF not found"}
            if name in cache: info=cache[name]
            elif p:
                try:
                    rd=PdfReader(str(p)); chunks=[]
                    for pg in rd.pages:
                        if sum(map(len,chunks))>=160000: break
                        try: chunks.append(pg.extract_text() or "")
                        except Exception: pass
                    text="\n".join(chunks); dois=list(dict.fromkeys(nd(x) for x in DOI_RX.findall(text[:120000])))
                    design="Unclear"
                    for label,pat in DESIGNS:
                        if pat.search(text): design=label; break
                    flags={"Bangladesh_Flag":"Yes" if BD.search(text) else "No","Child_Flag":"Yes" if CHILD.search(text) else "No","Vaccination_Flag":"Yes" if VAX.search(text) else "No","Outcome_Flag":"Yes" if OUTCOME.search(text) else "No"}
                    strong=all(v=="Yes" for v in flags.values())
                    info={"Detected_Pages":len(rd.pages),"Extracted_Text_Length":len(text),"Detected_DOIs":"; ".join(dois[:10]),"Suggested_Design":design,**flags,"Machine_Evidence_Status":"Strong protocol signal" if strong else ("Needs human verification" if flags["Bangladesh_Flag"]=="Yes" and flags["Vaccination_Flag"]=="Yes" else "Weak/uncertain signal"),"Evidence_Snippet":nt(text[:1200]),"Error":""}
                except Exception as e: info={"Error":f"{type(e).__name__}: {str(e)[:250]}"}
                cache[name]=info
            x=dict(r); x.update(info); x.update({"Human_Fulltext_Decision":"","Human_Exclusion_Reason":"","Human_Reviewer":"","Human_Notes":""}); out.append(x)
    root=Path(args.out); write(root/f"fulltext_{args.agent:02d}.csv",out); (root/f"summary_{args.agent:02d}.json").write_text(json.dumps({"agent":args.agent,"rows":len(out),"unique_pdfs":len(chosen),"human_decisions":0},indent=2),encoding="utf-8")

def retrieval(args):
    import requests
    rows=assigned(read(find(args.next,"unresolved_manual_retrieval_queue.csv")),args.agent,20); s=requests.Session(); s.headers.update({"User-Agent":f"SRMA/{PROSPERO} ({EMAIL})"})
    root=Path(args.out); (root/"pdfs").mkdir(parents=True,exist_ok=True); out=[]
    for r in rows:
        doi=nd(r.get("DOI")); pmid=re.sub(r"\D","",nt(r.get("PMID"))); candidates=[]; errors=[]; pmcid=""
        try:
            q=f"DOI:{doi}" if doi else (f"EXT_ID:{pmid}" if pmid else "")
            if q:
                data=s.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",params={"query":q,"format":"json","pageSize":3},timeout=30).json()
                for h in (data.get("resultList") or {}).get("result",[]):
                    if h.get("pmcid"): pmcid=h["pmcid"]; candidates.append(f"https://europepmc.org/articles/{pmcid}?pdf=render")
        except Exception as e: errors.append("EuropePMC:"+type(e).__name__)
        if doi:
            try:
                d=s.get(f"https://api.unpaywall.org/v2/{quote(doi,safe='')}",params={"email":EMAIL},timeout=30).json()
                for loc in [d.get("best_oa_location")]+list(d.get("oa_locations") or []):
                    if isinstance(loc,dict) and (loc.get("url_for_pdf") or loc.get("url")): candidates.append(loc.get("url_for_pdf") or loc.get("url"))
            except Exception as e: errors.append("Unpaywall:"+type(e).__name__)
            try:
                d=s.get(f"https://api.openalex.org/works/https://doi.org/{quote(doi,safe='/:')}",params={"mailto":EMAIL},timeout=30).json()
                for loc in [d.get("best_oa_location"),d.get("primary_location")]:
                    if isinstance(loc,dict) and (loc.get("pdf_url") or loc.get("landing_page_url")): candidates.append(loc.get("pdf_url") or loc.get("landing_page_url"))
            except Exception as e: errors.append("OpenAlex:"+type(e).__name__)
        prior=nt(r.get("Candidate_Open_PDF_URL")); candidates=([prior] if prior else [])+candidates; candidates=list(dict.fromkeys(x for x in candidates if x))
        saved=""; source=""; codes=[]
        for url in candidates[:8]:
            try:
                resp=s.get(url,timeout=45,allow_redirects=True,stream=True,headers={"Accept":"application/pdf,*/*"}); codes.append(str(resp.status_code)); data=resp.raw.read(25000001)
                if resp.status_code==200 and len(data)<=25000000 and (data.startswith(b"%PDF") or "application/pdf" in (resp.headers.get("Content-Type") or "").lower()):
                    fn=(nt(r.get("Integrated_ID")) or hashlib.sha256(url.encode()).hexdigest()[:16]).replace("::","_").replace("/","_")+".pdf"; (root/"pdfs"/fn).write_bytes(data); saved=f"pdfs/{fn}"; source=url; break
            except Exception as e: errors.append("GET:"+type(e).__name__)
        x=dict(r); x.update({"Retry_Candidate_URLs":"; ".join(candidates[:8]),"Discovered_PMCID_Retry":pmcid,"Retry_HTTP_Statuses":"; ".join(codes),"New_PDF_Saved":"Yes" if saved else "No","New_PDF_File":saved,"Successful_Source_URL":source,"Retry_Errors":"; ".join(errors),"Human_Verification":"Not reviewed"}); out.append(x)
    write(root/f"retrieval_{args.agent:02d}.csv",out); (root/f"summary_{args.agent:02d}.json").write_text(json.dumps({"agent":args.agent,"rows":len(out),"new_pdfs":sum(x["New_PDF_Saved"]=="Yes" for x in out),"human_verifications":0},indent=2),encoding="utf-8")

def exclusion(args):
    rows=[r for r in read(find(args.screen,"screening_enrichment_master_1129.csv")) if r.get("Machine_Triage")=="Likely exclude"]; rows=assigned(rows,args.agent,15); out=[]
    for r in rows:
        blob=f"{nt(r.get('Enriched_Title') or r.get('Title'))}. {nt(r.get('Enriched_Abstract') or r.get('Abstract'))}"; missing=len(nt(r.get('Enriched_Abstract') or r.get('Abstract')))<40
        if BD.search(blob) and VAX.search(blob) and (CHILD.search(blob) or OUTCOME.search(blob)): status,reason="High-priority human re-check","Liberal rules identify Bangladesh childhood vaccination relevance"
        elif missing: status,reason="Human re-check because abstract missing","Title-only exclusion is unsafe"
        elif BD.search(blob) and VAX.search(blob): status,reason="Human re-check","Bangladesh vaccination signal remains"
        else: status,reason="Lower-priority sensitivity sample","No liberal protocol signal detected"
        x=dict(r); x.update({"Sensitivity_Audit_Status":status,"Sensitivity_Audit_Reason":reason,"Human_Recheck_Decision":"","Human_Reviewer":"","Human_Notes":""}); out.append(x)
    root=Path(args.out); write(root/f"exclusion_{args.agent:02d}.csv",out); (root/f"summary_{args.agent:02d}.json").write_text(json.dumps({"agent":args.agent,"rows":len(out),"counts":dict(Counter(x["Sensitivity_Audit_Status"] for x in out)),"human_decisions":0},indent=2),encoding="utf-8")

def consolidate(args):
    root,out=Path(args.input),Path(args.out); out.mkdir(parents=True,exist_ok=True); summary={"prospero":PROSPERO,"parallel_agents":45,"formal_human_screening_completed":0,"fulltext_decisions_completed":0,"extractions_completed":0,"rob_completed":0,"generated_utc":now()}
    combined={}
    for name,pat in [("fulltext_machine_evidence.csv","fulltext_*.csv"),("retrieval_retry_master.csv","retrieval_*.csv"),("likely_exclude_sensitivity.csv","exclusion_*.csv")]:
        rows=[]
        for p in root.rglob(pat): rows.extend(read(p))
        write(out/name,rows); combined[name]=len(rows)
    ft=read(out/"fulltext_machine_evidence.csv"); screen=[]; extract=[]
    for r in ft:
        base={k:r.get(k,"") for k in ["Integrated_ID","Expected_Title","Expected_DOI","Expected_PMID","PDF_File","Actual_SHA256","Detected_Pages","Machine_Evidence_Status","Bangladesh_Flag","Child_Flag","Vaccination_Flag","Outcome_Flag","Suggested_Design","Evidence_Snippet"]}
        screen.append({**base,"Reviewer":"","Full_Text_Decision":"","Primary_Exclusion_Reason":"","Decision_Date":"","Notes":""})
        extract.append({**base,"Study_ID":"","Citation":"","Study_Design_Confirmed":"","Setting":"","Study_Period":"","Population":"","Sample_Size":"","Age_Group":"","Vaccination_Definition":"","Outcome_Type":"","Numerator":"","Denominator":"","Effect_Estimate":"","Confidence_Interval":"","Adjusted_Covariates":"","Determinants_or_Barriers":"","RoB_Tool":"","RoB_Domain_1":"","RoB_Domain_2":"","RoB_Domain_3":"","Overall_RoB":"","Extractor":"","Verifier":"","Notes":""})
    write(out/"blank_fulltext_screening_form.csv",screen); write(out/"blank_extraction_rob_form.csv",extract); summary["rows"]=combined; summary["new_pdfs_saved"]=sum(r.get("New_PDF_Saved")=="Yes" for r in read(out/"retrieval_retry_master.csv")); (out/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8"); (out/"README.md").write_text("Machine-assisted evidence and blank human-review forms only; no eligibility, extraction, or RoB decisions are claimed.\n",encoding="utf-8")

def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="cmd",required=True)
    x=sub.add_parser("fulltext"); x.add_argument("--agent",type=int,choices=range(1,11),required=True); x.add_argument("--next",required=True); x.add_argument("--pdf",required=True); x.add_argument("--out",required=True); x.set_defaults(func=fulltext)
    x=sub.add_parser("retrieval"); x.add_argument("--agent",type=int,choices=range(1,21),required=True); x.add_argument("--next",required=True); x.add_argument("--out",required=True); x.set_defaults(func=retrieval)
    x=sub.add_parser("exclusion"); x.add_argument("--agent",type=int,choices=range(1,16),required=True); x.add_argument("--screen",required=True); x.add_argument("--out",required=True); x.set_defaults(func=exclusion)
    x=sub.add_parser("consolidate"); x.add_argument("--input",required=True); x.add_argument("--out",required=True); x.set_defaults(func=consolidate)
    a=p.parse_args(); a.func(a)
if __name__=="__main__": main()
