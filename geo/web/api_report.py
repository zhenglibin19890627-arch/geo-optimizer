"""报告与首页接口：概览 / 趋势 / 竞品 / 信源（#1、#19~#21）。"""

import re

from flask import Blueprint, request

from geo.models import db as database
from geo.core import monitor_task, scheduler
from geo.web import ApiError, current_brand_id, ok

bp = Blueprint("api_report", __name__)


# ---------------- #1 首页聚合 ----------------
@bp.route("/overview", methods=["GET"])
def overview():
    brand_id = current_brand_id()
    with database.session_scope() as s:
        # 最新评分 + 近 30 次评分趋势（按品牌）
        snaps = (s.query(database.ScoreSnapshot)
                 .filter(database.ScoreSnapshot.brand_id == brand_id)
                 .order_by(database.ScoreSnapshot.id.desc()).limit(30).all())
        snaps = list(reversed(snaps))
        score = snaps[-1].score if snaps else None
        score_trend = [x.score for x in snaps]
        # 评分分项与总分同源：直接读最新快照 breakdown，避免前端重算导致分项加总≠总分
        score_breakdown = database.jloads(snaps[-1].breakdown, {}) if snaps else None

        # 未读预警（最多 5 条，按品牌）
        alerts = (s.query(database.Alert)
                  .filter(database.Alert.is_read == False)
                  .filter(database.Alert.brand_id == brand_id)
                  .order_by(database.Alert.id.desc()).limit(5).all())
        unread_alerts = [a.to_dict() for a in alerts]

        # 最近一轮摘要（只取正常完成的轮次，cancelled/failed 不计入统计口径）
        status_map = monitor_task.task_status_map(s)
        all_rounds = (s.query(database.MonitorRound)
                      .filter(database.MonitorRound.brand_id == brand_id)
                      .order_by(database.MonitorRound.id.desc()).all())
        normal_rounds = [r for r in all_rounds
                         if monitor_task.round_is_normal(status_map, r)]
        last_round = normal_rounds[0] if normal_rounds else None
        last_round_data = None
        if last_round:
            d = last_round.to_dict()
            d["mentioned_answers"] = 0
            d["total_answers"] = 0
            summary = database.jloads(last_round.summary, {}) or {}
            d["mentioned_answers"] = summary.get("mentioned_answers", 0)
            d["total_answers"] = summary.get("total_answers", 0)
            last_round_data = d

        # 引导提示（大白话）：轮次数只算正常完成的轮次
        round_count = len(normal_rounds)
        brand = database.get_brand(brand_id)
        hints = []
        if not brand.get("brand_name"):
            hints.append("还没有填写品牌信息，请先到「设置」页填一下品牌名")
        configured = _configured_engines_count(s)
        if configured == 0:
            hints.append("还没有填任何 AI 引擎的钥匙（API Key），请到「设置」页填写后就能发起监测")
        if 0 < round_count < 3:
            hints.append(f"再监测 {3 - round_count} 轮后，系统就能帮你盯着数据变化了")

        return ok({
            "score": score,
            "score_trend": score_trend,
            "score_breakdown": score_breakdown,
            "unread_alerts": unread_alerts,
            "last_round": last_round_data,
            "next_run_time": scheduler.next_run_time(),
            "round_count": round_count,
            "hints": hints,
        }, "获取成功")


def _configured_engines_count(s) -> int:
    from geo.engines import AUTO_CODES, get_adapter
    count = 0
    for code in AUTO_CODES:
        try:
            if get_adapter(code).is_configured():
                count += 1
        except Exception:
            pass
    return count


