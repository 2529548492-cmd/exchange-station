# -*- coding: utf-8 -*-
"""右下角齿轮：卡片墙的显示设置（遮罩 / 窗数 / 信息条方向 / 信息条样式 / 模糊）。"""

GEAR_CSS = r"""
<style id="wall-gear-css">
#wallGear{
  position:fixed; right:16px; bottom:16px; z-index:70;
  width:42px; height:42px; border-radius:50%; display:none;
  place-items:center; cursor:pointer; color:var(--ink); padding:0;
  background:var(--surface); border:1px solid var(--line);
  box-shadow:0 14px 34px -22px rgba(0,0,0,.5);
}
body.wall-on #wallGear{display:grid}
#wallGear:active{transform:scale(.94)}
.wp{
  position:fixed; right:14px; bottom:70px; z-index:75; width:min(320px,calc(100vw - 28px));
  background:var(--surface); border:1px solid var(--line); border-radius:16px;
  padding:14px 16px 10px; color:var(--ink); display:none;
  box-shadow:0 34px 80px -46px rgba(0,0,0,.55);
}
.wp.open{display:block}
.wp-top{display:flex; align-items:center; justify-content:space-between; padding-bottom:10px; border-bottom:1px solid var(--line)}
.wp-top b{font-size:13px; font-weight:800}
.wp-x{width:26px; height:26px; border-radius:50%; border:0; cursor:pointer; background:var(--surface-2); color:var(--muted); line-height:1}
.wp-row{display:flex; align-items:center; justify-content:space-between; gap:10px; padding:10px 0; border-bottom:1px solid var(--line)}
.wp-row:last-of-type{border-bottom:0}
.wp-row label{font-size:12.5px; color:var(--muted); white-space:nowrap}
.wp-seg{display:flex; gap:2px; background:var(--surface-2); border-radius:999px; padding:2px}
.wp-seg button{height:26px; min-width:30px; padding:0 9px; border:0; border-radius:999px; background:transparent; color:var(--muted); font-size:11.5px; cursor:pointer}
.wp-seg button[aria-pressed="true"]{background:var(--surface); color:var(--ink); box-shadow:0 1px 2px rgba(0,0,0,.1)}
.wp-tgl{width:40px; height:23px; border-radius:999px; border:0; background:var(--line); position:relative; cursor:pointer}
.wp-tgl i{position:absolute; top:2px; left:2px; width:19px; height:19px; border-radius:50%; background:#fff; transition:transform .25s; box-shadow:0 1px 3px rgba(0,0,0,.25)}
.wp-tgl[aria-pressed="true"]{background:var(--green)}
.wp-tgl[aria-pressed="true"] i{transform:translateX(17px)}
.wp-note{margin:8px 0 2px; font-size:10px; color:var(--muted)}

/* 遮罩与信息条样式，由面板上的 CSS 变量驱动 */
#albumGrid.wall2 .tile::after{
  content:""; position:absolute; inset:0; z-index:1; pointer-events:none;
  background:linear-gradient(180deg, transparent 42%, rgba(0,0,0,var(--wall-scrim,.42)));
}
body.wall-cap-v #albumGrid.wall2 .tile::after{
  background:linear-gradient(90deg, transparent 34%, rgba(0,0,0,var(--wall-scrim,.42)));
}
body.wall-cap-v #albumGrid.wall2 .tileinfo{
  left:auto; right:0; top:0; bottom:0; width:auto; max-width:26%;
  padding:7px 5px; writing-mode:vertical-rl; text-orientation:mixed;
  display:flex; flex-direction:column; justify-content:flex-end;
  background-image:none;
}
body.wall-cap-paper #albumGrid.wall2 .tileinfo{ background:#F2EEE8; }
body.wall-cap-paper #albumGrid.wall2 .tile-name,
body.wall-cap-paper #albumGrid.wall2 .tile-meta{ color:#3C3833 !important; }
body.wall-cap-ink #albumGrid.wall2 .tileinfo{ background:rgba(17,16,15,.92); }
body.wall-cap-frost #albumGrid.wall2 .tileinfo{
  background:rgba(255,255,255,.18);
  -webkit-backdrop-filter:blur(12px) saturate(150%);
  backdrop-filter:blur(12px) saturate(150%);
}
body.wall-blur #albumGrid.wall2 .tile .tileimg{ filter:blur(2px) saturate(.9); }
</style>
"""

