"""Stream a SIPP public-use person-month CSV (zipped) and keep only the columns the take-up test needs.

Usage: python -I analyses/sipp_extract.py <pu2023_csv.zip> <out.parquet>
Never loads the whole file: reads the zip member in 200k-row chunks. Exploratory; not on the chain.
"""
import sys
import zipfile

import pandas as pd

COLS = ["SSUID", "PNUM", "SPANEL", "SWAVE", "MONTHCODE", "WPFINWGT", "TEHC_ST", "TST_INTV", "TAGE", "ESEX",
        "EEDUC", "ERACE", "EORIGIN", "EMS", "ECITIZEN", "EDISABL", "ETENURE", "RHNUMPER", "RHNUMU18",
        "RHNUM65OVER", "EPNSPOUSE", "EPNPAR1", "EPNPAR2", "RMESR", "TJB1_MWKHRS", "TJB2_MWKHRS", "TJB3_MWKHRS",
        "TPEARN", "THTOTINC", "THINCPOV", "RSNAP_MNYN", "RPUBTYPE2", "EEITC", "AEITC", "EFSTATUS", "EFILING",
        "EDEPCLM", "RSNAP_YRYN", "RMCAIDANN", "TSNAP_AMT"]


def main(zpath: str, out: str) -> None:
    parts = []
    with zipfile.ZipFile(zpath) as z, z.open(z.namelist()[0]) as f:
        for ch in pd.read_csv(f, sep="|", usecols=COLS, chunksize=200_000, low_memory=False):
            num = [c for c in ch.columns if c != "SSUID"]
            ch[num] = ch[num].apply(pd.to_numeric, errors="coerce").astype("float32")
            parts.append(ch)
    df = pd.concat(parts, ignore_index=True)
    df.to_parquet(out)
    print(df.shape)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
