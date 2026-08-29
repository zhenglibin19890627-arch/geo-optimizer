# -*- coding: utf-8 -*-
"""监视 bridge.log：发现扩展重载（宿主重新拉起）后自动重试三平台分发并跟踪到终态。
用法：python bridge/watch_reload_and_retry.py [--max-minutes 45]
"""
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "bridge.log")
BASE = "http://127.0.0.1:5080"
TASK_IDS = [1, 2, 3]


def startup_count():
    try:
        with io.open(LOG, encoding="utf-8", errors="ignore") as f:
            return sum(1 for line in f if "宿主启动" in line)
    except OSError:
        return 0


def print_status(tag):
    from geo.models import db as database
    with database.session_scope() as s:
        rows = (s.query(database.DistributionChannelTask)
                .filter(database.DistributionChannelTask.id.in_(TASK_IDS)).all())
        for r in rows:
            print(f"[{tag}] #{r.id} [{r.platform}] {r.status} url={r.platform_url} "
                  f"err={(r.error_msg or '-')[:160]}")


def main():
    max_minutes = 45
    if "--max-minutes" in sys.argv:
        max_minutes = int(sys.argv[sys.argv.index("--max-minutes") + 1])

    baseline = startup_count()
    print(f"监视中（基线宿主启动次数={baseline}）。请在浏览器扩展卡片点「重新加载」，"
          f"我会自动重试分发并跟踪结果；最长等 {max_minutes} 分钟。", flush=True)

    deadline = time.time() + max_minutes * 60
    while time.time() < deadline:
        time.sleep(8)
        if startup_count() <= baseline:
            continue
        print("检测到扩展重载（宿主重新拉起），8 秒后重试分发…", flush=True)
        time.sleep(8)
        for tid in TASK_IDS:
            try:
                requests.post(f"{BASE}/api/distribution/channels/{tid}/retry", timeout=10)
            except Exception as e:
                print("retry 失败:", e, flush=True)
        # 跟踪到终态
        sub_deadline = time.time() + 300
        last = ""
        while time.time() < sub_deadline:
            from geo.models import db as database
            with database.session_scope() as s:
                rows = (s.query(database.DistributionChannelTask)
                        .filter(database.DistributionChannelTask.id.in_(TASK_IDS)).all())
                states = [(r.platform, r.status, r.platform_url, r.error_msg) for r in rows]
            line = " | ".join(f"{p}:{st}" for p, st, _, _ in states)
            if line != last:
                print(time.strftime("%H:%M:%S"), line, flush=True)
                last = line
            if all(st in ("published", "failed") for _, st, _, _ in states):
                print_status("最终")
                return
            time.sleep(6)
        print_status("超时未到终态")
        return
    print("等待重载超时，退出。", flush=True)


if __name__ == "__main__":
    main()
