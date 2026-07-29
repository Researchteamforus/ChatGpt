#!/usr/bin/env python3
from __future__ import annotations
import argparse, csv, hashlib, json, re, shutil, unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote

try:
    import requests
except Exception:
    requests = None

PROSPERO='CRD420261461557'
UA='SRMA-Bangladesh-Immunization/1.0 (systematic review; contact via repository)'

BD = {'bangladesh','bangladeshi','dhaka','chattogram','chittagong','sylhet','rajshahi','rangpur','khulna','barishal','barisal','mymensingh','comilla','cumilla','gazipur','dinajpur'}
CHILD = {'child','children','infant','infants','newborn','neonatal','under-five','under five','preschool','paediatric','pediatric','adolescent'}
VAX = {'vaccin','immunis','immuniz','epi','zero-dose','zero dose','measles','bcg','dpt','pentavalent','polio','hepatitis b','rotavirus','pneumococcal'}
OUTCOME = {'coverage','uptake','dropout','timeliness','delay','determinant','inequal','barrier','access','completion','fully immunized','fully vaccinated','missed','hesitan','service delivery','programme','program'}
NEG = {'pakistan','india','nepal','sri lanka','ethiopia','nigeria','kenya','uganda','ghana','china','indonesia','vietnam','united states','canada','australia','england'}

def norm_text(x):
    x='' if x is None else str(x)
    x=unicodedata.normalize('NFKC',x).lower()
    x=re.sub(r'https?://\S+',' ',x)
    x=re.sub(r'[^\w\s]',' ',x,flags=re.UNICODE)
    return re.sub(r'\s+',' ',x).strip()

def norm_doi(x):
    x='' if x is None else str(x).strip().lower()
    x=re.sub(r'^https?://(dx\.)?doi\.org/','',x)
    x=re.sub(r'^doi:\s*','',x)
    return x.strip().rstrip('.,;')

def stable_id(row):
    seed='|'.join([norm_doi(row.get('candidate_doi','')),norm_text(row.get('candidate_title','')),str(row.get('candidate_year',''))])
    return 'SUP-'+hashlib.sha1(seed.encode()).hexdigest()[:20]

def read_csv(path):
    with open(path,newline='',encoding='utf-8-sig') as f: return list(csv.DictReader(f))

def write_csv(path, rows, fields=None):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    rows=list(rows)
    if fields is None:
        fields=[]
        for r in rows:
            for k in r:
                if k not in fields: fields.append(k)
    with open(path,'w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore'); w.writeheader(); w.writerows(rows)

def locate(root, basename):
    hits=list(Path(root).rglob(basename))
    if not hits: raise FileNotFoundError(f'{basename} not found under {root}')
    return hits[0]

def triage(row):
    text=norm_text(' '.join([row.get('candidate_title',''), row.get('supplementary_source','')]))
    bd=any(t in text for t in BD)
    child=any(t in text for t in CHILD)
    vax=any(t in text for t in VAX)
    outcome=any(t in text for t in OUTCOME)
    neg=[t for t in NEG if t in text]
    score=(6 if bd else 0)+(3 if vax else 0)+(2 if child else 0)+(2 if outcome else 0)
    if neg and not bd: score-=3
    if bd and vax and (child or outcome): label='High priority'
    elif score>=4 or (vax and (child or outcome)): label='Unclear—human review'
    else: label='Low priority—not formal exclusion'
    evidence=[]
    if bd:evidence.append('Bangladesh signal')
    if vax:evidence.append('vaccination signal')
    if child:evidence.append('child population signal')
    if outcome:evidence.append('review outcome signal')
    if neg:evidence.append('other geography: '+', '.join(neg[:3]))
    return label,score,'; '.join(evidence) or 'weak protocol signal'

