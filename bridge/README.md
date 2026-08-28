# GEO 分发桥（org.synccaster.bridge）

把 GEO 里审阅好的稿件，经浏览器里的发布扩展发到知乎 / 搜狐号 / 今日头条。

```
GEO 后端(/api/agent/*) ←HTTP─ geo_bridge_host.py ─Native Messaging→ 发布扩展 → 平台
```

## 文件

- `geo_bridge_host.py` — 原生宿主本体（纯标准库，浏览器拉起，无 venv 依赖）
- `register_host.py` — 一键注册/注销（写 HKCU 注册表 + manifest + 启动 .bat）

## 首次接线（需要扩展 ID）

1. 打开 `chrome://extensions`（Edge 是 `edge://extensions`）→ 开启「开发者模式」
2. 找到发布扩展卡片，复制 32 位 ID
3. 在仓库根目录执行：

```
python bridge/register_host.py --ext-id <粘贴ID>
```

4. **完全重启浏览器**（所有窗口退出），扩展启动时会自动连上本桥
5. GEO 分发页顶部出现「分发桥在线」即接线成功

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
