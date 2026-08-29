# -*- coding: utf-8 -*-
"""联调测试：插入测试稿 → 排队三平台 → 轮询分发结果。
用法：python bridge/test_dispatch.py [--poll 180]
说明：知乎会真实发布（发布后可在知乎后台删除）；搜狐/头条适配器首版只验证
编辑器可达与注入，会在真正发布前主动中止并报告原因。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from geo.models import db as database

TITLE = "【联调测试】GEO 自研扩展发布链路验证（可删除）"
BODY = (
    "## 这是一篇联调测试稿\n\n"
    "本文章由 **GEO 优化系统** 的多平台分发链路自动发布，用于验证：\n\n"
    "- 审阅 → 勾选平台 → 一键分发的完整闭环\n"
    "- 自研发布扩展的编辑器注入与落地页回传\n\n"
    "看到这篇文章说明发布链路已经打通，可直接删除。谢谢！\n\n"
    "详情见系统: http://127.0.0.1:5080/\n"
)
SUMMARY = "GEO 自研扩展发布链路联调测试稿，验证后可删除。"
TAGS = "GEO,联调测试"


def main():
    poll_seconds = 180
    if "--poll" in sys.argv:
        poll_seconds = int(sys.argv[sys.argv.index("--poll") + 1])

    now = database.now()
    with database.session_scope() as s:
        draft = database.DistributionDraft(
            brand_id=1, title=TITLE, body_md=BODY, summary=SUMMARY, tags=TAGS,
            status="draft", created_at=now)
        s.add(draft)
        s.flush()
        draft_id = draft.id
    print(f"测试稿已创建 draft_id={draft_id}")

    r = requests.post(
        f"http://127.0.0.1:5080/api/distribution/drafts/{draft_id}/channels",
        json={"platforms": ["zhihu", "sohu", "toutiao"]}, timeout=10)
    print("排队接口:", r.json().get("message", r.text[:120]))

    deadline = time.time() + poll_seconds
    last_line = ""
    while time.time() < deadline:
        with database.session_scope() as s:
            rows = (s.query(database.DistributionChannelTask)
                    .filter(database.DistributionChannelTask.draft_id == draft_id).all())
            states = {r.platform: (r.status, r.platform_url or "", (r.error_msg or "")[:80])
                      for r in rows}
        line = " | ".join(
            f"{p}:{st}" + (f"→{url}" if url else "") + (f" err={err}" if err and st == "failed" else "")
            for p, (st, url, err) in states.items())
        if line != last_line:
            print(time.strftime("%H:%M:%S"), line)
            last_line = line
        if states and all(st in ("published", "failed") for st, _, _ in states.values()):
            print("全部到达终态。")
            break
        time.sleep(5)

    with database.session_scope() as s:
        rows = (s.query(database.DistributionChannelTask)
                .filter(database.DistributionChannelTask.draft_id == draft_id).all())
        for r in rows:
            print(f"最终 #{r.id} [{r.platform}] {r.status} url={r.platform_url} err={r.error_msg}")
        print(f"draft_id={draft_id}（知乎发布成功后可在知乎后台删除该文）")


if __name__ == "__main__":
    main()
