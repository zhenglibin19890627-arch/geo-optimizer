"""竞品名字提取与清洗（纯规则/纯库内，无 LLM 调用、无触发联动）。

从 competitor_analysis 拆出的职责：
- 规则法企业名提取（'XX有限公司' 模式）；
- LLM 提取结果的包装杂质清洗（引号/如/例如前缀、噪音、子串归一）；
- auto_competitors 落库 + 各回答顺位/竞品明细回算。

编排入口（extract_auto_brands / finalize_competitors，含 LLM 调用与
深度分析触发）仍在本体 geo/analyzers/competitor_analysis.py。
"""

import re

from geo.models import db as database

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


# 自动提取品牌：本轮回答最多看 8 条、每条截断 1000 字（控制 token 成本与
# 推理模型耗时；规则法不截断，企业名由规则法保证召回）
AUTO_BRAND_MAX_ANSWERS = 8
AUTO_BRAND_CHARS = 1000
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


def _current_auto(round_id: int) -> str:
    """读当前 auto_competitors 原始串（供 LLM 合并时取基线）。"""
    with database.session_scope() as s:
        row = s.get(database.MonitorRound, round_id)
        return row.auto_competitors if row else "[]"


def _save_and_recompute(round_id: int, self_name: str, aliases: list, cleaned: list):
    """落库 auto_competitors + 回算各回答顺位/竞品明细（单事务）。"""
    from geo.analyzers import mention as mention_mod
    with database.session_scope() as s:
        row = s.get(database.MonitorRound, round_id)
        if row:
            row.auto_competitors = database.jdumps(cleaned)
        # 回算：用提取的竞品名单重算每条回答的顺位与竞品明细
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
