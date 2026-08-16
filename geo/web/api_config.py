"""配置类接口：品牌/关键词/问题库/设置（接口规范第四章 #2~#10 及设置类）。"""

import re
from datetime import datetime

from flask import Blueprint, request

from geo import config
from geo.analyzers import llm_client
from geo.core import question_expander
from geo.engines import AUTO_CODES, adapter_meta, get_adapter
from geo.models import db as database
from geo.web import ApiError, current_brand_id, fail, get_json, ok

bp = Blueprint("api_config", __name__)


# ---------------- 品牌（多品牌 CRUD：N1~N5） ----------------
def _split_list(value):
    """字符串或数组统一拆成去重数组（顿号/逗号/分号/换行分隔）。"""
    if isinstance(value, str):
        return list(dict.fromkeys(
            x.strip() for x in re.split(r"[,，;；、\n]", value) if x.strip()))
    return value or []


def _validate_brand_fields(data: dict) -> dict:
    """品牌档案字段校验（与旧 PUT /api/brand 口径一致）。"""
    brand_name = str(data.get("brand_name") or "").strip()
    if not brand_name:
        raise ApiError("请先填写品牌名，这是监测的基础")
    if len(brand_name) > 50:
        raise ApiError("品牌名太长了，请控制在 50 个字以内")
    product_name = str(data.get("product_name") or "").strip()
    if len(product_name) > 50:
        raise ApiError("产品名太长了，请控制在 50 个字以内")
    return {
        "brand_name": brand_name,
        "product_name": product_name,
        "brand_aliases": _split_list(data.get("brand_aliases")),
        "brand_description": str(data.get("brand_description") or "").strip(),
        # 竞品设置已取消（2026-08-15）：竞品由 AI 回答自动提取，不再接收配置
        "competitors": [],
        "auto_monitor": True if data.get("auto_monitor") is None else bool(data.get("auto_monitor")),
    }


def _brand_name_taken(s, brand_name: str, exclude_id=None) -> bool:
    for row in s.query(database.BrandProfile).all():
        if (row.brand_name or "").strip() == brand_name and row.id != exclude_id:
            return True
    return False


@bp.route("/brands", methods=["GET"])
def list_brands():
    return ok(database.list_brands(), "获取成功")


@bp.route("/brands", methods=["POST"])
def add_brand():
    data = get_json()
    fields = _validate_brand_fields(data)
    with database.session_scope() as s:
        if _brand_name_taken(s, fields["brand_name"]):
            raise ApiError(f"已经有一个叫「{fields['brand_name']}」的品牌了，换个名字吧")
        if s.query(database.BrandProfile).count() >= 5:
            raise ApiError("最多只能建 5 个品牌，已经到上限了。想加新品牌，可以先删掉一个不用的")
        row = database.BrandProfile(
            brand_name=fields["brand_name"],
            product_name=fields["product_name"],
            brand_aliases=database.jdumps(fields["brand_aliases"]),
            brand_description=fields["brand_description"],
            competitors=database.jdumps(fields["competitors"]),
            auto_monitor=fields["auto_monitor"],
            updated_at=datetime.now(),
        )
        s.add(row)
        s.flush()
        return ok(row.to_dict(), "品牌已创建")


@bp.route("/brands/<int:brand_id>", methods=["GET"])
def get_brand_detail(brand_id):
    with database.session_scope() as s:
        row = s.get(database.BrandProfile, brand_id)
        if not row:
            raise ApiError("该品牌不存在，可能已被删除")
        return ok(row.to_dict(), "获取成功")


@bp.route("/brands/<int:brand_id>", methods=["PUT"])
def update_brand(brand_id):
    data = get_json()
    fields = _validate_brand_fields(data)
    with database.session_scope() as s:
        row = s.get(database.BrandProfile, brand_id)
        if not row:
            raise ApiError("该品牌不存在，可能已被删除")
        if _brand_name_taken(s, fields["brand_name"], exclude_id=brand_id):
            raise ApiError(f"已经有一个叫「{fields['brand_name']}」的品牌了，换个名字吧")
        row.brand_name = fields["brand_name"]
        row.product_name = fields["product_name"]
        row.brand_aliases = database.jdumps(fields["brand_aliases"])
        row.brand_description = fields["brand_description"]
        row.competitors = database.jdumps(fields["competitors"])
        row.auto_monitor = fields["auto_monitor"]
        row.updated_at = datetime.now()
        s.flush()
        return ok(row.to_dict(), "品牌信息已保存")


