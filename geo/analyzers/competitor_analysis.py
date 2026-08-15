"""竞品深度分析：监测收尾异步触发 + 原因/信源/建议生成（02d 3.3.2 / 3.3.3）。

- 触发（trigger_if_due）：监测收尾（status=done 且非 cancelled）后调用，纯库内
  规则判断（零费用）：metrics.total>0 且该轮有竞品被提及 且 品牌档案竞品非空；
  分析钥匙未配置 → 记 status=unavailable；全部满足 → 建 pending 记录并异步生成。
- 生成（generate）：仅对 mentioned=true 的竞品调模型总结（1 次重试），建议再调
  1 次；全程失败 → status=failed + error_msg 大白话；绝不中断/回滚监测轮次。
"""

import json
import re
import threading
import traceback

from geo.analyzers import llm_client
from geo.models import db as database

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_UNAVAILABLE = "unavailable"

UNMENTIONED_SUMMARY = "本轮 AI 回答里没有提到它"
FALLBACK_ERROR = "暂时无法分析，请稍后再试"
EVIDENCE_MAX = 3          # 每竞品最多 3 条证据
EVIDENCE_CHARS = 500      # 每条证据原文截断长度
RETRY_TIMES = 1           # 单次模型调用失败重试次数
# 自动提取的竞品可能很多（LLM 一轮提取几十家）：深度分析只聚焦被提到最多的
# 前 N 家，控制费用与报告长度；统计表/趋势不受影响，仍展示全部。
ANALYSIS_MAX_COMPETITORS = 8

_QUOTE_CHARS = "“”\"'‘’「」『』【】《》〈〉（）()[]<>"
_WRAP_LEAD = ("例如", "比如", "譬如")


def _strip_name_wraps(name: str) -> str:
    """清洗 LLM 提取的包装杂质：如“XX有限公司”→XX有限公司（引号/括号/如/例如前缀）。"""
    b = str(name or "").strip()
    if not b:
        return ""
    had_quote = any(q in b for q in "“”\"'‘’「」『』")
    for _ in range(4):
        prev = b
        b = b.strip(_QUOTE_CHARS + "，,。.;；:：、 ")
        for p in _WRAP_LEAD:
            if b.startswith(p) and len(b) > len(p) + 2:
                b = b[len(p):].strip()
        # 原本带引号的“如「XX」”形态：剥掉引号后再去“如”；不带引号的品牌名（如家酒店）不动
        if had_quote and b.startswith("如") and len(b) > 2:
            b = b[1:].strip()
        if b == prev:
            break
    return b

# 测试注入点：默认走真实分析模型
_chat = llm_client.chat


