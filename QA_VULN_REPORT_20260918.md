# osaka-mvp 安全 / 质量漏洞报告（v1.8.6–v1.8.9 落地代码复核）

- **复核范围**：2026-09-16、2026-09-17 两天及 9/18 凌晨延续落地的 v1.8.6–v1.8.9 全部相关代码
- **角色**：测试工程师 / QA（独立红队）
- **方法**：只读静态审查 + 局部动态逻辑推演，未修改任何代码
- **版本基线**：`core/version.py` → `VERSION = "1.8.9"`，`BUILD_AT = "2026-09-18 01:25"`
- **说明**：行号均来自实际读取；无法读到的标注「未读到」；推断性结论均明确标注「推测」

---

## 一、漏洞清单（按严重度排序）

### VULN-01　【高】`config.save()` 将本机私密配置污染进受版本控制的 `config.yaml`

**文件:行号**
- 根因：`core/config.py:61-63`（`save()` 写入 `CONFIG_PATH`，即入库的 `config.yaml`）
- 触发面：`web/app.py:208-214`（`_refresh_cfg()` 调用 `cfgmod.load()` 合并 `config.local.yaml`）
- 写回端点：`web/app.py:1353`（`api_publish_settings`）、`:1439`（`api_schedule`）、`:1465`（`api_crawl_settings`）、`:1540`（`api_mode`）
- 佐证：`core/config.py:52-58`（`load()` = `_DEFAULTS ← config.yaml ← config.local.yaml`）、`.gitignore:12-13`（`config.local.yaml` 被忽略，`config.yaml` 入库）

**现象 / 根因**
配置的「读取」与「写回」使用了不同的源/目标：
- 读取：`config.load()` 把 `config.local.yaml`（本机真凭据，gitignore）浅覆盖进模板 `config.yaml`，得到一个**含私密值**的合并字典。
- 写回：`config.save()` 却把这个**合并后的完整字典**原样写回 `config.yaml`（受版本控制、会被提交/发布的文件）。

于是只要管理员在 Web 端保存**任意**一项设置（发布设置 / 计划 / 抓取开关 / 模式切换），当前内存里由 `config.local.yaml` 提供的所有私密键（如 `public.access_code`、`publish.token`、以及未来任何放在 local 的凭据）就会被**持久化进 `config.yaml`**，等于把本应只存本机的私密值「晋升」进了会提交/同步的文件。

**证据**
```python
# core/config.py:52-58
def load() -> dict:
    if not CONFIG_PATH.exists():
        save(_DEFAULTS)
    tmpl = _read_yaml(CONFIG_PATH)
    local = _read_yaml(LOCAL_PATH)            # config.local.yaml（私密）
    return _deep_merge(_deep_merge(_DEFAULTS, tmpl), local)  # 合并，含私密值

# core/config.py:61-63
def save(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:   # CONFIG_PATH = config.yaml（入库）
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
```
```python
# web/app.py:1341-1353（api_publish_settings 典型路径）
cfg = _refresh_cfg()          # 合并 local 后的完整 dict
...
cfgmod.save(cfg)              # 把含私密值的 cfg 写回 config.yaml
```

**建议修复方向**
1. `save()` 只持久化「模板层」键：写回前把合并 dict 与 `_DEFAULTS`/`config.local.yaml` 做差集，仅将真正属于模板（非 local 提供）的键写回 `config.yaml`；local 提供的键回写 `config.local.yaml`。
2. 或：Web 写回端点统一只 `setdefault` 目标小节（如 `cfg["publish"]` 白名单键），且写入时剥离任何来自 local 的键。
3. 增加启动期自检：若 `config.yaml` 中出现 `access_code`/`token`/`password` 等私密键，告警并提示迁移到 `config.local.yaml`。

> **关联次级风险**：`.gitignore:15-19` 已记录 `config.yaml.bak_*` 时间戳备份曾一度裸成 untracked（含迁移前真凭据）。`save()` 当前不生成这种备份，但一旦未来加备份逻辑，需确保备份同样 gitignore，否则重演凭据进仓。

---

