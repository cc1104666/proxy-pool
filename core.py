# -*- coding: utf-8 -*-
"""core.py — 代理池公共层: 配置 / SQLite 存储 / 共享状态"""
import os
import sys
import time
import sqlite3
import threading
import datetime

sys.stdout.reconfigure(encoding='utf-8')

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, 'proxies.db')

# 抓取源站走本地 Clash; 代理测速必须直连
CLASH = {'http': 'http://127.0.0.1:7890', 'https': 'http://127.0.0.1:7890'}

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

SCRAPE_INTERVAL = 600      # 抓取周期 10min (持续扫模式)
ALIVE_RECHECK_S = 480      # 存活代理复测周期 8min
DEAD_RECHECK_S = 900       # 失败代理重试周期 15min (无限次重试)
ROUND_CAP = 8000            # 每轮 TCP 探测上限
SPEED_CAP = 400             # 每轮测速上限
ANON_CAP = 300              # 每轮匿名度检测上限
TCP_WORKERS = 220
VAL_WORKERS = 200
SPEED_WORKERS = 40
TCP_TIMEOUT = 2.8
VAL_TIMEOUT = 7.5

# ---- 云环境降档 (GitHub Actions 2核/15min 预算): 设 POOL_CLOUD=1 启用 ----
if os.environ.get('POOL_CLOUD') == '1':
    ROUND_CAP, SPEED_CAP, ANON_CAP = 1500, 150, 120
    TCP_WORKERS, VAL_WORKERS, SPEED_WORKERS = 500, 400, 40
    TCP_TIMEOUT, VAL_TIMEOUT = 2.0, 6.5
SPEED_TIMEOUT = 9
SPEED_BYTES = 1_000_000


def now_iso():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def now_ts():
    return time.time()


