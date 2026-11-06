# -*- coding: utf-8 -*-
"""主题切换（当前主题 / 莫兰迪）+ 卡片墙隐藏下载角标。"""

SKIN_JS = r"""
<script id="skin-js">
(function(){
  'use strict';
  var KEY = 'exchange-skin';
  function apply(name){
    var root = document.documentElement;
    if (name === 'morandi') root.setAttribute('data-skin','morandi');
    else root.removeAttribute('data-skin');
    try { localStorage.setItem(KEY, name); } catch(e){}
    var bs = document.querySelectorAll('.skin-btn');
    for (var i = 0; i < bs.length; i++){
      bs[i].setAttribute('aria-pressed', bs[i].dataset.skin === name ? 'true' : 'false');
    }
  }
  var saved = 'current';
  try { saved = localStorage.getItem(KEY) || 'current'; } catch(e){}
  if (saved === 'morandi') document.documentElement.setAttribute('data-skin','morandi');

  function bind(){
    var bs = document.querySelectorAll('.skin-btn');
    for (var i = 0; i < bs.length; i++){
      if (bs[i].dataset.bound) continue;
      bs[i].dataset.bound = '1';
      (function(b){
        b.addEventListener('click', function(){ apply(b.dataset.skin); });
      })(bs[i]);
    }
    var cur = document.documentElement.getAttribute('data-skin') === 'morandi' ? 'morandi' : 'current';
    var all = document.querySelectorAll('.skin-btn');
    for (var j = 0; j < all.length; j++){
      all[j].setAttribute('aria-pressed', all[j].dataset.skin === cur ? 'true' : 'false');
    }
  }
  bind();
  setTimeout(bind, 600);
  setTimeout(bind, 1500);
})();
</script>
"""

SKIN_CSS = r"""
<style id="skin-css2">
/* 卡片墙模式里不显示下载数量和下载按钮，这些放进详情页 */
body.wall-on .tile .tile-dl,
body.wall-on .tile .dlbtn{ display:none !important; }
</style>
"""


def build_skin():
    return SKIN_CSS, SKIN_JS
