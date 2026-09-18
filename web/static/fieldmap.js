/* ============================================================
 * fieldmap.js —— 字段名小词典（v1.2.0 公共化）
 *
 * 本机库 detail_json 的键**不是**给人看的：它既可能是英文键
 * （trade_type / management_fee …，抓取时定的），也可能是 REINS 的
 * 日文键（管理費 / 修繕積立金 …，兼容历史数据）。
 *
 * 这里把「原始键 → 当前语言的字段名」统一收口，供三处共用：
 *   ① 房源查询页的行内展开区（search.html → fillDetail）
 *   ② 房源对比页（compare.html → 详情字段并集）
 *   ③ 房源详情页（detail.html）
 *
 * ⚠ 硬规则（用户明确要求，勿违反）：
 *   字段名一律取自本项目 i18n 字典的 xf.* 系列，由我们按**业务含义**自己定义；
 *   **禁止**照抄 REINS 官方译名或第三方网站的译法。
 *
 *   v1.7.2 修订（勇哥 2026-09-16 反馈「后台的语言翻译成正常的」）：
 *   旧规则「未收录的键保留原始键」**作废** —— 它会让 has_map / unit_price_sqm 这类
 *   机器键直接上屏给客户看。现在改为：**未收录 = 不显示 + 控制台告警**，
 *   并由 tools/verify_field_labels.py 拿真实库全量键做断言，缺一个就红。
 *   宁可少显示一个字段，也绝不把后台机器语言露给客户。
 * ============================================================ */
