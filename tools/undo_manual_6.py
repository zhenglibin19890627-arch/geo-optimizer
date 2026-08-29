# -*- coding: utf-8 -*-
"""撤销 #6 渠道被误标的手动发布状态（恢复为 failed + 原现场信息）。"""
import sys
sys.path.insert(0, '.')
from datetime import datetime
from geo.models import db as database

ORIG_ERR = ("头条发布未能自动确认——编辑器标签页已保留，请手动完成发布（内容已注入，"
            "检查右侧「发表设置」后点右上角「发布」即可）。现场： 关键词[封面,封面] iframe=0 "
            "|| 标题=头条号 URL=https://mp.toutiao.com/profile_v4/graphic/publish "
            "|| 提示[消息 9 ;; 查看成长权益 ;; 头条认证未认证]")

with database.session_scope() as s:
    row = s.get(database.DistributionChannelTask, 6)
    print("当前状态:", row.status, "|", (row.platform_url or "")[:50])
    row.status = "failed"
    row.platform_url = ""
    row.error_msg = ORIG_ERR
    row.published_at = None
    row.updated_at = datetime.now()
    # 联动稿件状态交还用户决定：若稿件 published_url 来自本渠道则清掉标记
    draft = s.get(database.DistributionDraft, row.draft_id)
    if draft and draft.published_url and "mp.toutiao.com" in draft.published_url:
        draft.published_url = ""
print("已恢复为 failed")
