# -*- coding: utf-8 -*-
"""知识库：文档管理 + AI 关键词提取（蒸馏词）+ 基于知识库生成分发稿件。
流程参考 weiqi 的 knowledgeDocs/蒸馏词/AI 生成能力，代码全部自研，
AI 调用复用本系统的多厂商 llm_client。"""
import json

from geo.analyzers import llm_client
from geo.analyzers.llm_client import AnalysisError
from geo.models import db as database


class KnowledgeError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _parse_keywords(raw: str):
    try:
        arr = json.loads(raw or "[]")
        return arr if isinstance(arr, list) else []
    except (ValueError, TypeError):
        return []


MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 与 weiqi 口径一致：单个文件 ≤ 20MB
SUPPORTED_EXTS = ".md .markdown .txt .pdf .docx .doc .xlsx .xls".split()


def _pdf_to_text(data: bytes) -> str:
    import io
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return "\n\n".join(p for p in pages if p.strip())


def _docx_to_text(data: bytes) -> str:
    import io
    import docx  # python-docx
    document = docx.Document(io.BytesIO(data))
    lines = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:  # 表格按行转竖线分隔文本
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                lines.append(" | ".join(cells))
    return "\n".join(lines)


def _xlsx_to_text(data: bytes) -> str:
    import io
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts = []
    for sheet in wb.worksheets:
        lines = [f"## 工作表：{sheet.title}"]
        for row in sheet.iter_rows(values_only=True):
            cells = ["" if v is None else str(v).strip() for v in row]
            if any(cells):
                lines.append(" | ".join(cells))
        if len(lines) > 1:
            parts.append("\n".join(lines))
    return "\n\n".join(parts)


def parse_uploaded_file(filename: str, data: bytes) -> str:
    """按扩展名解析上传文件为纯文本（供知识库存储与 AI 参考）。"""
    lower = (filename or "").lower()
    if lower.endswith((".md", ".markdown", ".txt")):
        return data.decode("utf-8", errors="replace")
    if lower.endswith(".pdf"):
        return _pdf_to_text(data)
    if lower.endswith(".docx"):
        return _docx_to_text(data)
    if lower.endswith((".xlsx", ".xls")):
        if lower.endswith(".xls"):
            raise KnowledgeError("旧版 .xls 请先用 Excel 另存为 .xlsx 再上传")
        return _xlsx_to_text(data)
    raise KnowledgeError("暂不支持该格式：支持 " + " / ".join(SUPPORTED_EXTS))


def save_uploaded_file(brand_id: int, filename: str, data: bytes) -> int:
    if len(data) > MAX_UPLOAD_BYTES:
        raise KnowledgeError("文件太大（单个 ≤ 20MB）")
    text = parse_uploaded_file(filename, data)
    if not text.strip():
        raise KnowledgeError("文件里没有解析出文字内容（可能是扫描版 PDF 或空文件）")
    title = filename.rsplit(".", 1)[0] if "." in filename else filename
    return create_doc(brand_id, title, text)


def list_docs(brand_id: int) -> list:
    with database.session_scope() as s:
        rows = (s.query(database.KnowledgeDoc)
                .filter(database.KnowledgeDoc.brand_id == brand_id)
                .order_by(database.KnowledgeDoc.id.desc()).all())
        return [r.to_dict() for r in rows]


def create_doc(brand_id: int, title: str, content: str) -> int:
    if not (title or "").strip():
        raise KnowledgeError("文档标题不能为空")
    if not (content or "").strip():
        raise KnowledgeError("文档内容不能为空")
    now = database.now()
    with database.session_scope() as s:
        row = database.KnowledgeDoc(
            brand_id=brand_id, title=title.strip(), content=content,
            keywords="", created_at=now, updated_at=now)
        s.add(row)
        s.flush()
        return row.id


def update_doc(doc_id: int, brand_id: int, title: str, content: str) -> None:
    with database.session_scope() as s:
        row = s.get(database.KnowledgeDoc, doc_id)
        if not row or row.brand_id != brand_id:
            raise KnowledgeError("知识库文档不存在")
        if (title or "").strip():
            row.title = title.strip()
        if content is not None:
            row.content = content
        row.updated_at = database.now()


def delete_doc(doc_id: int, brand_id: int) -> None:
    with database.session_scope() as s:
        row = s.get(database.KnowledgeDoc, doc_id)
        if not row or row.brand_id != brand_id:
            raise KnowledgeError("知识库文档不存在")
        s.delete(row)


