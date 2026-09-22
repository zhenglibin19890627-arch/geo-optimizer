"""GEO 优化系统：Flask 应用工厂 + 路由层基建（只做参数校验与转发）。"""

import os
import re
import traceback
from urllib.parse import urlparse

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from geo import config
from geo.models import db as database
from geo.seed import seeder

# 只允许本机来源（127.0.0.1 / localhost / ::1，任意端口）跨源调用 API；
# 浏览器从恶意网页发起的跨站请求会被拒绝，防止静默覆写钥匙等写操作。
_LOCAL_ORIGIN_RE = re.compile(r"^https?://(\[::1\]|127\.0\.0\.1|localhost)(:\d+)?$")
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


class ApiError(Exception):
    """业务错误：message 必须是大白话中文。"""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code
        self.message = message


def ok(data=None, message: str = "成功"):
    return jsonify({"code": 0, "message": message, "data": data})


def fail(message: str, code: int = 1):
    return jsonify({"code": code, "message": message, "data": None})


def get_json():
    """取请求 JSON，非法则抛大白话错误。

    force=True：浏览器 fetch 字符串体默认 text/plain 头，强制按 JSON 解析，
    兼容前端 geoApi 未显式设置 Content-Type 的历史调用。"""
    data = request.get_json(force=True, silent=True)
    if data is None:
        raise ApiError("请求的内容格式不对，请刷新页面后再试一次")
    if not isinstance(data, dict):
        raise ApiError("请求的内容格式不对，请刷新页面后再试一次")
    return data


def current_brand_id(allow_missing: bool = False) -> int:
    """当前品牌 id：GET 取查询参数，写方法取表单/请求体。

    2026-09 评审 R8 修复：写方法（POST/PUT/DELETE）缺 brand_id 时不再静默
    回退品牌 1——多品牌模式下前端漏传会把数据悄悄写进品牌 1，无任何告警。
    现改为大白话报错，强制调用方显式带 brand_id。前端 static/js/api.js 的
    geoApi() 已给所有 JSON 写请求统一注入 brand_id（GET 加查询参数、
    POST/PUT/DELETE 写进请求体），正常页面操作不受影响；读方法（GET）保留
    缺省 1 的旧口径（读路径多有归属校验兜底，不会写坏数据）。

    2026-09 起无例外：知识库文件上传（POST /knowledge/docs/upload）前端
    distribution.js 已给 FormData 补 brand_id（与 geoApi 同源的
    localStorage.geo_brand_id），allow_missing 参数仅为兼容保留，当前无
    调用方使用——写路径缺 brand_id 一律大白话报错。
    """
    raw = None
    try:
        raw = request.args.get("brand_id")
        if raw is None and request.form:
            raw = request.form.get("brand_id")
        if raw is None:
            data = request.get_json(silent=True)
            if isinstance(data, dict) and data.get("brand_id") is not None:
                raw = data["brand_id"]
    except Exception:
        raw = None
    strict = request.method in ("POST", "PUT", "DELETE") and not allow_missing
    if raw is None:
        if strict:
            raise ApiError("这个操作没有说明要写到哪个品牌，请刷新页面后再试一次；"
                           "如果一直报错，请切换一下品牌再操作")
        return 1
    try:
        return int(raw)
    except (TypeError, ValueError):
        if strict:
            raise ApiError("品牌编号格式不对，请刷新页面后再试一次")
        return 1


def register_blueprints(app):
    # api_report_competitor 的路由挂在与 api_report 同一个 bp 上：
    # import 即完成装饰器挂载，须在 register_blueprint 之前 import。
    from geo.web import (api_agent, api_alert, api_config, api_distribution,
                         api_knowledge, api_monitor, api_optimize, api_report,
                         api_report_competitor)  # noqa: F401  ← 刻意的副作用 import
    for bp in (api_config.bp, api_monitor.bp, api_optimize.bp, api_report.bp,
               api_alert.bp, api_distribution.bp, api_agent.bp,
               api_knowledge.bp):
        app.register_blueprint(bp, url_prefix="/api")


