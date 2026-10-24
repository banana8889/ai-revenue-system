import datetime as d,json,time,urllib.request,urllib.error,urllib.parse,os,math,statistics,hashlib
url='https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page='
st={}
USDC='EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
quote_checks=0
MAX_ENTRY_QUOTE_CHECKS=8
MAX_SELL_DEPTH_PROBES_PER_RUN=1
sell_depth_probes_this_run=0
# A bounded 25/100 USDC exact-size buy+sell route book on NEW high-confidence
# research shadows. Both-sided quotation, not a filled initial trade.
# Reserve one buy-and-sell quote study per frozen prediction group.
# No hindsight favoring our preferred rule; <= 8 additional keyless quotes.
MAX_SIZE_MATCHED_ENTRY_STUDIES_PER_RUN=2
size_matched_entry_studies_this_run=0
size_matched_entry_by_group_this_run={'high_activity_early_v1':0,'other_eligible_v1':0}
MAX_SIZE_MATCHED_EXIT_STUDIES_PER_RUN=1
size_matched_exit_studies_this_run=0
sell_mark_checks=0
MAX_SELL_MARKS_PER_RUN=8
MAX_OPEN_QUOTED_POSITIONS=4
MAX_SELL_QUOTE_AGE_SECONDS=900
MAX_SHADOW_CANDIDATES=80
SHADOW_RECLAIM_AFTER_SECONDS=3600
SHADOW_RECLAIM_BELOW_QUOTE_X=2.0
SHADOW_RESEARCH_SECONDS=24*3600
SHADOW_NO_ENTRY_ROUTE_SECONDS=30*60
SHADOW_FLAT_QUOTE_SECONDS=90*60
SHADOW_KEEP_PEAK_QUOTE_X=1.25
# Twenty percent identity-only, 24h tail cohort to observe delayed breakouts.
LONG_TAIL_MAX_ACTIVE=16   # research only; 80 total slots
LONG_TAIL_MODULUS=5
EXPLOSIVE_MULTIPLIERS=(11,21,51,101)  # +1000%/+2000%/+5000%/+10000%
# Freeze competing quote-only exit policies for NEW shadows only.
# No wallet, order, fill, or realized net return.
QUOTE_EXIT_POLICY_VERSION=1
QUOTE_EXIT_STOP_X=0.75
QUOTE_EXIT_TARGETS=(('target_2x',2.0),('target_11x',11.0))
# Forward-only split exit: half inventory quote at >=2x, remaining half
# after a later >=11x, no fill assumptions, strict per-run API budget.
MOONSHOT_SPLIT_VERSION=1
MOONSHOT_SPLIT_MIN_LIQUIDITY_USD=10000
MOONSHOT_SPLIT_PRINCIPAL_QUOTE_USDC=5.25
MAX_MOONSHOT_SPLIT_QUOTES_PER_RUN=2
moonshot_split_quotes_this_run=0
def probe_moonshot_split(sh,mint,pool,token_id,at,multiple,liquidity):
    """Two NON-ATOMIC half-lot quotes. Never calculate half value by scaling."""
    global moonshot_split_quotes_this_run
    if (sh.get('moonshot_split_version')!=MOONSHOT_SPLIT_VERSION
            or not sh.get('entry_quote') or num(liquidity)<MOONSHOT_SPLIT_MIN_LIQUIDITY_USD
            or moonshot_split_quotes_this_run>=MAX_MOONSHOT_SPLIT_QUOTES_PER_RUN):
        return None
    first=sh.get('moonshot_split_first_leg_quote_v1')
    if not first:
        if multiple<2:return None
        leg='recover_principal_quote'
    else:
        if (multiple<11 or not first.get('gross_principal_buffer_observed')
                or sh.get('moonshot_split_second_leg_quote_v1')
                or at<=first['at']):
            return None
        leg='remaining_half_moonshot_quote'
    raw=int(sh['entry_quote'].get('token_raw') or 0)//2
    if raw<=0:return None
    moonshot_split_quotes_this_run+=1
    q=jupiter_quote(mint,USDC,raw)
    usd=(q['out_amount']/1000000) if q.get('ok') else None
    result={'at':d.datetime.now(d.timezone.utc).isoformat(),
        'trigger_full_lot_quote_at':at,'leg':leg,'half_token_raw_amount':raw,
        'full_lot_quote_multiple_at_trigger':round(num(multiple),8),
        'observed_liquidity_usd':round(num(liquidity),2),
        'half_lot_sale_quote_usdc':usd,'half_sell_route_quoted':bool(q.get('ok')),
        'gross_principal_buffer_observed':bool(leg=='recover_principal_quote'
           and usd is not None and usd>=MOONSHOT_SPLIT_PRINCIPAL_QUOTE_USDC),
        'no_actual_fill':True,'no_sold_tokens':True,
        'gas_mev_execution_excluded':True}
    out({'kind':'moonshot_split_half_lot_quote_only','pool':pool,
         'token_id':token_id,**result,'orders':0,'trades':0})
    if q.get('ok'):
        key=('moonshot_split_first_leg_quote_v1' if leg=='recover_principal_quote'
             else 'moonshot_split_second_leg_quote_v1')
        sh[key]=result
        if leg=='remaining_half_moonshot_quote':
            sh['moonshot_split_two_sequential_quotes_v1']={
                'first_quote_usdc':first['half_lot_sale_quote_usdc'],
                'second_quote_usdc':usd,
                'sum_usdc_if_both_quotes_could_have_executed':
                   round(first['half_lot_sale_quote_usdc']+usd,6),
                'not_a_realized_return':True,'trades':0}
    return result

def update_frozen_quote_exit_policies(sh,quote_multiple,quote_at,pool,token_id):
    """First threshold seen in chronological quotes; not a future peak."""
    if sh.get('quote_exit_policy_version')!=QUOTE_EXIT_POLICY_VERSION:
        return 0
    try:
        signal=d.datetime.fromisoformat(sh['at'])
        marked=d.datetime.fromisoformat(quote_at)
        elapsed=(marked-signal).total_seconds()
        q=float(quote_multiple)
    except (KeyError,ValueError,TypeError):
        return 0
    if not math.isfinite(q) or q<=0 or elapsed<=0:
        return 0
    decisions=sh.setdefault('quote_exit_policy_decisions',{})
    made=0
    for label,target in QUOTE_EXIT_TARGETS:
        if label in decisions:
            continue
        if q<=QUOTE_EXIT_STOP_X:
            reason='first_observed_stop_quote'
        elif q>=target:
            reason='first_observed_target_quote'
        elif elapsed>=SHADOW_RESEARCH_SECONDS:
            reason='first_observed_24h_horizon_quote'
        else:
            continue
        decisions[label]={'at':quote_at,'elapsed_minutes':round(elapsed/60,2),
            'target_x':target,'stop_x':QUOTE_EXIT_STOP_X,
            'reason':reason,'observed_quote_multiple':round(q,8),
            'no_fill':True,'not_executed':True,'gas_rent_mev_excluded':True}
        made+=1
        out({'kind':'frozen_quote_exit_policy_decision','at':quote_at,
            'pool':pool,'token_id':token_id,'policy':label,
            **decisions[label],'orders':0,'trades':0})
    return made
# Fixed live hypothesis, 19 Oct 2026. Never relabel prior observations.
PROSPECTIVE_ACTIVITY_VOLUME_USD=50000
PROSPECTIVE_ACTIVITY_BUYERS=450
def prospective_activity_arm(volume_usd,buyers):
    return ('high_activity_50k_450' if num(volume_usd)>=PROSPECTIVE_ACTIVITY_VOLUME_USD
            and num(buyers)>=PROSPECTIVE_ACTIVITY_BUYERS else 'other_eligible')
# Three explicit, fixed v2 prospective moonshot signal groups. Designed
# after inspecting data through 24 Oct 2026. Historical comparisons
# below are retrospective, NOT an untouched blind test.
MOONSHOT_RULES_V2={
  'age5_pressure2':{'age_max':5,'min_pressure':2},
  'age5_volume50k_buyers450':{'age_max':5,'min_volume':50000,'min_buyers':450},
  'age5_volume20k_buyers100':{'age_max':5,'min_volume':20000,'min_buyers':100}}
def moonshot_rule_tags_v2(age,volume,buyers,buys,sells):
    pressure=num(buys)/max(1,num(sells))
    values={'age_max':num(age),'min_volume':num(volume),
            'min_buyers':num(buyers),'min_pressure':pressure}
    return [name for name,rule in MOONSHOT_RULES_V2.items()
            if values['age_max']<=rule['age_max']
            and all(values[feature]>=threshold for feature,threshold in rule.items()
                    if feature!='age_max')]
def tail_is_selected(mint):
    if not mint:return False
    digest=hashlib.sha256(str(mint).encode('utf-8')).digest()
    return int.from_bytes(digest[:8],'big')%LONG_TAIL_MODULUS==0
def seed_long_tail_group(state):
    # LEGACY BACKFILL is recorded separately from prospective assignment.
    active=sum(bool(z.get('shadow') and not z['shadow'].get('expired')
                    and z['shadow'].get('long_tail_24h')) for z in state.values())
    candidates=sorted((z.get('token_id') or pool,pool) for pool,z in state.items()
                      if z.get('shadow') and not z['shadow'].get('expired')
                      and 'long_tail_24h' not in z['shadow'])
    count=0
    for mint,pool in candidates:
        sh=state[pool]['shadow']
        chosen=active<LONG_TAIL_MAX_ACTIVE and tail_is_selected(mint)
        sh['long_tail_24h']=bool(chosen)
        sh['long_tail_assignment_method']='sha256_mint_mod5_legacy_backfill'
        if chosen:active+=1;count+=1
    return count
long_tail_seeded_this_run=0
shadow_retirements_this_run={}
def shadow_retirement_reason(sh, now):
    """No future knowledge: early retirement is research censoring, NOT a fill."""
    try:age=(now-d.datetime.fromisoformat(sh['at'])).total_seconds()
    except (ValueError,TypeError,KeyError):return 'invalid_timestamp_censored'
    if age>SHADOW_RESEARCH_SECONDS:return 'natural_24h'
    if sh.get('long_tail_24h'):return None  # preserve delayed explosion evidence
    if age>=SHADOW_NO_ENTRY_ROUTE_SECONDS and not sh.get('entry_quote'):
        return 'no_two_sided_entry_quote_30m'
    if (age>=SHADOW_FLAT_QUOTE_SECONDS and sh.get('entry_quote')
        and float(sh.get('max_exit_quote_multiple') or 0)<SHADOW_KEEP_PEAK_QUOTE_X
        and not sh.get('quote_11x_logged')):
        return 'no_promising_later_quote_90m'
    return None
# Pre-registered equal-budget pressure versus control pools. No wallet/trades.
COHORT_MAX_PER_GROUP=12
COHORT_HORIZON_SEC=24*3600
COHORT_MIN_OBSERVATIONS=4
# CI fixtures default to three data cycles. The hosted canonical study requests
# nine cycles to cut its six-minute blind cooldown while staying within a bounded
# 20-minute job. The maximum prevents accidental unbounded requests.
try:CYCLES_PER_RUN=max(3,min(12,int(os.getenv('CRYPTO_STUDY_CYCLES','3'))))
except (TypeError,ValueError):CYCLES_PER_RUN=3
COHORT_GROUPS=('buy_pressure','lower_pressure_control')
cohort_added_this_run={x:0 for x in COHORT_GROUPS}
# Precommitted matched 5-USDC buy+sell quotes at FIRST DISCOVERY for
# independent pressure/control arms. Preserve denominator when route missing.
# Max two extra entry checks and two sell checks per run, not live orders.
COHORT_EXECUTABILITY_STUDY_VERSION=1
COHORT_MIN_LIQUIDITY_FOR_EXIT_USD=10000
COHORT_ENTRY_QUOTE_PER_ARM_PER_RUN=1
COHORT_SELL_QUOTE_PER_ARM_PER_RUN=1
cohort_entry_quote_checks={x:0 for x in COHORT_GROUPS}
cohort_followup_quote_checks={x:0 for x in COHORT_GROUPS}
shadow_new_this_run=0
shadow_chart_11x_this_run=0
shadow_quote_entries_this_run=0
shadow_quote_marks_this_run=0
shadow_quote_marks_available_this_run=0
frozen_policy_decisions_this_run=0
MAX_SHADOW_SELL_QUOTES_PER_RUN=8
# Guarantee some quote capacity for NEW quoteable candidates in the SAME
# study (first follow-up is ~65s), not ~13 minutes at the next self-dispatch.
FRESH_SHADOW_QUOTE_RESERVE=4
# Reserve two quote slots for abrupt chart acceleration, BEFORE an 11x candle.
# A spot price burst is merely a request for a real later sell quotation.
BURST_QUOTE_RESERVE=2
BURST_CHART_TRIGGER_X=1.8
BURST_MIN_LIQUIDITY_USD=10000
HOT_QUOTE_THRESHOLD_X=1.8
HOT_QUOTE_MAX_AGE_SECONDS=3600
HOT_QUOTE_MIN_LIQUIDITY_USD=10000
HOT_QUOTE_RESERVE=1
hot_quote_slots_this_run=set()
fresh_shadow_slots_this_run=set()
burst_quote_slots_this_run=set()
def burst_quote_eligible(sh, gross, liquidity, scheduled, quoted, pool, burst_slots):
    return bool(sh.get('entry_quote') and not sh.get('expired')
                and pool not in scheduled and pool not in quoted
                and burst_slots<BURST_QUOTE_RESERVE
                and gross>=BURST_CHART_TRIGGER_X
                and liquidity>=BURST_MIN_LIQUIDITY_USD)

shadow_quoted_this_run=set()
sell_marked_this_run=set()
blocked_candidates_this_run=set()
shadow_quote_scheduled_this_run=set()
first_seen_ages_minutes_this_run=[]
first_seen_unique_mints_this_run=set()
first_eligible_signals_this_run=0
first_eligible_with_discovery_baseline_this_run=0
first_eligible_already_2x_this_run=0
def hot_shadow_quote_targets(state, selected, now, budget):
    """Prioritize RECENT ROUTE quotes, never chart highs or hindsight prices."""
    ranked=[]
    for pool,item in state.items():
        sh=item.get('shadow')
        if (pool in selected or item.get('entry') or not sh or sh.get('expired')
                or not sh.get('entry_quote')):
            continue
        try:
            multiple=float(sh.get('last_exit_quote_multiple') or 0)
            quoted_at=d.datetime.fromisoformat(sh['last_exit_quote_at'])
            freshness=(now-quoted_at).total_seconds()
            liquidity=float(item.get('last_observed_liquidity_usd') or 0)
            liquidity_at=d.datetime.fromisoformat(item['last_liquidity_seen_at'])
            liquidity_freshness=(now-liquidity_at).total_seconds()
        except (ValueError,TypeError,KeyError):
            continue
        if (multiple>=HOT_QUOTE_THRESHOLD_X and 0<=freshness<=HOT_QUOTE_MAX_AGE_SECONDS
                and liquidity>=HOT_QUOTE_MIN_LIQUIDITY_USD
                and 0<=liquidity_freshness<=HOT_QUOTE_MAX_AGE_SECONDS):
            ranked.append((-multiple,quoted_at.isoformat(),pool))
    ranked.sort()
    return [pool for _,_,pool in ranked[:max(0,budget)]]

