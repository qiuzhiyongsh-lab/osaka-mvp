# -*- coding: utf-8 -*-
"""软件版本与构建时间（单一事实来源）。

- VERSION：语义版本号。
- BUILD_AT：本次软件"更新"对应的具体日期和时间（本地时区）。
  每次发布/重大更新时手动把这两个值改掉；页面底部会自动显示。
"""
from __future__ import annotations

VERSION = "1.9.54"
BUILD_AT = "2026-09-22 09:15"
# ============================================================================
# v1.9.54（2026-09-22 09:15 · 🎨查询页「房间/楼层」改版（勇哥图 1）：原来「房间」由两个 .field.sm 组成（各带一份单位「室」），窄容器下第二块被挤到下一行 → 单位重复 + 上下两行错位。改为单个 .range-row 容器，整条「房间 从 [N] 到 [M] 室」强制一行内完整显示；「楼层」同法处理。复用既有 i18n 键（search.rooms_from/rooms_to/rooms_unit、search.floor_from/floor_to/floor_unit），零新增。改动文件：web/templates/search.html（房间/楼层 HTML 改 .range-row）、web/static/style.css（新增 .range-row/.rr-lbl/.rr-sep 规则）、core/version.py）
# ============================================================================
# v1.9.53（2026-09-22 09:30 · 查询页「沿线/车站」面板体验增强（勇哥 3 图）：① 线路/车站面板新增「全选」按钮（修 msSelectAll 对字符串线路项 o.v=undefined 漏选的 bug，改用 msOptVal；且只勾当前筛选可见项）② 面板头部新增筛选文本框，按标签子串实时过滤选项、隐藏空组、不改选中态 ③ 分组标题与首行贴紧、减小纵向空隙（根因 = 通用 .msp-body 是 display:flex 横排，分组标题与车站行被当 flex 子项换行错位；线路/车站面板强制 display:block）④ 补回缺失官方站「なんば駅（S16）」：STATION_ORDER 千日前線/四つ橋線 難波→なんば（2014 改名，三方取证），stations_for 合并 STATION_ORDER 官方站序保证字典缺的真实站不遗漏，難波(汉字)仅南海本線等私营线保留不误归一。改动文件：core/store.py（STATION_ORDER+stations_for）、web/templates/search.html（面板 HTML+msRender/msSelectAll/msClear/msFilter/MS_FILTER）、web/static/style.css（.msp-group/.msp-row 收 margin + .msp-filter/.msp-nomatch + body display:block）、web/static/i18n.js（cat.filter/cat.no_match 四语）、core/version.py）
# ============================================================================
# ============================================================================
# v1.9.48（2026-09-21 12:10 · 🟠修「阶段B 番号検索补详情 3 条持续 TimeoutError 8000ms」：真机取证(forensics_bug2_dom.py)确认根因 = 那 3 套(300140791556/100140789119/100140779224)已 成約済/取り下げ，番号検索返回「検索結果が0件です」、结果页 0 行、根本无「詳細」按钮可点；原代码去点不存在的按钮卡满 8000ms 超时、每轮误报一次。非 locator 写错（已看真实 DOM）。修法 = _fetch_detail_by_no 点「詳細」前加「0件/结果0行/无詳細按钮」三重优雅跳过守卫，不再卡超时、不再误报。影响文件：core/crawler.py 仅 _fetch_detail_by_no 加守卫，纯行为防护、零 locator 改动。需重启本地后台后下一轮阶段B 即验证。）
# v1.9.47（2026-09-21 10:19 · 🔴修「推线上跳过 / 自动上传异常：OperationalError: 10 values for 9 columns」：根因 = publisher.set_state 的 INSERT 列名 9 列、VALUES 却给 10 个值（多写一个 ?），参数仅 8 个 → SQLite 报列数不符，publish_state 水位线写不进。线上推送本身（/api/ingest 60/60 确认）不受影响，仅本地台账缺失 + 误报「推线上跳过」。修法 = VALUES 删掉多余一个 ?（1+8? 对齐 9 列 8 参）。影响文件：core/publisher.py 仅 set_state，纯 SQL 对齐，零行为变更。需重启本地后台后下一轮推送即验证。）
# v1.9.46（2026-09-21 02:40 · PRD「查询页新增房产状态搜索条件」：筛选区新增「房产状态」双分组多选面板（复用 .msp 样式）。
#   【公开状态组·Phase 1 即用工】枚举来自真实库实测 public_status 去重 = 公開中/申込あり/一時停止/'-'/NULL；
#     后端走 json_extract(detail_json,'$.public_status') 精确 = 匹配（免提列、免回填，风险最低），__none__ 特判「'-' 或 NULL = 未公开/无状态」；
#     支持手动填写新增（仅本浏览器 qstate 记忆，零后端改动）。【现状组·Phase 2 待启用】status_now 键当前库 0 命中，面板灰显，预留过滤通道。
#   【改动面】core/store.search 加 public_statuses/status_nows 两组 OR 过滤；web/app.py /api/query 收 getlist；
#     search.html 加控件 HTML + statusRender/statusPick/statusValues/statusSync/statusClear/statusSelectAllPub/statusAddCustom/statusToggle；
#     i18n 四语补 search.status* 7 键；style.css 加灰显/手填样式。qstate 记忆 + URL 参数（public_status 多值）同步打通。）
# v1.9.45（2026-09-21 01:22 · 根治「线上种子按行数判断、行数相同不重灌」：改 `tools/sync_to_publish.py` 生成的线上入口 serve_public.py —— 判据从 `have != len(rows)` 改为**数据包内容印章** `data/site_data.stamp`（记录 site_data.json 的 generated_at），三者任一即重灌：① 空库 ② 行数变了 ③ 印章变了。这样某列批量回填（如 v1.9.42 的 has_floorplan）也能随发版刷到线上，不必再依赖手动「强制同步上传」。注：serve_public.py 是 sync 生成的，务必改生成模板而非手改入口。）
# v1.9.44（2026-09-21 00:53 · 「上传到线上」卡片新增「强制同步上传（房源+AI 全量）」：房源走 mode=full 全量重传；AI 解读**忽略增量水位线 last_ai_at** 全量重推（publisher._push_ai(force=True) / publish(force_ai=True) / /api/publish 收 force_ai）。一键把线上补齐成与本地 100% 一致，不再受增量水位线限制。前端二次确认防误触。）
# v1.9.43（2026-09-21 00:39 · 🔴修「设置页 AI 读取时间保存后回显旧值/像没保存」：根因 = `_refresh_cfg()` 只刷新 SCHED/PUB_LOOP，**漏了 AI_SCHED** → AI_SCHED.status() 恒按启动时 cfg 报数 → 保存成功后前端拉状态把表单刷回旧值（落盘其实成功，如 max_per_run 已 1000，页面却显示 300）。修：_refresh_cfg 同步 AI_SCHED.cfg；另加固前端 saveAISched —— 保存后核验服务端回读的 启用/单轮上限/并发 是否与提交一致，对不上明确提示「需重启本地服务」。）
# v1.9.42（2026-09-21 00:23 · 修「有 PDF 却図图标灰」：根因 = v1.8.3 起 PDF 下载移除了「列表図标」门禁，于是出现「有 PDF 但 has_floorplan=0」（查询页「図」图标灰掉，勇哥反馈「带 PDF 的中间那个应该亮」）。修法：① store 落库层加不变式「有 PDF ⇒ has_floorplan=1」（upsert_property/upsert_many，且库里已有 PDF 时防被打回 0）；② 历史回填 362 条（有 PDF 但 has_floorplan=0 → 1）。非显示 bug，是取数/回写缺口。）
# v1.9.41（2026-09-21 00:15 · 查询页价格行新增「详情」按钮：详细/简洁两种模式的价格右侧（PDF 标签之后）各加一个蓝色「详情页面」按钮，直达 /p/编号 详情页（target=_blank，不丢查询结果；stopPropagation 防误触展开）。新增 .qbtn-detail 样式。）
# v1.9.40（2026-09-20 23:43 · ① 线上详情页「重新生成」按 has_pdf 置灰——无落盘 PDF 时禁用+写清原因，根治线上点了必 404；按钮初始文案补齐 ② 线上只读加固：/api/ai/settings、/api/ai/generate 纳入 PUBLIC_HIDDEN_APIS，api_ai_run 加 PUBLIC 兜底，启停调度加 not PUBLIC 双保险 ③ 设置页保存后改拉完整状态回显（修「当前规则」文字被刷空）④ 正式生成：跑起来禁用按钮、跑完恢复、刷新页面后恢复进度轮询）
# v1.9.39（2026-09-20 22:24 · 设置页新增「AI 读取时间」卡片：① 自动生成调度（启用/时段/单轮上限/并发/强制重跑，仿更新方式，保存即启停 AIScheduler）② 正式生成（按 PDF 落盘日期圈定 + 仅当天下载 + 强制重跑，后台批量跑 AI 抽取并实时进度轮询，仿下载条件）。后端：/api/ai/settings 扩收 schedule_ai、/api/ai/schedule/status、/api/ai/generate、/api/ai/generate/status；AIScheduler 抽出 _run_batch + 新增 generate() 复用管道。）
# v1.9.37（2026-09-20 20:57 · UI 调整：详情页新增「PDF 文件生成出来的内容」蓝色区——位于「详细信息」下方、蓝色字、固定 5 列平铺、与「详细信息」值相同则去重不显示；「重新生成」按钮迁入「详细信息」标题行最右）
# v1.9.36（2026-09-20 17:45 · 加列 Release 清单：把 PDF 有/投资有用但原 60 列缺的字段补齐）
#   【需求·勇哥】发版前体检发现截图红框字段与原 60 列缺口；要求"按正常情况加、合并一次版发过去"。
#     新增 11 列（60→71）：ペット（飼育可）[勇哥点名必加]、権利形態、前面道路、セキュリティ、
#     駐輪場・バイク置場、エレベーター、建蔽率、容積率、地目、バルコニー方向、リフォーム履歴。
#   【① 抽取覆盖】pdf_local_extract.RULES + ocr_local.TARGET 各加 11 条；
#     ocr_local.BOUNDARY 加 方角/方位/バルコニー方向/ペット/接道/前面道路/セキュリティ/駐輪場/エレベーター/建蔽率/リフォーム/改装 防跨标签黏连。
#   【② 质量闸门】ai_quality.check 新增 11 列宽松闸门：残片/长度拦截 + 枚举关键词放行（宁缺勿滥）。
#   【③ 字段字典】ai_field_meta 经 _diag_seed_new_cols.py 幂等 INSERT 11 列（seq 61-71，按 group 归位）；
#     import_ai_pdf_excel._EXTRA_META 同步补 11 列，保证将来 Excel re-import 不丢。
#   【④ 防冲突】deploy 工具按 directory（非 appId）定位目标 app，冲突根因在「directory→app 映射」。
#     已查实 osaka-house-publish 仅绑定当前站 wbapp_C65E82hYRMCjxjcwW2Ph1E；同名第二 app
#     wbapp_L8M4tyzslRfAA0IWoy29Iz 不共享该目录。防冲突 = 发布前在 WorkBuddy「设置—数据管理—
#     发布的应用」确认目录只绑当前站 + 单一权威源=本地 osaka-mvp(git) + 发布后 /api/ping+版本号(cache-buster) 健康检查。
#   【验证】3 PDF 实跑（300139241123/300140713692/300139229454）新列命中与质量复核（见 _diag_batch3.py / 验证报告）。
# ============================================================================
# v1.9.35（2026-09-20 18:30 · PRD-25 扩字段：点击任意 PDF 都能尽量多、尽量准地生成 AI 数据）
#   【需求·勇哥】"以 300139241123 为例，AI 表没有但 PDF 能读到的（具体地址+区/地图/更详细地铁站/
#     构造/阳台面积/管理体制/駐車場/現況/設備条件·建物引渡/会所·区会社居住所/注意事项）都要加进去，越多越好；
#     点任意 PDF 图片都能生成，尽量完善、保证准确。"
#   【① 根因·跨路径择优合并】原 _merge_raw 是「先到者胜」，且 L2(OCR文字层切分) 覆盖 L1(正则) 好值——
#     实测把 L1 正确的「管理会社に全部委託管理方式」覆盖成 L2 的「勤」、把正确 駐車場=空無 覆盖成免责长串。
#     改为 _val_score 打分（过闸=3/模糊=2/特殊=1/驳回=0），逐列取高分值、同分取更长 → L1/L2 优势互补。
#   【② 质量闸门放宽度量口径】バルコニー 接受 テラス/ルーフバルコニー/サンルーム（合法阳台表述，原被拒）；
#     設備・条件 阈值 4→2（接受「仕様」这类短值）；会社住所 长度 42→60 且允许〒与地址跨换行。
#   【③ 补缺失规则】RULES/TARGET 新增 所在地/最寄駅1/共用施設；并修碎片化版式：
#     所在地 标签是「所在」且地址在下一行；構造 标签是「構造等」；最寄駅1 锚定「交通」到行首
#     （避开「国土交通大臣」误命中）+ 兜底抓「铁路公司+駅+徒歩/バス」；会社住所 跨换行锚定都道府県。
#   【④ 优先列强制 OCR 补全】run_one 新增 IMPORTANT_COLS（勇哥点名的 12 列）+ ocr_enrich_important 开关：
#     详情页手动抽取时，只要优先列有缺失就强制跑本地 OCR（仍先本地、绝不走云端，守 D1）。
#   【⑤ 主库上下文喂闸门】run_one 新增 main_store 参数，从主库取 専有面積㎡ 喂给质量闸门，
#     让 専有面積（坪）勾稽字段能过闸（300139241123 实测 47.64 坪过闸）。
#   【⑥ 新增 共用施設（会所）列】field_meta 增第 60 列（group=building），live 库已 INSERT、
#     import_ai_pdf_excel._EXTRA_META 兜底（re-import 不丢）；线上同步随 ai_pdf_store.db 走。
#   【验证·300139241123】过闸字段 7 → 19；勇哥 12 个优先列命中 11（仅 建物引渡 因「時期相談」被
#     「現況 居住中」行拆散而漏抓，属该 PDF 极端碎片化，宁可缺失不误填）；其余 PDF 批量 L1 回归无污染。
#   ⚠ 8 门禁：① grep 版本证伪无 ② locator 不适用（纯后端）③ 真机 e2e 待勇哥本地 8765 重启复验
#     ④ 不适用 ⑤ 无待复核标签 ⑥ 发布后健康检查待勇哥确认 ⑦ director_audit 已跑 ⑧ 本 PRD 教训已补。
# ============================================================================
# v1.9.34（2026-09-20 16:36 · 启动器加固：serve.py 端口占用改为「占用即退出」，根治互杀断服）
#   【需求·勇哥】"自己断、没操作却报错要按继续"——排查 server.log 锁定根因：
#     serve.py 原逻辑在端口被占用时用 PowerShell 杀掉占用进程再启动；一旦系统存在多个启动入口
#     （WorkBuddy 运行面板 / Windows 计划任务 / 手动），它们轮流触发这段代码互相杀，
#     表现为「无操作却自己断」。今日 09-20 日志出现 ≥6 次「已清理端口」即为互杀痕迹。
#   【修复】serve.py：① 删除 _free_port_via_powershell() 杀进程死代码；② 端口占用分支改为
#     友好提示「已有实例在跑、本次不重复拉起也不杀旧实例」并 return 0（正常退出）。
#     实测（模拟端口占用）：serve.py 退出码 0、输出『已被占用』、且占用进程未被杀 → 互杀已根治。
#   【注意】改完必须重启本地 8765 才生效（运行中进程是旧代码）；重启前先手动结束旧实例。
#   【验证】py_compile OK + 端到端模拟占用测试（占用进程存活、退出码 0）。
# ============================================================================
# v1.9.33（2026-09-20 16:10 · 修「点按钮只出 3 个垃圾字段」——min_fields=3 短路 OCR）
#   【根因·真机 300140580336 取证】图形型 PDF（1 页 28 图、文字层仅 1641 字符且碎片化）
#     L1 正则只出 3 个残片字段（含 '担当者連絡先=確認）'）；因 min_fields=3 恰好达标 →
#     ai_pipeline 判定 local_hit=True → **OCR 与云端兜底全被短路** → 卡片近空、评分 0。
#   实测反证：本机 Tesseract 可用，OCR 该 PDF 4.9s 读出 専有面積75.90㎡/所在地/所有権/総戸数135戸/
#     築年月2023年2月/管理費15,290円/合計28,240円… 一大批真字段 → 本该 OCR 却被阈值挡在门外。
#   【P0-1 OCR 触发条件】run_one 触发条件 = `字段数 < min_fields(=8)` 兜底——图形型 PDF 文字层只出
#     个位数字段必触发 OCR（300140580336 实测 L1 仅 3 字段 → 走 OCR 读出 専有面積/所在地/管理費等真字段）。
#     仅详情页手动路径传 key_gate=True（批量/调度不传，免得全量 OCR 拖垮夜间窗口）。
#   【P0-2 阈值】min_fields 3 → 8（config.yaml + core/config.py 默认值同步），杜绝垃圾字段蒙混过关。
#   【P0-3 严闸门】ai_quality.check 加两条通用前检：① 悬空右括号残片（有 ')' 无 '('）② 无实义字符
#     （纯符号）→ 实测拦掉 '確認）' 与 '_-__、、、' 这类 OCR 残片。
#   【P1 GAP-1】detail.html runAI 点击**立即**显示「稍等… 0s」（原先等 1s tick，秒出场景看不到）。
#   【验证】py_compile + node --check + 真机复验待勇哥本地 8765。
#   ⚠ 遗留：RULES 未覆盖 basic/price/transit/layout/building 核心列（59 列只覆盖约 24 列），
#     OCR 出结果后仍填不满卡片 → 下一步「扩 RULES」另排（勇哥拍板）。
# ============================================================================
# v1.9.32（2026-09-20 15:30 · MVP 操作员视角上线：补 GAP-1 / GAP-2 两处纯前端）
#   【需求·勇哥】MVP 以最轻量方式先行：线下详情页手动触发 AI 生成，线上只读展示。
#   【GAP-1 等待秒数】runAI 启动 setInterval 每秒更新「稍等… Ns」（i18n 新增 detail.ai_interpret_wait
#     四语），fetch 返回/异常 clearInterval，进入真正读取/结果展示。
#   【GAP-2 人工修改蓝字】style.css 加 .ai-edited{color:#3b6fd4;font-weight:600}；
#     paintAI 渲染 .dv 时读 f.edited 加 ai-edited（编辑优先于红/黄底）；
#     aiBindEdit 保存成功(ok)给 el 加 ai-edited、失败去蓝加红。后端 edit_field 已逐字段留痕 edited=True，
#     本改动纯前端、离线安全、可回退，不碰后端/线上约束。
#   【验证】node --check 校验内联 JS 语法；grep 三处落位；真机 e2e 待勇哥本地 8765 复验。
# ============================================================================
# v1.9.31（2026-09-20 14:18 · PRD-25 落地：①「AI 结构」→「AI 解读」全链路重命名 ② 修 editor 留痕 ③ 设置项手动选 AI 读取種目）
#   【需求·勇哥】"AI 解读（原 AI 结构）将来要可编辑、可手动选择哪些種目走 AI 读取"；吸收豆包评审 v1.2.0。
#   【① 重命名】对外命名统一「AI 解读」：路由 /api/ai-structure → /api/ai-interpret（get/edit/edits 三件）、
#     detail.html 卡片、i18n 四语标题（AI 解读 / AI 解讀 / AI Interpretation / AI 解読）、style.css 注释同步；
#     后端表 ai_structure 与模块 ai_structure_store.py 为内部实现名，按 PRD 保留不改。
#   【② editor 留痕修复】原 /edit 用 getattr(request,"user",None) 取操作人（Flask 无此属性）→ editor 恒空，
#     卡死「员工蓝字+留痕」。改为 _current_user()["username"]，登录态下 edit 落 ai_edit_log.editor=真实员工。
#   【③ 设置项】config.ai_read_scope（顶层键，避开 ai 私密块写不回的坑）：空=全部種目都读；非空=只对勾选種目走 AI 读取（api_ai_run 拦截未勾选種目，
#     返回 400 提示去设置页勾选）。collect.html 新增「AI 读取范围」卡片（売一戸建/売マンション/売土地 三勾选），
#     调 POST /api/ai/settings 写回 config.yaml（白名单键，不碰凭据）。
#   【验证】tools/_diag_ai_interpret.py 独立 test_client + 临时库副本：旧路由 404 / 新路由 200 / editor=真实员工 /
#     详情页「AI 解读」生效 / /api/ai/settings 持久化 / read_kinds 拦截未勾选種目，零污染真实数据。
# ============================================================================
# v1.9.30（2026-09-20 12:55 · /files 图纸列表收缩成一页 + 关键词查找；/account 加设密入口）
#   【需求·勇哥】"在 /account 页面里增加一个设置账号密码的按键；/files 收缩成一页，
#     多的可以去在新增的查找里去找，查找框是不限条件关键词搜索，关键词是房子查询里的
#     基本包包含的内容。"
#   【① /files 收缩】原来 3229 个 PDF 全量平铺成一张超长表（翻不动、也找不到想要的那套）。
#     现在默认**只列最近 PDF_FILES_LIMIT(50) 个**（一屏看完），页面高度不再被数据量决定。
#   【② /files 关键词查找】新增 GET /api/files/search：不限条件、一个框搞定。口径与
#     「房源查询」的关键词**一字不差**（PDF_SEARCH_COLS 与 store.search() 的 text_cols 同源）：
#       地址 / 楼名 / 物件番号 / 駅・沿線 / 間取り / 区 / 種目 / 築年月 / 详情；
#       空格分隔多词＝词间 AND（都要命中），词内字段 OR（任一中即算）。
#     另补一条：**文件名直接命中**也算 → 连主库里没有的遗留 PDF（约 300 个）也能靠番号翻出来。
#     结果行额外带出该 PDF 对应的**房源基本信息**（楼名 ｜ 地址 ｜ 間取り ｜ 価格）——
#     "按房子找图纸"这件事应当在结果里就看得见，而不是只给一个文件名让人猜。
#     实现细节：只查 7 列（**不 SELECT \***，detail_json 太大会拖垮响应）、按 400 一批占位符、
#     LIKE 通配符转义（% / _ 按字面匹配）。
#   【③ /account 设密入口】本页管的是 REINS 抓数账号，员工登录本系统的账号在 /staff，
#     两者以前没有任何入口提示、极易混淆。现在加一个「设置账号密码」按钮直跳 /staff
#     （勇哥确认：本页只做入口，不在此页设密）。
#   【i18n】新增 files.q / files.th_house / files.house_unknown / account.setpw 等 key，
#     ZH + zh-TW/en/ja 四语齐套（JS 动态串必须进 ZH，否则裸 key 上屏）。
# ============================================================================
# v1.9.29（2026-09-20 12:30 · /staff 新增「复制现有密码」按钮：待改密码可随时重发）
#   【需求·勇哥】"在重置随机码旁边，加一个复制现有密码的功能按钮。线上线下在改动后第一时间同步。"
#   【方案·路线 A（勇哥拍板）】给 accounts 加一个仅管理员可见的 current_code 字段：
#     生成随机码（新建 / 重置）时写入明文；员工首次登录设密、改密、被禁用、或线上回流认领
#     （adopt_remote）时自动清空。/staff 表格行新增「复制现有密码」按钮，点一下调
#     GET /api/accounts/<u>/code 取当前明文码并弹窗复制 → 不必重置就能重发，旧码不失效。
#   【同步】"改动后第一时间同步"已由 v1.9.28 的自动上云覆盖（重置/改密都秒级推送线上）。
#     current_code 仅本地管理员会话可见，不进种子、不进 /api/accounts/state（export_seed/
#     export_state 均只选指定列），明文码永不离开本地。
#   【迁移】init() 里 _ensure_current_code() 用 PRAGMA 探测后 ALTER 加列，本地持久库与线上
#     临时库启动都跑，保证两条路的 UPDATE 不缺列。
#   【遗留·可接受】v1.9.29 之前已存在、且仍未激活（待改密）的旧账号，其明文码升级前未记录
#     （GetCurrentCode 返回 no_code），需在 /staff 重置一次才能补录并可复制；新账号即时可用。
# ============================================================================
# v1.9.28（2026-09-20 11:40 · 账号双向同步：重置即刻上云 + 线上自设密码回流 /staff）
#   【需求·勇哥】"除确实需要人为操作部分，尽可能不要让我操作"；"当我这边密码改掉以后，
#     信息应该传给线上，线下就能知道这个密码已经被改过了"。
#   【v1.9.27 的遗留】自动导出只解决了"导出"，没解决"上云"—— 线上仍要靠**重新发布**
#     （容器重建）才能拿到新种子。于是同一个坑第二次出现：jiaojiao 11:25 重置 → 拿到新码
#     去线上登录 → 「密码错误」（线上还是 10:45 那版旧哈希）。
#   【本次除根】复用线上已有的 /api/ingest 机器对机器通道（同一个 publish.ingest_token），
#     新增两个接口（都要求 X-Publish-Token，且已在 _access_guard 里按 /api/ingest 同样的
#     理由豁免登录守卫）：
#       · POST /api/accounts/seed-sync  本地 → 线上：推种子（force_users 定向强制覆盖被改的账号）
#       · GET  /api/accounts/state      线上 → 本地：读账户状态，供回流
#     配套：core/account_sync.py（网络收发、内容指纹去重、水位线 account_sync 表）、
#     accounts.adopt_remote()（回流合并规则）、accounts.upsert_seed(force_users=...)、
#     发布定时器每轮兜底补推、serve.py 启动时自动对齐一次、/staff 顶部显示同步状态 +
#     重置弹窗直接标注"线上是否已生效" + 「立即同步」按钮。
#   【回流铁律】只采纳"线上 cleared=1 且本地 cleared=0 且本地该行自上次推送后没被动过"，
#     绝不猜测、绝不批量覆盖；两侧都已改密而哈希不同 → 只报冲突，保持本地不动。
#   【安全】只传哈希，明文永不经过该通道；任何失败只记日志/水位线，绝不阻断本地操作。
# ============================================================================
# v1.9.27（2026-09-20 11:06 · 账号变更后自动导出种子 → 根除"重置完忘了导出"）
#   【背景·勇哥】连续两次同一故障：在 /staff 重置员工随机码 → 线上登录报「密码错误」。
#     根因不在代码逻辑，而在流程缺口：/staff 重置只改本地主库，**不导出 seed**；
#     而线上账号的唯一来源就是 app_local/data/accounts.seed.json。重置后若忘记
#     "重新导出 + 重新发布"，线上拿到的仍是旧哈希 → 新码必然登不上。
#   【本次除根】core/accounts.py 里 export_seed() 早就写好了，但**全项目无人调用**，
#     所以"导出"一直是个靠人记的手工动作。现将其接进流程：新增 _auto_export_seed()，
#     任何一次账户写操作成功后自动导出最新种子（仍只含哈希、绝不落明文）到
#     ① 本地 data/accounts.seed.json ② 发布工程 app_local/data/accounts.seed.json（探测到即镜像）。
#   【挂载点·5 处】改密(change-pwd) / 首次设密(set-pwd) / 新建员工(POST /api/accounts) /
#     重置随机码(reset_code，最关键) / 改角色·禁用(PATCH)。
#   【安全】导出失败只记日志、绝不阻断主操作；线上 PUBLIC 模式不跑 /staff，故不会触发。
#   【实测】重导后主库 vs seed 哈希 14/14 全一致；boge 新码校验通过。
# ============================================================================
# v1.9.26（2026-09-20 11:05 · 登录页/改密框「显示明文」眼睛按钮）
#   【需求·勇哥】"我需要在线上和线下输入密码栏旁边有一个可以显示明码的按键"。
#     线上(发布版) + 本地(8765) 两个登录页都要有。
#   【改动】纯前端、零接口改动：
#     ① login.html：密码框旁加「👁 显示」切换按钮（登录表单 + 首次强制设密弹窗
#        的 new_password / confirm_password 共 3 个密码框全部覆盖）；
#     ② base.html：右上角「改密」弹窗的 cpOld / cpNew 两个密码框同样加上；
#     ③ style.css：新增 .pwd-wrap / .pwd-toggle 一组样式（相对定位 + 右侧内嵌图标），
#        按钮用 type="button" 防止误触发表单提交；输入框 padding-right 让位给按钮。
#   【安全取舍】按钮只切换 input.type（password ⇄ text），不改动密码值、不上报、
#     不写入任何日志/存储；默认仍是 password 密文态。
#   【实测】待本地 8765 目视 + 发布后线上目视。
# ============================================================================
# v1.9.25（2026-09-20 03:50 · PDF 的 AI 识别结果入库 → 详情页「AI 结构」）
#   【需求·勇哥】桌面《大阪房源PDF数据总表.xlsx》（2026-09-19 全量 AI 识别，193 条）
#     ① 数据写进**独立库**（不与其他数据直接相关，单独一个文件）→ data/ai_pdf_store.db
#     ② 在每套房子的详情页里展示为「AI 结构」
#     ③ 异常数值标注：存疑=红字；特别存疑=红字加黄底
#     ④ 字段可编辑、显示遵循「有则显示，无则不显」
#     ⑤ 顶端生成 AI 结论：六维雷达图打分 + 约 200 字卖点描述
#   【新增文件】core/ai_structure_store.py（独立库+编辑审计）、core/ai_score.py
#     （六维打分与结论生成）、tools/import_ai_pdf_excel.py（Excel→独立库导入）
#   【接口】GET /api/ai-structure/<番号>、POST /api/ai-structure/<番号>/edit、
#     GET /api/ai-structure/<番号>/edits
#   【前端】detail.html 的 aiCard 由「AI 解读」升级为「AI 结构」：
#     雷达图(SVG 手绘零依赖)+综合分 → 约200字结论 → 存疑提示 → 分组字段(可点击编辑)
#     样式新增 .ai-* 一组（style.css），i18n 四语言补齐 detail.ai_* 新键。
#   【为何移除「重新生成」按钮】旧按钮走 /api/ai/<番号>/run（现场调模型跑单份 PDF，
#     写主库 ai_extractions，实测全库仅 1 行）；新版卡片消费独立库的批量识别成品，
#     两者数据源不同，留着按钮会"点了没反应"。批量识别重跑请执行
#     python tools/import_ai_pdf_excel.py。
#   【实测】193 条房源番号与主库 100% 匹配（0 丢失），结论中位 209 字、
#     零 OCR 杂片；异常 126 处 danger + 231 处 warn。
# ============================================================================
# v1.9.24（2026-09-19 16:53 · 详情页章节对调 R-UI-1：房源図面 PDF 上移、AI 解读下移）
#   【需求·PRD-24 R-UI-1·勇哥】"AI 解读放在房源図面 PDF 下面"。
#   【改动】detail.html 仅两块模板对调（纯静态、零接口/JS 逻辑改动）：
#     新顺序 = 详细信息 → 房源図面 PDF → AI 解读 → 变更历史。
#     ① pdfCard（v1.8.6 页内 iframe 展示，{% if row.pdf_path %} 包裹）上移到 aiCard 之前；
#     ② aiCard（v1.7.4 AI 解读，/api/ai/<番号> 异步填充）下移到 pdfCard 之后；
#     ③ 同步修正两处位置注释，消除与上版顺序矛盾的旧注释。
#   【门禁】grep 实证：pdfCard(:106) 现已在 aiCard(:118) 之前、dinfo(:90) 之后、变更历史(:129) 之前；
#     loadAI/aiRegenerate 按 id 取元素（:249/:284/:290），无兄弟节点遍历依赖，对调零副作用。
#     无 PDF 房源 pdfCard 不渲染，AI 解读自然紧跟详细信息，为新顺序的预期降级。
# v1.9.23（2026-09-19 16:30 · 查询页单行重设计：同类聚拢 + 重要向前 + 未展开双按钮）
#   【需求·勇哥 16:26】"查询页里的单行重新设计，由 UI 经理为主去思考。同类放一起，重要的向前放。"
#     并承接 PRD-24 的 R-UI-2/R-UI-3（未展开即有两按钮；四行收一行）。
#   【设计·UI 经理】整行 5 大区，左→右重要度递减，同类聚拢：
#     ① qc-ops 操作与状态（加入对比 + 新增/变更/待补 + 取引態様専任/専属）
#     ② qc-click 位置与价格（地址·楼名 + 区 + 价格/降价 + ㎡単価/坪単価）—— 点此区展开详情
#     ③ qc-spec 房型与规模（面積 · 間取り · 駅）
#     ④ qc-meta 物件属性（種別 · 所在階/階建 · 築年月）
#     ⑤ qc-aux 辅助与入口（番号+REINS検索 · 図/照/画像 · 取得日 · 【本地PDF】【详情页】两按钮）
#   【关键实现】① 单行 nowrap + 横向滚动（375px 不换行错版）；② 两按钮未展开即常驻（覆盖 R-UI-2），
#     无 PDF 时「本地 PDF」置灰（位置不跳动）；③ 按钮各自 stopPropagation，不误触展开；
#     ④ 单行设为查询页默认视图（COMPACT=true），「详细⇄简洁」仍可切回多行；⑤ i18n 增 row.no_pdf（四语言）。
#   【门禁】node 渲染 QA：有PDF/降价/新增/専任 与 无PDF/待补 两样例均结构齐全、零异常。
# v1.9.22（2026-09-19 14:50 · 番号検索统一：同窗口新标签页 + 复制编号 + 明确提示弹窗）
#   【现象·勇哥反馈】① 本地原"后端代填代搜→本页弹窗截图"非交互（不能点详情/下 PDF）；
#     ② 线上点番号検索=同浏览器新标签页进 REINS 検索页、但开完"卡住"没任何提示
#     （旧 bukkenLiveNote 函数根本没定义 → 调用抛错静默失败）。
#   【根因】线上 bukkenOpenDirect 复制+开窗后调用未定义的 window.bukkenLiveNote →
#     异常被吞、用户只看到开了个标签页、不知下一步；本地仍走后端 headless 截图弹窗（非交互）。
#   【修复·统一】① 本地/线上不再区分：点击即在【当前浏览器同窗口】开新标签页进 REINS 検索页
#     （window.open(url,'_blank')，浏览器默认新标签、非独立窗口）；② 同步复制编号到剪贴板；
#     ③ 本页弹窗 bukkenLiveNote 明确告知"编号已复制 → 登录(如需) → 粘贴 → 点検索"三步；
#     ④ base.html 注入 window.REINS_BUKKEN_SEARCH_URL（取自 config.site.bukken_search_url 单一来源）；
#     ⑤ 丢弃 v1.9.22-WIP 的 keep_open 独立 Edge 窗口方案（勇哥明确不要独立浏览器）。
#   【为什么不能自动填号】REINS 会话在后端 Playwright 浏览器里，当前浏览器无该会话，
#     跨域 JS 无法代填 REINS 输入框（物理限制）→ 采用"复制+开页+提示"。
#   【门禁】py_compile 全绿 + node --check 全绿 + bukkenLiveNote 已定义（grep 证伪）。
# v1.9.21（2026-09-19 11:30 · 上传设置面板三项修复 + 搜索框自动填充根治）
#   【现象·勇哥反馈】① 点上传后日志非常不全、面板像"死掉"（状态恒「—」、按钮恒「暂停」）；
#     ② 希望按设置的周期（如每 10 分钟）自动上传；③ 暂停/启动按钮应能互切；
#     ④ 搜索关键词框被浏览器自动填入登录账号「yongge」。
#   【根因·四条】A `/api/publish/status` 只挂 @app.get，前端却用 postJSON(POST) 调 → 405 →
#     前端静默 catch → 面板永不刷新（致命统一根因）；B 自动上传定时器(PUB_LOOP)的日志只进
#     server.log，从不回传上传面板，看不到"定时上传"痕迹；C 暂停/启动只按轮询(≤5s)翻转，有滞后；
#     D 搜索框无 autocomplete 控制，浏览器把同源登录账号当用户名自动填入。
#   【修复】① 路由改 GET+POST；② 新增 publisher.PUB_LOG 共享缓冲，定时器日志同时写它，
#     /api/publish/status 合并回传（手动+定时全貌）；③ pub_log 面板常驻可见 + 暂停/启动乐观翻转
#     + 显式"每 N 分钟自动上传"提示；④ 搜索表单包 <form autocomplete="off"> + 关键词框 name/autocomplete。
#   【门禁】py_compile 全绿 + node --check 全绿 + i18n 四语言新键齐全 + 本地冒烟(后续可加)。
# v1.9.20（2026-09-19 10:43 · 番号検索改版：本页弹窗 + 线上不另开窗口 + 真键盘输入）
#   【现象·真机 3 条反馈】勇哥 10:16–10:27 连点 7 次番号検索截图：
#     ① 本地点按钮会"真的弹出一个 Edge 窗口，看到结果几秒就被关掉"（旧 headless:false）；
#     ② 线上（微软 Edge）点按钮"页面跳不过去、编号没录入"；
#     ③ server.log 显示号码回读一致、検索也点了，REINS 却回「検索結果が0件」——
#        勇哥最初判断"给的数据不对"，但日志证明号码真录进去了，是输入方式问题。
#   【根因·三条】A 旧版 `config.yaml browser.headless:false` → 后端 Playwright 真弹窗口 + 搜完
#     browser.close() 关掉；B 线上站无 REINS 会话/无本机浏览器，后端必失败 → 前端在
#     fetch().then() 里才 window.open（用户手势已丢）→ Edge 弹窗拦截+剪贴板拒双杀；
#     C 番号框是 PrimeVue p-textbox-type-digit 数字掩码组件，旧版 fill() 绕开键盘事件 →
#       掩码组件不写框架状态 → REINS 拿空条件搜 → 0 件（回读一致也只能证 DOM 有值，证不了框架认账）。
#   【修复·前端】① base.html 注入 window.OSAKA_PUBLIC；② 番号検索改两条路：
#     线上站（OSAKA_PUBLIC）→ **点击手势内同步**复制番号 + window.open REINS 検索页
#     （不再在 fetch 后开窗，Edge 不再拦）；本地站 → fetch 后端无头搜索，结果回**本页弹窗**
#     （bukkenShowResult/bukkenFail，永不自关、用户点✕才关）；③ 失败/0件统一 bukkenFail 弹窗
#     （复制/打开都是用户主动点，永不被拦），不再自动 window.open。
#   【修复·后端】① crawler.reins_bukken_search **强制 headless=True**（结果只回截图，不弹窗口）；
#     ② 番号改用 tb.press_sequentially 逐键真键盘输入（掩码组件认账）；③ 结果页用 v1.9.5 的
#     _read_zero_note 自证 0 件，明确回 ok:false+zero（不再把 0件截图当成功）；
#     ④ 路由 /api/reins/bukken_search 业务性失败回 200+ok:false（旧版 502 会被网关包 HTML 害前端炸）；
#     ⑤ 线上站该接口进 PUBLIC_HIDDEN_APIS → 403（前端按 OSAKA_PUBLIC 直接走复制+开窗，不再 500）。
#   【门禁】① 历史结论已查（v1.9.5 的 _read_zero_note 复用，非新造轮子）；
#     ③ 真机 e2e 待勇哥复验：本地点按钮=本页弹窗截图（不弹窗口、不自动关）、线上点按钮=同浏览器
#       新标签打开 REINS 検索页且番号已复制；④ 失败弹窗按钮 window.open 在用户点击手势内，不被拦；
#     ⑥ 发布后健康检查：线上 GET /search 内能 js 注入 OSAKA_PUBLIC=true；本地 8765 点按钮弹窗出截图。
#   【不变量·保留】三处按钮（列表卡片/详情页/对比栏）仍统一汇到 openBukkenSearch → bukkenSearchRun，
#     「逻辑一致」保持；REINS_BUKKEN_SEARCH_URL 仍来自 config.site.bukken_search_url（缺时回退常量）。
# ============================================================================
# v1.9.19（2026-09-19 10:22 · 🔴 修复「上传到线上」被强制登录守卫拦死 = 线上数据停更）
#   【现象】勇哥导出的启动日志：06:35 起每轮推送线上均失败，稳定报
#     `HTTP 401: {"message":"未登录","status":"error"}`；线上站「今天 0 条 / 最近有数据 09-18」，
#     而本地库已 3942 行、当日新增 158 套（reg 22 + chg 136）→ **下载正常、上传断了**。
#   【根因】v1.9.14 强制登录（accounts.auth_enabled=true）上线后，`_access_guard` 把
#     **机器对机器**端点 `/api/ingest` 一起拦了：上传器只带 `X-Publish-Token`、没有浏览器
#     账户会话 → 守卫先于业务校验把它 401 掉；且/api/*分支只回一句「未登录」，
#     与 api_ingest 自己那句「令牌不对（401）」不同 —— 这正是本次取证定位的抓手。
#   【修复】① web/app.py `_access_guard` 放行 `/api/ingest`（它自带令牌校验，等于多一层锁；
#     页面仍照旧跳 /login，未放开任何页面）；
#     ② core/publisher.py `post_rows` 令牌键名兜底：`publish.token` → `publish.ingest_token`
#     （同一密钥历史上有两个键名，发送读前者、线上只认后者，只改一个就会**静默 401**）。
#   【安全性论证】本豁免只影响带对令牌的 POST；无令牌/错令牌仍被 api_ingest 401。
#     实测：本地与线上 `ingest_token` 同值（sha8=2d7fd02b），故**无需改任何凭据**，只差这一关。
#   【门禁】① 历史结论已查；③ 真机 e2e 待勇哥本地 8765 复验（server.log 出现「✓ 上传完成：N 行」）；
#     ④ 令牌唯一性由 api_ingest 常量时间比对承担；⑥ 发布后健康检查 = 线上 /api/ingest 无令牌
#     时应答「令牌不对（401）」而非「未登录」 + 带对令牌推一次应回 200 且线上行数追上本地。
# ============================================================================
# ============================================================================
# v1.9.10（2026-09-18 20:27 · §13 UI 字段名统一加粗 + R8 番号検索选择器落地）
#   - §13 UI-1：查询页筛选项 label（#filterForm .field > span）+ 对比页字段名列
#     （.cmptable .cl）字重统一为 700；详情页 .df .dk 自 v1.8.6 已是 700（确认无需改）。
#     纯 CSS 字重变化，字号/颜色/布局不变；抬 VERSION 换 ?v= 强刷。
#   - R8 番号検索：config.yaml 填 bukken_search_inputs/button（真机 DOM 取证）；
#     crawler.reins_bukken_search 加「填完回读校验（不等即 ok:false）」+「SPA 补等 3s」。
#   - 门禁③真机 e2e 待勇哥双击重启 8765 复验（server.log 回读标记）。
# ============================================================================
# v1.9.5（2026-09-18 · 抓取链路：0 件页识别 + 種目无关的第 2 计数源；含 v1.9.4 止血）
#   - v1.9.4 止血：_read_total 默认上限 60–90s → 18s（戸建/マンション 实测 <2s 即出计数区，
#     拉长上限只对异常组空等；タウン 类曾 65–85s×4/轮 纯浪费）。
#   - v1.9.5 真根因（PRD-19 R4，勇哥真机 DOM 实证）：REINS「検索結果が0件です」页
#     **既没有计数区 div.text-dark.ml-3、也没有结果表 div.p-table-body-row**。
#     所以「等不到计数区」= 当日确実 0 件，不是渲染慢、更不是漏抓。
#     ① 新增 _read_zero_note：认 div.p-note(-danger) 里含「0件」的告示 → 立即定 `0件`（<1s）；
#     ② 新增 _read_tab_count：认 tab 见出し「売一戸建(9件)」的件数（種目无关的第 2 计数源，
#        多 tab 求和，两种渲染解释下都等于该種別总数）；
#     ③ 宽限 4s 后用 tab 件数、8s 后用首頁行数兜底，最后才记「未知」（18s 上界不变）。
#     保留 4s 宽限：戸建/マンション 走主选择器「<2s 即返」的既有行为不被兜底拖慢。
#   - 顺手堵一个隐患：v1.9.5 让「0 件」变成**成功**，若不设防，0 件组会进「下架作用域」
#     （_live_items 的 succeeded_subtypes），mark_delisted 会把库里该種目在架房源
#     连续 N 轮标 is_active=0（2026-09-15 误标 1189 条事故同型）→ 已加
#     `_parse_total(total_txt) != 0` 守卫，0 件组**不进**作用域（宁可漏判，绝不误杀）。
#   - 新增取证日志：線上报告有 N 件但本组 0 条入账 → 显式 ⚠ 提示 result_rows 疑不中。
#   - 门禁：② 新选择器全部来自勇哥真机 DOM（非猜测）；③ 真机 e2e 待勇哥双击重启 8765 复验；
#     mock 单测 6 用例全绿（0件页/戸建页/tab兜底/行兜底/全空未知/下架守卫）；py_compile 全绿。
# ============================================================================
# v1.9.8（2026-09-18 17:50 · P1：概览页日期同步内联补详情，主链统一两条下载链路）
#   - 勇哥拍板（对照 PRD-20 双链路差异分析）：把「行内点詳細」提回概览页主链，让详情/PDF
#     成为概览页「当日日期同步」的一等公民（与设置页「指定日期下载」完全同款路径），
#     阶段B 退化为安全网（仅补主链漏网，不再是一等依赖）。
#   - 实现（core/crawler.py sync_today_dates 翻页内循环）：每行先 store.get_property +
#     _needs_detail 比对本地详情页——有则跳过（仅刷新列表字段），无则调 _fetch_detail_inline
#     （行内点「詳細」→ 抓全字段 + 触发 PDF → go_back 回列表）内联补详情；补上的详情走
#     pipeline.ingest 落库（与设置页/阶段B 同款持久化，自动序列化 detail_json），PDF 走
#     后台 _PdfWorker（主线程不干等）。跨组累计 inlined_total 打到收尾日志 + 返回值。
#   - 用户原始口径逐条落实：①每一页都翻一次（保留 max_pages 翻页）；②检查每页的每行；
#     ③每页与本地详情页比对一次（有则不再点、无则下载）；④单页全量 vs 本地日期内对比；
#     ⑤首页面下载与检查同步做（同循环内完成）；⑥记好哪些需进详情页/下 PDF（即 _needs_detail 命中者）。
#   - 行为变化：概览页自动轮现在「当轮即完整」（落壳 + 内联补详情/PDF），不再依赖阶段B 事后兜底；
#     实测节奏约 4–6s/条（含拟人停顿），与设置页一致（"慢但稳"），非 bug。
#   - 门禁：③ 真机 e2e 待勇哥双击重启 8765 复验（看 server.log「概览页日期同步内联补详情 N 条」
#     + 前端「详情待补」清零）→ 通过即 commit + 发布（appId wbapp_C65E82hYRMCjxjcwW2Ph1E）。
# ============================================================================
# v1.9.9（2026-09-18 18:40 · 更新周期重算 + 上传独立定时器 + 日期段筛选）
#   - 需求1（截图1「时间设置没更新到下一轮」）：core/scheduler.py 加 rearm()（threading.Event），
#     _loop 分片 sleep 中检查 self._rearm → 跳出立即按新参数重算 delay/next_run_at（不跑轮次、
#     不丢排程）；web/app.py /api/schedule 保存后调 SCHED.rearm()。验收：保存设置后 ≤5 秒
#     「下一轮」时间按新参数刷新（之前只在每轮跑完瞬间算一次，热改不打断 sleep 故不更新）。
#   - 需求2（截图2「启动/暂停 + 周期 + 日期段」）：core/publisher.py 新增
#     · _scope_where 支持 date_from/date_to（闭区间；download 口径=COALESCE(last_seen_at,
#       first_seen_at)，platform 口径=reg/chg_date_iso）；与上/替代旧 month_scope。
#     · PublishLoop 独立上传线程：按 publish.interval_minutes（默认10，下限夹1）周期检查本地库，
#       有增量才推、空轮回静默（轻量、不碰 REINS）。PUBLIC 模式禁用。
#     · preview_scope：只算不发（设置页「预览会传多少」按钮用）。
#     · /api/publish/settings 扩 auto_enabled/interval_minutes/date_from/date_to 四键 + 启停 loop；
#       /api/publish/toggle（启动/暂停）、/api/publish/preview（计数）、/api/publish/status 带 loop 状态。
#     · collect.html「上传到线上」加 启动/暂停按钮 + 周期输入 + 日期段 + 预览 + 下次上传时间；
#       i18n.js 补 13 个 pub.* 键（4 语言）。
#   - 铁律：8765 进程只能勇哥双击 start_mvp.bat 重启才生效，我绝不动；PUBLIC 模式不启上传定时器。
#   - 门禁：③ 真机 e2e 待勇哥双击重启 8765 复验（看 server.log「⟳ 下一轮已按新参数重算」+
#     「☁ 上传定时器已启动」+ 设置页按钮可启停、预览出数）→ 通过即 commit + 发布。
# ============================================================================
# v1.9.7（2026-09-18 16:30 · 下载崩溃修复 + R6 日志清晰度）
#   - 修复 v1.9.6 阶段B 兜底的运行时崩溃：_backfill_via_inline 对 sqlite3.Row 调 .get()
#     触发 AttributeError（本环境 sqlite3.Row 无 .get）→ 阶段B 一进兜底就崩、0 条补上
#     （真机 server.log 16:28:54 实证「阶段B 补详情失败：AttributeError」）。改为循环内
#     r = dict(r) 转 dict 后用 .get，与全代码下标访问风格一致。
#   - R6 日志可读性（勇哥需求：下载过程日志更易看清、更全面）：
#     ① _log_session_summary 汇总框新增「阶段B 自动补详情 N 条」行（main_round_enabled=false
#        时落库 0 条但阶段B 实补了详情，此前看不出）；
#     ② _backfill_via_inline 收尾打「阶段B 兜底完成：共补详情 N 条 / 跨 K 天」汇总行；
#     ③ 阶段B 番号検索路径日志标注（番号検索）模式，便于区分两条后备路径。
#   - 下载问题在代码层彻底闭环（v1.9.6 已是正确架构，本次仅修崩溃 + 补日志）：双击重启
#     8765 吃 v1.9.7 后，自动轮阶段B 自动清 56 壳 + 后续新壳，无需填 DOM。
#   - 门禁：③ 真机 e2e 待勇哥双击重启 8765 复验（看 server.log「阶段B 兜底完成：共补详情 N 条」
#     + 前端「详情待补」清零）→ 通过即 commit + 发布（appId wbapp_C65E82hYRMCjxjcwW2Ph1E）。
# ============================================================================
# v1.9.6（2026-09-18 · 阶段B 详情漏补根因修复：番号検索开详情替代空 detail_href）
#   - 真因（勇哥日志 + 截图 + 真机 HTML 三联证）：main_round_enabled=false 时自动轮只落
#     列表壳（sync_today_dates，纯列表、无行内点詳細），详情全靠阶段B 兜底；但阶段B 候选
#     SQL 要求 detail_href 非空，而 REINS 列表行「詳細」是 <button> 无 href（真机 HTML 坐实）
#     → detail_href 全库恒空（jproperty.db 2560 在架、非空=0）→ 阶段B 每轮 0 候选 →
#     56 个新壳（中古マンション40 / 中古戸建8 / 新築戸建8）永远补不上详情/PDF。
#     今日 400 条有详情是靠白天手动「指定日期下载」（_fetch_detail_inline 行内点詳細，
#     绕开 detail_href）补的 → 证明点击退路可用，唯独阶段B 的 goto detail_href 因空 href 失效。
#   - 修复：阶段B 候选 SQL 改为「在架且 detail_json 空」选壳（不再依赖 detail_href）；
#     每条改用新增 _fetch_detail_by_no：goto 番号検索页 → 填番号 → 点検索 → 点结果行
#     「詳細」→ 解析全字段 + 下载 PDF。selectors.bukken_search_inputs/button 须由真机 DOM
#     取证填入（config.yaml 现为空）。
#   - v1.9.6 补强（兜底）：番号検索选择器未配置时，阶段B 不再空等——改按待补壳的
#     登録/変更日期分组、复用「指定日期下载」(run_round_options) 的行内点詳細路径补详情
#     （今日已实补 400 条、已证可用，且不依赖任何新选择器）。下载问题在代码层彻底闭环：
#     双击重启 8765 后自动轮阶段B 会自动清掉 56 壳 + 后续新壳，无需你填 DOM。
#     run_round_options 收尾调阶段B 传 _inner=True 防递归/重复。
#   - 门禁：② 番号検索选择器走 config.yaml（待勇哥真机取证填，非猜测；不填也有兜底）；
#     ③ 真机 e2e 待勇哥双击重启 8765 复验（沙箱禁公网、本机无法自测）；py_compile 全绿。
# ============================================================================
# v1.9.3（2026-09-18 · 推送链路修复 + D4 结果区超时修复）
#   - 推送「未知原因」静默失败根因修复（勇哥报「线上永远旧 / 待上传 758 行」）：
#     ① 真因：**publish.endpoint 从配置丢失（运行时为空）**，推送根本没发出去 —— 不是令牌过期、也不是网络。
#        线上站 osaka-house-v2 自 09-17 16:27 最后一次成功上传后，每轮都因 endpoint 空而失败。
#     ② 掩盖 bug：publisher.py post_rows 缺 endpoint 时早返回用单数 "error" 键，而 publish()
#        读复数 "errors" 键 → 键名不匹配把真实原因「没有配置线上地址」吞成「未知原因」。
#     ③ 修复：config.local.yaml 补回 endpoint=https://osaka-house-v2.app.workbuddy.host
#        （URL 由 publish_state.last_endpoint + tools/_check_online.py 双重实证，非猜测）；
#        post_rows 早返回改回 "errors" 键；publish() 调用方兜底读 error/errors 双键，绝不再吞。
#     ④ 验证（借 8765 真实网络 POST /api/publish incr）：ok=true sent=758 upserted=758
#        url=.../api/ingest → 线上站已刷新；令牌有效、网络通。last_at 更新 2026-09-18 12:25。
#   - D4（PRD-17）：结果条数区 _read_total 默认 timeout 45s，REINS 偏慢时タウン 类整组记「未知」→ 漏抓。
#     改默认 timeout_s=None → random.uniform(60,90)（慢渲染组有 60–90s 余量；快组仍即时返回，无额外延迟）。
#     原 docstring 里「None→随机 5–8s」分支因默认非 None 从未触发，现改为 60–90s 并生效。
#   - 门禁：推送修复属数据同步链路（非发布代码），已借 8765 真机端到端验证通过（门禁③⑤）；
#     D4 属抓取超时控制流，未改 locator/检索结构。crawler.py/publisher.py py_compile 全绿。
#     待勇哥重启 8765 吃 v1.9.3 后端（publisher.py 修复 + crawler.py D4 才生效）；发布线上版待拍板。
# ============================================================================
# v1.9.2（2026-09-18 · 抓取链路健壮性：直录超时+重试+只跳该组不杀整轮）
#   - 勇哥实抓事故（桌面日志 10:41）：use_saved_condition=false 直录路径，
#     地区「所在地１」区块 wait_for 写死 8000ms，REINS 偏慢（同轮结果区曾等 45s）时，
#     第 4 组控件晚于 8s 渲染 → TimeoutError → 原 raise RuntimeError("中止本轮") 把当天 12 组全打死。
#   - 修复（core/crawler.py）：
#     ① 新增 _apply_geo_manual_retry()：超时 8000→25000ms；失败先 page.goto 重进検索条件入力页
#        + 重展面板重试（默认 3 次含首次）；仍失败返回 False。
#     ② 直录分支失败改抛专用 _GeoFailed（RuntimeError 子类），不再 raise 裸 RuntimeError 中止整轮。
#     ③ 三处调用方（sync_today_dates / run_round_options×2 / run_round）捕获 _GeoFailed →
#        记「跳过本组」+ continue，其余组照常跑；SessionExpired 等真问题仍向上中止保护数据。
#   - 语义澄清：地区必填底线仍成立（无地区检索恒为空，绝不静默继续），但「瞬时超时」与
#     「真改版」区分对待——前者只漏抓该组、后者在日志明确标出待人工核。
#   - 门禁：本次仅改 crawler 超时/重试控制流，未改 locator、未改地区值、未改检索条件结构、
#     未改 use_saved_condition 开关；属健壮性加固。REINS 慢渲染下的端到端验证需勇哥真机轮
#     （门禁③⑤），故**未发布**，待真机确认（详见 docs/PRD/19_抓取链路健壮性）。
# ============================================================================
# v1.9.1（2026-09-18 · 解耦阶段B：主轮关也独立补「详情+PDF」）
#   - 根因（勇哥报「一轮下载完还挂详情待补」）：v1.8.4 把 main_round 默认关（防误标下架），
#     但阶段B(详情+PDF)被一并关掉 → 降级分支只跑 sync_today_dates（列表壳），详情永不补。
#   - 修复：把阶段B 从 main_round 解耦成独立环节 `_backfill_details_pdfs`（core/crawler.py）。
#     run_round 降级分支（main_round 关）现在也会调它 → 一轮下载默认就含详情+PDF，
#     消除「详情待补」长期挂起；阶段C(全期間下架基线)仍由 main_round 门控、默认关，
#     不 reintroduce 误标下架风险。
#   - 复用 _live_items 同一套 auth + _fetch_detail machinery；href 由 sync_today_dates
#     落库到新增列 detail_href（绝对直链），补详情时直接 goto，绝不猜 URL、绝不写脏。
#   - store.py 新增 detail_href 列（PROPERTY_COLUMNS + CREATE TABLE + _ADD_COLUMNS 幂等 ALTER）。
#   - config.yaml 新增 backfill_cap=300 / backfill_max_minutes=20（可调）。
#   - ⚠ 待真机：REINS 实际补详情需登录会话+代理+维护窗口外，门禁③⑤ 由勇哥真机轮验证；
#     历史积压（detail_href 为空的旧项）需跑一次 _await_then_fix.py 全量补，或等其自然重上架。
#   - 同版含「REINS 番号検索后端登录式」（Task B）：新增 crawler.reins_bukken_search
#     + /api/reins/bukken_search 路由 + fieldmap.js 改调后端；解决真机反馈「番号没录入/没点検索/未登录」。
#     番号検索页 URL/选择器走 config.yaml（site.bukken_search_url + selectors.bukken_search_*），
#     待勇哥真实 DOM 取证后填（门禁②，绝不猜 selector）；未配置即返回明确提示。同样 待真机门禁③⑤。
# ============================================================================
# v1.9.0（2026-09-18 · 5 项修复 + VULN-01/02/03 安全加固）
#   - A 降价字段裸 HTML 真根因修复：compare.html 字段构造器 F() 旧版漏转 `opt.html`
#     → 价格/降价徽标里的 <span> 被 esc() 成文本，列表/对比页显示
#     `<span class="qrate...">↓ 2.3%</span> 1,260 万円` 原样文本。F() 现正确透传
#     html:!!opt.html，带 {html:1} 的字段走 raw（不再 esc）；v1.8.6 的 else-if 分支是空跑。
#   - B 全站「REINS 物件番号検索」按钮：search/detail/compare 三页所有物件番号旁各加
#     搜索按钮（window.bukkenBtn 委托渲染），点击走 fieldmap.openBukkenSearch ——
#     跨域限制下用 REINS 番号検索 URL + 番号参数预填 + 剪贴板兜底（自动填表不可跨域），
#     底部 toast 提示；同时消除 detail.html 内联 onclick 注入 property_no 的 XSS（VULN-03）。
#   - C 对比页开关文案统一：对比页「隐藏其他详情」按钮与查询/详情页同款
#     （id=agency-toggle / cmpToggleAgency），文案随 i18n 状态切换。
#   - D 对比栏清空复现真根因修复（勇哥复现「清空后又复活 / 删到最后一个又全出现」）：
#     两条真因 ① noOf() 每次读 ?nos= URL 旧值当主源 → setAll 复活已删项；
#     改为 _urlNosTaken 一次性采纳 URL 后只用本地栏；② cmpNotify 防重入静默丢弃空态
#     通知 → UI 卡旧列表；改为 _cmpNotifyAgain 排队补一次 + clear 强制写空。
#     tools/test_compare_restore.js 确定性复现：旧逻辑删 6→复活 6，新逻辑删 6→剩 0（PASS）。
#   - E VULN 安全加固：
#     · VULN-01（中）：core/config.py save() 现剥离凭据（SECRET_KEYS/SECRET_PARTS 含
#       access_code/token/cookie…）→ 本地 config.yaml 入库不再带真值（真值在 config.local.yaml）。
#     · VULN-02（高）：tools/sync_to_publish.py 的 SECRET_KEYS 补齐 access_code/accesscode，
#       sync --dry 已验证剔除 ['publish.token','ai.api_key','public.access_code']。
#     · VULN-03（中）：detail.html 内联 openBukkenSearch('{{row.property_no}}') 改为
#       data-bukken 委托（property_no 不再进 JS 字符串）→ 消除 XSS。
#   - 门禁：node --check app.js/fieldmap.js/i18n.js 全过；I18N_KEYS_OK / FIELD_LABELS_OK /
#     VALUE_SANITY_OK 全绿；director_audit 红 0；test_compare_restore.js PASS；
#     sync --dry 验证凭据剔除。REINS 番号検索「自动填表」受跨域限制为 URL 参数+剪贴板兜底，
#     待勇哥晨间真机确认（非 bug）。
# ============================================================================
# v1.8.9（2026-09-18 · 修对比栏「清空/删到最后一个又全部恢复」竞态回归）
#   - 🔴 对比栏竞态真根因修复（勇哥复现「点清空→其他几个又回来 / 删到最后一个→全出现」）：
#     根因是 v1.8.6 引入 `CompareBox.setAll()` 校正本地栏时埋下的竞态 —— `load()` 异步，
#     `_cmpLoading` 闸门会**丢弃**用户后续触发的 load()，而最早那次 load() 在 `await api()`
#     期间捕获的是「删除前的旧 nos」，返回后用 `setAll()` 把旧列表整个写回对比栏 → 复活已删项。
#   - 修法：_loadBody 改为 `while` 循环，每次 `await` 回来都 `JSON.stringify(noOf())` 与本次
#     请求的 nos 比对；不一致就**不带 setAll 重跑一轮**（闸门仍开、绝不并发），直到 nos 稳定
#     才落盘。setAll 只刷新元数据/裁剪下架项，绝不复活已删项。保留 v1.8.7 `_cmpLoading` 闸门（不闪）。
#   - 门禁：tools/test_compare_restore.js 确定性复现——旧逻辑删 6 套→栏剩 6（复活），
#     新逻辑删 6 套→栏剩 0（正确）；node --check 通过；模板热重载即时可见，发布须重发。
# ============================================================================
# v1.8.6（2026-09-18 · 排序方向真根因修复 + 裸 key + 布局并齐 + 详情页 PDF 内嵌）
#   - 🔴 **排序「点了完全没反应」真根因修复**（前后端各一半责任）：
#       后端 store.py 原 `if order=='date_desc': sort,sdir='updated','desc'` **无条件硬覆盖**；
#       前端 search.html 又**无条件**发 `order=date_desc` → 用户选的排序键/方向全被丢弃。
#       修法：后端把 order 降级为「只在没显式给 sort 时才兜底」；前端只在默认排序时才发。
#       ⚠ 教训：v1.8.4 那轮误判成「浏览器旧 JS 缓存」，因为**只测了干净的 API、没带 order**。
#         测 UI 功能必须按前端真实发出的完整参数组合测。
#   - 🔴 修取引態様面板显示裸 key（search.trade_type.*）：这几个键**只写进了繁体字典**
#       （i18n.js zh-TW 块），简体字典 ZH 里没有 → 简体界面回退显示裸 key。
#       已补进 ZH + 四语齐套；并**修门禁盲区**：verify_i18n_keys.py 新增
#       EXTRA_DYN_PREFIXES 手工登记「数据驱动前缀」（key 写在 MS_OPTS[].i18n 里、
#       不是 t('前缀'+x) 字面拼接，静态正则扫不到），把 search.trade_type. 纳入校验。
#   - 多选面板加「全选」（区 / 取引態様 都有）；行为与「清空」一致（只改勾选、不自动查询）。
#   - 布局「并齐」：取引態様 / 只看带PDF / 每页条数 **并入「关键词/区/物件種目」同一行右端**
#     （margin-left:auto）；「收起条件」单键改挂**卡片标题行右侧**，保证折叠后仍点得到。
#     ⚠ 三项**只能渲染一次**，重复渲染会因同 ID 被 JS 只取第一个而静默失效。
#   - 详情页：删掉「重新生成」旁的「打开本地 PDF」跳转按钮；改为**页面内完整展示 PDF** ——
#     新增 #pdfCard + iframe.pdf-frame（宽度 100% = 网页同宽、高度 78vh 随窗口自适应、
#     位置在 AI 解读下方 / 变更历史上方）。PDF 仅本机，线上不传故线上不显示。
#   - 详情页「详细信息」分类标签（.df .dk）改**粗体**（只加 font-weight，字体字号不变）。
# ============================================================================
# v1.8.5（2026-09-17 · ⑤ 对账三段式+下钻 / ⑥ 计划预览 / #235#236#237 UI 修正）
#   - ⑤ 对账三段式 + 下钻（PRD §17.1）：store.reconcile() 返回值新增 segments
#     （platform / local / gap）与 runs（最近 5 轮）；**缺口不再笼统报一个数**，
#     按 PLATFORM_BROWSE_CAP=500（平台单次检索可浏览上限，10 页 × 50）拆成两类归因：
#       truncated = max(0, online-500) → 平台上翻不到，非我方责任（中性色）；
#       missed    = max(0, min(online,500)-local_live) → 可翻范围内没抓全 = 真漏采（标红）。
#     index.html 对账卡改三段式，每段点标题下钻看逐组明细 + 最近轮次。
#     兼容：后端无 segments 时前端只填大数，不报错、不裸 key。
#   - 修正 latest_run_at 恒为 None：runs 表列名是 started_at（没有 captured_at），补回退。
#   - ⑥ 计划预览交互（PRD §17.3）：collect.html 每轮检索计划卡加「日期模式」
#     （当日 / 指定日 / 全期間）+ 12 次检索预览清单（6 種目 × 登録/変更 2 轴），
#     每行「跳到该组」一键把 ④ 下载条件预填成这一组（種目勾上 + 日期填好）。
#   - #235 筛选区**单键双向**收合：删掉 filterCollapseBtn 与摘要内嵌按钮，
#     统一为右上角常驻 #filterToggle，箭头随状态翻转（▾ 点开 / ▴ 收起），
#     解决勇哥截图「修改条件只有下拉、没有上滑很别扭」。切语言走 i18n:changed 重新 sync。
#   - #236 静态资源缓存根治：base.html 的 style.css/app.js/i18n.js/fieldmap.js
#     四个 url_for('static') 加 ?v={{ version }}，发布后强制浏览器拉新版，
#     根治「排序方向不生效 / 中文裸 key」这类**旧 JS 常驻缓存**造成的假 bug。
#   - #237 取引態様 / 只看带PDF / 每页条数 三项移到右上角常驻区（.filter-tr），
#     折叠筛选区后仍可改；元素 ID 保持不变，既有 msToggle/buildParams/回填 JS 无需改动。
#     ⚠ 关键：三项**只能渲染一次**，重复渲染会因同 ID 被 JS 只取第一个而失效。
#   - i18n 四语齐套新增 recon.seg.*/sub.*/th.*/cause.*/runs.*/drill.*、
#     roundplan.date_mode/mode_*/preview_*/th_*/jump*/all_period/plus_*、filter.toggle_tip。
# ============================================================================
# v1.8.4（2026-09-17 · 冲突1 拍板 A：全期間主轮默认关）
#   - 冲突1（PRD v1.8「全期間安全网默认关」）勇哥拍板=A：main_round_enabled 默认 true→false。
#     根因（PM battle 实证）：全期間 bulk 受 REINS 500 上限，老房源被挤出窗口即不在本轮并集
#     → mark_delisted(complete=True) 连续 N 轮 → 误标 is_active=0（7/1 房源即此场景）。
#     「缺席≠下架」→ 关掉主轮，mark_delisted 作用域空 → complete=False → 只复活不判下架
#     （宁可漏判绝不误杀，crawler.py:584-609）。
#   - 降级路径安全：关主轮时 items/succeeded_subtypes 保持函数开头预声明空值，收尾
#     mark_delisted(seen=set, scope=set, complete=False) 无 NameError，仅复活。
#   - 代价（已知、可接受）：不再自动补详情/PDF；真正的下架判定待另起 per-番号 复核 pass
#     （逐番号查询返空=真下架，彻底绕开 500 上限，架构正确解）。
#   - 配套：collect.html「每轮检索计划」卡复选框默认不勾 + 注释更新；crawler 注释同步。
#   - 注：本次为 v1.8.3（冲突3/45s/长停留）之上的追加行为改动，按版本单一来源规则抬 1.8.4。
# ============================================================================
# v1.8.3（2026-09-17 · 冲突3/45s/长停留 勇哥拍板落地）
#   - 冲突3「以详情页为准」：移除列表「図」图标（p-icon-type-zu）做 PDF 下载门禁。
#     列表图标与详情页図面参照同源，勇哥选详情页为唯一判据；crawler 三处 need_pdf 去掉
#     `(_floorplan==1)` 拦截，改由 _fetch_detail 点「図面参照」按钮存在与否决定下不下 PDF
#     （按钮不在→优雅跳过）。图标仅保留统计用途。
#   - 45s 等待压到真人节奏：_read_total 上限由固定 45.0s 改为 None→random.uniform(5.0,8.0)，
#     轮询 0.5s 保留；超时文案「记未知」→「记失败·继续」（不阻塞下轮）。日志不再出现 45s 干等。
#   - 长停留区间 [15,30]→[10,20]s（delay_long_range_seconds，config.yaml）。
#   - 冲突1（全期間主轮默认开关）留待勇哥就「下架判定基线是否严谨」拍板后再动（见 PRD 差距 battle）。
# ============================================================================
# v1.8.2（2026-09-17 · D1/R2/D2/D7/D5：抓取稳健性 + 开关 UI + 概览对账）
#   - R1 修复：run_round 收尾汇总框曾引用未定义变量（live_groups 等），被宽 except 吞成
#     「本轮失败」、每轮都报失败但数据其实落了。改为用真实计数的 local 变量
#     （live_groups_n / online_total / succeeded_subtypes）+ 显式降级路径（main_round 关时只跑
#     当日同步、不判下架）。门禁：tests/test_crawl_round_summary.py 锁「demo 收尾 status=ok +
#     汇总框含『全期間主轮』+ 有 round_done 通知」，杜绝再次静默失败。
#   - D1/R2 全期間主轮：保留作下架判定基线（2026-09-15 误标 1189 条根因是「结果没抓全」，
#     不是「跑太多」），加开关 main_round_enabled（默认开）+ 改名「全期間主轮(数据主力+下架基线)」
#     讲清用途，防后人误砍。crawler._live_items 返回 4 值（collected/online_total/
#     succeeded_subtypes/组数），收尾框与下架判定都从它取值。
#   - D2 前日补齐：sync_today_dates 支持 backfill_prev 参数 + 拍平作业队列 + watermark 防重复
#     （一天最多补一次，常态 0 额外请求）。判据 _prev_day_needs_backfill：前日 0 条 / 最后成功轮
#     早于 cutoff(20:00) / 已补过不重复。门禁：test_crawl_round_summary.py 六个用例锁三种
#     触发 + 三种不触发。
#   - D7 开关 UI（web）：collect.html 新增「每轮检索计划」卡 —— 全期間主轮开关（默认开，绑
#     cfg.crawl.main_round_enabled）+ 前一天补齐开关（绑 sync_dates_backfill_prev_day）+
#     检索计划预览；新增 POST /api/crawl/settings 复用 _refresh_cfg()+cfgmod.save(cfg) 保存
#     白名单键。i18n 四语齐套。
#   - D5 概览对账卡（web）：index.html 新增「数据对账」卡，六数 = 线上报告总数 / 本机已抓(在架) /
#     缺口 / 本轮新增 / 本轮变更 / 待补详情；后端 STORE.reconcile() 组合 online_coverage +
#     recent_runs + detail_progress 归一，/api/overview 暴露 reconcile 键。一句话看清「平台报
#     多少、我抓了多少、漏了多少、还差多少详情」。
#   - i18n 回归门禁强化：tools/verify_i18n_keys.py 锁「t('k') 字面量必在 ZH + 四语齐套 +
#     data-i18n 属性 key 存在」。本轮补 ZH 的 sort.* / view.toggle / filter.* / search.* /
#     pub.* / roundplan.* / recon.*，根除「繁体正常、简体裸 key」复发。
#   - ⚠ D3 PDF 判定：勇哥要求先真机抽 20 套取证再决定改不改 —— 本轮未动 has_floorplan 逻辑，
#     待取证（列表『図』图标 / detail マイソク / 实际 PDF 链接 三方比对）后单独走一轮。
#   - 门禁：pytest 68 passed / 4 skipped（含 R1/D2 单测）；FIELD_LABELS_OK / VALUE_SANITY_OK /
#     I18N_KEYS_OK 全绿。发布前仍须勇哥点头（crawler 改动红线）。
#
# v1.8.1（2026-09-17 · P1 #215：R20 服务端排序 + R21 统计行合并 + R22 结果区最大化 A+B）
#   - R20 服务端排序（梓榮点名）：9 个排序字段全部改**服务端 SQL**（store.search 新增
#     _SORT_COLS + 空值排最后 + 物件番号作稳定次级键），前端 /api/query 透传 sort/sort_dir。
#     9 字段：price(价格)/exclusive_area(専有面積)/layout(間取り)/built(建築年，wareki TEXT
#     走 UDF _built_sort_key 转公元月序)/walk_minutes(駅徒歩)/ward(区)/date(取得日)/reg(登録日)/
#     chg(変更日)。方向 asc/desc 均可；空值排最后保证"有数据的永远在上"。
#     ⚠ 修两处真 BUG（e2e 9 键 × 双向回读才逮到）：
#       ① built UDF 误用未定义的 `_re`（应为 `re`，正则已 import）→ 纯「YYYY年」值会 NameError，
#          改 re.search/re.match 三处全修；
#       ② drop_date 排序列误用 `changes.created_at`（表实际列名 `detected_at`）→ SQL 报
#          no such column，改为 MAX(detected_at)。
#   - R21 统计行合并：本机库概况（总条数/今天/最近有数据）由结果区上方**并入副标题正下方**
#     一行（.libhint-inline），仅保留基准统计；警告/运行横幅/跳转动作拆到独立的
#     #libhint-extra 行（变窄、不挤占结果区）。"本机库共 N 条" 紧跟"数据全部来自本机库…"。
#   - R22 结果区最大化 A+B（勇哥 01:15 拍板 A+B）：
#       · A 收起筛选区：点「收起筛选」把筛选表单收进一行摘要（#filter-summary，显示已选条件
#         芯片 + 编辑按钮），再点展开；默认展开。
#       · B 结果区全宽：列表卡片去左边栏、清单占满；新增视图切换「标准 / 紧凑」——
#         紧凑模式 (.qlist.compact) 每套房压成一行（价格·地址·核心指标·降价），一屏多看 N 套。
#     + 工具栏（.toolbar）整合：左侧排序下拉（9 字段 × 方向），右侧视图切换 + 收起筛选。
#   - i18n：ZH 补 sort.* / view.toggle / filter.edit / today_short，EXT 同步四语；
#     字段词典 verify_field_labels 仍 OK（仅新增展示词，未改机器键）。
#   - 门禁：pytest 60 passed / 4 skipped（新增 9 排序键 store 层对拍用例）；
#     FIELD_LABELS_OK / VALUE_SANITY_OK；node --check i18n.js；内联 JS 语法 + 渲染双过。
#   - ⚠ **发布后逮到真 BUG（线上 500）**：sort=built（建築年，wareki UDF）在线上多线程
#     服务器报 `no such function: built_sort_key`。根因：`conn` 是**线程本地属性**，
#     旧代码在 search() 里「惰性注册 UDF + 置实例标志」——只有首线程连上注册了，
#     其余线程连接没注册 → 500。本地单线程 e2e 测不出（全走同一条连接）。
#     → 修复：把 UDF 注册挪进连接工厂（_conn 每建一条线程本地连接就注册一次），
#     删掉 search() 里的惰性块。补 _diag_mt_sort.py（11 键 × 3 轮 × 多线程）验证全过。
# ============================================================================
# v1.8.0（2026-09-17 · P0 合规/信任：N1 数据新鲜度 + N2 访问码 + token 轮换）
#   - N1 数据新鲜度：对外概览页 / 查询页概况行显示「数据更新于 X 分钟前」，
#     超 6 小时变黄（同事敢不敢拿去谈价一眼可见）。来源 = 库内 MAX(last_seen_at)
#     （store.stats / library_info 新增 data_updated_at；/api/overview、/api/query、
#     overview_public.html、search.html 均接上）。
#   - N2 访问码（合规红线）：对外模式（OSAKA_PUBLIC=1）若设 config.public.access_code，
#     未带正确口令（URL ?code= 或 Cookie）则拦截 —— 页面渲染简单输入页、
#     /api（除 /api/ping）返回 401。未设 → 完全开放（向后兼容）。只读保证已在 _public_guard。
#     口令由 config.local.yaml 真值提供，随 sync 上云到线上 config.yaml；生成强口令写入本地。
#   - token 轮换：publish.token / ingest_token 改为 32 位随机（历史值曾随 commit 进库，
#     已作废）。本地→线上推送靠 ingest_token 校验，重发线上即同步新值。
#   - ⚠ 上线 N2 需勇哥点头：设了访问码后当前线上（无码）会拒绝旧链接，需重发 + 把口令发给同事。
# ============================================================================
# v1.7.6（2026-09-17 · 查询页 5 项 UI 改动：区/取引態様多选 + 默认隐藏其他详情）
#   - 勇哥截图反馈的 5 项改动（全部已落地 + 端点/store 双测）：
#     ① 区（ward）改**多选**（点 chip 即勾/消、不弹确认），交互对齐物件種目面板；
#        后端 store.search 由单值 `=` 升级为 `IN (?,?,...)`，多值 OR。
#     ② 取引態様（trade_type）同样改**多选**（字段在 detail_json、多行枚举），
#        后端由单值 LIKE 升级为多值 `OR ... LIKE ...`，所选值并集匹配。
#     ③ 详情页 / 查询页**默认隐藏其他详情**：base.html 对 /search、/p/ 在**服务端**即加
#        body.hide-agency（避免「先显后藏」闪烁），用户点「隐藏其他详情」按钮可展开。
#     ④ 关键词框缩短，取引態様 / 只看带PDF / 每页条数 **挪到同一行**。
#     ⑤ 删除日期快捷档（今天/近3/7/30天/全部），本机库概况移到副标题「数据全部来自本机库…」下方。
#   - URL 参数同步支持多值（?ward=北区&ward=中央区、?trade_type=売主&trade_type=専任）。
#   - 门禁：test_filters.py + test_api_query.py 新增 8 个多值 OR 用例（store 层并集对拍 + 端点层
#       重复参数），全量 57 passed。
# ============================================================================
# v1.7.4（2026-09-17 · AI 解读：PDF → AI 提取 → 详情页「AI 解读」卡片）
#   - 勇哥需求（对应 PRD 09 需求 4 + 操作手册 v1.4.2）：把房源 PDF 经 AI 转成
#     结构化数据，在详情页「详细信息」与「变更历史」之间（红框指定位置）显示。
#   - 数据链路：PDF（文字层缺失=扫描件时走视觉）→ AI 出固定 JSON
#     {title, highlights[], fields[{label,value}], notes} → 独立派生表
#     ai_extractions（不碰 properties 主数据，符合 PRD 08 分层）→
#     详情页经 /api/ai/<番号> 异步加载渲染。
#   - 三条写入路径：① POST /api/ai/<番号>（本机提交，WorkBuddy/AI 代跑用，
#     当前主路径，不依赖任何 API Key）；② POST /api/ai/<番号>/run +
#     core/ai_extract.py（火山方舟豆包 API，config.yaml → ai.api_key 配好后
#     页面点「重新生成」即用）；③ tools/ai_extract_batch.py 批量（pdf_hash
#     去重 / 失败隔离 / 断点续跑）。
#   - 合规：AI 代跑方式全程本机读取 PDF，数据不出机；豆包 API 会把 PDF 内容
#     发往国内云（成本 ≈ 每份 1厘–1分），是否开批量由勇哥拍板（REINS 外传条款待确认）。
#   - UI：卡片复用 .dinfo 网格风格；四语文案齐（zh/zh-TW/en/ja）；空态有引导
#     （无数据/无 PDF/上次失败 三种文案）；不阻塞详情页首屏。
# ============================================================================
# v1.7.3（2026-09-16 · 修繕積立金列错位根治 + 数值合理性门禁 + 单价万円口径统一）
#   - 勇哥反馈：物件 100140455511「修繕積立金（每月）10,346,000 円」肯定不对。
#     核实：该值 == unit_price_tsubo(10,346,000) —— 列表页 (3,10) 这一格实际是
#     **坪単価**，却被存进 repair_fund。全库 1060 条 repair_fund **无一条有效**
#     （最小值 419,000 円/月，物理不可能），781 条(73.7%) 与坪単価完全相等。
#     对照：management_fee 1029 条中位 9,250、最大 91,000、>=100万 0 条 → 只有它错位。
#   - 教训（必须记住）：v1.6 的 BUG-1 补丁**只按「売地」一种種目**打，还写注释断言
#     「マンション 类 (3,10) 是真正的修繕積立金」—— 已被真实数据证伪。按種目打补丁
#     = 换个種目就复发。改为**与種目无关的值域判断**（_FUND_MAX_YEN=20万/月）。
#   - v1.7.2 的 22 个用例只测「字段词典/机器键不上屏」，**没有数值合理性这一层**，
#     所以一条都没拦住 → 本版补齐：tools/verify_value_sanity.py（值域 + 交叉校验，
#     含 repair_fund==unit_price_tsubo 列错位铁证）+ tools/value_sanity_baseline.json
#     （历史 1060 条待清洗清单；新增脏数据即红）+ 7 个新用例（共 29 passed）。
#   - 单价口径统一（勇哥拍板「房子都是万円为单位」）：>=1万 走万円(一位小数)，
#     小值走円；fieldmap.js fieldValue 与 app._detail_pairs 的 unit() 两页不再打架。
#     实测 100140455511：㎡単価 313.0 万円/㎡、坪単価 1,034.6 万円/坪（与 2.38億/76.05㎡ 吻合）。
#   - 待勇哥批准：清洗历史 1060 条错位数据（tools/clean_misaligned_repair_fund.py，
#     默认 dry-run；正确坪単価已在主列，清洗不丢信息）。
# ============================================================================
# v1.7.5（2026-09-17 · 修「选了日期区间却一条都查不出来」的静默 BUG）
#   - 勇哥报：「这个软件怎么一点数据都查不出来」——选了 09-11~09-17 却是 0 条，
#     摘要行还显示「日期 2026-09-17」（单日），看起来像"今天没数据"，实际是区间被吞了。
#   - 根因（坐实，非猜）：web/app.py 的 /api/query **参数归一化层** ——
#       if f.get("date_from") or f.get("date_to"): f["date"] = ""   # 先清空避免双重过滤
#       elif not f["date"]: f["date"] = 今天                        # 紧接着又填回来！
#     → store.search() 里「区间」与「date=今天」**同时生效**（AND），
#       只剩今天那一格；今天还没抓到数据 → 0 条。
#     页面 SUMMARY 用的是 f["date"]，所以显示成「2026-09-17」而不是区间，极具迷惑性。
#   - 修：区间模式(_has_range) 一律不填默认日期。实测 09-11~09-17: 0 条 → **1312 条**。
#   - ★ 教训（本次最大）：**下层 Store.search() 是自洽的，错在调用它之前的那几行**。
#     v1.7.4 的 10 个筛选用例全测了 store 层、唯独没测中间的路由层 → 一路漏到用户眼前。
#     → 新增 tests/test_api_query.py **端点级**集成测试 10 个（走真实路由，与 store 层对拍），
#       并做变异测试验证有效（恢复旧逻辑后 5 个用例失败）。共 **49 passed**。
#   - 提醒：此类 bug 的特征是**不报错、不崩溃，只是查出来的数不对**，
#     所以「能跑」不等于「对了」，必须回读真实条数。
# ============================================================================
# v1.7.4（2026-09-17 · PRD 11 已拍板项落地 + 日期区间两处静默 BUG + 历史数据清洗）
#   - 勇哥批示：「11_客户需求评审_2026-09-15 看看已经拍的就直接做」→ 逐条核对现状后落地：
#     · R1 日期时间段补齐：快捷档（今天/近3天/近7天/近30天/全部）、起>止自动交换+提示、
#       localStorage 记住「止」值（旧版只记「起」）。
#     · R2 REINS 物件番号検索按钮**移到物件番号之后**（PRD §5.3-2a，旧版在番号前面）。
#     · R8 取引態様（売主）标识 + 查询页筛选（勇哥当场拍板"能加进去"）。
#       该字段不在主列、在 detail_json 里，且是多行枚举 → 后端 json_extract + LIKE 包含匹配
#       （精确等值会漏掉 103 条「売主\nオーナーチェンジ」）；列表徽章「売主」用醒目色。
#     · R3 对比页「隐藏中介信息（客户版）」开关（梓榮点名；以前要导图再在 WPS 手动删）。
#   - ★ 日期区间两处**静默错**（不报错、只查错数，最难发现）：
#     · download（下载日）口径在 range 分支漏实现 → 静默退化成 any（实测 869 条，
#       正确按 last_seen_at 应为 1793 条）。
#     · any 口径写成跨字段 AND，违反 PRD §3.5 不变量 2 → 改为 (reg BETWEEN) OR (chg BETWEEN)。
#     · **改的过程中自己踩了参数顺序坑**：占位符顺序是 reg起,reg止,chg起,chg止，
#       而 args 按 [起,起,止,止] 追加 → 实际筛成 reg∈[起,起]，869 条只剩 344 条。
#       → 新增 10 个筛选用例（与手写正确 SQL 对拍）+ **变异测试验证测试有效**（注入旧错误
#         顺序后 3 个用例失败，证明拦得住）。共 39 passed。
#     · date_caliber 旧版只在单日分支传 → 选「変更日+时间段」时口径根本没发给后端。
#   - ★ 历史数据清洗（勇哥批准「洗」）：1060 条错位 repair_fund 已清洗
#     （267 条值迁入 unit_price_tsubo，其余置空）；自动备份 data/jproperty.db.bak_20260917_004556。
#     清洗后：>=10万/月 0 条、==坪単価 0 条。门禁 VALUE_SANITY_OK（baseline 已清零）。
#   - ★ 修正 v1.7.3 的一处误判：v1.7.3 说「真正的修繕積立金根本没抓到」——**错了**。
#     当时按数值统计，漏掉了**带「円」的文本形态**；实际另有 394 条是真实有效值
#     （30,500円 / 1,900円 / 4,598円 …），已保留。教训：统计前先看**值的形态分布**，
#     不能只 CAST 成数字就下结论。
#     · 「確認中」(25 条) / 「なし」(4 条) 原样显示日文 → 新增 TEXT_CN（JS+Python 两处同步）。
# ============================================================================
# v1.7.2（2026-09-16 · 后台机器键不再上屏：字段词典全域收口 + 门禁）
#   - 勇哥反馈（截图）：查询页展开区出现 has_floorplan / has_map / has_photo /
#     unit_price_sqm / ward 这类**后台机器键**，直接给客户看。
#   - 根因（真实库 2638 行 × 35 键实枚举 + 逐行复现坐实）：**不是某一行写错，是词典有缺口
#     且没有门禁拦**——fieldmap.js 的 FIELD_CN 漏了这几个键，而旧规则是「未收录 → 保留原始键」，
#     于是英文键原样上屏；app.py 的 JP2CN 同样漏，且 pdf_url 没进内部键表（详情页也会漏出来）。
#   - 规则反转（硬规则，写进 fieldmap.js 头部）：**未收录 = 不显示 + 控制台告警**，
#     宁可少显示一个字段，也绝不把后台机器语言露给客户。
#   - 收口：FIELD_KEY/FIELD_CN 补全（ward/unit_price_sqm/unit_price_tsubo/has_photo/
#     has_floorplan/has_map…）；FIELD_SKIP 收内部键（pdf_path/pdf_url/absent_runs/__fp__/_ms_pdf）；
#     新增 fieldSkip() / fieldValue()，查询页 fillDetail、详情页 _detail_pairs、对比页 extraKeys
#     三处统一走同一份词典（以前三处各有一份硬编码 skip 列表）。
#   - 值也本地化：has_* → 有/无；㎡单价/坪单价 → 千分位；交易方式/用途地域/公开状态
#     按真实枚举值翻中文（専任→专任媒介、近商→邻近商业地区、公開中→公开中，'-' 不显示）。
#   - 修 bug：㎡単価 5,632 円/㎡ 被显示成「0 万円/㎡」（旧 unit() 无条件 int(v)//10000）。
#   - i18n 四语言（zh/zh-TW/en/ja）补齐新增字段名 + 有/无。
#   - 门禁（新增两条，永久）：
#       · tools/verify_field_labels.py —— 拿真实库全量键/值断言：全库键覆盖、四语文案齐、
#         枚举覆盖、两条渲染路径实跑无机器键。红项即失败（退出码 1）。
#       · _diag_ui_fields.py —— 真机 DOM 复核：详情页 400 页服务端渲染 + 浏览器真跑 JS 渲染
#         查询页展开区/对比页，扫 .dk/.cl 标签无机器键、FIELD_UNKNOWN 为空。
#   - 实测证据：A) 详情页渲染 400 页，机器键 0；B) 查询页展开区 32 种标签、未收录键 0；
#     C) 对比页 36 字段、机器键 0；D) 门禁脚本 FIELD_LABELS_OK（红 0 / 黄 0）。
#   - ⚠ 待重启 8765 生效（app.py 不热重载；fieldmap.js/i18n.js 热重载即时可见）。
#
# v1.7.1（2026-09-16 · 显示/隐藏其他详情：改为整页级全局开关）
#   - 勇哥反馈：原「其他详情」按钮放在**每行/每套房产**里（search.html 行内 + detail.html 单套），
#     应是「针对整页所有房产信息共同生效」的一个设置项。
#   - 现删掉所有 per-row 按钮，查询页按钮行 + 详情页工具栏各放**一个**全局开关
#     （id=agency-toggle），点一下切换 body.hide-agency，CSS 一次隐藏全页 [data-agency] 中介字段。
#   - 文案即按钮动作：默认中介可见 → 显「隐藏其他详情」（点了就藏）；藏起后 → 显「显示其他详情」
#     （点了就显示）。i18n：search.other_detail='隐藏其他详情' / search.other_hide_off='显示其他详情'。
#   - fieldmap.js 新增 initAgencyToggle()：加载时把按钮文案对齐当前 state，刷新后文实相符。
#   - ⚠ 同日 勇哥报「日期段查询恒为 330」——经真实库副本 + v1.7.0 代码离线验证（_diag_daterange.py），
#     根因**不是查询逻辑 bug**：9/13~9/16 范围并集 = 1033（正确）；330 是 8765 仍跑 v1.6.x 老后端
#     （模板热重载了、app.py/store.py 未重启），老后端不认 date_from/date_to、静默退化成 date=今天(9/16)=330。
#     v1.7.0 代码正确，双击重启 8765 即正常。此坑 QA 未拦（UI 依赖非热重载后端却不提示），已记。
#
# v1.7.0（2026-09-16 · 指定日期下载：数据完整性透明化 + 查询页增强）
#   - 概览实时进度面板升级为「种目 × 登录/变更」12 行矩阵（R3）：
#       · online_stats 新增 axis 列（登録/変更），crawler 写分母时带轴；
#       · crawl_state 新增 target_date，phase_groups() 按日期+轴从 properties 实算
#         已下详情(fetched)/待补(need_detail)/PDF，供矩阵分母=平台报告、分子=库内；
#       · /api/phase 返回 groups[]，index.html 渲染 12 行表格；四步标签改中文。
#   - 查询页即时态横幅（R2）：所有下载入口轮询 /api/phase，显示「正在下载{日期}·累计·待补」。
#   - 查询页/详情页：物件番号検索按钮（打开 REINS 物件番号検索，预填+复制番号兜底，待真机确认自动搜索流）；
#     「其他详情」按钮一键隐藏中介字段（broker/broker_tel 等，data-agency 标记）。
#   - 日期时间段（起~止）筛选 + 按取得日期倒序（最新在上）；登录/变更标显式标在每行。
#   - 注：crawler 真机 e2e 与 REINS 物件番号検索自动填表流仍待勇哥重启 8765 后真机验证（8 门禁③⑥）。
#
# v1.6.5（2026-09-16 · P0 回归修复：v1.6.4 汇总框崩溃导致每轮只跑部分组）
#   - run_round_options / _live_items 调用 _log_session_summary 时传了未定义的
#     session_results / live_results → 每轮在「打印汇总框」那行抛 NameError 崩溃。
#     数据已落库，但崩溃发生在收尾阶段 → 后续组（g2 変更轴 / g3 タウン）被 abort，
#     表现为「指定日期下载只跑到第 2 组登録轴就停」「9/8 売マンション 本地 77 vs 平台 194」。
#   - 现补 session_results=[]（逐条件累加 online/downloaded）、live_results=[]，_on 先置 None。
#
# v1.6.4（2026-09-16 · 勇哥要求：每次下载结束打印「条件 + 结果」汇总框）
#   诉求：搜索条件录入是一切查询数据的基础（条件错→数据全错），日志必须让人一眼看清
#     "这次设定了哪些条件、下了哪些内容、结果多少"，杜绝"指定日期下载结束"只见一行没头没脑。
#   实现：
#     ① 新增 _log_session_summary(log, title, spec, results, stats, online_total)：
#        打印 38 字符宽汇总框 = 【下载条件】(種目组/日期轴/日期范围/区域) +
#        【下载结果】(线上报告合计/本地落库 新盘·变更·PDF) + 【逐条件结果】(每组 线上/下载)。
#     ② run_round_options(指定日期下载) 结束：列出本次 種目组×日期轴×日期范围×区域 设定，
#        并逐条件打印 线上报告/本次下载；主轮 _live_items 结束同样打印（全期間安全网）。
#     ③ 逐条检索结果行由 "→ 结果 N" 改为 "→ 线上报告 N 件"，语义明确。
#   QA（tools/qa_search_banner.py 已扩展）：新增 _log_session_summary 离线渲染断言
#     （条件行/结果行/逐条件行齐全，不登录 REINS），全过。
#   注：搜索条件录入被定为「数据正确性的地基」，PM/RD/QA 在三角色门禁中列为最高优先级核验项
#     （见 Skill osaka-role-agents / osaka-4role-standard 第 10 节「多 Agent 角色隔离」）。
# ============================================================================
# v1.6.3（2026-09-16 · 勇哥要求：每次检索在 DOS 框内直显搜索条件横幅）
#   诉求：在命令框里直观看到「房屋类型 / 区域(是否选大阪府大阪市) / 日期轴·范围」，
#     便于一眼确认检索条件对不对（避免 v1.6.0/1.6.1 那种"地区没录进去却记成完成"）。
#   实现：
#     ① 新增 _log_search_banner(log, ...)：输出 6 行横幅（序号/房屋类型[新築·中古]/
#        区域[直录or保存条件套用]/日期轴/日期范围/轮次）。
#     ② 在三个检索入口各插一处调用（检索真正发出前）：
#        · _live_items 主轮：6 种目 × 全期間(无日期轴) 安全网；
#        · run_round_options 指定日期下载：登録/変更 单轴或双轴并集、当日/指定日/全期間；
#        · sync_today_dates 当日同步：登録或変更 单轴 × 当日。
#   ③ 房屋类型自动标 新築/中古（组合组标 新築+中古）；区域统一显示「大阪府・大阪市」基线，
#      并标注当前是「直录」还是「保存条件套用」(由 config.search.use_saved_condition 决定)。
#   QA（tools/qa_search_banner.py，离线、不登录 REINS）：13 组合全过
#     6 種目 × 区域模式 × 登録/変更/双轴/全期間 × 当日/指定日/全期間 × 3 轮次，
#     断言关键字段正确 + 打印样本横幅供肉眼核对。
#   PM 完善清单（搜索条件模型仍存在的缺口，待排期）：
#     · 区(ward) 级筛选目前未启用（只设到 大阪市），若需某区需补「所在地１」小区位 locator；
#     · R24 翻页"读分页器翻到底"增强仍待 REINS 真机复核（config max_pages=10=500 已对齐平台上限）；
#     · 横幅已让"区域是否选上"可见，但页面同屏多排日期=AND 的语义仍靠代码注释保障，
#       后续可在横幅加一行「合并方式=按番号OR」提示。
# ============================================================================
# v1.6.2（2026-09-16 · P0 事故修复：R25 地区直录把整轮检索打崩）
#   事故现象：v1.6.0/1.6.1 在 use_saved_condition=false 下，每轮检索恒「结果 未知」、
#     落库 0 条（真机日志 17:26 / 17:49 两轮均如此），系统实质不可用。
#   根因（真机 DOM 快照 + 运行日志双向印证）：
#     ① REINS 検索条件入力页里「都道府県名 / 所在地名１」存在**两套同名控件**——
#        ·「所在地範囲選択１/２/３」：input 带 `disabled="disabled"`，只能靠
#          「保存した検索条件の選択 → 読込」把地区带出；
#        ·「所在地１/２/３」：字段名完全相同但**无 disabled、可手填**（勇哥截图那块）。
#     ② 旧 `_ctrl_input` 的 `div.p-label:has(...) + div input.p-textbox-input` 不带区块限定
#        → `has-text('都道府県名')` 一次命中 **9 个**元素 → Playwright 严格模式下
#        `wait_for()` 抛 `Error: strict mode violation ... resolved to 9 elements`
#        （注意：**不是** TimeoutError，故旧日志里显示为 `Error`）→ 填写静默失败。
#     ③ 放大器：失败分支只写「⚠ 沿用默认所在地」就继续跑，把「无地区检索」当正常完成
#        → 整轮抓 0 条却被记成 success（静默失败 = 最危险的一类 bug）。
#   修复：
#     ① `_ctrl_input` 新增 `area` 参数限定区块：
#        `div.row:has(> div > h3:text-is('所在地１')) + div.container` + 原 base 选择器
#        → 精确命中 1 个（已用真机 DOM 快照离线复现：旧 9 个 / 新 1 个且可填）。
#     ② else 分支改用 `area='所在地１'` 直录 大阪府/大阪市；ward 分支同样限定。
#     ③ 地区写不进时**直接抛 RuntimeError 中止本轮**，绝不再静默「沿用默认所在地」。
#   真机验证（_diag_live_geo.py，只读、不落库）：
#     · 表单回读 都道府県名='大阪府' / 所在地名１='大阪市' ✅
#     · 检索结果 =「1～50件 ／ 500件」✅（且清空地区后检索按钮不可达 → 证明地区是关键变量）
#   认知校正：v1.6.0 注释里「都道府県名 输入框默认禁用」只对「所在地範囲選択」那套成立；
#     「所在地１」那套一直可手填 —— 两条路（读込 / 直录）都能拿到地区基线。
# ============================================================================
# v1.6.1（2026-09-16 · BUG-1 真正落地 + 范围校正）
#   上一版 v1.6.0 的提交说明（1c7978a）声称 BUG-1 已修，但 crawler.py 实际并未写入
#   迁移代码（message 夸大）。本版把迁移真正补进 _postprocess_list_rec：
#   ① 売地（土地类，subtype 含「売地」）列表页第3行第10列的「坪単価」不再写进
#      repair_fund，改迁到 unit_price_tsubo 主列并清掉 repair_fund（土地无 修繕積立金）。
#   ② 全库核验范围：仅 売地 受影响（66 売地 + 1 売地／オークション = 67 行），
#      并非原始反馈里的 965/969 行——中古マンション/中古戸建 的 (3,10) 实测是真正的
#      修繕積立金（存在 '確認中' 文本值、与 unit_price_tsubo 不同），不动。
#   ③ tools/clean_bug1.py 已对 67 行历史错列执行清洗：repair_fund 全部清掉，
#      unit_price_tsubo 空缺的 5 行回填（其余 62 行详情页已给过正确值，保留）。
#   ④ R24 翻页「读分页器到最后」增强仍待 REINS 真机复核（config max_pages=10=500 已对齐）。
# ============================================================================
# v1.6.0（2026-09-16 · 查询条件与数据口径对齐 · 批1）
#   批1（PRD 13 已拍板 / PRD 14 双版本方案 A）：
#   ① BUG-2：publisher 透传本地列名（building_name/address/…），修线上名称/地址空白
#      （旧版重命名为 title/address_raw 被 upsert 丢弃）。修完需全量重传+发布生效。
#   ② BUG-1：売土地类列表页第3行第10列是「坪単価」非「修繕積立金」，按 kind 把该值
#      迁到 unit_price_tsubo（土地类无修繕積立金）；戸建/マンション 不受影响。历史错行待清洗。
#   ③ R25：use_saved_condition=false，不碰ワンタッチ検索，直接录 大阪府+大阪市 地区基线。
#      ⚠ 本条的实现有缺陷（locator 命中 9 个同名控件 → 地区根本录不进去 → 检索恒为空、
#        整轮抓 0 条），已在 **v1.6.2** 修复并做真机端到端验证，根因见 v1.6.2 条目。
#   ④ R23：运行日志页新增「平台件数 / 我方件数 / 差额(归因)」三列（对账用）。
#   ⑤ R24：max_pages 已对齐平台上限 500（=10 页）；翻页读分页器增强待真机复核。
#   双版本地基（PRD 14）：config 支持 OSAKA_DB；tools/db_guard.py 快照/校对/回滚；
#      桌面 8766 试验版 BAT。主库冻结、脏数据由 v1.6 工作库处理。
# ============================================================================
# v1.5.17（2026-09-15 19:38 · 查询页 JS 容错：字段缺失不再整页崩）
#   症状：线上发布版（osaka-house-v2.app.workbuddy.host）点「查询」弹
#     「查询：请求失败：Cannot read property 'value' of null」。
#   根因：线上是**独立部署的工程** osaka-house-publish（不是本地 8765 的代理），
#     它的 search.html 被刻意裁掉「只看带 PDF」字段（对外版不放 PDF），
#     但内联 JS 仍读 has_pdf → getElementById 返回 null → 抛 TypeError。
#   修复：search.html 三处加 null 守卫（线上工程同处已同步修）：
#     ① saveQState：读不到写空串（原先会连带整段条件存不进 localStorage）；
#     ② resetForm：字段不存在则跳过；
#     ③ buildParams：字段不存在则不参与筛选 —— 这一处正是点「查询」崩的直接原因。
#   验证：_test_online_search.js 用「has_pdf 返回 null」的假 DOM 跑 buildParams/resetForm，
#     修复前 RESULT=CRASH，修复后 RESULT=ALL_OK。
# ============================================================================
# v1.5.16（2026-09-15 19:21 · 补全 v1.5.15 实时阶段进度面板的缺失实现）
#   v1.5.15 只提交了骨架（/api/phase 端点、.phaser 样式、crawl_state 加 need_detail/need_pdf 列），
#     但前端渲染函数与两阶段 phase 发射漏提交，导致面板实际不可见、计数恒为 0。本版补齐：
#   ① 前端 index.html 注入 renderPhase() + pollPhase()（4s 轮询 /api/phase）：按代码真实时间线渲染
#      4 阶段步进条(date_sync→list→detail_pdf→export) + 整体进度 + ①列表查询·差别量卡
#      + ②详情＋図面PDF 双计数 subbar；无轮次在跑时整块隐藏。
#   ② crawler 真正发射四阶段 phase 事件：sync_today_dates 入口 phase="date_sync"（新增 run_id 形参）、
#      run_round 收尾 phase="export"；list/detail_pdf 事件 v1.5.15 已就位。
#   ③ /api/phase 改读 runs 表的运行中行（scanned/fetched/pdf_saved，progress_run 每 flush 实时写）
#      作为最可靠现场计数源，crawl_state 只负责阶段标签/分组/待处理估算，避免双写不一致。
#   ④ 阶段顺序对齐代码真实时间线：date_sync(①) → list(②) → detail_pdf(③) → export(④)
#      （视觉稿把 date_sync 排第③格，但日期同步先于列表跑，故前端按真实顺序高亮）。
# ============================================================================
# v1.5.15（2026-09-15 18:50 · 实时阶段进度面板骨架 + 固化 1.5.14 sqlite3 修复）
#   ① 新增「实时进度」面板（.phaser）：4 阶段步进条 + 整体进度 + ①列表查询·差别量卡
#      + ②详情＋図面PDF 双计数。后端 /api/phase 读 crawl_state，前端 4s 轮询；
#      无轮次在跑时整块隐藏（沿用静态覆盖层）。crawl_state 表加 need_detail/need_pdf 列，
#      save_crawl_state 白名单同步放开；crawler 在 date_sync/list/detail_pdf/export 四阶段
#      各发射一次 phase 事件，阶段2 每 5 条回写 fetched/pdf_saved（实时进度）。
#   ② 固化 1.5.14 的 sqlite3 写库 bug 修复（crawler.sync_today_dates 写库步骤
#      `con = sqlite3.connect(...)` 之前漏 `import sqlite3`，导致平台当日日期写不进库、
#      "今天"数量失真）。本版一并 checkpoint。
#   ③ 阶段顺序对齐代码真实时间线：date_sync(①) → list(②) → detail_pdf(③) → export(④)
#      （视觉稿把 date_sync 排第③格，但代码里日期同步先于列表跑，故前端按真实顺序高亮）。
# ============================================================================
# v1.5.14 hotfix（2026-09-15 17:50 · 修 sync_today_dates 写库步骤 datetime.datetime AttributeError）
#   上一版 1.5.14 只改对了 `today = datetime.now()`，但写库收尾那行 `now = datetime.datetime.now()`
#   漏改 → 平台日期收集齐了却在写库前抛 AttributeError，"今天"平台日期从未落库
#   （查询页仍是被回填洗掉的几十~78 条）。已改 datetime.now() 并清掉陈旧 crawler .pyc。
# ============================================================================
# v1.5.14（2026-09-15 17:05 · 修「今天(登録日或変更日)只剩 65 条」= 日期被回填洗掉）
#   背景（勇哥真机截图）：查询页「日期=今天 · 口径=登録日或変更日」只剩 65 条，
#     而平台当天真实是 866 量级（同一天上午刚验证过）。数量明显不对。
#   根因（v1.5.11/12 的设计漏洞，全程可复现）：
#     sync_today_dates 是把「登録年月日=今天」的**检索结果直接写进 reg_date_iso**——
#     这些行没有详情页原文(registration_date)。而 store.backfill_platform_dates（v1.5.4 起
#     **每次 8765 启动都跑**）老逻辑逐行无条件 `SET reg_date_iso=?, chg_date_iso=?`，
#     无原文 → _to_iso_day(None) → NULL ⇒ 把当天几百条平台日期全洗成 NULL。
#     实测：09-15 14:47 同步写入 866 条 → 15:2x 重启 8765 → 查询页只剩 65 条。
#   修法：
#     ① store.backfill_platform_dates：**只在"有原文"时才回算该列**。
#        没有原文 ≠ 日期不成立（它可能是检索条件直接给的权威结论），绝不能被清掉。
#        另加 _has_text() 助手统一判空。
#     ② crawler.sync_today_dates：写 ISO 的同时**把原文一起写**（ISO 文本本身即可被
#        _is_platform_date / _to_iso_day 识别），即使旧代码回填也不会再被洗掉（双保险）。
#     ③ crawler.sync_today_dates：被平台当日检索命中 ⇒ 顺手 is_active=1 / absent_runs=0
#        （与下架判定"本轮见到就复活"同一原则；否则它们不计入 is_active=1 的统计口径）。
# ============================================================================
# v1.5.13（2026-09-15 16:18 · PDF 下载拿列表「図」图标当闸门）
#   背景（勇哥真机日志观察）：列表页已能读「図」图标(p-icon-type-zu→has_floorplan)，
#     但 PDF 下载只看 need_pdf/config，完全不看 has_floorplan → 没図面的房源也被推进详情页
#     点「図面参照」→ 点不到 → 报 "PDF 未取到（本套可能本来就没有図面）" 误导噪音；
#     且 PDF 命中率分母=全库，被一堆"注定没有"的无図面房源无意义拉低。
#   修法（低风险，只跳过"确定没有"的）：
#     ① crawler：三处 need_pdf 计算处（阶段1 行内/阶段2/_collect_from_results）统一加闸门
#        `and (has_floorplan == 1)` —— 列表「図」图标直读永远准确，无图标=平台无図面，跳过下载。
#     ② 阶段1 行内点詳細的 want_pdf 也改为传已闸门的 need_pdf（不再裸传 want_pdf）。
#     ③ 每组日志新增「本组无「図」图标、跳过 PDF 下载 N 套」汇总行（替代逐条误导噪音）。
#     ④ store.pdf_progress(date) 新增：PDF 命中率分母只算 has_floorplan=1 子集（当天/累计两口径）；
#        同步面板第 4 层显示「図面 PDF」(当天) + 第 4b 层「累计図面」对照；i18n 四语补全。
#   效果：省去无谓详情页点击 → 一轮更快；噪音消除；PDF 命中率分母诚实（只看"本应有"的）。
#   注意：闸门只跳过"确定没有図面"的，绝不跳过"可能有"的；有图标的仍照常点「図面参照」。
# ============================================================================
# v1.5.12（2026-09-15 15:20 · 修「详情覆盖率 68.7% 失真」+ 消除新盘「一轮滞后」）
#   背景（勇哥反馈）：同步面板显示「已下详情 1589 / 待补 724 / 68.7%」，但今天子集(登録或変更=今天)
#     真实详情覆盖率只有 16.5%（143/866）；全库口径把历史 1599 条早已下好的详情稀释进去，显得乐观。
#   两个真问题：
#     ① 比例分母失真（Q2）：detail_progress 一直用**全库**做分母。用户要的是「按当天数据算」。
#        → detail_progress(date=None) 改为支持 date 范围（按 reg/chg=date 子集）；同步面板默认按
#          「今天」算分母显示（143/723/16.5%），同时保留「累计详情」(1589/724/68.7%) 作对照，不丢信息。
#     ② 新盘详情滞后（Q1 持久修复）：v1.5.11 把 sync_today_dates 放在**轮末**，导致它新增的 712 条
#        「只有列表、没详情」空壳要等**下一轮**才被阶段2 补上——这就是"大部分都没下载"的观感来源
#        （且 14:00 重启后到反馈时根本没再跑过一轮，所以一直挂着）。
#        → 把 sync_today_dates 调用**挪到 _live_items 之前**：先落今日列表壳，再让同一轮阶段2
#          同轮把它们的详情+PDF 补上，从根上消除「一轮滞后」。(_needs_detail 规则②：detail_json 空→抓)
#   当前存量缺口(724)用独立进程 _run_now.py 跑一轮清掉（写同一 DB、不碰 8765），跑完今天即 100%。
# ============================================================================
# v1.5.11（2026-09-15 14:47 · 修「日期=今天 只出 3 条」根因 + 固化自动同步）
#   背景（勇哥截图）：本地查询页筛「日期=今天 · 登録日或変更日」只出 3 条，但平台当天真实
#     是 866 条 → "金额数量不对"。根因三层：
#      ① REINS 列表行**没有日期字段**（已 dump outerHTML 实证）；
#      ② 已有 detail_json 的物件在更新轮里不再重抓详情（_collect_from_results「仅刷新列表字段」）
#         → 库里已有物件的平台日期(reg_date_iso/chg_date_iso) 永不刷新；
#      ③ 中古戸建 / 中古マンション 缺登録日、只有変更日，也凑不出"今天"。
#   附带发现（关键·与 v1.5.5 注释相反）：REINS「登録年月日 / 変更年月日」两排**同一次检索同时设 = AND
#     （交集），不是 OR**。两排都勾只返回「当天既新登録又変更」的极少数（实测 866 真并集 vs 145 交集）。
#     v1.5.5 注释写的"OR、只需一次检索"是错的，已更正。
#   修法：
#     ① 新增自包含函数 sync_today_dates()：分两次检索（登録=今 / 変更=今），各翻页收集，
#        按物件番号去重，直接把日期灌进库（不依赖详情页）。已用一次性脚本在真机验证：
#        本地[今 登録或変更] 由 3 → 866（reg=560 / chg=451 / 交集145 / 并集866），新建 712 条。
#     ② run_round 末尾（live、非试跑、config crawl.sync_dates_enabled 默认 true）自动调用
#        sync_today_dates → **每轮更新自动把今日平台日期对上**，查询页"今天"不再失真。
#     ③ _apply_manual_conditions：无论请求几个日期，都**先复位两排日期为「指定なし(全期間)」
#        再只设被请求的那个**——杜绝上一次检索/保存条件残留的日期污染本轮（604 误标最后一环）。
#     ④ run_round_options（「指定日期下载」）：rounds 由 [dts]（一次检索两排=AND）改为
#        [[dt] for dt in dts]（每日期一轮、分搜合并=真正并集），与 sync_today_dates 一致。
#   ⚠ 重启 8765（start_mvp.bat）才生效；重启前仍跑旧 v1.5.10。
# ============================================================================
# v1.5.10（2026-09-15 · 修「点検索后不确认 500件 事前確認框」——用户截图反馈）
#   现象（勇哥截图）：全量/实时更新的主抓取循环（run_round）点「検索」后，REINS 弹出
#     「検索結果が500件を超えています」事前確認 modal（__BVID__517/519，
#     <div role="dialog" aria-modal="true" class="modal fade show">），爬虫**不点「はい」**
#     就继续往下走 → 结果页不渲染 → 整组记「結果 未知」甚至 0 条。
#   根因：v1.5.9 只在「進組最开头（検索前）」与「読込后」调了 _dismiss_modal，
#     唯独漏了「点検索之后、读结果之前」这一处。而「指定日期下载」「预览」两流程
#     早已在検索后调过 _dismiss_modal（所以那两条路径没这毛病，主循环才有）。
#   修法：
#     ① 主循环 run_round 点 search_button 后、_read_total 前，插入 _dismiss_modal；
#        若真弹了框 → 点「はい/続行」确认 → 再等 1.5s 让结果页渲染（用户明确要求：
#        "遇到这个确认页要真去点，不能跳过/直接跳回检索页"）。
#     ② 顺手给测试查询 probe_query 也补上同样的検索后确认（保持一致）。
#     ③ _dismiss_modal 加固：count()==0 时立即返回（不拖慢无 modal 的常规路径）；
#        检测到 modal 后先 wait_for(visible, 4s) 再点，避免抢点在 hidden 态上。
#   注：此修复需重启 8765（start_mvp.bat）才生效；重启前 8765 仍跑旧 v1.5.9。
# ============================================================================
# v1.5.9（2026-09-15 · 修「组连续失败」爬虫 bug，让中古戸建/中古マンション 能抓到）
#   背景：ekUCcb 全量轮 6 组里 3 组失败——中古戸建/中古マンション 撞「結果 未知」、
#      新築マンション 被残留 modal 拦截 検索 点击 30s 超时。根因两件：
#      ① 上一组搜索后弹出的确认 modal（「〇〇件ありますが続けますか」等，__BVID__51x）
#         没关掉，残留到下一组的 読込/検索 点击，Playwright 报
#         "modal intercepts pointer events" → 点击超时 → 整组 0 条。
#         原代码只在「読込后」和「検索前」调 _dismiss_modal，漏了「进组最开头」。
#         修：每组 _check_maintenance 之后、_expand_search_panel 之前先 _dismiss_modal 一次。
#      ② 大房型（中古戸建 387 / 中古マンション 640 件）结果条数区渲染慢，
#         _read_total 的 25s 上限偏短 → 记「未知」。修：上限 25→45 秒。
#   这两处都还没在 REINS 真机复跑验证（需重跑一轮），但改动低风险、对症。
#   配合 v1.5.8 的下架作用域修复，即使个别组仍失败也**不会再误标下架**（只数据滞后）。
# ============================================================================
# v1.5.8（2026-09-15 · 修「失败组误标下架」事故）
#   现象：ekUCcb 全量轮 6 组里 3 组失败（中古戸建/中古マンション 撞「結果 未知」、
#      新築マンション 被残留 modal 拦截検索点击超时），但下架判定仍用「打算覆盖」的全集作用域
#      → 这 3 组的房源连续两轮不在并集 → 误标 is_active=0 共 1189 条（全库 1601 仅剩 412 在架）。
#      run54「昨天数据不见了」同类事故放大版。
#   根因：core/crawler.py 把 scope_subtypes 设成 _update_groups 拆平的全集，失败组仍留在作用域里，
#      mark_delisted 据此判它们"消失"→下架。作用域豁免只挡了"配置里没有的種目(売地)"，
#      挡不住"配置里有但本轮没抓到的種目"。
#   修法：_live_items 新增 succeeded_subtypes（只有 _read_total 真正取到结果条数的组才进），
#      mark_delisted 的 scope_subtypes 改用它。失败组不进作用域 → 它们的房源永不被判下架
#      （只走复活/安全阀）。同时 _live_items 返回值加第 3 个元素透传。
#   另：本次还发现两个会导致组失败的爬虫 bug（尚未修，待 REINS 真机验证）：
#      ① 残留 modal(__BVID__51x) 拦截 読込/検索 点击 → 需在每次进组前 _dismiss_modal；
#      ② 大房型(387/640 件)结果条数区渲染 >25s → _read_total 的 25s 上限偏短，需加长或轮询更久。
# ============================================================================
# ============================================================================
# v1.5.5（2026-09-14 深夜 · 勇哥拍板「组数=6」+「登録と変更 同时选」）
#   A. **组数严格 = 6**（勇哥原话：「组数是 6，一户建里的 1新築戸建 2中古戸建，公寓里的
#      3新築マンション 4中古マンション 5新築タウン 6中古タウン」）。
#      新增 `crawl.split_subtypes: true` → `_update_groups` 把配置组**拆平成单个種目一组**，
#      与「第二轴」解耦（v1.5.4 把两件事绑在一起，关掉第二轴就退回 3 组）。
#      `crawl.split_new_changed: false` 保持不变（REINS 无独立「変更」轴，跑两遍＝同结果请求两次）。
#   B. **登録年月日 + 変更年月日 同一次检索内一起设**（勇哥：「同时选登录和变更」）。
#      REINS 该区块自己写着「(いずれかの条件に一致する物件を検索します。)」＝ **OR**，
#      所以两排都勾**只需一次检索**；旧版「每个日期各检索一次再合并去重」＝同一批房源请求两遍。
#   C. ⚠ **撤回 v1.5.4 的「不点ワンタッチ検索」**（实测证明它是错的）：
#      v1.5.4 把 use_saved_condition 置 false。2026-09-14 23:42 真机探针打脸——
#      不点「読込」时整张检索表单根本填不了：
#        · 都道府県名 输入框 6 个全是 `disabled="disabled"`，所在地**无法手填**
#          （页面自己写着「範囲選択、所在地、沿線はいずれか必須」）；
#        · 物件種目 下拉禁用填不进（_fill_subtype_slots 返回 0）；
#        · 所有権のみ / 登録年月日 / 変更年月日 **根本没渲染** → _fill_date_block 抛
#          `NO_TITLE:登録年月日`，检索无法发起。
#      ⇒ 「読込」是**地区加载器**，不是结果筛选器（勇哥说的"与结果无关"＝选 01 还是 02
#        对结果没影响，因为種目随后会被 update_groups 覆盖）。故恢复 use_saved_condition: true。
#   D. **「当天」副作用改为代码消除**（这才是 604 条误标的最后一环）：
#      新增 `_reset_date_block()`——不指定日期时，把 登録/変更 年月日 **两排显式复位为
#      「指定なし(全期間)」**。老代码在这里是直接 return，于是保存条件自带的「当天」残留，
#      更新轮只能抓到当天房源 → 历史房源连续两轮不在并集 → 被误标下架。
# ============================================================================
# v1.5.4（2026-09-14 晚 · 勇哥报「昨天的数据查不到了」+「第二个轴多余」）
#   用户原话：①「这个（ワンタッチ検索）页的选择和后续的搜索没有任何关系，
#              在这个检索页里不要在这个位置去做任何的操作」；
#            ②「把第二个轴去掉（12→6）同意」；
#            ③「日期应该是实际在平台中新建或者是变更的日期，不是实际下载的日期」。
#   四处修（全部经用户确认后一次性改）：
#   A. 日期口径（core/store.py + core/pipeline.py）
#      · 新增 reg_date_iso / chg_date_iso：只存「确认来自平台」且能解到日的 ISO 日期。
#      · pipeline 不再用 today_iso 伪造「登録年月日」（本机 1019/1103 行是假的下载日）→
#        抓不到就留 NULL，绝不造假。启动自动回填（backfill_platform_dates，幂等）。
#      · store.search 的日期筛选改用平台日期，新增 date_caliber
#        （any / change / registration），查询页新增「日期口径」下拉。
#      · 顺带清掉 4 行误存进日期字段的下拉框文案、1103 行伪造的 8 位注册日。
#   B. first_seen_at 不再被覆盖（core/pipeline.py + core/store.py）
#      旧代码每轮无条件 setdefault(now()) 且并进 UPDATE 的 SET →
#      本机实测 1103/1103 行 first_seen_at 全等于 last_seen_at（＝最后下载时间），
#      「按天查历史」自然一天都查不到。现在只在首次入库时定下来。
#   C. 下架逻辑重写（core/store.py::mark_delisted）
#      ① 作用域豁免：只判本轮确实抓过的種目（配置里没有 売土地 → 它永远进不了并集，
#         旧版就把它当「消失了」→ 结构性误杀，604 行就是这么没的）；
#      ② 复活机制：曾下架又出现 → 立刻恢复在架（旧版标死永不复查）；
#      ③ 只在完整轮次判定 + 空结果安全阀（一条都没抓到 → 判定 crawl 出问题，绝不下架）。
#   D. 检索页不再碰「ワンタッチ検索」下拉（core/crawler.py + config.yaml）
#      use_saved_condition=false：保存条件 01 名字带「当天」，読込会把日期设成当日 →
#      更新轮只抓到当天房源 → 历史房源连续两轮不在并集 → 被误标下架。
#      地区沿用登录后默认所在地（大阪府/大阪市）。
#   E. 去掉第二轴：crawl.split_new_changed=false（12 组 → 6 组）。
# ============================================================================
# v1.5.3 补丁①（P0，run52 就是被它整轮崩掉的）：
#   `UnboundLocalError: cannot access local variable 'n_skip_no_href'`
#   —— 该计数器原来只在「阶段2 有待处理条目」时才创建，但有两处无条件引用它：
#     ① 阶段1 行内点詳細失败时 `n_skip_no_href[0] += 1`；
#     ② 阶段2 收尾汇总 `if n_skip_no_href[0]:`。
#   于是"本轮没有需要补详情的房源"（或刚有行内点击失败）→ 整轮抛异常 → runs 记 error、
#   scanned=0 —— **明明列表已经抓完了，却显示失败**（run52 16:17→17:12，55 分钟白跑）。
#   修法：把 `n_skip_no_href = [0]` 提到 `_live_items` 开头，只留一处定义。
# v1.5.3 补丁②：查询页分页器改成可点页码（首页/上一页 + 1 2 3 … 10 + 下一页/末页），
#   窗口=当前页±2，首尾常驻，中间用 … 省略，最多 7 个数字（页再多也不撑爆底栏）。
# v1.5.3：「画像 0 枚」是错的 —— 画/図/所 三个标记 + 真实照片张数（勇哥 2026-09-14 指出）
#   现象：查询页每一行都显示「画像 0 枚」，其实那些房源明明有照片。
#   **根因（两处，都不是"没数据"）**：
#     ① 旧代码用 `dp.locator("img").count()` 数照片，但 REINS 详情页**整页 <img> 恒为 0**
#        （图片走背景/懒加载）→ 永远写 0。库内 1081 条 image_count 全是 0，从来就没对过。
#     ② 阶段1 只抓列表时根本没有 image_count 字段，pipeline 无条件 `int(... or 0)` → 也写 0。
#        于是"还没抓到详情"被显示成"没有照片"。
#   **修法**：
#     A. 列表行右侧三个图标（真机快照坐实类名）——**列表页就能读到、永远准确**：
#          div.p-icon-type-ga  = 画（照片/画像）
#          div.p-icon-type-zu  = 図（間取図/図面）
#          div.p-icon-type-sho = 所（所在図/周辺地図）
#        一次 evaluate 顺手取回（零额外请求），落成 properties.has_photo / has_floorplan / has_map。
#     B. 真实张数：读详情页的「物件画像」区块——
#          没有照片 → 该区块写「物件画像は登録されていません。」→ 确定为 0；
#          有照片   → 数区块内的图片元素；数不出来就**不写**（保持未知），绝不瞎填 0。
#     C. image_count 语义改为 **-1 = 未知 / >=0 = 已知张数**；老库（全是 0）一次性迁到 -1。
#        pipeline：详情给了才写；新行没给写 -1；已有行**不动**（不覆盖已知值）。
#     D. 界面：查询行/详情页显示 画/図/所 三个小徽章（有=彩色、无=灰），
#        张数只在已知时显示；对比页/按天页/Excel 同样处理未知（显示 —，不再显示 0）。
#   ⚠ 重启前的老界面可能短暂显示「画像 -1 枚」（迁移已把 0 改成 -1），重启即正常。
#   ⚠ 三个标记要靠**跑一轮列表**才会亮起来（老数据那三列初始为 0）。
# v1.5.2：两件事（勇哥 2026-09-14 拍板，详见 docs/PRD/10 §14）
#   **A. 真跑满 12 次检索**（`crawl.reuse_axis_result: false`）。
#     背景·重要更正：REINS【有】「登録年月日 / 変更年月日」两排日期筛选（各含"当日"），
#     勇哥说的"新增和变更=2 个条件"是对的；以前两轴结果一样，是**代码没去设这两排筛选**，
#     不是 REINS 没有（v1.5.1 的结论写错了，已在 PRD/10 §13.3 与 MEMORY 更正）。
#     ⚠ 但**不能**改成只搜当日：存量房源刷不到 last_seen_at → 连续 2 轮不在并集里会被
#     mark_delisted 误标下架（库内 616 套中古マンション会全被标下架）。故两轴都跑全期間。
#     实测同房型两轴结果只差 0~2 件（26/26、169/171、3/3）—— 想省一半时间就把它改回 true。
#   **B. 断点记录 + 15 秒「问人」窗口**（勇哥提的三点：断点记录与咨询 / 15 秒默认超时 / 点了就断点续传）
#     ① 断点：新增 `crawl_state` 表，进组/翻页各记一笔（粒度到"房型+页码"；更细没必要，
#        因为 v1.4 起边抓边落库，中断最多丢当前一条）。
#     ② 只在**出问题**时弹窗：会话失效 / 连续 N 条行内点詳細失败 / 服务启动发现上轮被打断。
#     ③ 15 秒没点 → 按默认动作走（会话失效=停止本轮；行内失败=跳过本房型；重启断点=重新下载整轮），
#        **绝不卡住抓取**；点了就照办（`POST /api/decide`；resume/restart 会立刻开一轮）。
#     ④ 断点续传：`run_round(resume=True)` / `POST /api/run {resume:true}` → 跳过已跑完的房型。
# v1.5.1：修「一轮要跑 28 分钟、还什么都没干」的空转 BUG（勇哥 2026-09-14 16:32 报：
#   现象=日志刷几百行「· 30014xxxxxx 无详情直链，本轮跳过」、整轮 fe=0、就是不停）。
#   **根因（两条，都是"没干活却照睡"）**：
#     ① 阶段2 的循环不判断"本条到底干没干活"，一律 `sleep(delay)`。375 条无直链跳过
#        ⇒ 375 × ~4.6s ≈ **28.5 分钟纯空转**（实测 run49/50/51 整轮 fe=0、耗时 24~28 分钟，
#        几乎全是这段假停顿）。现在的规则：**只有真的访问了 REINS 才拟人停顿**
#        （新增 did_work 标志）；没发请求就没有"像人"的必要。
#     ② 行内点詳細（v1.5.0 新增）同样问题：点击失败也要等 3s。改为 _fetch_detail_inline
#        返回 `(rec, touched)`，只有 touched（真点开了详情页）才停。
#     · 顺带把刷屏压掉：阶段2 的无直链只打前 5 条示例 + 末尾一条汇总数（原来 375 行）。
# v1.5.1 第二改：**子查询去重（12 → 真跑 6）**。
#   REINS 的検索条件里**没有**独立的「変更」轴 ⇒「新增」「変更」两轴条件逐字节相同，
#   跑 12 次 = 同样 6 个房型各查两遍（多一倍 REINS 请求、多一倍时长、零信息增益——
#   「新增/变更」本来就是本地 changes 引擎按快照比出来的）。勇哥说"拆 12 次"的本意是
#   "拆细到单房型才不撞 500 条上限"，这层已由 split_new_changed 做到，故重复轴直接复用。
#   ⚠ 若哪天真发现 REINS 有「変更」筛选：config.yaml → crawl.reuse_axis_result: false 即恢复跑满 12 次。
# v1.5.0：抓取工作流重做（勇哥 2026-09-14 拍板，详见 docs/PRD/10）
#   **① 12 子查询**：6 房型（新築戸建/中古戸建/新築マンション/中古マンション/新築タウン/中古タウン）
#      × {新增,変更} = 12 组合，每组一次 REINS 检索，组间停顿 10~20s（config.subquery_interval_seconds）。
#      REINS 无独立「変更」筛选，両轴实际跑同一全期检索；"新增/变更"由本地 changes 引擎区分
#      （docs/PRD/10 §3）。单子查询 <500 件（REINS 硬上限 + max_pages=10），覆盖完整、无需拆条件。
#   **② 详情节奏 2~4s（勇哥真机推翻旧 20s）**：点詳細→详情→返回，列表走 bfcache 瞬时恢复，
#      中间无等待；旧"~20s/条"是把 重新加载+等全资源+PDF+重新导航 全算进去了，作废。
#      真正变量成本只剩 详情加载(几秒)+PDF 触发(不校验成功)，故 detail_gap_seconds=[2,4]（config）。
#   **③ 列表页行内点詳細 补「无直链」房源详情（填 371 空详情缺口）**：
#      这类行 詳細 是 <button>、行内无 GBK 直链，旧版阶段2 只能跳过→永远空。
#      现阶段1 遇到 need_detail 且无 href 的行，当场在列表页点 詳細→抓全字段+触发PDF→go_back(bfcache)回列表，
#      走新增 _fetch_detail_inline（同标签跳转；拿不到直链绝不写脏，下轮重试）。
#   **④ 下架/成交检测（is_active=0）**：本轮 12 子查询并集里消失的房源，连续
#      delist_consecutive_runs(=2) 轮不在才标 is_active=0（避免单次漏抓误标）。
#      新增 properties.absent_runs 列（mark_delisted 幂等补列）。仅 live 模式执行。
#   **⑤ 阶段1 列表匹配（保留并明确）**：库无→insert；库有→更新列表级 price/status 等字段，
#      但【不】重抓详情（_list_only 标记，pipeline.ingest 保留已有 detail_json）——修正"今日降价"失效的旧坑。
#   ⚠ 已知遗留（v1.5.0 一次性回填后应收敛）：371 条空详情缺口将由本轮"行内点詳細"自动补，
#      不再需要单独跑回填轮；若个别行点击/解析失败，下轮继续补（绝写脏）。
# v1.4.4：回应 PM 09-14 截图四类反馈（i18n 裸 key / 今日降价口径 / 保存并启动三态 / 原网页去留）
#   **① 今日降价口径修正（回应 PM：这数据不对）**：
#      旧算法 = changes.price_down 且 detected_at=今天。但 price_down 流水是"我方首次抓到该房源"时写的，
#      detected_at 实际≈该房源 first_seen_at。于是"今天才抓到、但平台上周就降过价"的房源被算成"今日降价"，
#      数字虚高（当天显示 1，其实是伪降价）。新算法改以 REINS 平台自身标注的「変更年月日」(properties.change_date)
#      转公元年 == 今天、且挂牌价 < 变更前价(previous_price) 为准——只有"平台今天确凿降价"的才算今日降价。
#      实测：09-14 今日降价由虚高 1 → 正确 0；按天分布 09-11=61/09-12=31/09-13=40 与平台对得上。
#      KPI 卡片「今日降价」新增鼠标悬浮说明（data-i18n-title=kpi.today_down_tip，四语言），解释算法与"今天显示 0 不算异常"。
#   **② i18n 补齐（修复回归：按钮显示裸 key btn.manual）**：
#      v1.4.3 的三态按钮直接 t('btn.manual') 动态取值，但 ZH 字典缺 btn.manual/btn.trial/btn.collect，
#      以及 step4.saveonly/step4.stop，zh-CN 下 t() 缺失即返回裸 key → 按钮显示"btn.manual"。
#      已补 ZH 条目（其余 3 语言本就有）；另给 t() 加缺失 key 控制台告警（[i18n] 缺失翻译 key: ...），
#      以后再漏翻译会立刻在 DevTools 暴露，不再静默显示裸 key。
#   **③ 保存并启动 三态（回应 PM：启动完应变成可关闭）**：
#      旧：点完「保存并启动」只是 reload，无"已启动"反馈，关闭靠另一个「停止自动更新」按钮但不直观。
#      新：保存并启动后该按钮立即变「✓ 已启动（自动更新中）」并禁用，关闭由同行「停止自动更新」按钮承担
#      （未启动时它禁用、已启动时启用）——形成 未启动[可点] → 已启动[✓·禁用·可关闭] 的清晰状态。
#      服务端按 sched.enabled 初判，前端 3 态机同步；四语言 i18n（step4.running）已补。
#   **④ 原网页按钮去留（PM 拍板：保留，不删除）**：
#      REINS 详情页**没有永久直链**（v1.4.3 已用真机探针坐实：GET ?bknno= 一律 E2171，详情只能登录内点「詳細」same-tab 打开）。
#      因此"原网页"不可能深链到某套房源的详情页。现按钮=「REINS 物件番号検索」(GBK004100) + 展示物件番号，
#      让用户自己输番号查——这是唯一诚实可用的路径。删除该按钮会直接丧失 REINS 导航能力，故保留。
#      详见 PRD/07 §12.3（对话上下文）。
#   ⚠ 已知遗留（同 v1.4.3 §11.6，数量随抓取增长）：库内 371 条 detail_json 为空（全部 first_seen 2026-09-14、
#      source_url=GBK001210 検索頁、无 PDF/previous_price）。非脏数据（688 条有效详情 0 脏 0 错配）。
#      根因：REINS 詳細 是 <button> 无 href，阶段2 无活列表可点就补不上；今天多轮（含中断/报错轮）又新增了一批。
#      待 PM 在 (a) 阶段1 inline 点詳細补详情（~20s/条·拉长抓取） vs (b) 接受缺口 之间拍板。
# v1.4.3：回退 v1.4.2 的致命回归 + 三项 PM 诉求落地（PM 2026-09-14 截图反馈）
#   **① 回退 v1.4.2「详情直链漏抓补救」补丁（它是错的、会写脏数据）**：
#      该补丁在阶段1/阶段2 拿不到列表锚点直链时，退回库里已存的 `source_url` 当详情直链。
#      但 `source_url` 是 pipeline._source_url 伪造的 `GBK001210?bknno=`（**検索頁**，不是详情頁）；
#      喂给 _fetch_detail → Playwright 把検索頁当詳細頁解析 → 写脏 detail_json。真机探针已证实：
#      GET `GBK001210?bknno=` 返回 200＝検索頁；GET `GBK003100?bknno=` 返回 200 但正文是
#      「不適切な画面操作が行われました」エラー番号 E2171（REINS 详情页**没有**永久 URL，
#      只能在登录会话内点击「詳細」打开，same-tab 跳 GBK003100）。故该回退路径已彻底删除，
#      拿不到直链的行阶段2 如实记 n_skip_no_href 跳过（详情保持空，绝不写脏）。
#   **② 详情页「原网页」按钮做诚实化（回应 PM 截图：原网页跳到検索頁/报错頁）**：
#      REINS 详情页无永久直链，原 `source_url`(GBK001210) 是伪造的。现改为「REINS 物件番号検索」
#      （GBK004100）按钮 + 展示物件番号，并注明"详情页需在 REINS 内点击詳細打开，无永久直链"。
#      pipeline._source_url 同步从伪造的 GBK001210?bknno= 改为诚实的 GBK004100（物件番号検索入口）；
#      真正抓到详情时 _fetch_detail 用真实 dp.url(GBK003100) 覆盖该值。
#   **③ 线上覆盖 101% 修正（回应 PM：101%对不对？是不是累计算法？）**：
#      不是累计——累计是独立的 `local`(901) 指标。pct 分子是"今天仍被看到"的 local_live(419)，
#      分母 online(415)；419>415 是"本机在架比线上快照多"的时差/口径假象，已把 pct **强制裁到 ≤100%**，
#      并新增 pct_raw（不裁，供核对）与 over（local_live-online，>0 提示"多几条"）。同步面板新增
#      "本机在架比线上快照多 N 条"提示，KPI 仍显示 线上/本机在架/累计。
#   **④ 手动更新/试跑 按钮三态机（回应 PM 截图三态诉求）**：
#      空闲→点一下立即「正在运行中…」并禁用（不靠等轮询）→ 跑完（sched.busy 转 false）自动「本次已完成 ✓
#      （再点重新跑）」并恢复可点 → 再点重新发起一轮。前端仅靠 /api/overview 的 sched.busy 校准，
#      发起后开 3 秒快轮询、跑完即停回落 60 秒常规轮询。四语言 i18n 已补齐。
#   ⚠ 已知遗留（非本次回归，待拍板）：库内 321 条 detail_json 为空——这些是列表页「詳細」为 <button>、
#      行内无 GBK 锚点、批量读取不到直链的行。REINS 详情只能 same-tab 点击打开（阶段2 没有活列表可点），
#      故 URL 直链路径补不上。候选方案：a) 阶段1 对无直链行 inline 点击詳細补详情（慢，~20s/条，与"列表快"权衡）；
#      b) 接受该部分缺口。本次未擅自改架构，先保证"不写脏 + 界面诚实 + 按钮稳健"。
# v1.4.2 详情直链漏抓补救（09-14 复盘，回应产品经理日志里一批"无详情直链，本轮跳过"）：
#      **【已回退 · 见 v1.4.3】** 该补丁把伪造的検索頁 URL(GBK001210) 当详情直链回灌 _fetch_detail，
#      会把検索頁当詳細頁解析、写脏 detail_json。真机探针证实 GBK001210?bknno= 是検索頁、
#      GBK003100?bknno= 触发 E2171，故直链路径不可行，已删除。
# v1.4.1：按勇哥 2026-09-14 提出的四项落地前三项（规格见 docs/PRD/09）。
#   ① **更新方式**：勇哥 09-14 最终拍板——调度**开启**（`schedule.enabled=true`），
#      运行窗口改为**日本时间白天 07:00–22:00**（避开 REINS 维护 22:00–次日07:00 这个非运行时段）。
#      时段一律以【日本时间】计算（core/scheduler._now_tz 取 UTC+9，不受本机时区影响），
#      故本机在中国时间也不会把窗口算错 1 小时。config 新增 `schedule.timezone: Asia/Tokyo`。
#      ⚠ 切勿改回「夜间 23:00–07:00」：那正是 REINS 维护，调度会跑但每轮都被守卫挡掉、抓不到数据。
#   ② **关键词字段缩短**：标签由一长串
#      「关键词（地址 / 楼名 / 物件番号 / 駅・沿線 / 間取り … 模糊匹配，含即命中）」
#      简化为「关键词」，完整说明移到悬停提示（i18n 键 `search.q.tip`），
#      把横向空间还给同行其余字段、整体更紧凑。
#      【易错】字典里 zh-TW/en/ja 的 `search.q` 原本就是长文案，只改 HTML 会被 i18n 覆盖，
#      三处字典值必须一起改短。
#   ③ **运行记录表头悬停说明**：# / 触发 / 开始 / 结束 / 扫描 / 落库 / 新盘 / 变更 / 状态 / 备注
#      每个表头悬停显示「含义 + 使用规则」（i18n 键 `runs.tip.*`），
#      概览页与运行日志页共用同一套文案。重点澄清两处最易误读的口径：
#        · 扫描 vs 落库 —— v1.4.0 两阶段后两者分离，只刷新列表字段的轮次
#          「扫描>0 而落库=0」是**正常**的，不是没跑；
#        · 新盘 vs 变更 —— 平台早已改价、我方才首次抓到的算「变更」不算「新盘」（PRD 6.11）。
#   ④ AI 提取 PDF 内容写入房源详情：**本期不开发**（勇哥指定放 v1.4.2）。
#      页面设计与逻辑规则已写入 docs/PRD/09 §4，待审核。
#
# v1.4.1 覆盖率修复（09-14 复盘）：online_coverage 的分子由「全库累计条数」改为
#      「今天仍被看到(last_seen_at=今天)的房源数」(local_live)，分母仍为 REINS 当天在架总数；
#      pct 现恒 ≤100%（= 当前在架覆盖率）。旧分子把已成交下架的历史存量也计入，
#      曾显示 199%~223% 的荒谬"覆盖"。累计数保留为独立指标「累计入库」(sync.local_total)。
#      同步状态面板重做为三层表达（当前在架覆盖 / 累计入库 / 详情层进度）+ 问号悬停说明。
# v1.4.1 列表阶段性能修复（09-14 复盘，回应产品经理"为什么列表收集这么慢"）：
#      **根因不是"读本地 DOM 慢"**——REINS 结果页是服务端一次性全量渲染（50 行全在本地 DOM，
#      真人和自动化看到的是同一份，滚动无延时）。慢的是两处隐性开销：
#      ① `_detail_href_of` 用 `a:has-text('詳細')` 抓直链，但 REINS 列表页里「詳細」是 `<button>`
#         （整页 0 个含"詳細"的 `<a>`），Playwright 每行干等满 3000ms 超时，再回落抓到行内唯一
#         `<a>`（中介公司链接 href="#"）→ 假直链。实测 5 行=15.11s ≈ 3.02s/行（占列表阶段~69%）。
#      ② 列表阶段每行 sleep(0.5~2.0)——列表阶段行内不发任何请求，该停顿对 REINS 完全不可见，
#         纯拖慢自己（50 行白等 25~100 秒）。
#      **修复**：列表阶段改用 `_bulk_list_rows`（一次 `page.evaluate` 取整页，零额外请求、不增风控）
#      ——实测 58 行 6.78s→0.037s（≈185×，逐字段与逐格读一致）；详情直链随整页一次取回，
#      并去掉每行 sleep，把"像人"的节奏挪到真正可见的「翻页停顿」(config.crawl.list_page_pause_seconds)。
#      另修：阶段2 无直链时由"静默跳过"改为如实记账(n_skip_no_href)；挡住一条把列表页当详情页
#      解析、写脏 detail_json 的隐患（row=None 且无直链时直接 return None）。
# v1.4.2 详情直链漏抓补救（09-14 复盘，回应产品经理日志里一批"无详情直链，本轮跳过"）：
#      **这是 v1.4.1 列表性能修复引入的真·BUG**——批量读 `_LIST_EXTRACT_JS` 只认「文字含 詳細」
#      或「href 含 GBK」的锚点；但个别列表行的详情链接不在 `<a href>` 里（詳細 是 <button>，
#      这类行的详情链接走 button/onclick 或别的结构），批量读返回空 → 阶段2 落到 else 分支
#      永久「无详情直链」跳过，详情永远补不上。真机日志 9 条连刷即是此现象。
#      **修复（用已验证数据，不靠猜）**：阶段1/阶段2 在拿不到列表锚点直链时，退回库里已存的
#      `source_url`（详情页真实地址；全库 1009/1009 都是 https://system.reins.jp/main/BK/GBK…?bknno=，
#      且带 bknno= 才能当直链，绝不会把列表页 URL 喂给详情解析器）。实测影响面：详情为空的 321 条
#      全部带 bknno= source_url → 补丁可自动补回 321/321（0 条无 source_url 补不了）。
#      验证：py_compile 全绿；_diag_skiprows.py 离线统计影响面。下一步真机跑一轮，日志应不再出现
#      "无详情直链"跳过（或仅剩 0 条），且 321 条详情会被补齐。
# v1.4.0：按用户 2026-09-13 拍板的四项落地（规格见 docs/PRD/07）。
#   ① **两阶段下载**（核心）：一轮内「先全列表、再全详情」（用户拍板：全列表优先，
#      不是按组交替）。阶段1 只解析列表行并落库（detail_json 留空，pipeline.ingest 支持
#      `_list_only` 标记），同时记下「詳細」详情直链；等所有组/页都翻完、列表已一致后，
#      阶段2 才回头逐条补详情 + PDF。收益：中断时列表已经完整，不会再出现
#      "列表下一半、详情全无"的半套状态。
#      【坑·非显然】阶段2 不能用 sink.add() —— _BatchSink.seen 在阶段1已记下该物件番号，
#      再 add 会被当"轮内重复"丢弃，详情永远写不进库；故阶段2 直连 pipeline.ingest。
#   ② **本地快照表 property_history**：每轮跑完 store.snapshot_run() 存一份全量快照，
#      任意两轮差异一律**本地比对**（prev_run_snapshot / history_for），
#      **不回线上查** —— 省请求、不碰风控（这是用户选本地快照而非线上查的原因）。
#   ③ **同步状态**：runs 表新增 online_total（列表层分母）/ pdf_saved（PDF 层分子）两列；
#      新增 /api/sync 与 store.sync_status() / store.detail_progress()，
#      概览新增「同步状态」面板：线上 N → 本地 M → 覆盖 Z%，以及第二个进度条
#      「已下详情 / 待补详情」（两阶段后详情是慢慢补的，必须给用户看见）。
#   ④ **已下/未下详情视觉区分**：/api/query 每行返回 has_detail，查询页对"只有列表、
#      详情待补"的行加 .nodetail（琥珀虚线左条）+「详情待补」灰标签。
#      刻意**不用红色** —— 待补是正常中间状态，红色会被误读成"下载失败"。
#   ⑤ **完成通知**：新增 notifications 表 + add_notification/list_notifications/
#      mark_notifications_read；手动更新与指定日期下载跑完各写一条，
#      前端轮询展示（不用一直盯日志）。新增 /api/history/<no> 查历轮快照与差异。
#   ⑥ **启动自愈 recover_runs()**：进程被直接关掉时 finish_run 没机会跑，runs 行会永远
#      卡在 running → 页面显示好几个"进行中"。服务启动扫一遍标 interrupted，杜绝假'进行中'。
#   ⑦ **风控加固**：context.add_init_script 把 navigator.webdriver 抹成 undefined
#      （自动化最直白的一条指纹，文档创建前执行比 goto 后 evaluate 更彻底，零副作用）。
#   ⑧ **补齐 v1.3.0 缺口**：「指定日期下载」路径（_collect_from_results / run_round_options）
#      以前还是同步等 PDF 下载，现已接上 _PdfWorker 后台下载。
#   ⑨ 概览 KPI 卡片可点：点了直接跳查询页并带上筛选（date=all 看全库 / date=今天 /
#      has_pdf=1），新增 /api/query 对 date=all 的支持。
#   ⑩ 修一个潜伏的 NameError：_live_items 里误用 self.stats（模块级函数没有 self），
#      v1.3.1 埋点引入，一旦重启会在第一条需要抓详情的房源上炸掉整组。
#      另修 differ：列表模式（无 detail_json）不再与旧的完整详情指纹比对，避免误报"变更"。
# v1.3.1：① **三层下载进度模型**（PRD 06，用户排期批准）+ ②「指定日期下载」路径也接后台 PDF
#          + ③ **单条耗时三段埋点**（本轮先做，先量后改）：落库日志现拆成「详情均耗时 / 停顿均耗时 / PDF 均耗时」，
#          用于回答"那 20 秒到底归谁"。实测（v1.3.0，17:54–17:59 连续 12 条）：详情→详情时差均值 28.3s，
#          其中停顿 2–5s+8%长停顿、PDF 后台 5.5s、REINS 详情页自身加载 ~20s。结论：单线程拟人浏览真实下限≈22–27s/条，
#          那 20s 是 REINS 服务端渲染，非我方故意等，压它只能并行详情页（破"一人在看"模型、最像机器、不推荐）。
# v1.3.0：按用户 2026-09-13 的完整规格实现「实时更新 + 灵活匹配 + 确定性存储 + 受限并发 + 真人化操作」。
#   ① **受限并发下 PDF**（用户拍板最大在途＝2）：新增 `_PdfWorker` —— 主线程把 PDF 直链丢进队列就
#      继续抓下一条详情，后台 2 个线程用**同一会话的 Cookie/UA/Referer** 直接 GET 落盘。
#      旧做法是 `dp.expect_download()` 点链接后等整个文件落盘，5–10 秒主流程完全空转（用户指出后改正）。
#      拿不到直链时**自动退回**原来的点击下载方式，保证不漏。
#   ② **确定性存储**：PDF 先写 `.pdf.part` 再 `os.replace()` **原子改名**（磁盘上永远要么没有、
#      要么是完整文件）；`flush_every` 由 5 改为 **1**（每抓一条立即落库+落盘）⇒ 中断时
#      **最多丢"当前这一条"**，此前所有详情与 PDF 都已在本机。
#   ③ **灵活匹配**：详情数据 与 PDF 文件**分别**判断要不要抓 ——
#      列表行已有详情但缺 PDF 时，直接用库里存的 `pdf_url` 补下 PDF（**不重开详情页**）；
#      新增 `properties.pdf_url` 列（`_ensure_columns()` 幂等补列，老库免手工迁移）。
#   ④ **真人化操作**：每页约 12% 概率把 1~2 条挪到本页末尾再处理（乱序但**一条都不少**，
#      符合用户"页面必须全存储、顺序可以不按列表来"）；节奏改 `[2,5]` 秒随机 + 约 8% 长停顿 `[15,30]`。
#   ⑤ 后台 PDF 的耗时单独统计（日志「↓ PDF 已落盘 xxx（x.xs）」+ 收尾汇总），用于持续观察风控反馈。
#   ⑥ 日志节流：落库是对每条都做，但**日志每 5 条打一次**，避免刷屏。
# v1.2.7：① 「线上覆盖」指标落地（用户 09-13 拍板）——每次检索后把 REINS 报的「結果 N 件」按组落库到新表
#            online_stats（CREATE TABLE IF NOT EXISTS，免迁移）；概览新增「线上覆盖」KPI：
#            线上 X ／ 本地 Y ／ 覆盖 Z%（口径见 docs/PRD/01 的 R8）；没有数据时如实显示「—」而不是 0。
#          ② 逐条耗时埋点：_fetch_detail 量出 **PDF 下载耗时**，边抓边落库那行日志追加
#            「PDF 均耗时 x.xs（n=N）」—— 用数据回答"到底哪一段慢"（用户质疑我说的"12 秒砍不掉"）。
#          ③ **更正上一版的结论**：PDF 是 `dp.expect_download()` 串行等待（点链接 → 等整个文件落盘），
#            这是**架构选择**，不是物理限制；把它挪到后台下载可省 5–10 秒/条。
#            该改动触及"不并发"红线（用户 09-12 亲自拍板），**待用户重新确认后再实施**。详见 docs/PRD/05。
# v1.2.6：PM 澄清「本地 345 vs 线上 406 差值为什么这么大」——概览 KPI 口径说清 + 当日统计拆开。
#          澄清结论：① REINS 406件 = 线上该检索条件下**现存总数**（快照，日志实测同条件读到 447～500 件）；
#          ② 本地 345 = 我们**累计入库**量（公寓系 267 / 土地 67 / 一户建 11），故 406−267≈139 条是"还没下下来"，
#             不是"只统计新增"；③「今日新盘 63」= 当天**首次入库**套数，与线上总数不是同一口径，不能直接比。
#          根因（已由 v1.2.3 修，但用户当时**还没重启**，15:27 那轮仍在跑旧逻辑）：
#             旧更新轮按保存条件 01→02 顺序跑，条件 02（売マンション）实测每次「结果 未知」后 2 秒内本轮结束，
#             公寓在更新轮里基本没被翻页抓取 —— 公寓主要靠「指定日期下载」分批入库。
#          本版改动：
#            ① 概览 KPI「今日新盘」→「今日更新」：大数字 新增 / 变更 并排（用户要的斜杠显示），
#               下方小字「合计 N」（用户要的更新总数）——三个数天然自洽（新增+变更=合计）。
#            ② 当日统计口径（store.stats）：一套房当天只归一类，任何变更类记录（modified/price_down/price_up）
#               都算「变更」（哪怕它同时也是当天首次入库）；且一律按 property_no 去重（同一天多条流水不重复计）。
#            ③ 列表行标签同样让「变更」优先于「新增」（web/app.py api_query）：平台早已改价、我方首抓的房
#               显示「变更」+ ↓「平台」，不再显示「新增」（落实 PRD 6.11 拍板）。
#            ④ 修回填日期隐患：backfill_platform_changes 原来不传日期 → detected_at 取 now()，
#               重启当天会把 79 条陈年平台改价算成"今日变更/今日降价"。现按 first_seen_at（兜底：平台変更年月日）回填。
#            ⑤ 和暦→公元年 收敛到单一实现 core/wareki.py（core 回填与 web 展示共用，避免两处正则口径不一致）。
#          详见 PRD 6.12（含"线上覆盖"指标待拍板）。
# v1.2.5：PM 澄清升级「新增/变更 语义对齐平台」——把"新增/变更"改为双信号判定。
#          根因：differ.classify 首抓（old_row is None）无条件写 new，没看平台的 previous_price/change_date，
#          导致"平台早已改过价、我方才首抓"的房源被错判"新增"，与平台对不齐。
#          修复（后端 + 前端）：① differ.classify 首抓分支先查 previous_price——有价差则写 price_down/price_up（不写 new），
#          列表即标"变更"、时间取平台 change_date；② 存量"伪新增"（有 previous_price）启动即一次性回填为变更
#          （store.backfill_platform_changes，幂等）；③ 列表/详情/对比三处平台降价标补「变更日」（和暦→公元年）；
#          降价+涨价都算"变更"（箭头区分方向）。与 v1.2.4 互补：蓝底「平台」降价标保留，和正确的"变更"标并列。详见 PRD 6.11。
# v1.2.4：PM 澄清「新增行为什么带降价」——视觉区分两种降价来源。
#          根因：「新增/变更」状态标来自本机 changes 表（首次入库=新增），而降价徽标来自 REINS「変更前価格」
#          （平台历史降价，发生在本系统下载之前）。两线独立：库证 changes 表从无 price_down 记录
#          （系统跳过已有 detail_json 的房源、永不重抓，故永远检测不到自家监测的降价）。
#          修复（纯前端，后端零改动）：给"平台历史降价"加蓝/中性胶囊 + 「平台」小标签
#          （search/detail/compare 三处统一），与（未来能力）绿底"本系统监测降价"区分；i18n 补 row.src_platform/system。
# v1.2.3：更新（自动/手动/正式下载）**固定覆盖 6 个物件種目**——修「更新只下一户建、公寓一条没下」。
#          根因：更新轮次是按 REINS「保存条件 01/02」顺序跑的，范围完全取决于线上保存条件里装了什么；
#          且条件 01（売土地+売一戸建）排在前，一旦撞上"单轮时长上限"就整轮 break 跳出 →
#          排在后面的公寓永远轮不到。修法：
#            ① config → search.update_groups 显式声明要覆盖的 6 个種目（一户建 新築/中古戸建 +
#               公寓 新築/中古マンション + 新築/中古タウン），按 REINS 原生槽位拆成 3 组检索；
#            ② 「保存条件」降级为**只带出地区（大阪府/大阪市）基线**，抓什么種目由 update_groups 决定，
#               并在填種目前先复位两行 6 个槽位（清掉保存条件的残留，避免多抓）；
#            ③ 时长上限从"整轮"改为**每组**（crawl.max_minutes_per_group，默认 25 分钟）：
#               到点只收尾本组、继续下一组，保证 6 个種目都被跑到；
#            ④ 「指定日期下载 / 正式下载 / 实时预览」同一套分组逻辑（前端第 4 步種目改分组多选，
#               默认勾好这 6 个），做到"哪里更新都下这 6 个"。详见 6.5 / 6.9。
# v1.2.2：边抓边落库——修「下载成功却看不到数据」的根因。
#          老流程是「整轮抓完 → 最后一次性 ingest」：一轮真实抓取按模拟人工节奏可能跑几小时，
#          这段时间里本地库 / PDF / Excel 全是空的，用户看到日志在刷、去查却一条都没有，
#          会误判成"下载成功但没数据"。现改为每抓 flush_every(=5) 条就写一次库 + PDF，
#          runs 表实时刷新进度，查询页「今天 0 条」会显示「正在抓取：已落库 N 条」并每 20s 自动刷新；
#          PDF 即时落到 data/attachments（「本地文件」页立刻可见）；中途中断也只丢最后不足一批的几条。
#          另加「单轮时长上限（v1.2.3 起改为**每组** crawl.max_minutes_per_group，默认 25 分钟）」：到点收尾入库 + 导 Excel/按天页，
#          剩下房源留给下一轮（已入库的下一轮会跳过详情、跑得很快）。详见 6.8。
# v1.2.1：登录会话过期自愈——REINS 回「セッションがタイムアウト」时，抓取入口
#          （auto 轮次 / 手动 / 测试查询 / 指定日期预览与下载）会自动用本机保存的账号
#          重登一次再重试，成功则整轮继续，失败才停机并提示点「手工登录」。
#          修掉「会话一过期、每轮都空转拿 0 条，而程序只会报停机」的老问题。详见 6.3。
#          「指定日期」默认值＝当天（本地时区），并与查询页保持一致；模板侧另有前端兜底。
# v1.2.0：结构改版——「指定日期下载」并入「数据抓取设置 → 第 4 步 下载条件」（导航 8 项→7 项）；
#          更新方式改「随机间隔·分钟制」、按钮改名「保存并启动」；下载条件分核心/可选/补充三层；
#          正式下载合并为一个按钮；查询页对比栏改「名称/地址(含区)/専有面積/价格」；
#          查询页用 localStorage 记住上次条件；对比页列头精简为「位置/名称/价格/面积」；
#          新增公共字段词典 web/static/fieldmap.js，展开区英文字段名四语言化。
# v1.1.12：修「下载条数比线上少」（每保存条件各一份配额 + 5000/300 上限 + 一次检索填满 4 个種目槽位 + 所有権のみ）；
#          修「对比栏概况小卡整片消失」（局部变量遮蔽全局 i18n 函数 t() 导致渲染中断）。
# v1.0.0：登录链路真机修复（有头模式 + 校准 Vue SPA 选择器 + 遵守条款勾选）；
#          全站页面按 PM/架构视角重整（新增「数据抓取设置」，仪表盘瘦身）；UI 统一刷新。
