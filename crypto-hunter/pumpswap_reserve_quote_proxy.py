#!/usr/bin/env python3
"""Independent PumpSwap 5-minute SOL reserve-aware chart-vs-quote-proxy audit.
No trade or solvency guarantees. Uses illustrative constant-product mathematics,
synthetic fee each leg, and NEXT observed bar as entry (avoid same-bar foresight).
No wallet, no signatures, no network orders. Missing later bars = censored.
"""
import gzip,hashlib,json,math,os,urllib.request,collections,statistics
from pathlib import Path
OUT=Path("evidence/amm_proxy");OUT.mkdir(parents=True,exist_ok=True)
ROOT="https://raw.githubusercontent.com/mivor7/pumpfun-pumpswap-market-data/main/pumpswap/"
SHA={
 "20260724":"a4d5f00beb354a9a540695f36109d46c13d683fd4653743e7a58c72e39ac8cf4",
 "20260725":"b2e530baf77dc44f346cb9c68827882525c566b354d28e6abf61bf84278bc6bd",
 "20260726":"904ff618abb75cc3513ba025c083540eb92c0aea9fe460b6407e683d7e77d87a",
 "20260727":"a1a05ad65bde95cc80a6d3d0e4a9580d99589a087a6d93339f4c8a427c937431"}
SIZES_SOL=(.05,.5)
FEE_ASSUMED=0.01 # Illustrative fee EACH LEG, not exact PumpSwap fee schedule.
MIN_RESERVE_SOL=10.
OBS_HORIZON_S=24*3600
def get(day):
    p=OUT/("bars_"+day+".jsonl.gz")
    if not p.exists():
        req=urllib.request.Request(ROOT+"pumpswap_bars_"+day+".jsonl.gz",headers={"User-Agent":"CryptoReserveProxy/1"})
        with urllib.request.urlopen(req,timeout=70) as r,open(str(p)+".part","wb") as w:
            n=0
            while b:=r.read(1<<20):
                n+=len(b)
                if n>20000000:raise ValueError("Unexpected large data file")
                w.write(b)
        os.replace(str(p)+".part",p)
    if hashlib.sha256(p.read_bytes()).hexdigest()!=SHA[day]:
        raise ValueError("Unpinned source content changed: "+day)
    return p
def good(a):
    try:
        n=float(a)
        return n if math.isfinite(n) else None
    except (ValueError,TypeError):return None
def proxy_sell_x(entry,exit,amount):
    """Hypothetical CP AMM, SOL reserve x, token reserve y=x/price; no route proof."""
    ri,pi=entry["res"],entry["px"]
    ro,po=exit["res"],exit["px"]
    if min(ri,pi,ro,po)<=0:return None
    token_reserve_at_buy=ri/pi
    buyer_sol=amount*(1-FEE_ASSUMED)
    tokens_out=token_reserve_at_buy*buyer_sol/(ri+buyer_sol)
    hypothetical_token_reserve_at_sale=ro/po
    tokens_in=tokens_out*(1-FEE_ASSUMED)
    sol_out=ro*tokens_in/(hypothetical_token_reserve_at_sale+tokens_in)
    return sol_out/amount
