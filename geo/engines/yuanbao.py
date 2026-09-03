"""腾讯元宝位适配器（用户裁决 D1 方案 A：腾讯云「大模型服务平台 TokenHub」）。

口径说明：腾讯元宝没有官方公开 API，本适配器使用腾讯云 TokenHub 平台
（混元家族模型，如 hy3）接入，报告与接口返回中须标注口径差异。
接口地址 2026-08 起为 https://tokenhub.tencentmaas.com/v1（旧混元
OpenAPI 网关 api.hunyuan.cloud.tencent.com 已不兼容 TokenHub 的 Key）。

联网提问（2026-08-22 修订，与混元官网同款）：chat/completions 携带内置
web_search 工具（tools:[{"type":"web_search"}]），服务端执行联网搜索并回信源
（响应 search_info.search_results，由 base 公共解析收进 ChatResult.sources）；
网关不认 tools 时自动回落旧的 enable_enhancement 功能增强参数；
两条路都没拿到信源时，再用腾讯云「联网搜索API」（SearchPro，wsa.tencentcloudapi.com，
见 geo/engines/tencent_wsa.py）按同一问题补结构化信源——需在 config.yaml
engines.yuanbao 下填 wsa_secret_id / wsa_secret_key，未配置时静默跳过。
"""

from geo.engines import tencent_wsa
from geo.engines.base import EngineAdapter, EngineError


class YuanbaoAdapter(EngineAdapter):
    code = "yuanbao"
    display_name = "腾讯元宝（混元底座）"
    note = "本数据来自腾讯云 TokenHub 平台（混元家族模型），与元宝 App 的回答口径可能存在差异。"
    supports_web_search = True

    # 旧联网方案：功能增强参数（网关不认 web_search 工具时的回落）
    _LEGACY_ENHANCEMENT = {
        "enable_enhancement": True,
        "search_info": True,
        "citation": True,
        "force_search_enhancement": True,
    }

    def chat(self, messages, temperature=None, jitter=False, timeout=60,
             web_search=False, model=None):
        if not web_search:
            return self.call_openai_compatible(messages, temperature, jitter=jitter,
                                               timeout=timeout, model=model)
        # 首选：与混元官网同款的内置 web_search 工具（服务端搜索并回信源）
        try:
            result = self.call_openai_compatible(
                messages, temperature, jitter=jitter, timeout=timeout,
                tools=[{"type": "web_search"}], model=model)
        except EngineError as e:
            # 只对"网关不认参数"这类通用错误回落增强参数；
            # 钥匙不对/模型不存在/限流等确定性错误原样抛出
            if "暂时出了点问题" not in str(e):
                raise
            result = self.call_openai_compatible(
                messages, temperature, jitter=jitter, timeout=timeout,
                extra_payload=dict(self._LEGACY_ENHANCEMENT), model=model)
        if not result.sources:
            # web_search 工具/增强参数都没回信源：SearchPro 兜底补齐
            # （凭据缺失时内部直接跳过，不影响回答）
            self._attach_wsa_sources(result, messages)
        return result

    def _attach_wsa_sources(self, result, messages):
        """SearchPro 拉信源挂到结果上；凭据缺失或接口失败都静默降级（无信源）。"""
        sid = str(self.cfg.get("wsa_secret_id") or "").strip()
        skey = str(self.cfg.get("wsa_secret_key") or "").strip()
        if not (sid and skey):
            return
        question = ""
        for m in reversed(messages or []):
            if isinstance(m, dict) and m.get("role") == "user":
                question = str(m.get("content") or "").strip()
                break
        if not question:
            return
        try:
            src = tencent_wsa.search(question, sid, skey)
        except Exception:
            src = []
        if src:
            result.sources = src
