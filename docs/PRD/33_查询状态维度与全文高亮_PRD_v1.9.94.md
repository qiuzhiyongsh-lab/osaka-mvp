# PRD｜房产状态 / 租赁状态「归一化维度搜索 + 自由文本关键词保真搜索」（详细实施稿）

- **文档类型**：功能需求稿（方式1：仅 PRD，**不写代码、不发版**；本稿交付给另一个对话框生成代码）
- **适用系统**（两仓同源，均需改动）：
  - `osaka-mvp`：本地算子 Web 查询（`core/store.py::search` + `core/store.py::upsert_property/upsert_many` + `web/templates/search.html` + `core/publisher.py::FIELD_MAP`）
  - `osaka-house-publish`（线上发布版 `osaka-house-v2`）：`app_local_v2/core/store.py::search/upsert` + `app_local_v2/web/templates/search.html` + `app_local_v2/web/app.py::api_query`
- **基线版本**：v1.9.77（2026-09-24 确认；发版时以实际为准）
- **目标版本**：v1.9.79（拟定，本轮功能迭代）
- **作者**：小巴（PM/RD 视角）｜**需求提出**：勇哥（2026-09-26）
- **状态**：待评审 → 待发版（代码由另一对话框实现）

---

## 0. 一句话结论（PM 视角）

勇哥要的两件事，现状是「一件已基本具备、一件要补一个统一维度」：

1. **(b) 关键词搜得到杂写信息** —— **现状已满足**，不用重做。后端 `q` 已把 `detail_json` 整段纳入 `LIKE`，实测搜 `オーナーチェンジ` 命中 926/926、`空室` 6 条、`賃貸中` 2 条。
2. **(a) 确定性字段里也能筛到这些状态** —— 需要补一个**统一派生维度 `occupancy_status`**，把散落在 5 处的状态信号收敛成一个可确定性多选筛选的枚举，并修两处现状缺陷（自定义关键词失效、现状组灰显）。

> 本稿核心价值 = 把"状态信息散落 5 处、无法一站式确定性筛选"收敛成**一个维度 + 一个面板**，并修复已存在但失效/灰显的功能。

---

## 1. 决策记录（勇哥本轮拍板，代码生成时直接照此，不要再追问）

| 编号 | 勇哥决定 | 落地含义 |
|---|---|---|
| D1 | **F1 同意**，并允许"再添加可能的关键词，作为自定义搜索条件" | 新增 `occupancy_status` 派生维度；面板内保留"自定义添加"输入框，自定义词走**自由文本关键词**逻辑（见 §6.4） |
| D2 | **多选 = OR，且跨分组都可选、都显示**（勇哥 2026-09-26 最终确认） | 房产状态多选 = **任一命中即返回**（OR）；公开组 / 租赁组 / 自定义**三组之间整体 OR**，不限制"必须同组"；选中几项就显示几项命中结果，**不是 AND**（见 §6.5） |
| D3 | **F2 不新增面板**，在原来的"房产状态"里调整 | 复用现有 `房产状态` 面板（L120-148），把"现状组"改造为"租赁·在租状态组"，**不新建筛选区** |
| D4 | **オーナーチェンジ 单独成类**，不并入"賃貸中" | 枚举值单列 `带租约OC`，与 `賃貸中` 区分 |
| D5 | F3 / F4 保留为功能项；**F5 已放弃**（见 D6） | F3 关键词保真+性能（P1）、F4 纳入 `trade_status`（含于 F1）保留；F5 从稿中删除 |
| D6 | **F5 放弃：不做，连后续（Phase 2）都不排**（勇哥 2026-09-26 最终拍板） | 不再抓取 `status_now`；面板"租赁·在租状态组"只读 `occupancy_status` 派生列（来自现有已抓字段）。`空室/賃貸中` 仅来自自由文本（8 条）+ 派生兜底，接受此口径，不追求 REINS 源真值 |

---

## 2. 背景与现状调研（证据先于结论）

数据源：`osaka-mvp/data/jproperty.db`，`properties` 共 **5538** 行，`detail_json` 内嵌。

### 2.1 当前搜索实现覆盖（`store.search` L1200-1345）

