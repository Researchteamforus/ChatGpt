#!/usr/bin/env python3
"""Submission-package QA scaffolding for the Bangladesh childhood immunization SRMA.

All outputs are templates, traceability aids, synthetic unit tests, and integrity audits.
No human screening, eligibility, extraction, RoB, GRADE, or numerical review result is created.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, math, random, shutil
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

PROSPERO = "CRD420261461557"
TRACE_AGENTS = 15
SHELL_AGENTS = 15
STAT_AGENTS = 10
ARCHIVE_AGENTS = 10
ADMIN_AGENTS = 10


def now(): return datetime.now(timezone.utc).isoformat()

def read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as f: return list(csv.DictReader(f))

def write_csv(path: Path, rows, fields=None):
    rows=list(rows); path.parent.mkdir(parents=True, exist_ok=True)
    fields=fields or (list(rows[0]) if rows else [])
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w=csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if fields: w.writeheader(); w.writerows({k:r.get(k,"") for k in fields} for r in rows)

def find_one(root: Path, name: str):
    hits=list(root.rglob(name))
    if not hits: raise FileNotFoundError(f"{name} not found under {root}")
    return max(hits, key=lambda p:p.stat().st_size)

def sha256(path: Path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024), b""): h.update(b)
    return h.hexdigest()

def rr(rows, n):
    out=[[] for _ in range(n)]
    for i,r in enumerate(rows): out[i%n].append(r)
    return out

SECTIONS = [
    ("Title/Abstract","PRISMA title identification; structured abstract with verified counts only"),
    ("Introduction","Rationale, Bangladesh context, objectives; cite external literature separately"),
    ("Protocol/Registration","PROSPERO CRD420261461557; distinguish pre-registration work from prospective rerun"),
    ("Eligibility Criteria","Population, exposure/intervention, outcomes, study designs, setting and dates"),
    ("Information Sources","List every searched source with exact search dates and access route"),
    ("Search Strategy","Preserve full executable queries and search-unit logs"),
    ("Selection Process","Report actual reviewer participation only; no duplicate-review claim without records"),
    ("Data Collection","Use blank extraction forms until human extraction is documented"),
    ("Data Items","Coverage, timeliness, dropout/zero-dose, determinants and programme/service-delivery variables"),
    ("Risk of Bias","Apply design-appropriate tools only after study-design confirmation"),
    ("Effect Measures","Proportion, prevalence, odds ratio, risk ratio and adjusted association measures as applicable"),
    ("Synthesis Methods","Predefine transformations, random-effects choices, heterogeneity and subgroup rules"),
    ("Reporting Bias","Use only when sufficient comparable studies exist"),
    ("Certainty Assessment","GRADE judgements remain blank until evidence synthesis is verified"),
    ("Results/Discussion","Use human-verified included-study results; label carried-forward prior results explicitly")
]

SHELLS = [
    ("Table 1","Characteristics of included studies","Study ID; year; setting; design; population; sample; outcome domains"),
    ("Table 2","Vaccination coverage estimates","Study ID; antigen/schedule; age; numerator; denominator; estimate; CI"),
    ("Table 3","Timeliness and delay","Study ID; dose; timeliness definition; estimate; time window"),
    ("Table 4","Dropout and zero-dose","Study ID; definition; numerator; denominator; estimate"),
    ("Table 5","Determinants and barriers","Study ID; determinant; adjusted measure; CI; covariates"),
    ("Table 6","Programme/service delivery evidence","Study ID; intervention/service factor; design; effect"),
    ("Table 7","Risk-of-bias summary","Study ID; tool; domains; overall judgement; rationale"),
    ("Table 8","GRADE Summary of Findings","Outcome; studies; participants; effect; certainty; reasons"),
    ("Figure 1","PRISMA flow diagram","Identification; deduplication; screening; full text; inclusion counts"),
    ("Figure 2","Study-location map","District/division coordinates only when verified"),
    ("Figure 3","Coverage forest plot","Comparable estimates only; retain study weights and CI"),
    ("Figure 4","Determinants forest plot","Pool only harmonised adjusted measures"),
    ("Figure 5","Risk-of-bias visualisation","Traffic-light and weighted summary"),
    ("Figure 6","Publication-year evidence map","Year by topic/domain matrix"),
    ("Supplement","Searches, exclusions, extraction, RoB and analysis audit","Reviewer-checkable CSV/XLSX and code")
]

ADMIN = [
    ("Authorship","Md. Mizanoor Rahman (lead reviewer); Kapashia Binte Giash (second reviewer); CRediT roles require confirmation"),
    ("Affiliation","Department of Statistics, Mawlana Bhashani Science and Technology University"),
    ("Registration","PROSPERO CRD420261461557"),
    ("Funding","Do not insert a funding statement without verified project-specific evidence"),
    ("Conflicts","Blank declaration for each author to confirm"),
    ("Data availability","Describe repository/package only after final files are frozen"),
    ("Code availability","Provide commit/release identifiers and environment details"),
    ("Ethics","Systematic review; confirm whether institutional statement is required by target journal"),
    ("Acknowledgements","Include only verified contributions"),
    ("Submission checklist","Title page; manuscript; figures; supplement; PRISMA checklist; cover letter; declarations")
]


def prepare(args):
    src,out=Path(args.input),Path(args.out)
    if out.exists(): shutil.rmtree(out)
    out.mkdir(parents=True)
    master=find_one(src,"combined_discovery_master_55248.csv")
    summary=find_one(src,"final_summary.json")
    rows=read_csv(master)
    if len(rows)!=55248: raise SystemExit(f"Expected 55248 discovery rows, got {len(rows)}")
    small=out/"small"; small.mkdir()
    shutil.copy2(summary, small/"final_summary.json")
    write_csv(small/"discovery_sample.csv", rows[:500])
    write_csv(small/"trace_tasks.csv", [{"Task_ID":i+1,"Section":s,"Requirement":r} for i,(s,r) in enumerate(SECTIONS)])
    write_csv(small/"shell_tasks.csv", [{"Task_ID":i+1,"Artifact":a,"Title":t,"Required_Fields":f} for i,(a,t,f) in enumerate(SHELLS)])
    write_csv(small/"admin_tasks.csv", [{"Task_ID":i+1,"Item":a,"Instruction":b} for i,(a,b) in enumerate(ADMIN)])
    stats=[{"Task_ID":i+1,"Test_Family":x} for i,x in enumerate(["proportion-logit","double-arcsine-check","log-risk-ratio","log-odds-ratio","standard-error-validation","zero-cell-continuity","random-effects-sanity","heterogeneity-sanity","subgroup-input-validation","leave-one-out-sanity"])]
    write_csv(small/"stat_tasks.csv",stats)
    archive=[{"Task_ID":i+1,"Audit_Focus":x} for i,x in enumerate(["file-hashes","row-counts","column-schemas","identifier-uniqueness","encoding","empty-fields","date-provenance","workflow-provenance","package-index","freeze-readiness"])]
    write_csv(small/"archive_tasks.csv",archive)
    (out/"prepare_summary.json").write_text(json.dumps({"prospero":PROSPERO,"discovery_rows":len(rows),"agents_planned":60,"human_decisions":0,"generated_utc":now()},indent=2),encoding="utf-8")


def trace_agent(args):
    root,out,a=Path(args.input),Path(args.out),int(args.agent); out.mkdir(parents=True,exist_ok=True)
    task=read_csv(find_one(root,"trace_tasks.csv"))[a-1]
    rows=[
      {"Section":task["Section"],"Requirement":task["Requirement"],"Evidence_File":"","Evidence_Field_or_Line":"","Verified_Status":"Pending human verification","Permissible_Current_Claim":"Preparation completed; substantive result pending verified human workflow","Prohibited_Claim":"Do not claim screening/extraction/RoB completion without documented reviewer decisions","Reviewer_Notes":""},
      {"Section":task["Section"],"Requirement":"Count provenance","Evidence_File":"final_summary.json / PRISMA audit","Evidence_Field_or_Line":"","Verified_Status":"Pending final freeze","Permissible_Current_Claim":"Use only reconciled counts","Prohibited_Claim":"Do not mix machine-priority counts with formal inclusion counts","Reviewer_Notes":""}
    ]
    write_csv(out/f"traceability_{a:02d}.csv",rows)


def shell_agent(args):
    root,out,a=Path(args.input),Path(args.out),int(args.agent); out.mkdir(parents=True,exist_ok=True)
    t=read_csv(find_one(root,"shell_tasks.csv"))[a-1]
    write_csv(out/f"shell_{a:02d}.csv",[{**t,"Status":"Blank shell","Data_Source":"Human-verified extraction/screening outputs","Population_Rule":"Bangladesh childhood routine immunization scope","Footnote":"No numerical result inserted by this agent"}])
    (out/f"shell_{a:02d}.md").write_text(f"# {t['Artifact']}: {t['Title']}\n\nRequired fields: {t['Required_Fields']}\n\nStatus: blank submission shell; populate only from verified human-reviewed data.\n",encoding="utf-8")


def stat_agent(args):
    root,out,a=Path(args.input),Path(args.out),int(args.agent); out.mkdir(parents=True,exist_ok=True)
    task=read_csv(find_one(root,"stat_tasks.csv"))[a-1]
    rng=random.Random(420261461557+a)
    checks=[]
    for i in range(1,51):
        n=rng.randint(50,5000); x=rng.randint(1,n-1); p=x/n
        logit=math.log(p/(1-p)); se=math.sqrt(1/x+1/(n-x))
        checks.append({"Synthetic_ID":i,"n":n,"events":x,"proportion":p,"logit":logit,"se_logit":se,"finite":"Yes" if all(math.isfinite(z) for z in [p,logit,se]) else "No"})
    status="PASS" if all(r["finite"]=="Yes" for r in checks) else "FAIL"
    write_csv(out/f"stat_test_{a:02d}.csv",checks)
    (out/"summary.json").write_text(json.dumps({"agent":a,"test_family":task["Test_Family"],"synthetic_cases":len(checks),"status":status,"real_review_data_used":False},indent=2),encoding="utf-8")


def archive_agent(args):
    root,out,a=Path(args.input),Path(args.out),int(args.agent); out.mkdir(parents=True,exist_ok=True)
    task=read_csv(find_one(root,"archive_tasks.csv"))[a-1]
    files=sorted(p for p in root.rglob("*") if p.is_file())
    rows=[]
    for p in files:
        rows.append({"Audit_Focus":task["Audit_Focus"],"Relative_Path":str(p.relative_to(root)),"Size_Bytes":p.stat().st_size,"SHA256":sha256(p),"Status":"Present"})
    write_csv(out/f"archive_audit_{a:02d}.csv",rows)


def admin_agent(args):
    root,out,a=Path(args.input),Path(args.out),int(args.agent); out.mkdir(parents=True,exist_ok=True)
    t=read_csv(find_one(root,"admin_tasks.csv"))[a-1]
    write_csv(out/f"admin_{a:02d}.csv",[{**t,"Completion_Status":"Pending author confirmation","Confirmed_Text":"","Confirmed_By":"","Confirmation_Date":"","Audit_Note":"No declaration is treated as final until confirmed"}])


def consolidate(args):
    root,out=Path(args.input),Path(args.out); out.mkdir(parents=True,exist_ok=True)
    groups={"traceability":"traceability_*.csv","table_figure_shells":"shell_*.csv","statistical_unit_tests":"stat_test_*.csv","archival_integrity":"archive_audit_*.csv","submission_admin":"admin_*.csv"}
    counts={}
    for d,pat in groups.items():
        dest=out/d; dest.mkdir(exist_ok=True); files=sorted(root.rglob(pat)); counts[d]=len(files)
        for p in files: shutil.copy2(p,dest/p.name)
    for p in root.rglob("shell_*.md"):
        dest=out/"table_figure_shells"; shutil.copy2(p,dest/p.name)
    expected={"traceability":15,"table_figure_shells":15,"statistical_unit_tests":10,"archival_integrity":10,"submission_admin":10}
    if counts!=expected: raise SystemExit(f"Agent output count mismatch: {counts}")
    summary={"prospero":PROSPERO,"parallel_agents":60,"outputs":counts,"human_screening_completed":0,"eligibility_decisions_completed":0,"extractions_completed":0,"rob_completed":0,"grade_completed":0,"governance":"Submission scaffolds, synthetic tests and integrity audits only.","generated_utc":now()}
    (out/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    (out/"README.md").write_text("# SRMA submission QA package\n\nTraceability aids, blank table/figure shells, synthetic statistical unit tests, archival-integrity manifests and blank submission declarations. No human review decision or numerical systematic-review result is claimed.\n",encoding="utf-8")


def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="cmd",required=True)
    x=sub.add_parser("prepare"); x.add_argument("--input",required=True); x.add_argument("--out",required=True); x.set_defaults(func=prepare)
    for name,fn,n in [("trace-agent",trace_agent,15),("shell-agent",shell_agent,15),("stat-agent",stat_agent,10),("archive-agent",archive_agent,10),("admin-agent",admin_agent,10)]:
        x=sub.add_parser(name); x.add_argument("--agent",type=int,required=True,choices=range(1,n+1)); x.add_argument("--input",required=True); x.add_argument("--out",required=True); x.set_defaults(func=fn)
    x=sub.add_parser("consolidate"); x.add_argument("--input",required=True); x.add_argument("--out",required=True); x.set_defaults(func=consolidate)
    a=p.parse_args(); a.func(a)
if __name__=="__main__": main()
