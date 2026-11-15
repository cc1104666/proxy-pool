# -*- coding: utf-8 -*-
"""dig_hacker.py — 下载有票页的 chunk 并定位数据端点"""
import os
import re
import sys
import warnings

sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import requests

BASE = 'https://proxy.h-acker.cc'
PROXY = {'http': 'http://127.0.0.1:7890', 'https': 'http://127.0.0.1:7890'}

s = requests.Session()
s.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0'})
s.verify = False
s.proxies = PROXY

h = open('hacker_home2.html', encoding='utf-8').read()
chunks = sorted(set(re.findall(r'/_next/static/chunks/[a-z0-9_.\-]+\.js', h)))
print('有票页 chunks:', len(chunks))
os.makedirs('hacker_js2', exist_ok=True)
new = 0
for c in chunks:
    name = c.rsplit('/', 1)[-1]
    if not os.path.exists('hacker_js2/' + name):
        r = s.get(BASE + c, timeout=15)
        open('hacker_js2/' + name, 'w', encoding='utf-8').write(r.text)
        new += 1
print('新增下载:', new)

for f in os.listdir('hacker_js2'):
    t = open('hacker_js2/' + f, encoding='utf-8', errors='replace').read()
    fs = re.findall(r'fetch\(\s*[`"\']([^`"\']+)', t)
    hs = re.findall(r'["\'](/api[a-zA-Z0-9_\-/.?=&]*)["\']', t)
    qs = re.findall(r'["\']([a-z0-9_]{3,30}\?(?:page|limit|type|proto)[^"\']*)["\']', t)
    kws = [k for k in ('socks', 'latency', 'anonymity', 'proxies', 'check_ip') if k in t.lower()]
    if fs or hs or kws:
        print(f, '| fetch:', fs[:10])
        print('   api:', hs[:10], '| query-urls:', qs[:6], '| kw:', kws)