def cmd_prepare(a):
    out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    supp=read_csv(locate(a.input,'supplementary_citation_candidates.csv'))
    known=read_csv(locate(a.input,'combined_discovery_master_55248.csv'))
    known_doi={norm_doi(r.get('DOI','')) for r in known if norm_doi(r.get('DOI',''))}
    known_ty={norm_text(r.get('Title',''))+'|'+str(r.get('Year','')).split('.')[0] for r in known if norm_text(r.get('Title',''))}
    base=[]
    for r in supp:
        r=dict(r); r['Supplementary_Record_ID']=stable_id(r)
        doi=norm_doi(r.get('candidate_doi','')); title=norm_text(r.get('candidate_title','')); year=str(r.get('candidate_year','')).split('.')[0]
        r['DOI_Key']=doi; r['Title_Year_Key']=title+'|'+year
        r['Known_Master_Exact_Match']='Yes' if (doi and doi in known_doi) or (title and title+'|'+year in known_ty) else 'No'
        r['Human_Title_Abstract_Decision']=''; r['Human_Reviewer']=''; r['Human_Review_Date']=''; r['Human_Notes']=''
        base.append(r)
    write_csv(out/'supplementary_base.csv',base)
    for mode,n in [('triage',60),('dedup',20)]:
        d=out/f'{mode}_shards'; d.mkdir(exist_ok=True)
        buckets=[[] for _ in range(n)]
        for i,r in enumerate(base): buckets[i%n].append(r)
        for i,b in enumerate(buckets,1): write_csv(d/f'{mode}_{i:02d}.csv',b)
    scored=[]
    for r in base:
        lab,s,ev=triage(r); q=dict(r); q.update({'Machine_Priority':lab,'Machine_Score':s,'Machine_Evidence':ev}); scored.append(q)
    priority=[r for r in scored if r['Known_Master_Exact_Match']=='No' and r['Machine_Priority']!='Low priority—not formal exclusion']
    priority.sort(key=lambda r:(-int(r['Machine_Score']), str(r.get('candidate_year',''))))
    route=priority[:4000]
    rd=out/'route_shards'; rd.mkdir(exist_ok=True); rb=[[] for _ in range(20)]
    for i,r in enumerate(route): rb[i%20].append(r)
    for i,b in enumerate(rb,1): write_csv(rd/f'route_{i:02d}.csv',b)
    sd=out/'screen_shards'; sd.mkdir(exist_ok=True); sb=[[] for _ in range(10)]
    for i,r in enumerate(priority): sb[i%10].append(r)
    for i,b in enumerate(sb,1): write_csv(sd/f'screen_{i:02d}.csv',b)
    qd=out/'qa_shards'; qd.mkdir(exist_ok=True); qb=[[] for _ in range(10)]
    for i,r in enumerate(base): qb[i%10].append(r)
    for i,b in enumerate(qb,1): write_csv(qd/f'qa_{i:02d}.csv',b)
    summary={'prospero':PROSPERO,'supplementary_candidates':len(base),'known_master_exact_matches':sum(r['Known_Master_Exact_Match']=='Yes' for r in base),'new_or_uncertain':sum(r['Known_Master_Exact_Match']=='No' for r in base),'route_validation_pool':len(route),'screening_priority_pool':len(priority),'formal_human_decisions':0}
    (out/'prepare_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')

def shard_file(root,mode,agent): return locate(root,f'{mode}_{int(agent):02d}.csv')

def cmd_triage(a):
    rows=read_csv(shard_file(a.input,'triage',a.agent)); out=[]
    for r in rows:
        lab,s,ev=triage(r); q=dict(r); q.update({'Agent':a.agent,'Machine_Priority':lab,'Machine_Score':s,'Machine_Evidence':ev}); out.append(q)
    write_csv(Path(a.out)/f'triage_{int(a.agent):02d}.csv',out)

def cmd_dedup(a):
    rows=read_csv(shard_file(a.input,'dedup',a.agent)); out=[]
    seen=defaultdict(list)
    for r in rows:
        key=('doi:'+r['DOI_Key']) if r.get('DOI_Key') else ('ty:'+r.get('Title_Year_Key',''))
        seen[key].append(r)
    for key,grp in seen.items():
        canon=min((r['Supplementary_Record_ID'] for r in grp),default='')
        for r in grp:
            q=dict(r); q.update({'Agent':a.agent,'Candidate_Family_Key':key,'Shard_Family_Size':len(grp),'Suggested_Canonical_Record_ID':canon,'Machine_Duplicate_Action':'Known-master duplicate' if r.get('Known_Master_Exact_Match')=='Yes' else ('Review family' if len(grp)>1 else 'Retain pending screening'),'Human_Duplicate_Adjudication':'','Human_Reviewer':'','Human_Notes':''}); out.append(q)
    write_csv(Path(a.out)/f'dedup_{int(a.agent):02d}.csv',out)

