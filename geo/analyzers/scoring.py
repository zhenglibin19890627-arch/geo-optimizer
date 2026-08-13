"""综合评分计算（技术方案第九章，纯函数、可单测）。

总分 = 提及覆盖分(40) + 情感分(20) + 顺位分(20) + 引擎覆盖分(10) + 提及深度分(10)
"""


def score_level_text(score: float) -> str:
    """分数段大白话文案。"""
    if score >= 90:
        return "非常亮眼！AI 回答里经常提到你，而且评价不错、位置靠前"
    if score >= 70:
        return "表现良好，AI 已经认识你，还有提升空间"
    if score >= 50:
        return "一般般，AI 有时提到你，建议按优化建议加强内容布局"
    if score >= 30:
        return "偏弱，AI 很少提到你，建议尽快做内容优化"
    return "AI 几乎不认识你，从「内容优化建议」开始一步步来吧"


def position_partial(position: float) -> float:
    """单条回答的顺位得分：第 1 位=1，第 2 位=0.5，第 3 位=0.33…"""
    if position is None or position <= 0:
        return 0.0
    return 1.0 / position


def depth_partial(avg_mention_count: float) -> float:
    """提及深度分项：平均每条提及次数 1 次=4 分、2 次=7 分、≥3 次=10 分。"""
    if avg_mention_count is None or avg_mention_count <= 0:
        return 0.0
    if avg_mention_count >= 3:
        return 10.0
    if avg_mention_count >= 2:
        return 7.0
    return 4.0


def compute_score(mention_rate: float,
                  net_sentiment: float,
                  position_scores: list,
                  engines_mentioned: int,
                  engines_total: int,
                  avg_mention_count: float) -> dict:
    """返回 {total, breakdown}。

    mention_rate: 0-1 提及率
    net_sentiment: -1~1 净情感率
    position_scores: 每条“提到我的回答”的顺位分（1/顺位）列表；空列表=无提及
    engines_mentioned / engines_total: 提到过我的引擎数 / 本轮实际引擎数
    avg_mention_count: 每条（提到我的）回答的平均提及次数
    """
    mention_rate = max(0.0, min(1.0, float(mention_rate or 0)))
    net_sentiment = max(-1.0, min(1.0, float(net_sentiment or 0)))

    mention_cover = round(mention_rate * 40, 1)
    sentiment = round((net_sentiment + 1) / 2 * 20, 1)

    if position_scores:
        position = round(sum(position_scores) / len(position_scores) * 20, 1)
    else:
        position = 0.0

    if engines_total > 0:
        engine_cover = round(min(engines_mentioned, engines_total) / engines_total * 10, 1)
    else:
        engine_cover = 0.0

    depth = depth_partial(avg_mention_count)

    total = round(mention_cover + sentiment + position + engine_cover + depth)
    total = max(0, min(100, total))

    breakdown = {
        "mention_cover": {"score": mention_cover, "full": 40,
                          "desc": "提到你的回答占所有回答的比例", "value": mention_rate},
        "sentiment": {"score": sentiment, "full": 20,
                      "desc": "正面评价减去负面评价后的净情感", "value": net_sentiment},
        "position": {"score": position, "full": 20,
                     "desc": "你被提到时排在第几位（越靠前越高）", "value": position_scores},
        "engine_cover": {"score": engine_cover, "full": 10,
                         "desc": "几家 AI 引擎提到过你", "value": engines_mentioned},
        "depth": {"score": depth, "full": 10,
                  "desc": "每次提到你的详细程度（被提了几次）", "value": avg_mention_count},
        "total": total,
        "level_text": score_level_text(total),
    }
    return {"total": total, "breakdown": breakdown}
