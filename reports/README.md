# reports/ 索引

本目录存放架构评审与交付过程文档；`测试脚本与日志/` 子目录是一次性人工验收
脚本与其输出产物（共 46 个文件：37 个 `.py` 脚本 + 9 个输出），针对当时指定
的构建手动执行，**不属于 pytest 套件**（目录不在 CI 的 lint/测试范围内）。
正式回归请以 `tests/` 为准。按文件名归类索引如下（用途按命名与脚本头注释
归纳，未逐行精读）。

## 文档

| 文件 | 用途 |
| --- | --- |
| `geo后端架构评审报告.md` | geo/ 全量架构与代码质量评审（风险清单 R1–R10 的出处） |
| `测试执行记录.md` | 各轮交付的测试执行记录 |

## 测试脚本与日志/ —— 边界与页面

| 脚本 | 用途 |
| --- | --- |
| `test_edge.py` / `edge_results.txt` | 边界用例（超长输入等）+ 输出 |
| `test_nokey_paths.py` | 无 API Key 时的降级路径（应大白话报错而非崩溃） |
| `pages_load.py` | 各页面加载冒烟（6 页面可打开） |
| `homepage_detail.py` | 首页细节检查 |

## 测试脚本与日志/ —— 禁词与预警

| 脚本 | 用途 |
| --- | --- |
| `banned_scan2.py` / `reg_banned_scan.py` | 监测回答违禁词扫描（两轮） |
| `test_alerting.py` | 预警规则（阈值/状态机）人工验证 |
| `test_alerts_paste.py` / `alerts_paste_results.txt` | 预警消息粘贴/展示验证 + 输出 |
| `reg7c_alert_msg.py` | 第 7 轮 c 项：预警消息文案回归 |

## 测试脚本与日志/ —— 取消与 UI 交互

| 脚本 | 用途 |
| --- | --- |
| `test_cancel_flow.py` / `ui_cancel_flow.py` | 监测取消流程（API 侧/UI 侧） |
| `test_cancel_running.py` | 取消正在运行任务的即时性 |
| `reg_spot1_cancel.py` | 回归抽查：取消 |
| `ui_interact3.py` / `reg_ui_badge.py` | UI 交互 / 状态角标回归 |

## 测试脚本与日志/ —— reg* 历轮回归（按轮次命名）

| 脚本 | 用途 |
| --- | --- |
| `reg1_real_monitor.py` | 第 1 轮：真实监测全链路 |
| `reg2_guide_fresh.py` / `reg2e_toast_detail.py` | 第 2 轮：新手引导 / toast 细节 |
| `reg3_status_brand.py` | 第 3 轮：状态与品牌口径 |
| `reg3a_fix8_5097.py` / `reg3a_verify_5097.py` / `reg3a_banner_5097.py`（+同名 `.txt`） | 第 3a 轮（独立端口 5097 环境）：缺陷 #8 修复验证/复核/横幅 |
| `reg3b_spot_5098.py` / `reg3b_report_5098.py` / `reg3b_cancel_race_5098.py`（+同名 `.txt`） | 第 3b 轮（独立端口 5098 环境）：抽查/报告/取消竞态 |
| `reg10_accumulate.py` / `reg10_alert_chain.py` | 第 10 轮：累计口径 / 预警链 |
| `reg12_scheduled.py` | 第 12 轮：定时调度触发 |
| `reg13_spot_check.py`（+`reg13_round_detail.json`） | 第 13 轮：抽查与轮次详情样例 |
| `reg_spot_checks.py` / `reg_spot1_cancel.py` | 历轮合并抽查 |

## 测试脚本与日志/ —— 种子数据与报告冒烟

| 脚本 | 用途 |
| --- | --- |
| `seed_report_data.py` / `cleanup_seed.py` | 造报告演示数据 / 清理种子（配对使用） |
| `verify_report.py` | 报告生成结果人工核验 |
| `smoke_report_split.py` | 报告拆分冒烟（独立端口 5091） |
| `test_reproduce.py` | 问题复现脚本 |

## 测试脚本与日志/ —— 托盘

| 脚本 | 用途 |
| --- | --- |
| `tray_smoke.py` | 托盘冒烟：拉起/暂停/重启/接管服务 |
| `tray_lock_lifecycle_smoke.py` | 托盘单实例锁生命周期冒烟 |
