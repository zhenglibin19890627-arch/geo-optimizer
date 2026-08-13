"""提及判定 / 次数 / 顺位 / 情感分析（规则法为主，无需钥匙即可运行，口径稳定）。"""

import re

# ---- 情感词典（面向品牌/产品语境的中文常用词）----
POSITIVE_WORDS = [
    "好", "优秀", "出色", "很棒", "很好", "非常好", "特别好", "极好", "一流", "顶级",
    "推荐", "强推", "值得买", "值得推荐", "性价比高", "高性价比", "划算", "实惠", "物超所值",
    "可靠", "靠谱", "稳定", "耐用", "扎实", "放心", "安全", "口碑好", "好评", "好评如潮",
    "喜欢", "满意", "满意", "惊艳", "惊喜", "好用", "实用", "方便", "便捷", "高效",
    "强大", "功能强", "全面", "丰富", "完善", "先进", "领先", "创新", "专业", "精致",
    "美观", "漂亮", "好看", "流畅", "轻便", "轻薄", "清晰", "省心", "省事", "舒服",
    "贴心", "良心", "诚心", "优秀", "无敌", "天花板", "首选", "王者", "标杆", "神器",
    "进步", "提升", "改善", "改进", "突破", "超越", "最佳", "最优", "完美", "神器",
]
NEGATIVE_WORDS = [
    "差", "很差", "非常差", "垃圾", "拉胯", "拉胯", "翻车", "踩雷", "避雷", "坑",
    "不值得", "不推荐", "别买", "不要买", "劝退", "差评", "差评如潮", "投诉", "维权",
    "问题多", "毛病", "故障", "死机", "卡顿", "卡死", "闪退", "掉线", "不稳", "不稳定",
    "延迟", "发热", "烫", "漏电", "炸", "爆炸", "缩水", "虚标", "夸大", "欺骗", "虚假",
    "失望", "后悔", "遗憾", "生气", "愤怒", "无语", "恶心", "糟糕", "平庸", "一般般",
    "凑合", "勉强", "落后", "过时", "难用", "费劲", "麻烦", "繁琐", "复杂", "笨重",
    "贵", "太贵", "溢价", "智商税", "割韭菜", "售后差", "客服差", "质保差", "偷工减料",
    "降级", "阉割", "缺陷", "漏洞", "bug", "闪断", "报错", "失败", "失灵", "失灵",
]

_NEG_PREFIX_RE = re.compile(r"(不|没|无|未|别|非|不太|一点也不|毫无)")


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


def _word_sentiment_hits(text: str, words: list) -> int:
    hits = 0
    for w in words:
        for m in re.finditer(re.escape(w), text):
            hits += 1
            # 前面 2 个字内出现否定词 → 意思反转
            start = max(0, m.start() - 2)
            if _NEG_PREFIX_RE.search(text[start:m.start()]):
                hits -= 2
    return hits


def sentiment(text: str) -> str:
    """正面 / 负面 / 中性（规则法）。"""
    if not text:
        return "neutral"
    pos = _word_sentiment_hits(text, POSITIVE_WORDS)
    neg = _word_sentiment_hits(text, NEGATIVE_WORDS)
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
