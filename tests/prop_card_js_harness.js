// -*- coding: utf-8 -*-
/* tests/prop_card_js_harness.js
 * 真·JS 回归：用 Node 实跑 web/static/prop_card.js 的 PropCard.full，
 * 断言「中介公司 + 电话」在员工收藏卡片（与查询页同构的渲染链）上正确渲染，
 * 且 broker 为空时不渲染 broker 块。
 *
 * 这是「保证原有信息后续不再丢」的关键门禁之一：员工卡片渲染链若被重构时
 * 漏掉 broker，本测试立即红灯。与 Python 侧 test_detail_pairs.py 的
 * _detail_pairs / _row_payload 断言、以及 search.html 母本 grep 构成三链一致性门禁。
 *
 * 用法：node prop_card_js_harness.js <path/to/prop_card.js>
 * 退出码：0 = 全过；1 = 有断言失败；2 = 用法错误。
 */
'use strict';
const fs = require('fs');
const PROP_JS = process.argv[2];
if (!PROP_JS) { console.error('用法: node prop_card_js_harness.js <prop_card.js>'); process.exit(2); }
const src = fs.readFileSync(PROP_JS, 'utf8');

function makeWindow() {
  const win = {};
  // 极简 i18n 桩：原样返回 key（broker 断言不依赖翻译，只校验结构/数据）
  win.t = function (k) { return k; };
  win.tf = function (k) { return k; };
  win.wan = function (v) { return (v == null ? '' : v + ' 円'); };
  win.warekiToAD = function (d) { return d; };
  win.console = console;
  win.navigator = { clipboard: null };
  win.addEventListener = function () {};
  win.open = function () {};
  // window.bukkenBtn 不桩 → full() 走 '' 分支
  return win;
}
function makeDocument() {
  return {
    readyState: 'complete',
    body: { classList: { contains: function () { return false; }, toggle: function () { return false; } } },
    head: { appendChild: function () {} },
    getElementById: function () { return null; },
    createElement: function () { return { textContent: '' }; },
    addEventListener: function () {},
  };
}

const window = makeWindow();
const document = makeDocument();
// pdfGroupHtml 是浏览器全局（来自 web/static/pdfbox.js，PRD#39 多选 PDF 控件组），
// 本 harness 只需它返回一个占位串即可（broker 断言不依赖它）。
const SHIM = 'function pdfGroupHtml(r){return "";}\n';
const fn = new Function('window', 'document', 'console', 'navigator', SHIM + src + '\n;return window;');
const w = fn(window, document, console, window.navigator);

let fails = 0;
function assert(name, cond, detail) {
  if (!cond) { fails++; console.error('FAIL ' + name + (detail ? ' :: ' + detail : '')); }
  else { console.log('PASS ' + name); }
}

const NO = '300140894939';
const COMPANY = '（株）ＥＳ＆Ｃｏｍｐａｎｙ';
const TEL = '0666849636';

// 1) broker 有值：必须渲染 .broker[data-agency]，且含公司名 + ☎ 电话
const rowWith = {
  property_no: NO, price: '165000000', kind: '中古マンション',
  exclusive_area: '69.91', layout: '1LDK', line_station: '西梅田', ward: '北区',
  broker: COMPANY, broker_tel: TEL, last_seen_at: '2026-09-28 08:08:16',
};
const htmlWith = w.PropCard.full(rowWith, { id: NO, expandable: false, onCompare: true });
assert('broker 块存在(data-agency)', htmlWith.indexOf('class="broker" data-agency') >= 0, htmlWith.slice(-200));
assert('broker 含公司名', htmlWith.indexOf(COMPANY) >= 0);
assert('broker 含 ☎ 电话', htmlWith.indexOf('☎ ' + TEL) >= 0);

// 2) broker 为空：不得渲染 broker 块（避免空框上屏）
const rowEmpty = Object.assign({}, rowWith, { broker: '', broker_tel: '' });
const htmlEmpty = w.PropCard.full(rowEmpty, { id: NO, expandable: false, onCompare: true });
assert('broker 空时不渲染 broker 块', htmlEmpty.indexOf('class="broker"') < 0);

// 3) broker 有公司无电话：仍渲染公司名、不渲染 ☎
const rowNoTel = Object.assign({}, rowWith, { broker_tel: '' });
const htmlNoTel = w.PropCard.full(rowNoTel, { id: NO, expandable: false, onCompare: true });
assert('broker 有公司无电话仍渲染公司', htmlNoTel.indexOf(COMPANY) >= 0 && htmlNoTel.indexOf('☎') < 0);

process.exit(fails ? 1 : 0);
