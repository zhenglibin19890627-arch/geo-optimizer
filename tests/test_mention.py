"""提及/情感/顺位纯函数单测（geo/analyzers/mention.py）。"""

from geo.analyzers.mention import (brand_position, build_brand_names,
                                   competitor_mentions, first_occurrences,
                                   mention_count, sentiment)


def test_build_brand_names_去空去重长名在前():
    names = build_brand_names({
        "brand_name": "威启",
        "brand_aliases": ["威启科技", "", "威启", "  "],
    })
    assert names == ["威启科技", "威启"]


def test_first_occurrences():
    r = first_occurrences("今天威启和好孩子都提到了", ["威启", "好孩子"])
    assert r["威启"] == 2
    assert r["好孩子"] == 5
    assert "不存在" not in first_occurrences("文本", ["不存在"])


def test_mention_count_别名与忽略大小写():
    assert mention_count("威启很好，威启科技也不错", ["威启", "威启科技"]) == 2
    assert mention_count("Apple 和 apple", ["apple"]) == 2
    assert mention_count("", ["威启"]) == 0
    assert mention_count("没有提及", []) == 0


def test_brand_position_自己第一():
    assert brand_position("威启做得不错，其他家一般", ["威启"], ["好孩子"]) == 1


def test_brand_position_被竞品压到第二():
    assert brand_position("好孩子先提到，然后才是威启", ["威启"], ["好孩子"]) == 2


def test_brand_position_未提及返回None():
    assert brand_position("完全没提", ["威启"], []) is None


def test_sentiment_正负中():
    assert sentiment("这个品牌非常好，值得推荐") == "positive"
    assert sentiment("质量很差，垃圾产品") == "negative"
    assert sentiment("今天天气不错") == "neutral"
    assert sentiment("") == "neutral"


def test_sentiment_否定反转():
    assert sentiment("服务不好") == "negative"
    # 修复前："很好"被"不是"反转 与"好"单独计数抵消 → 误判 neutral（交付报告已记录的
    # 已知限制）；否定窗口扩到 4 字 + 反转记 -1 后正确判负。
    assert sentiment("不是很好") == "negative"
    assert sentiment("没那么好") == "negative"
    # 负面词遇否定 → 翻正："一点也不差" 是好评
    assert sentiment("一点也不差") == "positive"
    assert sentiment("没啥毛病") == "positive"
    # "不太贵"（价格可接受）应翻正
    assert sentiment("价格不太贵") == "positive"


def test_sentiment_非否定短语不误反转():
    # "非常/无比/不仅/不过"等以否定字开头但非否定的短语，不得误判负面
    assert sentiment("这个品牌非常好，值得推荐") == "positive"
    assert sentiment("这款产品无比好用") == "positive"
    assert sentiment("不仅好用，而且便宜") == "positive"
    assert sentiment("虽然有点贵，不过质量很好") == "positive"


def test_sentiment_礼貌短语不误判():
    # "不好意思" 是固定礼貌短语，不应因"不+好"被判负面
    assert sentiment("不好意思，想请问一下") == "neutral"


def test_sentiment_词典无重复词条():
    from geo.analyzers.mention import NEGATIVE_WORDS, POSITIVE_WORDS
    # 重复词条会让一次出现计 2 分，属口径缺陷；词表必须保持无重复
    assert len(POSITIVE_WORDS) == len(set(POSITIVE_WORDS))
    assert len(NEGATIVE_WORDS) == len(set(NEGATIVE_WORDS))


def test_competitor_mentions():
    text = "好孩子很好，然后威启也不错，好孩子又出现一次"
    r = competitor_mentions(text, ["好孩子"], ["威启"])
    assert len(r) == 1
    assert r[0]["name"] == "好孩子"
    assert r[0]["count"] == 2
    assert r[0]["position"] == 1
