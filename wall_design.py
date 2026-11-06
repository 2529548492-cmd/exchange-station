# -*- coding: utf-8 -*-
"""已发布区：列表 / 卡片墙 切换 + 主题切换（当前主题 / 莫兰迪）。

只注入 CSS 和 JS，不改任何原有模板与后端逻辑。
默认进入卡片墙；抽屉里点「列表模式」或加 ?wall=0 回到列表。
"""

DRAWER_BLOCK = (
    '<div class="drawer-label">主题</div>'
    '<div class="skin-row">'
    '<button type="button" class="skin-btn" data-skin="current">当前主题</button>'
    '<button type="button" class="skin-btn morandi" data-skin="morandi">莫兰迪</button>'
    '</div>'
)

WALL_CSS = r"""
<style id="wall-view-css">
/* ================= 开关按钮 ================= */
.wall-toggle{
  display:inline-flex; align-items:center; gap:6px; flex:0 0 auto;
  height:34px; padding:0 12px; border-radius:10px; cursor:pointer;
  font:700 13px/1 Manrope,"Microsoft YaHei",sans-serif;
  background:var(--surface-2); color:var(--ink);
  border:1px solid var(--line);
}
.wall-toggle.on{background:var(--green); color:#fff; border-color:var(--green)}
.wall-toggle:hover{filter:brightness(1.05)}

/* ================= 莫兰迪主题 ================= */
html[data-skin="morandi"]{
  --bg:#F2EEE8;
  --surface:#FFFFFF;
  --surface-2:#EAE4DA;
  --ink:#3C3833;
  --muted:#7C756B;
  --line:#DCD4C6;
  --green:#A5736A;
  --green-ink:#8C5C54;
  --green-soft:#EFE3DC;
  --amber:#B4863C;
  --amber-soft:#F7EBD7;
  --amber-line:#E4CEA8;
  --red:#A6524A;
  --red-soft:#F6E6E3;
  --shadow:0 8px 28px rgba(90,72,60,.10);
}
html[data-skin="morandi"][data-theme="dark"]{
  --bg:#232019;
  --surface:#2C2822;
  --surface-2:#35302A;
  --ink:#EFE9DF;
  --muted:#B3A99B;
  --line:#423B32;
  --green:#C08E82;
  --green-ink:#D8A79B;
  --green-soft:#413229;
  --shadow:0 8px 28px rgba(0,0,0,.35);
}
html[data-skin="morandi"] body{background:var(--bg); color:var(--ink)}
html[data-skin="morandi"] #albumGrid.wall-grid .tile .tileimg{
  filter:saturate(.86) contrast(.98) brightness(1.02) sepia(.05);
}

/* ================= 主题切换按钮组 ================= */
.skin-row{display:flex; gap:6px; padding:4px 8px 8px}
.skin-btn{
  flex:1 1 0; height:32px; border-radius:9px; cursor:pointer;
  font:700 12px/1 Manrope,"Microsoft YaHei",sans-serif;
  border:1px solid var(--line); background:transparent; color:var(--muted);
}
.skin-btn[aria-pressed="true"]{background:var(--green); color:#fff; border-color:var(--green)}
.skin-btn.morandi[aria-pressed="true"]{background:#A5736A; border-color:#A5736A}

/* ================= 卡片墙模式 ================= */
body.wall-on{overflow:hidden}
body.wall-on #tab-published{position:fixed; inset:72px 0 0 0; overflow:hidden; padding:0; margin:0}
body.wall-on #tab-published .section{background:none; border:0; padding:0; margin:0}
body.wall-on .studio-hero, body.wall-on .studio-tagline{display:none}
body.wall-on #tab-published .pub-toolbar,
body.wall-on #tab-published .folderbar,
body.wall-on #tab-published #cattabs{
  position:relative; z-index:6; margin:0;
  background:var(--bg);
}
body.wall-on #tab-published .pub-toolbar{margin:8px 10px 0; border-radius:10px}
body.wall-on #tab-published .folderbar, body.wall-on #tab-published #cattabs{padding:0 10px}
body.wall-on #tab-published .section-head{display:none}
body.wall-on #tab-published .note, body.wall-on #tab-published .empty{display:none}

#albumGrid.wall-grid{
  position:absolute; left:0; right:0; bottom:0; top:0;
  display:block; margin:0; padding:0; overflow:hidden;
  gap:0; grid-template-columns:none; background:transparent;
  cursor:grab; touch-action:none; user-select:none;
}
body.wall-dragging #albumGrid.wall-grid{cursor:grabbing}
#albumGrid.wall-grid .tile{
  position:absolute; left:0; top:0; margin:0;
  border:0; border-radius:0; box-shadow:none; overflow:hidden;
  background:var(--surface-2);
  will-change:transform; backface-visibility:hidden;
  transition:none !important; animation:none !important;
}
#albumGrid.wall-grid .tile:hover{transform:none; box-shadow:none}
#albumGrid.wall-grid .tile .tileimg{
  position:absolute; inset:0; width:100%; height:100%; aspect-ratio:auto;
  object-fit:cover; border-radius:0;
}
#albumGrid.wall-grid .tile .tilefic,
#albumGrid.wall-grid .tile .fileicon{
  font-size:clamp(22px,3vw,40px) !important;
  font-weight:800;
  color:var(--green) !important;
  background:linear-gradient(160deg, var(--surface-2), var(--surface)) !important;
  letter-spacing:.02em;
}
#albumGrid.wall-grid .tile .tileinfo{
  position:absolute; left:0; right:0; bottom:0; z-index:2; margin:0;
  padding:16px 7px 6px; background:none;
  background-image:linear-gradient(180deg, transparent, rgba(0,0,0,.72));
}
#albumGrid.wall-grid .tile .tile-name{
  display:-webkit-box; -webkit-line-clamp:1; -webkit-box-orient:vertical;
  font-size:11px; line-height:1.3; margin:0; color:#fff;
}
#albumGrid.wall-grid .tile .tile-meta{
  font-size:8.5px; line-height:1.3; margin:1px 0 0; min-height:0; color:rgba(255,255,255,.72);
  display:-webkit-box; -webkit-line-clamp:1; -webkit-box-orient:vertical; overflow:hidden;
}
#albumGrid.wall-grid .tile .tile-dl{
  right:5px; top:5px; bottom:auto; font-size:8.5px; padding:1px 4px;
  border-radius:3px; background:rgba(0,0,0,.45); border:0;
}
#albumGrid.wall-grid .tile-check{top:5px; left:5px; width:16px; height:16px; border-radius:3px}

.wall-hint{
  position:fixed; left:50%; top:50%; transform:translate(-50%,-50%); z-index:5;
  pointer-events:none; padding:8px 16px; border-radius:999px;
  font:600 11px/1 Manrope,"Microsoft YaHei",sans-serif; letter-spacing:.16em;
  color:#fff; background:rgba(20,20,20,.66);
  transition:opacity .5s;
}
.wall-hint.gone{opacity:0}

/* ================= 移动端 ================= */
@media (max-width:760px){
  .wall-toggle{height:30px; padding:0 9px; font-size:11.5px; border-radius:8px}
  .shellhead .badge-live{display:none}
  body.wall-on #tab-published{inset:64px 0 0 0}
  body.wall-on #tab-published .pub-toolbar{margin:6px 6px 0}
  body.wall-on #tab-published .folderbar, body.wall-on #tab-published #cattabs{padding:0 6px}
  #albumGrid.wall-grid .tile .tile-name{font-size:10px}
  #albumGrid.wall-grid .tile .tile-meta{display:none}
  #albumGrid.wall-grid .tile .tileinfo{padding:14px 5px 4px}
  #albumGrid.wall-grid .tile .tile-dl{font-size:8px; padding:0 3px}
  .skin-row{padding:4px 6px 6px}
}
</style>
"""

