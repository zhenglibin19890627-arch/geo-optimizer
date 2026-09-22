# -*- coding: utf-8 -*-
"""R7b robots 合规回归：任何抓取路径都不得绕过 robots.txt。

覆盖（零真实网络：robots.txt 与 HTTP 全部打桩）：
- fetch_page：robots 禁止 → FetchError，且不发起 HTTP 请求；
- rewrite_from_url 兜底直连：fetch_page 失败后同样先过 robots 检查，
  禁止抓取时给大白话错误（2026-08 架构评审 R7b：此前兜底会绕过 robots.txt）；
- robots 允许时兜底链路照常工作（不误伤正常链接改写）。
"""

import pytest
import requests as requests_mod

from geo.analyzers.llm_client import AnalysisError
from geo.core import fetcher, knowledge
from geo.core.fetcher import FetchError, RobotsTxt
from geo.core.knowledge import KnowledgeError, rewrite_from_url

URL = "https://example.com/articles/one"
DENY_ALL = "User-agent: *\nDisallow: /\n"


def _deny_all(*a, **k):
    return RobotsTxt(DENY_ALL)


def _allow_all(*a, **k):
    return RobotsTxt("")


def _no_http(*a, **k):
    raise AssertionError("robots 禁止抓取时不应发起任何 HTTP 请求")


class _FakeResp:
    def __init__(self, text="", status_code=200, headers=None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}


def test_fetch_page_robots_disallowed_blocks(monkeypatch):
    """fetch_page：robots 禁止 → FetchError，且不发 HTTP 请求。"""
    monkeypatch.setattr(fetcher, "_fetch_robots", _deny_all)
    monkeypatch.setattr(requests_mod, "get", _no_http)
    with pytest.raises(FetchError) as ei:
        fetcher.fetch_page(URL)
    assert "robots" in ei.value.message


def test_robots_check_allows_when_robots_txt_missing(monkeypatch):
    """robots_check：robots.txt 拉不到/为空 → 视为允许，不抛错。"""
    monkeypatch.setattr(fetcher, "_fetch_robots", _allow_all)
    assert fetcher.robots_check(URL, "Mozilla/5.0") is None


def test_rewrite_from_url_fallback_respects_robots(monkeypatch):
    """R7b 回归：fetch_page 失败后的兜底直连也必须遵守 robots.txt——
    禁止抓取时返回大白话错误，而不是悄悄绕过（旧行为会直连硬抓）。"""
    monkeypatch.setattr(fetcher, "_fetch_robots", _deny_all)
    monkeypatch.setattr(requests_mod, "get", _no_http)
    with pytest.raises(KnowledgeError) as ei:
        rewrite_from_url(1, URL)
    assert "不允许程序读取" in ei.value.message
    assert "粘贴文字" in ei.value.message


def test_rewrite_from_url_fallback_still_works_when_allowed(monkeypatch):
    """robots 允许时兜底链路不误伤：fetch_page 因 5xx 失败 → 兜底直连
    抓到正文 → 正常走到 AI 改写阶段（此处打桩让 AI 失败，
    断言错误来自 AI 而非抓取环节）。"""
    monkeypatch.setattr(fetcher, "_fetch_robots", _allow_all)
    # 同一个假响应：fetch_page 看 503 报"打不开"，兜底直连只用 .text 提正文
    body = "这是一段足够长的正文内容，用来验证兜底抓取在 robots 允许时照常工作。" * 6
    resp = _FakeResp(status_code=503, text=f"<html><body><p>{body}</p></body></html>")
    monkeypatch.setattr(requests_mod, "get", lambda *a, **k: resp)

    def _ai_fail(*a, **k):
        raise AnalysisError("模拟AI失败")
    monkeypatch.setattr(knowledge.llm_client, "chat", _ai_fail)

    with pytest.raises(KnowledgeError) as ei:
        rewrite_from_url(1, URL)
    assert "模拟AI失败" in ei.value.message
