import asyncio,datetime as dt,json,os,time,urllib.error,urllib.parse,urllib.request
from pathlib import Path
import websockets

STATE=Path("evidence/state.json")
EVENTS=Path("evidence/liquidity_ws_events.jsonl")
HEALTH=Path("evidence/liquidity_ws_health.json")
WS_URL=os.getenv("SOLANA_WS_URL","wss://api.mainnet-beta.solana.com")
USDC="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
MAX_POOLS=max(1,min(12,int(os.getenv("LIQUIDITY_WS_POOLS","8"))))
DURATION=max(60,min(1800,int(os.getenv("LIQUIDITY_WS_DURATION","1500"))))
ALPHABET=set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
stats={"subscriptions":0,"events":0,"checks":0,"warnings":0,"critical":0,"quote_checks":0,"errors":0}
last_quote=0.0

def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def emit(kind,**fields):
    row={"kind":kind,"at":stamp(),**fields}
    with EVENTS.open("a",encoding="utf-8") as f:
        f.write(json.dumps(row,separators=(",",":"),allow_nan=False)+"\n")
    if kind in ("liquidity_warning","liquidity_critical","ws_error","ws_connected","ws_heartbeat"):
        print("LIQUIDITY_WS",json.dumps(row,separators=(",",":"),allow_nan=False),flush=True)

def health(connected):
    row={"at":stamp(),"connected":connected,**stats,"orders":0,"trades":0,"automatic_exit_enabled":False}
    HEALTH.write_text(json.dumps(row,indent=2),encoding="utf-8")
    emit("ws_heartbeat",**row)

def number(value):
    try:
        x=float(value)
        return x if 0<=x<float("inf") else 0.
    except (TypeError,ValueError):
        return 0.

def get_json(url,headers=None):
    h={"Accept":"application/json","User-Agent":"CryptoHunterLiquidityWatch/1.0"}
    h.update(headers or {})
    req=urllib.request.Request(url,headers=h)
    with urllib.request.urlopen(req,timeout=10) as r:
        return json.load(r)

def quote(mint,quantity):
    params=urllib.parse.urlencode({"inputMint":mint,"outputMint":USDC,"amount":str(quantity)})
    headers={}
    if os.getenv("JUPITER_API_KEY"):
        headers["x-api-key"]=os.environ["JUPITER_API_KEY"]
    try:
        data=get_json("https://api.jup.ag/swap/v2/order?"+params,headers)
        out=int(data.get("outAmount") or 0)
        return {"available":out>0,"usdc":round(out/1e6,6) if out>0 else None,"fee_bps":data.get("feeBps"),"router":data.get("router")}
    except (OSError,ValueError,TypeError,KeyError) as error:
        return {"available":False,"error":type(error).__name__}

def pool_liquidity(pool):
    data=get_json("https://api.geckoterminal.com/api/v2/networks/solana/pools/"+pool)
    raw=((data.get("data") or {}).get("attributes") or {}).get("reserve_in_usd")
    if raw is None:
        raise ValueError("reserve_missing")
    return number(raw)

def candidates():
    try:
        state=json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError,ValueError):
        return []
    selected=[]
    now=dt.datetime.now(dt.timezone.utc)
    for pool,item in state.items():
        shadow=item.get("shadow") or {}
        entry=shadow.get("entry_quote") or item.get("entry_quote") or {}
        mint=(item.get("token_id") or "").removeprefix("solana_")
        qty=int(entry.get("token_raw") or 0)
        if not (32<=len(pool)<=44 and set(pool)<=ALPHABET and 32<=len(mint)<=44 and set(mint)<=ALPHABET and qty>0):
            continue
        if shadow.get("expired") and not (item.get("entry") and not item.get("exit")):
            continue
        initial=number(shadow.get("entry_liquidity_usd") or item.get("entry_liquidity_usd"))
        latest=number(item.get("last_observed_liquidity_usd"))
        if initial<=0:
            continue
        try:
            age=(now-dt.datetime.fromisoformat(item["last_liquidity_seen_at"])).total_seconds()
        except (KeyError,ValueError,TypeError):
            age=1e9
        risk=latest/initial if latest else 0.
        priority=(0 if item.get("entry") and not item.get("exit") else 1,0 if latest>=10000 else 1,age,-latest)
        selected.append((priority,pool,{"pool":pool,"mint":mint,"qty":qty,"initial":initial,"last":latest,"seen":0.,"last_check":0.,"risk":None}))
    selected.sort(key=lambda item:item[0])
    return [item[2] for item in selected[:MAX_POOLS]]

