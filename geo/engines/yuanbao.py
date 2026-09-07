"""腾讯元宝位适配器（用户裁决 D1 方案 A：腾讯云「大模型服务平台 TokenHub」）。

口径说明：腾讯元宝没有官方公开 API，本适配器使用腾讯云 TokenHub 平台
（混元家族模型，如 hy3）接入，报告与接口返回中须标注口径差异。
接口地址 2026-08 起为 https://tokenhub.tencentmaas.com/v1（旧混元
OpenAPI 网关 api.hunyuan.cloud.tencent.com 已不兼容 TokenHub 的 Key）。

联网提问（2026-09-04 实测定案，SearchPro 检索增强）：TokenHub 网关
**不支持任何服务端联网方式**——tools:[{"type":"web_search"}] 直接 400
（tools[0].type invalid），enable_enhancement 等增强参数也不触发搜索
（模型自述"不具备实时联网功能"、知识库截止 2023；诊断脚本
tools/diag_yuanbao_web.py 可随时复测）。故联网档改为：
  1. 先用腾讯云「联网搜索API」（SearchPro，wsa.tencentcloudapi.com，
     见 geo/engines/tencent_wsa.py）按问题拉真实网页结果（含摘录）；
  2. 把搜索结果注入提问上下文，让模型"看着搜到的内容回答"；
  3. 信源随 ChatResult.sources 返回（真·联网口径）。
凭据：需在 config.yaml engines.yuanbao 填 wsa_secret_id / wsa_secret_key
（腾讯云 SecretId/SecretKey，与 TokenHub 的 sk- 钥匙不同）。未配置、
搜索失败或零结果时明确报错——**绝不用离线知识冒充联网回答**。
"""

from geo.analyzers import sources as sources_mod
from geo.engines import tencent_wsa
from geo.engines.base import EngineAdapter, EngineError

# 检索增强最多注入的搜索结果条数（防止提示词过长）
_MAX_INJECT_PAGES = 8
# 每条摘录最多注入的字符数
_MAX_PASSAGE_CHARS = 300


class YuanbaoAdapter(EngineAdapter):
    code = "yuanbao"
    display_name = "腾讯元宝（混元底座）"
    note = ("本数据来自腾讯云 TokenHub 平台（混元家族模型），与元宝 App 的回答口径可能存在差异；"
            "联网档为 SearchPro 检索增强口径（网关不支持模型原生联网，实测 2026-09-04）。")
    supports_web_search = True

    def chat(self, messages, temperature=None, jitter=False, timeout=60,
             web_search=False, model=None):
        if not web_search:
            return self.call_openai_compatible(messages, temperature, jitter=jitter,
                                               timeout=timeout, model=model)
        pages = self._search_pages(messages)
        result = self.call_openai_compatible(
            self._augment_messages(messages, pages), temperature, jitter=jitter,
            timeout=timeout, model=model)
        # 信源随回答一并返回（normalize 归一去重）
        raw = [{"url": p["url"], "title": p.get("title") or p.get("passage") or ""}
               for p in pages]
        result.sources = sources_mod.normalize_sources(raw) or None
        return result

    def _search_pages(self, messages) -> list:
        """按最后一条用户提问拉 SearchPro 结果；凭据缺失/失败/零结果都明确报错。"""
        sid = str(self.cfg.get("wsa_secret_id") or "").strip()
        skey = str(self.cfg.get("wsa_secret_key") or "").strip()
        if not (sid and skey):
            raise EngineError(
                f"{self.display_name}联网提问需要腾讯云「联网搜索API」凭据："
                "TokenHub 网关本身不支持联网（实测 2026-09-04），请到 config.yaml "
                "engines.yuanbao 填写 wsa_secret_id / wsa_secret_key"
                "（腾讯云 SecretId/SecretKey，需先在腾讯云控制台开通「联网搜索API」）")
        question = ""
        for m in reversed(messages or []):
            if isinstance(m, dict) and m.get("role") == "user":
                question = str(m.get("content") or "").strip()
                break
        if not question:
            raise EngineError(f"{self.display_name}联网提问没有找到问题内容，请重新发起")
        try:
            pages = tencent_wsa.search_raw(question, sid, skey)
        except tencent_wsa.WsaError as e:
            raise EngineError(f"{self.display_name}联网搜索失败：{e}")
        if not pages:
            # 零结果也不能拿离线知识冒充联网回答
            raise EngineError(
                f"{self.display_name}联网搜索没有返回任何网页结果，本轮无法联网回答"
                "（不会用离线知识冒充），请换个问法或稍后再试")
        return pages

    @staticmethod
    def _augment_messages(messages, pages) -> list:
        """把搜索结果（标题+网址+摘录）注入最后一条用户提问。"""
        lines = []
        for i, p in enumerate(pages[:_MAX_INJECT_PAGES], 1):
            line = f"{i}. {p.get('title') or '（无标题）'}（{p['url']}）"
            if p.get("date"):
                line += f"[{p['date']}]"
            passage = (p.get("passage") or "").strip()
            if passage:
                line += f"：{passage[:_MAX_PASSAGE_CHARS]}"
            lines.append(line)
        block = ("\n\n【实时搜索结果】以下是刚从互联网检索到的网页（含摘录），"
                 "可能包含比你的训练数据更新的信息：\n" + "\n".join(lines)
                 + "\n\n请优先依据以上搜索结果回答；若搜索结果与你的既有知识冲突，"
                   "以搜索结果为准；引用信息时注明来源编号或网址；"
                   "若搜索结果不足以回答，请直接说明，不要编造。")
        out = [dict(m) for m in (messages or [])]
        for i in range(len(out) - 1, -1, -1):
            if out[i].get("role") == "user":
                out[i]["content"] = str(out[i].get("content") or "") + block
                return out
        out.append({"role": "user", "content": block.strip()})
        return out
