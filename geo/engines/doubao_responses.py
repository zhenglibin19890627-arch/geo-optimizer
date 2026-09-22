"""豆包（火山方舟）联网提问适配器：Responses API（独立封装，不动现有 Chat 适配器）。

联网提问（02d 5.1）：控制台开通「联网内容插件（Web Search）」后，经
POST /api/v3/responses 携带 tools:[{"type":"web_search"}] 调用；
当前 Chat API（/chat/completions）官方文档未记录该工具，故独立新适配器。

信源口径（02d 修订）：调用成功后返回对象含 annotations（url_citation：
url/title/site_name/publish_time/summary），本适配器提取 url_citation 的
url+title 作为结构化信源（经 sources.normalize_sources 归一后随
ChatResult.sources 返回，供 monitor_task 优先落库；回答文本正则解析作为回落）。

2026-09 评审 R3：HTTP 重试/状态码翻译/output 解析/url_citation 信源提取与
DeepSeek Responses 适配器近乎逐行相同，已下沉到 base.ResponsesWebAdapter；
这里只保留豆包的差异项（404 时 ToolNotOpen 插件未开通的开通指引、平台版
模型不存在话术）。
"""

from geo.engines.base import EngineError, ResponsesWebAdapter


class DoubaoResponsesAdapter(ResponsesWebAdapter):
    """豆包 Responses API 适配器（仅联网档使用；chat 恒带 web_search）。

    code/display_name 与 Chat 适配器一致，便于监测任务按引擎代码记账与展示；
    常规档仍走 geo/engines/doubao.py 的 Chat 适配器，两者互不影响。
    """

    code = "doubao"
    display_name = "豆包"
    model_not_found_msg = (
        "豆包 提示模型不存在：请到火山方舟控制台确认模型 ID"
        "（形如 doubao-1-5-lite-32k），再到设置页换模型档位")

    def classify_error(self, resp):
        """404 且响应带 ToolNotOpen → 插件未开通，给控制台开通指引（优先级最高）。"""
        body_text = resp.text or ""
        if resp.status_code == 404 and (
                "ToolNotOpen" in body_text
                or "not activated web search" in body_text.lower()):
            return EngineError(
                "豆包 的联网搜索插件还没开通：请到火山方舟控制台开通"
                "「联网内容插件（Web Search）」（网址 console.volcengine.com/common-buy/"
                "CC_content_plugin）。开通前可先不用「联网提问」模式监测豆包")
        return None
