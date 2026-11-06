# -*- coding: utf-8 -*-
"""下载体验：不跳页、不刷新，点图标就地下载，图标上转圈显示进度。

做法：fetch + ReadableStream 边下边计数，完成后用 Blob + <a download> 落盘。
超大的文件（>400MB）或浏览器不支持流式读取时，回退成原来的 /download 链接。
"""

DL_CSS = r"""
<style id="dl-ux-css">
.dlbtn{
  position:absolute; right:6px; top:6px; z-index:6;
  width:26px; height:26px; border-radius:50%;
  display:grid; place-items:center; cursor:pointer; padding:0;
  background:rgba(0,0,0,.42); color:#fff; border:0;
  transition:background .2s, transform .2s;
}
.dlbtn:hover{background:rgba(0,0,0,.62); transform:scale(1.06)}
.dlbtn svg{width:13px; height:13px; display:block}
.dlbtn .ring{
  position:absolute; inset:0; border-radius:50%;
  background:conic-gradient(var(--green) calc(var(--p, 0) * 1%), rgba(255,255,255,.18) 0);
  -webkit-mask:radial-gradient(farthest-side, transparent calc(100% - 3px), #000 calc(100% - 3px));
  mask:radial-gradient(farthest-side, transparent calc(100% - 3px), #000 calc(100% - 3px));
  opacity:0; transition:opacity .2s;
}
.dlbtn.busy .ring{opacity:1}
.dlbtn.busy svg{opacity:.85}
.dlbtn.done{background:var(--green)}
.dlbtn.fail{background:#b42318}
/* 列表模式也给每个文件卡一个下载按钮 */
.file, .tile{position:relative}
.dlbtn.inline{position:static; margin-left:8px; background:var(--surface-2); color:var(--ink); width:30px; height:30px}
.dlbtn.inline:hover{background:var(--green); color:#fff}
</style>
"""

DL_JS = r"""
<script id="dl-ux-js">
(function(){
  'use strict';
  var MAX_STREAM = 400 * 1024 * 1024;   // 超过 400MB 不缓冲到内存

  function ring(el){ return el.querySelector('.ring'); }

  function setProgress(el, pct){
    if (ring(el)) ring(el).style.setProperty('--p', Math.max(0, Math.min(100, pct)).toFixed(1));
  }

  function saveBlob(blob, name){
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url; a.download = name || 'download';
    document.body.appendChild(a);
    a.click();
    setTimeout(function(){ URL.revokeObjectURL(url); a.remove(); }, 4000);
  }

  function inlineDownload(el, id, name){
    var url = '/download/' + encodeURIComponent(id);
    var total = 0, got = 0;
    el.classList.add('busy');
    setProgress(el, 0);

    fetch(url, {credentials: 'same-origin'})
      .then(function(res){
        if (!res.ok) throw new Error('HTTP ' + res.status);
        var len = parseInt(res.headers.get('Content-Length') || '0', 10);
        total = len;
        if (!res.body || !res.body.getReader || (len && len > MAX_STREAM)){
          // 回退：直接交给浏览器下载（不跳页）
          el.classList.remove('busy');
          var a = document.createElement('a');
          a.href = url; a.download = name || '';
          document.body.appendChild(a); a.click(); a.remove();
          el.classList.add('done');
          setTimeout(function(){ el.classList.remove('done'); }, 1500);
          return null;
        }
        var reader = res.body.getReader();
        var chunks = [];
        return (function pump(){
          return reader.read().then(function(r){
            if (r.done) return new Blob(chunks);
            chunks.push(r.value);
            got += r.value.length;
            if (total) setProgress(el, got / total * 100);
            else setProgress(el, Math.min(95, got / (8 * 1024 * 1024)));
            return pump();
          });
        })();
      })
      .then(function(blob){
        if (!blob) return;
        setProgress(el, 100);
        saveBlob(blob, name);
        el.classList.remove('busy');
        el.classList.add('done');
        setTimeout(function(){ el.classList.remove('done'); setProgress(el, 0); }, 1600);
      })
      .catch(function(){
        el.classList.remove('busy');
        el.classList.add('fail');
        setTimeout(function(){ el.classList.remove('fail'); }, 1600);
      });
  }

  function makeBtn(){
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'dlbtn';
    b.title = '下载';
    b.innerHTML = '<span class="ring"></span>'
      + '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">'
      + '<path d="M12 3v12m0 0 4-4m-4 4-4-4"/><path d="M4 19h16"/></svg>';
    b.addEventListener('click', function(e){
      e.stopPropagation();
      e.preventDefault();
      var host = b.closest('.tile, .file');
      if (!host || b.classList.contains('busy')) return;
      var id = host.dataset.id || host.dataset.fileId || '';
      var name = host.dataset.name || '';
      if (!id) return;
      inlineDownload(b, id, name);
    });
    return b;
  }

  function attach(){
    var hosts = document.querySelectorAll('#albumGrid .tile, #albumGrid .file');
    for (var i = 0; i < hosts.length; i++){
      var h = hosts[i];
      if (h.querySelector(':scope > .dlbtn')) continue;
      h.appendChild(makeBtn());
    }
  }

  attach();
  if (window.MutationObserver){
    var mo = new MutationObserver(function(){ clearTimeout(mo._t); mo._t = setTimeout(attach, 120); });
    var g = document.getElementById('albumGrid');
    if (g) mo.observe(g, {childList: true});
  }
})();
</script>
"""


def build_download_ux():
    return DL_CSS, DL_JS
