# -*- coding: utf-8 -*-
"""等待感优化：顶部进度条 + 图片骨架 + 桌面端才做「飞到中央」。

· 顶部细进度条：表单提交 / fetch / hash 切换时出现，结束自动收尾（苹果那种细线）
· 图片骨架：缩略图没加载完时先显示一层微光，加载完淡出
· 移动端不做 FLIP 放大（按你的要求）
"""

PROG_CSS = r"""
<style id="prog-ux-css">
#topProg{
  position:fixed; left:0; top:0; height:2px; width:0;
  z-index:9999; pointer-events:none;
  background:linear-gradient(90deg, var(--green), #7cc59a);
  box-shadow:0 0 8px rgba(120,200,150,.6);
  opacity:0; transition:opacity .3s ease;
  border-radius:0 2px 2px 0;
}
#topProg.on{opacity:1}

/* 图片骨架 */
.tileimg{background:var(--surface-2)}
.tileimg.loading{
  background-image:linear-gradient(100deg, var(--surface-2) 30%, var(--surface) 50%, var(--surface-2) 70%);
  background-size:200% 100%;
  animation:progShimmer 1.4s ease-in-out infinite;
}
@keyframes progShimmer{ from{background-position:120% 0} to{background-position:-20% 0} }

/* 下载图标在等的时候轻微呼吸，别让用户以为卡了 */
.dlbtn.busy{animation:dlPulse 1.1s ease-in-out infinite}
@keyframes dlPulse{ 0%,100%{transform:scale(1)} 50%{transform:scale(1.07)} }

@media (prefers-reduced-motion: reduce){
  #topProg{transition:none}
  .tileimg.loading{animation:none}
  .dlbtn.busy{animation:none}
}
</style>
"""

PROG_JS = r"""
<script id="prog-ux-js">
(function(){
  'use strict';
  var bar = document.createElement('div');
  bar.id = 'topProg';
  document.body.appendChild(bar);

  var timer = null, value = 0, visible = false;

  function paint(v){
    value = v;
    bar.style.width = v + '%';
  }
  function start(){
    if (visible) return;
    visible = true;
    paint(8);
    bar.classList.add('on');
    clearInterval(timer);
    timer = setInterval(function(){
      if (value < 88) paint(value + (88 - value) * 0.12);
    }, 180);
  }
  function done(){
    if (!visible) return;
    clearInterval(timer);
    paint(100);
    setTimeout(function(){
      bar.classList.remove('on');
      setTimeout(function(){ paint(0); visible = false; }, 320);
    }, 180);
  }

  /* 表单提交（上传、审核、登录等）自动显示 */
  document.addEventListener('submit', function(){ start(); }, true);
  window.addEventListener('beforeunload', function(){ start(); });

  /* 包一层 fetch：请求期间有进度条 */
  if (window.fetch){
    var nativeFetch = window.fetch.bind(window);
    window.fetch = function(){
      start();
      return nativeFetch.apply(null, arguments).then(function(r){
        done(); return r;
      }, function(e){ done(); throw e; });
    };
  }

  /* hash 切换（抽屉导航）也短暂显示一下，给人「有反应」的感觉 */
  window.addEventListener('hashchange', function(){ start(); setTimeout(done, 260); });

  /* 图片骨架 */
  function decorate(){
    var imgs = document.querySelectorAll('.tileimg');
    for (var i = 0; i < imgs.length; i++){
      var im = imgs[i];
      if (im.dataset.skelDone) continue;
      im.dataset.skelDone = '1';
      if (im.complete && im.naturalWidth) continue;
      im.classList.add('loading');
      im.addEventListener('load', function(){ this.classList.remove('loading'); }, {once:true});
      im.addEventListener('error', function(){ this.classList.remove('loading'); }, {once:true});
    }
  }
  decorate();
  if (window.MutationObserver){
    var g = document.getElementById('albumGrid');
    if (g){
      var mo = new MutationObserver(function(){ clearTimeout(mo._t); mo._t = setTimeout(decorate, 150); });
      mo.observe(g, {childList: true});
    }
  }
})();
</script>
"""


def build_progress_ux():
    return PROG_CSS, PROG_JS
