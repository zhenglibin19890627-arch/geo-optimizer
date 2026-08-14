"""腾讯元宝位适配器（用户裁决 D1 方案 A：腾讯云「大模型服务平台 TokenHub」）。

口径说明：腾讯元宝没有官方公开 API，本适配器使用腾讯云 TokenHub 平台
（混元家族模型，如 hy3）接入，报告与接口返回中须标注口径差异。
接口地址 2026-08 起为 https://tokenhub.tencentmaas.com/v1（旧混元
OpenAPI 网关 api.hunyuan.cloud.tencent.com 已不兼容 TokenHub 的 Key）。

联网提问（02d 5.1）：ChatCompletions 参数 enable_enhancement（功能增强开关，
2025-04-20 起默认关闭）+ search_info + citation + force_search_enhancement；
OpenAI 兼容接口用同名 snake_case 参数（TokenHub 网关对不支持的参数会忽略或报错，
联网档效果以实测为准）。

信源口径：联网档的引用由基类统一提取——优先 choices[0].message.citations[]
（{title, url, ...}，OpenAI 兼容通用结构），兜底顶层 search_info.search_results[]
（混元原生字段名），经 sources.normalize_sources 归一为 {title,url,domain,category}
后随 ChatResult.sources 返回（见 geo/engines/base.py call_openai_compatible）。
"""

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
        return self.call_openai_compatible(messages, temperature, jitter=jitter,
                                           timeout=timeout, extra_payload=extra,
                                           model=model)