| 搜索入口 | 覆盖字段 | 状态类信息能否命中 |
|---|---|---|
| 关键词 `q`（多词 AND、词内字段 OR） | `address, building_name, property_no, line_station, layout, ward, kind, property_subtype, built_year_month, **detail_json**` | ✅ 含 `detail_json` 整段，自由文本词全命中 |
| 公开状态 `public_statuses` | `json_extract(detail_json,'$.public_status')` 精确= | ✅ 公開中/申込あり/一時停止 可确定性筛 |
| 取引態様 `trade_types` | `json_extract(detail_json,'$.trade_type')` LIKE | ✅ 含 `オーナーチェンジ` 多行枚举（709 行）可确定性筛 |
| 现状 `status_nows` | `json_extract(detail_json,'$.status_now')` | ❌ 库内 **0 命中**（从未抓取该字段）；F5 已放弃，该字段永不会存在，故"现状组"改读 `occupancy_status`（见 D6） |
| 自定义添加 | `statusAddCustom()` → 当作 `public_status` 等值匹配 | ❌ **失效**：自定义词（如 `オーナーチェンジ`）按 `public_status=` 匹配，永远 0 命中 |

### 2.2 状态类信息在库内的真实分布（全库直查，已交付 `osaka_status_signal_926.xlsx`）

| 字段 / 位置 | 中文含义 | 含信息物件数 | 性质 |
|---|---|---|---|
| `public_status`（公开状态） | 挂牌公开状态 | 4692 行有值（公開中1967 / 申込あり234 / 一時停止11 / `'-'`2480 / NULL846） | 结构化、已筛 |
| `trade_type`（取引態様） | 交易方式（含 OC 多行枚举） | 5238 行；其中含 `オーナーチェンジ` **709** 行 | 结构化、已筛 |
| `trade_status`（取引状況） | 交易状态 | 88 行；其中 `オーナーチェンジ` **86** 行 | 结构化、**未纳入筛选**（缺口 F4） |
| `property_subtype` | 物业子类 | 含 `オーナーチェンジ` **211** 行 | 结构化、未纳入筛选 |
| `building_name` 自由文本 | 物件名随手写的备注 | 含 `空室` 6 / `賃貸中` 2 / `オーナーチェンジ` 7 | 非结构化、仅关键词可搜 |
| `status_now`（现状/空室状況） | 当前空置/在租 | **0 行**（从未抓取） | 字段不存在；**F5 已放弃**，不再抓取，原"现状组"改读 `occupancy_status` 派生列 |

> 结论：能代表"在租/带租约/空置"的信号分散在 `trade_type`(709) + `trade_status`(86) + `property_subtype`(211) + `building_name`(15)，合计去重 **926** 条；字面"空室/賃貸中"仅 8 条（且是中介随手写在物件名里）。这些都**没有归一成一个可确定性筛选的维度**，正是本次要修的。

---

## 3. 目标

G1. 提供**统一派生维度 `occupancy_status`**，把上述 5 处信号收敛成一个可多选筛选的枚举（公开中/申购中/暂停/带租约OC/空室/賃貸中/満室/居住中/其他/未公开）。
G2. 复用现有"房产状态"面板，让用户能在**确定性选项**里一站式勾选上述状态（点选即生效、多选 OR）。
G3. 修复"自定义添加"失效问题：自定义关键词走**自由文本搜索**，确保"对方信息写得再杂也搜得到"。
G4. 保留并增强关键词搜索的**保真与性能**（命中字段回显/高亮 + FTS 索引）。

---

## 4. 用户故事

- **U1（确定性筛选）**：作为中介，我想在查询页"房产状态"里直接勾"带租约OC"，一键筛出所有带租约出售的房源，而不必在关键词里手打 `オーナーチェンジ` 再肉眼挑。
- **U2（多选 OR）**：我想同时勾"空室"和"賃貸中"，系统应返回**任一命中**的房源（不是必须同时空室又賃貸中）。
- **U3（自定义关键词保真）**：有些房源把"空室"写在物件名备注里，我想在面板里手填"空室"也能搜出来，而不是只有勾选项才灵。
- **U4（性能）**：库涨到几万行时，关键词搜索不能明显变慢；且搜出来要能看到"是哪段文字命中了"。

---

## 5. 功能清单（总览）

| 编号 | 功能 | 优先级 | 类型 |
|---|---|---|---|
| **F1** | 派生维度 `occupancy_status`（5 处信号 → 统一标签列） | P0 | 数据模型 + 派生逻辑 |
| **F2** | 复用"房产状态"面板，改造"现状组"为"租赁·在租状态组"，启用并映射 `occupancy_status` | P0 | 前端 |
| **F3** | 关键词保真 + 性能：保留 `detail_json` LIKE、加命中字段回显/高亮、加 FTS5 索引 | P1 | 前端 + 后端 + 索引 |
| **F4** | 派生逻辑覆盖 `trade_status`（补漏 86 条 OC） | P0（含在 F1 内） | 数据模型 |

