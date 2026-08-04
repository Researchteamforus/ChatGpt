from __future__ import annotations
import importlib.util, sys, time
from pathlib import Path
import numpy as np
from sklearn.datasets import load_diabetes
from sklearn.model_selection import train_test_split

BASE=Path(__file__).resolve().parents[1]
p=BASE/'analysis'/'reviewer10_final_analysis.py'
spec=importlib.util.spec_from_file_location('r10bench',p)
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)

X,y=load_diabetes(return_X_y=True)
dev,te=train_test_split(np.arange(len(y)),test_size=.2,random_state=m.GLOBAL_SEED)
tr,cal=train_test_split(dev,test_size=.18,random_state=m.GLOBAL_SEED)
data=m.scale_split(X[tr],y[tr],X[cal],y[cal],X[te],y[te])
rows=[]
std,tt_std=m.train_net(data.Xtr,data.ytr,hidden=(64,32),dual=False,dropout=0,epochs=80,lr=.001,batch=32,seed=1300000)
dual,tt_dual=m.train_net(data.Xtr,data.ytr,hidden=(64,32),dual=True,dropout=0,epochs=80,lr=.001,batch=32,seed=1300010)
mc,tt_mc=m.train_net(data.Xtr,data.ytr,hidden=(64,32),dual=True,dropout=.1,epochs=80,lr=.001,batch=32,seed=1300020)
ens=[];tt_ens=0.0
for j in range(5):
    mm,tt=m.train_net(data.Xtr,data.ytr,hidden=(64,32),dual=True,dropout=0,epochs=80,lr=.001,batch=32,seed=1300100+j)
    ens.append(mm);tt_ens+=tt
for _ in range(5):
    m.predict_single(std,data.Xte)
    m.predict_dual(dual,data.Xte)
    for _ in range(40):m.predict_dual(mc,data.Xte,mc=True)
    for mm in ens:m.predict_dual(mm,data.Xte)

def timed(fn,reps=100):
    vals=[]
    for _ in range(reps):
        t=time.perf_counter();fn();vals.append(time.perf_counter()-t)
    return float(np.mean(vals)),float(np.std(vals,ddof=1))

specs=[
 ('Standard ANN',1,m.parameter_count(std),tt_std,lambda:m.predict_single(std,data.Xte,mc=False)),
 ('Deterministic Dual-Head',1,m.parameter_count(dual),tt_dual,lambda:m.predict_dual(dual,data.Xte,mc=False)),
 ('MC Dropout p=0.1',1,m.parameter_count(mc),tt_mc,lambda:[m.predict_dual(mc,data.Xte,mc=True) for _ in range(40)]),
 ('Deep Ensemble',5,sum(m.parameter_count(mm) for mm in ens),tt_ens,lambda:[m.predict_dual(mm,data.Xte,mc=False) for mm in ens]),
]
for name,stored,params,train_time,fn in specs:
    mean,stddev=timed(fn,reps=100)
    rows.append({'model':name,'stored_networks':stored,'total_parameters':params,'parameter_memory_mib_float32':params*4/1024**2,'training_time_seconds':train_time,'forward_evaluations_per_prediction':40 if name.startswith('MC') else stored,'inference_time_seconds_mean_for_89_cases':mean,'inference_time_seconds_sd_for_89_cases':stddev,'cpu_threads':m.torch.get_num_threads()})
import pandas as pd
pd.DataFrame(rows).to_csv(BASE/'results'/'compute_benchmark.csv',index=False)
print(pd.DataFrame(rows).to_string(index=False))
