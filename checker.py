# -*- coding: utf-8 -*-
"""checker.py — 代理实时测速引擎
流水线: TCP 探活 → 协议验证(带出口地理) → 下载测速 → 匿名度检测
纯手写 SOCKS4/4a/5 握手 + HTTP 正向代理, 零第三方依赖。
"""
import socket
import struct
import json
import time
import http.client
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed

warnings.filterwarnings('ignore')

import core
from core import (DB, UA, TCP_TIMEOUT, VAL_TIMEOUT, SPEED_TIMEOUT, SPEED_BYTES,
                  TCP_WORKERS, VAL_WORKERS, SPEED_WORKERS, now_iso, now_ts)

VAL_HOST = 'ip-api.com'
VAL_PATH = '/json/?fields=status,message,country,countryCode,city,isp,as,query&lang=zh-CN'
SPEED_HOST = 'speed.cloudflare.com'
SPEED_PATH = '/__down?bytes=1000000'


# ---------------------------------------------------------------- 基础探测
def tcp_probe(ip, port, timeout=TCP_TIMEOUT):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        t0 = time.time()
        s.connect((ip, int(port)))
        dt = (time.time() - t0) * 1000
        s.close()
        return dt
    except Exception:
        return None


def recvn(s, n):
    buf = b''
    while len(buf) < n:
        c = s.recv(n - len(buf))
        if not c:
            raise IOError('conn closed')
        buf += c
    return buf


# ---------------------------------------------------------------- SOCKS 隧道
def socks5_tunnel(ip, port, target, tport, timeout):
    s = socket.create_connection((ip, int(port)), timeout=timeout)
    s.settimeout(timeout)
    s.sendall(b'\x05\x01\x00')
    r = recvn(s, 2)
    if r[0] != 5:
        raise IOError('not socks5')
    if r[1] != 0:
        raise IOError('socks5 需要认证')
    tb = target.encode()
    s.sendall(b'\x05\x01\x00\x03' + bytes([len(tb)]) + tb + struct.pack('>H', tport))
    r = recvn(s, 4)
    if r[1] != 0:
        raise IOError(f'socks5 connect 失败 code={r[1]}')
    atyp = r[3]
    if atyp == 1:
        recvn(s, 4 + 2)
    elif atyp == 3:
        n = recvn(s, 1)[0]
        recvn(s, n + 2)
    elif atyp == 4:
        recvn(s, 16 + 2)
    return s


def socks4_tunnel(ip, port, target, tport, timeout):
    s = socket.create_connection((ip, int(port)), timeout=timeout)
    s.settimeout(timeout)
    try:
        dip = socket.getaddrinfo(target, None, socket.AF_INET)[0][4][0]
        ipb = socket.inet_aton(dip)
    except Exception:
        ipb = b'\x00\x00\x00\x01'          # SOCKS4a 域名模式
    s.sendall(b'\x04\x01' + struct.pack('>H', tport) + ipb + b'\x00')
    if ipb == b'\x00\x00\x00\x01':
        s.sendall(target.encode() + b'\x00')
    r = recvn(s, 8)
    if r[1] != 0x5A:
        raise IOError(f'socks4 connect 失败 code={r[1]}')
    return s


def build_tunnel(protocol, ip, port, target, tport, timeout):
    """返回 (socket, 需要绝对URI)"""
    if protocol == 'http':
        s = socket.create_connection((ip, int(port)), timeout=timeout)
        s.settimeout(timeout)
        return s, True
    if protocol == 'socks5':
        return socks5_tunnel(ip, port, target, tport, timeout), False
    if protocol == 'socks4':
        return socks4_tunnel(ip, port, target, tport, timeout), False
    raise ValueError(protocol)


class TunnelHTTP(http.client.HTTPConnection):
    """在已建立的代理隧道上跑 HTTP, 复用 http.client 的 chunked/keepalive 解析"""
    def __init__(self, sock, host, timeout=8):
        super().__init__(host, timeout=timeout)
        self._tunnel_sock = sock

    def connect(self):
        self.sock = self._tunnel_sock