---

## 6. F1 + F2 详细实施规范（代码生成直接照做）

### 6.1 新增列：`occupancy_status TEXT`

- **两仓均加**（`osaka-mvp/data/jproperty.db` 与 `osaka-house-publish` 的 properties 库）：
  ```sql
  -- 幂等：先 PRAGMA table_info 检查列是否存在，不存在才加
  ALTER TABLE properties ADD COLUMN occupancy_status TEXT;
  ```
- 存储格式：**竖线 `|` 分隔的标签集合**，例如 `公開中|带租约OC`、`申込あり|其他`、`公開中|空室`。
- 标签不重复、顺序固定（先公开状态、后租赁状态、最后兜底"其他/未公开"）。

### 6.2 派生函数（纯函数，两仓 `store.py` 各放一份，逻辑完全一致）

```python
# 建议函数名：derive_occupancy_status(detail: dict) -> str
# detail = json.loads(rec['detail_json']) 或空 dict
def derive_occupancy_status(detail: dict) -> str:
    if not isinstance(detail, dict):
        detail = {}
    tags = []

    # —— 公开状态 facet（恰好一个）——
    ps = detail.get('public_status')
    if ps in ('公開中',):
        tags.append('公開中')
    elif ps in ('申込あり',):
        tags.append('申込あり')
    elif ps in ('一時停止',):
        tags.append('一時停止')
    else:  # '-' / None / '' / 其它
        tags.append('未公开')

    # —— 租赁·在租 facet（零个或多个，彼此独立）——
    # 扫描这些来源字段（均为字符串，可能含换行/多值）
    sources = ' \n '.join(filter(None, [
        str(detail.get('trade_type') or ''),
        str(detail.get('trade_status') or ''),
        str(detail.get('property_subtype') or ''),
        str(detail.get('building_name') or ''),
        str(detail.get('detail_json') or ''),   # 兜底：整段文本（含自由备注）
    ]))
    if 'オーナーチェンジ' in sources:
        tags.append('带租约OC')
    if '空室' in sources:
        tags.append('空室')
    if '賃貸中' in sources:
        tags.append('賃貸中')
    if '満室' in sources:
        tags.append('満室')
    if ('居住中' in sources) or ('入居中' in sources):
        tags.append('居住中')

    # —— 兜底 ——
    if not any(t in tags for t in ('带租约OC','空室','賃貸中','満室','居住中')):
        tags.append('其他')   # 含义：未标注租赁/在租状态

    # 去重保序
    seen, out = set(), []
    for t in tags:
        if t not in seen:
            seen.add(t); out.append(t)
    return '|'.join(out)
```

- **优先级/冲突处理**：公开状态与租赁状态是**正交**的两个 facet，两者都保留（如 `公開中|带租约OC`）。同一 facet 内多个关键词同现（罕见，如既"空室"又"賃貸中"）则都保留。
- **`detail_json` 兜底扫描**确保今后任何新出现的状态词（写在任意文本里）都能被 `带租约OC/空室/賃貸中/満室/居住中` 的**字面匹配**兜住；匹配不到的归 `其他`。

### 6.3 落库钩子（两仓 `store.py`）

- 在 `upsert_property(rec)` 与 `upsert_many(rows)` 内，**写入前**计算并注入：
  ```python
  detail = {}
  try: detail = json.loads(rec.get('detail_json') or '{}')
  except Exception: pass
  rec['occupancy_status'] = derive_occupancy_status(detail)
  ```
- `osaka-mvp` 侧：本地 store 落库时算；`osaka-house-publish` 侧：ingest 落库时算（rec 自带 `detail_json`）。
- **发布链路**：`osaka-mvp/core/publisher.py::FIELD_MAP` 末尾追加一行：
  ```python
  ("occupancy_status", "occupancy_status"),   # v1.9.79：房产状态统一维度，推送至线上
  ```
  （确保增量推送把这个新列带上线；线上 `PROPERTY_COLUMNS`/upsert 也需接受该列，见 §6.6）

### 6.4 历史数据回填（一次性迁移脚本）

- 新列加好后，对**两仓全部已有行**回填：
  ```python
  for no, dj in con.execute("SELECT property_no, detail_json FROM properties"):
      d = {}
      try: d = json.loads(dj or '{}')
      except Exception: pass
      con.execute("UPDATE properties SET occupancy_status=? WHERE property_no=?",
                  (derive_occupancy_status(d), no))
  con.commit()
  ```
