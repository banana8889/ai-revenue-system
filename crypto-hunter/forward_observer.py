import datetime as d,json,time,urllib.request,urllib.error,urllib.parse,os,math,statistics
url='https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page='
st={}
USDC='EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
quote_checks=0
MAX_ENTRY_QUOTE_CHECKS=5
sell_mark_checks=0
MAX_SELL_MARKS_PER_RUN=8
MAX_OPEN_QUOTED_POSITIONS=4
MAX_SELL_QUOTE_AGE_SECONDS=900
MAX_SHADOW_CANDIDATES=25
SHADOW_RESEARCH_SECONDS=24*3600
# Pre-registered equal-budget pressure versus control pools. No wallet/trades.
COHORT_MAX_PER_GROUP=12
COHORT_HORIZON_SEC=24*3600
COHORT_MIN_OBSERVATIONS=4
COHORT_GROUPS=('buy_pressure','lower_pressure_control')
cohort_added_this_run={x:0 for x in COHORT_GROUPS}
shadow_new_this_run=0
shadow_chart_11x_this_run=0
shadow_quote_entries_this_run=0
shadow_quote_marks_this_run=0
shadow_quote_marks_available_this_run=0
MAX_SHADOW_SELL_QUOTES_PER_RUN=5
shadow_quoted_this_run=set()
sell_marked_this_run=set()
blocked_candidates_this_run=set()
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
    open_positions=[p for p in st.values() if p.get('entry_quote') and p.get('entry') and not p.get('exit')]
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