# ---------------------------------------------------------------- 验证
def validate(ip, port, protocol, timeout=VAL_TIMEOUT):
    """GET ip-api.com: 成功 ⇒ 存活 + 出口地理; 返回 dict"""
    t0 = time.time()
    s = None
    try:
        s, absolute = build_tunnel(protocol, ip, port, VAL_HOST, 80, timeout)
        conn = TunnelHTTP(s, VAL_HOST, timeout=timeout)
        url = (f'http://{VAL_HOST}{VAL_PATH}' if absolute else VAL_PATH)
        conn.request('GET', url, headers={
            'Host': VAL_HOST, 'User-Agent': UA, 'Accept': 'application/json',
            'Connection': 'close'})
        resp = conn.getresponse()
        body = resp.read(65536)
        conn.close()
        dt = (time.time() - t0) * 1000
        j = json.loads(body.decode('utf-8', 'replace'))
        if j.get('status') != 'success':
            raise IOError(f"ip-api: {j.get('message', '?')}")
        return {'addr': f'{ip}:{port}', 'alive': True, 'latency_ms': round(dt),
                'country': j.get('country') or '', 'country_code': j.get('countryCode') or '',
                'city': j.get('city') or '', 'isp': j.get('isp') or '',
                'asn': j.get('as') or '', 'exit_ip': j.get('query') or '', 'error': ''}
    except Exception as e:
        if s:
            try:
                s.close()
            except Exception:
                pass
        return {'addr': f'{ip}:{port}', 'alive': False, 'latency_ms': None,
                'country': '', 'country_code': '', 'city': '', 'isp': '',
                'asn': '', 'exit_ip': '', 'error': str(e)[:150]}


# ---------------------------------------------------------------- 测速
def speed_test(ip, port, protocol, timeout=SPEED_TIMEOUT, nbytes=SPEED_BYTES):
    """经代理下载 ~1MB, 统计有效字节/秒"""
    s = None
    try:
        s, absolute = build_tunnel(protocol, ip, port, SPEED_HOST, 80, timeout)
        s.settimeout(timeout)
        conn = TunnelHTTP(s, SPEED_HOST, timeout=timeout)
        url = (f'http://{SPEED_HOST}{SPEED_PATH}' if absolute else SPEED_PATH)
        conn.request('GET', url, headers={
            'Host': SPEED_HOST, 'User-Agent': UA, 'Accept': '*/*',
            'Connection': 'close'})
        resp = conn.getresponse()
        if resp.status != 200:
            raise IOError(f'HTTP {resp.status}')
        total, t0 = 0, time.time()
        deadline = t0 + 8.0
        while total < nbytes and time.time() < deadline:
            chunk = resp.read(65536)
            if not chunk:
                break
            total += len(chunk)
        conn.close()
        dt = time.time() - t0
        if total < 10240:
            raise IOError(f'仅 {total}B, 通道不稳定')
        return {'addr': f'{ip}:{port}', 'speed_bps': round(total / dt), 'error': ''}
    except Exception as e:
        if s:
            try:
                s.close()
            except Exception:
                pass
        return {'addr': f'{ip}:{port}', 'speed_bps': None, 'error': str(e)[:150]}


# ---------------------------------------------------------------- 匿名度
def anonymity_test(ip, port, protocol, my_ip, timeout=6):
    s = None
    try:
        s, absolute = build_tunnel(protocol, ip, port, 'httpbin.org', 80, timeout)
        conn = TunnelHTTP(s, 'httpbin.org', timeout=timeout)
        url = ('http://httpbin.org/get' if absolute else '/get')
        conn.request('GET', url, headers={'Host': 'httpbin.org', 'User-Agent': UA,
                                          'Connection': 'close'})
        resp = conn.getresponse()
        body = resp.read(65536)
        conn.close()
        j = json.loads(body.decode('utf-8', 'replace'))
        hdrs = {k.lower(): v for k, v in (j.get('headers') or {}).items()}
        origin = j.get('origin', '')
        if my_ip and my_ip in origin:
            anon = 'transparent'
        elif hdrs.get('x-forwarded-for') or hdrs.get('via') or hdrs.get('forwarded'):
            anon = 'anonymous'
        else:
            anon = 'elite'
        return {'addr': f'{ip}:{port}', 'anonymity': anon}
    except Exception:
        if s:
            try:
                s.close()
            except Exception:
                pass
        return {'addr': f'{ip}:{port}', 'anonymity': ''}


# ---------------------------------------------------------------- 调度
def get_my_ip(db: DB):
    """直连获取本机出口 IP (供匿名度判断), 写入 meta"""
    import requests
    for prx in (None, core.CLASH):
        try:
            r = requests.get('http://ip-api.com/json/?fields=status,query',
                             timeout=6, proxies=prx)
            j = r.json()
            if j.get('status') == 'success':
                db.set_meta('my_ip', j['query'])
                return j['query']
        except Exception:
            continue
    return ''