- 幂等可重复跑；后续增量行由 §6.3 钩子自动维护，**无需**每次回填。

### 6.5 `store.search()` 改造（两仓 `search` 函数，替换原 `public_statuses`/`status_nows` 段）

- **新增入参**（来自查询接口映射，见 §6.6）：
  - `occupancy_statuses`：列表，房产状态**枚举选项**选中值（来自公开组 + 租赁组）。
  - `occupancy_keywords`：列表，面板**自定义关键词**（自由文本）。
- **过滤逻辑**（OR 语义，整体作为一个外层 OR 子句，再与 ward/price 等 AND）：
  ```python
  os_list = [x for x in (f.get('occupancy_statuses') or []) if x]
  kw_list = [x for x in (f.get('occupancy_keywords') or []) if x]
  os_frags, os_args = [], []
  for v in os_list:                       # 枚举标签 → 查派生列
      os_frags.append("occupancy_status LIKE ?"); os_args.append("%" + v + "%")
  for kw in kw_list:                      # 自定义词 → 自由文本（复用 q 的 text_cols）
      inner = " OR ".join(f"COALESCE({c},'') LIKE ?" for c in TEXT_COLS)
      os_frags.append("(" + inner + ")")
      os_args += [f"%{kw}%"] * len(TEXT_COLS)
  if os_frags:
      where.append("(" + " OR ".join(os_frags) + ")")   # ← 关键：枚举与自定义整体 OR
      args += os_args
  ```
- **删除**原 `public_statuses`（L1314-1322）与 `status_nows`（L1323-1331）两段——`occupancy_status` 已完全取代；若担心旧分享链接，可在接口层把旧 `public_status`/`status_now` 参数**兼容映射到** `occupancy_statuses`（仅当 `occupancy_statuses` 为空时接管），过渡期后移除。
- `TEXT_COLS` 复用现有 `q` 的 10 个文本列（L1212-1214）。

### 6.6 查询接口 `api_query`（两仓 `web/app.py` L2181-2214）

- 入参映射新增/改动：
  ```python
  "occupancy_statuses": [s.strip() for s in request.args.getlist("occupancy_status") if s.strip()],
  "occupancy_keywords": [s.strip() for s in request.args.getlist("occupancy_keyword") if s.strip()],
  ```
- 前端发参名：枚举 → `occupancy_status`（重复）；自定义 → `occupancy_keyword`（重复）。接口 `getlist` 收成列表。
- 旧 `public_status`/`status_now` 参数：保留 `getlist` 读取但仅作兼容映射（见 §6.5 末句），新前端不再发这两个。

### 6.7 前端"房产状态"面板改造（`search.html`，两仓同一文件）

> 复用现有面板结构（L120-148），**不新建**。只改 3 处：状态枚举、启用开关、自定义行为、参数拼装、URL 回填/回显。

**(a) 枚举与开关（L771-773 附近）**
```javascript
var STATUS_PUB  = ['公開中', '申込あり', '一時停止', { v: '__none__', i18n: 'search.status_none' }]; // 公开组：保持
// 现状组 → 改造为「租赁·在租状态组」
var STATUS_LEASE = ['带租约OC', '空室', '賃貸中', '満室', '居住中', '其他'];   // 启用，映射 occupancy_status
var ST_PHASE_NOW = true;   // 由 false 改为 true（但仍读 occupancy_status，不读 status_now）
```
- 新增 i18n 键：`search.status_lease = "租赁·在租状态"`（替换原"现状"标题），`search.status_lease_tip` 可留空或"点选即可，可多选"。
- `STATUS_NOW`（L772 旧占位）删除或改名引用。

**(b) 渲染函数 `statusRender`（L781-806）**
- 公开组 `STATUS_PUB` 渲染逻辑不变。
- 现状组 body（`stNowBody`）改为渲染 `STATUS_LEASE`（去掉 `disabled`），组标题用 `search.status_lease`。
- `ST_PHASE_NOW=true` 时不再给 `stNowSec` 加 `msp-sec-disabled` 类（L804 已按该开关切换，无需大改）。

