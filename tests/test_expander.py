"""问题扩展器单测（geo/core/question_expander.py）。

- _extract_questions 解析（新 JSON / 旧数组 / 兜底行）
- expand_questions 参数校验与裁剪（用 monkeypatch 拦截模型调用，不产生真实费用）
"""

import pytest

from geo.core import question_expander
from geo.core.question_expander import _extract_questions


def test_extract_新JSON格式():
    text = ('{"brand": "威启", "questions": [{"id": 1, "type": "求推荐",'
            ' "question": "A问？", "expected_trigger": "t"}]}')
    qs, meta = _extract_questions(text)
    assert qs == ["A问？"]
    assert meta["brand"] == "威启"


def test_extract_旧数组格式():
    qs, meta = _extract_questions('["B问？", "C问？"]')
    assert qs == ["B问？", "C问？"]


def test_extract_代码块包裹():
    text = '```json\n{"questions": [{"question": "D问？"}]}\n```'
    qs, _ = _extract_questions(text)
    assert qs == ["D问？"]


def test_extract_兜底行提取():
    qs, _ = _extract_questions("1. 理化生实验室改造找哪家靠谱？\n2. 售后找谁？")
    assert qs == ["理化生实验室改造找哪家靠谱？", "售后找谁？"]


def test_extract_空输入():
    assert _extract_questions("") == ([], {})
    assert _extract_questions(None) == ([], {})


def test_expand_无关键词报错():
    with pytest.raises(Exception):
        question_expander.expand_questions([])


def test_expand_调用模型并裁剪(monkeypatch):
    captured = {}

    def fake_chat(prompt, temperature=0.3, timeout=60, system=None):
        captured["prompt"] = prompt
        captured["temperature"] = temperature
        return '["1问？", "2问？", "3问？", "4问？", "5问？"]'

    monkeypatch.setattr("geo.analyzers.llm_client.chat", fake_chat)
    qs = question_expander.expand_questions(["关键词A"], count=3)
    assert qs == ["1问？", "2问？", "3问？"]  # 只取前 count 条
    assert "关键词A" in captured["prompt"]
    assert captured["temperature"] == 0.7


def test_expand_count边界(monkeypatch):
    monkeypatch.setattr(
        "geo.analyzers.llm_client.chat",
        lambda prompt, temperature=0.3, timeout=60, system=None: '["x问？"]')
    assert len(question_expander.expand_questions(["k"], count=0)) == 1
    assert len(question_expander.expand_questions(["k"], count=99)) == 1


def test_expand_模型返回空则报错(monkeypatch):
    monkeypatch.setattr(
        "geo.analyzers.llm_client.chat",
        lambda prompt, temperature=0.3, timeout=60, system=None: "没有内容")
    with pytest.raises(Exception):
        question_expander.expand_questions(["k"], count=5)