async def inspect(item):
    global last_quote
    now=time.monotonic()
    if now-item["last_check"]<5:
        return
    item["last_check"]=now
    try:
        reserve=await asyncio.to_thread(pool_liquidity,item["pool"])
    except (OSError,ValueError,TypeError) as error:
        stats["errors"]+=1
        emit("source_error",pool=item["pool"],source="geckoterminal",error=type(error).__name__)
        return
    stats["checks"]+=1
    previous=item["last"]
    previous_seen=item["seen"]
    item["last"]=reserve
    item["seen"]=time.monotonic()
    initial=item["initial"]
    entry_drop=max(0.,1-reserve/initial)
    fast_drop=max(0.,1-reserve/previous) if previous>0 and previous_seen and item["seen"]-previous_seen<=60 else 0.
    danger=reserve<10000 or entry_drop>=0.5 or fast_drop>=0.25
    warning=danger or fast_drop>=0.10
    level="critical" if danger else "warning" if warning else "normal"
    if item["risk"]==level and (not danger or now-item.get("last_alert",0)<40):
        return
    item["risk"]=level
    if not warning:
        return
    item["last_alert"]=now
    details={"pool":item["pool"],"mint":item["mint"],"initial_reserve_usd":round(initial,2),"observed_reserve_usd":round(reserve,2),"drop_from_entry_pct":round(entry_drop*100,2),"last_60s_drop_pct":round(fast_drop*100,2),"source":"geckoterminal_after_solana_logs","quote_only":True,"no_executed_sale":True}
    if danger:
        stats["critical"]+=1
    else:
        stats["warnings"]+=1
    emit("liquidity_"+level,**details)
    if not danger:
        return
    delay=max(0.,2.4-(time.monotonic()-last_quote))
    if delay:
        await asyncio.sleep(delay)
    last_quote=time.monotonic()
    result=await asyncio.to_thread(quote,item["mint"],item["qty"])
    stats["quote_checks"]+=1
    emit("urgent_sell_quote",**details,**result,amount_raw=item["qty"],requires_wallet_and_confirmed_fill=True)

async def observe(ws,items,deadline):
    queue=asyncio.Queue(maxsize=64)
    subscriptions={}
    seen={}
    for idx,item in enumerate(items,1):
        await ws.send(json.dumps({"jsonrpc":"2.0","id":idx,"method":"logsSubscribe","params":[{"mentions":[item["pool"]]},{"commitment":"confirmed"}]}))
    async def worker():
        while True:
            item=await queue.get()
            try:
                await inspect(item)
            finally:
                queue.task_done()
    task=asyncio.create_task(worker())
    last_health=0.
    try:
        while time.monotonic()<deadline:
            if time.monotonic()-last_health>=60:
                health(bool(subscriptions))
                last_health=time.monotonic()
            try:
                msg=json.loads(await asyncio.wait_for(ws.recv(),timeout=15))
            except asyncio.TimeoutError:
                continue
            if msg.get("id") is not None:
                idx=msg.get("id")
                if msg.get("result") is not None and isinstance(idx,int) and 1<=idx<=len(items):
                    subscriptions[msg["result"]]=items[idx-1]
                    stats["subscriptions"]=len(subscriptions)
                elif msg.get("error"):
                    stats["errors"]+=1
                    emit("subscription_error",id=idx,error=str(msg["error"])[:120])
                continue
            if msg.get("method")!="logsNotification":
                continue
            params=msg.get("params") or {}
            item=subscriptions.get(params.get("subscription"))
            value=((params.get("result") or {}).get("value") or {})
            if item is None or value.get("err") is not None:
                continue
            stats["events"]+=1
            if time.monotonic()-seen.get(item["pool"],0)<5:
                continue
            seen[item["pool"]]=time.monotonic()
            if not queue.full():
                queue.put_nowait(item)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

async def main():
    EVENTS.parent.mkdir(parents=True,exist_ok=True)
    items=candidates()
    if not items:
        emit("ws_error",reason="no_active_two_sided_quoted_pools_in_checkpoint")
        health(False)
        return
    emit("ws_started",pools=[x["pool"] for x in items],read_only=True,orders=0,trades=0)
    deadline=time.monotonic()+DURATION
    pause=3
    while time.monotonic()<deadline:
        try:
            async with websockets.connect(WS_URL,open_timeout=12,ping_interval=20,ping_timeout=20,max_size=2**20) as ws:
                emit("ws_connected",monitored_pools=len(items))
                await observe(ws,items,deadline)
                pause=3
        except (OSError,ValueError,websockets.exceptions.WebSocketException,asyncio.TimeoutError) as error:
            stats["errors"]+=1
            stats["subscriptions"]=0
            emit("ws_error",reason=type(error).__name__,detail=str(error)[:180])
            health(False)
            await asyncio.sleep(min(pause,max(0.,deadline-time.monotonic())))
            pause=min(pause*2,60)
    health(False)

if __name__=="__main__":
    asyncio.run(main())