(function () {
  'use strict';

  /* 原始键（英文键 / REINS 日文键） → i18n 键（四语言，优先走这条） */
  var FIELD_KEY = {
    /* —— 本机库实际使用的英文键 —— */
    'trade_type': 'xf.trade_type',
    'use_zone': 'xf.use_zone',
    'management_fee': 'xf.management_fee',
    'repair_fund': 'xf.repair_fund',
    'broker': 'xf.broker',
    'broker_tel': 'xf.broker_tel',
    'public_status': 'xf.public_status',
    'source_url': 'xf.source_url',
    'structure': 'xf.structure',
    'status_now': 'xf.status_now',
    'handover': 'xf.handover',
    'balcony': 'xf.balcony',
    'direction': 'xf.direction',
    'total_units': 'xf.total_units',
    'parking': 'xf.parking',
    'transport': 'xf.transport',
    'selling_point': 'xf.selling_point',
    'facilities': 'xf.facilities',
    'remarks': 'xf.remarks',
    'surroundings': 'xf.surroundings',
    /* —— v1.7.2 补录：这几个此前漏收录，直接以机器键上屏（勇哥反馈的那批） —— */
    'ward': 'xf.ward',
    'unit_price_sqm': 'xf.unit_price_sqm',
    'unit_price_tsubo': 'xf.unit_price_tsubo',
    'has_photo': 'xf.has_photo',
    'has_floorplan': 'xf.has_floorplan',
    'has_map': 'xf.has_map',
    /* —— REINS 日文键（兼容历史 / 外部数据） —— */
    '管理費': 'xf.management_fee',
    '修繕積立金': 'xf.repair_fund',
    '構造': 'xf.structure',
    '用途地域': 'xf.use_zone',
    '現況': 'xf.status_now',
    '引渡': 'xf.handover',
    'バルコニー': 'xf.balcony',
    '方角': 'xf.direction',
    '総戸数': 'xf.total_units',
    '駐車場': 'xf.parking',
    '交通': 'xf.transport',
    'セールスポイント': 'xf.selling_point',
    '設備': 'xf.facilities',
    '備考': 'xf.remarks',
    '取引態様': 'xf.trade_type',
    '階': 'xf.floor',
    '周辺環境': 'xf.surroundings'
  };

  /* —— 内部键：抓取/存储用的机器字段，**永不显示**（不是给人看的） —— */
  var FIELD_SKIP = {
    'pdf_path': 1, 'pdf_url': 1, 'absent_runs': 1, 'detail_json': 1,
    '_ms_pdf': 1, '__fp__': 1,
    'has_detail': 1, 'media': 1, 'image_count': 1,
    /* 列表区/页头已经单独渲染过的核心列，网格里不重复显示 */
    'property_no': 1, 'property_subtype': 1, 'kind': 1, 'address': 1, 'building_name': 1,
    'price': 1, 'previous_price': 1, 'land_area': 1, 'exclusive_area': 1, 'building_area': 1,
    'layout': 1, 'floor': 1, 'above_ground_floors': 1, 'built_year_month': 1, 'line_station': 1,
    'registration_date': 1, 'change_date': 1, 'first_seen_at': 1, 'last_seen_at': 1
  };

  /* 原始键 → 中文兜底（i18n 未覆盖时用；覆盖范围＝真实库全量键，由
     tools/verify_field_labels.py 断言，缺一个即红） */
  var FIELD_CN = {
    /* —— 本机库实际使用的英文键 —— */
    'trade_type': '交易方式', 'use_zone': '用途地域', 'management_fee': '管理费（每月）',
    'repair_fund': '修缮积立金（每月）', 'broker': '中介公司', 'broker_tel': '中介电话',
    'public_status': '公开状态', 'source_url': '来源链接', 'structure': '构造',
    'status_now': '现状', 'handover': '交房', 'balcony': '阳台', 'direction': '朝向',
    'total_units': '总户数', 'parking': '停车场', 'transport': '交通',
    'selling_point': '卖点', 'facilities': '设备', 'remarks': '备注', 'surroundings': '周边环境',
    /* v1.7.2 补录：这几个此前漏收录 → 直接以英文机器键上屏（勇哥反馈的那批） */
    'ward': '所在区', 'unit_price_sqm': '㎡单价', 'unit_price_tsubo': '坪单价',
    'has_photo': '照片', 'has_floorplan': '户型图', 'has_map': '位置图',
    'building_type': '建筑种类', 'land_right': '权利', 'ground_area': '土地面积',
    'road_width': '道路宽度', 'city_plan': '城市规划', 'building_coverage': '建蔽率',
    'floor_area_ratio': '容积率', 'delivery_date': '交付时间', 'contract_type': '契约类型',
    /* —— REINS 日文键（兼容历史 / 外部数据） —— */
    '管理費': '管理费（每月）', '修繕積立金': '修缮积立金（每月）', '構造': '构造',
    '用途地域': '用途地域', '現況': '现状', '引渡': '交房', 'バルコニー': '阳台',
    '方角': '朝向', '総戸数': '总户数', '駐車場': '停车场', '交通': '交通',
    'セールスポイント': '卖点', '設備': '设备', '備考': '备注', '取引態様': '交易方式',
    '階': '楼层', '周辺環境': '周边环境',
    '地目': '地目', '接道状況': '接道状况', '土地権利': '土地权利', '都市計画': '城市规划',
    '建ぺい率': '建蔽率', '容積率': '容积率', '間取': '户型', '向き': '朝向',
    '築年月': '建成时间', '完成時期': '竣工时期', '引渡時期': '交付时间', '管理形態': '管理形态'
  };

  /* —— 有/无 类（0/1）—— */
  var FIELD_BOOL = { 'has_photo': 1, 'has_floorplan': 1, 'has_map': 1 };

  /* —— 值翻译：REINS 后台枚举 → 客户看得懂的说法 ——
     取值来自真实库全量统计（2026-09-16 实枚举），不靠猜。 */
  /* v1.7.4：REINS 固定文本值（与字段名无关，见到即译）。
     真实库实测：修繕積立金里有 25 条「確認中」、4 条「なし」，
     不译会被当成"没翻译的机器值"——这正是勇哥 9/16 截图反馈的那类问题。 */
  var TEXT_CN = {
    '確認中': '确认中', 'なし': '无', '未定': '未定', '不明': '不明',
    '有': '有', '無': '无', '－': '', '-': ''
  };

  var ENUM_CN = {
    'trade_type': {
      '専任': '专任媒介', '専属': '专属专任媒介', '一般': '一般媒介（多家代理）',
      '代理': '代理（卖方代理）', '売主': '业主直售', '準専任': '准专任媒介',
      'オーナーチェンジ': '业主变更（带租约出售）', '専任媒介': '专任媒介'
    },
    'use_zone': {
      '商業': '商业地区', '一住': '一类居住地区', '二住': '二类居住地区',
      '一中': '一类中高层居住专用地区', '二中': '二类中高层居住专用地区',
      '一低': '一类低层居住专用地区', '二低': '二类低层居住专用地区',
      '近商': '邻近商业地区', '準工': '准工业地区', '工業': '工业地区',
      '工専': '工业专用地区', '準住': '准居住地区', '田園住': '田园居住地区',
      '定めなし': '无指定', '無指定': '无指定',
      '第一種低層住居専用地域': '一类低层居住专用地区',
      '第一種中高層住居専用地域': '一类中高层居住专用地区',
      '第一種住居地域': '一类居住地区', '第二種住居地域': '二类居住地区',
      '近隣商業地域': '邻近商业地区', '商業地域': '商业地区',
      '準工業地域': '准工业地区', '工業地域': '工业地区'
    },
    'public_status': {
      '公開中': '公开中', '申込あり': '已有申请', '一時停止': '暂停招募',
      '取引完了': '已成交', '売却済': '已售出'
    }
  };

  /* 千分位 */
  function _sep(n) {
    var s = String(n);
    return s.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  /**
   * 原始键 → 当前语言的字段名。
   * 优先级：i18n 四语言（xf.*）→ 中文兜底 → **空串**（未收录＝不显示）。
   * 未收录时不再返回原始键：后台机器语言绝不上屏，同时在控制台告警，
   * 并计入 window.FIELD_UNKNOWN，由 QA 脚本核对。
   */
  function fieldLabel(k) {
    if (k == null || k === '') return '';
    if (FIELD_SKIP[k]) return '';
    var key = FIELD_KEY[k];
    if (key && typeof window.t === 'function') {
      var v = window.t(key);
      if (v && v !== key) return v;
    }
    if (FIELD_CN[k]) return FIELD_CN[k];
    if (!window.FIELD_UNKNOWN) window.FIELD_UNKNOWN = [];
    if (window.FIELD_UNKNOWN.indexOf(k) < 0) {
      window.FIELD_UNKNOWN.push(k);
      if (window.console && console.warn) {
        console.warn('[fieldmap] 未收录的字段键，已隐藏不显示，请补进词典：' + k);
      }
    }
    return '';
  }

  /** 该键是否应跳过（内部键 / 未收录键 / 空标签） */
  function fieldSkip(k) {
    if (k == null || k === '') return true;
    if (String(k).charAt(0) === '_') return true;
    return fieldLabel(k) === '';
  }

  /** 原始值 → 当前语言的展示值（有/无、円、枚举中文…） */
  function fieldValue(k, v) {
    if (v == null || v === '' || v === '-' || v === '－') return '';
    if (typeof v === 'object') { try { v = JSON.stringify(v); } catch (e) { return ''; } }
    var s = String(v).trim();
    if (s === '' || s === '-') return '';
    if (FIELD_BOOL[k]) return (s === '0' || s === 'false' || s === 'None') ? _t('xf.no', '无') : _t('xf.yes', '有');
    if ((k === 'unit_price_sqm' || k === 'unit_price_tsubo') && !isNaN(s) && s !== '') {
      // v1.7.3 口径统一（勇哥定：「房子都是万円为单位」）：>=1万 走万円(一位小数)，
      // 小值才走 円（避免 5632 被显示成「0.6 万円/㎡」这种没信息量的写法）。
      // 与详情页 Python 路径 app._detail_pairs 的 unit() 保持一致，两页不再打架。
      var n = Number(s), per = (k === 'unit_price_sqm') ? '㎡' : '坪';
      if (n <= 0) return '';
      if (n < 10000) return _sep(n) + ' 円/' + per;
      var wan = (n / 10000).toFixed(1).split('.');
      wan[0] = _sep(wan[0]);
      return wan.join('.') + ' 万円/' + per;
    }
    if ((k === 'management_fee' || k === 'repair_fund') && !isNaN(s) && s !== '') return _sep(s) + ' 円';
    if ((k === 'price' || k === 'previous_price') && !isNaN(s) && s !== '') return _sep(Math.floor(Number(s) / 10000)) + ' 万円';
    var en = ENUM_CN[k];
    if (en) {
      // 多行枚举（如「売主\nオーナーチェンジ」）逐段翻译后拼接，避免漏译
      var parts = s.split(/[\n\/]/).map(function (x) { return x.trim(); }).filter(Boolean);
      var out = parts.map(function (x) { return en[x] || x; });
      return out.join(' · ');
    }
    // v1.7.4：REINS 的固定文本值 —— 与字段名无关，见到就翻中文。
    // 例：修繕積立金是「確認中」（25 条）/「なし」（4 条），不翻会当成没翻译的机器值。
    if (TEXT_CN[s]) return TEXT_CN[s];
    return s;
  }

  function _t(key, dflt) {
    if (typeof window.t === 'function') { var v = window.t(key); if (v && v !== key) return v; }
    return dflt;
  }

  /* v1.7.4 R3：判断一个字段是否属「中介信息」（客户版对比表要一键隐藏）。
     与 app.py::_is_agency 保持同一套正则 —— 之前这个规则在 search.html、
     app.py 各写了一份，加对比页就是第三份；收口到这里，改一处三页同步。 */
  var AGENCY_RE = /取扱|担当|仲介|電話|メール|取引/;
  function fieldAgency(k) {
    if (k === 'broker' || k === 'broker_tel') return true;
    return AGENCY_RE.test(k || '');
  }

  window.fieldLabel = fieldLabel;
  window.fieldSkip = fieldSkip;
  window.fieldValue = fieldValue;
  window.fieldAgency = fieldAgency;
  /* 兼容：compare.html 历史上把这个函数叫 zh() */
  if (typeof window.zh !== 'function') window.zh = fieldLabel;

  /* ============================================================
     v1.9.0：物件番号旁边的「REINS 物件番号検索」按钮 —— 全站统一实现
     ------------------------------------------------------------
     勇哥要求：把**所有显示物件番号的位置**旁边都挂上这个按钮，点一下
     就进 REINS 検索页并把当前番号填进搜索栏。
     位置清单（改这里就等于改全站）：
       ① 查询页 列表行「次要属性」里的番号          search.html renderItem
       ② 查询页 点开行的「物件番号」格              search.html fillDetail
       ③ 详情页 头部「物件番号 …」                  detail.html
       ④ 详情页「详细信息」表里的 物件番号 行        detail.html
       ⑤ 对比页 字段行「物件番号」                  compare.html
     实现方式：统一的 HTML 生成器 + **事件委托**（document 捕获阶段）。
     不写 inline onclick —— 既避免把外部数据（番号）拼进 JS 字符串
     （旧 detail.html 就是 `onclick="openBukkenSearch('{{…}}')"`），
     也顺手拦掉「点按钮把整行详情也展开」的连带动作。
     ============================================================ */
  function htmlEscape(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  window.bukkenBtn = function (no) {
    if (!no) return '';
    var label = (typeof window.t === 'function') ? window.t('detail.reins_search') : 'REINS 物件番号検索';
    return '<a class="qbtn qbtn-reins" href="javascript:void(0)" data-bukken="'
         + htmlEscape(no) + '" title="' + htmlEscape(label) + '">' + htmlEscape(label) + '</a>';
  };
  document.addEventListener('click', function (e) {
    var el = e.target && e.target.closest && e.target.closest('[data-bukken]');
    if (!el) return;
    e.preventDefault();
    e.stopPropagation();          // 别再触发外层的 toggleDetail（捕获阶段拦下）
    window.openBukkenSearch(el.getAttribute('data-bukken'));
  }, true);

  /* v1.9.1：物件番号検索改为后端登录式 —— 由后端用已登录 REINS 会话代填番号+点検索+截图回传，
     解决纯前端跨域无法代填（真机反馈：番号没录入/没点検索/未登录）。
     后端 selectors 来自 config.yaml（门禁②，待真机取证）。 */
  window.openBukkenSearch = function (no) {
    if (!no) return;
    window.bukkenSearchRun(String(no));
  };

  window.bukkenSearchRun = function (no) {
    if (window.__bukkenSearching) return;
    window.__bukkenSearching = true;
    if (window.bukkenToast) window.bukkenToast(window.t('detail.searching') || 'REINS 検索中…', no, false);
    fetch('/api/reins/bukken_search', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({no: no})
    }).then(function (r) { return r.json(); }).then(function (j) {
      window.__bukkenSearching = false;
      if (j && j.ok && j.image_url) {
        window.bukkenShowResult(no, j.image_url, j.reins_url || '');
      } else {
        window.bukkenFallback(no, j && j.error ? j.error : '');
      }
    }).catch(function (e) {
      window.__bukkenSearching = false;
      window.bukkenFallback(no, String(e));
    });
  };

  window.bukkenShowResult = function (no, img, reinsUrl) {
    var ov = document.getElementById('bukkenModal');
    if (!ov) {
      ov = document.createElement('div');
      ov.id = 'bukkenModal';
      ov.style.cssText = 'position:fixed;left:0;top:0;right:0;bottom:0;background:rgba(0,0,0,.55);'
        + 'z-index:9999;display:none;align-items:center;justify-content:center;';
      ov.innerHTML = '<div style="background:#fff;max-width:92vw;max-height:88vh;overflow:auto;'
        + 'border-radius:10px;padding:14px;position:relative;">'
        + '<button id="bukkenClose" style="position:absolute;right:8px;top:6px;cursor:pointer;">✕</button>'
        + '<div id="bukkenBody"></div></div>';
      document.body.appendChild(ov);
      ov.addEventListener('click', function (e) {
        if (e.target === ov || e.target.id === 'bukkenClose') ov.style.display = 'none';
      });
    }
    var body = ov.querySelector('#bukkenBody');
    body.innerHTML = '<div style="font-weight:700;margin-bottom:8px;">REINS 物件番号検索：'
      + window.htmlEscape(no) + '</div>'
      + '<img src="' + img + '?t=' + Date.now() + '" style="max-width:100%;border:1px solid #ddd;"/>'
      + '<div style="margin-top:10px;">'
      + (reinsUrl ? '<a href="' + reinsUrl + '" target="_blank" rel="noopener" class="qbtn">在 REINS 打开</a> ' : '')
      + '<button id="bukkenCopy" class="qbtn">复制番号</button></div>';
    ov.style.display = 'flex';
    var cp = ov.querySelector('#bukkenCopy');
    if (cp) cp.onclick = function () {
      try { navigator.clipboard.writeText(no); if (window.bukkenToast) window.bukkenToast(window.t('detail.toast_copied') || '', no, true); } catch (e) {}
    };
  };

  window.bukkenFallback = function (no, err) {
    // 后端不可用/未配置/未登录 → 退化：剪贴板+开番号検索页+提示
    var base = (window.REINS_BUKKEN_SEARCH_URL || 'https://system.reins.jp/main/BK/GBK004100');
    try { if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(String(no)); } catch (e) {}
    window.open(base, '_blank', 'noopener');
    if (window.bukkenToast) window.bukkenToast((window.t('detail.toast_copied') || '已复制番号') + (err ? '（后端:' + err + '）' : ''), no, true);
  };

  /* 轻提示（右下角浮出 2.6 秒）—— 只用于番号検索这一处，不进 i18n 字典之外的地方 */
  window.bukkenToast = function (msg, no, copied) {
    if (!msg) return;
    try {
      var d = document.createElement('div');
      d.className = 'qtoast';
      d.textContent = msg.replace('{no}', no) + (copied ? '' : '');
      document.body.appendChild(d);
      setTimeout(function () { d.classList.add('out'); }, 2200);
      setTimeout(function () { d.remove(); }, 2700);
    } catch (e) {}
  };
  window.toggleAgency = function (btn) {
    var on = document.body.classList.toggle('hide-agency');
    if (btn && window.t) {
      btn.textContent = on ? window.t('search.other_hide_off') : window.t('search.other_detail');
    }
  };
  /* v1.7.1：整页级「显示/隐藏其他详情」开关 —— 全页共用一个按钮（id=agency-toggle），
     不再放在单个房产卡片里；点一下切换 body.hide-agency，CSS 一次隐藏所有 [data-agency]。
     这里在加载时把按钮文案对齐当前状态，避免刷新后文实不符。 */
  window.initAgencyToggle = function () {
    var b = document.getElementById('agency-toggle');
    if (!b || !window.t) return;
    b.textContent = document.body.classList.contains('hide-agency')
      ? window.t('search.other_hide_off') : window.t('search.other_detail');
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', window.initAgencyToggle);
  } else {
    window.initAgencyToggle();
  }
  /* 隐藏中介信息：一键隐藏后把带 data-agency 的字段藏起来（search/detail/compare 通用） */
  (function () {
    var s = document.createElement('style');
    s.textContent = '.hide-agency [data-agency]{display:none!important}'
      + ' .qbtn{margin-left:6px;font-size:12px;color:#1b5faa;cursor:pointer;text-decoration:underline;background:none;border:none;padding:0}'
      // v1.9.0：物件番号旁的 REINS 検索按钮（小一号，跟在番号后面不抢眼）
      + ' .qbtn-reins{margin-left:6px;font-size:11.5px;color:#1b5faa;cursor:pointer;'
      + 'text-decoration:underline;white-space:nowrap}'
      + ' .qbtn-reins:hover{color:#0b3f7d}'
      // v1.9.0：番号検索的轻提示
      + ' .qtoast{position:fixed;right:16px;bottom:96px;z-index:200;max-width:320px;'
      + 'background:#1b5faa;color:#fff;border-radius:8px;padding:9px 13px;font-size:12.5px;'
      + 'box-shadow:0 6px 20px rgba(0,0,0,.25);opacity:1;transition:opacity .45s ease}'
      + ' .qtoast.out{opacity:0}'
      + ' .qtag-reg{background:#e6f4ea;color:#1b7f3a} .qtag-chg{background:#e8f0fe;color:#1a56c4}';
    document.head.appendChild(s);
  })();
})();
