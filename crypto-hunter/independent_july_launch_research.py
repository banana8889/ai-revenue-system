#!/usr/bin/env python3
"""Independent July 2026 pump.fun launch study; not a 24h or executable-return study.
Reconstruct a *post-30-second observed sparse chart path* only from time-stamped
outcome.path points. Keep null and censored outcomes explicit. No trading.
"""
import gzip,hashlib,json,math,os,urllib.request
from datetime import datetime,timezone
from pathlib import Path
from collections import Counter
DATA=Path("evidence/historical_july");DATA.mkdir(parents=True,exist_ok=True)
SOURCE="https://raw.githubusercontent.com/mivor7/pumpfun-pumpswap-market-data/main/launches/launches_dataset.jsonl.gz"
SHA256="4608b5891174cb2d2bea1effaf139f86f0f2a01a57c37859a1891e8f642299dc"
FEATURES=("n_trades","buys_sol","sells_sol","net_sol","uniq_buyers","uniq_sellers",
          "dev_share","top_buyer_share","price","ret_from_first")
def val(x):
    try:
        n=float(x)
        return n if math.isfinite(n) else None
    except (ValueError,TypeError):return None
def get_data():
    p=DATA/"independent_july_launches.jsonl.gz"
    if not p.exists():
        req=urllib.request.Request(SOURCE,headers={"User-Agent":"CryptoResearchIndependentJuly/1.0"})
        with urllib.request.urlopen(req,timeout=80) as r,open(str(p)+".part","wb") as w:
            total=0
            while chunk:=r.read(1048576):
                total+=len(chunk)
                if total>35000000:raise ValueError("Source over 35MB")
                w.write(chunk)
        os.replace(str(p)+".part",p)
    actual=hashlib.sha256(p.read_bytes()).hexdigest()
    if actual!=SHA256:raise ValueError("Source changed: SHA mismatch")
    return p