@bp.route("/brands/<int:brand_id>", methods=["DELETE"])
def delete_brand(brand_id):
    data = get_json()
    if data.get("confirm") is not True:
        raise ApiError("删品牌前需要二次确认，勾选确认后再删")
    with database.session_scope() as s:
        if not s.get(database.BrandProfile, brand_id):
            raise ApiError("该品牌不存在，可能已被删除")
        running = (s.query(database.MonitorTask)
                   .filter(database.MonitorTask.brand_id == brand_id,
                           database.MonitorTask.status.in_(["pending", "running"]))
                   .count())
        if running:
            raise ApiError("该品牌有监测正在进行，请等待结束后再删除")
    database.delete_brand_cascade(brand_id)
    return ok({"deleted": True}, "品牌已删除，该品牌的数据已一并清理")


# ---------------- 关键词 ----------------
@bp.route("/keywords", methods=["GET"])
def list_keywords():
    brand_id = current_brand_id()
    enabled = request.args.get("enabled")
    with database.session_scope() as s:
        q = (s.query(database.Keyword)
             .filter(database.Keyword.brand_id == brand_id)
             .order_by(database.Keyword.id.desc()))
        if enabled is not None:
            q = q.filter(database.Keyword.enabled == (enabled == "true"))
        return ok([k.to_dict() for k in q.all()], "获取成功")


@bp.route("/keywords", methods=["POST"])
def add_keywords():
    data = get_json()
    brand_id = current_brand_id()
    texts = _split_list(data.get("texts"))
    for t in texts:
        if len(t) > 200:
            raise ApiError("关键词太长了，请控制在 200 个字以内")
    category = str(data.get("category") or "").strip()
    added = 0
    with database.session_scope() as s:
        existing = {k.text for k in s.query(database.Keyword)
                    .filter(database.Keyword.brand_id == brand_id).all()}
        for t in texts:
            t = str(t).strip()
            if not t or t in existing:
                continue
            s.add(database.Keyword(text=t, category=category or None,
                                   enabled=True, brand_id=brand_id))
            existing.add(t)
            added += 1
    return ok({"added": added}, f"成功添加 {added} 个关键词")


@bp.route("/keywords/<int:kid>", methods=["DELETE"])
def delete_keyword(kid):
    brand_id = current_brand_id()
    with database.session_scope() as s:
        row = s.get(database.Keyword, kid)
        if not row:
            raise ApiError("这个关键词不存在，可能已被删除")
        if row.brand_id != brand_id:
            raise ApiError("这个关键词不存在，可能已被删除")
        s.delete(row)
    return ok(None, "已删除")


# ---------------- 问题库 ----------------
@bp.route("/questions", methods=["GET"])
def list_questions():
    brand_id = current_brand_id()
    category = request.args.get("category")
    source = request.args.get("source")
    enabled = request.args.get("enabled")
    with database.session_scope() as s:
        q = (s.query(database.QuestionBank)
             .filter(database.QuestionBank.brand_id == brand_id)
             .order_by(database.QuestionBank.id.desc()))
        if category:
            q = q.filter(database.QuestionBank.category == category)
        if source:
            q = q.filter(database.QuestionBank.source == source)
        if enabled is not None:
            q = q.filter(database.QuestionBank.enabled == (enabled == "true"))
        return ok([x.to_dict() for x in q.all()], "获取成功")


@bp.route("/questions", methods=["POST"])
def add_question():
    data = get_json()
    brand_id = current_brand_id()
    text = str(data.get("text") or "").strip()
    if not text:
        raise ApiError("问题内容不能为空")
    category = str(data.get("category") or "").strip()
    source = str(data.get("source") or "manual").strip()
    if source not in ("preset", "expanded", "manual"):
        raise ApiError("这个来源不认，请从 预置/扩展/手动 里选")
    with database.session_scope() as s:
        row = database.QuestionBank(text=text, category=category or None,
                                    source=source, enabled=True, brand_id=brand_id)
        s.add(row)
        s.flush()
        return ok(row.to_dict(), "已添加到问题库")