# ---------------- #19 趋势图 ----------------
@bp.route("/report/trend", methods=["GET"])
def report_trend():
    brand_id = current_brand_id()
    metric = request.args.get("metric", "score")
    mode = str(request.args.get("mode") or "normal").strip() or "normal"
    if mode not in ("normal", "web"):
        raise ApiError("这个模式不认，请选择「常规提问」或「联网提问」")
    rounds = max(int(request.args.get("rounds") or 30), 1)
    if metric not in ("score", "mention_rate", "sentiment"):
        raise ApiError("这个指标类型不认识，请选择 评分/提及率/情感 之一")

    with database.session_scope() as s:
        # 趋势序列只取正常完成的轮次（cancelled/failed 不计入）；
        # 常规/联网轮次按模式各自独立统计（02d 5.2）
        status_map = monitor_task.task_status_map(s)
        all_rows = [r for r in (s.query(database.MonitorRound)
                                .filter(database.MonitorRound.brand_id == brand_id,
                                        database.MonitorRound.mode == mode)
                                .order_by(database.MonitorRound.id.desc()).all())
                    if monitor_task.round_is_normal(status_map, r)]
        rows = list(reversed(all_rows[:rounds]))
        labels = []
        values = []
        for i, r in enumerate(rows, 1):
            labels.append(f"第{i}轮")
            if metric == "score":
                values.append(r.overall_score)
            elif metric == "mention_rate":
                values.append(round(r.mention_rate * 100, 1) if r.mention_rate is not None else None)
            else:
                values.append(round(r.net_sentiment * 100, 1) if r.net_sentiment is not None else None)
        return ok({"labels": labels, "values": values}, "获取成功")


# ---------------- #20 竞品提及对比 ----------------
@bp.route("/report/competitor", methods=["GET"])
def report_competitor():
    brand_id = current_brand_id()
    round_id = request.args.get("round_id", type=int)
    with database.session_scope() as s:
        if round_id:
            round_row = s.get(database.MonitorRound, round_id)
            if not round_row:
                raise ApiError("这轮监测不存在，可能已被清理")
            if (round_row.brand_id or 1) != brand_id:
                raise ApiError("这轮数据属于其他品牌，切换品牌后再查看吧")
            results = (s.query(database.MonitorResult)
                       .filter(database.MonitorResult.round_id == round_id).all())
        else:
            round_row = (s.query(database.MonitorRound)
                         .filter(database.MonitorRound.brand_id == brand_id)
                         .order_by(database.MonitorRound.id.desc()).first())
            if not round_row:
                raise ApiError("还没有任何监测数据，先发起一轮监测吧")
            results = (s.query(database.MonitorResult)
                       .filter(database.MonitorResult.round_id == round_row.id).all())

        answered = [r for r in results if r.answer_text]
        total = max(len(answered), 1)
        brand = database.get_brand(brand_id)
        auto = database.jloads(round_row.auto_competitors, []) or []
        # 2026-08-15：竞品一律取本轮自动提取名单
        entities = [brand.get("brand_name") or "我方"] + list(auto)
        items = []
        for name in entities:
            name = str(name).strip()
            if not name or name == "我方":
                continue
            count = len([r for r in answered if name in (r.answer_text or "")])
            items.append({"name": name, "mention_count": count,
                          "mention_rate": round(count / total, 3),
                          "is_self": name == (brand.get("brand_name") or "")})
        # 去重（自动提取名单可能含重复项）
        seen = set()
        items = [it for it in items
                 if not (it["name"] in seen or seen.add(it["name"]))]
        return ok({
            "round_id": round_row.id,
            "round_time": round_row.created_at.strftime("%Y-%m-%d %H:%M:%S") if round_row.created_at else None,
            "items": items,
        }, "获取成功")