def extract_keywords(doc_id: int, brand_id: int, count: int = 10) -> list:
    """AI 从文档提炼核心关键词（蒸馏词），存回文档并返回。"""
    count = max(3, min(int(count or 10), 30))
    with database.session_scope() as s:
        row = s.get(database.KnowledgeDoc, doc_id)
        if not row or row.brand_id != brand_id:
            raise KnowledgeError("知识库文档不存在")
        title, content = row.title or "", row.content or ""
    if not content.strip():
        raise KnowledgeError("文档内容为空，无法提取关键词")

    system = ("你是一位专业的中文关键词提取专家，擅长从文章中提炼核心关键词。")
    prompt = (
        "从下面的【文章内容】中提取最核心、最能代表主题的关键词。\n"
        "要求：\n"
        f"- 提取 {count} 个左右（2-12 个字符的词或短语，不要长句）\n"
        "- 覆盖文章主题、核心概念、专有名词、技术术语\n"
        "- 优先提取反复出现、有区分度的概念；不要\"文章\"\"内容\"这类无意义词\n"
        '- 严格输出 JSON 数组，如 ["关键词一","关键词二"]，不要任何解释或 markdown 包裹\n\n'
        f"【文章标题】{title}\n【文章内容】\n{content[:6000]}")
    try:
        text = llm_client.chat(prompt, temperature=0.2, timeout=150, system=system)
    except AnalysisError as e:
        raise KnowledgeError(e.message)
    # 容错解析：剥掉 markdown 代码块围栏后找 JSON 数组
    cleaned = text.replace("```json", "").replace("```", "").strip()
    start, end = cleaned.find("["), cleaned.rfind("]")
    words = []
    if start >= 0 and end > start:
        try:
            arr = json.loads(cleaned[start:end + 1])
            words = [str(w).strip() for w in arr if str(w).strip()]
        except (ValueError, TypeError):
            words = []
    if not words:
        # 兜底：AI 没输出合法 JSON 时，按分隔符强行切词
        import re
        stripped = re.sub(r'^[\[\]{}"\s:，,。]*|["\[\]}\s]+$', "", cleaned)
        words = [w.strip(' "\'、，。') for w in re.split(r'[、,，\n；;]', stripped)]
        words = [w for w in words if 2 <= len(w) <= 12 and not w.startswith(("以下", "关键词", "提取"))]
    if not words:
        raise KnowledgeError("AI 返回的内容无法解析出关键词（原文前 80 字：" + cleaned[:80] + "），请再点一次重试")
    with database.session_scope() as s:
        row = s.get(database.KnowledgeDoc, doc_id)
        if row:
            row.keywords = json.dumps(words[:count], ensure_ascii=False)
            row.updated_at = database.now()
    return words[:count]


def generate_draft(brand_id: int, doc_ids: list, keywords: list = None,
                   user_instruction: str = "", generate_title: bool = True) -> int:
    """基于知识库文档（+ 蒸馏词 + 用户指令）生成一篇分发稿件草稿（draft 状态，人工审阅后发布）。"""
    with database.session_scope() as s:
        docs = (s.query(database.KnowledgeDoc)
                .filter(database.KnowledgeDoc.brand_id == brand_id,
                        database.KnowledgeDoc.id.in_(list(doc_ids or []))).all())
        refs = [f"【{d.title}】\n{(d.content or '')[:2500]}" for d in docs]
        if not keywords:
            merged = []
            for d in docs:
                merged.extend(_parse_keywords(d.keywords))
            keywords = merged
    if not refs:
        raise KnowledgeError("请先勾选至少一篇知识库文档")

    kw_line = "、".join(keywords[:8]) if keywords else ""
    system = "你是一位专业的中文内容创作者，善于把参考资料转化为可读性强的新文章。"
    prompt = (
        f"【知识库文档（参考资料）】共 {len(refs)} 篇\n\n" + "\n\n".join(refs) + "\n\n"
        + (f"【蒸馏词】（标题需自然融合其中 2-3 个关键词）{kw_line}\n" if kw_line and generate_title else "")
        + "【用户指令】基于知识库文档"
          + ("生成标题和正文" if generate_title else "生成正文") +
          (f"。{user_instruction.strip()}" if user_instruction.strip() else "。主题自由发挥，观点明确。") + "\n\n"
        "输出要求（严格 JSON 对象，不要任何解释、前后缀、markdown 包裹）：\n"
        '{"title": "文章标题（8-25 字，简洁有力）", "body": "Markdown 正文（可用 # ## ### 分层，'
        '从第一个段落或 # 标题开始，不含 title 字段内容；引号用中文引号）"}')
    try:
        text = llm_client.chat(prompt, temperature=0.7, timeout=180, system=system)
    except AnalysisError as e:
        raise KnowledgeError(e.message)
    cleaned = text.replace("```json", "").replace("```", "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise KnowledgeError("AI 返回内容无法解析为文章，请稍后再试")
    try:
        obj = json.loads(cleaned[start:end + 1])
        title = str(obj.get("title") or "").strip()
        body = str(obj.get("body") or "").strip()
    except (ValueError, TypeError):
        raise KnowledgeError("AI 返回内容无法解析为文章，请稍后再试")
    if not title or not body:
        raise KnowledgeError("AI 返回的文章标题或正文为空，请稍后再试")
    if len(body) < 100:
        raise KnowledgeError("AI 返回的正文太短（不足 100 字），请稍后再试或调整指令")

    now = database.now()
    with database.session_scope() as s:
        draft = database.DistributionDraft(
            brand_id=brand_id, title=title, body_md=body,
            summary=body[:120], tags="、".join(keywords[:5]) if keywords else "",
            status="draft", created_at=now, updated_at=now)
        s.add(draft)
        s.flush()
        return draft.id
