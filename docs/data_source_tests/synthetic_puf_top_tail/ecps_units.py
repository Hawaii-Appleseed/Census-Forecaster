import h5py, numpy as np, pandas as pd, json
f=h5py.File('/tmp/pufwork/dl2/enhanced_cps_2024.h5')
g=lambda k: np.asarray(f[k]['2024'])
pt=g('person_tax_unit_id'); tu=g('tax_unit_id'); n=len(tu)
idx=pd.Series(np.arange(n),index=tu)
pos=idx.loc[pt].values
hh_id=g('household_id'); hw=g('household_weight'); phh=g('person_household_id')
import sys
MODE=sys.argv[1]
H=len(hw);h=H//2
if MODE=='B':
    hw=hw.copy(); hw[h:]=hw[:h]; hw[:h]=0
hwmap=pd.Series(hw,index=hh_id)
# tax unit weight = household weight of first member
first=np.full(n,-1); 
for i in range(len(pt)-1,-1,-1): first[pos[i]]=i
w=hwmap.loc[phh[first]].values.astype(float)
def S(*ks):
    out=np.zeros(n)
    for k in ks: out+=np.bincount(pos,weights=g(k).astype(float),minlength=n)
    return out
d=pd.DataFrame({'w':w,'clone':g('tax_unit_is_puf_clone')})
d['wages']=S('employment_income_before_lsr')
d['passthru']=S('partnership_s_corp_income','self_employment_income_before_lsr','farm_income','rental_income','estate_income','farm_rent_income','farm_operations_income')
d['capgain']=S('long_term_capital_gains_before_response','short_term_capital_gains','non_sch_d_capital_gains')
d['divs']=S('qualified_dividend_income','non_qualified_dividend_income')
d['interest']=S('taxable_interest_income')
d['retire']=S('taxable_private_pension_income','taxable_401k_distributions','taxable_403b_distributions','taxable_ira_distributions','taxable_sep_distributions','keogh_distributions')
d['misc']=S('miscellaneous_income')
d['other']=S('unemployment_compensation','alimony_income','salt_refund_income')
d['agi']=d[['wages','passthru','capgain','divs','interest','retire','other']].sum(axis=1)
d['mort']=S('home_mortgage_interest'); d['ret']=S('real_estate_taxes')
d['char']=S('charitable_cash_donations','charitable_non_cash_donations'); d['invint']=S('investment_interest_expense')
d['med']=S('other_medical_expenses')
d.to_pickle(f'/tmp/pufwork/ecps_units_{MODE}.pkl')
print(n, d.w.sum()/1e6, (d.w*(d.agi>0)).sum()/1e6)
