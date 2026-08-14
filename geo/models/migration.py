"""数据库升级迁移（多品牌基础层 A，02d 3.1.2）。

流程：VACUUM INTO 文件级备份 → PRAGMA 幂等检查 + 单事务 DDL → 存量数据归属品牌 1
→ 存量竞品/别名整串拆分回填 → user_version=4。迁移失败整体回滚并打印大白话后
退出，不带着半成品结构启动。

事务说明：SQLite 的 ALTER TABLE 可随事务回滚，但 SQLAlchemy sqlite 方言在 DDL 前
隐式提交（engine.begin() 无法回滚 DDL），故本模块改用原生连接 + 显式
BEGIN/COMMIT/ROLLBACK（isolation_level=None 关闭驱动隐式事务）。
"""

import json
import re
import sys
import traceback
from datetime import datetime

from geo import config
from geo.models import db as database

MIGRATION_VERSION = 7

# 与保存口径 api_config._split_list 同正则（顿号/逗号/分号/换行），
# 用于存量整串数据的一次性拆分回填，避免两处正则分叉
_LIST_SPLIT_RE = re.compile(r"[,，;；、\n]")
# 需要做存量整串拆分的品牌档案 JSON 数组字段
_LEGACY_SPLIT_FIELDS = ("competitors", "brand_aliases")


def _split_legacy_field(raw: str):
    """对品牌档案 JSON 数组字段做一次性拆分回填。

    元素仍含分隔符（顿号/逗号/分号/换行）的整串拆成独立元素，去空白、去重；
    已是独立元素的数组原样返回 None（不产生 UPDATE，幂等）。
    """
    if not raw:
        return None
    try:
        items = json.loads(raw)
    except Exception:
        return None
    if not isinstance(items, list):
        return None
    out = []
    seen = set()
    changed = False
    for item in items:
        if not isinstance(item, str):
            continue
        if _LIST_SPLIT_RE.search(item):
            changed = True
            for piece in _LIST_SPLIT_RE.split(item):
                piece = piece.strip()
                if piece and piece not in seen:
                    seen.add(piece)
                    out.append(piece)
        else:
            piece = item.strip()
            if piece and piece not in seen:
                seen.add(piece)
                out.append(piece)
    if not changed:
        return None
    return json.dumps(out, ensure_ascii=False)


# 需要加 brand_id 列并做存量归属的业务表（api_call_log 不加列：Q4=A 全局汇总）
BUSINESS_TABLES = [
    "question_bank",
    "keyword",
    "monitor_task",
    "monitor_round",
    "monitor_result",
    "score_snapshot",
    "alert",
    "optimization_record",
]


def _raw_conn():
    """原生连接：关闭 sqlite3 驱动的隐式事务管理，事务由本模块显式控制。"""
    conn = database.engine.raw_connection()
    conn.isolation_level = None
    return conn


def _columns(cur, table: str) -> list:
    return [row[1] for row in cur.execute(f"PRAGMA table_info({table})").fetchall()]


