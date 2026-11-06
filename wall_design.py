# -*- coding: utf-8 -*-
"""在原有页面上加一个「画布视图」开关。

原则：
  · 不新增页面、不替换 DOM、不动任何原有 JS
  · 导航栏 / 抽屉 / 丝滑动效 / 点开预览 / 视频流畅高清 / 批量选择 / 搜索筛选 全部原样
  · 点开关后，只把 #albumGrid 的排布从 CSS Grid 换成「绝对定位 + 拖动 + 循环补位」
"""

WALL_CSS = r"""
<style id="wall-view-css">
.wall-toggle{
  display:inline-flex; align-items:center; gap:6px;
  height:34px; padding:0 12px; border-radius:8px; cursor:pointer;
  font:600 13px/1 Manrope,"Microsoft YaHei",sans-serif;
  background:#272727; color:#f5f5ef; border:1px solid #393a35;
}
.wall-toggle.on{background:#c7e6a1; color:#181818; border-color:#c7e6a1}
.wall-toggle:hover{filter:brightness(1.08)}

/* ---------- 移动端 ---------- */
@media (max-width:760px){
  .wall-toggle{height:30px; padding:0 9px; font-size:11.5px; border-radius:7px}
  .shellhead .badge-live{display:none}
  body.wall-on #tab-published .pub-toolbar{margin:6px 6px 0; border-radius:8px}
  body.wall-on #tab-published .folderbar,
  body.wall-on #tab-published #cattabs{font-size:12px}
  #albumGrid.wall-grid .tile .tile-name{font-size:10px}
  #albumGrid.wall-grid .tile .tile-meta{display:none}
  #albumGrid.wall-grid .tile .tileinfo{padding:14px 5px 4px}
  #albumGrid.wall-grid .tile .tile-dl{font-size:8px; padding:0 3px}
  .wall-hint{font-size:10px; padding:7px 13px}
}

/* ---------- 画布模式 ---------- */
body.wall-on{overflow:hidden}
body.wall-on #tab-published{position:fixed; inset:64px 0 0 0; overflow:hidden; padding:0; margin:0}
body.wall-on #tab-published .section{background:none; border:0; padding:0; margin:0}
body.wall-on .studio-hero, body.wall-on .studio-tagline{display:none}
body.wall-on #tab-published .pub-toolbar,
body.wall-on #tab-published .folderbar,
body.wall-on #tab-published #cattabs,
body.wall-on #tab-published .section-head{
  position:relative; z-index:6; margin:0;
  background:rgba(24,24,24,.72);
  backdrop-filter:blur(16px) saturate(150%);
}
body.wall-on #tab-published .pub-toolbar{margin:8px 8px 0; border-radius:10px}
body.wall-on #tab-published .folderbar,
body.wall-on #tab-published #cattabs{padding:0 8px}
body.wall-on #tab-published .section-head{display:none}
body.wall-on #tab-published .note, body.wall-on #tab-published .empty{display:none}

#albumGrid.wall-grid{
  position:absolute; left:0; right:0; bottom:0; top:0; display:block; margin:0; padding:0;
  overflow:hidden; gap:0; grid-template-columns:none;
  cursor:grab; touch-action:none; user-select:none;
}
body.wall-dragging #albumGrid.wall-grid{cursor:grabbing}
#albumGrid.wall-grid{background:#0d0d0d}
#albumGrid.wall-grid .tile{background:#1b1b1b}
#albumGrid.wall-grid .tile .tilefic{background:#232323 !important; color:#d8e9bd !important}
#albumGrid.wall-grid .tile .tileimg{filter:brightness(1.06) contrast(1.02)}
#albumGrid.wall-grid .tile{
  position:absolute; left:0; top:0; margin:0;
  border:0; border-radius:0; box-shadow:none; overflow:hidden;
  background:#1f1f1f; will-change:transform; backface-visibility:hidden;
}
#albumGrid.wall-grid .tile:hover{transform:none; border-color:transparent; box-shadow:none}
body.wall-on #albumGrid.wall-grid .tile{
  transition:none !important;
  animation:none !important;
}
#albumGrid.wall-grid .tile .tileimg{
  position:absolute; inset:0; width:100%; height:100%; aspect-ratio:auto;
  object-fit:cover; border-radius:0;
}
#albumGrid.wall-grid .tile .tilefic{
  position:absolute; inset:0; height:auto; aspect-ratio:auto;
  display:grid; place-items:center; font-size:clamp(18px,2.4vw,30px);
}
#albumGrid.wall-grid .tile .tileinfo{
  position:absolute; left:0; right:0; bottom:0; z-index:2;
  padding:16px 7px 6px; margin:0; background:none;
  background-image:linear-gradient(180deg, transparent, rgba(0,0,0,.7));
}
#albumGrid.wall-grid .tile .tile-name{
  display:-webkit-box; -webkit-line-clamp:1; -webkit-box-orient:vertical;
  font-size:11px; line-height:1.3; margin:0; color:#fff;
}
#albumGrid.wall-grid .tile .tile-meta{
  font-size:8.5px; line-height:1.3; margin:1px 0 0; min-height:0;
  color:rgba(255,255,255,.7); display:-webkit-box; -webkit-line-clamp:1;
  -webkit-box-orient:vertical; overflow:hidden;
}
#albumGrid.wall-grid .tile .tile-dl{
  right:5px; top:5px; bottom:auto; font-size:8.5px; padding:1px 4px;
  border-radius:3px; background:rgba(0,0,0,.45); border:0;
}
#albumGrid.wall-grid .tile-check{top:5px; left:5px; width:16px; height:16px; border-radius:3px}

.wall-hint{
  position:fixed; left:50%; top:50%; transform:translate(-50%,-50%);
  z-index:5; pointer-events:none; padding:8px 16px; border-radius:999px;
  font:500 11px/1 Manrope,"Microsoft YaHei",sans-serif; letter-spacing:.18em;
  color:#e9e9e2; background:rgba(24,24,24,.72); backdrop-filter:blur(12px);
  transition:opacity .5s;
}
.wall-hint.gone{opacity:0}
</style>
"""