**(c) 自定义添加 `statusAddCustom`（L828-834）——修复失效**
- 现状：自定义值被当作 `public_status` 等值匹配，永远 0 命中。改为：**自定义词一律视为自由文本关键词**。
  ```javascript
  function statusAddCustom(){
    var inp = document.getElementById('stAddInput'); if(!inp) return;
    var v = (inp.value || '').trim(); if(!v) return;
    var isEnum = STATUS_PUB.concat(STATUS_LEASE).some(function(o){
      return (typeof o==='string') ? o===v : o.v===v;
    });
    if(!isEnum && (ST_CUSTOM||[]).indexOf(v) < 0){ ST_CUSTOM.push(v); }  // 自由文本关键词
    ST_SEL[v] = 1;                 // 仍进选中集合（用于按钮文案/已选计数）
    ST_CUSTOM_FLAG[v] = !isEnum;   // 标记是否走关键词逻辑
    inp.value=''; statusRender();
  }
  ```
- 新增全局 `var ST_CUSTOM_FLAG = {};`（与 `ST_CUSTOM` 并列）。

**(d) 参数拼装 `buildParams`（L1380-1381 替换）**
```javascript
// 原：statusValues().forEach(function(v){ p.append('public_status', v); });
// 改：枚举值 → occupancy_status；自定义词 → occupancy_keyword
statusValues().forEach(function(v){
  if(ST_CUSTOM_FLAG[v]) p.append('occupancy_keyword', v);
  else                  p.append('occupancy_status', v);
});
```

**(e) URL 回填与回显**
- 回填（L1124-1126）：`qs.getAll('occupancy_status')` → 进 `ST_SEL`；`qs.getAll('occupancy_keyword')` → 进 `ST_CUSTOM` 并标记 `ST_CUSTOM_FLAG`。
  ```javascript
  var osv = qs.getAll('occupancy_status').filter(Boolean);
  if(osv.length){ ST_SEL={}; osv.forEach(function(v){ ST_SEL[v]=1; }); touched=true; }
  var okv = qs.getAll('occupancy_keyword').filter(Boolean);
  if(okv.length){ okv.forEach(function(v){ ST_CUSTOM.push(v); ST_CUSTOM_FLAG[v]=1; ST_SEL[v]=1; }); touched=true; }
  ```
- 回显（`restoreFilters` L1046-1049）：`st.status` 拆分——枚举值进 `ST_SEL`，非枚举进 `ST_CUSTOM`+`ST_CUSTOM_FLAG`。
- 摘要文案（`statusSync` L813-821）已用 `statusValues()`，无需改。

---

## 7. F3 / F4 小白说明 + 实施规范

### 7.1 F3 关键词保真 + 性能（P1）——给产品小白

**是什么**：查询页顶部那个"关键词"输入框（不是房产状态面板，是单独的搜索词框）。
**解决什么问题**：
- *保真*：现在搜 `オーナーチェンジ` 已经能搜到（因为底层把整段详情拿去模糊匹配），这一步是**保持它继续好用**，不退化。
- *性能*：库只有 5538 行时模糊匹配够快；但以后涨到几万行，每次搜索都把整段 `detail_json` 扫一遍会越来越慢。所以要建一个**全文索引（FTS5）**，让"按词找"像查字典一样快。
- *回显/高亮*：现在搜完，你只能看到房源卡片，但**不知道是哪段文字命中了关键词**。F3 要在卡片上显示"命中片段"并把关键词**高亮**（黄底），方便一眼确认"这套房为什么被搜出来"。

**对开发的具体要求**：
1. 保留现有 `q` 的 `text_cols` LIKE 逻辑（保真，不删）。
2. 建 `properties_fts` FTS5 虚拟表，索引 `detail_json`（可含 `address`、`building_name`）；`upsert` 时同步增删 FTS 行。搜索时若 `q` 非空，先用 FTS 取 `rowid` 再 `JOIN` 主表（替代全表 LIKE 扫描）。
3. 后端对 `q` 命中的每行，额外计算 `hit_field`（命中的字段名，优先 `building_name`→`address`→`detail_json`）+ `hit_snippet`（命中词前后约 30 字、用 `<mark>` 包裹）。前端在卡片上渲染该片段并高亮。
4. 多词（空格分隔）仍保持"词间 AND、词内字段 OR"的现有语义。

### 7.2 F4 把 `trade_status` 纳入筛选通道（P0，含在 F1 内）——给产品小白

