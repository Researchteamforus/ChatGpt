#!/usr/bin/env python3
"""SRMA manuscript-readiness preparation.

Creates reporting, extraction, RoB, synthesis, GRADE and reproducibility
materials only. It does not create human screening, eligibility, extraction,
RoB, GRADE judgements, or numerical review results.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, re, shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROSPERO="CRD420261461557"
COUNTS={"reporting":20,"codebook":20,"rob":20,"synthesis":20,"grade":10,"repro":10}
TOPICS=["coverage","timeliness","dropout/zero-dose","determinants/inequality","programme/service delivery"]
DESIGNS=["cross-sectional","cohort","case-control","randomized/quasi-experimental","ecological/mixed-methods"]

def now(): return datetime.now(timezone.utc).isoformat()
def read_csv(p):
    with p.open(encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))
def write_csv(p,rows,fields=None):
    rows=list(rows); p.parent.mkdir(parents=True,exist_ok=True); fields=fields or (list(rows[0]) if rows else [])
    with p.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore")
        if fields:w.writeheader(); w.writerows({k:r.get(k,"") for k in fields} for r in rows)
def sha(p):
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""):h.update(b)
    return h.hexdigest()
def find_files(root): return sorted([p for p in root.rglob("*") if p.is_file()])
def rr(items,n):
    out=[[] for _ in range(n)]
    for i,x in enumerate(items):out[i%n].append(x)
    return out

def prepare(args):
    roots=[Path(x) for x in args.inputs]; out=Path(args.out)
    if out.exists():shutil.rmtree(out)
    out.mkdir(parents=True)
    inv=[]; csv_schemas=[]
    for root in roots:
        for p in find_files(root):
            rel=f"{root.name}/{p.relative_to(root)}"
            inv.append({"Path":rel,"Size":p.stat().st_size,"SHA256":sha(p),"Suffix":p.suffix.lower()})
            if p.suffix.lower()==".csv":
                try:
                    with p.open(encoding="utf-8-sig",newline="") as f:
                        r=csv.reader(f); header=next(r,[])
                    for c in header:csv_schemas.append({"Source_File":rel,"Field":c})
                except Exception:pass
    write_csv(out/"inventory.csv",inv)
    write_csv(out/"schema_fields.csv",csv_schemas)
    for kind,n in COUNTS.items():
        task_rows=[{"Agent":i,"Workstream":kind,"Prospero":PROSPERO} for i in range(1,n+1)]
        write_csv(out/f"tasks/{kind}.csv",task_rows)
    summary={"prospero":PROSPERO,"input_files":len(inv),"schema_fields":len(csv_schemas),"agents_planned":sum(COUNTS.values()),"generated_utc":now(),"governance":"Preparation only; no human decisions or review results."}
    (out/"prepare_summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps(summary))

def reporting(args):
    a=int(args.agent); out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    items=list(range(1,28)); assigned=items[a-1::COUNTS["reporting"]]
    rows=[]
    for item in assigned:
        rows.append({"PRISMA_Item":item,"Required_Content":"Populate from verified protocol/search/screening/extraction records","Evidence_Status":"Automated evidence mapping prepared; human verification required","Permitted_Claim":"Only documented activity may be reported","Evidence_Source":"PROSPERO, search logs, workflow audit, reviewer forms","Final_Text":""})
    write_csv(out/f"prisma_reporting_{a:02d}.csv",rows)
    (out/f"methods_scaffold_{a:02d}.md").write_text(f"# Methods/reporting scaffold agent {a}\n\nPROSPERO: {PROSPERO}\n\nAssigned PRISMA items: {', '.join(map(str,assigned))}.\n\nDo not claim completed human screening, extraction, RoB, or post-registration activity unless documented.\n",encoding="utf-8")

def codebook(args):
    a=int(args.agent); root=Path(args.input); out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    rows=read_csv(root/"schema_fields.csv")[a-1::COUNTS["codebook"]]
    outrows=[]
    for r in rows:
        f=r["Field"]; low=f.lower()
        dtype="date" if "date" in low else "integer/number" if any(x in low for x in ["count","year","score","size","pages"]) else "categorical/text"
        role="Human decision field" if any(x in low for x in ["decision","reviewer","adjudication","notes"]) else "Identifier" if any(x in low for x in ["id","doi","pmid","pmcid"]) else "Study/retrieval metadata"
        outrows.append({**r,"Suggested_Type":dtype,"Functional_Role":role,"Missing_Value_Convention":"Blank/NA; never infer human decisions","Definition_To_Verify":""})
    write_csv(out/f"codebook_{a:02d}.csv",outrows)

def rob(args):
    a=int(args.agent); out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    design=DESIGNS[(a-1)%len(DESIGNS)]
    tool={"cross-sectional":"JBI Analytical Cross Sectional","cohort":"ROBINS-I/JBI Cohort as appropriate","case-control":"JBI Case Control","randomized/quasi-experimental":"RoB 2 or ROBINS-I","ecological/mixed-methods":"Design-appropriate JBI/MMAT"}[design]
    domains=["selection","exposure/intervention measurement","outcome measurement","confounding","missing data","selective reporting","overall judgement"]
    rows=[{"Design":design,"Suggested_Tool":tool,"Domain":d,"Signalling_Question":"","Reviewer1_Judgement":"","Reviewer2_Judgement":"","Consensus":"","Support_For_Judgement":""} for d in domains]
    write_csv(out/f"rob_template_{a:02d}.csv",rows)
    (out/f"rob_guidance_{a:02d}.md").write_text(f"# RoB mapping: {design}\n\nSuggested starting tool: {tool}. Final tool selection requires verified study design. No RoB judgement has been made.\n",encoding="utf-8")

def synthesis(args):
    a=int(args.agent); out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    topic=TOPICS[(a-1)%len(TOPICS)]; mode=["proportion","odds/risk ratio","mean/time difference","intervention effect"][(a-1)%4]
    spec={"prospero":PROSPERO,"topic":topic,"effect_family":mode,"eligibility_dependency":"Use only human-included studies","minimum_comparability":"Comparable population, outcome definition, design and time point","heterogeneity":"Random-effects if pooling is defensible; report tau2 and I2","sensitivity":["leave-one-out","risk-of-bias restriction","definition/time-window restriction"],"results":None}
    (out/f"synthesis_spec_{a:02d}.json").write_text(json.dumps(spec,indent=2),encoding="utf-8")
    code=f'''# Analysis skeleton only; no data/results\nlibrary(metafor)\n# topic: {topic}; effect family: {mode}\n# dat <- read.csv("human_verified_extraction.csv")\n# stopifnot(all(dat$Final_Inclusion == "Include"))\n# Fit model only after outcome harmonisation and reviewer verification.\n'''
    (out/f"analysis_skeleton_{a:02d}.R").write_text(code,encoding="utf-8")

def grade(args):
    a=int(args.agent); out=Path(args.out); out.mkdir(parents=True,exist_ok=True); topic=TOPICS[(a-1)%len(TOPICS)]
    rows=[]
    for domain in ["risk of bias","inconsistency","indirectness","imprecision","publication bias","large effect","dose-response","residual confounding","overall certainty"]:
        rows.append({"Outcome_Group":topic,"GRADE_Domain":domain,"Judgement":"","Rationale":"","Evidence_Source":"","Reviewer1":"","Reviewer2":"","Consensus":""})
    write_csv(out/f"grade_{a:02d}.csv",rows)

def repro(args):
    a=int(args.agent); root=Path(args.input); out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    inv=read_csv(root/"inventory.csv")[a-1::COUNTS["repro"]]
    rows=[]
    for r in inv:
        rows.append({**r,"Audit_Check":"SHA-256 recorded","Provenance_Status":"Present","Reproducibility_Note":"Retain source artifact ID/run ID and generation timestamp"})
    write_csv(out/f"reproducibility_{a:02d}.csv",rows)

def consolidate(args):
    root=Path(args.input); out=Path(args.out)
    if out.exists():shutil.rmtree(out)
    out.mkdir(parents=True)
    counts=Counter()
    for p in find_files(root):
        if p.name=="prepare_summary.json":continue
        category=p.parent.name
        dest=out/category/p.name; dest.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(p,dest); counts[category]+=1
    summary={"prospero":PROSPERO,"parallel_agents":sum(COUNTS.values()),"output_files_by_category":dict(counts),"human_screening_completed":0,"human_extraction_completed":0,"rob_completed":0,"grade_completed":0,"results_synthesis_completed":0,"generated_utc":now(),"governance":"Scaffolds/templates/audits only."}
    (out/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    (out/"README.md").write_text("# SRMA manuscript-readiness package\n\nReporting scaffolds, extraction codebook, blank RoB/GRADE forms, synthesis specifications and reproducibility audits. No human decisions or numerical review results are claimed.\n",encoding="utf-8")
    print(json.dumps(summary))

def main():
    p=argparse.ArgumentParser(); s=p.add_subparsers(dest="cmd",required=True)
    x=s.add_parser("prepare");x.add_argument("--inputs",nargs="+",required=True);x.add_argument("--out",required=True);x.set_defaults(fn=prepare)
    for name,fn,n in [("reporting",reporting,20),("codebook",codebook,20),("rob",rob,20),("synthesis",synthesis,20),("grade",grade,10),("repro",repro,10)]:
        x=s.add_parser(name);x.add_argument("--agent",type=int,choices=range(1,n+1),required=True);x.add_argument("--input",required=False,default="prepared");x.add_argument("--out",required=True);x.set_defaults(fn=fn)
    x=s.add_parser("consolidate");x.add_argument("--input",required=True);x.add_argument("--out",required=True);x.set_defaults(fn=consolidate)
    a=p.parse_args();a.fn(a)
if __name__=="__main__":main()
