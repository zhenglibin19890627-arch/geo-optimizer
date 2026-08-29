# -*- coding: utf-8 -*-
"""临时检查：分发桥状态 + 渠道任务一览（联调用，可删）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from geo.models import db as database
from geo.core import distribution

print("agent_online:", distribution.agent_online())
with database.session_scope() as s:
    rows = (s.query(database.DistributionChannelTask)
            .order_by(database.DistributionChannelTask.id.desc()).limit(10).all())
    print("channel tasks:", len(rows))
    for r in rows:
        url = r.platform_url or "-"
        err = (r.error_msg or "-")[:70]
        print("#%s [%s] %s url=%s err=%s" % (r.id, r.platform, r.status, url, err))