**是什么**：`trade_status` 是详情里另一个"交易状态"字段，目前只被**自由文本**搜到，**结构性筛选器读不到它**。
**具体问题**：库里 `オーナーチェンジ`（带租约）有 926 条，其中 **86 条**它的"带租约"信息写在 `trade_status` 字段里（不是 `trade_type`）。现在的确定性筛选器只认 `trade_type`，所以这 86 条在勾"带租约OC"时**会漏**。
**怎么落地（已被 F1 覆盖）**：§6.2 派生函数的"扫描来源"**显式包含 `trade_status`**（第 2 个扫描源），所以 F1 上线后这 86 条会自动被标成 `带租约OC`，勾选即命中——**不需要单独再写 SQL**。
> 备选（若团队不想引入 `occupancy_status`）：在 `search()` 里照 `trade_type` 的格式加一段 `json_extract(detail_json,'$.trade_status') LIKE`，入参 `trade_statuses`。但既然做 F1，走派生维度即可，无需此备选。

---

## 8. EARS 验收标准

- **Ubiquitous（始终满足）**：系统须为每条房源维护 `occupancy_status` 派生标签列，并在每次 `upsert` 时重算。
- **Event-driven（事件触发）**：当用户在"房产状态"筛选中勾选一个或多个值时，系统须只返回 `occupancy_status` 含任一选中标签、或 `detail_json` 含任一自定义关键词的房源。
- **Unwanted（异常）**：若某房源 `detail_json` 缺失/为空，系统须将其标为 `未公开|其他`，且在"不限"查询中仍可出现。
- **State-driven（状态相关）**：当"租赁·在租状态组"启用（`ST_PHASE_NOW=true`）时，系统须按 `occupancy_status` 租赁标签过滤。
- **Optional（F3 启用时）**：系统须用 FTS 索引服务关键词查询，并对命中行返回高亮片段 `hit_snippet`。

---

## 9. 边界与异常

- B1. 自定义词与枚举同名（如用户手填"公開中"）：按枚举处理（走 `occupancy_status`，非关键词）。
- B2. 同一房源既"空室"又"賃貸中"（极罕见）：两个标签都保留，`occupancy_status` 含两者；勾任一即命中（OR）。
- B3. `detail_json` 解析失败：派生函数捕获异常 → 返回 `未公开|其他`，不抛错、不阻断落库。
- B4. 旧分享链接带 `public_status=`：接口兼容映射到 `occupancy_statuses`（仅当新参数为空时），避免老链接失效。
- B5. 多选 OR 的副作用（已知限制）：公开组与租赁组之间也是 OR，**不支持"公開中 且 带租约OC"的 AND 组合**。若勇哥后续需要"分组内 OR、分组间 AND"，列为后续增强，本期不实现（先按 D2 交付纯 OR）。

---

## 10. 风险与回滚

- R1. **派生列/回填写错** → 影响全库筛选。缓解：回填脚本先 `SELECT` 抽样核对（取 20 条人工比对标签），再全量；回滚 = 置 `occupancy_status=NULL` 并删列（SQLite 不支持 DROP COLUMN 旧版，可用新建表替换或留 NULL 忽略）。
- R2. **FTS5 同步遗漏** → 新抓取房源关键词搜不到。缓解：upsert 同步写 FTS；加冒烟测试（抓取 1 条后搜其关键词必中）。
- R3. **前端参数名改错** → 筛选静默失效。缓解：保留 `public_status` 兼容映射过渡；发版后人工走查"勾 带租约OC → 结果数 ≈ 926"。
- R4. **发布链路漏推 `occupancy_status`** → 线上无该列。缓解：FIELD_MAP 加列 + 线上 `PROPERTY_COLUMNS`/upsert 接受该列；发版后查线上库确认列存在且非空。

---

## 11. 测试清单

- T1. 单元：派生函数对 5 类样本（公開中+OC / 申込あり+空室 / 未公开+其他 / trade_status=OC / building_name 含賃貸中）输出符合预期标签。
- T2. 集成：勾"带租约OC" → 结果数 ≈ 926；勾"空室" → ≈ 6；勾"賃貸中" → ≈ 2；多选"空室+賃貸中" → ≈ 8（OR）。
- T3. 自定义：面板手填"オーナーチェンジ" → 命中 926（走关键词，不再 0）。
- T4. OR 语义：勾"公開中 + 带租约OC" → 结果 = 公開中总数 ∪ 带租约OC（不减交集）。
- T5. 回填：全库 `occupancy_status` 非空率 100%；抽样 20 条人工比对。
- T6. F3：库增至压测量级，关键词查询耗时达标；命中卡片显示 `<mark>` 高亮片段。
- T7. 兼容：旧分享链接（`?public_status=公開中`）仍可正确筛选。

