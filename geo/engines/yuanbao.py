"""腾讯元宝位适配器（用户裁决 D1 方案 A：腾讯云「大模型服务平台 TokenHub」）。

口径说明：腾讯元宝没有官方公开 API，本适配器使用腾讯云 TokenHub 平台
（混元家族模型，如 hy3）接入，报告与接口返回中须标注口径差异。
接口地址 2026-08 起为 https://tokenhub.tencentmaas.com/v1（旧混元
OpenAPI 网关 api.hunyuan.cloud.tencent.com 已不兼容 TokenHub 的 Key）。

联网提问（02d 5.1）：ChatCompletions 参数 enable_enhancement（功能增强开关，
2025-04-20 起默认关闭）+ search_info + citation + force_search_enhancement；
OpenAI 兼容接口用同名 snake_case 参数（TokenHub 网关对不支持的参数会忽略或报错，
联网档效果以实测为准）。

信源口径（2026-08-16 修订）：TokenHub 的 chat 响应实测不含任何来源字段，故联网档
在回答成功后，用腾讯云「联网搜索API」（SearchPro，wsa.tencentcloudapi.com，
见 geo/engines/tencent_wsa.py）按同一问题拉取结构化信源挂到 ChatResult.sources；
需在 config.yaml engines.yuanbao 下填 wsa_secret_id / wsa_secret_key（腾讯云
SecretId/SecretKey），未配置时静默跳过（无信源，不影响回答）。
"""

from geo.engines import tencent_wsa
from geo.engines.base import EngineAdapter


class YuanbaoAdapter(EngineAdapter):
    code = "yuanbao"
    display_name = "腾讯元宝（混元底座）"
    note = "本数据来自腾讯云 TokenHub 平台（混元家族模型），与元宝 App 的回答口径可能存在差异。"
    supports_web_search = True

    def chat(self, messages, temperature=None, jitter=False, timeout=60,
             web_search=False, model=None):
        extra = None
        if web_search:
            extra = {
                "enable_enhancement": True,
                "search_info": True,
                "citation": True,
                "force_search_enhancement": True,
            }
        result = self.call_openai_compatible(messages, temperature, jitter=jitter,
                                             timeout=timeout, extra_payload=extra,
                                             model=model)
        if web_search:
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
