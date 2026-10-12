"""Synthetic backend for browser tests; never reads a real account or key."""
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ollama_buddy as buddy

os.environ['OLLAMA_BUDDY_KEYCHAIN_HELPER'] = 'synthetic-helper'
os.environ['OLLAMA_BUDDY_NATIVE'] = '1'
secret = {'value': 'fixture-secret'}
def fake_keychain(operation, value=''):
    if operation == 'set': secret['value'] = value
    if operation == 'delete': secret['value'] = ''
    return secret['value'] if operation == 'get' else ''
buddy.keychain = fake_keychain
now = datetime.now(timezone.utc)
anchor = (now - timedelta(days=11)).replace(hour=0, minute=0, second=0, microsecond=0)
buckets = [{"from": (now - timedelta(days=offset)).date().isoformat() + "T00:00:00Z",
            "usage_usd": round([.18, .72, 1.46, 0, .38, 2.14, .94][offset % 7], 2),
            "request_count": [1, 4, 7, 0, 2, 9, 5][offset % 7], "input_tokens": 4, "output_tokens": 5}
           for offset in range(30, -1, -1)]
data = {"buckets": buckets, "totals": {
    "usage_usd": sum(day["usage_usd"] for day in buckets),
    "request_count": sum(day["request_count"] for day in buckets)}}
buddy.ollama_account = lambda: {"plan": "pro", "email": "never-expose@example.test"}
buddy.cloud_usage = lambda key: data
state = buddy.State({**buddy.DEFAULT_CONFIG, "ollama_api_key": "fixture-secret",
                     "quota_reset_at": int(anchor.timestamp() * 1000)})
state.conn.execute("INSERT INTO cloud_samples VALUES (?,?,?,?)",
                   (int(now.timestamp() * 1000), '<img src=x onerror="window.injected=true">', 5, .2))
state.conn.commit()
buddy.Handler.state = state
server = buddy.ThreadingHTTPServer(("127.0.0.1", 0), buddy.Handler)
print(json.dumps({"url": f"http://127.0.0.1:{server.server_address[1]}",
                  "anchor": anchor.isoformat(), "today": now.date().isoformat()}), flush=True)
server.serve_forever()