WALL_JS = r"""
<script id="wall-view-js">
(function(){
  'use strict';
  var grid = document.getElementById('albumGrid');
  var toolbar = document.querySelector('#tab-published .pub-toolbar');
  if (!grid || !toolbar) return;

  /* ---------- 开关按钮 ---------- */
  var btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'wall-toggle';
  btn.textContent = '卡片墙';
  btn.title = '切换 列表 / 卡片墙';
  btn.setAttribute('aria-pressed', 'false');
  var head = document.querySelector('.shellhead');
  if (head) { btn.style.marginLeft = 'auto'; head.appendChild(btn); }
  else { toolbar.appendChild(btn); }

  var hint = document.createElement('div');
  hint.className = 'wall-hint';
  hint.textContent = '拖动 · 无限延展';

  var on = false, items = [], bankW = 0, bankH = 0, step = 128;
  var cam = {x: -80, y: -60};
  var drag = {on:false, lx:0, ly:0, lt:0, vx:0, vy:0, moved:0, tw:null};

  function visible(){
    var out = [], list = grid.querySelectorAll('.tile');
    for (var i = 0; i < list.length; i++) if (list[i].style.display !== 'none') out.push(list[i]);
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
        var pool = canBig && canBig[out.length] ? sizes : [[1,1],[1,1],[2,1]];
        var order = pool.slice().sort(function(){ return rand() - .5; });
        for (var k = 0; k < order.length; k++){
          var w = order[k][0], h = order[k][1];
          if (x + w > cols || y + h > rows) continue;
          var ok = true;
          for (var yy = y; yy < y + h && ok; yy++)
            for (var xx = x; xx < x + w; xx++) if (occ[yy][xx]){ ok = false; break; }
          if (ok){ pick = [w, h]; break; }
        }
        if (!pick) pick = [1, 1];
        for (var Y = y; Y < y + pick[1]; Y++)
          for (var X = x; X < x + pick[0]; X++) occ[Y][X] = 1;
        out.push({x:x, y:y, w:pick[0], h:pick[1]});
      }
    }
    return out;
  }

  function gridTop(){
    var top = 0;
    var sels = ['.pub-toolbar','.folderbar','#cattabs'];
    for (var i = 0; i < sels.length; i++){
      var el = document.querySelector('#tab-published ' + sels[i]);
      if (el && el.offsetParent !== null) top += el.offsetHeight;
    }
    return top;
  }

  var clones = [];

  function clearClones(){
    for (var i = 0; i < clones.length; i++) if (clones[i].parentNode) clones[i].parentNode.removeChild(clones[i]);
    clones = [];
  }

  var busy = false;

  function layout(){
    if (!on || busy) return;
    busy = true;
    try { layoutInner(); } finally { busy = false; }
  }

  var layoutCount = 0;

  function layoutInner(){
    if (!on) return;
    layoutCount++;
    grid.setAttribute('data-layouts', layoutCount);
    grid.style.top = gridTop() + 'px';

    var base = visible();
    clearClones();
    var all = grid.querySelectorAll('.tile');
    for (var r = 0; r < all.length; r++) all[r].style.display = all[r].dataset.wallHidden ? 'none' : '';

    if (!base.length){ items = []; bankW = bankH = 0; return; }

    var vw = window.innerWidth, vh = Math.max(360, window.innerHeight - gridTop() - 8);

    /* 先按理想格子大小估列数，再让 行×列 ≥ 文件数，最后放大到铺满视口 */
    var ideal = Math.max(96, Math.round(Math.min(vw / 6.4, vh / 3.6)));
    var cols = Math.max(2, Math.round(vw / ideal));
    var rows = Math.max(2, Math.ceil(base.length / cols));
    step = Math.max(Math.ceil(vw / cols), Math.ceil(vh / rows));
    bankW = cols * step;
    bankH = rows * step;

    /* 格子数一定 ≥ 文件数，多出来的位置用循环克隆补满，保证没有空洞 */
    var cells = cols * rows;
    var pool = [];
    for (var i = 0; i < cells; i++){
      if (i < base.length){ pool.push(base[i]); continue; }
      var src = base[i % base.length];
      var c = src.cloneNode(true);
      c.dataset.clone = '1';
      c.style.opacity = '1';
      grid.appendChild(c);
      clones.push(c);
      pool.push(c);
    }

    var canBig = [];
    for (var ci = 0; ci < pool.length; ci++){
      var im = pool[ci].querySelector('.tileimg');
      canBig.push(!!(im && im.getAttribute('src') && im.style.display !== 'none'));
    }
    var pat = pack(cells, cols, rows, canBig);

    items = [];
    var used = {};
    for (var k = 0; k < cells; k++){
      var el = pool[k], p = pat[k] || {x:0,y:0,w:1,h:1};
      used[k] = true;
      el.style.width = (p.w * step) + 'px';
      el.style.height = (p.h * step) + 'px';
      items.push({el:el, bx:p.x*step, by:p.y*step, w:p.w*step, h:p.h*step});
    }
    render();
  }

  function mod(v, m){ return ((v % m) + m) % m; }

  function render(){
    if (!bankW || !bankH) return;
    var vw = window.innerWidth, vh = window.innerHeight;
    for (var i = 0; i < items.length; i++){
      var it = items[i];
      var x = mod(it.bx + cam.x, bankW) - step;
      var y = mod(it.by + cam.y, bankH) - step;
      if (x > vw + it.w) x -= bankW;
      if (y > vh + it.h) y -= bankH;
      it.el.style.transform = 'translate3d(' + x + 'px,' + y + 'px,0)';
      it.el.style.visibility = (x < -it.w - 16 || y < -it.h - 16) ? 'hidden' : 'visible';
    }
  }

  function restore(){
    var list = grid.querySelectorAll('.tile');
    for (var i = 0; i < list.length; i++){
      var el = list[i];
      el.style.transform = ''; el.style.width = ''; el.style.height = '';
      el.style.visibility = ''; el.style.position = '';
    }
  }

  function setOn(v){
    on = v;
    document.body.classList.toggle('wall-on', on);
    grid.classList.toggle('wall-grid', on);
    btn.classList.toggle('on', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    btn.textContent = on ? '列表' : '卡片墙';
    if (on){
      cam = {x: -80, y: -60};
      var all = grid.querySelectorAll('.tile');
      for (var i = 0; i < all.length; i++){
        if (window.gsap) gsap.killTweensOf(all[i]);
        all[i].style.opacity = '1';
      }
      var links = document.querySelectorAll('#tab-published a, #tab-published .note, #tab-published span');
      for (var j = 0; j < links.length; j++){
        var t = (links[j].textContent || '').trim();
        if (t.length < 24 && t.indexOf('审核') > -1) links[j].style.display = 'none';
      }
      document.body.appendChild(hint);
      setTimeout(function(){ hint.classList.add('gone'); }, 3200);
      layout();
      setTimeout(layout, 260);
    } else {
      hint.remove();
      clearClones();
      restore();
      items = []; bankW = bankH = 0;
    }
  }
  btn.addEventListener('click', function(){ setOn(!on); });

  /* ---------- 拖动 ---------- */
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
    if (drag.moved > 4){ cam.x += dx; cam.y += dy; render(); }
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
      x: 0, y: 0, duration: 1.15, ease: 'power3.out',
      onUpdate: function(){ cam.x += st.x; cam.y += st.y; render(); }
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

  /* 拖动后不要误触预览 */
  grid.addEventListener('click', function(e){
    if (on && drag.moved > 8){ e.stopPropagation(); e.preventDefault(); drag.moved = 0; }
  }, true);

  /* 原站筛选/搜索最后都调 applyFilter() */
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
        var mine = false;
        for (var j = 0; j < added.length; j++) if (added[j].dataset && added[j].dataset.clone) mine = true;
        if (mine) return;
      }
      clearTimeout(mo._t); mo._t = setTimeout(layout, 160);
    });
    mo.observe(grid, { childList: true });
  }

  var rt;
  window.addEventListener('resize', function(){
    if (!on) return;
    clearTimeout(rt); rt = setTimeout(layout, 160);
  });

  /* 直接用链接打开画布：?design=canvas&wall=1 */
  if (/[?&]wall=1/.test(location.search)) setTimeout(function(){ setOn(true); }, 120);
})();
</script>
"""


def build_wall_page(page: str) -> str:
    """把「画布视图」开关注入现有页面，其它一切不动。"""
    page = page.replace("</head>", WALL_CSS + "</head>", 1)
    page = page.replace("</body>", WALL_JS + "</body>", 1)
    return page
