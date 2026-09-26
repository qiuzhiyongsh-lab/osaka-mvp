/* 收藏（v2.0.x · 勇哥 2026-09-26 拍板）：全站通用「心形」收藏按钮。
   ==========================================================================
   用法：页面任意位置放 <span class="favslot" data-fav="物件番号"></span>
        本脚本自动渲染成心形按钮；点击即收藏/取消收藏。

   设计要点：
   ① **心形**（勇哥选，与方形「加入对比」按钮区分明显）
   ② 状态来自 /api/emp/fav/nos（一次拉取，避免列表 50 条各发一次请求）
   ③ 未登录 / 拉取失败 → 按「未收藏」渲染，点击时由服务端返回 401 提示，不静默
   ④ MutationObserver：查询页列表是 JS 动态渲染的，新节点出现即自动补渲染
      （改 class/innerHTML 会再次触发 observer，但状态一致时不再改动 → 自然收敛）
*/
(function () {
  var NOS = null;        // 收藏：null=未加载；对象=已加载（{番号:1}）
  var CUSTNOS = null;    // 已加过客户的房源（勇哥要求：「添加客户」按钮要有已选态）
  var loading = false;
  var timer = null;

  var HEART = '<svg viewBox="0 0 24 24" fill="__F__" stroke="currentColor" stroke-width="1.8">'
    + '<path d="M12 20.7l-1.4-1.3C5.4 14.7 2 11.6 2 7.9 2 5 4.2 2.8 7 2.8c1.7 0 3.3.8 4.3 2.1'
    + ' 1-1.3 2.6-2.1 4.3-2.1 2.8 0 5 2.2 5 5.1 0 3.7-3.4 6.8-8.6 11.5L12 20.7z"/></svg>';

  function heart(filled) { return HEART.replace('__F__', filled ? 'currentColor' : 'none'); }

  function injectStyle() {
    if (document.getElementById('favbtn-style')) return;
    var css = ''
      + '.favslot{display:inline-flex;vertical-align:middle}'
      + '.favbtn{display:inline-flex;align-items:center;gap:4px;font-size:12px;padding:3px 10px;'
      + 'border-radius:6px;border:1px solid #d4dcea;background:#fff;color:#6b7c99;cursor:pointer;'
      + 'white-space:nowrap;line-height:1.6}'
      + '.favbtn:hover{border-color:#e05c6a;color:#c0392b}'
      + '.favbtn.on{border-color:#e05c6a;background:#fdeef0;color:#c0392b}'
      + '.favbtn svg{width:13px;height:13px}'
      + '.custslot{display:inline-flex;vertical-align:middle}'
      + '.custbtn{display:inline-flex;align-items:center;gap:4px;font-size:12px;padding:3px 10px;'
      + 'border-radius:6px;border:1px solid #d4dcea;background:#fff;color:#3b6fd4;cursor:pointer;'
      + 'white-space:nowrap;line-height:1.6}'
      + '.custbtn:hover{border-color:#3b6fd4;background:#eef4ff}'
      /* 勇哥 2026-09-26：已加过客户的房源要用另一种显示方式，一眼可辨 */
      + '.custbtn.on{border-color:#2f9e6f;background:#eafaf2;color:#1f7a52}'
      + '.custbtn.on:hover{border-color:#1f7a52;background:#dcf5e9}';
    var s = document.createElement('style');
    s.id = 'favbtn-style';
    s.appendChild(document.createTextNode(css));
    document.head.appendChild(s);
  }

  function load(cb) {
    if (NOS && CUSTNOS) { cb && cb(); return; }
    if (loading) { return; }
    loading = true;
    /* 一次并行拉两个集合：① 我收藏的番号 ② 我加过客户的番号（后者决定按钮选中态） */
    var p1 = fetch('/api/emp/fav/nos', { credentials: 'same-origin' })
      .then(function (r) { return r.json().catch(function () { return { ok: false }; }); });
    var p2 = fetch('/api/emp/cust/nos', { credentials: 'same-origin' })
      .then(function (r) { return r.json().catch(function () { return { ok: false }; }); });
    Promise.all([p1, p2]).then(function (rs) {
      loading = false;
      var j = rs[0] || {}, jc = rs[1] || {};
      NOS = {};
      if (j.ok && j.nos) { j.nos.forEach(function (n) { NOS[n] = 1; }); }
      CUSTNOS = {};
      if (jc.ok && jc.nos) { jc.nos.forEach(function (n) { CUSTNOS[n] = 1; }); }
      renderAll();
      cb && cb();
    }).catch(function () { loading = false; NOS = {}; CUSTNOS = {}; renderAll(); });
  }

  function isFav(no) { return !!(NOS && NOS[no]); }

  function html(no) {
    var on = isFav(no);
    return '<span class="favbtn' + (on ? ' on' : '') + '" data-favbtn="' + no + '"'
      + ' onclick="Fav.toggle(\'' + no + '\')"'
      + ' title="' + (on ? '已收藏（点击取消）' : '收藏这套房') + '">'
      + heart(on) + (on ? '已收藏' : '收藏') + '</span>';
  }

  /* 是否已把该房源关联到客户（≥1 个我的客户即算） */
  function isCust(no) { return !!(CUSTNOS && CUSTNOS[no]); }

  /* 「添加客户」按钮两态：未加 = 蓝字；已加 = **绿底绿框「已添加客户」**（勇哥要求一眼可辨） */
  function custHtml(no) {
    var on = isCust(no);
    /* v1.9.95 C10/R43：已关联 ⇒ 点开「已关联客户」列表（勇哥：要一眼看出关联了谁，
       而不是再开一次添加框）；未关联 ⇒ 仍是打开添加选择器。查询页 / 详情页同源，自动同步。 */
    return '<span class="custbtn' + (on ? ' on' : '') + '" data-custbtn="' + no + '"'
      /* ⚠ 防呆：若 showCustList 缺失/版本没更新到，回退到添加框 —— 保证「点了必有反应」 */
      + ' onclick="' + (on
            ? '(window.Fav&&Fav.showCustList?Fav.showCustList:Fav.openCustPicker)'
            : 'Fav.openCustPicker') + '(\'' + no + '\')"'
      + ' title="' + (on ? '查看这套房已关联的客户' : '把这个房源关联到客户') + '">'
      + (on ? '已添加客户' : '添加客户') + '</span>';
  }

  function renderAll() {
    injectStyle();
    var slots = document.querySelectorAll('.favslot');
    for (var i = 0; i < slots.length; i++) {
      var s = slots[i];
      var no = s.getAttribute('data-fav') || '';
      if (!no) continue;
      var cur = s.querySelector('[data-favbtn]');
      if (cur && cur.getAttribute('data-favbtn') === no) continue;  // 已渲染
      s.innerHTML = html(no);
    }
    /* 「添加客户」按钮（勇哥：在收藏右侧；2026-09-26 追加「已添加」态，一眼可辨） */
    var cslots = document.querySelectorAll('.custslot');
    for (var j = 0; j < cslots.length; j++) {
      var cs = cslots[j];
      var cno = cs.getAttribute('data-cust') || '';
      if (!cno) continue;
      var want = custHtml(cno);
      if (cs.getAttribute('data-custdone') === cno && cs.innerHTML === want) continue;  // 无变化，跳过
      cs.innerHTML = want;
      cs.setAttribute('data-custdone', cno);
    }
    var btns = document.querySelectorAll('[data-favbtn]');
    for (var k = 0; k < btns.length; k++) {
      var b = btns[k];
      var no2 = b.getAttribute('data-favbtn') || '';
      var on = isFav(no2);
      var has = b.className.indexOf('on') >= 0;
      if (has !== on) {
        b.className = 'favbtn' + (on ? ' on' : '');
        b.innerHTML = heart(on) + (on ? '已收藏' : '收藏');
        b.setAttribute('title', on ? '已收藏（点击取消）' : '收藏这套房');
      }
    }
  }

  function toggle(no) {
    fetch('/api/emp/fav/toggle', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ property_no: no })
    })
      .then(function (r) { return r.json().catch(function () { return { ok: false, error: 'bad json' }; }); })
      .then(function (j) {
        if (j && j.ok) {
          if (!NOS) NOS = {};
          if (j.added) { NOS[no] = 1; } else { delete NOS[no]; }
          renderAll();
          toast(j.added ? '已收藏' : '已取消收藏');
          /* 勇哥 C-1 + Q5：收藏时就地打标签 —— **仅首次弹**（该房源还没有标签时），
             已打过标签的不打断动线。 */
          if (j.added) { maybeFirstTags(no); }
        } else if (j && j.error) {
          alert('⚠ ' + j.error);
        }
      });
  }

  function toast(text) {
    var el = document.getElementById('favToast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'favToast';
      el.style.cssText = 'position:absolute;z-index:9999;padding:6px 14px;border-radius:8px;'
        + 'background:#22314f;color:#fff;font-size:13px;opacity:.95;pointer-events:none';
      document.body.appendChild(el);
    }
    el.textContent = text;
    var r = (document.querySelector('.qitem') || document.body).getBoundingClientRect();
    el.style.left = (window.scrollX + Math.max(12, r.left)) + 'px';
    el.style.top = (window.scrollY + 12) + 'px';
    el.style.display = 'block';
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { el.style.display = 'none'; }, 1600);
  }

  function schedule() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(renderAll, 60);
  }

  /* ---------- 收藏时就地打标签（勇哥 C-1 / Q5）----------
     只在「该房源还没有任何标签」时自动弹一次；其余时候不打断动线。
     选择器可多选、可当场新建并勾选、可跳过。 */
  function escHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function maybeFirstTags(no) {
    fetch('/api/emp/tags/of?property_no=' + encodeURIComponent(no), { credentials: 'same-origin' })
      .then(function (r) { return r.json().catch(function () { return { items: [] }; }); })
      .then(function (oj) {
        var have = (oj && oj.items) || [];
        if (!have.length) { openTagPicker(no); }
      })
      .catch(function () { /* 查不到就不弹，不打断 */ });
  }

  function openTagPicker(no, preselect) {
    if (document.getElementById('favTagModal')) return;
    fetch('/api/emp/tags?category=fav', { credentials: 'same-origin' })
      .then(function (r) { return r.json().catch(function () { return { items: [] }; }); })
      .then(function (tj) {
        return fetch('/api/emp/tags/of?property_no=' + encodeURIComponent(no), { credentials: 'same-origin' })
          .then(function (r) { return r.json().catch(function () { return { items: [] }; }); })
          .then(function (oj) { return { all: tj.items || [], mine: oj.items || [] }; });
      })
      .then(function (d) { renderTagPicker(no, d.all, d.mine, preselect); });
  }

  function renderTagPicker(no, all, mine, preselect) {
    var sel = {};
    (mine || []).forEach(function (t) { sel[t.id] = 1; });
    (preselect || []).forEach(function (id) { sel[id] = 1; });
    var box = document.createElement('div');
    box.id = 'favTagModal';
    box.style.cssText = 'position:absolute;z-index:10000;background:#fff;border:1px solid #d4dcea;'
      + 'border-radius:12px;padding:16px;max-width:440px;box-shadow:0 4px 18px rgba(30,50,90,.14)';
    var h = '<div style="font-size:14px;font-weight:700">给这个收藏打标签</div>'
      + '<div style="font-size:12px;color:#7a8aa5;margin:4px 0 10px">可直接多选，也能当场新建</div>'
      + '<div id="ftpChips" style="display:flex;flex-wrap:wrap;gap:8px">';
    all.forEach(function (t) {
      var on = !!sel[t.id];
      h += '<span class="ftpchip" data-id="' + t.id + '" style="font-size:12px;padding:5px 11px;'
        + 'border-radius:999px;cursor:pointer;border:1px solid ' + (on ? '#3b6fd4' : '#dfe6f2') + ';'
        + 'background:' + (on ? '#e8f0fe' : '#fff') + ';color:' + (on ? '#2a54a8' : '#33425f') + '">'
        + escHtml(t.name) + '</span>';
    });
    if (!all.length) { h += '<span style="font-size:12px;color:#9aa8bf">还没有收藏类标签，可在下方新建</span>'; }
    h += '</div>'
      + '<div style="display:flex;gap:8px;margin-top:12px">'
      + '<input id="ftpNew" placeholder="新建标签，如：投资客推荐" style="flex:1;padding:6px 10px;'
      + 'border:1px solid #cfd8e8;border-radius:7px;font-size:13px">'
      + '<button id="ftpNewBtn" style="font-size:12px;padding:6px 12px;white-space:nowrap">+ 新建并勾选</button>'
      + '</div>'
      + '<div style="display:flex;align-items:center;gap:10px;margin-top:14px">'
      + '<button id="ftpSave" style="font-size:13px;padding:6px 16px;border:1px solid #3b6fd4;'
      + 'background:#e8f0fe;color:#2a54a8;border-radius:8px">完成</button>'
      + '<span id="ftpSkip" style="font-size:12px;color:#7a8aa5;cursor:pointer">跳过</span></div>';
    box.innerHTML = h;
    document.body.appendChild(box);
    var r = (document.querySelector('.qitem') || document.body).getBoundingClientRect();
    box.style.left = (window.scrollX + Math.max(12, r.left)) + 'px';
    box.style.top = (window.scrollY + 90) + 'px';

    /* 勇哥 2026-09-26：标签弹窗**始终保留**，但 **5 秒无操作自动消失**
       —— 愿意点就继续操作（鼠标/键盘一动就重新计时），不管它则自动关掉，不打扰。 */
    var autoT = null;
    function close() {
      if (autoT) { clearTimeout(autoT); autoT = null; }
      if (box && box.parentNode) { box.parentNode.removeChild(box); }
    }
    function stay() {
      if (autoT) { clearTimeout(autoT); }
      autoT = setTimeout(close, 5000);
    }
    autoT = setTimeout(close, 5000);
    box.addEventListener('mousemove', stay);
    box.addEventListener('click', stay);
    box.addEventListener('keydown', stay);

    var chips = box.querySelectorAll('.ftpchip');
    for (var i = 0; i < chips.length; i++) {
      chips[i].onclick = function () {
        var id = parseInt(this.getAttribute('data-id'), 10);
        if (sel[id]) {
          delete sel[id];
          this.style.borderColor = '#dfe6f2'; this.style.background = '#fff'; this.style.color = '#33425f';
        } else {
          sel[id] = 1;
          this.style.borderColor = '#3b6fd4'; this.style.background = '#e8f0fe'; this.style.color = '#2a54a8';
        }
      };
    }
    document.getElementById('ftpNewBtn').onclick = function () {
      var v = (document.getElementById('ftpNew').value || '').trim();
      if (!v) return;
      fetch('/api/emp/tags', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: v, category: 'fav' })
      }).then(function (r) { return r.json(); }).then(function (j) {
        if (j && j.ok) {
          close();
          openTagPicker(no, Object.keys(sel).map(Number).concat([j.id]));  /* 重绘并带上新建项 */
        }
      });
    };
    document.getElementById('ftpSave').onclick = function () { saveTags(no, sel); close(); };
    document.getElementById('ftpSkip').onclick = close;
  }

  function saveTags(no, sel) {
    var want = Object.keys(sel).map(Number);
    fetch('/api/emp/tags/of?property_no=' + encodeURIComponent(no), { credentials: 'same-origin' })
      .then(function (r) { return r.json().catch(function () { return { items: [] }; }); })
      .then(function (oj) {
        var have = ((oj && oj.items) || []).map(function (t) { return t.id; });
        var jobs = [];
        have.forEach(function (id) { if (want.indexOf(id) < 0) { jobs.push({ property_no: no, tag_id: id, unbind: true }); } });
        want.forEach(function (id) { if (have.indexOf(id) < 0) { jobs.push({ property_no: no, tag_id: id }); } });
        return Promise.all(jobs.map(function (b) {
          return fetch('/api/emp/tags/bind', {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b)
          });
        }));
      })
      .then(function () {
        toast('标签已保存');
        /* 勇哥反馈「打完标签这里没刷新」→ 广播事件，让收藏列表 / 标签管理 / 客户卡片即时刷新 */
        try {
          document.dispatchEvent(new CustomEvent('fav-tags-changed', { detail: { no: no } }));
        } catch (e) { /* 老浏览器无 CustomEvent 时忽略 */ }
      })
      .catch(function () { toast('标签保存失败'); });
  }

  /* ---------- v1.9.95 C10/R43：「已添加客户」→ 列出这套房已关联的客户 ----------
     勇哥：点「已添加客户」要能一眼看到「关联了哪些客户」，而不是只弹添加框。
     浮层内仍保留「继续添加客户」入口；查询页 / 详情页同源（R48 两页同步）。 */
  function showCustList(no) {
    var old = document.getElementById('favCustModal');
    if (old && old.parentNode) { old.parentNode.removeChild(old); }
    var box = document.createElement('div');
    box.id = 'favCustModal';
    box.style.cssText = 'position:absolute;z-index:10000;background:#fff;border:1px solid #d4dcea;'
      + 'border-radius:12px;padding:16px;max-width:460px;box-shadow:0 4px 18px rgba(30,50,90,.14)';
    box.innerHTML = '<div style="font-size:14px;font-weight:700">已关联客户</div>'
      + '<div id="fcustLinked" style="margin:10px 0;max-height:300px;overflow:auto">'
      + '<div style="font-size:12px;color:#7a8aa5">加载中…</div></div>'
      + '<div style="display:flex;justify-content:space-between;align-items:center;margin-top:10px">'
      + '<span id="fcustAdd" style="font-size:12px;color:#3b6fd4;cursor:pointer">继续添加客户</span>'
      + '<span id="fcustClose" style="font-size:12px;color:#7a8aa5;cursor:pointer">关闭</span></div>';
    document.body.appendChild(box);
    var r0 = (document.querySelector('.qitem') || document.body).getBoundingClientRect();
    box.style.left = (window.scrollX + Math.max(12, Math.min(r0.left, 160))) + 'px';
    box.style.top = (window.scrollY + 90) + 'px';

    function close() { if (box.parentNode) { box.parentNode.removeChild(box); } }
    document.getElementById('fcustClose').onclick = close;
    document.getElementById('fcustAdd').onclick = function () { close(); openCustPicker(no); };

    fetch('/api/emp/customers/by-property?no=' + encodeURIComponent(no), { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        var host = document.getElementById('fcustLinked');
        if (!host) { return; }
        if (!j || !j.ok) { host.innerHTML = '<div style="font-size:12px;color:#c0392b">加载失败</div>'; return; }
        var items = j.items || [];
        if (!items.length) {
          host.innerHTML = '<div style="font-size:12px;color:#7a8aa5">暂无关联客户</div>';
          return;
        }
        host.innerHTML = items.map(function (c) {
          return '<div style="padding:7px 0;border-bottom:1px solid #eef1f6">'
            + '<b>' + escHtml(c.name || '') + '</b>'
            + (c.phone ? '　<span style="font-size:12px;color:#6b7c99">' + escHtml(c.phone) + '</span>' : '')
            + '<div style="font-size:11px;color:#9aa8bf;margin-top:2px">归属 '
            + escHtml(c.owner_username || '')
            + (c.bound_at ? '　关联于 ' + escHtml(String(c.bound_at).slice(0, 10)) : '')
            + '　<span class="fcust-unbind" data-cid="' + (c.id || 0) + '" data-name="'
            + escHtml(c.name || '') + '" style="color:#c0392b;cursor:pointer">取消关联</span>'
            + '</div></div>';
        }).join('');
        /* v1.9.96（勇哥 #9-②）：每个已关联客户行带「取消关联」——直接 DELETE 该客户与此房源的绑定。
           多客户场景下一行一行解，避免「一键全解」误伤。 */
        var ub = host.querySelectorAll('.fcust-unbind');
        for (var i = 0; i < ub.length; i++) {
          ub[i].onclick = function () {
            unbindCust(no, parseInt(this.getAttribute('data-cid'), 10), this.getAttribute('data-name'));
          };
        }
        /* 按钮选中态与服务端对齐：还有客户=绿「已添加客户」，全清=蓝「添加客户」 */
        if (items.length) { if (!CUSTNOS) { CUSTNOS = {}; } CUSTNOS[no] = 1; }
        else { if (CUSTNOS) { delete CUSTNOS[no]; } }
        renderAll();
      })
      .catch(function () {
        var host = document.getElementById('fcustLinked');
        if (host) { host.innerHTML = '<div style="font-size:12px;color:#c0392b">加载失败</div>'; }
      });
  }

  /* ---------- 添加客户（勇哥 2026-09-26：在「收藏」右侧的快捷入口）----------
     点开后：① 可搜索已有客户（名称 / 手机号 / 备注）并**关联本房源**；② 也可当场新建客户并关联。 */
  function openCustPicker(no) {
    /* v1.9.95 Fix D（勇哥 2026-09-27 实测「添加客户」偶发弹不出）：
       showCustList（绿色「已添加客户」浮层）与添加框共用 id #favCustModal。
       若上一层的 showCustList 浮层没走「关闭/继续添加」清除而残留在 DOM，
       这里原 `if(getElementById('favCustModal')) return;` 守卫会直接拦截 ⇒ 点蓝色
       「添加客户」永远没反应。改为：开前先清掉任何残留浮层，保证每次点击都重开。 */
    var stale = document.getElementById('favCustModal');
    if (stale && stale.parentNode) { stale.parentNode.removeChild(stale); }
    var box = document.createElement('div');
    box.id = 'favCustModal';
    box.style.cssText = 'position:absolute;z-index:10000;background:#fff;border:1px solid #d4dcea;'
      + 'border-radius:12px;padding:16px;max-width:460px;box-shadow:0 4px 18px rgba(30,50,90,.14)';
    box.innerHTML = '<div style="font-size:14px;font-weight:700">添加客户</div>'
      + '<div style="font-size:12px;color:#7a8aa5;margin:4px 0 10px">'
      + '搜索已有客户并关联本房源，或当场新建</div>'
      + '<div style="display:flex;gap:8px">'
      + '<input id="fcustQ" placeholder="搜索客户（名称 / 手机号）" style="flex:1;padding:6px 10px;'
      + 'border:1px solid #cfd8e8;border-radius:7px;font-size:13px">'
      + '<button id="fcustNewBtn" style="font-size:12px;padding:6px 12px;white-space:nowrap">'
      + '+ 新建并关联</button></div>'
      + '<div id="fcustList" style="margin-top:10px;max-height:260px;overflow:auto"></div>'
      + '<div style="display:flex;justify-content:flex-end;margin-top:12px">'
      + '<span id="fcustClose" style="font-size:12px;color:#7a8aa5;cursor:pointer">关闭</span></div>';
    document.body.appendChild(box);
    var r = (document.querySelector('.qitem') || document.body).getBoundingClientRect();
    box.style.left = (window.scrollX + Math.max(12, Math.min(r.left, 160))) + 'px';
    box.style.top = (window.scrollY + 90) + 'px';

    function close() { if (box.parentNode) { box.parentNode.removeChild(box); } }
    function closeAndKeep(list) { var m = document.getElementById('favCustModal'); if (m && m.parentNode) { m.parentNode.removeChild(m); } }

    document.getElementById('fcustClose').onclick = close;
    var qEl = document.getElementById('fcustQ');
    var tmr = null;
    function doSearch() { custSearch(no, qEl.value || ''); }
    qEl.oninput = function () { clearTimeout(tmr); tmr = setTimeout(doSearch, 250); };
    qEl.onkeydown = function (e) { if (e.key === 'Enter') { clearTimeout(tmr); doSearch(); } };
    document.getElementById('fcustNewBtn').onclick = function () {
      var name = (qEl.value || '').trim();
      if (!name) { qEl.focus(); return; }
      fetch('/api/emp/customers', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name })
      }).then(function (r) { return r.json(); }).then(function (j) {
        if (j && j.ok) { bindCust(no, j.id, name); closeAndKeep(); }
        else { alert('⚠ ' + ((j && j.error) || '新建失败')); }
      });
    };
    custSearch(no, '');
  }

  function custSearch(no, q) {
    var list = document.getElementById('fcustList');
    if (!list) return;
    list.innerHTML = '<div style="font-size:12px;color:#9aa8bf">搜索中…</div>';
    fetch('/api/emp/customers?q=' + encodeURIComponent(q), { credentials: 'same-origin' })
      .then(function (r) { return r.json().catch(function () { return { items: [] }; }); })
      .then(function (j) {
        var items = (j && j.items) || [];
        var el = document.getElementById('fcustList');
        if (!el) return;
        if (!items.length) {
          el.innerHTML = '<div style="font-size:12px;color:#9aa8bf">没有匹配的客户'
            + '（可在上方输入名称后点「新建并关联」）</div>';
          return;
        }
        el.innerHTML = items.map(function (c) {
          /* 勇哥：这里要把客户**已有标签**全部显示出来 */
          var tags = (c.tags || []).map(function (t) { return escHtml(t.name); }).join('、');
          return '<div class="fcustrow" data-id="' + c.id + '" data-name="' + escHtml(c.name) + '"'
            + ' style="padding:8px 10px;border:1px solid #eef1f6;border-radius:8px;margin-bottom:6px;cursor:pointer">'
            + '<div style="font-size:13px;font-weight:500;color:#22314f">' + escHtml(c.name) + '</div>'
            + '<div style="font-size:11px;color:#7a8aa5">'
            + escHtml([c.phone || '', c.note || ''].filter(Boolean).join(' ｜ ')) + '</div>'
            + (tags ? '<div style="font-size:11px;color:#2a54a8;margin-top:2px">已有标签：'
                + tags + '</div>' : '')
            + '</div>';
        }).join('');
        var rows = el.querySelectorAll('.fcustrow');
        for (var i = 0; i < rows.length; i++) {
          rows[i].onclick = function () {
            var m = document.getElementById('favCustModal');
            bindCust(no, parseInt(this.getAttribute('data-id'), 10), this.getAttribute('data-name'));
            if (m && m.parentNode) { m.parentNode.removeChild(m); }
          };
        }
      });
  }

  function bindCust(no, cid, name) {
    fetch('/api/emp/customers/' + cid + '/bind', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ property_no: no })
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (j && j.ok) {
        /* 勇哥：关联成功后**立刻**把按钮切成「已添加客户」态 */
        if (!CUSTNOS) { CUSTNOS = {}; }
        CUSTNOS[no] = 1;
        renderAll();
        toast('已关联到客户「' + (name || '') + '」');
      } else { alert('⚠ ' + ((j && j.error) || '关联失败')); }
    });
  }

  /* v1.9.96（勇哥 #9-②）：取消「客户 ⇄ 房源」关联。 */
  function unbindCust(no, cid, name) {
    if (!confirm('确定取消「' + (name || '') + '」与这套房的关联？')) return;
    fetch('/api/emp/customers/' + cid + '/bind', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ property_no: no, unbind: true })
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (j && j.ok) { toast('已取消关联「' + (name || '') + '」'); showCustList(no); }
      else { alert('⚠ ' + ((j && j.error) || '取消失败')); }
    }).catch(function () { alert('⚠ 网络错误，取消失败'); });
  }

  window.Fav = { load: load, renderAll: renderAll, toggle: toggle, isFav: isFav, html: html,
                 openTagPicker: openTagPicker, openCustPicker: openCustPicker,
                 showCustList: showCustList };   /* v1.9.95 C10 */

  document.addEventListener('DOMContentLoaded', function () {
    load();
    if (window.MutationObserver) {
      new MutationObserver(schedule).observe(document.body || document.documentElement,
        { childList: true, subtree: true });
    }
  });
})();
