# PDF 线上展示 — 精确操作清单（按现同源架构）

> 配套 PRD：`docs/PRD/osaka_mvp_pdf_online_prd.md`
> 编写：小巴　|　2026-09-22　|　状态：待勇哥确认 Q1–Q3 口径后执行
> 所有路径基于 `C:\Users\25374\WorkBuddy\2026-09-11-09-50-22\osaka-mvp\`
> 铁律：**动代码先 checkpoint（checkpoint.py）→ 写码不发布 → 勇哥说"发版"才 sync+deploy**

---

## 阶段 0　腾讯云控制台准备（勇哥操作，约 15 分钟）

> 🔗 **详细图文步骤见 `osaka_mvp_pdf_cos_setup_guide.md`**（含每个选项怎么填、策略 JSON、常见坑）。
> 2026-09-23 定稿：新建专用桶 · 地域**大阪 ap-osaka** · 私有读写 · 子账号最小权限。

1. **创建存储桶**（https://console.cloud.tencent.com/cos/bucket → 创建存储桶）
   - 名称：`osaka-house-pdf`（系统自动拼成 `osaka-house-pdf-<APPID>`）
   - 地域：**亚太 — 大阪（ap-osaka）** ✅ 已官方确认 COS 支持（文档 436/6224，2026-09-03）
   - 访问权限：**私有读写**（🔴 绝不能选公有读）
   - 多 AZ / 版本控制 / 日志：全部**不开启**（省成本）
2. **创建子账号 `osaka-pdf-bot`**（CAM → 用户 → 新建用户 → 只勾「编程访问」）
   - 挂**自定义策略**（只给这一个桶的 `GetObject`+`PutObject`+`HeadObject`，不给 Delete/Bucket 级权限）
   - 生成 API 密钥 → 拿到 `SecretId` / `SecretKey`（**子账号**，泄露也删不了、列不出）
3. **把 5 项信息交给小巴**：SecretId / SecretKey / Bucket 全名（含 APPID）/ Region(`ap-osaka`) / 默认访问域名
   → 小巴写入本机 `config.local.yaml`（已 .gitignore，`sync_to_publish` 的 `SECRET_KEYS` 保证其**永不上云**）
4. **本机装 SDK**（venv，隔离）
   ```bash
   "C:/Users/25374/.workbuddy/binaries/python/envs/default/Scripts/pip.exe" install cos-python-sdk-v5
   ```

---

## 阶段 1　本地配置 + 上传/签名脚本（小巴写码）

**1.1 `config.local.yaml` 新增 cos 段（本机真值，已 .gitignore）**
```yaml
cos:
  secret_id: "AKIDxxxxxxxx"      # 只读子账号
  secret_key: "xxxxxxxxxxxx"
  bucket: "osaka-pdf-xxxx-1234567890"
  region: "ap-guangzhou"
  base: "https://osaka-pdf-xxxx-1234567890.cos.ap-guangzhou.myqcloud.com"
```
> 注意：`sync_to_publish.py:61-70` 的 `SECRET_KEYS` 含 `secret`/`key` → 此段**自动被剔除**，不会上云（铁律保命）。

**1.2 新建 `tools/pdf_to_cos.py`**（约 120 行）
- `upload(no, local_pdf)`：上传 `data/attachments/<no>.pdf` → `cos://bucket/<no>.pdf`；返回预签名 URL（有效期 7 天，`get_presigned_download_url`）。
- `sign(no)`：仅重签（签名剩余 < 2 天时调用，省流量）。
- `batch_backfill()`：扫描 `data/attachments/*.pdf` **全量上传**（按 F8 修订：含孤儿），库内有对应房源的再写 `pdf_url`。
- 密钥读取走 `core.config.load()` 的 `cos` 段（本机 `config.local.yaml` 覆盖）。

**1.3 依赖**：`cos-python-sdk-v5`（阶段 0 已装 venv）。

**验证**：`python tools/pdf_to_cos.py --test-one <某在架番号>` → 控制台打印预签名 URL；浏览器开 URL 能下载该 PDF（7 天内）。

