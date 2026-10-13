# Crypto Hunter — prospective live research

Repository: banana8889/ai-revenue-system
Default branch: main

## Authoritative code and automation
- Scanner observer: [forward_observer.py](./forward_observer.py)
- GitHub Actions workflow: [crypto-hunter-prospective-study.yml](../.github/workflows/crypto-hunter-prospective-study.yml)
- Workflow history: https://github.com/banana8889/ai-revenue-system/actions/workflows/crypto-hunter-prospective-study.yml
- Public code, never store private keys or signing material here.

## Continuous operation
Scheduled research on the default branch every 10 minutes, with three live samples per run separated by 65 seconds. GitHub schedule is best-effort, not a resident process or a 24/7 latency guarantee. There is no fixed end date in the workflow. GitHub may delay/drop a scheduled event and may automatically disable public repository schedules after 60 days without repository activity.

Each healthy run must produce a LIVE_STUDY_HEARTBEAT, a JSON summary, state.json, and observations.jsonl; evidence is uploaded to a 30-day GitHub artifact. State restoration uses the most recent matching GitHub Actions cache, never treat artifacts alone as proof of an active runtime. A successful run must show nonempty live market samples and no orders.

## Strict research-only policy
- No signature, trades, token purchases, wallet secrets, transaction broadcast, or historical performance claims.
- Prospective signals must be timestamped before later price measurements.
- Do not confuse assumed costs or pool last-trade prices with executable buy/sell quotes.
- No observed signals or unverified fills mean NO PROVEN PROFITABILITY.
- Do not use the user-supplied historical private key exposed in chat.

## Future session verification
1. Query the GitHub Actions run history and filter by `event=schedule`; verify actual recent completed scheduled runs. Do not infer this from the YAML alone.
2. Read the newest job log: `LIVE_STUDY_HEARTBEAT` and `FORWARD_OBSERVATION_RECEIPT`. Validate increasing sample counts and new timestamps.
3. Verify the next run actually restores `crypto-prospective-state-...`, not merely writes it.
4. Check generated artifacts for raw observations and state continuity. Inspect errors and whether there are qualified entry/exit research signals.
5. If no scheduled run for >30 minutes, investigate workflow enablement and GitHub's schedule delivery rather than claiming that it is alive.
6. Improve true fill/exit verification using fresh live DEX aggregator quotes, and preserve strict prospective evaluation.

Verified (2026-10-13 11:24 UTC): GitHub push-triggered run 38389583041 restored prior cache state and tracked 129 unique pools with 3 new live samples, 60 pool observations, zero source errors and zero qualified entry/exit signals; zero transactions. Source improved afterward in commit 939641b... with three discovery pages, transient rate-limit retries, and a 5-minute maximum gap between qualifying observations. The update run is 38391696957; check its final conclusion and receipt before granting any new performance credit. As of 11:39 UTC the GitHub API still showed zero schedule-triggered runs despite successful push runs. Trigger registration and post-chat continuity are NOT VERIFIED. Do not call the system continuously alive until independent scheduled runs appear. Retain the user requirement to investigate and repair this, not just report it.

## Most recent verified empirical result (2026-10-13 11:47:56 UTC)
GitHub run 38392362653 PASSED and restored previous state. 331 unique pools tracked, 3 new live samples, 154 pool observations, 5 accumulated prospective research signals, 1 modeled exit, 0 instances of verified 11x gains, 0 broadcast orders or trades. One pool (Jellyfish on PumpSwap) fell between observations and registered modeled net_multiple_assumed=0.0790636 (approximately -92.1%); this is NOT an executable fill or realized result. The stop threshold did not demonstrate an achievable exit. Three same-name Jellyfish pools were independently verified to represent three distinct Solana mint addresses; never deduplicate by symbol. After this observation an exploratory forward-only entry filter was added requiring positive price change and no more than 10% reduction in liquidity between fresh independent samples. Do NOT retroactively apply the new filter to old observations or claim optimized profitability. Validate source commit 8f9b626a in a completed run before marking it verified.

GitHub schedule remains UNVERIFIED: the repository Actions API returned zero runs with event=schedule through the check after 11:47 UTC. Completed push-triggered runs DO NOT prove unattended recurrence; if none appear, investigate scheduler activation with repository settings or a reliable free external scheduler and preserve all evidence. Never use private keys in GitHub Actions. The study is research-only, not the full legacy 2.3 trading-oriented scanner.