WALL_JS = r"""
<script id="wall-view-js">
(function(){
  'use strict';
  var grid = document.getElementById('albumGrid');
  var toolbar = document.querySelector('#tab-published .pub-toolbar');
  if (!grid || !toolbar) return;

  var on = false, items = [], bankW = 0, bankH = 0, step = 120;
  var cam = {x: -80, y: -60};
  var busy = false, clones = [], layoutCount = 0;
  var drag = {on:false, lx:0, ly:0, lt:0, vx:0, vy:0, moved:0, tw:null};
  var btn, navBtn, hint;

  /* ---------------- 主题 ---------------- */
  var SKIN_KEY = 'exchange-skin';
  function applySkin(name){
    if (name === 'morandi') document.documentElement.setAttribute('data-skin', 'morandi');
    else document.documentElement.removeAttribute('data-skin');
    try { localStorage.setItem(SKIN_KEY, name); } catch(e){}
    var bs = document.querySelectorAll('.skin-btn');
    for (var i = 0; i < bs.length; i++) bs[i].setAttribute('aria-pressed', bs[i].dataset.skin === name ? 'true' : 'false');
    if (on) setTimeout(layout, 0);
  }
  (function initSkin(){
    var saved = 'current';
    try { saved = localStorage.getItem(SKIN_KEY) || 'current'; } catch(e){}
    if (saved === 'morandi') document.documentElement.setAttribute('data-skin','morandi');
  })();

  /* ---------------- 顶部按钮 ---------------- */
  btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'wall-toggle';
  btn.textContent = '列表';
  btn.setAttribute('aria-pressed','false');
  var head = document.querySelector('.shellhead');
  if (head){ btn.style.marginLeft = 'auto'; head.appendChild(btn); }
  else { toolbar.appendChild(btn); }

  hint = document.createElement('div');
  hint.className = 'wall-hint';
  hint.textContent = '拖动 · 无限延展';

  /* ---------------- 抽屉入口（服务端已插入，这里只绑事件） ---------------- */
  navBtn = document.getElementById('wallNavBtn');
  if (navBtn) navBtn.addEventListener('click', function(){ setOn(!on); });

  var skinBtns = document.querySelectorAll('.skin-btn');
  for (var si = 0; si < skinBtns.length; si++){
    (function(b){
      b.addEventListener('click', function(){ applySkin(b.dataset.skin); });
    })(skinBtns[si]);
  }

  /* ---------------- 布局 ---------------- */
  function visible(){
    var out = [], list = grid.querySelectorAll('.tile');
    for (var i = 0; i < list.length; i++){
      var el = list[i];
      if (el.dataset.clone) continue;
      if (el.style.display === 'none') continue;
      out.push(el);
    }
    return out;
  }

  function rng(seed){
    return function(){
      seed |= 0; seed = seed + 0x6D2B79F5 | 0;
      var t = Math.imul(seed ^ seed >>> 15, 1 | seed);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  }

  function pack(n, cols, rows, canBig){
    var rand = rng(cols * 7919 + rows * 131 + n);
    var occ = [], y, x;
    for (y = 0; y < rows; y++){ occ.push([]); for (x = 0; x < cols; x++) occ[y][x] = 0; }
    var sizes = [[2,2],[2,1],[1,2],[1,1],[1,1],[1,1],[2,1],[1,1]];
    var out = [];
    for (y = 0; y < rows && out.length < n; y++){
      for (x = 0; x < cols && out.length < n; x++){
        if (occ[y][x]) continue;
        var pick = null;
        var pool = (canBig && canBig[out.length]) ? sizes : [[1,1],[1,1],[2,1]];
        var order = pool.slice().sort(function(){ return rand() - .5; });
        for (var k = 0; k < order.length; k++){
          var w = order[k][0], h = order[k][1];
          if (x + w > cols || y + h > rows) continue;
          var ok = true;
          for (var yy = y; yy < y + h && ok; yy++)
            for (var xx = x; xx < x + w; xx++) if (occ[yy][xx]){ ok = false; break; }
          if (ok){ pick = [w, h]; break; }
        }
        if (!pick) pick = [1,1];
        for (var Y = y; Y < y + pick[1]; Y++)
          for (var X = x; X < x + pick[0]; X++) occ[Y][X] = 1;
        out.push({x:x, y:y, w:pick[0], h:pick[1]});
      }
    }
    return out;
  }

  function gridTop(){
    var top = 0, sels = ['.pub-toolbar','.folderbar','#cattabs'];
    for (var i = 0; i < sels.length; i++){
      var el = document.querySelector('#tab-published ' + sels[i]);
      if (el && el.offsetParent !== null) top += el.offsetHeight;
    }
    return top;
  }

  function clearClones(){
    for (var i = 0; i < clones.length; i++) if (clones[i].parentNode) clones[i].parentNode.removeChild(clones[i]);
    clones = [];
  }

  function layout(){
    if (!on || busy) return;
    busy = true;
    try { layoutInner(); } finally { busy = false; }
  }

  var world = {w: 0, h: 0};

  function layoutInner(){
    layoutCount++;
    grid.setAttribute('data-layouts', layoutCount);
    grid.style.top = gridTop() + 'px';

    clearClones();
    var base = visible();
    for (var bi = 0; bi < base.length; bi++) base[bi].style.display = '';
    if (!base.length){ items = []; return; }

    var vw = window.innerWidth, vh = Math.max(360, window.innerHeight - gridTop() - 8);
    var dens = 1;
    if (window.__wallCfg && window.__wallCfg.density) dens = parseFloat(window.__wallCfg.density) || 1;
    var unit = Math.max(64, Math.round(Math.min(vw / 7.2, vh / 4.2) / dens));

    /* 画布是视口的 2.5 倍，足够拖，且铺满不会有空洞 */
    var worldW = Math.ceil(vw * 2.5), worldH = Math.ceil(vh * 2.5);
    var bCols = Math.ceil(worldW / (2 * unit)), bRows = Math.ceil(worldH / (2 * unit));
    world.w = bCols * 2 * unit;
    world.h = bRows * 2 * unit;

    var blocks = [
      [[0,0,2,2]],
      [[0,0,1,2],[1,0,1,2]],
      [[0,0,2,1],[0,1,2,1]],
      [[0,0,2,1],[0,1,1,1],[1,1,1,1]],
      [[0,0,1,2],[1,0,1,1],[1,1,1,1]],
      [[0,0,1,1],[1,0,1,1],[0,1,2,1]]
    ];
    function pick(a, b){
      var x = Math.abs(((a + 11) * 73856093) ^ ((b + 7) * 19349663)) % 1000 / 1000;
      if (x < 0.16) return blocks[0];
      if (x < 0.40) return blocks[1];
      if (x < 0.56) return blocks[2];
      if (x < 0.74) return blocks[5];
      if (x < 0.88) return blocks[3];
      return blocks[4];
    }

    var pieces = [];
    for (var by = 0; by < bRows; by++){
      for (var bx = 0; bx < bCols; bx++){
        var pat = pick(bx, by);
        for (var q = 0; q < pat.length; q++){
          var pp = pat[q];
          pieces.push({x: bx*2 + pp[0], y: by*2 + pp[1], w: pp[2], h: pp[3]});
        }
      }
    }

    var pool = [];
    for (var i = 0; i < pieces.length; i++){
      if (i < base.length){ pool.push(base[i]); continue; }
      var src = base[i % base.length];
      var c = src.cloneNode(true);
      c.dataset.clone = '1';
      c.style.opacity = '1';
      grid.appendChild(c);
      clones.push(c);
      pool.push(c);
    }
    for (var h = pieces.length; h < base.length; h++) base[h].style.display = 'none';

    items = [];
    for (var k = 0; k < pieces.length; k++){
      var el2 = pool[k], pi = pieces[k];
      var w = pi.w * unit, hh = pi.h * unit;
      el2.style.width = w + 'px';
      el2.style.height = hh + 'px';
      items.push({el: el2, bx: pi.x*unit, by: pi.y*unit, w: w, h: hh});
    }
    clampCam();
    render();
  }

  function clampCam(){
    var vw = window.innerWidth, vh = window.innerHeight - gridTop();
    var maxX = Math.max(0, world.w - vw);
    var maxY = Math.max(0, world.h - vh);
    if (cam.x < 0) cam.x = 0;
    if (cam.y < 0) cam.y = 0;
    if (cam.x > maxX) cam.x = maxX;
    if (cam.y > maxY) cam.y = maxY;
  }

  function mod(v, m){ return ((v % m) + m) % m; }

  function render(){
    if (!bankW || !bankH) return;
    var vw = window.innerWidth, vh = window.innerHeight;
    for (var i = 0; i < items.length; i++){
      var it = items[i];
      var x = it.bx - cam.x;
      var y = it.by - cam.y;
      it.el.style.transform = 'translate3d(' + x + 'px,' + y + 'px,0)';
      it.el.style.visibility = (x < -it.w - 16 || y < -it.h - 16) ? 'hidden' : 'visible';
    }
  }

  function restore(){
    var all = grid.querySelectorAll('.tile');
    for (var i = 0; i < all.length; i++){
      all[i].style.transform = ''; all[i].style.width = ''; all[i].style.height = '';
      all[i].style.visibility = ''; all[i].style.opacity = ''; all[i].style.display = '';
    }
  }

  function setOn(v){
    on = v;
    document.body.classList.toggle('wall-on', on);
    grid.classList.toggle('wall-grid', on);
    btn.classList.toggle('on', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    btn.textContent = on ? '列表' : '卡片墙';
    if (navBtn) navBtn.textContent = on ? '列表模式' : '卡片墙视图';

    if (on){
      cam = {x: 60, y: 20};
      var all = grid.querySelectorAll('.tile');
      for (var i = 0; i < all.length; i++){
        if (window.gsap) gsap.killTweensOf(all[i]);
        all[i].style.opacity = '1';
      }
      document.body.appendChild(hint);
      setTimeout(function(){ hint.classList.add('gone'); }, 3000);
      layout();
      setTimeout(layout, 260);
    } else {
      if (hint.parentNode) hint.remove();
      clearClones();
      restore();
      items = []; bankW = bankH = 0;
    }
  }
  btn.addEventListener('click', function(){ setOn(!on); });

  /* ---------------- 拖动 ---------------- */
  function stopInertia(){ if (drag.tw){ drag.tw.kill(); drag.tw = null; } }

  grid.addEventListener('pointerdown', function(e){
    if (!on) return;
    if (e.button !== 0 && e.pointerType === 'mouse') return;
    stopInertia();
    drag.on = true; drag.moved = 0;
    drag.lx = e.clientX; drag.ly = e.clientY;
    drag.lt = performance.now(); drag.vx = drag.vy = 0;
    document.body.classList.add('wall-dragging');
    try { grid.setPointerCapture(e.pointerId); } catch(err){}
  });

  grid.addEventListener('pointermove', function(e){
    if (!on || !drag.on) return;
    var dx = e.clientX - drag.lx, dy = e.clientY - drag.ly;
    var now = performance.now(), dt = Math.max(1, now - drag.lt);
    drag.vx = dx / dt * 16; drag.vy = dy / dt * 16;
    drag.lx = e.clientX; drag.ly = e.clientY; drag.lt = now;
    drag.moved += Math.abs(dx) + Math.abs(dy);
    if (drag.moved > 4){ cam.x += dx; cam.y += dy; clampCam(); render(); }
  }, {passive:true});

  function endDrag(){
    if (!drag.on) return;
    drag.on = false;
    document.body.classList.remove('wall-dragging');
    if (!on) return;
    var vx = Math.max(-60, Math.min(60, drag.vx));
    var vy = Math.max(-60, Math.min(60, drag.vy));
    if (Math.abs(vx) < 1.2 && Math.abs(vy) < 1.2) return;
    var st = {x: vx, y: vy};
    drag.tw = gsap.to(st, {
      x: 0, y: 0, duration: 1.1, ease: 'power3.out',
      onUpdate: function(){ cam.x += st.x; cam.y += st.y; clampCam(); render(); }
    });
  }
  grid.addEventListener('pointerup', endDrag);
  grid.addEventListener('pointercancel', endDrag);

  grid.addEventListener('wheel', function(e){
    if (!on) return;
    stopInertia();
    cam.x -= (e.deltaX + e.deltaY) * 1.05;
    cam.y -= e.deltaY * 0.1;
    render();
    e.preventDefault();
  }, {passive:false});

  grid.addEventListener('click', function(e){
    if (on && drag.moved > 8){ e.stopPropagation(); e.preventDefault(); drag.moved = 0; }
  }, true);

  /* 原站筛选/搜索最后都会调 applyFilter() */
  if (typeof window.applyFilter === 'function'){
    var _af = window.applyFilter;
    window.applyFilter = function(){
      var r = _af.apply(this, arguments);
      if (on) setTimeout(layout, 0);
      return r;
    };
  }

  if (window.MutationObserver){
    var mo = new MutationObserver(function(recs){
      if (!on || busy) return;
      for (var i = 0; i < recs.length; i++){
        var added = recs[i].addedNodes;
        for (var j = 0; j < added.length; j++){
          if (added[j].dataset && added[j].dataset.clone) return;
        }
      }
      clearTimeout(mo._t); mo._t = setTimeout(layout, 180);
    });
    mo.observe(grid, { childList: true });
  }

  var rt;
  window.addEventListener('resize', function(){
    if (!on) return;
    clearTimeout(rt); rt = setTimeout(layout, 180);
  });

  /* ---------------- 点卡片：从原位置飞到正中放大（FLIP） ---------------- */
  function flipFrom(tile, card, overlay){
    if (!window.gsap || !tile || !card) return;
    var tr = tile.getBoundingClientRect();
    var cr = card.getBoundingClientRect();
    if (!cr.width || !cr.height) return;
    var sx = tr.width / cr.width, sy = tr.height / cr.height;
    var dx = (tr.left + tr.width / 2) - (cr.left + cr.width / 2);
    var dy = (tr.top + tr.height / 2) - (cr.top + cr.height / 2);
    gsap.killTweensOf(card);
    if (overlay) gsap.fromTo(overlay, {opacity:0}, {opacity:1, duration:.28, ease:'power2.out'});
    gsap.fromTo(card,
      {x:dx, y:dy, scale:Math.min(sx, sy), opacity:.5, transformOrigin:'50% 50%'},
      {x:0, y:0, scale:1, opacity:1, duration:.62, ease:'expo.out', clearProps:'transform'});
  }

  function hookPreview(){
    if (typeof window.openPreview !== 'function' || window.__flipHooked) return;
    window.__flipHooked = true;
    var _open = window.openPreview;
    window.openPreview = function(t){
      var r = _open.apply(this, arguments);
      setTimeout(function(){
        var ov = document.getElementById('previewOverlay');
        var card = document.getElementById('previewCard');
        if (card && t) flipFrom(t, card, ov);
      }, 16);
      return r;
    };
  }
  hookPreview();
  setTimeout(hookPreview, 800);

  /* 默认就是卡片墙；?wall=0 或点「列表模式」才回到列表 */
  /* 暂时默认列表：卡片墙排布还有 bug，等修稳了再默认开启（?wall=1 可手动进入） */
  window.__wallRelayout = function(){ if (on) layout(); };

  if (/[?&]wall=1/.test(location.search)) setTimeout(function(){ setOn(true); }, 120);
})();
</script>
"""


