"""评分纯函数单测（geo/analyzers/scoring.py）。"""

import pytest

from geo.analyzers.scoring import (compute_score, depth_partial,
                                   position_partial, score_level_text)


def test_score_level_text_五档():
    assert "非常亮眼" in score_level_text(95)
    assert "表现良好" in score_level_text(75)
    assert "一般般" in score_level_text(55)
    assert "偏弱" in score_level_text(35)
    assert "几乎不认识" in score_level_text(10)


def test_position_partial():
    assert position_partial(1) == 1.0
    assert position_partial(2) == 0.5
    assert position_partial(3) == pytest.approx(1 / 3)
    assert position_partial(None) == 0.0
    assert position_partial(0) == 0.0
    assert position_partial(-1) == 0.0


def test_depth_partial():
    assert depth_partial(None) == 0.0
    assert depth_partial(0) == 0.0
    assert depth_partial(1) == 4.0
    assert depth_partial(2) == 7.0
    assert depth_partial(3) == 10.0
    assert depth_partial(5) == 10.0


def test_compute_score_满分():
    r = compute_score(1.0, 1.0, [1.0], 5, 5, 3)
    assert r["total"] == 100
    b = r["breakdown"]
    assert b["mention_cover"]["score"] == 40
    assert b["sentiment"]["score"] == 20
    assert b["position"]["score"] == 20
    assert b["engine_cover"]["score"] == 10
    assert b["depth"]["score"] == 10


def test_compute_score_零分():
    r = compute_score(0.0, -1.0, [], 0, 5, 0)
    assert r["total"] == 0
    assert r["breakdown"]["position"]["score"] == 0
    assert r["breakdown"]["engine_cover"]["score"] == 0


def test_compute_score_边界收敛():
    # 越界输入应收敛到 0-100
    r = compute_score(2.0, 2.0, [1.0] * 10, 9, 5, 99)
    assert 0 <= r["total"] <= 100


def test_compute_score_部分提及():
    # 提及率 50%、净情感 0、顺位均值 0.5（第 2 位）、2/5 引擎、均提 1 次
    r = compute_score(0.5, 0.0, [0.5], 2, 5, 1)
    b = r["breakdown"]
    assert b["mention_cover"]["score"] == 20
    assert b["sentiment"]["score"] == 10
    assert b["position"]["score"] == 10
    assert b["engine_cover"]["score"] == 4
    assert b["depth"]["score"] == 4
    assert r["total"] == 48