GEAR_HTML = (
    '<button id="wallGear" type="button" aria-label="\u5361\u7247\u5899\u8bbe\u7f6e">'
    '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">'
    '<circle cx="12" cy="12" r="3.2"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z"/>'
    '</svg></button>'
    '<aside class="wp" id="wallPanel">'
    '<div class="wp-top"><b>\u5361\u7247\u5899\u8bbe\u7f6e</b>'
    '<button class="wp-x" id="wpClose" type="button">\u00d7</button></div>'
    '<div class="wp-row"><label>\u906e\u7f69</label>'
    '<div class="wp-seg" data-key="scrim">'
    '<button data-val="0.75">\u6df1</button>'
    '<button data-val="0.42">\u4e2d</button>'
    '<button data-val="0">\u65e0</button></div></div>'
    '<div class="wp-row"><label>\u7a97\u53e3\u5927\u5c0f</label>'
    '<div class="wp-seg" data-key="density">'
    '<button data-val="1.25">\u5927</button>'
    '<button data-val="1">\u4e2d</button>'
    '<button data-val="0.8">\u5c0f</button>'
    '<button data-val="0.65">\u66f4\u5c0f</button></div></div>'
    '<div class="wp-row"><label>\u4fe1\u606f\u6761\u65b9\u5411</label>'
    '<div class="wp-seg" data-key="dir">'
    '<button data-val="h">\u6a2a\u5411</button>'
    '<button data-val="v">\u7ad6\u5411</button></div></div>'
    '<div class="wp-row"><label>\u4fe1\u606f\u6761\u6837\u5f0f</label>'
    '<div class="wp-seg" data-key="cap">'
    '<button data-val="none">\u65e0\u5e95</button>'
    '<button data-val="paper">\u7eb8\u8272</button>'
    '<button data-val="ink">\u7eaf\u9ed1</button>'
    '<button data-val="frost">\u78e8\u7802</button></div></div>'
    '<div class="wp-row"><label>\u80cc\u666f\u6a21\u7cca</label>'
    '<button class="wp-tgl" id="wpBlur" type="button" aria-pressed="false"><i></i></button></div>'
    '<p class="wp-note">\u6539\u5b8c\u7acb\u5373\u751f\u6548\uff0c\u4e0b\u6b21\u6253\u5f00\u8fd8\u8bb0\u5f97\u3002</p>'
    '</aside>'
)

GEAR_JS = r"""
<script id="wall-gear-js">
(function(){
  var gear = document.getElementById('wallGear');
  var panel = document.getElementById('wallPanel');
  if (!gear || !panel) return;

  var KEY = 'exchange-wall-cfg';
  var cfg = {scrim: '0.42', density: '1', dir: 'h', cap: 'none', blur: false};
  try {
    var saved = JSON.parse(localStorage.getItem(KEY) || 'null');
    if (saved) for (var k in saved) if (k in cfg) cfg[k] = saved[k];
  } catch(e){}

  function apply(){
    var r = document.documentElement;
    r.style.setProperty('--wall-scrim', cfg.scrim);
    document.body.classList.toggle('wall-cap-v', cfg.dir === 'v');
    document.body.classList.toggle('wall-cap-paper', cfg.cap === 'paper');
    document.body.classList.toggle('wall-cap-ink', cfg.cap === 'ink');
    document.body.classList.toggle('wall-cap-frost', cfg.cap === 'frost');
    document.body.classList.toggle('wall-blur', !!cfg.blur);
    try { localStorage.setItem(KEY, JSON.stringify(cfg)); } catch(e){}
    if (typeof window.__wallRelayout === 'function') window.__wallRelayout();
    var btns = panel.querySelectorAll('[data-key]');
    for (var i = 0; i < btns.length; i++){
      var key = btns[i].dataset.key;
      var kids = btns[i].querySelectorAll('button');
      for (var j = 0; j < kids.length; j++){
        kids[j].setAttribute('aria-pressed', String(kids[j].dataset.val) === String(cfg[key]) ? 'true' : 'false');
      }
    }
    var blurBtn = document.getElementById('wpBlur');
    if (blurBtn) blurBtn.setAttribute('aria-pressed', cfg.blur ? 'true' : 'false');
  }

  gear.addEventListener('click', function(){
    panel.classList.toggle('open');
  });
  document.getElementById('wpClose').addEventListener('click', function(){
    panel.classList.remove('open');
  });
  panel.addEventListener('click', function(e){
    if (e.target.id === 'wpBlur'){
      cfg.blur = !cfg.blur; apply(); return;
    }
    var b = e.target.closest('[data-val]');
    if (!b) return;
    var key = b.parentElement.dataset.key;
    cfg[key] = b.dataset.val;
    apply();
  });

  apply();
  window.__wallCfg = cfg;
})();
</script>
"""


def build_gear():
    return GEAR_CSS, GEAR_HTML, GEAR_JS
