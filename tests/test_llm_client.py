"""分析模型客户端厂商切换单测（零网络调用）。"""

from geo.analyzers import llm_client


def test_厂商默认deepseek(monkeypatch):
    # 未配置 analysis_vendor 时回落 deepseek（不读真实库，避免受用户设置影响）
    def fake_get(key, default=None, session=None):
        return default
    monkeypatch.setattr(llm_client.database, "get_setting", fake_get)
    assert llm_client.get_analysis_vendor() == "deepseek"


def test_厂商配置读写(monkeypatch):
    def fake_get(key, default=None, session=None):
        return "opencode" if key == "analysis_vendor" else default
    monkeypatch.setattr(llm_client.database, "get_setting", fake_get)
    assert llm_client.get_analysis_vendor() == "opencode"


def test_opencode厂商配置来自引擎节():
    key, base, model = llm_client._vendor_cfg("opencode")
    assert base == "https://opencode.ai/zen/go/v1"
    assert model == "deepseek-v4-flash"
    assert isinstance(key, str)


def test_非法厂商回落deepseek(monkeypatch):
    def fake_get(key, default=None, session=None):
        return "no-such-vendor" if key == "analysis_vendor" else default
    monkeypatch.setattr(llm_client.database, "get_setting", fake_get)
    assert llm_client.get_analysis_vendor() == "deepseek"


def test_deepseek厂商兼容旧analysis节():
    # deepseek 引擎钥匙为空时回落 analysis 节（老配置口径）
    key, base, model = llm_client._vendor_cfg("deepseek")
    assert base == "https://api.deepseek.com"
    assert model == "deepseek-v4-flash"
    assert isinstance(key, str)
