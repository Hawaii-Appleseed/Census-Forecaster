import pandas as pd, numpy as np
d=pd.read_pickle(f'/tmp/pufwork/ecps_units_{__import__("sys").argv[1]}.pkl')
def tail(df,x,t):
    return df.w[df.agi>=t].sum()
out={}
print("ECPS weighted returns (units, agi>0): total %.1fM"%(d.w[d.agi>0].sum()/1e6))
for t in (200e3,500e3,1e6,5e6,10e6):
    m=d.agi>=t; print(f"  >=${t/1e6:g}M: N={d.w[m].sum():,.0f} unweighted={m.sum()} clone_share_w={(d.w*d.clone)[m].sum()/d.w[m].sum():.2f} meanAGI={np.average(d.agi[m],weights=d.w[m]):,.0f}")
print("\nPareto alpha")
def hill(t):
    m=d.agi>=t; x=d.agi[m].values; w=d.w[m].values
    return 1/(np.sum(w*np.log(x/t))/w.sum())
def mom(t):
    m=d.agi>=t; mean=np.average(d.agi[m],weights=d.w[m]); return mean/(mean-t)
def slope(a,b):
    return np.log(tail(d,0,a)/tail(d,0,b))/np.log(b/a)
for t in (200e3,500e3,1e6): print(f"  >=${t/1e6:g}M Hill={hill(t):.2f} mean-ratio={mom(t):.2f}")
for a,b in ((200e3,500e3),(500e3,1e6),(1e6,2e6),(1e6,5e6),(2e6,10e6)): print(f"  slope N(>{a/1e6:g}M)->N(>{b/1e6:g}M): {slope(a,b):.2f}")
comps=['wages','passthru','capgain','divs','interest','retire','other','misc']
print("\nShares of AGI-proxy (aggregate, weighted), by class")
bins=[(200e3,500e3),(500e3,1e6),(1e6,5e6),(5e6,1e12),(1e6,1e12),(200e3,1e12),(500e3,1e12)]
for lo,hi in bins:
    m=(d.agi>=lo)&(d.agi<hi); A=(d.w*d.agi)[m].sum()
    s={c:(d.w*d[c])[m].sum()/A for c in comps}
    it={c:(d.w*d[c])[m].sum()/A for c in ['mort','ret','char','invint','med']}
    print(f"[{lo/1e6:g}M,{hi/1e6 if hi<1e11 else 'inf'}) N={d.w[m].sum():,.0f}", {k:round(v,3) for k,v in s.items()}, "ITEM",{k:round(v,3) for k,v in it.items()})
# CG incidence
for lo in (200e3,500e3,1e6):
    m=d.agi>=lo; print(f"share of units >={lo/1e6:g}M with CG>0: {(d.w*(d.capgain>0))[m].sum()/d.w[m].sum():.2f}; with passthru>0: {(d.w*(d.passthru>0))[m].sum()/d.w[m].sum():.2f}")
print('\nmisc income aggregate above 1M', (d.w*d.misc)[d.agi>=1e6].sum()/1e9,'B; all', (d.w*d.misc).sum()/1e9)