# ---------------- #21 引用信源分析 ----------------
@bp.route("/report/sources", methods=["GET"])
def report_sources():
    brand_id = current_brand_id()
    round_id = request.args.get("round_id", type=int)
    with database.session_scope() as s:
        if round_id:
            round_row = s.get(database.MonitorRound, round_id)
            if not round_row:
                raise ApiError("这轮监测不存在，可能已被清理")
            if (round_row.brand_id or 1) != brand_id:
                raise ApiError("这轮数据属于其他品牌，切换品牌后再查看吧")
            results = (s.query(database.MonitorResult)
                       .filter(database.MonitorResult.round_id == round_id).all())
        else:
            # 最近 30 轮汇总：聚合这些轮次的所有结果
            rounds = _recent_normal_rounds(s, brand_id, limit=30)
            if not rounds:
                return ok([], "获取成功")
            rids = [r.id for r in rounds]
            results = (s.query(database.MonitorResult)
                       .filter(database.MonitorResult.round_id.in_(rids)).all())
        # 统一信源按规范化域名合并（m./www. 前缀归一同站），累计次数并记录各引擎引用
        from geo.analyzers import sources as sources_mod
        agg = {}
        for r in results:
            code = r.engine_code or ""
            for src in database.jloads(r.sources, []) or []:
                url = src.get("url", "")
                if not url:
                    continue
                domain = sources_mod.normalize_domain(
                    src.get("domain") or "")
                if not domain:
                    continue
                item = agg.setdefault(domain, {"domain": domain, "url": url,
                                               "count": 0, "engines": {}})
                item["count"] += 1
                eng = item["engines"].setdefault(code, {"engine_code": code, "count": 0})
                eng["count"] += 1
        from geo.engines import get_adapter as _get_adapter
        items = []
        for domain, item in sorted(agg.items(), key=lambda kv: -kv[1]["count"]):
            eng_list = []
            for code, e in sorted(item["engines"].items(), key=lambda kv: -kv[1]["count"]):
                try:
                    name = _get_adapter(code).display_name
                except Exception:
                    name = code or ""
                eng_list.append({"engine_code": code, "display_name": name, "count": e["count"]})
            items.append({
                "domain": item["domain"], "url": item["url"],
                "site_name": sources_mod.site_name(item["domain"]),
                "category": sources_mod.classify_domain(item["domain"]),
                "count": item["count"],
                "engines": eng_list,
            })
        return ok(items, "获取成功")


# ---------------- N11 竞品本轮对比统计（规则法，零费用） ----------------
def _resolve_round(s, brand_id: int, round_id):
    """显式 round_id 需归属校验；缺省取该品牌最近正常轮（cancelled/failed 不计入）。"""
    if round_id:
        round_row = s.get(database.MonitorRound, round_id)
        if not round_row:
            raise ApiError("这轮监测不存在，可能已被清理")
        if (round_row.brand_id or 1) != brand_id:
            raise ApiError("这轮数据属于其他品牌，切换品牌后再查看吧")
        return round_row
    status_map = monitor_task.task_status_map(s)
    rows = [r for r in (s.query(database.MonitorRound)
                        .filter(database.MonitorRound.brand_id == brand_id)
                        .order_by(database.MonitorRound.id.desc()).all())
            if monitor_task.round_is_normal(status_map, r)]
    if not rows:
        raise ApiError("还没有任何监测数据，先发起一轮监测吧")
    return rows[0]


def _recent_normal_rounds(s, brand_id: int, limit: int = 30, mode: str = None) -> list:
    """最近 limit 个正常轮（task done/缺失；可选按模式过滤），按 id 升序返回。

    与趋势图口径一致：cancelled/failed 不计入；“最近 30 轮汇总”用它取数。
    """
    status_map = monitor_task.task_status_map(s)
    q = (s.query(database.MonitorRound)
         .filter(database.MonitorRound.brand_id == brand_id))
    if mode:
        q = q.filter(database.MonitorRound.mode == mode)
    rows = [r for r in q.order_by(database.MonitorRound.id.desc()).all()
            if monitor_task.round_is_normal(status_map, r)]
    return list(reversed(rows[:limit]))