**回滚**：删 `tools/pdf_to_cos.py` + `config.local.yaml` 的 `cos` 段；主库 `pdf_url` 留空不影响现有功能。

---

## 阶段 2　历史补传（一次性，约 30 分钟 / 2.4GB）

```bash
cd osaka-mvp
"C:/Users/25374/.workbuddy/binaries/python/envs/default/Scripts/python.exe" tools/pdf_to_cos.py --backfill
```
- **全量上传**（勇哥口径"取下来就上云"）：扫描 `data/attachments/*.pdf` **全部上传，不过滤孤儿**；仅对"库内存在对应 `property_no` 且该房源 `has_floorplan=1`/`pdf_path` 非空"的番号写 `pdf_url`（孤儿虽上传但不挂链接，详情页按 F6 隐藏）。
- 进度日志每 100 份打印；失败计数末尾汇总。
- 完成后：`SELECT COUNT(*) FROM properties WHERE pdf_url IS NOT NULL` 应接近"有 PDF 房源数"（非 3725 全量，孤儿已剔）。

**验证**：抽 3 个已上传番号，开预签名 URL 确认可下载；COS 控制台看桶内对象数 ≈ 在架有 PDF 房源数。

**回滚**：`UPDATE properties SET pdf_url=NULL`；COS 桶清空（或保留，不上线即无害）。

---

## 阶段 3　`sync_to_publish.py` 放行 `pdf_url`（小巴写码，1 行改动）

**文件**：`tools/sync_to_publish.py:72`
```python
# 改前
PDF_KEYS = {"pdf_path", "pdf_url", "pdf_local", "pdf_rel", "pdf"}
# 改后（仅放行 pdf_url，pdf_path 仍剔除 —— 满足 memory §9.4 "对外数据包 pdf_path 须空"）
PDF_KEYS = {"pdf_path", "pdf_local", "pdf_rel", "pdf"}
```
- 同步校验：`L228 leak = sum(1 for r in rows if PDF_KEYS & set(r.keys()))` → 重构后 `leak` 应仍为 0（`pdf_url` 不在剔除集，但 `pdf_path` 仍在 → 若 `pdf_path` 误进 site_data 仍会被抓；实际 `pdf_url` 进、`pdf_path` 不出，leak 计数逻辑需改判"仅 pdf_path 等敏感键"——详见下方注意）。
- ⚠ **注意**：`L228` 的 `leak` 定义是"含任何 PDF_KEYS 的房源数"。放行 `pdf_url` 后需把 `leak` 语义改为"含 `pdf_path`/`pdf_local`/`pdf_rel`/`pdf` 中任一"（即敏感键），否则会把正常的 `pdf_url` 误报为泄漏。修正：
  ```python
  SENSITIVE_PDF_KEYS = {"pdf_path", "pdf_local", "pdf_rel", "pdf"}
  leak = sum(1 for r in rows if SENSITIVE_PDF_KEYS & set(r.keys()))
  ```

**验证**：`python tools/sync_to_publish.py --dry` → 打印"数据 N 行一致"且 `pdf_url` 在数据包内；`grep pdf_url data/site_data.json | head` 有值。

**回滚**：`git checkout tools/sync_to_publish.py`。

---

## 阶段 4　线上展示 `detail.html`（小巴写码）

**文件**：`web/templates/detail.html:58-59, 95, 119-125`
- 现状：区块以 `{% if row.pdf_path %}` 判定（线上 `pdf_path` 恒空 → 永不显示）。
- 改法（兼容本地+线上）：
  ```html
  {% set pdf_src = row.pdf_url if row.pdf_url else (url_for('download', dir='attachments', name=row.property_no + '.pdf') if row.pdf_path else '') %}
  {% if pdf_src %}
    <h2><span data-i18n="detail.pdf_h">房源図面 PDF</span>
      <span class="hint">{{ 'COS 直链（线上）' if row.pdf_url else '本机 PDF 原件（不会上传到线上）' }}</span></h2>
    <iframe class="pdf-frame" src="{{ pdf_src }}" title="物件図面 PDF"></iframe>
  {% endif %}
  ```