@bp.route("/questions/<int:qid>", methods=["DELETE"])
def delete_question(qid):
    brand_id = current_brand_id()
    with database.session_scope() as s:
        row = s.get(database.QuestionBank, qid)
        if not row:
            raise ApiError("这个问题不存在，可能已被删除")
        if row.brand_id != brand_id:
            raise ApiError("这个问题不属于当前品牌")
        s.delete(row)
    return ok(None, "已删除")


@bp.route("/questions/<int:qid>", methods=["PUT"])
def update_question(qid):
    """问题开关/内容修改（参与监测开关）。category 即分组，改字段=移入/移出分组。"""
    data = get_json()
    brand_id = current_brand_id()
    with database.session_scope() as s:
        row = s.get(database.QuestionBank, qid)
        if not row:
            raise ApiError("这个问题不存在，可能已被删除")
        if row.brand_id != brand_id:
            raise ApiError("这个问题不属于当前品牌")
        if "enabled" in data:
            row.enabled = bool(data["enabled"])
        if data.get("text"):
            row.text = str(data["text"]).strip()
        if data.get("category") is not None:
            row.category = str(data["category"]).strip() or None
        s.flush()
        return ok(row.to_dict(), "已更新")


# ---------------- 问题库分组（N6~N9：组即 question_bank.category，空=NULL=未分组） ----------------
def _groups_key(brand_id: int) -> str:
    return f"question_group_names_{brand_id}"


def _registered_names(s, brand_id: int) -> list:
    names = database.get_setting(_groups_key(brand_id), [], session=s) or []
    return [str(n) for n in names if str(n).strip()]


def _group_exists(s, brand_id: int, name: str) -> bool:
    return name in _registered_names(s, brand_id) or (
        s.query(database.QuestionBank)
        .filter(database.QuestionBank.brand_id == brand_id,
                database.QuestionBank.category == name).first()) is not None


def _group_names(s, brand_id: int) -> list:
    names = list(_registered_names(s, brand_id))
    rows = (s.query(database.QuestionBank.category)
            .filter(database.QuestionBank.brand_id == brand_id,
                    database.QuestionBank.category.isnot(None)).distinct().all())
    for (cat,) in rows:
        cat = (cat or "").strip()
        if cat and cat not in names:
            names.append(cat)
    return names


def _register_group(s, brand_id: int, name: str, remove: str = None):
    names = _registered_names(s, brand_id)
    if remove and remove in names:
        names.remove(remove)
    if name and name not in names:
        names.append(name)
    database.set_setting(_groups_key(brand_id), names, session=s)


@bp.route("/question-groups", methods=["GET"])
def list_question_groups():
    brand_id = current_brand_id()
    groups = {}
    with database.session_scope() as s:
        rows = (s.query(database.QuestionBank)
                .filter(database.QuestionBank.brand_id == brand_id).all())
    for x in rows:
        name = x.category or ""
        g = groups.setdefault(name, {"name": name, "question_count": 0, "enabled_count": 0})
        g["question_count"] += 1
        if x.enabled:
            g["enabled_count"] += 1
    with database.session_scope() as s:
        for name in _group_names(s, brand_id):
            groups.setdefault(name, {"name": name, "question_count": 0, "enabled_count": 0})
    items = sorted(groups.values(), key=lambda g: (g["name"] != "", g["name"]))
    return ok(items, "获取成功")


@bp.route("/question-groups", methods=["POST"])
def add_question_group():
    data = get_json()
    brand_id = current_brand_id()
    name = str(data.get("name") or "").strip()
    if not name:
        raise ApiError("组名不能为空")
    if len(name) > 50:
        raise ApiError("组名太长了，请控制在 50 个字以内")
    with database.session_scope() as s:
        if _group_exists(s, brand_id, name):
            raise ApiError(f"已经有「{name}」这个组了，换个名字吧")
        _register_group(s, brand_id, name)
    return ok({"name": name}, f"已创建分组「{name}」")


