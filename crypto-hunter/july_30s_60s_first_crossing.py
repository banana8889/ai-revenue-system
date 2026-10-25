#!/usr/bin/env python3
"""Independent JUL 2026 launch study: exact first-observed 11x after 30s and 60s.
Unlike peak time, first threshold crossing provides a prospective timing upper
bound. Sparse chart paths remain UNSUITABLE as proofs of executed gains.
"""
import gzip,hashlib,json,math,os,urllib.request,collections,csv,statistics
from pathlib import Path
OUT=Path("evidence/july_entry_delay");OUT.mkdir(parents=True,exist_ok=True)
URL="https://raw.githubusercontent.com/mivor7/pumpfun-pumpswap-market-data/main/launches/launches_dataset.jsonl.gz"
SHA="4608b5891174cb2d2bea1effaf139f86f0f2a01a57c37859a1891e8f642299dc"
def number(n):
    try:
        v=float(n);return v if math.isfinite(v) else None
    except (TypeError,ValueError):return None
def source():
    f=OUT/"launches_dataset.jsonl.gz"
    if not f.exists():
        req=urllib.request.Request(URL,headers={"User-Agent":"CryptoHunterFirstCrossAudit/1.0"})
        with urllib.request.urlopen(req,timeout=90) as r,open(str(f)+".part","wb") as o:
            n=0
            while c:=r.read(1<<20):
                n+=len(c)
                if n>35000000:raise RuntimeError("source too large")
                o.write(c)
        os.replace(str(f)+".part",f)
    if hashlib.sha256(f.read_bytes()).hexdigest()!=SHA:raise ValueError("Data changed")
    return f
def buck(t):
    if t<=5:return "0-5s"
    if t<=30:return "5-30s"
    if t<=60:return "30-60s"
    if t<=180:return "60-180s"
    return "180-600s"
