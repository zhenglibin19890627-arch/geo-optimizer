# -*- coding: utf-8 -*-
"""诊断：腾讯混元（TokenHub）联网档是否真的在联网搜索。

三变体实测（每个变体一次真实 API 调用，费用约几厘）：
  A. tools:[{"type":"web_search"}]   —— 混元官网同款工具
  B. enable_enhancement 等增强参数   —— 旧方案
  C. 裸调用（对照组）
判定依据：响应里是否出现 search_info / citation / web_search 等搜索痕迹字段；
回答内容是否为"知道检索到的事实"。
用法：python tools/diag_yuanbao_web.py [问题文本]
"""
import json
import sqlite3
import sys

import requests

from geo import config


def show_db_evidence():
    """最近元宝联网档的回答与信源落库情况。"""
    c = sqlite3.connect("data/geo.db")
    c.row_factory = sqlite3.Row
    tables = [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    rt = [t for t in tables if "result" in t.lower()]
    print("== 结果表：", rt)
    for t in rt:
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
        print(f"== {t} 字段：", cols)
        eng_col = "engine" if "engine" in cols else None
        if not eng_col:
            continue
        rows = c.execute(
            f"SELECT * FROM {t} WHERE engine='yuanbao' ORDER BY id DESC LIMIT 3").fetchall()
        for r in rows:
            d = dict(r)
            print("-" * 60)
            for k in ("id", "task_id", "model", "created_at", "answer", "sources"):
                if k in d:
                    v = str(d[k] or "")
                    print(f"  {k}: {v[:220]}")


def probe(question: str):
    cfg = config.get_engine_config("yuanbao")
    key = (cfg.get("api_key") or "").strip()
    base = str(cfg.get("base_url") or "").rstrip("/")
    model = cfg.get("model") or "hy3"
    if not key:
        print("!! engines.yuanbao 的 api_key 没填，无法实测")
        return
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    msgs = [{"role": "user", "content": question}]

    variants = {
        "A.tools(web_search)": {"tools": [{"type": "web_search"}]},
        "B.增强参数": {"enable_enhancement": True, "search_info": True,
                       "citation": True, "force_search_enhancement": True},
        "C.裸调用(对照)": {},
    }
    marks = ("search_info", "citation", "web_search", "search_results", "citations")
    for name, extra in variants.items():
        payload = {"model": model, "messages": msgs}
        payload.update(extra)
        try:
            resp = requests.post(base + "/chat/completions", json=payload,
                                 headers=headers, timeout=90)
        except Exception as e:
            print(f"\n### {name}：请求失败 {e}")
            continue
        print(f"\n### {name} → HTTP {resp.status_code}")
        try:
            data = resp.json()
        except Exception:
            print("  非JSON：", resp.text[:200])
            continue
        if resp.status_code != 200:
            print("  错误：", resp.text[:300])
            continue
        flat = json.dumps(data, ensure_ascii=False)
        hits = [m for m in marks if m in flat]
        print("  搜索痕迹字段：", hits or "（无——本次回答大概率没联网）")
        try:
            msg = data["choices"][0]["message"]
            print("  message 附加字段：", [k for k in msg.keys() if k not in ("role", "content")])
            print("  回答开头：", (msg.get("content") or "")[:160].replace("\n", " "))
        except Exception:
            print("  结构异常：", flat[:300])


if __name__ == "__main__":
    show_db_evidence()
    q = sys.argv[1] if len(sys.argv) > 1 else "今天（最新）有什么科技新闻？列两条并说明信息来源。"
    print("\n========== 实测（问题：", q, "）==========")
    probe(q)
