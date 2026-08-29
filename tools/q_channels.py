# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, '.')
from geo.models import db as database

with database.session_scope() as s:
    rows = (s.query(database.DistributionChannelTask)
            .order_by(database.DistributionChannelTask.id.desc()).limit(8).all())
    print("最新渠道任务：")
    for r in rows:
        print("#%d draft=%s [%s] %s url=%s" % (
            r.id, r.draft_id, r.platform, r.status, r.platform_url))
        if r.error_msg:
            print("   err:", r.error_msg[:300])
    drafts = (s.query(database.DistributionDraft)
              .order_by(database.DistributionDraft.id.desc()).limit(3).all())
    print("最新稿件：")
    for d in drafts:
        print("#%d [%s] %s" % (d.id, d.status, d.title))
