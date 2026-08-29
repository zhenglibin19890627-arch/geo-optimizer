# -*- coding: utf-8 -*-
"""知识库 API：文档 CRUD/上传 + 关键词提取 + 基于知识库生成分发稿件。"""
from flask import Blueprint, request

from geo.core import knowledge
from geo.core.knowledge import KnowledgeError
from geo.models import db as database
from geo.web import ApiError, current_brand_id, get_json, ok

bp = Blueprint("api_knowledge", __name__)


@bp.route("/knowledge/docs/upload", methods=["POST"])
def upload_doc():
    """上传文档/表格到知识库：支持 .md/.txt/.pdf/.docx/.xlsx，单个 ≤ 20MB。"""
    brand_id = current_brand_id()
    file = request.files.get("file")
    if file is None or not file.filename:
        raise ApiError("请选择要上传的文件")
    data = file.read()
    try:
        doc_id = knowledge.save_uploaded_file(brand_id, file.filename, data)
    except KnowledgeError as e:
        raise ApiError(e.message)
    return ok({"doc_id": doc_id}, f"《{file.filename}》已解析入库")


@bp.route("/knowledge/docs", methods=["GET"])
def list_docs():
    brand_id = current_brand_id()
    return ok({"docs": knowledge.list_docs(brand_id)})


@bp.route("/knowledge/docs", methods=["POST"])
def create_doc():
    brand_id = current_brand_id()
    data = get_json()
    try:
        doc_id = knowledge.create_doc(
            brand_id,
            str(data.get("title") or ""),
            str(data.get("content") or ""))
    except KnowledgeError as e:
        raise ApiError(e.message)
    return ok({"doc_id": doc_id}, "文档已加入知识库")


@bp.route("/knowledge/docs/<int:doc_id>", methods=["PUT"])
def update_doc(doc_id: int):
    brand_id = current_brand_id()
    data = get_json()
    try:
        knowledge.update_doc(
            doc_id, brand_id,
            str(data.get("title") or ""),
            str(data.get("content") or ""))
    except KnowledgeError as e:
        raise ApiError(e.message)
    return ok({"doc_id": doc_id}, "文档已更新")


@bp.route("/knowledge/docs/<int:doc_id>", methods=["DELETE"])
def delete_doc(doc_id: int):
    brand_id = current_brand_id()
    try:
        knowledge.delete_doc(doc_id, brand_id)
    except KnowledgeError as e:
        raise ApiError(e.message)
    return ok({"doc_id": doc_id}, "文档已删除")


@bp.route("/knowledge/docs/<int:doc_id>/keywords", methods=["POST"])
def extract_keywords(doc_id: int):
    brand_id = current_brand_id()
    data = get_json()
    try:
        words = knowledge.extract_keywords(doc_id, brand_id, data.get("count") or 10)
    except KnowledgeError as e:
        raise ApiError(e.message)
    return ok({"doc_id": doc_id, "keywords": words}, f"已提取 {len(words)} 个关键词")


@bp.route("/knowledge/rewrite", methods=["POST"])
def rewrite_from_url():
    """第三种创作方式：给文章链接（支持公众号等任意网页），AI 改写成新稿件。"""
    brand_id = current_brand_id()
    data = get_json()
    url = str(data.get("url") or "").strip()
    user_instruction = str(data.get("user_instruction") or "").strip()
    if len(user_instruction) > 500:
        raise ApiError("用户指令太长了（最多 500 字）")
    try:
        draft_id = knowledge.rewrite_from_url(
            brand_id, url, user_instruction=user_instruction,
            generate_title=bool(data.get("generate_title", True)))
    except KnowledgeError as e:
        raise ApiError(e.message)
    with database.session_scope() as s:
        row = s.get(database.DistributionDraft, draft_id)
        return ok(row.to_dict(), "改写完成，请审阅编辑后再发布")


@bp.route("/knowledge/generate", methods=["POST"])
def generate_draft():
    """基于知识库生成文章草稿（draft 状态，人工审阅后走分发）。"""
    brand_id = current_brand_id()
    data = get_json()
    doc_ids = data.get("doc_ids") or []
    if not isinstance(doc_ids, list) or not doc_ids:
        raise ApiError("请先勾选至少一篇知识库文档")
    user_instruction = str(data.get("user_instruction") or "").strip()
    if len(user_instruction) > 500:
        raise ApiError("用户指令太长了（最多 500 字）")
    try:
        draft_id = knowledge.generate_draft(
            brand_id, [int(x) for x in doc_ids],
            keywords=[str(k) for k in (data.get("keywords") or [])][:8],
            user_instruction=user_instruction,
            generate_title=bool(data.get("generate_title", True)))
    except (KnowledgeError, ValueError) as e:
        raise ApiError(getattr(e, "message", None) or "生成失败，请稍后再试")
    with database.session_scope() as s:
        row = s.get(database.DistributionDraft, draft_id)
        return ok(row.to_dict(), "文章已基于知识库生成，请审阅编辑后再发布")