@bp.route("/question-groups", methods=["PUT"])
def rename_question_group():
    data = get_json()
    brand_id = current_brand_id()
    name = str(data.get("name") or "").strip()
    new_name = str(data.get("new_name") or "").strip()
    if not name:
        raise ApiError("「未分组」不能改名")
    if not new_name:
        raise ApiError("组名不能为空")
    if len(new_name) > 50:
        raise ApiError("组名太长了，请控制在 50 个字以内")
    if new_name == name:
        raise ApiError("新组名和原来一样，没有变化")
    with database.session_scope() as s:
        if not _group_exists(s, brand_id, name):
            raise ApiError("这个组不存在，请刷新后重试")
        if _group_exists(s, brand_id, new_name):
            raise ApiError(f"已经有「{new_name}」这个组了，换个名字吧")
        affected = (s.query(database.QuestionBank)
                    .filter(database.QuestionBank.brand_id == brand_id,
                            database.QuestionBank.category == name)
                    .update({database.QuestionBank.category: new_name},
                            synchronize_session=False))
        _register_group(s, brand_id, new_name, remove=name)
        return ok({"affected": affected, "name": new_name}, f"分组已改名为「{new_name}」")


@bp.route("/question-groups", methods=["DELETE"])
def delete_question_group():
    data = get_json()
    if data.get("confirm") is not True:
        raise ApiError("删分组前需要二次确认")
    brand_id = current_brand_id()
    name = str(data.get("name") or "").strip()
    if not name:
        raise ApiError("「未分组」不能删除")
    with database.session_scope() as s:
        if not _group_exists(s, brand_id, name):
            raise ApiError("这个组不存在，请刷新后重试")
        affected = (s.query(database.QuestionBank)
                    .filter(database.QuestionBank.brand_id == brand_id,
                            database.QuestionBank.category == name)
                    .update({database.QuestionBank.category: None},
                            synchronize_session=False))
        _register_group(s, brand_id, None, remove=name)
        return ok({"affected": affected},
                  f"已删除分组「{name}」，{affected} 条问题已回到「未分组」")


# ---------------- 问题库批量（N10） ----------------
def _parse_ids(raw) -> list:
    if isinstance(raw, str):
        raw = [x for x in re.split(r"[,，;；、\n]", raw) if x.strip()]
    ids = []
    for x in (raw or []):
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            pass
    return ids


@bp.route("/questions/batch", methods=["POST"])
def batch_questions():
    data = get_json()
    brand_id = current_brand_id()
    action = str(data.get("action") or "").strip()
    if action not in ("delete", "move", "enable", "disable"):
        raise ApiError("这个操作不认，请从 删除/移动/参加监测/不参加监测 里选")
    ids = _parse_ids(data.get("ids"))
    group = str(data.get("group") or "").strip()
    if action in ("delete", "move") and not ids:
        raise ApiError("请勾选要操作的问题")
    with database.session_scope() as s:
        target = []
        if ids:
            rows = (s.query(database.QuestionBank)
                    .filter(database.QuestionBank.id.in_(ids)).all())
            foreign = [r for r in rows if r.brand_id != brand_id]
            if foreign:
                raise ApiError(f"有 {len(foreign)} 条问题不属于当前品牌，本次操作未执行，请刷新后再试")
            if not rows:
                raise ApiError("这些问题不存在，可能已被删除")
            target = rows
        elif action in ("enable", "disable"):
            if group and not _group_exists(s, brand_id, group):
                raise ApiError("这个组不存在，请刷新后重试")
            q = (s.query(database.QuestionBank)
                 .filter(database.QuestionBank.brand_id == brand_id))
            if group:
                q = q.filter(database.QuestionBank.category == group)
            else:
                q = q.filter(database.QuestionBank.category.is_(None))
            target = q.all()
        if action == "delete":
            if data.get("confirm") is not True:
                raise ApiError("删除操作需要二次确认")
            ids_ok = [r.id for r in target]
            (s.query(database.QuestionBank)
             .filter(database.QuestionBank.id.in_(ids_ok))
             .delete(synchronize_session=False))
            affected = len(target)
            return ok({"affected": affected}, f"已删除 {affected} 条问题")
        if action == "move":
            if group and not _group_exists(s, brand_id, group):
                raise ApiError(f"目标组「{group}」不存在，先建组再移动吧")
            ids_ok = [r.id for r in target]
            (s.query(database.QuestionBank)
             .filter(database.QuestionBank.id.in_(ids_ok))
             .update({database.QuestionBank.category: group or None},
                     synchronize_session=False))
            affected = len(target)
            label = f"「{group}」" if group else "「未分组」"
            return ok({"affected": affected}, f"已把 {affected} 条问题移到{label}")
        enabled = action == "enable"
        ids_ok = [r.id for r in target]
        (s.query(database.QuestionBank)
         .filter(database.QuestionBank.id.in_(ids_ok))
         .update({database.QuestionBank.enabled: enabled}, synchronize_session=False))
        affected = len(target)
        tip = "参加监测" if enabled else "不参加监测"
        return ok({"affected": affected}, f"已把 {affected} 条问题设为{tip}")


