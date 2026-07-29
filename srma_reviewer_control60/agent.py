from __future__ import annotations
import argparse,csv,json,hashlib
from pathlib import Path

def files(root:Path): return sorted(p for p in root.rglob('*') if p.is_file())
def sha(p:Path):
 h=hashlib.sha256();
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1<<20),b''): h.update(b)
 return h.hexdigest()
def rows(p:Path):
 try:
  with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
 except Exception:return []
def write_csv(p,rs,fields):
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rs)
def prepare(inp,out):
 out.mkdir(parents=True,exist_ok=True)
 fs=files(inp); manifest=[{'path':str(p.relative_to(inp)),'bytes':p.stat().st_size,'sha256':sha(p)} for p in fs]
 write_csv(out/'source_manifest.csv',manifest,['path','bytes','sha256'])
 csvs=[p for p in fs if p.suffix.lower()=='.csv']; allrows=[]
 for p in csvs:
  for r in rows(p):
   rid=r.get('record_id') or r.get('id') or r.get('study_id') or r.get('master_record_id')
   if rid: allrows.append({'record_id':rid,'source_file':str(p.relative_to(inp))})
 seen=set(); uniq=[]
 for r in allrows:
  if r['record_id'] not in seen: seen.add(r['record_id']);uniq.append(r)
 write_csv(out/'record_index.csv',uniq,['record_id','source_file'])
 (out/'summary.json').write_text(json.dumps({'files':len(fs),'indexed_records':len(uniq),'human_decisions':0},indent=2),encoding='utf-8')
def agent(kind,n,inp,out):
 out.mkdir(parents=True,exist_ok=True); idx=rows(inp/'record_index.csv'); shard=[r for i,r in enumerate(idx) if i%10==(n-1)%10]
 if kind=='calibration':
  rs=[{'record_id':r['record_id'],'reviewer_1_decision':'','reviewer_2_decision':'','agreement_status':'PENDING','kappa_input_ready':'NO'} for r in shard]
  write_csv(out/f'calibration_{n}.csv',rs,['record_id','reviewer_1_decision','reviewer_2_decision','agreement_status','kappa_input_ready'])
 elif kind=='workload':
  rs=[{'record_id':r['record_id'],'assigned_reviewer':'','batch_status':'NOT_STARTED','decision_blank':'YES'} for r in shard]
  write_csv(out/f'workload_{n}.csv',rs,['record_id','assigned_reviewer','batch_status','decision_blank'])
 elif kind=='schema':
  write_csv(out/f'decision_schema_{n}.csv',[{'field':x,'required':'YES' if x in ('record_id','decision') else 'NO','allowed_values':v} for x,v in [('record_id','unique stable id'),('decision','Include|Exclude|Unclear'),('reason_code','protocol-approved code'),('reviewer','Md. Mizanoor Rahman|Kapashia Binte Giash'),('timestamp','ISO-8601')]],['field','required','allowed_values'])
 elif kind=='adjudication':
  rs=[{'record_id':r['record_id'],'reviewer_1_decision':'','reviewer_2_decision':'','conflict':'PENDING','adjudicated_decision':'','adjudicator_note':''} for r in shard]
  write_csv(out/f'adjudication_{n}.csv',rs,['record_id','reviewer_1_decision','reviewer_2_decision','conflict','adjudicated_decision','adjudicator_note'])
 else:
  rs=[{'record_id':r['record_id'],'title_abstract_status':'PENDING','fulltext_required':'UNKNOWN','pdf_status':'UNKNOWN','fulltext_decision':''} for r in shard]
  write_csv(out/f'fulltext_schedule_{n}.csv',rs,['record_id','title_abstract_status','fulltext_required','pdf_status','fulltext_decision'])
 (out/'summary.json').write_text(json.dumps({'kind':kind,'agent':n,'rows':len(shard),'human_decisions':0},indent=2),encoding='utf-8')
def consolidate(inp,out):
 out.mkdir(parents=True,exist_ok=True); fs=files(inp); man=[{'path':str(p.relative_to(inp)),'bytes':p.stat().st_size,'sha256':sha(p)} for p in fs]
 write_csv(out/'artifact_manifest.csv',man,['path','bytes','sha256'])
 for pattern,name in [('calibration_*.csv','calibration_control_master.csv'),('workload_*.csv','reviewer_workload_master.csv'),('decision_schema_*.csv','decision_schema_master.csv'),('adjudication_*.csv','adjudication_master.csv'),('fulltext_schedule_*.csv','fulltext_schedule_master.csv')]:
  rs=[]
  for p in inp.rglob(pattern):rs+=rows(p)
  if rs: write_csv(out/name,rs,list(rs[0]))
 (out/'summary.json').write_text(json.dumps({'agents':60,'artifact_files':len(fs),'human_screening_decisions':0,'fulltext_decisions':0},indent=2),encoding='utf-8')
 (out/'README.md').write_text('Reviewer-control operational package. All human decision fields are blank.\n',encoding='utf-8')
def main():
 ap=argparse.ArgumentParser();ap.add_argument('cmd');ap.add_argument('--agent',type=int,default=1);ap.add_argument('--input',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
 if a.cmd=='prepare':prepare(a.input,a.out)
 elif a.cmd=='consolidate':consolidate(a.input,a.out)
 else:agent(a.cmd.replace('-agent',''),a.agent,a.input,a.out)
if __name__=='__main__':main()
