#!/usr/bin/env python3
"""Public research dataset experiment: ex-ante Solana meme-token 24h extreme-return labels.

Research-only. Uses only snapshot-time features, no trade execution, no wallets.
The 24h label is the maximum chart return AFTER snapshot, NOT an executable sale.
Run: python crypto-hunter/historical_extreme_research.py
"""
import hashlib, json, math, os, sys, urllib.request
from datetime import datetime, timezone
from pathlib import Path

SOURCE_REPO="ian05012/solana-memecoin-dataset"
SOURCE_REV="7070d00e1e4492f8604d988549d91785cc5f43f7"
SOURCE_URL=f"https://raw.githubusercontent.com/{SOURCE_REPO}/{SOURCE_REV}/data/tokens.parquet"
DATA=Path("evidence/historical");DATA.mkdir(parents=True,exist_ok=True)
TARGET="max_return_24h"
CUT_AGE=3600
# PRE-SNAPSHOT FEATURES ONLY: explicitly deny every target/outcome/time-to-peak field.
CANDIDATES=[
 "time_since_launch_sec","price_at_capture","market_cap_usd","liquidity_usd",
 "volume_h24","number_markets","holder_count","top10_holder_pct","dev_remaining_pct",
 "rat_position_pct","sniper_count","new_wallet_count","smart_money_count","smart_money_pct",
 "cluster_count","cluster_max_pct","cluster_top3_pct","cluster_newest_days",
 "bundler_pct_holders","fresh_wallet_pct","insider_pct_holders","sniper_pct_holders",
 "kol_pct_holders","smart_pct_holders","only_buy_pct","low_freq_pct","fishing_pct",
 "profitable_pct","pool_pct","mint_disabled","freeze_disabled","lp_burned_pct",
 "rug_probability","has_twitter","has_website","has_telegram","is_boosted",
 "boost_amount","boost_total","price_change_1h","price_change_6h",
 "unique_buyers","unique_sellers","active_wallets","buy_usd_1h","sell_usd_1h",
 "net_flow_usd","trade_buy_sell_ratio","smart_buy_1h","kol_buy_1h",
 "smart_concurrent_15m","flipper_ratio","large_buy_streak","seller_loss_ratio",
 "bundler_ratio","top_holder_ratio","insider_cluster_size","insider_holding_ratio",
 "insider_net_usd","known_smart_count","known_insider_count","smart_ratio",
 "early_smart_ratio","smart_earliness","crew_cohesion","buy_early","buy_late",
 "buy_total","buy_last10","sell_late","n_early","n_late","buyers_early",
 "buyers_late","smart_entry_rec"
]
# Numeric explicit snapshot columns must never be silently replaced by label fields.
BLOCKED_PARTS=("return_", "reaches_", "runner_", "exit_", "mdd_", "time_to_peak")
assert TARGET not in CANDIDATES
assert not any(any(t in name for t in BLOCKED_PARTS) for name in CANDIDATES)

def download_once(url,path,max_bytes=25_000_000):
    if path.exists() and 5_000_000<path.stat().st_size<max_bytes:
        return
    req=urllib.request.Request(url,headers={"User-Agent":"CryptoHunterHistoricalEvidence/1.0"})
    with urllib.request.urlopen(req,timeout=75) as r,open(str(path)+".part","wb") as f:
        total=0
        while chunk:=r.read(1024*1024):
            total+=len(chunk)
            if total>max_bytes:raise ValueError("Source exceeds budget")
            f.write(chunk)
    os.replace(str(path)+".part",path)

def safe_float(value):
    try:
        v=float(value)
        return v if math.isfinite(v) else None
    except (ValueError,TypeError):return None

def calc_counts(df):
    y=df[TARGET]
    return {k:int((y>=k).sum()) for k in (2,5,11,21)}

def median_cmp(train,col,winner):
    import pandas as pd
    a=pd.to_numeric(train.loc[winner,col],errors="coerce").replace([float("inf"),-float("inf")],float("nan")).dropna()
    b=pd.to_numeric(train.loc[~winner,col],errors="coerce").replace([float("inf"),-float("inf")],float("nan")).dropna()
    if len(a)<3 or len(b)<30:return None
    aa,bb=float(a.median()),float(b.median())
    return {"winner_median":round(aa,6),"other_median":round(bb,6),
            "median_ratio":round(aa/bb,3) if bb else None,
            "n_winners":len(a),"n_others":len(b)}