def run_migrations():
    """幂等迁移到 user_version=4；已到位则零操作直接返回。

    版本历史：
      v2（批次 A）：8 张业务表加 brand_id + brand_profile 加 auto_monitor/created_at
      v3（批次 C）：monitor_round / monitor_task 加 mode 列（normal=常规 / web=联网），存量行回填 normal
      v4（批次 D）：纯数据回填 + 时序修复——
        ① 全新空库不再插入 id=1 空名品牌占位行（缺省回退由 database.get_brand
           空档案兜底，首启引导创建的品牌自然落到 id=1，不再多占 5 个品牌上限名额）；
        ② brand_profile.competitors/brand_aliases 存量整串（缺陷 #12 修复前遗留）
           一次性拆分回填，与保存口径同正则、幂等。
    """
    conn = _raw_conn()
    try:
        cur = conn.cursor()
        version = cur.execute("PRAGMA user_version").fetchone()[0] or 0
        if version >= MIGRATION_VERSION:
            return

        # 备份：VACUUM INTO 文件级一致快照（WAL 安全；目标已存在则先删除）
        backup_path = config.DATA_DIR / f"geo.db.bak-{datetime.now():%Y%m%d}"
        if backup_path.exists():
            backup_path.unlink()
        cur.execute(f"VACUUM INTO '{backup_path.as_posix()}'")

        # 单事务 DDL + 存量归属（任一失败整体回滚，回到升级前原样）
        cur.execute("BEGIN")
        try:
            # ---- v2（批次 A）----
            for table in BUSINESS_TABLES:
                if "brand_id" not in _columns(cur, table):
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN brand_id INTEGER")
            bp_cols = _columns(cur, "brand_profile")
            if "auto_monitor" not in bp_cols:
                cur.execute("ALTER TABLE brand_profile ADD COLUMN auto_monitor BOOLEAN DEFAULT 1")
            if "created_at" not in bp_cols:
                cur.execute("ALTER TABLE brand_profile ADD COLUMN created_at DATETIME")

            # 存量归属品牌 1（一条不落，全部 UPDATE 无删改）
            for table in BUSINESS_TABLES:
                cur.execute(f"UPDATE {table} SET brand_id=1 WHERE brand_id IS NULL")

            # ---- v3（批次 C）：监测模式列（normal=常规提问 / web=联网提问）----
            # monitor_task 冗余 mode 用于把模式带给工作线程（轮次由任务创建）
            for table in ("monitor_round", "monitor_task"):
                if "mode" not in _columns(cur, table):
                    cur.execute(
                        f"ALTER TABLE {table} ADD COLUMN mode VARCHAR(10) DEFAULT 'normal'")
            for table in ("monitor_round", "monitor_task"):
                cur.execute(f"UPDATE {table} SET mode='normal' WHERE mode IS NULL OR mode=''")

            # ---- v4（批次 D）：存量竞品/别名整串一次性拆分回填（幂等）----
            # 缺陷 #12 修复前保存的整串（如 ["移动、电信、科大讯飞、希沃"]）拆成
            # 独立元素；已是独立元素的数组不产生 UPDATE。字段仍为 JSON 数组。
            for rid, comps, aliases in cur.execute(
                    "SELECT id, competitors, brand_aliases FROM brand_profile").fetchall():
                sets, params = [], []
                if comps:
                    new_comps = _split_legacy_field(comps)
                    if new_comps is not None:
                        sets.append("competitors=?")
                        params.append(new_comps)
                if aliases:
                    new_aliases = _split_legacy_field(aliases)
                    if new_aliases is not None:
                        sets.append("brand_aliases=?")
                        params.append(new_aliases)
                if sets:
                    params.append(rid)
                    cur.execute(
                        f"UPDATE brand_profile SET {', '.join(sets)} WHERE id=?", params)

            # ---- v5（批次 E）：monitor_result 加 error_msg 列 ----
            # 单条调用失败的原因落库（如模型不存在/钥匙失效），供报告页展示
            # “这家没回答”的具体原因，不再静默吞掉。
            if "error_msg" not in _columns(cur, "monitor_result"):
                cur.execute("ALTER TABLE monitor_result ADD COLUMN error_msg TEXT")

            # ---- v6（批次 F）：monitor_round 加 auto_competitors 列 ----
            # 每轮监测收尾时由分析模型自动提取回答中出现的品牌名，纳入竞品分析
            if "auto_competitors" not in _columns(cur, "monitor_round"):
                cur.execute("ALTER TABLE monitor_round ADD COLUMN auto_competitors TEXT")

            # ---- v7（批次 G）：同 key 多模型监测 ----
            # monitor_result 记录实际调用模型；monitor_task 存 {engine: [models]} 清单
            if "model" not in _columns(cur, "monitor_result"):
                cur.execute("ALTER TABLE monitor_result ADD COLUMN model VARCHAR(100)")
            if "models" not in _columns(cur, "monitor_task"):
                cur.execute("ALTER TABLE monitor_task ADD COLUMN models TEXT")

            # 版本号锚点（与 DDL 同事务，回滚即恢复旧版本号）
            cur.execute(f"PRAGMA user_version = {MIGRATION_VERSION}")
            cur.execute("COMMIT")
        except Exception:
            try:
                cur.execute("ROLLBACK")
            except Exception:
                pass
            raise
    except Exception:
        print("【重要】数据库升级没成功，数据没有损坏，"
              "请把控制台里的红色信息发给开发者。")
        traceback.print_exc()
        sys.exit(1)
    finally:
        try:
            conn.close()
        except Exception:
            pass