STATS_HTML = (
    '<div class="live-stats" id="liveStats" aria-live="polite">'
    '<span class="ls-item"><b id="lsOnline">–</b><i>在线</i></span>'
    '<span class="ls-sep"></span>'
    '<span class="ls-item"><b id="lsToday">–</b><i>今日活跃</i></span>'
    '<span class="ls-sep"></span>'
    '<span class="ls-item"><b id="lsTotal">–</b><i>累计使用</i></span>'
    '</div>'
)

STATS_CSS = r"""
<style id="live-stats-css">
.live-stats{
  display:inline-flex; align-items:center; gap:10px; flex:0 0 auto;
  height:34px; padding:0 12px; border-radius:10px;
  background:var(--surface-2); border:1px solid var(--line);
  font:700 12px/1 Manrope,"Microsoft YaHei",sans-serif; color:var(--ink);
}
.ls-item{display:inline-flex; align-items:baseline; gap:5px; white-space:nowrap}
.ls-item b{font-size:13px; font-variant-numeric:tabular-nums}
.ls-item i{font-style:normal; font-size:10px; color:var(--muted); letter-spacing:.04em}
.ls-sep{width:1px; height:14px; background:var(--line)}
@media (max-width:760px){
  .live-stats{height:28px; padding:0 9px; gap:7px}
  .ls-item b{font-size:12px}
  .ls-item i{font-size:9px}
  .live-stats .ls-sep{height:12px}
}
</style>
"""

