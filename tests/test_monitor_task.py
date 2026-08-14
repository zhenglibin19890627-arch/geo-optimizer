"""监测提问构造与回答分析口径单测。"""

from geo.core.monitor_task import SYSTEM_PROMPT, _analysis_for, build_messages
from geo.engines.base import ChatResult


class _Question:
    id = 1
    text = "实验室改造找哪家公司靠谱？"


class _Adapter:
    code = "deepseek"


def test_只含中性系统提示词与问题本身():
    msgs = build_messages("实验室改造找哪家公司靠谱？")
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == SYSTEM_PROMPT
    assert msgs[1] == {"role": "user", "content": "实验室改造找哪家公司靠谱？"}


def test_不注入品牌档案信息():
    # 防回归：此前把品牌名/简介作为"背景参考"注入系统提示词，导致提及率虚高
    # （连续 9 轮 100%）。提问里绝不允许出现品牌线索。
    msgs = build_messages("实验室改造找哪家公司靠谱？")
    joined = msgs[0]["content"] + "\n" + msgs[1]["content"]
    assert "威启" not in joined
    assert "背景参考" not in joined
    assert "提及该品牌" not in joined
    assert "简介" not in joined


def test_中性提示词不含任何品牌占位():
    assert "{" not in SYSTEM_PROMPT
    assert "品牌" not in SYSTEM_PROMPT


def test_未提及品牌的回答情感计中性():
    # 防回归：夸竞品/夸行业的好评不得算作我方正面（会虚高净情感率）
    brand = {"brand_name": "威启"}
    analysis = _analysis_for(
        _Adapter(), _Question(),
        ChatResult(text="好孩子很好，值得推荐，参考 https://example.com/b", model="m"),
        brand, competitors=["好孩子"], brand_names=["威启"])
    assert analysis["is_mentioned"] is False
    assert analysis["sentiment"] == "neutral"


def test_提及品牌的好评计正面():
    brand = {"brand_name": "威启"}
    analysis = _analysis_for(
        _Adapter(), _Question(),
        ChatResult(text="威启很好，值得推荐", model="m"),
        brand, competitors=["好孩子"], brand_names=["威启"])
    assert analysis["is_mentioned"] is True
    assert analysis["sentiment"] == "positive"
