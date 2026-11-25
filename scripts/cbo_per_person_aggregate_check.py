import logging, pandas as pd
from pathlib import Path
from tax_modeler.calibration.cbo_aging import load_cbo_rates, age_filers_with_components
logging.disable(logging.CRITICAL)
R=Path('data/tax_modeler/cbo')
df=pd.read_parquet(R.parent.parent/'artifacts/tax_units_cache.parquet')
print(len(df), float(df.weight.sum()))
base=float((df.income*df.weight).sum())/1e6
print('base income M',round(base))
live=load_cbo_rates(csv_path=R/'cbo_components_2025-01-cg2026-02.csv')
raw=load_cbo_rates(csv_path=R/'cbo_components_2026-02.csv')
pp=load_cbo_rates(csv_path=R/'cbo_components_2026-02-per-person.csv')
# one-component-at-a-time swap from live -> raw / per-person
import dataclasses
def swap(src,comp):
    f=dict(live.factors); f[comp]=src.factors[comp]
    if comp=='proprietors': f['business']=src.factors['business']
    if comp=='business': f['proprietors']=src.factors['proprietors']
    return dataclasses.replace(live,factors=f)
print(list(live.factors)[:3])
def tot(r,y):
    a=age_filers_with_components(df,y,cbo_rates=r); return float((a.income*a.weight).sum())/1e6
for y in (2025,2027):
    L=tot(live,y); print(y,'live',round(L),'raw',round(tot(raw,y)/L*100-100,2),'%','pp',round(tot(pp,y)/L*100-100,2),'%')
    for c in ['wages','interest','dividends','proprietors','business','retirement','other']:
        out=[]
        for nm,src in (('raw',raw),('pp',pp)):
            out.append((nm,round(tot(swap(src,c),y)/L*100-100,2)))
        print('  ',c,out)
