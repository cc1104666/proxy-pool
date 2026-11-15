// proxy_pool/cloudflare/worker.js — 代理池云端 API (读 KV, 由 GitHub Actions / 本机 export_kv 灌数据)
// KV keys: pool:http | pool:https | pool:socks4 | pool:socks5 | stats
// 面板视觉与本地 server.py 8899 面板同款

const J = (o, s = 200) => new Response(JSON.stringify(o), { status: s, headers: { 'content-type': 'application/json; charset=utf-8', 'access-control-allow-origin': '*' } });
const PROTS = ['http', 'https', 'socks4', 'socks5'];

async function allProxies(env, proto, maxLat) {
  const protos = proto ? [proto] : PROTS;
  let all = [];
  for (const p of protos) {
    const raw = await env.POOL.get(`pool:${p}`);
    if (raw) all = all.concat(JSON.parse(raw).map(x => ({ ...x, protocol: p })));
  }
  if (maxLat) all = all.filter(x => (x.latency_ms || 99999) <= maxLat);
  return all;
}

const DASH = `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
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
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.acc{color:var(--acc)}.dim{color:var(--dim)}
.bar{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-bottom:14px;display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:12px}
select,input{background:#0e1530;color:var(--tx);border:1px solid var(--line);border-radius:6px;padding:5px 8px;font-size:12px}
button{background:var(--acc);color:#fff;border:0;border-radius:6px;padding:6px 12px;font-size:12px;cursor:pointer}
button:hover{filter:brightness(1.15)}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;white-space:nowrap}
th{color:var(--dim);font-weight:500;cursor:pointer;user-select:none}
tr:hover td{background:#111936}
#scrapes{color:var(--dim);font-size:12px;margin-top:14px;line-height:1.7}
.spin{display:inline-block;width:11px;height:11px;border:2px solid var(--acc);border-top-color:transparent;border-radius:50%;animation:sp 1s linear infinite;vertical-align:-1px}
@keyframes sp{to{transform:rotate(360deg)}}
.pill{border:1px solid var(--line);border-radius:9px;padding:0 7px;font-size:11px}
</style></head><body>
<h1>🛰️ 代理池实时监控 <span id="state"></span></h1>
<div class="sub">抓取 → 入库 → 探活 → 验证 → 测速 全自动流水线 (GitHub Actions 每 15 分钟一轮) · 每 30 秒自动刷新 · 验证出口 IP: <span id="myip">…</span></div>
<div class="cards" id="cards"></div>
<div class="bar">
  协议 <select id="f_proto"><option value="">全部</option><option>http</option><option>socks4</option><option>socks5</option></select>
  延迟 <select id="f_lat"><option value="">不限</option><option value="300">≤300ms</option><option value="800">≤800ms</option><option value="1500">≤1.5s</option></select>
  排序 <select id="f_sort"><option value="latency">延迟最快</option><option value="speed">带宽最大</option><option value="recent">最近存活</option></select>
  <input id="f_q" placeholder="搜索 地址/国家/ISP/出口IP" size="22">
  <button onclick="load()">查询</button>
  <a id="exp" href="#" style="color:var(--acc);font-size:12px">⬇ 导出TXT</a>
  <span id="round" class="sub" style="margin:0"></span>
</div>
<table><thead><tr>
<th>协议</th><th>地址</th><th>延迟</th><th>带宽</th><th>匿名</th><th>国家/城市</th><th>ISP</th><th>出口IP</th><th>测次</th><th>最近存活</th><th>操作</th>
</tr></thead><tbody id="tb"><tr><td colspan="11">加载中…</td></tr></tbody></table>
<div id="scrapes"></div>
<script>
const esc=s=>s==null?'':String(s);
const flag=cc=>cc&&cc.length==2?String.fromCodePoint(...[...cc.toUpperCase()].map(c=>0x1F1E6+c.charCodeAt(0)-65)):'';
const latCls=v=>v==null?'':v<300?'ok':v<900?'warn':'bad';
const kb=v=>v?'<span class="ok">'+(v/1024).toFixed(1)+' KB/s</span>':'—';
async function load(){
  const p=document.getElementById('f_proto').value,
        lat=document.getElementById('f_lat').value,
        q=document.getElementById('f_q').value.trim().toLowerCase(),
        sort=document.getElementById('f_sort').value;
  const pr=await fetch('/api/proxies?limit=400'+(p?'&protocol='+p:'')+(lat?'&max_latency='+lat:''));
  const st=await (await fetch('/api/stats')).json();
  let items=(await pr.json()).proxies||[];
  if(q) items=items.filter(x=>(x.addr+' '+x.country+' '+x.isp+' '+x.exit_ip).toLowerCase().includes(q));
  const key={latency:x=>x.latency_ms??1e9,speed:x=>-(x.speed_bps||0),recent:x=>x.last_alive||''}[sort];
  items.sort((a,b)=>{const ka=key(a),kbv=key(b);return ka<kbv?-1:ka>kbv?1:0});
  const b=st.by||{};
  document.getElementById('cards').innerHTML=[
    ['总入库',st.total??'—','acc'],['存活可用',st.alive??st.total_alive,'ok'],
    ['HTTP',(b.http?.alive??0)+' / '+(b.http?.total??0),''],
    ['SOCKS4',(b.socks4?.alive??0)+' / '+(b.socks4?.total??0),''],
    ['SOCKS5',(b.socks5?.alive??0)+' / '+(b.socks5?.total??0),''],
    ['平均延迟',st.avg_latency_ms!=null?st.avg_latency_ms+'ms':'—',''],
    ['最快带宽',st.best_speed?st.best_speed.speed_kbps+' KB/s':'—','warn'],
    ['未测试',st.untested??'—','dim']
  ].map(([k,v,c])=>\`<div class="card"><div class="v \${c}">\${v}</div><div class="k">\${k}</div></div>\`).join('');
  document.getElementById('myip').textContent=st.my_ip||'—';
  document.getElementById('state').innerHTML='<span class="ok">✅ 云端就绪</span>';
  document.getElementById('round').textContent='数据更新于 '+st.updated+' · Actions 验证出口 '+st.my_ip;
  document.getElementById('tb').innerHTML=items.length?items.map(x=>{
    const anon={'elite':'<span class="ok pill" style="border-color:var(--ok)">elite</span>','高匿':'<span class="ok pill" style="border-color:var(--ok)">elite</span>','anonymous':'<span class="warn pill" style="border-color:var(--warn)">anon</span>','匿':'<span class="warn pill" style="border-color:var(--warn)">anon</span>','transparent':'<span class="bad pill" style="border-color:var(--bad)">trans</span>','透明':'<span class="bad pill" style="border-color:var(--bad)">trans</span>'}[x.anonymity]||'<span class="dim">—</span>';
    const geo=flag(x.country_code)+' '+esc(x.country)+(x.city?' · '+esc(x.city):'');
    return \`<tr><td><b class="acc">\${x.protocol}</b></td><td><b>\${x.addr}</b></td>
      <td class="\${latCls(x.latency_ms)}">\${x.latency_ms!=null?x.latency_ms+'ms':'—'}</td><td>\${kb(x.speed_bps)}</td>
      <td>\${anon}</td><td>\${geo}</td><td class="dim">\${esc(x.isp).slice(0,28)}</td>
      <td class="dim">\${esc(x.exit_ip)}</td><td class="dim">\${x.check_count??'—'}\${x.fail_count?'<span class="bad">/'+x.fail_count+'</span>':''}</td>
      <td class="dim">\${esc(x.last_alive).slice(5,16)||'—'}</td>
      <td><button onclick="cp(this)" data-a="\${x.addr}">复制</button></td></tr>\`;
  }).join(''):'<tr><td colspan="11" style="color:var(--dim)">暂无匹配 — Actions 流水线运行中, 稍候…</td></tr>';
  document.getElementById('scrapes').innerHTML='<b>最近抓取:</b><br>'+(st.scrape_log||[]).slice(0,14).map(l=>\`\${l.ts} · \${l.source}: 找到\${l.found} 新增\${l.new}\`).join('<br>');
  document.getElementById('exp').href='/api/export'+(p?'?protocol='+p:'');
}
function cp(btn){navigator.clipboard.writeText(btn.dataset.a).then(()=>{btn.textContent='✓';setTimeout(()=>btn.textContent='复制',1200)})}
load(); setInterval(load,30000);
</script></body></html>`;

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (path === '/' || path === '/dash')
      return new Response(DASH, { headers: { 'content-type': 'text/html; charset=utf-8' } });

    if (path === '/api/stats') {
      const s = await env.POOL.get('stats');
      return new Response(s || '{"protocols":{}}', { headers: { 'content-type': 'application/json; charset=utf-8', 'access-control-allow-origin': '*' } });
    }

    if (path === '/api/random') {
      const proto = url.searchParams.get('protocol') || '';
      const maxLat = parseInt(url.searchParams.get('max_latency') || '0');
      const candidates = await allProxies(env, proto, maxLat);
      if (!candidates.length) return J({ error: 'no alive proxy' }, 404);
      const pick = candidates[Math.floor(Math.random() * candidates.length)];
      return J({ proxy: pick.addr, ...pick });
    }

    if (path === '/api/proxies') {
      const proto = url.searchParams.get('protocol') || '';
      const maxLat = parseInt(url.searchParams.get('max_latency') || '0');
      const limit = Math.min(parseInt(url.searchParams.get('limit') || '100'), 500);
      const all = await allProxies(env, proto, maxLat);
      all.sort((a, b) => (a.latency_ms || 99999) - (b.latency_ms || 99999));
      return J({ count: all.slice(0, limit).length, proxies: all.slice(0, limit) });
    }

    if (path === '/api/export') {
      const proto = url.searchParams.get('protocol') || '';
      const fmt = url.searchParams.get('fmt') || 'txt';
      const all = await allProxies(env, proto, 0);
      if (fmt === 'json') return J({ count: all.length, proxies: all });
      const txt = all.map(x => x.addr).join('\n');
      return new Response(txt || '', { headers: { 'content-type': 'text/plain; charset=utf-8', 'access-control-allow-origin': '*' } });
    }

    return J({ error: 'not found', endpoints: ['/', '/api/stats', '/api/random', '/api/proxies', '/api/export'] }, 404);
  },
};