def _competitor_stats(s, round_id: int, competitors: list, self_name: str,
                      brand_names: list = None) -> list:
    """逐实体统计：{name, mention_count, mentioned_answers, avg_first_position, is_self}。

    竞品名单一律取本轮自动提取名单，统一规则法现算
    （mention.competitor_mentions），口径一致且不依赖落库明细。
    """
    from geo.analyzers import mention as mention_mod
    results = (s.query(database.MonitorResult)
               .filter(database.MonitorResult.round_id == round_id).all())
    answered = [r for r in results if r.answer_text]
    items = []
    if self_name:
        self_hits = [r for r in answered if r.is_mentioned]
        positions = [r.mention_position for r in self_hits
                     if r.mention_position is not None]
        items.append({
            "name": self_name,
            "mention_count": sum(r.mention_count or 0 for r in self_hits),
            "mentioned_answers": len(self_hits),
            "avg_first_position": round(sum(positions) / len(positions), 1) if positions else None,
            "is_self": True,
        })
    comps = list(dict.fromkeys(str(c).strip() for c in competitors if str(c).strip()))
    hits_by_name = {name: [] for name in comps}
    for r in answered:
        for c in mention_mod.competitor_mentions(r.answer_text, comps, brand_names or []):
            name = str(c.get("name") or "").strip()
            if name in hits_by_name:
                hits_by_name[name].append(c)
    for name in comps:
        if name == self_name or (brand_names and name in brand_names):
            continue
        hits = hits_by_name.get(name) or []
        positions = [c.get("position") for c in hits if c.get("position") is not None]
        items.append({
            "name": name,
            "mention_count": sum(c.get("count") or 0 for c in hits),
            "mentioned_answers": len(hits),
            "avg_first_position": round(sum(positions) / len(positions), 1) if positions else None,
            "is_self": False,
        })
    return items


def _competitor_stats_multi(s, round_ids: list, competitors: list, self_name: str,
                            brand_names: list = None) -> list:
    """多轮（近 30 轮）聚合统计：跨轮累计被提到次数 / 回答条数 / 平均首提位置。"""
    from geo.analyzers import mention as mention_mod
    rows = (s.query(database.MonitorResult)
            .filter(database.MonitorResult.round_id.in_(round_ids or [])).all())
    answered = [r for r in rows if r.answer_text]
    items = []
    if self_name:
        self_hits = [r for r in answered if r.is_mentioned]
        positions = [r.mention_position for r in self_hits
                     if r.mention_position is not None]
        items.append({
            "name": self_name,
            "mention_count": sum(r.mention_count or 0 for r in self_hits),
            "mentioned_answers": len(self_hits),
            "avg_first_position": round(sum(positions) / len(positions), 1) if positions else None,
            "is_self": True,
        })
    comps = list(dict.fromkeys(str(c).strip() for c in competitors if str(c).strip()))
    hits_by_name = {name: [] for name in comps}
    for r in answered:
        for c in mention_mod.competitor_mentions(r.answer_text, comps, brand_names or []):
            name = str(c.get("name") or "").strip()
            if name in hits_by_name:
                hits_by_name[name].append(c)
    for name in comps:
        if name == self_name or (brand_names and name in brand_names):
            continue
        hits = hits_by_name.get(name) or []
        positions = [c.get("position") for c in hits if c.get("position") is not None]
        items.append({
            "name": name,
            "mention_count": sum(c.get("count") or 0 for c in hits),
            "mentioned_answers": len(hits),
            "avg_first_position": round(sum(positions) / len(positions), 1) if positions else None,
            "is_self": False,
        })
    return items


