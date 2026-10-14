#!/usr/bin/env python3
"""Independent July 2026 snapshots: SAME future after-60s chart targets, compare
30-second and 60-second observation classifiers. No executable trading returns.
Data reference: source README says outcome.path time is seconds AFTER 30s and
price multiple is relative to snap_30.price; explicitly rebase for snap_60.
"""
import gzip,hashlib,json,math,os,urllib.request
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score
from scipy.stats import hypergeom
PATH=Path("evidence/july_lag_holdout");PATH.mkdir(parents=True,exist_ok=True)
URL="https://raw.githubusercontent.com/mivor7/pumpfun-pumpswap-market-data/main/launches/launches_dataset.jsonl.gz"
SHA="4608b5891174cb2d2bea1effaf139f86f0f2a01a57c37859a1891e8f642299dc"
FEATURES=("n_trades","buys_sol","sells_sol","net_sol","uniq_buyers","uniq_sellers",
          "dev_share","top_buyer_share","ret_from_first")
def fnum(v):
    try:
        n=float(v)
        return n if math.isfinite(n) else None
    except (TypeError,ValueError):return None
def source():
    dest=PATH/"launches.jsonl.gz"
    if not dest.exists():
        req=urllib.request.Request(URL,headers={"User-Agent":"CryptoHunter60sOutOfTime/1"})
        with urllib.request.urlopen(req,timeout=85) as r,open(str(dest)+".part","wb") as f:
            n=0
            while chunk:=r.read(1048576):
                n+=len(chunk)
                if n>35000000:raise ValueError("source too large")
                f.write(chunk)
        os.replace(str(dest)+".part",dest)
    if hashlib.sha256(dest.read_bytes()).hexdigest()!=SHA:raise ValueError("Source SHA mismatch")
    return dest
def sample():
    data=[]
    counters={"rows":0,"missing30or60":0,"missing_future_censored":0,
              "path_snapshot60_inconsistent_excluded":0,"observed_valid":0}
    for line in gzip.open(source(),"rt",encoding="utf-8"):
        rec=json.loads(line);counters["rows"]+=1
        s30,s60=rec.get("snap_30"),rec.get("snap_60")
        if not isinstance(s30,dict) or not isinstance(s60,dict):
            counters["missing30or60"]+=1;continue
        p30,p60=fnum(s30.get("price")),fnum(s60.get("price"))
        if not p30 or not p60 or p30<=0 or p60<=0:
            counters["missing30or60"]+=1;continue
        out=rec.get("outcome")
        path=out.get("path") if isinstance(out,dict) else None
        if not path or not isinstance(path,list):
            counters["missing_future_censored"]+=1;continue
        observed=[]
        near60=[]
        for item in path:
            if not isinstance(item,(list,tuple)) or len(item)<2:continue
            secs,ratio=fnum(item[0]),fnum(item[1])
            if secs is None or ratio is None or ratio<=0 or not 0<secs<=600:continue
            if abs(secs-30)<=15:near60.append((abs(secs-30),ratio))
            if secs>30:observed.append((secs-30,ratio*p30/p60))
        if not observed:
            counters["missing_future_censored"]+=1;continue
        if near60:
            measured=min(near60)[1]
            predicted=p60/p30
            if max(measured/predicted,predicted/measured)>3:
                counters["path_snapshot60_inconsistent_excluded"]+=1;continue
        peak=max(price for _,price in observed)
        cross=min((secs for secs,price in observed if price>=11),default=None)
        record={"mint":str(rec.get("mint")),"created_ts":rec.get("created_ts"),
                "chart_peak_after_60_x":peak,"first_11x_after_60_seconds":cross,
                "label_11x_after_60":int(peak>=11)}
        for col in FEATURES:
            record["v30_"+col]=fnum(s30.get(col))
            record["v60_"+col]=fnum(s60.get(col))
        data.append(record);counters["observed_valid"]+=1
    return pd.DataFrame(data),counters
def evaluate(tr,te,feature_prefix):
    columns=[feature_prefix+x for x in FEATURES]
    y=tr.label_11x_after_60.to_numpy(dtype=int)
    ey=te.label_11x_after_60.to_numpy(dtype=int)
    im=SimpleImputer(strategy="median",keep_empty_features=True)
    train=im.fit_transform(tr[columns].apply(pd.to_numeric,errors="coerce"))
    test=im.transform(te[columns].apply(pd.to_numeric,errors="coerce"))
    clf=HistGradientBoostingClassifier(max_iter=105,max_leaf_nodes=15,min_samples_leaf=70,
          learning_rate=.05,l2_regularization=5,class_weight="balanced",random_state=7)
    clf.fit(train,y)
    pred=clf.predict_proba(test)[:,1]
    idx=np.argsort(-pred,kind="stable")
    result={"auc":round(float(roc_auc_score(ey,pred)),6),
            "average_precision":round(float(average_precision_score(ey,pred)),6),
            "baseline_rate":round(float(ey.mean()),7)}
    for fraction in (.01,.02,.05,.10):
        best=idx[:max(1,math.ceil(fraction*len(idx)))]
        hits=int(ey[best].sum());pct=hits/len(best)
        result[f"top_{round(fraction*100)}pct"]={
            "candidates":len(best),"hits":hits,"precision":round(pct,6),
            "lift":round(pct/ey.mean(),4) if ey.mean() else None,
            "recall":round(hits/ey.sum(),5),
            "one_sided_enrichment_p":round(float(hypergeom.sf(hits-1,len(ey),int(ey.sum()),len(best))),8)}
    return result
def main():
    df,ct=sample()
    if df.empty:raise RuntimeError("Empty sample")
    df["created_ts"]=pd.to_datetime(df.created_ts,utc=True,errors="coerce")
    df=df.dropna(subset=["created_ts","mint"])
    df=df.sort_values("created_ts",kind="stable").drop_duplicates("mint")
    pivot=int(.7*len(df))
    train,test=df.iloc[:pivot],df.iloc[pivot:]
    assert set(train.mint).isdisjoint(set(test.mint))
    result={"source_sha":SHA,"source_docs":"path [seconds_after_30s, price_multiple_vs_30s_reference]",
      "source_counts":ct,"unique_cohort":len(df),"train_count":len(train),"test_count":len(test),
      "train_first":str(train.created_ts.min()),"train_last":str(train.created_ts.max()),
      "test_first":str(test.created_ts.min()),"test_last":str(test.created_ts.max()),
      "post60_chart_11x_train":int(train.label_11x_after_60.sum()),
      "post60_chart_11x_test":int(test.label_11x_after_60.sum()),
      "compared_on_exact_same_test_token_mints":True,
      "no_future_features":list(FEATURES),
      "chart_targets_not_executable":True,
      "trades":0,"wallets":False,
      "limits":["Sparse path is not every actual trade or executable quote",
                "Only selected launches with later outcome paths; missing data censored",
                "Data July 16-27 not equal to today's regime",
                "60s signal is later than 30s; response latency and chain fees excluded"]}
    if train.label_11x_after_60.sum()>=10 and test.label_11x_after_60.sum()>=5:
        result["model_at_30s"]=evaluate(train,test,"v30_")
        result["model_at_60s"]=evaluate(train,test,"v60_")
    (PATH/"same_cohort_30s_vs_60s_out_of_time.json").write_text(json.dumps(result,indent=2,allow_nan=False))
    print("JULY_30S_VS_60S_SAME_TARGET_HOLDOUT",json.dumps(result,allow_nan=False))
    print("NO_EXECUTABLE_RETURNS_PROVEN=True")
if __name__=="__main__":main()
