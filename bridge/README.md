# GEO 分发桥（com.geo.bridge）

把 GEO 里审阅好的稿件，经浏览器里的**自研发布扩展**（本仓库 `extension/` 目录）发到知乎 / 搜狐号 / 今日头条。

```
GEO 后端(/api/agent/*) ←HTTP─ geo_bridge_host.py ─Native Messaging→ 发布扩展 → 平台
```

> 旧宿主名 `org.synccaster.bridge`（对接 weiqi 扩展）已退役：需要回退启用时用
> `python bridge/register_host.py --host-name org.synccaster.bridge --ext-id <weiqi扩展ID>` 重注册即可。

## 文件

- `geo_bridge_host.py` — 原生宿主本体（纯标准库，浏览器拉起，无 venv 依赖）
- `register_host.py` — 一键注册/注销（写 HKCU 注册表 + manifest + 启动 .bat；`--host-name` 可覆盖宿主名）
- `preflight.py` — 一键自检（注册表 / 宿主 / GEO / 各平台发布就绪度）
- `status_check.py` — 状态速查（桥在线 + 渠道任务一览）
- `test_dispatch.py` — 联调测试：插入测试稿 → 排队三平台 → 跟踪到终态
- `watch_reload_and_retry.py` — 监视扩展重载后自动重试分发（联调用）

## 首次接线（自研扩展）

1. `chrome://extensions`（Edge 是 `edge://extensions`）→ 开启「开发者模式」→「加载已解压的扩展程序」→ 选本仓库 `extension/` 目录
2. 复制扩展卡片上的 32 位 ID，在仓库根目录执行：

```
python bridge/register_host.py --ext-id <粘贴ID>
```

3. 在扩展「账号管理」页检测各平台登录并添加账号（登录态即凭证，扩展不保存账号密码）
4. GEO 分发页顶部出现「分发桥在线」、平台旁出现「就绪」徽章即接线成功

## 日常机制

- 宿主由浏览器在扩展启动时拉起，扩展关闭即退出，无需手动管理
- 宿主每 30 秒向 GEO 心跳；循环领取待发任务 → 建稿 → 发布 → 轮询 → 回写
- 发布失败的任务在分发页可单独「重试」
- 日志：`data/bridge.log`

## 可选配置（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `GEO_BASE_URL` | 自动发现 | GEO 地址（默认从 `data/service.json` 读端口） |
| `GEO_AGENT_TOKEN` | 空 | 与 settings 里 `agent_bridge_token` 一致则启用鉴权 |
| `GEO_HEARTBEAT_SECONDS` | 30 | 心跳间隔 |
| `GEO_POLL_SECONDS` | 4 | 发布任务轮询间隔 |
| `GEO_JOB_TIMEOUT_SECONDS` | 150 | 单任务发布超时 |

## 卸载

```
python bridge/register_host.py --unregister
```
