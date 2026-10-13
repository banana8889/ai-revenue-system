#!/usr/bin/env python3
"""Rebuild 24h early-pump labels from real timestamped 15m OHLCV and test precursors.
Research-only: historical prices are NOT fillable exit quotes. No orders, no secrets.
"""
import hashlib,json,math,os,urllib.request
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import average_precision_score,roc_auc_score

REV="7070d00e1e4492f8604d988549d91785cc5f43f7"
REPO="ian05012/solana-memecoin-dataset"
OUTPUT=Path("evidence/historical_ohlcv");OUTPUT.mkdir(parents=True,exist_ok=True)
# These are at-detection observations. No smart-money tag engineered with future
# trades, candle-derived outcomes, max_return_*, or later market prices as features.
FEAT=[
"time_since_launch_sec","market_cap_usd","liquidity_usd","volume_h24",
"number_markets","holder_count","top10_holder_pct","dev_remaining_pct",
"rat_position_pct","sniper_count","new_wallet_count","cluster_count",
"cluster_max_pct","bundler_pct_holders","fresh_wallet_pct","insider_pct_holders",
"sniper_pct_holders","only_buy_pct","mint_disabled","freeze_disabled",
"lp_burned_pct","has_twitter","has_website","has_telegram","is_boosted",
"price_change_1h","unique_buyers","unique_sellers","active_wallets",
"buy_usd_1h","sell_usd_1h","net_flow_usd","trade_buy_sell_ratio",
"flipper_ratio","large_buy_streak","seller_loss_ratio",
"buy_early","buy_late","buy_total","buy_last10","sell_late",
"n_early","n_late","buyers_early","buyers_late"
]
assert not any("return" in x or "runner_peak" in x for x in FEAT)

def download(kind):
    dest=OUTPUT/(kind+".parquet")
    if not dest.exists():
        url=f"https://raw.githubusercontent.com/{REPO}/{REV}/data/{kind}.parquet"
        req=urllib.request.Request(url,headers={"User-Agent":"CryptoHunterOwnOHLCVLabels/1"})
        with urllib.request.urlopen(req,timeout=90) as r,open(str(dest)+".part","wb") as w:
            while chunk:=r.read(1024*1024):w.write(chunk)
        os.replace(str(dest)+".part",dest)
    return dest,hashlib.sha256(dest.read_bytes()).hexdigest()

def clean(x):
    try:
        v=float(x)
        return v if math.isfinite(v) else None
    except (TypeError,ValueError):return None

def outcome_counts(a):
    return {str(level):int((a>=level).sum()) for level in (2,5,11,21)}

def comp(frame,column,label):
    a=pd.to_numeric(frame.loc[label,column],errors="coerce").replace([np.inf,-np.inf],np.nan).dropna()
    b=pd.to_numeric(frame.loc[~label,column],errors="coerce").replace([np.inf,-np.inf],np.nan).dropna()
    if len(a)<3 or len(b)<30:return None
    ap,bp=float(a.median()),float(b.median())
    return {"winner_median":clean(ap),"control_median":clean(bp),
      "ratio":round(ap/bp,4) if bp else None,
      "winner_n":len(a),"control_n":len(b)}

