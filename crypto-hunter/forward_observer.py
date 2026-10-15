import datetime as d,json,time,urllib.request,urllib.error,urllib.parse,os,math,statistics,hashlib
url='https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page='
st={}
USDC='EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
quote_checks=0
MAX_ENTRY_QUOTE_CHECKS=5
sell_mark_checks=0
MAX_SELL_MARKS_PER_RUN=8
MAX_OPEN_QUOTED_POSITIONS=4
MAX_SELL_QUOTE_AGE_SECONDS=900
MAX_SHADOW_CANDIDATES=80
SHADOW_RESEARCH_SECONDS=24*3600
SHADOW_NO_ENTRY_ROUTE_SECONDS=30*60
SHADOW_FLAT_QUOTE_SECONDS=90*60
SHADOW_KEEP_PEAK_QUOTE_X=1.25
# Twenty percent identity-only, 24h tail cohort to observe delayed breakouts.
LONG_TAIL_MAX_ACTIVE=16   # research only; 80 total slots
LONG_TAIL_MODULUS=5
EXPLOSIVE_MULTIPLIERS=(11,21,51,101)  # +1000%/+2000%/+5000%/+10000%
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
shadow_new_this_run=0
shadow_chart_11x_this_run=0
shadow_quote_entries_this_run=0
shadow_quote_marks_this_run=0
shadow_quote_marks_available_this_run=0
MAX_SHADOW_SELL_QUOTES_PER_RUN=8
# Guarantee some quote capacity for NEW quoteable candidates in the SAME
# study (first follow-up is ~65s), not ~13 minutes at the next self-dispatch.
FRESH_SHADOW_QUOTE_RESERVE=4
# Reserve two quote slots for abrupt chart acceleration, BEFORE an 11x candle.
# A spot price burst is merely a request for a real later sell quotation.
BURST_QUOTE_RESERVE=2
BURST_CHART_TRIGGER_X=1.8
BURST_MIN_LIQUIDITY_USD=10000
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
        if liquidity is None or num(liquidity)>=100:
            continue
        try:
            age=(now-item['entry'][0]).total_seconds()
        except (TypeError,ValueError,IndexError):
            continue
        if age<1800:
            continue
        item['quote_study_censored_at']=now.isoformat()
        item['quote_study_censored_reason']='missing_sell_route_with_liquidity_below_100_usd'
        item['quote_study_no_exit_fill']=True
        retired+=1
        out({'kind':'quote_study_censored_no_fill','at':now.isoformat(),'pool':pool,
             'token_id':item.get('token_id'),'failed_sell_checks':item.get('quote_missing_streak'),
             'last_liquidity_usd':liquidity,'reason':item['quote_study_censored_reason'],
             'no_exit_quote':True,'no_trade':True,'no_fill':True,'no_realized_return':True})
    return retired

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
                        'no_trade':True,'no_executable_quote':True}
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
                            out({'kind':'shadow_exit_quote_only','at':marked,'pool':p,'token_id':s.get('token_id'),
                                 'usdc_proceeds':q['out_amount']/1000000,'quote_multiple':multiple,
                                 'no_trade':True,'no_fill':True,'no_capital_allocated':True})
                            if multiple>=11 and not shadow.get('quote_11x_logged'):
                                shadow['quote_11x_logged']=True
                                out({'kind':'shadow_11x_quote_only_unfilled','at':marked,'pool':p,
                                     'token_id':s.get('token_id'),'quote_multiple':multiple,
                                     'no_trade':True,'no_fill':True,'gas_rent_mev_not_included':True})
                        else:
                            shadow['exit_quote_missing_count']=shadow.get('exit_quote_missing_count',0)+1
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
                                'initial_dex':x['dex'],
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
   'prospective_signals':sum(x['entry'] is not None for x in st.values()),
   'paper_exits':sum(x.get('exit') is not None for x in st.values()),
   'paper_targets_11x':sum(str(x.get('exit',{}).get('reason','')).startswith('target_') for x in st.values() if x.get('exit')),
   'new_sell_quote_mark_attempts':sell_mark_checks,
   'quote_studies_censored_no_fill_total':sum(bool(x.get('quote_study_censored_at')) for x in st.values()),
   'observed_11x_after_signal':sum(x.get('peak_net',0)>=11 for x in st.values()),
   'orders':0,'trades':0,'exit_execution_verified':False,
   'quote_preflight_attempts':quote_checks,
   'risk_blocked_candidate_mints':len(blocked_candidates_this_run),
   'new_shadow_candidates_research_only':shadow_new_this_run,
   'shadow_active_research_only':sum(bool(x.get('shadow')) and not x['shadow'].get('expired')
                                     and not x.get('entry') for x in st.values()),
   'shadow_chart_11x_unfilled_total':sum(1 for x in st.values()
                                           if x.get('shadow') and bool(x['shadow'].get('chart_11x_logged'))),
   'shadow_chart_11x_new_this_run':shadow_chart_11x_this_run,
   'cohort_new_buy_pressure':cohort_added_this_run['buy_pressure'],
   'cohort_new_control':cohort_added_this_run['lower_pressure_control'],
   'cohort_total':sum(bool(z.get('cohort')) for z in st.values()),
   'shadow_chart_11x_first_observed_liquidity_below_usd_10000':sum(1 for x in st.values()
       if x.get('shadow') and x['shadow'].get('chart_11x_illiquid_at_first_observation') is True),
   'shadow_buy_and_sell_quote_baselines_new':shadow_quote_entries_this_run,
   'shadow_buy_and_sell_quote_baselines_total':sum(1 for x in st.values() if x.get('shadow') and x['shadow'].get('entry_quote')),
   'shadow_quote_only_exit_checks_this_run':shadow_quote_marks_this_run,
   'shadow_quote_only_exit_available_this_run':shadow_quote_marks_available_this_run,
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
        'max_later_sell_quote_multiple_unfilled':sh.get('max_exit_quote_multiple'),
        'ever_observed_later_quote_11x':bool(sh.get('quote_11x_logged')),
        'missing_route_does_not_prove_exit':True,
        'return_unrealized':True,'orders':0,'trades':0})
    archived.add(pool)
shadow_ledger=shadow_ledger[-5000:]
# Build descriptive, rigorously prospectively sourced comparisons. A missing
# future sell quotation is CENSORED, not a failed profitable trade or 0x.
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
shadow_learning={'as_of':r['at'],'archived_mints':len(shadow_ledger),
                 'eligible_quote_observed_after_entry':len(complete_quotes),
                 'unfilled_later_quote_2x_count':len(winner),
                 'unfilled_later_quote_below_2x_count':len(control),
                 'unknown_later_quote_excluded':sum(x.get('initial_buy_sell_quote_available') and
                        not x.get('later_sell_quotes_available',0) for x in shadow_ledger),
                 'precursor_medians':precursors,
                 'enough_evidence_for_precursor_inference':bool(precursors),
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
        'not_an_executable_quote':True,'trades':0})
    known.add(pool)
# A 24h chart cohort is not an actual fill. Retain only the last 1000
# records in small local cache; archived per-run artifacts retain snapshots.
ledger=ledger[-1000:]
with open(ledger_path,'w',encoding='utf-8') as f:json.dump(ledger,f,indent=1)
cohort_report['historical_completed_or_censored_retained']=len(ledger)
with open('evidence/prospective_cohort_comparison.json','w') as f:
    json.dump(cohort_report,f,indent=2,allow_nan=False)
print('LIVE_COHORT_24H_COMPARISON',json.dumps(cohort_report,allow_nan=False))
