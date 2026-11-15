# -*- coding: utf-8 -*-
"""kv_restore.py — check_only/recheck 模式: 从 KV 恢复上轮存活数据到 db 供复测
(Actions 每次 checkout 都是空库; 上轮数据只在 KV 里)"""
import json
import os
import sys
import urllib.request

sys.stdout.reconfigure(encoding='utf-8')
from core import DB

AID = os.environ['CF_ACCOUNT_ID']
NS = os.environ['CF_KV_NS']
TOK = os.environ['CF_TOKEN']

db = DB()
total = 0
for proto in ('http', 'https', 'socks4', 'socks5'):
    url = f'https://api.cloudflare.com/client/v4/accounts/{AID}/storage/kv/namespaces/{NS}/values/pool:{proto}'
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {TOK}'})
    try:
        items = json.loads(urllib.request.urlopen(req, timeout=30).read())
    except Exception:
        continue  # KV 无此 key (冷启动) → 跳过
    rows = [(x['addr'], x['addr'].rsplit(':', 1)[0], int(x['addr'].rsplit(':', 1)[1]),
             proto, x.get('source') or 'kv-restore', '', '', '', '') for x in items]
    before = db.conn.execute('SELECT COUNT(*) FROM proxies').fetchone()[0]
    db.insert_proxies(rows)
    total += db.conn.execute('SELECT COUNT(*) FROM proxies').fetchone()[0] - before
# 标记复测队列: alive=1 且 last_checked_ts=0 → pick_workload 存活过期分支全量入选
db.conn.execute('UPDATE proxies SET alive=1, check_count=1, last_checked=0, last_checked_ts=0')
db.conn.commit()
print(f'✓ 从 KV 恢复 {total} 条存活代理 → 复测队列')
