#!/usr/bin/env python3
"""Stress-test original 798k 11x historical chart result *within July 2026 regime*.
The already-tested June->July model had a dramatic label base-rate change.
No orders, no wallet, no profit claims. Uses launch-time features only.
"""
import json,hashlib,math,os,urllib.request
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score,roc_auc_score
from scipy.stats import hypergeom
DIR=Path("evidence/regime_check");DIR.mkdir(parents=True,exist_ok=True)
SHA="c005d86d424013e5c78701161b025f3d8c3d472afb61466e0ad6fd5afe9e8ea6"
URL="https://huggingface.co/datasets/Slinky21/Pumpfun_Memecoin_Corpus/resolve/main/tokens.parquet"
FIELDS=["mint","detected_at","initial_market_cap_sol","peak_market_cap_sol","peak_market_cap_at",
        "top10_pct_suspect","initial_buy_sol","initial_holder_count","initial_gini",
        "initial_top1_pct_corrected","initial_top5_pct_corrected",
        "initial_top10_pct_corrected","dev_buy_pct_corrected","is_mayhem_mode"]
FEATURES=["initial_buy_sol","initial_holder_count","initial_gini",
        "initial_top1_pct_corrected","initial_top5_pct_corrected",
        "initial_top10_pct_corrected","dev_buy_pct_corrected","is_mayhem_mode"]
def download():
    p=DIR/"tokens.parquet"
    if not p.exists():
        r=urllib.request.Request(URL,headers={"User-Agent":"CryptoHunterWithinRegimeAudit/1"})
        with urllib.request.urlopen(r,timeout=90) as fd,open(str(p)+".part","wb") as out:
            while chunk:=fd.read(1<<20):out.write(chunk)
        os.replace(str(p)+".part",p)
    if hashlib.sha256(p.read_bytes()).hexdigest()!=SHA:raise RuntimeError("Unexpected source data")
    return p
def ci95(k,n):
    if not n:return [None,None]
    z=1.96;p=k/n;denom=1+z*z/n
    cent=(p+z*z/(2*n))/denom
    span=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/denom
    return [round(max(0,cent-span),6),round(min(1,cent+span),6)]
def evaluate(label,train,test,features):
    tr=train[label].to_numpy(dtype=int);te=test[label].to_numpy(dtype=int)
    score={"n_train":len(tr),"n_test":len(te),"hits_train":int(tr.sum()),"hits_test":int(te.sum()),
           "base_rate":round(float(te.mean()),7)}
    if tr.sum()<10 or te.sum()<10:return {**score,"trained":False}
    im=SimpleImputer(strategy="median",keep_empty_features=True)
    x=im.fit_transform(train[features].apply(pd.to_numeric,errors="coerce").replace([np.inf,-np.inf],np.nan))
    z=im.transform(test[features].apply(pd.to_numeric,errors="coerce").replace([np.inf,-np.inf],np.nan))
    cfg=dict(max_iter=75,max_leaf_nodes=14,min_samples_leaf=180,
             learning_rate=.055,l2_regularization=7,class_weight="balanced",random_state=19)
    clf=HistGradientBoostingClassifier(**cfg)
    clf.fit(x,tr)
    prob=clf.predict_proba(z)[:,1]
    rank=np.argsort(-prob,kind="stable")
    score.update(trained=True,auc=round(float(roc_auc_score(te,prob)),5),
                 average_precision=round(float(average_precision_score(te,prob)),6))
    for frac in (.001,.005,.01,.05):
        chosen=rank[:max(1,math.ceil(len(te)*frac))]
        hits=int(te[chosen].sum());rate=hits/len(chosen);base=te.mean()
        score[f"top_{frac*100:g}pct"]={"n":len(chosen),"hits":hits,
              "precision":round(rate,6),"base_rate":round(float(base),6),
              "lift":round(rate/base,3) if base else None,
              "wilson95":ci95(hits,len(chosen)),
              "one_sided_enrichment_p":round(float(hypergeom.sf(hits-1,len(te),int(te.sum()),len(chosen))),8)}
    # Compare against simplest baseline, same fixed cutoff volume.
    for col in ("initial_buy_sol","initial_holder_count"):
        simple=pd.to_numeric(test[col],errors="coerce").fillna(-1).to_numpy()
        idx=np.argsort(-simple,kind="stable")[:max(1,math.ceil(len(te)*.01))]
        hits=int(te[idx].sum())
        score[f"simple_{col}_top1pct"]={"n":len(idx),"hits":hits,
            "precision":round(hits/len(idx),6),
            "lift":round(hits/len(idx)/te.mean(),3) if te.mean() else None}
    return score
