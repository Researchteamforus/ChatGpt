#!/usr/bin/env python3
import argparse, csv, hashlib, json, os, re, shutil, sys, urllib.error, urllib.parse, urllib.request
from collections import defaultdict
from pathlib import Path

BLANK_EQUIV = {'', 'not reviewed', 'not checked', 'pending', 'na', 'n/a'}
HUMAN_TOKENS = ('human_', 'reviewer', 'decision', 'adjudication', 'exclusion_reason', 'extractor', 'verifier')


def norm(s):
    return re.sub(r'\s+', ' ', (s or '').strip())

def read_csv(path):
    with open(path, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

def write_csv(path, rows, fields=None):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = list(rows[0].keys()) if rows else []
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader(); w.writerows(rows)

def find_file(root, filename):
    hits = list(Path(root).rglob(filename))
    if not hits:
        raise FileNotFoundError(f'{filename} not found under {root}')
    return hits[0]

def sha256_file(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()

def shard(rows, batch, total=20):
    return [r for i,r in enumerate(rows) if i % total == batch-1]

def blank_human_count(rows):
    bad=[]
    for idx,r in enumerate(rows,2):
        for k,v in r.items():
            kl=k.lower()
            if any(t in kl for t in HUMAN_TOKENS):
                if norm(v).lower() not in BLANK_EQUIV:
                    bad.append((idx,k,v))
    return bad

def prepare(args):
    out=Path(args.out); out.mkdir(parents=True, exist_ok=True)
    retrieval=read_csv(find_file(args.source,'retrieval_master.csv'))
    revalidation=read_csv(find_file(args.source,'revalidation_master.csv'))
    fulltext=read_csv(find_file(args.source,'fulltext_master.csv'))
    duplicates=read_csv(find_file(args.source,'duplicates_master.csv'))
    supp=read_csv(find_file(args.source,'supplementary_route_validation_master.csv'))
    extraction=read_csv(find_file(args.source,'blank_extraction_rob_form.csv'))

    fmap={r.get('Record_ID',''):r for r in fulltext}
    unresolved=[]
    current = [(r,'Previously unresolved') for r in retrieval if norm(r.get('PDF_Verified_This_Run')).lower() != 'yes']
    current += [(r,'Previously retrieved but failed revalidation') for r in revalidation if norm(r.get('PDF_Verified_This_Run')).lower() != 'yes']
    seen=set()
    for r,origin in current:
        rid=r.get('Record_ID','')
        if not rid or rid in seen: continue
        seen.add(rid); f=fmap.get(rid,{})
        unresolved.append({
            'Record_ID':rid,'Title':r.get('Title','') or f.get('Title',''),'DOI':r.get('DOI','') or f.get('DOI',''),
            'PMID':r.get('PMID','') or f.get('PMID',''),'Original_Candidate_Routes':r.get('Candidate_Routes',''),
            'Previous_Attempt_Log':r.get('Attempt_Log',''),'Operational_Priority':f.get('Operational_Priority',''),
            'Queue_Status':f.get('Queue_Status',''),'Current_Route_Origin':origin,
            'Human_Route_Verification':'','Human_Notes':''
        })
    write_csv(out/'unresolved_fulltext_routes.csv', unresolved)
    write_csv(out/'duplicate_evidence.csv', duplicates)
    write_csv(out/'fulltext_screening_seed.csv', fulltext)
    write_csv(out/'extraction_rob_seed.csv', extraction)
    write_csv(out/'supplementary_route_reference.csv', supp)
    manifest={
        'pipeline':'SRMA 80-Agent Full-text and Evidence Readiness',
        'unresolved_fulltext':len(unresolved),'duplicate_rows':len(duplicates),
        'fulltext_rows':len(fulltext),'extraction_seed_rows':len(extraction),
        'supplementary_reference_rows':len(supp),'protected_human_values':0,
    }
    for name,rows in [('unresolved',unresolved),('duplicates',duplicates),('fulltext',fulltext),('extraction',extraction)]:
        bad=blank_human_count(rows)
        if bad:
            raise AssertionError(f'Nonblank protected values in {name}: {bad[:3]}')
    (out/'prepare_summary.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(manifest))

def doi_routes(doi):
    doi=norm(doi)
    if not doi: return []
    doi=doi.replace('https://doi.org/','').replace('http://doi.org/','')
    q=urllib.parse.quote(doi,safe='/()')
    return [
        ('DOI',f'https://doi.org/{q}'),
        ('UnpaywallLanding',f'https://api.unpaywall.org/v2/{q}?email=srma.audit@example.com'),
        ('CrossrefWorks',f'https://api.crossref.org/works/{q}'),
    ]

def pmid_routes(pmid):
    pmid=re.sub(r'\D','',norm(pmid))
    if not pmid:return []
    return [
        ('PubMed',f'https://pubmed.ncbi.nlm.nih.gov/{pmid}/'),
        ('PMCIdConv',f'https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?ids={pmid}&format=json'),
        ('EuropePMC',f'https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=EXT_ID:{pmid}%20AND%20SRC:MED&format=json'),
    ]

def parse_original_routes(text):
    out=[]
    for piece in re.split(r'[|;]\s*', norm(text)):
        if 'http' not in piece: continue
        m=re.search(r'(https?://\S+)',piece)
        if m: out.append(('PriorRoute',m.group(1).rstrip('),.]')))
    return out

def probe(url, timeout=8):
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 SRMA evidence verifier','Accept':'application/pdf,text/html,application/json;q=0.9,*/*;q=0.1'})
    try:
        with urllib.request.urlopen(req,timeout=timeout) as resp:
            data=resp.read(2048)
            ctype=resp.headers.get('Content-Type','')
            final=resp.geturl()
            pdf=data.startswith(b'%PDF') or 'application/pdf' in ctype.lower()
            return {'status':'PDF verified' if pdf else 'Reachable non-PDF','http_status':getattr(resp,'status',200),'content_type':ctype,'final_url':final,'pdf_signature':'Yes' if pdf else 'No','error':''}
    except Exception as e:
        return {'status':'Failed','http_status':'','content_type':'','final_url':'','pdf_signature':'No','error':type(e).__name__+': '+str(e)[:180]}

def route_agent(args):
    rows=read_csv(Path(args.prepared)/'unresolved_fulltext_routes.csv')
    subset=shard(rows,args.batch,20); out=[]
    for r in subset:
        routes=[]; seen=set()
        for src,url in parse_original_routes(r.get('Original_Candidate_Routes',''))+doi_routes(r.get('DOI',''))+pmid_routes(r.get('PMID','')):
            if url not in seen: seen.add(url); routes.append((src,url))
        if not routes:
            routes=[('ManualTitleSearch','')]
        attempts=[]; best={'status':'No route','final_url':'','pdf_signature':'No'}
        for src,url in routes[:args.max_routes]:
            if not url:
                attempts.append({'source':src,'url':'','status':'Manual search required','error':''}); continue
            p=probe(url,args.timeout)
            attempts.append({'source':src,'url':url,**p})
            if p['pdf_signature']=='Yes': best=p; best['source']=src; break
            if best.get('status') in ('No route','Failed') and p['status']!='Failed': best=p; best['source']=src
        out.append({**r,'Agent':str(args.batch),'Routes_Tested':str(len(attempts)),
                    'Machine_Route_Status':best.get('status','No route'),'Machine_Verified_PDF':'Yes' if best.get('pdf_signature')=='Yes' else 'No',
                    'Best_Source':best.get('source',''),'Best_Final_URL':best.get('final_url',''),
                    'Attempt_Log_JSON':json.dumps(attempts,ensure_ascii=False),
                    'Human_Route_Verification':'','Human_Notes':''})
    write_csv(Path(args.out)/f'route_recovery_{args.batch:02d}.csv',out)

def duplicate_agent(args):
    rows=read_csv(Path(args.prepared)/'duplicate_evidence.csv')
    groups=defaultdict(list)
    for r in rows: groups[r.get('Candidate_Key','')].append(r)
    keys=sorted(groups)
    selected=[k for i,k in enumerate(keys) if i%20==args.batch-1]
    out=[]
    for key in selected:
        grp=groups[key]
        dois={norm(r.get('DOI')).lower() for r in grp if norm(r.get('DOI'))}
        canonical=next((r.get('Suggested_Canonical_Record_ID') for r in grp if r.get('Suggested_Canonical_Record_ID')),grp[0].get('Record_ID',''))
        strength='Strong' if len(dois)<=1 and any(r.get('Machine_Action')=='Strong duplicate evidence' for r in grp) else 'Review'
        for r in grp:
            out.append({**r,'Agent':str(args.batch),'Family_Row_Count':str(len(grp)),'Machine_Family_Strength':strength,
                        'Provisional_Canonical_Record_ID':canonical,'Human_Duplicate_Decision':'','Human_Canonical_Record_ID':'','Human_Notes':''})
    write_csv(Path(args.out)/f'duplicate_family_{args.batch:02d}.csv',out)

def fulltext_agent(args):
    rows=read_csv(Path(args.prepared)/'fulltext_screening_seed.csv')
    subset=shard(rows,args.batch,20); out=[]
    for r in subset:
        out.append({
            'Record_ID':r.get('Record_ID',''),'Title':r.get('Title',''),'DOI':r.get('DOI',''),'PMID':r.get('PMID',''),
            'PDF_Retrieved':r.get('PDF_Retrieved',''),'Machine_Evidence_Status':r.get('Machine_Evidence_Status',''),
            'Operational_Priority':r.get('Operational_Priority',''),'Required_Next_Action':r.get('Required_Next_Action',''),
            'Agent':str(args.batch),'Reviewer_1_Full_Text_Decision':'','Reviewer_1_Exclusion_Reason':'','Reviewer_1_Notes':'',
            'Reviewer_2_Full_Text_Decision':'','Reviewer_2_Exclusion_Reason':'','Reviewer_2_Notes':'',
            'Consensus_Full_Text_Decision':'','Consensus_Exclusion_Reason':'','Consensus_Notes':'','Review_Date':''})
    write_csv(Path(args.out)/f'fulltext_form_{args.batch:02d}.csv',out)

def extraction_agent(args):
    rows=read_csv(Path(args.prepared)/'extraction_rob_seed.csv')
    subset=shard(rows,args.batch,20); out=[]
    for r in subset:
        r=dict(r); r['Agent']=str(args.batch); r['Eligibility_Prerequisite']='Await final human full-text inclusion'
        for k in list(r):
            kl=k.lower()
            if k in ('Integrated_ID','Expected_Title','Expected_DOI','Expected_PMID','PDF_File','Actual_SHA256','Detected_Pages','Machine_Evidence_Status','Bangladesh_Flag','Child_Flag','Vaccination_Flag','Outcome_Flag','Suggested_Design','Evidence_Snippet','Agent','Eligibility_Prerequisite'):
                continue
            if any(t in kl for t in ('study_id','citation','confirmed','setting','period','population','sample','age_group','definition','outcome_type','numerator','denominator','effect','confidence','covariates','determinants','rob_','overall_rob','extractor','verifier','notes')):
                r[k]=''
        out.append(r)
    write_csv(Path(args.out)/f'extraction_rob_form_{args.batch:02d}.csv',out)

def gate(args):
    root=Path(args.input); files=list(root.rglob('*.csv'))
    expected=args.expected
    if len(files)!=expected: raise AssertionError(f'Expected {expected} CSV files, found {len(files)}')
    total=0; bad=[]
    for p in files:
        rows=read_csv(p); total+=len(rows); bad.extend((str(p),)+x for x in blank_human_count(rows))
    if bad: raise AssertionError(f'Protected human fields populated: {bad[:5]}')
    Path(args.out).mkdir(parents=True,exist_ok=True)
    summary={'gate':args.name,'status':'PASS','files':len(files),'rows':total,'protected_human_values':0}
    (Path(args.out)/f'{args.name}_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    for p in files:
        target=Path(args.out)/p.name
        shutil.copy2(p,target)
    print(json.dumps(summary))

def consolidate(args):
    inp=Path(args.input); out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    categories={'route_recovery':'route_recovery_*.csv','duplicate_family':'duplicate_family_*.csv','fulltext_forms':'fulltext_form_*.csv','extraction_rob_forms':'extraction_rob_form_*.csv'}
    summary={'pipeline':'SRMA 80-Agent Full-text and Evidence Readiness','status':'OPERATIONAL_EVIDENCE_PACKAGE_READY','agents':80,'human_decisions_created':0}
    for cat,pat in categories.items():
        files=list(inp.rglob(pat)); rows=[]
        for p in files: rows.extend(read_csv(p))
        write_csv(out/f'{cat}_master.csv',rows)
        summary[cat+'_files']=len(files); summary[cat+'_rows']=len(rows)
    route=read_csv(out/'route_recovery_master.csv')
    summary['new_machine_verified_pdf_routes']=sum(r.get('Machine_Verified_PDF')=='Yes' for r in route)
    summary['unresolved_after_route_probe']=sum(r.get('Machine_Verified_PDF')!='Yes' for r in route)
    summary['next_human_action']='Complete title-abstract recheck; then use full-text forms only for records retained for full-text review.'
    (out/'final_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    (out/'README.md').write_text('# SRMA 80-Agent Full-text and Evidence Readiness\n\nThis package prepares lawful route evidence, duplicate-family evidence, blank full-text forms, and blank extraction/RoB forms. It does not create human eligibility, extraction, or risk-of-bias decisions.\n',encoding='utf-8')
    print(json.dumps(summary))

def main():
    p=argparse.ArgumentParser(); sp=p.add_subparsers(dest='cmd',required=True)
    q=sp.add_parser('prepare'); q.add_argument('--source',required=True); q.add_argument('--out',required=True); q.set_defaults(func=prepare)
    q=sp.add_parser('route-agent'); q.add_argument('--prepared',required=True); q.add_argument('--out',required=True); q.add_argument('--batch',type=int,required=True); q.add_argument('--timeout',type=int,default=8); q.add_argument('--max-routes',type=int,default=3); q.set_defaults(func=route_agent)
    q=sp.add_parser('duplicate-agent'); q.add_argument('--prepared',required=True); q.add_argument('--out',required=True); q.add_argument('--batch',type=int,required=True); q.set_defaults(func=duplicate_agent)
    q=sp.add_parser('fulltext-agent'); q.add_argument('--prepared',required=True); q.add_argument('--out',required=True); q.add_argument('--batch',type=int,required=True); q.set_defaults(func=fulltext_agent)
    q=sp.add_parser('extraction-agent'); q.add_argument('--prepared',required=True); q.add_argument('--out',required=True); q.add_argument('--batch',type=int,required=True); q.set_defaults(func=extraction_agent)
    q=sp.add_parser('gate'); q.add_argument('--input',required=True); q.add_argument('--out',required=True); q.add_argument('--expected',type=int,required=True); q.add_argument('--name',required=True); q.set_defaults(func=gate)
    q=sp.add_parser('consolidate'); q.add_argument('--input',required=True); q.add_argument('--out',required=True); q.set_defaults(func=consolidate)
    args=p.parse_args(); args.func(args)
if __name__=='__main__': main()
