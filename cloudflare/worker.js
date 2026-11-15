// proxy_pool/cloudflare/worker.js — 代理池云端 API (读 KV, 由 GitHub Actions 定时灌数据)
// KV keys: pool:http | pool:https | pool:socks4 | pool:socks5 | stats
// 数据格式: [{addr:"1.2.3.4:8080", latency_ms:320, anonymity:"高匿", country:"US", last_alive:"...", source:"..."}]

const P = k => new Response(k, { headers: { 'content-type': 'application/json; charset=utf-8', 'access-control-allow-origin': '*' } });
const J = (o, s = 200) => new Response(JSON.stringify(o), { status: s, headers: { 'content-type': 'application/json; charset=utf-8', 'access-control-allow-origin': '*' } });

const DASH = `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><title>🛰️ 免费代理池</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>:root{--bg:#0b1020;--card:#131a2e;--line:#232c4a;--tx:#dbe4ff;--dim:#8492c0;--acc:#4f8cff;--ok:#2ecc71}
*{box-sizing:border-box;margin:0;padding:0}body{background:var(--bg);color:var(--tx);font:14px/1.5 "Segoe UI",system-ui,sans-serif;padding:18px;max-width:960px;margin:0 auto}
h1{font-size:20px}.sub{color:var(--dim);font-size:12px;margin:4px 0 14px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px}
.card .v{font-size:22px;font-weight:700}.card .k{color:var(--dim);font-size:11px}
.bar{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;margin-bottom:14px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
select{background:#0e1530;color:var(--tx);border:1px solid var(--line);border-radius:6px;padding:6px 8px}
button{background:var(--acc);color:#fff;border:0;border-radius:6px;padding:7px 14px;cursor:pointer}
button:hover{filter:brightness(1.15)}
#out{background:#0e1530;border:1px solid var(--line);border-radius:8px;padding:12px;font-family:Consolas,monospace;font-size:13px;white-space:pre-wrap;word-break:break-all;min-height:48px}
table{width:100%;border-collapse:collapse;font-size:12px;margin-top:14px}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left}th{color:var(--dim);font-weight:500}
.tag{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11px;background:#1b2a4d}</style></head><body>
<h1>🛰️ 免费代理池</h1><div class="sub">数据源 40+ 公开源站 · GitHub Actions 每 15 分钟自动抓取验证 · Cloudflare KV 存储</div>
<div class="cards" id="cards"></div>
<div class="bar">
  <select id="proto"><option value="">全部协议</option><option>http</option><option>https</option><option>socks4</option><option>socks5</option></select>
  <select id="lat"><option value="">不限延迟</option><option value="500">≤500ms</option><option value="1000">≤1s</option><option value="2000">≤2s</option></select>
  <button onclick="rand()">🎲 随机取一个</button>
  <button onclick="take(10)">取10个</button>
  <button onclick="exportTxt()">导出TXT</button>
  <span class="sub" id="stat"></span>
</div>
<div id="out">点击按钮取用代理 →</div>
<table id="tb"><thead><tr><th>代理</th><th>协议</th><th>延迟</th><th>匿名度</th><th>国家</th><th>存活时间</th></tr></thead><tbody></tbody></table>
<script>
const $=s=>document.querySelector(s);
async function stats(){const d=await (await fetch('/api/stats')).json();
 $('#cards').innerHTML=Object.entries(d.protocols||{}).map(([k,v])=>\`<div class="card"><div class="v">\${v.alive}</div><div class="k">\${k.toUpperCase()} 存活 / \${v.total}</div></div>\`).join('')+['<div class="card"><div class="v">'+(d.updated_ago||'?')+'</div><div class="k">分钟前更新</div></div>'].join('');}
async function rand(){const p=$('#proto').value,l=$('#lat').value;
 const d=await (await fetch('/api/random?protocol='+p+(l?'&max_latency='+l:''))).json();
 $('#out').textContent=d.proxy?('✔ '+d.proxy+'   ('+(d.latency_ms||'?')+'ms, '+(d.anonymity||'?')+')'):'池子空了, 等 Actions 下一轮';
 if(d.proxy)navigator.clipboard?.writeText(d.proxy).catch(()=>{});}
async function take(n){const p=$('#proto').value,l=$('#lat').value;
 const d=await (await fetch('/api/proxies?limit='+n+(p?'&protocol='+p:'')+(l?'&max_latency='+l:''))).json();
 $('#out').textContent=(d.proxies||[]).map(x=>x.addr).join('\\n')||'无';
 $('#tb tbody').innerHTML=(d.proxies||[]).slice(0,20).map(x=>\`<tr><td><b>\${x.addr}</b></td><td><span class="tag">\${x.protocol}</span></td><td>\${x.latency_ms??'-'}ms</td><td>\${x.anonymity||'-'}</td><td>\${x.country||'-'}</td><td>\${(x.last_alive||'').slice(5,16)}</td></tr>\`).join('');}
async function exportTxt(){const p=$('#proto').value;location.href='/api/export'+(p?'?protocol='+p:'');}
stats();setInterval(stats,60000);
</script></body></html>`;

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (path === '/' || path === '/dash')
      return new Response(DASH, { headers: { 'content-type': 'text/html; charset=utf-8' } });

    if (path === '/api/stats') {
      const s = await env.POOL.get('stats');
      return P(s || '{"protocols":{}}');
    }

    if (path === '/api/random') {
      const proto = url.searchParams.get('protocol') || '';
      const maxLat = parseInt(url.searchParams.get('max_latency') || '0');
      const protos = proto ? [proto] : ['http', 'https', 'socks4', 'socks5'];
      let candidates = [];
      for (const p of protos) {
        const raw = await env.POOL.get(`pool:${p}`);
        if (raw) candidates = candidates.concat(JSON.parse(raw).map(x => ({ ...x, protocol: p })));
      }
      if (maxLat) candidates = candidates.filter(x => (x.latency_ms || 9999) <= maxLat);
      if (!candidates.length) return J({ error: 'no alive proxy' }, 404);
      const pick = candidates[Math.floor(Math.random() * candidates.length)];
      return J({ proxy: pick.addr, ...pick });
    }

    if (path === '/api/proxies') {
      const proto = url.searchParams.get('protocol') || '';
      const maxLat = parseInt(url.searchParams.get('max_latency') || '0');
      const limit = Math.min(parseInt(url.searchParams.get('limit') || '100'), 500);
      const protos = proto ? [proto] : ['http', 'https', 'socks4', 'socks5'];
      let all = [];
      for (const p of protos) {
        const raw = await env.POOL.get(`pool:${p}`);
        if (raw) all = all.concat(JSON.parse(raw).map(x => ({ ...x, protocol: p })));
      }
      if (maxLat) all = all.filter(x => (x.latency_ms || 9999) <= maxLat);
      all.sort((a, b) => (a.latency_ms || 9999) - (b.latency_ms || 9999));
      return J({ count: all.slice(0, limit).length, proxies: all.slice(0, limit) });
    }

    if (path === '/api/export') {
      const proto = url.searchParams.get('protocol') || '';
      const fmt = url.searchParams.get('fmt') || 'txt';
      const protos = proto ? [proto] : ['http', 'https', 'socks4', 'socks5'];
      let all = [];
      for (const p of protos) {
        const raw = await env.POOL.get(`pool:${p}`);
        if (raw) all = all.concat(JSON.parse(raw).map(x => ({ ...x, protocol: p })));
      }
      if (fmt === 'json') return J({ count: all.length, proxies: all });
      const txt = all.map(x => x.addr).join('\n');
      return new Response(txt || '', { headers: { 'content-type': 'text/plain; charset=utf-8', 'access-control-allow-origin': '*' } });
    }

    return J({ error: 'not found', endpoints: ['/', '/api/stats', '/api/random', '/api/proxies', '/api/export'] }, 404);
  },
};
