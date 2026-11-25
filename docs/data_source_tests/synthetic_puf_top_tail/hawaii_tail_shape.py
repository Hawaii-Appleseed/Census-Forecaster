import pandas as pd, numpy as np
u=pd.read_parquet('/tmp/pufwork/hi_units.parquet')
syn=u.is_synthetic_ultra_high.fillna(False).astype(bool)
s=u[syn]
pd.set_option('display.width',300); pd.set_option('display.float_format','{:,.3f}'.format)
print(s[['weight','agi','synthetic_cg_share','primary_wagp','primary_intp','investment_income','hi_itemized_deduction','total_deductions','standard_deduction','hi_standard_deduction','hi_taxable_income']].iloc[::4])
print('NaN total_deductions among synth', s.total_deductions.isna().sum(), 'among all', u.total_deductions.isna().sum())
w=u.weight
def stats(m,label):
    ww=w[m]; a=u.agi[m]
    wage=(u.primary_wagp+u.secondary_wagp.fillna(0))[m]
    semp=(u.primary_semp+u.secondary_semp.fillna(0))[m]
    intr=(u.primary_intp+u.secondary_intp.fillna(0))[m]
    div=(u.primary_div+u.secondary_div.fillna(0))[m]
    ret=(u.primary_retp+u.secondary_retp.fillna(0))[m]
    A=(ww*a).sum()
    print(label,'N=%.0f'%ww.sum(),'meanAGI=%.0f'%(A/ww.sum()),'wages %.3f semp %.3f intp %.3f div %.3f retp %.3f'%tuple((ww*x).sum()/A for x in (wage,semp,intr,div,ret)),'item/agi %.3f'%((ww*u.hi_itemized_deduction.fillna(0)[m]).sum()/A))
for lo in (2e5,5e5,1e6):
    stats(u.agi>=lo,f'>= {lo/1e6:g}M all')
stats((u.agi>=2e5)&(u.agi<5e5),'200-500K'); stats((u.agi>=5e5)&(u.agi<1e6),'500K-1M')
stats(syn,'synthetic >=1M')
for lo,hi in ((2e5,5e5),(5e5,1e6),(1e6,1e13)):
    m=(u.agi>=lo)&(u.agi<hi); print(lo,'itemizers share w',(w*(u.hi_itemized_deduction.fillna(0)>u.hi_standard_deduction.fillna(0)))[m].sum()/w[m].sum())
# tail index
def N(t): return w[u.agi>=t].sum()
for a,b in ((2e5,5e5),(5e5,1e6),(1e6,2e6),(1e6,5e6),(2e6,10e6)): print('slope',a,b,np.log(N(a)/N(b))/np.log(b/a), N(a),N(b))
for t in (1e6,):
    m=u.agi>=t; x=u.agi[m]; ww=w[m]; print('Hill',1/((ww*np.log(x/t)).sum()/ww.sum()))
for t in (2e5,5e5):
    m=u.agi>=t; x=u.agi[m]; ww=w[m]; print('Hill',t,1/((ww*np.log(x/t)).sum()/ww.sum()))
