# -*- coding: utf-8 -*-
"""export_kv.py — 把本地 proxies.db 存活代理灌入 Cloudflare KV (本机/Actions 通用)
环境变量: CF_ACCOUNT_ID, CF_KV_NS, CF_TOKEN  (本机无 CF_TOKEN 时自动用 wrangler OAuth token)
KV keys: pool:http / pool:https / pool:socks4 / pool:socks5 / stats
stats 结构与本地面板 server.py /api/stats 对齐 (total/alive/by/untested/avg_latency_ms/best_speed/...)
"""
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, 'proxies.db')
PER_PROTO_CAP = 400          # 每协议最多导出条数 (KV 单值 25MB 上限内绰绰有余)
PROTOS = ('http', 'https', 'socks4', 'socks5')
SELECT_FULL = """SELECT addr, latency_ms, speed_bps, anonymity, country, country_code,
                        city, isp, exit_ip, check_count, fail_count, last_alive, source
                 FROM proxies WHERE protocol=? AND alive=1
                 ORDER BY (latency_ms IS NULL), latency_ms LIMIT ?"""

def _token():
    tok = os.environ.get('CF_TOKEN')
    if tok:
        return tok
    cfg = os.path.expanduser('~/AppData/Roaming/xdg.config/.wrangler/config/default.toml')
    return re.search(r'oauth_token\s*=\s*"([^"]+)"', open(cfg, encoding='utf-8').read()).group(1)

def _account_id():
    aid = os.environ.get('CF_ACCOUNT_ID')
    if aid:
        return aid
    try:
        return open(os.path.join(HERE, '..', 'trip', 'cloudflare', 'account_id.txt')).read().strip()
    except Exception:
        import urllib.request as u
        r = json.loads(u.urlopen(u.Request('https://api.cloudflare.com/client/v4/accounts',
                                           headers={'Authorization': f'Bearer {_token()}'}), timeout=20).read())
        return r['result'][0]['id']

AID = _account_id()
NS = os.environ.get('CF_KV_NS') or '3a2948490081435fba76948cbef90ba4'
H = {'Authorization': f'Bearer {_token()}', 'Content-Type': 'application/json'}

def kv_put(key, value):
    url = f'https://api.cloudflare.com/client/v4/accounts/{AID}/storage/kv/namespaces/{NS}/values/{key}'
    req = urllib.request.Request(url, data=value.encode('utf-8'), headers=H, method='PUT')
    r = json.loads(urllib.request.urlopen(req, timeout=60).read())
    if not r.get('success'):
        raise RuntimeError(f'KV put {key}: {r}')

def get_my_ip():
    try:
        j = json.loads(urllib.request.urlopen(urllib.request.Request(
            'http://ip-api.com/json/?fields=query', headers={'User-Agent': 'curl/8'}), timeout=8).read())
        return j.get('query') or ''
    except Exception:
        return ''

def to_item(r):
    return dict(addr=r['addr'], latency_ms=r['latency_ms'], speed_bps=r['speed_bps'],
                anonymity=r['anonymity'] or '', country=(r['country'] or '')[:24],
                country_code=r['country_code'] or '', city=(r['city'] or '')[:20],
                isp=(r['isp'] or '')[:40], exit_ip=r['exit_ip'] or '',
                check_count=r['check_count'], fail_count=r['fail_count'],
                last_alive=r['last_alive'] or '', source=r['source'] or '')

def main():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    stats_p, last_alive_max = {}, ''
    for proto in PROTOS:
        rows = db.execute(SELECT_FULL, (proto, PER_PROTO_CAP)).fetchall()
        stats_p[proto] = dict(alive=len(rows),
                              total=db.execute('SELECT COUNT(*) FROM proxies WHERE protocol=?',
                                               (proto,)).fetchone()[0])
        for r in rows:
            if r['last_alive'] and r['last_alive'] > last_alive_max:
                last_alive_max = r['last_alive']

    total_all = db.execute('SELECT COUNT(*) FROM proxies').fetchone()[0]
    alive_all = db.execute('SELECT COUNT(*) FROM proxies WHERE alive=1').fetchone()[0]
    untested = db.execute('SELECT COUNT(*) FROM proxies WHERE check_count=0').fetchone()[0]
    avg_lat = db.execute('SELECT AVG(latency_ms) FROM proxies WHERE alive=1 AND latency_ms IS NOT NULL').fetchone()[0]
    best = db.execute("""SELECT addr, protocol, speed_bps, latency_ms, country FROM proxies
                         WHERE alive=1 AND speed_bps IS NOT NULL ORDER BY speed_bps DESC LIMIT 1""").fetchone()
    best_speed = (dict(addr=best['addr'], protocol=best['protocol'],
                       speed_kbps=round(best['speed_bps'] / 1024, 1),
                       latency_ms=best['latency_ms'], country=best['country']) if best else None)
    scrape_log = [dict(ts=r['ts'], source=r['source'], found=r['found'], new=r['new'])
                  for r in db.execute('SELECT ts, source, found, new FROM scrape_log ORDER BY ts DESC LIMIT 14')]
    meta = {r['key']: r['value'] for r in db.execute("SELECT key, value FROM meta WHERE key='last_check_round'")}

    stats = dict(protocols=stats_p, total=total_all, alive=alive_all, by=stats_p,
                 untested=untested, avg_latency_ms=round(avg_lat) if avg_lat else None,
                 best_speed=best_speed, my_ip=get_my_ip(),
                 last_scrape=scrape_log[0]['ts'] if scrape_log else None,
                 last_check_round=meta.get('last_check_round'),
                 scrape_log=scrape_log, total_alive=alive_all,
                 updated=time.strftime('%Y-%m-%d %H:%M:%S'), updated_ago=0)

    for proto in PROTOS:
        rows = db.execute(SELECT_FULL, (proto, PER_PROTO_CAP)).fetchall()
        if not rows:
            # 保护合并: 本轮该协议无存活 → 保留 KV 旧数据 (Actions 新库冷启动期)
            print(f'  pool:{proto:6s} → 0 条 (本轮无存活, 保留旧值)')
            continue
        kv_put(f'pool:{proto}', json.dumps([to_item(r) for r in rows], ensure_ascii=False))
        print(f'  pool:{proto:6s} → {len(rows)} 条')
    kv_put('stats', json.dumps(stats, ensure_ascii=False))
    print(f'✓ KV 灌池完成: 存活 {alive_all}, 最近存活 {last_alive_max}')

if __name__ == '__main__':
    main()