STATS_JS = r"""
<script id="live-stats-js">
(function(){
  var box = document.getElementById('liveStats');
  if (!box) return;
  var oEl = document.getElementById('lsOnline'),
      tEl = document.getElementById('lsToday'),
      aEl = document.getElementById('lsTotal');
  function fmt(n){
    n = n || 0;
    return (n >= 10000) ? (n / 10000).toFixed(1) + 'w' : String(n);
  }
  function tick(){
    fetch('/api/stats', {credentials: 'same-origin'})
      .then(function(r){ return r.ok ? r.json() : null; })
      .then(function(d){
        if (!d) return;
        oEl.textContent = fmt(d.online);
        tEl.textContent = fmt(d.today);
        aEl.textContent = fmt(d.total);
      })
      .catch(function(){});
  }
  tick();
  setInterval(tick, 30000);
})();
</script>
"""


try:
    from download_ux import build_download_ux
    DL_CSS, DL_JS = build_download_ux()
except Exception:
    DL_CSS = DL_JS = ""

try:
    from wall_gear import build_gear
    GEAR_CSS, GEAR_HTML, GEAR_JS = build_gear()
except Exception:
    GEAR_CSS = GEAR_HTML = GEAR_JS = ""


def build_wall_page(page: str) -> str:
    page = page.replace("</head>", STATS_CSS + DL_CSS + "</head>", 1)
    # 顶部：交换站 标题右边插入在线/累计
    head_anchor = '<div class="badge-live">'
    if head_anchor in page:
        page = page.replace(head_anchor, STATS_HTML + head_anchor, 1)
    anchor = '<button id="themeBtn"'
    if anchor in page:
        page = page.replace(anchor, DRAWER_BLOCK + anchor, 1)
    page = page.replace("</body>", STATS_JS + DL_JS + "</body>", 1)
    return page
