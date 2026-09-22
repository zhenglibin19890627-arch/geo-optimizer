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


# ---------------- 分析/创作模型自由填写（2026-09 用户需求） ----------------

def test_分析模型留空回落厂商当前档(monkeypatch):
    """analysis_model 留空（显式存空串）或未设置 = 厂商当前默认档。"""
    def fake_get(key, default=None, session=None):
        if key == "analysis_model":
            return ""  # 用户在设置页清空
        return default
    monkeypatch.setattr(llm_client.database, "get_setting", fake_get)
    assert llm_client.get_analysis_model() == "deepseek-v4-flash"


def test_分析模型填了就用填的(monkeypatch):
    """手填型号优先，且原样生效（档位白名单已取消，存在性由引擎 API 报错）。"""
    def fake_get(key, default=None, session=None):
        if key == "analysis_model":
            return " my-custom-model "
        return default
    monkeypatch.setattr(llm_client.database, "get_setting", fake_get)
    assert llm_client.get_analysis_model() == "my-custom-model"


def test_创作模型留空回落分析模型(monkeypatch):
    """create_model 留空/未设置 = 跟随分析模型（含分析模型自身的留空回落）。"""
    def fake_get(key, default=None, session=None):
        if key == "create_model":
            return ""
        if key == "analysis_model":
            return "analysis-x"
        return default
    monkeypatch.setattr(llm_client.database, "get_setting", fake_get)
    assert llm_client.get_create_model() == "analysis-x"