def main():
    tp,thash=download("tokens")
    op,ohash=download("ohlcv")
    raw=pd.read_parquet(tp)
    candles=pd.read_parquet(op)
    raw["captured_at"]=pd.to_datetime(raw["captured_at"],utc=True,errors="coerce")
    raw["time_since_launch_sec"]=pd.to_numeric(raw["time_since_launch_sec"],errors="coerce")
    raw["price_at_capture"]=pd.to_numeric(raw["price_at_capture"],errors="coerce")
    raw=raw.dropna(subset=["token_address","captured_at","price_at_capture","time_since_launch_sec"])
    raw=raw[(raw["price_at_capture"]>0)&(raw["time_since_launch_sec"]>=0)]
    # Freeze first detection for each mint, before looking at ANY future candle.
    first=raw.sort_values("captured_at",kind="stable").drop_duplicates("token_address",keep="first")
    candles["ts"]=pd.to_datetime(candles["ts"],unit="s",utc=True,errors="coerce")
    for x in ("high","close","volume"):
        candles[x]=pd.to_numeric(candles[x],errors="coerce")
    # For every first detection, only include candles strictly after capture and <24h.
    joined=candles.merge(first[["token_address","captured_at","price_at_capture"]],on="token_address")
    joined["elapsed_sec"]=(joined["ts"]-joined["captured_at"]).dt.total_seconds()
    joined=joined[(joined.elapsed_sec>=0)&(joined.elapsed_sec<86400)]
    joined=joined[(joined.close>0)&(joined.high>0)&(joined.price_at_capture>0)]
    joined["glitch"]=joined.high>10*joined.close
    # Fixed rule: remove isolated extreme high relative to same candle close.
    valid=joined[~joined.glitch].copy()
    valid["high_multiple"]=valid.high/valid.price_at_capture
    valid["close_multiple"]=valid.close/valid.price_at_capture
    # Minimum $100 observed trading in the CLOSE candle used for primary label.
    valid["confirmed_close_multiple"]=valid.close_multiple.where(valid.volume>=100,other=np.nan)
    gp=valid.groupby("token_address",sort=False)
    features=gp.agg(
        candles_observed=("close","size"),
        first_candle_delay_s=("elapsed_sec","min"),
        last_candle_s=("elapsed_sec","max"),
        highest_wick_x=("high_multiple","max"),
        highest_close_x=("confirmed_close_multiple","max"),
        total_candle_volume_usd=("volume","sum")
    )
    labelled=first.merge(features,left_on="token_address",right_index=True,how="inner")
    labelled=labelled[(labelled.candles_observed>=4)&(labelled.first_candle_delay_s<=1800)]
    labelled=labelled[labelled.highest_close_x.notna()]
    labelled=labelled.sort_values("captured_at",kind="stable")
    # Confirmed price chart close, NOT fill: a 15m close at least 11x and
    # >= $100 volume in the candle. Still fails orderbook/slippage/MEV proof.
    labelled["target_11x_chart_close"]=(labelled.highest_close_x>=11).astype(int)
    # Use earliest launch age <= 1h only if positive examples permit; document fallbacks.
    subset=labelled[labelled.time_since_launch_sec<=3600]
    eligible=subset if len(subset)>=500 and int(subset.target_11x_chart_close.sum())>=10 else labelled
    desc="first_detected_age_le_1h" if eligible is subset else "earliest_detected_any_age"
    pivot=int(len(eligible)*.70)
    train,test=eligible.iloc[:pivot].copy(),eligible.iloc[pivot:].copy()
    assert set(train.token_address).isdisjoint(set(test.token_address))
    tr=train.target_11x_chart_close.to_numpy()
    te=test.target_11x_chart_close.to_numpy()
    # Training-only retrospective contrasts; never tune threshold on holdout.
    compares={k:comp(train,k,train.target_11x_chart_close==1) for k in FEAT if k in train}
    compares={k:v for k,v in compares.items() if v is not None}
    all_fields=[]
    for col in FEAT:
        if col in train:
            ser=pd.to_numeric(train[col],errors="coerce")
            if ser.notna().sum()>=max(20,int(.05*len(train))) and ser.nunique(dropna=True)>=2:
                all_fields.append(col)
    report={
      "source":REPO,"source_sha":REV,"tokens_parquet_sha256":thash,"ohlcv_parquet_sha256":ohash,
      "raw_snapshot_rows":len(raw),"raw_first_mints":len(first),"raw_candle_rows":len(candles),
      "mints_with_valid_candles":int(valid.token_address.nunique()),
      "mints_eligible_four_candles_with_early_coverage":len(labelled),
      "mints_first_detected_le_1h":len(subset),
      "all_eligible_wick_counts":outcome_counts(labelled.highest_wick_x),
      "all_eligible_close_counts_min_100usd_candle":outcome_counts(labelled.highest_close_x),
      "eligibility_for_model":desc,
      "observation_definition":"From first mint detection; OHLCV 15m close at 11x within following 24h, with >= $100 candle USD volume; not executable route",
      "train_rows":len(train),"test_rows":len(test),
      "train_11x":int(tr.sum()),"test_11x":int(te.sum()),
      "train_window":[str(train.captured_at.iloc[0]),str(train.captured_at.iloc[-1])],
      "test_window":[str(test.captured_at.iloc[0]),str(test.captured_at.iloc[-1])],
      "train_only_precursor_medians":compares,"allowed_snapshot_features":all_fields,
      "no_hindsight_features":True,"all_fills_verified":False,"trades":0,
      "limitations":["OHLCV exists for selected tokens only; missing later data makes losers potentially censored.",
       "Single market regime ~20 days Jun 2026; some 15m bars may be unreliable.",
       "At 15-minute resolution, first close may already be after initial price move.",
       "No buy/sell quote, slippage, liquidity available at future candle high; chart-only signal.",
       "Features sampled at token detection rather than universal token launch.",
       "No statistically established real-world edge until independent future paper quote tests."]}
    if tr.sum()>=15 and te.sum()>=5 and len(all_fields)>=5:
        x=train[all_fields].apply(pd.to_numeric,errors="coerce").replace([np.inf,-np.inf],np.nan)
        z=test[all_fields].apply(pd.to_numeric,errors="coerce").replace([np.inf,-np.inf],np.nan)
        imp=SimpleImputer(strategy="median",keep_empty_features=True)
        x=imp.fit_transform(x)
        z=imp.transform(z)
        clf=HistGradientBoostingClassifier(max_iter=90,max_leaf_nodes=12,min_samples_leaf=30,
               learning_rate=.05,l2_regularization=4,class_weight="balanced",random_state=11)
        clf.fit(x,tr)
        proba=clf.predict_proba(z)[:,1]
        ranks=np.argsort(-proba,kind="stable")
        quality={"holdout_base_rate":round(float(te.mean()),7),
           "average_precision":round(float(average_precision_score(te,proba)),6),
           "auc_roc":round(float(roc_auc_score(te,proba)),6)}
        for frac in (.01,.02,.05,.10):
            chosen=ranks[:max(1,int(math.ceil(len(test)*frac)))]
            hits=int(te[chosen].sum())
            prec=hits/len(chosen)
            quality[f"top_{int(frac*100)}pct"]={"inspected":len(chosen),"hits":hits,
                "precision":round(prec,6),
                "lift_vs_holdout_base":round(prec/te.mean(),4) if te.mean() else None,
                "recall":round(hits/te.sum(),6)}
        # Small-sample uncertainty: a 2/21 top-1%-hit is not proof of an edge.
        picked=quality["top_1pct"]["inspected"]
        hits=quality["top_1pct"]["hits"]
        den=len(test)
        pos=int(te.sum())
        if picked>0:
            phat=hits/picked
            z=1.96
            denom=1+z*z/picked
            center=(phat+z*z/(2*picked))/denom
            span=z*math.sqrt(phat*(1-phat)/picked+z*z/(4*picked*picked))/denom
            quality["top_1pct"]["wilson_95pct_precision_interval"]=[round(max(0,center-span),6),round(min(1,center+span),6)]
            # Fixed cohort hypergeometric enrichment null with no replacement.
            null=sum(math.comb(pos,k)*math.comb(den-pos,picked-k)/math.comb(den,picked)
                     for k in range(hits,min(pos,picked)+1))
            quality["top_1pct"]["one_sided_hypergeometric_p"] = round(null,6)
            quality["top_1pct"]["multiple_thresholds_checked"]=4
        report["out_of_time_holdout"]=quality
        report["model"]="fixed HistGradientBoosting snapshot-only features, train median imputer fit before holdout"
        examples=[]
        for i in ranks[:min(30,len(ranks))]:
            row=test.iloc[int(i)]
            examples.append({"mint":str(row.token_address),"detected_at":str(row.captured_at),
                "predicted_chart_11x_score":round(float(proba[int(i)]),6),
                "later_chart_max_close_x":round(float(row.highest_close_x),6),
                "later_highest_wick_x":round(float(row.highest_wick_x),6),
                "entry_liquidity_snapshot_usd":clean(row.get("liquidity_usd")),
                "first_candle_delay_seconds":clean(row.first_candle_delay_s),
                "this_is_not_trading_advice":True})
        (OUTPUT/"holdout_top30_chart_only.json").write_text(json.dumps(examples,ensure_ascii=False,indent=2))
    else:
        report["model"]="NOT FIT: too few positives or features in temporally heldout 24h window"
        report["out_of_time_holdout"]={}
    # No labels usable for trade automation absent venue + sellable quote.
    (OUTPUT/"validated_candles_results.json").write_text(json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2,default=str))
    print("OHLCV_HISTORICAL_RESEARCH_RECEIPT",json.dumps({k:v for k,v in report.items() if k in (
        "raw_snapshot_rows","raw_first_mints","raw_candle_rows","mints_with_valid_candles",
        "mints_eligible_four_candles_with_early_coverage","mints_first_detected_le_1h",
        "all_eligible_wick_counts","all_eligible_close_counts_min_100usd_candle",
        "eligibility_for_model","train_rows","test_rows","train_11x","test_11x",
        "train_window","test_window","train_only_precursor_medians","out_of_time_holdout","model")},ensure_ascii=False,allow_nan=False))
    print("OHLCV_HAS_NO_EXECUTED_TRADES=True")
if __name__=="__main__":main()
