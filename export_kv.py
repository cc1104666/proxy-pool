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
TOK = _token()
H = {'Authorization': f'Bearer {TOK}', 'Content-Type': 'application/json'}

def kv_put(key, value):
    url = f'https://api.cloudflare.com/client/v4/accounts/{AID}/storage/kv/namespaces/{NS}/values/{key}'
    req = urllib.request.Request(url, data=value.encode('utf-8'), headers=H, method='PUT')
    r = json.loads(urllib.request.urlopen(req, timeout=60).read())
    if not r.get('success'):
        raise RuntimeError(f'KV put {key}: {r}')

def kv_get(key):
    """读 KV 现值 (不存在返回 None)"""
    url = f'https://api.cloudflare.com/client/v4/accounts/{AID}/storage/kv/namespaces/{NS}/values/{key}'
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {TOK}'})
    try:
        return urllib.request.urlopen(req, timeout=30).read().decode('utf-8')
    except Exception:
        return None

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

    total_all = db.execute('SELECT COUNT(*) FROM proxies').fetchone()[0]
    if total_all == 0:
        # check_only/recheck 冷启动轮(未抓取+恢复失败): 空库 → 跳过导出, 保护 KV
        print('空库 → 跳过导出 (保护 KV 现有数据)')
        return

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
    val_errors = [dict(err=(r['last_error'] or '?')[:80], n=r['n'])
                  for r in db.execute("""SELECT last_error, COUNT(*) AS n FROM proxies
                                         WHERE last_error IS NOT NULL AND last_error != ''
                                         GROUP BY last_error ORDER BY n DESC LIMIT 5""")]

    stats = dict(protocols=stats_p, total=total_all, alive=alive_all, by=stats_p,
                 untested=untested, avg_latency_ms=round(avg_lat) if avg_lat else None,
                 best_speed=best_speed, my_ip=get_my_ip(),
                 last_scrape=scrape_log[0]['ts'] if scrape_log else None,
                 last_check_round=meta.get('last_check_round'),
                 scrape_log=scrape_log, val_errors=val_errors, total_alive=alive_all,
                 updated=time.strftime('%Y-%m-%d %H:%M:%S'), updated_ago=0)

    # ---- 合并导出: 新数据 ∪ KV 现有存活 − 本轮验证失败 → 数据只增不减 ----
    merged_counts = {}
    for proto in PROTOS:
        new_items = [to_item(r) for r in db.execute(SELECT_FULL, (proto, PER_PROTO_CAP)).fetchall()]
        new_map = {x['addr']: x for x in new_items}
        old_raw = kv_get(f'pool:{proto}')
        old_map = {x['addr']: x for x in (json.loads(old_raw) if old_raw else [])}
        # 本轮明确验证失败的 addr (Actions 独立库: alive=0 即本轮复测/探测结论)
        dead = {r['addr'] for r in db.execute(
            'SELECT addr FROM proxies WHERE protocol=? AND alive=0', (proto,))}
        merged = dict(old_map)
        merged.update(new_map)          # 新数据优先 (延迟/带宽等字段最新鲜)
        for a in dead:                  # 本轮死掉的剔除 (无论新旧)
            merged.pop(a, None)
        items = sorted(merged.values(),
                       key=lambda x: (x.get('latency_ms') is None, x.get('latency_ms') or 99999))
        items = items[:PER_PROTO_CAP]
        if not items:
            if not old_map:
                print(f'  pool:{proto:6s} → 0 条 (无新无旧, 跳过)')
            else:
                kv_put(f'pool:{proto}', json.dumps([], ensure_ascii=False))
                print(f'  pool:{proto:6s} → 0 条 (全部失活, 清空)')
            merged_counts[proto] = 0
            stats_p[proto] = dict(alive=0, total=stats_p[proto]['total'])
            continue
        kv_put(f'pool:{proto}', json.dumps(items, ensure_ascii=False))
        merged_counts[proto] = len(items)
        stats_p[proto] = dict(alive=len(items), total=max(stats_p[proto]['total'], len(items)))
        print(f'  pool:{proto:6s} → {len(items)} 条 (本轮验证新{len(new_map)} 旧保留{len(items) - len(new_map)} 剔除失活{len(dead)})')
    alive_merged = sum(merged_counts.values())
    kv_put('stats', json.dumps(
        {**stats, 'alive': alive_merged, 'total_alive': alive_merged,
         'by': stats_p, 'protocols': stats_p}, ensure_ascii=False))
    print(f'✓ KV 灌池完成 (合并口径): 存活 {alive_merged}')

if __name__ == '__main__':
    main()
