# -*- coding: utf-8 -*-
"""export_kv.py — 把本地 proxies.db 存活代理灌入 Cloudflare KV
用法: python export_kv.py
环境变量: CF_ACCOUNT_ID, CF_KV_NS, CF_TOKEN   (GitHub Actions secrets 注入)
KV keys: pool:http / pool:https / pool:socks4 / pool:socks5 / stats
"""
import json
import os
import sqlite3
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding='utf-8')
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'proxies.db')
PER_PROTO_CAP = 400          # 每协议最多导出条数 (KV 单值 25MB 上限内绰绰有余)

def main():
    aid = os.environ['CF_ACCOUNT_ID']
    ns = os.environ['CF_KV_NS']
    tok = os.environ['CF_TOKEN']
    H = {'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json'}

    def kv_put(key, value):
        url = f'https://api.cloudflare.com/client/v4/accounts/{aid}/storage/kv/namespaces/{ns}/values/{key}'
        req = urllib.request.Request(url, data=value.encode('utf-8'), headers=H, method='PUT')
        r = json.loads(urllib.request.urlopen(req, timeout=30).read())
        if not r.get('success'):
            raise RuntimeError(f'KV put {key}: {r}')

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    protocols, stats_p, last_alive_max = {}, {}, ''
    for proto in ('http', 'https', 'socks4', 'socks5'):
        rows = db.execute(
            """SELECT addr, latency_ms, anonymity, country, last_alive, source
               FROM proxies WHERE protocol=? AND alive=1
               ORDER BY (latency_ms IS NULL), latency_ms LIMIT ?""",
            (proto, PER_PROTO_CAP)).fetchall()
        items = [dict(addr=r['addr'],
                      latency_ms=r['latency_ms'],
                      anonymity=r['anonymity'] or '',
                      country=(r['country'] or '')[:24],
                      last_alive=r['last_alive'] or '',
                      source=r['source'] or '') for r in rows]
        protocols[proto] = items
        stats_p[proto] = dict(alive=len(items),
                              total=db.execute('SELECT COUNT(*) FROM proxies WHERE protocol=?',
                                               (proto,)).fetchone()[0])
        for r in rows:
            if r['last_alive'] and r['last_alive'] > last_alive_max:
                last_alive_max = r['last_alive']

    total_alive = sum(v['alive'] for v in stats_p.values())
    stats = dict(protocols=stats_p, total_alive=total_alive,
                 updated=time.strftime('%Y-%m-%d %H:%M:%S'), updated_ago=0)
    for proto, items in protocols.items():
        if not items:
            # 保护合并: 本轮该协议无存活 → 保留 KV 旧数据 (Actions 新库抓取有冷启动期)
            print(f'  pool:{proto:6s} → 0 条 (本轮无存活, 保留旧值)')
            continue
        kv_put(f'pool:{proto}', json.dumps(items, ensure_ascii=False))
        print(f'  pool:{proto:6s} → {len(items)} 条')
    kv_put('stats', json.dumps(stats, ensure_ascii=False))
    print(f'✓ KV 灌池完成: 存活 {total_alive}, 最近存活 {last_alive_max}')

if __name__ == '__main__':
    main()
