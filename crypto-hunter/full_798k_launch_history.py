#!/usr/bin/env python3
"""798,430 historical pump.fun token launches: reconstruct chart-only peak multiples,
with 24h timestamp bounds and post-graduation outcomes. Exclude known bad fields.
Do not use peak, migration, later trades or wallet lifetime statistics as features.
Does NOT demonstrate executable profits and never places orders.
"""
import hashlib,os,json,math,urllib.request
from pathlib import Path
import pandas as pd,numpy as np
from sklearn.impute import SimpleImputer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score,roc_auc_score
OUT=Path("evidence/corpus_798k");OUT.mkdir(parents=True,exist_ok=True)
PREFIX="https://huggingface.co/datasets/Slinky21/Pumpfun_Memecoin_Corpus/resolve/main/"
HASHES={
 "tokens.parquet":"c005d86d424013e5c78701161b025f3d8c3d472afb61466e0ad6fd5afe9e8ea6",
 "postgard_outcomes.parquet":"00a9980cb97630e9ea89574742f0e134d448e5566c5d7bda461f59bb02de022b"}
# Only known at discovery, supply-corrected, not derived from future trading.
BASELINE=[
 "initial_buy_sol","initial_market_cap_sol","initial_holder_count",
 "initial_top1_pct_corrected","initial_top5_pct_corrected",
 "initial_top10_pct_corrected","dev_buy_pct_corrected",
 "initial_gini","is_mayhem_mode"]
HINDSIGHT=("peak_market_cap","graduated","seconds_to_graduation","rug","trade_count",
"data_quality_score","is_zombie","is_training_ready","entry_price",
"launch_snipe","creator_past","first_trade_price_sol")
assert all(not any(frag in f for frag in HINDSIGHT) for f in BASELINE)
def load(name,limit):
    p=OUT/name
    if not p.exists():
        req=urllib.request.Request(PREFIX+name,headers={"User-Agent":"CryptoHunter786kEarlyOutcomes/1.0"})
        with urllib.request.urlopen(req,timeout=90) as r,open(str(p)+".part","wb") as w:
            n=0
            while chunk:=r.read(1<<20):
                n+=len(chunk)
                if n>limit:raise ValueError("Source file over allowed byte count")
                w.write(chunk)
        os.replace(str(p)+".part",p)
    sha=hashlib.sha256(p.read_bytes()).hexdigest()
    if sha!=HASHES[name]:raise ValueError("Dataset changed unexpectedly: "+name)
    return p
def count_sizes(x):
    return {str(k):int((x>=k).sum()) for k in (2,5,11,21,51)}
def medians(df,y,cols):
    result={}
    for c in cols:
        a=pd.to_numeric(df.loc[y,c],errors="coerce").dropna()
        b=pd.to_numeric(df.loc[~y,c],errors="coerce").dropna()
        if len(a)<20 or len(b)<100:continue
        v,u=float(a.median()),float(b.median())
        result[c]={"chart_winner_median":round(v,6),"control_median":round(u,6),
                   "ratio":round(v/u,3) if abs(u)>1e-12 else None,
                   "winners":len(a),"controls":len(b)}
    return result
