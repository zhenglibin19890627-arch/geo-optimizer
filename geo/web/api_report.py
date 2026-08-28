"""报告与首页接口：概览 / 趋势 / 引擎对比 / 信源（#1、#19~#21）。

竞品相关接口（对比/明细/趋势/命中/深度分析状态，#20 与 N11~N14）
拆在 api_report_competitor.py，挂同一个 bp，URL 不变。
"""

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
            hints.append("尚未填写品牌信息，请先到「设置」页填写品牌名")
        configured = _configured_engines_count(s)
        if configured == 0:
            hints.append("尚未填写任何 AI 引擎的 API 钥匙，请到「设置」页填写后即可发起监测")
        if 0 < round_count < 3:
            hints.append(f"再完成 {3 - round_count} 轮监测后，系统将开始监测数据变化并预警")

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


def recent_normal_rounds(s, brand_id: int, limit: int = 30, mode: str = None) -> list:
    """最近 limit 个正常轮（task done/缺失；可选按模式过滤），按 id 升序返回。

    与趋势图口径一致：cancelled/failed 不计入；“最近 30 轮汇总”用它取数。
    竞品接口（api_report_competitor）跨模块复用本助手。
    """
    status_map = monitor_task.task_status_map(s)
    q = (s.query(database.MonitorRound)
         .filter(database.MonitorRound.brand_id == brand_id))
    if mode:
        q = q.filter(database.MonitorRound.mode == mode)
    rows = [r for r in q.order_by(database.MonitorRound.id.desc()).all()
            if monitor_task.round_is_normal(status_map, r)]
    return list(reversed(rows[:limit]))


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


# ---------------- 各引擎厂商对比（2026-08-22） ----------------
@bp.route("/report/engines", methods=["GET"])
def report_engines():
    """近 N 个正常轮次里，每家引擎/每个模型的回答数、提及率、净情感、
    平均顺位与带信源数（常规/联网分开列），供报告页「引擎厂商对比」卡使用。"""
    brand_id = current_brand_id()
    rounds = max(int(request.args.get("rounds") or 30), 1)
    with database.session_scope() as s:
        status_map = monitor_task.task_status_map(s)
        all_rows = [r for r in (s.query(database.MonitorRound)
                                .filter(database.MonitorRound.brand_id == brand_id)
                                .order_by(database.MonitorRound.id.desc()).all())
                    if monitor_task.round_is_normal(status_map, r)]
        recent = list(reversed(all_rows[:rounds]))  # 旧→新，与趋势图口径一致
        rids = [r.id for r in recent]
        if not rids:
            return ok({"engines": [], "rounds_used": 0}, "获取成功")
        mode_of = {r.id: (r.mode or "normal") for r in recent}

        results = (s.query(database.MonitorResult)
                   .filter(database.MonitorResult.round_id.in_(rids)).all())

        def _blank():
            return {"answered": 0, "mentioned": 0, "pos": 0, "neg": 0,
                    "pos_sum": [], "with_sources": 0}

        agg = {}  # engine -> {"_": _blank(), "modes": {mode: _blank()}, "models": {model: _blank()}}
        for r in results:
            if not r.answer_text:
                continue
            mode = mode_of.get(r.round_id, "normal")
            eng = agg.setdefault(r.engine_code, {
                "_": _blank(), "modes": {"normal": _blank(), "web": _blank()},
                "models": {}})
            model_key = r.model or "(当前档)"
            slots = (eng["_"], eng["modes"][mode], eng["models"].setdefault(model_key, _blank()))
            has_src = bool(database.jloads(r.sources, []) or [])
            for slot in slots:
                slot["answered"] += 1
                if r.is_mentioned:
                    slot["mentioned"] += 1
                    if r.mention_position:
                        slot["pos_sum"].append(r.mention_position)
                if r.sentiment == "positive":
                    slot["pos"] += 1
                elif r.sentiment == "negative":
                    slot["neg"] += 1
                if mode == "web" and has_src:
                    slot["with_sources"] += 1

        def _fill(slot):
            answered = slot["answered"]
            pos_sum = slot["pos_sum"]
            return {
                "answered": answered,
                "mentioned": slot["mentioned"],
                "mention_rate": round(slot["mentioned"] / answered, 3) if answered else None,
                "net_sentiment": round((slot["pos"] - slot["neg"]) / answered, 3) if answered else None,
                "avg_position": round(sum(pos_sum) / len(pos_sum), 1) if pos_sum else None,
                "with_sources": slot["with_sources"],
            }

        from geo.engines import get_adapter
        engines = []
        for code, eng in agg.items():
            try:
                adapter = get_adapter(code)
                # 订阅网关类（如 opencode）不是模型厂家：回答照常监测统计，
                # 但不进「引擎厂商对比」卡（模型归属随档位走，算在网关名下会失真）
                if not adapter.model_vendor:
                    continue
                display_name = adapter.display_name
            except Exception:
                display_name = code
            models = [{"model": m, **_fill(slot)}
                      for m, slot in sorted(eng["models"].items(),
                                            key=lambda kv: -kv[1]["answered"])]
            engines.append({
                "engine": code,
                "display_name": display_name,
                **_fill(eng["_"]),
                "modes": {m: _fill(slot) for m, slot in eng["modes"].items()},
                "models": models,
            })
        # 总提及率降序：表现好的厂商排前面
        engines.sort(key=lambda e: (-(e["mention_rate"] or 0), -e["answered"]))
        return ok({"engines": engines, "rounds_used": len(recent)}, "获取成功")