@bp.route("/questions/expand", methods=["POST"])
def expand_questions():
    data = get_json()
    keywords = _split_list(data.get("keywords"))
    count = int(data.get("count") or 10)
    direction = str(data.get("direction") or "").strip()
    # 携带当前品牌档案：系统提示词围绕品牌/产品设计问题，避免生成引不来本品牌的问题
    brand = database.get_brand(current_brand_id())
    try:
        questions = question_expander.expand_questions(keywords, count, direction or None,
                                                       brand=brand)
    except llm_client.AnalysisError as e:
        raise ApiError(e.message)
    return ok({"questions": questions}, "扩展成功，确认后点“加入问题库”即可保存")


# ---------------- 设置 ----------------
def _mask_key(key: str) -> str:
    """钥匙脱敏：前 4 位 + **** + 后 4 位（短钥匙只留后 2 位），页面永不回显明文。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "****" + key[-2:]
    return key[:4] + "****" + key[-4:]


def _ensure_current_model(options: list, current: str) -> list:
    """模型下拉里保证包含当前模型：用户在 config.yaml 手填、不在模板选项里的也能显示。"""
    if not current:
        return options or []
    opts = list(options or [])
    names = {str(o.get("name", "")) for o in opts if isinstance(o, dict)}
    if current not in names:
        opts.append({"name": current, "desc": "当前使用档位"})
    return opts


@bp.route("/settings/keys", methods=["GET"])
def settings_keys():
    items = []
    for code in AUTO_CODES:
        item = adapter_meta(code)
        item["api_key_masked"] = _mask_key(
            (config.get_engine_config(code).get("api_key") or "").strip())
        item["model_options"] = _ensure_current_model(item.get("model_options") or [],
                                                      item.get("model") or "")
        items.append(item)

    # 分析用模型：厂商可切换（复用各家引擎的钥匙/地址/档位），型号随厂商联动
    vendor = llm_client.get_analysis_vendor()
    vendors = []
    vendor_model_options = {}
    for code in AUTO_CODES:
        meta = adapter_meta(code)
        vendors.append({"engine": code, "display_name": meta["display_name"]})
        vendor_model_options[code] = _ensure_current_model(
            meta.get("model_options") or [], meta.get("model") or "")
    items.append({
        "engine": "analysis",
        "display_name": "分析用模型",
        "note": "系统自己的思考用的模型（扩展问法、优化建议等），可切换厂商",
        "configured": llm_client.is_configured(),
        "enabled": True,
        "vendor": vendor,
        "vendors": vendors,
        "vendor_model_options": vendor_model_options,
        "model": llm_client.get_analysis_model(),
        "api_key_masked": _mask_key(
            (config.get_analysis_config().get("api_key") or "").strip()),
        "model_options": _ensure_current_model(
            vendor_model_options.get(vendor) or [],
            llm_client.get_analysis_model()),
    })
    return ok(items, "获取成功")


@bp.route("/settings/keys", methods=["POST"])
def save_key():
    """设置页直接填钥匙：写回本地 config.yaml，保存后立即生效。"""
    data = get_json()
    code = str(data.get("engine_code") or "").strip()
    api_key = str(data.get("api_key") or "").strip()
    if code not in AUTO_CODES and code != "analysis":
        raise ApiError("没找到这家引擎，请刷新页面后再试")
    if not api_key:
        raise ApiError("钥匙（API Key）不能为空")
    if len(api_key) > 200:
        raise ApiError("钥匙（API Key）太长了，请检查是不是复制完整了")
    if code == "analysis":
        config.save_analysis_api_key(api_key)
    else:
        config.save_engine_api_key(code, api_key)
    return ok({"engine_code": code, "api_key_masked": _mask_key(api_key)},
              "钥匙已保存，保存后立即生效")


@bp.route("/settings", methods=["POST"])
def save_settings():
    data = get_json()
    saved = {"engine_model": {}, "engine_enabled": {}, "analysis_model": None,
             "analysis_vendor": None}

    for code, model in (data.get("engine_model") or {}).items():
        if code not in AUTO_CODES:
            continue
        model = str(model or "").strip()
        if not model:
            raise ApiError("模型档位不能为空")
        meta = adapter_meta(code)
        options = [o["name"] for o in meta["model_options"]]
        if options and model not in options:
            raise ApiError(f"{meta['display_name']}没有这个档位，请从下拉列表里选")
        database.set_setting(f"engine_model_{code}", model)
        saved["engine_model"][code] = model

    for code, flag in (data.get("engine_enabled") or {}).items():
        if code not in AUTO_CODES:
            continue
        database.set_setting(f"engine_enabled_{code}", bool(flag))
        saved["engine_enabled"][code] = bool(flag)

    # 分析模型厂商（复用该引擎的钥匙/地址/档位；先切厂商再校验型号）
    if data.get("analysis_vendor"):
        vendor = str(data["analysis_vendor"]).strip()
        if vendor not in AUTO_CODES:
            raise ApiError("没找到这个厂商，请刷新页面后重试")
        database.set_setting("analysis_vendor", vendor)
        saved["analysis_vendor"] = vendor

    if data.get("analysis_model"):
        model = str(data["analysis_model"]).strip()
        vendor = str(data.get("analysis_vendor")
                     or llm_client.get_analysis_vendor()).strip()
        meta = adapter_meta(vendor)
        options = [o.get("name", "") for o in (meta.get("model_options") or [])
                   if isinstance(o, dict) and o.get("name")]
        if options and model not in options:
            raise ApiError(f"{meta['display_name']}没有这个分析档位，请从下拉列表里选")
        database.set_setting("analysis_model", model)
        saved["analysis_model"] = model

    return ok(saved, "设置已保存")


@bp.route("/settings/keys/test", methods=["POST"])
def test_key():
    data = get_json()
    code = str(data.get("engine_code") or "").strip()
    if code == "analysis":
        if not llm_client.is_configured():
            return ok({"ok": False, "message": "钥匙尚未填写，请先到设置页填写"}, "测试完成")
        try:
            llm_client.chat("你好", temperature=0)
        except llm_client.AnalysisError as e:
            return ok({"ok": False, "message": e.message}, "测试完成")
        return ok({"ok": True, "message": "连接成功，钥匙可用"}, "测试完成")

    if code not in AUTO_CODES:
        raise ApiError("未找到该引擎，请刷新页面后重试")
    adapter = get_adapter(code)
    if not adapter.is_configured():
        return ok({"ok": False,
                   "message": f"{adapter.display_name}的 API 钥匙尚未填写，请先到设置页填写"},
                  "测试完成")
    try:
        adapter.chat([{"role": "user", "content": "你好"}], temperature=0)
    except Exception as e:
        from geo.engines import base as engine_base
        return ok({"ok": False, "message": engine_base.friendly_error(e, adapter.display_name)},
                  "测试完成")
    return ok({"ok": True, "message": f"{adapter.display_name}连接成功，钥匙可用"}, "测试完成")


@bp.route("/settings/cost", methods=["GET"])
def settings_cost():
    now_dt = datetime.now()
    first_day = datetime(now_dt.year, now_dt.month, 1)
    by_engine = {}
    total = 0.0
    with database.session_scope() as s:
        rows = (s.query(database.ApiCallLog)
                .filter(database.ApiCallLog.created_at >= first_day).all())
        for r in rows:
            total += r.cost_yuan or 0
            item = by_engine.setdefault(r.engine_code, {"cost": 0.0, "name": r.engine_code})
            item["cost"] += r.cost_yuan or 0
    for item in by_engine.values():
        item["cost"] = round(item["cost"], 2)
        if item["name"] in AUTO_CODES:
            item["name"] = get_adapter(item["name"]).display_name
        elif item["name"] == "analysis":
            item["name"] = "分析用模型"
    return ok({"month_cost_yuan": round(total, 2), "by_engine": by_engine}, "获取成功")
