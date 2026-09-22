# GEO 优化系统架构一页纸

> 以仓库当前代码为准（2026-09 校对）。评审背景见 `reports/geo后端架构评审报告.md`。

## 形态

单机本地 Web 应用：Flask 应用工厂（`geo/web/__init__.py:create_app`）+ SQLite
（SQLAlchemy 2.0）+ APScheduler 后台线程 + requests 直连各家 AI 官方 API。
只绑 `127.0.0.1`，端口 5080–5090 自动顺延（`geo/server.py:find_free_port`）。
零外部基础设施：无消息队列/Redis/Docker，异步靠 daemon 线程，互斥靠数据库行状态。
面向零代码用户：报错全是大白话中文；配置首次启动自动生成（`geo/config.py:ensure_config_file`，
YAML 零硬编码，`_write_yaml` 临时文件+`os.replace` 原子写回）。

## 分层（依赖自上而下单向）

```
入口    app.py（前台 python app.py / 后台 --no-browser，写 data/service.json）
       tray_app.py → geo/tray.py（子进程拉起/暂停/重启、HKCU Run 自启、
       CreateFileW 排他单实例锁、GEO 退出清理）＋ geo/service_log.py
       （后台模式运行日志轮转：RotatingFileHandler 5MB×3，行缓冲桥保
       "写一条见一条"，句柄占用时延后轮转）
Web 层  geo/web/ create_app + 8 个 Blueprint 全挂 /api：
       api_config / api_monitor / api_optimize / api_report（api_report_competitor
       路由挂同一 bp，import 即挂载）/ api_alert / api_distribution / api_agent /
       api_knowledge。只做参数校验、品牌归属校验、转发；横切：CORS 本机白名单、
       写操作 Origin 守卫、ApiError 统一封装、404/405/500 兜底、静态 no-cache、
       启动钩子（建库/迁移/僵尸回收/调度器，GEO_NO_SCHEDULER=1 可关调度器）。
编排层  geo/core/ monitor_task（任务生命周期/any_task_running 全局互斥/统计口径）
       monitor_runner（单任务循环：逐引擎×模型×问题）scheduler（APScheduler+
       启动补跑）model_selection（模型清洗归一+中性 build_messages）
       question_expander / fetcher / knowledge / distribution / text_utils
引擎层  geo/engines/ base.EngineAdapter（统一接口/重试/费用记账/错误翻译）+
       注册表 _REGISTRY、AUTO_CODES=["deepseek","doubao","qwen","yuanbao","opencode"]；
       适配器：deepseek / doubao / qwen / yuanbao / opencode / manual /
       deepseek_responses / doubao_responses（联网档独立适配器）/ tencent_wsa（TC3 签名）
分析层  geo/analyzers/ mention（提及/顺位/情感，规则法）sources（信源归类）
       scoring（纯函数评分）alerting（预警状态机）brand_extract + competitor_analysis
       content_advice（GEO 评分建议）llm_client（分析/创作模型统一客户端）
数据层  geo/models/ db.py：15 张表（brand_profile keyword question_bank monitor_task
       monitor_round monitor_result optimization_record score_snapshot alert
       competitor_analysis settings api_call_log knowledge_doc distribution_draft
       distribution_channel_task）+ session_scope + WAL/重试参数；
       migration.py：PRAGMA user_version=7 幂等迁移，VACUUM INTO 先备份。
种子    geo/seed/seeder.py（预置问题已按裁决停用，保留空实现）
```

## 分发链路（多平台发布）

```
GEO 后端 /api/agent/*  ←HTTP─  bridge/geo_bridge_host.py  ←Native Messaging→  发布扩展  → 知乎/搜狐/头条
```

- 浏览器经 connectNative 拉起宿主（`bridge/register_host.py` 写注册表 manifest）；
  宿主纯标准库，stdout 专属协议帧，日志只进 stderr 与 `data/bridge.log`。
- 宿主主循环：心跳（`POST /api/agent/heartbeat`，带各平台账号就绪度）→ 领任务
  （`POST /api/agent/claim`）→ create_post/publish_post（扩展在浏览器登录态执行）→
  轮询 job → 回写 `POST /api/agent/tasks/{id}/status`。
- GEO 地址发现：GEO_BASE_URL > data/service.json > 默认 5080；HTTP 探活桥
  127.0.0.1:39123/v1/health 供扩展 UI 探测。自检/状态：`bridge/preflight.py`、
  `bridge/status_check.py`。

## 关键机制

- **串行监测与互斥**：`monitor_task.any_task_running` 全局同时刻只允许一轮；
  任务行状态（pending/running/done/cancelled）+ 模块级取消集合；启动时回收僵尸任务
  （上次中断的 running → failed，避免永久拦截）；`(question_id, engine, model)` 集
  合断点续跑去重。
- **调度器 + 睡眠唤醒看门狗**：APScheduler 定时每日监测；Windows 整机睡眠唤醒后
  misfire 可能静默丢失，独立 `geo-schedule-watchdog` 线程（30s 巡检）兜底补跑，
  每天最多一次，避免与手动监测撞车。
- **中性提问口径**：`model_selection.build_messages` 用中性系统提示词（不诱导品牌），
  评分只反映 AI 真实知识/检索；情感口径"未提及品牌的回答一律计中性"，夸竞品/夸行业
  不算我方正面。
- **迁移**：`PRAGMA user_version` 标记 schema 版本（当前 7），迁移前 `VACUUM INTO`
  备份到 data/，幂等可重入。
- **本地安全边界**：只绑 127.0.0.1；CORS 仅放行本机来源（正则白名单）；写操作
  （POST/PUT/DELETE）校验 Origin 为本机（无 Origin 的脚本/curl 放行）；启动页静态
  资源 no-cache。
- **服务日志治理**：托盘后台模式 stdout/stderr 经行缓冲桥进
  `logging.handlers.RotatingFileHandler`（`data/service.log` 5MB×3），轮转遇句柄
  占用自动延后重试；前台控制台输出行为不变。

## 测试与启动

```bash
pip install -r requirements.txt            # 运行
python app.py                              # 前台启动（浏览器自动打开 5080+）
pythonw tray_app.py                        # 托盘常驻（自启/暂停/重启）
pip install -r requirements-dev.txt        # 开发/测试依赖
python -m pytest tests/ -q                 # 全量测试（GEO_NO_SCHEDULER=1 自动生效）
ruff check geo/ app.py tray_app.py bridge/ tools/ tests/     # lint（ruff.toml 最小规则集）
python -m pytest tests/ -q --cov=geo --cov-report=term-missing   # 覆盖率基线（无硬阈值）
```

测试约定：`tests/conftest.py` 注入 `GEO_NO_SCHEDULER=1`（不跑定时器）、预警用例换
临时库不碰正式 `data/geo.db`、迁移用例标 `user_version` 跳过 VACUUM INTO 备份、
不发真实 AI 调用（无钥匙路径 + monkeypatch 双保险）。`reports/测试脚本与日志/`
是一次性人工验收脚本索引见 `reports/README.md`。