- 线上 `_public_guard` 对 `/download/*` 404 不影响（线上走 `pdf_url` 外链）；本地仍走 `download` 路由。
- `detail.html:190` `CUR_PDF` 同步改判 `row.pdf_url or row.pdf_path`。

**验证**：本地起 8765（双击 `start_mvp.bat`）→ 详情页有 PDF 房源 → iframe 显示（本地走 download）；`director_audit` 红灯 0。
**线上验证（发版后）**：登录详情页 → iframe 走 COS 签名直链，可翻页。

**回滚**：`git checkout web/templates/detail.html`。

---

## 阶段 5　增量自动化钩子（小巴写码，Q4 落地）

**文件**：`core/crawler.py` 落盘 PDF 处（约 L3362 附近 `_fetch_detail_by_no` 写 `data/attachments` 之后）
- 落盘成功后调用：
  ```python
  try:
      from tools.pdf_to_cos import upload as cos_upload
      cos_upload(property_no, local_pdf_path)
  except Exception as e:
      log(f"[PDF→COS] 上传失败（不阻断主流程）：{e}")
  ```
- 幂等：COS key=番号，覆盖写；`pdf_url` 仅签名 < 2 天时重签。
- 失败留 `pdf_url` 空 → 详情页按 F6 隐藏，不 500。

**验证**：本地抓一轮新房源（含 PDF）→ 该番号 COS 出现 + `pdf_url` 入库 → 下次 sync 线上可见。
**回滚**：注释掉钩子调用。

---

## 阶段 6　门禁 + 发版（勇哥说"发版"才执行）

1. **抬版本**：`core/version.py` `VERSION="1.9.60"`（单一源）。
2. **director_audit 增项**（可选）：检查 `pdf_url` 非空但 `pdf_path` 也非空 → 告警（防敏感路径泄漏）。
3. **checkpoint**：`python checkpoint.py "v1.9.60 PDF 线上展示：cos 上传+签名+sync 放行 pdf_url+detail 展示+增量钩子"`。
4. **sync**：`python tools/sync_to_publish.py`（③a 账号回流校验）。
5. **deploy**：workbuddy_sites_deploy → 复用 `wbapp_C65E82hYRMCjxjcwW2Ph1E`。
6. **健康检查**：`/api/selfcheck` + `/api/ping` + 抽 1 房源详情页确认 iframe 走 COS。
7. **门禁⑦**：`director_audit.py` 红 0。

---

## 当前状态（2026-09-23 拍板定稿）

✅ Q2 = **§4.A**（私有读 + 本地预签名直链，线上零密钥）　✅ Q1 = **新建桶 · 大阪 `ap-osaka`**
✅ Q3 = **历史 3725 份 + 未来每轮新增，全量上云**　✅ Q4 = **每轮新 PDF 自动上云**

### 🔴 唯一阻塞：等勇哥交付 COS 子账号密钥

按 **`osaka_mvp_pdf_cos_setup_guide.md`** 完成建桶 + 建子账号后，把 **5 项信息**给小巴：

```
1. SecretId:        AKIDxxxxxxxxxxxxxxxxxxxxxxxx
2. SecretKey:       xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
3. Bucket 全名:     osaka-house-pdf-1258xxxxxx      ← 必须带 APPID 后缀
4. Region:          ap-osaka
5. 默认访问域名:     https://osaka-house-pdf-1258xxxxxx.cos.ap-osaka.myqcloud.com
```

小巴收到后：写入 `config.local.yaml`（已 .gitignore，`SECRET_KEYS` 保证永不上云）→ 装 SDK → 写 `tools/pdf_to_cos.py` → 全量补传 → 改 sync/detail → 挂增量钩子 → **写码不发布**，等勇哥说「发版」。

- [ ] 门禁③ 真机复核近期 PDF 落盘率（全量开增量前；不阻塞历史补传先行）