## Operational verification update (2026-10-13 11:54 UTC)
Run 38392898527: PASS, restored cache, 409 unique pools tracked, 3 new live samples, 169 pool observations, 8 cumulative prospective mint-deduplicated research signals, 1 modeled exit, 0 verified 11x, 0 signed orders, 0 trades, 0 source errors. The modeled exit at 0.079x is evidence of severe gap risk, not an actual fill. Workflow run 38393370523 called GitHub Actions enable endpoint using existing GitHub token: state was ACTIVE before and ACTIVE after; activation job PASS. Crucially schedule-triggered run count remained ZERO at the verified check. Do not describe automatic execution after conversation as verified until event=schedule appears with successful runs and the expected checkpoint restoration. Workflow still uses GitHub's best-effort cron. Latest prospective observer source commit 8f9b626a uses an exploratory rising-price/liquidity gate; triggered build 38392898527 verified it. Workflow source pinned to github.sha in commit 7d1b163c; verify its full downstream run separately. No wallet keys and no real trades.

## Latest evidence (2026-10-13 11:55:12 UTC)
GitHub run 38393078134 completed successfully from the commit-pinned workflow. It restored checkpoint 38392898527, observed 155 pool records across three fresh live samples, saved 493 unique tracked pools and nine cumulative forward-only signals, and recorded one modeled exit. Zero verified net 11x events, zero orders, zero trades, zero source errors. Signal gate now requires positive price momentum and non-collapsing liquidity; this is a prospective filter, not proof of effectiveness. GitHub activation run 38393370523 independently observed the workflow ACTIVE before and after invoking enable; no event=schedule run had yet appeared. At the next session query both actual GitHub scheduled runs and newest artifacts; avoid claiming unattended activity without a recent scheduled receipt. Older recorded failed Jellyfish simulated exit remains approximately 0.079x modeled net, not an executable exit.

## 2026-10-13 autonomous research loop recovery (verified at 12:55 UTC)

GitHub Actions `schedule` events still did not appear for approximately one hour after the workflow became active. The GitHub-hosted runner can, however, self-initiate an existing `workflow_dispatch` with the repository's short-lived `GITHUB_TOKEN` and Actions write permission; no external credentials or wallet keys are used.

- Diagnostic dispatch workflow run 38400114664 succeeded, creating research run 38400124532 (`event=workflow_dispatch`). This run restored checkpoint 38393078134 and completed with **592 tracked pools, 10 prospective signals, 1 modeled exit, 0 verified 11x, 0 orders**.
- An opt-in chain mode was added to the existing Crypto Hunter Prospective Study workflow. Bootstrap run 38400395100 succeeded, creating research run 38400405185 with `inputs.chain=true`.
- The **self-trigger test passed**: research run 38400405185 completed successfully and itself dispatched research run 38400907753, without any intervening user message or assistant call. This confirms autonomous cross-run dispatch, not just manually bootstrapped execution.
- After proof, the short 25-second verification pause was increased to a six-minute cooldown in commit `8b8ac375`, yielding approximately 9–10 minutes between self-dispatched research cycles (3 minutes of market sampling plus cooldown). This revision must also be verified in a completed successor run before claiming the exact cadence.
- A `workflow_dispatch` without `inputs.chain=true`, ordinary `push`, or `schedule` event does NOT start another chain. Self-dispatch occurs only after successful study verification; GitHub concurrency prevents simultaneous study jobs. The step checks for other active `workflow_dispatch` runs before spawning its successor, reducing duplicate chains.
- This is still a GitHub-hosted periodic process, **not a permanent resident process or guaranteed uptime**. A failed job, permissions change, repository suspension, GitHub outage, API limits, or disabled Actions may stop the chain. Public-repository Actions usage is subject to GitHub limits and policies.
- No live trades, signing, wallet credentials, or unproven gain claims. A previously exposed private key must never be used.

Future status check: filter workflow runs by `event=workflow_dispatch`, compare successive run timestamps, then inspect each receipt for nonempty market samples, latest persisted state, independent candidate signals, modeled exits, and absence of private-key handling. If stale, inspect job failure and resume via a permitted `workflow_dispatch` rather than claiming it continued.

## Evidence update — 2026-10-13 13:05 UTC

