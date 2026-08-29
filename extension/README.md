# GEO 多平台发布助手（浏览器扩展）

自研的 Chrome MV3 扩展（clean-room，零第三方扩展代码），与本仓库 `bridge/geo_bridge_host.py`
宿主通过 Native Messaging 通信，把 GEO 审阅后的稿件发布到知乎/搜狐/今日头条。

## 架构

```
GEO 分发页 → bridge 宿主(com.geo.bridge) → 本扩展(service worker) → 平台编辑器/接口
```

- `background/rpc.js`：与宿主的长连接（Chrome 116+ 原生消息端口可保活 SW），断线退避重连
- `background/actions.js`：RPC 路由，契约与宿主一致（health/list_platforms/list_accounts/
  create_post/update_post/publish_post/get_job_status/cancel_job/render_wechat_html）
- `background/store.js`：chrome.storage.local 存账号/稿件/任务
- `background/publish.js`：任务执行器（逐平台、进度与落地页回写）
- `background/platforms/`：平台注册表 + 各平台适配器（登录检测 cookie 探测；发布走
  「打开编辑器页 → 注入标题/HTML → 触发发布 → 回读落地页」的页面驱动路线，选择器在真机联调中核对）
- `options/`：账号管理页（检测登录、添加/启用/删除账号、默认发布账号）
- `popup/`：各平台就绪状态一览

## 加载（开发者模式）

1. `chrome://extensions` → 开发者模式 → 加载已解压的扩展程序 → 选本目录
2. 注册宿主：`python bridge/register_host.py --ext-id <本扩展的实际ID>`
   （ID 在加载后的扩展卡片上；实际 ID 由 Chrome 按路径生成，可能与预测不同，注册脚本支持多 ID 并存）
3. 在扩展「账号管理」页检测各平台登录并添加账号（登录态即凭证，扩展不保存账号密码）
4. GEO 分发页确认「分发桥在线」后即可一键分发

## 与 weiqi 扩展的关系

行为参考自对 weiqi 扩展的逆向分析（RPC 形状、平台清单、编辑器发布思路），
代码全部重写；原 weiqi 扩展路径保留为可选回退，不阻塞本项目。