def fair_shadow_quote_targets(state, selected, budget):
    """Choose previously unserved shadows before repeating earlier quote checks.

    State is a checkpoint: last_quote_scheduled_at persists through workflows.
    The timestamp tracks scheduling, NOT an actually observed sell quote.
    """
    waiting=[(pool,item['shadow']) for pool,item in state.items()
             if pool not in selected and not item.get('entry')
             and item.get('shadow') and not item['shadow'].get('expired')
             and item['shadow'].get('entry_quote')]
    waiting.sort(key=lambda pair:(pair[1].get('last_quote_scheduled_at') or '',
                                  pair[1].get('at') or '',pair[0]))
    return [pool for pool,shadow in waiting[:max(0,budget-len(selected))]]
try:
    with open('evidence/state.json') as f:st=json.load(f)
    for a in st.values():
        a['first']=d.datetime.fromisoformat(a['first'])
        if a.get('last_distinct_at'):a['last_distinct_at']=d.datetime.fromisoformat(a['last_distinct_at'])
        if a.get('entry'):a['entry']=[d.datetime.fromisoformat(a['entry'][0]),a['entry'][1]]
        if a.get('previous_signature'):a['previous_signature']=tuple(a['previous_signature'])
except FileNotFoundError:pass
def out(x):
    with open('evidence/observations.jsonl','a') as f:f.write(json.dumps(x,allow_nan=False)+'\n')
def num(x):
    try:
        v=float(x);return v if math.isfinite(v) else 0.
    except (TypeError,ValueError):return 0.