---

## 12. 依赖与排期建议

- **本期（v1.9.79）**：F1 + F2 + F4（含在 F1）+ 前端改造 + 回填 + 发布链路。建议同步做 F3（性能/高亮）一并交付，因同触搜索路径、改动集中。
- **后续（Phase 2）**：仅 B5 的"分组间 AND"增强（待勇哥确认是否需要）。F5 已放弃，不再排期（见 D6）。
- **跨仓**：`osaka-mvp` 与 `osaka-house-publish` 同源改动需同版本发版；发布顺序 = 先 mvp 落库/回填 → publisher 推 `occupancy_status` → 线上 ingest 接该列 → 线上前端发版。

---

## 13. 待确认 / 开放项（含详细说明 + 建议，待勇哥拍板）

### 13.1 分组内 OR、分组间 AND 进阶语义（B5）
- **是什么**：当前 D2 已定为"纯 OR"——所有勾选项（无论属于哪个组）整体 OR，任一命中即返回。进阶语义 = "同组内多选取 OR，但不同组之间用 AND"，即跨组多条件须**同时满足**。
- **举例**（公开组：公開中/申込あり/一時停止/未公开；租赁组：带租约OC/空室/賃貸中/満室/居住中/其他）：
  - 纯 OR（当前）：勾「公開中 + 带租约OC」→ 返回「公開中 ∪ 带租约OC」≈ 公開中全集（因绝大多数 OC 房源本身也是公開中），带租约OC 的筛选几乎被淹没。
  - 进阶 AND：勾「公開中 + 带租约OC」→ 返回「公開中 ∩ 带租约OC」= 真正"在售且带租约"的子集，结果大幅收窄、语义更精准。
- **代价**：`search()` 需识别分组边界、构造嵌套 AND/OR；URL 参数需区分组；前端需明确提示当前是 OR 还是 AND（否则用户困惑）；自定义关键词（第三组）与各组的关系也需定义。
- **建议**：**默认仍按 D2 纯 OR 交付**（本轮小步快跑、零额外复杂度）；但强烈建议把"分组间 AND"列为**紧随其后的增强**——因为纯 OR 跨组多选时，结果集几乎等于最大那一组，会让用户觉得"多选没用"。实现上它是在现有 `occupancy_statuses` 过滤之上**叠加一层分组 AND**，不破坏已定结构，可独立排期。最终以勇哥是否需要"同时满足多状态维度"为准。

### 13.2 FTS5 不支持时退回 LIKE + 节流（F3 备选）
- **是什么**：F3 计划用 SQLite 的 FTS5 全文索引加速关键词搜索。但 FTS5 是**可选编译模块**，并非所有 SQLite 构建都带它（老版本/精简嵌入版可能没有）。若运行环境没有 FTS5，就退回现有的 `detail_json` 整段 `LIKE` 扫描，并加"节流"（限制扫描行数/结果条数/防抖）避免大库时界面卡顿。
- **代价/风险**：FTS5 路径需在每个 upsert/delete 时同步维护索引表，有额外写入成本；LIKE 路径在大库（几万行）会变慢。
- **建议**：**这不是产品决策，而是上线前的兼容性检查**——编码前先在 mvp 与 publish 两个运行环境各跑一次：`SELECT sqlite_version();` 并试建 `CREATE VIRTUAL TABLE t USING fts5(c)`。两者都支持 → 走 FTS5；任一不支持 → 实现 LIKE+节流兜底路径（片段高亮仍可用手动子串实现，不依赖 FTS）。**最佳实践**：代码里做成"启动自检 + 自动降级"，FTS5 与 LIKE 两条路径都留着，运行时按能力自动选择，这样勇哥无需拍板、工程侧兜底即可。故建议从"待确认"改为"工程自检项"，默认双路径自动降级。

### 13.3 "其他"标签是否暴露到面板
- **是什么**：`occupancy_status` 派生列有一个兜底标签「其他」（含义：有公开状态、但无任何租赁/在租信号）。问题是——它要不要作为"租赁·在租状态组"里一个**可勾选的选项**显示在面板上？
  - 暴露：用户可显式勾「其他」→ 筛出"完全没有标注租赁状态的房源"（数据质量视图）。
  - 仅后台（默认）：「其他」只作为存储值，面板只展示 5 个有意义标签（带租约OC/空室/賃貸中/満室/居住中）；标「其他」的房源仍出现在"不限/全部"结果里，但无法被单独隔离。
