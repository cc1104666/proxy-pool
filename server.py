# -*- coding: utf-8 -*-
"""server.py — 本地代理池服务 (JSON API + 实时监控面板, 纯标准库)"""
import json
import threading
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import core
from core import DB

DASH = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>🛰️ 代理池实时监控</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#0b1020;--card:#131a2e;--line:#232c4a;--tx:#dbe4ff;--dim:#8492c0;--ok:#2ecc71;--warn:#f39c12;--bad:#e74c3c;--acc:#4f8cff}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font:14px/1.5 "Segoe UI",system-ui,sans-serif;padding:18px}
h1{font-size:20px;margin-bottom:4px}
.sub{color:var(--dim);font-size:12px;margin-bottom:14px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px}
.card .v{font-size:22px;font-weight:700}
.card .k{color:var(--dim);font-size:11px}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.acc{color:var(--acc)}
.bar{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-bottom:14px;display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:12px}
select,input{background:#0e1530;color:var(--tx);border:1px solid var(--line);border-radius:6px;padding:5px 8px;font-size:12px}
button{background:var(--acc);color:#fff;border:0;border-radius:6px;padding:6px 12px;font-size:12px;cursor:pointer}
button:hover{filter:brightness(1.15)}
table{width:100%;border-collapse:collapse;background:var(--card);border-radius:10px;overflow:hidden}
th{position:sticky;top:0;background:#0e1530;color:var(--dim);font-size:11px;text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);cursor:pointer;white-space:nowrap}
td{padding:6px 10px;border-bottom:1px solid #1a2240;font-size:12px;white-space:nowrap}
tr:hover td{background:#18224a}
.pill{display:inline-block;padding:1px 8px;border-radius:20px;font-size:10px;border:1px solid}
.spin{display:inline-block;width:12px;height:12px;border:2px solid var(--acc);border-top-color:transparent;border-radius:50%;animation:sp 1s linear infinite;vertical-align:-2px}
@keyframes sp{to{transform:rotate(360deg)}}
#scrapes{font-size:11px;color:var(--dim);margin-top:12px;max-height:110px;overflow:auto;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 12px}
a{color:var(--acc);text-decoration:none}
</style></head><body>
<h1>🛰️ 代理池实时监控 <span id="state"></span></h1>
<div class="sub">抓取 → 入库 → 探活 → 验证 → 测速 全自动流水线 · 每 5 秒自动刷新 · 本机出口 IP: <span id="myip">…</span></div>
<div class="cards" id="cards"></div>
<div class="bar">
  协议 <select id="f_proto"><option value="all">全部</option><option>http</option><option>socks4</option><option>socks5</option></select>
  状态 <select id="f_alive"><option value="1">存活</option><option value="all">全部</option><option value="0">失败</option></select>
  排序 <select id="f_sort"><option value="latency">延迟最快</option><option value="speed">带宽最大</option><option value="recent">最近存活</option><option value="created">最新抓取</option></select>
  <input id="f_q" placeholder="搜索 地址/国家/ISP/出口IP">
  <button onclick="load()">查询</button>
  <button onclick="doScrape()">立即抓取</button>
  <button onclick="doCheckAlive()">测速全部存活</button>
  <a id="exp" href="#">⬇ 导出存活列表</a>
  <span id="round"></span>
</div>
<table><thead><tr>
<th data-s="protocol">协议</th><th data-s="addr">地址</th><th data-s="latency_ms">延迟</th><th data-s="speed_bps">带宽</th>
<th>匿名</th><th>国家/城市</th><th>ISP</th><th>出口IP</th><th>测次</th><th>最近存活</th><th>操作</th>
</tr></thead><tbody id="tb"><tr><td colspan="11">加载中…</td></tr></tbody></table>
<div id="scrapes"></div>
<script>
let curSort='latency_ms',curOrder='asc';
const esc=s=>s==null?'':String(s);
const flag=cc=>cc&&cc.length==2?String.fromCodePoint(...[...cc.toUpperCase()].map(c=>0x1F1E6+c.charCodeAt(0)-65)):'';
const latCls=v=>v==null?'':v<300?'ok':v<900?'warn':'bad';
async function load(){
  const p=document.getElementById('f_proto').value, a=document.getElementById('f_alive').value,
        q=encodeURIComponent(document.getElementById('f_q').value),
        sortMap={latency_ms:'latency',speed_bps:'speed',last_alive_ts:'recent',created_ts:'created'},
        url=`/api/proxies?protocol=${p}&alive=${a}&q=${q}&sort=${sortMap[curSort]}&order=${curOrder}&limit=300`;
  const [pr,dr]=await Promise.all([fetch(url),fetch('/api/stats')]);
  const d=await pr.json(), st=await dr.json();
  const b=st.by||{};const kb=v=>v==null?'—':(v/1024).toFixed(1)+' KB/s';
  document.getElementById('cards').innerHTML=[
    ['总入库',st.total,'acc'],['存活可用',st.alive,'ok'],['HTTP',b.http?.alive+' / '+b.http?.total,''],
    ['SOCKS4',b.socks4?.alive+' / '+b.socks4?.total,''],['SOCKS5',b.socks5?.alive+' / '+b.socks5?.total,''],
    ['平均延迟',st.avg_latency_ms!=null?st.avg_latency_ms+'ms':'—',''],
    ['最快带宽',st.best_speed?st.best_speed.speed_kbps+' KB/s':'—','warn'],
    ['未测试',st.untested,'dim']
  ].map(([k,v,c])=>`<div class="card"><div class="v ${c}">${v}</div><div class="k">${k}</div></div>`).join('');
  document.getElementById('myip').textContent=st.my_ip||'—';
  document.getElementById('state').innerHTML=st.testing?'<span class="spin"></span> 测速中':'✅ 待机';
  document.getElementById('round').innerHTML=st.testing
    ?`本轮 ${st.round_done}/${st.round_size} · ${st.round_stage}`
    :(st.last_round?`上轮: 探${st.last_round.picked} TCP过${st.last_round.tcp_ok} 存活${st.last_round.validated_alive} @${st.last_round.end}`:'');
  document.getElementById('tb').innerHTML=d.items.length?d.items.map(x=>{
    const sp=x.speed_bps?`<span class="ok">${kb(x.speed_bps)}</span>`:(x.alive?'<span class="dim">未测</span>':'—');
    const anon={elite:'<span class="ok pill" style="border-color:var(--ok)">elite</span>',anonymous:'<span class="warn pill" style="border-color:var(--warn)">anon</span>',transparent:'<span class="bad pill" style="border-color:var(--bad)">trans</span>'}[x.anonymity]||'—';
    const geo=flag(x.country_code)+' '+esc(x.country)+(x.city?' · '+esc(x.city):'');
    const last=x.last_alive||'<span class="dim">从未</span>';
    const btn=x.alive?'<button onclick="recheck(this)" data-a="'+x.addr+'">重测</button>':'<button onclick="recheck(this)" data-a="'+x.addr+'">再试</button>';
    return `<tr><td><b class="acc">${x.protocol}</b></td><td><b>${x.addr}</b></td>
      <td class="${latCls(x.latency_ms)}">${x.latency_ms!=null?x.latency_ms+'ms':'—'}</td><td>${sp}</td>
      <td>${anon}</td><td>${geo}</td><td class="dim">${esc(x.isp).slice(0,28)}</td>
      <td class="dim">${esc(x.exit_ip)}</td><td class="dim">${x.check_count}${x.fail_count?'<span class="bad">/'+x.fail_count+'</span>':''}</td>
      <td class="dim">${last}</td><td>${btn}</td></tr>`;
  }).join(''):'<tr><td colspan="11" style="color:var(--dim)">暂无匹配 — 抓取与测速流水线运行中, 稍候…</td></tr>';
  document.getElementById('scrapes').innerHTML='<b>最近抓取:</b><br>'+ (st.scrape_log||[]).slice(0,14).map(l=>`${l.ts} · ${l.source}: 找到${l.found} 新增${l.new}`).join('<br>');
  document.getElementById('exp').href=`/api/export?alive=1&protocol=${p}&format=uri`;
}
async function recheck(btn){btn.textContent='…';const r=await fetch('/api/check/'+btn.dataset.a,{method:'POST'});setTimeout(load,2500)}
async function doScrape(){const r=await fetch('/api/scrape',{method:'POST'});setTimeout(load,8000)}
async function doCheckAlive(){const r=await fetch('/api/checkalive',{method:'POST'});setTimeout(load,4000)}
document.querySelectorAll('th[data-s]').forEach(th=>th.onclick=()=>{
  const s=th.dataset.s; curOrder=(curSort===s&&curOrder==='asc')?'desc':'asc'; curSort=s; load();});
document.getElementById('f_q').addEventListener('keydown',e=>{if(e.key==='Enter')load()});
load(); setInterval(load,5000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    db: DB = None
    state: dict = None
    my_ip: str = ''

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def _text(self, text, filename=None):
        body = text.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename={filename}')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        p = u.path
        if p == '/' or p == '/index.html':
            body = DASH.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        try:
            if p == '/api/stats':
                st = self.db.stats()
                st.update(testing=self.state['testing'],
                          round_size=self.state['round_size'],
                          round_done=self.state['round_done'],
                          round_stage=self.state['round_stage'],
                          last_round=self.state.get('last_round'),
                          rounds_done=self.state['rounds_done'],
                          scraping=self.state['scraping'],
                          uptime_s=round(core.now_ts() - self.state['started']))
                self._json(st)
            elif p == '/api/proxies':
                items = self.db.query(protocol=q.get('protocol'), alive=q.get('alive', '1'),
                                      sort=q.get('sort', 'latency'), limit=q.get('limit', 300),
                                      q=q.get('q', ''))
                self._json({'items': items, 'total_matched': len(items)})
            elif p == '/api/random':
                r = self.db.random_alive(q.get('protocol'))
                if not r:
                    self._json({'error': 'no alive proxy'}, 404)
                else:
                    uri = f"{r['protocol']}://{r['addr']}"
                    self._json({**r, 'uri': uri})
            elif p == '/api/export':
                items = self.db.query(protocol=q.get('protocol'), alive=q.get('alive', '1'),
                                      sort=q.get('sort', 'speed'), limit=100000)
                fmt = q.get('format', 'uri')
                lines = [f"{x['protocol']}://{x['addr']}" if fmt == 'uri' else x['addr']
                         for x in items]
                self._text('\n'.join(lines), 'proxies.txt')
            elif p == '/api/scrapes':
                st = self.db.stats()
                self._json({'scrape_log': st['scrape_log']})
            else:
                self._json({'error': 'not found'}, 404)
        except Exception as e:
            self._json({'error': str(e)}, 500)

    def do_POST(self):
        u = urlparse(self.path)
        try:
            if u.path == '/api/scrape':
                if not self.state['scraping']:
                    threading.Thread(target=self._run_scrape, daemon=True).start()
                    self._json({'queued': True})
                else:
                    self._json({'queued': False, 'msg': '抓取进行中'})
            elif u.path == '/api/checkalive':
                threading.Thread(target=self._run_checkalive, daemon=True).start()
                self._json({'queued': True})
            elif u.path.startswith('/api/check/'):
                addr = u.path.rsplit('/', 1)[-1]
                if ':' not in addr:
                    self._json({'error': 'bad addr'}, 400)
                    return
                threading.Thread(target=checker_manual, args=(self.db, addr, self.my_ip),
                                 daemon=True).start()
                self._json({'queued': True, 'addr': addr})
            else:
                self._json({'error': 'not found'}, 404)
        except Exception as e:
            self._json({'error': str(e)}, 500)

    def _run_scrape(self):
        import scraper
        try:
            scraper.scrape_all(self.db, self.state)
        except Exception as e:
            self.state['last_error'] = str(e)
            self.state['scraping'] = False

    def _run_checkalive(self):
        """手动触发: 立即对全部存活代理跑一轮验证+测速"""
        import checker
        from concurrent.futures import ThreadPoolExecutor, as_completed
        try:
            rows = self.db.query(alive=1, limit=100000)
            results = []
            with ThreadPoolExecutor(max_workers=120) as ex:
                futs = [ex.submit(checker.validate, r['ip'], r['port'], r['protocol'])
                        for r in rows]
                for f in as_completed(futs):
                    results.append(f.result())
            self.db.apply_validate(results)
            alive_addrs = [r['addr'] for r in results if r['alive']]
            sp_rows = self.db.need_speed(alive_addrs, limit=1000)
            sp_results = []
            with ThreadPoolExecutor(max_workers=40) as ex:
                futs = [ex.submit(checker.speed_test, ip, port, proto)
                        for _, ip, port, proto in sp_rows]
                for f in as_completed(futs):
                    sp_results.append(f.result())
            self.db.apply_speed(sp_results)
            self.db.set_meta('last_check_round', core.now_iso())
        except Exception as e:
            self.state['last_error'] = str(e)


def checker_manual(db, addr, my_ip):
    import checker
    try:
        checker.check_one_manual(db, addr, my_ip)
    except Exception:
        pass


def serve(db: DB, state: dict, host='127.0.0.1', port=8899, my_ip=''):
    Handler.db = db
    Handler.state = state
    Handler.my_ip = my_ip
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f'[server] 代理池面板: http://{host}:{port}  (API: /api/stats /api/proxies /api/random /api/export)')
    httpd.serve_forever()