for cycle in range(3):
    t=d.datetime.now(d.timezone.utc)
    # Time-based expiration even for disappeared/delisted pools: a shadow is never a trade.
    for old in st.values():
        sh=old.get('shadow')
        if sh and not sh.get('expired'):
            try:
                if (t-d.datetime.fromisoformat(sh['at'])).total_seconds()>SHADOW_RESEARCH_SECONDS:
                    sh['expired']=True
            except (TypeError,ValueError,KeyError):
                sh['expired']=True
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
        active=[a for a,x in st.items() if (x.get('entry') and not x.get('exit')) or (x.get('qualified',0)>0 and not x.get('entry') and (t-x['first']).total_seconds()<1200)]
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
                if dt_from>SHADOW_RESEARCH_SECONDS:
                    shadow['expired']=True
                    out({'kind':'shadow_watch_expired','pool':p,'token_id':s.get('token_id'),'at':t.isoformat(),
                         'peak_chart_multiple':shadow.get('peak_chart_multiple',1.0),'no_trade':True})
                elif dt_from>0 and shadow.get('entry_pool_price',0)>0:
                    gross=v/shadow['entry_pool_price']
                    if gross>shadow.get('peak_chart_multiple',1.):
                        shadow['peak_chart_multiple']=gross
                        shadow['peak_chart_liquidity_usd']=x['liq']
                        shadow['peak_chart_liquidity_observed_at']=t.isoformat()
                    shadow['last_chart_multiple']=gross
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
                    if shadow.get('entry_quote') and p not in shadow_quoted_this_run and shadow_quote_marks_this_run<MAX_SHADOW_SELL_QUOTES_PER_RUN:
                        shadow_quoted_this_run.add(p)
                        shadow_quote_marks_this_run+=1
                        q=jupiter_quote(s.get('token_id','').removeprefix('solana_'),USDC,
                                        int(shadow['entry_quote']['token_raw']))
                        marked=d.datetime.now(d.timezone.utc).isoformat()
                        shadow['last_exit_quote_check_at']=marked
                        if q.get('ok'):
                            shadow_quote_marks_available_this_run+=1
                            multiple=q['out_amount']/5000000
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
            already_signaled=any(k!=p and z.get('entry') and z.get('token_id')==token_id for k,z in st.items()) if token_id else False
            if s['entry'] is None and eligible and not already_signaled:
                risk=paper_entry_risk_reason(d.datetime.now(d.timezone.utc))
                if risk:
                    if token_id not in blocked_candidates_this_run:
                        blocked_candidates_this_run.add(token_id)
                        out({'kind':'risk_blocked_candidate','at':d.datetime.now(d.timezone.utc).isoformat(),
                             'pool':p,'token_id':token_id,'reason':risk,'new_position':False})
                    known_shadow=any(k!=p and z.get('shadow') and not z['shadow'].get('expired')
                                     and z.get('token_id')==token_id for k,z in st.items())
                    open_shadows=sum(bool(z.get('shadow')) and not z['shadow'].get('expired')
                                     and not z.get('entry') for z in st.values())
                    if not s.get('shadow') and not known_shadow and open_shadows<MAX_SHADOW_CANDIDATES:
                        shadow={'at':d.datetime.now(d.timezone.utc).isoformat(),
                                'entry_pool_price':v,'peak_chart_multiple':1.,
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
                                out({'kind':'shadow_entry_quote_only','at':shadow['initial_route_check']['at'],
                                     'pool':p,'token_id':token_id,
                                     'immediate_roundtrip_multiple':proposal['immediate_roundtrip_multiple'],
                                     'no_trade':True,'no_fill':True,'no_capital_allocated':True})
                            else:
                                out({'kind':'shadow_entry_route_unavailable','at':shadow['initial_route_check']['at'],
                                     'pool':p,'token_id':token_id,'reason':proposal.get('reason'),
                                     'no_trade':True,'no_capital_allocated':True})
                        s['shadow']=shadow
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
                    s['entry_quote']=preflight
                    s['entry_liquidity_usd']=x['liq']
                    out({'kind':'signal','at':entry_at.isoformat(),'pool':p,'token_id':token_id,'pool_price_at_sample':v,'jupiter_quote_only':preflight,'note':'prospective study, no order/fill; sample price may differ from route quote'})
            elif s['entry'] is not None and not s.get('exit') and t>s['entry'][0]:
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
    if cycle<2:time.sleep(65)
keep={p:x for p,x in st.items() if (d.datetime.now(d.timezone.utc)-x['first']).total_seconds()<=72*3600 or x.get('entry')}
serial={}
for p,x in keep.items():
    y=dict(x);y['first']=y['first'].isoformat()
    if y.get('last_distinct_at'):y['last_distinct_at']=y['last_distinct_at'].isoformat()
    if y.get('entry'):y['entry']=[y['entry'][0].isoformat(),y['entry'][1]]
    serial[p]=y
with open('evidence/state.json','w') as f:json.dump(serial,f,allow_nan=False)
r={'at':d.datetime.now(d.timezone.utc).isoformat(),'unique_pools':len(st),
   'prospective_signals':sum(x['entry'] is not None for x in st.values()),
   'paper_exits':sum(x.get('exit') is not None for x in st.values()),
   'paper_targets_11x':sum(str(x.get('exit',{}).get('reason','')).startswith('target_') for x in st.values() if x.get('exit')),
   'new_sell_quote_mark_attempts':sell_mark_checks,
   'observed_11x_after_signal':sum(x['peak_net']>=11 for x in st.values()),
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
   'shadow_quote_only_11x_unfilled_total':sum(1 for x in st.values() if x.get('shadow') and x['shadow'].get('quote_11x_logged')),
   'quote_marked_distinct_positions':len(sell_marked_this_run),
   'quoted_open_positions':sum(bool(x.get('entry_quote')) and bool(x.get('entry')) and not bool(x.get('exit')) for x in st.values()),
   'quoted_positions_missing_sell_route':sum(bool(x.get('entry_quote')) and bool(x.get('entry')) and not bool(x.get('exit')) and x.get('quote_missing_streak',0)>0 for x in st.values()),
   'quoted_positions_liquidity_under_usd_100':sum(1 for x in st.values()
              if x.get('entry_quote') and x.get('entry') and not x.get('exit')
              and x.get('last_observed_liquidity_usd') is not None and x['last_observed_liquidity_usd']<100),
   'quoted_positions_liquidity_not_observed':sum(1 for x in st.values()
              if x.get('entry_quote') and x.get('entry') and not x.get('exit')
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
groups={}
for label in COHORT_GROUPS:
    obs=[z['cohort'] for z in st.values() if z.get('cohort',{}).get('group')==label]
    done=[z for z in obs if z.get('completed')]
    groups[label]={'enrolled':len(obs),'completed':len(done),
        'censored':sum(bool(z.get('censored')) for z in obs),
        'active':sum(not z.get('completed') and not z.get('censored') for z in obs),
        'first_age_median_minutes':statistics.median([z['first_age_minutes'] for z in obs]) if obs else None,
        'first_liquidity_median_usd':statistics.median([z['first_liquidity_usd'] for z in obs]) if obs else None,
        'first_buyers_1h_median':statistics.median([z['first_buyers_1h'] for z in obs]) if obs else None,
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
