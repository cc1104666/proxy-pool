# 🛰️ 全网代理池 · 抓取 + 实时测速 + 本地服务

全自动流水线：**抓取 → 入库 → TCP探活 → 协议验证(带出口地理) → 下载测速 → 匿名度分级**，全部数据存 SQLite，带实时监控面板。

## 快速启动

```bash
python run.py serve --port 8899
# 打开面板: http://127.0.0.1:8899
```

服务模式自动完成：库空先抓一轮 → 后台持续测速(存活 10 分钟复测/失败 30 分钟重试) → 每 30 分钟自动重新抓取全网源。

## 子命令

| 命令 | 说明 |
|---|---|
| `python run.py scrape` | 一次性抓取 38 个公开源入库 |
| `python run.py check 3` | 跑 3 轮测速流水线(命令行输出) |
| `python run.py serve` | 全自动常驻服务 + 面板 |
| `python run.py all` | 先抓取再起服务 |

## HTTP API

| 端点 | 说明 |
|---|---|
| `GET /api/stats` | 总量/存活/平均延迟/最快带宽/抓取日志 |
| `GET /api/proxies?protocol=socks5&alive=1&sort=speed&limit=100` | 代理列表(可过滤/排序/搜索) |
| `GET /api/random?protocol=http` | 随机取一条低延迟存活代理 |
| `GET /api/export?alive=1&format=uri` | 导出 `socks5://ip:port` 文本列表 |
| `POST /api/check/ip:port` | 单条立即重测 |
| `POST /api/scrape` | 立即触发一轮全网抓取 |
| `POST /api/checkalive` | 立即对全部存活代理跑验证+测速 |

消费示例：

```bash
curl "http://127.0.0.1:8899/api/random?protocol=socks5"
curl "http://127.0.0.1:8899/api/export?alive=1&format=plain" -o alive.txt
```

## 数据源 (38 个)

- GitHub 大列表：TheSpeedX / monosans / mmpx12 / proxifly / hookzof / roosterkid / ShiftyTR / clarketm
- API 站：proxyscrape / openproxylist / proxyspace / proxy-list.download / geonode(JSON)
- HTML 页：free-proxy-list.net / sslproxies.org / us-proxy.org / socks-proxy.net

抓取源站请求走本地 Clash(127.0.0.1:7890)，代理测速一律直连。

## 测速方法

1. **TCP 探活** — 220 并发，2.8s 超时
2. **协议验证** — 手写 SOCKS4/4a/5 握手 + HTTP 正向代理请求 `ip-api.com`：能拿到出口 IP/国家/ISP 才算存活，同时记录真实延迟
3. **下载测速** — 经代理从 `speed.cloudflare.com/__down?bytes=1000000` 拉流，8 秒封顶，统计有效 KB/s
4. **匿名度** — 经代理请求 `httpbin.org/get`：回显本机 IP=transparent，带 X-Forwarded-For/Via=anonymous，干净=elite

## 数据结构 (proxies.db)

`proxies` 表核心字段：`addr`(ip:port 主键)、`protocol`、`alive`、`latency_ms`、`speed_bps`、`anonymity`、`country/city/isp/asn`、`exit_ip`、`source`、`check_count/fail_count`、`last_checked/last_alive`。