# ---------------- #21 引用信源分析 ----------------
@bp.route("/report/sources", methods=["GET"])
def report_sources():
    brand_id = current_brand_id()
    round_id = request.args.get("round_id", type=int)
    with database.session_scope() as s:
        brand = database.get_brand(brand_id)
        from geo.analyzers import mention as mention_mod
        brand_names = mention_mod.build_brand_names(brand)
        self_name = str(brand.get("brand_name") or "").strip()

        if round_id:
            round_row = s.get(database.MonitorRound, round_id)
            if not round_row:
                raise ApiError("该轮监测不存在，可能已被清理")
            if (round_row.brand_id or 1) != brand_id:
                raise ApiError("该轮数据属于其他品牌，请切换品牌后查看")
            results = (s.query(database.MonitorResult)
                       .filter(database.MonitorResult.round_id == round_id).all())
            # 该轮自动提取的竞品名单（信源背书的品牌从这里面找）
            competitors = [str(c).strip() for c in
                           (database.jloads(round_row.auto_competitors, []) or []) if c]
        else:
            # 最近 30 轮汇总：聚合这些轮次的所有结果；竞品取各轮并集
            rounds = recent_normal_rounds(s, brand_id, limit=30)
            if not rounds:
                return ok([], "获取成功")
            rids = [r.id for r in rounds]
            results = (s.query(database.MonitorResult)
                       .filter(database.MonitorResult.round_id.in_(rids)).all())
            competitors = []
            for rr in rounds:
                competitors.extend(str(c).strip() for c in
                                   (database.jloads(rr.auto_competitors, []) or []) if c)
            competitors = list(dict.fromkeys(competitors))

        # 统一信源按规范化域名合并（m./www. 前缀归一同站），累计次数、各引擎引用、
        # 以及「引用该信源的回答提到了哪些品牌」（2026-08-27：我方 + 自动竞品名单）
        from geo.analyzers import sources as sources_mod
        agg = {}
        for r in results:
            if not r.answer_text:
                continue
            code = r.engine_code or ""
            # 该条回答提到的品牌（我方/竞品），按提及次数计
            brand_hits = {}
            if mention_mod.mention_count(r.answer_text, brand_names) > 0:
                brand_hits[self_name or "我方品牌"] = True
            for c in mention_mod.competitor_mentions(r.answer_text, competitors,
                                                     brand_names):
                name = str(c.get("name") or "").strip()
                if name:
                    brand_hits[name] = True
            for src in database.jloads(r.sources, []) or []:
                url = src.get("url", "")
                if not url:
                    continue
                domain = sources_mod.normalize_domain(
                    src.get("domain") or "")
                if not domain:
                    continue
                item = agg.setdefault(domain, {"domain": domain, "url": url,
                                               "count": 0, "engines": {},
                                               "brands": {}})
                item["count"] += 1
                eng = item["engines"].setdefault(code, {"engine_code": code, "count": 0})
                eng["count"] += 1
                for bname in brand_hits:
                    b = item["brands"].setdefault(bname, {"name": bname, "count": 0,
                                                          "is_self": bname == self_name})
                    b["count"] += 1
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
            # 品牌列表按被背书次数降序，最多显示前 5 个（避免行太长）
            brands = sorted(item["brands"].values(),
                            key=lambda b: (-b["count"], b["name"]))[:5]
            items.append({
                "domain": item["domain"], "url": item["url"],
                "site_name": sources_mod.site_name(item["domain"]),
                "category": sources_mod.classify_domain(item["domain"]),
                "count": item["count"],
                "engines": eng_list,
                "brands": brands,
            })
        return ok(items, "获取成功")