def check_round(db: DB, my_ip: str, state=None):
    """一轮完整流水线; 返回统计 dict"""
    picked = db.pick_workload()
    if not picked:
        return {'picked': 0}
    if state:
        state['testing'] = True
        state['round_size'] = len(picked)
        state['round_done'] = 0
        state['round_stage'] = 'TCP探活'

    # ---- Stage A: TCP 探活 ----
    tcp_ok, tcp_dead = [], []
    with ThreadPoolExecutor(max_workers=TCP_WORKERS) as ex:
        futs = {ex.submit(tcp_probe, ip, port): (ip, port) for _, ip, port, _ in picked}
        done = 0
        for f in as_completed(futs):
            ip, port = futs[f]
            r = f.result()
            (tcp_ok if r is not None else tcp_dead).append((ip, port, r))
            done += 1
            if state:
                state['round_done'] = done
    db.mark_tcp_dead([f'{ip}:{port}' for ip, port, _ in tcp_dead])

    # ---- Stage B: 协议验证 + 出口地理 ----
    if state:
        state['round_stage'] = '协议验证'
    proto_by_addr = {f'{ip}:{port}': proto for _, ip, port, proto in picked}
    val_results, fresh_alive = [], []
    with ThreadPoolExecutor(max_workers=VAL_WORKERS) as ex:
        futs = {ex.submit(validate, ip, port, proto_by_addr[f'{ip}:{port}']): (ip, port)
                for ip, port, _ in tcp_ok}
        done = 0
        for f in as_completed(futs):
            r = f.result()
            val_results.append(r)
            if r['alive']:
                fresh_alive.append(r['addr'])
            done += 1
            if state:
                state['round_done'] = done
    db.apply_validate(val_results)
    db.set_meta('last_check_round', now_iso())

    # ---- Stage C: 测速 (新存活 + 测速过期) ----
    if state:
        state['round_stage'] = '下载测速'
    speed_picked = db.need_speed(fresh_alive[:200])
    speed_results = []
    with ThreadPoolExecutor(max_workers=SPEED_WORKERS) as ex:
        futs = {ex.submit(speed_test, ip, port, proto): (ip, port, proto)
                for _, ip, port, proto in speed_picked}
        for f in as_completed(futs):
            speed_results.append(f.result())
    db.apply_speed(speed_results)

    # ---- Stage D: 匿名度 (新存活, 限量) ----
    if state:
        state['round_stage'] = '匿名度检测'
    if my_ip:
        anon_picked = db.need_anonymity(fresh_alive[:300])
        anon_results = []
        with ThreadPoolExecutor(max_workers=30) as ex:
            futs = {ex.submit(anonymity_test, ip, port, proto, my_ip): 0
                    for _, ip, port, proto in anon_picked}
            for f in as_completed(futs):
                anon_results.append(f.result())
        db.apply_anonymity(anon_results)

    db.set_meta('alive_now', str(db.stats()['alive']))
    if state:
        state['testing'] = False
        state['round_stage'] = '空闲'
        state['round_done'] = state['round_size']
        state['last_round_end'] = now_iso()
        state['rounds_done'] += 1
        state['last_round'] = {
            'picked': len(picked), 'tcp_ok': len(tcp_ok),
            'validated_alive': len(fresh_alive), 'speed_tested': len(speed_picked),
            'end': now_iso()}
    return {'picked': len(picked), 'tcp_ok': len(tcp_ok),
            'alive': len(fresh_alive), 'speed_tested': len(speed_picked)}


def check_one_manual(db: DB, addr, my_ip=''):
    """单条手动复测 (dashboard 按钮)"""
    p = db.get_proxy(addr)
    if not p:
        return None
    r = validate(p['ip'], p['port'], p['protocol'])
    db.apply_validate([r])
    if r['alive']:
        sp = speed_test(p['ip'], p['port'], p['protocol'])
        db.apply_speed([sp])
        if my_ip:
            db.apply_anonymity([anonymity_test(p['ip'], p['port'], p['protocol'], my_ip)])
    return r


if __name__ == '__main__':
    db = DB()
    my_ip = get_my_ip(db)
    print(f'本机出口 IP: {my_ip}')
    import sys
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    for i in range(rounds):
        t0 = time.time()
        r = check_round(db, my_ip)
        print(f"第{i+1}轮: 探测{r.get('picked',0)} TCP通过{r.get('tcp_ok',0)} "
              f"存活{r.get('alive',0)} 测速{r.get('speed_tested',0)} "
              f"耗时{time.time()-t0:.0f}s  库内存活={db.stats()['alive']}")
