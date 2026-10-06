/* ============================================================
 * v1.9.114 · PRD#39「多选 PDF 合并下载」—— 前端共用模块
 *
 * 设计要点（勇哥 2026-10-06 拍板）：
 *   · 与「对比栏」**双独立选择集**：PdfBox 管 PDF 合并、CompareBox 管对比，互不干扰。
 *   · ☑ 与「PDF下载」**紧邻成组**（pdfgrp）：☑ 在左、PDF下载 在右；PDF下载=单套直下，
 *     ☑=加入合并选择集（FR18）。
 *   · 选择集**跨页 / 跨查询条件 / 跨刷新保留**（localStorage）—— 改条件不清空，用顶部提示条告知。
 *   · 清空 / 退出**免确认**（FR20）。
 *   · 全选本页 = **三态**，且**只选有 PDF 的行**（无 PDF 的行 ☑ 置灰，PRD Q10）。
 *   · 合并动作走 `POST /api/pdf/merge` → 后端合并（本机读盘 / 线上服务端拉 COS，**无 CORS 问题**）。
 * ============================================================ */
(function () {
  "use strict";
  var KEY = "osaka_pdf_sel_v1";
  var SOFT = 50;
  var cbs = [];

  function _load() {
    try {
      var raw = localStorage.getItem(KEY);
      var arr = raw ? JSON.parse(raw) : [];
      return Array.isArray(arr) ? arr.filter(function (x) { return x; }) : [];
    } catch (e) { return []; }
  }
  function _save(arr) {
    try { localStorage.setItem(KEY, JSON.stringify(arr)); } catch (e) {}
  }

  // 作用域：emp 页有多个 .emp-panel（收藏/客户/标签），「全选本页」只应作用于当前激活面板；
  // 查询页/对比页无 .emp-panel → 回退全文档（行为不变）。
  function _pdfScope() {
    var ap = document.querySelector(".emp-panel.on");
    return ap || document;
  }

  var PdfBox = {
    items: function () { return _load(); },
    has: function (no) { return _load().indexOf(no) >= 0; },
    count: function () { return _load().length; },
    add: function (no) {
      if (!no) return;
      var a = _load();
      if (a.indexOf(no) < 0) { a.push(no); _save(a); this._emit(); }
    },
    remove: function (no) {
      var a = _load();
      var i = a.indexOf(no);
      if (i >= 0) { a.splice(i, 1); _save(a); this._emit(); }
    },
    toggle: function (no) {
      if (this.has(no)) this.remove(no); else this.add(no);
    },
    clear: function () { _save([]); this._emit(); },
    onChange: function (fn) { if (typeof fn === "function") cbs.push(fn); },
    _emit: function () { var its = this.items(); cbs.forEach(function (fn) { try { fn(its); } catch (e) {} }); }
  };

  // ---- 行内控件组：☑ + PDF下载（勇哥：两控件紧邻成组）----
  // r 是一行已 _row_payload 整理过的数据（含 r.pdf_url）。
  window.pdfGroupHtml = function (r) {
    var no = r.no || r.property_no || "";
    var hasPdf = !!r.pdf_url;
    var chk = '<input type="checkbox" class="pdfchk" data-no="' + esc(no) + '"' +
      (PdfBox.has(no) ? " checked" : "") + (hasPdf ? "" : " disabled") +
      ' onclick="togglePdf(this,event)"' +
      ' title="' + (hasPdf ? esc(t("pdf.tip_add")) : esc(t("pdf.tip_nopdf"))) + '">';
    var btn;
    if (hasPdf) {
      btn = '<a class="pdfbtn" data-no="' + esc(no) + '" href="' + esc(r.pdf_url) + '"' +
        ' target="_blank" rel="noopener" onclick="event.stopPropagation()">' + esc(t("pdf.dl")) + "</a>";
    } else {
      btn = '<span class="pdfbtn disabled" title="' + esc(t("pdf.tip_nopdf")) + '">' + esc(t("pdf.dl")) + "</span>";
    }
    return '<span class="pdfgrp">' + chk + btn + "</span>";
  };

  // ☑ 勾选 → 切换合并选择集（不冒泡到卡片展开）
  window.togglePdf = function (el, ev) {
    if (ev) ev.stopPropagation();
    var no = el.getAttribute("data-no");
    if (!no || el.disabled) return;
    if (el.checked) PdfBox.add(no); else PdfBox.remove(no);
    refreshPdfBar();
    refreshPdfSelAll();
  };

  // 渲染完成后，把选择集回写到行内 ☑（跨页/跨刷新保留）
  window.markPdfRows = function () {
    var boxes = document.querySelectorAll(".pdfchk");
    boxes.forEach(function (b) {
      var no = b.getAttribute("data-no");
      b.checked = PdfBox.has(no);
    });
  };

  // ---- 顶部绿色托盘（选中后浮出）----
  function refreshPdfBar() {
    var bar = document.getElementById("pdfbar");
    if (!bar) return;
    var items = PdfBox.items();
    var n = items.length;
    bar.classList.toggle("show", n > 0);
    document.body.classList.toggle("has-pdfbar", n > 0);
    var sel = document.getElementById("pdfsel");
    if (sel) sel.textContent = n;
    var note = document.getElementById("pdfnote");
    if (note) {
      if (n > SOFT) note.textContent = t("pdf.oversoft", { n: n });
      else note.textContent = t("pdf.persist");
    }
  }
  window.refreshPdfBar = refreshPdfBar;

  // 全选本页：三态，只选「有 PDF」的行
  window.refreshPdfSelAll = function () {
    var sa = document.getElementById("pdfselall");
    if (!sa) return;
    var boxes = Array.prototype.slice.call(_pdfScope().querySelectorAll(".pdfchk"));
    var enabled = boxes.filter(function (b) { return !b.disabled; });
    var on = enabled.filter(function (b) { return b.checked; }).length;
    sa.disabled = enabled.length === 0;
    sa.indeterminate = (on > 0 && on < enabled.length);
    sa.checked = (enabled.length > 0 && on === enabled.length);
  };

  function initBarHandlers() {
    var merge = document.getElementById("pdfmerge");
    if (merge && !merge._bound) {
      merge._bound = true;
      merge.addEventListener("click", function () { pdfDoMerge(); });
    }
    var clr = document.getElementById("pdfclear");
    if (clr && !clr._bound) {
      clr._bound = true;
      clr.addEventListener("click", function () {
        PdfBox.clear();           // 免确认清空（FR20）
        markPdfRows(); refreshPdfBar(); refreshPdfSelAll();
      });
    }
    var sa = document.getElementById("pdfselall");
    if (sa && !sa._bound) {
      sa._bound = true;
      sa.addEventListener("change", function () {
        var turnOn = this.checked;
        _pdfScope().querySelectorAll(".pdfchk").forEach(function (b) {
          if (b.disabled) return;
          var no = b.getAttribute("data-no");
          b.checked = turnOn;
          if (turnOn) PdfBox.add(no); else PdfBox.remove(no);
        });
        refreshPdfBar(); refreshPdfSelAll();
      });
    }
  }

  function _parseFilename(cd) {
    if (!cd) return null;
    var m = cd.match(/filename\*=UTF-8''([^;]+)/i);
    if (m) { try { return decodeURIComponent(m[1]); } catch (e) {} }
    m = cd.match(/filename="?([^";]+)"?/i);
    return m ? m[1] : null;
  }

  // 合并下载：POST nos → 后端合成 PDF → 浏览器触发下载
  window.pdfDoMerge = function () {
    var nos = PdfBox.items();
    if (!nos.length) return;
    var btn = document.getElementById("pdfmerge");
    var prev = btn ? btn.textContent : "";
    if (btn) { btn.disabled = true; btn.textContent = t("pdf.merging"); }
    fetch("/api/pdf/merge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nos: nos })
    })
      .then(function (res) {
        if (!res.ok) {
          return res.json().then(function (j) { throw new Error(j.msg || ("HTTP " + res.status)); });
        }
        var fn = _parseFilename(res.headers.get("Content-Disposition")) || "bukken_merged.pdf";
        return res.blob().then(function (blob) { return { blob: blob, fn: fn }; });
      })
      .then(function (o) {
        var url = URL.createObjectURL(o.blob);
        var a = document.createElement("a");
        a.href = url; a.download = o.fn;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
      })
      .catch(function (e) {
        alert((e && e.message) || t("pdf.fail"));
      })
      .finally(function () {
        if (btn) { btn.disabled = false; btn.textContent = prev; applyI18n(); }
      });
  };

  // 启动：绑定事件 + 首次刷新（保留上一会话的选择集）
  document.addEventListener("DOMContentLoaded", function () {
    initBarHandlers();
    refreshPdfBar();
    PdfBox.onChange(function () { refreshPdfBar(); refreshPdfSelAll(); });
  });

  window.PdfBox = PdfBox;
})();