def get_json(url,params=None,timeout=20):
    if requests is None:return None,'requests unavailable'
    try:
        rr=requests.get(url,params=params,headers={'User-Agent':UA,'Accept':'application/json'},timeout=timeout)
        if rr.status_code==200:return rr.json(),''
        return None,f'HTTP {rr.status_code}'
    except Exception as e:return None,type(e).__name__+': '+str(e)[:160]

def try_pdf(url,outfile):
    if requests is None:return False,0,'','requests unavailable'
    try:
        r=requests.get(url,headers={'User-Agent':UA,'Accept':'application/pdf,*/*'},timeout=25,allow_redirects=True,stream=True)
        if r.status_code!=200:return False,0,'',f'HTTP {r.status_code}'
        data=b''
        for ch in r.iter_content(65536):
            if ch:
                data+=ch
                if len(data)>25_000_000:return False,0,'','over 25MB'
        if not data.startswith(b'%PDF'):return False,len(data),'','not PDF signature'
        Path(outfile).parent.mkdir(parents=True,exist_ok=True); Path(outfile).write_bytes(data)
        return True,len(data),hashlib.sha256(data).hexdigest(),''
    except Exception as e:return False,0,'',type(e).__name__+': '+str(e)[:160]

def routes_for(r):
    doi=norm_doi(r.get('candidate_doi','')); routes=[]; errors=[]
    if doi:
        j,e=get_json('https://www.ebi.ac.uk/europepmc/webservices/rest/search',{'query':f'DOI:"{doi}"','format':'json','pageSize':5})
        if j:
            for x in j.get('resultList',{}).get('result',[]):
                pmcid=x.get('pmcid')
                if pmcid: routes.append(('EuropePMC',f'https://europepmc.org/articles/{pmcid}?pdf=render'))
        elif e:errors.append('EuropePMC '+e)
        j,e=get_json('https://api.openalex.org/works/https://doi.org/'+quote(doi,safe='/:'))
        if j:
            locs=[]
            if j.get('best_oa_location'):locs.append(j['best_oa_location'])
            locs+=j.get('locations') or []
            for loc in locs:
                u=loc.get('pdf_url') or loc.get('landing_page_url')
                if u:routes.append(('OpenAlex',u))
        elif e:errors.append('OpenAlex '+e)
        routes.append(('DOI',f'https://doi.org/{doi}'))
    unique=[]; seen=set()
    for src,u in routes:
        if u and u not in seen:seen.add(u);unique.append((src,u))
    return unique,errors

def cmd_route(a):
    rows=read_csv(shard_file(a.input,'route',a.agent)); out=[]; pdfdir=Path(a.out)/'pdfs'
    for r in rows:
        routes,errs=routes_for(r); verified=False; src=url=''; size=0; sha=''; attempts=[]
        for s,u in routes[:8]:
            ok,n,h,e=try_pdf(u,pdfdir/(r['Supplementary_Record_ID']+'.pdf'))
            attempts.append(f'{s}:{"OK" if ok else e}')
            if ok: verified=True;src=s;url=u;size=n;sha=h;break
        q=dict(r); q.update({'Agent':a.agent,'PDF_Verified':'Yes' if verified else 'No','Verified_Source':src,'Verified_URL':url,'PDF_Bytes':size,'PDF_SHA256':sha,'Candidate_Routes':' | '.join(f'{s}:{u}' for s,u in routes),'Lookup_Errors':' | '.join(errs),'Attempt_Log':' | '.join(attempts),'Human_Verification':'','Human_Notes':''}); out.append(q)
    write_csv(Path(a.out)/f'route_{int(a.agent):02d}.csv',out)
    (Path(a.out)/'summary.json').write_text(json.dumps({'agent':int(a.agent),'rows':len(out),'pdfs_verified':sum(r['PDF_Verified']=='Yes' for r in out)},indent=2),encoding='utf-8')

def cmd_screen(a):
    rows=read_csv(shard_file(a.input,'screen',a.agent)); fields=['Supplementary_Record_ID','candidate_title','candidate_year','candidate_doi','supplementary_source','Human_Title_Abstract_Decision','Primary_Exclusion_Code','Reviewer','Review_Date','Reviewer_Notes']
    clean=[]; admin=[]
    for r in rows:
        clean.append({**r,'Human_Title_Abstract_Decision':'','Primary_Exclusion_Code':'','Reviewer':'','Review_Date':'','Reviewer_Notes':''})
        admin.append({'Supplementary_Record_ID':r['Supplementary_Record_ID'],'Machine_Priority':r.get('Machine_Priority',''),'Machine_Score':r.get('Machine_Score',''),'Known_Master_Exact_Match':r.get('Known_Master_Exact_Match','')})
    od=Path(a.out); write_csv(od/f'SUP_TA_{int(a.agent):02d}_Mizan.csv',clean,fields); write_csv(od/f'SUP_TA_{int(a.agent):02d}_Kapashia.csv',clean,fields); write_csv(od/f'SUP_TA_{int(a.agent):02d}_Admin_Key.csv',admin)