### VULN-02　【高】`sync_to_publish.py` 脱敏清单未覆盖 `public.access_code`，访问码被推入公开仓库

**文件:行号**
- 脱敏名单：`tools/sync_to_publish.py:50-54`（`SECRET_KEYS` / `SECRET_KEY_PARTS`）
- 过滤逻辑：`tools/sync_to_publish.py:62-84`（`_sanitize`）
- 写回公开配置：`tools/sync_to_publish.py:175`（`cfg = cfgmod.load()` 合并 local）、`:211`（`_sanitize(cfg)`）、`:217-218`（写入 `TARGET/config.yaml`）

**现象 / 根因**
`sync_to_publish` 的脱敏基于键名匹配：丢弃键名命中 `password/passwd/secret/token/cookie/cred` 的条目（`access_code` 不在其中，也不含任一子串）。
而 `access_code` 正是用来保护「对外公开模式」（`OSAKA_PUBLIC=1`）的访问口令。sync 先把合并配置（含来自 `config.local.yaml` 的 `public.access_code`）送入 `_sanitize`，因不匹配任何规则而**被原样保留**，随后 `yaml.safe_dump` 写入**公开的线上 `config.yaml`**。

后果：用于「保护公开站」的口令，反而被同步进公开仓库——任何能读仓库的人都能拿到访问码，直接绕过 `web/app.py:155-178` 的 `_access_guard`。

**证据**
```python
# tools/sync_to_publish.py:50-57
SECRET_KEYS = ("password", "passwd", "secret", "token", "cookie", "cred")
SECRET_KEY_PARTS = ("password", "passwd", "secret", "token", "cookie", "cred")
KEEP_KEYS = {"ingest_token"}
# "access_code" 既不在 SECRET_KEYS，也不含上述任一子串 → 不会被剔除
```
```python
# tools/sync_to_publish.py:175 / 211 / 217-218
cfg = cfgmod.load()                       # 合并 config.local.yaml（含 public.access_code）
...
safe, dropped = _sanitize(cfg or {})      # access_code 不在 dropped
...
(TARGET / "config.yaml").write_text(yaml.safe_dump(safe, ...))  # 明文进公开仓库
```

**建议修复方向**
1. 把 `access_code` 显式加入 `SECRET_KEYS` 或新增 `SECRET_KEY_PARTS` 子串（如 `"code"`、`"access"`）；同时把 `public` 小节整体纳入脱敏（可保留 `public.enabled` 等非敏感标志，剔除 `access_code`）。
2. 公开站自身若需要访问码，应由部署方单独注入环境变量/Secret，而非随代码仓库下发。
3. 在 `_sanitize` 增加「白名单回退」：只放行已知非敏感键，其余一律脱敏，避免未来新增私密键再次漏网。

---

### VULN-03　【中/高】`detail.html` 的 `property_no` 以内联字符串注入，未做 JS 转义（XSS / 属性破坏）

**文件:行号**
- 漏洞点：`web/templates/detail.html:63`
- 正确写法对照：`web/templates/detail.html:162`

**现象 / 根因**
```html
<!-- detail.html:63 —— 未转义 -->
<a class="btn ghost" href="javascript:void(0)" onclick="openBukkenSearch('{{ row.property_no }}')"
   data-i18n="detail.reins_search">REINS 物件番号検索</a>

<!-- detail.html:162 —— 正确做法 -->
var CUR_NO = {{ row.property_no|tojson }};
```
`property_no` 被以**原生字符串**直接拼进内联 JS 字符串与 `onclick` 属性。若数据源（`row.property_no`，来自 REINS 入库）含有单引号 `'` 或 `<` 等字符，即可：
- 破坏 `onclick` 属性 / JS 字符串，造成脚本执行或页面结构错乱；
- 与同文件 `:162` 的 `|tojson` 正确做法形成不一致，说明是遗漏而非有意为之。

`property_no` 来自外部数据源（REINS），虽非直接用户投稿，但作为「外部数据进展示层」仍属防御性 XSS 必防点；当前其它渲染路径（`tf()`/`aiEsc()` 等，见 `:235-237`、`:255-269`）均做了转义，此点属于盲区。发生概率低、影响面有限（只读展示、需数据含特殊字符），故判中/高。

