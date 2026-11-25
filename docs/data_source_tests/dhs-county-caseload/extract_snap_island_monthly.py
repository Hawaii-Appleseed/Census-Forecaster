import sys, csv, openpyxl, xlrd, re
rows_out=[]
areamap={'OAHU TOTALS':'Honolulu','HAWAII TOTALS':'Hawaii','KAUAI TOTALS':'Kauai','MAUI (ISLAND) TOTALS':'Maui Island','MOLOKAI TOTALS':'Molokai','LANAI TOTALS':'Lanai','MAUI COUNTY TOTALS':'Maui County (incl Molokai, Lanai)','STATE TOTALS':'State'}
mon={m:i+1 for i,m in enumerate('JUL AUG SEP OCT NOV DEC JAN FEB MAR APR MAY JUN'.split())}
def sfy_year(sfy,m): return sfy-1 if m>=7 else sfy
# xls
for sfy,path in [(2025,'SFY-2025-SUMMARY.xls'),(2027,'SFY-2027-SUMMARY-1.xls')]:
    wb=xlrd.open_workbook('raw/'+path)
    for sn,area in areamap.items():
        ws=wb.sheet_by_name(sn); tab=None
        for i in range(ws.nrows):
            r=ws.row_values(i); c=str(r[0]).strip()
            if c=='NUMBER OF PERSONS': tab='persons'
            elif c=='NUMBER OF HOUSEHOLDS': tab='households'
            elif c=='COUPON ISSUANCE': tab='issuance_usd'
            elif tab and c[:3].upper() in ('JUL','AUG','SEP','OCT','NOV','DEC','JAN','FEB','MAR','APR','MAY','JUN') and len(c)<=4:
                m={'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12,'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6}[c[:3].upper()]
                if r[7]: rows_out.append((f'{sfy_year(sfy,m)}-{m:02d}',area,tab,int(r[7])))
# xlsx SFY2026
wb=openpyxl.load_workbook('raw/Partic-SFY-2026-WCAG.xlsx',data_only=True)
am={'Oahu Branch':'Honolulu','Hawaii Branch':'Hawaii','Kauai Branch':'Kauai','Maui Island':'Maui Island','Molokai Island':'Molokai','Lanai Island':'Lanai','Maui Branch':'Maui County (incl Molokai, Lanai)','STATE':'State'}
for ws in wb:
    mm=re.match(r'(JUL|AUG|SEP|OCT|NOV|DEC|JAN|FEB|MAR|APR|MAY|JUN) (\d{4})',ws.title)
    if not mm: continue
    ym=f"{mm[2]}-{mon[mm[1]] if False else {'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12,'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6}[mm[1]]:02d}"
    tab=None
    for r in ws.iter_rows(values_only=True):
        c=str(r[0]) if r[0] else ''
        if c.startswith('Number of Participants Receiving'): tab='persons'
        elif c.startswith('Number of Households Receiving'): tab='households'
        elif c.startswith('SNAP Benefits Issued, by Island'): tab='issuance_usd'
        elif c.startswith('Section 2'): tab=None
        elif tab and c in am and r[7]: rows_out.append((ym,am[c],tab,int(r[7])))
rows_out=sorted(set(rows_out))
w=csv.writer(open(sys.argv[1],'w')); w.writerow(['month','area','measure','value']); w.writerows(rows_out)
print(len(rows_out))
