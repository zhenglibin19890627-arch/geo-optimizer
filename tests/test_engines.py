"""引擎注册表与 OpenCode 适配器单测。"""

import pytest

from geo.engines import AUTO_CODES, EngineError_NotFound, get_adapter, get_web_adapter


def test_opencode已注册为自动引擎():
    assert "opencode" in AUTO_CODES
    adapter = get_adapter("opencode")
    assert adapter.code == "opencode"
    assert adapter.display_name == "OpenCode"
    assert adapter.supports_web_search is False
    # 未填钥匙 → 未配置；模型名从配置模板取默认档
    assert adapter.is_configured() is False
    assert adapter.get_model()


def test_opencode联网档被排除():
    with pytest.raises(EngineError_NotFound):
        get_web_adapter("opencode")


def test_未知引擎报错():
    with pytest.raises(EngineError_NotFound):
        get_adapter("no-such-engine")


def test_六家自动引擎注册齐全():
    assert AUTO_CODES == ["deepseek", "kimi", "doubao", "qwen", "yuanbao", "opencode"]
    for code in AUTO_CODES:
        adapter = get_adapter(code)
        assert adapter.code == code
        assert adapter.display_name