def _extract_json(text: str):
    """从模型输出中提取 JSON（容忍 ```json 代码块与前后杂文）。"""
    if not text or not str(text).strip():
        raise llm_client.AnalysisError("分析结果为空，请稍后再试")
    t = str(text).strip()
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```$", "", t).strip()
    if t.startswith("["):
        end = t.rfind("]")
        if end > 0:
            return json.loads(t[:end + 1])
    start = t.find("{")
    end = t.rfind("}")
    if start >= 0 and end > start:
        return json.loads(t[start:end + 1])
    raise llm_client.AnalysisError("分析结果格式不对，请稍后再试")


# 自动提取品牌：本轮回答最多看 10 条、每条截断 500 字（控制 token 成本）
AUTO_BRAND_MAX_ANSWERS = 10
# 提取用的回答截断长度：企业名常出现在回答中后段，500 字会漏（实测 1200-2100 字回答
# 里第 3-5 个企业名被截掉）。1500 字覆盖大部分回答全文，一轮一次调用成本可控。
AUTO_BRAND_CHARS = 1500
# 规则兜底：企业名模式（LLM 漏提时兜底提取，双保险）。
# 前段排除标点/括号/“为/公司”等前缀干扰（如“总公司为浙江龙威…”“前身为杭州鼎林…”只取公司名本身）
_COMPANY_RE = re.compile(r"[^，。；：、（）()·\-—\s为*#]{2,20}?(?:有限责任公司|股份有限公司|有限公司)")
# 信息平台噪音（非竞品）：回答里“信息来源于企查查/天眼查”这类
_PLATFORM_NOISE = ("企查查", "天眼查", "爱企查", "百度百科", "知乎")


def _rule_extract_companies(texts: list, exclude: list) -> list:
    """规则法提取企业名：'XX有限公司/有限责任公司' 模式，排除自己品牌/别名。"""
    out = []
    for t in texts:
        for m in _COMPANY_RE.finditer(t or ""):
            name = m.group(0).strip()
            if len(name) < 6:  # 过滤过短泛称
                continue
            if name in exclude:
                continue
            if name not in out:
                out.append(name)
    return out


def _clean_brands(names: list, self_related: list) -> list:
    """清洗：包装杂质（引号/如/例如前缀）/自己品牌/信息平台噪音/超长；子串归一保留最长形式。"""
    cleaned = []
    for b in names:
        b = _strip_name_wraps(b)
        if len(b) < 2 or len(b) > 40:
            continue
        if any(e and e in b for e in self_related):  # 子串匹配：全称“浙江威启…有限公司”也排除
            continue
        if any(p in b for p in _PLATFORM_NOISE):
            continue
        if b not in cleaned:
            cleaned.append(b)
    # 子串归一：保留最长形式（“XX公司龙泉分公司”覆盖“XX公司”，避免重复统计）
    final = []
    for n in cleaned:
        if any(n in o for o in final):
            continue
        final = [o for o in final if o not in n]
        final.append(n)
    return final


def extract_auto_brands(round_id: int, brand_id: int):
    """从本轮回答中提取被提到的品牌，存入 round.auto_competitors，并回算
    各回答的顺位/竞品明细（2026-08-15 起竞品全部来自自动提取，档案竞品已取消）。

    双保险：分析模型（LLM）语义提取 + 规则法（企业名模式）兜底合并；
    无分析钥匙时规则法仍可用（零成本）。失败静默，绝不影响监测收尾。
    """
    try:
        brand = database.get_brand(brand_id)
        self_name = str(brand.get("brand_name") or "").strip()
        aliases = [str(a or "").strip() for a in (brand.get("brand_aliases") or []) if str(a or "").strip()]
        with database.session_scope() as s:
            results = (s.query(database.MonitorResult)
                       .filter(database.MonitorResult.round_id == round_id).all())
            texts = [r.answer_text for r in results if r.answer_text]
            if not texts:
                return
        exclude = [n for n in dict.fromkeys([self_name] + aliases) if n]

        # 规则法兜底（零成本，不依赖钥匙）
        rule_brands = _rule_extract_companies(texts, exclude)
        cleaned = _clean_brands(rule_brands, exclude)

        # LLM 语义提取（无钥匙时跳过，规则结果仍生效）
        if llm_client.is_configured():
            pieces = []
            for i, t in enumerate(texts[:AUTO_BRAND_MAX_ANSWERS], 1):
                pieces.append(f"--- 第 {i} 条 ---\n{_truncate(t, AUTO_BRAND_CHARS)}")
            exclude_text = "、".join(exclude) or "我方品牌"
            prompt = (
                "你是品牌监测助手。下面是一轮 AI 问答监测中，多个 AI 对用户问题的回答原文。\n"
                f"请找出这些回答里【被提到的所有品牌或企业名称】（公司名、产品系列名、服务品牌名等），"
                f"一条都不要漏，但排除我方品牌「{exclude_text}」。\n"
                "要求：\n"
                "1. 只输出品牌名组成的 JSON 数组，不要任何解释；\n"
                "2. 使用回答里的原样名称，不要改写；\n"
                "3. 没有提到任何其他品牌就输出 []。\n\n"
                f"回答原文：\n{chr(10).join(pieces)}"
            )
            raw = _chat_with_retry(prompt, temperature=0)
            brands = _extract_json(raw)
            if isinstance(brands, list):
                cleaned = _clean_brands(cleaned + brands, exclude)

        if not cleaned:
            return
        with database.session_scope() as s:
            row = s.get(database.MonitorRound, round_id)
            if row:
                row.auto_competitors = database.jdumps(cleaned)
            # 回算：用自动提取的竞品名单重算每条回答的顺位与竞品明细
            # （分析时无档案竞品，position 暂为 1/竞品明细为空）
            from geo.analyzers import mention as mention_mod
            rows = (s.query(database.MonitorResult)
                    .filter(database.MonitorResult.round_id == round_id).all())
            brand_names = [n for n in dict.fromkeys([self_name] + aliases) if n]
            for r in rows:
                if not r.answer_text:
                    continue
                mentioned = mention_mod.mention_count(r.answer_text, brand_names) > 0
                if mentioned:
                    r.mention_position = mention_mod.brand_position(
                        r.answer_text, brand_names, cleaned)
                r.competitor_mentions = database.jdumps(
                    mention_mod.competitor_mentions(r.answer_text, cleaned, brand_names))
    except Exception:
        # 静默：自动提取失败不影响监测收尾与既有竞品分析
        return


def finalize_competitors(round_id: int, brand_id: int):
    """监测收尾统一入口：提取竞品（含回算）→ 触发竞品深度分析。"""
    extract_auto_brands(round_id, brand_id)
    try:
        trigger_if_due(round_id, brand_id)
    except Exception:
        import traceback
        traceback.print_exc()


def _chat_with_retry(prompt: str, **kwargs) -> str:
    """单次模型调用 + 1 次重试；仍失败抛 AnalysisError。"""
    last_err = None
    for _ in range(RETRY_TIMES + 1):
        try:
            return _chat(prompt, **kwargs)
        except llm_client.AnalysisError as e:
            last_err = e
    raise last_err


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "……"


def _round_evidence_map(s, round_id: int, competitors: list) -> dict:
    """规则法聚证据：每竞品命中回答原文片段（截断 500 字，最多 3 条）。"""
    results = (s.query(database.MonitorResult)
               .filter(database.MonitorResult.round_id == round_id)
               .order_by(database.MonitorResult.id.asc()).all())
    items = {name: [] for name in competitors}
    for r in results:
        if not r.answer_text:
            continue
        mentioned = set()
        for c in database.jloads(r.competitor_mentions, []) or []:
            name = str(c.get("name") or "").strip()
            if name in items:
                mentioned.add(name)
        for name in mentioned:
            if len(items[name]) < EVIDENCE_MAX:
                items[name].append({
                    "engine_code": r.engine_code or "",
                    "question_text": r.question_text or "",
                    "excerpt": _truncate(r.answer_text, EVIDENCE_CHARS),
                    "answer_full": r.answer_text,
                })
    return items


def _brand_context_lines(brand: dict) -> list:
    lines = [
        f"- 我方品牌名：{brand.get('brand_name') or ''}"
        f"（别名：{'、'.join(brand.get('brand_aliases') or [])}）",
        f"- 我方产品：{brand.get('product_name') or ''}",
        f"- 我方简介：{brand.get('brand_description') or ''}",
    ]
    return lines


def _summarize_competitor(brand: dict, name: str, evidences: list,
                          range_label: str = "本轮") -> tuple:
    """单竞品原因总结：返回 (summary, source_types, features)。range_label 支持汇总分析。"""
    lines = ["品牌档案："]
    lines += _brand_context_lines(brand)
    lines.append("")
    lines.append(f"{range_label}监测里，AI 的回答提到了竞品「{name}」。以下是命中的回答原文摘录：")
    lines.append("")
    for i, ev in enumerate(evidences, 1):
        head = f"【第{i}条】"
        if ev.get("round_label"):
            head += f"轮次：{ev['round_label']}｜"
        lines.append(head + f"引擎：{ev['engine_code']}")
        lines.append(f"问题：{ev['question_text']}")
        lines.append(f"回答片段：{ev['excerpt']}")
        lines.append("")
    lines.append("请只根据上面的素材分析，不要编造素材里没有的内容。")
    lines.append("只输出一个 JSON 对象，不要输出任何其他内容：")
    lines.append('{')
    lines.append('  "summary": "为什么 AI 会提到「' + name + '」：用一段大白话说明（50~120 字），'
                 '说清楚它是在什么场景/什么类型的问题里被提到的、它有什么特点让 AI 愿意提它、'
                 '与我方相比它强在哪或有什么不同。只总结素材里有的内容，不要猜测素材外的事实。",')
    lines.append('  "features": ["它被提到时突出表现的 1~3 个特点，每个 20 字以内"],')
    lines.append('  "source_types": ["AI 大概从哪类信源听说它：如 行业媒体报道类、官网产品介绍类、'
                 '用户评价类、电商平台类 等，1~3 个；只做类型推测，不要写任何具体网站名、链接或文章标题"]')
    lines.append('}')
    prompt = "\n".join(lines)

    system = "你是一个中文竞品分析助手。只根据提供的素材回答，不编造；输出必须是合法 JSON。"
    raw = _chat_with_retry(prompt, temperature=0.3, system=system)
    obj = _extract_json(raw)
    if not isinstance(obj, dict):
        raise llm_client.AnalysisError("分析结果格式不对，请稍后再试")
    summary = str(obj.get("summary") or "").strip()
    if not summary:
        raise llm_client.AnalysisError("分析结果缺少总结，请稍后再试")
    features = [str(f).strip() for f in (obj.get("features") or []) if str(f).strip()]
    source_types = [str(st).strip() for st in (obj.get("source_types") or [])
                    if str(st).strip()]
    return summary, source_types, features


def _generate_advice(brand: dict, competitors_data: list, self_note: str,
                     range_label: str = "本轮") -> list:
    """优化方向建议（3~5 条 {gap, where, what}），1 次调用。"""
    lines = ["品牌档案："]
    lines += _brand_context_lines(brand)
    lines.append("")
    lines.append(self_note)
    lines.append(f"{range_label}内 AI 回答里各竞品的情况：")
    for c in competitors_data:
        if c["mentioned"]:
            feat = "；".join(c["features"]) if c.get("features") else "（素材未给出突出特点）"
            lines.append(f"- 「{c['name']}」被提到了。它被提到时的特点：{feat}")
        else:
            lines.append(f"- 「{c['name']}」本轮没被提到。")
    lines.append("")
    lines.append("请基于上面的差距（哪些问题/场景里竞品被提到、我方没被提到，或竞品表现比我方突出），")
    lines.append("给出 3~5 条「下一步优化方向」。要求：大白话、可直接照着做、不承诺效果。")
    lines.append("只输出一个 JSON 数组，不要输出任何其他内容：")
    lines.append('[{"gap": "差距在哪：哪类问题/场景提到了竞品、没提到我方",'
                 ' "where": "去哪做：官网/行业媒体/案例介绍等渠道方向",'
                 ' "what": "做什么：一条具体可操作的内容动作"}]')
    prompt = "\n".join(lines)

    system = "你是一个中文优化建议助手。只根据提供的素材给出建议；输出必须是合法 JSON 数组。"
    raw = _chat_with_retry(prompt, temperature=0.3, system=system)
    obj = _extract_json(raw)
    if not isinstance(obj, list):
        raise llm_client.AnalysisError("分析结果格式不对，请稍后再试")
    advice = []
    for item in obj:
        if not isinstance(item, dict):
            continue
        gap = str(item.get("gap") or "").strip()
        where = str(item.get("where") or "").strip()
        what = str(item.get("what") or "").strip()
        if gap and where and what:
            advice.append({"gap": gap, "where": where, "what": what})
    if not advice:
        raise llm_client.AnalysisError("分析结果缺少建议，请稍后再试")
    return advice[:5]


def _set_failed(round_id: int, message: str):
    with database.session_scope() as s:
        row = (s.query(database.CompetitorAnalysis)
               .filter(database.CompetitorAnalysis.round_id == round_id).first())
        if not row:
            return
        row.status = STATUS_FAILED
        row.error_msg = message
        row.finished_at = database.now()


def trigger_if_due(round_id: int, brand_id: int):
    """监测收尾触发（02d 3.3.3）：条件全满足才生成，钥匙缺失记 unavailable。

    2026-08-15 起竞品名单一律取本轮自动提取结果（round.auto_competitors），
    不再使用品牌档案配置的竞品。
    """
    with database.session_scope() as s:
        round_row = s.get(database.MonitorRound, round_id)
        if not round_row:
            return
        competitors = [str(c).strip() for c in
                       (database.jloads(round_row.auto_competitors, []) or [])]
        competitors = list(dict.fromkeys(c for c in competitors if c))
        if not competitors:
            return
    with database.session_scope() as s:
        # 每轮至多 1 条：重复收尾不重复生成
        existing = (s.query(database.CompetitorAnalysis)
                    .filter(database.CompetitorAnalysis.round_id == round_id).first())
        if existing:
            return
        results = (s.query(database.MonitorResult)
                   .filter(database.MonitorResult.round_id == round_id).all())
        answered = [r for r in results if r.answer_text]
        if not answered:  # metrics.total=0：无有效回答不生成
            return
        # 提及判断规则法现算（2026-08-15）：不依赖落库 competitor_mentions，
        # 与统计接口 _competitor_stats 口径一致，旧轮次没回算明细也能正确触发
        from geo.analyzers import mention as mention_mod
        brand_row = s.get(database.BrandProfile, brand_id)
        brand_names = mention_mod.build_brand_names(
            brand_row.to_dict() if brand_row else {})
        mentioned_any = any(
            mention_mod.competitor_mentions(r.answer_text, competitors, brand_names)
            for r in answered)
        if not mentioned_any:  # 本轮竞品提及为空：无可分析内容
            return
        if not llm_client.is_configured():
            s.add(database.CompetitorAnalysis(
                round_id=round_id, brand_id=brand_id, status=STATUS_UNAVAILABLE,
                data=None, error_msg="", finished_at=database.now()))
            return
        s.add(database.CompetitorAnalysis(
            round_id=round_id, brand_id=brand_id, status=STATUS_PENDING))
    threading.Thread(target=generate, args=(round_id, brand_id, competitors),
                     daemon=True).start()


def generate(round_id: int, brand_id: int, competitors: list):
    """异步生成：仅对 mentioned=true 竞品调模型；失败 → failed，绝不中断监测。"""
    try:
        _generate_inner(round_id, brand_id, competitors)
    except Exception:
        traceback.print_exc()
        _set_failed(round_id, FALLBACK_ERROR)


def _generate_inner(round_id: int, brand_id: int, competitors: list):
    brand = database.get_brand(brand_id)
    with database.session_scope() as s:
        row = (s.query(database.CompetitorAnalysis)
               .filter(database.CompetitorAnalysis.round_id == round_id).first())
        if not row:
            return
        row.status = STATUS_RUNNING
        evidence_map = _round_evidence_map(s, round_id, competitors)
        results = (s.query(database.MonitorResult)
                   .filter(database.MonitorResult.round_id == round_id).all())
        self_answered = len([r for r in results if r.answer_text])
        self_mentioned = len([r for r in results if r.answer_text and r.is_mentioned])
    # running 已提交；后续模型调用各自独立写库（api_call_log），不持有写事务

    # 聚焦被提到次数最多的前 N 家（自动提取名单可能几十家）：控费用、控页面长度
    hits = {name: len(evidence_map.get(name, []) or []) for name in competitors}
    order = {name: i for i, name in enumerate(competitors)}
    focus = sorted(competitors, key=lambda n: (-hits.get(n, 0), order.get(n, 999)))[
        :ANALYSIS_MAX_COMPETITORS]

    competitors_data = []
    for name in focus:
        evidences = evidence_map.get(name, []) or []
        if evidences:
            summary, source_types, features = _summarize_competitor(brand, name, evidences)
        else:
            summary, source_types, features = UNMENTIONED_SUMMARY, [], []
        competitors_data.append({
            "name": name, "mentioned": bool(evidences),
            "summary": summary, "source_types": source_types,
            "features": features, "evidence": evidences,
        })

    self_note = (f"本轮我方「{brand.get('brand_name') or ''}」在 {self_mentioned}/{self_answered} "
                 f"条有效回答里被提到。")
    advice = _generate_advice(brand, competitors_data, self_note)

    data = {
        "is_speculative": True,
        "competitors": [
            {"name": c["name"], "mentioned": c["mentioned"], "summary": c["summary"],
             "source_types": c["source_types"], "evidence": c["evidence"]}
            for c in competitors_data
        ],
        "advice": advice,
        "truncated": len(competitors) > len(focus),
        "total": len(competitors),
    }
    with database.session_scope() as s:
        row = (s.query(database.CompetitorAnalysis)
               .filter(database.CompetitorAnalysis.round_id == round_id).first())
        if not row:
            return
        row.status = STATUS_DONE
        row.data = database.jdumps(data)
        row.error_msg = ""
        row.finished_at = database.now()


# ============================================================
# 「最近 30 轮」汇总分析（2026-08-15）：报告页选汇总视图时，③ 按 30 轮数据整体分析。
# 存储复用 competitor_analysis 表，round_id=NULL 表示汇总记录（唯一键对 NULL 不冲突），
# data.latest_round_id 记录生成时最新轮，用于报告页查看时判断是否需要重新生成。
# ============================================================
AGGREGATE_MAX_ROUNDS = 30


def _recent_normal_round_rows(brand_id: int, limit: int = AGGREGATE_MAX_ROUNDS) -> list:
    """该品牌最近 limit 个正常轮（task done/缺失），按时间升序返回。"""
    from geo.core import monitor_task
    with database.session_scope() as s:
        status_map = monitor_task.task_status_map(s)
        rows = [r for r in (s.query(database.MonitorRound)
                            .filter(database.MonitorRound.brand_id == brand_id)
                            .order_by(database.MonitorRound.id.desc()).all())
                if monitor_task.round_is_normal(status_map, r)]
        return list(reversed(rows[:limit]))


def trigger_aggregate_if_due(brand_id: int):
    """汇总分析触发：竞品取范围内各轮并集、提及规则法现算；无钥匙记 unavailable；
    结果过期（新轮次产生）或 failed/unavailable（钥匙已补）时重新生成。幂等。"""
    rows = _recent_normal_round_rows(brand_id)
    if not rows:
        return
    auto = []
    for r in rows:
        auto.extend(database.jloads(r.auto_competitors, []) or [])
    auto = list(dict.fromkeys(str(c).strip() for c in auto if str(c).strip()))
    if not auto:
        return
    latest_id = rows[-1].id

    brand = database.get_brand(brand_id)
    from geo.analyzers import mention as mention_mod
    brand_names = mention_mod.build_brand_names(brand)
    with database.session_scope() as s:
        results = (s.query(database.MonitorResult)
                   .filter(database.MonitorResult.round_id.in_([r.id for r in rows])).all())
        answered = [x for x in results if x.answer_text]
        if not answered:
            return
        mentioned_any = any(
            mention_mod.competitor_mentions(x.answer_text, auto, brand_names)
            for x in answered)
        if not mentioned_any:
            return
        existing = (s.query(database.CompetitorAnalysis)
                    .filter(database.CompetitorAnalysis.round_id.is_(None),
                            database.CompetitorAnalysis.brand_id == brand_id).first())
        if existing is not None:
            if existing.status in (STATUS_PENDING, STATUS_RUNNING):
                return  # 正在生成，别重复触发
            if existing.status == STATUS_DONE:
                data = database.jloads(existing.data, None) or {}
                if data.get("latest_round_id") == latest_id:
                    return  # 已是最新结果
            s.delete(existing)  # unavailable / failed / 结果过期 → 重新生成
        if not llm_client.is_configured():
            s.add(database.CompetitorAnalysis(
                round_id=None, brand_id=brand_id, status=STATUS_UNAVAILABLE,
                data=None, error_msg="", finished_at=database.now()))
            return
        s.add(database.CompetitorAnalysis(
            round_id=None, brand_id=brand_id, status=STATUS_PENDING))
    threading.Thread(target=generate_aggregate,
                     args=(brand_id, [r.id for r in rows], auto, latest_id),
                     daemon=True).start()


def generate_aggregate(brand_id: int, round_ids: list, competitors: list,
                       latest_round_id: int):
    try:
        _generate_aggregate_inner(brand_id, round_ids, competitors, latest_round_id)
    except Exception:
        traceback.print_exc()
        _set_failed_aggregate(brand_id, FALLBACK_ERROR)


def _set_failed_aggregate(brand_id: int, message: str):
    with database.session_scope() as s:
        row = (s.query(database.CompetitorAnalysis)
               .filter(database.CompetitorAnalysis.round_id.is_(None),
                       database.CompetitorAnalysis.brand_id == brand_id).first())
        if not row:
            return
        row.status = STATUS_FAILED
        row.error_msg = message
        row.finished_at = database.now()


def _aggregate_evidence_map(s, round_ids: list, competitors: list) -> tuple:
    """跨轮证据（每竞品最多 EVIDENCE_MAX 条，优先最近轮）+ 每竞品累计提及次数。"""
    label_by_id = {rid: f"第{i}轮" for i, rid in enumerate(round_ids, 1)}
    items = {name: [] for name in competitors}
    hits = {name: 0 for name in competitors}
    for rid in reversed(round_ids):  # 最近轮优先保留证据
        results = (s.query(database.MonitorResult)
                   .filter(database.MonitorResult.round_id == rid)
                   .order_by(database.MonitorResult.id.asc()).all())
        for r in results:
            if not r.answer_text:
                continue
            for c in database.jloads(r.competitor_mentions, []) or []:
                name = str(c.get("name") or "").strip()
                if name not in items:
                    continue
                hits[name] = hits.get(name, 0) + (c.get("count") or 0)
                if len(items[name]) < EVIDENCE_MAX:
                    items[name].append({
                        "round_label": label_by_id.get(rid, ""),
                        "engine_code": r.engine_code or "",
                        "question_text": r.question_text or "",
                        "excerpt": _truncate(r.answer_text, EVIDENCE_CHARS),
                        "answer_full": r.answer_text,
                    })
    return items, hits


def _generate_aggregate_inner(brand_id: int, round_ids: list, competitors: list,
                              latest_round_id: int):
    brand = database.get_brand(brand_id)
    range_label = f"近 {len(round_ids)} 轮"
    with database.session_scope() as s:
        row = (s.query(database.CompetitorAnalysis)
               .filter(database.CompetitorAnalysis.round_id.is_(None),
                       database.CompetitorAnalysis.brand_id == brand_id).first())
        if not row:
            return
        row.status = STATUS_RUNNING
        evidence_map, hits = _aggregate_evidence_map(s, round_ids, competitors)
        results = (s.query(database.MonitorResult)
                   .filter(database.MonitorResult.round_id.in_(round_ids)).all())
        self_answered = len([r for r in results if r.answer_text])
        self_mentioned = len([r for r in results if r.answer_text and r.is_mentioned])

    order = {name: i for i, name in enumerate(competitors)}
    focus = sorted(competitors, key=lambda n: (-hits.get(n, 0), order.get(n, 999)))[
        :ANALYSIS_MAX_COMPETITORS]

    competitors_data = []
    for name in focus:
        evidences = evidence_map.get(name, []) or []
        if evidences:
            summary, source_types, features = _summarize_competitor(
                brand, name, evidences, range_label)
        else:
            summary, source_types, features = UNMENTIONED_SUMMARY, [], []
        competitors_data.append({
            "name": name, "mentioned": bool(evidences),
            "summary": summary, "source_types": source_types,
            "features": features, "evidence": evidences,
        })

    self_note = (f"{range_label}内我方「{brand.get('brand_name') or ''}」在 "
                 f"{self_mentioned}/{self_answered} 条有效回答里被提到。")
    advice = _generate_advice(brand, competitors_data, self_note, range_label)

    data = {
        "is_speculative": True,
        "competitors": [
            {"name": c["name"], "mentioned": c["mentioned"], "summary": c["summary"],
             "source_types": c["source_types"], "evidence": c["evidence"]}
            for c in competitors_data
        ],
        "advice": advice,
        "truncated": len(competitors) > len(focus),
        "total": len(competitors),
        "range": "30",
        "rounds": len(round_ids),
        "latest_round_id": latest_round_id,
    }
    with database.session_scope() as s:
        row = (s.query(database.CompetitorAnalysis)
               .filter(database.CompetitorAnalysis.round_id.is_(None),
                       database.CompetitorAnalysis.brand_id == brand_id).first())
        if not row:
            return
        row.status = STATUS_DONE
        row.data = database.jdumps(data)
        row.error_msg = ""
        row.finished_at = database.now()
