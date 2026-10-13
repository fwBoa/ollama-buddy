<div align="center">
  <img src="web/ollama.png" alt="Ollama Buddy" width="88">
  <h1>Ollama Buddy</h1>
  <p><em>Your Ollama Cloud spending on your Mac — official credits, tokens, live updates</em></p>

  [Français](README.fr.md) · [Interface](#interface) · [Install](#install) · [Monthly cycle](#monthly-cycle) · [Security](#security)
</div>

Ollama Buddy reads your account's daily cloud spending with your own API key. A
Python server serves a dashboard in a native macOS window, with a compact summary
in the menu bar. Running the server directly also works in a browser.

- Overview with today’s spending and requests, monthly credits and a cycle preview.
- **Activity** view with an interactive current-cycle/7/30 calendar-day chart and every daily
  record in the available period. Missing records are distinct from zero spending.
- Dedicated settings for connection, monthly cycle, alerts and appearance.
  Unfinished input survives live updates.
- Official monthly allowance, remaining credits, reset date and purchased credits.
- Input/output/cached token counters for the current cycle or selected 7/30-day activity range.
- Estimated projections once a full day of balance samples has accumulated.
- Optional native notifications at 70%, 90% and 100% of the monthly allowance used.
- English/French and automatic/light/dark themes in Settings, shared between both views.
- Native API-key storage in macOS Keychain, with migration from the previous file.
- Python 3.9+ standard library at runtime; no frontend framework or build step.

## Interface

Redesign screenshots use synthetic data and the same server and pages as the app.

**Overview**: today’s spending and requests, plus monthly credits. The cycle
preview opens a selected day in Activity; it does not duplicate its table or tokens.

<img src="docs/dashboard.png" alt="Overview with an interactive daily chart and official monthly credits" width="100%">

<details>
<summary>Explore activity, settings, dark mode and the menu bar</summary>

### Activity

Activity starts at the latest renewal. Switch between the full current cycle
and 7/30 calendar days within it, including input, output and cached token counters. Missing records are
distinct from days with zero spending.

<img src="docs/activity.png" alt="Activity view with daily chart, token counters and daily records" width="100%">

### Settings

Configure connection, alerts and appearance. The cycle comes from Ollama;
optional fallback settings use a UTC reset reference with its local equivalent.

<img src="docs/settings.png" alt="Settings for connection, cap, UTC reset reference, alerts, theme and language" width="100%">

### Dark mode

<img src="docs/dashboard-dark.png" alt="Overview in the dark theme" width="100%">

### Menu bar

Check remaining credits, the next local renewal and today’s spending and requests,
then open the full dashboard. Zero purchased credits are hidden.

<img src="docs/menu-bar.png" alt="Menu bar panel showing official credits and daily spending" width="300">

</details>

## Install

To build the native app: macOS 12+, Xcode command line tools (`swiftc`, `lipo`,
`iconutil`, `codesign`), Python 3.9+ and Pillow for the icon.

```bash
xcode-select --install
pip3 install pillow

git clone https://github.com/fwBoa/ollama-buddy.git
cd ollama-buddy
./build_app.sh
open "Ollama Buddy.app"
```

The bundle contains both Apple Silicon and Intel executables. Add `--install` to
copy it into `/Applications`. The native launcher uses `/usr/bin/python3`, so its
runtime requires the Xcode command line tools too.

The app is signed ad-hoc, without Developer ID notarisation. A downloaded bundle
can be blocked by Gatekeeper; building locally is the recommended installation.
Only for a bundle you trust, removing its quarantine marker is possible with:

```bash
xattr -dr com.apple.quarantine "/Applications/Ollama Buddy.app"
```

To use the dashboard without the native wrapper:

```bash
python3 ollama_buddy.py
# Optional: --no-browser --port 11499
```

Closing the native window keeps the menu bar app running. `⌘Q` stops the server
if this app started it; a compatible pre-existing native server stays running. A plain CLI server is
kept separate so the native app can use Keychain storage. On launch, the app also
checks that a reused server supports daily tracking from renewal, Balance and token counters.

## Connect and configure

1. Create an API key at [ollama.com/settings/keys](https://ollama.com/settings/keys).
2. Paste it into the dashboard.
3. The app reads your allowance, remaining credits and exact cycle dates from
   [Ollama Balance](https://docs.ollama.com/api/balance), using the same key.
4. **Settings** retains an optional fallback cap and UTC reset reference. These
   apply only when Balance is unavailable, and the result is labelled estimated.
   With no manual cap, the fallback uses the local plan's default (Pro $60,
   Max $300, Team $1,000). It is not the authority for your actual allowance.

The top-right button refreshes cloud data. Theme and language selectors live
in Settings and default to the system preferences. Both are saved on the server.

Reset dates are shown once in local time with the timezone; their UTC reference
is available in a tooltip. Purchased
credits are displayed separately from included monthly credits. Disconnecting
requires confirmation and clears locally saved account history.

| Shortcut | Action |
|---|---|
| `⌘1` | Show the window |
| `⌘R` | Reload the dashboard |
| `⌘⇧R` | Invalidate the cache and refresh |
| `⌘O` | Open in the browser |
| `⌘Q` | Quit |

The menu bar shows a llama and the consumed percentage, with an orange alert
at 70% and red at 90%. Unavailable usage shows a dash. Its summary shows
today’s spending and requests. Old model counters are no longer shown.

## Monthly cycle

The source for the quota is [`GET /api/balance`](https://docs.ollama.com/api/balance).
It supplies `included.allowance_usd`, `included.balance_usd`, the exact period
`from`/`until`, and `purchased.balance_usd`. Included credits used are calculated
as allowance minus remaining balance. Purchased credits remain separate; they
are not included in that percentage. The official balance works without a manual
reset date or complete daily history, including on a reset day.

Legacy plans instead return session and weekly limits. The app displays those
remaining percentages, without inventing a monthly dollar quota or sending
monthly threshold notifications. A zero included allowance is also shown without
a made-up percentage. For teams, Balance is shared across members whereas the
activity endpoint uses the default personal scope (`self`).

[`GET /api/usage?range=30d`](https://docs.ollama.com/api/cloud-usage) supplies the
UTC daily chart and table. Activity starts on the latest renewal date from Balance (or the configured
cycle reference in fallback mode). Earlier daily records remain stored but are
hidden from charts, tables, token counters and the menu bar. The default **Current
cycle** chart can show all 31 days; 7/30-day choices stay within that cycle.
With no known cycle, available records from the last 30 days are shown. Spending
can draw on purchased credits and is not the same measure as included credits consumed. New usage may take time to appear on
Ollama. If the activity request fails, an available official balance remains visible.

Daily buckets cannot be split at an exact renewal time. If renewal occurs during
a UTC day, that first daily record is shown with a contextual label and a collapsed explanation that it may include
pre-renewal spending, requests and tokens. The official monthly balance still
uses the exact cycle boundary; the fallback quota excludes that partial day.

If Balance is unavailable, the fallback quota sums complete UTC days in the
manually configured cycle and divides by the cap. A partial reset day is excluded;
an incomplete cycle withholds this estimate. Both boundaries are replayed from
the original UTC anchor, preserving day 31 through February. Empty fallback
settings do not prevent the official balance from working.

Daily readings are retained in SQLite for 400 days. Complete stored days can fill
older API gaps, including 31-day cycles; partial leading buckets never overwrite
complete days. Switching keys clears the daily and balance sample history.

Balance samples are recorded at most every five minutes. **Estimated outlook**
requires at least 24 hours of samples with the same cycle start and allowance.
Projections remain estimates and do not reconstruct purchased-credit spending.
Fallback rate samples are kept separate from official balance samples.

### Token counters

**Activity** shows input, output and cached input tokens for the current cycle or selected 7/30-day
range, plus the selected day’s counters in a collapsed detail. Cached input is a subset of input tokens
and is never added again. Missing counters are shown as unavailable, not zero.
Old stored totals cannot reveal the split; migrations preserve them without
inventing values. A subsequent API reading fills in counters it actually supplies.

The API does not yet expose usage by model; Ollama lists it under
[Coming soon](https://docs.ollama.com/api/cloud-usage#coming-soon). Old per-model
archives are not displayed. Replacing a key does not revoke it on Ollama.

## Notifications

Enable **Notifications at 70%, 90% and 100%** in Settings in the native app.
The feature is off by default. macOS asks for notification permission, and the
page explains when system settings block notifications. No alert is generated
without a calculated quota. Keep the native app running for alerts to work.

The highest threshold reached is notified once per cycle. If usage jumps from
below 70% to 95%, one 90% alert is sent. Falling below and recrossing a notified
threshold, restarting the app or toggling alerts does not repeat it. A new cycle,
key, cap or reset anchor starts a new alert context. Clicking the alert opens the
dashboard. Alerts use the official balance when available, or the explicitly labelled fallback estimate.
Notifications are unavailable for a standalone CLI server.

## Security

- The server binds only to `127.0.0.1`, on port `11499` by default.
- Requests must use the actual local host and port. Foreign browser origins and
  cross-site requests are rejected, protecting against cross-site settings changes
  and DNS rebinding. Refresh is a POST action.
- Settings endpoints require a JSON object, bounded request size, known fields,
  finite non-negative amounts, a valid timestamp and supported preference values.
- The native app stores the key in **macOS Keychain**, through its signed native
  executable and Security framework. The key is passed through standard input,
  never command-line arguments. No third-party key-storage dependency is used.
- On native launch, an existing plaintext key is copied to Keychain before it is
  removed from `config.json`. If access fails, the file is kept and migration is
  retried rather than losing the key. macOS may ask you to authorize access again
  after rebuilding the ad-hoc signed app.
- Standalone CLI launches keep the private-file storage (`0600`) unless launched
  with `OLLAMA_BUDDY_KEYCHAIN_HELPER` pointing to the new native executable.
  CLI file storage remains plaintext. Keychain protects storage at rest; it does
  not make this app an isolation boundary against software running as your user.
- The key goes to `ollama.com` in the HTTPS Authorization header. It is never sent
  back to the dashboard. The account payload exposes only the plan, not the email.
- Dynamic error text is HTML-escaped. Pages carry a nonce-based script policy,
  deny framing and restrict their network requests to the local server.

These protections target browser-origin attacks. Local programs can still access
the HTTP API. The native bundle is not sandboxed or notarised.

## Product specification

[specs.md](specs.md) defines each feature, view, data source, expected state and
acceptance criteria. Overview is the quick summary; Activity owns exploration,
period totals, tokens and daily records. Detailed calculations and fallback
settings are collapsed by default.

## Development and data

| File | Role |
|---|---|
| `ollama_buddy.py` | Cloud/account reads, cycle calculation, SQLite, HTTP and SSE |
| `web/index.html` | Dashboard, settings and translations |
| `web/mini.html` | Menu bar summary |
| `app/main.swift` | Native lifecycle, windows, menus and navigation |
| `app/make_icon.py` | Pillow/iconutil icon generator |
| `build_app.sh` | Universal bundle assembly |
| `config.example.json` | Configuration shape; fallback cap; zero follows the local plan |
| `tests/test_backend.py` | Cycle, validation, cache, storage and HTTP regression tests |
| `tests/test_frontend.cjs` | Browser regression tests with synthetic data; requires Playwright |
| `tests/capture_ui.cjs` | English/French README screenshots using synthetic data |

The native app stores `config.json` and `usage.db` in
`~/Library/Application Support/OllamaBuddy/`. Direct launches use the project
folder unless `OLLAMA_BUDDY_DATA` selects another directory. Secrets, databases
and build artefacts are ignored by Git.

SQLite stores `daily_usage`, `balance_samples` and `cloud_samples`, using WAL mode. Legacy model
rows/colours may remain in older databases but have no display. Deleting the
base loses the saved daily history and rate samples; only what is still available
from the API can be recovered. Startup also removes unused legacy tables.

The web views receive SSE pushes, with keepalives and reconnection. The dashboard
falls back to five-second polling if EventSource is unavailable. A watcher checks
every two seconds; shared caching limits reads to each cloud endpoint to roughly one per minute
except explicit refreshes. A minute tick updates the cycle/countdown even if
spending has not changed. The native menu bar separately reads the cached payload
every 20 seconds.

Run the backend tests without dependencies or a real Ollama account:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The browser suite is run with `node tests/test_frontend.cjs` when Playwright and
its Chromium browser are available. It uses a temporary data directory, mocked
Ollama responses and a simulated Keychain, without touching real credentials.

Regenerate the English and French README screenshots with the same dependencies:

```bash
node tests/capture_ui.cjs
```

Screenshots also use synthetic data without accessing your account or Keychain.
The script only replaces the demonstration PNGs in `docs/`.

After building, the native threshold/deduplication checks can be run with:

```bash
"Ollama Buddy.app/Contents/MacOS/OllamaBuddy" --test-alerts
```

Real Keychain access and notification permission are completed by macOS on native
launch/opt-in. Rebuild the bundle after source edits to use the latest version.

[MIT licence](LICENSE). This is an independent app, not an official Ollama client.
