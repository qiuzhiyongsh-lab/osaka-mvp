/* 大阪房源 · 本地版 —— 前端通用脚本
 *
 * 解决什么：
 *   浏览器原生报错是 "TypeError: Failed to fetch"，看着像代码 bug，
 *   实际上 99% 是**本地服务已经退出**（黑窗口被关 / 进程被杀）导致连接被拒。
 *   这里把它翻译成"人话 + 下一步怎么做"，并自动探测服务是否还活着。
 *
 * 提供：
 *   api(url, opts)        统一请求：网络失败 → ServerDownError；HTTP 错误 → ApiError
 *   postJSON(url, body)   POST JSON 快捷方式
 *   errText(e)            把异常转成给用户看的文案
 *   alertErr(e, label)    弹窗展示
 */
(function () {
  'use strict';

  var PING_URL = '/api/ping';
  var PING_INTERVAL = 4000;

  // ---------- 错误类型 ----------
  function ServerDownError() {
    this.name = 'ServerDownError';
    this.message = '本地服务没有响应';
  }
  ServerDownError.prototype = Object.create(Error.prototype);

  function ApiError(message, status, payload) {
    this.name = 'ApiError';
    this.message = message || ('HTTP ' + status);
    this.status = status;
    this.payload = payload || {};
  }
  ApiError.prototype = Object.create(Error.prototype);

  // ---------- 离线横幅 ----------
  var bannerEl = null, offline = false;

  function ensureBanner() {
    if (bannerEl && document.body.contains(bannerEl)) return bannerEl;

    // 横幅里的 <code> 不能被站点的全局 code 样式（浅底深字）盖住，否则看不清
    if (!document.getElementById('offline-banner-style')) {
      var st = document.createElement('style');
      st.id = 'offline-banner-style';
      st.textContent =
        '#offline-banner code{background:rgba(255,255,255,.22);color:#fff;' +
        'border:0;border-radius:3px;padding:1px 5px;font-size:13px;' +
        'font-family:ui-monospace,Consolas,monospace;}' +
        '#offline-banner b{font-weight:700;}';
      document.head.appendChild(st);
    }

    bannerEl = document.createElement('div');
    bannerEl.id = 'offline-banner';
    bannerEl.style.cssText =
      'display:none;position:fixed;left:0;right:0;top:0;z-index:99999;' +
      'background:#b3261e;color:#fff;' +
      'font:14px/1.65 system-ui,-apple-system,"Microsoft YaHei",sans-serif;' +
      'padding:10px 16px;box-shadow:0 2px 12px rgba(0,0,0,.28)';
    bannerEl.innerHTML =
      '<b>⚠ 本地服务已停止</b> —— 浏览器连不上 <code>127.0.0.1:8765</code>，' +
      '所以点按钮没反应（不是你操作错了）。<br>' +
      '处理办法：确认启动服务的那个黑色窗口还在；若已关闭，' +
      '双击项目里的 <b>start_mvp.bat</b> 重启，然后按 F5 刷新本页。' +
      '<span style="opacity:.85;margin-left:6px">（服务恢复后本提示会自动消失）</span>';
    document.body.appendChild(bannerEl);
    return bannerEl;
  }

  function setOffline(on) {
    if (on === offline) return;
    offline = on;
    var el = ensureBanner();
    el.style.display = on ? 'block' : 'none';
    document.body.style.paddingTop = on ? '62px' : '';
  }

  function ping() {
    fetch(PING_URL, { method: 'GET', cache: 'no-store' })
      .then(function (r) { setOffline(!r.ok); })
      .catch(function () { setOffline(true); });
  }

  // ---------- 统一请求 ----------
  async function api(url, opts) {
    opts = opts || {};
    var res;
    try {
      res = await fetch(url, opts);
    } catch (e) {
      // 网络层失败 = 服务不在。绝不把它当成"接口报错"。
      setOffline(true);
      throw new ServerDownError();
    }
    setOffline(false);

    var payload = null;
    var text = await res.text();
    if (text) {
      try { payload = JSON.parse(text); } catch (e) { payload = { raw: text }; }
    }

    if (!res.ok) {
      var msg = (payload && payload.message) || (res.status + ' ' + res.statusText);
      throw new ApiError(msg, res.status, payload);
    }
    return payload || {};
  }

  function postJSON(url, body) {
    return api(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    });
  }

  // ---------- 文案 ----------
  function errText(e) {
    if (e instanceof ServerDownError) {
      return '本地服务已停止，连不上 127.0.0.1:8765。\n\n' +
             '请双击项目里的 start_mvp.bat 重新启动服务，再刷新本页（F5）。';
    }
    if (e instanceof ApiError) return '接口返回错误：' + e.message;
    return '请求失败：' + (e && e.message ? e.message : e);
  }

  function alertErr(e, label) {
    alert((label ? label + '：\n\n' : '') + errText(e));
    if (e instanceof ServerDownError) ensureBanner();
  }

  // ==========================================================
  // 对比栏（跨页面共享）
  //   查询页把「加入对比」的房子写进浏览器本地（localStorage），
  //   对比页与顶部导航徽标都从这里读。最多 MAX 套。
  // ==========================================================
  var CMP_KEY = 'osaka.compare.v1';
  var CMP_MAX = 6;
  var cmpListeners = [];

  function cmpRead() {
    try {
      var v = JSON.parse(localStorage.getItem(CMP_KEY) || '[]');
      if (!Array.isArray(v)) return [];
      return v.filter(function (x) { return x && x.no; }).slice(0, CMP_MAX);
    } catch (e) { return []; }
  }

  function cmpWrite(list) {
    try { localStorage.setItem(CMP_KEY, JSON.stringify(list.slice(0, CMP_MAX))); }
    catch (e) { /* 隐私模式下写不了就算了，不影响本次会话 */ }
    cmpNotify();
  }

  function cmpNotify() {
    var n = cmpRead().length;
    document.querySelectorAll('#nav-cmp-n, .js-cmp-n').forEach(function (el) {
      el.textContent = n ? n : '';
      el.style.display = n ? 'inline-block' : 'none';
    });
    cmpListeners.forEach(function (fn) { try { fn(cmpRead()); } catch (e) {} });
  }

  // 别的标签页改了对比栏时，本页也同步（localStorage 的 storage 事件不会在本页触发）
  window.addEventListener('storage', function (e) {
    if (e.key === CMP_KEY) cmpNotify();
  });

  var CompareBox = {    MAX: CMP_MAX,
    items: cmpRead,
    nos: function () { return cmpRead().map(function (x) { return x.no; }); },
    count: function () { return cmpRead().length; },
    has: function (no) { return cmpRead().some(function (x) { return x.no === no; }); },
    /** 加入一套房。返回 {ok:true} 或 {ok:false, reason:'full'|'dup'} */
    add: function (item) {
      var list = cmpRead();
      if (list.some(function (x) { return x.no === item.no; })) return { ok: false, reason: 'dup' };
      if (list.length >= CMP_MAX) return { ok: false, reason: 'full' };
      list.push({ no: item.no, addr: item.addr || '', name: item.name || '',
                  sub: item.sub || '', ward: item.ward || '', area: item.area || '',
                  price: item.price == null ? null : item.price, pdf: !!item.pdf });
      cmpWrite(list);
      return { ok: true };
    },
    /** 补齐/刷新对比栏里某套房的摘要（地址/楼名/間取り·面積·駅/价格）。
        老版本存进 localStorage 的条目缺 sub 字段，进查询页时用它补齐，槽位才不会少一行。
        不改其它字段、不占新名额，返回是否真的有变化。 */
    update: function (no, fields) {
      var list = cmpRead(), hit = false;
      list.forEach(function (x) {
        if (x.no !== no) return;
        ['addr', 'name', 'sub', 'ward', 'area', 'price', 'pdf'].forEach(function (k) {
          var v = fields[k];
          if (v == null || v === '') return;
          if (typeof v === 'number' && isNaN(v)) return;
          if (x[k] !== v) { x[k] = v; hit = true; }
        });
      });
      if (hit) cmpWrite(list);
      return hit;
    },
    remove: function (no) {
      cmpWrite(cmpRead().filter(function (x) { return x.no !== no; }));
    },
    clear: function () { cmpWrite([]); },
    onChange: function (fn) { cmpListeners.push(fn); fn(cmpRead()); },
    refresh: cmpNotify
  };

  // ---------- 启动心跳 ----------
  function boot() {
    if (!document.getElementById('offline-banner')) ensureBanner();
    ping();
    setInterval(ping, PING_INTERVAL);
    cmpNotify();                       // 顶部导航上的「已选 N 套」角标
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  window.api = api;
  window.postJSON = postJSON;
  window.errText = errText;
  window.alertErr = alertErr;
  window.ServerDownError = ServerDownError;
  window.isServerOffline = function () { return offline; };
  window.CompareBox = CompareBox;
  window.wan = function (v) {                 // 円 → 「1,234 万円」显示
    return (v == null || v === '') ? '—'
      : (Math.round(Number(v) / 10000)).toLocaleString() + ' 万円';
  };
  // 降价比例 = 降价額 / 修改前価格（分母用「修改前」金额）。无降价返回 ''。
  window.dropRate = function (prev, cur) {
    var p = Number(prev), c = Number(cur);
    if (!isFinite(p) || !isFinite(c) || p <= 0 || c >= p) return '';
    return ((p - c) / p * 100).toFixed(1) + '%';
  };
  // 和暦（令和/平成/昭和 年月日，可能带全角空格）→ 'YYYY-MM-DD'；非和暦原样返回。
  // 用于把 REINS「変更年月日」转公元年展示（v1.2.5）。
  window.warekiToAD = function (s) {
    if (!s) return '';
    var m = String(s).replace(/\s+/g, '').match(/(令和|平成|昭和)(\d+)年(?:(\d+)月)?(?:(\d+)日)?/);
    if (!m) return String(s);
    var base = { '令和': 2018, '平成': 1988, '昭和': 1925 }[m[1]];
    var out = String(base + parseInt(m[2], 10));
    if (m[3]) out += '-' + ('0' + m[3]).slice(-2);
    if (m[4]) out += '-' + ('0' + m[4]).slice(-2);
    return out;
  };
  window.escHtml = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  };
  window.esc = window.escHtml;                // 页面内联脚本习惯用短名
})();