def cmd_qa(a):
    rows=read_csv(shard_file(a.input,'qa',a.agent)); ids=[r['Supplementary_Record_ID'] for r in rows]
    report={'Agent':a.agent,'Rows':len(rows),'Unique_IDs':len(set(ids)),'Duplicate_IDs':len(ids)-len(set(ids)),'Known_Master_Matches':sum(r.get('Known_Master_Exact_Match')=='Yes' for r in rows),'Human_Decision_Nonblank':sum(bool((r.get('Human_Title_Abstract_Decision') or '').strip()) for r in rows),'Checksum_SHA256':hashlib.sha256('\n'.join(sorted(ids)).encode()).hexdigest(),'Governance':'Supplementary candidates require documented human screening before inclusion.'}
    write_csv(Path(a.out)/f'qa_{int(a.agent):02d}.csv',[report])

def gather(root,pattern):
    rows=[]
    for p in Path(root).rglob(pattern): rows+=read_csv(p)
    return rows

def cmd_consolidate(a):
    root=Path(a.input); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    tri=gather(root,'triage_*.csv'); ded=gather(root,'dedup_*.csv'); route=gather(root,'route_*.csv'); qa=gather(root,'qa_*.csv')
    tri=[r for r in tri if 'Machine_Priority' in r and 'Agent' in r]
    ded=[r for r in ded if 'Candidate_Family_Key' in r]
    route=[r for r in route if 'PDF_Verified' in r]
    qa=[r for r in qa if 'Checksum_SHA256' in r]
    byid={r['Supplementary_Record_ID']:r for r in tri}
    for r in ded:
        byid.setdefault(r['Supplementary_Record_ID'],{}).update({k:v for k,v in r.items() if k in ['Candidate_Family_Key','Shard_Family_Size','Suggested_Canonical_Record_ID','Machine_Duplicate_Action']})
    final=list(byid.values())
    write_csv(out/'supplementary_machine_triage_master.csv',final)
    write_csv(out/'supplementary_duplicate_audit_master.csv',ded)
    write_csv(out/'supplementary_route_validation_master.csv',route)
    write_csv(out/'supplementary_prisma_qa_master.csv',qa)
    for p in root.rglob('SUP_TA_*.csv'):
        d=out/'reviewer_batches'; d.mkdir(exist_ok=True); shutil.copy2(p,d/p.name)
    counts=Counter(r.get('Machine_Priority','') for r in final)
    summary={'prospero':PROSPERO,'parallel_agents':120,'supplementary_candidates':len(final),'priority_counts':dict(counts),'known_master_exact_matches':sum(r.get('Known_Master_Exact_Match')=='Yes' for r in final),'route_records_attempted':len(route),'pdfs_verified':sum(r.get('PDF_Verified')=='Yes' for r in route),'reviewer_batch_files':len(list((out/'reviewer_batches').glob('*.csv'))) if (out/'reviewer_batches').exists() else 0,'formal_human_screening_decisions':0,'formal_eligibility_decisions':0}
    (out/'final_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    (out/'README.md').write_text('Machine-assisted supplementary-search preparation only. No human screening or eligibility decisions are claimed.\n',encoding='utf-8')

def main():
    p=argparse.ArgumentParser(); sp=p.add_subparsers(dest='cmd',required=True)
    for cmd in ['prepare','triage-agent','dedup-agent','route-agent','screen-agent','qa-agent','consolidate']:
        q=sp.add_parser(cmd); q.add_argument('--input',required=True); q.add_argument('--out',required=True)
        if cmd not in ['prepare','consolidate']: q.add_argument('--agent',required=True)
    a=p.parse_args(); {'prepare':cmd_prepare,'triage-agent':cmd_triage,'dedup-agent':cmd_dedup,'route-agent':cmd_route,'screen-agent':cmd_screen,'qa-agent':cmd_qa,'consolidate':cmd_consolidate}[a.cmd](a)
if __name__=='__main__': main()
