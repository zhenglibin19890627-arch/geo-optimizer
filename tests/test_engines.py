"""引擎注册表与各引擎适配器单测。"""

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
