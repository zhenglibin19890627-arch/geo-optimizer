"""提及判定 / 次数 / 顺位 / 情感分析（规则法为主，无需钥匙即可运行，口径稳定）。"""

import re

# ---- 情感词典（面向品牌/产品语境的中文常用词）----
# 注意：词条已去重（旧版"优秀/满意/神器/拉胯/失灵"等重复词条会让一次出现计 2 分）。
POSITIVE_WORDS = [
    "好", "优秀", "出色", "很棒", "很好", "非常好", "特别好", "极好", "一流", "顶级",
    "推荐", "强推", "值得买", "值得推荐", "性价比高", "高性价比", "划算", "实惠", "物超所值",
    "可靠", "靠谱", "稳定", "耐用", "扎实", "放心", "安全", "口碑好", "好评", "好评如潮",
    "喜欢", "满意", "惊艳", "惊喜", "好用", "实用", "方便", "便捷", "高效",
    "强大", "功能强", "全面", "丰富", "完善", "先进", "领先", "创新", "专业", "精致",
    "美观", "漂亮", "好看", "流畅", "轻便", "轻薄", "清晰", "省心", "省事", "舒服",
    "贴心", "良心", "诚心", "无敌", "天花板", "首选", "王者", "标杆", "神器",
    "进步", "提升", "改善", "改进", "突破", "超越", "最佳", "最优", "完美",
]
NEGATIVE_WORDS = [
    "差", "很差", "非常差", "垃圾", "拉胯", "翻车", "踩雷", "避雷", "坑",
    "不值得", "不推荐", "别买", "不要买", "劝退", "差评", "差评如潮", "投诉", "维权",
    "问题多", "毛病", "故障", "死机", "卡顿", "卡死", "闪退", "掉线", "不稳", "不稳定",
    "延迟", "发热", "烫", "漏电", "炸", "爆炸", "缩水", "虚标", "夸大", "欺骗", "虚假",
    "失望", "后悔", "遗憾", "生气", "愤怒", "无语", "恶心", "糟糕", "平庸", "一般般",
    "凑合", "勉强", "落后", "过时", "难用", "费劲", "麻烦", "繁琐", "复杂", "笨重",
    "贵", "太贵", "溢价", "智商税", "割韭菜", "售后差", "客服差", "质保差", "偷工减料",
    "降级", "阉割", "缺陷", "漏洞", "bug", "闪断", "报错", "失败", "失灵",
]

_NEG_PREFIX_RE = re.compile(r"(不太|一点也不|毫无|并非|非但|不|没|无|未|别)")
# 注意：不用单字"非"作否定前缀——"非常/非凡/是非"里都有"非"，会误伤"非常好"等高频好评。

# 以否定字开头但并非否定的常用短语：先剔除，避免"无比好用/不仅好用/不过很好"被误反转。
_NON_NEGATION_RE = re.compile(r"(无比|不仅|不但|不光|不只|不止|不过|不然|不妨|无论|没准)")

# 否定词回看窗口（字符）：2 字窗口抓不到"不是很好"里"好"前面的"不是"，
# 导致"很好"被反转与"好"单独计数抵消、误判中性；扩到 4 字可覆盖
# "不是很好/不太贵/一点也不差/没那么好"等常见口语否定。
_NEG_LOOKBEHIND = 4

# 固定礼貌短语，不含情感：剔除后避免"不+好"被误判为负面（如"不好意思，打扰了"）。
_FILLER_PHRASES = ("不好意思",)


def _strip_punct(text: str) -> str:
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", " ", text)


def build_brand_names(brand_profile: dict) -> list:
    """品牌名 + 别名，去空去重，长名在前（避免子串吞并）。"""
    names = [brand_profile.get("brand_name", "")]
    names += brand_profile.get("brand_aliases") or []
    names = [str(n).strip() for n in names if str(n).strip()]
    names = list(dict.fromkeys(names))
    names.sort(key=len, reverse=True)
    return names


def first_occurrences(text: str, names: list) -> dict:
    """每个名称在文本中首次出现的位置（字符下标，忽略大小写）；没出现则为 None。"""
    result = {}
    low = text.lower()
    for name in names:
        idx = low.find(name.lower())
        if idx >= 0:
            result[name] = idx
    return result


def mention_count(text: str, names: list) -> int:
    """提及次数（别名也算；同一位置的重复子串不重复计数，忽略大小写）。"""
    if not names or not text:
        return 0
    pattern = "|".join(re.escape(n) for n in names)
    return len(re.findall(pattern, text, flags=re.IGNORECASE))


def brand_position(text: str, brand_names: list, competitor_names: list):
    """首次提及顺位：把自己和竞品按“谁先出现”排序，返回自己排第几（1=最早）。
    没提到自己返回 None；竞品名单为空时自己永远是第 1 位。"""
    if not brand_names or not text:
        return None
    low = text.lower()
    brand_first = min((low.find(n.lower()) for n in brand_names if low.find(n.lower()) >= 0), default=None)
    if brand_first is None:
        return None
    rival_firsts = []
    for c in competitor_names:
        c = str(c).strip()
        if not c or c in brand_names:
            continue
        idx = low.find(c.lower())
        if idx >= 0:
            rival_firsts.append(idx)
    earlier = sum(1 for idx in rival_firsts if idx < brand_first)
    return earlier + 1


def _polarity_hits(text: str, words: list) -> int:
    """词典打分：命中 +1；命中词前面 4 字内有否定词 → 整词反转为 -1。

    反转直接记 -1（而不是 -2 与其余词条正负抵消），保证"不是很好"
    （"很好"翻负 + "好"翻负）不再净化为 0 误判中性；"一点也不差"
    （负面词遇否定）也能正确翻为正面。回看窗口先剔除"无比/不仅"等
    非否定短语，避免误反转。
    """
    hits = 0
    for w in words:
        for m in re.finditer(re.escape(w), text):
            start = max(0, m.start() - _NEG_LOOKBEHIND)
            win = _NON_NEGATION_RE.sub("  ", text[start:m.start()])
            if _NEG_PREFIX_RE.search(win):
                hits -= 1
            else:
                hits += 1
    return hits


def sentiment(text: str) -> str:
    """正面 / 负面 / 中性（规则法）。"""
    if not text:
        return "neutral"
    for phrase in _FILLER_PHRASES:
        text = text.replace(phrase, "  ")
    pos = _polarity_hits(text, POSITIVE_WORDS)
    neg = _polarity_hits(text, NEGATIVE_WORDS)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def competitor_mentions(text: str, competitor_names: list, brand_names: list) -> list:
    """竞品提及明细 [{name, count, position}]，position=该竞品首次出现顺位。"""
    result = []
    all_names = brand_names + [str(c).strip() for c in competitor_names if str(c).strip()]
    all_names = list(dict.fromkeys(all_names))
    firsts = first_occurrences(text, all_names)
    ordered = sorted(firsts.items(), key=lambda kv: (kv[1], -len(kv[0])))
    for name in competitor_names:
        name = str(name).strip()
        if not name or name in brand_names:
            continue
        idx = firsts.get(name)
        if idx is None:
            continue
        rank = sum(1 for _, other_idx in ordered if other_idx < idx) + 1
        count = len(re.findall(re.escape(name), text, flags=re.IGNORECASE))
        result.append({"name": name, "count": count, "position": rank})
    return result
