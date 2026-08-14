"""监测提问构造单测：中性提问，绝不注入品牌信息（GEO 测试原则）。"""

from geo.core.monitor_task import SYSTEM_PROMPT, build_messages


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
