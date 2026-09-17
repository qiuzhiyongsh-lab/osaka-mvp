'use strict';
// 确定性复现对比栏「清空 / 删到最后一个又全部恢复」竞态回归。
//   - 旧逻辑（v1.8.6 引入的 setAll，无竞态闸门）：页面加载后立刻清空/逐个删 → 栏被复活
//   - 新逻辑（v1.8.9 while 循环重比对 nos）：同样操作 → 栏保持空
// 用法：node tools/test_compare_restore.js
// 退出码 0 = 旧逻辑确为 BUG、新逻辑确已修复；1 = 不符预期。

function makeEnv(useNewLogic) {
  // ---- mock localStorage ----
  let store = {};
  const listeners = [];
  function cmpRead() { try { return JSON.parse(store.CMP || '[]'); } catch (e) { return []; } }
  function cmpWrite(list) {
    const next = JSON.stringify((list || []).slice(0, 6));
    if (store.CMP === next) return false;        // 值没变：不写不通知（掐断回环）
    store.CMP = next;
    listeners.forEach(fn => { try { fn(cmpRead()); } catch (e) {} });
    return true;
  }
  // ---- 共享状态 ----
  let locationSearch = '/compare';                // 模拟 URL 的 ?nos=
  let rows = [];
  let syncing = false, suppress = false, _cmpLoading = false;
  // ---- CompareBox（与 web/static/app.js 行为一致）----
  const CompareBox = {
    items: cmpRead,
    nos: () => cmpRead().map(x => x.no),
    count: () => cmpRead().length,
    has: no => cmpRead().some(x => x.no === no),
    add(item) {
      const l = cmpRead();
      if (l.some(x => x.no === item.no)) return { ok: false, reason: 'dup' };
      if (l.length >= 6) return { ok: false, reason: 'full' };
      l.push({ no: item.no, addr: '', name: '', sub: '', ward: '', area: '', price: null, pdf: false });
      cmpWrite(l); return { ok: true };
    },
    update() { return false; },
    remove(no) { cmpWrite(cmpRead().filter(x => x.no !== no)); },
    clear() { cmpWrite([]); },
    setAll(list) {
      cmpWrite((list || []).slice(0, 6).map(x => ({ no: x.no, addr: '', name: '', sub: '', ward: '', area: '', price: null, pdf: !!x.pdf })));
    },
    onChange(fn) { listeners.push(fn); fn(cmpRead()); },
    refresh: () => listeners.forEach(fn => { try { fn(cmpRead()); } catch (e) {} })
  };
  // ---- 取值 / 模拟 api ----
  function noOf() {
    const m = locationSearch.match(/[?&]nos=([^&]*)/);
    const list = (m && m[1]) ? decodeURIComponent(m[1]).split(',') : CompareBox.nos();
    const out = [];
    (list || []).forEach(x => { x = (x || '').trim(); if (x && out.indexOf(x) < 0) out.push(x); });
    return out.slice(0, 6);
  }
  function api(url) {
    const m = url.match(/nos=([^&]*)/);
    const nos = m ? decodeURIComponent(m[1]).split(',').filter(Boolean) : [];
    return new Promise(res => setTimeout(() => res({
      rows: nos.map(no => ({ property_no: no, address: 'addr-' + no, building_name: 'name-' + no, price: 100, pdf_url: null })),
      over: 0, missing: []
    }), 40));   // 模拟本地接口延迟
  }
  function t(k) { return k; }

  async function load() {
    if (_cmpLoading) return;
    _cmpLoading = true;
    try { await _loadBody(); } finally { _cmpLoading = false; }
  }
  async function _loadBody() {
    if (useNewLogic) {
      // v1.8.9：每次 await 回来重比对 nos，不一致就重跑（不写回）
      let first = true;
      while (true) {
        const nos = noOf();
        if (nos.length < 2) { rows = []; return; }
        if (first) first = false;
        let j;
        try { j = await api('/api/compare?nos=' + encodeURIComponent(nos.join(','))); }
        catch (e) { return; }
        if (JSON.stringify(noOf()) !== JSON.stringify(nos)) continue;   // 选择已变 → 丢弃，重跑
        rows = j.rows || [];
        syncing = true;
        CompareBox.setAll(rows.map(r => ({ no: r.property_no, addr: r.address, name: r.building_name, price: r.price, pdf: !!r.pdf_url })));
        syncing = false;
        return;
      }
    } else {
      // v1.8.6 旧逻辑：无竞态闸门，setAll 写回"删除前捕获的旧 nos"
      const nos = noOf();
      if (nos.length < 2) { rows = []; return; }
      let j;
      try { j = await api('/api/compare?nos=' + encodeURIComponent(nos.join(','))); }
      catch (e) { return; }
      rows = j.rows || [];
      syncing = true;
      CompareBox.setAll(rows.map(r => ({ no: r.property_no, addr: r.address, name: r.building_name, price: r.price, pdf: !!r.pdf_url })));
      syncing = false;
      return;
    }
  }
  function dropCol(no) {
    suppress = true; CompareBox.remove(no); suppress = false;
    const nos = CompareBox.nos();
    locationSearch = '/compare' + (nos.length ? '?nos=' + encodeURIComponent(nos.join(',')) : '');
    rows = [];
    load();
  }
  function clearAll() {
    locationSearch = '/compare';
    suppress = true; CompareBox.clear(); suppress = false;
    rows = [];
    load();
  }
  return { CompareBox, load, dropCol, clearAll };
}

async function run(useNewLogic, scenario) {
  const env = makeEnv(useNewLogic);
  ['A', 'B', 'C', 'D', 'E', 'F'].forEach(no => env.CompareBox.add({ no }));
  env.load();                       // 模拟页面加载即发起的首次 load()（在途 40ms）
  scenario(env);                    // 在 load() 仍 await 时同步执行清空/逐个删
  await new Promise(r => setTimeout(r, 200));   // 等所有在途 api + load 落地
  return env.CompareBox.count();
}

(async () => {
  const old1 = await run(false, env => env.clearAll());
  const new1 = await run(true, env => env.clearAll());
  const old2 = await run(false, env => ['A', 'B', 'C', 'D', 'E', 'F'].forEach(no => env.dropCol(no)));
  const new2 = await run(true, env => ['A', 'B', 'C', 'D', 'E', 'F'].forEach(no => env.dropCol(no)));

  console.log('OLD  clear-all → 对比栏剩余 =', old1, old1 === 0 ? '(未复活?)' : '(复活=BUG)');
  console.log('NEW  clear-all → 对比栏剩余 =', new1, new1 === 0 ? '(正确)' : '(仍BUG)');
  console.log('OLD  drop-all  → 对比栏剩余 =', old2, old2 === 0 ? '(未复活?)' : '(复活=BUG)');
  console.log('NEW  drop-all  → 对比栏剩余 =', new2, new2 === 0 ? '(正确)' : '(仍BUG)');

  const expectOldBug = (old1 > 0 && old2 > 0);
  const expectNewOk = (new1 === 0 && new2 === 0);
  if (expectOldBug && expectNewOk) {
    console.log('\nRESULT = PASS（旧逻辑确为 BUG：清空/删光后又复活；新逻辑已修复：栏保持空）');
    process.exit(0);
  } else {
    console.log('\nRESULT = FAIL（与预期不符）');
    process.exit(1);
  }
})();