def create_app() -> Flask:
    app = Flask(
        __name__,
        static_folder=str(config.PROJECT_ROOT / "static"),
        static_url_path="/static",
    )
    app.json.ensure_ascii = False
    CORS(app, resources={r"/api/*": {"origins": _LOCAL_ORIGIN_RE}})

    # 禁缓存钩子（2026-09 评审 R10 杂项合并：原 no_cache_for_static 与
    # revalidate_static 两个 after_request 功能重叠、后者只覆盖 /static，
    # 合并为一个，语义取并集：页面/JS/CSS/接口响应全部 no-cache）
    @app.after_request
    def no_cache(resp):
        """页面/JS/CSS/接口响应全部禁缓存，改动刷新即生效。

        no-cache 语义 = 可以缓存，但用前必须向服务端回源校验：文件没变时
        命中 304（几乎零开销），变了就自动拿新版。
        2026-08-19 事故：升级监测中心后，浏览器对同名 monitor.js 走了
        启发式缓存、未回源校验，导致新版页面配旧版脚本——用户勾了
        常规+联网，旧脚本只发起常规。全部显式 no-cache 后不再复发。
        """
        path = request.path or ""
        if (path.startswith("/static") or path.startswith("/api")
                or path == "/" or path.endswith(".html")):
            resp.headers["Cache-Control"] = "no-cache, max-age=0"
        return resp

    @app.before_request
    def guard_cross_origin_writes():
        """写操作（POST/PUT/DELETE）只接受本机来源：无 Origin 的脚本/curl 放行，
        浏览器同源放行，恶意跨站页面（Origin 非本机）一律拒绝。"""
        if request.method not in ("POST", "PUT", "DELETE"):
            return None
        origin = request.headers.get("Origin") or ""
        if not origin:
            return None
        host = urlparse(origin).hostname or ""
        if host in _LOCAL_HOSTS:
            return None
        return fail("这个操作只能从本机页面发起，请刷新页面后再试"), 403

    # 配置与数据库
    config.ensure_config_file()
    config.load_config()
    database.init_db()
    from geo.models import migration
    migration.run_migrations()
    seeder.seed_questions()
    # 回收僵尸任务（R2 分工）：中断超过 2 小时的监测任务在这里置 failed；
    # 2 小时内中断的留活口，交由下方 ensure_scheduler_started →
    # _recover_and_catchup 断点续跑（已问到的回答保留，自动去重续跑）
    from geo.core import monitor_task as monitor_task_mod
    try:
        monitor_task_mod.reap_stale_tasks()
    except Exception:
        traceback.print_exc()
    # 同理回收分发桥中断的多平台任务（dispatching 超时无下文 → failed）
    try:
        from geo.core.distribution import reap_stale_channel_tasks
        reap_stale_channel_tasks()
    except Exception:
        traceback.print_exc()

    register_blueprints(app)

    @app.errorhandler(ApiError)
    def handle_api_error(e):
        return fail(e.message, e.code)

    @app.errorhandler(404)
    def handle_404(e):
        if request.path.startswith("/api"):
            return fail("你要找的功能不存在，请回到首页重新点一次"), 404
        return send_from_directory(str(config.PROJECT_ROOT / "static"), "index.html"), 404

    @app.errorhandler(405)
    def handle_405(e):
        return fail("这个功能的用法不对（方法不允许），请刷新页面后再试一次"), 405

    @app.errorhandler(Exception)
    def handle_500(e):
        traceback.print_exc()
        return fail("服务器开小差了，请稍后再试一次；如果一直报错，请把控制台里的红色信息发给开发者"), 500

    @app.route("/")
    def index():
        return send_from_directory(str(config.PROJECT_ROOT / "static"), "index.html")

    # 定时器（测试脚本可用 GEO_NO_SCHEDULER=1 关闭）
    if os.environ.get("GEO_NO_SCHEDULER") != "1":
        from geo.core.scheduler import ensure_scheduler_started
        try:
            ensure_scheduler_started()
        except Exception:
            traceback.print_exc()

    return app