def main():
    import numpy as np, pandas as pd
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.metrics import average_precision_score,roc_auc_score
    path=DATA/"source_tokens.parquet"
    download_once(SOURCE_URL,path)
    blob=path.read_bytes()
    sha=hashlib.sha256(blob).hexdigest()
    raw=pd.read_parquet(path)
    required={"token_address","captured_at","time_since_launch_sec",TARGET}
    missing=required-set(raw.columns)
    if missing:raise ValueError("Missing fields "+str(missing))
    raw["captured_at"]=pd.to_datetime(raw["captured_at"],utc=True,errors="coerce")
    raw["time_since_launch_sec"]=pd.to_numeric(raw["time_since_launch_sec"],errors="coerce")
    raw[TARGET]=pd.to_numeric(raw[TARGET],errors="coerce")
    # KEEP ALL detections with valid timing first; then freeze earliest record PER MINT.
    raw=raw.dropna(subset=["token_address","captured_at","time_since_launch_sec"])
    raw=raw[raw["time_since_launch_sec"]>=0]
    raw=raw.sort_values(["captured_at","token_address"],kind="stable")
    mint_count=int(raw["token_address"].nunique())
    first=raw.drop_duplicates("token_address",keep="first").copy()
    # Label from the same earliest snapshot only; refuse to backfill from later captures.
    first=first[first[TARGET].notna() & (first[TARGET]>=0)]
    first=first.sort_values("captured_at",kind="stable")
    # Dataset only observes post-discovery markets, not the entire original population.
    early=first[first["time_since_launch_sec"]<=CUT_AGE].copy()
    fields=[c for c in CANDIDATES if c in early.columns]
    # Fallback to entire earliest-capture cohort if enough <=1h samples are missing.
    cohort=early if len(early)>=300 and (early[TARGET]>=11).sum()>=8 else first
    cohort_choice="earliest_snapshot_age_le_1h" if cohort is early else "earliest_detected_snapshot_any_age"
    n=len(cohort)
    if n<100:raise ValueError("Not enough unique mints for historical test")
    # TEMPORAL HOLDOUT: last 30% of DISTINCT MINTS chronologically.
    pivot=int(n*0.70)
    train=cohort.iloc[:pivot].copy()
    test=cohort.iloc[pivot:].copy()
    assert not(set(train["token_address"]) & set(test["token_address"]))
    train_y=(train[TARGET]>=11).to_numpy(dtype=int)
    test_y=(test[TARGET]>=11).to_numpy(dtype=int)
    feature_cols=[]
    for col in fields:
        vals=pd.to_numeric(train[col],errors="coerce")
        if vals.notna().sum()>=max(20,int(len(train)*.05)) and vals.nunique(dropna=True)>1:
            feature_cols.append(col)
    features={}
    for col in feature_cols:
        features[col]=median_cmp(train,col,train[TARGET]>=11)
    features={k:v for k,v in features.items() if v is not None}
    candidate=(train[TARGET]>=11)
    estimates={}
    for col in ("liquidity_usd","volume_h24","unique_buyers","smart_money_count",
                "top10_holder_pct","trade_buy_sell_ratio","time_since_launch_sec",
                "net_flow_usd","holder_count","cluster_max_pct","rug_probability"):
        if col in train.columns:estimates[col]=median_cmp(train,col,candidate)
    outcome={"source":SOURCE_REPO,"source_commit":SOURCE_REV,"source_sha256":sha,
      "source_rows":len(raw),"source_distinct_mints":mint_count,"earliest_mints_with_labels":len(first),
      "early_detected_le_1h":len(early),"early_11x_chart_labeled":int((early[TARGET]>=11).sum()),
      "cohort_used":cohort_choice,
      "label_definition":"MAX chart high / snapshot price during next 24h >= 11; NOT a fillable 1000 percent return",
      "first_snapshot_only":True,"train_test_split":"first chronological 70pct distinct mints train, last 30pct untouched test",
      "train_count":len(train),"test_count":len(test),
      "train_time_first":str(train["captured_at"].iloc[0]),"train_time_last":str(train["captured_at"].iloc[-1]),
      "test_time_first":str(test["captured_at"].iloc[0]),"test_time_last":str(test["captured_at"].iloc[-1]),
      "train_peak_counts":calc_counts(train),"test_peak_counts":calc_counts(test),
      "usable_features":feature_cols,"predictor_not_licensed":False,
      "train_only_precursor_median_comparisons":estimates,
      "forecast_for_live_tokens_not_validated":True,"executed_trades":0,
      "caveats":["Snapshot is at detection, not necessarily launch; label max is not executable sell.",
       "Selection/survivorship bias: not all Solana token launches.",
       "Original GMGN labels may contain residual high-candle glitches.",
       "No proven liquidity or buy/sell route on historic winner candles.",
       "Wallet smart-money definitions may introduce additional time leakage.",
       "One Jun 2026 market regime; temporal holdout alone cannot prove generalization."]}
    }
    if train_y.sum()>=8 and test_y.sum()>=3 and len(feature_cols)>=5:
        X_train=train[feature_cols].apply(pd.to_numeric,errors="coerce").replace([np.inf,-np.inf],np.nan)
        X_test=test[feature_cols].apply(pd.to_numeric,errors="coerce").replace([np.inf,-np.inf],np.nan)
        # Imputation fits TRAIN ONLY. No hyperparameter search on untouched holdout.
        imputer=SimpleImputer(strategy="median",add_indicator=False,keep_empty_features=True)
        xt=imputer.fit_transform(X_train)
        xe=imputer.transform(X_test)
        clf=HistGradientBoostingClassifier(max_iter=100,learning_rate=.06,max_leaf_nodes=12,
                                           min_samples_leaf=40,l2_regularization=3,
                                           class_weight="balanced",random_state=17)
        clf.fit(xt,train_y)
        prob=clf.predict_proba(xe)[:,1]
        ords=np.argsort(-prob,kind="stable")
        scored={}
        base=test_y.mean()
        for fraction in (.01,.02,.05,.10):
            top=ords[:max(1,int(math.ceil(len(test)*fraction)))]
            hits=int(test_y[top].sum())
            precision=float(test_y[top].mean())
            scored[f"top_{int(100*fraction)}pct"]={"inspected":len(top),"11x_chart_hits":hits,
                "precision":round(precision,6),"base_rate":round(float(base),6),
                "lift_vs_holdout_base":round(precision/base,4) if base>0 else None,
                "recall":round(hits/test_y.sum(),4)}
        outcome["holdout"]=scored
        outcome["holdout"]["average_precision"]=round(float(average_precision_score(test_y,prob)),5)
        outcome["holdout"]["auc_roc"]=round(float(roc_auc_score(test_y,prob)),5)
        outcome["model"]="historical ex-ante snapshot feature HistGradientBoosting, fixed hyperparameters, chronological holdout"
        # Export TOP PREDICTIONS from untouched TEST ONLY with ex-ante snapshot values,
        # never mistake later-revealed labels as information available at trade time.
        highlights=[]
        for idx in ords[:min(30,len(ords))]:
            row=test.iloc[int(idx)]
            highlights.append({"mint":str(row["token_address"]),"captured_at":str(row["captured_at"]),
                "predicted_11x_chart_score":round(float(prob[int(idx)]),6),
                "historical_actual_24h_max_x":round(float(row[TARGET]),4),
                "liquidity_at_capture":safe_float(row.get("liquidity_usd")),
                "smart_buy_at_capture":safe_float(row.get("smart_buy_1h"))})
        (DATA/"holdout_top30_not_tradeable.json").write_text(json.dumps(highlights,indent=2))
    else:
        outcome["model"]="NOT FIT: too few positive extreme events in temporal train/test or features"
        outcome["holdout"]={}
    (DATA/"historical_results.json").write_text(json.dumps(outcome,indent=2,ensure_ascii=False,allow_nan=False,default=str))
    # A report readable in GitHub Actions logs without downloading any Parquet.
    concise={k:v for k,v in outcome.items() if k in (
      "source_rows","source_distinct_mints","earliest_mints_with_labels","early_detected_le_1h",
      "early_11x_chart_labeled","cohort_used","train_count","test_count","train_peak_counts",
      "test_peak_counts","model","holdout","train_only_precursor_median_comparisons")}
    print("HISTORICAL_EXTREME_RESEARCH_RECEIPT",json.dumps(concise,ensure_ascii=False,allow_nan=False,default=str))
    print("NEVER_USE_HISTORICAL_LABELS_AS_FEATURES=True")
    print("HISTORICAL_EXECUTABLE_RETURN_VERIFIED=False")
    print("EXECUTED_TRADES=0")
if __name__=="__main__":main()
