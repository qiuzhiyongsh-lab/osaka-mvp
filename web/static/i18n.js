/* i18n.js — 页面语言切换（仅 UI 文案；单位/房源数据不变）
 * 用法：
 *   - 静态文案：<span data-i18n="key">中文</span>
 *   - 占位符：   <input data-i18n-ph="key">
 *   - 标题属性： <button data-i18n-title="key">
 *   - JS 动态串： window.t('key')  （zh-CN 缺失时回退到中文）
 * 选择持久化在 localStorage.lang，默认 zh-CN。
 */
(function () {
  'use strict';
  var LANGS = [
    { code: 'zh-CN', label: '简' },
    { code: 'zh-TW', label: '繁' },
    { code: 'en',   label: 'EN' },
    { code: 'ja',   label: '日' }
  ];

  // 仅存目标语言；zh-CN 用元素原始文案（首次 apply 时缓存）
  var DICT = {
    'zh-TW': {
      'nav.index': '概覽', 'nav.collect': '數據抓取設定', 'nav.search': '房源查詢',
      'nav.specified': '指定日期下載', 'nav.compare': '房源對比', 'nav.account': '帳號管理',
      'nav.files': '本地檔案', 'nav.runs': '執行日誌',
      'badge.live': '真實抓取', 'badge.demo': '離線示範',
      'footer.local': '數據全部保存在本機：',
      'footer.window': '僅在 07:00–23:00 內自動更新（REINS 其餘時間維護）',
      'footer.ver': '版本', 'footer.updated': '更新於',
      'logo': '大阪房源', 'logo.em': '本地版',
      'lang.label': '語言',

      'ov.title': '概覽', 'ov.auto': '每 60 秒自動重整',
      'ov.refresh': '🔄 重新整理', 'ov.last': '上次重整',
      'kpi.total': '本機房源總數', 'kpi.today_new': '今日新盤', 'kpi.today_down': '今日降價',
      'kpi.pdf': '已落盤 PDF', 'kpi.last_run': '最近一次更新',
      'kpi.today_update': '今日更新（新增 / 變更）', 'kpi.sum': '合計',
      'kpi.coverage': '線上覆蓋', 'kpi.cov_online': '線上', 'kpi.cov_local': '本地',
      'status.auto': '自動更新：', 'status.running': '執行中', 'status.stopped': '已停止',
      'status.next': '下一輪', 'status.busy': '正在進行', 'status.outwin': '當前不在執行時段',
      'btn.manual': '▶ 手動更新（立刻抓一輪）', 'btn.trial': '試跑（最輕量）',
      'btn.manual_running': '正在執行中…（完成後自動提示）', 'btn.manual_done': '本次已完成 ✓（再點重新跑）', 'btn.manual_busy': '系統更新中（自動）',
      'detail.reins_search': 'REINS 物件番号検索', 'detail.search_hint': '（詳細頁需在 REINS 內點擊「詳細」開啟，無永久直鏈）',
      'sync.over': '本機在架比線上快照多 {n} 條（時差/口徑，非真實覆蓋溢出）',
      'btn.collect': '數據抓取設定 →',
      'recent.changes': '最近變更', 'hint.newdownchg': '新盤 / 降價 / 變更',
      'th.time': '時間', 'th.property': '房源', 'th.type': '類型', 'th.old': '原值', 'th.new': '新值',
      'ch.new': '新規', 'ch.price_down': '降價', 'ch.price_up': '漲價', 'ch.modified': '變更',
      'empty.changes': '暫無變更記錄。先點上面的「手動更新」跑一輪。',
      'recent.runs': '最近執行', 'hint.fullog': '完整記錄見「執行日誌」',
      'th.id': '#', 'th.start': '開始', 'th.trigger': '觸發', 'th.scanned': '掃描',
      'th.fetched': '落庫', 'th.new': '新盤', 'th.change': '變更', 'th.status': '狀態',
      'run.ok': '成功', 'run.running': '進行中', 'run.fail': '失敗',
      'link.allruns': '查看全部執行日誌 →', 'empty.runs': '還沒有執行記錄，先點「手動更新」跑一輪。',

      'collect.h1': '數據抓取設定',
      'collect.sub': '把 REINS 的數據抓回本機：先確保能登入、能查詢，再正式開跑。',
      'collect.mode.live': '真實抓取（REINS）', 'collect.mode.demo': '離線示範（內建樣例）',
      'collect.switch': '切換到',
      'step1.h': '環境自檢', 'step1.hint': '不聯網，只檢查本機是否具備真實抓取條件',
      'step1.btn': '開始自檢',
      'step1.desc': '檢查三件事：Playwright 庫是否安裝、Edge 瀏覽器能否被自動驅動、登入作業階段檔是否已存在。',
      'step2.h': '帳號與登入', 'step2.hint': '錄入帳號後才能自動登入',
      'step2.to': '帳號管理 →',
      'tag.saved': '已保存帳號', 'tag.nosaved': '未保存',
      'step2.nosaved': '還沒錄入帳號 —— 到「帳號管理」填會員ID + 密碼並保存',
      'step2.test': '① 測試登入', 'step2.manual': '手工登入（開啟瀏覽器）',
      'step2.tip': '測試登入會彈出瀏覽器視窗（REINS 攔截無視窗瀏覽器，必須用可見視窗）',
      'step2.sess': '最近登入', 'step2.sess_tip': '（工作階段有時效；過期時程式會自動用本機帳號重新登入一次）',
      'step2.sess_none': '尚未登入過 —— 請先點「① 測試登入」或「手工登入」',
      'step3.h': '測試查詢與下載', 'step3.hint': '先小量驗證，再正式下載',
      'step3.q': '② 測試查詢', 'step3.dl': '③ 測試下載數據', 'step3.real': '④ 真實下載數據',
      'step3.d0': '② 只跑 1 個最小查詢，返回條數 + 樣本（<b>不落庫</b>）；',
      'step3.d1': '③ 真實下載但限小量（≤3 條 + PDF），驗證鏈路通不通；',
      'step3.d2': '④ 按配置全量跑一輪，寫入正式庫（SQLite + Excel + 按天頁 + PDF）。',
      'step4.h': '更新方式', 'step4.hint': '目前規則：',
      'step4.save': '保存並', 'step4.start': '啟動', 'step4.saveonly': '保存設定（暫不啟動）',
      'step4.stop': '停止自動更新',
      'pub.h': '上傳到線上', 'pub.hint': '把本機抓到的房源推到線上網站（只傳詳情內容，不傳 PDF）',
      'pub.mode': '上傳方式', 'pub.mode.auto': '① 自動完成（每輪抓完自動增量推送）', 'pub.mode.manual': '② 手動方式（點按鈕才上傳）',
      'pub.endpoint': '線上地址', 'pub.month': '上傳月份', 'pub.caliber': '月份口徑',
      'pub.date_from': '上傳日期 起', 'pub.date_to': '上傳日期 止', 'pub.auto': '自動送信タイマー',
      'pub.interval': '送信間隔（分）', 'pub.preview': '預覽會傳多少', 'pub.on': '運行中',
      'pub.off': '已暫停', 'pub.pause': '暫停', 'pub.start': '啟動', 'pub.next': '下次送信：',
      'pub.every': '每 ', 'pub.min': ' 分鐘自動上傳', 'pub.paused_tip': '已暫停（點「啟動」即按週期自動上傳）', 'pub.log_empty': '暫無上傳記錄',
      'pub.previewing': '計算中…', 'pub.preview_count': '範圍內共 ', 'pub.preview_fail': '預覽失敗',
      'pub.caliber.dl': '下載日（本機抓到的月份）', 'pub.caliber.pl': '平台登錄/變更日',
      'pub.save': '保存上傳設置', 'pub.btn_full': '立即上傳（全量重傳）', 'pub.btn_incr': '增量推送（只傳新增 / 變化）',
      'pub.last': '上次上傳：', 'pub.nopdf': '不傳 PDF，只傳詳情內容', 'pub.never': '還沒傳過', 'pub.saved': '上傳設置已保存',
      'step4.manual': '手動更新 = 點一下按鈕，立刻按你點擊的<b>目前時間節點</b>抓一輪（在「概覽」頁）。',
      'testbox.h': '測試結果', 'testbox.hint': '四個測試按鈕的最近一次結果',
      'testbox.empty': '還沒有測試記錄。按上面 1→4 步依序點一遍即可。',
      'log.h': '即時日誌',

      'search.h': '查詢本地房源', 'search.hint': '數據全部來自本機庫（檢索 + 詳情 + PDF）',
      'search.q': '關鍵詞',                       /* v1.4.1：只留兩個字，完整說明移到 search.q.tip 懸停 */
      'search.q.ph': '例：中之島 / グランドメゾン / 天下茶屋 / 3001407',
      'search.q.tip': '地址 / 樓名 / 物件番号 / 駅・沿線 / 間取り，模糊匹配，含即命中；空格分隔多詞＝都要命中',
      'search.ward': '區', 'search.subtype': '物件種目', 'search.all': '全部',
      'search.price_min': '價格下限(萬円)', 'search.price_max': '價格上限(萬円)',
      'search.area_min': '面積下限(㎡)', 'search.area_max': '面積上限(㎡)',
      'search.price_min.ph': '例：2000', 'search.price_max.ph': '例：5000','search.unit_price_sqm':'㎡單價','search.unit_price_tsubo':'坪單價','search.na':'—',
      'search.area_min.ph': '例：50', 'search.area_max.ph': '例：80',
      'search.date': '日期', 'search.has_pdf': '只看帶 PDF', 'search.has_pdf.all': '不限',
      'search.has_pdf.yes': '僅帶 PDF', 'search.limit': '每頁條數',
      'search.date_caliber': '日期口徑', 'search.date_caliber.any': '登録日或変更日',
      'search.date_caliber.change': '変更日（平台變更）', 'search.date_caliber.reg': '登録日（平台新建）',
      'search.date_caliber.dl': '下載日（本地抓到）',
      /* v1.7.4 R1 日期快捷檔 + 起止自動調換 + R8 取引態様 */
      'search.quick': '日期快捷：', 'search.quick.today': '今天',
      'search.quick.d3': '近 3 天', 'search.quick.d7': '近 7 天',
      'search.quick.d30': '近 30 天', 'search.quick.all': '全部',
      'search.date_swap': '起止日期已自動調換',
      'search.trade_type': '取引態様',
      'search.trade_type.all': '全部', 'search.trade_type.seller': '売主（業主直售）',
      'search.trade_type.sennin': '専任（專任媒介）', 'search.trade_type.senzoku': '専属（專屬媒介）',
      'search.trade_type.dairi': '代理（賣方代理）', 'search.trade_type.ippan': '一般（一般媒介）',
      'search.limit.50': '50 條', 'search.limit.100': '100 條', 'search.limit.200': '200 條',
      'search.btn': '查詢', 'search.reset': '重置',
      'search.cmp.tip': '勾幾套房 → 底部「對比欄」→ 點「開始對比」可橫向比 2–6 套',
      'search.loading': '正在載入當天數據…', 'search.fail': '查詢失敗，請重試。',
      'search.none': '沒有匹配的房源。', 'search.nodata': '該日期 / 條件下沒有房源。',
      'search.lib.base': '本機庫共', 'search.lib.today': '今天', 'search.lib.latest': '最近有數據：',
      'search.lib.warn': '⚠ 本機庫<b>今天（',
      'search.lib.warn2': '）還沒有任何數據</b>。多半是「當天的下載」還沒跑，或者<b>登入作業階段已過期</b>（此時抓取會一聲不吭地入庫 0 條）。',
      'search.lib.running': '正在抓取：已落庫 {n} 條（新盤 {newn}）。', 'search.lib.runningtip': '資料正在寫入，稍候重新整理即可看到。',
      'search.lib.err': '最近一輪下載未成功：', 'search.lib.go': '去「數據抓取設定」跑一輪',
      'search.lib.view': '查看最近有數據的日期（',
      'cmp.name': '對比欄', 'cmp.sel': '已選', 'cmp.go': '開始對比', 'cmp.clear': '清空',
      'cmp.full': '最多同時對比', 'pager.first': '首頁', 'pager.prev': '上一頁',
      'pager.next': '下一頁', 'pager.last': '末頁', 'pager.of': '第', 'pager.page': '頁（共',

      'spec.h': '指定日期下載',
      'spec.hint': '手動設定所有選項 → 點「確定」即時預覽 → 點「下載」正式全部下載（數據 + 図面PDF）',
      'spec.pause': '下載期間會<strong>自動暫停「當天的下載」</strong>，等指定日期下載完成後才恢復，保證同一時刻只有一條 REINS 抓取在跑。',
      'spec.kind': '物件種別（必填）', 'spec.kind.ph': '— 請選擇 —', 'spec.price_min.ph': '例：2000', 'spec.price_max.ph': '例：5000', 'spec.area_min.ph': '例：50', 'spec.area_max.ph': '例：80',
      'spec.subtype': '物件種目（可選）', 'spec.ward': '區（大阪市，可選）',
      'spec.price_min': '價格下限(萬円)', 'spec.price_max': '價格上限(萬円)',
      'spec.area_min': '面積下限(㎡)', 'spec.area_max': '面積上限(㎡)',
      'spec.line': '沿線名（可選）', 'spec.station': '駅名（可選）',
      'spec.datetype': '日期類型', 'spec.date': '指定日期（必填，YYYY-MM-DD）',
      'spec.date_reg': '登録年月日（註冊日）', 'spec.date_chg': '変更年月日（變更日）',
      'spec.ok': '① 確定（即時預覽）', 'spec.dl': '② 下載（正式全部下載）',
      'spec.ownonly': '土地權利／借地權：僅所有權（推薦，與線上口徑一致）',
      'spec.preview.h': '① 預覽結果', 'spec.dl.h': '② 下載進度',
      'spec.empty.preview': '點「確定」後，這裡顯示即時連 REINS 查到的條數 + 樣本。',
      'spec.empty.dl': '點「下載」後，這裡顯示正式下載（數據 + 図面PDF）的進度與落庫統計。',
      'spec.pausenote': '⏸ 當天的下載已暫停（指定日期下載進行中），完成後自動恢復。',
      'spec.querying': '即時連 REINS 查詢中…', 'spec.err': '出錯', 'spec.done': '· 完成',
      'spec.sample.of': '即時查詢到', 'spec.sample.tail': '（樣本', 'spec.nomatch': '該條件下沒有匹配房源。',
      'spec.downloading': '正式下載中（數據 + 図面PDF）…',
      'spec.stat.fetched': '落庫', 'spec.stat.new': '新盤', 'spec.stat.changed': '變更', 'spec.stat.pdf': 'PDF',

      'cmp.h1': '房源橫向對比',
      'cmp.sub': '把 2–6 套房放進同一張表逐項對照：<b>地段 · 位置 · 價格 · 面積 · 建物 · 持有成本</b>。選中的房子記在本機瀏覽器裡，重整/切頁面都不會丟；數據即時從本機庫讀，永遠是最新的。',
      'cmp.back': '← 回到房源查詢', 'cmp.clear2': '清空對比欄',
      'cmp.viewmode': '查看方式：', 'cmp.best': '標出最優', 'cmp.hidesame': '隱藏相同項', 'cmp.core': '只看核心參數',
      'cmp.notenough': '對比要 2–6 套房，現在還不夠', 'cmp.only1a': '你目前只選了',
      'cmp.only1b': '套：', 'cmp.only1c': '再選', 'cmp.only1d': '套就能橫向對比（最多 6 套）——回『房源查詢』頁，在別的房子左邊的「加入對比」上點一下即可。',
      'cmp.none': '對比欄裡還沒有房子。', 'cmp.none2': '去『房源查詢』頁，點每行左邊的「', 'cmp.none3': '」，選 2–6 套，再點底部的「開始對比」。',
      'cmp.none4': '也可以從某套房的詳情頁點「加入對比」，把這一套先放進來。', 'cmp.gosearch': '去選房子',
      'cmp.notfound': '這些房子在本機庫裡已經找不到了（可能被清理過）。',
      'cmp.summary.h': '一眼結論', 'cmp.summary.hint': '按選中這 {n} 套自動算出來的', 'cmp.hint.slide': '← 手機上左右滑動表格，可看到全部 {n} 套房 →',
      'cmp.cfg': '配置狀況', 'cmp.drop': '把這套移出對比', 'cmp.detail': '看詳情頁',
      'cmp.extra': '詳情頁的其他資訊', 'cmp.over': '最多同時對比', 'cmp.ignore': '套，多出的', 'cmp.skipped': '套已忽略。', 'cmp.missing': '{n} 套不在本機庫裡，已跳過。',

      'detail.h': '房源詳情', 'detail.back': '← 回到查詢', 'detail.sub': '一套房的全部資訊（資料來自本機庫，REINS 欄位原樣保留）。',
      'detail.openpdf': '打開 PDF', 'detail.tobank': '加入對比', 'detail.fromcmp': '已加入 ✓',
      'detail.notfound': '沒有這套房源（可能從未抓過，或已被清理）。', 'detail.back2': '回查詢頁',

      'files.h': '本地文件', 'files.sub': '抓回來的數據都落在這裡：Excel 總表、按天網頁、每套房的 PDF 圖紙。',
      'files.excel': 'Excel 總表', 'files.daily': '按天網頁', 'files.pdf': '図面 PDF',
      'files.open': '打開', 'files.none': '（暫無）', 'files.noping': '服務未連線，無法打開。',

      'runs.h': '運行日誌', 'runs.sub': '每一輪抓取的完整過程：開始/結束、掃描多少、落庫多少、成功或失敗。',
      'runs.empty': '還沒有運行記錄。', 'runs.refresh': '🔄 重新整理', 'runs.tail': '即時日誌（自動滾動）',
      'runs.live': '即時日誌',

      'account.h': '帳號管理', 'account.sub': '填寫 REINS 的會員 ID 與密碼，保存後即可自動登入。密碼只存在本機，不會上傳。',
      'account.site': '網站地址', 'account.site.ph': 'https://system.reins.jp', 'account.name': '帳號名稱',
      'account.mid': '會員 ID', 'account.pw': '密碼', 'account.pw.ph': '填寫後保存即更新',
      'account.save': '保存', 'account.test': '測試登入', 'account.saved': '已保存',
      'account.unsaved': '尚未保存', 'account.lastlogin': '上次登入', 'account.never': '從未',
      'account.ok': '已保存帳號', 'account.testing': '測試登入中…', 'account.oktip': '測試登入：'
    },

    'en': {
      'nav.index': 'Overview', 'nav.collect': 'Data Capture', 'nav.search': 'Search',
      'nav.specified': 'Specific-date Download', 'nav.compare': 'Compare', 'nav.account': 'Account',
      'nav.files': 'Local Files', 'nav.runs': 'Run Logs',
      'badge.live': 'Live', 'badge.demo': 'Demo',
      'footer.local': 'All data stored locally:',
      'footer.window': 'Auto-update only 07:00–23:00 (REINS maintenance otherwise)',
      'footer.ver': 'Version', 'footer.updated': 'Updated',
      'logo': 'Osaka Homes', 'logo.em': 'Local',
      'lang.label': 'Language',

      'ov.title': 'Overview', 'ov.auto': 'Auto-refresh every 60s',
      'ov.refresh': '🔄 Refresh', 'ov.last': 'Last refresh',
      'kpi.total': 'Total local listings', 'kpi.today_new': 'New today', 'kpi.today_down': 'Price drops today',
      'kpi.pdf': 'PDFs saved', 'kpi.last_run': 'Last update',
      'kpi.today_update': 'Updated today (new / changed)', 'kpi.sum': 'Total',
      'kpi.coverage': 'Online coverage', 'kpi.cov_online': 'Online', 'kpi.cov_local': 'Local',
      'status.auto': 'Auto-update: ', 'status.running': 'Running', 'status.stopped': 'Stopped',
      'status.next': 'Next run', 'status.busy': 'In progress', 'status.outwin': 'Outside run window',
      'btn.manual': '▶ Manual update (fetch now)', 'btn.trial': 'Trial run (lightest)',
      'btn.manual_running': 'Running… (auto-notifies when done)', 'btn.manual_done': 'Done this run ✓ (click to re-run)', 'btn.manual_busy': 'System updating (auto)',
      'detail.reins_search': 'REINS property-no search', 'detail.search_hint': '(detail opens only by clicking 詳細 inside REINS; no permanent link)',
      'sync.over': 'Local live exceeds online snapshot by {n} (time/lens drift, not real overflow)',
      'btn.collect': 'Data Capture →',
      'recent.changes': 'Recent changes', 'hint.newdownchg': 'New / Drop / Change',
      'th.time': 'Time', 'th.property': 'Property', 'th.type': 'Type', 'th.old': 'Old', 'th.new': 'New',
      'ch.new': 'New', 'ch.price_down': 'Drop', 'ch.price_up': 'Up', 'ch.modified': 'Changed',
      'empty.changes': 'No change records yet. Click "Manual update" above to run a round.',
      'recent.runs': 'Recent runs', 'hint.fullog': 'Full log in "Run Logs"',
      'th.id': '#', 'th.start': 'Start', 'th.trigger': 'Trigger', 'th.scanned': 'Scanned',
      'th.fetched': 'Saved', 'th.new': 'New', 'th.change': 'Changed', 'th.status': 'Status',
      'run.ok': 'OK', 'run.running': 'Running', 'run.fail': 'Failed',
      'link.allruns': 'View all run logs →', 'empty.runs': 'No run records yet. Click "Manual update" to run a round.',

      'collect.h1': 'Data Capture',
      'collect.sub': 'Pull REINS data back to this machine: make sure login & query work, then run for real.',
      'collect.mode.live': 'Live (REINS)', 'collect.mode.demo': 'Offline demo (built-in samples)',
      'collect.switch': 'Switch to',
      'step1.h': 'Environment check', 'step1.hint': 'No network — only checks if this machine can do live capture',
      'step1.btn': 'Start check',
      'step1.desc': 'Checks three things: Playwright installed, Edge drivable, login session file exists.',
      'step2.h': 'Account & login', 'step2.hint': 'Enter account to enable auto-login',
      'step2.to': 'Account →',
      'tag.saved': 'Account saved', 'tag.nosaved': 'Not saved',
      'step2.nosaved': 'No account yet — go to "Account" and fill member ID + password, then save',
      'step2.test': '① Test login', 'step2.manual': 'Manual login (opens browser)',
      'step2.tip': 'Test login opens a browser window (REINS blocks headless browsers, so a visible window is required)',
      'step2.sess': 'Last login', 'step2.sess_tip': '(The session does expire; the app will re-login automatically with the saved account)',
      'step2.sess_none': 'Never logged in — click "① Test login" or "Manual login" first',
      'step3.h': 'Test query & download', 'step3.hint': 'Validate small first, then download for real',
      'step3.q': '② Test query', 'step3.dl': '③ Test download', 'step3.real': '④ Real download',
      'step3.d0': '② runs 1 minimal query, returns count + sample (<b>not saved</b>);',
      'step3.d1': '③ real download but limited (≤3 + PDF) to verify the pipeline;',
      'step3.d2': '④ full round by config, writes the real DB (SQLite + Excel + daily page + PDF).',
      'step4.h': 'Update mode', 'step4.hint': 'Current rule:',
      'step4.save': 'Save &', 'step4.start': 'start', 'step4.saveonly': 'Save (do not start)',
      'step4.stop': 'Stop auto-update',
      'pub.h': 'Publish to website', 'pub.hint': 'Push local listings to the public site (details only, no PDF)',
      'pub.mode': 'Upload mode', 'pub.mode.auto': '(1) Automatic (incremental after each crawl)', 'pub.mode.manual': '(2) Manual (button only)',
      'pub.endpoint': 'Site URL', 'pub.month': 'Month', 'pub.caliber': 'Month basis',
      'pub.date_from': 'From date', 'pub.date_to': 'To date', 'pub.auto': 'Auto-upload timer',
      'pub.interval': 'Interval (min)', 'pub.preview': 'Preview count', 'pub.on': 'Running',
      'pub.off': 'Paused', 'pub.pause': 'Pause', 'pub.start': 'Start', 'pub.next': 'Next upload: ',
      'pub.every': 'Every ', 'pub.min': ' min auto-upload', 'pub.paused_tip': 'Paused (click Start to auto-upload by interval)', 'pub.log_empty': 'No upload records yet',
      'pub.previewing': 'Calculating…', 'pub.preview_count': 'In range: ', 'pub.preview_fail': 'Preview failed',
      'pub.caliber.dl': 'Download date', 'pub.caliber.pl': 'Platform registration/change date',
      'pub.save': 'Save upload settings', 'pub.btn_full': 'Upload now (full)', 'pub.btn_incr': 'Incremental push',
      'pub.last': 'Last upload: ', 'pub.nopdf': 'No PDF, details only', 'pub.never': 'Never uploaded', 'pub.saved': 'Upload settings saved',
      'step4.manual': 'Manual update = one click, fetches a round at the <b>current moment</b> (on "Overview").',
      'testbox.h': 'Test results', 'testbox.hint': 'Latest result of the four test buttons',
      'testbox.empty': 'No test records yet. Click steps 1→4 in order.',
      'log.h': 'Live log',

      'search.h': 'Search local listings', 'search.hint': 'All data from the local DB (search + detail + PDF)',
      'search.q': 'Keyword',                     /* v1.4.1: shortened; full rules moved to search.q.tip */
      'search.q.ph': 'e.g. Nakanoshima / Grand Maison / Tennoji / 3001407',
      'search.q.tip': 'Address / building / property no / station・line / layout — fuzzy match, any hit counts. Separate words with a space = ALL must match',
      'search.ward': 'Ward', 'search.subtype': 'Type', 'search.all': 'All',
      'search.price_min': 'Price min (10k ¥)', 'search.price_max': 'Price max (10k ¥)',
      'search.area_min': 'Area min (㎡)', 'search.area_max': 'Area max (㎡)',
      'search.price_min.ph': 'e.g. 2000', 'search.price_max.ph': 'e.g. 5000','search.unit_price_sqm':'Price/㎡','search.unit_price_tsubo':'Price/坪','search.na':'—',
      'search.area_min.ph': 'e.g. 50', 'search.area_max.ph': 'e.g. 80',
      'search.date': 'Date', 'search.has_pdf': 'PDF only', 'search.has_pdf.all': 'Any',
      'search.has_pdf.yes': 'With PDF', 'search.limit': 'Per page',
      'search.date_caliber': 'Date basis', 'search.date_caliber.any': 'Registered or changed',
      'search.date_caliber.change': 'Change date', 'search.date_caliber.reg': 'Registration date',
      'search.date_caliber.dl': 'Download date (locally fetched)',
      /* v1.7.4 R1 date shortcuts + auto-swap + R8 trade type */
      'search.quick': 'Quick:', 'search.quick.today': 'Today',
      'search.quick.d3': 'Last 3 days', 'search.quick.d7': 'Last 7 days',
      'search.quick.d30': 'Last 30 days', 'search.quick.all': 'All',
      'search.date_swap': 'Start/end dates swapped automatically',
      'search.trade_type': 'Transaction type',
      'search.trade_type.all': 'All', 'search.trade_type.seller': 'Owner-direct (売主)',
      'search.trade_type.sennin': 'Exclusive (専任)', 'search.trade_type.senzoku': 'Sole agency (専属)',
      'search.trade_type.dairi': 'Agency (代理)', 'search.trade_type.ippan': 'General (一般)',
      'search.limit.50': '50', 'search.limit.100': '100', 'search.limit.200': '200',
      'search.btn': 'Search', 'search.reset': 'Reset',
      'search.cmp.tip': 'Pick a few → bottom "Compare bar" → "Start compare" to contrast 2–6 homes',
      'search.loading': 'Loading today\'s data…', 'search.fail': 'Query failed, please retry.',
      'search.none': 'No matching listings.', 'search.nodata': 'No listings for that date / condition.',
      'search.lib.base': 'Local DB total', 'search.lib.today': 'today', 'search.lib.latest': 'latest data:',
      'search.lib.warn': '⚠ The local DB has <b>no data for today (',
      'search.lib.warn2': ')</b>. Usually "today\'s download" hasn\'t run, or the <b>login session expired</b> (then capture silently saves 0 rows).',
      'search.lib.running': 'Capturing: {n} saved (new {newn}).', 'search.lib.runningtip': 'Data is being written — refresh shortly to see it.',
      'search.lib.err': 'Last download round failed:',
      'search.lib.go': 'Go to "Data Capture" to run a round',
      'search.lib.view': 'View latest date with data (',
      'cmp.name': 'Compare bar', 'cmp.sel': 'Selected', 'cmp.go': 'Start compare', 'cmp.clear': 'Clear',
      'cmp.full': 'Max', 'pager.first': 'First', 'pager.prev': 'Prev', 'pager.next': 'Next',
      'pager.last': 'Last', 'pager.of': 'Page', 'pager.page': 'of',

      'spec.h': 'Specific-date Download',
      'spec.hint': 'Set all options manually → click "OK" for live preview → click "Download" for the full download (data + floor PDF)',
      'spec.pause': 'During download it <strong>auto-pauses "today\'s download"</strong> and resumes after the specific-date download finishes, so only one REINS crawl runs at a time.',
      'spec.kind': 'Property type (required)', 'spec.kind.ph': '— choose —', 'spec.price_min.ph': 'e.g. 2000', 'spec.price_max.ph': 'e.g. 5000', 'spec.area_min.ph': 'e.g. 50', 'spec.area_max.ph': 'e.g. 80',
      'spec.subtype': 'Sub type (optional)', 'spec.ward': 'Ward (Osaka city, optional)',
      'spec.price_min': 'Price min (10k ¥)', 'spec.price_max': 'Price max (10k ¥)',
      'spec.area_min': 'Area min (㎡)', 'spec.area_max': 'Area max (㎡)',
      'spec.line': 'Line name (optional)', 'spec.station': 'Station (optional)',
      'spec.datetype': 'Date type', 'spec.date': 'Specific date (required, YYYY-MM-DD)',
      'spec.date_reg': 'Registration date', 'spec.date_chg': 'Change date',
      'spec.ok': '① OK (live preview)', 'spec.dl': '② Download (full)',
      'spec.ownonly': 'Land right / leasehold: ownership only (recommended, same as online)',
      'spec.preview.h': '① Preview result', 'spec.dl.h': '② Download progress',
      'spec.empty.preview': 'After clicking "OK", the live REINS count + samples show here.',
      'spec.empty.dl': 'After clicking "Download", the progress and ingest stats show here.',
      'spec.pausenote': '⏸ Today\'s download is paused (specific-date download in progress); auto-resumes when done.',
      'spec.querying': 'Querying REINS live…', 'spec.err': 'Error', 'spec.done': '· Done',
      'spec.sample.of': 'Live query found', 'spec.sample.tail': ' (sample', 'spec.nomatch': 'No matching listings for this condition.',
      'spec.downloading': 'Downloading (data + floor PDF)…',
      'spec.stat.fetched': 'Saved', 'spec.stat.new': 'New', 'spec.stat.changed': 'Changed', 'spec.stat.pdf': 'PDF',

      'cmp.h1': 'Side-by-side Compare',
      'cmp.sub': 'Put 2–6 homes in one table, contrast item by item: <b>location · position · price · area · building · holding cost</b>. Selection is kept in this browser (survives refresh/navigation); data is read live from the local DB, always latest.',
      'cmp.back': '← Back to search', 'cmp.clear2': 'Clear compare bar',
      'cmp.viewmode': 'View mode:', 'cmp.best': 'Mark best', 'cmp.hidesame': 'Hide same', 'cmp.core': 'Core only',
      'cmp.notenough': 'Compare needs 2–6 homes; not enough yet',
      'cmp.only1a': 'You have selected only', 'cmp.only1b': 'home:', 'cmp.only1c': 'Pick',
      'cmp.only1d': 'more to compare (max 6) — back to "Search", click "Add to compare" on another home.',
      'cmp.none': 'The compare bar is empty.', 'cmp.none2': 'Go to "Search", click "',
      'cmp.none3': '" on each row, pick 2–6, then "Start compare" at the bottom.',
      'cmp.none4': 'Or from a home\'s detail page click "Add to compare" to put it in.',
      'cmp.gosearch': 'Go pick homes',
      'cmp.notfound': 'These homes are no longer in the local DB (maybe cleaned up).',
      'cmp.summary.h': 'At a glance', 'cmp.summary.hint': 'auto-computed from these {n}', 'cmp.hint.slide': '← On mobile, swipe the table to see all {n} homes',
      'cmp.cfg': 'Configuration', 'cmp.drop': 'Remove from compare', 'cmp.detail': 'Detail page',
      'cmp.extra': 'Other info from detail page', 'cmp.over': 'Max', 'cmp.ignore': 'homes; extra',
      'cmp.skipped': 'ignored.', 'cmp.missing': '{n} not in local DB, skipped.',

      'detail.h': 'Property detail', 'detail.back': '← Back to search',
      'detail.sub': 'All info for one home (from local DB; REINS fields kept as-is).',
      'detail.openpdf': 'Open PDF', 'detail.tobank': 'Add to compare', 'detail.fromcmp': 'Added ✓',
      'detail.notfound': 'No such home (never captured, or cleaned up).', 'detail.back2': 'Back to search',

      'files.h': 'Local Files', 'files.sub': 'Captured data lands here: Excel summary, daily web page, per-home floor PDF.',
      'files.excel': 'Excel summary', 'files.daily': 'Daily web page', 'files.pdf': 'Floor PDF',
      'files.open': 'Open', 'files.none': '(none)', 'files.noping': 'Service offline, cannot open.',

      'runs.h': 'Run Logs', 'runs.sub': 'Full process of every capture round: start/end, scanned, saved, success/failure.',
      'runs.empty': 'No run records yet.', 'runs.refresh': '🔄 Refresh', 'runs.tail': 'Live log (auto-scroll)',
      'runs.live': 'Live log',

      'account.h': 'Account', 'account.sub': 'Fill REINS member ID & password; after saving, auto-login works. Password stays local only, never uploaded.',
      'account.site': 'Site URL', 'account.site.ph': 'https://system.reins.jp', 'account.name': 'Account name',
      'account.mid': 'Member ID', 'account.pw': 'Password', 'account.pw.ph': 'Saved on change',
      'account.save': 'Save', 'account.test': 'Test login', 'account.saved': 'Saved',
      'account.unsaved': 'Not saved', 'account.lastlogin': 'Last login', 'account.never': 'Never',
      'account.ok': 'Account saved', 'account.testing': 'Testing login…', 'account.oktip': 'Test login: '
    },

    'ja': {
      'nav.index': '概要', 'nav.collect': 'データ取得設定', 'nav.search': '物件検索',
      'nav.specified': '指定日ダウンロード', 'nav.compare': '物件比較', 'nav.account': 'アカウント管理',
      'nav.files': 'ローカルファイル', 'nav.runs': '実行ログ',
      'badge.live': '本番取得', 'badge.demo': 'オフライン演示',
      'footer.local': 'データはすべて本機に保存：',
      'footer.window': '自動更新は 07:00–23:00 のみ（それ以外はREINSメンテ）',
      'footer.ver': 'バージョン', 'footer.updated': '更新日',
      'logo': '大阪物件', 'logo.em': 'ローカル版',
      'lang.label': '言語',

      'ov.title': '概要', 'ov.auto': '60秒ごとに自動更新',
      'ov.refresh': '🔄 更新', 'ov.last': '最終更新',
      'kpi.total': '本機物件総数', 'kpi.today_new': '本日新規', 'kpi.today_down': '本日値下げ',
      'kpi.pdf': '保存済PDF', 'kpi.last_run': '最終取得',
      'kpi.today_update': '本日更新（新規 / 変更）', 'kpi.sum': '合計',
      'kpi.coverage': 'オンライン網羅率', 'kpi.cov_online': 'オンライン', 'kpi.cov_local': 'ローカル',
      'status.auto': '自動更新：', 'status.running': '実行中', 'status.stopped': '停止',
      'status.next': '次回', 'status.busy': '実行中', 'status.outwin': '実行時間外',
      'btn.manual': '▶ 手動更新（今すぐ取得）', 'btn.trial': '試走（軽量）',
      'btn.manual_running': '実行中…（完了すると自動で通知）', 'btn.manual_done': '今回完了 ✓（再クリックで再実行）', 'btn.manual_busy': 'システム更新中（自動）',
      'detail.reins_search': 'REINS 物件番号検索', 'detail.search_hint': '（詳細ページは REINS 内で「詳細」をクリックして開きます。永続リンクなし）',
      'sync.over': '本機在架がオンラインスナップショットより {n} 件多い（時差／口径です。実際のカバー過剰ではありません）',
      'btn.collect': 'データ取得設定 →',
      'recent.changes': '最近の変更', 'hint.newdownchg': '新規 / 値下げ / 変更',
      'th.time': '時間', 'th.property': '物件', 'th.type': '種類', 'th.old': '変更前', 'th.new': '変更後',
      'ch.new': '新規', 'ch.price_down': '値下げ', 'ch.price_up': '値上がり', 'ch.modified': '変更',
      'empty.changes': '変更記録はまだありません。上の「手動更新」で一回取得してください。',
      'recent.runs': '最近の実行', 'hint.fullog': '詳細は「実行ログ」',
      'th.id': '#', 'th.start': '開始', 'th.trigger': 'トリガー', 'th.scanned': '走査',
      'th.fetched': '格納', 'th.new': '新規', 'th.change': '変更', 'th.status': '状態',
      'run.ok': '成功', 'run.running': '実行中', 'run.fail': '失敗',
      'link.allruns': '全実行ログを見る →', 'empty.runs': '実行記録はまだありません。上の「手動更新」で一回取得してください。',

      'collect.h1': 'データ取得設定',
      'collect.sub': 'REINS のデータを本機に取り込みます：ログインと検索ができることを確認してから本番実行。',
      'collect.mode.live': '本番取得（REINS）', 'collect.mode.demo': 'オフライン演示（内蔵サンプル）',
      'collect.switch': '切替：',
      'step1.h': '環境自己診断', 'step1.hint': 'ネット不要。本機が本番取得できるか only 確認',
      'step1.btn': '診断開始',
      'step1.desc': '3点を確認：Playwright 導入済み / Edge を自動操作できる / ログインセッション檔存在。',
      'step2.h': 'アカウントとログイン', 'step2.hint': 'アカウント登録で自動ログイン可能に',
      'step2.to': 'アカウント管理 →',
      'tag.saved': 'アカウント保存済', 'tag.nosaved': '未保存',
      'step2.nosaved': 'アカウント未登録 —— 「アカウント管理」で会員ID＋パスワードを入力し保存',
      'step2.test': '① ログイン検証', 'step2.manual': '手動ログイン（ブラウザ起動）',
      'step2.tip': 'ログイン検証はブラウザ窓口を開きます（REINS は非表示ブラウザを拒否するため可視窓口必須）',
      'step2.sess': '最終ログイン', 'step2.sess_tip': '（セッションには有効期限があります。切れた場合は保存済みアカウントで自動再ログインします）',
      'step2.sess_none': 'まだログインしていません —— 先に「① ログイン検証」か「手動ログイン」を実行',
      'step3.h': '検索とダウンロード検証', 'step3.hint': 'まず少量検証、その後本番',
      'step3.q': '② 検索検証', 'step3.dl': '③ ダウンロード検証', 'step3.real': '④ 本番ダウンロード',
      'step3.d0': '② 最小検索1件のみ、件数＋サンプルを返す（<b>保存しない</b>）；',
      'step3.d1': '③ 本番ダウンロードだが少量（≤3件＋PDF）で経路を確認；',
      'step3.d2': '④ 設定どおり全件取得、正式DBへ書込（SQLite＋Excel＋按天頁＋PDF）。',
      'step4.h': '更新方式', 'step4.hint': '現在の規則：',
      'step4.save': '保存＆', 'step4.start': '開始', 'step4.saveonly': '保存のみ（開始しない）',
      'step4.stop': '自動更新を停止',
      'pub.h': 'サイトへ公開', 'pub.hint': '本機の物件を公開サイトへ送信（詳細のみ、PDF なし）',
      'pub.mode': '送信モード', 'pub.mode.auto': '① 自動（各ラウンド後に差分送信）', 'pub.mode.manual': '② 手動（ボタン操作時のみ）',
      'pub.endpoint': 'サイト URL', 'pub.month': '対象月', 'pub.caliber': '月の基準',
      'pub.date_from': '開始日', 'pub.date_to': '終了日', 'pub.auto': '自動送信タイマー',
      'pub.interval': '送信間隔（分）', 'pub.preview': 'プレビュー（送信なし）', 'pub.on': '稼働中',
      'pub.off': '停止中', 'pub.pause': '一時停止', 'pub.start': '開始', 'pub.next': '次回送信：',
      'pub.every': '每 ', 'pub.min': ' 分自動送信', 'pub.paused_tip': '停止中（「開始」を押すと周期で自動送信）', 'pub.log_empty': '送信履歴なし',
      'pub.previewing': '計算中…', 'pub.preview_count': '範囲内：', 'pub.preview_fail': 'プレビュー失敗',
      'pub.caliber.dl': 'ダウンロード日', 'pub.caliber.pl': '登録日/変更日',
      'pub.save': '送信設定を保存', 'pub.btn_full': '今すぐ送信（全件）', 'pub.btn_incr': '差分送信',
      'pub.last': '最終送信：', 'pub.nopdf': 'PDF なし、詳細のみ', 'pub.never': '未送信', 'pub.saved': '送信設定を保存しました',
      'step4.manual': '手動更新 ＝ ボタン一つで、クリックした<b>その時点</b>に一回取得（「概要」ページ）。',
      'testbox.h': '検証結果', 'testbox.hint': '4つの検証ボタンの最新結果',
      'testbox.empty': '検証記録はまだありません。上の手順1→4を順にクリック。',
      'log.h': 'リアルタイムログ',

      'search.h': '本機物件を検索', 'search.hint': 'データはすべて本機DB（検索＋詳細＋PDF）',
      'search.q': 'キーワード',                   /* v1.4.1：短縮。詳細は search.q.tip のホバーへ */
      'search.q.ph': '例：中之島 / グランドメゾン / 天下茶屋 / 3001407',
      'search.q.tip': '住所／建物名／物件番号／駅・沿線／間取りが部分一致で対象。スペース区切りは「すべて含む」条件',
      'search.ward': '区', 'search.subtype': '物件種目', 'search.all': 'すべて',
      'search.price_min': '価格下限(万円)', 'search.price_max': '価格上限(万円)',
      'search.area_min': '面積下限(㎡)', 'search.area_max': '面積上限(㎡)',
      'search.price_min.ph': '例：2000', 'search.price_max.ph': '例：5000','search.unit_price_sqm':'㎡単価','search.unit_price_tsubo':'坪単価','search.na':'—',
      'search.area_min.ph': '例：50', 'search.area_max.ph': '例：80',
      'search.date': '日付', 'search.has_pdf': 'PDFありのみ', 'search.has_pdf.all': 'すべて',
      'search.has_pdf.yes': 'PDFあり', 'search.limit': '每ページ件数',
      'search.date_caliber': '日付の基準', 'search.date_caliber.any': '登録日または変更日',
      'search.date_caliber.change': '変更日', 'search.date_caliber.reg': '登録日',
      'search.date_caliber.dl': 'ダウンロード日（ローカル取得）',
      /* v1.7.4 R1 日付ショートカット + 自動入替 + R8 取引態様 */
      'search.quick': '日付ショートカット：', 'search.quick.today': '今日',
      'search.quick.d3': '直近3日', 'search.quick.d7': '直近7日',
      'search.quick.d30': '直近30日', 'search.quick.all': 'すべて',
      'search.date_swap': '開始・終了日を自動で入れ替えました',
      'search.trade_type': '取引態様',
      'search.trade_type.all': 'すべて', 'search.trade_type.seller': '売主',
      'search.trade_type.sennin': '専任', 'search.trade_type.senzoku': '専属',
      'search.trade_type.dairi': '代理', 'search.trade_type.ippan': '一般',
      'search.limit.50': '50件', 'search.limit.100': '100件', 'search.limit.200': '200件',
      'search.btn': '検索', 'search.reset': 'リセット',
      'search.cmp.tip': '数件チェック → 下部「比較バー」→ 「比較開始」で2～6件を横断比較',
      'search.loading': '本日のデータを読込中…', 'search.fail': '検索失敗、再試行してください。',
      'search.none': '該当する物件がありません。', 'search.nodata': 'その日付／条件では物件がありません。',
      'search.lib.base': '本機DB総数', 'search.lib.today': '本日', 'search.lib.latest': '最近のデータ：',
      'search.lib.warn': '⚠ 本機DBは<b>本日（',
      'search.lib.warn2': '）のデータがありません</b>。たいてい「本日のダウンロード」が未実行、または<b>ログインセッション期限切れ</b>（この場合取得は無音で0件）。',
      'search.lib.running': '取得中：登録済 {n} 件（新着 {newn}）。', 'search.lib.runningtip': 'データを書き込み中です。しばらくして再読み込みすると見えます。',
      'search.lib.err': '直近のダウンロードが失敗：', 'search.lib.go': '「データ取得設定」で一回実行',
      'search.lib.view': 'データがある最近日付を見る（',
      'cmp.name': '比較バー', 'cmp.sel': '選択中', 'cmp.go': '比較開始', 'cmp.clear': 'クリア',
      'cmp.full': '最大同時比較', 'pager.first': '最初', 'pager.prev': '前へ',
      'pager.next': '次へ', 'pager.last': '最後', 'pager.of': 'ページ', 'pager.page': '／全',

      'spec.h': '指定日ダウンロード',
      'spec.hint': 'すべての項目を手動設定 → 「確定」で即時プレビュー → 「ダウンロード」で正式に全件取得（データ＋図面PDF）',
      'spec.pause': 'ダウンロード中は<b>「本日のダウンロード」を自動停止</b>し、指定日ダウンロード完了後に再開。同時実行は1本のみ。',
      'spec.kind': '物件種別（必須）', 'spec.kind.ph': '— 選択 —', 'spec.price_min.ph': '例：2000', 'spec.price_max.ph': '例：5000', 'spec.area_min.ph': '例：50', 'spec.area_max.ph': '例：80',
      'spec.subtype': '物件種目（任意）', 'spec.ward': '区（大阪市、任意）',
      'spec.price_min': '価格下限(万円)', 'spec.price_max': '価格上限(万円)',
      'spec.area_min': '面積下限(㎡)', 'spec.area_max': '面積上限(㎡)',
      'spec.line': '沿線名（任意）', 'spec.station': '駅名（任意）',
      'spec.datetype': '日付種別', 'spec.date': '指定日（必須、YYYY-MM-DD）',
      'spec.date_reg': '登録年月日', 'spec.date_chg': '変更年月日',
      'spec.ok': '① 確定（即時プレビュー）', 'spec.dl': '② ダウンロード（正式全件）',
      'spec.ownonly': '土地権利／借地権：所有権のみ（推奨・オンライン条件と一致）',
      'spec.preview.h': '① プレビュー結果', 'spec.dl.h': '② ダウンロード進捗',
      'spec.empty.preview': '「確定」を押すと、REINS から即時取得した件数＋サンプルを表示。',
      'spec.empty.dl': '「ダウンロード」を押すと、正式取得（データ＋図面PDF）の進捗と格納統計を表示。',
      'spec.pausenote': '⏸ 本日のダウンロードは停止中（指定日ダウンロード実行中）。完了後に自動再開。',
      'spec.querying': 'REINS に即時接続して検索中…', 'spec.err': 'エラー', 'spec.done': '· 完了',
      'spec.sample.of': '即時検索で', 'spec.sample.tail': '（サンプル', 'spec.nomatch': 'この条件に一致する物件はありません。',
      'spec.downloading': 'ダウンロード中（データ＋図面PDF）…',
      'spec.stat.fetched': '格納', 'spec.stat.new': '新規', 'spec.stat.changed': '変更', 'spec.stat.pdf': 'PDF',

      'cmp.h1': '物件横断比較',
      'cmp.sub': '2～6件を1表にして項目ごと対照：<b>立地・位置・価格・面積・建物・持有コスト</b>。選択は本機ブラウザに保存（更新／移動しても消えない）、データは本機DBから都度読むので常に最新。',
      'cmp.back': '← 検索へ戻る', 'cmp.clear2': '比較バーをクリア',
      'cmp.viewmode': '表示方式：', 'cmp.best': '最優を強調', 'cmp.hidesame': '同一項目を非表示', 'cmp.core': '核心項目のみ',
      'cmp.notenough': '比較には2～6件必要、まだ不足',
      'cmp.only1a': '現在', 'cmp.only1b': '件のみ選択中：', 'cmp.only1c': 'あと',
      'cmp.only1d': '件選ぶと横断比較可能（最大6件）——「物件検索」へ戻り、別の物件の左「比較に追加」をクリック。',
      'cmp.none': '比較バーは空です。', 'cmp.none2': '「物件検索」へ行き、各行左の「',
      'cmp.none3': '」をクリック、2～6件選んで下部「比較開始」を押す。',
      'cmp.none4': 'または物件詳細ページの「比較に追加」で1件入れることも可。',
      'cmp.gosearch': '物件を選びに行く',
      'cmp.notfound': 'これらの物件は本機DBに見つかりません（削除された可能性）。',
      'cmp.summary.h': 'ひと目で結論', 'cmp.summary.hint': '選択した {n} 套で自動算出', 'cmp.hint.slide': '← 携帯では表を左右スワイプで全件表示（{n} 套）',
      'cmp.cfg': '設定状況', 'cmp.drop': '比較から除外', 'cmp.detail': '詳細ページ',
      'cmp.extra': '詳細ページのその他情報', 'cmp.over': '最大同時比較', 'cmp.ignore': '件、超過分',
      'cmp.skipped': '件は無視。', 'cmp.missing': '{n} 件は本機DBになくスキップ。',

      'detail.h': '物件詳細', 'detail.back': '← 検索へ戻る',
      'detail.sub': '1件の全情報（本機DBから。REINS項目はそのまま保持）。',
      'detail.openpdf': 'PDFを開く', 'detail.tobank': '比較に追加', 'detail.fromcmp': '追加済 ✓',
      'detail.notfound': 'その物件はありません（未取得、または削除済）。', 'detail.back2': '検索へ戻る',

      'files.h': 'ローカルファイル', 'files.sub': '取得データはここに格納：Excel総表、按天ウェブページ、物件ごとの図面PDF。',
      'files.excel': 'Excel総表', 'files.daily': '按天ウェブページ', 'files.pdf': '図面PDF',
      'files.open': '開く', 'files.none': '（なし）', 'files.noping': 'サービス未接続のため開けません。',

      'runs.h': '実行ログ', 'runs.sub': '各取得ラウンドの全体過程：開始／終了、走査数、格納数、成功／失敗。',
      'runs.empty': '実行記録はまだありません。', 'runs.refresh': '🔄 更新', 'runs.tail': 'リアルタイムログ（自動スクロール）',
      'runs.live': 'リアルタイムログ',

      'account.h': 'アカウント管理', 'account.sub': 'REINS の会員IDとパスワードを入力、保存で自動ログイン可能に。パスワードは本機のみ、アップロードしない。',
      'account.site': 'サイトURL', 'account.site.ph': 'https://system.reins.jp', 'account.name': 'アカウント名',
      'account.mid': '会員ID', 'account.pw': 'パスワード', 'account.pw.ph': '変更時に保存',
      'account.save': '保存', 'account.test': 'ログイン検証', 'account.saved': '保存済',
      'account.unsaved': '未保存', 'account.lastlogin': '前回ログイン', 'account.never': '未',
      'account.ok': 'アカウント保存済', 'account.testing': 'ログイン検証中…', 'account.oktip': 'ログイン検証：'
    }
  };

  /* ============ zh-CN 中文原文（供 zh-CN 与缺失语言回退） ============
     仅覆盖会被 JS 动态渲染的 key（静态串由元素原文 __o 兜底）。 */
  var ZH = {
    'ov.last2':'上次刷新',
    'ch.new':'新規','ch.price_down':'降价','ch.price_up':'涨价','ch.modified':'变更',
    'run.ok':'成功','run.running':'进行中','run.fail':'失败',
    'empty.changes':'暂无变更记录。先点上面的「手动更新」跑一轮。',
    'empty.runs':'还没有运行记录，先点「手动更新」跑一轮。',
    'search.loading':'正在加载当天数据…','search.fail':'查询失败，请重试。','search.none':'没有匹配的房源。','search.unit_price_sqm':'㎡単価','search.unit_price_tsubo':'坪単価','search.na':'—',
    'search.sum':'日期 {date} ｜ 共 {total} 条 ｜ 每页 {limit} 条 ｜ 本页 {rows} 条',
    'search.lib.base':'本机库共','search.lib.today':'今天','search.lib.latest':'最近有数据：',
    'search.lib.warn':'⚠ 本机库<b>今天（','search.lib.warn2':'）还没有任何数据</b>。多半是「当天的下载」还没跑，或者<b>登录会话已过期</b>（此时抓取会一声不吭地入库 0 条）。',
    'search.lib.running':'正在抓取：已落库 {n} 条（新盘 {newn}）。','search.lib.runningtip':'数据正在写入，稍候刷新即可看到。',
    'search.lib.err':'最近一轮下载未成功：','search.lib.go':'去「数据抓取设置」跑一轮','search.lib.view':'查看最近有数据的日期（',
    'search.alert_full':'最多同时对比 {n} 套房。\n\n请先在底部「对比栏」里点 × 去掉一套，再添加新的。',
    'search.alert_min':'至少要选 2 套房才能对比。\n\n在每一行左边的「加入对比」按钮上点一下即可选中。',
    'cmp.added_short':'已加入 ✓','detail.tobank':'加入对比','search.cmp_alone':'单独对比这套',
    'pager.first':'首页','pager.prev':'上一页','pager.next':'下一页','pager.last':'末页','pager.of':'第','pager.page':'页（共','pager.unit':'条）',
    'cmp.notenough':'对比要 2–6 套房，现在还不够','cmp.only1a':'你目前只选了','cmp.only1b':'套：','cmp.only1c':'再选','cmp.only1d':'套就能横向对比（最多 6 套）——回『房源查询』页，在别的房子左边的「加入对比」上点一下即可。',
    'cmp.none':'对比栏里还没有房子。','cmp.none2':'去『房源查询』页，点每行左边的「','cmp.none3':'」，选 2–6 套，再点底部的「开始对比」。','cmp.none4':'也可以从某套房的详情页点「加入对比」，把这一套先放进来。','cmp.gosearch':'去选房子',
    'cmp.notfound':'这些房子在本机库里已经找不到了（可能被清理过）。','cmp.back':'← 回到房源查询','cmp.clear2':'清空对比栏',
    'cmp.summary.h':'一眼结论','cmp.summary.hint':'按选中这 {n} 套自动算出来的','cmp.hint.slide':'← 手机上左右滑动表格，可看到全部 {n} 套房 →',
    'cmp.cfg':'配置状况','cmp.over':'最多同时对比','cmp.ignore':'套，多出的','cmp.skipped':'套已忽略。','cmp.missing':'{n} 套不在本机库里，已跳过。',
    'cmp.extra':'详情页的其他信息',
    'cmp.f.pdf':'PDF 图纸','cmp.f.detail':'详情页','cmp.f.src':'原网站',
    'cmp.bl.total':'最低价','cmp.bl.barea':'建筑面积最大','cmp.bl.imgs':'图片最多',
    'collect.switch_demo':'切换到离线演示','collect.switch_live':'切换到真实抓取','collect.started':'已开始',
    'step4.mode_lbl':'自动更新方式','step4.opt_interval':'① 定时（固定间隔）','step4.opt_random':'② 随机（随机间隔）',
    'step4.interval_lbl':'间隔','step4.rand_min':'随机下限(h)','step4.rand_max':'随机上限(h)',
    'step4.win_from':'运行时段 起','step4.win_to':'运行时段 止',
    'test.l_login':'测试登录','test.l_query':'测试查询','test.l_dl':'测试下载数据','test.l_real':'真实下载数据',
    'detail.notfound_local':'本地库里没有物件番号 {no}。',
    'detail.chg_th_time':'时间','detail.chg_th_type':'类型','detail.chg_th_old':'原值','detail.chg_th_new':'新值',
    'spec.previewing':'预览中…','spec.dling':'下载中…','spec.nodetail':'（无详情）',
    'spec.ownonly':'土地权利/借地权：仅所有权（推荐，与线上口径一致）',
    'cmp.f.loc':'所在地','cmp.f.ward':'所在区','cmp.f.line':'沿線・駅','cmp.f.bname':'建物名','cmp.f.sub':'物件種目','cmp.f.kind':'種別','cmp.f.no':'物件番号',
    'cmp.f.price':'価格','cmp.f.prev':'前回価格','cmp.f.drop':'値下がり幅','cmp.f.unit':'㎡単価','cmp.f.tsubo':'坪単価',
    'cmp.f.excl':'専有面積','cmp.f.land':'土地面積','cmp.f.barea':'建物面積','cmp.f.layout':'間取り','cmp.f.floor':'所在階','cmp.f.floors':'階建',
    'cmp.f.year':'築年月','cmp.f.imgs':'画像数','cmp.f.reg':'登録年月日','cmp.f.chg':'変更年月日','cmp.f.first':'初回取得','cmp.f.last':'最終取得',
    'cmp.g.loc':'地段 · 位置','cmp.g.price':'价格','cmp.g.area':'面积 · 間取り','cmp.g.bld':'建物','cmp.g.rec':'记录','cmp.g.act':'附件 · 操作',
    'cmp.s.total':'総額最低','cmp.s.unit':'㎡单价最低','cmp.s.excl':'专有面积最大','cmp.s.land':'土地面积最大','cmp.s.year':'筑年最新',
    'cmp.thumb':'房源','cmp.pdfc':'有 PDF','cmp.detail':'看详情页','cmp.srcpage':'详情原页','cmp.srcsite':'原网站 ↗','cmp.best_def':'最优','cmp.drop':'把这套移出对比',
    'cmp.pick1':'回查询页再选 1 套','cmp.loading':'正在读取对比数据…','cmp.readfail':'读取失败，请重试。','cmp.backq':'回查询页','cmp.confirm_clear':'清空对比栏？\n\n（只是取消选中，不会删除任何房源数据）',
    'spec.querying':'实时连 REINS 查询中…','spec.err':'出错','spec.done':'· 完成',
    'spec.sample.of':'实时查询到','spec.sample.tail':'（样本','spec.nomatch':'该条件下没有匹配房源。',
    'spec.downloading':'正式下载中（数据 + 図面PDF）…',
    'spec.stat.fetched':'落库','spec.stat.new':'新盘','spec.stat.changed':'变更','spec.stat.pdf':'PDF','spec.unit_cnt':'条','spec.unit_pdf':'份',
    'spec.pausenote':'⏸ 当天的下载已暂停（指定日期下载进行中），完成后自动恢复。','spec.empty.preview':'点「确定」后，这里显示实时连 REINS 查到的条数 + 样本。','spec.empty.dl':'点「下载」后，这里显示正式下载（数据 + 図面PDF）的进度与落库统计。',
    'spec.alert_kind':'请先选择「物件種別」（必填）。','spec.alert_date':'请先选择「指定日期」（必填，YYYY-MM-DD）。',
    'spec.confirm_dl':'确定要正式下载「{date}」的房源（含 図面PDF）吗？\n\n下载期间会暂停「当天的下载」，完成后自动恢复。',
    'collect.act_env':'环境自检','collect.act_login':'重新登录（打开浏览器手工登录）','collect.act_query':'测试查询','collect.act_dld':'测试下载数据','collect.act_real':'真实下载数据','collect.act_stop':'停止自动更新','collect.act_start':'已启动自动更新','collect.act_save':'设置已保存',
    'collect.sw_live':'真实抓取','collect.sw_demo':'离线演示','collect.env_title':'环境自检（不连 REINS）','collect.testtip':'测试登录：',
    'detail.info_h':'详细信息','detail.info_hint':'共 {n} 项 · 电脑端一屏基本能看完','detail.chg_h':'变更历史','detail.chg_hint':'降价 / 变更 会被记在这里（{n} 条）','detail.snap_h':'抓取快照','detail.snap_hint':'每次抓到的价格都留痕（{n} 条）',
    'detail.ai_h':'AI 结构','detail.ai_hint2':'AI 从房源 PDF 识别了 {n} 项 · 仅供参考，以 REINS 原件为准','detail.ai_none':'本房源尚未纳入 PDF AI 识别。','detail.ai_nopdf':'本房源没有落盘 PDF，无法做 AI 提取。','detail.ai_failed':'上次 AI 提取失败：{err}','detail.ai_regen':'重新生成','detail.ai_regen_running':'AI 生成中…（约 5–15 秒）','detail.ai_fail':'生成失败，请稍后再试','detail.ai_updated':'识别于 {time}','detail.ai_skipped':'PDF 未变化，无需重跑','detail.ai_incomplete':'本地只抽出 {n} 项，是否调用云端模型补全？（会产生费用）','detail.ai_local_only':'已按本地结果保存（未调用云端）','detail.ai_local_done':'本地抽取完成：{n} 项（0 元，未调用云端）','detail.ai_cloud_done':'云端补全完成（已产生费用）',
    'detail.ai_overall':'综合评分','detail.ai_concl':'AI 结论','detail.ai_anom':'本条有 {n} 处 AI 识别值存疑','detail.ai_warn':'存疑（红字）','detail.ai_danger':'特别存疑（红字黄底）','detail.ai_danger_n':'其中 {n} 处特别存疑','detail.ai_edited':'含人工修订',
    'detail.snap_th_time':'抓取时间','detail.snap_th_price':'价格(円)','detail.snap_th_hash':'指纹',
    'detail.chg_none':'暂无变更记录（首次抓到时不会显示，改价后才会有）。','detail.snap_none':'暂无快照。',
    'detail.added':'已加入对比 ✓（{n}/6）','detail.tobank2':'加入对比（已选 {n}/6）','detail.goto_n':'去对比这 {n} 套 →','detail.goto_lack':'去对比（还差 {n} 套）',
    'detail.hint_on':'这一套已在对比篮里（共 {n}/6）。再点按钮可取消，凑够 2 套就能横向比。','detail.hint_off':'对比＝把最多 6 套房并排放一起看「价格 / 面积 / 地段 / 建物」。点左边按钮把这套放进对比篮，再去别的房子点「加入对比」。',
    'detail.alert_full':'最多同时对比 6 套房。\n\n去了「房源对比」页，点某一列右上角的 × 去掉一套，再回来添加。','detail.alert_min':'至少要 2 套房才能对比。\n\n① 点左边「加入对比」把这一套放进去；\n② 回「房源查询」页，在另一套房子左边点「加入对比」。','detail.goto':'去对比',
    'files.cl_h':'下载文件清单','files.cl_intro':'每轮下载（含自动抓取）固定落这 4 类文件：','files.cl_db':'数据（主档 / 价格快照 / 变更流水）→ data/jproperty.db','files.cl_xls':'Excel（全量一份）→ data/exports/大阪房源_日期.xlsx','files.cl_day':'按天页（可离线看）→ data/daily/日期.html','files.cl_pdf':'図面 PDF（一套一份，仅「新盘 / 首次见到」才下载）→ data/attachments/物件番号.pdf','files.cl_note':'注意：Excel 与按天页是「全局一份」，不按检索条件分包（想按条件看，用 Excel 的「物件種目 / 大类」筛选）；PDF 是按房源一份，文件名=物件番号。',
    'files.root_h':'本地落盘目录','files.root_note':'根目录：{root} ｜ 想换到外挂存储 / 自己的网站空间，改 config.yaml → app.output_root 即可。','files.daily2':'按天页面（可离线看）','files.excel2':'Excel 导出','files.pdf2':'PDF 图纸','files.th_name':'文件名','files.th_size':'大小','files.th_time':'时间','files.empty':'暂无文件。',
    'runs.live_h':'实时日志','runs.live_hint':'当前正在进行的抓取过程','runs.rec_h':'运行记录','runs.rec_hint':'每轮抓取的成败与条数','runs.th_end':'结束','runs.th_scan':'扫描','runs.th_new':'新盘','runs.th_chg':'变更','runs.th_note':'备注',
    'account.hint':'录入你的 REINS 账号，用于自动登录','account.name_hint':'（给这个账号起个名，如：主账号 / 备用）','account.eye_show':'👁 显示','account.eye_hide':'🙈 隐藏','account.showpw':'显示已保存密码','account.clear':'清空','account.pw_ph2':'录入后仅本机加密保存','account.site_hint':'（这个账号要登录的网址，可以随时改）',
    'account.alert_empty':'会员ID 与密码都不能为空','account.alert_show':'已显示已保存密码（仅本机可见，用完可点清空）','account.confirm_clear':'确定清空已保存的账号？',
    'account.sec_note':'⚠️ 账号密码只存于你本机（加密文件 data/credentials.json + 密钥 data/.cred_key），不联网、不上传、不共享。\n程序仅在你本账号自动登录时使用；若出现验证码或登录失败，请改用「手工登录」。\n「账号名称」只是本机标注，方便以后多账号时区分，不影响登录；\n「网站地址」是真正会被用到的登录网址，改它就能换登录入口。',
    'account.sel_h':'登录页选择器','account.sel_hint':'高级 · 自动登录失败时才需要看','account.sel_desc':'下面是当前自动登录用的页面元素选择器。REINS 登录页是 Vue 单页应用（输入框没有 name，id 每次加载都变），所以这里用稳定的 class 定位。',
    'account.sel_use':'用途','account.sel_sel':'选择器','account.sel_id':'会员ID 输入框','account.sel_pw':'密码 输入框','account.sel_agree':'遵守条款勾选框','account.sel_submit':'登录按钮',
    'account.sel_note':'登录必须勾选「所属機構の規程及びガイドラインを遵守します」，否则登录按钮保持禁用。改动位置：config.yaml → selectors.login。',
    /* v1.9.30（勇哥 2026-09-20）：/files 页 PDF 关键词搜索 + /account 页「设置账号密码」入口。
       ⚠ 这些 key 会被 JS 的 t()/tf() 取用（rowHtml / paintHead），**必须**进 ZH ——
         漏了就是裸 key 上屏，跑 tools/verify_i18n_keys.py 会红。 */
    'files.open':'打开',
    'files.pdf_hint':'共 {total} 个 ｜ 默认只显示最近 {limit} 个 ｜ 更早的用上面的关键词框找',
    'files.q':'关键词搜索（不限条件）',
    'files.q_ph':'例：中之島 / グランドメゾン / 3001407 / 3ＬＤＫ / 西区',
    'files.q_btn':'搜索',
    'files.q_clear':'清除',
    'files.q_found':'命中 {n} 个 ｜ 当前显示前 {shown} 个',
    'files.q_none':'没有匹配的 PDF。换个词试试：楼名 / 地址 / 物件番号 / 駅・沿線 / 間取り / 区。',
    'files.q_tip':'关键词与「房源查询」同一口径：地址 / 楼名 / 物件番号 / 駅・沿線 / 間取り / 区 / 種目 / 築年月；空格分隔多词＝都要命中。',
    'files.th_house':'房源（基本信息）',
    'files.house_unknown':'库中无此房源资料',
    'account.setpw':'设置账号密码',
    'account.setpw_note':'去「员工管理」页：新建员工、重置随机码、复制现有密码、设为管理员 / 禁用（该页仅本机 8765 可见）。'
  };

  /* ============ 新增 key 的繁/英/日翻译（仅本次新增的 key） ============ */
  var EXT = {
    'zh-TW': {
      'ov.last2':'上次重整',
      'search.sum':'日期 {date} ｜ 共 {total} 筆 ｜ 每頁 {limit} 筆 ｜ 本頁 {rows} 筆',
      'search.today_short':'今天',
      'sort.h':'排序','sort.updated':'更新時間','sort.price':'價格','sort.area':'面積','sort.unit_sqm':'單價(元/㎡)','sort.unit_tsubo':'單價(元/坪)','sort.drop_amt':'降價額','sort.drop_pct':'降價率','sort.drop_date':'降價日期','sort.built':'建築年份','sort.bukken':'物件番號',
      'sort.clear':'清除排序','sort.dir_desc':'倒序','sort.dir_asc':'正序','sort.now':'目前：{k} {d}',
      'view.toggle':'詳細 ⇄ 簡潔','filter.edit':'修改條件 ▾',
      'search.alert_full':'最多同時對比 {n} 套房。\n\n請先在底部「對比欄」裡點 × 去掉一套，再添加新的。',
      'search.alert_min':'至少要選 2 套房才能對比。\n\n在每一行左邊的「加入對比」按鈕上點一下即可選中。',
      'cmp.added_short':'已加入 ✓','search.cmp_alone':'單獨對比這套',
      'pager.unit':'筆）',
      'cmp.f.loc':'所在地','cmp.f.ward':'所在區','cmp.f.line':'沿線・駅','cmp.f.bname':'建物名','cmp.f.sub':'物件種目','cmp.f.kind':'種別','cmp.f.no':'物件番號',
      'cmp.f.price':'価格','cmp.f.prev':'前回価格','cmp.f.drop':'降幅','cmp.f.unit':'㎡單價','cmp.f.tsubo':'坪單價',
      'cmp.f.excl':'專有面積','cmp.f.land':'土地面積','cmp.f.barea':'建物面積','cmp.f.layout':'間取り','cmp.f.floor':'所在階','cmp.f.floors':'階建',
      'cmp.f.year':'築年月','cmp.f.imgs':'圖片數','cmp.f.reg':'登録年月日','cmp.f.chg':'変更年月日','cmp.f.first':'首次取得','cmp.f.last':'最終取得',
      'cmp.g.loc':'地段 · 位置','cmp.g.price':'價格','cmp.g.area':'面積 · 間取り','cmp.g.bld':'建物','cmp.g.rec':'記錄','cmp.g.act':'附件 · 操作',
      'cmp.s.total':'總額最低','cmp.s.unit':'㎡單價最低','cmp.s.excl':'專有面積最大','cmp.s.land':'土地面積最大','cmp.s.year':'築年最新',
      'cmp.thumb':'房源','cmp.pdfc':'有 PDF','cmp.srcpage':'詳情原頁','cmp.srcsite':'原網站 ↗','cmp.best_def':'最優','cmp.drop':'把這套移出對比',
      'cmp.pick1':'回查詢頁再選 1 套','cmp.loading':'正在讀取對比資料…','cmp.readfail':'讀取失敗，請重試。','cmp.backq':'回查詢頁','cmp.confirm_clear':'清空對比欄？\n\n（只是取消選中，不會刪除任何房源資料）',
      'spec.alert_kind':'請先選擇「物件種別」（必填）。','spec.alert_date':'請先選擇「指定日期」（必填，YYYY-MM-DD）。',
      'spec.confirm_dl':'確定要正式下載「{date}」的房源（含 図面PDF）嗎？\n\n下載期間會暫停「當天的下載」，完成後自動恢復。',
      'collect.switch_demo':'切換到離線演示','collect.switch_live':'切換到真實抓取',
      'collect.act_env':'環境自檢','collect.act_login':'重新登錄（開啟瀏覽器手工登錄）','collect.act_query':'測試查詢','collect.act_dld':'測試下載資料','collect.act_real':'真實下載資料','collect.act_stop':'停止自動更新','collect.act_start':'已啟動自動更新','collect.act_save':'設定已保存',
      'collect.sw_live':'真實抓取','collect.sw_demo':'離線演示','collect.env_title':'環境自檢（不連 REINS）','collect.testtip':'測試登錄：',
      'detail.info_h':'詳細資訊','detail.info_hint':'共 {n} 項 · 電腦端一屏基本能看完','detail.chg_h':'變更歷史','detail.chg_hint':'降價 / 變更 會被記在這裡（{n} 條）','detail.snap_h':'抓取快照','detail.snap_hint':'每次抓到的價格都留痕（{n} 條）',
      'detail.ai_h':'AI 結構','detail.ai_hint2':'AI 從房源 PDF 識別了 {n} 項 · 僅供參考，以 REINS 原件為準','detail.ai_none':'本房源尚未納入 PDF AI 識別。','detail.ai_nopdf':'本房源沒有落盤 PDF，無法做 AI 提取。','detail.ai_failed':'上次 AI 提取失敗：{err}','detail.ai_regen':'重新生成','detail.ai_regen_running':'AI 生成中…（約 5–15 秒）','detail.ai_fail':'生成失敗，請稍後再試','detail.ai_updated':'識別於 {time}','detail.ai_skipped':'PDF 未變更，無需重跑','detail.ai_incomplete':'本地只抽出 {n} 項，是否呼叫雲端模型補全？（會產生費用）','detail.ai_local_only':'已按本地結果保存（未呼叫雲端）','detail.ai_local_done':'本地抽取完成：{n} 項（0 元，未呼叫雲端）','detail.ai_cloud_done':'雲端補全完成（已產生費用）',
      'detail.ai_overall':'綜合評分','detail.ai_concl':'AI 結論','detail.ai_anom':'本條有 {n} 處 AI 識別值存疑','detail.ai_warn':'存疑（紅字）','detail.ai_danger':'特別存疑（紅字黃底）','detail.ai_danger_n':'其中 {n} 處特別存疑','detail.ai_edited':'含人工修訂',
      'detail.snap_th_time':'抓取時間','detail.snap_th_price':'價格(円)','detail.snap_th_hash':'指紋',
      'detail.chg_none':'暫無變更記錄（首次抓到時不會顯示，改價後才會有）。','detail.snap_none':'暫無快照。',
      'detail.added':'已加入對比 ✓（{n}/6）','detail.tobank2':'加入對比（已選 {n}/6）','detail.goto_n':'去對比這 {n} 套 →','detail.goto_lack':'去對比（還差 {n} 套）',
      'detail.hint_on':'這一套已在對比籃裡（共 {n}/6）。再點按鈕可取消，湊夠 2 套就能橫向比。','detail.hint_off':'對比＝把最多 6 套房並排放一起看「價格 / 面積 / 地段 / 建物」。點左邊按鈕把這套放進對比籃，再去別的房子點「加入對比」。',
      'detail.alert_full':'最多同時對比 6 套房。\n\n去了「房源對比」頁，點某一列右上角的 × 去掉一套，再回來添加。','detail.alert_min':'至少要 2 套房才能對比。\n\n① 點左邊「加入對比」把這一套放進去；\n② 回「房源查詢」頁，在另一套房子左邊點「加入對比」。','detail.goto':'去對比',
      'files.cl_h':'下載檔案清單','files.cl_intro':'每輪下載（含自動抓取）固定落這 4 類檔案：','files.cl_db':'資料（主檔 / 價格快照 / 變更流水）→ data/jproperty.db','files.cl_xls':'Excel（全量一份）→ data/exports/大阪房源_日期.xlsx','files.cl_day':'按天頁（可離線看）→ data/daily/日期.html','files.cl_pdf':'圖面 PDF（一套一份，僅「新盤 / 首次見到」才下載）→ data/attachments/物件番号.pdf','files.cl_note':'注意：Excel 與按天頁是「全域一份」，不按檢索條件分包（想按條件看，用 Excel 的「物件種目 / 大類」篩選）；PDF 是按房源一份，檔名=物件番号。',
      'files.root_h':'本機落盤目錄','files.root_note':'根目錄：{root} ｜ 想換到外接儲存 / 自己的網站空間，改 config.yaml → app.output_root 即可。','files.daily2':'按天頁面（可離線看）','files.excel2':'Excel 匯出','files.pdf2':'PDF 圖紙','files.th_name':'檔名','files.th_size':'大小','files.th_time':'時間','files.empty':'暫無檔案。',
      'runs.live_h':'即時日誌','runs.live_hint':'當前正在進行的抓取過程','runs.rec_h':'運行記錄','runs.rec_hint':'每輪抓取的成敗與條數','runs.th_end':'結束','runs.th_scan':'掃描','runs.th_new':'新盤','runs.th_chg':'變更','runs.th_note':'備註',
      'account.hint':'錄入你的 REINS 帳號，用於自動登錄','account.name_hint':'（給這個帳號起個名，如：主帳號 / 備用）','account.eye_show':'👁 顯示','account.eye_hide':'🙈 隱藏','account.showpw':'顯示已保存密碼','account.clear':'清空','account.pw_ph2':'錄入後僅本機加密保存','account.site_hint':'（這個帳號要登錄的網址，可以隨時改）',
      'account.alert_empty':'會員ID 與密碼都不能為空','account.alert_show':'已顯示已保存密碼（僅本機可見，用完可點清空）','account.confirm_clear':'確定清空已保存的帳號？',
      'account.sec_note':'⚠️ 帳號密碼只存於你本機（加密檔 data/credentials.json + 金鑰 data/.cred_key），不連網、不上傳、不共享。\n程式僅在你本帳號自動登錄時使用；若出現驗證碼或登錄失敗，請改用「手工登錄」。\n「帳號名稱」只是本機標註，方便以後多帳號時區分，不影響登錄；\n「網站地址」是真正會被用到的登錄網址，改它就能換登錄入口。',
      'account.sel_h':'登錄頁選擇器','account.sel_hint':'進階 · 自動登錄失敗時才需要看','account.sel_desc':'下面是目前自動登錄用的頁面元素選擇器。REINS 登錄頁是 Vue 單頁應用（輸入框沒有 name，id 每次載入都變），所以這裡用穩定的 class 定位。',
      'account.sel_use':'用途','account.sel_sel':'選擇器','account.sel_id':'會員ID 輸入框','account.sel_pw':'密碼 輸入框','account.sel_agree':'遵守條款勾選框','account.sel_submit':'登錄按鈕',
      'cmp.f.pdf':'PDF 圖紙','cmp.f.detail':'詳情頁','cmp.f.src':'原網站','spec.nodetail':'（無詳情）',
      'cmp.bl.total':'最低價','cmp.bl.barea':'建物面積最大','cmp.bl.imgs':'圖片最多',
      'collect.switch_demo':'切換到離線演示','collect.switch_live':'切換到真實抓取','collect.started':'已開始',
      'step4.mode_lbl':'自動更新方式','step4.opt_interval':'① 定時（固定間隔）','step4.opt_random':'② 隨機（隨機間隔）',
      'step4.interval_lbl':'間隔','step4.rand_min':'隨機下限(h)','step4.rand_max':'隨機上限(h)',
      'step4.win_from':'執行時段 起','step4.win_to':'執行時段 止',
      'test.l_login':'測試登錄','test.l_query':'測試查詢','test.l_dl':'測試下載資料','test.l_real':'真實下載資料',
      'detail.notfound_local':'本機庫裡沒有物件番號 {no}。',
      'detail.chg_th_time':'時間','detail.chg_th_type':'類型','detail.chg_th_old':'原值','detail.chg_th_new':'新值',
      'spec.previewing':'預覽中…','spec.dling':'下載中…',
      'account.sel_note':'登錄必須勾選「所屬機構的規程及びガイドラインを遵守します」，否則登錄按鈕保持禁用。改動位置：config.yaml → selectors.login。',
      'spec.unit_cnt':'筆','spec.unit_pdf':'份',
      'files.pdf_hint':'共 {total} 個 ｜ 預設只顯示最近 {limit} 個 ｜ 更早的用上面的關鍵詞框找',
      'files.q':'關鍵詞搜尋（不限條件）','files.q_ph':'例：中之島 / グランドメゾン / 3001407 / 3ＬＤＫ / 西區',
      'files.q_btn':'搜尋','files.q_clear':'清除',
      'files.q_found':'命中 {n} 個 ｜ 目前顯示前 {shown} 個',
      'files.q_none':'沒有符合的 PDF。換個詞試試：樓名 / 地址 / 物件番號 / 駅・沿線 / 間取り / 區。',
      'files.q_tip':'關鍵詞與「房源查詢」同一口徑：地址 / 樓名 / 物件番號 / 駅・沿線 / 間取り / 區 / 種目 / 築年月；空格分隔多詞＝都要命中。',
      'files.th_house':'房源（基本資訊）','files.house_unknown':'本機庫無此房源資料',
      'account.setpw':'設定帳號密碼',
      'account.setpw_note':'前往「員工管理」頁：新建員工、重設隨機碼、複製現有密碼、設為管理員 / 停用（該頁僅本機 8765 可見）。'
    },
    'en': {
      'ov.last2':'Last refresh',
      'search.sum':'Date {date} ｜ {total} total ｜ {limit}/page ｜ {rows} this page',
      'search.alert_full':'Max {n} homes side by side.\n\nRemove one from the bottom "Compare bar" first, then add a new one.',
      'search.alert_min':'Pick at least 2 homes to compare.\n\nClick "Add to compare" on the left of each row.',
      'cmp.added_short':'Added ✓','search.cmp_alone':'Compare this one',
      'pager.unit':'rows)',
      'cmp.f.loc':'Address','cmp.f.ward':'Ward','cmp.f.line':'Line・Station','cmp.f.bname':'Building','cmp.f.sub':'Type','cmp.f.kind':'Category','cmp.f.no':'Property No.',
      'cmp.f.price':'Price','cmp.f.prev':'Prev price','cmp.f.drop':'Drop','cmp.f.unit':'¥/㎡','cmp.f.tsubo':'¥/tsubo',
      'cmp.f.excl':'Excl. area','cmp.f.land':'Land area','cmp.f.barea':'Bldg area','cmp.f.layout':'Layout','cmp.f.floor':'Floor','cmp.f.floors':'Floors',
      'cmp.f.year':'Built','cmp.f.imgs':'Images','cmp.f.reg':'Registered','cmp.f.chg':'Changed','cmp.f.first':'First seen','cmp.f.last':'Last seen',
      'cmp.g.loc':'Location','cmp.g.price':'Price','cmp.g.area':'Area · Layout','cmp.g.bld':'Building','cmp.g.rec':'Records','cmp.g.act':'Links · Actions',
      'cmp.s.total':'Lowest total','cmp.s.unit':'Lowest ¥/㎡','cmp.s.excl':'Largest excl. area','cmp.s.land':'Largest land','cmp.s.year':'Newest build',
      'cmp.thumb':'Home','cmp.pdfc':'Has PDF','cmp.srcpage':'Detail page','cmp.srcsite':'Source ↗','cmp.best_def':'Best','cmp.drop':'Remove from compare',
      'cmp.pick1':'Back to search to pick 1 more','cmp.loading':'Reading compare data…','cmp.readfail':'Failed to read. Retry.','cmp.backq':'Back to search','cmp.confirm_clear':'Clear compare bar?\n\n(Only deselects; no listing data is deleted)',
      'spec.alert_kind':'Please choose a property type (required).','spec.alert_date':'Please choose the specific date (required, YYYY-MM-DD).',
      'spec.confirm_dl':'Download "{date}" listings (incl. floor PDF) for real?\n\n"Today\'s download" pauses during this and auto-resumes after.',
      'collect.switch_demo':'Switch to demo','collect.switch_live':'Switch to live',
      'collect.act_env':'Env check','collect.act_login':'Re-login (manual, opens browser)','collect.act_query':'Test query','collect.act_dld':'Test download','collect.act_real':'Real download','collect.act_stop':'Stop auto-update','collect.act_start':'Auto-update started','collect.act_save':'Settings saved',
      'collect.sw_live':'Live','collect.sw_demo':'Demo','collect.env_title':'Env check (no REINS)','collect.testtip':'Test login: ',
      'detail.info_h':'Details','detail.info_hint':'{n} items · fits one screen on desktop','detail.chg_h':'Change history','detail.chg_hint':'Drops / changes logged here ({n})','detail.snap_h':'Capture snapshots','detail.snap_hint':'Every captured price is kept ({n})',
      'detail.ai_h':'AI Structure','detail.ai_hint2':'{n} fields recognised by AI from the listing PDF · for reference, the REINS original prevails','detail.ai_none':'Not yet covered by PDF AI recognition.','detail.ai_nopdf':'No local PDF for this listing, AI extraction unavailable.','detail.ai_failed':'Last AI extraction failed: {err}','detail.ai_regen':'Regenerate','detail.ai_regen_running':'Generating… (5–15 s)','detail.ai_fail':'Generation failed, please retry later','detail.ai_updated':'Recognised on {time}','detail.ai_skipped':'PDF unchanged, nothing to redo','detail.ai_incomplete':'Local extraction got only {n} fields. Call the cloud model to fill the rest? (costs money)','detail.ai_local_only':'Saved local result (no cloud call)','detail.ai_local_done':'Local extraction done: {n} fields (free, no cloud call)','detail.ai_cloud_done':'Cloud completion done (charged)',
      'detail.ai_overall':'Overall','detail.ai_concl':'AI Verdict','detail.ai_anom':'{n} AI-recognised values are questionable','detail.ai_warn':'Questionable (red)','detail.ai_danger':'Highly questionable (red on yellow)','detail.ai_danger_n':'including {n} highly questionable','detail.ai_edited':'manually edited',
      'detail.snap_th_time':'Captured','detail.snap_th_price':'Price (¥)','detail.snap_th_hash':'Hash',
      'detail.chg_none':'No change records yet (appears after a price change).','detail.snap_none':'No snapshots.',
      'detail.added':'Added ✓ ({n}/6)','detail.tobank2':'Add to compare ({n}/6 selected)','detail.goto_n':'Compare these {n} →','detail.goto_lack':'Compare (need {n} more)',
      'detail.hint_on':'This home is in the compare bar ({n}/6). Click again to remove; 2+ lets you compare.','detail.hint_off':'Compare = put up to 6 homes side by side to see price / area / location / building. Click the left button to add this one, then "Add to compare" on others.',
      'detail.alert_full':'Max 6 homes at once.\n\nGo to "Compare", click × on a column to remove one, then come back.','detail.alert_min':'Need at least 2 homes.\n\n① Click "Add to compare" on the left of this one;\n② Back on "Search", click "Add to compare" on another.','detail.goto':'Compare',
      'files.cl_h':'Download checklist','files.cl_intro':'Every round (auto capture included) writes these 4 files:','files.cl_db':'Database (records / price snapshots / change log) → data/jproperty.db','files.cl_xls':'Excel (one full sheet) → data/exports/大阪房源_日期.xlsx','files.cl_day':'Daily page (readable offline) → data/daily/日期.html','files.cl_pdf':'Floor PDF (one per home; downloaded only for new listings) → data/attachments/物件番号.pdf','files.cl_note':'Note: the Excel and the daily page are single global files, not split by search condition (filter by the "物件種目 / 大类" columns inside Excel instead); PDFs are per-home, named by property no.',
      'files.root_h':'Local files','files.root_note':'Root: {root} ｜ To use external storage / your own web space, set config.yaml → app.output_root.','files.daily2':'Daily pages (offline)','files.excel2':'Excel export','files.pdf2':'Floor PDF','files.th_name':'File','files.th_size':'Size','files.th_time':'Time','files.empty':'No files.',
      'runs.live_h':'Live log','runs.live_hint':'Current capture in progress','runs.rec_h':'Run history','runs.rec_hint':'Outcome & counts per round','runs.th_end':'End','runs.th_scan':'Scanned','runs.th_new':'New','runs.th_chg':'Changed','runs.th_note':'Note',
      'account.hint':'Enter your REINS account for auto-login','account.name_hint':'(Name this account, e.g. Primary / Backup)','account.eye_show':'👁 Show','account.eye_hide':'🙈 Hide','account.showpw':'Show saved password','account.clear':'Clear','account.pw_ph2':'Saved locally, encrypted','account.site_hint':'(The login URL for this account; editable anytime)',
      'account.alert_empty':'Member ID and password are required','account.alert_show':'Saved password shown (local only; clear when done)','account.confirm_clear':'Clear the saved account?',
      'account.sec_note':'⚠️ Your account password is stored only on this machine (encrypted file data/credentials.json + key data/.cred_key); no network, no upload, no sharing.\nThe app uses it only for auto-login to your account; if a CAPTCHA or login failure appears, use "Manual login".\n"Account name" is just a local label to tell accounts apart; it does not affect login.\n"Site URL" is the real login URL used; change it to switch the entry.',
      'account.sel_h':'Login selectors','account.sel_hint':'Advanced · only if auto-login fails','account.sel_desc':'Below are the page element selectors used for auto-login. The REINS login page is a Vue SPA (inputs have no name, and id changes every load), so we locate by stable class.',
      'account.sel_use':'Use','account.sel_sel':'Selector','account.sel_id':'Member ID field','account.sel_pw':'Password field','account.sel_agree':'Agree checkbox','account.sel_submit':'Login button',
      'cmp.f.pdf':'Floor PDF','cmp.f.detail':'Detail page','cmp.f.src':'Source site','spec.nodetail':'(no details)',
      'cmp.bl.total':'Lowest price','cmp.bl.barea':'Largest bldg area','cmp.bl.imgs':'Most images',
      'collect.switch_demo':'Switch to demo','collect.switch_live':'Switch to live','collect.started':'Started',
      'step4.mode_lbl':'Auto-update mode','step4.opt_interval':'① Interval (fixed)','step4.opt_random':'② Random (random interval)',
      'step4.interval_lbl':'Interval','step4.rand_min':'Random min (h)','step4.rand_max':'Random max (h)',
      'step4.win_from':'Window start','step4.win_to':'Window end',
      'test.l_login':'Test login','test.l_query':'Test query','test.l_dl':'Test download','test.l_real':'Real download',
      'detail.notfound_local':'No property {no} in local DB.',
      'detail.chg_th_time':'Time','detail.chg_th_type':'Type','detail.chg_th_old':'Old','detail.chg_th_new':'New',
      'spec.previewing':'Previewing…','spec.dling':'Downloading…',
      'account.sel_note':'Login requires checking "I comply with the institution\'s regulations and guidelines", otherwise the login button stays disabled. Change at: config.yaml → selectors.login.',
      'spec.unit_cnt':'rows','spec.unit_pdf':'files',
      'files.pdf_hint':'{total} files ｜ showing only the latest {limit} ｜ find older ones with the keyword box above',
      'files.q':'Keyword search (no conditions)','files.q_ph':'e.g. 中之島 / グランドメゾン / 3001407 / 3ＬＤＫ / 西区',
      'files.q_btn':'Search','files.q_clear':'Clear',
      'files.q_found':'{n} matched ｜ showing top {shown}',
      'files.q_none':'No matching PDF. Try another word: building name / address / property no. / station / layout / ward.',
      'files.q_tip':'Same rule as the Search page: address / building / property no. / line & station / layout / ward / type / built date. Space-separated words must all match.',
      'files.th_house':'Home (basics)','files.house_unknown':'No record in local DB',
      'account.setpw':'Set account password',
      'account.setpw_note':'Go to Staff Management: create staff, reset the one-time code, copy the current code, set admin / disable (that page is local-only, port 8765).'
    },
    'ja': {
      'ov.last2':'最終更新',
      'search.sum':'日付 {date} ｜ 全 {total} 件 ｜ 每頁 {limit} 件 ｜ 本頁 {rows} 件',
      'search.alert_full':'最大同時 {n} 套。\n\n下部の「比較バー」で × を押して1套削除してから追加してください。',
      'search.alert_min':'比較には最低 2 套必要。\n\n各行左の「比較に追加」をクリックして選択。',
      'cmp.added_short':'追加済 ✓','search.cmp_alone':'この1件を比較',
      'pager.unit':'件）',
      'cmp.f.loc':'所在地','cmp.f.ward':'所在区','cmp.f.line':'沿線・駅','cmp.f.bname':'建物名','cmp.f.sub':'物件種目','cmp.f.kind':'種別','cmp.f.no':'物件番号',
      'cmp.f.price':'価格','cmp.f.prev':'前回価格','cmp.f.drop':'値下げ幅','cmp.f.unit':'㎡単価','cmp.f.tsubo':'坪単価',
      'cmp.f.excl':'専有面積','cmp.f.land':'土地面積','cmp.f.barea':'建物面積','cmp.f.layout':'間取り','cmp.f.floor':'所在階','cmp.f.floors':'階建',
      'cmp.f.year':'築年月','cmp.f.imgs':'画像数','cmp.f.reg':'登録年月日','cmp.f.chg':'変更年月日','cmp.f.first':'初回取得','cmp.f.last':'最終取得',
      'cmp.g.loc':'地段 · 位置','cmp.g.price':'価格','cmp.g.area':'面積 · 間取り','cmp.g.bld':'建物','cmp.g.rec':'記録','cmp.g.act':'附件 · 操作',
      'cmp.s.total':'総額最低','cmp.s.unit':'㎡単価最低','cmp.s.excl':'専有面積最大','cmp.s.land':'土地面積最大','cmp.s.year':'築年最新',
      'cmp.thumb':'物件','cmp.pdfc':'PDFあり','cmp.srcpage':'詳細ページ','cmp.srcsite':'元サイト ↗','cmp.best_def':'最優','cmp.drop':'比較から除外',
      'cmp.pick1':'検索へ戻りあと1套選ぶ','cmp.loading':'比較データ読込中…','cmp.readfail':'読込失敗、再試行してください。','cmp.backq':'検索へ戻る','cmp.confirm_clear':'比較バーをクリア？\n\n（選択解除のみ。物件データは削除されません）',
      'spec.alert_kind':'「物件種別」を選択してください（必須）。','spec.alert_date':'「指定日」を選択してください（必須、YYYY-MM-DD）。',
      'spec.confirm_dl':'「{date}」の物件（図面PDF含む）を本番ダウンロードしますか？\n\n期間中「本日のダウンロード」は一時停止、完了後自動再開。',
      'collect.switch_demo':'デモに切替','collect.switch_live':'本番に切替',
      'collect.act_env':'環境自己診断','collect.act_login':'再ログイン（ブラウザ手動）','collect.act_query':'検索検証','collect.act_dld':'ダウンロード検証','collect.act_real':'本番ダウンロード','collect.act_stop':'自動更新停止','collect.act_start':'自動更新開始','collect.act_save':'設定保存済',
      'collect.sw_live':'本番取得','collect.sw_demo':'オフライン演示','collect.env_title':'環境自己診断（REINS未接続）','collect.testtip':'ログイン検証：',
      'detail.info_h':'詳細情報','detail.info_hint':'全 {n} 項 · PCなら一画面で概ね表示','detail.chg_h':'変更履歴','detail.chg_hint':'値下げ／変更を記録（{n} 件）','detail.snap_h':'取得スナップショット','detail.snap_hint':'取得ごとの価格を記録（{n} 件）',
      'detail.ai_h':'AI 構造','detail.ai_hint2':'AI が物件 PDF から {n} 項目を認識 · 参考情報（REINS 原本優先）','detail.ai_none':'この物件は PDF AI 認識の対象外です。','detail.ai_nopdf':'この物件は PDF 未保存のため、AI 抽出できません。','detail.ai_failed':'前回の AI 抽出に失敗：{err}','detail.ai_regen':'再生成','detail.ai_regen_running':'AI 生成中…（約 5–15 秒）','detail.ai_fail':'生成に失敗しました。後でお試しください','detail.ai_updated':'認識日 {time}','detail.ai_skipped':'PDF は未変更のため再実行不要','detail.ai_incomplete':'ローカル抽出は {n} 項目のみ。クラウドモデルで補完しますか？（有料）','detail.ai_local_only':'ローカル結果を保存（クラウド未使用）','detail.ai_local_done':'ローカル抽出完了：{n} 項目（無料・クラウド未使用）','detail.ai_cloud_done':'クラウド補完完了（課金あり）',
      'detail.ai_overall':'総合評価','detail.ai_concl':'AI 結論','detail.ai_anom':'AI 認識値のうち {n} 件に疑いあり','detail.ai_warn':'要確認（赤字）','detail.ai_danger':'要注意（赤字・黄背景）','detail.ai_danger_n':'うち {n} 件は要注意','detail.ai_edited':'手動修正あり',
      'detail.snap_th_time':'取得日時','detail.snap_th_price':'価格(円)','detail.snap_th_hash':'指紋',
      'detail.chg_none':'変更記録はまだありません（価格変更後に表示）。','detail.snap_none':'スナップショットはありません。',
      'detail.added':'追加済 ✓（{n}/6）','detail.tobank2':'比較に追加（{n}/6 選択）','detail.goto_n':'この {n} 套を比較 →','detail.goto_lack':'比較へ（あと {n} 套）',
      'detail.hint_on':'この物件は比較バーに入っています（{n}/6）。再クリックで解除、2套で比較可能。','detail.hint_off':'比較＝最大6套を並べて価格／面積／地段／建物を对照。左ボタンで追加、他物件でも「比較に追加」を。',
      'detail.alert_full':'最大同時6套。\n\n「物件比較」で列右上の × を押して1套削除、戻って追加。','detail.alert_min':'比較には最低2套。\n① 左の「比較に追加」でこの物件を；\n②「検索」で別の物件の左「比較に追加」を。','detail.goto':'比較へ',
      'files.cl_h':'ダウンロード一覧','files.cl_intro':'1ラウンド（自動取得含む）で必ず次の4種類が出力されます：','files.cl_db':'データ（本体 / 価格スナップショット / 変更履歴）→ data/jproperty.db','files.cl_xls':'Excel（全量1ファイル）→ data/exports/大阪房源_日期.xlsx','files.cl_day':'按天ページ（オフライン可）→ data/daily/日期.html','files.cl_pdf':'図面PDF（1物件1ファイル、「新規 / 初回」のみ取得）→ data/attachments/物件番号.pdf','files.cl_note':'注意：Excel と按天ページは検索条件で分割されない全体1ファイルです（条件別に見るには Excel の「物件種目 / 大類」列で絞り込み）。PDF は物件単位で、ファイル名＝物件番号です。',
      'files.root_h':'本機保存ディレクトリ','files.root_note':'ルート：{root} ｜ 外付け保存／自サイト利用は config.yaml → app.output_root を変更。','files.daily2':'按天ページ（オフライン可）','files.excel2':'Excel出力','files.pdf2':'図面PDF','files.th_name':'ファイル名','files.th_size':'サイズ','files.th_time':'時間','files.empty':'ファイルはありません。',
      'runs.live_h':'リアルタイムログ','runs.live_hint':'現在実行中の取得過程','runs.rec_h':'実行記録','runs.rec_hint':'各ラウンドの成否と件数','runs.th_end':'終了','runs.th_scan':'走査','runs.th_new':'新規','runs.th_chg':'変更','runs.th_note':'備考',
      'account.hint':'REINS アカウントを入力（自動ログイン用）','account.name_hint':'（このアカウントの名前、例：メイン／予備）','account.eye_show':'👁 表示','account.eye_hide':'🙈 隠す','account.showpw':'保存済パスワード表示','account.clear':'クリア','account.pw_ph2':'入力後は本機で暗号化保存','account.site_hint':'（このアカウントのログインURL、いつでも変更可）',
      'account.alert_empty':'会員IDとパスワードは必須','account.alert_show':'保存済パスワードを表示（本機のみ、終わったらクリア可）','account.confirm_clear':'保存済アカウントをクリアしますか？',
      'account.sec_note':'⚠️ アカウント暗号は本機のみ保存（暗号化ファイル data/credentials.json ＋ 鍵 data/.cred_key）、通信・アップロード・共有なし。\n自動ログイン（あなたのアカウント）のみ使用；CAPTCHAやログイン失敗時は「手動ログイン」を。\n「アカウント名」は本機ラベル（複数アカウント判別用）、ログインには無影響。\n「サイトURL」が実際に使われるログインURL、変更で入口を切替。',
      'account.sel_h':'ログインページ選択子','account.sel_hint':'上級 · 自動ログイン失敗時のみ','account.sel_desc':'以下は自動ログインで使うページ要素セレクタ。REINS ログインは Vue 単一ページアプリ（入力欄に name なし、id は毎回変わる）なので、安定した class で特定。',
      'account.sel_use':'用途','account.sel_sel':'セレクタ','account.sel_id':'会員ID入力欄','account.sel_pw':'パスワード入力欄','account.sel_agree':'規約同意チェックボックス','account.sel_submit':'ログインボタン',
      'cmp.f.pdf':'図面PDF','cmp.f.detail':'詳細ページ','cmp.f.src':'元サイト','spec.nodetail':'（詳細なし）',
      'cmp.bl.total':'最低価格','cmp.bl.barea':'建物面積最大','cmp.bl.imgs':'画像最多',
      'collect.switch_demo':'デモに切替','collect.switch_live':'本番に切替','collect.started':'開始しました',
      'step4.mode_lbl':'自動更新方式','step4.opt_interval':'① 定時（固定間隔）','step4.opt_random':'② ランダム（間隔変動）',
      'step4.interval_lbl':'間隔','step4.rand_min':'ランダム下限(h)','step4.rand_max':'ランダム上限(h)',
      'step4.win_from':'実行時間帯 始','step4.win_to':'実行時間帯 終',
      'test.l_login':'ログイン検証','test.l_query':'検索検証','test.l_dl':'ダウンロード検証','test.l_real':'本番ダウンロード',
      'detail.notfound_local':'本機庫に物件番号 {no} はありません。',
      'detail.chg_th_time':'時間','detail.chg_th_type':'種類','detail.chg_th_old':'変更前','detail.chg_th_new':'変更後',
      'spec.previewing':'プレビュー中…','spec.dling':'ダウンロード中…',
      'account.sel_note':'ログインには「所属機構の規程及びガイドラインを遵守します」へのチェックが必要、未チェックだとボタンは無効。変更場所：config.yaml → selectors.login。',
      'spec.unit_cnt':'件','spec.unit_pdf':'件',
      'files.pdf_hint':'全 {total} 件 ｜ 直近 {limit} 件のみ表示 ｜ それ以前は上のキーワード欄で検索',
      'files.q':'キーワード検索（条件なし）','files.q_ph':'例：中之島 / グランドメゾン / 3001407 / 3ＬＤＫ / 西区',
      'files.q_btn':'検索','files.q_clear':'クリア',
      'files.q_found':'{n} 件一致 ｜ 先頭 {shown} 件を表示',
      'files.q_none':'一致するPDFがありません。別の語でお試しください：建物名 / 住所 / 物件番号 / 沿線・駅 / 間取り / 区。',
      'files.q_tip':'キーワードは「物件検索」と同じ基準：住所 / 建物名 / 物件番号 / 沿線・駅 / 間取り / 区 / 種目 / 築年月。空白区切りの複数語はすべて一致が必要。',
      'files.th_house':'物件（基本情報）','files.house_unknown':'本機DBに該当物件なし',
      'account.setpw':'アカウントのパスワード設定',
      'account.setpw_note':'「社員管理」ページへ：社員作成、ワンタイムコード再発行、現在のコードのコピー、管理者設定 / 無効化（同ページは本機 8765 のみ）。'
    }
  };
  ['zh-TW','en','ja'].forEach(function (l) {
    var o = EXT[l] || {};
    for (var k in o) { if (DICT[l]) DICT[l][k] = o[k]; }
  });

  /* ============ v1.8.2：补齐「JS 动态文案」的 zh-CN 词条 ============
     ⚠ 历史教训（勇哥 2026-09-17 真机截图）：i18n 的 zh-CN 走**两条不同路径**——
       · 静态元素 `<span data-i18n="k">中文</span>`：用元素原文（el.__o）兜底，
         **缺 key 根本看不出来**；
       · JS 动态串 `t('k')` / `tf('k')`：**只查 ZH 字典**，缺 key 就把 key 原样上屏
         —— 查询页「排序」下拉里直接显示 `sort.area` / `sort.price` / `sort.unit_sqm`。
     v1.8.1 加 R20/R21/R22 时，这批 key **只写进了 EXT['zh-TW']（繁体）**，没进 ZH，
     于是「繁体界面正常、简体界面裸 key」；当时验收截图恰好是繁体，就漏过去了。
     ⇒ 门禁：tools/verify_i18n_keys.py（按「谁会被 t() 取用」逐 key 查 ZH；发布前必跑）。 */
  ZH['sort.h']='排序'; ZH['sort.dir']='方向';
  ZH['sort.updated']='更新时间'; ZH['sort.price']='价格'; ZH['sort.area']='面积';
  ZH['sort.unit_sqm']='㎡单价'; ZH['sort.unit_tsubo']='坪单价';
  ZH['sort.drop_amt']='降价额'; ZH['sort.drop_pct']='降价率'; ZH['sort.drop_date']='降价日期';
  ZH['sort.built']='建筑年份'; ZH['sort.bukken']='物件番号';
  ZH['sort.clear']='清除排序';
  ZH['sort.dir_desc']='降序 ↓'; ZH['sort.dir_asc']='升序 ↑';
  ZH['sort.dir_tip']='降序：数值/日期 由大到小（新→旧）；升序：由小到大（旧→新）';
  ZH['sort.now']='当前：{k} {d}';
  ZH['view.toggle']='详细 ⇄ 简洁'; ZH['filter.edit']='修改条件 ▾'; ZH['filter.collapse']='收起条件 ▴';
  ZH['search.today_short']='今天'; ZH['search.date_swap']='起止日期已自动调换';
  ZH['search.date_caliber.any']='登録日或変更日';
  ZH['search.date_caliber.change']='変更日（平台变更）';
  ZH['search.date_caliber.reg']='登録日（平台新建）';
  ZH['search.date_caliber.dl']='下载日（本地抓到）';
  ZH['pub.h']='上传到线上'; ZH['pub.never']='还没传过'; ZH['pub.saved']='上传设置已保存';
  ZH['pub.date_from']='上传日期 起'; ZH['pub.date_to']='上传日期 止'; ZH['pub.auto']='自动上传定时器';
  ZH['pub.interval']='上传周期（分钟）'; ZH['pub.preview']='预览会传多少'; ZH['pub.on']='运行中';
  ZH['pub.off']='已暂停'; ZH['pub.pause']='暂停'; ZH['pub.start']='启动'; ZH['pub.next']='下次上传：';
  ZH['pub.every']='每 '; ZH['pub.min']=' 分钟自动上传'; ZH['pub.paused_tip']='已暂停（点「启动」即按周期自动上传）'; ZH['pub.log_empty']='暂无上传记录';
  ZH['pub.previewing']='计算中…'; ZH['pub.preview_count']='范围内共 '; ZH['pub.preview_fail']='预览失败';
  ZH['cat.ms_hint']='点选即可，可多选';
  // D7/D5（v1.8.2）：roundplan.*（检索计划卡）/ recon.*（概览对账卡）
  ZH['roundplan.h']='每轮检索计划'; ZH['roundplan.hint']='自动更新每一轮抓什么；改完立即生效';
  ZH['roundplan.main_round']='全期間主轮（数据主力 + 下架基线）';
  ZH['roundplan.main_round_tip']='关掉则只跑当天同步，不补历史详情、也不判下架（极少用）';
  ZH['roundplan.backfill']='前一天补齐';
  ZH['roundplan.backfill_tip']='上一日尾巴断了自动补搜一次（一天最多一次，常态不补）';
  ZH['roundplan.preview_h']='本轮实际检索计划';
  ZH['roundplan.p1']='① 平台日期同步：当天 登録/変更 2 轴 × 6 组 = 12 次';
  ZH['roundplan.p2']='② 全期間主轮：6 组 × 2 轴 = 12 次（补详情 + 図面 + 下架判定）';
  ZH['roundplan.p3']='③ 前日补齐（若触发）：前一天 12 次';
  ZH['roundplan.save']='保存检索计划'; ZH['roundplan.saved']='已保存';
  ZH['recon.h']='数据对账'; ZH['recon.hint']='线上报告总数 vs 本机已抓 vs 本轮变化 vs 待补详情';
  ZH['recon.online']='线上报告总数'; ZH['recon.live']='本机已抓（在架）';
  ZH['recon.gap']='缺口（我方漏采）'; ZH['recon.new']='本轮新增'; ZH['recon.changed']='本轮变更'; ZH['recon.pending']='待补详情';
  DICT['zh-TW']['sort.h']='排序'; DICT['zh-TW']['sort.dir']='方向';
  DICT['zh-TW']['sort.updated']='更新時間'; DICT['zh-TW']['sort.price']='價格';
  DICT['zh-TW']['sort.area']='面積'; DICT['zh-TW']['sort.unit_sqm']='㎡單價';
  DICT['zh-TW']['sort.unit_tsubo']='坪單價'; DICT['zh-TW']['sort.drop_amt']='降價額';
  DICT['zh-TW']['sort.drop_pct']='降價率'; DICT['zh-TW']['sort.drop_date']='降價日期';
  DICT['zh-TW']['sort.built']='建築年份'; DICT['zh-TW']['sort.bukken']='物件番號';
  DICT['zh-TW']['sort.clear']='清除排序';
  DICT['zh-TW']['sort.dir_desc']='降序 ↓'; DICT['zh-TW']['sort.dir_asc']='升序 ↑';
  DICT['zh-TW']['sort.dir_tip']='降序：數值/日期 由大到小（新→舊）；升序：由小到大（舊→新）';
  DICT['zh-TW']['sort.now']='目前：{k} {d}';
  DICT['zh-TW']['search.today_short']='今天'; DICT['zh-TW']['search.date_swap']='起止日期已自動調換';
  DICT['zh-TW']['search.date_caliber.any']='登録日或変更日';
  DICT['zh-TW']['search.date_caliber.change']='変更日（平台變更）';
  DICT['zh-TW']['search.date_caliber.reg']='登録日（平台新建）';
  DICT['zh-TW']['search.date_caliber.dl']='下載日（本機抓到）';
  DICT['zh-TW']['search.other_detail']='隱藏其他詳情'; DICT['zh-TW']['search.other_hide_off']='顯示其他詳情';
  DICT['zh-TW']['search.reins_search']='物件番號檢索'; DICT['zh-TW']['cat.ms_hint']='點選即可，可多選';
  DICT['en']['sort.h']='Sort'; DICT['en']['sort.dir']='Direction';
  DICT['en']['sort.updated']='Updated'; DICT['en']['sort.price']='Price';
  DICT['en']['sort.area']='Area'; DICT['en']['sort.unit_sqm']='Unit price /㎡';
  DICT['en']['sort.unit_tsubo']='Unit price /坪'; DICT['en']['sort.drop_amt']='Price drop';
  DICT['en']['sort.drop_pct']='Drop %'; DICT['en']['sort.drop_date']='Drop date';
  DICT['en']['sort.built']='Year built'; DICT['en']['sort.bukken']='Property no.';
  DICT['en']['sort.clear']='Clear sort';
  DICT['en']['sort.dir_desc']='Desc ↓'; DICT['en']['sort.dir_asc']='Asc ↑';
  DICT['en']['sort.dir_tip']='Desc = large to small (new to old); Asc = small to large (old to new)';
  DICT['en']['sort.now']='Now: {k} {d}';
  DICT['en']['search.today_short']='Today'; DICT['en']['search.date_swap']='Start/end date swapped';
  DICT['en']['search.date_caliber.any']='Reg or change date';
  DICT['en']['search.date_caliber.change']='Change date (platform)';
  DICT['en']['search.date_caliber.reg']='Registration date (platform)';
  DICT['en']['search.date_caliber.dl']='Download date (local)';
  DICT['en']['search.other_detail']='Hide extra details'; DICT['en']['search.other_hide_off']='Show extra details';
  DICT['en']['search.reins_search']='Property-no search'; DICT['en']['cat.ms_hint']='Click to select, multi-select OK';
  DICT['ja']['sort.h']='並び替え'; DICT['ja']['sort.dir']='方向';
  DICT['ja']['sort.updated']='更新時間'; DICT['ja']['sort.price']='価格';
  DICT['ja']['sort.area']='面積'; DICT['ja']['sort.unit_sqm']='㎡単価';
  DICT['ja']['sort.unit_tsubo']='坪単価'; DICT['ja']['sort.drop_amt']='値下げ額';
  DICT['ja']['sort.drop_pct']='値下げ率'; DICT['ja']['sort.drop_date']='値下げ日';
  DICT['ja']['sort.built']='築年月'; DICT['ja']['sort.bukken']='物件番号';
  DICT['ja']['sort.clear']='並び替えを解除';
  DICT['ja']['sort.dir_desc']='降順 ↓'; DICT['ja']['sort.dir_asc']='昇順 ↑';
  DICT['ja']['sort.dir_tip']='降順：数値/日付を大きい順（新しい順）／昇順：小さい順（古い順）';
  DICT['ja']['sort.now']='現在：{k} {d}';
  DICT['ja']['search.today_short']='今日'; DICT['ja']['search.date_swap']='開始日と終了日を入れ替えました';
  DICT['ja']['search.date_caliber.any']='登録日または変更日';
  DICT['ja']['search.date_caliber.change']='変更日（プラットフォーム）';
  DICT['ja']['search.date_caliber.reg']='登録日（プラットフォーム）';
  DICT['ja']['search.date_caliber.dl']='ダウンロード日（本機）';
  DICT['ja']['search.other_detail']='その他の詳細を隠す'; DICT['ja']['search.other_hide_off']='その他の詳細を表示';
  DICT['ja']['search.reins_search']='物件番号検索'; DICT['ja']['cat.ms_hint']='クリックで選択、複数可';
  // D7/D5（v1.8.2）：roundplan.* / recon.* 四语齐套
  DICT['zh-TW']['roundplan.h']='每輪檢索計劃'; DICT['zh-TW']['roundplan.hint']='自動更新每一輪抓什麼；改完立即生效';
  DICT['zh-TW']['roundplan.main_round']='全期間主輪（資料主力 + 下架基線）';
  DICT['zh-TW']['roundplan.main_round_tip']='關掉則只跑當天同步，不補歷史詳情、也不判下架（極少用）';
  DICT['zh-TW']['roundplan.backfill']='前一天補齊';
  DICT['zh-TW']['roundplan.backfill_tip']='上一日尾巴斷了自動補搜一次（一天最多一次，常態不補）';
  DICT['zh-TW']['roundplan.preview_h']='本輪實際檢索計劃';
  DICT['zh-TW']['roundplan.p1']='① 平台日期同步：當天 登録/変更 2 軸 × 6 組 = 12 次';
  DICT['zh-TW']['roundplan.p2']='② 全期間主輪：6 組 × 2 軸 = 12 次（補詳情 + 図面 + 下架判定）';
  DICT['zh-TW']['roundplan.p3']='③ 前日補齊（若觸發）：前一天 12 次';
  DICT['zh-TW']['roundplan.save']='保存檢索計劃'; DICT['zh-TW']['roundplan.saved']='已保存';
  DICT['zh-TW']['recon.h']='資料對帳'; DICT['zh-TW']['recon.hint']='線上報告總數 vs 本機已抓 vs 本輪變化 vs 待補詳情';
  DICT['zh-TW']['recon.online']='線上報告總數'; DICT['zh-TW']['recon.live']='本機已抓（在架）';
  DICT['zh-TW']['recon.gap']='缺口（我方漏採）'; DICT['zh-TW']['recon.new']='本輪新增'; DICT['zh-TW']['recon.changed']='本輪變更'; DICT['zh-TW']['recon.pending']='待補詳情';
  DICT['en']['roundplan.h']='Round fetch plan'; DICT['en']['roundplan.hint']='What each auto-update round fetches; takes effect immediately';
  DICT['en']['roundplan.main_round']='Full-period main round (data backbone + delist baseline)';
  DICT['en']['roundplan.main_round_tip']='Off = only sync today, no backfill of history detail and no delist check (rarely used)';
  DICT['en']['roundplan.backfill']='Previous-day backfill';
  DICT['en']['roundplan.backfill_tip']='Auto re-searches the previous day once if its tail broke (at most once/day, normally off)';
  DICT['en']['roundplan.preview_h']='This round\'s actual fetch plan';
  DICT['en']['roundplan.p1']='① Platform date sync: today reg/change 2 axes × 6 groups = 12 searches';
  DICT['en']['roundplan.p2']='② Full-period main round: 6 groups × 2 axes = 12 searches (detail + floorplan + delist)';
  DICT['en']['roundplan.p3']='③ Previous-day backfill (if triggered): previous day 12 searches';
  DICT['en']['roundplan.save']='Save fetch plan'; DICT['en']['roundplan.saved']='Saved';
  DICT['en']['recon.h']='Data reconciliation'; DICT['en']['recon.hint']='Platform total vs local fetched vs this round\'s changes vs pending detail';
  DICT['en']['recon.online']='Platform reported total'; DICT['en']['recon.live']='Local fetched (live)';
  DICT['en']['recon.gap']='Gap (we missed)'; DICT['en']['recon.new']='New this round'; DICT['en']['recon.changed']='Changed this round'; DICT['en']['recon.pending']='Pending detail';
  DICT['ja']['roundplan.h']='毎ラウンド取得計画'; DICT['ja']['roundplan.hint']='自動更新の各ラウンドで何を取得するか。保存で即反映';
  DICT['ja']['roundplan.main_round']='全期間メインラウンド（データ主体 ＋ 下架基準）';
  DICT['ja']['roundplan.main_round_tip']='オフにすると当日同期のみ。履歴詳細の補完も下架判定も行わない（ほぼ使わない）';
  DICT['ja']['roundplan.backfill']='前日補完';
  DICT['ja']['roundplan.backfill_tip']='前日の末尾が切れた場合のみ自動で再検索（1日最大1回、通常は行わない）';
  DICT['ja']['roundplan.preview_h']='このラウンドの実際の取得計画';
  DICT['ja']['roundplan.p1']='① プラットフォーム日付同期：当日 登録/変更 2 軸 × 6 組 = 12 回';
  DICT['ja']['roundplan.p2']='② 全期間メインラウンド：6 組 × 2 軸 = 12 回（詳細 ＋ 間取図 ＋ 下架判定）';
  DICT['ja']['roundplan.p3']='③ 前日補完（発動時）：前日 12 回';
  DICT['ja']['roundplan.save']='取得計画を保存'; DICT['ja']['roundplan.saved']='保存済';
  DICT['ja']['recon.h']='データ突合'; DICT['ja']['recon.hint']='プラットフォーム総数 vs 本機取得 vs 本ラウンド変化 vs 詳細待ち';
  DICT['ja']['recon.online']='プラットフォーム報告総数'; DICT['ja']['recon.live']='本機取得（掲載中）';
  DICT['ja']['recon.gap']='欠損（未取得分）'; DICT['ja']['recon.new']='本ラウンド新規'; DICT['ja']['recon.changed']='本ラウンド変更'; DICT['ja']['recon.pending']='詳細待ち';

  /* ---- v1.8.5：⑤ 对账三段式 + 下钻 ／ ⑥ 计划预览 ／ #235 单键收合 ---- */
  // ⑤ 对账三段式（recon.*）
  ZH['recon.hint']='平台报告 → 我方扫描 → 缺口归因（点每段展开明细）';
  ZH['recon.seg.platform']='平台报告'; ZH['recon.seg.local']='我方扫描'; ZH['recon.seg.gap']='缺口归因';
  ZH['recon.sub.groups']='共 {n} 组';
  ZH['recon.sub.local']='累计 {total} ｜ 待补详情 {pending}';
  ZH['recon.sub.gap']='漏采 {missed} ｜ 平台截断 {truncated}';
  ZH['recon.th.group']='组'; ZH['recon.th.online']='平台报告'; ZH['recon.th.local']='我方扫到';
  ZH['recon.th.gap']='缺口'; ZH['recon.th.cause']='归因'; ZH['recon.th.at']='时间';
  ZH['recon.th.trigger']='触发'; ZH['recon.th.fetched']='已抓'; ZH['recon.th.status']='状态';
  ZH['recon.cause.ok']='无缺口'; ZH['recon.cause.truncated']='平台 500 截断'; ZH['recon.cause.missed']='我方漏采';
  ZH['recon.note.cap']='平台单次检索最多可浏览 {cap} 条（10 页 × 50），超出部分翻不到，属「平台截断」，非我方漏采。';
  ZH['recon.runs.h']='最近轮次（下钻）'; ZH['recon.drill.empty']='暂无明细';
  // ⑥ 计划预览（roundplan.*）
  ZH['roundplan.date_mode']='日期模式'; ZH['roundplan.mode_today']='当日（今天）';
  ZH['roundplan.mode_specified']='指定日期'; ZH['roundplan.mode_all']='全期間（不带日期）';
  ZH['roundplan.preview_btn']='预览本轮计划'; ZH['roundplan.preview_of']='本轮实际会跑的检索';
  ZH['roundplan.preview_meta']='共 {n} 次 ｜ 日期：{date}';
  ZH['roundplan.th.subtype']='物件種目'; ZH['roundplan.th.axis']='日期轴'; ZH['roundplan.th.date']='日期';
  ZH['roundplan.jump']='跳到该组'; ZH['roundplan.jumped']='已把 ④ 下载条件预填为这一组';
  ZH['roundplan.all_period']='全期間';
  ZH['roundplan.plus_main']='＋ 全期間主轮（已勾选）：再跑 6 组 × 2 轴 = 12 次';
  ZH['roundplan.plus_backfill']='＋ 前一天补齐（已勾选）：前日再跑 12 次';
  // #235 单键双向收合
  ZH['filter.toggle_tip']='展开 / 收起查询条件';
  // #235：文案由 syncFilterToggle() 写入 #filterToggleTxt，箭头在**单独的** #filterToggleCare
  // span 里 —— 所以这里**不再带 ▾/▴**（否则会出现两个箭头）。顺带补齐 en/ja 缺失的这俩键，
  // 否则切到英/日文时 t() 会回退成中文（非裸 key，但四语不齐）。
  ZH['filter.edit']='修改条件'; ZH['filter.collapse']='收起条件';
  DICT['zh-TW']['filter.edit']='修改條件'; DICT['zh-TW']['filter.collapse']='收起條件';
  DICT['en']['filter.edit']='Modify filters'; DICT['en']['filter.collapse']='Collapse filters';
  DICT['ja']['filter.edit']='条件を変更'; DICT['ja']['filter.collapse']='条件を折りたたむ';

  DICT['zh-TW']['recon.hint']='平台報告 → 我方掃描 → 缺口歸因（點每段展開明細）';
  DICT['zh-TW']['recon.seg.platform']='平台報告'; DICT['zh-TW']['recon.seg.local']='我方掃描'; DICT['zh-TW']['recon.seg.gap']='缺口歸因';
  DICT['zh-TW']['recon.sub.groups']='共 {n} 組';
  DICT['zh-TW']['recon.sub.local']='累計 {total} ｜ 待補詳情 {pending}';
  DICT['zh-TW']['recon.sub.gap']='漏採 {missed} ｜ 平台截斷 {truncated}';
  DICT['zh-TW']['recon.th.group']='組'; DICT['zh-TW']['recon.th.online']='平台報告'; DICT['zh-TW']['recon.th.local']='我方掃到';
  DICT['zh-TW']['recon.th.gap']='缺口'; DICT['zh-TW']['recon.th.cause']='歸因'; DICT['zh-TW']['recon.th.at']='時間';
  DICT['zh-TW']['recon.th.trigger']='觸發'; DICT['zh-TW']['recon.th.fetched']='已抓'; DICT['zh-TW']['recon.th.status']='狀態';
  DICT['zh-TW']['recon.cause.ok']='無缺口'; DICT['zh-TW']['recon.cause.truncated']='平台 500 截斷'; DICT['zh-TW']['recon.cause.missed']='我方漏採';
  DICT['zh-TW']['recon.note.cap']='平台單次檢索最多可瀏覽 {cap} 條（10 頁 × 50），超出部分翻不到，屬「平台截斷」，非我方漏採。';
  DICT['zh-TW']['recon.runs.h']='最近輪次（下鑽）'; DICT['zh-TW']['recon.drill.empty']='暫無明細';
  DICT['zh-TW']['roundplan.date_mode']='日期模式'; DICT['zh-TW']['roundplan.mode_today']='當日（今天）';
  DICT['zh-TW']['roundplan.mode_specified']='指定日期'; DICT['zh-TW']['roundplan.mode_all']='全期間（不帶日期）';
  DICT['zh-TW']['roundplan.preview_btn']='預覽本輪計劃'; DICT['zh-TW']['roundplan.preview_of']='本輪實際會跑的檢索';
  DICT['zh-TW']['roundplan.preview_meta']='共 {n} 次 ｜ 日期：{date}';
  DICT['zh-TW']['roundplan.th.subtype']='物件種目'; DICT['zh-TW']['roundplan.th.axis']='日期軸'; DICT['zh-TW']['roundplan.th.date']='日期';
  DICT['zh-TW']['roundplan.jump']='跳到該組'; DICT['zh-TW']['roundplan.jumped']='已把 ④ 下載條件預填為這一組';
  DICT['zh-TW']['roundplan.all_period']='全期間';
  DICT['zh-TW']['roundplan.plus_main']='＋ 全期間主輪（已勾選）：再跑 6 組 × 2 軸 = 12 次';
  DICT['zh-TW']['roundplan.plus_backfill']='＋ 前一天補齊（已勾選）：前日再跑 12 次';
  DICT['zh-TW']['filter.toggle_tip']='展開 / 收起查詢條件';

  DICT['en']['recon.hint']='Platform report → Our scan → Gap attribution (click a row to expand)';
  DICT['en']['recon.seg.platform']='Platform report'; DICT['en']['recon.seg.local']='Our scan'; DICT['en']['recon.seg.gap']='Gap attribution';
  DICT['en']['recon.sub.groups']='{n} groups';
  DICT['en']['recon.sub.local']='Cumulative {total} ｜ Pending detail {pending}';
  DICT['en']['recon.sub.gap']='Missed {missed} ｜ Platform cap {truncated}';
  DICT['en']['recon.th.group']='Group'; DICT['en']['recon.th.online']='Platform'; DICT['en']['recon.th.local']='Our scan';
  DICT['en']['recon.th.gap']='Gap'; DICT['en']['recon.th.cause']='Cause'; DICT['en']['recon.th.at']='Time';
  DICT['en']['recon.th.trigger']='Trigger'; DICT['en']['recon.th.fetched']='Fetched'; DICT['en']['recon.th.status']='Status';
  DICT['en']['recon.cause.ok']='No gap'; DICT['en']['recon.cause.truncated']='Platform 500 cap'; DICT['en']['recon.cause.missed']='We missed';
  DICT['en']['recon.note.cap']='One search can browse at most {cap} listings (10 pages × 50); anything beyond is unreachable, counted as "platform cap", not our miss.';
  DICT['en']['recon.runs.h']='Recent rounds (drill-down)'; DICT['en']['recon.drill.empty']='No detail yet';
  DICT['en']['roundplan.date_mode']='Date mode'; DICT['en']['roundplan.mode_today']='Today';
  DICT['en']['roundplan.mode_specified']='Specific date'; DICT['en']['roundplan.mode_all']='All periods (no date)';
  DICT['en']['roundplan.preview_btn']='Preview this round'; DICT['en']['roundplan.preview_of']='Searches this round will run';
  DICT['en']['roundplan.preview_meta']='{n} searches ｜ Date: {date}';
  DICT['en']['roundplan.th.subtype']='Property type'; DICT['en']['roundplan.th.axis']='Date axis'; DICT['en']['roundplan.th.date']='Date';
  DICT['en']['roundplan.jump']='Jump to group'; DICT['en']['roundplan.jumped']='Prefilled ④ download conditions for this group';
  DICT['en']['roundplan.all_period']='All periods';
  DICT['en']['roundplan.plus_main']='＋ Full-period main round (checked): 6 groups × 2 axes = 12 more searches';
  DICT['en']['roundplan.plus_backfill']='＋ Previous-day backfill (checked): 12 more searches';
  DICT['en']['filter.toggle_tip']='Expand / collapse the filters';

  DICT['ja']['recon.hint']='プラットフォーム報告 → 本機取得 → 欠損の要因（クリックで展開）';
  DICT['ja']['recon.seg.platform']='プラットフォーム報告'; DICT['ja']['recon.seg.local']='本機取得'; DICT['ja']['recon.seg.gap']='欠損の要因';
  DICT['ja']['recon.sub.groups']='全 {n} 組';
  DICT['ja']['recon.sub.local']='累計 {total} ｜ 詳細待ち {pending}';
  DICT['ja']['recon.sub.gap']='未取得 {missed} ｜ 平台上限制 {truncated}';
  DICT['ja']['recon.th.group']='組'; DICT['ja']['recon.th.online']='平台報告'; DICT['ja']['recon.th.local']='本機取得';
  DICT['ja']['recon.th.gap']='欠損'; DICT['ja']['recon.th.cause']='要因'; DICT['ja']['recon.th.at']='時刻';
  DICT['ja']['recon.th.trigger']='トリガー'; DICT['ja']['recon.th.fetched']='取得済'; DICT['ja']['recon.th.status']='状態';
  DICT['ja']['recon.cause.ok']='欠損なし'; DICT['ja']['recon.cause.truncated']='平台 500 上限'; DICT['ja']['recon.cause.missed']='未取得（本機）';
  DICT['ja']['recon.note.cap']='1 回の検索で閲覧できるのは最大 {cap} 件（10 ページ × 50）まで。超過分は取得不能のため「平台上限制」に分類し、未取得とはみなしません。';
  DICT['ja']['recon.runs.h']='最近のラウンド（ドリルダウン）'; DICT['ja']['recon.drill.empty']='明細はまだありません';
  DICT['ja']['roundplan.date_mode']='日付モード'; DICT['ja']['roundplan.mode_today']='当日（今日）';
  DICT['ja']['roundplan.mode_specified']='日付指定'; DICT['ja']['roundplan.mode_all']='全期間（日付なし）';
  DICT['ja']['roundplan.preview_btn']='本ラウンドをプレビュー'; DICT['ja']['roundplan.preview_of']='本ラウンドで実行される検索';
  DICT['ja']['roundplan.preview_meta']='全 {n} 回 ｜ 日付：{date}';
  DICT['ja']['roundplan.th.subtype']='物件種目'; DICT['ja']['roundplan.th.axis']='日付軸'; DICT['ja']['roundplan.th.date']='日付';
  DICT['ja']['roundplan.jump']='この組へ'; DICT['ja']['roundplan.jumped']='④ ダウンロード条件をこの組に設定しました';
  DICT['ja']['roundplan.all_period']='全期間';
  DICT['ja']['roundplan.plus_main']='＋ 全期間メインラウンド（選択済）：さらに 6 組 × 2 軸 = 12 回';
  DICT['ja']['roundplan.plus_backfill']='＋ 前日補完（選択済）：前日さらに 12 回';
  DICT['ja']['filter.toggle_tip']='検索条件を展開 / 折りたたむ';

  /* ---- v1.8.6：修「取引態様面板显示裸 key」 ----
     ⚠ 根因：search.trade_type.* 这几个键**只存在于繁体字典**（上面 line 108-110 的 zh-TW 块），
     简体字典 ZH（line 514）里没有 → 简体界面 t() 查不到 → 回退显示 `search.trade_type.seller`。
     这与 v1.8.1 的 sort.* 是同一个坑。门禁（verify_i18n_keys.py）的动态前缀没覆盖它 → 漏检，
     已同步把 search.trade_type. 加进门禁。
     值保留「日文原名 + 中文释义」的写法（用户要能对应回 REINS 上的原文）。 */
  ZH['search.trade_type.all']='全部';
  ZH['search.trade_type.seller']='売主（业主直售）';
  ZH['search.trade_type.sennin']='専任（专任媒介）';
  ZH['search.trade_type.senzoku']='専属（专属媒介）';
  ZH['search.trade_type.dairi']='代理（卖方代理）';
  ZH['search.trade_type.ippan']='一般（一般媒介）';
  // 多选面板「全选」（勇哥要求：除了清空，还要能一键全选）
  ZH['cat.select_all']='全选'; ZH['cat.selected_all']='已全选 {n} 项';

  DICT['zh-TW']['search.trade_type.all']='全部';
  DICT['zh-TW']['search.trade_type.seller']='売主（業主直售）';
  DICT['zh-TW']['search.trade_type.sennin']='専任（專任媒介）';
  DICT['zh-TW']['search.trade_type.senzoku']='専属（專屬媒介）';
  DICT['zh-TW']['search.trade_type.dairi']='代理（賣方代理）';
  DICT['zh-TW']['search.trade_type.ippan']='一般（一般媒介）';
  DICT['zh-TW']['cat.select_all']='全選'; DICT['zh-TW']['cat.selected_all']='已全選 {n} 項';

  DICT['en']['search.trade_type.all']='All';
  DICT['en']['search.trade_type.seller']='売主 (direct from owner)';
  DICT['en']['search.trade_type.sennin']='専任 (exclusive agency)';
  DICT['en']['search.trade_type.senzoku']='専属 (sole agency)';
  DICT['en']['search.trade_type.dairi']='代理 (seller agency)';
  DICT['en']['search.trade_type.ippan']='一般 (open listing)';
  DICT['en']['cat.select_all']='Select all'; DICT['en']['cat.selected_all']='All {n} selected';

  DICT['ja']['search.trade_type.all']='すべて';
  DICT['ja']['search.trade_type.seller']='売主（元付・直接取引）';
  DICT['ja']['search.trade_type.sennin']='専任（専任媒介）';
  DICT['ja']['search.trade_type.senzoku']='専属（専属専任媒介）';
  DICT['ja']['search.trade_type.dairi']='代理（売主代理）';
  DICT['ja']['search.trade_type.ippan']='一般（一般媒介）';
  DICT['ja']['cat.select_all']='すべて選択'; DICT['ja']['cat.selected_all']='{n} 件を全選択';

  /* ---- v1.8.6：详情页内嵌 PDF 卡片（替掉原来的「打开本地 PDF」跳转按钮）---- */
  ZH['detail.pdf_h']='房源図面 PDF'; ZH['detail.pdf_hint']='本机 PDF 原件（不会上传到线上）';
  DICT['zh-TW']['detail.pdf_h']='房源図面 PDF'; DICT['zh-TW']['detail.pdf_hint']='本機 PDF 原件（不會上傳到線上）';
  DICT['en']['detail.pdf_h']='Property floorplan PDF'; DICT['en']['detail.pdf_hint']='Local PDF original (never uploaded to the public site)';
  DICT['ja']['detail.pdf_h']='物件図面 PDF'; DICT['ja']['detail.pdf_hint']='ローカル PDF 原本（公開サイトにはアップロードしません）';

  // 补充 key（按钮文案含动态态 / 切换提示）
  ZH['step4.btn_run']='保存并启动自动更新'; ZH['step4.btn_upd']='保存并更新自动更新'; ZH['collect.sw_msg']='已切换到：{mode}';
  DICT['zh-TW']['step4.btn_run']='保存並啟動自動更新'; DICT['zh-TW']['step4.btn_upd']='保存並更新自動更新'; DICT['zh-TW']['collect.sw_msg']='已切換到：{mode}';
  DICT['en']['step4.btn_run']='Save & start auto-update'; DICT['en']['step4.btn_upd']='Save & update auto-update'; DICT['en']['collect.sw_msg']='Switched to: {mode}';
  DICT['ja']['step4.btn_run']='保存して自動更新開始'; DICT['ja']['step4.btn_upd']='保存して自動更新更新'; DICT['ja']['collect.sw_msg']='切替完了：{mode}';

  /* ---------- v1.2.0：更新方式改「随机间隔 · 分钟制」 ---------- */
  ZH['step4.btn_start']='保存并启动';
  ZH['step4.saveonly']='保存设置（暂不启动）';
  ZH['step4.stop']='停止自动更新';
  ZH['step4.rand_min_min']='随机下限（分钟）'; ZH['step4.rand_max_min']='随机上限（分钟）';
  ZH['step4.min_tip']='每轮之间的间隔在你设的下限–上限之间随机取值（更像真人操作，避免被平台判定为机器）。最低 5 分钟。';
  DICT['zh-TW']['step4.btn_start']='儲存並啟動';
  DICT['zh-TW']['step4.rand_min_min']='隨機下限（分鐘）'; DICT['zh-TW']['step4.rand_max_min']='隨機上限（分鐘）';
  DICT['zh-TW']['step4.min_tip']='每輪之間的間隔在你設的下限–上限之間隨機取值（更像真人操作，避免被平台判定為機器）。最低 5 分鐘。';
  DICT['en']['step4.btn_start']='Save & Start';
  DICT['en']['step4.rand_min_min']='Random min (min)'; DICT['en']['step4.rand_max_min']='Random max (min)';
  DICT['en']['step4.min_tip']='Each round waits a random number of minutes between your lower and upper bound (more human-like, avoids being flagged as a bot). Minimum 5 minutes.';
  DICT['ja']['step4.btn_start']='保存して起動';
  DICT['ja']['step4.rand_min_min']='ランダム下限（分）'; DICT['ja']['step4.rand_max_min']='ランダム上限（分）';
  DICT['ja']['step4.min_tip']='各回の間隔は設定した下限〜上限の間でランダムに決まります（人間らしい操作で、機械判定を避けるため）。最短 5 分。';

  /* ---------- v1.2.0：④ 下载条件（原「指定日期下载」并入「数据抓取设置」） ---------- */
  ZH['dlcond.h']='下载条件'; ZH['dlcond.hint']='决定「正式下载」抓什么；不想用的条件不用管';
  ZH['dlcond.pause']='点「正式下载」时会<strong>自动暂停「当天的下载」</strong>，跑完自动恢复，保证同一时刻只有一条 REINS 抓取在跑。';
  ZH['dlcond.core']='核心条件'; ZH['dlcond.date_lbl']='日期（可多选）';
  ZH['dlcond.core_hint']='更新与下载都按这里跑，默认覆盖一户建 2 个 + 公寓 4 个';
  ZH['dlcond.sub_hint']='默认已勾好「一户建 2 个 + 公寓 4 个」；一次下载会按種別自动分成几组检索，结果合并去重';
  ZH['dlcond.grp_house']='一户建'; ZH['dlcond.grp_apt']='公寓'; ZH['dlcond.grp_land']='土地';
  ZH['dlcond.dt_reg']='当天登录（登録年月日＝当天）'; ZH['dlcond.dt_chg']='当天更新（変更年月日＝当天）';
  ZH['dlcond.opt']='可选条件（默认不启用）'; ZH['dlcond.opt_show']='展开 ▾'; ZH['dlcond.opt_hide']='收起 ▴';
  ZH['dlcond.use_price']='价格（万円）'; ZH['dlcond.use_area']='面积（㎡）';
  ZH['dlcond.preview']='① 确定（实时预览）'; ZH['dlcond.download']='② 正式下载';
  ZH['dlcond.alert_sub']='请至少勾选一个物件種目。';
  ZH['step3.d3']='正式全量下载移到了下面的 <b>④ 下载条件</b>，那里可以先配条件再下。';
  ZH['spec.kind_lbl']='物件種別'; ZH['spec.subtype_lbl']='物件種目（可多选）';
  DICT['zh-TW']['dlcond.h']='下載條件'; DICT['zh-TW']['dlcond.hint']='決定「正式下載」抓什麼；不想用的條件不用管';
  DICT['zh-TW']['dlcond.pause']='點「正式下載」時會<strong>自動暫停「當天的下載」</strong>，跑完自動恢復，保證同一時刻只有一條 REINS 抓取在跑。';
  DICT['zh-TW']['dlcond.core']='核心條件'; DICT['zh-TW']['dlcond.date_lbl']='日期（可多選）';
  DICT['zh-TW']['dlcond.core_hint']='更新與下載都按這裡跑，預設涵蓋一戶建 2 個 + 公寓 4 個';
  DICT['zh-TW']['dlcond.sub_hint']='預設已勾選「一戶建 2 個 + 公寓 4 個」；一次下載會依種別自動分成數組檢索，結果合併去重';
  DICT['zh-TW']['dlcond.grp_house']='一戶建'; DICT['zh-TW']['dlcond.grp_apt']='公寓'; DICT['zh-TW']['dlcond.grp_land']='土地';
  DICT['zh-TW']['dlcond.dt_reg']='當天登錄（登録年月日＝當天）'; DICT['zh-TW']['dlcond.dt_chg']='當天更新（変更年月日＝當天）';
  DICT['zh-TW']['dlcond.opt']='可選條件（預設不啟用）'; DICT['zh-TW']['dlcond.opt_show']='展開 ▾'; DICT['zh-TW']['dlcond.opt_hide']='收起 ▴';
  DICT['zh-TW']['dlcond.use_price']='價格（万円）'; DICT['zh-TW']['dlcond.use_area']='面積（㎡）';
  DICT['zh-TW']['dlcond.preview']='① 確定（即時預覽）'; DICT['zh-TW']['dlcond.download']='② 正式下載';
  DICT['zh-TW']['dlcond.alert_sub']='請至少勾選一個物件種目。';
  DICT['zh-TW']['step3.d3']='正式全量下載移到了下面的 <b>④ 下載條件</b>，那裡可以先設條件再下。';
  DICT['zh-TW']['spec.kind_lbl']='物件種別'; DICT['zh-TW']['spec.subtype_lbl']='物件種目（可多選）';
  DICT['en']['dlcond.h']='Download conditions'; DICT['en']['dlcond.hint']='Decides what the formal download fetches; skip any you do not need';
  DICT['en']['dlcond.pause']='Clicking "Formal download" <strong>pauses the daily download</strong> and resumes it afterwards, so only one REINS crawl runs at a time.';
  DICT['en']['dlcond.core']='Core conditions'; DICT['en']['dlcond.date_lbl']='Date (multi-select)';
  DICT['en']['dlcond.core_hint']='Both updates and downloads follow this list — defaults cover 2 house + 4 apartment subtypes';
  DICT['en']['dlcond.sub_hint']='The first 2 house + 4 apartment subtypes are ticked by default; one run splits into a few searches by kind and merges the results';
  DICT['en']['dlcond.grp_house']='Houses'; DICT['en']['dlcond.grp_apt']='Apartments'; DICT['en']['dlcond.grp_land']='Land';
  DICT['en']['dlcond.dt_reg']='Registered today (登録年月日 = today)'; DICT['en']['dlcond.dt_chg']='Updated today (変更年月日 = today)';
  DICT['en']['dlcond.opt']='Optional conditions (off by default)'; DICT['en']['dlcond.opt_show']='Expand ▾'; DICT['en']['dlcond.opt_hide']='Collapse ▴';
  DICT['en']['dlcond.use_price']='Price (10k JPY)'; DICT['en']['dlcond.use_area']='Area (㎡)';
  DICT['en']['dlcond.preview']='① OK (live preview)'; DICT['en']['dlcond.download']='② Formal download';
  DICT['en']['dlcond.alert_sub']='Please tick at least one property type.';
  DICT['en']['step3.d3']='The full formal download moved to <b>④ Download conditions</b> below, where you can set conditions first.';
  DICT['en']['spec.kind_lbl']='物件種別'; DICT['en']['spec.subtype_lbl']='物件種目 (multi-select)';
  DICT['ja']['dlcond.h']='ダウンロード条件'; DICT['ja']['dlcond.hint']='「正式ダウンロード」で何を取得するかを決めます。不要な条件は放っておいて構いません';
  DICT['ja']['dlcond.pause']='「正式ダウンロード」を押すと<strong>当日のダウンロードを自動で一時停止</strong>し、終了後に再開します。同時に走る REINS 取得は常に 1 本だけです。';
  DICT['ja']['dlcond.core']='コア条件'; DICT['ja']['dlcond.date_lbl']='日付（複数選択可）';
  DICT['ja']['dlcond.core_hint']='更新・ダウンロードはここに従います（既定で戸建2＋マンション4）';
  DICT['ja']['dlcond.sub_hint']='既定で戸建2＋マンション4にチェック済み。1回の取得は種別ごとに数回の検索に分けて実行し、結果を統合します';
  DICT['ja']['dlcond.grp_house']='戸建'; DICT['ja']['dlcond.grp_apt']='マンション'; DICT['ja']['dlcond.grp_land']='土地';
  DICT['ja']['dlcond.dt_reg']='当日登録（登録年月日＝当日）'; DICT['ja']['dlcond.dt_chg']='当日更新（変更年月日＝当日）';
  DICT['ja']['dlcond.opt']='オプション条件（既定は無効）'; DICT['ja']['dlcond.opt_show']='展開 ▾'; DICT['ja']['dlcond.opt_hide']='折りたたむ ▴';
  DICT['ja']['dlcond.use_price']='価格（万円）'; DICT['ja']['dlcond.use_area']='面積（㎡）';
  DICT['ja']['dlcond.preview']='① 確定（リアルタイム確認）'; DICT['ja']['dlcond.download']='② 正式ダウンロード';
  DICT['ja']['dlcond.alert_sub']='物件種目を最低 1 つ選択してください。';
  DICT['ja']['step3.d3']='正式な全件ダウンロードは下の <b>④ ダウンロード条件</b> に移動しました。条件を設定してから実行できます。';
  DICT['ja']['spec.kind_lbl']='物件種別'; DICT['ja']['spec.subtype_lbl']='物件種目（複数選択可）';

  /* ---------- v1.2.0：查询页条件保留 ---------- */
  ZH['search.qstate']='当前为上次的查询条件'; ZH['search.qstate_reset']='重置为默认';
  DICT['zh-TW']['search.qstate']='目前為上次的查詢條件'; DICT['zh-TW']['search.qstate_reset']='重置為預設';
  DICT['en']['search.qstate']='Showing your last query conditions'; DICT['en']['search.qstate_reset']='Reset to default';
  DICT['ja']['search.qstate']='前回の検索条件を表示中'; DICT['ja']['search.qstate_reset']='既定値にリセット';

  // 补充 key（账号页提示 + 运行日志表头）
  ZH['account.oktip']='测试登录：'; ZH['account.cleared']='已清空';
  ZH['runs.th_id']='#'; ZH['runs.th_trigger']='触发'; ZH['runs.th_start']='开始'; ZH['runs.th_fetched']='落库'; ZH['runs.th_status']='状态'; ZH['runs.th_ponline']='平台件数'; ZH['runs.th_pmine']='我方件数'; ZH['runs.th_pdiff']='差额(归因)';
  DICT['zh-TW']['account.oktip']='測試登錄：'; DICT['zh-TW']['account.cleared']='已清空';
  DICT['zh-TW']['runs.th_id']='#'; DICT['zh-TW']['runs.th_trigger']='觸發'; DICT['zh-TW']['runs.th_start']='開始'; DICT['zh-TW']['runs.th_fetched']='格納'; DICT['zh-TW']['runs.th_status']='狀態'; DICT['zh-TW']['runs.th_ponline']='平台件數'; DICT['zh-TW']['runs.th_pmine']='我方件數'; DICT['zh-TW']['runs.th_pdiff']='差額(歸因)';
  DICT['en']['account.oktip']='Test login: '; DICT['en']['account.cleared']='Cleared';
  DICT['en']['runs.th_id']='#'; DICT['en']['runs.th_trigger']='Trigger'; DICT['en']['runs.th_start']='Start'; DICT['en']['runs.th_fetched']='Saved'; DICT['en']['runs.th_status']='Status'; DICT['en']['runs.th_ponline']='Platform'; DICT['en']['runs.th_pmine']='Mine'; DICT['en']['runs.th_pdiff']='Diff(reason)';
  DICT['ja']['account.oktip']='ログイン検証：'; DICT['ja']['account.cleared']='クリアしました';
  DICT['ja']['runs.th_id']='#'; DICT['ja']['runs.th_trigger']='トリガー'; DICT['ja']['runs.th_start']='開始'; DICT['ja']['runs.th_fetched']='格納'; DICT['ja']['runs.th_status']='状態'; DICT['ja']['runs.th_ponline']='プラットフォーム件数'; DICT['ja']['runs.th_pmine']='自側件数'; DICT['ja']['runs.th_pdiff']='差(帰因)';

  // v1.4.1 · 运行记录表头悬停说明（含义 + 使用规则）。概览页与运行日志页共用同一套文案。
  ZH['search.q']='关键词';
  ZH['search.q.tip']='地址 / 楼名 / 物件番号 / 駅・沿線 / 間取り，模糊匹配，含即命中；空格分隔多词＝都要命中';
  ZH['runs.tip.id']='轮次唯一编号（自增）。越大越新；报错时按这个号去日志里定位';
  ZH['runs.tip.trigger']='本轮由什么发起。manual＝手动更新；random/interval＝调度器自动；startup＝服务启动即跑；specified-download＝指定日期下载。用它区分「人为的还是自动的」';
  ZH['runs.tip.start']='本轮开始时间（本机时区）';
  ZH['runs.tip.end']='本轮结束时间。为空＝还没结束（进行中）；若状态是 interrupted，说明上次进程被直接关掉、没正常收尾（启动时会自愈补状态）';
  ZH['runs.tip.scan']='本轮从列表页读到的房源条数（列表层）。v1.4.0 起与「落库」分离：只刷新了列表字段的轮次，扫描>0 而落库=0 是正常的，不是没跑';
  ZH['runs.tip.fetched']='本轮真正写入/更新详情的条数（详情层）。两阶段下载中，这一项在阶段2（补详情）才会增长';
  ZH['runs.tip.new']='本轮首次进入本地库的房源数。注意：平台早已改过价、我方才第一次抓到的，算「变更」不算新盘';
  ZH['runs.tip.chg']='本轮识别到的变更条数（改价 / 信息变动）。按条数计，非去重套数；同一套房可能有多条';
  ZH['runs.tip.status']='本轮结果：ok 成功 ／ running 进行中 ／ error 失败 ／ interrupted 被中断';
  ZH['runs.tip.note']='失败原因或补充说明。最多显示 70 字符，完整内容见服务日志 data/logs/server.log';
  DICT['zh-TW']['search.q']='關鍵詞';
  DICT['zh-TW']['search.q.tip']='地址 / 樓名 / 物件番號 / 駅・沿線 / 間取り，模糊匹配，含即命中；空格分隔多詞＝都要命中';
  DICT['zh-TW']['runs.tip.id']='輪次唯一編號（自增）。越大越新；報錯時按這個號去日誌裡定位';
  DICT['zh-TW']['runs.tip.trigger']='本輪由什麼發起。manual＝手動更新；random/interval＝排程自動；startup＝服務啟動即跑；specified-download＝指定日期下載。用它區分「人為的還是自動的」';
  DICT['zh-TW']['runs.tip.start']='本輪開始時間（本機時區）';
  DICT['zh-TW']['runs.tip.end']='本輪結束時間。為空＝還沒結束（進行中）；若狀態是 interrupted，表示上次程式被直接關掉、沒正常收尾（啟動時會自癒補狀態）';
  DICT['zh-TW']['runs.tip.scan']='本輪從列表頁讀到的房源筆數（列表層）。v1.4.0 起與「落庫」分離：只刷新了列表欄位的輪次，掃描>0 而落庫=0 是正常的，不是沒跑';
  DICT['zh-TW']['runs.tip.fetched']='本輪真正寫入/更新詳情的筆數（詳情層）。兩階段下載中，這一項在階段2（補詳情）才會成長';
  DICT['zh-TW']['runs.tip.new']='本輪首次進入本地庫的房源數。注意：平台早已改過價、我方才第一次抓到的，算「變更」不算新盤';
  DICT['zh-TW']['runs.tip.chg']='本輪識別到的變更筆數（改價 / 資訊變動）。按筆數計，非去重套數；同一套房可能有多筆';
  DICT['zh-TW']['runs.tip.status']='本輪結果：ok 成功 ／ running 進行中 ／ error 失敗 ／ interrupted 被中斷';
  DICT['zh-TW']['runs.tip.note']='失敗原因或補充說明。最多顯示 70 字元，完整內容見服務日誌 data/logs/server.log';
  DICT['en']['search.q']='Keyword';
  DICT['en']['search.q.tip']='Address / building / property no / station・line / layout — fuzzy match, any hit counts. Separate words with a space = ALL must match';
  DICT['en']['runs.tip.id']='Unique round id (auto-increment). Larger = newer; use it to locate the round in the server log';
  DICT['en']['runs.tip.trigger']='What started this round: manual = you clicked Manual update; random/interval = scheduler; startup = service start; specified-download = date-specified download. Use it to tell human-triggered from automatic';
  DICT['en']['runs.tip.start']='Round start time (local timezone)';
  DICT['en']['runs.tip.end']='Round end time. Empty = still running; "interrupted" means the process was killed last time and the round never finished (self-healed on next start)';
  DICT['en']['runs.tip.scan']='Properties read from listing pages this round (list layer). Since v1.4.0 this is separate from Saved: a round that only refreshed list fields shows Scanned>0 with Saved=0 — normal, not a failure';
  DICT['en']['runs.tip.fetched']='Properties whose detail was actually written/updated this round (detail layer). With two-stage download this only grows during phase 2';
  DICT['en']['runs.tip.new']='Properties entering the local library for the first time. Note: a property the platform had already repriced before we first saw it counts as Changed, not New';
  DICT['en']['runs.tip.chg']='Change records detected this round (price / info). Counted per record, not per property — one property can have several';
  DICT['en']['runs.tip.status']='Round result: ok = success / running = in progress / error = failed / interrupted = stopped mid-way';
  DICT['en']['runs.tip.note']='Failure reason or extra note. Truncated to 70 chars; full text is in data/logs/server.log';
  DICT['ja']['search.q']='キーワード';
  DICT['ja']['search.q.tip']='住所／建物名／物件番号／駅・沿線／間取りが部分一致で対象。スペース区切りは「すべて含む」条件';
  DICT['ja']['runs.tip.id']='ラウンドID（自動採番）。大きいほど新しい。エラー時はこの番号でログを追う';
  DICT['ja']['runs.tip.trigger']='何がこのラウンドを開始したか。manual＝手動更新／random・interval＝自動スケジュール／startup＝サービス起動時／specified-download＝指定日ダウンロード。「人が起動したか自動か」の判別に使う';
  DICT['ja']['runs.tip.start']='ラウンド開始時刻（ローカルタイムゾーン）';
  DICT['ja']['runs.tip.end']='ラウンド終了時刻。空欄＝まだ実行中。interrupted は前回プロセスが強制終了され正常に完了しなかったもの（次回起動時に自動補正）';
  DICT['ja']['runs.tip.scan']='一覧ページから読み取った物件数（リスト層）。v1.4.0 以降は「格納」と分離：リスト項目のみ更新したラウンドは スキャン>0 かつ 格納=0 になるが、それは正常で未実行ではない';
  DICT['ja']['runs.tip.fetched']='実際に詳細を書き込み／更新した件数（詳細層）。二段階ダウンロードではフェーズ2（詳細補完）で増える';
  DICT['ja']['runs.tip.new']='今回ローカルDBに初めて入った物件数。注意：掲載元が既に値下げしていた物件を当方が初取得した場合は「新規」ではなく「変更」扱い';
  DICT['ja']['runs.tip.chg']='今回検出した変更件数（価格／情報）。件数カウントで物件の重複排除なし。同一物件が複数入る場合あり';
  DICT['ja']['runs.tip.status']='ラウンド結果：ok 成功／running 実行中／error 失敗／interrupted 中断';
  DICT['ja']['runs.tip.note']='失敗理由や補足。70文字で丸められます。全文は data/logs/server.log を参照';

  // 补充 key（概览变更表头 / 账号页 / 查询页 / 详情动作 / 指定日期下载：动态 t() 文案，zh-CN 缺条目会显示裸 key）
  ZH['th.time']='时间'; ZH['th.property']='房源'; ZH['th.type']='类型'; ZH['th.old']='原值'; ZH['th.new']='新值';
  ZH['account.saved']='已保存'; ZH['account.save']='保存账号'; ZH['account.test']='测试登录';
  ZH['search.btn']='查询'; ZH['search.nodata']='该日期 / 条件下没有房源。';
  ZH['search.lib.running']='正在抓取：已落库 {n} 条（新盘 {newn}）。'; ZH['search.lib.runningtip']='数据正在写入，稍候刷新即可看到。';
  ZH['detail.openpdf']='打开本地 PDF'; ZH['detail.srcpage']='详情页面'; ZH['spec.dl']='下载';
  // v1.4.3：手动更新按钮三态 + 详情页"诚实的原网页" + 覆盖溢出提示
  ZH['btn.manual_running']='正在运行中…（完成后自动提示）';
  ZH['btn.manual_done']='本次已完成 ✓（再点重新跑）';
  ZH['btn.manual_busy']='系统更新中（自动）';
  // v1.4.4：补齐基础按钮 ZH 条目（否则 updateManualBtn 动态 t('btn.manual') 在 zh-CN 下显示裸 key）
  ZH['btn.manual']='▶ 手动更新（立刻抓一轮）';
  ZH['btn.trial']='试跑（最轻量）';
  ZH['btn.collect']='数据抓取设置 →';
  // 保存并启动 三态：保存并启动 → ✓ 已启动（自动更新中，由「停止自动更新」关闭）
  ZH['step4.running']='✓ 已启动（自动更新中）';
  DICT['zh-TW']['step4.running']='✓ 已啟動（自動更新中）';
  DICT['en']['step4.running']='✓ Running (auto-update active)';
  DICT['ja']['step4.running']='✓ 起動済（自動更新中）';
  // 今日降价 KPI 悬浮说明（zh-CN 用元素原 title 回退，其余语言走字典）
  DICT['zh-TW']['kpi.today_down_tip']='今日降價＝REINS 平台標註「変更年月日」為今天、且掛牌價低於變更前價的房源數。以平台自身降價的官方日期為準，避免把「今天才抓到、但前幾天就已降價」的房源算進來。今天顯示 0 表示沒有平台在今天蓋戳降價的房源（已打折但早於今天的房源不計入）。';
  DICT['en']['kpi.today_down_tip']='Price drops today = listings whose REINS "change date" (変更年月日) is today AND whose listed price is below the prior price. Based on the platform\'s own price-change stamp, so a listing we only fetched today but that dropped days ago is NOT counted. 0 today means no listing was stamped as dropped by the platform today (already-discounted listings dated earlier are excluded).';
  DICT['ja']['kpi.today_down_tip']='本日値下げ＝REINS が「変更年月日」を今日としており、かつ掲載価格が変更前価格を下回る物件数。プラットフォーム自身の値下げ公式日付を基準とし、今日取得しただけで数日前に下がっていた物件は含みません。今日が 0 なら、プラットフォームが「今日値下げ」と印をつけた物件はない（以前に値下げ済みの物件は除外）。';
  ZH['detail.reins_search']='REINS 物件番号検索';
  ZH['detail.search_hint']='（详情页需在 REINS 内点击「詳細」打开，无永久直链）';
  // v1.7.0：日期时间段 + 物件番号検索 + 一键隐藏中介
  ZH['search.date_to']='日期(止)';
  ZH['search.reins_search']='物件番号検索';
  // v1.7.1：全局「显示/隐藏其他详情」开关 —— 文案即按钮动作：
  //   默认中介可见 → 按钮显「隐藏其他详情」(点了就藏)；藏起后 → 显「显示其他详情」(点了就显示)。
  ZH['search.other_detail']='隐藏其他详情';
  ZH['search.other_hide_off']='显示其他详情';
  ZH['sync.over']='本机在架比线上快照多 {n} 条（时差/口径，非真实覆盖溢出）';
  DICT['zh-TW']['detail.srcpage']='詳細頁面';
  DICT['en']['detail.srcpage']='Detail page';
  DICT['ja']['detail.srcpage']='詳細ページ';

  // 补充 key（查询页：物件種目「两级目录 · 多选」面板 —— 面板整块由 JS 渲染，zh-CN 也必须有条目）
  ZH['search.all']='全部';      // JS 里 t('search.all') 用（静态 <option> 靠元素原文，JS 拿不到）
  ZH['cat.title']='两级目录'; ZH['cat.hint']='一级、二级都可多选';
  ZH['cat.pickall']='全选'; ZH['cat.clear']='清空'; ZH['cat.n']='已选 {n} 项';
  ZH['cat.more']=' 等 {n} 项';
  ZH['cat.house']='一戸建'; ZH['cat.apt']='公寓（マンション）'; ZH['cat.land']='土地';
  ZH['cat.misc']='其他'; ZH['cat.new']='新築（新）'; ZH['cat.old']='中古';
  DICT['zh-TW']['cat.title']='兩級目錄'; DICT['zh-TW']['cat.hint']='一級、二級都可多選';
  DICT['zh-TW']['cat.pickall']='全選'; DICT['zh-TW']['cat.clear']='清空';
  DICT['zh-TW']['cat.n']='已選 {n} 項';
  DICT['zh-TW']['cat.more']=' 等 {n} 項';
  DICT['zh-TW']['cat.house']='一戶建'; DICT['zh-TW']['cat.apt']='公寓（マンション）';
  DICT['zh-TW']['cat.land']='土地'; DICT['zh-TW']['cat.misc']='其他';
  DICT['zh-TW']['cat.new']='新築（新）'; DICT['zh-TW']['cat.old']='中古';
  DICT['en']['cat.title']='Two-level list'; DICT['en']['cat.hint']='multi-select at both levels';
  DICT['en']['cat.pickall']='Select all'; DICT['en']['cat.clear']='Clear';
  DICT['en']['cat.n']='{n} selected'; DICT['en']['cat.more']=' +{n} more';
  DICT['en']['cat.house']='House'; DICT['en']['cat.apt']='Apartment';
  DICT['en']['cat.land']='Land'; DICT['en']['cat.misc']='Other';
  DICT['en']['cat.new']='New'; DICT['en']['cat.old']='Used';
  DICT['ja']['cat.title']='2階層ディレクトリ'; DICT['ja']['cat.hint']='1階・2階とも複数選択可';
  DICT['ja']['cat.pickall']='すべて選択'; DICT['ja']['cat.clear']='クリア';
  DICT['ja']['cat.n']='{n} 件選択'; DICT['ja']['cat.more']=' 他 {n} 件';
  DICT['ja']['cat.house']='一戸建'; DICT['ja']['cat.apt']='マンション';
  DICT['ja']['cat.land']='土地'; DICT['ja']['cat.misc']='その他';
  DICT['ja']['cat.new']='新築'; DICT['ja']['cat.old']='中古';
  // 两级目录面板：① 一级类目 / ② 二级类目 区块标签与提示
  // （物件種別 / 物件種目 是 REINS 原始字段名，按约定各语言保持原文不变）
  ZH['cat.lv1']='一级类目'; ZH['cat.lv1sub']='物件種別';
  ZH['cat.lv2']='二级类目'; ZH['cat.lv2sub']='物件種目';
  ZH['cat.needlv1']='先勾①一级类目，二级才可选'; ZH['cat.nosub']='该一级本身就是叶子，无需再选二级';
  DICT['zh-TW']['cat.lv1']='一級類目'; DICT['zh-TW']['cat.lv1sub']='物件種別';
  DICT['zh-TW']['cat.lv2']='二級類目'; DICT['zh-TW']['cat.lv2sub']='物件種目';
  DICT['zh-TW']['cat.needlv1']='先勾①一級類目，二級才可選'; DICT['zh-TW']['cat.nosub']='該一級本身就是葉子，無需再選二級';
  DICT['en']['cat.lv1']='Level 1'; DICT['en']['cat.lv1sub']='物件種別';
  DICT['en']['cat.lv2']='Level 2'; DICT['en']['cat.lv2sub']='物件種目';
  DICT['en']['cat.needlv1']='Tick ① Level 1 first, then Level 2 becomes selectable';
  DICT['en']['cat.nosub']='This level-1 is already a leaf — no level-2.';
  DICT['ja']['cat.lv1']='1階層目'; DICT['ja']['cat.lv1sub']='物件種別';
  DICT['ja']['cat.lv2']='2階層目'; DICT['ja']['cat.lv2sub']='物件種目';
  DICT['ja']['cat.needlv1']='先に①1階層目を選択してください'; DICT['ja']['cat.nosub']='この1階層目は葉（2階層目なし）';

  // ===== 补充 key：硬编码文案国际化（逐页补齐：查询 / 数据抓取设置 / 账号 / 详情 / 指定日期下载）=====
  // 查询页：紧凑列表行的短标签（原先硬编码 専有/土地/建物/所在階/階建/築/画像 N 枚/取得）
  ZH['row.excl']='専有'; ZH['row.land']='土地'; ZH['row.bldg']='建物';
  ZH['row.floor']='所在階'; ZH['row.floors']='階建'; ZH['row.built']='築';
  ZH['row.imgs']='画像 {n} 枚'; ZH['row.fetched']='取得'; ZH['row.no_pdf']='无 PDF';
  // v1.5.3：REINS 列表右侧三个图标的说明（画=照片 / 図=間取図 / 所=所在図）
  ZH['media.photo_n']='有照片（{n} 张）';
  ZH['media.photo_yes']='有照片（张数未知，还没抓详情）';
  ZH['media.plan_yes']='有間取図（户型图）';
  ZH['media.map_yes']='有所在図（周边地图）';
  ZH['media.no']='没有这项';
  ZH['unit.count']='条'; ZH['search.lib.viewclose']='）'; ZH['misc.nodetail']='(无详情)';
  DICT['zh-TW']['row.excl']='専有'; DICT['zh-TW']['row.land']='土地'; DICT['zh-TW']['row.bldg']='建物';
  DICT['zh-TW']['row.floor']='所在樓層'; DICT['zh-TW']['row.floors']='樓層數'; DICT['zh-TW']['row.built']='築';
  DICT['zh-TW']['row.imgs']='圖片 {n} 張'; DICT['zh-TW']['row.fetched']='取得'; DICT['zh-TW']['row.no_pdf']='無 PDF';
  DICT['zh-TW']['media.photo_n']='有照片（{n} 張）';
  DICT['zh-TW']['media.photo_yes']='有照片（張數未知，還沒抓詳情）';
  DICT['zh-TW']['media.plan_yes']='有間取圖（格局圖）';
  DICT['zh-TW']['media.map_yes']='有所在圖（周邊地圖）';
  DICT['zh-TW']['media.no']='沒有這項';
  DICT['zh-TW']['unit.count']='筆'; DICT['zh-TW']['search.lib.viewclose']='）'; DICT['zh-TW']['misc.nodetail']='(無詳情)';
  DICT['en']['row.excl']='Excl.'; DICT['en']['row.land']='Land'; DICT['en']['row.bldg']='Bldg';
  DICT['en']['row.floor']='Floor'; DICT['en']['row.floors']='Floors'; DICT['en']['row.built']='Built';
  DICT['en']['row.imgs']='Images {n}'; DICT['en']['row.fetched']='Fetched'; DICT['en']['row.no_pdf']='No PDF';
  DICT['en']['media.photo_n']='Has photos ({n})';
  DICT['en']['media.photo_yes']='Has photos (count unknown — detail not fetched yet)';
  DICT['en']['media.plan_yes']='Has floor plan (間取図)';
  DICT['en']['media.map_yes']='Has location map (所在図)';
  DICT['en']['media.no']='Not available';
  DICT['en']['unit.count']='items'; DICT['en']['search.lib.viewclose']=')'; DICT['en']['misc.nodetail']='(no details)';
  DICT['ja']['row.excl']='専有'; DICT['ja']['row.land']='土地'; DICT['ja']['row.bldg']='建物';
  DICT['ja']['row.floor']='所在階'; DICT['ja']['row.floors']='階建'; DICT['ja']['row.built']='築';
  DICT['ja']['row.imgs']='画像 {n} 枚'; DICT['ja']['row.fetched']='取得'; DICT['ja']['row.no_pdf']='PDFなし';
  DICT['ja']['media.photo_n']='写真あり（{n} 枚）';
  DICT['ja']['media.photo_yes']='写真あり（枚数不明・詳細未取得）';
  DICT['ja']['media.plan_yes']='間取図あり';
  DICT['ja']['media.map_yes']='所在図あり';
  DICT['ja']['media.no']='なし';
  DICT['ja']['unit.count']='件'; DICT['ja']['search.lib.viewclose']='）'; DICT['ja']['misc.nodetail']='(詳細なし)';

  // 查询列表行的「状态标」：新增 / 变更（实心彩色胶囊，一眼可见）
  ZH['row.tag_new']='新增'; ZH['row.tag_changed']='变更';
  DICT['zh-TW']['row.tag_new']='新增'; DICT['zh-TW']['row.tag_changed']='變更';
  DICT['en']['row.tag_new']='New'; DICT['en']['row.tag_changed']='Changed';
  DICT['ja']['row.tag_new']='新規'; DICT['ja']['row.tag_changed']='変更';

  // 降价来源标签（v1.2.4）：平台历史降价（REINS 変更前価格，下载前已发生）/ 本系统监测降价（未来能力）
  ZH['row.src_platform']='平台'; ZH['row.src_system']='系统';
  DICT['zh-TW']['row.src_platform']='平台'; DICT['zh-TW']['row.src_system']='系統';
  DICT['en']['row.src_platform']='Platform'; DICT['en']['row.src_system']='System';
  DICT['ja']['row.src_platform']='掲載元'; DICT['ja']['row.src_system']='当社検知';

  // v1.7.4 R8：取引態様徽章提示 ／ R3：客户版（隐藏中介信息）开关
  ZH['row.trade_type_tip']='取引態様'; ZH['cmp.hide_agency']='隐藏中介信息（客户版）';
  DICT['zh-TW']['row.trade_type_tip']='取引態様'; DICT['zh-TW']['cmp.hide_agency']='隱藏仲介資訊（客戶版）';
  DICT['en']['row.trade_type_tip']='Transaction type'; DICT['en']['cmp.hide_agency']='Hide agency info (client version)';
  DICT['ja']['row.trade_type_tip']='取引態様'; DICT['ja']['cmp.hide_agency']='仲介情報を非表示（顧客版）';

  // v1.9.0：物件番号旁的「REINS 物件番号検索」按钮 —— 打开后的轻提示
  ZH['detail.searching']='REINS 検索中…';
  ZH['detail.toast_open']='已带番号打开 REINS 検索页：{no}';
  ZH['detail.toast_copied']='已打开 REINS 検索页；番号 {no} 已复制，粘贴即可搜';
  DICT['zh-TW']['detail.toast_open']='已帶番號開啟 REINS 檢索頁：{no}';
  DICT['zh-TW']['detail.toast_copied']='已開啟 REINS 檢索頁；番號 {no} 已複製，貼上即可搜';
  DICT['en']['detail.toast_open']='Opened REINS search with no. {no}';
  DICT['en']['detail.toast_copied']='Opened REINS search; no. {no} copied — just paste it';
  DICT['ja']['detail.toast_open']='REINS 物件番号検索を開きました：{no}';
  DICT['ja']['detail.toast_copied']='REINS 物件番号検索を開きました。番号 {no} をコピー済み（貼り付けで検索）';
  DICT['ja']['detail.searching']='REINS 検索中…';
  DICT['zh-TW']['detail.searching']='REINS 檢索中…';
  DICT['en']['detail.searching']='Searching REINS…';
  ZH['cmp.reins_batch']='REINS 检索'; ZH['cmp.reins_batch_empty']='对比栏为空';
  DICT['zh-TW']['cmp.reins_batch']='REINS 檢索'; DICT['zh-TW']['cmp.reins_batch_empty']='對比欄為空';
  DICT['en']['cmp.reins_batch']='REINS search'; DICT['en']['cmp.reins_batch_empty']='Compare bar is empty';
  DICT['ja']['cmp.reins_batch']='REINS 検索'; DICT['ja']['cmp.reins_batch_empty']='比較バーは空です';

  // v1.4.0 · 同步状态面板 / 完成通知 / 「详情待补」
  ZH['row.detail_pending']='详情待补'; ZH['row.detail_pending_tip']='列表已下载，详情页还没抓（下一轮会自动补，不是下载失败）';
  ZH['search.all_dates']='全部日期';
  ZH['sync.title']='同步状态'; ZH['sync.online']='线上'; ZH['sync.local']='本地';
  ZH['sync.detail_have']='已下详情'; ZH['sync.detail_wait']='待补详情';
  ZH['sync.detail_all']='累计详情'; ZH['sync.detail_all_hint']='全库口径（含历史存量）';
  ZH['sync.pdf']='図面 PDF'; ZH['sync.pdf_sub']='分母=有図面图标';
  ZH['sync.pdf_tip']='只统计列表页带「図」图标的房源；无图标的平台本就没有図面，不计分母、也不进详情页下载。';
  ZH['sync.pdf_have']='已下 PDF'; ZH['sync.pdf_wait']='待补 PDF';
  ZH['sync.pdf_all']='累计図面'; ZH['sync.pdf_all_hint']='全库有図面图标房源';
  ZH['sync.as_of']='截至'; ZH['sync.nodata']='还没有线上数据，跑一轮后就有了';
  ZH['sync.running']='进行中'; ZH['sync.pdf']='PDF';
  ZH['sync.live']='当前在架覆盖'; ZH['sync.live_sub']='REINS 今天在架 → 本机已同步';
  ZH['sync.local_live']='本机在架'; ZH['sync.remain']='未抓/触顶';
  ZH['sync.cum']='累计入库'; ZH['sync.cum_sub']='含已成交下架的历史存量';
  ZH['sync.cum_total']='累计'; ZH['sync.cum_hint']='历史存量，非覆盖率';
  ZH['sync.detail']='详情层进度'; ZH['sync.detail_sub']='两阶段下载：列表先齐、详情慢慢补';
  ZH['sync.cov_tip']='线上覆盖 = 本机今天仍在架的房源 ÷ REINS 今天在架总数，恒 ≤100%。旧版用累计存量作分子曾显示 200%+，v1.4.1 已修正。';
  ZH['sync.cum_tip']='累计入库 = 本机库里该类房源的全部条数（含已成交下架仍留库的），会大于当前在架，因此不叫"覆盖"。';
  ZH['sync.detail_tip']='已下详情 = 已抓到详情页全字段的套数；待补 = 只有列表、阶段2 还没补的（正常中间态）。';
  ZH['ntf.title']='完成通知'; ZH['ntf.mark_read']='全部标已读'; ZH['ntf.empty']='暂无通知';
  // v1.5.2：询问窗口（只在出问题时出现；15 秒没点就按默认动作走）
  ZH['dec.title']='需要你决定';
  ZH['dec.hint']='{sec} 秒内点一个按钮就按那个办；没点就按默认（{def}）继续，不会卡住抓取';
  ZH['dec.kind.session_expired']='REINS 会话失效：{reason}';
  ZH['dec.kind.inline_fail']='连续 {failed} 条点「詳細」失败（{group}）';
  ZH['dec.kind.resume_after_restart']='上一轮 run{run_id} 在第 {group_index} 组（{group}）被打断';
  DICT['zh-TW']['row.detail_pending']='詳情待補'; DICT['zh-TW']['row.detail_pending_tip']='清單已下載，詳情頁還沒抓（下一輪會自動補，不是下載失敗）';
  DICT['zh-TW']['search.all_dates']='全部日期';
  DICT['zh-TW']['sync.title']='同步狀態'; DICT['zh-TW']['sync.online']='線上'; DICT['zh-TW']['sync.local']='本地';
  DICT['zh-TW']['sync.detail_have']='已下詳情'; DICT['zh-TW']['sync.detail_wait']='待補詳情';
  DICT['zh-TW']['sync.detail_all']='累計詳情'; DICT['zh-TW']['sync.detail_all_hint']='全庫口徑（含歷史存量）';
  DICT['zh-TW']['sync.pdf']='圖面 PDF'; DICT['zh-TW']['sync.pdf_sub']='分母=有圖面圖標';
  DICT['zh-TW']['sync.pdf_tip']='只統計清單頁帶「圖」圖標的房源；無圖標的平台本就沒有圖面，不計分母、也不進詳情頁下載。';
  DICT['zh-TW']['sync.pdf_have']='已下 PDF'; DICT['zh-TW']['sync.pdf_wait']='待補 PDF';
  DICT['zh-TW']['sync.pdf_all']='累計圖面'; DICT['zh-TW']['sync.pdf_all_hint']='全庫有圖面圖標房源';
  DICT['zh-TW']['sync.as_of']='截至'; DICT['zh-TW']['sync.nodata']='還沒有線上資料，跑一輪後就有了';
  DICT['zh-TW']['sync.running']='進行中'; DICT['zh-TW']['sync.pdf']='PDF';
  DICT['zh-TW']['sync.live']='當前在架覆蓋'; DICT['zh-TW']['sync.live_sub']='REINS 今天在架 → 本機已同步';
  DICT['zh-TW']['sync.local_live']='本機在架'; DICT['zh-TW']['sync.remain']='未抓/觸頂';
  DICT['zh-TW']['sync.cum']='累計入庫'; DICT['zh-TW']['sync.cum_sub']='含已成交下架的歷史存量';
  DICT['zh-TW']['sync.cum_total']='累計'; DICT['zh-TW']['sync.cum_hint']='歷史存量，非覆蓋率';
  DICT['zh-TW']['sync.detail']='詳情層進度'; DICT['zh-TW']['sync.detail_sub']='兩階段下載：清單先齊、詳情慢慢補';
  DICT['zh-TW']['sync.cov_tip']='線上覆蓋 = 本機今天仍在架的房源 ÷ REINS 今天在架總數，恆 ≤100%。舊版用累計存量作分子曾顯示 200%+，v1.4.1 已修正。';
  DICT['zh-TW']['sync.cum_tip']='累計入庫 = 本機庫裡該類房源的全部條數（含已成交下架仍留庫的），會大於當前在架，因此不叫"覆蓋"。';
  DICT['zh-TW']['sync.detail_tip']='已下詳情 = 已抓到詳情頁全欄位的套數；待補 = 只有清單、階段2 還沒補的（正常中間態）。';
  DICT['zh-TW']['ntf.title']='完成通知'; DICT['zh-TW']['ntf.mark_read']='全部標已讀'; DICT['zh-TW']['ntf.empty']='暫無通知';
  DICT['zh-TW']['dec.title']='需要你決定';
  DICT['zh-TW']['dec.hint']='{sec} 秒內點一個按鈕就照辦；沒點就按預設（{def}）繼續，不會卡住抓取';
  DICT['zh-TW']['dec.kind.session_expired']='REINS 會話失效：{reason}';
  DICT['zh-TW']['dec.kind.inline_fail']='連續 {failed} 條點「詳細」失敗（{group}）';
  DICT['zh-TW']['dec.kind.resume_after_restart']='上一輪 run{run_id} 在第 {group_index} 組（{group}）被打斷';
  DICT['en']['row.detail_pending']='Detail pending'; DICT['en']['row.detail_pending_tip']='List row saved; detail not fetched yet (auto-filled next round, not a failure)';
  DICT['en']['search.all_dates']='All dates';
  DICT['en']['sync.title']='Sync status'; DICT['en']['sync.online']='Online'; DICT['en']['sync.local']='Local';
  DICT['en']['sync.detail_have']='With detail'; DICT['en']['sync.detail_wait']='Detail pending';
  DICT['en']['sync.detail_all']='Cumulative'; DICT['en']['sync.detail_all_hint']='Whole-DB scope (incl. history)';
  DICT['en']['sync.pdf']='Floor plan PDF'; DICT['en']['sync.pdf_sub']='denom = has floorplan icon';
  DICT['en']['sync.pdf_tip']='Counts only listings showing the 「図」(floorplan) icon; those without it have no floorplan on REINS, so excluded from denominator and never downloaded.';
  DICT['en']['sync.pdf_have']='PDF fetched'; DICT['en']['sync.pdf_wait']='PDF pending';
  DICT['en']['sync.pdf_all']='Cumulative'; DICT['en']['sync.pdf_all_hint']='Whole-DB listings with floorplan';
  DICT['en']['sync.as_of']='as of'; DICT['en']['sync.nodata']='No online data yet — run one round';
  DICT['en']['sync.running']='Running'; DICT['en']['sync.pdf']='PDF';
  DICT['en']['sync.live']='Live coverage'; DICT['en']['sync.live_sub']='REINS live today → synced locally';
  DICT['en']['sync.local_live']='Synced'; DICT['en']['sync.remain']='Missing/capped';
  DICT['en']['sync.cum']='Cumulative'; DICT['en']['sync.cum_sub']='History incl. sold/delisted';
  DICT['en']['sync.cum_total']='Total'; DICT['en']['sync.cum_hint']='History, not coverage';
  DICT['en']['sync.detail']='Detail layer'; DICT['en']['sync.detail_sub']='Two-stage: list first, details later';
  DICT['en']['sync.cov_tip']='Live coverage = properties still live today in our DB ÷ REINS live total today, always ≤100%. Old code used cumulative count as numerator (200%+); fixed in v1.4.1.';
  DICT['en']['sync.cum_tip']='Cumulative = all properties of these types in our DB (incl. sold/delisted still kept), exceeds live count, so it is NOT "coverage".';
  DICT['en']['sync.detail_tip']='With detail = properties whose detail page fields are fetched; pending = list-only, stage-2 not yet filled (normal mid-state).';
  DICT['en']['ntf.title']='Notifications'; DICT['en']['ntf.mark_read']='Mark all read'; DICT['en']['ntf.empty']='No notifications';
  DICT['en']['dec.title']='Your decision needed';
  DICT['en']['dec.hint']='Pick one within {sec}s; otherwise the default ({def}) applies — the crawl never stalls';
  DICT['en']['dec.kind.session_expired']='REINS session expired: {reason}';
  DICT['en']['dec.kind.inline_fail']='{failed} consecutive "詳細" clicks failed ({group})';
  DICT['en']['dec.kind.resume_after_restart']='Last run #{run_id} was interrupted at group {group_index} ({group})';
  DICT['ja']['row.detail_pending']='詳細待ち'; DICT['ja']['row.detail_pending_tip']='一覧は取得済み、詳細ページは未取得（次回で自動補完。失敗ではありません）';
  DICT['ja']['search.all_dates']='全期間';
  DICT['ja']['sync.title']='同期状況'; DICT['ja']['sync.online']='オンライン'; DICT['ja']['sync.local']='ローカル';
  DICT['ja']['sync.detail_have']='詳細取得済'; DICT['ja']['sync.detail_wait']='詳細待ち';
  DICT['ja']['sync.detail_all']='累計詳細'; DICT['ja']['sync.detail_all_hint']='全件対象（履歴含む）';
  DICT['ja']['sync.pdf']='間取図 PDF'; DICT['ja']['sync.pdf_sub']='分母=間取図アイコンあり';
  DICT['ja']['sync.pdf_tip']='一覧で「図」アイコンがある物件のみ集計。アイコン無しはREINS上に間取図無しのため分母外、かつ詳細頁で取得しません。';
  DICT['ja']['sync.pdf_have']='PDF取得済'; DICT['ja']['sync.pdf_wait']='PDF待ち';
  DICT['ja']['sync.pdf_all']='累計図面'; DICT['ja']['sync.pdf_all_hint']='全件のうち間取図あり';
  DICT['ja']['sync.as_of']='時点'; DICT['ja']['sync.nodata']='オンラインデータなし（1回実行すると表示されます）';
  DICT['ja']['sync.running']='実行中'; DICT['ja']['sync.pdf']='PDF';
  DICT['ja']['sync.live']='現在掲載の网羅率'; DICT['ja']['sync.live_sub']='REINS 本日掲載 → 本機同期済';
  DICT['ja']['sync.local_live']='本機同期'; DICT['ja']['sync.remain']='未取得/上限';
  DICT['ja']['sync.cum']='累計蓄積'; DICT['ja']['sync.cum_sub']='成約・掲載終了を含む履歴';
  DICT['ja']['sync.cum_total']='累計'; DICT['ja']['sync.cum_hint']='履歴であり網羅率ではない';
  DICT['ja']['sync.detail']='詳細層進捗'; DICT['ja']['sync.detail_sub']='2段階: 一覧先、詳細後';
  DICT['ja']['sync.cov_tip']='线上网羅率 = 本機で本日も掲載中の物件 ÷ REINS 本日在架総数、常に ≤100%。旧版は累計を分子にし200%+を表示、v1.4.1で修正。';
  DICT['ja']['sync.cum_tip']='累計蓄積 = 本機DBの該当種目全件（成約・掲載終了も保持）で在架を超えるため「网羅率」ではない。';
  DICT['ja']['sync.detail_tip']='詳細取得済 = 詳細ページ全項目を取得済の套数；待ち = 一覧のみで段階2未補完（正常な中間状態）。';
  DICT['ja']['ntf.title']='完了通知'; DICT['ja']['ntf.mark_read']='すべて既読'; DICT['ja']['ntf.empty']='通知はありません';
  DICT['ja']['dec.title']='判断が必要です';
  DICT['ja']['dec.hint']='{sec} 秒以内に選ぶとその通りに実行します。選ばない場合は既定（{def}）で続行し、停止しません';
  DICT['ja']['dec.kind.session_expired']='REINS セッション失効：{reason}';
  DICT['ja']['dec.kind.inline_fail']='「詳細」クリックが {failed} 件連続で失敗（{group}）';
  DICT['ja']['dec.kind.resume_after_restart']='前回 run{run_id} は第 {group_index} 組（{group}）で中断されました';

  // 数据抓取设置页：操作失败时显示的动作名（原先硬编码 切换模式/测试登录/环境自检）
  ZH['collect.lbl_switchmode']='切换模式'; ZH['collect.lbl_testlogin']='测试登录'; ZH['collect.lbl_envcheck']='环境自检';
  DICT['zh-TW']['collect.lbl_switchmode']='切換模式'; DICT['zh-TW']['collect.lbl_testlogin']='測試登錄'; DICT['zh-TW']['collect.lbl_envcheck']='環境自檢';
  DICT['en']['collect.lbl_switchmode']='Switch mode'; DICT['en']['collect.lbl_testlogin']='Test login'; DICT['en']['collect.lbl_envcheck']='Env check';
  DICT['ja']['collect.lbl_switchmode']='モード切替'; DICT['ja']['collect.lbl_testlogin']='ログイン検証'; DICT['ja']['collect.lbl_envcheck']='環境診断';

  // 账号管理页：两条硬编码提示
  ZH['account.msg_nopw']='没有可显示的密码（可能未保存或解密失败）'; ZH['account.tip_browser']='\n\n浏览器窗口会弹出，请稍候…';
  DICT['zh-TW']['account.msg_nopw']='沒有可顯示的密碼（可能未保存或解密失敗）'; DICT['zh-TW']['account.tip_browser']='\n\n瀏覽器視窗會彈出，請稍候…';
  DICT['en']['account.msg_nopw']='No password to show (not saved, or decryption failed).'; DICT['en']['account.tip_browser']='\n\nA browser window will open, please wait…';
  DICT['ja']['account.msg_nopw']='表示できるパスワードがありません（未保存、または復号失敗）'; DICT['ja']['account.tip_browser']='\n\nブラウザ画面が開きます。しばらくお待ちください…';

  // 详情页：对比篮已有该房源
  ZH['detail.inbank']='这一套已经在对比篮里了。';
  DICT['zh-TW']['detail.inbank']='這一套已經在對比籃裡了。';
  DICT['en']['detail.inbank']='This property is already in the compare basket.';
  DICT['ja']['detail.inbank']='この物件はすでに比較リストに入っています。';

  // 概览页：状态行 + 运行表头（原先整行硬编码；键只有繁/英/日，补 zh-CN）
  ZH['status.auto']='自动更新：'; ZH['status.running']='运行中'; ZH['status.stopped']='已停止';
  ZH['status.next']='下一轮'; ZH['status.busy']='正在进行'; ZH['status.outwin']='当前不在运行时段';
  ZH['th.id']='#'; ZH['th.start']='开始'; ZH['th.trigger']='触发'; ZH['th.scanned']='扫描';
  ZH['th.fetched']='落库'; ZH['th.change']='变更'; ZH['th.status']='状态';
  ZH['link.allruns']='查看全部运行日志 →';
  // 运行表的「新盘」列（与变更表的 th.new=新值 区分，避免同键冲突）
  ZH['th.newrun']='新盘';
  DICT['zh-TW']['th.newrun']='新盤'; DICT['en']['th.newrun']='New'; DICT['ja']['th.newrun']='新規';

  // 对比页：detail_json 附加字段的标签（原先 JP2CN 硬编码中文）
  ZH['xf.trade_type']='交易方式'; ZH['xf.use_zone']='用途地域'; ZH['xf.management_fee']='管理费（每月）';
  ZH['xf.repair_fund']='修缮积立金（每月）'; ZH['xf.broker']='中介公司'; ZH['xf.broker_tel']='中介电话';
  ZH['xf.public_status']='公开状态'; ZH['xf.source_url']='来源链接'; ZH['xf.structure']='构造';
  ZH['xf.status_now']='现状'; ZH['xf.handover']='交房'; ZH['xf.balcony']='阳台';
  ZH['xf.direction']='朝向'; ZH['xf.total_units']='总户数'; ZH['xf.parking']='停车场';
  ZH['xf.transport']='交通'; ZH['xf.selling_point']='卖点'; ZH['xf.facilities']='设备';
  ZH['xf.remarks']='备注'; ZH['xf.floor']='楼层'; ZH['xf.surroundings']='周边环境';
  // v1.7.2：此前漏收录、以机器键上屏的那批（所在区/㎡单价/坪单价/照片/户型图/位置图）+ 有·无
  ZH['xf.ward']='所在区'; ZH['xf.unit_price_sqm']='㎡单价'; ZH['xf.unit_price_tsubo']='坪单价';
  ZH['xf.has_photo']='照片'; ZH['xf.has_floorplan']='户型图'; ZH['xf.has_map']='位置图';
  ZH['xf.yes']='有'; ZH['xf.no']='无';
  DICT['zh-TW']['xf.trade_type']='交易方式'; DICT['zh-TW']['xf.use_zone']='用途地域'; DICT['zh-TW']['xf.management_fee']='管理費（每月）';
  DICT['zh-TW']['xf.repair_fund']='修繕積立金（每月）'; DICT['zh-TW']['xf.broker']='仲介公司'; DICT['zh-TW']['xf.broker_tel']='仲介電話';
  DICT['zh-TW']['xf.public_status']='公開狀態'; DICT['zh-TW']['xf.source_url']='來源連結'; DICT['zh-TW']['xf.structure']='構造';
  DICT['zh-TW']['xf.status_now']='現況'; DICT['zh-TW']['xf.handover']='交屋'; DICT['zh-TW']['xf.balcony']='陽台';
  DICT['zh-TW']['xf.direction']='朝向'; DICT['zh-TW']['xf.total_units']='總戶數'; DICT['zh-TW']['xf.parking']='停車場';
  DICT['zh-TW']['xf.transport']='交通'; DICT['zh-TW']['xf.selling_point']='賣點'; DICT['zh-TW']['xf.facilities']='設備';
  DICT['zh-TW']['xf.remarks']='備註'; DICT['zh-TW']['xf.floor']='樓層'; DICT['zh-TW']['xf.surroundings']='周邊環境';
  DICT['zh-TW']['xf.ward']='所在區'; DICT['zh-TW']['xf.unit_price_sqm']='㎡單價'; DICT['zh-TW']['xf.unit_price_tsubo']='坪單價';
  DICT['zh-TW']['xf.has_photo']='照片'; DICT['zh-TW']['xf.has_floorplan']='戶型圖'; DICT['zh-TW']['xf.has_map']='位置圖';
  DICT['zh-TW']['xf.yes']='有'; DICT['zh-TW']['xf.no']='無';
  DICT['en']['xf.trade_type']='Trade type'; DICT['en']['xf.use_zone']='Use zone'; DICT['en']['xf.management_fee']='Management fee (mo.)';
  DICT['en']['xf.repair_fund']='Repair fund (mo.)'; DICT['en']['xf.broker']='Agency'; DICT['en']['xf.broker_tel']='Agency tel';
  DICT['en']['xf.public_status']='Public status'; DICT['en']['xf.source_url']='Source link'; DICT['en']['xf.structure']='Structure';
  DICT['en']['xf.status_now']='Condition'; DICT['en']['xf.handover']='Handover'; DICT['en']['xf.balcony']='Balcony';
  DICT['en']['xf.direction']='Facing'; DICT['en']['xf.total_units']='Total units'; DICT['en']['xf.parking']='Parking';
  DICT['en']['xf.transport']='Transport'; DICT['en']['xf.selling_point']='Selling points'; DICT['en']['xf.facilities']='Facilities';
  DICT['en']['xf.remarks']='Remarks'; DICT['en']['xf.floor']='Floor'; DICT['en']['xf.surroundings']='Surroundings';
  DICT['en']['xf.ward']='Ward'; DICT['en']['xf.unit_price_sqm']='Price per sqm'; DICT['en']['xf.unit_price_tsubo']='Price per tsubo';
  DICT['en']['xf.has_photo']='Photos'; DICT['en']['xf.has_floorplan']='Floor plan'; DICT['en']['xf.has_map']='Site map';
  DICT['en']['xf.yes']='Yes'; DICT['en']['xf.no']='No';
  DICT['ja']['xf.trade_type']='取引態様'; DICT['ja']['xf.use_zone']='用途地域'; DICT['ja']['xf.management_fee']='管理費（月額）';
  DICT['ja']['xf.repair_fund']='修繕積立金（月額）'; DICT['ja']['xf.broker']='仲介会社'; DICT['ja']['xf.broker_tel']='仲介電話';
  DICT['ja']['xf.public_status']='公開状態'; DICT['ja']['xf.source_url']='出典リンク'; DICT['ja']['xf.structure']='構造';
  DICT['ja']['xf.status_now']='現況'; DICT['ja']['xf.handover']='引渡'; DICT['ja']['xf.balcony']='バルコニー';
  DICT['ja']['xf.direction']='方角'; DICT['ja']['xf.total_units']='総戸数'; DICT['ja']['xf.parking']='駐車場';
  DICT['ja']['xf.transport']='交通'; DICT['ja']['xf.selling_point']='セールスポイント'; DICT['ja']['xf.facilities']='設備';
  DICT['ja']['xf.remarks']='備考'; DICT['ja']['xf.floor']='階'; DICT['ja']['xf.surroundings']='周辺環境';
  DICT['ja']['xf.ward']='所在区'; DICT['ja']['xf.unit_price_sqm']='㎡単価'; DICT['ja']['xf.unit_price_tsubo']='坪単価';
  DICT['ja']['xf.has_photo']='写真'; DICT['ja']['xf.has_floorplan']='間取図'; DICT['ja']['xf.has_map']='所在図';
  DICT['ja']['xf.yes']='あり'; DICT['ja']['xf.no']='なし';

  // 通用：操作失败/弹窗兜底标题
  ZH['ov.act']='操作';
  DICT['zh-TW']['ov.act']='操作'; DICT['en']['ov.act']='Action'; DICT['ja']['ov.act']='操作';

  // 指定日期下载：zh-CN 兜底（否则简体下警报标题显示裸 key）
  ZH['spec.preview.h']='① 预览结果';

  function getLang() { return localStorage.getItem('lang') || 'zh-CN'; }
  function setLang(l) {
    localStorage.setItem('lang', l);
    document.documentElement.lang = l;
    applyI18n();
    try { window.dispatchEvent(new Event('i18n:changed')); } catch (e) {}
  }
  function t(key) {
    if (key == null) return key;
    var l = getLang();
    var v;
    if (l === 'zh-CN') {
      v = (ZH && ZH[key] != null) ? ZH[key] : null;
    } else {
      v = (DICT[l] && DICT[l][key] != null) ? DICT[l][key]
        : ((ZH && ZH[key] != null) ? ZH[key] : null);
    }
    if (v != null) return v;
    // 缺失 key：开发期在控制台告警，便于发现「裸 key 泄漏」（如 btn.manual 在 zh-CN 下显示裸 key）
    if (typeof console !== 'undefined' && typeof key === 'string' && key.indexOf('.') > 0) {
      console.warn('[i18n] 缺失翻译 key: ' + key + ' (lang=' + l + ')');
    }
    return key;
  }

  // 带 {name} 占位符的翻译（用于含数字的动态串）
  function tf(key, p) {
    var s = t(key);
    if (p) { for (var k in p) { s = s.split('{' + k + '}').join(p[k]); } }
    return s;
  }

  // 取某语言的翻译；zh-CN 或无翻译时返回 null（交由调用方回退到元素原文）
  function dictText(key, lang) {
    if (lang === 'zh-CN') return null;
    if (DICT[lang] && DICT[lang][key] != null) return DICT[lang][key];
    return null;
  }

  function trText(el) {
    var key = el.getAttribute('data-i18n');
    var v = dictText(key, getLang());
    if (v != null) el.innerHTML = v;
    else el.innerHTML = (el.__o != null) ? el.__o : (v != null ? v : key);
  }

  function applyI18n() {
    var l = getLang();
    if (l !== 'zh-CN') document.documentElement.lang = l;
    // 缓存原始中文文案（含内部标签，如 <b>），zh-CN 下直接还原
    document.querySelectorAll('[data-i18n]').forEach(function (el) {
      if (el.__o == null) el.__o = el.innerHTML;
      trText(el);
    });
    document.querySelectorAll('[data-i18n-ph]').forEach(function (el) {
      if (el.__oph == null) el.__oph = el.getAttribute('placeholder') || '';
      var v = dictText(el.getAttribute('data-i18n-ph'), l);
      el.setAttribute('placeholder', (v != null) ? v : el.__oph);
    });
    document.querySelectorAll('[data-i18n-title]').forEach(function (el) {
      if (el.__ot == null) el.__ot = el.getAttribute('title') || '';
      var v = dictText(el.getAttribute('data-i18n-title'), l);
      el.setAttribute('title', (v != null) ? v : el.__ot);
    });
    // 切换器高亮
    document.querySelectorAll('.lang-switcher button').forEach(function (b) {
      b.classList.toggle('on', b.getAttribute('data-lang') === l);
    });
  }

  function buildSwitcher() {
    var bar = document.querySelector('.lang-switcher');
    if (!bar) return;
    bar.innerHTML = '';
    LANGS.forEach(function (L) {
      var b = document.createElement('button');
      b.setAttribute('data-lang', L.code);
      b.textContent = L.label;
      b.className = 'lang-btn';
      b.setAttribute('data-i18n-title', 'lang.label');
      b.onclick = function () { setLang(L.code); };
      bar.appendChild(b);
    });
    applyI18n();
  }

  function init() {
    buildSwitcher();
    applyI18n();
    // 动态渲染后由页面脚本调用 window.applyI18n() 再次套用
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else { init(); }

  window.I18N = DICT;
  window.t = t;
  window.tf = tf;
  window.applyI18n = applyI18n;
  window.setLang = setLang;
})();