**建议修复方向**
1. 将 `:63` 改为 `onclick="openBukkenSearch({{ row.property_no|tojson }})"`（与 `:162` 一致），靠 `|tojson` 完成 JS 上下文转义；或改为 `data-no` 属性 + 事件委托读取，彻底移出内联 `onclick`。
2. 统一约定：模板中凡进入 JS 上下文的变量，一律 `|tojson`。

---

### VULN-04　【中/低】`_access_guard` 在未设置访问码时对外完全开放（部署安全警示）

**文件:行号**
- `web/app.py:167-168`

**现象 / 根因**
```python
# web/app.py:167-168
code = (CFG.get("public") or {}).get("access_code") or ""
if not code:
    return None          # 无码 → 不拦截，任何人可访问
```
`OSAKA_PUBLIC=1` 公开部署时，若管理员**忘记配置** `public.access_code`，守卫直接放行，站点零防护对外暴露全部房源与 PDF 下载接口。属向后兼容的设计取舍，但缺少「已启用公开模式却无访问码」的强告警。

**建议修复方向**
1. 公开模式 + 无访问码时，启动日志/健康检查中明确告警（如 `log("⚠ PUBLIC 模式已启用但未设置 access_code，站点完全开放")`）。
2. 可选：提供 `public.require_code` 显式开关，默认要求设码，避免「忘了设就等于全开」的静默陷阱。

---

### VULN-05　【中】主轮（全期間主轮）默认关闭导致「已成交房源长期滞留在架」脏数据

**文件:行号**
- 开关读取：`core/crawler.py:511`
- 降级分支与后果声明：`core/crawler.py:527-533`

**现象 / 根因**
```python
# core/crawler.py:511
main_round_on = bool((cfg.get("crawl", {}) or {}).get("main_round_enabled", False))  # 默认 False
```
自 v1.8.4 起全期間主轮默认关闭。关闭后走 `:527-534` 的显式降级分支：只跑当日日期同步（列表壳），**不补详情/PDF**，且 `mark_delisted` 作用域为空、`complete=False` → **只复活、不判下架**。

代码注释已诚实记录代价（`:532`「长期关会攒出已成交仍在架的脏数据」）。这是已知权衡，但在「长期保持主轮关闭」的真实运营场景下，已成交/已下架房源会持续滞留在架，污染查询结果、对账指标与对外展示。

**建议修复方向**
1. 在概览/对账页对 `main_round_skipped` 状态给出长期可见提示，并标注在架数据可能含已下架房源。
2. 提供独立的、低成本的「per-番号 下架复核 pass」（注释 `:508` 已点名的架构正确解），与主轮解耦，避免为下架判定必须跑全量主轮。
3. 权衡默认：若产品定位是「持续对外展示」，建议把主轮默认改为开启，或至少把脏数据风险写进部署文档醒目处。

---

### VULN-06　【中 / 推测】`reconcile()` 缺口归因存在日期口径混用

**文件:行号**
- `core/store.py:1366-1388`（`reconcile` 三段式逐组归因）
- 本地计数：`core/store.py:1372-1374`

**现象 / 根因（推测）**
```python
# core/store.py:1372-1374
g_local = self.conn.execute(
    f"SELECT COUNT(*) FROM properties WHERE property_subtype IN ({ph})"
    f" AND substr(last_seen_at,1,10)=?", subs + [today]).fetchone()[0]
```
`g_online`（`store.py:1368`，来自 `online_coverage()`）是**平台报告数**（平台某日期/種目组的总数）；而 `g_local` 统计的是 `substr(last_seen_at,1,10)=today`——即**本机下载日=今天**的条数。两者日期基准不同：只有当 `online_coverage` 所覆盖的日期与 `today` 完全一致时，gap/attribution 才有意义；若在线统计覆盖的是其它日期（或跨天的 runs），`gap = g_online - g_local` 与 `avoidable/missed/truncated` 归因会失真（把时差/口径差异误判成「漏采」或「截断」）。