def main():
    df=pd.read_parquet(download(),columns=FIELDS)
    df["detected_at"]=pd.to_datetime(df.detected_at,utc=True,errors="coerce")
    df["peak_market_cap_at"]=pd.to_datetime(df.peak_market_cap_at,utc=True,errors="coerce")
    raw_initial=pd.to_numeric(df.initial_market_cap_sol,errors="coerce")
    raw_peak=pd.to_numeric(df.peak_market_cap_sol,errors="coerce")
    interval=(df.peak_market_cap_at-df.detected_at).dt.total_seconds()
    valid=(raw_initial>0)&(raw_peak>0)&interval.between(0,86400)&~df.top10_pct_suspect.fillna(True).astype(bool)
    df=df[valid].copy()
    df["peak_x"]=(raw_peak[valid]/raw_initial[valid]).to_numpy()
    df["peak_latency_s"]=interval[valid].to_numpy()
    df=df[(df.detected_at>="2026-07-04T00:00:00Z")&(df.detected_at<"2026-07-15T00:00:00Z")]
    df=df.sort_values("detected_at",kind="stable").drop_duplicates("mint",keep="first")
    # A genuinely later test within the same observed collection regime.
    train=df[df.detected_at<"2026-07-10T00:00:00Z"].copy()
    test=df[df.detected_at>="2026-07-10T00:00:00Z"].copy()
    assert not(set(train.mint)&set(test.mint))
    # Peak-after-5-min is a _necessary_ timing condition, not proof the
    # first crossing of 11x occurred after 5m.
    for frame in [train,test]:
        frame["chart_11x"]=(frame.peak_x>=11).astype(int)
        frame["chart_11x_peak_after_5min"]=((frame.peak_x>=11)&(frame.peak_latency_s>=300)).astype(int)
    summary={"source_sha":SHA,"regime":"2026-07-04 through 2026-07-14 ONLY",
      "train_dates":"July 4-9 2026","test_dates":"July 10-14 2026",
      "time_split_predeclared":True,
      "source_mints_july_regime":len(df),
      "qualifying_peaks_11x":int((df.peak_x>=11).sum()),
      "peaks_11x_after_5minutes":int(((df.peak_x>=11)&(df.peak_latency_s>=300)).sum()),
      "lag_warning":"PEAK AFTER 5MIN DOES NOT PROVE FIRST 11X CROSSING AFTER 5MIN",
      "chart_11x":evaluate("chart_11x",train,test,FEATURES),
      "chart_11x_peak_after_5min":evaluate("chart_11x_peak_after_5min",train,test,FEATURES),
      "ex_ante_features_only":FEATURES,
      "source_quality_flag_unknown_excluded":True,
      "realizable_returns_verified":False,"orders":0,"trades":0,
      "limitations":["June to July previous model had ~28x target base-rate break",
        "Within-July Jul4-9 vs Jul10-14 still susceptible to collection changes",
        "Peak mcap chart multiples don't establish quoteable buys, exits or token decimals.",
        "Early peak may precede first viable entry even when final peak latency >5min.",
        "Unknown quality flags excluded; missing future data censored from this dataset.",
        "Do not transfer the classifier's snapshot features to GeckoTerminal without proof of parity."]}
    (DIR/"within_july_regime_reality_check.json").write_text(json.dumps(summary,indent=2,allow_nan=False))
    print("WITHIN_REGIME_EVALUATION",json.dumps(summary,allow_nan=False))
    print("ZERO_EXECUTABLE_PROFITS_VERIFIED=True")
if __name__=="__main__":main()