def main():
    data=collections.defaultdict(list);source_stats=collections.Counter()
    for day in SHA:
        source_stats["archives"]+=1
        for line in gzip.open(get(day),"rt",encoding="utf-8"):
            source_stats["raw_rows"]+=1
            x=json.loads(line)
            pool=x.get("pool")
            mint=x.get("mint")
            t=good(x.get("t"));px=good(x.get("px"));res=good(x.get("sol_res"))
            buys=good(x.get("vb"));sells=good(x.get("vs"));n=good(x.get("n"))
            if not pool or not mint or any(z is None for z in (t,px,res,buys,sells,n)) or px<=0 or res<=0:
                source_stats["invalid_rows"]+=1;continue
            data[pool].append({"t":int(t),"px":px,"res":res,"volume":max(0,buys+sells),
                              "n":n,"mint":mint})
    source_stats["distinct_pools"]=len(data)
    results=[]
    examples=[]
    for pool,events in data.items():
        # Duplicate pool-timestamp bars from source restarts can be inconsistent;
        # use first reported timestamp once, never pick better future performance.
        events.sort(key=lambda x:x["t"])
        unique=[];seen=set()
        for x in events:
            if x["t"] not in seen:unique.append(x);seen.add(x["t"])
        events=unique
        if len(set(x["mint"] for x in events))>1:
            source_stats["inconsistent_pool_mint"]+=1
            continue
        idx=None
        for i,bar in enumerate(events[:-1]):
            # Pre-entry anchor may only depend on its already-known bar fields.
            if bar["res"]>=MIN_RESERVE_SOL and bar["volume"]>=1 and bar["n"]>=10:
                idx=i
                break
        if idx is None:
            source_stats["no_eligible_anchor"]+=1
            continue
        anchor=events[idx];entry=events[idx+1]
        # A hypothesized order lands at NEXT bar close; no simultaneous
        # bar signal+buy price. Skips missing next-bar execution window.
        if entry["t"]-anchor["t"]<=0 or entry["t"]-anchor["t"]>900 or entry["res"]<MIN_RESERVE_SOL:
            source_stats["missing_next_bar_entry_proxy"]+=1
            continue
        fut=[r for r in events[idx+2:] if 0<r["t"]-entry["t"]<=OBS_HORIZON_S]
        if len(fut)<4 or fut[-1]["t"]-entry["t"]<1800:
            source_stats["short_followup_censored"]+=1
            continue
        if any(fut[j]["t"]-fut[j-1]["t"]>3600 for j in range(1,len(fut))):
            source_stats["gap_gt_1h_censored"]+=1
            continue
        source_stats["analyzable_pool_with_30min_followup"]+=1
        chart_peak=max(z["px"]/entry["px"] for z in fut)
        all_peak={}
        liq_peak={}
        for qty in SIZES_SOL:
            avail=[(proxy_sell_x(entry,z,qty),z["res"],z["t"]) for z in fut]
            avail=[p for p in avail if p[0] is not None]
            liquid=[v for v in avail if v[1]>=MIN_RESERVE_SOL]
            all_peak[str(qty)]=max((v[0] for v in avail),default=None)
            liq_peak[str(qty)]=max((v[0] for v in liquid),default=None)
        # Forward first-trigger study: stop / TP are fixed BEFORE outcomes.
        # On 5-minute close sampled bars, first observable eligible trigger
        # wins; never cherry-pick a later maximum. These are NOT actual fills.
        def trigger(target):
            for bar in fut:
                if bar["res"]<MIN_RESERVE_SOL:
                    return {"status":"unquoteable_reserve_below_minimum","mark_x":None,
                            "last_at":bar["t"],"actual_fill":False}
                mark=proxy_sell_x(entry,bar,.05)
                if mark is None:
                    return {"status":"missing_estimate","mark_x":None,
                            "last_at":bar["t"],"actual_fill":False}
                if mark<=.75:
                    return {"status":"stop_first_seen","mark_x":round(mark,8),
                            "last_at":bar["t"],"actual_fill":False}
                if mark>=target:
                    return {"status":"target_first_seen","mark_x":round(mark,8),
                            "last_at":bar["t"],"actual_fill":False}
            return {"status":"still_open_censored","mark_x":None,
                    "last_at":fut[-1]["t"],"actual_fill":False}
        exit3=trigger(3)
        exit11=trigger(11)
        results.append({"pool":pool,"mint":entry["mint"],"anchor_at":anchor["t"],"entry_at":entry["t"],
                        "following_bars":len(fut),"following_elapsed_sec":fut[-1]["t"]-entry["t"],
                        "entry_sol_reserve":entry["res"],"peak_chart_close_x":chart_peak,
                        "best_quote_proxy_0_05_sol_x":all_peak[str(.05)],
                        "best_quote_proxy_liquid_0_05_sol_x":liq_peak[str(.05)],
                        "best_quote_proxy_liquid_0_5_sol_x":liq_peak[str(.5)],
                        "precommitted_target_3x_stop_0_75x":exit3,
                        "precommitted_target_11x_stop_0_75x":exit11,
                        "research_only":True})
    source_stats["pool_rows_eligible"]=len(results)
    def count(pred):return sum(bool(pred(x)) for x in results)
    def metrics(f):
        scores=[r[f] for r in results if r[f] is not None]
        return {"available_count":len(scores),"at_least_2x":sum(x>=2 for x in scores),
                "at_least_5x":sum(x>=5 for x in scores),
                "at_least_11x":sum(x>=11 for x in scores),
                "max_observed_multiple":round(max(scores),5) if scores else None}
    def trigger_counts(field):
        counts=collections.Counter(r[field]["status"] for r in results)
        return {"total":len(results),
                "outcomes":dict(counts),
                "triggered_target_marks":counts["target_first_seen"],
                "triggered_stop_marks":counts["stop_first_seen"],
                "no_reliable_exit_mark":counts["unquoteable_reserve_below_minimum"]+counts["missing_estimate"]+counts["still_open_censored"],
                "target_mark_multiples":[r[field]["mark_x"] for r in results if r[field]["status"]=="target_first_seen"][:20],
                "not_executed_trades":True}
    report={
      "source":"mivor7/pumpfun-pumpswap-market-data","source_dates":list(SHA.keys()),"pinned_sha256":SHA,
      "bar_granularity":"5-minute last-price/volume/reserve on pools with >=1 SOL per source README",
      "source_stats":dict(source_stats),
      "entry_model":"First >=10 SOL pool reserve, >=1 SOL bar volume, >=10 trades as known anchor; hypothetical buy at next 5m bar close if <=15m later and reserve >=10 SOL",
      "followup_model":"At least 4 post-entry bars spanning >=30m, no >60m gap; censored rather than scored if missing",
      "fee_per_leg_assumed":FEE_ASSUMED,
      "quote_proxy":"constant product x*y=k implied token reserve = SOL reserve / last price, synthetic SOL buy and future token sell",
      "min_sale_reserve_sol_for_liquid_proxy":MIN_RESERVE_SOL,
      "max_observed_5m_chart_close_x":metrics("peak_chart_close_x"),
      "quote_proxy_0_05_sol_any_positive_reserve":metrics("best_quote_proxy_0_05_sol_x"),
      "quote_proxy_0_05_sol_sale_reserve_at_least_10":metrics("best_quote_proxy_liquid_0_05_sol_x"),
      "quote_proxy_0_5_sol_sale_reserve_at_least_10":metrics("best_quote_proxy_liquid_0_5_sol_x"),
      "fixed_first_trigger_3x_target_vs_0_75x_stop":trigger_counts("precommitted_target_3x_stop_0_75x"),
      "fixed_first_trigger_11x_target_vs_0_75x_stop":trigger_counts("precommitted_target_11x_stop_0_75x"),
      "count_chart_11x_but_no_liquid_proxy_11x_0_05_sol":count(lambda z:z["peak_chart_close_x"]>=11 and
            (z["best_quote_proxy_liquid_0_05_sol_x"] or 0)<11),
      "meaning":"Upper-bound best observed future BAR quote proxy, not execution or prospective take-profit strategy",
      "no_executable_fills":True,"orders":0,"trades":0,
      "caveats":["Source only PumpSwap post-migration busy pools over 4 days, not all launches or a randomized cohort.",
          "Price/reserves sampled after bars, not simultaneously actionable; route, transaction landing and priority fees unknown.",
          "Assumed constant-product reserves, no token transfer tax, no token extensions, no actual DEX SDK quotes.",
          "Fees are illustrative, likely differ from venue-specific actual fees.",
          "Best FUTURE observed quote proxy is selected with hindsight, NOT a realizable decision rule.",
          "Separate precommitted first-trigger 3x and 11x price-target-vs-0.75 stop studies use earliest observed 5m bar close, not fills or trading receipts.",
          "Even precommitted first-trigger bar closes can gap sharply beyond a stop, and unknown microstructure prevents reliable stop execution.",
          "Solana SOL-denominated prices; profits in SOL proxy exclude SOL/USD movement.",
          "Missing follow-up bars and reserve collapses censored, may overstate success.",
          "Low reserve (<10 SOL) sale proxies excluded to reject many dust price spikes."]}
    top=sorted(results,key=lambda x:x["peak_chart_close_x"],reverse=True)[:12]
    report["top_chart_anomalies"]=[{k:v for k,v in e.items() if k in
        ("mint","entry_at","entry_sol_reserve","peak_chart_close_x",
        "best_quote_proxy_liquid_0_05_sol_x","best_quote_proxy_liquid_0_5_sol_x")} for e in top]
    (OUT/"july_pool_liquidity_quote_proxy.json").write_text(json.dumps(report,indent=2,allow_nan=False))
    print("PUMPSWAP_5M_LIQUIDITY_CONDITIONED_AUDIT",json.dumps(report,allow_nan=False))
    print("QUOTE_PROXY_NOT_EXECUTED_FILL=True")
if __name__=="__main__":main()
