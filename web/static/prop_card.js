/* 房源卡片渲染（公共）· v2.0.x
   ==========================================================================
   勇哥 2026-09-26：「员工业务里的收藏内容，要和房源查询里完全一致 —— 直接调用房源查询的
   信息」。此前 emp.html 另写了一套卡片（字段/布局都不同），导致两边显示差别很大。

   本文件把「查询页 renderFullItem」的结构，连同它依赖的辅助函数（num / mediaBadges /
   qtHtml / unitPriceInline）集中到这里，供 emp.html 的收藏列表 / 标签下钻复用。

   数据来源：后端 `employee_api._prop_cards()` → 复用 `web/app.py::_row_payload()`
   （查询页唯一的行→前端形状转换出口）→ **字段与查询页同源**。

   ⚠ 维护点：查询页 `search.html::renderFullItem` 是这条渲染链的「母本」，
     若其结构/字段变化，需同步本文件（已记入项目 MEMORY 的「两条渲染链」条）。
*/
(function () {
  function esc(s) { return (window.esc ? window.esc(s) : String(s == null ? '' : s)); }
  function t(k) { return (window.t ? window.t(k) : k); }
  function tf(k, o) { return (window.tf ? window.tf(k, o) : (window.t ? window.t(k) : k)); }
  function wan(v) { return (window.wan ? window.wan(v) : (v == null ? '' : v + ' 円')); }
  function num(v) { return (v == null || v === '') ? '—' : v; }
  function wareki(d) { return (window.warekiToAD ? window.warekiToAD(d) : d); }

  /* 媒体小徽章（画 / 図 / 所 + 张数）—— 与查询页同款 */
  function mediaBadges(r) {
    var m = r.media || {};
    function b(cls, label, on, title) {
      return '<span class="mdb ' + cls + (on ? ' on' : ' off') + '" title="' + esc(title) + '">'
        + label + '</span>';
    }
    var hp = t(m.photo ? 'media.photo_yes' : 'media.no');
    if (m.photo && m.count != null) hp = tf('media.photo_n', { n: num(m.count) });
    var h = '<span class="mdbset">'
      + b('ga', '画', m.photo, hp)
      + b('zu', '図', m.plan, t(m.plan ? 'media.plan_yes' : 'media.no'))
      + b('sho', '所', m.map, t(m.map ? 'media.map_yes' : 'media.no'))
      + '</span>';
    if (m.count != null) h += '<span>' + tf('row.imgs', { n: num(m.count) }) + '</span>';
    return h;
  }

  /* 位置展示（地址可点谷歌地图；地址缺失用楼名兜底；都无则不渲染）—— 与查询页同款 */
  function qtHtml(r) {
    function mapSpan(v) {
      return '<span class="qt-gmap" data-gmap="' + esc(v) + '"'
        + ' title="' + esc(t('detail.gmap_search')) + '：' + esc(v) + '">'
        + esc(v) + '</span>';
    }
    if (r.address) {
      var out = mapSpan(r.address);
      if (r.building_name) { out += ' · ' + esc(r.building_name); }
      return out;
    }
    if (r.building_name) { return mapSpan(r.building_name); }
    return '';
  }

  /* ㎡単価 / 坪単価（行内 span，并入核心指标行）—— 与查询页同款 */
  function unitPriceInline(r) {
    var sqm = (r.unit_price_sqm && Number(r.unit_price_sqm) > 0)
      ? (Number(r.unit_price_sqm) / 10000).toFixed(1) + ' 万円' : '';
    var tsubo = (r.unit_price_tsubo && Number(r.unit_price_tsubo) > 0)
      ? (Number(r.unit_price_tsubo) / 10000).toFixed(1) + ' 万円' : '';
    var h = '';
    if (sqm) { h += '<span class="k">' + t('search.unit_price_sqm') + ' ' + sqm + '</span>'; }
    if (tsubo) { h += '<span class="k">' + t('search.unit_price_tsubo') + ' ' + tsubo + '</span>'; }
    return h;
  }

  /* 详细卡片（= 查询页「详细视图」同构）
     opts.faved        : 是否已收藏（决定心形填充）
     opts.expandable   : 是否允许点击展开行内详情（员工业务收藏页 = false，勇哥要求不展开）
     opts.extraHtml    : 追加在价格行下方的自定义 HTML（员工业务用来放标签行）
     opts.compareLabel : 「加入对比」按钮文案（默认取 i18n） */
  function full(r, opts) {
    opts = opts || {};
    var no = r.property_no || '';
    var badge = '';
    if (window.CUR_DATE) {
      if (r.reg_date_iso === window.CUR_DATE) { badge = '<span class="qtag qtag-reg">' + t('row.tag_new') + '</span>'; }
      else if (r.chg_date_iso === window.CUR_DATE) { badge = '<span class="qtag qtag-chg">' + t('row.tag_chg') + '</span>'; }
    }
    var ttBadge = '';
    if (r.trade_type) {
      var isSeller = (r.trade_type.indexOf('売主') >= 0);
      ttBadge = '<span class="qtag qtag-tt' + (isSeller ? ' qtag-seller' : '') + '"'
        + ' title="' + esc(t('row.trade_type_tip')) + '：' + esc(r.trade_type) + '">'
        + esc(r.trade_type) + '</span>';
    }
    var kindline = (r.kind && r.property_subtype) ? (r.kind + '／' + r.property_subtype)
      : (r.kind || r.property_subtype || '');
    var drop = '', rate = '';
    if (r.previous_price && r.price && r.price != r.previous_price) {
      var up = r.price > r.previous_price;
      var arrow = up ? '↑ ' : '↓ ';
      var diff = Math.abs(r.previous_price - r.price);
      var pct = (diff / Number(r.previous_price) * 100).toFixed(1) + '%';
      var src = '<span class="qsrc">' + t('row.src_platform')
        + (r.change_date ? ' · ' + wareki(r.change_date) : '') + '</span>';
      drop = '<span class="qdrop qdrop-platform">' + arrow + wan(diff) + ' ' + src + '</span>';
      rate = arrow + pct;
    }
    var cmpnm = r.building_name || r.property_subtype || r.kind || '';
    var cmparea = r.exclusive_area ? num(r.exclusive_area) + '㎡'
      : (r.land_area ? num(r.land_area) + '㎡'
        : (r.building_area ? num(r.building_area) + '㎡' : ''));
    var cmpsub = [r.layout, cmparea, r.line_station].filter(function (x) { return !!x; }).join(' · ');
    var pending = (r.has_detail === false);

    var headOpen = opts.expandable
      ? ' onclick="(window.toggleDetail?toggleDetail(\'' + opts.id + '\'):null)"' : '';

    return '<div class="qitem' + (pending ? ' nodetail' : '') + '" data-no="' + esc(no) + '"'
      + ' data-nm="' + esc(cmpnm) + '" data-sub="' + esc(cmpsub) + '"'
      + ' data-addr="' + esc(r.address || '') + '" data-ward="' + esc(r.ward || '') + '"'
      + ' data-area="' + esc(cmparea) + '">'
      + '<div class="qhead"' + headOpen + '>'
      + '<div class="qrow1">'
      + '<button class="qcmp" data-no="' + esc(no) + '" onclick="event.stopPropagation();'
      + (opts.onCompare ? 'window.PropCard._cmp(\'' + esc(no) + '\')' : 'void 0') + '">'
      + esc(opts.compareLabel || t('detail.tobank')) + '</button>'
      + '<span class="favslot" data-fav="' + esc(no) + '"></span>'
      + '<span class="custslot" data-cust="' + esc(no) + '"></span>'
      + badge + ttBadge
      + '<div class="qt">' + qtHtml(r) + '</div>'
      + '<div class="qprice">'
      + drop
      + (rate ? '<span class="qrate qrate-platform">' + rate + '</span>' : '')
      + '<span class="price">' + wan(r.price) + '</span>'
      + (r.pdf_url ? '<span class="qpdf">PDF</span>' : '')
      + '<a class="qbtn-detail" href="/p/' + encodeURIComponent(no) + '" target="_blank"'
      + ' onclick="event.stopPropagation()" title="' + esc(t('detail.srcpage')) + '">'
      + esc(t('detail.srcpage')) + '</a>'
      + '</div></div>'
      + '<div class="qcore">'
      + (r.exclusive_area ? '<span class="k">' + t('row.excl') + ' ' + num(r.exclusive_area) + ' ㎡</span>' : '')
      + (r.land_area ? '<span class="k">' + t('row.land') + ' ' + num(r.land_area) + ' ㎡</span>' : '')
      + (r.building_area ? '<span class="k">' + t('row.bldg') + ' ' + num(r.building_area) + ' ㎡</span>' : '')
      + (r.layout ? '<span class="k">' + esc(r.layout) + '</span>' : '')
      + (r.line_station ? '<span class="k">' + esc(r.line_station) + '</span>' : '')
      + (r.ward ? '<span class="k">' + esc(r.ward) + '</span>' : '')
      + unitPriceInline(r)
      + '</div>'
      + (opts.extraHtml || '')
      + '<div class="qsub">'
      + '<span class="mono">' + esc(no) + '</span>' + (window.bukkenBtn ? window.bukkenBtn(no) : '')
      + (kindline ? '<span>' + esc(kindline) + '</span>' : '')
      + (r.floor ? '<span>' + t('row.floor') + ' ' + esc(r.floor) + '</span>' : '')
      + (r.above_ground_floors ? '<span>' + t('row.floors') + ' ' + esc(r.above_ground_floors) + '</span>' : '')
      + (r.built_year_month ? '<span>' + t('row.built') + ' ' + esc(r.built_year_month) + '</span>' : '')
      + mediaBadges(r)
      + (r.last_seen_at ? '<span>' + t('row.fetched') + ' ' + esc(String(r.last_seen_at).slice(0, 10)) + '</span>' : '')
      + '</div>'
      + '</div>'
      + '</div>';
  }

  window.PropCard = { full: full, esc: esc, num: num, wan: wan, mediaBadges: mediaBadges,
                      qtHtml: qtHtml, unitPriceInline: unitPriceInline, _cmp: null };
})();
