#!/usr/bin/env python3
"""Phased reviewer-handoff and evidence-support pipeline.

Operational preparation only. It never fills or infers human screening,
adjudication, eligibility, extraction, risk-of-bias, or GRADE decisions.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, shutil
from pathlib import Path
from typing import Iterable

REVIEWERS = ("Mizan", "Kapashia")
EMPTY_SENTINELS = {"", "not reviewed", "not started", "pending", "to be completed", "n/a", "na"}
PROTECTED = {
    "decision", "human_screening_decision", "human_title_abstract_decision",
    "human_full_text_decision", "full_text_decision", "human_adjudication",
    "formal_fulltext_decision", "human_verification", "adjudication_decision",
    "reviewer_decision", "primary_exclusion_reason", "exclusion_reason_code",
}

def read_csv(p: Path) -> list[dict[str,str]]:
    with p.open('r', encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

def write_csv(p: Path, rows: Iterable[dict], fields: list[str] | None = None) -> None:
    rows=list(rows); p.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields=[]; seen=set()
        for r in rows:
            for k in r:
                if k not in seen: fields.append(k); seen.add(k)
    with p.open('w', encoding='utf-8-sig', newline='') as f:
        w=csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for r in rows: w.writerow({k:r.get(k,'') for k in fields})

def sha256(p: Path) -> str:
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024), b''): h.update(b)
    return h.hexdigest()

def locate(root: Path, name: str) -> Path:
    hits=list(root.rglob(name))
    if not hits: raise SystemExit(f"Missing required input: {name}")
    return hits[0]

def nonblank_protected(rows: list[dict[str,str]]) -> int:
    n=0
    for r in rows:
        for k,v in r.items():
            if k.strip().lower() in PROTECTED and str(v or '').strip().lower() not in EMPTY_SENTINELS: n+=1
    return n

def split_even(rows: list[dict], n: int) -> list[list[dict]]:
    return [rows[i::n] for i in range(n)]

def choose_id(r: dict[str,str], idx:int=0) -> str:
    for k in ('Record_ID','record_id','Supplementary_Record_ID','seed_record_id','Candidate_Key','DOI','candidate_doi','Title','candidate_title'):
        if str(r.get(k,'')).strip(): return str(r[k]).strip()
    return f"ROW-{idx+1:06d}"

def batch_num(p: Path) -> int:
    import re
    m=re.search(r'_(\d{2})_', p.name)
    return int(m.group(1)) if m else 999

def cmd_prepare(a):
    src,out=Path(a.input),Path(a.out); out.mkdir(parents=True,exist_ok=True)
    cal_root=locate(src,'final_summary.json').parent
    for p in src.rglob('final_summary.json'):
        try:
            d=json.loads(p.read_text(encoding='utf-8'))
            if d.get('pipeline')=='SRMA 100-Agent Calibration and Screening Launch': cal_root=p.parent; break
        except Exception: pass
    cal_m=sorted(cal_root.rglob('CAL_*_Mizan.csv'), key=batch_num)
    cal_k=sorted(cal_root.rglob('CAL_*_Kapashia.csv'), key=batch_num)
    main_m=sorted(cal_root.rglob('MAIN_POSTCAL_*_Mizan.csv'), key=batch_num)
    main_k=sorted(cal_root.rglob('MAIN_POSTCAL_*_Kapashia.csv'), key=batch_num)
    sup_m=sorted(cal_root.rglob('SUP_POSTCAL_*_Mizan.csv'), key=batch_num)
    sup_k=sorted(cal_root.rglob('SUP_POSTCAL_*_Kapashia.csv'), key=batch_num)
    if not all(len(x)==10 for x in (cal_m,cal_k,main_m,main_k,sup_m,sup_k)):
        raise SystemExit(f"Unexpected batch counts: {[len(x) for x in (cal_m,cal_k,main_m,main_k,sup_m,sup_k)]}")
    manifest=[]
    for stage,pairs in [('calibration',(cal_m,cal_k)),('main',(main_m,main_k)),('supplementary',(sup_m,sup_k))]:
        for reviewer,files in zip(REVIEWERS,pairs):
            for i,p in enumerate(files,1):
                rows=read_csv(p)
                if nonblank_protected(rows): raise SystemExit(f"Protected decisions already populated: {p}")
                manifest.append({'stage':stage,'reviewer':reviewer,'batch':f'{i:02d}','file':str(p.relative_to(src)),'rows':len(rows),'sha256':sha256(p)})
    def total(stage,reviewer): return sum(int(r['rows']) for r in manifest if r['stage']==stage and r['reviewer']==reviewer)
    expected={('calibration','Mizan'):1000,('calibration','Kapashia'):1000,('main','Mizan'):7433,('main','Kapashia'):7433,('supplementary','Mizan'):3732,('supplementary','Kapashia'):3732}
    actual={(s,r):total(s,r) for s,r in expected}
    if actual!=expected: raise SystemExit(f"Reviewer pool count mismatch: {actual}")
    cal_id_rows=[]
    for i,(pm,pk) in enumerate(zip(cal_m,cal_k),1):
        rm,rk=read_csv(pm),read_csv(pk)
        im=[choose_id(x,j) for j,x in enumerate(rm)]; ik=[choose_id(x,j) for j,x in enumerate(rk)]
        if im!=ik: raise SystemExit(f"Calibration batch {i} reviewer IDs differ")
        for order,rid in enumerate(im,1): cal_id_rows.append({'batch':f'{i:02d}','order':order,'record_id':rid})
    full=read_csv(locate(src,'fulltext_master.csv')); dups=read_csv(locate(src,'duplicates_master.csv')); routes=read_csv(locate(src,'supplementary_route_validation_master.csv'))
    if (len(full),len(dups),len(routes))!=(768,806,3732): raise SystemExit(f"Evidence seed mismatch: {len(full)}, {len(dups)}, {len(routes)}")
    if nonblank_protected(full)+nonblank_protected(dups)+nonblank_protected(routes):
        raise SystemExit('Protected human decisions found in evidence seeds')
    write_csv(out/'batch_manifest.csv',manifest)
    write_csv(out/'calibration_id_master.csv',cal_id_rows)
    write_csv(out/'fulltext_seed.csv',full); write_csv(out/'duplicate_seed.csv',dups); write_csv(out/'supplementary_route_seed.csv',routes)
    for stage,fileset in [('calibration',cal_m+cal_k),('main',main_m+main_k),('supplementary',sup_m+sup_k)]:
        for p in fileset:
            dest=out/'reviewer_files'/stage/p.name
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(p,dest)
    tasks=[]
    for i in range(1,31):
        stream='calibration_handoff' if i<=10 else 'calibration_integrity' if i<=20 else 'synthetic_return_validation'
        tasks.append({'stage':1,'agent':i,'stream':stream})
    for i in range(1,31):
        stream='fulltext_evidence' if i<=10 else 'duplicate_evidence' if i<=20 else 'supplementary_route_evidence'
        tasks.append({'stage':2,'agent':i,'stream':stream})
    for i in range(1,31):
        stream='main_intake_control' if i<=10 else 'supplementary_intake_control' if i<=20 else 'prisma_progress_control'
        tasks.append({'stage':3,'agent':i,'stream':stream})
    write_csv(out/'tasks.csv',tasks)
    summary={'status':'PREPARED','calibration_records':1000,'main_remaining_records':7433,'supplementary_records':3732,'fulltext_seed':768,'duplicate_seed':806,'supplementary_verified_pdfs':sum(str(r.get('PDF_Verified','')).lower()=='yes' for r in routes),'human_decisions':0}
    (out/'prepare_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')

def task(prepared:Path,stage:int,agent:int)->str:
    return next(r['stream'] for r in read_csv(prepared/'tasks.csv') if int(r['stage'])==stage and int(r['agent'])==agent)

def cmd_stage1(a):
    p,o,agent=Path(a.prepared),Path(a.out),int(a.agent); o.mkdir(parents=True,exist_ok=True); stream=task(p,1,agent)
    b=f'{((agent-1)%10)+1:02d}'
    if stream=='calibration_handoff':
        rows=[]
        for rev in REVIEWERS:
            src=p/'reviewer_files'/'calibration'/f'CAL_{b}_{rev}.csv'
            data=read_csv(src)
            if nonblank_protected(data): raise SystemExit('Calibration decisions not blank')
            shutil.copy2(src,o/src.name)
            rows.append({'batch':b,'reviewer':rev,'rows':len(data),'sha256':sha256(src),'handoff_status':'READY_FOR_INDEPENDENT_HUMAN_REVIEW','returned_file':'','human_completion_confirmed':''})
        write_csv(o/f'CAL_{b}_handoff_manifest.csv',rows)
        (o/f'CAL_{b}_instructions.md').write_text('Complete independently. Allowed Decision values: Include, Exclude, Unclear. Do not view the other reviewer file.\n',encoding='utf-8')
    elif stream=='calibration_integrity':
        batch=f'{agent-10:02d}'; rows=[]
        pm=p/'reviewer_files'/'calibration'/f'CAL_{batch}_Mizan.csv'; pk=p/'reviewer_files'/'calibration'/f'CAL_{batch}_Kapashia.csv'
        rm,rk=read_csv(pm),read_csv(pk); ids_m=[choose_id(x,i) for i,x in enumerate(rm)]; ids_k=[choose_id(x,i) for i,x in enumerate(rk)]
        rows.append({'batch':batch,'mizan_rows':len(rm),'kapashia_rows':len(rk),'same_record_order':'PASS' if ids_m==ids_k else 'FAIL','mizan_blank':'PASS' if nonblank_protected(rm)==0 else 'FAIL','kapashia_blank':'PASS' if nonblank_protected(rk)==0 else 'FAIL'})
        write_csv(o/f'CAL_{batch}_integrity.csv',rows)
    else:
        batch=f'{agent-20:02d}'
        cases=[
            {'case':'valid_complete','decision':'Include','reason':'','expected':'ACCEPT'},
            {'case':'valid_exclude','decision':'Exclude','reason':'E01','expected':'ACCEPT'},
            {'case':'missing_exclusion_reason','decision':'Exclude','reason':'','expected':'REJECT'},
            {'case':'invalid_decision','decision':'Maybe','reason':'','expected':'REJECT'},
        ]
        write_csv(o/f'CAL_{batch}_synthetic_return_cases.csv',cases)
        spec={'batch':batch,'allowed_decisions':['Include','Exclude','Unclear'],'exclude_requires_reason':True,'real_human_data_used':False}
        (o/f'CAL_{batch}_validator_spec.json').write_text(json.dumps(spec,indent=2),encoding='utf-8')

def cmd_gate1(a):
    inp,out=Path(a.input),Path(a.out); out.mkdir(parents=True,exist_ok=True)
    hands=list(inp.rglob('CAL_*_handoff_manifest.csv')); ints=list(inp.rglob('CAL_*_integrity.csv')); specs=list(inp.rglob('CAL_*_validator_spec.json'))
    if (len(hands),len(ints),len(specs))!=(10,10,10): raise SystemExit(f'Gate1 missing outputs {len(hands)}, {len(ints)}, {len(specs)}')
    allrows=[]
    for x in hands+ints: allrows+=read_csv(x)
    if any(str(v).upper()=='FAIL' for r in allrows for v in r.values()): raise SystemExit('Gate1 integrity failure')
    if nonblank_protected(allrows): raise SystemExit('Gate1 protected field populated')
    write_csv(out/'gate1_calibration_handoff_audit.csv',allrows)
    (out/'gate1.json').write_text(json.dumps({'status':'PASS','handoff_batches':10,'human_decisions':0},indent=2),encoding='utf-8')

def cmd_stage2(a):
    p,o,agent=Path(a.prepared),Path(a.out),int(a.agent); o.mkdir(parents=True,exist_ok=True); stream=task(p,2,agent)
    if stream=='fulltext_evidence':
        rows=split_even(read_csv(p/'fulltext_seed.csv'),10)[agent-1]; out=[]
        for i,r in enumerate(rows):
            out.append({'record_id':choose_id(r,i),'title':r.get('Title',''),'doi':r.get('DOI',''),'pdf_retrieved':r.get('PDF_Retrieved',''),'machine_evidence_status':r.get('Machine_Evidence_Status',''),'operational_priority':r.get('Operational_Priority',''),'required_next_action':r.get('Required_Next_Action',''),'human_full_text_decision':''})
        write_csv(o/f'fulltext_evidence_{agent:02d}.csv',out)
    elif stream=='duplicate_evidence':
        idx=agent-11; rows=split_even(read_csv(p/'duplicate_seed.csv'),10)[idx]; out=[]
        for i,r in enumerate(rows): out.append({'candidate_key':r.get('Candidate_Key',''),'record_id':r.get('Record_ID',''),'title':r.get('Title',''),'doi':r.get('DOI',''),'group_size':r.get('Group_Size',''),'title_similarity_max':r.get('Title_Similarity_Max',''),'machine_recommendation':r.get('Machine_Recommendation',''),'suggested_canonical_record_id':r.get('Suggested_Canonical_Record_ID',''),'human_adjudication':''})
        write_csv(o/f'duplicate_evidence_{idx+1:02d}.csv',out)
    else:
        idx=agent-21; rows=split_even(read_csv(p/'supplementary_route_seed.csv'),10)[idx]; out=[]
        for i,r in enumerate(rows): out.append({'record_id':choose_id(r,i),'title':r.get('candidate_title',''),'doi':r.get('candidate_doi',''),'pdf_verified':r.get('PDF_Verified',''),'verified_source':r.get('Verified_Source',''),'verified_url':r.get('Verified_URL',''),'pdf_sha256':r.get('PDF_SHA256',''),'machine_priority':r.get('Machine_Priority',''),'human_verification':''})
        write_csv(o/f'supplementary_route_evidence_{idx+1:02d}.csv',out)

def cmd_gate2(a):
    inp,out=Path(a.input),Path(a.out); out.mkdir(parents=True,exist_ok=True)
    f=list(inp.rglob('fulltext_evidence_*.csv')); d=list(inp.rglob('duplicate_evidence_*.csv')); s=list(inp.rglob('supplementary_route_evidence_*.csv'))
    if (len(f),len(d),len(s))!=(10,10,10): raise SystemExit('Gate2 missing shards')
    rf=sum(len(read_csv(x)) for x in f); rd=sum(len(read_csv(x)) for x in d); rs=sum(len(read_csv(x)) for x in s)
    if (rf,rd,rs)!=(768,806,3732): raise SystemExit(f'Gate2 row mismatch {(rf,rd,rs)}')
    rows=[]
    for x in f+d+s: rows+=read_csv(x)
    if nonblank_protected(rows): raise SystemExit('Gate2 protected decisions populated')
    write_csv(out/'gate2_evidence_master.csv',rows)
    (out/'gate2.json').write_text(json.dumps({'status':'PASS','fulltext':rf,'duplicates':rd,'supplementary_routes':rs,'human_decisions':0},indent=2),encoding='utf-8')

def cmd_stage3(a):
    p,o,agent=Path(a.prepared),Path(a.out),int(a.agent); o.mkdir(parents=True,exist_ok=True); stream=task(p,3,agent)
    b=f'{((agent-1)%10)+1:02d}'
    if stream=='main_intake_control':
        rows=[]
        for rev in REVIEWERS:
            src=p/'reviewer_files'/'main'/f'MAIN_POSTCAL_{b}_{rev}.csv'; data=read_csv(src)
            rows.append({'stage':'main_title_abstract','batch':b,'reviewer':rev,'expected_rows':len(data),'expected_sha256':sha256(src),'returned_file':'','schema_validated':'','rows_complete':'','decision_values_valid':'','human_completion_confirmed':''})
        write_csv(o/f'MAIN_{b}_return_intake.csv',rows)
    elif stream=='supplementary_intake_control':
        batch=f'{agent-10:02d}'; rows=[]
        for rev in REVIEWERS:
            src=p/'reviewer_files'/'supplementary'/f'SUP_POSTCAL_{batch}_{rev}.csv'; data=read_csv(src)
            rows.append({'stage':'supplementary_title_abstract','batch':batch,'reviewer':rev,'expected_rows':len(data),'expected_sha256':sha256(src),'returned_file':'','schema_validated':'','rows_complete':'','decision_values_valid':'','human_completion_confirmed':''})
        write_csv(o/f'SUP_{batch}_return_intake.csv',rows)
    else:
        idx=agent-20
        stages=[('calibration',1000),('main_title_abstract',7433),('supplementary_title_abstract',3732),('duplicate_adjudication',806),('fulltext_screening',768)]
        rows=[{'control_agent':idx,'stage':s,'expected_records':n,'completed_human_records':0,'pending_records':n,'status':'NOT_STARTED','prisma_count_approved_by_human':''} for s,n in stages]
        write_csv(o/f'progress_control_{idx:02d}.csv',rows)

def cmd_consolidate(a):
    inp,out=Path(a.input),Path(a.out); out.mkdir(parents=True,exist_ok=True)
    g1=list((inp/'gate1').rglob('gate1_calibration_handoff_audit.csv')); g2=list((inp/'gate2').rglob('gate2_evidence_master.csv'))
    main=list((inp/'stage3').rglob('MAIN_*_return_intake.csv')); sup=list((inp/'stage3').rglob('SUP_*_return_intake.csv')); prog=list((inp/'stage3').rglob('progress_control_*.csv'))
    if not g1 or not g2 or (len(main),len(sup),len(prog))!=(10,10,10): raise SystemExit('Missing final outputs')
    shutil.copy2(g1[0],out/'01_calibration_handoff_audit.csv'); shutil.copy2(g2[0],out/'02_fulltext_duplicate_route_evidence_master.csv')
    rows=[]
    for p in main+sup: rows+=read_csv(p)
    write_csv(out/'03_returned_file_intake_control.csv',rows)
    prows=[]
    for p in prog: prows+=read_csv(p)
    write_csv(out/'04_prisma_progress_control.csv',prows)
    if nonblank_protected(rows+prows): raise SystemExit('Protected final fields populated')
    summary={'pipeline':'SRMA 90-Agent Handoff Evidence and Intake Support','status':'OPERATIONAL_SUPPORT_READY','stage1_agents':30,'stage2_agents':30,'stage3_agents':30,'calibration_records':1000,'main_remaining_records':7433,'supplementary_records':3732,'fulltext_evidence_records':768,'duplicate_evidence_records':806,'human_screening_decisions':0,'next_human_action':'Mizan and Kapashia independently complete CAL_01 and return both files.'}
    (out/'final_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    (out/'README.md').write_text('# Handoff, evidence, and intake support\n\nAll human decision fields remain blank.\n',encoding='utf-8')

def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd',required=True)
    for name,func,args in [
        ('prepare',cmd_prepare,['input','out']),('stage1-agent',cmd_stage1,['prepared','out','agent']),('gate1',cmd_gate1,['input','out']),
        ('stage2-agent',cmd_stage2,['prepared','out','agent']),('gate2',cmd_gate2,['input','out']),('stage3-agent',cmd_stage3,['prepared','out','agent']),('consolidate',cmd_consolidate,['input','out'])]:
        p=sub.add_parser(name)
        for x in args: p.add_argument('--'+x,required=True)
        p.set_defaults(func=func)
    a=ap.parse_args(); a.func(a)
if __name__=='__main__': main()