def main():
    launches=pd.read_parquet(load("tokens.parquet",260000000))
    graduated=pd.read_parquet(load("postgard_outcomes.parquet",4000000))
    known_quality=~launches["top10_pct_suspect"].fillna(True).astype(bool)
    initial=pd.to_numeric(launches["initial_market_cap_sol"],errors="coerce")
    top=pd.to_numeric(launches["peak_market_cap_sol"],errors="coerce")
    created=pd.to_datetime(launches["detected_at"],utc=True,errors="coerce")
    top_at=pd.to_datetime(launches["peak_market_cap_at"],utc=True,errors="coerce")
    delta=(top_at-created).dt.total_seconds()
    horizon24=delta.ge(0)&delta.le(86400)
    present=initial.gt(0)&top.gt(0)&created.notna()&top_at.notna()
    eligible=present&horizon24&known_quality
    years=created.dt.strftime("%Y-%m-%d").value_counts().sort_index()
    ratio=(top/initial.replace(0,np.nan)).where(eligible)
    table=launches.loc[eligible,["mint","detected_at","graduated_at"]+BASELINE].copy()
    table["chart_peak_x"]=(top[eligible]/initial[eligible]).to_numpy()
    table=table.dropna(subset=["detected_at","chart_peak_x","mint"])
    table=table.sort_values("detected_at",kind="stable").drop_duplicates("mint",keep="first")
    table["target_chart_11x"]=(table["chart_peak_x"]>=11).astype(int)
    table["graduated"]=table["graduated_at"].notna().astype(int)
    # Separate graded post-migration cohort: timestamp-bounded after graduation.
    go=graduated.copy()
    go["mint"]=go["mint"].astype(str)
    tgrad=pd.to_datetime(launches[["mint","graduated_at"]].drop_duplicates("mint").set_index("mint")["graduated_at"],utc=True,errors="coerce")
    go["graduated_at"]=go["mint"].map(tgrad)
    go["peak_price_at"]=pd.to_datetime(go["peak_price_at"],utc=True,errors="coerce")
    go["peak_age_hours"]=(go["peak_price_at"]-go["graduated_at"]).dt.total_seconds()/3600
    go["ratio_peak_vs_grad"]=pd.to_numeric(go["peak_price_usd"],errors="coerce")/pd.to_numeric(go["price_at_grad_usd"],errors="coerce").replace(0,np.nan)
    post_valid=go["peak_age_hours"].between(0,24,inclusive="both")&np.isfinite(go["ratio_peak_vs_grad"])&go["ratio_peak_vs_grad"].ge(0)
    post=go.loc[post_valid]
    post_still=post[post["still_liquid_at_24h"]==True]
    report={"source":"Slinky21/Pumpfun_Memecoin_Corpus",
      "file_sha256":HASHES,"total_launches":len(launches),"distinct_launch_mints":int(launches.mint.nunique()),
      "source_date_min":str(created.min()),"source_date_max":str(created.max()),
      "known_bad_holder_rows":int((~known_quality).sum()),
      "peak_at_missing_or_initial_missing":int((~present).sum()),
      "peak_after_24h_or_before_detection":int((present & ~horizon24).sum()),
      "eligible_quality_filtered_timestamped_24h_chart_peaks":len(table),
      "chart_peak_counts":count_sizes(table.chart_peak_x),
      "graduated_in_eligible":int(table.graduated.sum()),
      "label":"peak market cap / initial market cap in SOL for observed peak within 0..24h after launch detection; NEVER executable trade",
      "postgraduation_outcomes_rows":len(go),
      "postgrad_valid_peak_inside_24h":len(post),
      "postgrad_peak_counts":count_sizes(post.ratio_peak_vs_grad),
      "postgrad_with_liquidity_at_24h":len(post_still),
      "postgrad_liquid24h_peak_counts":count_sizes(post_still.ratio_peak_vs_grad),
      "postgrad_liquid_24h_does_not_verify_peak_fill":True,
      "raw_launch_day_counts_minmax":{"min":int(years.min()),"max":int(years.max()),"days":len(years)},
      "launch_ex_ante_features":BASELINE,
      "exclusions":["top10_pct_suspect","post-detection trade_count","peak-market-cap as predictor",
         "graduated_at as predictor","later wallet_stats","creator_past_tokens without timestamp proof"],
      "not_all_blockchains":True,"no_trades_or_wallet_access":True}
    cut=int(.7*len(table))
    train=table.iloc[:cut]
    test=table.iloc[cut:]
    tr=train.target_chart_11x.to_numpy(dtype=int)
    te=test.target_chart_11x.to_numpy(dtype=int)
    report["train_sample"]={"rows":len(train),"chart_ge11":int(tr.sum()),
       "date_min":str(train.detected_at.iloc[0]),"date_max":str(train.detected_at.iloc[-1])}
    report["test_sample"]={"rows":len(test),"chart_ge11":int(te.sum()),
       "date_min":str(test.detected_at.iloc[0]),"date_max":str(test.detected_at.iloc[-1])}
    report["train_only_feature_contrasts"]=medians(train,tr==1,BASELINE)
    if tr.sum()>=25 and te.sum()>=15:
        X=train[BASELINE].astype(float).replace([np.inf,-np.inf],np.nan)
        Z=test[BASELINE].astype(float).replace([np.inf,-np.inf],np.nan)
        imp=SimpleImputer(strategy="median",keep_empty_features=True)
        x=imp.fit_transform(X);z=imp.transform(Z)
        clf=HistGradientBoostingClassifier(max_iter=75,max_leaf_nodes=14,
            min_samples_leaf=180,learning_rate=.055,l2_regularization=7,
            class_weight="balanced",random_state=19)
        clf.fit(x,tr)
        prob=clf.predict_proba(z)[:,1]
        idx=np.argsort(-prob,kind="stable")
        score={"auc":round(float(roc_auc_score(te,prob)),5),
               "average_precision":round(float(average_precision_score(te,prob)),5),
               "holdout_base":round(float(te.mean()),6)}
        for part in (.001,.005,.01,.02,.05):
            subset=idx[:max(1,int(math.ceil(len(te)*part)))]
            hits=int(te[subset].sum())
            precision=hits/len(subset)
            score[f"top_{part*100:.1f}pct"]={"n":len(subset),"hits":hits,
                "precision":round(precision,6),
                "lift":round(precision/te.mean(),3) if te.mean() else None,
                "recall":round(hits/te.sum(),5)}
        report["out_of_time_holdout"]=score
    else:
        report["out_of_time_holdout"]={"model_not_trained":"too few timestamped 11x chart events in train or future test"}
    report["caveats"]=[
     "Source is pump.fun only, 39-day Jun-Jul 2026; launch-to-peak market cap chart estimates, not trade fills.",
     "Missing or after-24h peak stamps excluded from denominator, not treated as losers; selection bias remains.",
     "Original peak market-cap fields are metadata aggregates and may have outliers / regime breaks.",
     "Mayhem-mode supply change, RPC holder cap, and program outages have documented temporal breaks.",
     "Postgraduation outcome peak and liquidity24h are distinct timestamps; neither proves exit at the peak.",
     "No buy/sell router, MEV, gas, slippage or priority fee data. Zero executed trades."]
    (OUT/"large_launch_cohort_report.json").write_text(json.dumps(report,indent=2,allow_nan=False,default=str))
    subset={k:v for k,v in report.items() if k in (
      "total_launches","distinct_launch_mints","source_date_min","source_date_max",
      "known_bad_holder_rows","peak_at_missing_or_initial_missing",
      "peak_after_24h_or_before_detection","eligible_quality_filtered_timestamped_24h_chart_peaks",
      "chart_peak_counts","postgraduation_outcomes_rows","postgrad_valid_peak_inside_24h",
      "postgrad_peak_counts","postgrad_with_liquidity_at_24h",
      "postgrad_liquid24h_peak_counts","train_sample","test_sample",
      "train_only_feature_contrasts","out_of_time_holdout")}
    print("CORPUS_798K_RESULT",json.dumps(subset,allow_nan=False,default=str))
    print("CORPUS_NO_EXECUTED_PROFITS=True")
if __name__=="__main__":main()