此点属**推测**：是否触发取决于 `online_coverage()` 与当前 `today` 是否对齐，需结合运行时数据确认；但从代码看，本地侧硬编码了 `today` 而在线侧未强制同天，存在口径不对齐风险。

**建议修复方向**
1. `online_coverage()` 返回的每组应携带其统计基准日期；`reconcile` 用同一基准日做本地计数（而非硬编码 `today`），或显式断言两者同天并在不一致时标注「口径不一致、归因仅供参考」。
2. 前端对账卡在 `cause` 为 `missed/truncated` 时，可附「基准日=…」便于人工核对。

---

### VULN-07　【低】`search.html` `buildParams` 存在永不执行的死分支

**文件:行号**
- `web/templates/search.html:821-828`

**现象 / 根因**
```javascript
if(dfrom || dto){
    if(dfrom) p.set('date_from', dfrom);
    if(dto)   p.set('date_to', dto);
} else if(ALL_DATES){
    p.set('date', 'all');
} else {
    if(dfrom) p.set('date', dfrom);   // ← 进入此 else 时必满足 !(dfrom||dto)，故 dfrom 恒为假
}
```
进入最末 `else` 的前提是「既无 `dfrom` 也无 `dto` 且非 `ALL_DATES`」，此时 `dfrom` 必为假，`if(dfrom)` 永不成立 → 该分支为空操作。属历史演变遗留的死代码，无功能影响，但提示参数构造逻辑可简化。

**建议修复方向**：删除末段 `else { if(dfrom) ... }`，或明确统一为「单日模式走 `date_from/date_to` 开放区间」以与后端 `search()`（`store.py:859-880`）一致。

---

### VULN-08　【低】`compare.html` 存在不可达的死分支

**文件:行号**
- `web/templates/compare.html:458-465`

**现象 / 根因**
```javascript
// :458-463（已在前面 return）
if(!items.length){ rows=[]; history.replaceState('/compare'); 显示引导页; return; }
...
// :465（永远不可达）
if(!items.length && rows.length){ rows=[]; load(); }
```
`:465` 的 `if(!items.length && rows.length)` 永远不可能在 `:458` 的 `if(!items.length){...return;}` 之后执行，属 v1.8.7 修复遗留的死分支。无功能影响，仅代码整洁度问题。

**建议修复方向**：删除 `:465` 不可达分支。

---

## 二、按严重度排序的优先修复清单

| 优先级 | 编号 | 严重度 | 问题 | 一句话修复 |
|---|---|---|---|---|
| P0 | VULN-01 | 高 | `config.save()` 把 `config.local.yaml` 私密值写回入库的 `config.yaml` | `save()` 差集剥离 local 键 / 写回只动白名单小节 |
| P0 | VULN-02 | 高 | `sync_to_publish` 未脱敏 `access_code`，访问码进公开仓库 | 把 `access_code`/`public` 纳入脱敏，或改用部署方 Secret |
| P1 | VULN-03 | 中/高 | `detail.html:63` `property_no` 内联未转义 | 改用 `{{ row.property_no\|tojson }}` 或事件委托 |
| P1 | VULN-04 | 中/低 | 公开模式无访问码即全开放 | 启动强告警 + 可选 `require_code` |
| P1 | VULN-05 | 中 | 主轮默认关闭导致已下架房源滞留 | 概览提示 + 独立下架复核 pass；或默认开启主轮 |
| P2 | VULN-06 | 中/推测 | `reconcile` 本地计数硬编码 `today` 与在线口径可能不对齐 | 用在线组基准日统一计数 / 标注口径不一致 |
| P3 | VULN-07 | 低 | `search.html` 死分支 | 删除末段 `else` |
| P3 | VULN-08 | 低 | `compare.html` 不可达分支 | 删除 `:465` |

---

## 三、已确认无明显问题的模块（复核通过）

