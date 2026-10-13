import datetime as d,json,time,urllib.request,urllib.error,urllib.parse,os,math
url='https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page='
st={}
USDC='EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
quote_checks=0
MAX_ENTRY_QUOTE_CHECKS=5
sell_mark_checks=0
MAX_SELL_MARKS_PER_RUN=8
MAX_OPEN_QUOTED_POSITIONS=4
MAX_SELL_QUOTE_AGE_SECONDS=900
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
            already_signaled=any(k!=p and z.get('entry') and z.get('token_id')==token_id for k,z in st.items()) if token_id else False
            if s['entry'] is None and eligible and not already_signaled:
                risk=paper_entry_risk_reason(d.datetime.now(d.timezone.utc))
                if risk:
                    if token_id not in blocked_candidates_this_run:
                        blocked_candidates_this_run.add(token_id)
                        out({'kind':'risk_blocked_candidate','at':d.datetime.now(d.timezone.utc).isoformat(),
                             'pool':p,'token_id':token_id,'reason':risk,'new_position':False})
                    continue
                mint=token_id.removeprefix('solana_')
                preflight=entry_quote_preflight(mint)
                out({'kind':'jupiter_route_preflight','at':d.datetime.now(d.timezone.utc).isoformat(),'pool':p,'token_id':token_id,'result':preflight})
                if preflight.get('ok'):
                    entry_at=d.datetime.now(d.timezone.utc)
                    s['entry']=(entry_at,v)
                    s['entry_quote']=preflight
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
   'quote_marked_distinct_positions':len(sell_marked_this_run),
   'quoted_open_positions':sum(bool(x.get('entry_quote')) and bool(x.get('entry')) and not bool(x.get('exit')) for x in st.values()),
   'quoted_positions_missing_sell_route':sum(bool(x.get('entry_quote')) and bool(x.get('entry')) and not bool(x.get('exit')) and x.get('quote_missing_streak',0)>0 for x in st.values()),
   'method':'prospective-only live data; new entries require Jupiter $5 buy and sell quotes; new exits monitored with exact token-amount Jupiter sell quotes where available; old chart-only signals remain legacy; no taker or execution, gas/rent/MEV excluded'}
print('FORWARD_OBSERVATION_RECEIPT',json.dumps(r))
with open('evidence/summary.json','w') as f:json.dump(r,f,indent=2)
