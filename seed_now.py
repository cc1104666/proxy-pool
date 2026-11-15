# -*- coding: utf-8 -*-
"""seed_now.py — 本机一次性灌池 (用 wrangler OAuth token, 含 workers_kv:write)"""
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
wt = re.search(r'oauth_token\s*=\s*"([^"]+)"', open(
    os.path.expanduser('~/AppData/Roaming/xdg.config/.wrangler/config/default.toml'),
    encoding='utf-8').read()).group(1)
aid = open(os.path.join(HERE, '..', 'trip', 'cloudflare', 'account_id.txt')).read().strip()
ns = '3a2948490081435fba76948cbef90ba4'
H = {'Authorization': f'Bearer {wt}', 'Content-Type': 'application/json'}

def kv_put(key, value):
    url = f'https://api.cloudflare.com/client/v4/accounts/{aid}/storage/kv/namespaces/{ns}/values/{key}'
    req = urllib.request.Request(url, data=value.encode('utf-8'), headers=H, method='PUT')
    r = json.loads(urllib.request.urlopen(req, timeout=60).read())
    if not r.get('success'):
        raise RuntimeError(f'{key}: {r}')

db = sqlite3.connect(os.path.join(HERE, 'proxies.db'))
db.row_factory = sqlite3.Row
stats_p, last_max = {}, ''
total = 0
for proto in ('http', 'https', 'socks4', 'socks5'):
    rows = db.execute(
        """SELECT addr, latency_ms, anonymity, country, last_alive, source FROM proxies
           WHERE protocol=? AND alive=1 ORDER BY (latency_ms IS NULL), latency_ms LIMIT 400""",
        (proto,)).fetchall()
    items = [dict(addr=r['addr'], latency_ms=r['latency_ms'], anonymity=r['anonymity'] or '',
                  country=(r['country'] or '')[:24], last_alive=r['last_alive'] or '',
                  source=r['source'] or '') for r in rows]
    kv_put(f'pool:{proto}', json.dumps(items, ensure_ascii=False))
    stats_p[proto] = dict(alive=len(items), total=db.execute(
        'SELECT COUNT(*) FROM proxies WHERE protocol=?', (proto,)).fetchone()[0])
    total += len(items)
    for r in rows:
        if r['last_alive'] and r['last_alive'] > last_max:
            last_max = r['last_alive']
    print(f'pool:{proto:6s} → {len(items)} 条')

stats = dict(protocols=stats_p, total_alive=total,
             updated=time.strftime('%Y-%m-%d %H:%M:%S'), updated_ago=0)
kv_put('stats', json.dumps(stats, ensure_ascii=False))
print(f'✓ 灌池完成: 存活 {total}')
