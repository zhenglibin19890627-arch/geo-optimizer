"""DeepSeek 联网提问适配器：Responses API（独立封装，不动现有 Chat 适配器）。

DeepSeek 官方 2026 年提供 Responses API 联网搜索
（文档 https://api-docs.deepseek.com/zh-cn/guides/responses_api）：
POST /responses 携带 tools:[{"type":"web_search"}]，服务端执行联网搜索
（事件 response.web_search_call.in_progress/searching/completed 用于流式进度），
非流式返回的 output[].message.content[] 含最终回答，引用以 url_citation
annotations 形式给出（字段与 OpenAI Responses 兼容口径一致）。

信源口径：本适配器提取 output 中 url_citation 的 url+title 作为结构化信源
（经 sources.normalize_sources 归一后随 ChatResult.sources 返回，供
monitor_task 优先落库；回答文本正则解析作为回落）。

2026-09 评审 R3：HTTP 重试/状态码翻译/output 解析/url_citation 信源提取与
豆包 Responses 适配器近乎逐行相同，已下沉到 base.ResponsesWebAdapter；
这里只保留 DeepSeek 的差异项（联网工具名可配置、404 平台话术、空回答校验）。
"""

from geo.engines.base import ResponsesWebAdapter


class DeepSeekResponsesAdapter(ResponsesWebAdapter):
    """DeepSeek Responses API 适配器（仅联网档使用；chat 恒带 web_search）。

    code/display_name 与 Chat 适配器一致，便于监测任务按引擎代码记账与展示；
    常规档仍走 geo/engines/deepseek.py 的 Chat 适配器，两者互不影响。
    """

    code = "deepseek"
    display_name = "DeepSeek"
    # 工具名以官方文档为准（web_search）；如官方后续改名，改 config.yaml 的
    # engines.deepseek.web_tool_type 即可，无需动代码
    web_tool_type_setting = "web_tool_type"
    model_not_found_msg = (
        "DeepSeek 提示模型不存在或不支持联网搜索：请到官方文档"
        "（api-docs.deepseek.com）确认支持 Responses API 联网的模型名，"
        "再到设置页换模型档位")
    empty_text_message = "DeepSeek 联网搜索没有返回内容，请稍后再试"
