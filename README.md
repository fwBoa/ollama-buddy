<div align="center">
  <img src="web/ollama.png" alt="Ollama Buddy" width="88">
  <h1>Ollama Buddy</h1>
  <p><em>Your Ollama Cloud spending on your Mac — cycle, quota estimate, live updates</em></p>

  [Français](README.fr.md) · [Install](#install) · [Monthly cycle](#monthly-cycle) · [Security](#security)
</div>

Ollama Buddy reads your account's daily cloud spending with your own API key. A
Python server serves a dashboard in a native macOS window, with a compact summary
in the menu bar. Running the server directly also works in a browser.

- Cycle spending in dollars, requests and the seven most recent days.
- Estimated monthly quota, reset countdown and projections once a full day of
  samples has accumulated.
- English/French and automatic/light/dark themes, shared between both views.
- Python 3.9+ standard library at runtime; no frontend framework or build step.

The screenshot below predates the latest cycle and model-history changes; labels
and figures in the current app differ.

<img src="docs/dashboard.png" alt="An earlier Ollama Buddy dashboard" width="100%">

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
if this app started it; an existing independently launched server stays running.

## Connect and configure

1. Create an API key at [ollama.com/settings/keys](https://ollama.com/settings/keys).
2. Paste it into the dashboard.
3. In **Settings**, enter **Next reset (UTC)**, including the time if known. Use
   the date displayed by Ollama, or the subscription anniversary converted to UTC.
4. Check **Monthly cap**. An explicit value overrides the local Ollama plan.
   Leaving it empty uses the built-in plan defaults: Pro $60, Max $300, Team $1,000.
   Unknown plans have no inferred cap. Confirm your own plan, especially legacy plans.

The cap values and subscription-day rule follow [Ollama's pricing FAQ](https://www.ollama.com/pricing),
checked on 12 October 2026. They can change; the manual cap remains available.

The top-right buttons refresh cloud data, cycle the theme and cycle the language.
Settings also offers a language selector. Both preferences are saved on the server.

| Shortcut | Action |
|---|---|
| `⌘1` | Show the window |
| `⌘R` | Reload the dashboard |
| `⌘⇧R` | Invalidate the cache and refresh |
| `⌘O` | Open in the browser |
| `⌘Q` | Quit |

The menu bar shows a llama and the estimated percentage, with an orange alert
at 70% and red at 90%. Unavailable usage shows a dash. Its summary shows the
last three days and, when available, a dated historical model reading.

## Monthly cycle

Ollama says included credits reset monthly on the subscription's original day of
the month, including annual subscriptions. Switching to the new pricing resets
usage immediately while keeping that original anniversary.

The app does **not** discover your subscription date or observe credit resets.
Its configured UTC timestamp is the monthly anchor. Both cycle boundaries are
calculated from that original anchor, so a day 31 becomes February 28/29 and
returns to day 31 in March. Old anchors are replayed without a one-year limit.
Clearing the date removes the cycle calculation.

The API integration requests `https://ollama.com/api/usage?range=30d` and expects
`buckets` with `from`, `usage_usd`, `request_count`, plus `totals`. This usage route
is undocumented and its response format can change. The source was adapted to
this format on 7 October 2026; it no longer reads `limits.monthly.usage`.

**The quota is an estimate, not Ollama's authoritative remaining credit balance.**
The app sums the available daily spending in the configured cycle and divides
by the cap. Buckets are UTC days: if the cycle starts during a day, that entire
partial first day is excluded because the API cannot split it at the reset time.
Changing the reset hour can therefore change the sum. Refunds, purchased credits,
plan switches and differing billing attribution are not reconstructed.

A 30-day response may not cover a 31-day cycle. If its earliest day is later than
the first full cycle day, the app withholds the quota instead of presenting a
truncated sum. A missing key, missing cap, missing cycle, invalid response or API
failure also hides the figures and gives a message in the dashboard.

Samples are recorded at most every five minutes. The average daily rate requires
at least 24 hours of samples from the current cycle. Projections extrapolate that
rate and can differ from future spending. Changing the cap or cycle clears those
samples so incompatible measurements are never mixed.

The API no longer supplies the old per-model breakdown expected by this project.
If SQLite contains an earlier model reading, the app shows its date and counts.
A fresh database has no model breakdown. Replacing/removing the key clears the
saved readings and colours, avoiding attribution of one account's history to another.
Replacing a key **does not revoke it on Ollama**; revoke it on Ollama's settings page.

## Security

- The server binds only to `127.0.0.1`, on port `11499` by default.
- Requests must use the actual local host and port. Foreign browser origins and
  cross-site requests are rejected, protecting against cross-site settings changes
  and DNS rebinding. Refresh is a POST action.
- Settings endpoints require a JSON object, bounded request size, known fields,
  finite non-negative amounts, a valid timestamp and supported preference values.
- The key is saved atomically in `config.json` with mode `0600` from file creation.
  It is stored **in plaintext**, not in macOS Keychain. Other software running as
  your user can read it; this is not isolation from local malware or processes.
- The key goes to `ollama.com` in the HTTPS Authorization header. It is never sent
  back to the dashboard. The account payload exposes only the plan, not the email.
- Dynamic model/error text is HTML-escaped. Pages carry a nonce-based script policy,
  deny framing and restrict their network requests to the local server.

These protections target browser-origin attacks. Local programs can still access
the HTTP API. The native bundle is not sandboxed or notarised.

## Development and data

| File | Role |
|---|---|
| `ollama_buddy.py` | Cloud/account reads, cycle calculation, SQLite, HTTP and SSE |
| `web/index.html` | Dashboard, settings and translations |
| `web/mini.html` | Menu bar summary |
| `app/main.swift` | Native lifecycle, windows, menus and navigation |
| `app/make_icon.py` | Pillow/iconutil icon generator |
| `build_app.sh` | Universal bundle assembly |
| `config.example.json` | Configuration shape; zero cap follows the plan |
| `tests/test_backend.py` | Cycle, validation, cache, storage and HTTP regression tests |
| `tests/test_frontend.cjs` | Browser regression tests with synthetic data; requires Playwright |

The native app stores `config.json` and `usage.db` in
`~/Library/Application Support/OllamaBuddy/`. Direct launches use the project
folder unless `OLLAMA_BUDDY_DATA` selects another directory. Secrets, databases
and build artefacts are ignored by Git.

SQLite stores `cloud_samples` and `model_color`, using WAL mode. Deleting the
base loses rate history and old model readings; only new global samples are
rebuilt. Startup also removes legacy tables that this version no longer uses.

The web views receive SSE pushes, with keepalives and reconnection. The dashboard
falls back to five-second polling if EventSource is unavailable. A watcher checks
every two seconds; shared caching limits cloud reads to roughly one per minute
except explicit refreshes. A minute tick updates the cycle/countdown even if
spending has not changed. The native menu bar separately reads the cached payload
every 20 seconds.

Run the backend tests without dependencies or a real Ollama account:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The browser suite is run with `node tests/test_frontend.cjs` when Playwright and
its Chromium browser are available. It uses a temporary data directory and mocked
Ollama responses. Rebuild the bundle after source edits to use the latest version.

MIT licence. This is an independent app, not an official Ollama client.
