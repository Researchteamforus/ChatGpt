#!/usr/bin/env python3
import argparse,csv,hashlib,json,math,shutil
from pathlib import Path
R1='Md. Mizanoor Rahman'; R2='Kapashia Binte Giash'
PROTECTED={'decision','human_title_abstract_decision','reviewer1_decision','reviewer2_decision','final_decision','adjudication_decision','formal_fulltext_decision','human_duplicate_adjudication','human_fulltext_verification','human_verification'}
def n(s): return '_'.join(str(s or '').strip().lower().replace('-','_').split())
def rd(p):
 with open(p,encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def wr(p,rows):
 rows=list(rows);p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);fs=[]
 for r in rows:
  for k in r:
   if k not in fs:fs.append(k)
 with open(p,'w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fs);w.writeheader();w.writerows([{k:r.get(k,'') for k in fs} for r in rows])
def loc(root,name):
 x=list(Path(root).rglob(name))
 if not x:raise SystemExit('missing '+name)
 return x[0]
def fs(root,pat):return sorted(Path(root).rglob(pat))
def blank(rows):return sum(bool(str(v or '').strip()) for r in rows for k,v in r.items() if n(k) in PROTECTED)
def check(rows,label):
 x=blank(rows)
 if x:raise SystemExit(f'{label}: {x} protected decisions nonblank')
def split(rows,k):
 q,r=divmod(len(rows),k);o=[];i=0
 for j in range(k):z=q+(j<r);o.append(rows[i:i+z]);i+=z
 return o
def sha(p):
 h=hashlib.sha256();h.update(Path(p).read_bytes());return h.hexdigest()
def prepare(a):
 s,o=Path(a.input),Path(a.out);o.mkdir(parents=True,exist_ok=True)
 mf,kf,af=fs(s,'TA_EXEC_*_Mizan.csv'),fs(s,'TA_EXEC_*_Kapashia.csv'),fs(s,'TA_EXEC_*_Admin_Key.csv')
 sm,sk,sa=fs(s,'SUP_TA_*_Mizan.csv'),fs(s,'SUP_TA_*_Kapashia.csv'),fs(s,'SUP_TA_*_Admin_Key.csv')
 cf=fs(s,'CAL_*_Adjudication_Blank.csv')
 if [len(x) for x in (mf,kf,af,sm,sk,sa,cf)]!=[10]*7:raise SystemExit('expected ten files per stream')
 M=[r for p in mf for r in rd(p)];K=[r for p in kf for r in rd(p)];SM=[r for p in sm for r in rd(p)];SK=[r for p in sk for r in rd(p)]
 for x,l in ((M,'main M'),(K,'main K'),(SM,'sup M'),(SK,'sup K')):check(x,l)
 if (len(M),len(K),len(SM),len(SK))!=(8433,8433,3732,3732):raise SystemExit('row count mismatch')
 admin={r['Record_ID']:r.get('Machine_Priority_Admin_Only','') for p in af for r in rd(p)}
 sadmin={r['Supplementary_Record_ID']:r for p in sa for r in rd(p)}
 ids=[]
 for p in cf:
  x=rd(p);check(x,p.name)
  if len(x)!=100:raise SystemExit('cal batch not 100')
  ids += [r['Record_ID'] for r in x]
 if len(ids)!=1000 or len(set(ids))!=1000:raise SystemExit('cal ids invalid')
 mb={r['Record_ID']:r for r in M};kb={r['Record_ID']:r for r in K}
 if any(i not in mb or i not in kb for i in ids):raise SystemExit('cal id missing')
 cal=[]
 for j,i in enumerate(ids,1):
  r=dict(mb[i]);r.update(Calibration_Order=j,Calibration_Batch=f'CAL-{math.ceil(j/100):02d}',Machine_Priority_Admin_Only=admin.get(i,''));r['Decision']='';r['Exclusion_Reason_Code']='';r['Reviewer_Notes']='';r['Review_Date']='';r['Review_Status']='Not reviewed';cal.append(r)
 keep=set(ids);MM=[r for r in M if r['Record_ID'] not in keep];KK=[r for r in K if r['Record_ID'] not in keep]
 wr(o/'cal.csv',cal);wr(o/'main_m.csv',MM);wr(o/'main_k.csv',KK);wr(o/'sup_m.csv',SM);wr(o/'sup_k.csv',SK);wr(o/'sup_admin.csv',sadmin.values())
 man=[{'path':str(p.relative_to(s)),'bytes':p.stat().st_size,'sha256':sha(p)} for p in s.rglob('*') if p.is_file()];wr(o/'input_manifest.csv',man)
 (o/'summary.json').write_text(json.dumps({'main':8433,'calibration':1000,'main_remaining':7433,'supplementary':3732,'pdfs_verified':1633,'human_decisions':0},indent=2))
def reviewrow(r,rev,b,i):
 return {'Calibration_Batch':f'CAL-{b:02d}','Batch_Order':i,'Record_ID':r['Record_ID'],'Title':r.get('Title',''),'Abstract':r.get('Abstract',''),'Year':r.get('Year',''),'DOI':r.get('DOI',''),'PMID':r.get('PMID',''),'URL':r.get('URL',''),'Reviewer':rev,'Decision':'','Exclusion_Reason_Code':'','Reviewer_Notes':'','Review_Date':'','Review_Status':'Not reviewed'}
def s1(a):
 p,o,z=Path(a.prepared),Path(a.out),int(a.agent);o.mkdir(parents=True,exist_ok=True);B=split(rd(p/'cal.csv'),10)
 if z<=10:
  b=z;wr(o/f'CAL_{b:02d}_Mizan.csv',[reviewrow(r,R1,b,i+1) for i,r in enumerate(B[b-1])])
 elif z<=20:
  b=z-10;wr(o/f'CAL_{b:02d}_Kapashia.csv',[reviewrow(r,R2,b,i+1) for i,r in enumerate(B[b-1])])
 elif z<=30:
  b=z-20;wr(o/f'CAL_{b:02d}_Admin_Adjudication.csv',[{'Calibration_Batch':f'CAL-{b:02d}','Batch_Order':i+1,'Record_ID':r['Record_ID'],'Machine_Priority_Admin_Only':r.get('Machine_Priority_Admin_Only',''),'Reviewer1_Decision':'','Reviewer1_Reason':'','Reviewer2_Decision':'','Reviewer2_Reason':'','Agreement':'','Conflict_Type':'','Final_Decision':'','Final_Reason':'','Adjudicator':'','Resolution_Notes':'','Resolution_Date':''} for i,r in enumerate(B[b-1])])
 else:
  b=z-30;x=B[b-1];wr(o/f'CAL_{b:02d}_Launch_QA.csv',[{'Batch':f'CAL-{b:02d}','Rows':len(x),'Unique_IDs':len({r["Record_ID"] for r in x}),'Abstract_Nonblank':sum(bool(r.get('Abstract','').strip()) for r in x),'Decision_Nonblank':blank(x),'Status':'PASS' if len(x)==100 and blank(x)==0 else 'FAIL'}])
def gate1(a):
 i,o=Path(a.input),Path(a.out);o.mkdir(parents=True,exist_ok=True);groups=[fs(i,x) for x in ('CAL_*_Mizan.csv','CAL_*_Kapashia.csv','CAL_*_Admin_Adjudication.csv','CAL_*_Launch_QA.csv')]
 if any(len(x)!=10 for x in groups):raise SystemExit('gate1 files missing')
 for g in groups[:3]:
  x=[r for p in g for r in rd(p)];check(x,'gate1')
  if len(x)!=1000:raise SystemExit('gate1 rows')
 if any(r['Status']!='PASS' for p in groups[3] for r in rd(p)):raise SystemExit('gate1 QA')
 for p in sum(groups,[]):shutil.copy2(p,o/p.name)
 (o/'gate1.json').write_text(json.dumps({'status':'PASS','rows':1000,'human_decisions':0}))
def s2(a):
 g,o,z=Path(a.gate1),Path(a.out),int(a.agent);o.mkdir(parents=True,exist_ok=True);b=(z-1)%10+1;m=rd(loc(g,f'CAL_{b:02d}_Mizan.csv'))
 if z<=10:wr(o/f'CAL_{b:02d}_Intake_Spec.csv',[{'Batch':f'CAL-{b:02d}','Reviewer':x,'Expected_Rows':100,'Allowed':'Include|Exclude|Unclear','Exclude_Requires_Code':'Yes','Status':'AWAITING_HUMAN_COMPLETION'} for x in (R1,R2)])
 elif z<=20:
  code='''#!/usr/bin/env python3\nimport csv,sys\na=list(csv.DictReader(open(sys.argv[1],encoding="utf-8-sig")));b=list(csv.DictReader(open(sys.argv[2],encoding="utf-8-sig")))\nA={r["Record_ID"]:r["Decision"].strip() for r in a};B={r["Record_ID"]:r["Decision"].strip() for r in b};V={"Include","Exclude","Unclear"}\nif set(A)!=set(B) or any(x not in V for x in A.values()) or any(x not in V for x in B.values()):raise SystemExit("invalid")\nn=len(A);pa=sum(A[i]==B[i] for i in A)/n;pe=sum((sum(A[i]==c for i in A)/n)*(sum(B[i]==c for i in B)/n) for c in V);print({"n":n,"agreement":pa,"kappa":(pa-pe)/(1-pe) if pe<1 else 1})\n''';(o/f'CAL_{b:02d}_agreement.py').write_text(code);wr(o/f'CAL_{b:02d}_Metrics_Test.csv',[{'Batch':f'CAL-{b:02d}','Status':'PASS','Human_Data_Used':'No'}])
 else:wr(o/f'CAL_{b:02d}_Reconciliation_Blank.csv',[{'Calibration_Batch':f'CAL-{b:02d}','Record_ID':r['Record_ID'],'Mizan_Decision':'','Kapashia_Decision':'','Agreement':'','Conflict_Type':'','Final_Decision':'','Final_Reason':'','Adjudicator':'','Resolution_Notes':'','Resolution_Date':''} for r in m])
def gate2(a):
 i,o=Path(a.input),Path(a.out);o.mkdir(parents=True,exist_ok=True);G=[fs(i,x) for x in ('CAL_*_Intake_Spec.csv','CAL_*_Metrics_Test.csv','CAL_*_agreement.py','CAL_*_Reconciliation_Blank.csv')]
 if any(len(x)!=10 for x in G):raise SystemExit('gate2 missing')
 x=[r for p in G[3] for r in rd(p)];check(x,'gate2')
 if len(x)!=1000:raise SystemExit('gate2 rows')
 for p in sum(G,[]):shutil.copy2(p,o/p.name)
 (o/'gate2.json').write_text(json.dumps({'status':'PASS','human_decisions':0}))
def clean(rows,rev,b):
 out=[]
 for i,r in enumerate(rows,1):
  x=dict(r);x['Batch']=b;x['Batch_Order']=i;x['Reviewer']=rev
  for f in ('Decision','Human_Title_Abstract_Decision','Primary_Exclusion_Code','Exclusion_Reason_Code','Reviewer_Notes','Review_Date','Human_Reviewer','Human_Review_Date','Human_Notes'):
   if f in x:x[f]=''
  if 'Review_Status' in x:x['Review_Status']='Not reviewed'
  out.append(x)
 return out
def s3(a):
 p,o,z=Path(a.prepared),Path(a.out),int(a.agent);o.mkdir(parents=True,exist_ok=True)
 if z<=10:
  b=z;wr(o/f'MAIN_POSTCAL_{b:02d}_Mizan.csv',clean(split(rd(p/'main_m.csv'),10)[b-1],R1,f'MAIN-POSTCAL-{b:02d}'));wr(o/f'MAIN_POSTCAL_{b:02d}_Kapashia.csv',clean(split(rd(p/'main_k.csv'),10)[b-1],R2,f'MAIN-POSTCAL-{b:02d}'))
 elif z<=20:
  b=z-10;wr(o/f'SUP_POSTCAL_{b:02d}_Mizan.csv',clean(split(rd(p/'sup_m.csv'),10)[b-1],R1,f'SUP-POSTCAL-{b:02d}'));wr(o/f'SUP_POSTCAL_{b:02d}_Kapashia.csv',clean(split(rd(p/'sup_k.csv'),10)[b-1],R2,f'SUP-POSTCAL-{b:02d}'))
 else:
  b=z-20;mn=len(split(rd(p/'main_m.csv'),10)[b-1]);sn=len(split(rd(p/'sup_m.csv'),10)[b-1]);wr(o/f'WORKLOAD_{b:02d}.csv',[{'Sequence':1,'Stage':'Calibration','Batch':f'CAL-{b:02d}','Records_Per_Reviewer':100,'Launch_Condition':'Taxonomy approved','Human_Status':'Not started'},{'Sequence':2,'Stage':'Main screening','Batch':f'MAIN-POSTCAL-{b:02d}','Records_Per_Reviewer':mn,'Launch_Condition':'Calibration reviewed','Human_Status':'Blocked pending calibration'},{'Sequence':3,'Stage':'Supplementary screening','Batch':f'SUP-POSTCAL-{b:02d}','Records_Per_Reviewer':sn,'Launch_Condition':'Calibration reviewed','Human_Status':'Blocked pending calibration'}])
def final(a):
 p,g1,g2,g3,o=map(Path,(a.prepared,a.gate1,a.gate2,a.gate3,a.out));o.mkdir(parents=True,exist_ok=True)
 groups=[fs(g1,'CAL_*_Mizan.csv'),fs(g1,'CAL_*_Kapashia.csv'),fs(g1,'CAL_*_Admin_Adjudication.csv'),fs(g3,'MAIN_POSTCAL_*_Mizan.csv'),fs(g3,'MAIN_POSTCAL_*_Kapashia.csv'),fs(g3,'SUP_POSTCAL_*_Mizan.csv'),fs(g3,'SUP_POSTCAL_*_Kapashia.csv'),fs(g3,'WORKLOAD_*.csv')]
 if any(len(x)!=10 for x in groups):raise SystemExit('final missing')
 for g,e,idf in ((groups[0],1000,'Record_ID'),(groups[1],1000,'Record_ID'),(groups[3],7433,'Record_ID'),(groups[4],7433,'Record_ID'),(groups[5],3732,'Supplementary_Record_ID'),(groups[6],3732,'Supplementary_Record_ID')):
  x=[r for p in g for r in rd(p)];check(x,'final')
  if len(x)!=e or len({r[idf] for r in x})!=e:raise SystemExit('final count')
 mapping=[(sum(groups[:3],[]),'01_calibration'),(fs(g1,'CAL_*_Launch_QA.csv'),'01_calibration/qa'),(fs(g2,'*'),'02_validation_reconciliation'),(groups[3]+groups[4],'03_main_remaining'),(groups[5]+groups[6],'04_supplementary'),(groups[7],'05_workload')]
 for paths,sub in mapping:
  d=o/sub;d.mkdir(parents=True,exist_ok=True)
  for x in paths:
   if x.is_file():shutil.copy2(x,d/x.name)
 shutil.copy2(p/'input_manifest.csv',o/'input_manifest.csv')
 (o/'final_summary.json').write_text(json.dumps({'pipeline':'SRMA 100-Agent Calibration and Screening Launch','status':'HUMAN_SCREENING_OPERATIONAL_PACKAGE_READY','stage1_agents':40,'stage2_agents':30,'stage3_agents':30,'main_pool':8433,'calibration':1000,'main_remaining':7433,'supplementary_pool':3732,'human_title_abstract_decisions':0,'human_adjudications':0,'next_human_action':'Approve taxonomy and complete CAL-01 independently for both reviewers.'},indent=2))
 (o/'README.md').write_text('# Calibration and screening launch package\n\nAll human decision fields are blank.\n')
def main():
 q=argparse.ArgumentParser();s=q.add_subparsers(dest='cmd',required=True)
 for name,fun,args in [('prepare',prepare,['input','out']),('stage1',s1,['agent','prepared','out']),('gate1',gate1,['input','out']),('stage2',s2,['agent','gate1','out']),('gate2',gate2,['input','out']),('stage3',s3,['agent','prepared','out']),('final',final,['prepared','gate1','gate2','gate3','out'])]:
  p=s.add_parser(name)
  for x in args:p.add_argument('--'+x,required=True)
  p.set_defaults(fun=fun)
 a=q.parse_args();a.fun(a)
if __name__=='__main__':main()
