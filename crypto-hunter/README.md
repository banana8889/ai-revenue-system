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
