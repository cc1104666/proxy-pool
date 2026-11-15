# -*- coding: utf-8 -*-
"""scraper.py — 全网公开免费代理抓取 (HTTP / SOCKS4 / SOCKS5)"""
import re
import json
import time
import warnings

warnings.filterwarnings('ignore')

import core
from core import DB, CLASH, UA

try:
    import requests
except ImportError:
    requests = None

if requests is None:
    import urllib.request

    def _get(url, timeout=25):
        for prx in (CLASH, None):
            try:
                h = urllib.request.ProxyHandler(prx or {})
                opener = urllib.request.build_opener(h)
                req = urllib.request.Request(url, headers={'User-Agent': UA})
                return opener.open(req, timeout=timeout).read().decode('utf-8', 'replace')
            except Exception:
                continue
        return None
else:
    def _get(url, timeout=25):
        for prx in (CLASH, None):
            try:
                r = requests.get(url, timeout=timeout, proxies=prx,
                                 headers={'User-Agent': UA}, verify=False)
                if r.status_code == 200 and r.text and len(r.text) > 20:
                    return r.text
            except Exception:
                continue
        return None


IP_RE = re.compile(r'(\d{1,3}(?:\.\d{1,3}){3}):(\d{2,5})')
PROTO_RE = re.compile(r'^(https?|socks4a?|socks5)://', re.I)
TD_RE = re.compile(r'<td>\s*(\d{1,3}(?:\.\d{1,3}){3})\s*</td>\s*<td>\s*(\d{2,5})\s*</td>')


def valid_pair(ip, port):
    try:
        parts = ip.split('.')
        if len(parts) != 4:
            return False
        if any(not (0 <= int(p) <= 255) for p in parts):
            return False
        if ip.startswith(('0.', '127.', '255.255.', '10.', '192.168.', '169.254.')):
            return False
        return 1 <= int(port) <= 65535
    except Exception:
        return False


def parse_lines(text, default_proto):
    """每行 ip:port / proto://ip:port / JSON 行"""
    out = []
    for line in text.splitlines():
        line = line.split('#')[0].strip().strip(',;')
        if not line:
            continue
        proto = default_proto
        extra = {}
        if line.startswith('{'):
            try:
                j = json.loads(line)
                ip = j.get('ip') or j.get('host') or ''
                port = j.get('port') or ''
                protos = j.get('protocols') or [default_proto]
                proto = {'HTTPS': 'http', 'HTTP': 'http', 'SOCKS4': 'socks4',
                         'SOCKS5': 'socks5'}.get(str(protos[0]).upper(),
                                                 str(protos[0]).lower()) if protos else default_proto
                extra = {'anonymity': j.get('anonymity') or j.get('anonymityLevel') or '',
                         'country': j.get('country') or '', 'isp': j.get('org') or ''}
                if valid_pair(str(ip), str(port)):
                    out.append((str(ip), int(port), proto, extra))
            except Exception:
                pass
            continue
        m = PROTO_RE.match(line)
        if m:
            p = m.group(1).lower()
            proto = 'http' if p in ('http', 'https') else \
                ('socks4' if p.startswith('socks4') else 'socks5')
            line = line[m.end():]
        m = IP_RE.search(line)
        if m and valid_pair(m.group(1), m.group(2)):
            out.append((m.group(1), int(m.group(2)), proto, extra))
    return out


def parse_geonode(text):
    out = []
    try:
        j = json.loads(text)
        for d in j.get('data', []):
            protos = d.get('protocols') or ['http']
            proto = str(protos[0]).lower()
            if proto == 'https':
                proto = 'http'
            ip, port = d.get('ip'), d.get('port')
            if valid_pair(str(ip), str(port)):
                out.append((ip, int(port), proto,
                            {'anonymity': d.get('anonymity_level') or '',
                             'country': d.get('country') or '',
                             'city': d.get('city') or '',
                             'isp': d.get('asn_org') or ''}))
    except Exception:
        pass
    return out


def parse_html_table(text):
    out = []
    for ip, port in TD_RE.findall(text):
        if valid_pair(ip, port):
            out.append((ip, int(port), 'http', {}))
    return out


PSC = 'https://api.proxyscrape.com/v2/?request=displayproxies&protocol={}&timeout=10000&country=all&ssl=all&anonymity=all'

