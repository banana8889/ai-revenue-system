import sys

def decode_pumpfun_anchor_event(encoded):
    """Read-only strict Borsh prefix from official Pump.fun onchain IDL.

    CreateEvent: discriminator 27,114,169,77,222,235,99,118.
    TradeEvent: 189,219,127,211,78,230,97,238.
    Data inside a *confirmed Pump.fun program log*, not a transaction
    simulation; unknown discriminators and truncated data return None.
    """
    import base64,struct
    try:
        data=base64.b64decode(encoded,validate=True)
        if len(data)<9 or len(data)>8192:
            return None
        disc=data[:8]
        is_create=disc==bytes((27,114,169,77,222,235,99,118))
        is_trade=disc==bytes((189,219,127,211,78,230,97,238))
        if not (is_create or is_trade):
            return None
        pos=8
        alphabet="123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
        def take(n):
            nonlocal pos
            if n<0 or pos+n>len(data):
                raise ValueError("truncated_borsh_event")
            out=data[pos:pos+n]
            pos+=n
            return out
        def integer(n,signed=False):
            return int.from_bytes(take(n),"little",signed=signed)
        def string():
            size=integer(4)
            if size>2048:
                raise ValueError("oversized_borsh_string")
            return take(size).decode("utf-8")
        def pubkey():
            raw=take(32)
            v=int.from_bytes(raw,"big")
            enc=""
            while v:
                v,mod=divmod(v,58)
                enc=alphabet[mod]+enc
            return "1"*(len(raw)-len(raw.lstrip(bytes((0,)))))+(enc or "")
        if is_create:
            name=string()
            symbol=string()
            _uri=string()
            mint=pubkey()
            curve=pubkey()
            user=pubkey()
            creator=pubkey()
            timestamp=integer(8,True)
            virtual_token=integer(8)
            virtual_sol=integer(8)
            real_token=integer(8)
            total_supply=integer(8)
            token_program=pubkey()
            mayhem=integer(1)
            cashback=integer(1)
            if (mayhem not in (0,1) or cashback not in (0,1)
                    or virtual_token<=0 or total_supply<=0 or
                    virtual_sol<=0 or not 1600000000<timestamp<2000000000):
                return None
            return {"kind":"CreateEvent","mint":mint,
                    "bonding_curve":curve,"user":user,"creator":creator,
                    "created_at_unix":timestamp,
                    "name":name[:128],"symbol":symbol[:32],
                    "virtual_token_raw":virtual_token,
                    "virtual_sol_lamports":virtual_sol,
                    "real_token_raw":real_token,
                    "total_supply_raw":total_supply,
                    "token_program":token_program,
                    "mayhem_mode":bool(mayhem),
                    "cashback_enabled":bool(cashback),
                    "onchain_create_event_discriminator_verified":True}
        mint=pubkey()
        sol_lamports=integer(8)
        token_raw=integer(8)
        bought=integer(1)
        user=pubkey()
        ts=integer(8,True)
        virtual_sol=integer(8)
        virtual_token=integer(8)
        real_sol=integer(8)
        real_token=integer(8)
        if (bought not in (0,1) or virtual_token<=0 or
                not 1600000000<ts<2000000000):
            return None
        return {"kind":"TradeEvent","mint":mint,"user":user,
                "timestamp_unix":ts,"is_buy":bool(bought),
                "sol_lamports_reported":sol_lamports,
                "token_amount_raw":token_raw,
                "virtual_sol_lamports":virtual_sol,
                "virtual_token_raw":virtual_token,
                "real_sol_lamports":real_sol,"real_token_raw":real_token,
                "onchain_trade_event_discriminator_verified":True}
    except (ValueError,UnicodeDecodeError,OverflowError,TypeError,struct.error):
        return None