def main():
    src=get_data()
    samples=[]
    stats=Counter()
    examples=[]
    for raw in gzip.open(src,"rt",encoding="utf-8"):
        try:o=json.loads(raw)
        except ValueError:
            stats["corrupt_rows"]+=1
            continue
        stats["rows"]+=1
        if len(examples)<2 and o.get("outcome") and o["outcome"].get("path"):
            examples.append({"created_ts":o.get("created_ts"),"outcome_keys":list(o["outcome"]),
                             "path_first":o["outcome"].get("path",[])[:5]})
        snap=o.get("snap_30")
        if not isinstance(snap,dict) or val(snap.get("price")) in (None,0):
            stats["no_valid_30s_baseline"]+=1
            continue
        stats["valid_30s_snapshot"]+=1
        outcome=o.get("outcome")
        if not isinstance(outcome,dict):
            stats["no_later_outcome"]+=1
            continue
        path=outcome.get("path")
        if not isinstance(path,list) or not path:
            stats["outcome_without_sparse_path"]+=1
            continue
        parsed=[]
        for pair in path:
            if not isinstance(pair,(list,tuple)) or len(pair)<2:continue
            elapsed,mult=val(pair[0]),val(pair[1])
            if elapsed is not None and mult is not None and 0<elapsed<=600 and mult>0:
                parsed.append((elapsed,mult))
        if not parsed:
            stats["no_post30_valid_path_points"]+=1
            continue
        stats["with_post30_path_points"]+=1
        peak=max(v for t,v in parsed)
        late=max(t for t,v in parsed)
        rec={"mint":str(o.get("mint")),"created_ts":o.get("created_ts"),
             "peak_sparse_chart_x":peak,"last_path_sec_post30":late,
             "rugged":outcome.get("rugged")}
        for key in FEATURES:rec[key]=val(snap.get(key))
        samples.append(rec)
    stats["labelled_mints"]=len(samples)
    stats["11x_sparse_chart_peaks"]=sum(z["peak_sparse_chart_x"]>=11 for z in samples)
    stats["21x_sparse_chart_peaks"]=sum(z["peak_sparse_chart_x"]>=21 for z in samples)
    for threshold in (2,5,11,21,51):
        stats[f"sparse_chart_peak_ge_{threshold}x"]=sum(z["peak_sparse_chart_x"]>=threshold for z in samples)
    # Never assume a missing sparse path is a known losing token.
    import pandas as pd,numpy as np
    df=pd.DataFrame(samples)
    if df.empty:raise ValueError("No parseable post-30s path events")
    df["created_ts"]=pd.to_datetime(df["created_ts"],utc=True,errors="coerce")
    df=df.dropna(subset=["created_ts","mint"])
    df=df.sort_values("created_ts",kind="stable").drop_duplicates("mint",keep="first")
    stats["distinct_valid_labelled_mints"]=len(df)
    date_range=[str(df.created_ts.min()),str(df.created_ts.max())]
    pivot=int(len(df)*.70)
    train,test=df.iloc[:pivot],df.iloc[pivot:]
    comparisons={}
    y=train["peak_sparse_chart_x"]>=11
    for feat in FEATURES:
        a=pd.to_numeric(train.loc[y,feat],errors="coerce").dropna()
        b=pd.to_numeric(train.loc[~y,feat],errors="coerce").dropna()
        if len(a)>=5 and len(b)>=25:
            ma,mb=float(a.median()),float(b.median())
            comparisons[feat]={"winner_median":round(ma,5),
                "nonwinner_median":round(mb,5),
                "median_ratio":round(ma/mb,4) if mb else None,
                "winner_n":len(a),"nonwinner_n":len(b)}
    base=float((test.peak_sparse_chart_x>=11).mean())
    report={"source":SOURCE,"sha256":SHA256,"independent_period":date_range,
        "observation_window":"post 30 second reference, sparse path timestamps 0-600sec (not full day)",
        "source_counts":dict(stats),"path_examples":examples,
        "coverage_fraction_of_all_launches":round(len(df)/max(stats["rows"],1),6),
        "train_mints":len(train),"test_mints":len(test),
        "train_chart_ge11":int(y.sum()),
        "test_chart_ge11":int((test.peak_sparse_chart_x>=11).sum()),
        "test_base_rate_ge11":round(base,6),
        "train_only_30s_precursor_medians":comparisons,
        "holdout_model":"NO MODEL until sufficient labelled winners and explicitly bounded path coverage",
        "false_24h_claims_blocked":True,"missing_paths_not_treated_as_losses":True,
        "net_return_verified":False,"orders":0,"trades":0,
        "limitations":["Sparse outcome paths cover only a fraction of all launches.",
            "Price multipliers are chart-only; no executable liquidity, route, slippage or fills.",
            "Seconds-after-30s labels do not answer which tokens rose >1000% in 24h.",
            "Data collection outages, censoring and provider biases remain.",
            "Outcome path reference units assumed from dataset README and inspected samples."]}
    # Fixed hyperparameters, never fitted on JUL late dates. Outcome gaps
    # are CENSORED, not treated as losing launches. No optimization on holdout.
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.metrics import average_precision_score,roc_auc_score
    usable=[key for key in FEATURES if key!="price"
        and pd.to_numeric(train[key],errors="coerce").notna().sum()>100
        and pd.to_numeric(train[key],errors="coerce").nunique()>3]
    y_tr=(train.peak_sparse_chart_x>=11).to_numpy(dtype=int)
    y_te=(test.peak_sparse_chart_x>=11).to_numpy(dtype=int)
    if y_tr.sum()>=25 and y_te.sum()>=10 and len(usable)>=5:
        imputer=SimpleImputer(strategy="median",keep_empty_features=True)
        xt=imputer.fit_transform(train[usable].apply(pd.to_numeric,errors="coerce"))
        xe=imputer.transform(test[usable].apply(pd.to_numeric,errors="coerce"))
        clf=HistGradientBoostingClassifier(max_iter=105,max_leaf_nodes=15,
            min_samples_leaf=70,learning_rate=.05,l2_regularization=5.,
            class_weight="balanced",random_state=7)
        clf.fit(xt,y_tr)
        prob=clf.predict_proba(xe)[:,1]
        rank=np.argsort(-prob,kind="stable")
        results={"auc":round(float(roc_auc_score(y_te,prob)),6),
                 "average_precision":round(float(average_precision_score(y_te,prob)),6),
                 "baseline_rate":round(float(y_te.mean()),7)}
        for fraction in (.01,.02,.05,.10):
            chosen=rank[:max(1,int(math.ceil(len(test)*fraction)))]
            hits=int(y_te[chosen].sum())
            precision=hits/len(chosen)
            results[f"top_{int(fraction*100)}pct"]={
                "candidates":len(chosen),"chart_11x_hits":hits,
                "precision":round(precision,7),
                "lift_vs_unseen_base":round(precision/y_te.mean(),4) if y_te.mean() else None,
                "recall":round(hits/y_te.sum(),6)}
        report["holdout_model"]="one fixed 30-second snapshot-only HistGradientBoosting on early 70%, evaluate last 30%; NO execution"
        report["holdout_ranking"]=results
        report["features_used_from_30s"]=usable
        examples=[]
        for ix in rank[:min(60,len(rank))]:
            r=test.iloc[int(ix)]
            examples.append({"mint":str(r.mint),"created_at":str(r.created_ts),
              "snapshot30_trades":int(r.n_trades) if pd.notna(r.n_trades) else None,
              "snapshot30_dev_share":val(r.dev_share),
              "model_score":round(float(prob[int(ix)]),6),
              "later_sparse_chart_peak_x":round(float(r.peak_sparse_chart_x),4),
              "not_an_executable_return":True})
        (DATA/"july_holdout_ranked60_not_usable_as_trades.json").write_text(json.dumps(examples,indent=2))
    (DATA/"july_early_market_results.json").write_text(json.dumps(report,indent=2,allow_nan=False))
    print("INDEPENDENT_JULY_EARLY_RESEARCH",json.dumps({k:v for k,v in report.items()
       if k in ("independent_period","source_counts","coverage_fraction_of_all_launches",
         "train_mints","test_mints","train_chart_ge11","test_chart_ge11",
         "test_base_rate_ge11","train_only_30s_precursor_medians","path_examples","holdout_ranking","holdout_model")},allow_nan=False))
    print("JULY_24H_PROFITABLE_TRADES_PROVEN=False")
if __name__=="__main__":main()