SOURCES = [
    # --- GitHub 大列表 ---
    ('thespeedx-http',    'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt',    'lines-http'),
    ('thespeedx-socks4',  'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks4.txt',  'lines-socks4'),
    ('thespeedx-socks5',  'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt',  'lines-socks5'),
    ('monosans-http',     'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt',   'lines-http'),
    ('monosans-socks4',   'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks4.txt', 'lines-socks4'),
    ('monosans-socks5',   'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt', 'lines-socks5'),
    ('mmpx12-http',       'https://raw.githubusercontent.com/mmpx12/proxy-list/master/http.txt',   'lines-http'),
    ('mmpx12-socks4',     'https://raw.githubusercontent.com/mmpx12/proxy-list/master/socks4.txt', 'lines-socks4'),
    ('mmpx12-socks5',     'https://raw.githubusercontent.com/mmpx12/proxy-list/master/socks5.txt', 'lines-socks5'),
    ('proxifly-all',      'https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/all/data.txt', 'lines-auto'),
    ('hookzof-socks5',    'https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt', 'lines-socks5'),
    ('roosterkid-https',  'https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt',  'lines-auto'),
    ('roosterkid-socks5', 'https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS5_RAW.txt', 'lines-auto'),
    ('roosterkid-socks4', 'https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS4_RAW.txt', 'lines-auto'),
    # --- API 站 ---
    ('proxyscrape-http',   PSC.format('http'),   'lines-http'),
    ('proxyscrape-socks4', PSC.format('socks4'), 'lines-socks4'),
    ('proxyscrape-socks5', PSC.format('socks5'), 'lines-socks5'),
    ('openproxylist-http',   'https://api.openproxylist.xyz/http.txt',   'lines-http'),
    ('openproxylist-socks4', 'https://api.openproxylist.xyz/socks4.txt', 'lines-socks4'),
    ('openproxylist-socks5', 'https://api.openproxylist.xyz/socks5.txt', 'lines-socks5'),
    ('proxyspace-http',   'https://proxyspace.pro/http.txt',   'lines-http'),
    ('proxyspace-socks4', 'https://proxyspace.pro/socks4.txt', 'lines-socks4'),
    ('proxyspace-socks5', 'https://proxyspace.pro/socks5.txt', 'lines-socks5'),
    ('plistdl-http',      'https://www.proxy-list.download/api/v1/get?type=http',   'lines-http'),
    ('plistdl-socks4',    'https://www.proxy-list.download/api/v1/get?type=socks4', 'lines-socks4'),
    ('plistdl-socks5',    'https://www.proxy-list.download/api/v1/get?type=socks5', 'lines-socks5'),
    ('shifty-http',       'https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt', 'lines-http'),
    ('shifty-socks4',     'https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/socks4.txt', 'lines-socks4'),
    ('shifty-socks5',     'https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/socks5.txt', 'lines-socks5'),
    ('clarketm-raw',      'https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt', 'lines-http'),
    # --- JSON 接口 ---
    ('geonode', 'https://proxylist.geonode.com/api/proxy-list?limit=500&page=1&sort_by=lastChecked&sort_type=desc', 'geonode'),
    ('geonode2', 'https://proxylist.geonode.com/api/proxy-list?limit=500&page=2&sort_by=lastChecked&sort_type=desc', 'geonode'),
    ('geonode3', 'https://proxylist.geonode.com/api/proxy-list?limit=500&page=3&sort_by=lastChecked&sort_type=desc', 'geonode'),
    # --- HTML 页面 ---
    ('free-proxy-list', 'https://free-proxy-list.net/', 'html'),
    ('sslproxies',      'https://sslproxies.org/',      'html'),
    ('us-proxy',        'https://us-proxy.org/',        'html'),
    ('uk-proxy',        'https://free-proxy-list.net/uk-proxy.html', 'html'),
    ('anony-http',      'https://www.socks-proxy.net/', 'lines-socks4'),
]


def scrape_all(db: DB, state=None):
    if state:
        state['scraping'] = True
    summary = []
    for name, url, kind in SOURCES:
        text = _get(url, timeout=25)
        if not text:
            summary.append({'source': name, 'found': 0, 'new': 0, 'ok': False})
            db.log_scrape(name, 0, 0)
            continue
        if kind == 'geonode':
            items = parse_geonode(text)
        elif kind == 'html':
            items = parse_html_table(text)
        else:
            default = kind.replace('lines-', '') if kind != 'lines-auto' else 'http'
            items = parse_lines(text, default if default != 'auto' else 'http')
        rows = []
        for ip, port, proto, extra in items:
            rows.append((f'{ip}:{port}', ip, port, proto, name,
                         extra.get('anonymity', ''), extra.get('country', ''),
                         extra.get('city', ''), extra.get('isp', '')))
        new = db.insert_proxies(rows)
        db.log_scrape(name, len(rows), new)
        summary.append({'source': name, 'found': len(rows), 'new': new, 'ok': True})
        if state:
            state['last_scrape_progress'] = f'{name} +{new}'
        time.sleep(0.3)
    db.set_meta('last_scrape', core.now_iso())
    if state:
        state['scraping'] = False
    return summary


if __name__ == '__main__':
    db = DB()
    s = scrape_all(db)
    tot_new = sum(x['new'] for x in s)
    print(f"抓取完成: 源 {len(s)} 个, 新增 {tot_new}, 库内总量 {db.total()}")
    for x in s:
        mark = '✓' if x['ok'] else '✗'
        print(f"  [{mark}] {x['source']:22s} found={x['found']:6d} new={x['new']:6d}")