def run_pumpfun_first_seconds_stream():
    """Real confirmed Pump.fun CREATE events before GeckoTerminal listing.
    Pure observation: never sign, buy, simulate fills, or infer safe liquidity.
    """
    import asyncio,datetime as dt,hashlib,json,os,time,urllib.request,urllib.error,urllib.parse
    from pathlib import Path
    import websockets
    PUMP_PROGRAM="6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
    BASE58=set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
    ENDPOINTS=tuple(dict.fromkeys(filter(None,(
        os.getenv("SOLANA_WS_URL","wss://api.mainnet-beta.solana.com"),
        os.getenv("SOLANA_WS_BACKUP_URL","wss://solana-rpc.publicnode.com"),
        os.getenv("SOLANA_WS_THIRD_URL","wss://solana.api.onfinality.io/public-ws"),
        os.getenv("SOLANA_WS_FOURTH_URL","wss://solana-mainnet.gateway.tatum.io/")))))
    RPC_URLS=tuple(dict.fromkeys(filter(None,(
        os.getenv("SOLANA_RPC_URL","https://api.mainnet-beta.solana.com"),
        os.getenv("SOLANA_RPC_BACKUP_URL","https://solana-rpc.publicnode.com")))))
    AUDIT=Path("evidence/pumpfun_first_seconds_live.jsonl")
    VERIFIED=Path("evidence/pumpfun_verified_mints.jsonl")
    AUDIT.parent.mkdir(exist_ok=True)
    PROOF="--pumpfun-early-proof" in sys.argv
    DURATION=120 if PROOF else max(60,min(1800,int(
        os.getenv("PUMPFUN_FIRST_SECONDS_DURATION","1500"))))
    stats={"program_subscribed":False,"launch_signatures":0,
           "creator_instructions":0,"mint_verified":0,"rpc_attempts":0,
           "rpc_unavailable":0,"latency_within_30s":0,"errors":0,
           "orders":0,"trades":0}
    def stamp():return dt.datetime.now(dt.timezone.utc).isoformat()
    def event(kind,**fields):
        row={"kind":kind,"at":stamp(),**fields,"orders":0,"trades":0}
        serialized=json.dumps(row,separators=(",",":"),allow_nan=False)+"\n"
        with AUDIT.open("a",encoding="utf-8") as log:
            log.write(serialized)
        # Persist only genuine chain-verified mint evidence between study
        # cycles. Unverified signatures do not pollute the durable registry.
        if kind=="pumpfun_early_verified":
            with VERIFIED.open("a",encoding="utf-8") as durable:
                durable.write(serialized)
        if kind in ("program_subscribed","pumpfun_early_verified",
                     "create_transaction_unverified","ws_reconnect"):
            print("PUMPFUN_FIRST_SECONDS",
                  json.dumps(row,separators=(",",":"),allow_nan=False),flush=True)
    def is_pumpfun_create_data(encoded):
        # Anchor global:create / global:create_v2 are distinct from buy,
        # sell, migrate and other Pump.fun instructions in a mixed tx.
        if not isinstance(encoded,str):
            return False
        chars="123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
        value=0
        for char in encoded:
            ix=chars.find(char)
            if ix<0:return False
            value=value*58+ix
        raw=value.to_bytes((value.bit_length()+7)//8,"big")
        raw=bytes((0,))*(len(encoded)-len(encoded.lstrip("1")))+raw
        expected=(hashlib.sha256(b"global:"+name).digest()[:8]
                  for name in (b"create",b"create_v2"))
        return len(raw)>=8 and raw[:8] in expected
    def decode_create_transaction(signature):
        query={"jsonrpc":"2.0","id":19,"method":"getTransaction",
               "params":[signature,{"encoding":"jsonParsed",
                    "maxSupportedTransactionVersion":0,
                    "commitment":"confirmed"}]}
        # Solana logs at confirmed commitment can precede the HTTP node's
        # transaction-indexed read. Retry later and cross-provider; never
        # equate a short-lived HTTP null with an invalid mint.
        tx=None
        last_reason="not_indexed_yet"
        for attempt in range(6):
            endpoint=RPC_URLS[attempt%len(RPC_URLS)]
            try:
                req=urllib.request.Request(endpoint,
                    data=json.dumps(query,separators=(",",":")).encode(),
                    headers={"Content-Type":"application/json",
                             "User-Agent":"CryptoHunterReadOnlyFirstSeconds/1"})
                with urllib.request.urlopen(req,timeout=6) as response:
                    document=json.load(response)
                tx=document.get("result")
                if tx and isinstance(tx,dict):
                    break
                last_reason=("rpc_error" if document.get("error")
                             else "not_indexed_yet")
            except (OSError,ValueError,urllib.error.HTTPError,TimeoutError) as error:
                last_reason=type(error).__name__
            if attempt<5:
                time.sleep(min(2.5,0.7*(attempt+1)))
        if not isinstance(tx,dict) or (tx.get("meta") or {}).get("err"):
            return {"_missing_reason":last_reason}
        message=((tx.get("transaction") or {}).get("message") or {})
        instructions=list(message.get("instructions") or [])
        for entry in (tx.get("meta") or {}).get("innerInstructions") or []:
            instructions.extend(entry.get("instructions") or [])
        for instruction in instructions:
            if (instruction.get("programId")!=PUMP_PROGRAM
                    or not is_pumpfun_create_data(instruction.get("data"))):
                continue
            accounts=instruction.get("accounts") or []
            if not accounts:continue
            mint=accounts[0]
            if not (isinstance(mint,str) and 32<=len(mint)<=44
                    and set(mint)<=BASE58):
                continue
            # Mint is account index zero of the verified Pump.fun Create /
            # CreateV2 instruction. A post-balance alone is not sufficient.
            return {"mint":mint,"slot":tx.get("slot"),
                    "block_time":tx.get("blockTime"),
                    "instruction_account_index":0,
                    "pump_program_verified":True}
        return {"_missing_reason":"program_create_accounts_not_parsed"}
    async def main():
        until=time.monotonic()+DURATION
        seen_signatures=set()
        watched={}
        forward_outcomes={}
        provider_attempt=0
        stats["create_events_decoded"]=0
        stats["trade_events_decoded"]=0
        stats["watched_first30"]=0
        stats["first30_complete"]=0
        stats["first30_censored"]=0
        stats["first30_trades_matched"]=0
        stats["forward5m_complete"]=0
        stats["forward5m_censored"]=0
        stats["forward5m_post30_trade_events"]=0
        stats["stale_provider_reconnects"]=0
        last_progress=time.monotonic()
        FUTURE=Path("evidence/pumpfun_first30_to_5m_outcomes.jsonl")
        FEATURES=Path("evidence/pumpfun_30s_features.jsonl")
        NATIVE_ROUTES=Path("evidence/pumpfun_native_first30_route_quotes.jsonl")
        native_route_state={}
        native_route_tasks=set()
        last_selected_minute={}
        native_quote_lock=asyncio.Semaphore(1)
        stats["native_quote_selected"]=0
        stats["native_quote_buy_available"]=0
        stats["native_quote_roundtrip_available"]=0
        stats["native_quote_five_minute_checked"]=0
        stats["native_quote_five_minute_available"]=0
        def native_route_record(kind,**data):
            row={"kind":kind,"at":stamp(),**data,"orders":0,"trades":0,
                 "no_fill":True,"quote_not_an_executable_transaction":True}
            with NATIVE_ROUTES.open("a",encoding="utf-8") as output:
                output.write(json.dumps(row,separators=(",",":"),
                                        allow_nan=False)+"\n")
            return row
        def native_jupiter_quote(input_mint,output_mint,amount):
            # Same read-only keyless request used by the canonical scanner.
            # Never POST, sign, construct an order, or infer an executed fill.
            usdc="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
            if (not isinstance(amount,int) or amount<=0 or
                    input_mint==output_mint or
                    not {input_mint,output_mint}.issuperset({usdc})):
                return {"available":False,"reason":"invalid_quote_request"}
            time.sleep(2.3)
            params=urllib.parse.urlencode({
                "inputMint":input_mint,"outputMint":output_mint,
                "amount":str(amount)})
            req=urllib.request.Request(
                "https://api.jup.ag/swap/v2/order?"+params,
                headers={"User-Agent":"CryptoHunterFirst30Study/1.0",
                         "Accept":"application/json"})
            try:
                with urllib.request.urlopen(req,timeout=12) as response:
                    result=json.load(response)
                raw=int(result.get("outAmount") or 0)
                return {"available":raw>0,"out_amount":raw if raw>0 else None,
                        "router":result.get("router"),
                        "fee_bps":result.get("feeBps"),
                        "reason":None if raw>0 else "no_positive_quote"}
            except urllib.error.HTTPError as err:
                return {"available":False,"http":err.code,
                        "reason":"http_error_not_a_fill"}
            except (OSError,ValueError,TypeError,TimeoutError):
                return {"available":False,"reason":"unavailable_not_a_loss"}
        def start_native_task(coro):
            task=asyncio.create_task(coro)
            native_route_tasks.add(task)
            task.add_done_callback(native_route_tasks.discard)
        async def quote_native_first30(mint,row,arm):
            usdc="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
            async with native_quote_lock:
                quoted_at=stamp()
                buy=await asyncio.to_thread(
                    native_jupiter_quote,usdc,mint,5000000)
                sell={"available":False,"reason":"buy_route_unavailable"}
                qty=int(buy.get("out_amount") or 0)
                if buy.get("available") and qty>0:
                    stats["native_quote_buy_available"]+=1
                    sell=await asyncio.to_thread(
                        native_jupiter_quote,mint,usdc,qty)
                if sell.get("available"):
                    stats["native_quote_roundtrip_available"]+=1
                observed_sell=int(sell.get("out_amount") or 0)
                native_route_record("native_first30_quote_baseline",
                    mint=mint,arm=arm,
                    completed_first30_at=row["completion_at"],
                    create_at_unix=row["created_at_unix"],
                    quote_started_at=quoted_at,
                    buy_route_available=bool(buy.get("available")),
                    sellback_route_available=bool(sell.get("available")),
                    token_raw=qty if qty>0 else None,
                    sellback_usdc=observed_sell/1000000
                        if sell.get("available") else None,
                    roundtrip_quote_x=round(observed_sell/5000000,8)
                        if sell.get("available") else None,
                    buy_http=buy.get("http"),sell_http=sell.get("http"),
                    buy_router=buy.get("router"),
                    sell_router=sell.get("router"),
                    missing_quote_is_censored_not_loss=True,
                    before_later_curve_outcomes=True,
                    quote_sequential_not_atomic=True)
                if qty>0:
                    native_route_state[mint]={
                        "mint":mint,"arm":arm,
                        "created_at_unix":row["created_at_unix"],
                        "initial_quote_started_at":quoted_at,
                        "token_raw":qty,
                        "initial_sellback_available":bool(sell.get("available")),
                        "followup_scheduled":False}
        async def quote_native_5m(mint,state):
            usdc="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
            async with native_quote_lock:
                at=stamp()
                delay=time.time()-state["created_at_unix"]
                if not 300<=delay<=420:
                    native_route_record("native_first30_late_quote_censored",
                        mint=mint,arm=state["arm"],elapsed_seconds=round(delay,2),
                        not_counted_as_an_executable_outcome=True)
                    return
                q=await asyncio.to_thread(
                    native_jupiter_quote,mint,usdc,state["token_raw"])
                stats["native_quote_five_minute_checked"]+=1
                if q.get("available"):
                    stats["native_quote_five_minute_available"]+=1
                native_route_record("native_first30_five_minute_sell_quote",
                    mint=mint,arm=state["arm"],quote_started_at=at,
                    initial_quote_started_at=state["initial_quote_started_at"],
                    elapsed_since_launch_seconds=round(delay,2),
                    token_raw=state["token_raw"],
                    original_buy_quote_amount_usdc=5,
                    initial_sellback_available=state["initial_sellback_available"],
                    sell_route_available=bool(q.get("available")),
                    sell_quote_usdc=round(int(q["out_amount"])/1000000,8)
                        if q.get("available") else None,
                    future_quote_multiple_x=round(int(q["out_amount"])/5000000,8)
                        if q.get("available") else None,
                    sell_http=q.get("http"),sell_router=q.get("router"),
                    not_a_realized_gain_or_trade=True,
                    fees_rent_mev_and_execution_not_proven=True)
        def schedule_native_quotes():
            for mint,state in native_route_state.items():
                if (not state["followup_scheduled"] and
                        time.time()>=state["created_at_unix"]+300):
                    state["followup_scheduled"]=True
                    start_native_task(quote_native_5m(mint,state))
        def select_native_quote(mint,row):
            # First complete mint per arm per UTC minute; selection is made
            # prospectively BEFORE later 5m price or route outcomes.
            if (row["observed_trades"]<2 or
                    stats["native_quote_selected"]>=60 or
                    len(native_route_tasks)>=4):
                return
            buyers=row["distinct_buyers_first30"]
            if buyers<1:return
            arm="five_plus_buyers" if buyers>=5 else "one_to_four_buyers"
            minute=int(time.time()//60)
            if last_selected_minute.get(arm)==minute:return
            last_selected_minute[arm]=minute
            stats["native_quote_selected"]+=1
            native_route_record("native_first30_quote_selection",
                mint=mint,arm=arm,completed_first30_at=row["completion_at"],
                observed_trades=row["observed_trades"],
                distinct_buyers_first30=buyers,
                creator_sell_first30=row["creator_sell_observed_before30s"],
                selected_before_future_prices=True,
                first_complete_in_arm_for_minute=True)
            start_native_task(quote_native_first30(mint,row,arm))
        def snapshot(mint,state,complete,why):
            if state.get("done"):
                return
            state["done"]=True
            buys=state["buys"]
            sells=state["sells"]
            initial_sol=state["initial_virtual_sol"]
            initial_token=state["initial_virtual_token"]
            last_sol=state["virtual_sol"]
            last_token=state["virtual_token"]
            last_curve_x=(last_sol*initial_token/(last_token*initial_sol)
                          if last_token>0 and initial_token>0 and
                             initial_sol>0 else None)
            max_curve_x=state["peak_curve_x"]
            retracement=(1-last_curve_x/max_curve_x
                         if last_curve_x is not None and max_curve_x>0
                         else None)
            row={"mint":mint,"created_at_unix":state["created_at_unix"],
                 "first_observed_at":state["first_observed_at"],
                 "initial_lag_seconds":state["initial_lag_seconds"],
                 "completion_at":stamp(),
                 "full_first_30s_observation":bool(complete),
                 "censored_reason":None if complete else why,
                 "observed_trades":buys+sells,
                 "buy_events_first30":buys,
                 "sell_events_first30":sells,
                 "distinct_buyers_first30":len(state["buyers"]),
                 "distinct_sellers_first30":len(state["sellers"]),
                 "creator_buy_events_first30":state["creator_buys"],
                 "creator_sell_events_first30":state["creator_sells"],
                 "creator_sell_observed_before30s":state["creator_sells"]>0,
                 "first30_buy_event_count_exceeds_sell_count":buys>sells,
                 "onchain_virtual_curve_last_price_multiple":
                     round(last_curve_x,7) if last_curve_x is not None else None,
                 "onchain_virtual_curve_peak_price_multiple":
                     round(max_curve_x,7),
                 "onchain_curve_peak_to_last_retracement_fraction":
                     round(max(0,retracement),7) if retracement is not None else None,
                 "curve_price_is_not_executable_quote":True,
                 "creator_sale_is_risk_observation_not_rug_proof":True,
                 "buy_sol_lamports_reported_first30":state["buy_lamports"],
                 "sell_sol_lamports_reported_first30":state["sell_lamports"],
                 "last_virtual_sol_lamports":state["virtual_sol"],
                 "last_virtual_token_raw":state["virtual_token"],
                 "initial_virtual_sol_lamports":state["initial_virtual_sol"],
                 "initial_virtual_token_raw":state["initial_virtual_token"],
                 "initial_real_token_raw":state["initial_real_token"],
                 "initial_supply_raw":state["initial_supply"],
                 "is_mayhem_mode":state["mayhem"],
                 "creator":state["creator"],
                 "observation_not_a_trade":True,
                 "quote_type_and_token_decimals_not_inferred":True,
                 "no_execution":True}
            if complete:
                stats["first30_complete"]+=1
                # Durable features for an out-of-time cohort. The first
                # 30s snapshots are frozen BEFORE any later quoted outcome.
                with FEATURES.open("a",encoding="utf-8") as output:
                    output.write(json.dumps(row,separators=(",",":"),
                                            allow_nan=False)+"\n")
                event("pumpfun_first30_features",**row)
                select_native_quote(mint,row)
                # Future outcomes start only AFTER immutable first-30s
                # baseline has been committed. Never leak future trading
                # prices into eligibility or precursor features.
                if last_sol>0 and last_token>0:
                    forward_outcomes[mint]={
                        "created_at_unix":state["created_at_unix"],
                        "baseline_30s_virtual_sol":last_sol,
                        "baseline_30s_virtual_token":last_token,
                        "creator_sell_first30":state["creator_sells"]>0,
                        "buyers_first30":len(state["buyers"]),
                        "buy_events_first30":buys,
                        "sell_events_first30":sells,
                        "peak_x_after30":1.0,"last_x_after30":1.0,
                        "first_cross_2x_at":None,
                        "first_cross_5x_at":None,
                        "first_cross_11x_at":None,
                        "last_trade_unix":None,"post30_trade_events":0,
                        "stream_gap":False}
            else:
                stats["first30_censored"]+=1
                event("pumpfun_first30_censored",
                      mint=mint,reason=why,partial_trades=buys+sells,
                      not_a_negative_label=True)
        def finalize_forward_outcomes(at_end=False):
            complete=[]
            for mint,state in forward_outcomes.items():
                horizon=state["created_at_unix"]+300
                if not at_end and time.time()<horizon+0.5:
                    continue
                valid=not at_end and not state["stream_gap"]
                row={"mint":mint,"first30_to_5m_horizon_seconds":300,
                     "window_baseline":"actual observed 30-second onchain curve state",
                     "outcome_frozen_at":stamp(),
                     "original_launch_unix":state["created_at_unix"],
                     "creator_sell_first30":state["creator_sell_first30"],
                     "distinct_buyers_first30":state["buyers_first30"],
                     "buy_events_first30":state["buy_events_first30"],
                     "sell_events_first30":state["sell_events_first30"],
                     "post30_trade_events":state["post30_trade_events"],
                     "first_cross_2x_at":state["first_cross_2x_at"],
                     "first_cross_5x_at":state["first_cross_5x_at"],
                     "first_cross_11x_at":state["first_cross_11x_at"],
                     "observed_later_curve_peak_x":
                        round(state["peak_x_after30"],7) if valid else None,
                     "observed_later_curve_last_x":
                        round(state["last_x_after30"],7) if valid else None,
                     "curve_peak_ge2x":state["peak_x_after30"]>=2 if valid else None,
                     "curve_peak_ge5x":state["peak_x_after30"]>=5 if valid else None,
                     "curve_peak_ge11x":state["peak_x_after30"]>=11 if valid else None,
                     "complete_5m_telemetry_without_provider_gap":valid,
                     "censored_reason":None if valid else (
                         "provider_gap_during_followup"
                         if state["stream_gap"] else "study_ended_before_5m"),
                     "not_executable_quote_or_filled_trade":True,
                     "future_not_used_in_entry_features":True,"trades":0}
                if valid:
                    stats["forward5m_complete"]+=1
                    with FUTURE.open("a",encoding="utf-8") as handle:
                        handle.write(json.dumps(row,allow_nan=False,
                                                separators=(",",":"))+"\n")
                    event("pumpfun_5m_curve_outcome",**row)
                else:
                    stats["forward5m_censored"]+=1
                    event("pumpfun_5m_outcome_censored",
                          mint=mint,reason=row["censored_reason"],
                          incomplete_not_negative=True)
                complete.append(mint)
            for mint in complete:
                del forward_outcomes[mint]
        def finalize_expired():
            expired=[]
            for mint,state in watched.items():
                if time.time()-state["created_at_unix"]>=30.1:
                    snapshot(mint,state,
                        complete=state["initial_lag_seconds"]<=5 and
                                 not state["stream_gap"],
                        why=("late_discovery" if state["initial_lag_seconds"]>5
                             else "provider_disconnected_during_30s"
                             if state["stream_gap"] else None))
                    expired.append(mint)
            for mint in expired:
                del watched[mint]
        while time.monotonic()<until:
            ep=ENDPOINTS[provider_attempt%len(ENDPOINTS)]
            provider_attempt+=1
            try:
                async with websockets.connect(ep,open_timeout=8,
                        ping_interval=20,ping_timeout=20,max_size=2**21) as ws:
                    await ws.send(json.dumps({"jsonrpc":"2.0","id":91,
                        "method":"logsSubscribe","params":[
                            {"mentions":[PUMP_PROGRAM]},
                            {"commitment":"confirmed"}]}))
                    acknowledged=False
                    stale_create_streak=0
                    latest_fresh_create_at=time.monotonic()
                    while time.monotonic()<until:
                        finalize_expired()
                        finalize_forward_outcomes()
                        schedule_native_quotes()
                        if time.monotonic()-last_progress>=60:
                            print("PUMPFUN_FIRST30_PROGRESS",json.dumps(
                                {"at":stamp(),**stats,"watched_current":len(watched),
                                 "forward5m_current":len(forward_outcomes),
                                 "orders":0,"trades":0},allow_nan=False),flush=True)
                            last_progress=time.monotonic()
                        try:
                            msg=json.loads(await asyncio.wait_for(ws.recv(),timeout=2))
                        except asyncio.TimeoutError:
                            if time.monotonic()-latest_fresh_create_at>35:
                                stats["stale_provider_reconnects"]+=1
                                raise asyncio.TimeoutError("pumpfun_confirmed_log_feed_no_fresh_creates")
                            continue
                        if msg.get("id")==91:
                            acknowledged=isinstance(msg.get("result"),int)
                            if not acknowledged:
                                raise ValueError("pumpfun_program_log_subscription_denied")
                            stats["program_subscribed"]=True
                            event("program_subscribed",provider=ep,
                                  pump_program=PUMP_PROGRAM,
                                  anchor_create_and_trade_direct_decode=True)
                            continue
                        if not acknowledged or msg.get("method")!="logsNotification":
                            continue
                        result=(msg.get("params") or {}).get("result") or {}
                        value=result.get("value") or {}
                        signature=value.get("signature")
                        logs=value.get("logs") or []
                        if (not signature or signature in seen_signatures
                                or value.get("err")):
                            continue
                        seen_signatures.add(signature)
                        if len(seen_signatures)>50000:
                            # Drop old signatures, never erase cohort state.
                            seen_signatures.clear()
                            seen_signatures.add(signature)
                        slot=(result.get("context") or {}).get("slot")
                        decoded=[]
                        for line in logs:
                            if isinstance(line,str) and line.startswith("Program data: "):
                                data=decode_pumpfun_anchor_event(line[14:].strip())
                                if data:
                                    decoded.append(data)
                        for observed in decoded:
                            kind=observed["kind"]
                            mint=observed["mint"]
                            if kind=="CreateEvent":
                                if mint in watched:
                                    continue
                                stats["launch_signatures"]+=1
                                stats["creator_instructions"]+=1
                                stats["create_events_decoded"]+=1
                                lag=time.time()-observed["created_at_unix"]
                                under30=0<=lag<=30
                                if under30:
                                    stale_create_streak=0
                                    latest_fresh_create_at=time.monotonic()
                                else:
                                    stale_create_streak+=1
                                    if stale_create_streak>=4:
                                        stats["stale_provider_reconnects"]+=1
                                        raise asyncio.TimeoutError(
                                            "pumpfun_confirmed_logs_arriving_30s_plus_late")
                                stats["mint_verified"]+=1
                                stats["latency_within_30s"]+=int(under30)
                                event("pumpfun_early_verified",
                                    signature=signature,mint=mint,slot=slot,
                                    block_time_unix=observed["created_at_unix"],
                                    event_timestamp_not_rpc_blocktime=True,
                                    detection_lag_seconds=round(lag,3),
                                    detected_within_first_30s=under30,
                                    launch_eligible_for_live_study=True,
                                    verification_source="onchain_anchor_create_event",
                                    creator=observed["creator"],
                                    user=observed["user"],
                                    bonding_curve=observed["bonding_curve"],
                                    symbol=observed["symbol"],
                                    initial_virtual_sol_lamports=observed["virtual_sol_lamports"],
                                    initial_virtual_token_raw=observed["virtual_token_raw"],
                                    initial_real_token_raw=observed["real_token_raw"],
                                    initial_total_supply_raw=observed["total_supply_raw"],
                                    mayhem_mode=observed["mayhem_mode"],
                                    owner_or_liquidity_safety_not_inferred=True,
                                    not_an_executable_buy=True)
                                if under30 and len(watched)<512:
                                    watched[mint]={
                                        "created_at_unix":observed["created_at_unix"],
                                        "first_observed_at":stamp(),
                                        "initial_lag_seconds":round(lag,3),
                                        "creator":observed["creator"],
                                        "mayhem":observed["mayhem_mode"],
                                        "initial_virtual_sol":observed["virtual_sol_lamports"],
                                        "initial_virtual_token":observed["virtual_token_raw"],
                                        "initial_real_token":observed["real_token_raw"],
                                        "initial_supply":observed["total_supply_raw"],
                                        "virtual_sol":observed["virtual_sol_lamports"],
                                        "virtual_token":observed["virtual_token_raw"],
                                        "buyers":set(),"sellers":set(),
                                        "buys":0,"sells":0,
                                        "creator_buys":0,"creator_sells":0,
                                        "buy_lamports":0,"sell_lamports":0,
                                        "peak_curve_x":1.0,
                                        "creator_first_sell_alerted":False,
                                        "stream_gap":False,"done":False}
                                    stats["watched_first30"]+=1
                            elif kind=="TradeEvent":
                                stats["trade_events_decoded"]+=1
                                state=watched.get(mint)
                                if state is None:
                                    later=forward_outcomes.get(mint)
                                    if later is not None:
                                        age=observed["timestamp_unix"]-later["created_at_unix"]
                                        if 30<age<=300 and observed["virtual_token_raw"]>0:
                                            vs=later["baseline_30s_virtual_sol"]
                                            vt=later["baseline_30s_virtual_token"]
                                            price_x=(observed["virtual_sol_lamports"]*vt/
                                                     (observed["virtual_token_raw"]*vs))
                                            later["post30_trade_events"]+=1
                                            stats["forward5m_post30_trade_events"]+=1
                                            later["last_trade_unix"]=observed["timestamp_unix"]
                                            later["last_x_after30"]=price_x
                                            later["peak_x_after30"]=max(
                                                later["peak_x_after30"],price_x)
                                            for multiplier in (2,5,11):
                                                key="first_cross_"+str(multiplier)+"x_at"
                                                if price_x>=multiplier and later[key] is None:
                                                    later[key]=observed["timestamp_unix"]
                                    continue
                                elapsed=observed["timestamp_unix"]-state["created_at_unix"]
                                if not 0<=elapsed<=30:
                                    continue
                                stats["first30_trades_matched"]+=1
                                user=observed["user"]
                                if observed["is_buy"]:
                                    state["buys"]+=1
                                    state["buyers"].add(user)
                                    state["buy_lamports"]+=observed["sol_lamports_reported"]
                                    state["creator_buys"]+=int(user==state["creator"])
                                else:
                                    state["sells"]+=1
                                    state["sellers"].add(user)
                                    state["sell_lamports"]+=observed["sol_lamports_reported"]
                                    state["creator_sells"]+=int(user==state["creator"])
                                    if (user==state["creator"] and not
                                            state["creator_first_sell_alerted"]):
                                        state["creator_first_sell_alerted"]=True
                                        event("pumpfun_creator_sell_first30_observed",
                                              mint=mint,signature=signature,
                                              observed_trade_timestamp=observed["timestamp_unix"],
                                              minutes_since_launch=round(elapsed/60,5),
                                              creator=state["creator"],
                                              not_a_predicted_rug=True,
                                              not_a_sell_order=True)
                                state["virtual_sol"]=observed["virtual_sol_lamports"]
                                state["virtual_token"]=observed["virtual_token_raw"]
                                if (state["initial_virtual_sol"]>0 and
                                        state["initial_virtual_token"]>0 and
                                        state["virtual_token"]>0):
                                    x=(state["virtual_sol"]*state["initial_virtual_token"]/
                                       (state["virtual_token"]*state["initial_virtual_sol"]))
                                    state["peak_curve_x"]=max(state["peak_curve_x"],x)
            except (OSError,ValueError,asyncio.TimeoutError,
                    websockets.exceptions.WebSocketException) as error:
                stats["errors"]+=1
                for state in watched.values():
                    state["stream_gap"]=True
                for state in forward_outcomes.values():
                    state["stream_gap"]=True
                event("ws_reconnect",reason=type(error).__name__,
                      detail=str(error)[:120])
                await asyncio.sleep(min(2,max(0.,until-time.monotonic())))
        finalize_expired()
        finalize_forward_outcomes(at_end=True)
        schedule_native_quotes()
        if native_route_tasks:
            await asyncio.wait(native_route_tasks,timeout=18)
        stats["native_quote_followups_not_matured_at_end"]=sum(
            not value["followup_scheduled"]
            for value in native_route_state.values())
        # A truncated observation window is censored, never called 0 trades.
        for mint,state in watched.items():
            snapshot(mint,state,False,"study_window_ended_before_30s")
        print("PUMPFUN_REAL_MAINNET_LAUNCH_RECEIPT",
              json.dumps({"at":stamp(),**stats,"read_only":True,
                          "first30_coverage_not_100pct_claimed":True,
                          "no_trading_method_proven":True},allow_nan=False),
              flush=True)
        if PROOF:
            persisted=0
            if FEATURES.is_file():
                with FEATURES.open(encoding="utf-8") as source:
                    for line in source:
                        try:
                            row=json.loads(line)
                        except ValueError:
                            continue
                        if (row.get("full_first_30s_observation") is True
                                and row.get("no_execution") is True):
                            persisted+=1
            print("PUMPFUN_FIRST30_PERSISTENCE_RECEIPT",
                  json.dumps({"observed_complete":stats["first30_complete"],
                              "valid_saved_rows":persisted,
                              "write_integrity_verified":
                                  persisted==stats["first30_complete"],
                              "read_only":True,"orders":0,"trades":0}),flush=True)
            if persisted!=stats["first30_complete"]:
                raise SystemExit("PUMPFUN_FIRST30_PERSISTENCE_FAILED")
        if PROOF and (not stats["program_subscribed"]
                      or stats["create_events_decoded"]<1
                      or stats["trade_events_decoded"]<1
                      or stats["mint_verified"]<1
                      or stats["first30_complete"]<1):
            raise SystemExit("PUMPFUN_FIRST_SECONDS_PROOF_FAILED")
    asyncio.run(main())

if ('--pumpfun-early-proof' in sys.argv or
        '--pumpfun-early-sensor' in sys.argv):
    run_pumpfun_first_seconds_stream()
    raise SystemExit(0)

def first_confirmed_drain_after(pool,trigger_at,evidence,horizon_seconds=None):
    """First recorded confirmed dual-vault drain; never skip a near-immediate one.
    Times are RPC receipt times, not authoritative transaction start times.
    """
    import datetime as _dt
    try:
        t0=_dt.datetime.fromisoformat(trigger_at)
    except (ValueError,TypeError):
        return None
    candidates=[]
    for ev in evidence:
        if ev.get('pool')!=pool or not (
            ev.get('kind')=='onchain_vault_joint_drop'
            or (ev.get('kind')=='onchain_vault_rpc_confirmation'
                and ev.get('outcome')=='confirmed')):
            continue
        try:
            seconds=(_dt.datetime.fromisoformat(ev['at'])-t0).total_seconds()
        except (KeyError,ValueError,TypeError):
            continue
        if seconds>=0 and (horizon_seconds is None
                            or seconds<=horizon_seconds):
            candidates.append((seconds,ev))
    return min(candidates,key=lambda z:z[0]) if candidates else None

def audit_first_drain_from_real_ledger(path):
    import json
    from pathlib import Path
    file=Path(path)
    if not file.is_file() or not file.stat().st_size:
        raise SystemExit('AUDIT_FAIL=no_real_persisted_event_ledger')
    records=[]
    with file.open(encoding='utf-8') as fh:
        for line in fh:
            if line.strip():
                records.append(json.loads(line))
    first={}
    precursor_kinds={
        'onchain_preemptive_sell_pressure',
        'onchain_lp_creator_balance_drop',
        'onchain_pool_creator_token_balance_drop'}
    for ev in records:
        k=(ev.get('pool'),ev.get('kind'))
        if k[0] and k[1] in precursor_kinds and ev.get('at'):
            if k not in first or ev['at']<first[k]['at']:
                first[k]=ev
    confirmed=[]
    immediate=[]
    for (pool,kind),ev in first.items():
        hit=first_confirmed_drain_after(pool,ev['at'],records,600)
        if hit is None:continue
        seconds,drain=hit
        row={'pool':pool,'kind':kind,'first_confirmed_delay_seconds':round(seconds,3),
             'first_confirmed_at':drain['at'],'not_physical_start_time':True}
        (confirmed if seconds>=30 else immediate).append(row)
        # Strong invariant: there cannot be an earlier verified drain which
        # the report skipped to manufacture an apparent lead time.
        assert all(not (e.get('pool')==pool and e.get('at') and
            e.get('kind')=='onchain_vault_rpc_confirmation'
            and e.get('outcome')=='confirmed'
            and ev['at']<=e['at']<drain['at']) for e in records)
    report={'real_persisted_events':len(records),
            'first_trigger_groups':len(first),
            'first_drain_over_30_seconds':len(confirmed),
            'first_drain_under_30_seconds':len(immediate),
            'confirmed_cases':confirmed[:15],'too_late_cases':immediate[:15],
            'physical_drain_start_not_proven':True,
            'quote_fills_or_profit_not_implied':True}
    print('CRYPTO_REAL_FIRST_DRAIN_AUDIT',json.dumps(report,allow_nan=False),flush=True)
    return report

if '--audit-precursor-evidence' in sys.argv:
    audit_first_drain_from_real_ledger('evidence/onchain_risk_outcomes_ledger.jsonl')
    raise SystemExit(0)

def run_embedded_liquidity_sensor():
    import asyncio,base64,datetime as dt,hashlib,json,os,time,urllib.error,urllib.parse,urllib.request
    from pathlib import Path
    import websockets

    STATE=Path("evidence/state.json")
    EVENTS=Path("evidence/liquidity_ws_events.jsonl")
    HEALTH=Path("evidence/liquidity_ws_health.json")
    LIVE_FEED=Path("evidence/liquidity_live_candidates.jsonl")
    LP_PROSPECTIVE=Path("evidence/onchain_lp_control_prospective.jsonl")
    WS_URL=os.getenv("SOLANA_WS_URL","wss://api.mainnet-beta.solana.com")
    WS_BACKUP=os.getenv("SOLANA_WS_BACKUP_URL","wss://solana-rpc.publicnode.com")
    WS_ENDPOINTS=tuple(dict.fromkeys(url for url in (WS_URL,WS_BACKUP) if url))
    USDC="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    MAX_POOLS=max(1,min(12,int(os.getenv("LIQUIDITY_WS_POOLS","8"))))
    DURATION=max(60,min(1800,int(os.getenv("LIQUIDITY_WS_DURATION","1500"))))
    MAX_PAIR_TELEMETRY_ROWS=max(1000,MAX_POOLS*((DURATION+2)//3+20))
    B58="123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    ALPHABET=set(B58)
    PUMP_PROGRAM="pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
    PUMP_POOL_DISCRIMINATOR=bytes((241,154,109,4,17,177,109,188))
    stats={"subscriptions":0,"events":0,"checks":0,"warnings":0,"critical":0,"quote_checks":0,"errors":0,"dex_checks":0,"gecko_fallback_checks":0,"lp_instruction_hints":0,"max_subscriptions":0,"tx_rpc_checks":0,"tx_rpc_errors":0,"onchain_token_balance_changes":0,"mint_risk_checks":0,"mint_risk_warnings":0,"pump_vault_queries":0,"pump_vault_confirmed":0,"pump_vault_joint_drops":0,"pump_vault_errors":0,"vault_ws_subscriptions":0,"max_vault_ws_subscriptions":0,"vault_ws_events":0,"vault_ws_joint_drops":0,"lp_control_probes":0,"lp_control_confirmed":0,"lp_control_high_risk":0,"lp_control_unknown":0,"pre_rug_sell_pressure_events":0,"candidate_preflight_queued":0,"creator_lp_ws_subscriptions":0,"creator_lp_ws_events":0,"creator_lp_early_decreases":0,"creator_token_probes":0,"creator_token_confirmed":0,"creator_token_ws_subscriptions":0,"creator_token_ws_events":0,"creator_token_decreases":0,"vault_pairs_same_slot":0,"vault_pairs_bounded_skew":0,"vault_pair_skew_rejections":0,"vault_cross_slot_joint_drop_suspected":0,"vault_pair_telemetry_rows":0,"vault_notifications_rejected":0,"vault_binary_account_updates_decoded":0,"vault_rpc_confirmation_checks":0,"vault_rpc_joint_drops_confirmed":0,"vault_rpc_joint_drops_not_confirmed":0,"vault_rpc_joint_drops_unknown":0,"slot_notifications":0,"slot_subscriptions_confirmed":0}
    last_quote=0.0
    last_liquidity_request=0.0
    source_cooldown_until=0.0
    quote_lock=asyncio.Lock()

    def stamp():
        return dt.datetime.now(dt.timezone.utc).isoformat()

    def emit(kind,**fields):
        row={"kind":kind,"at":stamp(),**fields}
        with EVENTS.open("a",encoding="utf-8") as f:
            f.write(json.dumps(row,separators=(",",":"),allow_nan=False)+"\n")
        if kind in ("liquidity_warning","liquidity_critical","source_error","subscription_error","urgent_sell_quote","lp_withdrawal_log_hint","onchain_token_balance_evidence","onchain_token_balance_error","onchain_mint_risk","onchain_pumpswap_vaults","onchain_vault_joint_drop","onchain_vault_ws_joint_drop","onchain_vault_cross_slot_joint_drop_suspected","onchain_vault_ws_joint_drop_suspected","onchain_vault_rpc_confirmation","onchain_lp_control_snapshot","onchain_lp_creator_balance_drop","onchain_pool_creator_token_snapshot","onchain_pool_creator_token_balance_drop","onchain_preemptive_sell_pressure","ws_candidate_preflight","ws_vault_subscribed","ws_dynamic_candidate","ws_error","ws_connected","ws_slot_confirmed","ws_heartbeat"):
            print("LIQUIDITY_WS",json.dumps(row,separators=(",",":"),allow_nan=False),flush=True)

    def health(connected):
        row={"at":stamp(),"connected":connected,**stats,
             "vault_pair_telemetry_budget":MAX_PAIR_TELEMETRY_ROWS,
             "vault_pair_telemetry_budget_exhausted":stats["vault_pair_telemetry_rows"]>=MAX_PAIR_TELEMETRY_ROWS,
             "ever_subscribed":stats["max_subscriptions"]>0,
             "ever_received_events":stats["events"]>0,
             "orders":0,"trades":0,"automatic_exit_enabled":False}
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

    def parse_dex_liquidity(data,pool):
        # The pair address MUST match; never substitute another pool of the mint.
        for pair in (data.get("pairs") or []):
            if not isinstance(pair,dict) or pair.get("chainId")!="solana" or pair.get("pairAddress")!=pool:
                continue
            raw=(pair.get("liquidity") or {}).get("usd")
            if raw is not None:
                return number(raw)
        return None

    def pool_liquidity(pool):
        # Separate public data provider: the observer already heavily uses
        # GeckoTerminal, so use DexScreener first and Gecko only as fallback.
        try:
            data=get_json("https://api.dexscreener.com/latest/dex/pairs/solana/"+pool)
            reserve=parse_dex_liquidity(data,pool)
            if reserve is not None:
                return reserve,"dexscreener"
        except (OSError,ValueError,TypeError):
            pass
        if time.monotonic()<source_cooldown_until:
            raise RuntimeError("dex_unavailable_gecko_cooldown")
        data=get_json("https://api.geckoterminal.com/api/v2/networks/solana/pools/"+pool)
        raw=((data.get("data") or {}).get("attributes") or {}).get("reserve_in_usd")
        if raw is None:
            raise ValueError("both_liquidity_sources_missing")
        return number(raw),"geckoterminal"

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
            try:
                qty=int(entry.get("token_raw") or 0)
            except (ValueError,TypeError):
                continue
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
            priority=(0 if item.get("entry") and not item.get("exit") else 1,0 if latest>=10000 else 1,age,-latest)
            selected.append((priority,pool,{"pool":pool,"mint":mint,"qty":qty,"initial":initial,"last":latest,"seen":0.,"last_check":0.,"risk":None}))
        selected.sort(key=lambda item:item[0])
        # Reserve 50% of slots for same-run mint discoveries. Two historical
        # slots remain allocated to open quote positions and recent liquidity.
        # For the other historical slots, deterministically rotate pools
        # WITHOUT a previously verified first LP snapshot. Otherwise a
        # fixed top-4 shortlist repeatedly tests the same pools every cycle.
        startup_slots=max(1,MAX_POOLS//2)
        reserved=min(startup_slots,max(1,MAX_POOLS//4))
        core=selected[:reserved]
        known_lp=set()
        try:
            with LP_PROSPECTIVE.open("r",encoding="utf-8") as lp_file:
                for line in lp_file:
                    try:
                        row=json.loads(line)
                        if row.get("available") and row.get("pool"):
                            known_lp.add(row["pool"])
                    except (ValueError,TypeError):
                        continue
        except OSError:
            pass
        cycle=os.getenv("GITHUB_RUN_ID",dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H"))
        other=selected[reserved:]
        fresh=[item for item in other if item[1] not in known_lp and item[0][2]<=7200]
        if len(fresh)<startup_slots-reserved:
            fresh=[item for item in other if item[1] not in known_lp]
        if len(fresh)<startup_slots-reserved:
            fresh=other
        # Stable within each run; different across runs. No future outcomes
        # or retrospectively observed peaks affect sample selection.
        fresh.sort(key=lambda item:hashlib.sha256(
            (str(cycle)+":"+item[1]).encode("ascii")).digest())
        rotating=fresh[:startup_slots-reserved]
        chosen=core+rotating
        emit("candidate_sampling_receipt",
             run_key_sha256=hashlib.sha256(str(cycle).encode("ascii")).hexdigest()[:12],
             eligible=len(selected),historical_slots=len(chosen),
             always_on_open_or_liquid_positions=len(core),
             rotating_lp_unseen_slots=sum(x[1] not in known_lp for x in rotating),
             prior_lp_seen=len(known_lp),
             fresh_discovery_slots=max(0,MAX_POOLS-len(chosen)),
             selection_precedes_new_market_outcomes=True)
        return [item[2] for item in chosen]

    async def inspect(item):
        nonlocal last_quote,last_liquidity_request,source_cooldown_until
        now=time.monotonic()
        if now-item["last_check"]<12:
            return
        wait=max(0.,3.2-(now-last_liquidity_request))
        if wait:
            await asyncio.sleep(wait)
        now=time.monotonic()
        last_liquidity_request=now
        item["last_check"]=now
        try:
            reserve,provider=await asyncio.to_thread(pool_liquidity,item["pool"])
        except urllib.error.HTTPError as error:
            stats["errors"]+=1
            if error.code in (429,500,502,503,504):
                source_cooldown_until=time.monotonic()+(90 if error.code==429 else 30)
            emit("source_error",pool=item["pool"],source="geckoterminal",http_status=error.code,cooldown_seconds=round(max(0.,source_cooldown_until-time.monotonic()),2))
            return
        except Exception as error:
            stats["errors"]+=1
            emit("source_error",pool=item["pool"],source="geckoterminal",error=type(error).__name__)
            return
        stats["checks"]+=1
        stats["dex_checks" if provider=="dexscreener" else "gecko_fallback_checks"]+=1
        previous=item["last"]
        previous_seen=item["seen"]
        item["last"]=reserve
        item["seen"]=time.monotonic()
        # Initial checkpoint reserves came from GeckoTerminal. Never compare
        # DexScreener reserves to that baseline as proof of a liquidity drain.
        # Establish a per-provider baseline on the first observation.
        baselines=item.setdefault("provider_baselines",{})
        if provider not in baselines:
            baselines[provider]=item["initial"] if provider=="geckoterminal" else reserve
        initial=baselines[provider]
        entry_drop=max(0.,1-reserve/initial) if initial>0 else 0.
        fast_drop=max(0.,1-reserve/previous) if previous>0 and previous_seen and item["seen"]-previous_seen<=60 and item.get("last_provider")==provider else 0.
        item["last_provider"]=provider
        danger=reserve<10000 or entry_drop>=0.5 or fast_drop>=0.25
        warning=danger or fast_drop>=0.10
        level="critical" if danger else "warning" if warning else "normal"
        if item["risk"]==level and not (danger and reserve<=item.get("last_alert_reserve",0)*0.75 and now-item.get("last_alert",0)>=15):
            return
        item["risk"]=level
        if not warning:
            return
        item["last_alert"]=now
        item["last_alert_reserve"]=reserve
        details={"pool":item["pool"],"mint":item["mint"],"initial_reserve_usd":round(initial,2),"observed_reserve_usd":round(reserve,2),"drop_from_entry_pct":round(entry_drop*100,2),"last_60s_drop_pct":round(fast_drop*100,2),"source":provider+"_after_solana_logs","quote_only":True,"no_executed_sale":True}
        if danger:
            stats["critical"]+=1
        else:
            stats["warnings"]+=1
        emit("liquidity_"+level,**details)
        if warning:
            await urgent_quote_only(item,reason="reserve_warning" if not danger else "reserve_critical",details=details)

    def rpc_read(method,params):
        endpoint=os.getenv("SOLANA_HTTP_RPC_URL","https://api.mainnet-beta.solana.com")
        payload=json.dumps({"jsonrpc":"2.0","id":1,"method":method,
                            "params":params}).encode("utf-8")
        req=urllib.request.Request(endpoint,data=payload,
            headers={"Accept":"application/json","Content-Type":"application/json",
                     "User-Agent":"CryptoHunterLiquidityWatch/1.1"})
        with urllib.request.urlopen(req,timeout=8) as response:
            out=json.load(response)
        if out.get("error"):
            raise RuntimeError("solana_rpc_error_"+str((out["error"] or {}).get("code")))
        return out.get("result")

    def encode_pubkey(raw):
        if not isinstance(raw,(bytes,bytearray)) or len(raw)!=32:
            raise ValueError("invalid_pubkey_bytes")
        n=int.from_bytes(raw,"big")
        digits=""
        while n:
            n,r=divmod(n,58)
            digits=B58[r]+digits
        return "1"*(32-len(raw.lstrip(bytes([0]))))+(digits or "")

    def parse_pumpswap_pool(account,mint):
        # PumpSwap anchor discriminator and immutable field offsets 8..203.
        # Reject every unknown owner, discriminator, mint or short layout.
        if not isinstance(account,dict) or account.get("owner")!=PUMP_PROGRAM:
            return None
        data=account.get("data") or []
        if not (isinstance(data,list) and len(data)==2 and data[1]=="base64"):
            return None
        try:
            blob=base64.b64decode(data[0],validate=True)
        except (ValueError,TypeError):
            return None
        if len(blob)<211 or blob[:8]!=PUMP_POOL_DISCRIMINATOR:
            return None
        keys={"base_mint":encode_pubkey(blob[43:75]),
              "quote_mint":encode_pubkey(blob[75:107]),
              "lp_mint":encode_pubkey(blob[107:139]),
              "pool_creator":encode_pubkey(blob[11:43]),
              "pool_lp_supply_raw":str(int.from_bytes(blob[203:211],"little")) if len(blob)>=211 else None,
              "base_vault":encode_pubkey(blob[139:171]),
              "quote_vault":encode_pubkey(blob[171:203])}
        if mint not in (keys["base_mint"],keys["quote_mint"]):
            return None
        return keys

    def parse_pumpswap_vault_amounts(accounts,pool,layout):
        values=(accounts or {}).get("value") or []
        if not isinstance(values,list) or len(values)!=2:
            return None
        amounts=[]
        for i,account in enumerate(values):
            if not isinstance(account,dict):
                return None
            parsed=((account.get("data") or {}).get("parsed") or {})
            info=parsed.get("info") or {}
            expected=layout["base_mint"] if i==0 else layout["quote_mint"]
            if (parsed.get("type")!="account" or info.get("mint")!=expected
                    or info.get("owner")!=pool):
                return None
            raw=((info.get("tokenAmount") or {}).get("amount"))
            try:
                amount=int(raw)
            except (TypeError,ValueError):
                return None
            if amount<0:
                return None
            amounts.append(amount)
        return amounts

    def read_pumpswap_vaults(pool,mint):
        account=rpc_read("getAccountInfo",[pool,{"encoding":"base64",
                                                "commitment":"confirmed"}])
        layout=parse_pumpswap_pool((account or {}).get("value"),mint)
        if layout is None:
            return {"verified_pumpswap_layout":False,
                    "reason":"not_matching_pumpswap_pool"}
        pair=rpc_read("getMultipleAccounts",
            [[layout["base_vault"],layout["quote_vault"]],
             {"encoding":"jsonParsed","commitment":"confirmed"}])
        amounts=parse_pumpswap_vault_amounts(pair,pool,layout)
        if amounts is None:
            return {"verified_pumpswap_layout":False,
                    "reason":"vault_account_mint_or_owner_mismatch"}
        return {"verified_pumpswap_layout":True,
                "base_mint":layout["base_mint"],"quote_mint":layout["quote_mint"],
                "lp_mint":layout["lp_mint"],
                "pool_creator":layout["pool_creator"],
                "pool_lp_supply_raw":layout["pool_lp_supply_raw"],
                "base_vault":layout["base_vault"],"quote_vault":layout["quote_vault"],
                "base_raw":str(amounts[0]),"quote_raw":str(amounts[1]),
                "slot":(pair or {}).get("context",{}).get("slot"),
                "raw_token_reserves_not_USD_or_effective_quote":True}

    def lp_control_risk_from_balances(lp_supply_raw,token_supply_raw,
                                      creator_lp_raw,largest_lp_raw):
        # Provable bounds only. An unknown owner is NOT zero LP.
        try:
            pool_supply=int(lp_supply_raw)
            issued=int(token_supply_raw)
        except (TypeError,ValueError):
            return {"available":False,"reason":"unknown_total_supply"}
        if pool_supply<=0 or issued<0 or issued>pool_supply:
            return {"available":False,"reason":"inconsistent_lp_supply"}
        creator=None
        largest=None
        try:
            if creator_lp_raw is not None:
                creator=int(creator_lp_raw)
            if largest_lp_raw is not None:
                largest=int(largest_lp_raw)
        except (TypeError,ValueError):
            return {"available":False,"reason":"malformed_holder_amounts"}
        if any(v is not None and (v<0 or v>issued) for v in (creator,largest)):
            return {"available":False,"reason":"holder_exceeds_supply"}
        if issued==0:
            # All liquid balances are necessarily zero when mint supply is zero.
            creator=largest=0
        outstanding=issued/pool_supply
        creator_frac=creator/pool_supply if creator is not None else None
        largest_frac=largest/pool_supply if largest is not None else None
        flags=[]
        if outstanding>=0.8:
            flags.append("outstanding_lp_supply_ge_80pct_of_pool_basis")
        if creator_frac is not None and creator_frac>=0.2:
            flags.append("creator_direct_lp_balance_ge_20pct")
        if largest_frac is not None and largest_frac>=0.5:
            flags.append("largest_account_lp_balance_ge_50pct")
        directly_concentrated=(creator_frac is not None and creator_frac>=0.2
                               or largest_frac is not None and largest_frac>=0.5)
        # High outstanding LP supply is only broad redeemability exposure.
        # It must not be conflated with known direct control by one holder.
        control_category=("verified_concentrated_direct_holder"
                          if directly_concentrated else
                          "outstanding_supply_only_holder_unknown"
                          if outstanding>=0.8 and (creator is None or largest is None)
                          else "outstanding_supply_only_no_large_direct_holder"
                          if outstanding>=0.8 else
                          "holder_coverage_incomplete"
                          if creator is None or largest is None else
                          "no_large_direct_holder_observed_not_safe")
        direct_status=(True if directly_concentrated else
                       False if creator is not None and largest is not None
                       else None)
        return {"available":True,"holder_detail_complete":creator is not None and largest is not None,
                "direct_large_holder_risk_observed":direct_status,
                "outstanding_supply_only_flag":outstanding>=0.8 and not directly_concentrated,
                "lp_control_evidence_category":control_category,
                "outstanding_lp_supply_fraction":round(outstanding,6),
                "creator_direct_lp_fraction":round(creator_frac,6) if creator_frac is not None else None,
                "largest_token_account_lp_fraction":round(largest_frac,6) if largest_frac is not None else None,
                "structural_risk_flags":flags,
                "structural_risk_tier":("elevated" if flags else
                                        "incomplete" if creator is None or largest is None else
                                        "unflagged_not_safe"),
                "mint_supply_upper_bound_on_lp_withdrawal_not_guarantee":True,
                "not_probability_or_advance_knowledge":True,
                "creator_related_wallets_not_inferred":True}

    def creator_lp_token_accounts(accounts,lp_mint,creator):
        # Directly registered owner only; transfers to other wallets remain
        # unlinked. Never infer beneficial ownership from token movement.
        if not isinstance(accounts,list):
            return []
        result=[]
        for account in accounts:
            if not isinstance(account,dict):
                continue
            pubkey=account.get("pubkey")
            info=(((account.get("account") or {}).get("data") or {}).get("parsed") or {}).get("info") or {}
            if (not isinstance(pubkey,str) or not 32<=len(pubkey)<=44
                    or not set(pubkey)<=ALPHABET
                    or info.get("mint")!=lp_mint or info.get("owner")!=creator):
                continue
            try:
                qty=int(((info.get("tokenAmount") or {}).get("amount")))
            except (ValueError,TypeError):
                continue
            if qty>0:
                result.append({"pubkey":pubkey,"amount_raw":str(qty)})
        return sorted(result,key=lambda x:int(x["amount_raw"]),reverse=True)

    def lp_balance_decline(previous,current):
        # Fixed 10% fall, minimum one token; can represent transfer or burn.
        # This is not an advance warning if vaults drain in the SAME slot.
        return (isinstance(previous,int) and isinstance(current,int)
                and previous>0 and current>=0
                and current*100<=previous*90)

    def read_lp_control(proof):
        lp=proof["lp_mint"]
        creator=proof["pool_creator"]
        endpoint_health={}
        def query(method,params):
            try:
                result=rpc_read(method,params)
                endpoint_health[method]="ok"
                return result
            except urllib.error.HTTPError as error:
                endpoint_health[method]="HTTP_"+str(error.code)
                return None
            except Exception as error:
                endpoint_health[method]=type(error).__name__
                return None
        supply_result=query("getTokenSupply",[lp,{"commitment":"confirmed"}])
        value=(supply_result or {}).get("value") or {}
        mint_supply=value.get("amount")
        top_result=None
        creator_response=None
        watched_creator_accounts=[]
        if mint_supply is not None:
            try:
                no_supply=int(mint_supply)==0
            except (ValueError,TypeError):
                no_supply=False
            if no_supply:
                # No outstanding LP means no LP holder account can hold units.
                top_amount=0
                own_amount=0
                endpoint_health["getTokenLargestAccounts"]="deduced_zero_from_mint_supply"
                endpoint_health["getTokenAccountsByOwner"]="deduced_zero_from_mint_supply"
            else:
                creator_response=query("getTokenAccountsByOwner",[
                    creator,{"mint":lp},{"encoding":"jsonParsed","commitment":"confirmed"}])
                own_accounts=(creator_response or {}).get("value") if creator_response else None
                own_amount=None
                if isinstance(own_accounts,list):
                    own_amount=0
                    for acct in own_accounts:
                        info=(((acct.get("account") or {}).get("data") or {}).get("parsed") or {}).get("info") or {}
                        if info.get("mint")!=lp or info.get("owner")!=creator:
                            continue
                        own_amount+=int(((info.get("tokenAmount") or {}).get("amount")) or 0)
                    valid_accounts=creator_lp_token_accounts(own_accounts,lp,creator)
                    watched_creator_accounts=valid_accounts[:2]
                else:
                    valid_accounts=[]
                # If the creator's verified token accounts sum to the ENTIRE
                # mint supply, the biggest LP account is already known.
                # Avoid a redundant getTokenLargestAccounts public-RPC call.
                if (own_amount is not None and own_amount==int(mint_supply)
                        and sum(int(x["amount_raw"]) for x in valid_accounts)==own_amount):
                    top_amount=max((int(x["amount_raw"]) for x in valid_accounts),
                                   default=0)
                    endpoint_health["getTokenLargestAccounts"]="proven_from_full_owner_supply"
                else:
                    top_result=query("getTokenLargestAccounts",[
                        lp,{"commitment":"confirmed"}])
                    top_values=(top_result or {}).get("value") if top_result else None
                    top_amount=(max((int(x.get("amount") or 0) for x in top_values
                                    if isinstance(x,dict)),default=0)
                                if isinstance(top_values,list) else None)
        else:
            top_amount=None
            own_amount=None
        risk=lp_control_risk_from_balances(
            proof.get("pool_lp_supply_raw"),mint_supply,own_amount,top_amount)
        return {"lp_mint":lp,"pool_creator":creator,
                "pool_lp_supply_raw":proof.get("pool_lp_supply_raw"),
                "current_lp_mint_supply_raw":mint_supply,
                "creator_direct_lp_raw":str(own_amount) if own_amount is not None else None,
                "largest_lp_token_account_raw":str(top_amount) if top_amount is not None else None,
                "read_endpoint_health":endpoint_health,
                "creator_lp_token_accounts":watched_creator_accounts,
                "creator_lp_token_accounts_only_directly_held":True,
                "owner_scope":"direct creator public key only, not related wallets",
                "pool_state_slot":proof.get("slot"),
                "supply_slot":(supply_result or {}).get("context",{}).get("slot"),
                "largest_slot":(top_result or {}).get("context",{}).get("slot"),
                "max_request_budget":3,
                **risk}

    async def inspect_lp_control(item,proof):
        if item.get("lp_control_checked") or stats["lp_control_probes"]>=6:
            return
        item["lp_control_checked"]=True
        stats["lp_control_probes"]+=1
        observed_at=stamp()
        try:
            detail=await asyncio.to_thread(read_lp_control,proof)
        except Exception as error:
            detail={"available":False,"reason":type(error).__name__}
        if detail.get("available"):
            item["lp_control_first_observed"]=detail.get("structural_risk_tier")
            item["creator_lp_monitor_accounts"]=detail.get("creator_lp_token_accounts") or []
            item["creator_lp_mint"]=detail.get("lp_mint")
            item["creator_lp_owner"]=detail.get("pool_creator")
            item["creator_lp_baseline_slot"]=detail.get("supply_slot")
            stats["lp_control_confirmed"]+=1
            if detail.get("structural_risk_flags"):
                stats["lp_control_high_risk"]+=1
        else:
            stats["lp_control_unknown"]+=1
        snapshot={"pool":item["pool"],"mint":item["mint"],
                  "sample_started_at":observed_at,"sample_completed_at":stamp(),
                  "as_observed_not_at_original_signal":True,
                  "future_outcome_not_used_to_set_risk":True,
                  "no_future_data_used_to_set_risk":True,
                  "no_real_trade":True,**detail}
        LP_PROSPECTIVE.parent.mkdir(parents=True,exist_ok=True)
        if detail.get("available"):
            first_already_recorded=False
            try:
                with LP_PROSPECTIVE.open("r",encoding="utf-8") as previous_evidence:
                    for oldline in previous_evidence:
                        try:
                            prev=json.loads(oldline)
                        except (TypeError,ValueError):
                            continue
                        if prev.get("pool")==item["pool"] and prev.get("available"):
                            first_already_recorded=True
                            break
            except OSError:
                pass
            if not first_already_recorded:
                with LP_PROSPECTIVE.open("a",encoding="utf-8") as f:
                    f.write(json.dumps(snapshot,allow_nan=False,separators=(",",":"))+"\n")
        emit("onchain_lp_control_snapshot",first_verified_baseline_preserved=True,
             **snapshot)
        # Price the SAME token quantity at first observed structural risk,
        # before any later verified drain. No wallet, signature or fill.
        if (detail.get("available") and
                detail.get("structural_risk_tier")=="elevated" and
                not item.get("structural_risk_quote_checked")):
            item["structural_risk_quote_checked"]=True
            await urgent_quote_only(item,reason="lp_control_prewithdrawal_review",
                details={"lp_structural_tier":"elevated",
                         "lp_sample_completed_at":snapshot["sample_completed_at"],
                         "risk_is_not_certain_future_drain":True})

    def read_pool_creator_token_holdings(proof,watch_mint):
        # Observe the pool-creator public key, NOT an inferred developer.
        # Migrated PumpSwap pools may have burned LP while the creator or
        # related wallets still hold the tradeable token.
        if watch_mint!=proof["base_mint"]:
            return {"available":False,"reason":"watch_mint_not_pool_base"}
        creator=proof["pool_creator"]
        total=rpc_read("getTokenSupply",[watch_mint,
                                        {"commitment":"confirmed"}])
        data=(total or {}).get("value") or {}
        supply=int(data["amount"])
        holders=rpc_read("getTokenAccountsByOwner",[
            creator,{"mint":watch_mint},
            {"encoding":"jsonParsed","commitment":"confirmed"}])
        accounts=(holders or {}).get("value")
        if supply<=0 or not isinstance(accounts,list):
            return {"available":False,"reason":"missing_supply_or_owner_accounts"}
        verified=creator_lp_token_accounts(accounts,watch_mint,creator)
        owned=sum(int(a["amount_raw"]) for a in verified)
        if owned>supply:
            return {"available":False,"reason":"owner_total_exceeds_current_mint_supply"}
        return {"available":True,"pool_creator_address":creator,
                "watched_tradeable_mint":watch_mint,
                "creator_direct_token_raw":str(owned),
                "mint_supply_raw":str(supply),
                "creator_direct_supply_fraction":round(owned/supply,6),
                "creator_token_accounts_for_ws":verified[:2],
                "token_control_flag_ge_2pct":owned*100>=supply*2,
                "owner_rpc_slot":(holders or {}).get("context",{}).get("slot"),
                "mint_supply_rpc_slot":(total or {}).get("context",{}).get("slot"),
                "pool_creator_is_not_verified_token_developer":True,
                "other_wallets_and_related_accounts_unknown":True,
                "no_trade_or_execution":True}

    async def inspect_pool_creator_token(item,proof):
        if (not proof or not proof.get("verified_pumpswap_layout")
                or item.get("creator_token_checked")
                or stats["creator_token_probes"]>=4):
            return
        item["creator_token_checked"]=True
        stats["creator_token_probes"]+=1
        try:
            detail=await asyncio.to_thread(read_pool_creator_token_holdings,
                                           proof,item["mint"])
        except Exception as error:
            detail={"available":False,"reason":type(error).__name__}
        if detail.get("available"):
            stats["creator_token_confirmed"]+=1
            item["creator_token_monitor_accounts"]=detail["creator_token_accounts_for_ws"]
            item["creator_token_owner"]=detail["pool_creator_address"]
            item["creator_token_baseline_slot"]=detail.get("owner_rpc_slot")
            item["creator_token_mint"]=item["mint"]
        emit("onchain_pool_creator_token_snapshot",
             pool=item["pool"],mint=item["mint"],
             observed_at=stamp(),first_observed_without_future_outcome=True,
             **detail)

    async def inspect_pumpswap_vaults(item):
        if stats["pump_vault_queries"]>=16:
            return
        if time.monotonic()-item.get("last_vault_probe_at",0)<25:
            return
        item["last_vault_probe_at"]=time.monotonic()
        if item.get("verified_not_pumpswap"):
            return
        stats["pump_vault_queries"]+=1
        try:
            result=await asyncio.to_thread(read_pumpswap_vaults,item["pool"],item["mint"])
        except Exception as error:
            stats["pump_vault_errors"]+=1
            emit("onchain_pumpswap_vaults",pool=item["pool"],mint=item["mint"],
                 verified_pumpswap_layout=False,reason=type(error).__name__)
            return
        if not result.get("verified_pumpswap_layout"):
            if result.get("reason")=="not_matching_pumpswap_pool":
                item["verified_not_pumpswap"]=True
            return
        stats["pump_vault_confirmed"]+=1
        current=(int(result["base_raw"]),int(result["quote_raw"]))
        previous=item.get("last_vault_raw")
        item["last_vault_raw"]=current
        emit("onchain_pumpswap_vaults",pool=item["pool"],mint=item["mint"],**result)
        await inspect_lp_control(item,result)
        # A swap normally moves vault balances in opposite directions.
        # Both vaults dropping together warrants an urgent sell quotation.
        if (previous and all(x>0 for x in previous)
                and current[0]*100<=previous[0]*80
                and current[1]*100<=previous[1]*80):
            stats["pump_vault_joint_drops"]+=1
            emit("onchain_vault_joint_drop",pool=item["pool"],mint=item["mint"],
                 previous_base_raw=str(previous[0]),current_base_raw=str(current[0]),
                 previous_quote_raw=str(previous[1]),current_quote_raw=str(current[1]),
                 verified_pumpswap_accounts=True,
                 observation_not_proof_of_user_fill=True)
            await urgent_quote_only(item,reason="confirmed_joint_pumpswap_vault_balance_drop")
        return result

    def read_onchain_transaction(signature,watch_mint):
        # Read-only onchain transaction metadata, never a swap, order, or
        # implied proof that the changed token account is a pool vault.
        body=json.dumps({"jsonrpc":"2.0","id":1,"method":"getTransaction",
            "params":[signature,{"encoding":"jsonParsed","commitment":"confirmed",
                                  "maxSupportedTransactionVersion":0}]}).encode("utf-8")
        url=os.getenv("SOLANA_HTTP_RPC_URL","https://api.mainnet-beta.solana.com")
        req=urllib.request.Request(url,data=body,
            headers={"Accept":"application/json","Content-Type":"application/json",
                     "User-Agent":"CryptoHunterLiquidityWatch/1.1"})
        with urllib.request.urlopen(req,timeout=8) as r:
            response=json.load(r)
        tx=response.get("result")
        if not isinstance(tx,dict) or not isinstance(tx.get("meta"),dict):
            return {"available":False,"reason":"transaction_unavailable_or_unconfirmed"}
        meta=tx["meta"]
        pre={(x.get("accountIndex"),x.get("mint")):x
             for x in meta.get("preTokenBalances") or []
             if isinstance(x,dict)}
        post={(x.get("accountIndex"),x.get("mint")):x
              for x in meta.get("postTokenBalances") or []
              if isinstance(x,dict)}
        keys=((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or []
        changes=[]
        for key in pre.keys()|post.keys():
            if key[1]!=watch_mint:
                continue
            before=pre.get(key) or {}
            after=post.get(key) or {}
            try:
                a=int(((before.get("uiTokenAmount") or {}).get("amount")) or 0)
                b=int(((after.get("uiTokenAmount") or {}).get("amount")) or 0)
            except (ValueError,TypeError):
                continue
            if a==b:
                continue
            idx=key[0]
            account_key=keys[idx] if isinstance(idx,int) and 0<=idx<len(keys) else None
            account=(account_key.get("pubkey") if isinstance(account_key,dict) else
                     account_key if isinstance(account_key,str) else None)
            changes.append({"token_account":account,"mint":watch_mint,
                            "raw_delta":str(b-a),
                            "owner_before":before.get("owner"),
                            "owner_after":after.get("owner")})
        return {"available":True,"slot":tx.get("slot"),
                "matched_token_accounts_changed":len(changes),
                "changes":changes[:12],
                "not_validated_pool_vaults":True,
                "tx_successful":meta.get("err") is None}

    async def verify_lp_hint_onchain(item,signature):
        if not isinstance(signature,str) or not (70<=len(signature)<=100 and set(signature)<=ALPHABET):
            return
        if stats["tx_rpc_checks"]>=10:
            return
        stats["tx_rpc_checks"]+=1
        try:
            result=await asyncio.to_thread(read_onchain_transaction,signature,item["mint"])
            stats["onchain_token_balance_changes"]+=result.get("matched_token_accounts_changed",0)
            emit("onchain_token_balance_evidence",pool=item["pool"],mint=item["mint"],
                 signature=signature,**result)
        except Exception as error:
            stats["tx_rpc_errors"]+=1
            emit("onchain_token_balance_error",pool=item["pool"],
                 reason=type(error).__name__)

    def parse_onchain_mint_risk(account):
        # Mint authorities indicate control risk, not certainty of a rug.
        if not isinstance(account,dict):
            return {"available":False,"reason":"no_account"}
        data=account.get("data") or {}
        parsed=data.get("parsed") or {}
        if parsed.get("type")!="mint":
            return {"available":False,"reason":"not_parsed_mint"}
        info=parsed.get("info") or {}
        if "mintAuthority" not in info and "freezeAuthority" not in info:
            return {"available":False,"reason":"mint_authorities_not_reported"}
        extensions=info.get("extensions") or []
        flags=[x.get("extension") for x in extensions if isinstance(x,dict) and x.get("extension")]
        risky={"transferFeeConfig","permanentDelegate","transferHook","defaultAccountState",
               "nonTransferable","confidentialTransferMint","pausableConfig"}
        special=sorted(set(flags)&risky)
        return {"available":True,"program_owner":account.get("owner"),
                "mint_authority_active":bool(info.get("mintAuthority")),
                "freeze_authority_active":bool(info.get("freezeAuthority")),
                "risk_extensions":special,
                "risk_notice":bool(info.get("mintAuthority") or
                                   info.get("freezeAuthority") or special),
                "not_a_guarantee_of_safety":True}

    def read_mint_account(mint):
        url=os.getenv("SOLANA_HTTP_RPC_URL","https://api.mainnet-beta.solana.com")
        payload=json.dumps({"jsonrpc":"2.0","id":1,"method":"getAccountInfo",
            "params":[mint,{"encoding":"jsonParsed","commitment":"confirmed"}]}).encode("utf-8")
        request=urllib.request.Request(url,data=payload,
            headers={"Accept":"application/json","Content-Type":"application/json",
                     "User-Agent":"CryptoHunterLiquidityWatch/1.1"})
        with urllib.request.urlopen(request,timeout=8) as response:
            body=json.load(response)
        result=body.get("result") or {}
        return parse_onchain_mint_risk(result.get("value"))

    async def inspect_onchain_mint_risk(item):
        if item.get("mint_risk_checked") or stats["mint_risk_checks"]>=8:
            return
        item["mint_risk_checked"]=True
        stats["mint_risk_checks"]+=1
        try:
            result=await asyncio.to_thread(read_mint_account,item["mint"])
            if result.get("risk_notice"):
                stats["mint_risk_warnings"]+=1
            emit("onchain_mint_risk",pool=item["pool"],mint=item["mint"],**result)
        except Exception as error:
            stats["tx_rpc_errors"]+=1
            emit("onchain_mint_risk",pool=item["pool"],mint=item["mint"],
                 available=False,reason=type(error).__name__)

    async def urgent_quote_only(item,reason,details=None):
        nonlocal last_quote
        # Serialize urgent quote requests independently of the slower reserve
        # worker so WS reserve emergencies cannot starve behind Gecko requests.
        async with quote_lock:
            delay=max(0.,2.4-(time.monotonic()-last_quote))
            if delay:
                await asyncio.sleep(delay)
            last_quote=time.monotonic()
            result=await asyncio.to_thread(quote,item["mint"],item["qty"])
            stats["quote_checks"]+=1
            fields=dict(details or {})
            fields.setdefault("pool",item["pool"])
            fields.setdefault("mint",item["mint"])
            emit("urgent_sell_quote",trigger=reason,**fields,**result,
                 amount_raw=item["qty"],requires_wallet_and_confirmed_fill=True)

    def ws_vault_balance(value,expected_mint,expected_owner):
        if not isinstance(value,dict):return None
        data=value.get("data")
        if isinstance(data,list):
            if (len(data)!=2 or data[1]!="base64" or
                    value.get("owner") not in (
                        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                        "TokenzQdBNbLqP5VEhdkAS6EPFHGubZyfxZbGbqXoU")):
                return None
            def key_bytes(address):
                if not isinstance(address,str) or not 32<=len(address)<=44:
                    return None
                n=0
                for ch in address:
                    digit=B58.find(ch)
                    if digit<0:return None
                    n=n*58+digit
                raw=n.to_bytes((n.bit_length()+7)//8,"big")
                raw=bytes((0,))*(len(address)-len(address.lstrip("1")))+raw
                return raw if len(raw)==32 else None
            try:
                raw=base64.b64decode(data[0],validate=True)
            except (ValueError,TypeError):
                return None
            if (not 165<=len(raw)<=4096 or raw[108] not in (1,2) or
                    raw[:32]!=key_bytes(expected_mint) or
                    raw[32:64]!=key_bytes(expected_owner)):
                return None
            stats["vault_binary_account_updates_decoded"]+=1
            return int.from_bytes(raw[64:72],"little")
        if not isinstance(data,dict):return None
        parsed=data.get("parsed")
        if not isinstance(parsed,dict):return None
        info=parsed.get("info")
        if not isinstance(info,dict):return None
        if (parsed.get("type")!="account" or info.get("mint")!=expected_mint
                or info.get("owner")!=expected_owner):
            return None
        token_amount=info.get("tokenAmount")
        if not isinstance(token_amount,dict):return None
        try:
            amount=int(token_amount.get("amount"))
        except (TypeError,ValueError):
            return None
        return amount if amount>=0 else None

    def pair_recent_vault_updates(latest,side,slot,amount,min_slot,max_skew=3):
        # Cross-account observations are NOT atomic unless both have same
        # Solana context slot. Reject outdated/replayed notifications; accept
        # close-but-different slots only as explicitly non-atomic observations.
        if side not in ("base","quote") or not isinstance(slot,int) or slot<=min_slot:
            return None
        old=latest.get(side)
        if old and slot<=old[0]:
            return None
        latest[side]=(slot,amount)
        if "base" not in latest or "quote" not in latest:
            return None
        bs,ba=latest["base"]
        qs,qa=latest["quote"]
        skew=abs(bs-qs)
        if skew>max_skew:
            return None
        return {"current":(ba,qa),"pair_slot":max(bs,qs),
                "slot_skew":skew,"same_slot_ws_observation":skew==0,
                "atomic_same_slot":False,"not_an_atomic_snapshot":True,
                "non_atomic_cross_slot_observation":skew>0}

    def both_vaults_refreshed(latest,previous_slots):
        return all(side in latest and
                   latest[side][0]>previous_slots.get(side,0)
                   for side in ("base","quote"))

    def early_sell_pressure(before,now,watched_index):
        # A net sale of the watched mint INTO the pool grows its reserves
        # and consumes the counter-asset, independent of base/quote orientation.
        # 12%/12% is a pre-registered observational alarm, not calibrated odds.
        if not (isinstance(watched_index,int) and watched_index in (0,1)):
            return False
        if not before or len(before)!=2 or len(now)!=2:
            return False
        other=1-watched_index
        return (before[watched_index]>0 and before[other]>0
                and now[watched_index]*100>=before[watched_index]*112
                and now[other]*100<=before[other]*88
                and now[other]>0)

    def vault_joint_drop(previous,current):
        return (previous is not None and len(previous)==len(current)==2
                and all(x>0 for x in previous)
                and all(n>=0 and n*100<=p*80 for p,n in zip(previous,current)))

    def rpc_joint_drop_status(baseline,snapshot,trigger_slot):
        # getMultipleAccounts is a single consistent RPC bank snapshot.
        # Two accountNotifications, even at the same slot, are NOT atomic.
        if not (isinstance(baseline,dict) and isinstance(snapshot,dict)
                and snapshot.get("verified_pumpswap_layout")):
            return "unknown"
        try:
            if (any(snapshot[k]!=baseline[k] for k in
                    ("base_vault","quote_vault","base_mint","quote_mint"))
                    or not isinstance(trigger_slot,int)):
                return "unknown"
            before=(int(baseline["base_raw"]),int(baseline["quote_raw"]))
            after=(int(snapshot["base_raw"]),int(snapshot["quote_raw"]))
            prior_slot=int(baseline["slot"])
            fresh_slot=int(snapshot["slot"])
        except (ValueError,TypeError,KeyError):
            return "unknown"
        if fresh_slot<=prior_slot or fresh_slot<trigger_slot:
            return "stale"
        return "confirmed" if vault_joint_drop(before,after) else "not_confirmed"

    def feed_candidate(raw):
        pool=raw.get("pool","")
        mint=raw.get("mint","")
        try:
            qty=int(raw.get("qty") or 0)
        except (ValueError,TypeError):
            return None
        if not (isinstance(pool,str) and 32<=len(pool)<=44 and set(pool)<=ALPHABET
                and isinstance(mint,str) and 32<=len(mint)<=44 and set(mint)<=ALPHABET
                and qty>0):
            return None
        liquidity=number(raw.get("liquidity_usd"))
        if liquidity<=0:
            return None
        return {"pool":pool,"mint":mint,"qty":qty,"initial":liquidity,"last":liquidity,
                "seen":0.,"last_check":0.,"risk":None,"fresh":True}

    async def observe(ws,items,deadline):
        queue=asyncio.Queue(maxsize=64)
        subscriptions={}
        seen={}
        vault_pending=asyncio.Queue(maxsize=10)
        pending_vault_requests={}
        vault_subscriptions={}
        vault_streamed=set()
        vault_last_pair={}
        vault_pair_updates={}
        vault_history={}
        urgent_ws_tasks=set()
        next_vault_id=1000
        old_streamed=False
        last_feed_scan=0.
        feed_offset=0
        subscribed_pools={item["pool"] for item in items}
        SLOT_REQUEST_ID=900001
        slot_request_at=time.monotonic()
        last_slot_at=0.
        slot_acknowledged=False
        await ws.send(json.dumps({"jsonrpc":"2.0","id":SLOT_REQUEST_ID,
                                  "method":"slotSubscribe"}))
        for idx,item in enumerate(items,1):
            await ws.send(json.dumps({"jsonrpc":"2.0","id":idx,"method":"logsSubscribe","params":[{"mentions":[item["pool"]]},{"commitment":"confirmed"}]}))
        async def worker():
            nonlocal old_streamed
            while True:
                task_entry=await queue.get()
                is_vault_alarm=isinstance(task_entry,tuple) and task_entry[0]=="vault_alarm"
                item=task_entry[1] if is_vault_alarm else task_entry
                try:
                    if is_vault_alarm:
                        await urgent_quote_only(item,reason="same_slot_pumpswap_vault_ws_joint_drop")
                        continue
                    signature=item.pop("early_lp_signature",None)
                    if signature:
                        await urgent_quote_only(item,reason="unverified_lp_withdrawal_log")
                        await verify_lp_hint_onchain(item,signature)
                    await inspect(item)
                    proof=await inspect_pumpswap_vaults(item)
                    await inspect_pool_creator_token(item,proof)
                    if (proof and proof.get("verified_pumpswap_layout")
                            and item["pool"] not in vault_streamed
                            and (item.get("fresh") or not old_streamed)
                            and not vault_pending.full()):
                        if not item.get("fresh"):
                            old_streamed=True
                        vault_streamed.add(item["pool"])
                        vault_pending.put_nowait((item,proof))
                    await inspect_onchain_mint_risk(item)
                except Exception as error:
                    stats["errors"]+=1
                    emit("ws_error",reason="event_worker_error",detail=type(error).__name__)
                finally:
                    queue.task_done()
        task=asyncio.create_task(worker())
        # The first historical core pool gets one slot. Reserve the other
        # two vault pairs for freshly discovered coins, not stale incumbents.
        if items and not queue.full():
            queue.put_nowait(items[0])
            stats["candidate_preflight_queued"]+=1
            emit("ws_candidate_preflight",pool=items[0]["pool"],
                 fresh_discovery=False,signal_quote_only=True)
        last_health=0.
        try:
            while time.monotonic()<deadline:
                clock=time.monotonic()
                if ((not last_slot_at and clock-slot_request_at>45)
                        or (last_slot_at and clock-last_slot_at>45)):
                    emit("ws_error",reason="solana_slot_feed_stale",
                         slot_acknowledged=slot_acknowledged,
                         last_slot_age_seconds=(round(clock-last_slot_at,2)
                                                if last_slot_at else None))
                    raise asyncio.TimeoutError("solana_slot_feed_stale")
                while not vault_pending.empty() and len(vault_last_pair)<3:
                    item,proof=vault_pending.get_nowait()
                    pool=item["pool"]
                    if pool in vault_last_pair:
                        continue
                    vault_last_pair[pool]=(int(proof["base_raw"]),int(proof["quote_raw"]))
                    item["rpc_vault_proof"]=proof
                    vault_history[pool]=[(time.monotonic(),vault_last_pair[pool])]
                    item["watched_vault_index"]=(
                        0 if item["mint"]==proof["base_mint"] else
                        1 if item["mint"]==proof["quote_mint"] else None)
                    if isinstance(proof.get("slot"),int):
                        item["last_vault_ws_slot"]=proof["slot"]
                        item["rpc_vault_baseline_slot"]=proof["slot"]
                    vault_pair_updates[pool]={}
                    item["last_paired_account_slots"]={
                        "base":int(proof.get("slot") or 0),
                        "quote":int(proof.get("slot") or 0)}
                    item["last_paired_atomic"]=True
                    for side in ("base","quote"):
                        next_vault_id+=1
                        vault_addr=proof[side+"_vault"]
                        pending_vault_requests[next_vault_id]=(item,side,proof[side+"_mint"])
                        await ws.send(json.dumps({"jsonrpc":"2.0","id":next_vault_id,
                            "method":"accountSubscribe","params":[vault_addr,
                                {"encoding":"jsonParsed","commitment":"confirmed"}]}))
                    # Creator LP token account is an OPTIONAL third stream.
                    # It is distinct from the two pool token vaults, and may
                    # expose a transfer/burn before any subsequent rug.
                    for account in (item.get("creator_lp_monitor_accounts") or [])[:1]:
                        lp_address=account.get("pubkey")
                        if not (lp_address and item.get("creator_lp_mint")
                                and item.get("creator_lp_owner")):
                            continue
                        next_vault_id+=1
                        pending_vault_requests[next_vault_id]=(
                            item,"creator_lp",item["creator_lp_mint"])
                        item["creator_lp_ws_initial_raw"]=int(account["amount_raw"])
                        item["creator_lp_ws_account"]=lp_address
                        await ws.send(json.dumps({"jsonrpc":"2.0","id":next_vault_id,
                            "method":"accountSubscribe","params":[lp_address,
                                {"encoding":"jsonParsed","commitment":"confirmed"}]}))
                    for account in (item.get("creator_token_monitor_accounts") or [])[:1]:
                        holder_address=account.get("pubkey")
                        if not (holder_address and item.get("creator_token_mint")
                                and item.get("creator_token_owner")):
                            continue
                        next_vault_id+=1
                        pending_vault_requests[next_vault_id]=(
                            item,"creator_token",item["creator_token_mint"])
                        item["creator_token_ws_initial_raw"]=int(account["amount_raw"])
                        item["creator_token_ws_account"]=holder_address
                        await ws.send(json.dumps({"jsonrpc":"2.0","id":next_vault_id,
                            "method":"accountSubscribe","params":[holder_address,
                                {"encoding":"jsonParsed","commitment":"confirmed"}]}))
                    emit("ws_vault_subscribed",pool=pool,mint=item["mint"],
                         base_vault=proof["base_vault"],quote_vault=proof["quote_vault"],
                         creator_lp_account=item.get("creator_lp_ws_account"),
                         pool_creator_token_account=item.get("creator_token_ws_account"),
                         rpc_baseline_slot=proof.get("slot"),
                         active_vault_pairs=len(vault_last_pair))
                if time.monotonic()-last_feed_scan>=2 and len(items)<MAX_POOLS:
                    last_feed_scan=time.monotonic()
                    try:
                        with LIVE_FEED.open("r",encoding="utf-8") as feed:
                            feed.seek(feed_offset)
                            while len(items)<MAX_POOLS:
                                line=feed.readline()
                                if not line:
                                    break
                                try:
                                    new=feed_candidate(json.loads(line))
                                except (ValueError,TypeError):
                                    continue
                                if new is None or new["pool"] in subscribed_pools:
                                    continue
                                idx=len(items)+1
                                items.append(new)
                                subscribed_pools.add(new["pool"])
                                await ws.send(json.dumps({"jsonrpc":"2.0","id":idx,"method":"logsSubscribe",
                                    "params":[{"mentions":[new["pool"]]},{"commitment":"confirmed"}]}))
                                emit("ws_dynamic_candidate",pool=new["pool"],mint=new["mint"],slot=idx)
                                # Audit LP control, mint authority and direct
                                # PumpSwap vaults NOW, even without a later
                                # transaction log for this new pool.
                                if not queue.full():
                                    queue.put_nowait(new)
                                    stats["candidate_preflight_queued"]+=1
                                    emit("ws_candidate_preflight",pool=new["pool"],
                                         fresh_discovery=True,signal_quote_only=True)
                            feed_offset=feed.tell()
                    except FileNotFoundError:
                        pass
                if time.monotonic()-last_health>=60:
                    health(bool(subscriptions) and slot_acknowledged and
                           bool(last_slot_at) and time.monotonic()-last_slot_at<=45)
                    last_health=time.monotonic()
                try:
                    msg=json.loads(await asyncio.wait_for(ws.recv(),timeout=3))
                except asyncio.TimeoutError:
                    continue
                if msg.get("id") is not None:
                    idx=msg.get("id")
                    if idx==SLOT_REQUEST_ID:
                        slot_acknowledged=isinstance(msg.get("result"),int)
                        if slot_acknowledged:
                            stats["slot_subscriptions_confirmed"]+=1
                        else:
                            raise asyncio.TimeoutError("solana_slot_subscription_denied")
                        continue
                    if idx in pending_vault_requests:
                        item,side,mint=pending_vault_requests.pop(idx)
                        if isinstance(msg.get("result"),int):
                            vault_subscriptions[msg["result"]]=(item,side,mint)
                            if side=="creator_lp":
                                stats["creator_lp_ws_subscriptions"]+=1
                            elif side=="creator_token":
                                stats["creator_token_ws_subscriptions"]+=1
                            else:
                                stats["vault_ws_subscriptions"]=sum(
                                    1 for _,kind,_ in vault_subscriptions.values()
                                    if kind in ("base","quote"))
                                stats["max_vault_ws_subscriptions"]=max(
                                    stats["max_vault_ws_subscriptions"],
                                    stats["vault_ws_subscriptions"])
                        elif msg.get("error"):
                            stats["errors"]+=1
                            emit("subscription_error",id=idx,reason="vault_account_subscribe",
                                 error=str(msg["error"])[:120])
                        continue
                    if msg.get("result") is not None and isinstance(idx,int) and 1<=idx<=len(items):
                        subscriptions[msg["result"]]=items[idx-1]
                        stats["subscriptions"]=len(subscriptions)
                        stats["max_subscriptions"]=max(stats["max_subscriptions"],len(subscriptions))
                    elif msg.get("error"):
                        stats["errors"]+=1
                        emit("subscription_error",id=idx,error=str(msg["error"])[:120])
                    continue
                if msg.get("method")=="slotNotification":
                    context=(msg.get("params") or {}).get("result") or {}
                    current_slot=context.get("slot")
                    if slot_acknowledged and isinstance(current_slot,int):
                        first_slot=not last_slot_at
                        last_slot_at=time.monotonic()
                        stats["slot_notifications"]+=1
                        if first_slot:
                            emit("ws_slot_confirmed",slot=current_slot,
                                 mainnet_slot_stream=True,
                                 trade_or_pool_event_not_implied=True)
                    continue
                if msg.get("method")=="accountNotification":
                    params=msg.get("params") or {}
                    match=vault_subscriptions.get(params.get("subscription"))
                    if match is None:
                        continue
                    item,side,mint=match
                    payload=params.get("result") or {}
                    slot=(payload.get("context") or {}).get("slot")
                    if not isinstance(slot,int):
                        continue
                    expected_owner=(item.get("creator_lp_owner") if side=="creator_lp"
                                    else item.get("creator_token_owner")
                                    if side=="creator_token" else item["pool"])
                    amount=ws_vault_balance(payload.get("value"),mint,expected_owner)
                    if amount is None:
                        stats["vault_notifications_rejected"]+=1
                        continue
                    if side in ("creator_lp","creator_token"):
                        base_key=side
                        if slot<=int(item.get(base_key+"_baseline_slot") or 0):
                            continue
                        previous=item.get(base_key+"_ws_initial_raw")
                        stats[base_key+"_ws_events"]+=1
                        item[base_key+"_baseline_slot"]=slot
                        item[base_key+"_ws_initial_raw"]=amount
                        if (lp_balance_decline(previous,amount)
                                and not item.get(base_key+"_early_alarm_sent")):
                            item[base_key+"_early_alarm_sent"]=True
                            event_kind=("onchain_lp_creator_balance_drop"
                                if side=="creator_lp"
                                else "onchain_pool_creator_token_balance_drop")
                            quote_trigger=("observed_creator_lp_balance_decrease"
                                if side=="creator_lp"
                                else "observed_pool_creator_token_balance_decrease")
                            stats[(base_key+"_early_decreases") if side=="creator_lp"
                                  else "creator_token_decreases"]+=1
                            fields={"pool":item["pool"],"mint":item["mint"],
                                "creator":expected_owner,"slot":slot,
                                "token_account":item.get(base_key+"_ws_account"),
                                "tracked_asset":"liquidity_lp_token" if side=="creator_lp"
                                     else "tradeable_pool_base_token",
                                "previous_raw":str(previous),"new_raw":str(amount),
                                "drop_threshold":"ge_10_percent_direct_owner_balance",
                                "transfer_or_sale_or_burn_not_proven":True,
                                "pool_creator_is_not_verified_token_developer":True,
                                "advance_warning_not_proven":True,
                                "real_trades":0}
                            emit(event_kind,**fields)
                            if len(urgent_ws_tasks)<3:
                                async def check_owner_exit(watched,reason):
                                    try:
                                        await urgent_quote_only(watched,reason=reason)
                                    except Exception as error:
                                        stats["errors"]+=1
                                        emit("ws_error",reason="creator_account_quote_failed",
                                             detail=type(error).__name__)
                                emergency=asyncio.create_task(
                                    check_owner_exit(item,quote_trigger))
                                urgent_ws_tasks.add(emergency)
                                emergency.add_done_callback(urgent_ws_tasks.discard)
                        continue
                    stats["vault_ws_events"]+=1
                    pool=item["pool"]
                    pending=vault_pair_updates.setdefault(pool,{})
                    paired=pair_recent_vault_updates(
                        pending,side,slot,amount,
                        int(item.get("rpc_vault_baseline_slot") or 0),max_skew=3)
                    if paired is None:
                        if (len(pending)==2 and
                                abs(pending["base"][0]-pending["quote"][0])>3):
                            stats["vault_pair_skew_rejections"]+=1
                        continue
                    # Both token accounts must have NEW observations since
                    # the preceding pair. Never reuse one stale quote vault
                    # with a new base notification to invent an apparent swap.
                    previous_pair_slots=item.get("last_paired_account_slots") or {}
                    if not both_vaults_refreshed(pending,previous_pair_slots):
                        continue
                    current=paired["current"]
                    previous=vault_last_pair.get(pool)
                    previous_pair_atomic=bool(item.get("last_paired_atomic"))
                    pair_slot=paired["pair_slot"]
                    last_slot=int(item.get("last_vault_ws_slot") or 0)
                    if pair_slot<=last_slot:
                        continue
                    item["last_vault_ws_slot"]=pair_slot
                    item["last_paired_account_slots"]={
                        name:pending[name][0] for name in ("base","quote")}
                    item["last_paired_atomic"]=paired["atomic_same_slot"]
                    vault_last_pair[pool]=current
                    if paired["same_slot_ws_observation"]:
                        stats["vault_pairs_same_slot"]+=1
                    else:
                        stats["vault_pairs_bounded_skew"]+=1
                    history=vault_history.setdefault(pool,[])
                    now_s=time.monotonic()
                    # Timestamped data for future pre-registered predictors:
                    # observe reserves BEFORE future collapse events, rather
                    # than fitting a story to the collapse after the fact.
                    # Keep sensor output bounded under API-heavy conditions.
                    baseline_observed=item.get("last_pair_telemetry_at",0.)
                    base_move=bool(previous and previous[0]>0 and
                                   abs(current[0]-previous[0])*100>=previous[0]*5)
                    quote_move=bool(previous and previous[1]>0 and
                                    abs(current[1]-previous[1])*100>=previous[1]*5)
                    if (stats["vault_pair_telemetry_rows"]<MAX_PAIR_TELEMETRY_ROWS and
                            (now_s-baseline_observed>=15 or
                             ((base_move or quote_move) and now_s-baseline_observed>=3))):
                        stats["vault_pair_telemetry_rows"]+=1
                        item["last_pair_telemetry_at"]=now_s
                        emit("onchain_vault_pair_observation",
                             pool=pool,mint=item["mint"],
                             slot=pair_slot,
                             slot_skew=paired["slot_skew"],
                             current_pair_atomic=paired["atomic_same_slot"],
                             previous_pair_atomic=previous_pair_atomic,
                             new_base_raw=str(current[0]),
                             new_quote_raw=str(current[1]),
                             previous_base_raw=str(previous[0]) if previous else None,
                             previous_quote_raw=str(previous[1]) if previous else None,
                             tracked_mint_side=item.get("watched_vault_index"),
                             genuine_vault_accounts_verified=True,
                             not_a_price_or_filled_exit=True,
                             prospectively_captured=True)
                    history.append((now_s,current))
                    history[:]=[(ts,balances) for ts,balances in history
                                if now_s-ts<=120]
                    if (len(history)>=3 and not item.get("sell_pressure_alarm_sent")
                            and early_sell_pressure(history[0][1],current,
                                                    item.get("watched_vault_index"))):
                        item["sell_pressure_alarm_sent"]=True
                        stats["pre_rug_sell_pressure_events"]+=1
                        first_ts,starting=history[0]
                        emit("onchain_preemptive_sell_pressure",
                             pool=pool,mint=item["mint"],slot=pair_slot,
                             vault_pair_slot_skew=paired["slot_skew"],
                             non_atomic_observation=not paired["atomic_same_slot"],
                             window_seconds=round(now_s-first_ts,3),
                             watched_vault_index=item["watched_vault_index"],
                             initial_watched_raw=str(starting[item["watched_vault_index"]]),
                             current_watched_raw=str(current[item["watched_vault_index"]]),
                             initial_counter_raw=str(starting[1-item["watched_vault_index"]]),
                             current_counter_raw=str(current[1-item["watched_vault_index"]]),
                             observed_lp_structural_tier=item.get("lp_control_first_observed"),
                             policy="observed12pct_watched_inflow_and_12pct_counter_outflow_in_120s",
                             hypothetical_early_only_no_guarantee=True,
                             not_an_executed_trade=True)
                        if len(urgent_ws_tasks)<3:
                            async def sell_pressure_quote(watched):
                                try:
                                    await urgent_quote_only(
                                        watched,reason="early_sell_pressure_vault_observation")
                                except Exception as error:
                                    stats["errors"]+=1
                                    emit("ws_error",reason="early_sell_pressure_quote_failed",
                                         detail=type(error).__name__)
                            task=asyncio.create_task(sell_pressure_quote(item))
                            urgent_ws_tasks.add(task)
                            task.add_done_callback(urgent_ws_tasks.discard)
                    if vault_joint_drop(previous,current):
                        # WS notifications are only leads, including SAME-SLOT
                        # notifications. Confirm raw balances by a fresh RPC
                        # getMultipleAccounts snapshot before claiming proof.
                        stats["vault_cross_slot_joint_drop_suspected"]+=1
                        emit("onchain_vault_ws_joint_drop_suspected",
                             pool=pool,mint=item["mint"],slot=pair_slot,
                             vault_pair_slot_skew=paired["slot_skew"],
                             same_slot_ws_observation=paired["same_slot_ws_observation"],
                             same_slot_confirmation=False,
                             current_pair_atomic=False,
                             previous_pair_atomic=previous_pair_atomic,
                             previous_base_raw=str(previous[0]),
                             previous_quote_raw=str(previous[1]),
                             new_base_raw=str(current[0]),new_quote_raw=str(current[1]),
                             verified_vault_owners=True,
                             not_proof_of_atomic_joint_withdrawal=True,
                             requires_rpc_corroboration=True,
                             read_only=True,no_fill_executed=True)
                        if len(urgent_ws_tasks)<3:
                            async def quote_vault_alarm(watched):
                                try:
                                    await urgent_quote_only(
                                        watched,reason="ws_pumpswap_joint_drop_suspected")
                                except Exception as error:
                                    stats["errors"]+=1
                                    emit("ws_error",reason="urgent_vault_quote_error",
                                         detail=type(error).__name__)
                            pending_quote=asyncio.create_task(quote_vault_alarm(item))
                            urgent_ws_tasks.add(pending_quote)
                            pending_quote.add_done_callback(urgent_ws_tasks.discard)
                        else:
                            emit("ws_error",reason="urgent_vault_quote_task_capacity_exceeded",
                                 pool=pool)
                        if (stats["vault_rpc_confirmation_checks"]<8
                                and len(urgent_ws_tasks)<3
                                and time.monotonic()-item.get("last_ws_rpc_check_at",-1e9)>=25):
                            item["last_ws_rpc_check_at"]=time.monotonic()
                            stats["vault_rpc_confirmation_checks"]+=1
                            async def corroborate_ws_alarm(watched,trigger_slot):
                                baseline=watched.get("rpc_vault_proof")
                                try:
                                    proof=await asyncio.to_thread(
                                        read_pumpswap_vaults,watched["pool"],watched["mint"])
                                    decision=rpc_joint_drop_status(
                                        baseline,proof,trigger_slot)
                                    if decision in ("confirmed","not_confirmed"):
                                        watched["rpc_vault_proof"]=proof
                                    if decision=="confirmed":
                                        stats["vault_rpc_joint_drops_confirmed"]+=1
                                        stats["vault_ws_joint_drops"]+=1
                                    elif decision=="not_confirmed":
                                        stats["vault_rpc_joint_drops_not_confirmed"]+=1
                                    else:
                                        stats["vault_rpc_joint_drops_unknown"]+=1
                                    emit("onchain_vault_rpc_confirmation",
                                         pool=watched["pool"],mint=watched["mint"],
                                         outcome=decision,ws_trigger_slot=trigger_slot,
                                         prior_rpc_slot=(baseline or {}).get("slot"),
                                         confirming_rpc_slot=proof.get("slot"),
                                         prior_base_raw=(baseline or {}).get("base_raw"),
                                         prior_quote_raw=(baseline or {}).get("quote_raw"),
                                         confirming_base_raw=proof.get("base_raw"),
                                         confirming_quote_raw=proof.get("quote_raw"),
                                         consistent_bank_snapshots=decision in
                                             ("confirmed","not_confirmed"),
                                         not_proof_of_single_withdrawal_transaction=True,
                                         not_proof_of_executable_exit=True,
                                         orders=0,trades=0)
                                except Exception as error:
                                    stats["errors"]+=1
                                    stats["vault_rpc_joint_drops_unknown"]+=1
                                    emit("onchain_vault_rpc_confirmation",
                                         pool=watched["pool"],mint=watched["mint"],
                                         outcome="unknown",error=type(error).__name__,
                                         orders=0,trades=0)
                            validation=asyncio.create_task(
                                corroborate_ws_alarm(item,pair_slot))
                            urgent_ws_tasks.add(validation)
                            validation.add_done_callback(urgent_ws_tasks.discard)
                    continue
                if msg.get("method")!="logsNotification":
                    continue
                params=msg.get("params") or {}
                item=subscriptions.get(params.get("subscription"))
                value=((params.get("result") or {}).get("value") or {})
                if item is None or value.get("err") is not None:
                    continue
                stats["events"]+=1
                logs=value.get("logs") or []
                lp_hint=any(
                    isinstance(line,str) and any(op in line.lower() for op in
                        ("instruction: removeliquidity","instruction: decreaseliquidity",
                         "instruction: withdrawliquidity","instruction: removeliquidityv2"))
                    for line in logs)
                if lp_hint:
                    # Program instruction text only; NOT proof of reserves withdrawn.
                    stats["lp_instruction_hints"]+=1
                    emit("lp_withdrawal_log_hint",pool=item["pool"],mint=item["mint"],
                         signature=value.get("signature"),verified_reserve_change=False)
                    item["early_lp_signature"]=value.get("signature")
                if not lp_hint and time.monotonic()-seen.get(item["pool"],0)<30:
                    continue
                seen[item["pool"]]=time.monotonic()
                if not queue.full():
                    queue.put_nowait(item)
        finally:
            if urgent_ws_tasks:
                _,pending=await asyncio.wait(urgent_ws_tasks,timeout=7)
                for unfinished in pending:
                    unfinished.cancel()
                if pending:
                    await asyncio.gather(*pending,return_exceptions=True)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def main():
        if "--pool-creator-token-live-proof" in sys.argv:
            pool="GseMAnNDvntR5uFePZ51yZBXzNSn7GdFPkfHwfr6d77J"
            mint="7LSsEoJGhLeZzGvDofTdNg7M3JttxQqGWNLo6vWMpump"
            proof=await asyncio.to_thread(read_pumpswap_vaults,pool,mint)
            if not proof.get("verified_pumpswap_layout"):
                raise RuntimeError("invalid_reference_pool")
            try:
                token=await asyncio.to_thread(read_pool_creator_token_holdings,
                                              proof,mint)
            except Exception as error:
                token={"available":False,"reason":type(error).__name__}
            print("PUMPSWAP_REAL_CREATOR_TRADEABLE_TOKEN_PROOF",
                  json.dumps({"at":stamp(),"pool":pool,"mint":mint,
                      "onchain_pool_verified":True,
                      "creator_is_not_verified_developer":True,
                      "zero_balance_does_not_imply_related_wallets_safe":True,
                      "orders":0,"trades":0,**token},allow_nan=False),flush=True)
            if not token.get("available"):
                raise SystemExit(1)
            return
        if "--creator-lp-ws-live-proof" in sys.argv:
            # Use the newest timestamped FIRST-OBSERVED risk snapshots from
            # durable research state; old test addresses may have since
            # burned/transferred LP. No private key or transaction.
            lp_proof_candidates=[]
            try:
                with LP_PROSPECTIVE.open("r",encoding="utf-8") as records:
                    for line in records:
                        try:
                            row=json.loads(line)
                            if (row.get("available") and
                                int(row.get("creator_direct_lp_raw") or 0)>0
                                and row.get("pool") and row.get("mint")):
                                lp_proof_candidates.append((row["sample_completed_at"],
                                                   row["pool"],row["mint"]))
                        except (ValueError,TypeError,KeyError):
                            continue
            except OSError:
                pass
            lp_proof_candidates.sort(reverse=True)
            fallback=[
                ("Gom42WgFrT7sXRYnhSGuZaR91Uxmt4BZMgc2JSdfMW8b",
                 "7AVC9kUy9RWAL9TAQAa9bd2Hc1AZLYBH3Y5ZmudaACC"),
                ("FprgYs7j6A8GmBAWJMUFVqfGe7pZ6p68CFdPp6vcgvTo",
                 "APeJi5ZEjBZXqGxQ1DKWuJjsHtsBtbV7oj1Ukfn6qPGa")]
            samples=[]
            seen=set()
            for pool,mint in ([(x[1],x[2]) for x in lp_proof_candidates]+fallback):
                if pool not in seen and len(samples)<16:
                    samples.append((pool,mint))
                    seen.add(pool)
            errors=[]
            zero_lp_fallbacks=[]
            zero_lp_count=0
            positive_lp_without_direct_owner=0
            for pool,mint in samples:
                try:
                    proof=await asyncio.to_thread(read_pumpswap_vaults,pool,mint)
                    if not proof.get("verified_pumpswap_layout"):
                        continue
                    lp=await asyncio.to_thread(read_lp_control,proof)
                    creators=lp.get("creator_lp_token_accounts") or []
                    if not creators:
                        if (lp.get("available") and
                                lp.get("current_lp_mint_supply_raw")=="0"):
                            zero_lp_count+=1
                            if len(zero_lp_fallbacks)<3:
                                zero_lp_fallbacks.append((pool,mint,proof))
                            errors.append("zero_outstanding_lp_no_direct_owner_"+pool[:8])
                        else:
                            positive_lp_without_direct_owner+=1
                            errors.append("owner_not_verified_"+pool[:8]+
                                "_"+str((lp.get("read_endpoint_health") or {}).get(
                                    "getTokenAccountsByOwner")))
                        continue
                    address=creators[0]["pubkey"]
                    ack=None
                    async with websockets.connect(WS_URL,open_timeout=10,
                        ping_interval=20,ping_timeout=20) as ws:
                        await ws.send(json.dumps({"jsonrpc":"2.0","id":77,
                            "method":"accountSubscribe","params":[address,
                            {"encoding":"jsonParsed","commitment":"confirmed"}]}))
                        for _ in range(5):
                            msg=json.loads(await asyncio.wait_for(ws.recv(),timeout=4))
                            if msg.get("id")==77:
                                ack=msg.get("result")
                                break
                    receipt={"at":stamp(),"pool":pool,"lp_mint":proof["lp_mint"],
                             "verified_token_account":address,
                             "observed_creator_lp_raw":creators[0]["amount_raw"],
                             "account_subscribe_ack":ack,
                             "network_confirmed":isinstance(ack,int),
                             "balance_change_event_observed":False,
                             "real_orders":0,"real_trades":0}
                    print("CREATOR_LP_ACCOUNT_WS_MAINNET_PROOF",
                          json.dumps(receipt,allow_nan=False),flush=True)
                    if isinstance(ack,int):
                        return
                    errors.append("no_subscription_ack_"+pool[:8])
                except Exception as error:
                    errors.append(type(error).__name__+"_"+pool[:8])
            # Zero outstanding LP has NO positive-balance creator LP account.
            # Still verify two genuine live pool vault subscriptions instead.
            # This is vault coverage, NOT proof of LP owner control coverage.
            for pool,mint,proof in zero_lp_fallbacks:
                for provider_index,endpoint in enumerate(WS_ENDPOINTS):
                    try:
                        acknowledgements=set()
                        async with websockets.connect(endpoint,open_timeout=10,
                                ping_interval=20,ping_timeout=20) as ws:
                            for index,side in enumerate(("base","quote"),1):
                                await ws.send(json.dumps({"jsonrpc":"2.0","id":index,
                                    "method":"accountSubscribe",
                                    "params":[proof[side+"_vault"],
                                        {"encoding":"jsonParsed",
                                         "commitment":"confirmed"}]}))
                            for _ in range(8):
                                message=json.loads(await asyncio.wait_for(
                                    ws.recv(),timeout=4))
                                request_id=message.get("id")
                                if (request_id in (1,2)
                                        and isinstance(message.get("result"),int)):
                                    acknowledgements.add(request_id)
                                if len(acknowledgements)==2:
                                    break
                        verified=len(acknowledgements)==2
                        print("LP_COVERAGE_MAINNET_PROOF",
                              json.dumps({"at":stamp(),"pool":pool,"mint":mint,
                                  "mode":"two_verified_pool_vault_streams_only",
                                  "ws_provider":"primary" if provider_index==0
                                      else "backup",
                                  "issued_lp_raw":"0",
                                  "creator_lp_owner_stream_verified":False,
                                  "creator_lp_stream_applicable":False,
                                  "vault_stream_subscriptions_confirmed":
                                      len(acknowledgements),
                                  "onchain_pool_layout_verified":True,
                                  "vault_stream_verified":verified,
                                  "does_not_establish_related_wallet_safety":True,
                                  "candidates_checked":len(samples),
                                  "zero_outstanding_lp_pools":zero_lp_count,
                                  "positive_or_unknown_lp_pools_without_direct_owner":
                                      positive_lp_without_direct_owner,
                                  "owner_monitoring_not_claimed":True,
                                  "real_orders":0,"real_trades":0},
                                  allow_nan=False),flush=True)
                        if verified:
                            return
                        errors.append("vault_subscription_ack_missing_"+pool[:8]+
                                      "_provider_"+str(provider_index))
                    except Exception as error:
                        errors.append("vault_fallback_"+type(error).__name__+"_"+
                                      pool[:8]+"_provider_"+str(provider_index))
            print("LP_COVERAGE_MAINNET_PROOF",
                  json.dumps({"at":stamp(),"vault_stream_verified":False,
                              "creator_lp_owner_stream_verified":False,
                              "candidates_checked":len(samples),
                              "zero_outstanding_lp_pools":zero_lp_count,
                              "positive_or_unknown_lp_pools_without_direct_owner":
                                  positive_lp_without_direct_owner,
                              "reasons":errors,"no_claim_of_success":True,
                              "real_orders":0,"real_trades":0},allow_nan=False),
                  flush=True)
            raise SystemExit(1)
        if "--pumpswap-lp-live-proof" in sys.argv:
            pool="GseMAnNDvntR5uFePZ51yZBXzNSn7GdFPkfHwfr6d77J"
            mint="7LSsEoJGhLeZzGvDofTdNg7M3JttxQqGWNLo6vWMpump"
            proof=await asyncio.to_thread(read_pumpswap_vaults,pool,mint)
            if not proof.get("verified_pumpswap_layout"):
                raise RuntimeError("pump_pool_layout_not_verified")
            try:
                ownership=await asyncio.to_thread(read_lp_control,proof)
            except Exception as error:
                ownership={"available":False,"reason":type(error).__name__}
            print("PUMPSWAP_REAL_LP_CONTROL_PROOF",
                  json.dumps({"at":stamp(),"pool":pool,"mint":mint,
                      "read_only":True,"orders":0,"trades":0,**ownership},
                      allow_nan=False),flush=True)
            if not ownership.get("available"):
                raise SystemExit(1)
            return
        if "--pumpswap-ws-live-proof" in sys.argv:
            pool="GseMAnNDvntR5uFePZ51yZBXzNSn7GdFPkfHwfr6d77J"
            mint="7LSsEoJGhLeZzGvDofTdNg7M3JttxQqGWNLo6vWMpump"
            proof=await asyncio.to_thread(read_pumpswap_vaults,pool,mint)
            if not proof.get("verified_pumpswap_layout"):
                raise RuntimeError("cannot_validate_pumpswap_for_ws_probe")
            ack=set()
            events=0
            async with websockets.connect(WS_URL,open_timeout=10,
                                         ping_interval=20,ping_timeout=20) as ws:
                for i,side in enumerate(("base","quote"),1):
                    await ws.send(json.dumps({"jsonrpc":"2.0","id":i,
                        "method":"accountSubscribe","params":[proof[side+"_vault"],
                            {"encoding":"jsonParsed","commitment":"confirmed"}]}))
                until=time.monotonic()+25
                while time.monotonic()<until:
                    try:
                        msg=json.loads(await asyncio.wait_for(ws.recv(),timeout=4))
                    except asyncio.TimeoutError:
                        if len(ack)==2:
                            break
                        continue
                    if msg.get("id") in (1,2) and isinstance(msg.get("result"),int):
                        ack.add(msg["id"])
                    if msg.get("method")=="accountNotification":
                        events+=1
                    if len(ack)==2 and events:
                        break
            receipt={"at":stamp(),"pool":pool,"subscription_acks":len(ack),
                     "account_change_events":events,
                     "both_verified_vault_accounts":bool(proof.get("verified_pumpswap_layout")),
                     "stream_verified":len(ack)==2,"real_trades":0}
            print("PUMPSWAP_VAULT_WS_LIVE_PROOF",
                  json.dumps(receipt,allow_nan=False),flush=True)
            if len(ack)!=2:
                raise SystemExit(1)
            return
        if "--pumpswap-live-proof" in sys.argv:
            pool="GseMAnNDvntR5uFePZ51yZBXzNSn7GdFPkfHwfr6d77J"
            mint="7LSsEoJGhLeZzGvDofTdNg7M3JttxQqGWNLo6vWMpump"
            try:
                evidence=await asyncio.to_thread(read_pumpswap_vaults,pool,mint)
            except Exception as error:
                evidence={"verified_pumpswap_layout":False,
                          "reason":type(error).__name__}
            print("PUMPSWAP_REAL_ONCHAIN_VAULT_PROOF",
                  json.dumps({"at":stamp(),"pool":pool,"mint":mint,
                              "read_only":True,"orders":0,"trades":0,
                              **evidence},allow_nan=False),flush=True)
            if not evidence.get("verified_pumpswap_layout"):
                raise SystemExit(1)
            return
        if "--liquidity-selftest" in sys.argv:
            a="1"*32
            mock={"pairs":[{"chainId":"solana","pairAddress":"2"*32,"liquidity":{"usd":9000}},
                           {"chainId":"solana","pairAddress":a,"liquidity":{"usd":12345.67}}]}
            assert parse_dex_liquidity(mock,a)==12345.67
            assert parse_dex_liquidity(mock,"3"*32) is None
            assert parse_dex_liquidity({"pairs":[]},a) is None
            assert parse_dex_liquidity({"pairs":[{"chainId":"ethereum",
                "pairAddress":a,"liquidity":{"usd":30000}}]},a) is None
            assert parse_dex_liquidity({"pairs":[{"chainId":"solana",
                "pairAddress":a,"liquidity":{"usd":0}}]},a)==0
            assert parse_onchain_mint_risk({"data":{"parsed":{"type":"mint","info":{
                "mintAuthority":None,"freezeAuthority":None}}}})["risk_notice"] is False
            assert parse_onchain_mint_risk({"data":{"parsed":{"type":"mint","info":{
                "mintAuthority":"owner","freezeAuthority":None}}}})["risk_notice"] is True
            assert parse_onchain_mint_risk(None)["available"] is False
            fake=bytearray(245)
            fake[:8]=PUMP_POOL_DISCRIMINATOR
            for off,val in ((43,11),(75,12),(139,13),(171,14)):
                fake[off:off+32]=bytes([val])*32
            assert encode_pubkey(bytes(32))=="1"*32
            base_mint=encode_pubkey(bytes([11])*32)
            quote_mint=encode_pubkey(bytes([12])*32)
            parsed_pool=parse_pumpswap_pool(
                {"owner":PUMP_PROGRAM,"data":[base64.b64encode(fake).decode(),"base64"]},
                base_mint)
            assert parsed_pool["base_mint"]==base_mint
            assert parsed_pool["quote_mint"]==quote_mint
            assert parsed_pool["lp_mint"]==encode_pubkey(bytes([0])*32)
            assert parsed_pool["pool_lp_supply_raw"]=="0"
            assert parse_pumpswap_pool({"owner":"wrong",
                "data":[base64.b64encode(fake).decode(),"base64"]},base_mint) is None
            fake[:8]=b"invalid!"
            assert parse_pumpswap_pool({"owner":PUMP_PROGRAM,
                "data":[base64.b64encode(fake).decode(),"base64"]},base_mint) is None
            fake[:8]=PUMP_POOL_DISCRIMINATOR
            def mock_vault(mint,amount):
                return {"data":{"parsed":{"type":"account","info":{
                    "owner":a,"mint":mint,"tokenAmount":{"amount":str(amount)}}}}}
            pair={"value":[mock_vault(base_mint,100),mock_vault(quote_mint,200)]}
            assert parse_pumpswap_vault_amounts(pair,a,parsed_pool)==[100,200]
            pair["value"][1]["data"]["parsed"]["info"]["owner"]="wrong"
            assert parse_pumpswap_vault_amounts(pair,a,parsed_pool) is None
            assert lp_control_risk_from_balances(1000,900,250,600)["structural_risk_tier"]=="elevated"
            assert lp_control_risk_from_balances(1000,200,0,100)["structural_risk_tier"]=="unflagged_not_safe"
            assert lp_control_risk_from_balances(1000,1200,0,100)["available"] is False
            partial=lp_control_risk_from_balances(1000,900,None,None)
            assert partial["available"] and partial["structural_risk_tier"]=="elevated"
            assert partial["creator_direct_lp_fraction"] is None
            assert lp_control_risk_from_balances(1000,None,None,None)["available"] is False
            assert creator_lp_token_accounts([
                {"pubkey":a,"account":{"data":{"parsed":{"info":{
                    "mint":base_mint,"owner":quote_mint,"tokenAmount":{"amount":"400"}}}}}},
                {"pubkey":"invalid","account":{"data":{"parsed":{"info":{
                    "mint":base_mint,"owner":quote_mint,"tokenAmount":{"amount":"500"}}}}}}
                ],base_mint,quote_mint)==[{"pubkey":a,"amount_raw":"400"}]
            assert lp_control_risk_from_balances(1000,900,250,600)[
                "direct_large_holder_risk_observed"] is True
            assert lp_control_risk_from_balances(1000,900,0,100)[
                "lp_control_evidence_category"]=="outstanding_supply_only_no_large_direct_holder"
            assert lp_control_risk_from_balances(1000,900,None,None)[
                "lp_control_evidence_category"]=="outstanding_supply_only_holder_unknown"
            assert lp_control_risk_from_balances(1000,900,None,None)[
                "direct_large_holder_risk_observed"] is None
            assert lp_control_risk_from_balances(1000,900,0,100)[
                "direct_large_holder_risk_observed"] is False
            assert lp_balance_decline(100,90) is True
            assert lp_balance_decline(100,91) is False
            assert lp_balance_decline(0,0) is False
            observed={}
            assert pair_recent_vault_updates(observed,"base",101,100,100) is None
            near=pair_recent_vault_updates(observed,"quote",103,200,100)
            assert near["current"]==(100,200) and near["slot_skew"]==2
            assert near["non_atomic_cross_slot_observation"] is True
            assert pair_recent_vault_updates(observed,"base",99,150,100) is None
            assert pair_recent_vault_updates(observed,"quote",104,201,100) is not None
            assert pair_recent_vault_updates(observed,"base",108,90,100) is None
            assert pair_recent_vault_updates(observed,"quote",108,150,100)[
                "same_slot_ws_observation"] is True
            assert pair_recent_vault_updates(observed,"quote",109,150,100)[
                "atomic_same_slot"] is False
            assert pair_recent_vault_updates({},"base",99,100,100) is None
            assert both_vaults_refreshed(
                {"base":(108,100),"quote":(108,200)},
                {"base":105,"quote":106}) is True
            assert both_vaults_refreshed(
                {"base":(108,100),"quote":(106,200)},
                {"base":105,"quote":106}) is False
            assert both_vaults_refreshed(
                {"base":(108,100)},{"base":105,"quote":106}) is False
            rpc_base={"verified_pumpswap_layout":True,
                      "base_mint":base_mint,"quote_mint":quote_mint,
                      "base_vault":a,"quote_vault":"2"*32,
                      "slot":100,"base_raw":"100","quote_raw":"200"}
            rpc_after={**rpc_base,"slot":108,"base_raw":"75","quote_raw":"140"}
            assert rpc_joint_drop_status(rpc_base,rpc_after,105)=="confirmed"
            assert rpc_joint_drop_status(rpc_base,
                {**rpc_after,"quote_raw":"205"},105)=="not_confirmed"
            assert rpc_joint_drop_status(rpc_base,
                {**rpc_after,"slot":104},105)=="stale"
            assert rpc_joint_drop_status(rpc_base,
                {**rpc_after,"base_vault":"3"*32},105)=="unknown"
            assert rpc_joint_drop_status(rpc_base,
                {"verified_pumpswap_layout":False},105)=="unknown"
            assert early_sell_pressure((100,200),(114,175),0) is True
            assert early_sell_pressure((100,200),(85,228),1) is True
            assert early_sell_pressure((100,200),(114,210),0) is False
            assert early_sell_pressure((100,200),(114,175),None) is False
            assert vault_joint_drop((100,200),(79,159)) is True
            assert vault_joint_drop((100,200),(80,160)) is True
            assert vault_joint_drop((100,200),(70,210)) is False
            assert ws_vault_balance({"data":{"parsed":{"type":"account",
                "info":{"mint":base_mint,"owner":a,
                        "tokenAmount":{"amount":"42"}}}}},base_mint,a)==42
            assert ws_vault_balance({"data":{"parsed":{"type":"account",
                "info":{"mint":base_mint,"owner":"wrong",
                        "tokenAmount":{"amount":"42"}}}}},base_mint,a) is None
            assert ws_vault_balance({"data":["AA==","base64"]},base_mint,a) is None
            assert ws_vault_balance({"data":{"parsed":[]}},base_mint,a) is None
            assert ws_vault_balance({"data":{"parsed":{"type":"account",
                "info":{"owner":a,"mint":base_mint,"tokenAmount":[]}}}},base_mint,a) is None
            raw_token=bytearray(165)
            raw_token[:32]=bytes([1])*32
            raw_token[32:64]=bytes([2])*32
            raw_token[64:72]=(42).to_bytes(8,"little")
            raw_token[108]=1
            mint_binary=encode_pubkey(bytes([1])*32)
            owner_binary=encode_pubkey(bytes([2])*32)
            raw_message={"owner":"TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                         "data":[base64.b64encode(raw_token).decode(),"base64"]}
            assert ws_vault_balance(raw_message,mint_binary,owner_binary)==42
            assert ws_vault_balance(raw_message,mint_binary,a) is None
            assert ws_vault_balance({**raw_message,"owner":"invalid"},
                                    mint_binary,owner_binary) is None
            assert ws_vault_balance({**raw_message,"data":["!","base64"]},
                                    mint_binary,owner_binary) is None
            assert feed_candidate({"pool":a,"mint":"2"*32,"qty":100,
                                   "liquidity_usd":15000})["initial"]==15000
            assert feed_candidate({"pool":"invalid","mint":"2"*32,"qty":100,
                                   "liquidity_usd":15000}) is None
            assert feed_candidate({"pool":a,"mint":"2"*32,"qty":0,
                                   "liquidity_usd":15000}) is None
            print("LIQUIDITY_SENSOR_SELFTEST=PASS (50 source/pool/LP-owner/freshness/RPC-corrob checks)",flush=True)
            return
        EVENTS.parent.mkdir(parents=True,exist_ok=True)
        items=candidates()
        if not items:
            emit("ws_started_without_previous_pools",awaiting_fresh_candidates=True)
        emit("ws_started",pools=[x["pool"] for x in items],read_only=True,orders=0,trades=0)
        deadline=time.monotonic()+DURATION
        pause=3
        failed_connections=0
        while time.monotonic()<deadline:
            provider_index=failed_connections%len(WS_ENDPOINTS)
            try:
                async with websockets.connect(WS_ENDPOINTS[provider_index],
                        open_timeout=12,ping_interval=20,ping_timeout=20,
                        max_size=2**20) as ws:
                    emit("ws_connected",monitored_pools=len(items),
                         provider="primary" if provider_index==0 else "backup")
                    await observe(ws,items,deadline)
                    pause=3
            except (OSError,ValueError,websockets.exceptions.WebSocketException,
                    asyncio.TimeoutError) as error:
                failed_connections+=1
                stats["errors"]+=1
                stats["subscriptions"]=0
                emit("ws_error",reason=type(error).__name__,
                     provider="primary" if provider_index==0 else "backup",
                     detail=type(error).__name__)
                health(False)
                await asyncio.sleep(min(pause,max(0.,deadline-time.monotonic())))
                pause=min(pause*2,20)
        stats["subscriptions"]=0
        health(False)

    asyncio.run(main())

if ('--liquidity-sensor' in sys.argv or '--liquidity-selftest' in sys.argv
        or '--pumpswap-live-proof' in sys.argv or '--pumpswap-ws-live-proof' in sys.argv
        or '--pumpswap-lp-live-proof' in sys.argv
        or '--creator-lp-ws-live-proof' in sys.argv
        or '--pool-creator-token-live-proof' in sys.argv):
    run_embedded_liquidity_sensor()
    raise SystemExit(0)

import datetime as d,json,time,urllib.request,urllib.error,urllib.parse,os,math,statistics,hashlib
url='https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page='
st={}
USDC='EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
quote_checks=0
MAX_ENTRY_QUOTE_CHECKS=10
MAX_SELL_DEPTH_PROBES_PER_RUN=2
sell_depth_probes_this_run=0
# A bounded 25/100 USDC exact-size buy+sell route book on NEW high-confidence
# research shadows. Both-sided quotation, not a filled initial trade.
# Reserve one buy-and-sell quote study per frozen prediction group.
# No hindsight favoring our preferred rule; <= 8 additional keyless quotes.
MAX_SIZE_MATCHED_ENTRY_STUDIES_PER_RUN=2
size_matched_entry_studies_this_run=0
size_matched_entry_by_group_this_run={'high_activity_early_v1':0,'other_eligible_v1':0}
MAX_SIZE_MATCHED_EXIT_STUDIES_PER_RUN=2
size_matched_exit_studies_this_run=0
sell_mark_checks=0
MAX_SELL_MARKS_PER_RUN=10
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
MAX_MOONSHOT_SPLIT_QUOTES_PER_RUN=3
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
COHORT_ENTRY_QUOTE_PER_ARM_PER_RUN=2
COHORT_SELL_QUOTE_PER_ARM_PER_RUN=2
cohort_entry_quote_checks={x:0 for x in COHORT_GROUPS}
cohort_followup_quote_checks={x:0 for x in COHORT_GROUPS}
shadow_new_this_run=0
shadow_chart_11x_this_run=0
shadow_quote_entries_this_run=0
nascent_entry_quotes_this_run=0
NASCENT_ENTRY_RESERVED=2
shadow_quote_marks_this_run=0
shadow_quote_marks_available_this_run=0
frozen_policy_decisions_this_run=0
# Single bounded real-time micro-window to test whether a quoted market
# can actually be observed on the ~30s historical crash time scale.
RAPID_QUOTE_WINDOWS_PER_RUN=1
RAPID_QUOTE_SAMPLES=3
RAPID_QUOTE_SECONDS_BETWEEN=2  # keyless Jupiter also rate-limits itself at 2.3s
rapid_quote_windows_this_run=0
rapid_quote_available_marks_this_run=0
rapid_quote_warning_events_this_run=0
rapid_quote_first_sample_90pct_cliffs_this_run=0
MAX_SHADOW_SELL_QUOTES_PER_RUN=12
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

# Research-only first-observation strata. NEVER use future price.
def emergence_research_arm(age,liquidity,buyers,buys,sells,pre_chart_x=1):
    try:
        age=float(age);liquidity=float(liquidity);buyers=int(buyers)
        buys=int(buys);sells=int(sells);pre_chart_x=float(pre_chart_x)
    except (ValueError,TypeError):
        return 'unclassified_missing_baseline'
    if not (0<=age<=6 and liquidity>=10000 and 1<=pre_chart_x<2
            and sells>=1 and buys>=1.5*sells):
        return 'other_first_observed'
    if 20<=buyers<200:
        return 'nascent_20_199'
    if 200<=buyers<450:
        return 'growth_200_449'
    if buyers>=450:
        return 'crowded_450_plus'
    return 'other_first_observed'

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
    slots=max(0,budget-len(selected))
    chosen=[]
    # Balanced observation only: one young low-crowd and one crowded
    # reference, where actually available, before fair rotation fills slots.
    for arm in ('nascent_20_199','crowded_450_plus'):
        if len(chosen)>=slots:break
        for pool,sh in waiting:
            frozen=sh.get('emergence_research_arm')
            if not frozen:
                # Self-contained legacy fallback: the isolated deterministic
                # regression harness loads only this fair-rotation function.
                # Never query future outcomes for the cohort assignment.
                try:
                    age=float(sh.get('initial_age_minutes'))
                    liq=float(sh.get('entry_liquidity_usd'))
                    buyers=int(sh.get('initial_buyers_1h'))
                    buys=int(sh.get('initial_buys_1h'))
                    sells=int(sh.get('initial_sells_1h'))
                    prec=float(sh.get('pre_signal_chart_x_v1') or 1)
                    if (0<=age<=6 and liq>=10000 and 1<=prec<2
                            and sells>=1 and buys>=1.5*sells):
                        frozen=('nascent_20_199' if 20<=buyers<200 else
                                'growth_200_449' if 200<=buyers<450 else
                                'crowded_450_plus' if buyers>=450 else
                                'other_first_observed')
                    else:
                        frozen='other_first_observed'
                except (ValueError,TypeError):
                    frozen='unclassified_missing_baseline'
            if frozen==arm and pool not in chosen:
                chosen.append(pool)
                break
    for pool,_ in waiting:
        if len(chosen)>=slots:break
        if pool not in chosen:chosen.append(pool)
    return chosen
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

def rapid_quote_crash_watch(sh,mint,pool,token_id):
    """~30s live quote sequence after frozen initial $5 two-sided quote.

    Keyless Jupiter router. Bounded to 3 calls and 8s waits. These are
    sequential *unfilled* quotes and cannot establish continuous exitability.
    """
    global rapid_quote_available_marks_this_run,rapid_quote_warning_events_this_run
    global rapid_quote_first_sample_90pct_cliffs_this_run
    if not sh.get('entry_quote'):return None
    qty=int(sh['entry_quote'].get('token_raw') or 0)
    if qty<=0:return None
    first=num(sh['entry_quote'].get('immediate_roundtrip_multiple'))
    baseline_at=sh['entry_quote'].get('observed_at')
    stamps=[{'at':baseline_at,'quoted_x':round(first,8) if first>0 else None,
             'quote_ok':first>0,'relative_time':'signal','unfilled':True}]
    peak=first if first>0 else 0
    warnings=set()
    last_valid=(baseline_at,first) if first>0 and baseline_at else None
    first_sample_cliff=False
    for i in range(RAPID_QUOTE_SAMPLES):
        time.sleep(RAPID_QUOTE_SECONDS_BETWEEN)
        q=jupiter_quote(mint,USDC,qty)
        now=d.datetime.now(d.timezone.utc).isoformat()
        multiple=(q['out_amount']/5000000 if q.get('ok') else None)
        row={'at':now,'quoted_x':round(multiple,8) if multiple is not None else None,
             'quote_ok':bool(q.get('ok')),'relative_time':'followup_'+str(i+1),
             'unfilled':True}
        if multiple is not None and multiple>0:
            rapid_quote_available_marks_this_run+=1
            # A jump directly from a normal quote to near-zero is NOT an
            # actionable 20%/40%/60% warning: all thresholds were skipped.
            # Record the FIRST actual next-quote interval; do not infer the
            # exact intrainterval event timestamp or market fill price.
            if i==0 and last_valid is not None and multiple<=last_valid[1]*0.1:
                try:
                    elapsed=(d.datetime.fromisoformat(now)
                             -d.datetime.fromisoformat(last_valid[0])).total_seconds()
                except (TypeError,ValueError):
                    elapsed=None
                first_sample_cliff=True
                rapid_quote_first_sample_90pct_cliffs_this_run+=1
                row['first_observation_cliff_90pct_with_no_prior_warning']=True
                row['first_observation_elapsed_seconds']=round(elapsed,3) if elapsed is not None else None
                out({'kind':'rapid_quote_no_advance_warning_gap','pool':pool,
                     'token_id':token_id,'previous_at':last_valid[0],
                     'at':now,'prior_quote_x':round(last_valid[1],8),
                     'now_quote_x':round(multiple,8),
                     'observed_interval_seconds':row['first_observation_elapsed_seconds'],
                     'not_exact_crash_time':True,'not_an_executable_exit':True,
                     'orders':0,'trades':0})
            peak=max(peak,multiple)
            drawdown=(1-multiple/peak) if peak>0 else 0
            row['peak_so_far_quote_x']=round(peak,8)
            row['drawdown_from_observed_peak_pct']=round(100*drawdown,3)
            for threshold in (20,40,60):
                if drawdown*100>=threshold and threshold not in warnings:
                    warnings.add(threshold)
                    rapid_quote_warning_events_this_run+=1
                    out({'kind':'rapid_quote_crash_warning','pool':pool,
                         'token_id':token_id,'at':now,'threshold_pct':threshold,
                         'quoted_x':round(multiple,8),
                         'first_sample_no_advance_warning':bool(first_sample_cliff and i==0),
                         'not_an_executed_exit':True,'no_trade':True})
            last_valid=(now,multiple)
        stamps.append(row)
        out({'kind':'rapid_sell_quote_observation_only','pool':pool,
             'token_id':token_id,**row,'trades':0,'orders':0})
    return {'version':1,'at':d.datetime.now(d.timezone.utc).isoformat(),
        'sample_count':RAPID_QUOTE_SAMPLES,'quotes':stamps,
        'observed_warning_thresholds_pct':sorted(warnings),
        'first_sample_no_advance_warning_90pct_cliff':first_sample_cliff,
        'not_continuous_or_executable':True,'capital_allocated':0,
        'trades':0,'orders':0}

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

def read_verified_pumpfun_launches():
    """Only chain-verified create mints, not unverified WebSocket signatures."""
    file='evidence/pumpfun_verified_mints.jsonl'
    launches={}
    try:
        with open(file,encoding='utf-8') as stream:
            for line in stream:
                try:row=json.loads(line)
                except ValueError:continue
                if (row.get('kind')!='pumpfun_early_verified'
                        or not row.get('mint')
                        or row.get('detected_within_first_30s') is not True
                        or not isinstance(row.get('block_time_unix'),int)):
                    continue
                mint=row['mint']
                old=launches.get(mint)
                if old is None or row['at']<old['at']:
                    launches[mint]=row
    except OSError:
        pass
    return launches

def live_jsonl_evidence(path):
    """Read real sensor records, including legacy literal-backslash-n separators.
    No generated observations and no file rewrites; reject malformed spans.
    """
    decoder=json.JSONDecoder()
    try:
        with open(path,encoding='utf-8') as stream:
            for line in stream:
                try:
                    row=json.loads(line)
                    if isinstance(row,dict):yield row
                    continue
                except ValueError:
                    pass
                if r'\n' not in line:
                    continue
                pos=0
                while pos<len(line):
                    while pos<len(line) and line[pos].isspace():
                        pos+=1
                    if pos>=len(line):break
                    try:row,end=decoder.raw_decode(line,pos)
                    except ValueError:break
                    if not isinstance(row,dict):break
                    if end<len(line) and not (
                            line.startswith(r'\n',end) or
                            line[end:].strip()=='' or
                            line[end].isspace()):
                        break
                    yield row
                    pos=end+2 if line.startswith(r'\n',end) else end
    except OSError:
        return

def read_native_first30_features():
    """Frozen real first-30s records, never recomputed from future prices."""
    data={}
    for row in live_jsonl_evidence('evidence/pumpfun_30s_features.jsonl'):
        if not (row.get('mint') and
                row.get('full_first_30s_observation') is True
                and row.get('no_execution') is True and
                isinstance(row.get('completion_at'),str)):
            continue
        key=row['mint']
        if key not in data or row['completion_at']<data[key]['completion_at']:
            data[key]=row
    return data

for cycle in range(CYCLES_PER_RUN):
    t=d.datetime.now(d.timezone.utc)
    pumpfun_native_verified=read_verified_pumpfun_launches()
    pumpfun_early_flow=read_native_first30_features()
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
        available_discovery_pages=0
        for page in (1,2,3):
            try:
                page_data=read(url+str(page))
                if page_data:
                    data.extend(page_data)
                    available_discovery_pages+=1
            except Exception as ex:
                out({'kind':'discovery_page_error','at':t.isoformat(),'page':page,'error':str(ex)[:120]})
            time.sleep(2)
        if not data:
            raise RuntimeError('all_new_pool_pages_unavailable')
        if available_discovery_pages<3:
            out({'kind':'discovery_partial_coverage','at':t.isoformat(),
                 'pages_ok':available_discovery_pages,'pages_requested':3,
                 'missing_discovery_censored':True})
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
            if (token_id and token_id.startswith('solana_')
                    and 'pumpfun_first30_features' not in s):
                feat=pumpfun_early_flow.get(token_id.removeprefix('solana_'))
                if feat and feat.get('completion_at','')<=t.isoformat():
                    s['pumpfun_first30_features']={k:feat.get(k) for k in (
                        'created_at_unix','completion_at',
                        'buy_events_first30','sell_events_first30',
                        'distinct_buyers_first30','distinct_sellers_first30',
                        'creator_buy_events_first30',
                        'creator_sell_events_first30',
                        'creator_sell_observed_before30s',
                        'onchain_virtual_curve_last_price_multiple',
                        'onchain_virtual_curve_peak_price_multiple',
                        'onchain_curve_peak_to_last_retracement_fraction',
                        'buy_sol_lamports_reported_first30',
                        'sell_sol_lamports_reported_first30',
                        'initial_virtual_sol_lamports',
                        'initial_virtual_token_raw',
                        'last_virtual_sol_lamports',
                        'last_virtual_token_raw','is_mayhem_mode')}
                    # The first pool observation is OUR observation, not a
                    # proof of the first listing on an external index.
                    pre_first_pool_observation=(
                        feat['completion_at']<=s['first'].isoformat())
                    s['pumpfun_first30_features']['frozen_pre_gecko']=(
                        pre_first_pool_observation)
                    s['pumpfun_first30_features']['before_first_scanner_pool_observation']=(
                        pre_first_pool_observation)
                    s['pumpfun_first30_features']['listing_publication_time_unknown']=True
                    out({'kind':'native_30s_signal_joined_later_gecko',
                         'pool':p,'mint':feat['mint'],'at':t.isoformat(),
                         'features_measured_before_gecko':pre_first_pool_observation,
                         'not_proof_of_pre_listing':True,
                         'buy_events_first30':feat.get('buy_events_first30'),
                         'distinct_buyers_first30':feat.get('distinct_buyers_first30'),
                         'creator_buy_events_first30':feat.get('creator_buy_events_first30'),
                         'no_order':True,'no_trade':True})
            if (token_id and token_id.startswith('solana_')
                    and 'pumpfun_first_seconds' not in s):
                launch=pumpfun_native_verified.get(token_id.removeprefix('solana_'))
                if launch:
                    lag=max(0,t.timestamp()-launch['block_time_unix'])
                    s['pumpfun_first_seconds']={
                        'create_signature':launch['signature'],
                        'slot':launch.get('slot'),
                        'block_time_unix':launch['block_time_unix'],
                        'first_onchain_received_at':launch['at'],
                        'onchain_create_observation_lag_seconds':
                            launch.get('detection_lag_seconds'),
                        'first_gecko_listing_lag_seconds':round(lag,3),
                        'mint_verified_before_gecko_listing':True,
                        'not_necessarily_executable_at_launch':True}
                    out({'kind':'native_launch_joined_to_first_gecko_listing',
                         'at':t.isoformat(),'pool':p,'mint':launch['mint'],
                         'onchain_to_index_listing_seconds':round(lag,3),
                         'no_trade':True})
            # Persist the genuine FIRST discovery observation, never
            # reconstruct a discovery price from future market observations.
            if s['first']==t and 'first_discovery_price_v1' not in s:
                s['first_discovery_price_v1']=v
                s['first_discovery_observed_at_v1']=t.isoformat()
                s['first_discovery_age_minutes_v1']=round(age,4)
                # First-observed universe, including NON-SELECTED pools.
                # Future observation gaps remain censored, never negatives.
                s['first_discovery_research_v1']={
                    'at':t.isoformat(),'age_minutes':round(age,4),
                    'liquidity_usd':round(x['liq'],4),
                    'volume_1h_usd':round(x['volume_1h'],4),
                    'buyers_1h':x['buyers_1h'],
                    'buys_1h':x['buys_1h'],'sells_1h':x['sells_1h'],
                    'arm':emergence_research_arm(age,x['liq'],x['buyers_1h'],
                                x['buys_1h'],x['sells_1h'],1),
                    'selected_for_shadow':False,
                    'future_outcome_not_yet_observed':True}

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
                # Compare liquidity against the actual signal-time snapshot.
                # Fresh market-data samples are minutes apart, so this is
                # an upper bound on a drain observation, NOT early notice.
                initial_liq=num(shadow.get('entry_liquidity_usd'))
                if initial_liq>0 and x['liq']>=0:
                    for fraction,label in ((.1,'90'),(.01,'99')):
                        key='first_liquidity_drop_'+label+'pct_v1'
                        if x['liq']<=fraction*initial_liq and not shadow.get(key):
                            mark={'at':t.isoformat(),'initial_liquidity_usd':round(initial_liq,2),
                                  'observed_liquidity_usd':round(x['liq'],8),
                                  'elapsed_since_signal_seconds':round(
                                    (t-d.datetime.fromisoformat(shadow['at'])).total_seconds(),2),
                                  'not_exact_drain_time_or_trade_exit':True}
                            shadow[key]=mark
                            out({'kind':'shadow_liquidity_drain_observed','pool':p,
                                 'token_id':token_id,'threshold_drop_pct':int(label),
                                 **mark,'orders':0,'trades':0})
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
                        research_arm=emergence_research_arm(
                            age,x['liq'],x['buyers_1h'],x['buys_1h'],
                            x['sells_1h'],
                            s.get('first_eligible_vs_first_discovery_chart_x_v1') or 1)
                        if s.get('first_discovery_research_v1'):
                            s['first_discovery_research_v1']['selected_for_shadow']=True
                        shadow={'at':d.datetime.now(d.timezone.utc).isoformat(),
                                'emergence_research_arm':research_arm,
                                'arm_frozen_before_later_quote':True,
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
                        remaining_nascent_reserve=max(0,
                            NASCENT_ENTRY_RESERVED-nascent_entry_quotes_this_run)
                        may_quote=(quote_checks<MAX_ENTRY_QUOTE_CHECKS and (
                            research_arm=='nascent_20_199'
                            or quote_checks<MAX_ENTRY_QUOTE_CHECKS-remaining_nascent_reserve
                            or cycle==CYCLES_PER_RUN-1))
                        if may_quote:
                            if research_arm=='nascent_20_199':
                                nascent_entry_quotes_this_run+=1
                            proposal=entry_quote_preflight(token_id.removeprefix('solana_'))
                            shadow['initial_route_check']={'at':d.datetime.now(d.timezone.utc).isoformat(),
                                                           'reason':proposal.get('reason'),'ok':bool(proposal.get('ok'))}
                            if proposal.get('ok'):
                                shadow['entry_quote']=proposal
                                # Same-run stream for the embedded WebSocket sensor.
                                # Quote-only research: no signature and no purchase.
                                raw_qty=int(proposal.get('token_raw') or 0)
                                if raw_qty>0:
                                    with open('evidence/liquidity_live_candidates.jsonl','a',encoding='utf-8') as feed:
                                        feed.write(json.dumps({'pool':p,
                                            'mint':token_id.removeprefix('solana_'),
                                            'qty':raw_qty,'liquidity_usd':x['liq'],
                                            'signal_at':shadow['at'],
                                            'no_capital_allocated':True},separators=(',',':'))+'\n')
                                shadow_quote_entries_this_run+=1
                                # Prioritize the critical first rapid sell
                                # quotation BEFORE expensive $25/$100 size probes.
                                # Both are still strictly prospective, no trade.
                                # Existing deterministic 3-cycle fixtures do not
                                # activate the 12-cycle-only production burst.
                                if (CYCLES_PER_RUN>=9 and cycle<CYCLES_PER_RUN-2
                                    and rapid_quote_windows_this_run<RAPID_QUOTE_WINDOWS_PER_RUN
                                    and age<=5 and x['liq']>=10000
                                    and x['volume_1h']>=20000 and x['buyers_1h']>=100):
                                    rapid_quote_windows_this_run+=1
                                    burst=rapid_quote_crash_watch(shadow,
                                        token_id.removeprefix('solana_'),p,token_id)
                                    if burst:shadow['rapid_quote_window_v1']=burst
                                # Preserve the existing size-aware research,
                                # conducted AFTER the latency-sensitive phase.
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
                    # Route-qualified signal: attach WS liquidity watch now,
                    # not after a 10-cycle checkpoint or future workflow.
                    raw_qty=int(preflight.get('token_raw') or 0)
                    if raw_qty>0:
                        with open('evidence/liquidity_live_candidates.jsonl','a',encoding='utf-8') as feed:
                            feed.write(json.dumps({'pool':p,'mint':mint,'qty':raw_qty,
                                'liquidity_usd':x['liq'],'signal_at':entry_at.isoformat(),
                                'no_capital_allocated':True},separators=(',',':'))+'\n')
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
   'onchain_native_first_seconds_mints_visible_this_run':
       len(pumpfun_native_verified),
   'complete_first30_flow_vectors_available_this_run':
       len(pumpfun_early_flow),
   'complete_pre_gecko_first30_flow_vectors_visible_this_run':sum(
       bool(z.get('pumpfun_first30_features',{}).get('before_first_scanner_pool_observation'))
       for z in st.values()),
   'pre_gecko_here_means_before_our_first_pool_observation_not_public_listing':True,
   'first30_flow_vectors_matched_later_gecko':sum(
       bool(z.get('pumpfun_first30_features')) for z in st.values()),
   'onchain_create_mints_later_matched_gecko':sum(
       bool(z.get('pumpfun_first_seconds')) for z in st.values()),
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
   'rapid_quote_windows_this_run':rapid_quote_windows_this_run,
   'rapid_quote_available_marks_this_run':rapid_quote_available_marks_this_run,
   'rapid_quote_warning_events_this_run':rapid_quote_warning_events_this_run,
   'rapid_quote_first_sample_90pct_cliffs_this_run':rapid_quote_first_sample_90pct_cliffs_this_run,
   'frozen_quote_exit_policy_decisions_this_run':frozen_policy_decisions_this_run,
   'nascent_entry_quote_attempts_this_run':nascent_entry_quotes_this_run,
   'nascent_entry_quote_reserved_capacity':NASCENT_ENTRY_RESERVED,
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
        'emergence_research_arm':sh.get('emergence_research_arm') or
            emergence_research_arm(sh.get('initial_age_minutes'),
                sh.get('entry_liquidity_usd'),sh.get('initial_buyers_1h'),
                sh.get('initial_buys_1h'),sh.get('initial_sells_1h'),
                sh.get('pre_signal_chart_x_v1') or 1),
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
        'rapid_quote_window_v1':sh.get('rapid_quote_window_v1'),
        'first_liquidity_drop_90pct_v1':sh.get('first_liquidity_drop_90pct_v1'),
        'first_liquidity_drop_99pct_v1':sh.get('first_liquidity_drop_99pct_v1'),
        'quote_trace':sh.get('quote_trace',[])[-48:],
        'quote_trace_truncated':bool(sh.get('quote_trace_truncated')),
        'max_later_sell_quote_multiple_unfilled':sh.get('max_exit_quote_multiple'),
        'ever_observed_later_quote_11x':bool(sh.get('quote_11x_logged')),
        'missing_route_does_not_prove_exit':True,
        'return_unrealized':True,'orders':0,'trades':0})
    archived.add(pool)
shadow_ledger=shadow_ledger[-5000:]
# Compare actual future exact-quantity SELL QUOTES against strata frozen
# BEFORE those quotes were seen. Keep active vs archived windows separate:
# active survivors are severely selected; never pool into a success rate.
arm_groups=('nascent_20_199','growth_200_449','crowded_450_plus',
            'other_first_observed','unclassified_missing_baseline')
arm_histories={}
for observation_class in ('archived','active'):
    if observation_class=='archived':
        rows=[(z.get('pool'),z) for z in shadow_ledger]
    else:
        rows=[(pool,z['shadow']) for pool,z in st.items()
              if z.get('shadow') and not z['shadow'].get('expired')
              and not z.get('entry')]
    by_arm={arm:{'enrolled':0,'later_quote_observed':0,
                 'future_quote_2x':0,'future_quote_5x':0,'future_quote_11x':0,
                 'quote_missing_or_unchecked_censored':0}
            for arm in arm_groups}
    unique=set()
    for pool,sh in rows:
        if not pool or pool in unique:continue
        unique.add(pool)
        arm=sh.get('emergence_research_arm') or emergence_research_arm(
            sh.get('initial_age_minutes'),sh.get('entry_liquidity_usd'),
            sh.get('initial_buyers_1h'),sh.get('initial_buys_1h'),
            sh.get('initial_sells_1h'),sh.get('pre_signal_chart_x_v1') or 1)
        bucket=by_arm.get(arm,by_arm['unclassified_missing_baseline'])
        bucket['enrolled']+=1
        highest=(sh.get('max_later_sell_quote_multiple_unfilled')
                 if observation_class=='archived'
                 else sh.get('max_exit_quote_multiple'))
        if not isinstance(highest,(int,float)) or not math.isfinite(highest):
            bucket['quote_missing_or_unchecked_censored']+=1
            continue
        bucket['later_quote_observed']+=1
        for tier in (2,5,11):
            if highest>=tier:
                bucket['future_quote_'+str(tier)+'x']+=1
    arm_histories[observation_class]=by_arm
first_discovery_unselected={arm:0 for arm in arm_groups}
first_discovery_selected={arm:0 for arm in arm_groups}
for item in st.values():
    snapshot=item.get('first_discovery_research_v1')
    if not snapshot:continue
    arm=snapshot.get('arm')
    if arm not in first_discovery_unselected:continue
    target=(first_discovery_selected if snapshot.get('selected_for_shadow')
            else first_discovery_unselected)
    target[arm]+=1
emergence_report={
    'as_of':r['at'],
    'objective':'Find uncrowded early opportunities via matched quoted followups',
    'research_arm_fixed_before_future_quotes':True,
    'strata_not_trading_rules_or_learned_probabilities':True,
    'archived_and_active_never_pooled_for_rate_claims':True,
    'live_active_survivor_selection_bias':True,
    'missing_quotes_censored_not_failures':True,
    'first_discovery_all_new_pool_denominators':{
        'selected':first_discovery_selected,
        'unselected':first_discovery_unselected},
    'future_quote_by_frozen_arm_and_observation_class':arm_histories,
    'early_nascent_quote_attempts_this_run':nascent_entry_quotes_this_run,
    'guaranteed_first_quote_reserve_if_available':NASCENT_ENTRY_RESERVED,
    'missing_first_discovery_history_not_backfilled':True,
    'no_execution_or_profitability_proven':True,'orders':0,'trades':0}
with open('evidence/emergence_research_arms.json','w',encoding='utf-8') as evidence_file:
    json.dump(emergence_report,evidence_file,indent=2,allow_nan=False)
print('CRYPTO_EMERGENCE_RESEARCH_ARMS',json.dumps(emergence_report,allow_nan=False))
# Forward model-preparation only: compare PRE-OUTCOME 30-second signals to
# independently recorded later 30s-to-5min onchain curve observations.
# Pools absent from GeckoTerminal remain valid here. No hindsight selection.
early_complete=read_native_first30_features()
later_complete={}
for row in live_jsonl_evidence('evidence/pumpfun_first30_to_5m_outcomes.jsonl'):
    if (row.get('complete_5m_telemetry_without_provider_gap') is True
            and row.get('mint') and isinstance(row.get('outcome_frozen_at'),str)):
        mint=row['mint']
        if (mint not in later_complete or
                row['outcome_frozen_at']<later_complete[mint]['outcome_frozen_at']):
            later_complete[mint]=row
print('PUMPFUN_NATIVE_PIPELINE_RECEIPT',json.dumps({
    'as_of':r['at'],
    'verified_first30_feature_mints':len(early_complete),
    'complete_post30_five_minute_outcomes':len(later_complete),
    'same_mint_forward_pairs':len(early_complete.keys() & later_complete.keys()),
    'unpaired_outcome_unknown_not_negative':True,
    'feature_original_observation_preserved':True,
    'actual_trades':0},allow_nan=False))
frozen_strata={}
for name in ('creator_sold_first30','creator_not_sold_first30',
             'five_plus_distinct_buyers','under_five_distinct_buyers'):
    frozen_strata[name]={'with_30s_features':0,'matched_forward_5m':0,
        'not_yet_observed_or_gap_censored':0,'post30_curve_2x':0,
        'post30_curve_5x':0,'post30_curve_11x':0}
sample_cases=[]
for mint,early in early_complete.items():
    arms=[
        'creator_sold_first30' if early.get('creator_sell_observed_before30s')
        else 'creator_not_sold_first30',
        'five_plus_distinct_buyers' if
            int(early.get('distinct_buyers_first30') or 0)>=5
        else 'under_five_distinct_buyers']
    later=later_complete.get(mint)
    for name in arms:
        entry=frozen_strata[name]
        entry['with_30s_features']+=1
        if later is None:
            entry['not_yet_observed_or_gap_censored']+=1
        else:
            entry['matched_forward_5m']+=1
            for level in (2,5,11):
                if later.get('curve_peak_ge'+str(level)+'x') is True:
                    entry['post30_curve_'+str(level)+'x']+=1
    if later and len(sample_cases)<40:
        sample_cases.append({
            'mint':mint,'creator_sells_first30':
                early.get('creator_sell_events_first30'),
            'distinct_buyers_first30':early.get('distinct_buyers_first30'),
            'post30_observed_curve_peak_x':
                later.get('observed_later_curve_peak_x'),
            'no_quote_or_filled_exit':True})
native_forward_report={
    'as_of':r['at'],'frozen_initial_feature_mints':len(early_complete),
    'complete_future_5m_mints':len(later_complete),
    'same_mint_paired_complete_first30_then_future5m':len(
        set(early_complete)&set(later_complete)),
    'precursor_strata_defined_without_future_labels':True,
    'real_first30_creator_sale_vs_followup_curve_truth':frozen_strata,
    'paired_examples':sample_cases,
    'unmatched_followup_censored_not_negative':True,
    'onchain_curve_price_not_sell_quote_or_executed_profit':True,
    'tracked_beyond_gecko_and_without_token_listing':True,
    'selection_and_provider_bias_possible':True,
    'no_backfilled_timestamps_or_future_labels_in_features':True,
    'orders':0,'trades':0}
with open('evidence/pumpfun_first30_5m_forward_validation.json','w',
          encoding='utf-8') as f:
    json.dump(native_forward_report,f,indent=2,allow_nan=False)
print('PUMPFUN_PRECURSOR_TO_FORWARD_5M',json.dumps(
    {k:v for k,v in native_forward_report.items()
     if k!='paired_examples'},allow_nan=False))

# Quantify first-quote cliff events from *actually observed* intraminute
# Jupiter sell routes. Historical and new v1 windows both have timestamped
# quotations; no unobserved intervals are interpolated, no fills assumed.
# One candidate per pool: active checkpoint takes precedence over archive.
rapid_records={}
for old in shadow_ledger:
    if old.get('rapid_quote_window_v1') and old.get('pool'):
        rapid_records[old['pool']]=old
for pool,rec in st.items():
    sh=rec.get('shadow') or {}
    if sh.get('rapid_quote_window_v1'):
        rapid_records[pool]={'pool':pool,'token_id':rec.get('token_id'),
            'entry_liquidity_usd':sh.get('entry_liquidity_usd'),
            'initial_age_minutes':sh.get('initial_age_minutes'),
            'initial_volume_1h_usd':sh.get('initial_volume_1h_usd'),
            'initial_buyers_1h':sh.get('initial_buyers_1h'),
            'initial_dex':sh.get('initial_dex'),
            'rapid_quote_window_v1':sh.get('rapid_quote_window_v1'),
            'first_liquidity_drop_90pct_v1':sh.get('first_liquidity_drop_90pct_v1'),
            'first_liquidity_drop_99pct_v1':sh.get('first_liquidity_drop_99pct_v1')}
rapid_cases=[]
for pool,z in rapid_records.items():
    history=(z.get('rapid_quote_window_v1') or {}).get('quotes') or []
    valid=[]
    for step in history:
        v=num(step.get('quoted_x'))
        at=step.get('at')
        if step.get('quote_ok') and v>0 and at:
            valid.append((at,v))
    first=history[0] if history else {}
    if not valid:continue
    baseline_at,baseline_x=valid[0]
    following=[(at,v) for at,v in valid if at>baseline_at]
    if not following:continue
    follow_at,follow_x=following[0]
    try:gap_s=(d.datetime.fromisoformat(follow_at)-d.datetime.fromisoformat(baseline_at)).total_seconds()
    except (TypeError,ValueError):continue
    if gap_s<=0:continue
    cliff=follow_x<=baseline_x*.1
    severe=follow_x<=baseline_x*.01
    liquidity_90=z.get('first_liquidity_drop_90pct_v1')
    liquidity_99=z.get('first_liquidity_drop_99pct_v1')
    rapid_cases.append({'pool':pool,'token_id':z.get('token_id'),
        'entry_liquidity_usd':z.get('entry_liquidity_usd'),
        'entry_volume_1h_usd':z.get('initial_volume_1h_usd'),
        'initial_buyers_1h':z.get('initial_buyers_1h'),
        'initial_age_minutes':z.get('initial_age_minutes'),
        'initial_dex':z.get('initial_dex'),
        'signal_quote_x':round(baseline_x,8),
        'first_followup_quote_x':round(follow_x,8),
        'first_followup_elapsed_seconds':round(gap_s,3),
        'first_followup_quote_loss_pct':round(100*(1-follow_x/baseline_x),4),
        'first_followup_90pct_cliff':cliff,
        'first_followup_99pct_cliff':severe,
        'no_prior_warning_between_two_quotes':cliff,
        'observed_liquidity_drop_90pct_at':liquidity_90.get('at') if liquidity_90 else None,
        'observed_liquidity_drop_99pct_at':liquidity_99.get('at') if liquidity_99 else None,
        'observed_later_liquidity_usd':(liquidity_99 or liquidity_90 or {}).get('observed_liquidity_usd'),
        'not_exact_crash_time':True,'not_executable_profit':True})
cliffed=[z for z in rapid_cases if z['first_followup_90pct_cliff']]
rapid_report={
    'as_of':r['at'],'enrolled_quote_windows_total':len(rapid_records),
    'with_valid_baseline_and_later_quote':len(rapid_cases),
    'first_followup_90pct_cliff_count':len(cliffed),
    'first_followup_99pct_cliff_count':sum(z['first_followup_99pct_cliff'] for z in rapid_cases),
    'first_followup_90pct_cliff_no_advance_warning_count':len(cliffed),
    'later_liquidity_90pct_drop_observed_count':sum(bool(z['observed_liquidity_drop_90pct_at']) for z in rapid_cases),
    'later_liquidity_99pct_drop_observed_count':sum(bool(z['observed_liquidity_drop_99pct_at']) for z in rapid_cases),
    'median_baseline_to_next_quote_seconds':round(statistics.median(
        z['first_followup_elapsed_seconds'] for z in rapid_cases),3) if rapid_cases else None,
    'pre_signal_filter_test_is_exploratory_only':True,
    'small_convenience_sample_not_base_rate':True,
    'realized_profit_verified':False,'real_orders':0,'real_trades':0,
    'no_intrainterval_quote_or_liquidity_assumed':True,
    'case_count':len(rapid_cases),
    'cases_most_severe_first':sorted(rapid_cases,
        key=lambda x:x['first_followup_quote_loss_pct'],reverse=True)[:50]}
with open('evidence/rapid_quote_crash_risk_study.json','w',encoding='utf-8') as f:
    json.dump(rapid_report,f,indent=2,allow_nan=False)
print('RAPID_QUOTE_CRASH_RISK_STUDY',json.dumps({
    k:v for k,v in rapid_report.items() if k!='cases_most_severe_first'},allow_nan=False))
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
# Merge only THIS RUN'S on-chain authority evidence from the embedded WS
# worker. Unknown or stale mint authority is not evidence of safety.
mint_audits={}
try:
    with open('evidence/liquidity_ws_events.jsonl',encoding='utf-8') as risk_file:
        for line in risk_file:
            try:
                event=json.loads(line)
                if event.get('kind')!='onchain_mint_risk':
                    continue
                key=event.get('mint')
                if isinstance(key,str):
                    mint_audits[key]=event
            except (ValueError,TypeError):
                continue
except OSError:
    pass
# Attach only FIRST verified LP observation for each pool. If no coverage
# exists, never silently treat it as creator ownership being zero.
first_lp_control_by_pool={}
try:
    with open('evidence/onchain_lp_control_prospective.jsonl',
              encoding='utf-8') as lp_index_file:
        for lp_line in lp_index_file:
            try:
                lp_row=json.loads(lp_line)
                key=lp_row.get('pool')
                if key and lp_row.get('available'):
                    first_lp_control_by_pool.setdefault(key,lp_row)
            except (ValueError,TypeError):
                continue
except OSError:
    pass
for candidate in priority:
    mint=(candidate.get('token_id') or '').removeprefix('solana_')
    audit=mint_audits.get(mint) or {}
    try:
        age_seconds=(now-d.datetime.fromisoformat(audit['at'])).total_seconds()
    except (ValueError,TypeError,KeyError):
        age_seconds=float('inf')
    fresh_audit=bool(audit.get('available') and 0<=age_seconds<=300)
    authority_risk=bool(audit.get('risk_notice')) if fresh_audit else None
    candidate['onchain_mint_audit_available']=fresh_audit
    candidate['onchain_mint_authority_risk']=authority_risk
    candidate['mint_safety_assessment']='flagged' if authority_risk else (
        'no_listed_authority_flags' if fresh_audit else 'unknown_or_stale')
    candidate['mint_risk_review_passed']=fresh_audit and not authority_risk
    lp_control=first_lp_control_by_pool.get(candidate['pool']) or {}
    try:
        lp_age=(now-d.datetime.fromisoformat(
            lp_control['sample_completed_at'])).total_seconds()
    except (KeyError,ValueError,TypeError):
        lp_age=float('inf')
    lp_recent=bool(lp_control.get('available') and 0<=lp_age<=2*3600)
    lp_flags=lp_control.get('structural_risk_flags') if lp_recent else None
    candidate['lp_control_snapshot_available']=lp_recent
    candidate['lp_control_snapshot_age_seconds']=round(lp_age,1) if lp_recent else None
    candidate['lp_outstanding_fraction']=(
        lp_control.get('outstanding_lp_supply_fraction') if lp_recent else None)
    candidate['lp_creator_direct_fraction']=(
        lp_control.get('creator_direct_lp_fraction') if lp_recent else None)
    candidate['lp_structural_flags']=lp_flags
    candidate['lp_structural_risk_tier']=(
        lp_control.get('structural_risk_tier') if lp_recent else 'unknown')
    # Unflagged LP is not an exhaustive security audit or proof of profit.
    candidate['risk_review_complete']=bool(
        lp_recent and lp_control.get('holder_detail_complete')
        and not lp_flags and fresh_audit and not authority_risk)
    candidate['eligible_for_unattended_capital']=False

priority_report={'at':now.isoformat(),'method':'live signal-time fundamentals; never retrospective peak',
    'ranking_not_win_probability':True,'fresh_quote_is_not_a_fill':True,
    'tracked_eligible':len(priority),
    'lp_control_snapshots_recent':sum(x['lp_control_snapshot_available'] for x in priority),
    'lp_control_structurally_elevated':sum(x['lp_structural_risk_tier']=='elevated' for x in priority),
    'lp_control_unknown':sum(x['lp_structural_risk_tier']=='unknown' for x in priority),
    'risk_review_complete_not_capital_authorized':sum(x['risk_review_complete'] for x in priority),
    'onchain_mint_audits_fresh':sum(x['onchain_mint_audit_available'] for x in priority),
    'onchain_mint_authority_flags':sum(x['onchain_mint_authority_risk'] is True for x in priority),
    'mint_risk_review_pass_count':sum(x['mint_risk_review_passed'] for x in priority),
    'recent_liquid_quote_candidates':sum(p['recent_liquid_sell_quote_observed'] for p in priority),
    'recent_quote_but_liquidity_failed':sum(p['fresh_sell_quote_within_15m'] and
        p['current_liquidity_risk_reason'] in
        ('current_liquidity_below_10000_usd','current_liquidity_drawdown_over_50pct') for p in priority),
    'top':priority[:10],'orders':0,'trades':0}
with open('evidence/live_candidate_priority.json','w') as f:
    json.dump(priority_report,f,indent=2,allow_nan=False)
print('LIVE_CANDIDATE_PRIORITY',json.dumps(priority_report,allow_nan=False))

# Evidence-backed readiness gate: failed prerequisites must never be
# interpreted as an instruction to sign or fund any trade.
try:
    with open('evidence/liquidity_ws_health.json',encoding='utf-8') as sensor_file:
        sensor_health=json.load(sensor_file)
except (OSError,ValueError,TypeError):
    sensor_health={}
try:
    with open('evidence/observations.jsonl',encoding='utf-8') as observation_file:
        sample_available=any(row.get('kind')=='sample' and row.get('pool_count',0)>0
            for line in observation_file if line.strip()
            for row in (json.loads(line),))
except (OSError,ValueError,TypeError):
    sample_available=False
quoted=historical_backtest['paper_exit_quote_subpopulation']
quoted_edge=(quoted['later_exit_quote_observed']>=100 and
             quoted['gross_quote_pl_usdc_conditional_on_later_quote']>0 and
             quoted['winners_quote_gt_1x']>quoted['losers_quote_lt_1x'])
gates={
    'live_market_samples_available':sample_available,
    'live_ws_subscriptions_observed':(
        int(sensor_health.get('max_subscriptions') or 0)>0 and
        int(sensor_health.get('events') or 0)>0),
    'live_liquidity_checks_observed':int(sensor_health.get('checks') or 0)>0,
    'liquid_buy_and_sell_quotes_fresh':priority_report['recent_liquid_quote_candidates']>0,
    'positive_quote_edge_on_observed_exits':quoted_edge,
    'direct_onchain_vault_reserve_tracking_verified':(
        int(sensor_health.get('pump_vault_confirmed') or 0)>0),
    'direct_pumpswap_vault_websocket_subscriptions_verified':(
        int(sensor_health.get('max_vault_ws_subscriptions') or 0)>=2),
    'other_dex_onchain_vault_coverage_verified':False,
    'real_filled_entry_and_exit_returns_verified':False,
    'secure_wallet_signing_and_send_verified':False}
readiness={
    'as_of':now.isoformat(),
    'status':'NOT_READY_FOR_LIVE_CAPITAL' if not all(gates.values()) else 'REVIEW_REQUIRED',
    'gates':gates,
    'failed_gates':[name for name,passed in gates.items() if not passed],
    'live_sensor_snapshot':{k:sensor_health.get(k) for k in
        ('at','connected','subscriptions','max_subscriptions','ever_subscribed','events','checks','errors','tx_rpc_checks','onchain_token_balance_changes','dex_checks',
         'gecko_fallback_checks','lp_instruction_hints','pump_vault_queries','pump_vault_confirmed','pump_vault_joint_drops','pump_vault_errors','vault_ws_subscriptions','max_vault_ws_subscriptions','vault_ws_events','vault_ws_joint_drops','lp_control_probes','lp_control_confirmed','lp_control_high_risk','lp_control_unknown','pre_rug_sell_pressure_events','candidate_preflight_queued',
         'creator_lp_ws_subscriptions','creator_lp_ws_events','creator_lp_early_decreases',
         'creator_token_probes','creator_token_confirmed','creator_token_ws_subscriptions',
         'creator_token_ws_events','creator_token_decreases',
         'vault_pairs_same_slot','vault_pairs_bounded_skew',
         'vault_pair_skew_rejections','vault_cross_slot_joint_drop_suspected',
         'vault_pair_telemetry_rows','slot_notifications',
         'slot_subscriptions_confirmed','vault_rpc_confirmation_checks',
         'vault_rpc_joint_drops_confirmed','vault_rpc_joint_drops_not_confirmed',
         'vault_rpc_joint_drops_unknown')},
    'observed_quote_economics':{k:quoted.get(k) for k in
        ('entry_quoted_total','later_exit_quote_observed','winners_quote_gt_1x',
         'losers_quote_lt_1x','gross_quote_pl_usdc_conditional_on_later_quote')},
    'real_orders':0,'real_trades':0,
    'onchain_coverage':'Verified PumpSwap pools only, not all Solana DEX pools',
    'guardrail':'An executable quote is not a fill; authorization and a secret do not imply profitable execution'}
with open('evidence/live_trade_readiness.json','w',encoding='utf-8') as readiness_file:
    json.dump(readiness,readiness_file,indent=2,allow_nan=False)
print('CRYPTO_LIVE_READINESS',json.dumps(readiness,allow_nan=False))

# Predeclared operational evidence: latency from first risk observation to
# the next matching read-only exit quote. It is NOT a filled exit or an
# estimate of lead time before the unobserved actual reserve withdrawal.
alert_types={'lp_withdrawal_log_hint':'unverified_lp_withdrawal_log',
             'onchain_vault_joint_drop':'confirmed_joint_pumpswap_vault_balance_drop',
             'onchain_vault_ws_joint_drop':'same_slot_pumpswap_vault_ws_joint_drop',
             'onchain_vault_ws_joint_drop_suspected':'ws_pumpswap_joint_drop_suspected',
             'onchain_preemptive_sell_pressure':'early_sell_pressure_vault_observation',
             'onchain_lp_creator_balance_drop':'observed_creator_lp_balance_decrease',
             'onchain_pool_creator_token_balance_drop':'observed_pool_creator_token_balance_decrease',
             'liquidity_critical':'reserve_critical',
             'liquidity_warning':'reserve_warning'}
events=[]
try:
    with open('evidence/liquidity_ws_events.jsonl',encoding='utf-8') as sensor_log:
        for line in sensor_log:
            try:
                event=json.loads(line)
                if event.get('kind') in (*alert_types.keys(),'urgent_sell_quote',
                                          'onchain_vault_pair_observation',
                                          'onchain_vault_rpc_confirmation'):
                    events.append(event)
            except (ValueError,TypeError):
                continue
except OSError:
    pass
# The previous report dropped preemptive sell-pressure events from "events".
# Persist FIRST-OBSERVED, timestamped risk/drain observations across workflows.
# This allows forward tests after GitHub's individual 20-minute job ends.
onchain_outcomes_file='evidence/onchain_risk_outcomes_ledger.jsonl'
persistent_kinds={'onchain_preemptive_sell_pressure',
                  'onchain_lp_creator_balance_drop',
                  'onchain_pool_creator_token_snapshot',
                  'onchain_pool_creator_token_balance_drop',
                  'onchain_vault_ws_joint_drop','onchain_vault_joint_drop',
                  'onchain_vault_pair_observation',
                  'onchain_vault_cross_slot_joint_drop_suspected',
                  'onchain_vault_ws_joint_drop_suspected',
                  'onchain_vault_rpc_confirmation',
                  'urgent_sell_quote',
                  'liquidity_warning','liquidity_critical',
                  'lp_withdrawal_log_hint'}
onchain_evidence_by_id={}
try:
    with open(onchain_outcomes_file,encoding='utf-8') as old_evidence:
        for line in old_evidence:
            try:
                ev=json.loads(line)
                if ev.get('kind') in persistent_kinds:
                    key=(ev.get('kind'),ev.get('pool'),ev.get('at'),ev.get('slot'))
                    onchain_evidence_by_id.setdefault(key,ev)
            except (ValueError,TypeError):
                continue
except OSError:
    pass
old_evidence_count=len(onchain_evidence_by_id)
for ev in events:
    if ev.get('kind') in persistent_kinds:
        key=(ev.get('kind'),ev.get('pool'),ev.get('at'),ev.get('slot'))
        onchain_evidence_by_id.setdefault(key,ev)
onchain_evidence=list(onchain_evidence_by_id.values())
onchain_evidence.sort(key=lambda x:(x.get('at') or '',x.get('pool') or ''))
# Write atomically: no partial event ledger if the GitHub runner is stopped.
tmp_path=onchain_outcomes_file+'.tmp'
with open(tmp_path,'w',encoding='utf-8') as eventfile:
    for ev in onchain_evidence:
        eventfile.write(json.dumps(ev,allow_nan=False,separators=(',',':'))+'\n')
os.replace(tmp_path,onchain_outcomes_file)
print('ONCHAIN_FORWARD_EVENT_LEDGER',json.dumps({
    'previous_event_count':old_evidence_count,
    'new_events_this_run':len(onchain_evidence)-old_evidence_count,
    'retained_events':len(onchain_evidence),
    'vault_pair_samples_retained':sum(
        z.get('kind')=='onchain_vault_pair_observation' for z in onchain_evidence),
    'vault_pair_distinct_pools':len({
        z.get('pool') for z in onchain_evidence
        if z.get('kind')=='onchain_vault_pair_observation'}),
    'vault_pair_atomic_sample_count':sum(
        z.get('kind')=='onchain_vault_pair_observation' and
        z.get('current_pair_atomic') is True for z in onchain_evidence),
    'confirmed_rpc_joint_drops':sum(
        z.get('kind')=='onchain_vault_rpc_confirmation' and
        z.get('outcome')=='confirmed' for z in onchain_evidence),
    'unverified_ws_joint_drop_hints':sum(
        z.get('kind')=='onchain_vault_ws_joint_drop_suspected'
        for z in onchain_evidence),
    'vault_pair_cross_slot_sample_count':sum(
        z.get('kind')=='onchain_vault_pair_observation' and
        z.get('current_pair_atomic') is False for z in onchain_evidence),
    'read_only':True,'observations_not_profit_predictions':True},allow_nan=False))
risk_alerts=[x for x in onchain_evidence if x.get('kind') in alert_types]
risk_quotes=[x for x in onchain_evidence if x.get('kind')=='urgent_sell_quote']
matching_quotes=[]
for alert in risk_alerts:
    try:
        at=d.datetime.fromisoformat(alert['at'])
    except (KeyError,TypeError,ValueError):
        continue
    possibilities=[]
    for quote in risk_quotes:
        if (quote.get('pool')!=alert.get('pool') or
                quote.get('trigger')!=alert_types[alert['kind']]):
            continue
        try:
            secs=(d.datetime.fromisoformat(quote['at'])-at).total_seconds()
        except (KeyError,TypeError,ValueError):
            continue
        if 0<=secs<=60:
            possibilities.append((secs,quote))
    if possibilities:
        secs,quote=min(possibilities,key=lambda pair:pair[0])
        matching_quotes.append({'trigger_kind':alert['kind'],
            'pool':alert.get('pool'),'alert_at':alert['at'],
            'quote_at':quote['at'],'quote_latency_seconds':round(secs,3),
            'quote_available':bool(quote.get('available')),
            'quote_output_usdc':quote.get('usdc'),
            'not_an_executed_trade':True})
latencies=[x['quote_latency_seconds'] for x in matching_quotes]
alert_latency_report={
    'as_of':now.isoformat(),
    'observation_window':'durable cross-run ledger when restored plus current job',
    'sensor_risk_alerts_total':len(risk_alerts),
    'sensor_risk_alerts_new_this_run':sum(x.get('kind') in alert_types for x in events),
    'retained_sell_quotes_total':len(risk_quotes),
    'matching_requires_same_pool_trigger_and_quote_after_alert_within_60s':True,
    'missing_quote_is_unknown_not_unexecutable':True,
    'risk_alerts_by_type':{k:sum(x.get('kind')==k for x in risk_alerts)
                           for k in alert_types},
    'matched_readonly_quotes_within_60s':len(matching_quotes),
    'matched_quotes_with_positive_output':sum(
        x['quote_available'] and x['quote_output_usdc'] is not None
        and x['quote_output_usdc']>0 for x in matching_quotes),
    'median_alert_to_quote_seconds':round(statistics.median(latencies),3)
        if latencies else None,
    'paired_evidence':matching_quotes[:50],
    'wallet_fill_count':0,
    'lead_time_before_collapse_proven':False,
    'no_profitability_claim':True}
with open('evidence/liquidity_alert_latency.json','w',encoding='utf-8') as evidence:
    json.dump(alert_latency_report,evidence,indent=2,allow_nan=False)
print('LIQUIDITY_ALERT_LATENCY',json.dumps(alert_latency_report,allow_nan=False))

# Frozen before the outcomes: first verifiable LP ownership observation per
# pool. The current run may then provide a later observed reserve-drain label.
# Unobserved events, provider gaps and future runs remain censored/unknown.
lp_ledger={}
try:
    with open('evidence/onchain_lp_control_prospective.jsonl',encoding='utf-8') as lpfile:
        for lp_line in lpfile:
            try:
                lp_row=json.loads(lp_line)
            except (TypeError,ValueError):
                continue
            pool=lp_row.get('pool')
            if not (pool and lp_row.get('available') and
                    lp_row.get('sample_completed_at')):
                continue
            lp_ledger.setdefault(pool,lp_row)
except OSError:
    pass
lp_drain_events=[event for event in onchain_evidence
                 if (event.get('kind')=='onchain_vault_joint_drop' or
                     (event.get('kind')=='onchain_vault_rpc_confirmation'
                      and event.get('outcome')=='confirmed'))]
lp_labels=[]
for pool,baseline in lp_ledger.items():
    try:
        frozen_at=d.datetime.fromisoformat(baseline['sample_completed_at'])
    except (KeyError,TypeError,ValueError):
        continue
    matched=[]
    for ev in lp_drain_events:
        if ev.get('pool')!=pool:
            continue
        try:
            lag=(d.datetime.fromisoformat(ev['at'])-frozen_at).total_seconds()
        except (KeyError,TypeError,ValueError):
            continue
        if lag>=0:
            matched.append((lag,ev))
    if matched:
        lag,first=min(matched,key=lambda x:x[0])
        if lag<30:
            continue
        lp_labels.append({'pool':pool,'lp_measured_at':baseline['sample_completed_at'],
            'risk_at_measurement':baseline.get('structural_risk_tier'),
            'first_onchain_joint_drain_at':first['at'],
            'lead_seconds':round(lag,3),
            'first_drain_event_type':first['kind'],
            'read_only_event_not_realized_exit':True})
lp_study={
    'as_of':now.isoformat(),
    'method':'Frozen first onchain LP ownership evidence; later same-pool RPC-confirmed dual-vault decrease >=20% on both legs',
    'first_verified_lp_snapshots':len(lp_ledger),
    'elevated_structural_risk_at_first_snapshot':sum(
        z.get('structural_risk_tier')=='elevated' for z in lp_ledger.values()),
    'incomplete_structural_risk_at_first_snapshot':sum(
        z.get('structural_risk_tier')=='incomplete' for z in lp_ledger.values()),
    'verified_large_direct_holder_control':sum(
        z.get('direct_large_holder_risk_observed') is True for z in lp_ledger.values()),
    'high_outstanding_supply_without_verified_concentrated_holder':sum(
        z.get('outstanding_supply_only_flag') is True for z in lp_ledger.values()),
    'unknown_direct_holder_status':sum(
        not z.get('holder_detail_complete') for z in lp_ledger.values()),
    'structural_categories':{
        category:sum(z.get('lp_control_evidence_category')==category
                     for z in lp_ledger.values())
        for category in (
            'verified_concentrated_direct_holder',
            'outstanding_supply_only_holder_unknown',
            'outstanding_supply_only_no_large_direct_holder',
            'holder_coverage_incomplete',
            'no_large_direct_holder_observed_not_safe')},
    'historical_rows_without_classification_left_unclassified':sum(
        'lp_control_evidence_category' not in z for z in lp_ledger.values()),
    'subsequent_verified_joint_drain_observed_after_30s':len(lp_labels),
    'cross_run_event_ledger_used':True,
    'matched_future_drain_evidence':lp_labels[:30],
    'no_outcome_for_unmatched_cohort_is_censored_not_negative':True,
    'provider_outages_possible':True,'unseen_events_between_runs_unknown':True,
    'prediction_quality_or_success_rate_proven':False,
    'no_executed_trades':True}
with open('evidence/onchain_lp_early_warning_study.json','w',encoding='utf-8') as resultfile:
    json.dump(lp_study,resultfile,indent=2,allow_nan=False)
print('LP_CONTROL_FORWARD_VALIDATION',json.dumps(lp_study,allow_nan=False))

# Counterfactual liquidity-exit feasibility: record whether an observed
# quote at FIRST high LP-control risk was better than a quote following
# an observed later two-vault drain, for EXACT same token raw amount.
# Treat missing quotes and pool observations as censored, not profits.
prewithdrawal_quote_pairs=[]
for matched in lp_labels:
    pool=matched['pool']
    baseline=lp_ledger.get(pool) or {}
    if baseline.get('structural_risk_tier')!='elevated':
        continue
    candidates=[]
    for ev in onchain_evidence:
        if ev.get('pool')!=pool or ev.get('kind')!='urgent_sell_quote':
            continue
        try:
            seconds=(d.datetime.fromisoformat(ev['at'])-d.datetime.fromisoformat(
                baseline['sample_completed_at'])).total_seconds()
        except (KeyError,ValueError,TypeError):
            continue
        if (ev.get('trigger')=='lp_control_prewithdrawal_review'
                and 0<=seconds<=120 and ev.get('available')):
            candidates.append(ev)
    if not candidates:
        continue
    earliest=min(candidates,key=lambda x:x['at'])
    try:
        drain_at=d.datetime.fromisoformat(matched['first_onchain_joint_drain_at'])
    except (KeyError,ValueError,TypeError):
        continue
    later=[]
    for ev in onchain_evidence:
        if (ev.get('pool')!=pool or ev.get('kind')!='urgent_sell_quote'
                or not ev.get('available')
                or ev.get('amount_raw')!=earliest.get('amount_raw')):
            continue
        try:
            seconds=(d.datetime.fromisoformat(ev['at'])-drain_at).total_seconds()
        except (KeyError,ValueError,TypeError):
            continue
        if 0<=seconds<=120:
            later.append(ev)
    if not later:
        continue
    first_later=min(later,key=lambda x:x['at'])
    before=earliest.get('usdc')
    after=first_later.get('usdc')
    if not all(isinstance(v,(int,float)) and v>=0 for v in (before,after)):
        continue
    prewithdrawal_quote_pairs.append({
        'pool':pool,'baseline_at':baseline['sample_completed_at'],
        'early_quote_usdc':before,'later_quote_usdc':after,
        'estimated_quote_only_value_preserved_usdc':round(before-after,6),
        'same_token_raw_amount':str(earliest.get('amount_raw')),
        'joint_drain_at':matched['first_onchain_joint_drain_at'],
        'no_executed_trades':True,
        'not_net_profit_accounting':True})
early_exit_report={
    'as_of':now.isoformat(),
    'predeclared_high_lp_risk_snapshot_count':sum(
        z.get('structural_risk_tier')=='elevated' for z in lp_ledger.values()),
    'paired_before_and_after_drain_same_size_quotes':len(prewithdrawal_quote_pairs),
    'quote_only_comparisons':prewithdrawal_quote_pairs[:30],
    'unobserved_exits_and_missing_later_rugs_censored':True,
    'potential_rug_prevention_not_proven':True,
    'no_execution_or_net_profit_proven':True}
with open('evidence/onchain_lp_early_quote_vs_drain.json','w',encoding='utf-8') as evfile:
    json.dump(early_exit_report,evfile,indent=2,allow_nan=False)
print('LP_EARLY_VERSUS_DRAIN_QUOTE',json.dumps(early_exit_report,allow_nan=False))


# Falsifiable order-flow precursor: pre-registered 12%/12% reserve pressure
# within 120s, recorded at notification time. Only LATER >=30s confirmed
# two-vault drains may count as lead-time evidence.
precursor_kinds={'onchain_preemptive_sell_pressure',
                 'onchain_lp_creator_balance_drop',
                 'onchain_pool_creator_token_balance_drop'}
precursor_events=[e for e in onchain_evidence
                  if e.get('kind') in precursor_kinds]
# FIRST verifiable drain after each trigger; never skip an immediate drain
# to cherry-pick a later recurrence 30+ seconds after the trigger.
# These are RPC-confirmation timestamps, NOT proven physical drain start.
forward_precursor_matches=[]
too_late_precursor_matches=[]
for trigger in precursor_events:
    earliest=first_confirmed_drain_after(
        trigger.get('pool'),trigger.get('at'),lp_drain_events)
    if earliest is None:
        continue
    lead,drain=earliest
    row={'pool':trigger.get('pool'),'trigger_at':trigger['at'],
         'drain_at':drain['at'],'trigger_kind':trigger.get('kind'),
         'first_confirmation_delay_seconds':round(lead,3),
         'trigger_threshold_fixed_before_observation':True,
         'drain_recorded_by':drain.get('kind'),
         'confirmed_time_not_exact_chain_drain_time':True,
         'not_an_executed_exit':True}
    if lead>=30:
        forward_precursor_matches.append(row)
    else:
        too_late_precursor_matches.append(row)
precursor_report={
    'as_of':now.isoformat(),
    'precommitted_definition':{
        'sell_pressure':'Watched mint vault rises >=12% and counterasset falls >=12% within 120s, after two observed vault-pair slots',
        'creator_lp_balance':'Creator direct verified LP token account drops >=10% across observed account notifications',
        'pool_creator_tradeable_token':'Pool creator (not necessarily original token developer) verified tradeable base token account drops >=10%; may be transfer not sale',
        'outcome':'A later (>=30s) same-pool verified >=20% simultaneous two-vault loss',
        'same_slot_lp_burn_is_not_a_lead_signal':True},
    'precursor_observations_in_this_run':sum(
        x.get('kind') in precursor_kinds for x in events),
    'precursor_observations_in_retained_ledger':len(precursor_events),
    'precursor_counts_by_kind':{
        k:sum(x.get('kind')==k for x in precursor_events)
        for k in sorted(precursor_kinds)},
    'precursor_matches_by_kind':{
        k:sum(x['trigger_kind']==k for x in forward_precursor_matches)
        for k in sorted(precursor_kinds)},
    'observed_subsequent_verified_2_vault_drains_at_least_30s_later':len(forward_precursor_matches),
    'first_confirmed_drains_too_late_under_30s':len(too_late_precursor_matches),
    'first_confirmation_only_no_later_event_cherry_pick':True,
    'confirmation_time_does_not_prove_physical_drain_start':True,
    'matched_evidence':forward_precursor_matches[:40],
    'too_late_evidence':too_late_precursor_matches[:40],
    'unmatched_outcomes_are_unknown_due_to_incomplete_followup':True,
    'no_sensitivity_precision_or_profitability_claim':True,
    'not_all_dexes_covered':True,
    'no_executed_trades':True}
with open('evidence/onchain_rug_precursor_forward_test.json','w',encoding='utf-8') as precursorfile:
    json.dump(precursor_report,precursorfile,indent=2,allow_nan=False)
print('ONCHAIN_PRE_RUG_FORWARD_TEST',json.dumps(precursor_report,allow_nan=False))

# 10-minute forward outcome verification: absence of a recorded rug must NOT
# count as a true negative unless paired-vault observations cover the window
# without substantial telemetry gaps. Each pool/kind contributes one FIRST
# alert; later near-simultaneous drains (<30s) count as too-late, not success.
pairs_by_pool={}
for e in onchain_evidence:
    if e.get('kind')!='onchain_vault_pair_observation':
        continue
    try:
        at=d.datetime.fromisoformat(e['at'])
    except (KeyError,TypeError,ValueError):
        continue
    if e.get('genuine_vault_accounts_verified') is not True:
        continue
    pairs_by_pool.setdefault(e.get('pool'),[]).append((at,e))
for pool,history in pairs_by_pool.items():
    history.sort(key=lambda pair:pair[0])
drains_by_pool={}
for e in lp_drain_events:
    try:
        at=d.datetime.fromisoformat(e['at'])
    except (KeyError,TypeError,ValueError):
        continue
    drains_by_pool.setdefault(e.get('pool'),[]).append((at,e))
for pool,history in drains_by_pool.items():
    history.sort(key=lambda pair:pair[0])
first_alerts={}
for ev in precursor_events:
    key=(ev.get('pool'),ev.get('kind'))
    if key[0] and key[1]:
        previous=first_alerts.get(key)
        if previous is None or ev.get('at','')<previous.get('at',''):
            first_alerts[key]=ev
evaluated=[]
for (pool,kind),ev in sorted(first_alerts.items()):
    try:
        t0=d.datetime.fromisoformat(ev['at'])
    except (KeyError,TypeError,ValueError):
        continue
    t1=t0+d.timedelta(minutes=10)
    earliest=first_confirmed_drain_after(pool,ev['at'],lp_drain_events,600)
    first_drain=((t0+d.timedelta(seconds=earliest[0]),earliest[1])
                 if earliest is not None else None)
    post=[t for t,_ in pairs_by_pool.get(pool,[]) if t0<=t<=t1]
    post.sort()
    full_window=(
        now>=t1 and len(post)>=6 and
        (post[0]-t0).total_seconds()<=90 and
        (t1-post[-1]).total_seconds()<=90 and
        all((b-a).total_seconds()<=90 for a,b in zip(post,post[1:])))
    if first_drain is not None:
        lead=(first_drain[0]-t0).total_seconds()
        outcome='observed_30s_plus_early' if lead>=30 else 'too_late_under_30s'
    elif full_window:
        lead=None
        outcome='no_verified_joint_drain_during_covered_10m'
    else:
        lead=None
        outcome='right_censored_or_provider_gap'
    evaluated.append({'pool':pool,'trigger_kind':kind,'trigger_at':ev['at'],
        'horizon_seconds':600,'outcome':outcome,
        'observed_lead_seconds':round(lead,3) if lead is not None else None,
        'paired_vault_observations_in_window':len(post),
        'full_10m_telemetry_coverage_proven':full_window,
        'no_actual_sell_or_buy':True})
outcomes=['observed_30s_plus_early','too_late_under_30s',
          'no_verified_joint_drain_during_covered_10m',
          'right_censored_or_provider_gap']
forward_ten_min={
    'as_of':now.isoformat(),
    'fixed_horizon_seconds':600,
    'minimum_lead_seconds':30,
    'telemetry_complete_definition':'At least 6 paired reserve samples; first and last <=90s from horizon edges; every gap <=90s',
    'first_alerts_one_per_pool_and_type':len(evaluated),
    'outcome_counts':{label:sum(e['outcome']==label for e in evaluated)
                      for label in outcomes},
    'censored_cases_are_not_true_negatives':True,
    'same_slot_drain_only_for_verified_outcome':True,
    'dataset_is_ascertained_by_live_sensor_not_all_pools':True,
    'not_out_of_sample_proven_or_profitable':True,
    'evidence':evaluated[:100]}
with open('evidence/onchain_precursor_10m_followup.json','w',encoding='utf-8') as research_file:
    json.dump(forward_ten_min,research_file,indent=2,allow_nan=False)
print('ONCHAIN_PRECURSOR_10M_FOLLOWUP',json.dumps(forward_ten_min,allow_nan=False))