@bp.route("/report/competitor/detail", methods=["GET"])
def competitor_detail():
    brand_id = current_brand_id()
    round_id = request.args.get("round_id", type=int)
    mode = str(request.args.get("mode") or "").strip() or None
    with database.session_scope() as s:
        brand = database.get_brand(brand_id)
        from geo.analyzers import mention as mention_mod
        brand_names = mention_mod.build_brand_names(brand)
        # 2026-08-15：竞品一律来自自动提取（品牌档案竞品已取消）
        if round_id:
            round_row = _resolve_round(s, brand_id, round_id)
            auto = database.jloads(round_row.auto_competitors, []) or []
            round_time = (round_row.created_at.strftime("%Y-%m-%d %H:%M:%S")
                          if round_row.created_at else None)
            round_label = "本轮"
        else:
            # 最近 30 轮汇总（可传 mode 过滤常规/联网）：自动竞品取各轮并集
            rounds = _recent_normal_rounds(s, brand_id, limit=30, mode=mode)
            if not rounds:
                return ok({"round_id": None, "round_time": None,
                           "range": "30",
                           "items": []}, "获取成功")
            auto = []
            for rr in rounds:
                auto.extend(database.jloads(rr.auto_competitors, []) or [])
            round_time = None
            round_label = "近 30 轮"
        competitors = [str(c).strip() for c in list(auto)]
        competitors = list(dict.fromkeys(c for c in competitors if c))
        if not competitors:
            items = []
        elif round_id:
            items = _competitor_stats(s, round_id, competitors,
                                      brand.get("brand_name") or "", brand_names)
        else:
            # 30 轮聚合统计：跨轮累计
            rids = [rr.id for rr in rounds]
            items = _competitor_stats_multi(s, rids, competitors,
                                            brand.get("brand_name") or "", brand_names)
        return ok({
            "round_id": round_id,
            "round_time": round_time,
            "range": "30" if not round_id else "round",
            "round_label": round_label,
            "items": items,
        }, "获取成功")


# ---------------- N12 近 30 轮逐竞品提及序列（含我方） ----------------
def _round_mention_total(s, round_id: int, name: str) -> int:
    """本轮某竞品的提及次数合计（跨回答累加，规则法）。"""
    rows = (s.query(database.MonitorResult)
            .filter(database.MonitorResult.round_id == round_id).all())
    total = 0
    for r in rows:
        for c in database.jloads(r.competitor_mentions, []) or []:
            if str(c.get("name") or "").strip() == name:
                total += c.get("count") or 0
    return total


def _round_self_mention_total(s, round_id: int) -> int:
    rows = (s.query(database.MonitorResult)
            .filter(database.MonitorResult.round_id == round_id).all())
    return sum(r.mention_count or 0 for r in rows if r.is_mentioned)


@bp.route("/report/competitor/trend", methods=["GET"])
def competitor_trend():
    brand_id = current_brand_id()
    rounds = max(int(request.args.get("rounds") or 30), 1)
    wanted = [str(c).strip() for c in
              str(request.args.get("competitors") or "").split(",") if str(c).strip()]

    with database.session_scope() as s:
        brand = database.get_brand(brand_id)
        status_map = monitor_task.task_status_map(s)
        normal = [r for r in (s.query(database.MonitorRound)
                              .filter(database.MonitorRound.brand_id == brand_id)
                              .order_by(database.MonitorRound.id.desc()).all())
                  if monitor_task.round_is_normal(status_map, r)]
        rows = list(reversed(normal[:rounds]))
        if not rows:
            raise ApiError("还没有任何监测数据，先发起一轮监测吧")
        labels = [f"第{i}轮" for i in range(1, len(rows) + 1)]

        # 2026-08-15：竞品一律来自自动提取——取范围内各轮 auto_competitors 并集
        auto = []
        for r in rows:
            auto.extend(database.jloads(r.auto_competitors, []) or [])
        auto = list(dict.fromkeys(str(c).strip() for c in auto if str(c).strip()))

        series = []
        self_name = brand.get("brand_name") or ""
        if self_name:
            series.append({"name": self_name, "values": [
                _round_self_mention_total(s, r.id) for r in rows]})
        if wanted:
            names = [n for n in auto if n in wanted]
        else:
            names = auto
        for name in names:
            series.append({"name": name, "values": [
                _round_mention_total(s, r.id, name) for r in rows]})
        return ok({"labels": labels, "series": series}, "获取成功")


# ---------------- N13 指定竞品命中明细 ----------------
def _answer_excerpt(text: str, name: str, window: int = 120) -> str:
    """围绕命中词截取 120 字窗口（命中词前 30 字起），供前端高亮。"""
    text = text or ""
    low = text.lower()
    idx = low.find(name.lower())
    if idx < 0:
        idx = 0
    start = max(0, idx - 30)
    return text[start:start + window]


