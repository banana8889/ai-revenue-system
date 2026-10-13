import datetime as d,json,time,urllib.request,urllib.error,os,math
url='https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page='
st={}
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
            x={'kind':'pool','at':t.isoformat(),'pool':p,'pair':a.get('name'),'price':v,
               'age_minutes':round(age,2),'liq':num(a.get('reserve_in_usd')),
               'volume_1h':num((a.get('volume_usd') or {}).get('h1')),
               'buys_1h':int(tx.get('buys') or 0),'sells_1h':int(tx.get('sells') or 0),
               'buyers_1h':int(tx.get('buyers') or 0),'dex':((obj.get('relationships') or {}).get('dex') or {}).get('data')}
            out(x)
            s=st.setdefault(p,{'count':0,'first':t,'entry':None,'peak_net':0.,'previous_signature':None,'last_distinct_at':None,'qualified':0,'exit':None})
            signature=(v,x['liq'],x['volume_1h'],x['buys_1h'],x['sells_1h'],x['buyers_1h'])
            gate=(0<=age<=20 and x['liq']>=10000 and x['volume_1h']>=3000 and x['buyers_1h']>=20 and x['buys_1h']>=1.5*max(1,x['sells_1h']))
            gap=(t-s['last_distinct_at']).total_seconds() if s['last_distinct_at'] is not None else None
            if gap is not None and gap>300:s['qualified']=0
            if signature!=s['previous_signature'] and (gap is None or gap>=60):
                s['count']+=1;s['qualified']=s.get('qualified',0)+1 if gate else 0
                s['previous_signature']=signature;s['last_distinct_at']=t
            eligible=s.get('qualified',0)>=2 and gate
            if s['entry'] is None and eligible:
                s['entry']=(t,v);out({'kind':'signal','at':t.isoformat(),'pool':p,'price':v,'note':'prospective research only; no trade fill'})
            elif s['entry'] is not None and not s.get('exit') and t>s['entry'][0]:
                gross=v/s['entry'][1]
                assumed_net=0.97*0.97*gross-0.008
                s['peak_net']=max(s['peak_net'],assumed_net)
                reason=('target_net_11x' if assumed_net>=11 else 'hard_stop_25pct' if assumed_net<=0.75
                        else 'peak_trail_25pct' if s['peak_net']>=3 and assumed_net<=0.75*s['peak_net']
                        else 'hold_limit_24h' if (t-s['entry'][0]).total_seconds()>=86400 else None)
                out({'kind':'mark','at':t.isoformat(),'pool':p,'gross_x':gross,'assumed_net_x':assumed_net,'not_executable_quote':True})
                if reason:
                    s['exit']={'at':t.isoformat(),'reason':reason,'net_multiple_assumed':assumed_net}
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
   'paper_targets_11x':sum(x.get('exit',{}).get('reason')=='target_net_11x' for x in st.values() if x.get('exit')),
   'observed_11x_after_signal':sum(x['peak_net']>=11 for x in st.values()),
   'orders':0,'trades':0,'exit_execution_verified':False,
   'method':'live forward-only multi-page observation; independent qualifying observations within 5 minutes; assumed 3 percent each side + 0.04 USD roundtrip; pool price not an executable quote'}
print('FORWARD_OBSERVATION_RECEIPT',json.dumps(r))
with open('evidence/summary.json','w') as f:json.dump(r,f,indent=2)
