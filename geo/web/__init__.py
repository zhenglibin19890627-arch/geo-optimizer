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
    """取请求 JSON，非法则抛大白话错误。"""
    data = request.get_json(silent=True)
    if data is None:
        raise ApiError("请求的内容格式不对，请刷新页面后再试一次")
    if not isinstance(data, dict):
        raise ApiError("请求的内容格式不对，请刷新页面后再试一次")
    return data


def current_brand_id() -> int:
    """当前品牌 id：GET 取查询参数，其他取请求体；缺省 1（威启，旧调用兼容）。"""
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
    try:
        return int(raw) if raw is not None else 1
    except (TypeError, ValueError):
        return 1


def register_blueprints(app):
    # api_report_competitor 的路由挂在与 api_report 同一个 bp 上：
    # import 即完成装饰器挂载，须在 register_blueprint 之前 import。
    from geo.web import (api_alert, api_config, api_monitor, api_optimize,
                         api_report, api_report_competitor)
    for bp in (api_config.bp, api_monitor.bp, api_optimize.bp, api_report.bp, api_alert.bp):
        app.register_blueprint(bp, url_prefix="/api")


def create_app() -> Flask:
    app = Flask(
        __name__,
        static_folder=str(config.PROJECT_ROOT / "static"),
        static_url_path="/static",
    )
    app.json.ensure_ascii = False
    CORS(app, resources={r"/api/*": {"origins": _LOCAL_ORIGIN_RE}})

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

    @app.after_request
    def revalidate_static(resp):
        """静态资源一律 no-cache（=缓存但用前必须向服务端校验）。

        2026-08-19 事故：升级监测中心后，浏览器对同名 monitor.js 走了
        启发式缓存、未回源校验，导致新版页面配旧版脚本——用户勾了
        常规+联网，旧脚本只发起常规。加 no-cache 后，文件没变时命中
        304（几乎零开销），变了就自动拿新版，刷新页面即生效。
        """
        if request.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    # 配置与数据库
    config.ensure_config_file()
    config.load_config()
    database.init_db()
    from geo.models import migration
    migration.run_migrations()
    seeder.seed_questions()
    # 回收僵尸任务：上次程序退出时中断的监测任务，避免永久拦截新监测
    from geo.core import monitor_task as monitor_task_mod
    try:
        monitor_task_mod.reap_stale_tasks()
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