def _self_excerpt_anchor(text: str, brand_names: list, prefer: str) -> str:
    """取我方品牌明细的回答片段高亮锚点：优先传入的品牌名/别名，否则按长名优先取文本中实际出现的品牌名。"""
    text = text or ""
    low = text.lower()
    if prefer and prefer.lower() in low:
        return prefer
    for n in brand_names:
        if n and n.lower() in low:
            return n
    return prefer or (brand_names[0] if brand_names else "")


@bp.route("/report/competitor/mentions", methods=["GET"])
def competitor_mentions():
    brand_id = current_brand_id()
    round_id = request.args.get("round_id", type=int)
    competitor = str(request.args.get("competitor") or "").strip()
    if not competitor:
        raise ApiError("请先选一个竞品，再看它被提到的具体回答")

    from geo.analyzers import mention as mention_mod
    from geo.engines import get_adapter
    with database.session_scope() as s:
        brand = database.get_brand(brand_id)
        brand_names = mention_mod.build_brand_names(brand)
        # 我方品牌（含别名）走我方命中明细分支；竞品名单内走既有竞品分支
        is_self = competitor in brand_names
        round_row = _resolve_round(s, brand_id, round_id)
        auto = database.jloads(round_row.auto_competitors, []) or []
        # 2026-08-15：竞品一律来自本轮自动提取名单
        if not is_self and competitor not in auto:
            raise ApiError("这个竞品不在本轮自动提取的名单里")
        results = (s.query(database.MonitorResult)
                   .filter(database.MonitorResult.round_id == round_row.id)
                   .order_by(database.MonitorResult.id.asc()).all())
        items = []
        for r in results:
            if not r.answer_text:
                continue
            if is_self:
                # 我方品牌分支：复用监测落库的提及判定字段（is_mentioned / mention_count），口径与评分统计一致
                if not r.is_mentioned:
                    continue
                hit_count = r.mention_count or 0
                anchor = _self_excerpt_anchor(r.answer_text, brand_names, competitor)
            else:
                hit = next((c for c in database.jloads(r.competitor_mentions, []) or []
                            if str(c.get("name") or "").strip() == competitor), None)
                if hit is None:
                    # 本轮自动提取的竞品无落库明细：规则法现算（回答包含该名字即命中）
                    if competitor not in auto:
                        continue
                    cnt = len(re.findall(re.escape(competitor), r.answer_text,
                                         flags=re.IGNORECASE))
                    if cnt == 0:
                        continue
                    hit = {"name": competitor, "count": cnt}
                hit_count = hit.get("count") or 0
                anchor = competitor
            try:
                engine_name = get_adapter(r.engine_code).display_name
            except Exception:
                engine_name = r.engine_code or ""
            items.append({
                "result_id": r.id,
                "engine_code": r.engine_code or "",
                "engine_name": engine_name,
                "question_text": r.question_text or "",
                "answer_excerpt": _answer_excerpt(r.answer_text, anchor),
                "answer_full": r.answer_text,
                "hit_count": hit_count,
            })
        return ok(items, "获取成功")


# ---------------- N14 分析状态（与统计接口解耦） ----------------
@bp.route("/report/competitor/analysis", methods=["GET"])
def competitor_analysis_status():
    brand_id = current_brand_id()
    round_id = request.args.get("round_id", type=int)
    with database.session_scope() as s:
        round_row = _resolve_round(s, brand_id, round_id)
        row = (s.query(database.CompetitorAnalysis)
               .filter(database.CompetitorAnalysis.round_id == round_row.id).first())
        if not row:
            # 该轮未触发（无有效回答/竞品空/无提及）→ 按 unavailable 语义返回
            return ok({"status": "unavailable", "data": None, "error_msg": ""},
                      "获取成功")
        return ok({"status": row.status or "pending",
                   "data": database.jloads(row.data, None),
                   "error_msg": row.error_msg or ""}, "获取成功")