def read(endpoint):
    req=urllib.request.Request(endpoint,headers={'Accept':'application/json;version=20230203','User-Agent':'MoonshotObserver/1.1'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req,timeout=20) as r:
                return json.load(r)['data']
        except urllib.error.HTTPError as e:
            if e.code not in (429,500,502,503,504) or attempt==2:raise
        except (urllib.error.URLError,TimeoutError):
            if attempt==2:raise
        time.sleep(3*(attempt+1))

def jupiter_quote(input_mint,output_mint,raw_amount):
    """No taker, no transaction, no API key, strictly a read-only quotation."""
    time.sleep(2.3)  # keyless gateway 0.5 requests/second
    params=urllib.parse.urlencode({'inputMint':input_mint,'outputMint':output_mint,'amount':str(raw_amount)})
    req=urllib.request.Request('https://api.jup.ag/swap/v2/order?'+params,headers={'User-Agent':'CryptoHunterRouteStudy/1.0','Accept':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=15) as resp:body=json.load(resp)
        amount=int(body.get('outAmount') or 0)
        return {'ok':amount>0,'out_amount':amount,'router':body.get('router'),'fee_bps':body.get('feeBps'),'error_code':body.get('errorCode')}
    except urllib.error.HTTPError as e:
        return {'ok':False,'http':e.code,'error':'no_quote'}
    except (ValueError,TypeError,urllib.error.URLError,TimeoutError):
        return {'ok':False,'error':'quote_unavailable'}
def probe_sell_depth(mint,raw_tokens,reference_sell_raw,observed_at,pool,token_id):
    """Read-only size sensitivity after a real prospective 11x exit quote.
    The scaled token inventory has NO 25/100 USDC buy-price baseline.
    Sequential API quotes are not simultaneous or executable fills.
    """
    global sell_depth_probes_this_run
    if sell_depth_probes_this_run>=MAX_SELL_DEPTH_PROBES_PER_RUN:
        return None
    if not raw_tokens or not reference_sell_raw or int(raw_tokens)<=0 or int(reference_sell_raw)<=0:
        return None
    sell_depth_probes_this_run+=1
    outcomes=[]
    for factor in (5,20):
        q=jupiter_quote(mint,USDC,int(raw_tokens)*factor)
        available=bool(q.get('ok'))
        ref=int(reference_sell_raw)*factor
        retention=(q['out_amount']/ref if available and ref>0 else None)
        result={'hypothetical_token_inventory_factor':factor,
                'sell_quote_available':available,
                'sell_quote_usdc':q['out_amount']/1e6 if available else None,
                'proportional_quote_retention':round(retention,8) if retention is not None else None,
                'quote_router':q.get('router'),'http_status':q.get('http'),
                'sequential_not_atomic':True,'no_buy_baseline_at_this_size':True,'no_fill':True}
        outcomes.append(result)
        out({'kind':'sell_depth_quote_only','at':d.datetime.now(d.timezone.utc).isoformat(),
             'reference_quote_at':observed_at,'pool':pool,'token_id':token_id,
             **result,'no_trade':True,'not_executable_proof':True})
    return {'reference_token_raw':int(raw_tokens),
            'reference_sell_quote_usdc':int(reference_sell_raw)/1e6,
            'levels':outcomes,'no_execution':True,'no_realized_profit':True}

def probe_size_matched_entry(mint,pool,token_id):
    """At signal time, obtain actual $25 and $100 buy-and-sell BACK quotes."""
    global size_matched_entry_studies_this_run
    if size_matched_entry_studies_this_run>=MAX_SIZE_MATCHED_ENTRY_STUDIES_PER_RUN:
        return None
    size_matched_entry_studies_this_run+=1
    measured={}
    for usdc in (25,100):
        buy=jupiter_quote(USDC,mint,usdc*1000000)
        raw=int(buy.get('out_amount') or 0)
        sell=jupiter_quote(mint,USDC,raw) if buy.get('ok') and raw>0 else {'ok':False}
        acceptable=bool(sell.get('ok') and sell['out_amount']/(usdc*1000000)>=0.85)
        measured[str(usdc)]={'usdc_at_signal':usdc,
            'entry_token_raw_quote_only':raw if buy.get('ok') else None,
            'entry_buy_quote_available':bool(buy.get('ok')),
            'instant_sellback_quote_available':bool(sell.get('ok')),
            'instant_sellback_multiple':(round(sell['out_amount']/(usdc*1000000),8)
                                         if sell.get('ok') else None),
            'roundtrip_gate_passed':acceptable,
            'buy_router':buy.get('router'),'sell_router':sell.get('router'),
            'no_fill':True,'no_transaction':True}
    at=d.datetime.now(d.timezone.utc).isoformat()
    out({'kind':'size_matched_entry_quotes_only','at':at,'pool':pool,
         'token_id':token_id,'sizes':measured,'no_orders':True,'no_fill':True})
    return {'quoted_at':at,'sizes':measured,'no_entry_filled':True}

def probe_size_matched_exit(sh,mint,pool,token_id,reference_quote_at,trigger_tier='11x'):
    """Reprice actual historical $25/$100 buy-quoted token amounts at 2x or 11x.

    Both sides are quote-only. Never equate a quote with actual fill or net P&L.
    """
    global size_matched_exit_studies_this_run
    if size_matched_exit_studies_this_run>=MAX_SIZE_MATCHED_EXIT_STUDIES_PER_RUN:
        return None
    if not sh.get('size_matched_entry_v1'):
        return None
    available=[(usdc,z) for usdc,z in sh['size_matched_entry_v1']['sizes'].items()
               if z.get('roundtrip_gate_passed') and z.get('entry_token_raw_quote_only')]
    if not available:
        return None
    size_matched_exit_studies_this_run+=1
    results={}
    for usdc,original in available:
        quote=jupiter_quote(mint,USDC,int(original['entry_token_raw_quote_only']))
        results[usdc]={'entry_usdc_reference':int(usdc),
            'original_token_raw_from_signal_buy_quote':original['entry_token_raw_quote_only'],
            'later_sell_quote_available':bool(quote.get('ok')),
            'later_usdc_quote_only':(quote['out_amount']/1000000 if quote.get('ok') else None),
            'later_quote_multiple_unfilled':(round(quote['out_amount']/(int(usdc)*1000000),8)
                                            if quote.get('ok') else None),
            'router':quote.get('router'),
            'quote_unavailable_unknown':not bool(quote.get('ok')),
            'no_execution':True}
    marked=d.datetime.now(d.timezone.utc).isoformat()
    out({'kind':'size_matched_later_sell_quotes_only','at':marked,
        'trigger_tier':trigger_tier,
        'original_reference_quote_at':reference_quote_at,'pool':pool,
        'token_id':token_id,'sizes':results,'no_trades':True,'no_fills':True,
        'gas_rent_mev_not_included':True})
    return {'quoted_at':marked,'original_reference_quote_at':reference_quote_at,
            'trigger_tier':trigger_tier,
            'sizes':results,'no_executed_trades':True,'not_net_profits':True}

def paper_entry_risk_reason(now):
    """Prevent more simulated exposure when previously opened positions cannot be marked."""
    open_positions=[p for p in st.values() if p.get('entry_quote') and p.get('entry') and not p.get('exit') and not p.get('quote_study_censored_at')]
    if len(open_positions)>=MAX_OPEN_QUOTED_POSITIONS:
        return 'unclosed_quote_position_capacity'
    for position in open_positions:
        if position.get('quote_missing_streak',0)>0:
            return 'existing_position_missing_sell_quote'
        last=position.get('last_valid_sell_quote_at') or position['entry_quote'].get('observed_at')
        if not last:
            return 'existing_position_unpriced'
        try:
            age=(now-d.datetime.fromisoformat(last)).total_seconds()
            if age>MAX_SELL_QUOTE_AGE_SECONDS:
                return 'existing_sell_quote_stale'
        except (ValueError,TypeError):
            return 'existing_sell_quote_timestamp_invalid'
    return None

def censor_failed_quote_research(now):
    """Retire dead quote-only studies from admission limits; NEVER assume a sale."""
    retired=0
    for pool,item in st.items():
        if not item.get('entry_quote') or not item.get('entry') or item.get('exit') or item.get('quote_study_censored_at'):
            continue
        if item.get('quote_missing_streak',0)<3:
            continue
        liquidity=item.get('last_observed_liquidity_usd')
        try:
            age=(now-item['entry'][0]).total_seconds()
        except (TypeError,ValueError,IndexError):
            continue
        if liquidity is not None and num(liquidity)<100 and age>=1800:
            reason='missing_sell_route_with_liquidity_below_100_usd'
        elif age>=3600 and item.get('quote_missing_streak',0)>=4:
            reason='persistent_missing_sell_route_over_1h'
        else:
            continue
        item['quote_study_censored_at']=now.isoformat()
        item['quote_study_censored_reason']=reason
        item['quote_study_no_exit_fill']=True
        retired+=1
        out({'kind':'quote_study_censored_no_fill','at':now.isoformat(),'pool':pool,
             'token_id':item.get('token_id'),'failed_sell_checks':item.get('quote_missing_streak'),
             'last_liquidity_usd':liquidity,'reason':item['quote_study_censored_reason'],
             'no_exit_quote':True,'no_trade':True,'no_fill':True,'no_realized_return':True})
    return retired

def reclaim_weak_shadow_slot(now):
    """Censor one weak old non-tail watch to admit a fresh live candidate."""
    candidates=[]
    for pool,item in st.items():
        sh=item.get('shadow')
        if not sh or sh.get('expired') or item.get('entry') or sh.get('long_tail_24h'):
            continue
        try:
            age=(now-d.datetime.fromisoformat(sh['at'])).total_seconds()
            peak=float(sh.get('max_exit_quote_multiple') or 0)
        except (ValueError,TypeError,KeyError):
            continue
        if (age>=SHADOW_RECLAIM_AFTER_SECONDS and peak<SHADOW_RECLAIM_BELOW_QUOTE_X
                and not sh.get('quote_11x_logged')):
            candidates.append((sh['at'],pool,sh,peak))
    if not candidates:return None
    _,pool,sh,peak=min(candidates,key=lambda row:(row[0],row[1]))
    sh['expired']=True
    sh['expired_at']=now.isoformat()
    sh['expired_reason']='capacity_reclaimed_non_tail_under_2x'
    sh['full_24h_observation']=False
    sh['shortened_24h_outcome_censored']=True
    reason=sh['expired_reason']
    shadow_retirements_this_run[reason]=shadow_retirements_this_run.get(reason,0)+1
    out({'kind':'shadow_research_retired','at':sh['expired_at'],
         'pool':pool,'token_id':st[pool].get('token_id'),'reason':reason,
         'peak_quote_unfilled':peak,'completed_24h':False,'outcome_censored':True,
         'no_actual_sell':True,'no_trade':True})
    return pool

def entry_quote_preflight(mint):
    """Require both exact-size 5 USDC entry and sell-back paths at observation time."""
    global quote_checks
    if quote_checks>=MAX_ENTRY_QUOTE_CHECKS:
        return {'ok':False,'reason':'quota_deferred'}
    quote_checks+=1
    entry=jupiter_quote(USDC,mint,5000000)
    if not entry.get('ok'):return {'ok':False,'reason':'buy_route_missing','buy':entry}
    exit_quote=jupiter_quote(mint,USDC,entry['out_amount'])
    if not exit_quote.get('ok'):return {'ok':False,'reason':'sell_route_missing','buy':entry,'sell':exit_quote}
    net=exit_quote['out_amount']/5000000
    # This is a same-moment price check, not an executed fill; gas/rent/MEV excluded.
    return {'ok':net>=0.85,'reason':'route_verified' if net>=0.85 else 'quote_roundtrip_too_costly',
            'initial_usdc':5,'token_raw':entry['out_amount'],'immediate_roundtrip_usdc':exit_quote['out_amount']/1000000,
            'immediate_roundtrip_multiple':round(net,8),
            'buy_router':entry.get('router'),'sell_router':exit_quote.get('router'),
            'buy_fee_bps':entry.get('fee_bps'),'sell_fee_bps':exit_quote.get('fee_bps'),
            'observed_at':d.datetime.now(d.timezone.utc).isoformat(),'quote_only':True}

for cycle in range(CYCLES_PER_RUN):
    t=d.datetime.now(d.timezone.utc)
    if cycle==0:
        long_tail_seeded_this_run=seed_long_tail_group(st)
        out({'kind':'long_tail_identity_cohort_seeded','at':t.isoformat(),
             'legacy_watches_assigned':long_tail_seeded_this_run,
             'method':'sha256_mint_mod5','no_trades':True})
    censor_failed_quote_research(t)
    # Rotate research observations while archiving early dropouts as CENSORED,
    # never treating them as 24h negatives, filled exits or market orders.
    for pool,old in st.items():
        sh=old.get('shadow')
        if sh and not sh.get('expired'):
            why=shadow_retirement_reason(sh,t)
            if why:
                sh['expired']=True
                sh['expired_at']=t.isoformat()
                sh['expired_reason']=why
                sh['full_24h_observation']=why=='natural_24h'
                sh['shortened_24h_outcome_censored']=why!='natural_24h'
                shadow_retirements_this_run[why]=shadow_retirements_this_run.get(why,0)+1
                out({'kind':'shadow_research_retired','at':t.isoformat(),
                     'pool':pool,'token_id':old.get('token_id'),
                     'reason':why,'completed_24h':why=='natural_24h',
                     'no_actual_sell':True,'no_trade':True})
    # Persisted fair rotation avoids repeatedly quoting the oldest five.
    if cycle==0:
        # Reserve four of eight exact-size sell checks for fresh signals.
        # Scheduling a quote is not evidence that one was received.
        old_budget=max(0,MAX_SHADOW_SELL_QUOTES_PER_RUN-FRESH_SHADOW_QUOTE_RESERVE-BURST_QUOTE_RESERVE)
        for pool in hot_shadow_quote_targets(st,shadow_quote_scheduled_this_run,t,
                                              min(HOT_QUOTE_RESERVE,old_budget)):
            shadow_quote_scheduled_this_run.add(pool)
            hot_quote_slots_this_run.add(pool)
            st[pool]['shadow']['last_quote_scheduled_at']=t.isoformat()
            out({'kind':'shadow_sell_quote_scheduled','at':t.isoformat(),'pool':pool,
                 'quota_slot':len(shadow_quote_scheduled_this_run),
                 'quote_fetched':False,'no_trade':True,'lane':'recent_route_quote_hot'})
        for pool in fair_shadow_quote_targets(st,shadow_quote_scheduled_this_run,old_budget):
            shadow_quote_scheduled_this_run.add(pool)
            st[pool]['shadow']['last_quote_scheduled_at']=t.isoformat()
            out({'kind':'shadow_sell_quote_scheduled','at':t.isoformat(),'pool':pool,
                 'quota_slot':len(shadow_quote_scheduled_this_run),
                 'quote_fetched':False,'no_trade':True,'lane':'existing'})
    if cycle==CYCLES_PER_RUN-1:
        # Use any fresh-candidate slots not needed this job. These old pools
        # can still be quoted in THIS final cycle if their market is present.
        # Keep emergency quote budget through the last sample; the remaining
        # two unused slots may be reported as unused, NEVER as acquired quotes.
        release_budget=MAX_SHADOW_SELL_QUOTES_PER_RUN-BURST_QUOTE_RESERVE+len(burst_quote_slots_this_run)
        for pool in fair_shadow_quote_targets(st,shadow_quote_scheduled_this_run,release_budget):
            shadow_quote_scheduled_this_run.add(pool)
            st[pool]['shadow']['last_quote_scheduled_at']=t.isoformat()
            out({'kind':'shadow_sell_quote_scheduled','at':t.isoformat(),'pool':pool,
                 'quota_slot':len(shadow_quote_scheduled_this_run),
                 'quote_fetched':False,'no_trade':True,'lane':'unused_fresh_capacity'})
    try:
        data=[]
        for page in (1,2,3):
            try:
                data.extend(read(url+str(page)))
            except Exception as ex:
                out({'kind':'discovery_page_error','at':t.isoformat(),'page':page,'error':str(ex)[:120]})
                if page==1:raise
            time.sleep(2)
        seen=set()
        data=[p for p in data if p.get('attributes',{}).get('address') and not (p['attributes']['address'] in seen or seen.add(p['attributes']['address']))]
        active=[a for a,x in st.items() if (x.get('entry') and not x.get('exit') and not x.get('quote_study_censored_at')) or (x.get('qualified',0)>0 and not x.get('entry') and (t-x['first']).total_seconds()<1200)]
        shadows=[p for p,x in st.items() if x.get('shadow') and not x.get('entry') and not x['shadow'].get('expired')
                 and (t-d.datetime.fromisoformat(x['shadow']['at'])).total_seconds()<=SHADOW_RESEARCH_SECONDS]
        shadows.sort(key=lambda p: st[p]['shadow']['at'])
        active.extend(p for p in shadows[:MAX_SHADOW_CANDIDATES] if p not in active)
        cohorts=[k for k,z in st.items() if z.get('cohort') and not z['cohort'].get('completed')
                 and not z['cohort'].get('censored')]
        active.extend(k for k in cohorts if k not in active)
        active.sort(key=lambda p: st[p].get('last_sell_quote_at') or '')
        for n in range(0,len(active),30):
            try:
                more=read('https://api.geckoterminal.com/api/v2/networks/solana/pools/multi/'+','.join(active[n:n+30]))
                present={p.get('attributes',{}).get('address') for p in data}
                data.extend(p for p in more if p.get('attributes',{}).get('address') not in present)
            except Exception as ex:out({'kind':'tracked_pool_error','at':t.isoformat(),'error':str(ex)[:120]})
            time.sleep(4)
        out({'kind':'sample','at':t.isoformat(),'cycle':cycle,'pool_count':len(data)})
        # Rebuild O(1) identity indexes once per live observation cycle.
        entry_token_ids={z.get('token_id') for z in st.values() if z.get('entry') and z.get('token_id')}
        active_shadow_mints={z.get('token_id') for z in st.values() if z.get('shadow') and not z['shadow'].get('expired') and not z.get('entry') and z.get('token_id')}
        active_shadow_count=sum(1 for z in st.values() if z.get('shadow') and not z['shadow'].get('expired') and not z.get('entry'))
        active_tail_count=sum(1 for z in st.values() if z.get('shadow') and not z['shadow'].get('expired') and z['shadow'].get('long_tail_24h'))
        for obj in data:
            a=obj.get('attributes') or {}
            p=a.get('address')
            v=num(a.get('base_token_price_usd'))
            if not p or v<=0:continue
            tx=((a.get('transactions') or {}).get('h1') or {})
            created=a.get('pool_created_at') or ''
            try:age=(t-d.datetime.fromisoformat(created.replace('Z','+00:00'))).total_seconds()/60
            except (ValueError,TypeError):age=999999
            rel=obj.get('relationships') or {}
            token_id=((rel.get('base_token') or {}).get('data') or {}).get('id')
            quote_id=((rel.get('quote_token') or {}).get('data') or {}).get('id')
            x={'kind':'pool','at':t.isoformat(),'pool':p,'pair':a.get('name'),'token_id':token_id,'quote_id':quote_id,'price':v,
               'age_minutes':round(age,2),'liq':num(a.get('reserve_in_usd')),
               'volume_1h':num((a.get('volume_usd') or {}).get('h1')),
               'buys_1h':int(tx.get('buys') or 0),'sells_1h':int(tx.get('sells') or 0),
               'buyers_1h':int(tx.get('buyers') or 0),'dex':((obj.get('relationships') or {}).get('dex') or {}).get('data')}
            out(x)
            if p not in st and token_id and token_id.startswith('solana_') and token_id not in first_seen_unique_mints_this_run:
                first_seen_unique_mints_this_run.add(token_id)
                if age>=0 and math.isfinite(age) and age<100000:
                    first_seen_ages_minutes_this_run.append(age)
            s=st.setdefault(p,{'count':0,'first':t,'entry':None,'peak_net':0.,'previous_signature':None,'last_distinct_at':None,'qualified':0,'exit':None})
            if token_id:s['token_id']=token_id
            # Persist the genuine FIRST discovery observation, never
            # reconstruct a discovery price from future market observations.
            if s['first']==t and 'first_discovery_price_v1' not in s:
                s['first_discovery_price_v1']=v
                s['first_discovery_observed_at_v1']=t.isoformat()
                s['first_discovery_age_minutes_v1']=round(age,4)

            # Cohort selected only from FIRST discovery record, before future prices.
            # Both groups have identical minimum age, liquidity, buyer and seller filters.
            if (not s.get('cohort') and s['count']==0 and not s.get('entry') and
                not s.get('shadow') and token_id and token_id.startswith('solana_')
                and 0<=age<=20 and x['liq']>=10000 and x['buyers_1h']>=20 and x['sells_1h']>=1):
                group=('buy_pressure' if x['buys_1h']>=1.5*x['sells_1h']
                       else 'lower_pressure_control')
                occupied=sum(z.get('cohort',{}).get('group')==group
                             and not z['cohort'].get('completed') and not z['cohort'].get('censored')
                             for z in st.values() if z.get('cohort'))
                already_enrolled=any(z.get('cohort') and z.get('token_id')==token_id for z in st.values())
                if occupied<COHORT_MAX_PER_GROUP and not already_enrolled:
                    s['cohort']={'group':group,'at':t.isoformat(),
                        'first_chart_price':v,'first_liquidity_usd':x['liq'],
                        'first_age_minutes':age,'first_buyers_1h':x['buyers_1h'],
                        'first_buys_1h':x['buys_1h'],'first_sells_1h':x['sells_1h'],
                        'last_observed_at':None,'n_observations':0,
                        'peak_chart_multiple':1.,'peak_liquidity_usd':x['liq'],
                        'cohort_executability_study_version':COHORT_EXECUTABILITY_STUDY_VERSION,
                        'cohort_entry_quote_attempted':False,
                        'cohort_quote_marks_v1':[],
                        'no_trade':True,'no_executable_quote':True}
                    # Fixed budget by arm, at discovery rather than retrospectively
                    # choosing chart winners. A failed quote remains unknown.
                    co_enroll=s['cohort']
                    if cohort_entry_quote_checks[group]<COHORT_ENTRY_QUOTE_PER_ARM_PER_RUN:
                        cohort_entry_quote_checks[group]+=1
                        co_enroll['cohort_entry_quote_attempted']=True
                        mint=token_id.removeprefix('solana_')
                        buy=jupiter_quote(USDC,mint,5000000)
                        raw=int(buy.get('out_amount') or 0)
                        sell=jupiter_quote(mint,USDC,raw) if buy.get('ok') and raw>0 else {'ok':False}
                        back=(sell.get('out_amount',0)/5000000 if sell.get('ok') else None)
                        ok=bool(back is not None and back>=0.85)
                        if ok:
                            co_enroll['cohort_entry_quote_v1']={'observed_at':d.datetime.now(d.timezone.utc).isoformat(),
                                'token_raw':raw,'immediate_roundtrip_multiple':round(back,8),
                                'entry_usdc':5,'quote_only':True}
                        out({'kind':'cohort_discovery_matched_entry_quote_v1','pool':p,
                            'at':d.datetime.now(d.timezone.utc).isoformat(),'token_id':token_id,
                            'group':group,'route_buy':bool(buy.get('ok')),
                            'route_sell':bool(sell.get('ok')),
                            'two_sided_quote_acceptability':ok,
                            'no_transaction':True,'no_fill':True})
                    cohort_added_this_run[group]+=1
                    out({'kind':'prospective_cohort_baseline','pool':p,'token_id':token_id,
                         'at':t.isoformat(),'group':group,'baseline':s['cohort'],
                         'no_trade':True})
            co=s.get('cohort')
            if co and not co.get('completed') and not co.get('censored'):
                try:elapsed=(t-d.datetime.fromisoformat(co['at'])).total_seconds()
                except (ValueError,TypeError):elapsed=COHORT_HORIZON_SEC+1
                if 0<=elapsed<COHORT_HORIZON_SEC:
                    last=co.get('last_observed_at')
                    since=(t-d.datetime.fromisoformat(last)).total_seconds() if last else 99999
                    if since>=60:
                        co['n_observations']+=1
                        co['last_observed_at']=t.isoformat()
                        co['last_liquidity_usd']=x['liq']
                        multiple=v/co['first_chart_price']
                        if multiple>co['peak_chart_multiple']:
                            co['peak_chart_multiple']=multiple
                            co['peak_liquidity_usd']=x['liq']
                            co['peak_seen_at']=t.isoformat()
                        if multiple>=11 and not co.get('first_chart_11x_at'):
                            co['first_chart_11x_at']=t.isoformat()
                            co['first_chart_11x_liquidity_usd']=x['liq']
                            out({'kind':'cohort_11x_chart_only','pool':p,'token_id':token_id,
                                 'group':co['group'],'at':t.isoformat(),
                                 'chart_multiple':multiple,'liquidity_usd':x['liq'],
                                 'unfilled':True,'not_executable_quote':True})
                        # The chart threshold alone is NEVER a sellable return.
                        # Use a pre-discovery quoted token amount, current
                        # minimum liquidity, and equal per-arm quote budget.
                        tier='11x' if multiple>=11 else '2x'
                        if (multiple>=2 and x['liq']>=COHORT_MIN_LIQUIDITY_FOR_EXIT_USD
                            and co.get('cohort_executability_study_version')==COHORT_EXECUTABILITY_STUDY_VERSION
                            and co.get('cohort_entry_quote_v1')
                            and cohort_followup_quote_checks[co['group']]<COHORT_SELL_QUOTE_PER_ARM_PER_RUN):
                            marks=co.setdefault('cohort_quote_marks_v1',[])
                            tier_attempts=sum(z.get('tier')==tier for z in marks)
                            tier_observed=any(z.get('tier')==tier and z.get('sell_quote_available') for z in marks)
                            if tier_attempts<2 and not tier_observed:
                                cohort_followup_quote_checks[co['group']]+=1
                                mint=token_id.removeprefix('solana_')
                                q=jupiter_quote(mint,USDC,int(co['cohort_entry_quote_v1']['token_raw']))
                                qm=(q['out_amount']/5000000 if q.get('ok') else None)
                                mark={'at':d.datetime.now(d.timezone.utc).isoformat(),'tier':tier,
                                    'chart_trigger_x':round(multiple,6),
                                    'current_liquidity_usd':round(x['liq'],2),
                                    'sell_quote_available':bool(q.get('ok')),
                                    'quote_only_x':round(qm,8) if qm is not None else None,
                                    'no_fill':True,'no_executed_order':True}
                                marks.append(mark)
                                out({'kind':'cohort_matched_exit_quote_v1','pool':p,
                                    'token_id':token_id,'group':co['group'],**mark,
                                    'realized_profit':False,'orders':0,'trades':0})
            s['last_observed_liquidity_usd']=x['liq']
            s['last_liquidity_seen_at']=d.datetime.now(d.timezone.utc).isoformat()
            if s.get('entry') and not s.get('exit') and x['liq']<100:
                s['liquidity_drain_warning']=True
                if not s.get('liquidity_collapse_first_alert_at'):
                    s['liquidity_collapse_first_alert_at']=d.datetime.now(d.timezone.utc).isoformat()
                    out({'kind':'paper_position_liquidity_collapse','pool':p,'token_id':token_id,
                         'observed_liquidity_usd':x['liq'],
                         'reference_entry_liquidity_usd':s.get('entry_liquidity_usd'),
                         'at':s['liquidity_collapse_first_alert_at'],
                         'exit_not_assumed':True,'orders':0})
            signature=(v,x['liq'],x['volume_1h'],x['buys_1h'],x['sells_1h'],x['buyers_1h'])
            gap=(t-s['last_distinct_at']).total_seconds() if s['last_distinct_at'] is not None else None
            prev=s.get('previous_signature')
            improving=(prev is None or (gap is not None and 60<=gap<=300 and v>prev[0] and x['liq']>=0.9*prev[1]))
            gate=(bool(token_id) and token_id.startswith('solana_') and token_id!='solana_So11111111111111111111111111111111111111112' and 0<=age<=20 and x['liq']>=10000 and x['volume_1h']>=3000 and x['buyers_1h']>=20 and x['buys_1h']>=1.5*max(1,x['sells_1h']) and improving)
            if gap is not None and gap>300:s['qualified']=0
            if signature!=s['previous_signature'] and (gap is None or gap>=60):
                s['count']+=1;s['qualified']=s.get('qualified',0)+1 if gate else 0
                s['previous_signature']=signature;s['last_distinct_at']=t
            eligible=s.get('qualified',0)>=2 and gate
            # Snapshot FIRST eligibility independently of whether the research
            # quote position is blocked. This MUST precede every future exit.
            if eligible and not s.get('first_eligible_at_v1'):
                s['first_eligible_at_v1']=t.isoformat()
                s['first_eligible_age_minutes_v1']=round(age,4)
                first_price=num(s.get('first_discovery_price_v1'))
                early_multiple=(v/first_price if first_price>0 else None)
                if early_multiple is not None and not math.isfinite(early_multiple):
                    early_multiple=None
                s['first_eligible_vs_first_discovery_chart_x_v1']=(
                    round(early_multiple,8) if early_multiple is not None else None)
                s['first_eligible_price_v1']=v
                # FROZEN v1 prospective challenge, selected before any later
                # sell quotations; prior 24h cohort used to DESIGN, never to
                # VALIDATE, this hypothesis. It predicts quote-only 2x
                # opportunity, NOT executed P&L or 11x probability.
                if early_multiple is None:
                    preselected='unknown_missing_discovery_reference'
                else:
                    buy_pressure=x['buys_1h']/max(1,x['sells_1h'])
                    selection=(x['volume_1h']>=50000 and x['buyers_1h']>=450
                               and buy_pressure>=2 and 0<age<=7 and
                               0<early_multiple<=1.5)
                    preselected='high_activity_early_v1' if selection else 'other_eligible_v1'
                s['frozen_prediction_group_v1']=preselected
                s['frozen_prediction_at_v1']=t.isoformat()
                out({'kind':'precommitted_quote_2x_predictor_v1',
                     'pool':p,'token_id':token_id,'at':t.isoformat(),
                     'prediction_group':preselected,
                     'thresholds':{'initial_volume_1h_usd_min':50000,
                        'buyers_1h_min':450,'buy_sell_ratio_min':2,
                        'signal_age_minutes_max':7,
                        'discovery_to_signal_chart_multiple_max':1.5},
                     'is_not_11x_probability':True,'no_future_quote_used':True,
                     'not_a_trade_recommendation':True,'orders':0,'trades':0})
                first_eligible_signals_this_run+=1
                if early_multiple is not None:
                    first_eligible_with_discovery_baseline_this_run+=1
                    if early_multiple>=2:first_eligible_already_2x_this_run+=1
                out({'kind':'first_eligible_signal_leadtime_audit',
                     'pool':p,'token_id':token_id,'at':t.isoformat(),
                     'pool_age_minutes':round(age,4),
                     'discovery_age_minutes':s.get('first_discovery_age_minutes_v1'),
                     'discovery_to_signal_chart_x':s['first_eligible_vs_first_discovery_chart_x_v1'],
                     'unknown_reference_excluded':early_multiple is None,
                     'not_a_quote':True,'not_an_executable_buy':True,'orders':0,'trades':0})
            shadow=s.get('shadow')
            if shadow and not s.get('entry') and not shadow.get('expired'):
                dt_from=(t-d.datetime.fromisoformat(shadow['at'])).total_seconds()
                if dt_from>0 and shadow.get('entry_pool_price',0)>0:
                    gross=v/shadow['entry_pool_price']
                    if gross>shadow.get('peak_chart_multiple',1.):
                        shadow['peak_chart_multiple']=gross
                        shadow['peak_chart_liquidity_usd']=x['liq']
                        shadow['peak_chart_liquidity_observed_at']=t.isoformat()
                    shadow['last_chart_multiple']=gross
                    # Fast chart motion may happen between slow fair-rotation
                    # turns. Check exact-size sell quote NOW, not post-hoc
                    # chart peaks. Never use future price to choose candidates.
                    if burst_quote_eligible(shadow,gross,x['liq'],
                             shadow_quote_scheduled_this_run,shadow_quoted_this_run,
                             p,len(burst_quote_slots_this_run)) and (
                             len(shadow_quote_scheduled_this_run)<MAX_SHADOW_SELL_QUOTES_PER_RUN):
                        shadow_quote_scheduled_this_run.add(p)
                        burst_quote_slots_this_run.add(p)
                        shadow['last_quote_scheduled_at']=d.datetime.now(d.timezone.utc).isoformat()
                        out({'kind':'shadow_sell_quote_scheduled','at':shadow['last_quote_scheduled_at'],
                             'pool':p,'quota_slot':len(shadow_quote_scheduled_this_run),
                             'quote_fetched':False,'no_trade':True,'lane':'chart_burst_emergency',
                             'chart_multiple_unfilled':gross,'liquidity_usd':x['liq']})
                    if gross>=11 and not shadow.get('chart_11x_logged'):
                        shadow['chart_11x_logged']=True
                        shadow['first_chart_11x_liquidity_usd']=x['liq']
                        shadow['first_chart_11x_observed_at']=t.isoformat()
                        shadow['chart_11x_illiquid_at_first_observation']=x['liq']<10000
                        out({'kind':'shadow_chart_11x_unfilled','at':t.isoformat(),'pool':p,'token_id':s.get('token_id'),
                             'chart_multiple':gross,'liquidity_usd_at_11x':x['liq'],
                             'liquidity_below_10000_usd':x['liq']<10000,
                             'no_entry_quote':not bool(shadow.get('entry_quote')),
                             'no_trade':True,'not_proven_realizable':True})
                        shadow_chart_11x_this_run+=1
                    # Only shadows with a buy+sell quote captured at initial signal can
                    # be prospectively measured with quote-only exits. NEVER backfill.
                    if (shadow.get('entry_quote') and p in shadow_quote_scheduled_this_run
                        and p not in shadow_quoted_this_run
                        and shadow_quote_marks_this_run<MAX_SHADOW_SELL_QUOTES_PER_RUN):
                        shadow_quoted_this_run.add(p)
                        shadow_quote_marks_this_run+=1
                        q=jupiter_quote(s.get('token_id','').removeprefix('solana_'),USDC,
                                        int(shadow['entry_quote']['token_raw']))
                        marked=d.datetime.now(d.timezone.utc).isoformat()
                        shadow['last_exit_quote_check_at']=marked
                        if q.get('ok'):
                            shadow['quote_available_count']=shadow.get('quote_available_count',0)+1
                            shadow_quote_marks_available_this_run+=1
                            multiple=q['out_amount']/5000000
                            thresholds=shadow.setdefault('first_explosive_quote_milestones',{})
                            for level in EXPLOSIVE_MULTIPLIERS:
                                label=str(level)
                                if multiple>=level and label not in thresholds:
                                    thresholds[label]={'at':marked,'quote_multiple':multiple,
                                        'quote_usdc':q['out_amount']/1000000,
                                        'unfilled':True,'gas_rent_mev_not_included':True}
                                    out({'kind':'research_explosive_quote_milestone',
                                         'at':marked,'pool':p,'token_id':s.get('token_id'),
                                         'tier_x':level,'observed_quote_multiple':multiple,
                                         'no_actual_fill':True,'no_trade':True})
                            shadow['max_exit_quote_multiple']=max(shadow.get('max_exit_quote_multiple',0),multiple)
                            shadow['last_exit_quote_multiple']=multiple
                            shadow['last_exit_quote_at']=marked
                            frozen_policy_decisions_this_run+=update_frozen_quote_exit_policies(
                                shadow,multiple,marked,p,s.get('token_id'))
                            if shadow.get('moonshot_split_version')==MOONSHOT_SPLIT_VERSION:
                                probe_moonshot_split(shadow,
                                   s.get('token_id','').removeprefix('solana_'),
                                   p,s.get('token_id'),marked,multiple,x['liq'])
                            # Immutable prospective evidence of WHEN an exact-size
                            # sell quotation was seen (not when chart peaked).
                            # Only trace NEW cohorts started under this version.
                            if shadow.get('quote_trace_version')==1:
                                trace=shadow.setdefault('quote_trace',[])
                                trace.append({'at':marked,'ok':True,
                                    'quote_multiple':round(multiple,8),
                                    'liquidity_at_sample_usd':round(x['liq'],2),
                                    'no_fill':True})
                                if len(trace)>48:
                                    del trace[:-48]
                                    shadow['quote_trace_truncated']=True
                            out({'kind':'shadow_exit_quote_only','at':marked,'pool':p,'token_id':s.get('token_id'),
                                 'usdc_proceeds':q['out_amount']/1000000,'quote_multiple':multiple,
                                 'no_trade':True,'no_fill':True,'no_capital_allocated':True})
                            # First ask if an apparent 2x on $5 is also
                            # quoted at matched $25/$100 original buy amounts.
                            # The first available 2x event is recorded once.
                            # A quota-deferred study can retry on a later
                            # genuinely observed 2x quotation, never a chart peak.
                            if (2<=multiple<11 and shadow.get('size_matched_entry_v1')
                                    and not shadow.get('size_matched_exit_at_2x_v2')):
                                at2=probe_size_matched_exit(shadow,
                                    s.get('token_id','').removeprefix('solana_'),
                                    p,s.get('token_id'),marked,trigger_tier='2x')
                                if at2:shadow['size_matched_exit_at_2x_v2']=at2
                            # Larger exact-size exit studies must not be lost
                            # if the ONE-per-run quota was exhausted on the first
                            # 11x observation. Retry on another real >=11x mark.
                            if (multiple>=11 and shadow.get('size_matched_entry_v1')
                                    and not shadow.get('size_matched_exit_v1')):
                                larger=probe_size_matched_exit(shadow,
                                    s.get('token_id','').removeprefix('solana_'),
                                    p,s.get('token_id'),marked)
                                if larger:shadow['size_matched_exit_v1']=larger
                            if multiple>=11 and not shadow.get('quote_11x_logged'):
                                depth=probe_sell_depth(s.get('token_id','').removeprefix('solana_'),
                                    shadow['entry_quote']['token_raw'],q['out_amount'],
                                    marked,p,s.get('token_id'))
                                if depth:shadow['first_11x_sell_depth_probe']=depth
                                shadow['quote_11x_logged']=True
                                out({'kind':'shadow_11x_quote_only_unfilled','at':marked,'pool':p,
                                     'token_id':s.get('token_id'),'quote_multiple':multiple,
                                     'no_trade':True,'no_fill':True,'gas_rent_mev_not_included':True})
                        else:
                            shadow['exit_quote_missing_count']=shadow.get('exit_quote_missing_count',0)+1
                            if shadow.get('quote_trace_version')==1:
                                trace=shadow.setdefault('quote_trace',[])
                                trace.append({'at':marked,'ok':False,
                                    'quote_multiple':None,
                                    'liquidity_at_sample_usd':round(x['liq'],2),
                                    'no_fill':True})
                                if len(trace)>48:
                                    del trace[:-48]
                                    shadow['quote_trace_truncated']=True
                            out({'kind':'shadow_exit_quote_unavailable','at':marked,'pool':p,'token_id':s.get('token_id'),
                                 'quote_error':q,'no_fill':True,'no_capital_allocated':True})
            already_signaled=bool(token_id and token_id in entry_token_ids)
            if s['entry'] is None and eligible and not already_signaled:
                risk=paper_entry_risk_reason(d.datetime.now(d.timezone.utc))
                if risk:
                    if token_id not in blocked_candidates_this_run:
                        blocked_candidates_this_run.add(token_id)
                        out({'kind':'risk_blocked_candidate','at':d.datetime.now(d.timezone.utc).isoformat(),
                             'pool':p,'token_id':token_id,'reason':risk,'new_position':False})
                    known_shadow=bool(token_id and token_id in active_shadow_mints)
                    open_shadows=active_shadow_count
                    if (not s.get('shadow') and not known_shadow
                            and open_shadows>=MAX_SHADOW_CANDIDATES
                            and quote_checks<MAX_ENTRY_QUOTE_CHECKS):
                        reclaimed=reclaim_weak_shadow_slot(d.datetime.now(d.timezone.utc))
                        if reclaimed is not None:
                            active_shadow_count-=1
                            active_shadow_mints.discard(st[reclaimed].get('token_id'))
                            open_shadows=active_shadow_count
                    if not s.get('shadow') and not known_shadow and open_shadows<MAX_SHADOW_CANDIDATES:
                        active_tail=active_tail_count
                        tail_choice=active_tail<LONG_TAIL_MAX_ACTIVE and tail_is_selected(token_id)
                        shadow={'at':d.datetime.now(d.timezone.utc).isoformat(),
                                'long_tail_24h':bool(tail_choice),
                                'long_tail_assignment_method':'sha256_mint_mod5_at_signal',
                                'entry_pool_price':v,'entry_liquidity_usd':x['liq'],
                                'initial_age_minutes':age,
                                'initial_volume_1h_usd':x['volume_1h'],
                                'initial_buyers_1h':x['buyers_1h'],
                                'initial_buys_1h':x['buys_1h'],
                                'initial_sells_1h':x['sells_1h'],
                                'signal_activity_arm':prospective_activity_arm(x['volume_1h'],x['buyers_1h']),
                                'moonshot_rules_frozen_v2':moonshot_rule_tags_v2(
                                    age,x['volume_1h'],x['buyers_1h'],
                                    x['buys_1h'],x['sells_1h']),
                                'moonshot_rule_version':2,
                                'pre_signal_chart_x_v1':s.get('first_eligible_vs_first_discovery_chart_x_v1'),
                                'frozen_prediction_group_v1':s.get('frozen_prediction_group_v1'),
                                'frozen_prediction_at_v1':s.get('frozen_prediction_at_v1'),
                                'signal_age_minutes_v1':s.get('first_eligible_age_minutes_v1'),
                                'discovery_age_minutes_v1':s.get('first_discovery_age_minutes_v1'),
                                'initial_dex':x['dex'],
                                'quote_trace_version':1,
                                'quote_exit_policy_version':QUOTE_EXIT_POLICY_VERSION,
                                'quote_exit_policy_decisions':{},
                                'moonshot_split_version':MOONSHOT_SPLIT_VERSION,
                                'quote_trace':[],
                                'quote_trace_truncated':False,
                                'peak_chart_multiple':1.,'quote_available_count':0,
                                'reason':risk,'no_capital_allocated':True}
                        # Even when allocating no paper capital, research tradability in
                        # real time. Quote evidence never becomes a simulated fill.
                        if quote_checks<MAX_ENTRY_QUOTE_CHECKS:
                            proposal=entry_quote_preflight(token_id.removeprefix('solana_'))
                            shadow['initial_route_check']={'at':d.datetime.now(d.timezone.utc).isoformat(),
                                                           'reason':proposal.get('reason'),'ok':bool(proposal.get('ok'))}
                            if proposal.get('ok'):
                                shadow['entry_quote']=proposal
                                shadow_quote_entries_this_run+=1
                                # Strictly at SIGNAL, before future mark outcomes.
                                # No large-size retroactive buys for old winners.
                                arm=shadow.get('frozen_prediction_group_v1')
                                if (arm in size_matched_entry_by_group_this_run
                                        and size_matched_entry_by_group_this_run[arm]<1
                                        and size_matched_entry_studies_this_run<MAX_SIZE_MATCHED_ENTRY_STUDIES_PER_RUN):
                                    size_probe=probe_size_matched_entry(
                                        token_id.removeprefix('solana_'),p,token_id)
                                    if size_probe:
                                        size_probe['precommitted_arm']=arm
                                        shadow['size_matched_entry_v1']=size_probe
                                        size_matched_entry_by_group_this_run[arm]+=1
                                # Only assign a scarce real-time mark slot when
                                # there is a FOLLOWING cycle left to observe.
                                # Keep the initial quote time separate from the
                                # subsequent exact-token-amount sell mark.
                                if (cycle<CYCLES_PER_RUN-1
                                    and len(fresh_shadow_slots_this_run)<FRESH_SHADOW_QUOTE_RESERVE
                                    and len(shadow_quote_scheduled_this_run)<MAX_SHADOW_SELL_QUOTES_PER_RUN):
                                    shadow_quote_scheduled_this_run.add(p)
                                    fresh_shadow_slots_this_run.add(p)
                                    shadow['last_quote_scheduled_at']=d.datetime.now(d.timezone.utc).isoformat()
                                    out({'kind':'shadow_sell_quote_scheduled','at':shadow['last_quote_scheduled_at'],
                                         'pool':p,'quota_slot':len(shadow_quote_scheduled_this_run),
                                         'quote_fetched':False,'no_trade':True,'lane':'fresh_within_same_run'})
                                out({'kind':'shadow_entry_quote_only','at':shadow['initial_route_check']['at'],
                                     'pool':p,'token_id':token_id,
                                     'immediate_roundtrip_multiple':proposal['immediate_roundtrip_multiple'],
                                     'no_trade':True,'no_fill':True,'no_capital_allocated':True})
                            else:
                                out({'kind':'shadow_entry_route_unavailable','at':shadow['initial_route_check']['at'],
                                     'pool':p,'token_id':token_id,'reason':proposal.get('reason'),
                                     'no_trade':True,'no_capital_allocated':True})
                        s['shadow']=shadow
                        if token_id:active_shadow_mints.add(token_id)
                        active_shadow_count+=1
                        if tail_choice:active_tail_count+=1
                        shadow_new_this_run+=1
                        out({'kind':'prospective_activity_arm_assigned','at':shadow['at'],
                             'pool':p,'token_id':token_id,'arm':shadow['signal_activity_arm'],
                             'volume_min_usd':PROSPECTIVE_ACTIVITY_VOLUME_USD,
                             'buyers_min':PROSPECTIVE_ACTIVITY_BUYERS,
                             'assigned_before_future_exit_quotes':True,'no_trade':True})
                        out({'kind':'shadow_research_only','at':shadow['at'],'pool':p,
                             'token_id':token_id,'observed_price':v,'entry_intentionally_blocked':True,
                             'entry_route_observed':bool(shadow.get('entry_quote')),
                             'no_order':True,'reason':risk})
                    continue
                mint=token_id.removeprefix('solana_')
                preflight=entry_quote_preflight(mint)
                out({'kind':'jupiter_route_preflight','at':d.datetime.now(d.timezone.utc).isoformat(),'pool':p,'token_id':token_id,'result':preflight})
                if preflight.get('ok'):
                    entry_at=d.datetime.now(d.timezone.utc)
                    s['entry']=(entry_at,v)
                    entry_token_ids.add(token_id)
                    s['entry_quote']=preflight
                    s['entry_liquidity_usd']=x['liq']
                    out({'kind':'signal','at':entry_at.isoformat(),'pool':p,'token_id':token_id,'pool_price_at_sample':v,'jupiter_quote_only':preflight,'note':'prospective study, no order/fill; sample price may differ from route quote'})
            elif s['entry'] is not None and not s.get('exit') and not s.get('quote_study_censored_at') and t>s['entry'][0]:
                if s.get('entry_quote'):
                    # New positions are priced on fresh exact-size sell quotes, never a chart-only stop.
                    if p in sell_marked_this_run:
                        continue
                    if sell_mark_checks>=MAX_SELL_MARKS_PER_RUN:
                        out({'kind':'sell_mark_deferred','at':d.datetime.now(d.timezone.utc).isoformat(),'pool':p,'reason':'quote_rate_budget'})
                        continue
                    sell_mark_checks+=1
                    sell_marked_this_run.add(p)
                    mint=s.get('token_id','').removeprefix('solana_')
                    q=jupiter_quote(mint,USDC,int(s['entry_quote']['token_raw']))
                    mark_at=d.datetime.now(d.timezone.utc)
                    s['last_sell_quote_at']=mark_at.isoformat()
                    if not q.get('ok'):
                        s['quote_missing_streak']=s.get('quote_missing_streak',0)+1
                        s['last_missing_sell_quote_at']=mark_at.isoformat()
                        out({'kind':'sell_quote_missing','at':mark_at.isoformat(),'pool':p,
                             'token_id':s.get('token_id'),'route':q,
                             'missing_streak':s['quote_missing_streak'],'no_exit_fill':True})
                        continue
                    s['quote_missing_streak']=0
                    s['last_valid_sell_quote_at']=mark_at.isoformat()
                    net=q['out_amount']/5000000
                    s['peak_net']=max(s['peak_net'],net)
                    if net>=11 and not s.get('first_11x_sell_depth_probe'):
                        depth=probe_sell_depth(mint,s['entry_quote']['token_raw'],
                            q['out_amount'],mark_at.isoformat(),p,s.get('token_id'))
                        if depth:s['first_11x_sell_depth_probe']=depth
                    reason=('target_quote_11x' if net>=11 else 'hard_stop_quote_25pct' if net<=0.75
                            else 'peak_trail_quote_25pct' if s['peak_net']>=3 and net<=0.75*s['peak_net']
                            else 'hold_limit_quote_24h' if (mark_at-s['entry'][0]).total_seconds()>=86400 else None)
                    out({'kind':'jupiter_sell_quote_mark','at':mark_at.isoformat(),'pool':p,'token_id':s.get('token_id'),
                         'quote_usdc':q['out_amount']/1000000,'quote_net_multiple':net,'router':q.get('router'),
                         'not_executed':True,'gas_rent_mev_not_included':True})
                    if reason:
                        s['exit']={'at':mark_at.isoformat(),'reason':reason,'net_multiple_assumed':net,'source':'jupiter_quote_only'}
                        out({'kind':'paper_exit','at':mark_at.isoformat(),'pool':p,**s['exit'],'not_an_actual_fill':True})
                else:
                    # Preserve legacy research signals exactly as originally measured.
                    gross=v/s['entry'][1]
                    assumed_net=0.97*0.97*gross-0.008
                    s['peak_net']=max(s['peak_net'],assumed_net)
                    reason=('target_net_11x' if assumed_net>=11 else 'hard_stop_25pct' if assumed_net<=0.75
                            else 'peak_trail_25pct' if s['peak_net']>=3 and assumed_net<=0.75*s['peak_net']
                            else 'hold_limit_24h' if (t-s['entry'][0]).total_seconds()>=86400 else None)
                    out({'kind':'legacy_chart_mark','at':t.isoformat(),'pool':p,'gross_x':gross,'assumed_net_x':assumed_net,'not_executable_quote':True})
                    if reason:
                        s['exit']={'at':t.isoformat(),'reason':reason,'net_multiple_assumed':assumed_net,'source':'legacy_chart_only'}
                        out({'kind':'paper_exit','at':t.isoformat(),'pool':p,**s['exit'],'not_an_actual_fill':True})
    except Exception as e:out({'kind':'source_error','at':t.isoformat(),'error':str(e)[:200]})
    # Missing future pool data is CENSORED, not scored as a losing position.
    for pool,z in st.items():
        co=z.get('cohort')
        if not co or co.get('completed') or co.get('censored'):continue
        try:
            age_sec=(t-d.datetime.fromisoformat(co['at'])).total_seconds()
            recency=(t-d.datetime.fromisoformat(co['last_observed_at'])).total_seconds() if co.get('last_observed_at') else 999999
        except (TypeError,ValueError,KeyError):
            age_sec=COHORT_HORIZON_SEC+1
            recency=999999
        if age_sec>=COHORT_HORIZON_SEC:
            usable=co.get('n_observations',0)>=COHORT_MIN_OBSERVATIONS and recency<=3600
            if usable:co['completed']=True
            else:co['censored']=True
            co['finished_at']=t.isoformat()
            out({'kind':'prospective_cohort_24h_end','pool':pool,'at':t.isoformat(),
                 'group':co['group'],'complete':bool(usable),
                 'samples':co.get('n_observations',0),
                 'peak_chart_multiple':co.get('peak_chart_multiple',1.),
                 'no_trade':True,'no_fill':True})
    if cycle<CYCLES_PER_RUN-1:time.sleep(65)
keep={p:x for p,x in st.items() if (d.datetime.now(d.timezone.utc)-x['first']).total_seconds()<=72*3600 or x.get('entry')}
serial={}
for p,x in keep.items():
    y=dict(x);y['first']=y['first'].isoformat()
    if y.get('last_distinct_at'):y['last_distinct_at']=y['last_distinct_at'].isoformat()
    if y.get('entry'):y['entry']=[y['entry'][0].isoformat(),y['entry'][1]]
    serial[p]=y
with open('evidence/state.json','w') as f:json.dump(serial,f,allow_nan=False)
r={'at':d.datetime.now(d.timezone.utc).isoformat(),'unique_pools':len(st),
   'requested_samples_this_run':CYCLES_PER_RUN,
   'new_mints_first_detected_this_run':len(first_seen_unique_mints_this_run),
   'new_mints_with_known_launch_age':len(first_seen_ages_minutes_this_run),
   'new_mints_first_seen_within_1m':sum(v<=1 for v in first_seen_ages_minutes_this_run),
   'new_mints_first_seen_within_3m':sum(v<=3 for v in first_seen_ages_minutes_this_run),
   'new_mints_first_seen_within_5m':sum(v<=5 for v in first_seen_ages_minutes_this_run),
   'new_mints_first_seen_after_5m':sum(v>5 for v in first_seen_ages_minutes_this_run),
   'first_eligible_decisions_this_run':first_eligible_signals_this_run,
   'first_eligible_with_real_discovery_reference_this_run':first_eligible_with_discovery_baseline_this_run,
   'first_eligible_already_2x_since_first_discovery_this_run':first_eligible_already_2x_this_run,
   'prospective_signals':sum(x['entry'] is not None for x in st.values()),
   'paper_exits':sum(x.get('exit') is not None for x in st.values()),
   'paper_targets_11x':sum(str(x.get('exit',{}).get('reason','')).startswith('target_') for x in st.values() if x.get('exit')),
   'new_sell_quote_mark_attempts':sell_mark_checks,
   'quote_studies_censored_no_fill_total':sum(bool(x.get('quote_study_censored_at')) for x in st.values()),
   'shadow_capacity_reclaimed_this_run':shadow_retirements_this_run.get('capacity_reclaimed_non_tail_under_2x',0),
   'hot_route_quote_priority_scheduled_this_run':len(hot_quote_slots_this_run),
   'observed_11x_after_signal':sum(x.get('peak_net',0)>=11 for x in st.values()),
   'orders':0,'trades':0,'exit_execution_verified':False,
   'quote_preflight_attempts':quote_checks,
   'sell_depth_size_sensitivity_probes_this_run':sell_depth_probes_this_run,
   'size_matched_entry_studies_this_run':size_matched_entry_studies_this_run,
   'size_matched_entry_by_group_this_run':size_matched_entry_by_group_this_run,
   'size_matched_exit_studies_this_run':size_matched_exit_studies_this_run,
   'moonshot_split_half_lot_quote_probes_this_run':moonshot_split_quotes_this_run,
   'risk_blocked_candidate_mints':len(blocked_candidates_this_run),
   'new_shadow_candidates_research_only':shadow_new_this_run,
   'shadow_active_research_only':sum(bool(x.get('shadow')) and not x['shadow'].get('expired')
                                     and not x.get('entry') for x in st.values()),
   'shadow_chart_11x_unfilled_total':sum(1 for x in st.values()
                                           if x.get('shadow') and bool(x['shadow'].get('chart_11x_logged'))),
   'shadow_chart_11x_new_this_run':shadow_chart_11x_this_run,
   'cohort_new_buy_pressure':cohort_added_this_run['buy_pressure'],
   'cohort_new_control':cohort_added_this_run['lower_pressure_control'],
   'cohort_entry_quotes_attempted_by_arm':cohort_entry_quote_checks,
   'cohort_later_sell_quotes_attempted_by_arm':cohort_followup_quote_checks,
   'cohort_total':sum(bool(z.get('cohort')) for z in st.values()),
   'shadow_chart_11x_first_observed_liquidity_below_usd_10000':sum(1 for x in st.values()
       if x.get('shadow') and x['shadow'].get('chart_11x_illiquid_at_first_observation') is True),
   'shadow_buy_and_sell_quote_baselines_new':shadow_quote_entries_this_run,
   'shadow_buy_and_sell_quote_baselines_total':sum(1 for x in st.values() if x.get('shadow') and x['shadow'].get('entry_quote')),
   'shadow_quote_only_exit_checks_this_run':shadow_quote_marks_this_run,
   'shadow_quote_only_exit_available_this_run':shadow_quote_marks_available_this_run,
   'frozen_quote_exit_policy_decisions_this_run':frozen_policy_decisions_this_run,
   'shadow_quote_slots_scheduled':len(shadow_quote_scheduled_this_run),
   'shadow_quote_slots_without_live_mark':len(shadow_quote_scheduled_this_run)-shadow_quote_marks_this_run,
   'shadow_quote_baselines_never_marked':sum(1 for x in st.values()
       if x.get('shadow') and x['shadow'].get('entry_quote') and not x['shadow'].get('last_exit_quote_check_at')),
   'shadow_live_capacity':MAX_SHADOW_CANDIDATES,
   'long_tail_active_24h':sum(1 for z in st.values() if z.get('shadow')
        and not z['shadow'].get('expired') and z['shadow'].get('long_tail_24h')),
   'long_tail_seeded_from_existing':long_tail_seeded_this_run,
   'long_tail_max_active':LONG_TAIL_MAX_ACTIVE,
   'shadow_retirements_this_run':shadow_retirements_this_run,
   'shadow_early_retirements_censored':sum(v for k,v in shadow_retirements_this_run.items() if k!='natural_24h'),
   'shadow_fresh_quote_slots_same_run':len(fresh_shadow_slots_this_run),
   'shadow_reserved_fresh_quote_capacity':FRESH_SHADOW_QUOTE_RESERVE,
   'shadow_chart_burst_quote_slots_used':len(burst_quote_slots_this_run),
   'shadow_reserved_chart_burst_capacity':BURST_QUOTE_RESERVE,
   'shadow_mark_budget_per_run':MAX_SHADOW_SELL_QUOTES_PER_RUN,
   'shadow_remaining_capacity':max(0,MAX_SHADOW_CANDIDATES-sum(
       1 for x in st.values() if x.get('shadow') and not x['shadow'].get('expired') and not x.get('entry'))),
   'shadow_quote_only_11x_unfilled_total':sum(1 for x in st.values() if x.get('shadow') and x['shadow'].get('quote_11x_logged')),
   'quote_marked_distinct_positions':len(sell_marked_this_run),
   'quoted_open_positions':sum(bool(x.get('entry_quote')) and bool(x.get('entry')) and not bool(x.get('exit')) and not bool(x.get('quote_study_censored_at')) for x in st.values()),
   'quoted_positions_missing_sell_route':sum(bool(x.get('entry_quote')) and bool(x.get('entry')) and not bool(x.get('exit')) and not bool(x.get('quote_study_censored_at')) and x.get('quote_missing_streak',0)>0 for x in st.values()),
   'quoted_positions_liquidity_under_usd_100':sum(1 for x in st.values()
              if x.get('entry_quote') and x.get('entry') and not x.get('exit') and not x.get('quote_study_censored_at')
              and x.get('last_observed_liquidity_usd') is not None and x['last_observed_liquidity_usd']<100),
   'quoted_positions_liquidity_not_observed':sum(1 for x in st.values()
              if x.get('entry_quote') and x.get('entry') and not x.get('exit') and not x.get('quote_study_censored_at')
              and x.get('last_observed_liquidity_usd') is None),
   'method':'forward-only: paper positions gated on Jupiter routes and safe existing exits; blocked candidates get chart shadows and optional Jupiter buy+sell quote baselines with exact-amount future quote marks; a quote is NOT an executed fill, gas/rent/MEV omitted; no signing or execution'}
print('FORWARD_OBSERVATION_RECEIPT',json.dumps(r))
with open('evidence/summary.json','w') as f:json.dump(r,f,indent=2)
# Independent, non-promotional research scorecard. Never conflate exit quotes
# with fills, synthetic test wins, or historically backfilled entries.
quoted=[x for x in st.values() if x.get('entry') and x.get('entry_quote')]
legacy=[x for x in st.values() if x.get('entry') and not x.get('entry_quote')]
quoted_exits=[x for x in quoted if x.get('exit') and x['exit'].get('source')=='jupiter_quote_only']
shadows=[x['shadow'] for x in st.values() if x.get('shadow') and not x['shadow'].get('expired')]
quote_shadow=[x for x in shadows if x.get('entry_quote')]
evaluation={
 'evaluated_at':r['at'],
 'tracked_unique_pool_addresses':len(st),
 'tracked_unique_mints':len({x['token_id'] for x in st.values() if x.get('token_id')}),
 'paper_entries_legacy_chart_only':len(legacy),
 'paper_entries_with_route_quotes':len(quoted),
 'paper_exits_with_later_jupiter_sell_quote':len(quoted_exits),
 'paper_quote_exit_multiples':[x['exit'].get('net_multiple_assumed') for x in quoted_exits],
 'quote_based_paper_exit_winners_at_11x':sum(1 for x in quoted_exits if x['exit'].get('net_multiple_assumed',0)>=11),
 'paper_positions_with_depth_probe':sum(bool(x.get('first_11x_sell_depth_probe')) for x in quoted),
 'quoted_open_positions':r['quoted_open_positions'],
 'quoted_open_positions_without_sell_route':r['quoted_positions_missing_sell_route'],
 'shadow_research_only_active':len(shadows),
 'shadow_chart_peaks_top5':sorted((float(x.get('peak_chart_multiple',1)) for x in shadows),reverse=True)[:5],
 'shadow_with_quote_at_signal':len(quote_shadow),
 'shadow_with_later_11x_quote_only_unfilled':sum(1 for x in quote_shadow if x.get('quote_11x_logged')),
 'shadow_with_11x_chart_only_unfilled':r['shadow_chart_11x_unfilled_total'],
 'shadow_11x_with_verified_low_liquidity':r['shadow_chart_11x_first_observed_liquidity_below_usd_10000'],
 'real_transactions':0,
 'realized_net_profit_verified':False,
 'strategy_reliably_profitable':False,
 'caveats':'Paper quotes may fail to land or execute; charts are not fills; fees, slippage, MEV, rent and rug risks excluded.',
}
with open('evidence/evaluation.json','w') as f:json.dump(evaluation,f,indent=2,allow_nan=False)
print('PAPER_STRATEGY_EVALUATION',json.dumps(evaluation,allow_nan=False))
# Evidence-driven 24h outcome inventory: chart peaks are NOT executable
# returns; an exact-size quote never establishes successful execution.
live_shadows=[(pool,z['shadow']) for pool,z in st.items()
              if z.get('shadow') and not z['shadow'].get('expired') and not z.get('entry')]
eligible_quote_shadows=[(pool,sh) for pool,sh in live_shadows if sh.get('entry_quote')]
quoted_marks=[float(sh['max_exit_quote_multiple']) for _,sh in eligible_quote_shadows
              if sh.get('max_exit_quote_multiple') is not None]
last_marks=[float(sh['last_exit_quote_multiple']) for _,sh in eligible_quote_shadows
            if sh.get('last_exit_quote_multiple') is not None]
route_missing=sum(sh.get('exit_quote_missing_count',0) for _,sh in eligible_quote_shadows)
ranked=[{'pool':pool,'first_at':sh.get('at'),
         'peak_chart_x':round(float(sh.get('peak_chart_multiple',1)),6),
         'max_quote_only_x':round(float(sh.get('max_exit_quote_multiple',0)),6)
                        if sh.get('max_exit_quote_multiple') is not None else None,
         'last_quote_only_x':round(float(sh.get('last_exit_quote_multiple')),6)
                        if sh.get('last_exit_quote_multiple') is not None else None,
         'failed_sell_quote_checks':sh.get('exit_quote_missing_count',0),
         'quote_seen_after_signal':bool(sh.get('last_exit_quote_at')),
         'at_peak_pool_liquidity_usd':sh.get('peak_chart_liquidity_usd'),
         'first_entry_buy_sell_quoted':bool(sh.get('entry_quote')),
         'no_trade':True}
        for pool,sh in eligible_quote_shadows]
ranked.sort(key=lambda x:(x['max_quote_only_x'] is not None,
                          x['max_quote_only_x'] or -1),reverse=True)
shadow_report={'at':r['at'],
 'sampled_pool_mints_with_any_shadow':len(live_shadows),
 'fresh_buy_and_sell_quote_baselines':len(eligible_quote_shadows),
 'distinct_with_later_sell_quote':len(quoted_marks),
 'distinct_with_last_sell_quote':len(last_marks),
 'quote_ever_reached_2x_unfilled':sum(v>=2 for v in quoted_marks),
 'quote_ever_reached_5x_unfilled':sum(v>=5 for v in quoted_marks),
 'quote_ever_reached_11x_unfilled':sum(v>=11 for v in quoted_marks),
 'quote_ever_reached_21x_unfilled':sum(v>=21 for v in quoted_marks),
 'quote_ever_reached_51x_unfilled':sum(v>=51 for v in quoted_marks),
 'quote_ever_reached_101x_unfilled':sum(v>=101 for v in quoted_marks),
 'long_tail_24h_current_active':sum(bool(sh.get('long_tail_24h')) for _,sh in live_shadows),
 'highest_quoted_sell_multiple_unfilled':max(quoted_marks) if quoted_marks else None,
 'median_last_quoted_sell_multiple_unfilled':statistics.median(last_marks) if last_marks else None,
 'quote_failures_accumulated':route_missing,
 'active_shadows_with_11x_depth_probe':sum(bool(sh.get('first_11x_sell_depth_probe')) for _,sh in live_shadows),
 'active_quote_exit_policy_v1_enrolled':sum(sh.get('quote_exit_policy_version')==QUOTE_EXIT_POLICY_VERSION for _,sh in live_shadows),
 'active_quote_exit_policy_11x_decisions':sum(bool(sh.get('quote_exit_policy_decisions',{}).get('target_11x')) for _,sh in live_shadows),
 'active_size_matched_25_100_entry_quoted':sum(bool(sh.get('size_matched_entry_v1')) for _,sh in live_shadows),
 'active_size_matched_25_100_exit_quoted':sum(bool(sh.get('size_matched_exit_v1')) for _,sh in live_shadows),
 'active_size_matched_25_100_exit_quoted_at_2x':sum(bool(sh.get('size_matched_exit_at_2x_v2')) for _,sh in live_shadows),
 'active_moonshot_split_first_leg_quotes':sum(bool(sh.get('moonshot_split_first_leg_quote_v1')) for _,sh in live_shadows),
 'active_moonshot_split_second_leg_quotes':sum(bool(sh.get('moonshot_split_second_leg_quote_v1')) for _,sh in live_shadows),
 'ranked_shadow_quote_only_examples':ranked[:10],
 'realized_profit_count':0,'trades':0,
 'not_a_full_cohort':'Current active shadows only; expired shadows are excluded and retained in canonical checkpoint until ordinary pruning',
 'fees_and_execution_not_proven':True}
with open('evidence/quote_only_shadow_outcomes.json','w') as f:
    json.dump(shadow_report,f,indent=2,allow_nan=False)
print('QUOTE_ONLY_SHADOW_OUTCOMES',json.dumps({
    k:v for k,v in shadow_report.items() if k!='ranked_shadow_quote_only_examples'},allow_nan=False))
# Preserve completed 24h shadow research outside the 72h tracked-pool
# checkpoint, maintaining uncertainty for missing sale routes and no fills.
ledger_path='evidence/shadow_outcomes_ledger.json'
try:
    with open(ledger_path,encoding='utf-8') as f:shadow_ledger=json.load(f)
    assert isinstance(shadow_ledger,list)
except (FileNotFoundError,ValueError,TypeError,AssertionError):
    shadow_ledger=[]
archived={item.get('pool') for item in shadow_ledger}
for pool,z in st.items():
    sh=z.get('shadow')
    if not sh or not sh.get('expired') or pool in archived:continue
    shadow_ledger.append({
        'pool':pool,'token_id':z.get('token_id'),'signal_at':sh.get('at'),
        'expired_at':sh.get('expired_at'),
        'retirement_reason':sh.get('expired_reason','legacy_unspecified'),
        'completed_24h':bool(sh.get('full_24h_observation')),
        'shortened_24h_outcome_censored':bool(sh.get('shortened_24h_outcome_censored')),
        'evaluated_24h_trading_return':False,
        'entry_chart_price':sh.get('entry_pool_price'),
        'entry_liquidity_usd':sh.get('entry_liquidity_usd'),
        'initial_age_minutes':sh.get('initial_age_minutes'),
        'initial_volume_1h_usd':sh.get('initial_volume_1h_usd'),
        'initial_buyers_1h':sh.get('initial_buyers_1h'),
        'initial_buys_1h':sh.get('initial_buys_1h'),
        'initial_sells_1h':sh.get('initial_sells_1h'),
        'signal_activity_arm':sh.get('signal_activity_arm'),
        'moonshot_rule_version':sh.get('moonshot_rule_version'),
        'moonshot_rules_frozen_v2':sh.get('moonshot_rules_frozen_v2'),
        'pre_signal_chart_x_v1':sh.get('pre_signal_chart_x_v1'),
        'frozen_prediction_group_v1':sh.get('frozen_prediction_group_v1'),
        'frozen_prediction_at_v1':sh.get('frozen_prediction_at_v1'),
        'signal_age_minutes_v1':sh.get('signal_age_minutes_v1'),
        'discovery_age_minutes_v1':sh.get('discovery_age_minutes_v1'),
        'initial_dex':sh.get('initial_dex'),
        'long_tail_24h':bool(sh.get('long_tail_24h')),
        'long_tail_assignment_method':sh.get('long_tail_assignment_method'),
        'first_explosive_quote_milestones':sh.get('first_explosive_quote_milestones',{}),
        'peak_chart_multiple_unfilled':sh.get('peak_chart_multiple',1),
        'peak_chart_liquidity_usd':sh.get('peak_chart_liquidity_usd'),
        'initial_buy_sell_quote_available':bool(sh.get('entry_quote')),
        'initial_roundtrip_quote_multiple':sh.get('entry_quote',{}).get('immediate_roundtrip_multiple'),
        'later_sell_quotes_available':sh.get('quote_available_count',0),
        'later_sell_quotes_failed':sh.get('exit_quote_missing_count',0),
        'quote_trace_version':sh.get('quote_trace_version'),
        'quote_exit_policy_version':sh.get('quote_exit_policy_version'),
        'quote_exit_policy_decisions':sh.get('quote_exit_policy_decisions',{}),
        'size_matched_entry_v1':sh.get('size_matched_entry_v1'),
        'size_matched_exit_v1':sh.get('size_matched_exit_v1'),
        'size_matched_exit_at_2x_v2':sh.get('size_matched_exit_at_2x_v2'),
        'moonshot_split_version':sh.get('moonshot_split_version'),
        'moonshot_split_first_leg_quote_v1':sh.get('moonshot_split_first_leg_quote_v1'),
        'moonshot_split_second_leg_quote_v1':sh.get('moonshot_split_second_leg_quote_v1'),
        'quote_trace':sh.get('quote_trace',[])[-48:],
        'quote_trace_truncated':bool(sh.get('quote_trace_truncated')),
        'max_later_sell_quote_multiple_unfilled':sh.get('max_exit_quote_multiple'),
        'ever_observed_later_quote_11x':bool(sh.get('quote_11x_logged')),
        'missing_route_does_not_prove_exit':True,
        'return_unrealized':True,'orders':0,'trades':0})
    archived.add(pool)
shadow_ledger=shadow_ledger[-5000:]
# Primary objective research: PRESELECTED FULL 24H tail only. This avoids
# labeling stopped 90m/early capacity studies as failures. Still a sample of
# eligible shadow watches, NOT a random sample of all Solana token launches.
# The denominator includes missing buy/sell routes and sparse marks.
fixed_tail=[z for z in shadow_ledger
            if z.get('long_tail_24h') is True
            and z.get('long_tail_assignment_method')=='sha256_mint_mod5_at_signal'
            and z.get('completed_24h') is True]
fixed_tail_quotable=[z for z in fixed_tail if z.get('initial_buy_sell_quote_available')]
fixed_tail_marked=[z for z in fixed_tail_quotable
                   if z.get('later_sell_quotes_available',0)>0
                   and z.get('max_later_sell_quote_multiple_unfilled') is not None]
# Pre-signal movement experiment begins ONLY with new v1 enrollments.
# This baseline is first observed pool price, not token-launch entry/fill.
fixed_tail_leadtime={}
for label in ('pre_signal_below_1_5x','pre_signal_at_least_1_5x'):
    eligible_lead=[z for z in fixed_tail if z.get('pre_signal_chart_x_v1') is not None
                   and ((num(z['pre_signal_chart_x_v1'])>=1.5)==(label=='pre_signal_at_least_1_5x'))]
    quoted_lead=[z for z in eligible_lead if z.get('initial_buy_sell_quote_available')]
    observed_lead=[z for z in quoted_lead if z.get('later_sell_quotes_available',0)>0
                   and z.get('max_later_sell_quote_multiple_unfilled') is not None]
    fixed_tail_leadtime[label]={'completed':len(eligible_lead),
        'two_sided_entry_quote':len(quoted_lead),
        'later_sell_quote_observed':len(observed_lead),
        'missing_later_quote_unknown':len(quoted_lead)-len(observed_lead),
        'later_quote_2x_unfilled':sum(z['max_later_sell_quote_multiple_unfilled']>=2 for z in observed_lead),
        'later_quote_11x_unfilled':sum(z['max_later_sell_quote_multiple_unfilled']>=11 for z in observed_lead)}
# Censor-aware, fixed-cohort comparison of first-seen exit decisions.
# No decision means UNKNOWN, not a simulated sale at a perfect later price.
quote_policy_comparison={}
for label,target in QUOTE_EXIT_TARGETS:
    eligible_policy=[z for z in fixed_tail if z.get('quote_exit_policy_version')==QUOTE_EXIT_POLICY_VERSION
                     and z.get('initial_buy_sell_quote_available')]
    observed_policy=[z for z in eligible_policy if z.get('quote_exit_policy_decisions',{}).get(label)]
    reasons={}
    for z in observed_policy:
        why=z['quote_exit_policy_decisions'][label]['reason']
        reasons[why]=reasons.get(why,0)+1
    quote_policy_comparison[label]={
        'target_x':target,'stop_x':QUOTE_EXIT_STOP_X,
        'full_24h_eligible':len(eligible_policy),
        'first_threshold_quote_decisions':len(observed_policy),
        'decision_missing_unknown':len(eligible_policy)-len(observed_policy),
        'reasons':reasons,
        'median_observed_quote_multiple_unfilled':(round(statistics.median(
             z['quote_exit_policy_decisions'][label]['observed_quote_multiple']
             for z in observed_policy),6) if observed_policy else None),
        'not_net_trading_returns':True,'trades':0}
# Matched 25/100-dollar two-sided quotes from fixed, preselected 24h cohort;
# compare both signal-time prediction groups. A quote is NOT a fill.
size_matched_truth={}
for arm in ('high_activity_early_v1','other_eligible_v1'):
    sized=[z for z in fixed_tail if (z.get('size_matched_entry_v1') or {}).get('precommitted_arm')==arm]
    sized_2x=[z for z in sized if z.get('size_matched_exit_at_2x_v2')]
    sized_11x=[z for z in sized if z.get('size_matched_exit_v1')]
    counts={}
    for usdc in ('25','100'):
        for tier,group in (('2x',sized_2x),('11x',sized_11x)):
            quotes=[v[usdc] for z in group
                    for v in [(z['size_matched_exit_at_2x_v2'] if tier=='2x' else z['size_matched_exit_v1'])['sizes']]
                    if usdc in v and v[usdc].get('later_sell_quote_available')]
            counts[usdc+'_dollar_'+tier]={'route_quoted':len(quotes),
                'at_least_double_quote_only':sum(num(q.get('later_quote_multiple_unfilled'))>=2 for q in quotes),
                'at_least_11x_quote_only':sum(num(q.get('later_quote_multiple_unfilled'))>=11 for q in quotes)}
    size_matched_truth[arm]={'completed_24h_matched_entry':len(sized),
       'exit_2x_checked_n':len(sized_2x),'exit_11x_checked_n':len(sized_11x),
       'exit_not_checked_unknown_at_2x':len(sized)-len(sized_2x),
       'quote_only_levels':counts,'real_executions':0}
# v1 prediction challenge: an immutable rule applied BEFORE any later mark.
# Finished 24h shadows only; missing quotes remain an unknown outcome.
frozen_v1={}
for label in ('high_activity_early_v1','other_eligible_v1'):
    members=[z for z in fixed_tail if z.get('frozen_prediction_group_v1')==label
             and z.get('frozen_prediction_at_v1')]
    quoted=[z for z in members if z.get('initial_buy_sell_quote_available')]
    marked=[z for z in quoted if z.get('later_sell_quotes_available',0)>0
            and z.get('max_later_sell_quote_multiple_unfilled') is not None]
    frozen_v1[label]={'completed_24h':len(members),
        'entry_buy_and_sell_quoted':len(quoted),
        'followup_sell_quote_observed':len(marked),
        'followup_sell_quote_missing_unknown':len(quoted)-len(marked),
        'quote_only_2x_seen':sum(z['max_later_sell_quote_multiple_unfilled']>=2 for z in marked),
        'quote_only_11x_seen':sum(z['max_later_sell_quote_multiple_unfilled']>=11 for z in marked),
        'realized_trade_count':0}
fixed_tail_arms={}
for label in ('high_activity_50k_450','other_eligible'):
    arm=[z for z in fixed_tail if z.get('signal_activity_arm')==label]
    quoted=[z for z in arm if z.get('initial_buy_sell_quote_available')]
    measured=[z for z in quoted if z.get('later_sell_quotes_available',0)>0
              and z.get('max_later_sell_quote_multiple_unfilled') is not None]
    fixed_tail_arms[label]={'completed':len(arm),'entry_quoted':len(quoted),
        'later_quote_observed':len(measured),
        'later_quote_missing_unknown':len(quoted)-len(measured),
        'quote_2x_unfilled':sum(z['max_later_sell_quote_multiple_unfilled']>=2 for z in measured),
        'quote_11x_unfilled':sum(z['max_later_sell_quote_multiple_unfilled']>=11 for z in measured)}
fixed_tail_trace=[z for z in fixed_tail if z.get('quote_trace_version')==1
                  and not z.get('quote_trace_truncated')]
windows={}
for minutes in (15,60,240,1440):
    eligible_window=[]
    for z in fixed_tail_trace:
        try:signal=d.datetime.fromisoformat(z['signal_at'])
        except (KeyError,ValueError,TypeError):continue
        valid=[]
        for mark in z.get('quote_trace',[]):
            try:elapsed=(d.datetime.fromisoformat(mark['at'])-signal).total_seconds()/60
            except (KeyError,ValueError,TypeError):continue
            if 0<elapsed<=minutes and mark.get('ok') and mark.get('quote_multiple') is not None:
                valid.append(float(mark['quote_multiple']))
        if valid:eligible_window.append(max(valid))
    windows[str(minutes)+'m']={'sell_route_observed':len(eligible_window),
        'quote_2x_unfilled':sum(q>=2 for q in eligible_window),
        'quote_11x_unfilled':sum(q>=11 for q in eligible_window),
        'not_observed_excluded':len(fixed_tail_trace)-len(eligible_window)}
tail_report={'as_of':r['at'],'population':'fixed prospective hash-selected shadow 24h followup',
    'completed_24h_n':len(fixed_tail),
    'entry_two_sided_quote_n':len(fixed_tail_quotable),
    'later_sell_quote_observed_n':len(fixed_tail_marked),
    'quote_2x_unfilled_n':sum(z['max_later_sell_quote_multiple_unfilled']>=2 for z in fixed_tail_marked),
    'quote_11x_unfilled_n':sum(z['max_later_sell_quote_multiple_unfilled']>=11 for z in fixed_tail_marked),
    'entry_route_missing_n':len(fixed_tail)-len(fixed_tail_quotable),
    'later_route_missing_unknown_n':len(fixed_tail_quotable)-len(fixed_tail_marked),
    'new_precommitted_activity_groups':fixed_tail_arms,
    'prospective_leadtime_groups':fixed_tail_leadtime,
    'frozen_v1_prediction_challenge':frozen_v1,
    'frozen_quote_exit_policy_comparison':quote_policy_comparison,
    'size_matched_25_100_forward_comparison':size_matched_truth,
    'moonshot_split_24h_quote_observation':{
        'new_split_policy_enrolled':sum(z.get('moonshot_split_version')==MOONSHOT_SPLIT_VERSION for z in fixed_tail),
        'first_half_quote_observed':sum(bool(z.get('moonshot_split_first_leg_quote_v1')) for z in fixed_tail),
        'first_half_quote_with_5_25_usdc_gross_buffer':sum(bool(
            (z.get('moonshot_split_first_leg_quote_v1') or {}).get('gross_principal_buffer_observed')) for z in fixed_tail),
        'second_half_11x_quote_observed':sum(bool(z.get('moonshot_split_second_leg_quote_v1')) for z in fixed_tail),
        'two_non_atomic_quotes_are_not_trades':True,'real_profit_verified':False},
    'frozen_exit_first_threshold_no_hindsight':True,
    'frozen_v1_selected_only_at_first_eligibility':True,
    'in_sample_design_is_not_validation':True,
    'leadtime_reference_only_first_observed_price':True,
    'missing_legacy_leadtime_excluded':sum(z.get('pre_signal_chart_x_v1') is None for z in fixed_tail),
    'completed_with_full_quote_trace_v1':len(fixed_tail_trace),
    'quote_only_opportunity_by_observation_window':windows,
    'min_completed_24h_per_group_before_advantage_claim':40,
    'prediction_advantage_verified':False,
    'not_random_all_new_tokens':True,'not_filled_trades':True,
    'execution_or_realized_profit_proven':False,'orders':0,'trades':0}
with open('evidence/fixed_24h_tail_truth_report.json','w') as f:
    json.dump(tail_report,f,indent=2,allow_nan=False)
print('FIXED_24H_TAIL_TRUTH_REPORT',json.dumps(tail_report,allow_nan=False))
# Backtest signal groups on stored history. 21 Oct is a calendar split,
# NOT a genuinely unseen holdout: the candidate rules were examined
# retrospectively before launch. True v2 validation uses frozen tags only.
def summarize_moonshot_samples(source,versioned=False):
    by_rule={}
    for name in ('all_eligible',)+tuple(MOONSHOT_RULES_V2):
        assigned=[]
        for z in source:
            if name=='all_eligible':
                selected=True
            elif versioned:
                selected=z.get('moonshot_rule_version')==2 and name in (z.get('moonshot_rules_frozen_v2') or [])
            else:
                selected=name in moonshot_rule_tags_v2(
                    z.get('initial_age_minutes'),z.get('initial_volume_1h_usd'),
                    z.get('initial_buyers_1h'),z.get('initial_buys_1h'),
                    z.get('initial_sells_1h'))
            if selected:assigned.append(z)
        with_entry=[z for z in assigned if z.get('initial_buy_sell_quote_available')]
        with_later=[z for z in with_entry if z.get('later_sell_quotes_available',0)>0
                    and z.get('max_later_sell_quote_multiple_unfilled') is not None]
        wins2=sum(num(z['max_later_sell_quote_multiple_unfilled'])>=2 for z in with_later)
        wins11=sum(num(z['max_later_sell_quote_multiple_unfilled'])>=11 for z in with_later)
        by_rule[name]={'enrolled_n':len(assigned),
            'entry_quote_n':len(with_entry),'later_quote_n':len(with_later),
            'missing_later_quote_unknown_n':len(with_entry)-len(with_later),
            'quote_only_2x_n':wins2,'quote_only_11x_n':wins11,
            'quote_only_2x_rate_conditional':round(wins2/len(with_later),7) if with_later else None,
            'quote_only_11x_rate_conditional':round(wins11/len(with_later),7) if with_later else None,
            'zero_fills':True}
    return by_rule
seen_ids=set()
unique_shadow=[]
for z in sorted(shadow_ledger,key=lambda q:q.get('signal_at') or ''):
    key=z.get('token_id') or z.get('pool')
    if not key or key in seen_ids:continue
    seen_ids.add(key)
    unique_shadow.append(z)
calendar_cut='2026-10-21T00:00:00+00:00'
older=[z for z in unique_shadow if (z.get('signal_at') or '')<calendar_cut]
newer=[z for z in unique_shadow if (z.get('signal_at') or '')>=calendar_cut]
frozen_new=[z for z in unique_shadow if z.get('moonshot_rule_version')==2]
moonshot_hypotheses={
 'as_of':r['at'],'archive_only_last_5000_records':True,
 'dedup_by_token_id':True,'train_or_design_period_before':calendar_cut,
 'not_an_unseen_holdout_or_p_value':True,
 'earlier_calendar_part_retrospective':summarize_moonshot_samples(older),
 'later_calendar_part_still_retrospective':summarize_moonshot_samples(newer),
 'new_forward_frozen_rule_records':summarize_moonshot_samples(frozen_new,versioned=True),
 'archive_early_retirements_selection_bias':True,
 'later_quote_missing_is_unknown':True,
 'quote_only_not_realizable_profit':True,
 'claim_of_live_strategy_advantage':False,'orders':0,'trades':0}
with open('evidence/moonshot_rule_validation_v2.json','w',encoding='utf-8') as fh:
    json.dump(moonshot_hypotheses,fh,indent=2,allow_nan=False)
print('MOONSHOT_RULE_VALIDATION_V2',json.dumps(moonshot_hypotheses,allow_nan=False))
# AUTOMATED REAL HISTORICAL QUOTE REPLAY, not a trading backtest:
# chronological first available observed quote, no look-ahead peaks.
# Never mark a quote-missing position at $0 or a perfect target fill.
paper_observed=[num(x) for x in evaluation['paper_quote_exit_multiples']
                if x is not None and num(x)>0]
paper_entry_quoted=evaluation['paper_entries_with_route_quotes']
paper_replay={'entry_quoted_total':paper_entry_quoted,
    'later_exit_quote_observed':len(paper_observed),
    'unknown_exit_or_open':paper_entry_quoted-len(paper_observed),
    'winners_quote_gt_1x':sum(x>1 for x in paper_observed),
    'losers_quote_lt_1x':sum(x<1 for x in paper_observed),
    'exit_quotes_at_least_11x':sum(x>=11 for x in paper_observed),
    'median_observed_exit_x':(round(statistics.median(paper_observed),8)
                              if paper_observed else None),
    'gross_quote_pl_usdc_conditional_on_later_quote':round(
        5*(sum(paper_observed)-len(paper_observed)),3),
    'quoted_exit_notional_usdc':5*len(paper_observed),
    'not_portfolio_return_missing_ignored':True,
    'not_filled_gas_mev_slippage_excluded':True}
def replay_first_observed_quote_thresholds(z,target,stop=0.75):
    """Only contemporaneous sell quotes strictly after the initial signal."""
    try:signal=d.datetime.fromisoformat(z['signal_at'])
    except (ValueError,TypeError,KeyError):return None
    marks=sorted(enumerate(z.get('quote_trace') or []),
                 key=lambda e:(e[1].get('at') or '',e[0]))
    for _,mark in marks:
        if not mark.get('ok') or mark.get('quote_multiple') is None:continue
        try:
            seen=d.datetime.fromisoformat(mark['at'])
            elapsed=(seen-signal).total_seconds()
            multiple=float(mark['quote_multiple'])
        except (ValueError,TypeError,KeyError):continue
        if not (0<elapsed<=SHADOW_RESEARCH_SECONDS
                and math.isfinite(multiple) and multiple>0):continue
        if multiple<=stop:return ('stop',multiple)
        if multiple>=target:return ('target',multiple)
    return None
# Fixed, hash-preselected, full 24h only. Exclude *truncated* records
# instead of pretending the missing earlier marks never happened.
replay_cohort=[z for z in fixed_tail
               if z.get('initial_buy_sell_quote_available')
               and z.get('quote_trace_version')==1
               and not z.get('quote_trace_truncated')]
first_quote_replays={}
for target in (2,3,5,11):
    outcomes=[replay_first_observed_quote_thresholds(z,target)
              for z in replay_cohort]
    observed=[v for v in outcomes if v is not None]
    first_quote_replays[str(target)+'x']={
        'eligible_quote_24h_traces':len(replay_cohort),
        'target_first':sum(a=='target' for a,b in observed),
        'stop_first':sum(a=='stop' for a,b in observed),
        'unresolved_censored':len(outcomes)-len(observed),
        'quote_profit_usdc_on_5usd_per_resolved':round(
            5*sum(b-1 for a,b in observed),3),
        'quoted_entry_cost_resolved_usdc':5*len(observed),
        'stop_not_guaranteed_at_0_75x':True,
        'quotes_not_fills':True}
historical_backtest={'as_of':r['at'],'source':'prospective_immutable_quotes_only',
    'paper_exit_quote_subpopulation':paper_replay,
    'strict_fixed_24h_first_observed_quote_replays':first_quote_replays,
    'full_24h_cohort_completed':len(fixed_tail),
    'quote_complete_24h_count':len(fixed_tail_marked),
    'full_24h_11x_quote_observed':sum(
        z['max_later_sell_quote_multiple_unfilled']>=11 for z in fixed_tail_marked),
    'all_entries_without_exit_are_unknown_not_zero':True,
    'strategy_profitable_proven':False,
    'real_fills':0,'orders':0,
    'does_not_model_taxes_slippage_mev_priority_fees':True}
with open('evidence/historical_quote_backtest.json','w',encoding='utf-8') as f:
    json.dump(historical_backtest,f,indent=2,allow_nan=False)
print('HISTORICAL_QUOTE_BACKTEST',json.dumps(historical_backtest,allow_nan=False))
# Supplemental descriptive comparison ONLY. Early exits are selection biased.
# A missing future sell quotation is CENSORED, not a failed profit or 0x.
complete_quotes=[z for z in shadow_ledger
                 if z.get('initial_buy_sell_quote_available')
                 and z.get('later_sell_quotes_available',0)>0
                 and z.get('max_later_sell_quote_multiple_unfilled') is not None]
winner=[z for z in complete_quotes if z['max_later_sell_quote_multiple_unfilled']>=2]
control=[z for z in complete_quotes if z['max_later_sell_quote_multiple_unfilled']<2]
precursors={}
for feature in ('entry_liquidity_usd','initial_age_minutes','initial_volume_1h_usd',
                'initial_buyers_1h','initial_buys_1h','initial_sells_1h'):
    a=[float(z[feature]) for z in winner if z.get(feature) is not None]
    b=[float(z[feature]) for z in control if z.get(feature) is not None]
    if len(a)>=5 and len(b)>=20:
        precursors[feature]={'quote_2x_winner_median':statistics.median(a),
                            'control_with_valid_later_quote_median':statistics.median(b),
                            'winner_n':len(a),'control_n':len(b)}
# Forward-only observational comparison. Exclude unlabeled older candidates.
# Missing sell quotes remain UNKNOWN; none of these are executed trades.
activity_arm_names=('high_activity_50k_450','other_eligible')
activity_arms={}
for arm in activity_arm_names:
    archived_arm=[z for z in shadow_ledger if z.get('signal_activity_arm')==arm]
    active_arm=[z['shadow'] for z in st.values() if z.get('shadow')
                and not z['shadow'].get('expired')
                and z['shadow'].get('signal_activity_arm')==arm]
    started=[z for z in archived_arm if z.get('initial_buy_sell_quote_available')]
    measured=[z for z in started if z.get('later_sell_quotes_available',0)>0
              and z.get('max_later_sell_quote_multiple_unfilled') is not None]
    activity_arms[arm]={'active_newly_assigned':len(active_arm),
        'archived':len(archived_arm),'archived_with_entry_quote':len(started),
        'archived_with_later_quote':len(measured),
        'missing_later_quote_censored':len(started)-len(measured),
        'ever_quote_2x_unfilled':sum(z['max_later_sell_quote_multiple_unfilled']>=2 for z in measured),
        'ever_quote_11x_unfilled':sum(z['max_later_sell_quote_multiple_unfilled']>=11 for z in measured),
        'no_actual_trades':True}
activity_report={'as_of':r['at'],
    'fixed_signal_time_cutoffs':{'initial_volume_1h_usd':PROSPECTIVE_ACTIVITY_VOLUME_USD,
                                 'initial_buyers_1h':PROSPECTIVE_ACTIVITY_BUYERS},
    'arms':activity_arms,'legacy_unlabeled_excluded':True,
    'not_randomized_or_causal':True,'quote_selection_bias_possible':True,
    'unfilled_outcomes_only':True,'trades':0}
with open('evidence/prospective_activity_arms.json','w') as f:json.dump(activity_report,f,indent=2,allow_nan=False)
print('PROSPECTIVE_ACTIVITY_ARMS',json.dumps(activity_report,allow_nan=False))
shadow_learning={'as_of':r['at'],'archived_mints':len(shadow_ledger),
                 'eligible_quote_observed_after_entry':len(complete_quotes),
                 'unfilled_later_quote_2x_count':len(winner),
                 'unfilled_later_quote_below_2x_count':len(control),
                 'unknown_later_quote_excluded':sum(x.get('initial_buy_sell_quote_available') and
                        not x.get('later_sell_quotes_available',0) for x in shadow_ledger),
                 'precursor_medians':precursors,
                 'enough_evidence_for_precursor_inference':False,
                 'precursor_medians_are_exploratory_and_selection_biased':True,
                 'see_primary_preselected_24h_tail_report':True,
                 'no_filled_trades':True,'trades':0}
with open('evidence/shadow_learning_evaluation.json','w') as f:
    json.dump(shadow_learning,f,indent=2,allow_nan=False)
print('SHADOW_PREDICTOR_VALIDATION',json.dumps(shadow_learning,allow_nan=False))
with open(ledger_path,'w',encoding='utf-8') as f:
    json.dump(shadow_ledger,f,indent=1,allow_nan=False)
print('SHADOW_ARCHIVE_CHECKPOINT',json.dumps({
    'expired_shadow_records_preserved':len(shadow_ledger),
    'expired_with_quote_at_signal':sum(x['initial_buy_sell_quote_available'] for x in shadow_ledger),
    'expired_with_later_sell_quote':sum(x['later_sell_quotes_available']>0 for x in shadow_ledger),
    'expired_with_later_quote_11x_unfilled':sum(x['ever_observed_later_quote_11x'] for x in shadow_ledger),
    'archived_tail_24h_count':sum(x.get('long_tail_24h',False) for x in shadow_ledger),
    'archived_tail_full_24h_count':sum(x.get('long_tail_24h',False) and x.get('completed_24h',False) for x in shadow_ledger),
    'trades':0},allow_nan=False))
groups={}
for label in COHORT_GROUPS:
    obs=[z['cohort'] for z in st.values() if z.get('cohort',{}).get('group')==label]
    done=[z for z in obs if z.get('completed')]
    groups[label]={'enrolled':len(obs),'completed':len(done),
        'censored':sum(bool(z.get('censored')) for z in obs),
        'active':sum(not z.get('completed') and not z.get('censored') for z in obs),
        'first_age_median_minutes':statistics.median([z['first_age_minutes'] for z in obs if z.get('first_age_minutes') is not None]) if any(z.get('first_age_minutes') is not None for z in obs) else None,
        'first_liquidity_median_usd':statistics.median([z['first_liquidity_usd'] for z in obs if z.get('first_liquidity_usd') is not None]) if any(z.get('first_liquidity_usd') is not None for z in obs) else None,
        'first_buyers_1h_median':statistics.median([z['first_buyers_1h'] for z in obs if z.get('first_buyers_1h') is not None]) if any(z.get('first_buyers_1h') is not None for z in obs) else None,
        'completed_chart_2x':sum(z.get('peak_chart_multiple',0)>=2 for z in done),
        'completed_chart_11x':sum(z.get('peak_chart_multiple',0)>=11 for z in done),
        'completed_11x_at_peak_liq_above_usd_10k':sum(
            z.get('peak_chart_multiple',0)>=11 and z.get('peak_liquidity_usd',0)>=10000 for z in done)}
cohort_report={'as_of':r['at'],
    'design':'fixed prospective first-discovery 24h groups, buy count / sell count >=1.5 vs <1.5 with shared eligibility minima (NOT individually matched); 24h chart outcomes, censored data tracked separately',
    'groups':groups,'training_model_transferred':False,
    'executable_returns_verified':False,'orders':0,'trades':0}
# Retain bounded completed/censored outcomes separately from short-lived pool
# checkpoints; subsequent run restores this ledger through the same Actions cache.
ledger_path='evidence/prospective_cohort_ledger.json'
try:
    with open(ledger_path,encoding='utf-8') as f:ledger=json.load(f)
    assert isinstance(ledger,list)
except (FileNotFoundError,ValueError,TypeError,AssertionError):
    ledger=[]
known={record.get('pool') for record in ledger}
for pool,z in st.items():
    co=z.get('cohort')
    if not co or not (co.get('completed') or co.get('censored')) or pool in known:continue
    ledger.append({'pool':pool,'token_id':z.get('token_id'),
        'group':co['group'],'enrolled_at':co['at'],
        'finished_at':co.get('finished_at'),
        'samples':co.get('n_observations',0),
        'completed':bool(co.get('completed')),'censored':bool(co.get('censored')),
        'peak_chart_multiple':co.get('peak_chart_multiple',1.),
        'peak_liquidity_usd':co.get('peak_liquidity_usd'),
        'cohort_executability_study_version':co.get('cohort_executability_study_version'),
        'cohort_entry_quote_attempted':bool(co.get('cohort_entry_quote_attempted')),
        'cohort_entry_quote_v1':co.get('cohort_entry_quote_v1'),
        'cohort_quote_marks_v1':co.get('cohort_quote_marks_v1',[]),
        'not_an_executable_quote':True,'trades':0})
    known.add(pool)
# A 24h chart cohort is not an actual fill. Retain only the last 1000
# records in small local cache; archived per-run artifacts retain snapshots.
ledger=ledger[-1000:]
with open(ledger_path,'w',encoding='utf-8') as f:json.dump(ledger,f,indent=1)
cohort_report['historical_completed_or_censored_retained']=len(ledger)
# Match both buy and future sell QUOTES in the SAME frozen cohort.
# Missing quotes and earlier chart-only cohorts remain separate denominators.
cohort_route_arm_report={}
for group in COHORT_GROUPS:
    members=[z for z in ledger if z.get('completed') and z.get('group')==group
             and z.get('cohort_executability_study_version')==COHORT_EXECUTABILITY_STUDY_VERSION]
    attempted=[z for z in members if z.get('cohort_entry_quote_attempted')]
    entered=[z for z in attempted if z.get('cohort_entry_quote_v1')]
    later=[z for z in entered if any(q.get('sell_quote_available') for q in z.get('cohort_quote_marks_v1',[]))]
    qmarks=[q for z in later for q in z['cohort_quote_marks_v1']
            if q.get('sell_quote_available') and q.get('quote_only_x') is not None]
    reached_2=sum(any(num(q.get('quote_only_x'))>=2 for q in z.get('cohort_quote_marks_v1',[])) for z in later)
    reached_11=sum(any(num(q.get('quote_only_x'))>=11 for q in z.get('cohort_quote_marks_v1',[])) for z in later)
    cohort_route_arm_report[group]={'completed_24h':len(members),
        'entry_quote_checked':len(attempted),'two_sided_entry_quote':len(entered),
        'later_sell_quote_route_observed':len(later),'later_route_missing_or_not_checked':len(entered)-len(later),
        'observed_matched_quote_2x':reached_2,
        'observed_matched_quote_11x':reached_11,
        'future_quotes_not_fills':True,'trades':0}
cohort_route_report={'as_of':r['at'],'study_version':COHORT_EXECUTABILITY_STUDY_VERSION,
    'hypothesis':'higher first-discovery buy/sell pressure predicts later matched sellable quote',
    'preselected_24h_arms':cohort_route_arm_report,'historic_chart_cohorts_excluded':True,
    'new_unquoted_preselected_members_count':sum(
       z.get('completed') and not z.get('cohort_executability_study_version') for z in ledger),
    'not_randomized':True,'not_independent_trades':True,'trades':0}
with open('evidence/prospective_cohort_executable_quote_validation.json','w') as f:
    json.dump(cohort_route_report,f,indent=2,allow_nan=False)
print('COHORT_EXECUTABLE_QUOTE_VALIDATION',json.dumps(cohort_route_report,allow_nan=False))
with open('evidence/prospective_cohort_comparison.json','w') as f:
    json.dump(cohort_report,f,indent=2,allow_nan=False)
print('LIVE_COHORT_24H_COMPARISON',json.dumps(cohort_report,allow_nan=False))

# A transparent *live-only* ranking. No historical peaks, simulated entries or trades.
priority=[]
now=d.datetime.now(d.timezone.utc)
for pool,item in st.items():
    sh=item.get('shadow')
    if not sh or sh.get('expired') or item.get('entry') or not sh.get('entry_quote'):
        continue
    try:
        at=d.datetime.fromisoformat(sh['at'])
        signal_minutes=(now-at).total_seconds()/60
        if signal_minutes<0 or signal_minutes>24*60:continue
        buys=float(sh.get('initial_buys_1h') or 0)
        sells=float(sh.get('initial_sells_1h') or 0)
        buyers=float(sh.get('initial_buyers_1h') or 0)
        liquidity=float(sh.get('entry_liquidity_usd') or 0)
        volume=float(sh.get('initial_volume_1h_usd') or 0)
        first_age=float(sh.get('initial_age_minutes') or 0)
        roundtrip=float(sh['entry_quote'].get('immediate_roundtrip_multiple') or 0)
    except (ValueError,TypeError,KeyError):
        continue
    pressure=buys/max(1.,sells)
    quote_at=sh.get('last_exit_quote_at')
    liquidity_at=item.get('last_liquidity_seen_at')
    try:
        quote_age=(now-d.datetime.fromisoformat(quote_at)).total_seconds()/60 if quote_at else None
        liquidity_age=(now-d.datetime.fromisoformat(liquidity_at)).total_seconds()/60 if liquidity_at else None
    except (ValueError,TypeError):
        quote_age=None;liquidity_age=None
    fresh_route=(quote_age is not None and 0<=quote_age<=15
                 and liquidity_age is not None and 0<=liquidity_age<=15)
    latest_multiple=float(sh.get('last_exit_quote_multiple') or 0) if quote_at else None
    current_liquidity=float(item.get('last_observed_liquidity_usd') or 0)
    # A high initial score cannot hide a drained CURRENT pool. Five-minute
    # recency is still NOT proof the quoted swap can execute on-chain.
    if quote_age is None or quote_age<0 or quote_age>5:
        priority_risk='sell_quote_missing_or_over_5m'
    elif liquidity_age is None or liquidity_age<0 or liquidity_age>5:
        priority_risk='liquidity_observation_missing_or_over_5m'
    elif current_liquidity<10000:
        priority_risk='current_liquidity_below_10000_usd'
    elif current_liquidity<0.5*liquidity:
        priority_risk='current_liquidity_drawdown_over_50pct'
    elif latest_multiple is None or latest_multiple<0.75:
        priority_risk='sell_quote_below_0_75x'
    else:
        priority_risk=None
    recent_liquid_quote_observed=priority_risk is None
    score=(min(25.,pressure/3*25)+min(20.,liquidity/50000*20)
           +min(15.,volume/100000*15)+min(15.,buyers/200*15)
           +(10 if 0<=first_age<=3 else 5 if first_age<=10 else 0)
           +min(10.,max(0.,roundtrip-.85)/.15*10)+(5 if fresh_route else 0))
    priority.append({'pool':pool,'token_id':item.get('token_id'),
        'score_heuristic_not_probability':round(score,2),
        'signal_at':sh['at'],'signal_age_minutes':round(signal_minutes,2),
        'age_at_signal_minutes':round(first_age,2),
        'frozen_prediction_group_v1':sh.get('frozen_prediction_group_v1'),
        'initial_buy_sell_ratio':round(pressure,3),
        'initial_liquidity_usd':round(liquidity,2),
        'initial_volume_1h_usd':round(volume,2),
        'initial_roundtrip_quote_x':round(roundtrip,5),
        'last_known_liquidity_usd':round(current_liquidity,2),
        'last_quote_only_multiple':latest_multiple,
        'last_quote_age_minutes':round(quote_age,2) if quote_age is not None else None,
        'fresh_sell_quote_within_15m':fresh_route,
        'recent_liquid_sell_quote_observed':recent_liquid_quote_observed,
        'current_liquidity_risk_reason':priority_risk,
        'not_currently_executable':True,'orders':0,'trades':0})
priority.sort(key=lambda row:(row['recent_liquid_sell_quote_observed'],
    row['score_heuristic_not_probability']),reverse=True)
priority_report={'at':now.isoformat(),'method':'live signal-time fundamentals; never retrospective peak',
    'ranking_not_win_probability':True,'fresh_quote_is_not_a_fill':True,
    'tracked_eligible':len(priority),
    'recent_liquid_quote_candidates':sum(p['recent_liquid_sell_quote_observed'] for p in priority),
    'recent_quote_but_liquidity_failed':sum(p['fresh_sell_quote_within_15m'] and
        p['current_liquidity_risk_reason'] in
        ('current_liquidity_below_10000_usd','current_liquidity_drawdown_over_50pct') for p in priority),
    'top':priority[:10],'orders':0,'trades':0}
with open('evidence/live_candidate_priority.json','w') as f:
    json.dump(priority_report,f,indent=2,allow_nan=False)
print('LIVE_CANDIDATE_PRIORITY',json.dumps(priority_report,allow_nan=False))
