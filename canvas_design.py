# -*- coding: utf-8 -*-
"""无限画布首页：数据来自 Flask 的 files 上下文，不改变任何后端逻辑。

用法（app.py）：
    from canvas_design import build_canvas_page
    CANVAS_PAGE = build_canvas_page()
"""

CANVAS_HTML = r"""<!doctype html>
<html lang="zh-CN" data-theme="morandi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>交换站 · 无限画布</title>
<meta name="theme-color" content="#F2EEE8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@300;400;500;600&family=Geist+Mono:wght@400;500&family=Noto+Sans+SC:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#F2EEE8; --fg:#3C3833; --sub:#7C756B; --faint:#ACA49A;
  --hair:rgba(60,56,51,.14); --accent:#A5736A; --glass:rgba(242,238,232,.76);
  --font:"Manrope","Noto Sans SC","PingFang SC","Microsoft YaHei",system-ui,sans-serif;
  --mono:"Geist Mono",ui-monospace,monospace;
  --step:212px;
  --scrim-a:.62;         /* 遮罩深浅 */
  --img-blur:0px;
  --cap-blur:0px;
  --cap-bg:rgba(242,238,232,.92);
  --cap-fg:#3C3833;
  --cap-border:0;
  --cap-pad:5px;
  --cap-shadow:none;
  --size:196px;
}
html[data-theme="apple"]{
  --bg:#FBFBFD; --fg:#0B0B0C; --sub:#6E6E73; --faint:#A1A1A6;
  --hair:rgba(0,0,0,.09); --accent:#B03A2B; --glass:rgba(251,251,253,.74);
}
*,*::before,*::after{box-sizing:border-box}
html,body{margin:0;padding:0;height:100%;background:var(--bg);color:var(--fg);overflow:hidden}
body{font-family:var(--font);font-size:15px;line-height:1.45;-webkit-font-smoothing:antialiased}
img{display:block;max-width:100%}
button{font:inherit;color:inherit;background:none;border:0;padding:0;cursor:pointer}
::selection{background:var(--fg);color:var(--bg)}

/* ---------- nav ---------- */
.nav{
  position:fixed;top:0;left:0;right:0;z-index:60;height:56px;
  display:flex;align-items:center;gap:12px;padding:0 18px;
  background:var(--glass);-webkit-backdrop-filter:blur(20px) saturate(170%);backdrop-filter:blur(20px) saturate(170%);
  border-bottom:1px solid var(--hair);
}
.seal{width:34px;height:34px;border-radius:9px;background:var(--accent);color:#FFF7F1;
  display:grid;place-items:center;font-size:16px;font-weight:600;box-shadow:0 1px 2px rgba(0,0,0,.14);flex:0 0 auto}
.brand{font-size:14.5px;font-weight:600;letter-spacing:-.01em;white-space:nowrap}
.brand em{font-family:var(--mono);font-size:9px;font-style:normal;letter-spacing:.2em;color:var(--faint);margin-left:7px;text-transform:uppercase}
.nav-mid{display:none;gap:6px;margin-left:14px}
.chip{flex:0 0 auto;height:28px;padding:0 12px;border-radius:999px;font-size:12px;
  background:rgba(60,56,51,.06);color:var(--fg);transition:background .25s,color .25s}
html[data-theme="apple"] .chip{background:rgba(0,0,0,.05)}
.chip[aria-pressed="true"]{background:var(--fg);color:var(--bg)}
.nav-right{margin-left:auto;display:flex;align-items:center;gap:8px}
.seg{display:none;align-items:center;gap:2px;padding:2px;border-radius:999px;background:rgba(60,56,51,.07);height:30px}
html[data-theme="apple"] .seg{background:rgba(0,0,0,.05)}
.seg button{height:26px;padding:0 10px;border-radius:999px;font-size:11.5px;color:var(--sub)}
.seg button[aria-pressed="true"]{background:var(--bg);color:var(--fg)}
.count{font-family:var(--mono);font-size:10px;letter-spacing:.1em;color:var(--faint);white-space:nowrap}

/* ---------- stage ---------- */
#stage{
  position:fixed;inset:0;overflow:hidden;cursor:grab;
  touch-action:none;user-select:none;
  will-change:transform;
}
body.dragging #stage{cursor:grabbing}
.tile{
  position:absolute;top:0;left:0;width:var(--size);height:var(--size);
  margin:0;overflow:hidden;border-radius:0;background:none;border:0;
  will-change:transform;backface-visibility:hidden;
  contain:layout style paint;
}
.tile img{width:100%;height:100%;object-fit:cover;display:block;border:0;filter:saturate(.9) contrast(.98);transition:transform .5s cubic-bezier(.16,1,.3,1)}
html[data-theme="morandi"] .tile img{filter:saturate(.62) contrast(.94) brightness(1.03) sepia(.07)}
.tile img{filter:saturate(.9) contrast(.98)}
.tile::after{
  content:"";position:absolute;inset:auto 0 0 0;height:64%;pointer-events:none;
  background:linear-gradient(180deg,transparent,rgba(28,25,22,var(--scrim-a)));
}
.tile .n{position:absolute;top:5px;left:6px;z-index:2;font-family:var(--mono);font-size:8px;letter-spacing:.06em;
  color:rgba(255,255,255,.92);background:rgba(20,18,16,.42);padding:1px 4px;border-radius:3px}
.tile .cap{
  position:absolute;left:6px;right:6px;bottom:6px;z-index:2;
  color:var(--cap-fg);background:var(--cap-bg);
  padding:var(--cap-pad);border-radius:0;
  box-shadow:var(--cap-shadow);
  -webkit-backdrop-filter:blur(var(--cap-blur)) saturate(140%);
  backdrop-filter:blur(var(--cap-blur)) saturate(140%);
  border:var(--cap-border) solid rgba(0,0,0,.08);
}
.tile .cap b{display:block;font-size:11.5px;font-weight:600;line-height:1.25;letter-spacing:-.005em;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tile .cap span{display:block;margin-top:1px;font-family:var(--mono);font-size:7.5px;letter-spacing:.08em;
  opacity:.62;text-transform:uppercase;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tile:hover img{transform:scale(1.05)}
.tile.wide .cap b,.tile.big .cap b{font-size:13px}
.tile.big .cap b{font-size:15px}
.tile.big .cap span,.tile.wide .cap span{font-size:8.5px}
.tile.big .n,.tile.wide .n{font-size:9px}

/* 信息条方向：竖向 */
.wall-dir-v .tile .cap{
  left:auto;right:0;top:0;bottom:0;width:auto;max-width:27%;padding:7px 5px;
  writing-mode:vertical-rl;text-orientation:mixed;
  display:flex;flex-direction:column;justify-content:flex-end;
  border-radius:0;padding:8px 6px;
}
.wall-dir-v .tile .cap b{white-space:normal;line-height:1.15;font-size:10.5px}
.wall-dir-v .tile .cap span{margin-top:0;margin-left:4px;writing-mode:horizontal-tb}
.wall-dir-v .tile::after{inset:0 0 0 auto;width:42%;height:auto;
  background:linear-gradient(90deg,transparent,rgba(28,25,22,var(--scrim-a)))}

/* 信息条样式 */
html[data-capstyle="print"]{--cap-bg:#fff;--cap-fg:#111;--cap-border:1px;--cap-shadow:0 1px 0 rgba(0,0,0,.06)}
html[data-capstyle="ink"]{--cap-bg:rgba(17,16,15,.94);--cap-fg:#F4EFE7}
html[data-capstyle="frost"]{--cap-bg:rgba(255,255,255,.16);--cap-fg:#fff}
html[data-capstyle="frost"] .tile .cap{-webkit-backdrop-filter:blur(14px) saturate(150%);backdrop-filter:blur(14px) saturate(150%)}
html[data-capstyle="hatch"]{--cap-bg:rgba(242,238,232,.9);--cap-fg:#2E2A26}
html[data-capstyle="hatch"] .tile .cap{
  background-image:repeating-linear-gradient(45deg,rgba(0,0,0,.06) 0 1px,transparent 1px 5px);
}
html[data-capstyle="none"]{--cap-bg:transparent;--cap-fg:#fff;--cap-shadow:none;--cap-pad:0}
html[data-capstyle="none"] .tile .cap{text-shadow:0 1px 2px rgba(0,0,0,.55),0 2px 14px rgba(0,0,0,.6),0 0 1px rgba(0,0,0,.8)}
.tile .glyph{
  position:absolute;inset:0;display:grid;place-items:center;
  font-size:clamp(18px,2.6vw,34px);color:var(--faint);
  background:rgba(60,56,51,.06);letter-spacing:.1em;
}
html[data-theme="morandi"] .tile .glyph{background:#E7E1D7}
.empty-note{
  position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);
  max-width:min(520px,80vw);text-align:center;color:var(--sub);
  font-size:14px;line-height:1.7;
}
.tile.dim{opacity:.12;pointer-events:none;filter:grayscale(1)}

/* ---------- hud ---------- */
.hud{
  position:fixed;left:18px;bottom:18px;z-index:55;pointer-events:none;max-width:min(70vw,420px);
  background:var(--glass);border:1px solid var(--hair);border-radius:14px;padding:14px 16px 12px;
  -webkit-backdrop-filter:blur(18px) saturate(160%);backdrop-filter:blur(18px) saturate(160%);
  box-shadow:0 24px 50px -34px rgba(20,18,16,.55);
}
.hud h1{margin:0;font-size:clamp(22px,4.6vw,34px);font-weight:500;letter-spacing:-.025em;line-height:1.12}
.hud p{margin:6px 0 0;color:var(--sub);font-size:12.5px}
.hud .keys{margin-top:10px;font-family:var(--mono);font-size:9px;letter-spacing:.14em;color:var(--faint);text-transform:uppercase}
.hint{
  position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);z-index:56;
  font-family:var(--mono);font-size:10.5px;letter-spacing:.22em;color:var(--sub);
  background:var(--glass);border:1px solid var(--hair);border-radius:999px;padding:9px 16px;
  -webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px);pointer-events:none;
  transition:opacity .5s;
}
.hint.gone{opacity:0}

/* ---------- gear + panel ---------- */
.gear{
  position:fixed;right:16px;bottom:16px;z-index:70;width:42px;height:42px;border-radius:50%;
  display:grid;place-items:center;color:var(--fg);
  background:var(--glass);border:1px solid var(--hair);
  -webkit-backdrop-filter:blur(18px) saturate(160%);backdrop-filter:blur(18px) saturate(160%);
  box-shadow:0 16px 40px -26px rgba(20,18,16,.6);
}
.gear:active{transform:scale(.94)}
.panel{
  position:fixed;right:14px;bottom:70px;z-index:75;width:min(340px,calc(100vw - 28px));
  background:var(--glass);border:1px solid var(--hair);border-radius:16px;padding:14px 16px 10px;
  -webkit-backdrop-filter:blur(26px) saturate(170%);backdrop-filter:blur(26px) saturate(170%);
  box-shadow:0 40px 90px -50px rgba(20,18,16,.7);
  visibility:hidden;opacity:0;transform:translateY(10px);
}
.panel.open{visibility:visible}
.panel-top{display:flex;align-items:center;justify-content:space-between;padding-bottom:10px;border-bottom:1px solid var(--hair)}
.panel-top b{font-size:13px;font-weight:600;letter-spacing:-.01em}
.xbtn{width:26px;height:26px;border-radius:50%;display:grid;place-items:center;background:rgba(60,56,51,.08);color:var(--sub)}
html[data-theme="apple"] .xbtn{background:rgba(0,0,0,.06)}
.row{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:11px 0;border-bottom:1px solid rgba(60,56,51,.07)}
.row:last-of-type{border-bottom:0}
.row.col{display:block}
.row label{font-size:12.5px;color:var(--sub);white-space:nowrap}
.seg2{display:flex;gap:2px;background:rgba(60,56,51,.07);border-radius:999px;padding:2px}
html[data-theme="apple"] .seg2{background:rgba(0,0,0,.05)}
.seg2 button{height:26px;min-width:30px;padding:0 9px;border-radius:999px;font-size:11.5px;color:var(--sub)}
.seg2 button[aria-pressed="true"]{background:var(--bg);color:var(--fg);box-shadow:0 1px 2px rgba(0,0,0,.08)}
.styles{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:9px}
.styles button{
  display:flex;align-items:center;gap:6px;padding:7px;border-radius:9px;
  border:1px solid var(--hair);font-size:10.5px;color:var(--sub);background:rgba(60,56,51,.03);
}
.styles button[aria-pressed="true"]{border-color:var(--fg);color:var(--fg);background:rgba(60,56,51,.06)}
.sw{width:14px;height:14px;border-radius:3px;border:1px solid rgba(0,0,0,.15);flex:0 0 auto}
.sw.paper{background:#F2EEE8}.sw.print{background:#fff}
.sw.ink{background:#111}.sw.frost{background:linear-gradient(135deg,rgba(255,255,255,.5),rgba(255,255,255,.15));backdrop-filter:blur(3px)}
.sw.hatch{background:repeating-linear-gradient(45deg,#DDD6C8 0 1px,#F2EEE8 1px 4px)}
.sw.none{background:linear-gradient(135deg,transparent 45%,#B03A2B 45% 55%,transparent 55%)}
.tgl{width:40px;height:23px;border-radius:999px;background:rgba(60,56,51,.18);position:relative;transition:background .3s;flex:0 0 auto}
html[data-theme="apple"] .tgl{background:rgba(0,0,0,.15)}
.tgl i{position:absolute;top:2px;left:2px;width:19px;height:19px;border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.25);transition:transform .28s cubic-bezier(.16,1,.3,1)}
.tgl[aria-pressed="true"]{background:var(--accent)}
.tgl[aria-pressed="true"] i{transform:translateX(17px)}
.panel-note{margin:8px 0 2px;font-family:var(--mono);font-size:9px;letter-spacing:.1em;color:var(--faint)}

/* ---------- lightbox ---------- */
.lb{position:fixed;inset:0;z-index:120;display:none}
.lb.open{display:block}
.lb-bg{position:absolute;inset:0;background:rgba(22,20,17,.72);-webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px);opacity:0}
.lb-card{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);
  width:min(820px,92vw);max-height:88vh;background:var(--bg);border-radius:14px;overflow:hidden;
  display:flex;flex-direction:column;box-shadow:0 50px 120px -50px rgba(0,0,0,.7);opacity:0}
.lb-img{flex:0 0 auto;max-height:54vh;overflow:hidden;background:rgba(60,56,51,.08)}
.lb-img img{width:100%;height:100%;max-height:54vh;object-fit:contain}
.lb-body{padding:14px 18px 18px}
.lb-body h2{margin:0;font-size:18px;font-weight:600;letter-spacing:-.02em}
.lb-meta{margin-top:6px;font-family:var(--mono);font-size:9px;letter-spacing:.1em;color:var(--faint);
  display:flex;flex-wrap:wrap;gap:4px 12px;text-transform:uppercase}
.lb-acts{display:flex;gap:8px;margin-top:14px}
.btn{height:38px;flex:1 1 0;display:inline-flex;align-items:center;justify-content:center;border-radius:999px;
  background:var(--fg);color:var(--bg);font-size:13px}
.btn.ghost{background:rgba(60,56,51,.07);color:var(--fg)}
html[data-theme="apple"] .btn.ghost{background:rgba(0,0,0,.05)}

@media(min-width:760px){
  .nav{height:60px;padding:0 22px}
  .nav-mid{display:flex}
  .seg{display:flex}
  .brand em{display:inline}
  :root{--size:196px;--step:208px}
}
@media(max-width:759px){
  :root{--size:118px;--step:126px}
  .hud{left:12px;bottom:12px;max-width:78vw}
  .hud h1{font-size:20px}
}
@media(prefers-reduced-motion:reduce){*{transition-duration:.01ms!important}}
</style>
</head>
<body>

<header class="nav">
  <button class="seal" aria-label="交换站">交</button>
  <span class="brand">交换站<em>Canvas</em></span>
  <div class="nav-mid" id="chips">
    <button class="chip" data-type="all" aria-pressed="true">全部</button>
    <button class="chip" data-type="image" aria-pressed="false">图片</button>
    <button class="chip" data-type="audio" aria-pressed="false">音频</button>
    <button class="chip" data-type="doc" aria-pressed="false">文档</button>
    <button class="chip" data-type="video" aria-pressed="false">影像</button>
  </div>
  <div class="nav-right">
    <span class="count" id="count">135 格</span>
    <div class="seg" id="themeSeg">
      <button data-theme="morandi" aria-pressed="true">莫兰迪</button>
      <button data-theme="apple" aria-pressed="false">苹果</button>
    </div>
  </div>
</header>

<div id="stage" aria-label="无限画布"></div>

<div class="hint" id="hint">拖动 · 无限延展</div>

<button class="gear" id="gear" aria-label="墙体设置">
  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">
    <circle cx="12" cy="12" r="3.2"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z"/>
  </svg>
</button>

<aside class="panel" id="panel" aria-label="墙体设置">
  <div class="panel-top">
    <b>墙体设置</b>
    <button class="xbtn" id="panelClose" aria-label="关闭">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M18 6 6 18M6 6l12 12"/></svg>
    </button>
  </div>

  <div class="row">
    <label>遮罩</label>
    <div class="seg2" data-key="scrim">
      <button data-val="solid">实色</button>
      <button data-val="half">部分透明</button>
      <button data-val="clear">全透明</button>
    </div>
  </div>

  <div class="row">
    <label>每块窗数</label>
    <div class="seg2" data-key="density">
      <button data-val="1">1</button><button data-val="2">2</button>
      <button data-val="3">3</button><button data-val="4">4</button>
      <button data-val="5">5</button><button data-val="6">6</button>
    </div>
  </div>

  <div class="row">
    <label>信息条方向</label>
    <div class="seg2" data-key="dir">
      <button data-val="auto">自动</button>
      <button data-val="v">竖向</button>
      <button data-val="h">横向</button>
    </div>
  </div>

  <div class="row col">
    <label>信息条样式</label>
    <div class="styles" data-key="style">
      <button data-val="paper"><span class="sw paper"></span>主题纸色</button>
      <button data-val="print"><span class="sw print"></span>纯白印刷</button>
      <button data-val="ink"><span class="sw ink"></span>纯黑</button>
      <button data-val="frost"><span class="sw frost"></span>磨砂</button>
      <button data-val="hatch"><span class="sw hatch"></span>斜纹</button>
      <button data-val="none"><span class="sw none"></span>无底</button>
    </div>
  </div>

  <div class="row">
    <label>信息条始终透明</label>
    <button class="tgl" data-key="transparent" aria-pressed="false"><i></i></button>
  </div>

  <div class="row">
    <label>墙后画面模糊</label>
    <button class="tgl" data-key="blur" aria-pressed="false"><i></i></button>
  </div>

  <p class="panel-note">改完立即生效，刷新也会记住。</p>
</aside>

<div class="lb" id="lb" role="dialog" aria-modal="true">
  <div class="lb-bg" id="lbBg"></div>
  <div class="lb-card" id="lbCard">
    <div class="lb-img"><img id="lbImg" alt=""></div>
    <div class="lb-body">
      <h2 id="lbTitle">—</h2>
      <div class="lb-meta" id="lbMeta"></div>
      <div class="lb-acts">
        <button class="btn" id="lbDl">下载</button>
        <button class="btn ghost" id="lbClose">关闭</button>
      </div>
    </div>
  </div>
</div>

<script>
window.onerror=function(m,src,l,c){
  var d=document.createElement('pre');d.id='errbox';
  d.style.cssText='position:fixed;z-index:99999;left:0;right:0;bottom:0;background:#ffe4e1;color:#8b0000;font:12px/1.45 monospace;padding:8px;margin:0;white-space:pre-wrap';
  d.textContent='JS ERROR: '+m+' @'+l+':'+c;
  document.body.appendChild(d);
};
</script>
<script src="/static/gsap.min.js"></script>
<script>
(function(){
  'use strict';
  var g = gsap, $ = function(s,r){return (r||document).querySelector(s)}, $$ = function(s,r){return [].slice.call((r||document).querySelectorAll(s))};
  var reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------- cover pool ---------- */
  /* 数据来自 Flask：files = [{id,name,kind,size,size_bytes,dl_count,is_image,is_video,is_audio,...}] */
  var FILES = {{ files|tojson }};
  var IMGS = FILES.map(function(f){ return (f.is_image || f.is_video) ? ('/thumb/' + f.id) : ''; });
  var GLYPH = {'文档':'文','音频':'音','压缩包':'压','安装包':'包','影像':'影','图片':'图'};
  var TITLES = FILES.map(function(f){
    return [f.name || '未命名', (f.kind || '其他') + ' · ' + (f.size || ''), f.kind || '其他', f.id, !!(f.is_image || f.is_video)];
  });

  /* ---------- state ---------- */
  var stage = $('#stage');
  var tiles = [];
  var cam = {x: -120, y: -60};
  var size = 196, step = 208, cols = 0, rows = 0, bankW = 0, bankH = 0;
  var GAP = 0;
  var drag = {on:false, sx:0, sy:0, lx:0, ly:0, lt:0, vx:0, vy:0, moved:0, tw:null};
  var filter = 'all';

  function metrics(){
    var w = window.innerWidth, h = window.innerHeight;
    size = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--size')) || 196;
    step = size + GAP;
    cols = Math.ceil(w / step) + 2;
    rows = Math.ceil(h / step) + 2;
    bankW = cols * step;
    bankH = rows * step;
  }

  /* ---------- pattern: var-sized tiles that tile perfectly ---------- */
  function makePattern(nc, nr){
    var occ = [], y, x, i;
    for (y = 0; y < nr; y++){ occ.push([]); for (x = 0; x < nc; x++) occ[y][x] = false; }
    var sizes = [[2,2],[2,1],[1,2],[1,1],[1,1],[1,1],[2,1],[1,1]];
    var out = [];
    for (y = 0; y < nr; y++){
      for (x = 0; x < nc; x++){
        if (occ[y][x]) continue;
        var order = sizes.slice().sort(function(){ return Math.random() - .5; });
        var pick = null;
        for (i = 0; i < order.length; i++){
          var w = order[i][0], h = order[i][1];
          if (x + w > nc || y + h > nr) continue;
          var ok = true;
          for (var yy = y; yy < y + h && ok; yy++)
            for (var xx = x; xx < x + w; xx++){ if (occ[yy][xx]){ ok = false; break; } }
          if (ok){ pick = [w, h]; break; }
        }
        if (!pick) pick = [1, 1];
        for (var Y = y; Y < y + pick[1]; Y++)
          for (var X = x; X < x + pick[0]; X++) occ[Y][X] = true;
        out.push({x:x, y:y, w:pick[0], h:pick[1]});
      }
    }
    return out;
  }

  function emptyState(){
    if (FILES.length) return;
    var d = document.createElement('div');
    d.className = 'empty-note';
    d.textContent = '还没有已发布的文件。去「发布区」投一份，审核通过后就会出现在这里。';
    document.getElementById('stage').appendChild(d);
  }

  function build(){
    metrics();
    stage.innerHTML = '';
    tiles = [];

    var pattern = makePattern(cols, rows);
    for (var i = 0; i < pattern.length; i++){
      var p = pattern[i];
      var t = TITLES[i % TITLES.length];
      var big = (p.w >= 2 && p.h >= 2) ? ' big' : (p.w >= 2 ? ' wide' : '');

      var el = document.createElement('figure');
      el.className = 'tile' + big;
      el.dataset.type = t[2];
      el.dataset.idx = i;
      el.style.width = (p.w * step - GAP) + 'px';
      el.style.height = (p.h * step - GAP) + 'px';
      var src = IMGS[i % IMGS.length];
      var media = src
        ? '<img src="' + src + '" alt="" loading="lazy" draggable="false" onerror="this.parentNode.classList.add(\'noimg\');this.remove()">'
        : '<div class="glyph">' + (GLYPH[t[2]] || '他') + '</div>';
      el.innerHTML = media
        + '<span class="n">' + (101 + i) + '</span>'
        + '<figcaption class="cap"><b>' + t[0] + '</b><span>' + t[1] + '</span></figcaption>';
      stage.appendChild(el);

      tiles.push({ el: el, bx: p.x * step, by: p.y * step, data: t });
    }

    $('#count').textContent = (FILES.length || 0) + ' 份 · ' + tiles.length + ' 块';
    applyFilter(filter, false);
    render();
  }

  /* ---------- infinite wrap ---------- */
  function mod(v, m){ return ((v % m) + m) % m; }

  function render(){
    var w = window.innerWidth, h = window.innerHeight;
    for (var i = 0; i < tiles.length; i++){
      var t = tiles[i];
      var x = mod(t.bx + cam.x, bankW);
      var y = mod(t.by + cam.y, bankH);
      /* pull the overflowed ones to the other side so the lattice is seamless */
      if (x > w + size + 24) x -= bankW;
      if (y > h + size + 24) y -= bankH;
      t.el.style.transform = 'translate3d(' + x.toFixed(1) + 'px,' + y.toFixed(1) + 'px,0)';
      t.el.style.visibility = (x < -size - 40 || y < -size - 40) ? 'hidden' : 'visible';
    }
  }

  /* ---------- drag + inertia ---------- */
  function stopInertia(){ if (drag.tw){ drag.tw.kill(); drag.tw = null; } }

  stage.addEventListener('pointerdown', function(e){
    if (e.button !== 0 && e.pointerType === 'mouse') return;
    stopInertia();
    drag.on = true;
    drag.sx = drag.lx = e.clientX; drag.sy = drag.ly = e.clientY;
    drag.lt = performance.now(); drag.vx = drag.vy = 0; drag.moved = 0;
    document.body.classList.add('dragging');
    try { stage.setPointerCapture(e.pointerId); } catch(err){}
    var hint = $('#hint'); if (hint) hint.classList.add('gone');
  });

  stage.addEventListener('pointermove', function(e){
    if (!drag.on) return;
    var dx = e.clientX - drag.lx, dy = e.clientY - drag.ly;
    var now = performance.now(), dt = Math.max(1, now - drag.lt);
    drag.vx = dx / dt * 16; drag.vy = dy / dt * 16;
    drag.lx = e.clientX; drag.ly = e.clientY; drag.lt = now;
    drag.moved += Math.abs(dx) + Math.abs(dy);
    cam.x += dx; cam.y += dy;
    render();
    e.preventDefault();
  }, {passive:false});

  function endDrag(){
    if (!drag.on) return;
    drag.on = false;
    document.body.classList.remove('dragging');
    var vx = Math.max(-70, Math.min(70, drag.vx));
    var vy = Math.max(-70, Math.min(70, drag.vy));
    if (reduce || (Math.abs(vx) < 1.2 && Math.abs(vy) < 1.2)) return;
    var st = {x: vx, y: vy};
    drag.tw = g.to(st, {
      x: 0, y: 0, duration: 1.25, ease: 'power3.out',
      onUpdate: function(){ cam.x += st.x; cam.y += st.y; render(); }
    });
  }
  stage.addEventListener('pointerup', endDrag);
  stage.addEventListener('pointercancel', endDrag);
  stage.addEventListener('pointerleave', endDrag);

  /* wheel: convert vertical wheel into horizontal travel (like a canvas) */
  stage.addEventListener('wheel', function(e){
    e.preventDefault();
    stopInertia();
    var k = 1.1;
    cam.x -= (e.deltaX + e.deltaY) * k;
    cam.y -= e.deltaY * 0.15;
    render();
  }, {passive:false});

  /* keyboard */
  document.addEventListener('keydown', function(e){
    var d = 120;
    if (e.key === 'ArrowLeft'){ cam.x += d; render(); }
    else if (e.key === 'ArrowRight'){ cam.x -= d; render(); }
    else if (e.key === 'ArrowUp'){ cam.y += d; render(); }
    else if (e.key === 'ArrowDown'){ cam.y -= d; render(); }
    else if (e.key === 'Escape') hideLb();
  });

  /* ---------- click -> lightbox (drag must not trigger) ---------- */
  var movedAtDown = 0;
  stage.addEventListener('click', function(e){
    if (drag.moved > 10){ drag.moved = 0; return; }
    var t = e.target.closest('.tile'); if (t) showLb(tiles[+t.dataset.idx]);
  }, true);

  /* ---------- lightbox ---------- */
  var lb = $('#lb'), lbCard = $('#lbCard'), lbBg = $('#lbBg'), openLb = false;
  function showLb(t){
    if (!t) return;
    var fileId = t.data[3] || '';
    var src = IMGS[+t.el.dataset.idx % IMGS.length];
    var img = $('#lbImg');
    if (src){ img.src = src; img.style.display = 'block'; }
    else { img.style.display = 'none'; }
    var dl = $('#lbDl');
    if (dl && fileId) dl.dataset.id = fileId;
    $('#lbTitle').textContent = t.data[0];
    $('#lbMeta').innerHTML = t.data[1].split(' · ').map(function(x){ return '<span>' + x + '</span>'; }).join('')
      + '<span>#' + (101 + +t.el.dataset.idx) + '</span>';
    lb.classList.add('open'); openLb = true;
    if (reduce){ g.set(lbBg,{opacity:1}); g.set(lbCard,{opacity:1,scale:1,y:0}); return; }
    g.timeline({defaults:{ease:'power3.out'}})
      .to(lbBg, {opacity:1, duration:.3}, 0)
      .fromTo(lbCard, {opacity:0, scale:.94, y:16}, {opacity:1, scale:1, y:0, duration:.5, ease:'expo.out'}, .04);
  }
  function hideLb(){
    if (!openLb) return;
    openLb = false;
    if (reduce){ lb.classList.remove('open'); return; }
    g.to(lbCard, {opacity:0, scale:.96, duration:.26, ease:'power2.in'});
    g.to(lbBg, {opacity:0, duration:.3, ease:'power2.in', onComplete:function(){ lb.classList.remove('open'); }});
  }
  $('#lbClose').addEventListener('click', hideLb);
  lbBg.addEventListener('click', hideLb);
  $('#lbDl').addEventListener('click', function(){
    var id = this.dataset.id;
    if (id){ window.location.href = '/download/' + id; }
  });

  /* ---------- filter ---------- */
  var chips = $('#chips');
  if (chips){
    chips.addEventListener('click', function(e){
      var b = e.target.closest('.chip'); if (!b || b.dataset.type === filter) return;
      $$('#chips .chip').forEach(function(c){ c.setAttribute('aria-pressed','false'); });
      b.setAttribute('aria-pressed','true');
      filter = b.dataset.type;
      applyFilter(filter, true);
    });
  }
  function applyFilter(type, animate){
    var shown = 0;
    tiles.forEach(function(t){
      var on = type === 'all' || t.data[2] === type;
      t.el.classList.toggle('dim', !on);
      if (on) shown++;
    });
    $('#count').textContent = shown + ' / ' + tiles.length + ' 格';
    if (animate && !reduce){
      g.fromTo('.tile:not(.dim)', {opacity:.35}, {opacity:1, duration:.5, stagger:.006, ease:'power2.out'});
    }
  }

  /* ---------- theme ---------- */
  var seg = $('#themeSeg');
  if (seg){
    seg.addEventListener('click', function(e){
      var b = e.target.closest('button'); if (!b) return;
      $$('#themeSeg button').forEach(function(x){ x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); });
      document.documentElement.setAttribute('data-theme', b.dataset.theme);
    });
  }

  /* ---------- wall settings ---------- */
  var CFG = {scrim:'clear', density:'3', dir:'auto', style:'none', transparent:true, blur:false};
  var DEN = {'1':300,'2':244,'3':196,'4':164,'5':136,'6':112};

  function applyCfg(rebuild){
    var r = document.documentElement;
    r.style.setProperty('--scrim-a', CFG.scrim === 'solid' ? .92 : CFG.scrim === 'half' ? .62 : 0);
    r.setAttribute('data-capstyle', CFG.transparent ? 'none' : CFG.style);
    r.style.setProperty('--cap-blur', CFG.blur ? '9px' : '0px');
    document.body.classList.toggle('wall-dir-v', CFG.dir === 'v');

    if (rebuild){
      var px = DEN[CFG.density] || 196;
      r.style.setProperty('--size', px + 'px');
      r.style.setProperty('--step', (px + 10) + 'px');
      build();
    }
    try { localStorage.setItem('xs-wall', JSON.stringify(CFG)); } catch(e){}
  }

  function syncUI(){
    $$('[data-key]').forEach(function(group){
      var key = group.dataset.key;
      if (group.classList.contains('tgl')){
        group.setAttribute('aria-pressed', CFG[key] ? 'true' : 'false');
        return;
      }
      $$('button', group).forEach(function(b){
        b.setAttribute('aria-pressed', String(b.dataset.val) === String(CFG[key]) ? 'true' : 'false');
      });
    });
  }

  (function initCfg(){
    var saved = null;
    try { saved = JSON.parse(localStorage.getItem('xs-wall') || 'null'); } catch(e){}
    if (saved) for (var k in saved) if (k in CFG) CFG[k] = saved[k];
    var p = new URLSearchParams(location.search);
    if (p.get('scrim')) CFG.scrim = p.get('scrim');
    if (p.get('density')) CFG.density = p.get('density');
    if (p.get('dir')) CFG.dir = p.get('dir');
    if (p.get('style')) CFG.style = p.get('style');
    if (p.get('transparent')) CFG.transparent = true;
    if (p.get('blur')) CFG.blur = true;
  })();

  var panel = $('#panel');
  function openPanel(){
    panel.classList.add('open');
    if (!reduce) g.fromTo(panel, {opacity:0, y:12}, {opacity:1, y:0, duration:.42, ease:'expo.out'});
    else g.set(panel, {opacity:1, y:0});
  }
  function closePanel(){
    if (reduce){ panel.classList.remove('open'); return; }
    g.to(panel, {opacity:0, y:10, duration:.25, ease:'power2.in', onComplete:function(){ panel.classList.remove('open'); }});
  }
  $('#gear').addEventListener('click', function(){
    panel.classList.contains('open') ? closePanel() : openPanel();
  });
  $('#panelClose').addEventListener('click', closePanel);

  panel.addEventListener('click', function(e){
    var t = e.target.closest('.tgl');
    if (t){
      CFG[t.dataset.key] = !CFG[t.dataset.key];
      syncUI(); applyCfg(false);
      return;
    }
    var b = e.target.closest('[data-val]');
    if (!b) return;
    var key = b.parentElement.dataset.key;
    CFG[key] = b.dataset.val;
    syncUI();
    applyCfg(key === 'density');
  });

  /* ---------- boot ---------- */
  build();
  emptyState();
  applyCfg(true);
  syncUI();
  var hintEl = document.getElementById('hint');
  if (hintEl) setTimeout(function(){ hintEl.classList.add('gone'); }, 3200);
  if (new URLSearchParams(location.search).get('panel')) openPanel();
  if (!reduce){
    g.from('.tile', {opacity:0, scale:.92, duration:.7, stagger:{each:.008, from:'random'}, ease:'expo.out'});
    g.from('.hud > *', {y:14, opacity:0, duration:.7, stagger:.08, ease:'expo.out'}, .1);
  }

  var rt;
  window.addEventListener('resize', function(){
    clearTimeout(rt);
    rt = setTimeout(function(){ build(); }, 160);
  });

  /* deep link for screenshots: ?ox=-600&oy=-200 */
  var p = new URLSearchParams(location.search);
  if (p.get('ox') || p.get('oy')){
    cam.x = parseFloat(p.get('ox') || '0');
    cam.y = parseFloat(p.get('oy') || '0');
    render();
    var h2 = $('#hint'); if (h2) h2.classList.add('gone');
  }
})();
</script>
</body>
</html>
"""


def build_canvas_page():
    """返回可直接交给 render_template_string 的页面（Jinja 变量：files）。"""
    return CANVAS_HTML
