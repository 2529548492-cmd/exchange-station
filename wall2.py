# -*- coding: utf-8 -*-
"""卡片墙 v2：周期图案 + 九宫复制。

几何原理（这次是不会出空洞的写法）：
  · 图案是一个 P=cols*unit × Q=rows*unit 的周期块，内部由 2×2 / 2×1 / 1×2 / 1×1 铺满
  · 每块砖画 3×3 份（偏移 -P/0/+P、-Q/0/+Q），出视口的自动裁剪
  · 相机取模 cam ∈ [0,P)×[0,Q)，于是「拖动 = 无限延展」且图案永远接得上
"""

WALL2_CSS = r"""
<style id="wall2-css">
body.wall-on{overflow:hidden}
body.wall-on #tab-published{position:fixed; inset:72px 0 0 0; overflow:hidden; padding:0; margin:0}
body.wall-on #tab-published .section{background:none; border:0; padding:0; margin:0}
body.wall-on .studio-hero, body.wall-on .studio-tagline{display:none}
body.wall-on #tab-published .pub-toolbar,
body.wall-on #tab-published .folderbar,
body.wall-on #tab-published #cattabs{
  position:relative; z-index:6; margin:0; background:var(--bg);
}
body.wall-on #tab-published .pub-toolbar{margin:8px 10px 0; border-radius:10px}
body.wall-on #tab-published .folderbar, body.wall-on #tab-published #cattabs{padding:0 10px}
body.wall-on #tab-published .section-head{display:none}
body.wall-on #tab-published .note, body.wall-on #tab-published .empty{display:none}

#albumGrid.wall2{
  position:absolute; left:0; right:0; bottom:0; top:0;
  display:block; margin:0; padding:0; overflow:hidden;
  gap:0; background:transparent;
  cursor:grab; touch-action:none; user-select:none;
}
body.wall-dragging #albumGrid.wall2{cursor:grabbing}
#albumGrid.wall2 .tile{
  position:absolute; left:0; top:0; margin:0;
  border:0; border-radius:0; box-shadow:none; overflow:hidden;
  background:var(--surface-2);
  will-change:transform; backface-visibility:hidden;
  transition:none !important; animation:none !important;
}
#albumGrid.wall2 .tile:hover{transform:none; box-shadow:none}
#albumGrid.wall2 .tile .tileimg{
  position:absolute; inset:0; width:100%; height:100%; aspect-ratio:auto;
  object-fit:cover; border-radius:0;
}
#albumGrid.wall2 .tile .tileinfo{
  position:absolute; left:0; right:0; bottom:0; z-index:2; margin:0;
  padding:16px 7px 6px; background:none;
  background-image:linear-gradient(180deg, transparent, rgba(0,0,0,.72));
}
#albumGrid.wall2 .tile .tile-name{
  display:-webkit-box; -webkit-line-clamp:1; -webkit-box-orient:vertical;
  font-size:11px; line-height:1.3; margin:0; color:#fff;
}
#albumGrid.wall2 .tile .tile-meta{
  font-size:8.5px; line-height:1.3; margin:1px 0 0; min-height:0;
  color:rgba(255,255,255,.72); display:-webkit-box;
  -webkit-line-clamp:1; -webkit-box-orient:vertical; overflow:hidden;
}
#albumGrid.wall2 .tile .tilefic{
  font-weight:800; color:var(--green) !important;
  background:linear-gradient(160deg, var(--surface-2), var(--surface)) !important;
}

.wall-hint{
  position:fixed; left:50%; top:50%; transform:translate(-50%,-50%); z-index:5;
  pointer-events:none; padding:8px 16px; border-radius:999px;
  font:600 11px/1 Manrope,"Microsoft YaHei",sans-serif; letter-spacing:.16em;
  color:#fff; background:rgba(20,20,20,.66); transition:opacity .5s;
}
.wall-hint.gone{opacity:0}

@media (max-width:760px){
  body.wall-on #tab-published{inset:64px 0 0 0}
  body.wall-on #tab-published .pub-toolbar{margin:6px 6px 0}
  #albumGrid.wall2 .tile .tile-name{font-size:10px}
  #albumGrid.wall2 .tile .tile-meta{display:none}
}
</style>
"""