- **代价**：暴露「其他」能让用户发现"未标注库存"（利于数据治理），但它是个"负向"筛选（找缺信息的），可能让普通用户困惑；仅后台则面板干净、聚焦可用状态。
- **建议**：**默认仅后台兜底**（面板只放 5 个 actionable 标签），保持界面简洁；若勇哥后续想要"数据质量视图"，再把「其他」以置底/灰显形式暴露。当前不阻塞开工。

> **已拍板项（不再开放）**：D2 跨分组 OR、D3 复用现有面板、D4 オーナーチェンジ 单独成类、D6 F5 放弃——见 §1 决策记录。

---

## 14. 实际落地修正（2026-09-26 勇哥拍板 + 小巴核对）

> 本节记录本 PRD 在 `osaka-mvp` 实际落地时与原稿的偏差，以及已拍板的决策。
> 落地目标版本：**v1.9.94**（原稿写 v1.9.79 基线，已严重过时；v1.9.79–v1.9.93 均已被占用）。

### 14.1 已拍板决策（AskUserQuestion 确认）
- **D7 日期默认值**：保持「今天」为默认（不改默认今天行为）；但**空结果时给友好提示**（提示用户当前限定在今天、可取消日期限制查全部）。
- **D8 租赁组选项**：完全照 PRD 全放 —— `STATUS_LEASE = ['带租约OC','空室','賃貸中','満室','居住中','其他']`（含「其他」作可勾选项，置底）。
- **D9 实施范围**：**F1 + F2 + F3 一起做**（派生维度 + 前端筛选 + FTS5 全文高亮）。

### 14.2 与原稿的技术偏差
1. **版本基线**：原稿 §12 写「v1.9.79」，实际落地 **v1.9.94**（版本号单一源 `core/version.py`）。
2. **单仓改动**：原稿 §12「跨仓」段假设 `osaka-mvp` 与 `osaka-house-publish` 同源改动。**实际只改 `osaka-mvp` 一套代码**，发布流程 = 改完 → `sync_to_publish.py` → 重新发布线上（或仅 `force_full_push` 推数据，见下）。`osaka-house-publish` 不再手改。
3. **发布链路放行须三处同步**（铁律，原稿 §6.3 漏写）：
   - `core/store.py::PROPERTY_COLUMNS`（落库过滤，加列必改，否则 `upsert_property`/`upsert_many` 的 `if k in PROPERTY_COLUMNS` 会把新列过滤掉）；
   - `core/publisher.py::FIELD_MAP`（线上推送字段映射，加 `("occupancy_status","occupancy_status")`）；
   - `web/app.py::NEVER_UPLOAD_KEYS`（白名单放行）；
   - 以及 `web/app.py::_row_payload()`（前端载荷出口，须把 `occupancy_status` 带出去）。
   - 仅改 `store.py` 列定义 → **白推（本地有、线上无）**。
4. **URL 回填机制不符**：原稿 §6.7 用 `qs.getAll(...)` 回填查询状态。但本仓 `search.html` 实际走 `restoreQState()`（localStorage 路径），已有 `ST_SEL` 直接回填逻辑。落地须改用 `restoreQState` 兼容写法（枚举项进 `ST_SEL`、自定义项进 `ST_CUSTOM`），**不能**直接套 `qs.getAll`。
5. **FTS5 已实测可用**：本机 SQLite 3.53.1 支持 `CREATE VIRTUAL TABLE ... USING fts5(c)`；日文子串需 `trigram` tokenizer。故 §13.2 从「待确认」转为「已确认双路径自动降级」——代码默认 `trigram` FTS5，任一词<3 字符或 FTS 不可用时自动回退 `LIKE`，无需勇哥再拍板。

### 14.3 数据实证（落地前抽样，待回填脚本全量复核）
- 派生样本（勇哥拍板前小样本）：空室 7 件、賃貸中 2 件等。最终分布以 `tools/backfill_occupancy_fts.py` 全量跑完后的 `GROUP BY occupancy_status` 为准（回填后由 QA 实证）。
- `occupancy_status` 含竖线 `|` 分隔多标签（公开 facet + 租赁 facet 去重保序拼接），筛选用 `LIKE '%标签%'`。

### 14.4 不主动发版
按 osaka 线协作约定：小巴改完代码 + QA 实证 + checkpoint 后，**不主动发版**，等勇哥在对话窗口明确说「去整合发版」才执行 `sync_to_publish` + 重新发布。
