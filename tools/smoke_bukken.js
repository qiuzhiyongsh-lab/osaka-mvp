// 轻量前端冒烟：stub window/document/fetch，验证 fieldmap.js 番号検索三路径不抛错、弹窗真挂 DOM。
const fs = require('fs');
const path = require('path');

const _byId = {};
function El(tag) {
  const el = {
    tagName: tag, style: {}, _children: [], _html: '',
    setAttribute() {}, appendChild(c) { this._children.push(c); return c; },
    remove() {}, select() {}, setSelectionRange() {},
    addEventListener() {}, textContent: '', onclick: null,
    classList: { add() {}, toggle() { return false; }, contains() { return false; } },
    querySelector(sel) { if (!this._qs) this._qs = {}; if (!this._qs[sel]) this._qs[sel] = El('stub'); return this._qs[sel]; },
    removeAttribute() {},
  };
  Object.defineProperty(el, 'innerHTML', { set(v) { this._html = v; }, get() { return this._html; } });
  Object.defineProperty(el, 'id', {
    set(v) { this._id = v; if (v) _byId[v] = this; },
    get() { return this._id; },
  });
  return el;
}
global.document = {
  createElement: (t) => El(t),
  getElementById: (id) => _byId[id] || null,
  addEventListener: () => {},
  body: El('body'),
  head: El('head'),
  execCommand: () => true,
};
global.navigator = { clipboard: { writeText: () => {} } };
global.window = global;
global.window.t = (k) => k;
// 浏览器里顶层 function htmlEscape 会挂到 window；eval 作用域不会，故桩里补一份（仅转义，够用）。
global.window.htmlEscape = (v) => String(v == null ? '' : v)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
global.window.OSAKA_PUBLIC = false;
global.window.REINS_BUKKEN_SEARCH_URL = 'https://system.reins.jp/main/BK/GBK004100';
global.fetch = () => Promise.reject(new Error('offline-smoke'));  // 本地路径走 catch → bukkenFail

const src = fs.readFileSync(path.join(__dirname, '..', 'web/static/fieldmap.js'), 'utf-8');
try {
  eval(src);
} catch (e) {
  console.log('LOAD_THROW:', e.message);
  process.exit(2);
}

function assert(c, m) { if (!c) { console.log('FAIL:', m); process.exit(1); } console.log('ok:', m); }

// ① 失败弹窗：bukkenFail 应挂上 #bukkenModal 且番号进 innerHTML
global.window.bukkenFail(['300140791556', '100140789119'], 'REINS 返回 0 件', null);
const modal = _byId['bukkenModal'];
assert(modal, '#bukkenModal 已创建');
assert(/300140791556/.test(modal.querySelector('#bukkenBody').innerHTML), '弹窗含番号');
assert(/REINS 返回 0 件/.test(modal.querySelector('#bukkenBody').innerHTML), '弹窗含错误信息');

// ② 线上站：bukkenSearchRun(PUBLIC) → bukkenOpenDirect（同步开窗，不抛）
let opened = null;
global.window.open = (u) => { opened = u; return {}; };
global.window.OSAKA_PUBLIC = true;
try { global.window.bukkenSearchRun(['100140779224']); } catch (e) { console.log('FAIL 线上开窗抛错:', e.message); process.exit(1); }
assert(opened === 'https://system.reins.jp/main/BK/GBK004100', '线上站同步打开 REINS 検索页');

// ③ 本地站：bukkenSearchRun(false) → fetch 失败 → catch → bukkenFail（不抛）
global.window.OSAKA_PUBLIC = false;
try { global.window.bukkenSearchRun(['300140791556']); } catch (e) { console.log('FAIL 本地异步抛错:', e.message); process.exit(1); }
assert(true, '本地站 fetch 失败路径未同步抛错（catch→bukkenFail）');

console.log('SMOKE_PASS');
