# osaka-mvp 本地后台重启清单

> 用途：变更落到 `core/*.py`（含 `publisher.py` / `crawler.py` / `store.py`）后，**必须重启本地 8765 才生效** ——
> 这类文件**不热重载**（模板/JS/CSS 才热重载）。本清单供「Bug1/Bug2 修复发版 PRD §8」引用执行。
> 适用版本：v1.9.48 及以上（含 v1.9.49）。

---

## 0. 先判断：到底要不要重启？

| 改动位置 | 重启需求 |
|---|---|
| `web/templates/*.html`、`web/static/*.js`、`web/static/*.css` | ❌ 不需要（热重载），但改了 `core/version.py` 后要抬版本号换前端 `?v=` |
| `core/*.py`、`web/app.py`、`core/version.py`、`serve.py` | ✅ **必须重启 8765** |
| `config.yaml` / `config.local.yaml` | ✅ 建议重启（部分配置启动时读取） |

> Bug1（`publisher.set_state` INSERT 对齐）与 Bug2（`_fetch_detail_by_no` 优雅跳过守卫）
> 都属于 `core/*.py` → **必须重启本地 8765 才会生效**。

## 1. 🚨 铁律（先看这个）

- **8765 端口属于勇哥双击 `start_mvp.bat` 起的进程，`绝不 taskkill`**。
- 正确做法：**手动关闭旧窗口**（点红叉 / 在窗口内 Ctrl+C），让它自己释放端口。
- 若旧进程没退干净就双击新的，新进程会因端口占用起不来（表现为「后台已启动但页面打不开」）。

## 2. 重启前预检（1 分钟）

```bash
cd C:\Users\25374\WorkBuddy\2026-09-11-09-50-22\osaka-mvp
git status --short          # 应为空 → 没有改了一半的代码
grep '^VERSION' core/version.py   # 记下版本号，用于重启后核对
```
- [ ] `git status` 干净（或改动已确认要带上）
- [ ] 已记下预期版本号
- [ ] 数据房产软件正在跑的抓取轮次已结束（看后台日志最后一行没有「运行中」）
- [ ] 已知晓：**先手动关旧窗口**，不动 taskkill

## 3. 执行重启

1. 找到正在运行的「大阪房产软件」控制台窗口 → **点红叉关闭**（或窗口内 `Ctrl+C`）。
2. 确认端口释放（可选核对，应无 LISTENING 记录）：
   ```cmd
   netstat -ano | findstr :8765
   ```
3. 双击 `start_mvp.bat`（工程根目录）。
4. 等待启动日志滚出，看到类似：
   ```
   · 双栈监听：127.0.0.1:8765 + [::1]:8765
   ```
   > 该行是 v1.9.48 `serve.py` 双栈监听的产物；有它说明新代码已加载。

## 4. 重启后即刻验证（3 项，任一不对就停下排查）

- [ ] **端口**：浏览器开 `http://127.0.0.1:8765`（Edge 若打不开，改用此 IPv4 显式地址；不要用 `localhost`）
- [ ] **版本号**：页面底部「版本 x.y.z」= 预期版本
- [ ] **健康检查**：`http://127.0.0.1:8765/api/ping` 返回 `{"ok":true,...}`

## 5. Bug1 / Bug2 专项验收（跑完至少 2 轮抓取再判）

打开 `server.log`（或后台日志窗口），检索以下关键字：

| 期望**不再出现**（修复前必有） | 对应 Bug |
|---|---|
| `OperationalError: 10 values for 9 columns` | Bug1 |
| `推线上跳过` | Bug1 误报 |
| `番号検索 打开详情失败：TimeoutError ... 8000ms exceeded` | Bug2 |

| 期望**出现**（新行为） | 对应 Bug |
|---|---|
| 不再有上传异常；`publish_state` 台账正常写入 | Bug1 |
| `· <番号> 番号検索 0件（成約済/取り下げ等，无詳細可补，跳过）` | Bug2 秒级跳过 |

针对 PRD §7 验收标准，覆盖这 3 个已知番号应秒级返回「0件」不再卡 8 秒：
`300140791556` / `100140789119` / `100140779224`

```bash
# 记账：抓一轮后台日志，统计上面几类关键字出现次数
python tools/_diag_bug12_verify.py   # 若不存在，可用 grep 手动核对
```

- [ ] 连续 2 轮抓取：无 `OperationalError`、无「推线上跳过」
- [ ] 3 个已知番号秒级跳过，无 8000ms 超时
- [ ] 线上推送成功率维持 100%

## 6. 回滚

```bash
cd C:\Users\25374\WorkBuddy\2026-09-11-09-50-22\osaka-mvp
git log --oneline -5          # 确认要回到的那个 commit
# 🚨 禁用 git stash（2026-09-17 血案）
```
如需回退到 Bug 修复前：`git revert` 目标 commit，再按本清单重启。
数据库回滚备用：`.backups/db/` 下按时间戳取。

---

*本清单随 v1.9.49 建立（2026-09-22），供 Bug1/Bug2 PRD §8 引用。*