**Autonomous handoffs independently verified twice**:
- workflow_dispatch run 38400405185 emitted a successful `CHAIN_NEXT_REQUEST_ACCEPTED=true`, and its successor 38400907753 started autonomously.
- workflow_dispatch run 38400907753 completed and independently dispatched run 38401325741. The latter used chain-enabled source commit e5cb5fc, with a six-minute cooldown added in workflow commit 8b8ac375. It entered the cooldown phase after saving its study state. The next long-interval handoff remained to be verified when this note was written.
- Observed study run 38400405185: 688 cumulative tracked pools, 10 cumulative prospective signals, one legacy modeled exit, zero verified 11x, zero real trades. The successor studies retain historic signals unchanged.
- Jupiter Swap V2 keyless quote-only test 38401352505 PASS: 5 USDC -> SOL -> USDC quoted 4.998944 USDC returned before network/rent/MEV, while both examined young tokens 88FaFkE... and FFfNkzx... returned HTTP 400 "Failed to get quotes" on their entry quote. Do not interpret that failure as a global inability to trade on the underlying DEXes. Jupiter router is not necessarily the same as the directly selected AMM.
- Observer commit 65a26d2 now requires a successful read-only 5 USDC Jupiter buy AND same-amount sell-back route and no worse than 15% immediate roundtrip quote loss for NEW prospective signals. Prior 10 signals remain legacy, not retroactively upgraded. Observer commit b7a66e1 additionally uses fresh exact-token-amount Jupiter sell quotes for NEW monitored positions, with per-run budgets and fail-closed no-quote handling. These revisions passed `python -m py_compile` and read-only safety assertions in GitHub Actions run 38402089534, but their **live market execution path still requires a completed prospective study with candidate quotes** before claiming real-world verification.
- The workflow uses no taker, private key, wallet signer, order execution, or trading funds. All simulated quote profits are hypothetical; transaction fees, priority fees, rent, slippage, MEV, transaction landing, fraud/rug risks and stale-quote risk can further reduce or erase them.
- Source and workflow commit hashes are in Actions logs. For future sessions inspect actual succeeding `workflow_dispatch` chain runs, logs, durable cache restore/save, and candidate quote marks. A chain failing once is not guaranteed to recover without an external watchdog.

## Recovery of long-interval autonomous chain (2026-10-13 13:10 UTC)

Important correction to the previous entry: workflow_dispatch run **38401325741 was CANCELLED** before it could dispatch its successor. Root cause: the research job had `timeout-minutes: 7` while it spent ~3 minutes collecting data and then waited 6 additional minutes for its next cycle. **Do not count that run as successful chain continuity.**

Fixes implemented:
1. Study workflow job timeout raised to 20 minutes in commit `bf1f5c41`, enough for a 3-minute research cycle, the quote-only checks, and the six-minute cooldown.
2. Removed study `push` event in commit `a30157ae` so code/documentation edits don't start extra overlapping research workers. Only `workflow_dispatch` with `inputs.chain=true` self-chains, with standard `schedule` also left enabled as an independent best-effort fallback.
3. Bootstrap GitHub Action run **38402454610** completed successfully, re-dispatching new research run **38402474318** on the corrected timeout. Inspect this run for research success and a follow-up automatically dispatched run before declaring the long-interval recovery proven.
4. Recovery Guard workflow added in commit `532da00a`: on failed/cancelled `workflow_dispatch` study, inspect most recent completed outcomes, do not retry after three consecutive failures, do not dispatch if another study is active, otherwise retry once after a 45-second backoff. Guard's push-time syntax/permissions test **38402722502** passed; a real failover recovery is not yet exercised.
5. Jupiter exact-size roundtrip entry preflight added in commit `65a26d2` and quote-only exit marking for new paper positions in commit `b7a66e18`. Source syntax/no-execution test **38402089534** passed, while an actual new Jupiter-verified candidate paper entry/exit is NOT YET VERIFIED. Historical chart-only signals remain legacy. A failed quote cannot be counted as a successful hypothetical fill.

No real swaps, signatures, private keys, or user funds. The chain is best-effort GitHub-hosted automation, not a resident always-on scanner. Validate two successive completed `workflow_dispatch` runs separated by the intended long cooldown, cache continuity, sample work counters, entry quote checks, and guard behavior. User requested continued paper-only testing before any real money.

## Last completed successful read-only study before timeout recovery (2026-10-13 12:58:38 UTC)
Run `38400907753` completed successfully and self-dispatched `38401325741`. Verified receipt: **773 tracked pools, 12 cumulative *legacy* graph-only paper entry signals, one legacy modeled exit, zero modeled 11x gains, zero actual orders/trades, zero source errors**. Three new samples and 172 pool observations. All these signals predate the Jupiter route-verification gate and must NOT be upgraded to executable trades after the fact. The immediately following long-wait run `38401325741` was cancelled by the 7-minute limit, prompting the 20-minute job-timeout repair and new chain `38402474318`. No verified proof yet of a successful long-cooldown successor after that repair as of this note.
