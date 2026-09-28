# WorkBuddy 反馈/支持渠道 + 成功案例（2026-09-28）

> 背景：2104（appId `wbapp_L8M4tyzslRfAA0IWoy29Iz`）在 09-28 凌晨 selfrestart 事故后，平台侧发布记录被删除，成为"活着的孤儿沙箱"——能访问、不能更新代码、UI 无操作入口。目标：联系官方**恢复 L8M4 的发布记录**，回到"redeploy 复用同一 app、地址不变"的正轨。
> 本文档全网查证官方文档（codebuddy.cn / workbuddy.ai）+ 社区 wiki + 用户实操文得出。

## 一、反馈/支持渠道（按有效性排序）

| 优先级 | 渠道 | 入口/地址 | 适用场景 | 官方说明 |
|---|---|---|---|---|
| **① 首选** | App 内「帮助与反馈」 | 左下角头像 → 帮助与反馈 → 提交反馈（**勾"上传日志"**） | Bug/异常，直达产品团队 | workbuddy.ai 文档："Your feedback goes directly to the product team" |
| **② 强力** | 官方邮箱 | `codebuddy@tencent.com`（**服务协议法定联系邮箱**，最权威）/ `workbuddy_ai@tencent.com`（企业协议页）/ `workbuddy@tencent.com`（联系页） | 账号/数据/应用记录类问题 | `codebuddy@tencent.com`：验证身份后 **15 个工作日**内回复（服务协议 §17）；另两个 1–2 工作日，紧急加 `[Urgent]` |
| ③ | 腾讯频道 / 企业微信 | 设置→联系页扫码 | 售前/交流 | 真人响应，可追问 |
| ④ | 社区论坛 | `community.workbuddy.ai` | 经验交流、找同类案例 | 用户互助，非官方承诺 |
| ⑤ | Discord | `discord.gg/workbuddy` | 开发者实时讨论 | 海外用户为主 |
| ⑥ | 公众号 / 微信群 | 微信搜"WorkBuddy"公众号→菜单"加入社群" | 中文用户交流 | 信息同步 |
| ⑦ | 服务状态页 | `status.workbuddy.ai` | 查平台是否故障 | 仅看状态 |

**来源**：
- codebuddy.cn/docs/workbuddy/Contact：技术支持 `workbuddy@tencent.com`；需求反馈走 App 内"帮助与反馈"；售前企业微信；腾讯频道；公众号。
- workbuddy.ai/docs/.../Feedback：头像→帮助与反馈→提交反馈（附截图+上传日志）；邮箱 `workbuddy_ai@tencent.com`；1–2 工作日响应，紧急加 `[Urgent]`。
- workbuddy.ai/document/enterprise-agreement 4.2：咨询/投诉联系 `workbuddy_ai@tencent.com`。
- IMA 社区 wiki：文档站 `docs.workbuddy.ai`、论坛 `community.workbuddy.ai`、Discord、邮箱 `support@workbuddy.ai`、状态页 `status.workbuddy.ai`、微信群（公众号菜单"加入社群"）。
- **轻量发布文档（2026-09-28 补充查证）→ 服务协议 §17「联系我们」**：法定联系邮箱 **`codebuddy@tencent.com`**，明确写在具有同等法律效力的服务协议里（轻量发布页"声明"引用）。这是**最可追溯**的官方邮箱，提恢复 L8M4 记录工单时优先引用此条（平台无法以"无官方联系渠道"推脱）。SLA：验证用户身份后 15 个工作日内回复。

## 二、成功案例（证明"更新不换地址"是平台常态）

### 案例 A：流浪猫认养网站（今日头条实操文，最强证据）
- 作者用 WorkBuddy 部署全栈网站上线 `cat-adoption.app.workbuddy.host`。
- **原文引用**：*"这个域名你不删除掉全栈网站应用对应的云服务应用，它是不会失效的，并且地址也不会变动，无论你后面怎样更新迭代网站。"*
- **意义**：平台设计上 redeploy 复用同一 app、地址永久不变。我们 2104 卡住不是平台不支持，而是**发布记录在事故后被误删**（异常）。请求恢复该记录即回到正轨——有平台设计依据，属合理诉求。

### 案例 B：一键部署新手（什么值得买）
- 确认管理入口：设置 → 数据管理 → 我发布的应用（查看/停止/删除）。
- **风险补充**：底层 CloudStudio 工作空间"闲置超 30 天"会在维护窗口回收资源释放数据；沙箱型部署链接也有"闲置回收"风险。
- **意义**：2104 虽已重启活了，但**别再长时间离线**，否则有被回收风险 → 尽快走官方恢复记录、接回更新通道。

### 案例 C：小程序发布能力（网易，2026-09-24）
- 确认"设置 → 数据管理 → 应用"可统一查看已发布应用、下线、看云服务、下载小程序码。
- 印证 UI 管理入口的真实路径（与我们定位的"设置→数据管理→发布的应用"一致）。

## 三、给官方的提交话术（双渠道）

### 邮件 `codebuddy@tencent.com`（**首选，主题加 [Urgent]**，并抄送 `workbuddy_ai@tencent.com` 加速）

> 依据：该邮箱为服务协议 §17「联系我们」法定联系渠道，具法律效力，最可追溯。
```
主题：[Urgent] 已发布应用记录被误删，请求恢复 appId wbapp_L8M4tyzslRfAA0IWoy29Iz 的发布记录

正文：
1. 应用：大阪房源查询系统（appId wbapp_L8M4tyzslRfAA0IWoy29Iz）
2. 线上地址：https://2104034688228982784.app.workbuddy.host/（生产主地址，短期绝不可变更）
3. 现象：09-28 凌晨一次 selfrestart 后服务失联，恢复重启后应用仍可访问，但发布系统里该 app 的记录已不存在（AI 发布工具返回 sites_deploy_app_deleted）。现为"孤儿沙箱"：能访问、无法经正规通道更新代码、UI 无重新部署入口。
4. 诉求：请恢复该 app 的发布记录（使其重新出现在"设置→数据管理→发布的应用"中，且可经同目录 redeploy 复用、地址不变）。
5. 依据：平台设计上 redeploy 复用同一 app、地址永久不变（见公开实操案例 cat-adoption.app.workbuddy.host）。本次属记录被误删的异常，非功能不支持。
6. 数据红线：该应用含真实业务数据（客户/收藏/标签），恢复记录后任何 redeploy 必须排除 data/ 覆盖，请确认安全机制。
```

### App 内「帮助与反馈」
- 路径：头像 → 帮助与反馈 → 提交反馈
- 勾选：**上传日志**
- 描述：同上邮件正文要点（appId + 地址 + 现象 + 诉求）

## 四、红线自查（我方不做）
- ❌ 不 deploy（含 data/ 覆盖风险）❌ 不新建 app（换地址，撞死命令）❌ 不切 v100（库已分叉）
- 2104 现状未破坏：`/api/ping`=200、`/search` 可达、线上 fav.js 仍 27517B 旧代码。
