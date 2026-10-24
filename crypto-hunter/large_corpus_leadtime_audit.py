#!/usr/bin/env python3
"""Forensic check: were 11x chart peaks ACTUALLY observed after an investable lead time?
Do not relabel fast peaks as prospective signals. All data historical and read-only.
"""
import hashlib,json,os,urllib.request
from pathlib import Path
import numpy as np,pandas as pd
OUTPUT=Path("evidence/corpus_798k_leadtime");OUTPUT.mkdir(parents=True,exist_ok=True)
URI="https://huggingface.co/datasets/Slinky21/Pumpfun_Memecoin_Corpus/resolve/main/tokens.parquet"
SHA="c005d86d424013e5c78701161b025f3d8c3d472afb61466e0ad6fd5afe9e8ea6"
def main():
    file=OUTPUT/"tokens.parquet"
    if not file.exists():
        req=urllib.request.Request(URI,headers={"User-Agent":"CryptoHunterLeadTimeForensics/1.0"})
        with urllib.request.urlopen(req,timeout=100) as r,open(str(file)+".part","wb") as w:
            while chunk:=r.read(1<<20):w.write(chunk)
        os.replace(str(file)+".part",file)
    assert hashlib.sha256(file.read_bytes()).hexdigest()==SHA
    fields=["mint","detected_at","peak_market_cap_at","initial_market_cap_sol","peak_market_cap_sol",
            "top10_pct_suspect","is_mayhem_mode","initial_holder_count",
            "initial_buy_sol","dev_buy_pct_corrected"]
    f=pd.read_parquet(file,columns=fields)
    f["detected_at"]=pd.to_datetime(f.detected_at,utc=True,errors="coerce")
    f["peak_market_cap_at"]=pd.to_datetime(f.peak_market_cap_at,utc=True,errors="coerce")
    dt=(f.peak_market_cap_at-f.detected_at).dt.total_seconds()
    ratio=pd.to_numeric(f.peak_market_cap_sol,errors="coerce")/pd.to_numeric(f.initial_market_cap_sol,errors="coerce").replace(0,np.nan)
    usable=(dt.between(0,86400)&np.isfinite(ratio)&ratio.ge(0)&~f.top10_pct_suspect.fillna(True).astype(bool))
    f=f.loc[usable].copy()
    f["peak_x"]=ratio[usable]
    f["secs"]=dt[usable]
    f["day"]=f.detected_at.dt.strftime("%Y-%m-%d")
    extreme=f[f.peak_x>=11].copy()
    # COMPLETE named-by-mint census of eligible chart peaks. Dataset does not
    # contain a trustworthy human-readable name/symbol or after-peak path.
    # Mint is the unique public identifier; peak is NOT 11x executable profit.
    cols=["mint","detected_at","peak_market_cap_at","peak_x","secs",
          "initial_buy_sol","initial_holder_count","dev_buy_pct_corrected",
          "is_mayhem_mode"]
    extreme[cols].sort_values(["detected_at","mint"]).to_csv(
        OUTPUT/"all_11x_chart_mints.csv",index=False)
    buckets=[("0-5 seconds",0,5),("5-15 seconds",5,15),("15-60 seconds",15,60),
             ("1-5 minutes",60,300),("5-60 minutes",300,3600),
             ("1-24 hours",3600,86400.1)]
    hist={k:int(extreme.secs.ge(start).mul(extreme.secs.lt(end)).sum()) for k,start,end in buckets}
    report={
      "source_sha":SHA,"eligible":len(f),"chart_11x_total":len(extreme),
      "all_11x_chart_mints_csv":"all_11x_chart_mints.csv",
      "mint_ids_not_verified_symbols":True,
      "post_peak_price_series_in_this_table":False,
      "post_peak_crash_latency_known_from_this_table":False,
      "peak_after_1min":int((extreme.secs>=60).sum()),
      "peak_after_5min":int((extreme.secs>=300).sum()),
      "peak_after_1h":int((extreme.secs>=3600).sum()),
      "earliest_to_peak_buckets":hist,
      "rates_by_day":{},
      "source_changepoint_warning":"Collection and mayhem-mode regimes changed during June/July; do not pool blindly",
      "simple_peak_timing_not_first_crossing":"Peak at >=5min does not prove the first 11x threshold was after 5min",
      "no_fill_proven":True,"wallets_connected":False,"trades":0}
    for day,sub in f.groupby("day"):
        winners=sub[sub.peak_x>=11]
        report["rates_by_day"][day]={"eligible":len(sub),
         "chart_11x":len(winners),
         "chart_11x_rate":round(len(winners)/len(sub),7),
         "11x_peaks_after_5min":int((winners.secs>=300).sum())}
    for cohort,subset in [("train_early",f[f.detected_at<"2026-07-01T15:56:28Z"]),
                          ("holdout_late",f[f.detected_at>="2026-07-01T15:56:28Z"])]:
        winners=subset[subset.peak_x>=11]
        report[cohort]={"eligible":len(subset),"chart_11x":len(winners),
            "chart_11x_rate":round(len(winners)/len(subset),7),
            "winners_peak_0to60sec":int((winners.secs<60).sum()),
            "winners_peak_5min_plus":int((winners.secs>=300).sum())}
    (OUTPUT/"lead_time_truth.json").write_text(json.dumps(report,indent=2,allow_nan=False))
    print("CORPUS_CHART_11X_LEAD_TIME_FORENSICS",json.dumps({k:v for k,v in report.items()
        if k not in ("rates_by_day",)},allow_nan=False))
    print("CORPUS_DAILY_RATE",json.dumps(report["rates_by_day"],allow_nan=False))
    print("NO_1000_PERCENT_EXECUTABLE_TRADES_CLAIM=TRUE")
if __name__=="__main__":main()