def main():
    c=collections.Counter()
    derived=[]
    all_winners=[]
    crash_times_50=[]
    crash_times_90=[]
    known_peak_with_future=0
    peak_no_later_sample=0
    replay_records=[]
    # Precommitted contemporaneous trailing drawdowns. Compute only from
    # observations known at the time, never the future/global peak.
    WARN_DRAWDOWNS=(.20,.40,.60)
    CRASH_DRAWDOWN=.90
    EVAL_WINDOW_SECONDS=120
    for line in gzip.open(source(),"rt",encoding="utf-8"):
        try:d=json.loads(line)
        except ValueError:
            c["corrupt_rows"]+=1
            continue
        c["rows"]+=1
        snap30=d.get("snap_30")
        if not isinstance(snap30,dict):c["no_30s_snapshot"]+=1;continue
        price30=number(snap30.get("price"))
        if not price30 or price30<=0:c["invalid_price30"]+=1;continue
        c["valid_30s_price"]+=1
        outcome=d.get("outcome")
        path=(outcome or {}).get("path") if isinstance(outcome,dict) else None
        if not isinstance(path,list) or not path:
            c["no_future_path_censored"]+=1;continue
        points=[]
        for pair in path:
            if not isinstance(pair,(list,tuple)) or len(pair)<2:continue
            t,p=number(pair[0]),number(pair[1])
            if t is not None and p is not None and 0<t<=600 and p>0:points.append((t,p))
        if not points:c["no_valid_future_points_censored"]+=1;continue
        points.sort()
        c["future_30s_path_observable"]+=1
        crossings=[t for t,p in points if p>=11]
        if crossings:
            c["chart_ge11_from_30s_baseline"]+=1
            c["first_11x_cross_"+buck(crossings[0])]+=1
            # A 10-minute SPARSE path permits upper bounds on observed
            # first crossing and crash; not continuous tick timing/fills.
            peak_at,peak_x=max(points,key=lambda x:x[1])
            later=[(t,p) for t,p in points if t>peak_at]
            drop50=next((t for t,p in later if p<=peak_x*0.5),None)
            drop90=next((t for t,p in later if p<=peak_x*0.1),None)
            if later:known_peak_with_future+=1
            else:peak_no_later_sample+=1
            if drop50 is not None:crash_times_50.append(drop50-peak_at)
            if drop90 is not None:crash_times_90.append(drop90-peak_at)
            # NEW: prospective-style replay anchored at FIRST observed 11x.
            # Running high is known at each point; crash means an observed
            # >=90% drawdown from THAT running high (not final hindsight peak).
            first_cross=next(t for t,p in points if p>=11)
            at_or_after=[(t,p) for t,p in points if t>=first_cross]
            rolling_peak=0.0
            crash_at=None
            alerts={str(int(x*100)):None for x in WARN_DRAWDOWNS}
            alerts_before_crash={str(int(x*100)):None for x in WARN_DRAWDOWNS}
            first_eligible_price=next(p for t,p in at_or_after if t==first_cross)
            exits={}
            for t,p in at_or_after:
                rolling_peak=max(rolling_peak,p)
                drawdown=(1-p/rolling_peak) if rolling_peak>0 else 0
                for dd in WARN_DRAWDOWNS:
                    k=str(int(dd*100))
                    if drawdown>=dd and alerts[k] is None:
                        alerts[k]=t
                        exits[k]={'at':t,'price_multiple_vs_30s':round(p,8),
                                  'running_peak_x_at_exit':round(rolling_peak,8)}
                if drawdown>=CRASH_DRAWDOWN and crash_at is None:
                    crash_at=t
                    # Alerts recorded at the same timestamp offer ZERO lead.
                    for k,warn_at in alerts.items():
                        if warn_at is not None and warn_at<crash_at:
                            alerts_before_crash[k]=round(crash_at-warn_at,6)
            last_time=max(t for t,p in at_or_after)
            horizon_complete=bool(last_time-first_cross>=EVAL_WINDOW_SECONDS
                                  and len(at_or_after)>=3)
            # Time-window label requires observed crash within 120 seconds.
            # No later sample or an earlier end makes it censored, not safe.
            if crash_at is not None and crash_at-first_cross<=EVAL_WINDOW_SECONDS:
                risk_label='crash_90pct_within_120s'
            elif horizon_complete:
                risk_label='no_observed_crash_within_120s'
            else:
                risk_label='unknown_short_followup'
            snap15=d.get("snap_15") if isinstance(d.get("snap_15"),dict) else {}
            replay_records.append({
                'mint':str(d.get('mint')),'created_ts':d.get('created_ts'),
                'cross11_at_sec_after30':first_cross,
                'cross11_chart_price_x':round(first_eligible_price,8),
                'last_observed_sec_after30':last_time,
                'observed_points_at_or_after_11x':len(at_or_after),
                'risk_label_120s':risk_label,
                'crash90_running_peak_at_sec_after30':crash_at,
                'seconds_first_11x_to_running_peak_crash90':(
                   round(crash_at-first_cross,6) if crash_at is not None else None),
                'lead_seconds_before_observed_crash90_warn20':alerts_before_crash['20'],
                'lead_seconds_before_observed_crash90_warn40':alerts_before_crash['40'],
                'lead_seconds_before_observed_crash90_warn60':alerts_before_crash['60'],
                'warn20_first_at':alerts['20'],'warn40_first_at':alerts['40'],
                'warn60_first_at':alerts['60'],
                'observed_chart_11x_to_final_x':round(at_or_after[-1][1]/first_eligible_price,8)
                      if first_eligible_price>0 else None,
                'first_warn20_exit_x_vs_30s':exits.get('20',{}).get('price_multiple_vs_30s'),
                'first_warn40_exit_x_vs_30s':exits.get('40',{}).get('price_multiple_vs_30s'),
                'first_warn60_exit_x_vs_30s':exits.get('60',{}).get('price_multiple_vs_30s'),
                'snap30_trades':number(snap30.get('n_trades')),
                'snap30_unique_buyers':number(snap30.get('uniq_buyers')),
                'snap30_net_sol':number(snap30.get('net_sol')),
                'snap30_dev_share':number(snap30.get('dev_share')),
                'no_liquidity_or_fill_verification':True})
            all_winners.append({
               "mint":d.get("mint"),"symbol_if_recorded":d.get("symbol"),
               "created_ts":d.get("created_ts"),
               "first_11x_crossing_observed_sec_after_30s":min(crossings),
               "peak_chart_x":round(peak_x,6),
               "peak_observed_sec_after_30s":peak_at,
               "post_peak_points_count":len(later),
               "first_50pct_from_peak_observed_sec_after_30s":drop50,
               "minutes_from_peak_to_first_50pct_drop":round((drop50-peak_at)/60,4) if drop50 is not None else None,
               "first_90pct_from_peak_observed_sec_after_30s":drop90,
               "minutes_from_peak_to_first_90pct_drop":round((drop90-peak_at)/60,4) if drop90 is not None else None,
               "observed_path_end_sec_after_30s":max(t for t,p in points),
               "snap15_trades":snap15.get("n_trades"),
               "snap30_trades":snap30.get("n_trades"),
               "snap30_buyers":snap30.get("uniq_buyers"),
               "snap30_net_sol":snap30.get("net_sol"),
               "snap30_dev_share":snap30.get("dev_share"),
               "rugged_flag_if_present":outcome.get("rugged"),
               "not_an_executable_quote":True})

        snap60=d.get("snap_60")
        price60=number(snap60.get("price")) if isinstance(snap60,dict) else None
        if not price60 or price60<=0:
            c["no_valid_60s_baseline_censored"]+=1;continue
        future60=[(t-30,p*price30/price60) for t,p in points if t>30]
        if not future60:
            c["no_price_points_after_60s_censored"]+=1;continue
        c["future_60s_path_observable"]+=1
        crossings60=[t for t,p in future60 if p>=11]
        if crossings60:
            c["chart_ge11_from_60s_baseline"]+=1
            c["first_11x_from60_"+buck(crossings60[0])]+=1
        # 30s and 60s snapshot consistency, when there was a nearby sparse
        # price quote, is an empirical check that the path is normalized as
        # the public README claims (vs 30 second price).
        near=[(abs(t-30),p) for t,p in points if abs(t-30)<=15]
        if near:
            expected=price60/price30
            q=min(near)[1]
            if q>0 and expected>0:
                drift=max(q/expected,expected/q)
                c["path60_near30_comparable"]+=1
                if drift>3:c["path60_snapshot_discrepancy_over_3x"]+=1
        derived.append({"mint":d.get("mint"),"created_ts":d.get("created_ts"),
          "has30_ge11":bool(crossings),"first_ge11_after30s":min(crossings) if crossings else None,
          "has60_ge11":bool(crossings60),"first_ge11_after60s":min(crossings60) if crossings60 else None})
    if c["rows"]<100000:raise RuntimeError("Source coverage too small")
    observed60=c["future_60s_path_observable"]
    stats={"source_sha":SHA,"units":"path seconds after 30s and multiples versus snap_30.price, per source README",
      "counts":dict(c),"eligible_rate_30s":c["chart_ge11_from_30s_baseline"]/c["future_30s_path_observable"],
      "eligible_rate_60s":c["chart_ge11_from_60s_baseline"]/observed60 if observed60 else None,
      "no_outcome_means_censored":True,"no_quote_or_fill_data":True,
      "no_wallets":True,"orders":0,"trades":0,
      "warning":"first observed >=11x in a sampled price path is not continuous first trade crossing, not necessarily fillable",
      "purpose":"Sanity-check whether ranking after 30s or 60s still has time to catch late, non-executable 11x chart rise"}
    # CRASH-WARNING REALITY CHECK. Never count same-sample detection as
    # positive warning: if the price gaps >90% there was no prior warning.
    horizon=[z for z in replay_records if z['risk_label_120s']!='unknown_short_followup']
    positives=[z for z in horizon if z['risk_label_120s']=='crash_90pct_within_120s']
    negatives=[z for z in horizon if z['risk_label_120s']=='no_observed_crash_within_120s']
    warning_results={}
    for pct in (20,40,60):
        alertkey='warn'+str(pct)+'_first_at'
        leadkey='lead_seconds_before_observed_crash90_warn'+str(pct)
        early=[z for z in positives if z[leadkey] is not None and z[leadkey]>0]
        on_crash=[z for z in positives if z[alertkey] is not None
                  and z[leadkey] is None]
        false=[z for z in negatives if z[alertkey] is not None
               and z[alertkey]-z['cross11_at_sec_after30']<=EVAL_WINDOW_SECONDS]
        lead=[z[leadkey] for z in early]
        warning_results[str(pct)+'pct']={
           'positives_warned_before_crash':len(early),
           'positive_crash_cases':len(positives),
           'detected_only_at_crash_no_lead':len(on_crash),
           'no_crash_group_false_warnings':len(false),
           'observed_no_crash_group':len(negatives),
           'warning_lead_median_seconds':round(statistics.median(lead),4) if lead else None,
           'warning_lead_max_seconds':round(max(lead),4) if lead else None,
           'warning_is_chart_only':True}
    feature_contrasts={}
    for col in ('snap30_trades','snap30_unique_buyers','snap30_net_sol','snap30_dev_share',
                'cross11_at_sec_after30'):
        pos=[z[col] for z in positives if z[col] is not None]
        neg=[z[col] for z in negatives if z[col] is not None]
        feature_contrasts[col]={
            'crash_group_median':round(statistics.median(pos),5) if pos else None,
            'no_observed_crash_group_median':round(statistics.median(neg),5) if neg else None,
            'n_crash':len(pos),'n_no_observed_crash':len(neg),
            'descriptive_only_not_an_ex_ante_classifier':True}
    crash_report={
      'scope':'492 earliest observed 11x July launches; sparse 10m chart paths',
      'asof_source_sha':SHA,'total_cross11_mints':len(replay_records),
      'at_risk_after_first_11x':len(horizon),
      'crash90_within_120s_after_first_11x':len(positives),
      'no_observed_crash_with_120s_followup':len(negatives),
      'unknown_followup_censored':len(replay_records)-len(horizon),
      'warnings_before_crash_by_observed_drawdown':warning_results,
      'snapshot_before_cross_descriptive_groups':feature_contrasts,
      'no_unseen_holdout_optimization':True,
      'thresholds_selected_before_scoring':True,
      'same_sample_warning_is_not_lead':True,
      'missing_future_is_not_safe':True,
      'chart_quotes_are_not_trades':True,'trades':0,'orders':0}
    with open(OUT/"crash_warning_first11x_study.json","w",encoding="utf-8") as w:
        json.dump(crash_report,w,indent=2,allow_nan=False)
    if replay_records:
        with open(OUT/"all_11x_online_drawdown_warning_cases.csv","w",newline="",encoding="utf-8") as w:
            writer=csv.DictWriter(w,fieldnames=list(replay_records[0]))
            writer.writeheader();writer.writerows(replay_records)
    print('JULY_FIRST_11X_CRASH_WARNING_REPLAY',json.dumps(crash_report,allow_nan=False))
    stats["11x_observed_peak_has_later_sample"]=known_peak_with_future
    stats["11x_peak_last_observation_no_future"]=peak_no_later_sample
    stats["11x_with_observed_half_peak_drop_after_peak"]=len(crash_times_50)
    stats["11x_with_observed_90pct_peak_drop_after_peak"]=len(crash_times_90)
    stats["cannot_estimate_crash_after_10min_sparse_window"]=True
    stats["crash_detection_is_interval_censored_not_exact_tick"]=True
    stats["winner_mint_list_file"]="all_11x_winners_with_observed_crash_windows.csv"
    if all_winners:
        with open(OUT/"all_11x_winners_with_observed_crash_windows.csv","w",newline="",encoding="utf-8") as w:
            wr=csv.DictWriter(w,fieldnames=list(all_winners[0]))
            wr.writeheader();wr.writerows(all_winners)
    (OUT/"entry_lag_first_crossing.json").write_text(json.dumps(stats,indent=2))
    (OUT/"token_observed_crossings.json").write_text(json.dumps(derived[:2000],indent=2))
    print("JULY_ENTRY_DELAY_REALITY_CHECK",json.dumps(stats))
    print("NO_REALIZED_PNL=TRUE")
if __name__=="__main__":main()
