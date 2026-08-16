"""引擎注册表与各引擎适配器单测。"""

import json

import pytest

from geo.engines import AUTO_CODES, EngineError_NotFound, get_adapter, get_web_adapter
from geo.engines.deepseek_responses import DeepSeekResponsesAdapter
from geo.engines.opencode import OpenCodeAdapter


def test_opencode已注册为自动引擎():
    assert "opencode" in AUTO_CODES
    adapter = get_adapter("opencode")
    assert adapter.code == "opencode"
    assert adapter.display_name == "OpenCode"
    assert adapter.supports_web_search is False  # 官方 API 无联网工具
    assert adapter.get_model()  # 模型名从配置模板取默认档
    # is_configured 取决于用户是否已填钥匙（真实 config.yaml 状态），不做断言


def test_opencode联网档被排除():
    with pytest.raises(EngineError_NotFound):
        get_web_adapter("opencode")


def test_opencode按模型路由三种API形态():
    assert OpenCodeAdapter._shape_for("grok-4.5") == "responses"
    assert OpenCodeAdapter._shape_for("gpt-5.6-luna") == "responses"
    assert OpenCodeAdapter._shape_for("minimax-m3") == "anthropic"
    assert OpenCodeAdapter._shape_for("qwen3.7-plus") == "anthropic"
    assert OpenCodeAdapter._shape_for("glm-5.3") == "chat"
    assert OpenCodeAdapter._shape_for("kimi-k3") == "chat"
    assert OpenCodeAdapter._shape_for("deepseek-v4-flash") == "chat"
    assert OpenCodeAdapter._shape_for("hy3") == "chat"


def test_deepseek支持联网且走Responses适配器():
    adapter = get_adapter("deepseek")
    assert adapter.supports_web_search is True
    web = get_web_adapter("deepseek")
    assert isinstance(web, DeepSeekResponsesAdapter)
    assert web.code == "deepseek"
    assert web.display_name == "DeepSeek"


def test_qwen联网档白名单下发():
    from geo.engines import adapter_meta
    meta = adapter_meta("qwen")
    names = [o["name"] for o in meta["web_model_options"]]
    assert names == ["qwen3.7-max-2026-05-20"]  # 实时翻译模型不在联网白名单


def test_qwen联网请求带enable_source并解析来源(monkeypatch):
    """2026-08-16：联网请求必须带 search_options.enable_source，
    响应的 search_info.search_results 才会被解析进 sources。"""
    from geo.engines import qwen as qwen_mod
    captured = {}

    class FakeResp:
        status_code = 200
        text = ""

        def json(self):
            return {
                "output": {
                    "choices": [{"message": {"content": "回答内容 [1]"}}],
                    "search_info": {"search_results": [
                        {"index": 1, "title": "示例网",
                         "url": "https://example.com/a"}]},
                },
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        return FakeResp()

    monkeypatch.setattr(qwen_mod.requests, "post", fake_post)
    adapter = qwen_mod.QwenAdapter()
    res = adapter._dashscope_chat_once(
        qwen_mod.QwenAdapter._TEXT_EP,
        [{"role": "user", "content": "杭州天气"}], "qwen-plus", 0.3, 60)
    params = captured["payload"]["parameters"]
    assert params["enable_search"] is True
    assert params["search_options"] == {"enable_source": True}
    assert res.sources and res.sources[0]["url"] == "https://example.com/a"


def test_qwen37max按文本模型路由():
    """2026-08-16：qwen3.7-max 是纯文本模型，误走多模态端点会静默丢搜索来源。"""
    from geo.engines.qwen import QwenAdapter
    assert QwenAdapter._is_multimodal("qwen3.7-max-2026-05-20") is False
    assert QwenAdapter._is_multimodal("qwen3.7-plus") is False
    assert QwenAdapter._is_multimodal("qwen-vl-max") is True


def test_tencent_wsa_search解析来源并带TC3签名(monkeypatch):
    """2026-08-16：腾讯云联网搜索API（SearchPro）→ 标准信源 + TC3 签名头。"""
    from geo.engines import tencent_wsa

    captured = {}

    class FakeResp:
        def json(self):
            return {"Response": {"Pages": [
                json.dumps({"title": "示例页", "url": "https://example.com/a",
                            "site": "示例网"})]}}

    def fake_post(url, data=None, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = data
        return FakeResp()

    monkeypatch.setattr(tencent_wsa.requests, "post", fake_post)
    out = tencent_wsa.search("测试词", "AKIDx", "SECRETx")
    assert out and out[0]["url"] == "https://example.com/a"
    assert captured["url"] == "https://wsa.tencentcloudapi.com"
    assert captured["headers"]["X-TC-Action"] == "SearchPro"
    assert captured["headers"]["X-TC-Version"] == "2025-05-08"
    assert captured["headers"]["Authorization"].startswith(
        "TC3-HMAC-SHA256 Credential=AKIDx/")
    assert json.loads(captured["body"].decode("utf-8")) == {
        "Query": "测试词", "Mode": 0}


def test_tencent_wsa_error抛WsaError(monkeypatch):
    from geo.engines import tencent_wsa

    class FakeResp:
        def json(self):
            return {"Response": {"Error": {
                "Code": "ResourceNotFound", "Message": "用户资源未开通"}}}

    monkeypatch.setattr(tencent_wsa.requests, "post",
                        lambda *a, **k: FakeResp())
    with pytest.raises(tencent_wsa.WsaError):
        tencent_wsa.search("测试词", "AKIDx", "SECRETx")


def test_yuanbao联网档无WSA凭据静默无信源(monkeypatch):
    from geo.engines.base import ChatResult
    from geo.engines.yuanbao import YuanbaoAdapter
    a = YuanbaoAdapter()
    a.cfg = {"api_key": "sk-x", "wsa_secret_id": "", "wsa_secret_key": ""}
    monkeypatch.setattr(a, "call_openai_compatible",
                        lambda *args, **kw: ChatResult(text="答", model="hy3"))
    res = a.chat([{"role": "user", "content": "问题"}], web_search=True)
    assert res.sources is None


def test_yuanbao联网档挂SearchPro信源(monkeypatch):
    from geo.engines import tencent_wsa
    from geo.engines.base import ChatResult
    from geo.engines.yuanbao import YuanbaoAdapter
    a = YuanbaoAdapter()
    a.cfg = {"api_key": "sk-x", "wsa_secret_id": "AKIDx",
             "wsa_secret_key": "SECRETx"}
    monkeypatch.setattr(a, "call_openai_compatible",
                        lambda *args, **kw: ChatResult(text="答", model="hy3"))
    monkeypatch.setattr(tencent_wsa, "search",
                        lambda q, sid, skey: [{"title": "t",
                                               "url": "https://example.com/x"}])
    res = a.chat([{"role": "user", "content": "龙泉市公司"}], web_search=True)
    assert res.sources == [{"title": "t", "url": "https://example.com/x"}]


def test_联网档四家引擎齐全():
    # DeepSeek（Responses API）+ 豆包（Responses API）+ 通义千问 + 腾讯元宝
    for code in ("deepseek", "doubao", "qwen", "yuanbao"):
        assert get_web_adapter(code).supports_web_search is True


def test_未知引擎报错():
    with pytest.raises(EngineError_NotFound):
        get_adapter("no-such-engine")


def test_五家自动引擎注册齐全():
    assert AUTO_CODES == ["deepseek", "doubao", "qwen", "yuanbao", "opencode"]
    for code in AUTO_CODES:
        adapter = get_adapter(code)
        assert adapter.code == code
        assert adapter.display_name
