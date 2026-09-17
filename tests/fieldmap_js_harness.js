// -*- coding: utf-8 -*-
/* tests/fieldmap_js_harness.js
 * 真·JS 回归：用 Node 实跑 web/static/fieldmap.js 的 fieldLabel / fieldValue / fieldSkip。
 * 不依赖浏览器，只桩最小 window/document/console/navigator。
 * 用法：node fieldmap_js_harness.js <path/to/fieldmap.js>
 * 退出码：0 = 全过；1 = 有断言失败。
 */
'use strict';
const fs = require('fs');
const FIELD_JS = process.argv[2];
if (!FIELD_JS) { console.error('用法: node fieldmap_js_harness.js <fieldmap.js>'); process.exit(2); }
const src = fs.readFileSync(FIELD_JS, 'utf8');

function makeWindow() {
  const win = {};
  // 极简 i18n 桩：返回原始 key → 强制 fieldLabel 落到 FIELD_CN 兜底（测中文兜底是否齐全）
  win.t = function (k) { return k; };
  win.console = console;
  win.navigator = { clipboard: null };
  win.FIELD_UNKNOWN = [];
  win.addEventListener = function () {};
  win.open = function () {};
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
const fn = new Function('window', 'document', 'console', 'navigator', src + '\n;return window;');
const w = fn(window, document, console, window.navigator);

let fails = 0;
function assert(name, got, want) {
  const ok = got === want;
  if (!ok) { fails++; console.error('FAIL ' + name + ': got=' + JSON.stringify(got) + ' want=' + JSON.stringify(want)); }
  else { console.log('PASS ' + name); }
}

// 1) 规则反转：未收录机器键 → 空串（绝不原样上屏）
assert('fieldLabel(unknown) === 空', w.fieldLabel('xyz_unknown_machine_key'), '');
assert('FIELD_UNKNOWN 记录 unknown', w.FIELD_UNKNOWN.indexOf('xyz_unknown_machine_key') >= 0, true);

// 2) 此前直接上屏的 6 个机器键，现在有中文标签（走 FIELD_CN 兜底）
assert("fieldLabel('ward')", w.fieldLabel('ward'), '所在区');
assert("fieldLabel('unit_price_sqm')", w.fieldLabel('unit_price_sqm'), '㎡单价');
assert("fieldLabel('unit_price_tsubo')", w.fieldLabel('unit_price_tsubo'), '坪单价');
assert("fieldLabel('has_photo')", w.fieldLabel('has_photo'), '照片');
assert("fieldLabel('has_floorplan')", w.fieldLabel('has_floorplan'), '户型图');
assert("fieldLabel('has_map')", w.fieldLabel('has_map'), '位置图');

// 3) 内部键 / 核心列一律跳过
assert("fieldSkip('pdf_url')", w.fieldSkip('pdf_url'), true);
assert("fieldSkip('pdf_path')", w.fieldSkip('pdf_path'), true);
assert("fieldSkip('_ms_pdf')", w.fieldSkip('_ms_pdf'), true);
assert("fieldSkip('absent_runs')", w.fieldSkip('absent_runs'), true);
assert("fieldSkip('property_no')", w.fieldSkip('property_no'), true);

// 4) 值本地化：有/无、円/㎡（小值千分位）、円/坪、大额只做千分位（查询页路径）
assert("fieldValue('has_floorplan','0')", w.fieldValue('has_floorplan', '0'), '无');
assert("fieldValue('has_map','1')", w.fieldValue('has_map', '1'), '有');
assert("fieldValue('has_photo','false')", w.fieldValue('has_photo', 'false'), '无');
// v1.7.2 bug 修复：5632 不能显示成「0 万円/㎡」
assert("fieldValue('unit_price_sqm','5632')", w.fieldValue('unit_price_sqm', '5632'), '5,632 円/㎡');
// v1.7.3 口径：>=1万 走万円（勇哥定「房子都是万円为单位」），小值走円
assert("fieldValue('unit_price_sqm','120000')", w.fieldValue('unit_price_sqm', '120000'), '12.0 万円/㎡');
assert("fieldValue('unit_price_tsubo','80000')", w.fieldValue('unit_price_tsubo', '80000'), '8.0 万円/坪');
assert("fieldValue('unit_price_sqm','3130000')", w.fieldValue('unit_price_sqm', '3130000'), '313.0 万円/㎡');
assert("fieldValue('management_fee','12000')", w.fieldValue('management_fee', '12000'), '12,000 円');

// 5) 空值/占位符 → 空串（不渲染「-」）
assert("fieldValue('has_photo','-')", w.fieldValue('has_photo', '-'), '');
assert("fieldValue('has_map','')", w.fieldValue('has_map', ''), '');

// 6) 枚举中文（多行逐段翻译）
assert("fieldValue('trade_type','専任')", w.fieldValue('trade_type', '専任'), '专任媒介');
assert("fieldValue('use_zone','近商')", w.fieldValue('use_zone', '近商'), '邻近商业地区');
assert("fieldValue('public_status','公開中')", w.fieldValue('public_status', '公開中'), '公开中');

process.exit(fails ? 1 : 0);