1. **对比栏跨标签竞态修复（v1.8.9）**
   - `web/static/app.js`：`cmpWrite`（`:164` 值没变不写不通知）防回环、`cmpNotify`（`:172` `_cmpNotifying` 防重入）、`storage` 事件丢弃重复（`:188`）、`setAll`（`:234` 原子写）。
   - `web/templates/compare.html`：`_cmpLoading` 闸门（`:264-269`）、`_loadBody` 的 `while` 重比对 `noOf()`（`:280-316`，关键 `:296` `if(JSON.stringify(noOf())!==JSON.stringify(nos)) continue;`）。竞态收敛，已修复确认。

2. **服务端排序 UDF 注册（多线程安全）**
   - `core/store.py:357-365`（线程本地连接工厂在每条连接上注册 UDF）、`:367-373`（`_register_udfs`）、`:51-66`（`_built_sort_key` 和暦→公元月序）、`:70-83`（`_SORT_COLS`）。旧「仅首线程注册」问题已修复。

3. **`store.search()` 日期 any 口径参数顺序与排序兜底**
   - 日期 any 参数顺序已修正为 `[_df,_dt,_df,_dt]`（`store.py:841-844`），与占位符 `reg起,reg止,chg起,chg止` 对齐，旧漏数据 bug 已修。
   - `order` 仅作兜底（无显式 `sort` 时才生效，`store.py:900-901`），旧「排序没反应」已修。

4. **`/download` 路径穿越防护**
   - `web/app.py:703-707`：`dir` 白名单（exports/daily/attachments）+ `send_from_directory`（内部 `safe_join` 防 `../` 穿越）。确认无穿越。

5. **配置三层合并与缺失兼容**
   - `core/config.py:31-38`（`_deep_merge`）、`:41-49`（`_read_yaml` 缺失/坏文件返回 `{}` 向后兼容）、`:52-58`（加载顺序）。逻辑正确。

6. **`fieldmap.js` 未知键收口**
   - 未知字段键返回空串 + `console.warn`，不再裸上屏；`AGENCY_RE` 中介类键识别合理。未发现盲区。

7. **i18n 动态键盲区（v1.8.6 修复确认）**
   - `search.trade_type.*`（i18n.js:975-1005）四种字典已补齐。
   - 其余动态拼接键族 `ch.*`/`recon.cause.*`/`sort.*`/`search.date_caliber.*`/`row.tag_*`/`collect.switch_*`（i18n.js 各字典块）四种语言均齐备，`t()` 查不到回退裸键 + `console.warn`。确认无明显盲区；唯一残留风险为「后端若新增枚举值须同步补 4 字典」，属流程约束而非实现缺陷。

8. **静态资源缓存破坏**
   - `web/templates/base.html:7,11,13,16` 对 `style.css/app.js/i18n.js/fieldmap.js` 全部使用 `?v={{ version }}` 缓存破坏；未发现裸 `<script>`/`<link>` 引用。缓存错位风险已收敛。

9. **Web 配置写回端点白名单**
   - `api_publish_settings`/`api_schedule`/`api_crawl_settings` 仅改白名单小节键，不触碰凭据字段（注：此「不触碰」仅针对直接赋值，但仍受 VULN-01 的「合并写回」影响——故 VULN-01 才是真问题）。

---

## 四、结论

- **最高风险**：VULN-01 与 VULN-02 构成「本机私密值 → 入库/公开文件」的双重泄漏链，且 VULN-01 为系统性（任何 Web 配置保存都会触发），应优先修复。
- **已落地修复确认有效**：对比栏竞态（v1.8.9）、排序 UDF 多线程注册、search 日期 any 参数顺序、排序兜底、download 穿越防护、i18n 动态键盲区——均在本次复核中读到了修复代码，确认无明显问题。
- **数据一致性**：主轮默认关闭（VULN-05）与 reconcile 口径对齐（VULN-06）是真实存在、需产品/架构决策的残留项，建议纳入下一迭代。
- **代码整洁**：VULN-07、VULN-08 两处死分支无害，建议顺手清理。

> 本报告所有结论均基于静态读取，未执行运行态验证；凡标注「推测」者（VULN-06）需结合运行时数据进一步确认。未对任何源文件做修改。
