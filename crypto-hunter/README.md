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
