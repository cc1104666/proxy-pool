# -*- coding: utf-8 -*-
"""run.py — 代理池入口
  python run.py scrape           一次性抓取全网源入库
  python run.py check [N]        跑 N 轮测速流水线
  python run.py serve [--port 8899] [--host 127.0.0.1]   全自动服务(推荐)
  python run.py all              抓取+测速+面板 一条龙
"""
import sys
import time
import threading

sys.stdout.reconfigure(encoding='utf-8')

import core
from core import DB, SCRAPE_INTERVAL, now_iso


def initial_bootstrap(db: DB, state):
    """空库时先抓一轮, 已有数据直接开测"""
    if db.total() < 1000:
        print('[bootstrap] 库内代理不足, 先抓取全网源…')
        import scraper
        s = scraper.scrape_all(db, state)
        new = sum(x['new'] for x in s)
        ok = sum(1 for x in s if x['ok'])
        print(f'[bootstrap] 抓取完成: {ok}/{len(s)} 源成功, 新增 {new}, 总量 {db.total()}')


def check_loop(db: DB, state, my_ip):
    import checker
    while True:
        if not state['testing']:
            try:
                r = checker.check_round(db, my_ip, state)
                if r.get('picked'):
                    print(f"[check] 轮次{state['rounds_done']}: 探测{r['picked']} "
                          f"TCP通过{r['tcp_ok']} 存活{r['alive']} "
                          f"测速{r['speed_tested']} · 库内存活 {db.stats()['alive']}")
                    time.sleep(2)
                else:
                    time.sleep(30)   # 队列暂空, 等 30s 再取
            except Exception as e:
                state['testing'] = False
                state['last_error'] = f'{type(e).__name__}: {e}'
                print(f'[check] 轮次异常: {e}')
                time.sleep(5)
        else:
            time.sleep(3)


def scrape_loop(db: DB, state, interval=SCRAPE_INTERVAL):
    import scraper
    while True:
        try:
            if not state.get('scraping'):
                print(f"[scrape] {now_iso()} 周期抓取开始…")
                scraper.scrape_all(db, state)
                print(f"[scrape] 完成, 总量 {db.total()}")
                # h-acker.cc (PoW 站) 附带爬取
                try:
                    import hackercc
                    items, total = hackercc.fetch_all()
                    rows = hackercc.normalize(items)
                    new = db.insert_proxies(rows)
                    db.log_scrape('h-acker.cc', len(rows), new)
                    print(f"[scrape] h-acker.cc: {len(rows)} 条, 新增 {new}")
                except Exception as e:
                    print(f"[scrape] h-acker.cc 失败: {type(e).__name__}: {e}")
        except Exception as e:
            state['scraping'] = False
            state['last_error'] = f'scrape: {e}'
        time.sleep(interval)


def cmd_scrape():
    db = DB()
    import scraper
    s = scraper.scrape_all(db, None)
    tot_new = sum(x['new'] for x in s)
    ok = sum(1 for x in s if x['ok'])
    print(f"抓取完成: {ok}/{len(s)} 源成功, 新增 {tot_new}, 库内总量 {db.total()}")
    for x in s:
        print(f"  [{'✓' if x['ok'] else '✗'}] {x['source']:22s} found={x['found']:6d} new={x['new']:6d}")


def cmd_check(rounds=1):
    db = DB()
    import checker
    my_ip = checker.get_my_ip(db)
    print(f'本机出口 IP: {my_ip}')
    for i in range(rounds):
        t0 = time.time()
        r = checker.check_round(db, my_ip, core.STATE)
        print(f"第{i+1}轮: 探测{r.get('picked', 0)} TCP通过{r.get('tcp_ok', 0)} "
              f"存活{r.get('alive', 0)} 测速{r.get('speed_tested', 0)} "
              f"耗时{time.time()-t0:.0f}s · 库内存活 {db.stats()['alive']}")


def cmd_serve(host='127.0.0.1', port=8899):
    db = DB()
    state = core.STATE
    import checker
    my_ip = checker.get_my_ip(db)
    db.set_meta('my_ip', my_ip)
    print(f'[init] 本机出口 IP: {my_ip}')
    initial_bootstrap(db, state)

    threading.Thread(target=check_loop, args=(db, state, my_ip), daemon=True).start()
    threading.Thread(target=scrape_loop, args=(db, state), daemon=True).start()
    print(f'[init] 后台流水线已启动: 每轮 探活→验证→测速→匿名度, 存活代理每 10 分钟复测')

    import server
    server.serve(db, state, host=host, port=port, my_ip=my_ip)


def main():
    import argparse
    ap = argparse.ArgumentParser(description='全网代理池: 抓取+入库+实时测速服务')
    ap.add_argument('cmd', choices=['scrape', 'check', 'serve', 'all'], help='子命令')
    ap.add_argument('rounds', nargs='?', type=int, default=1, help='check 轮数')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8899)
    a = ap.parse_args()
    if a.cmd == 'scrape':
        cmd_scrape()
    elif a.cmd == 'check':
        cmd_check(a.rounds)
    elif a.cmd == 'serve':
        cmd_serve(a.host, a.port)
    elif a.cmd == 'all':
        cmd_scrape()
        cmd_serve(a.host, a.port)


if __name__ == '__main__':
    main()