WALL2_JS = r"""
<script id="wall2-js">
(function(){
  'use strict';
  var grid = document.getElementById('albumGrid');
  var toolbar = document.querySelector('#tab-published .pub-toolbar');
  if (!grid || !toolbar) return;

  var on = false, busy = false, pieces = [], copies = [], P = 0, Q = 0, unit = 120;
  var cam = {x: 0, y: 0};
  var drag = {on:false, lx:0, ly:0, lt:0, vx:0, vy:0, moved:0, tw:null};
  var btn, navBtn, hint, layoutCount = 0;

  btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'wall-toggle';
  btn.textContent = '卡片墙';
  btn.setAttribute('aria-pressed','false');
  var head = document.querySelector('.shellhead');
  if (head){ btn.style.marginLeft = 'auto'; head.appendChild(btn); }
  else { toolbar.appendChild(btn); }

  hint = document.createElement('div');
  hint.className = 'wall-hint';
  hint.textContent = '拖动 · 无限延展';

  navBtn = document.getElementById('wallNavBtn');
  if (navBtn) navBtn.addEventListener('click', function(){ setOn(!on); });

  function visible(){
    var out = [], list = grid.querySelectorAll('.tile');
    for (var i = 0; i < list.length; i++){
      var el = list[i];
      if (el.dataset.copy) continue;
      if (el.style.display === 'none') continue;
      out.push(el);
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

  function clearCopies(){
    for (var i = 0; i < copies.length; i++) if (copies[i].parentNode) copies[i].parentNode.removeChild(copies[i]);
    copies = [];
  }

  function layout(){
    if (!on || busy) return;
    busy = true;
    try { layoutInner(); } finally { busy = false; }
  }

  function layoutInner(){
    layoutCount++;
    grid.setAttribute('data-layouts', layoutCount);
    grid.style.top = gridTop() + 'px';
    clearCopies();

    var base = visible();
    for (var b = 0; b < base.length; b++) base[b].style.display = '';
    if (!base.length){ pieces = []; return; }

    var vw = window.innerWidth, vh = Math.max(360, window.innerHeight - gridTop() - 8);
    var dens = 1;
    if (window.__wallCfg && window.__wallCfg.density) dens = parseFloat(window.__wallCfg.density) || 1;
    unit = Math.max(64, Math.round(Math.min(vw / 7, vh / 4) / dens));

    var cols = Math.ceil(vw / unit) + 2;
    var rows = Math.ceil(vh / unit) + 2;
    if (cols % 2) cols++;
    if (rows % 2) rows++;
    P = cols * unit;
    Q = rows * unit;

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

    pieces = [];
    for (var by = 0; by < rows / 2; by++){
      for (var bx = 0; bx < cols / 2; bx++){
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
      c.dataset.copy = '1';
      c.style.opacity = '1';
      grid.appendChild(c);
      copies.push(c);
      pool.push(c);
    }
    for (var hh = pieces.length; hh < base.length; hh++) base[hh].style.display = 'none';

    /* 九宫复制：每块砖 3×3 份 */
    var offs = [];
    for (var oy = -1; oy <= 1; oy++) for (var ox = -1; ox <= 1; ox++) offs.push([ox*P, oy*Q]);

    var nodes = [];
    for (var k = 0; k < pieces.length; k++){
      var pi = pieces[k];
      var w = pi.w * unit, h2 = pi.h * unit;
      for (var oi = 0; oi < offs.length; oi++){
        var node = pool[k];
        if (oi > 0){
          node = pool[k].cloneNode(true);
          node.dataset.copy = '1';
          node.style.opacity = '1';
          grid.appendChild(node);
          copies.push(node);
        }
        node.style.width = w + 'px';
        node.style.height = h2 + 'px';
        nodes.push({el: node, x: pi.x*unit + offs[oi][0], y: pi.y*unit + offs[oi][1], w: w, h: h2});
      }
    }
    pieces = nodes;
    render();
  }

  function mod(v, m){ return ((v % m) + m) % m; }

  function render(){
    if (!P || !Q) return;
    var vw = window.innerWidth, vh = window.innerHeight;
    for (var i = 0; i < pieces.length; i++){
      var it = pieces[i];
      var x = it.x - cam.x, y = it.y - cam.y;
      if (x <= -it.w - 8 || x >= vw + 8 || y <= -it.h - 8 || y >= vh + 8){
        it.el.style.visibility = 'hidden';
        continue;
      }
      it.el.style.visibility = 'visible';
      it.el.style.transform = 'translate3d(' + x + 'px,' + y + 'px,0)';
    }
  }

  function setOn(v){
    on = v;
    document.body.classList.toggle('wall-on', on);
    grid.classList.toggle('wall2', on);
    btn.classList.toggle('on', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    btn.textContent = on ? '列表' : '卡片墙';
    if (navBtn) navBtn.textContent = on ? '列表模式' : '卡片墙视图';

    if (on){
      cam = {x: 0, y: 0};
      document.body.appendChild(hint);
      setTimeout(function(){ hint.classList.add('gone'); }, 3000);
      layout();
      setTimeout(layout, 240);
    } else {
      if (hint.parentNode) hint.remove();
      clearCopies();
      var all = grid.querySelectorAll('.tile');
      for (var i = 0; i < all.length; i++){
        all[i].style.transform = ''; all[i].style.width = '';
        all[i].style.height = ''; all[i].style.visibility = '';
        all[i].style.opacity = ''; all[i].style.display = '';
      }
      pieces = []; P = Q = 0;
    }
  }
  btn.addEventListener('click', function(){ setOn(!on); });

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
    if (drag.moved > 4){
      cam.x = mod(cam.x - dx, P);
      cam.y = mod(cam.y - dy, Q);
      render();
    }
  }, {passive:true});

  function endDrag(){
    if (!drag.on) return;
    drag.on = false;
    document.body.classList.remove('wall-dragging');
    if (!on) return;
    var vx = Math.max(-55, Math.min(55, drag.vx));
    var vy = Math.max(-55, Math.min(55, drag.vy));
    if (Math.abs(vx) < 1.2 && Math.abs(vy) < 1.2) return;
    var st = {x: vx, y: vy};
    drag.tw = gsap.to(st, {
      x: 0, y: 0, duration: 1.1, ease: 'power3.out',
      onUpdate: function(){
        cam.x = mod(cam.x - st.x, P);
        cam.y = mod(cam.y - st.y, Q);
        render();
      }
    });
  }
  grid.addEventListener('pointerup', endDrag);
  grid.addEventListener('pointercancel', endDrag);

  grid.addEventListener('wheel', function(e){
    if (!on) return;
    stopInertia();
    cam.x = mod(cam.x - (e.deltaX + e.deltaY) * 1.05, P);
    cam.y = mod(cam.y - e.deltaY * 0.12, Q);
    render();
    e.preventDefault();
  }, {passive:false});

  grid.addEventListener('click', function(e){
    if (on && drag.moved > 8){ e.stopPropagation(); e.preventDefault(); drag.moved = 0; }
  }, true);

  if (typeof window.applyFilter === 'function'){
    var _af = window.applyFilter;
    window.applyFilter = function(){
      var r = _af.apply(this, arguments);
      if (on) setTimeout(layout, 0);
      return r;
    };
  }

  var rt;
  window.addEventListener('resize', function(){
    if (!on) return;
    clearTimeout(rt);
    rt = setTimeout(layout, 180);
  });

  /* 点卡片：从原位置飞向正中放大 */
  function flipFrom(tile, card, overlay){
    if (!window.gsap || !tile || !card) return;
    var tr = tile.getBoundingClientRect(), cr = card.getBoundingClientRect();
    if (!cr.width || !cr.height) return;
    var sx = tr.width / cr.width, sy = tr.height / cr.height;
    var dx = (tr.left + tr.width/2) - (cr.left + cr.width/2);
    var dy = (tr.top + tr.height/2) - (cr.top + cr.height/2);
    gsap.killTweensOf(card);
    if (overlay) gsap.fromTo(overlay, {opacity:0}, {opacity:1, duration:.26, ease:'power2.out'});
    gsap.fromTo(card,
      {x:dx, y:dy, scale:Math.min(sx, sy), opacity:.55, transformOrigin:'50% 50%'},
      {x:0, y:0, scale:1, opacity:1, duration:.62, ease:'expo.out', clearProps:'transform'});
  }
  function hookPreview(){
    if (typeof window.openPreview !== 'function' || window.__flip2) return;
    window.__flip2 = true;
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
  setTimeout(hookPreview, 700);

  if (!/[?&]wall=0/.test(location.search)) setTimeout(function(){ setOn(true); }, 140);
})();
</script>
"""


def build_wall2():
    return WALL2_CSS, WALL2_JS