SCHEMA = """
CREATE TABLE IF NOT EXISTS proxies(
    addr            TEXT PRIMARY KEY,   -- ip:port
    ip              TEXT NOT NULL,
    port            INTEGER NOT NULL,
    protocol        TEXT NOT NULL,      -- http / socks4 / socks5
    alive           INTEGER DEFAULT 0,
    latency_ms      REAL,
    speed_bps       REAL,
    anonymity       TEXT DEFAULT '',    -- elite / anonymous / transparent
    country         TEXT DEFAULT '',
    country_code    TEXT DEFAULT '',
    city            TEXT DEFAULT '',
    isp             TEXT DEFAULT '',
    asn             TEXT DEFAULT '',
    exit_ip         TEXT DEFAULT '',
    source          TEXT DEFAULT '',
    check_count     INTEGER DEFAULT 0,
    fail_count      INTEGER DEFAULT 0,
    last_error      TEXT DEFAULT '',
    last_checked    TEXT DEFAULT '',
    last_checked_ts REAL DEFAULT 0,
    last_alive      TEXT DEFAULT '',
    last_alive_ts   REAL DEFAULT 0,
    last_speed_ts   REAL DEFAULT 0,
    created         TEXT,
    created_ts      REAL
);
CREATE INDEX IF NOT EXISTS idx_alive ON proxies(alive, protocol);
CREATE INDEX IF NOT EXISTS idx_chk   ON proxies(check_count);
CREATE TABLE IF NOT EXISTS scrape_log(
    ts     TEXT, source TEXT, found INTEGER, new INTEGER
);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""


class DB:
    def __init__(self, path=DB_PATH):
        self.path = path
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA synchronous=NORMAL')
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    # ---------- 写入 ----------
    def insert_proxies(self, rows):
        """rows: [(addr, ip, port, protocol, source, anonymity, country, city, isp)]"""
        if not rows:
            return 0
        with self.lock:
            before = self.conn.execute('SELECT COUNT(*) FROM proxies').fetchone()[0]
            self.conn.executemany(
                'INSERT OR IGNORE INTO proxies(addr,ip,port,protocol,source,anonymity,country,city,isp,created,created_ts)'
                ' VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                [(a, i, p, pr, s, an, c, ci, ispn, now_iso(), now_ts())
                 for (a, i, p, pr, s, an, c, ci, ispn) in rows])
            self.conn.commit()
            after = self.conn.execute('SELECT COUNT(*) FROM proxies').fetchone()[0]
        return after - before

    def log_scrape(self, source, found, new):
        with self.lock:
            self.conn.execute('INSERT INTO scrape_log VALUES(?,?,?,?)',
                              (now_iso(), source, found, new))
            self.conn.commit()

    def set_meta(self, k, v):
        with self.lock:
            self.conn.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (k, str(v)))
            self.conn.commit()

    def get_meta(self, k, default=''):
        with self.lock:
            r = self.conn.execute('SELECT value FROM meta WHERE key=?', (k,)).fetchone()
        return r[0] if r else default

    def mark_tcp_dead(self, addrs):
        t = now_iso(); ts = now_ts()
        with self.lock:
            self.conn.executemany(
                'UPDATE proxies SET alive=0, check_count=check_count+1, fail_count=fail_count+1,'
                ' last_error="tcp连不上", last_checked=?, last_checked_ts=? WHERE addr=?',
                [(t, ts, a) for a in addrs])
            self.conn.commit()

    def apply_validate(self, results):
        """results: [{'addr','alive','latency_ms','country','country_code','city','isp','asn',
                      'exit_ip','error'}]"""
        t = now_iso(); ts = now_ts()
        rows = []
        for r in results:
            al = 1 if r.get('alive') else 0
            rows.append((al, r.get('latency_ms'), r.get('country') or '',
                         r.get('country_code') or '', r.get('city') or '',
                         r.get('isp') or '', r.get('asn') or '',
                         r.get('exit_ip') or '', (r.get('error') or '')[:160],
                         al, t, ts, al, t, al, ts, r['addr']))
        with self.lock:
            self.conn.executemany(
                'UPDATE proxies SET alive=?, latency_ms=?, country=?, country_code=?, city=?,'
                ' isp=?, asn=?, exit_ip=?, last_error=?,'
                ' check_count=check_count+1, fail_count=fail_count+1-?,'
                ' last_checked=?, last_checked_ts=?,'
                ' last_alive=CASE WHEN ?=1 THEN ? ELSE last_alive END,'
                ' last_alive_ts=CASE WHEN ?=1 THEN ? ELSE last_alive_ts END'
                ' WHERE addr=?', rows)
            self.conn.commit()

    def apply_speed(self, results):
        """results: [{'addr','speed_bps','error'}]"""
        ts = now_ts()
        with self.lock:
            self.conn.executemany(
                'UPDATE proxies SET speed_bps=?, last_error=?, last_speed_ts=? WHERE addr=?',
                [(r.get('speed_bps'), (r.get('error') or '')[:160], ts, r['addr'])
                 for r in results])
            self.conn.commit()

    def apply_anonymity(self, results):
        with self.lock:
            self.conn.executemany(
                'UPDATE proxies SET anonymity=? WHERE addr=?',
                [(r.get('anonymity') or '', r['addr']) for r in results])
            self.conn.commit()

    # ---------- 读取 ----------
    def total(self):
        with self.lock:
            return self.conn.execute('SELECT COUNT(*) FROM proxies').fetchone()[0]

    def pick_workload(self):
        """优先级: 未测试 > 存活过期 > 失败库全量轮扫(按失败次数少优先, 永不停止)
        云档(POOL_CLOUD=1): 按协议均衡配额, 避免单协议霸占整轮"""
        with self.lock:
            now = now_ts()
            if os.environ.get('POOL_CLOUD') == '1':
                rows = []
                for _p in ('http', 'https', 'socks4', 'socks5'):
                    rows += self.conn.execute(
                        'SELECT addr,ip,port,protocol FROM proxies WHERE protocol=? AND check_count=0'
                        ' ORDER BY created_ts LIMIT ?', (_p, ROUND_CAP // 4)).fetchall()
            else:
                rows = self.conn.execute(
                    'SELECT addr,ip,port,protocol FROM proxies WHERE check_count=0'
                    ' ORDER BY created_ts LIMIT ?', (ROUND_CAP,)).fetchall()
            if len(rows) < 500:
                rows += self.conn.execute(
                    'SELECT addr,ip,port,protocol FROM proxies WHERE alive=1 AND ?-last_checked_ts > ?'
                    ' LIMIT ?', (now, ALIVE_RECHECK_S, ROUND_CAP - len(rows))).fetchall()
            if len(rows) < ROUND_CAP:
                rows += self.conn.execute(
                    'SELECT addr,ip,port,protocol FROM proxies WHERE alive=0 AND check_count>0'
                    ' AND ?-last_checked_ts > ?'
                    ' ORDER BY fail_count ASC, RANDOM() LIMIT ?',
                    (now, DEAD_RECHECK_S, ROUND_CAP - len(rows))).fetchall()
        seen, out = set(), []
        for r in rows:
            if r[0] not in seen:
                seen.add(r[0]); out.append(r)
        return out

    def need_speed(self, fresh_addrs, limit=SPEED_CAP):
        """本轮新存活 + 测速过期(>30min)的存活代理"""
        with self.lock:
            now = now_ts()
            rows = self.conn.execute(
                'SELECT addr,ip,port,protocol FROM proxies WHERE alive=1 AND addr IN (%s)'
                ' LIMIT ?' % ','.join('?' * len(fresh_addrs)),
                (*fresh_addrs, limit)).fetchall() if fresh_addrs else []
            if len(rows) < limit:
                rows += self.conn.execute(
                    'SELECT addr,ip,port,protocol FROM proxies WHERE alive=1'
                    ' AND (speed_bps IS NULL OR ?-last_speed_ts>1800)'
                    ' AND latency_ms<2500 ORDER BY latency_ms LIMIT ?',
                    (now, limit - len(rows))).fetchall()
        seen, out = set(), []
        for r in rows:
            if r[0] not in seen:
                seen.add(r[0]); out.append(r)
        return out[:limit]

    def need_anonymity(self, fresh_addrs, limit=ANON_CAP):
        with self.lock:
            ph = ','.join('?' * len(fresh_addrs))
            rows = self.conn.execute(
                'SELECT addr,ip,port,protocol FROM proxies WHERE alive=1 AND addr IN (%s)'
                ' AND anonymity="" LIMIT ?' % ph, (*fresh_addrs, limit)).fetchall()
        return rows

    def query(self, protocol=None, alive=None, sort='latency', limit=300, q=''):
        sql = 'SELECT * FROM proxies WHERE 1=1'
        args = []
        if protocol and protocol != 'all':
            sql += ' AND protocol=?'; args.append(protocol)
        if alive in (0, 1, '0', '1'):
            sql += ' AND alive=?'; args.append(int(alive))
        if q:
            sql += ' AND (addr LIKE ? OR country LIKE ? OR isp LIKE ? OR exit_ip LIKE ?)'
            args += [f'%{q}%'] * 4
        order = {
            'latency': '(latency_ms IS NULL), latency_ms ASC',
            'speed':   '(speed_bps IS NULL), speed_bps DESC',
            'recent':  '(last_alive_ts IS NULL), last_alive_ts DESC',
            'created': 'created_ts DESC',
        }.get(sort, '(latency_ms IS NULL), latency_ms ASC')
        sql += f' ORDER BY alive DESC, {order} LIMIT ?'
        args.append(int(limit))
        with self.lock:
            cur = self.conn.execute(sql, args)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def stats(self):
        with self.lock:
            total = self.conn.execute('SELECT COUNT(*) FROM proxies').fetchone()[0]
            alive = self.conn.execute('SELECT COUNT(*) FROM proxies WHERE alive=1').fetchone()[0]
            by = {}
            for proto in ('http', 'socks4', 'socks5'):
                t = self.conn.execute('SELECT COUNT(*) FROM proxies WHERE protocol=?', (proto,)).fetchone()[0]
                a = self.conn.execute('SELECT COUNT(*) FROM proxies WHERE protocol=? AND alive=1', (proto,)).fetchone()[0]
                by[proto] = {'total': t, 'alive': a}
            avg = self.conn.execute('SELECT AVG(latency_ms) FROM proxies WHERE alive=1').fetchone()[0]
            best = self.conn.execute(
                'SELECT addr, protocol, speed_bps, latency_ms, country FROM proxies'
                ' WHERE alive=1 AND speed_bps IS NOT NULL ORDER BY speed_bps DESC LIMIT 1').fetchone()
            untested = self.conn.execute('SELECT COUNT(*) FROM proxies WHERE check_count=0').fetchone()[0]
            logs = self.conn.execute('SELECT ts,source,found,new FROM scrape_log ORDER BY rowid DESC LIMIT 20').fetchall()
        s = {
            'total': total, 'alive': alive, 'by': by, 'untested': untested,
            'avg_latency_ms': round(avg) if avg else None,
            'best_speed': ({'addr': best[0], 'protocol': best[1],
                            'speed_kbps': round(best[2] / 1024, 1),
                            'latency_ms': round(best[3]), 'country': best[4]} if best else None),
            'last_scrape': self.get_meta('last_scrape'),
            'last_check_round': self.get_meta('last_check_round'),
            'my_ip': self.get_meta('my_ip'),
            'scrape_log': [{'ts': a, 'source': b, 'found': c, 'new': d} for a, b, c, d in logs],
        }
        return s

    def random_alive(self, protocol=None, max_latency=1500):
        sql = 'SELECT addr,ip,port,protocol,latency_ms,speed_bps,country,anonymity FROM proxies WHERE alive=1'
        args = []
        if protocol and protocol != 'all':
            sql += ' AND protocol=?'; args.append(protocol)
        sql += ' AND (latency_ms IS NULL OR latency_ms<?) ORDER BY RANDOM() LIMIT 1'
        args.append(max_latency)
        with self.lock:
            cur = self.conn.execute(sql, args)
            cols = [d[0] for d in cur.description]
            r = cur.fetchone()
        return dict(zip(cols, r)) if r else None

    def get_proxy(self, addr):
        with self.lock:
            cur = self.conn.execute('SELECT * FROM proxies WHERE addr=?', (addr,))
            cols = [d[0] for d in cur.description]
            r = cur.fetchone()
        return dict(zip(cols, r)) if r else None


STATE = {
    'testing': False, 'round_size': 0, 'round_done': 0, 'round_stage': '',
    'last_round_end': '', 'rounds_done': 0, 'last_error': '',
    'scraping': False, 'started': now_ts(),
}
