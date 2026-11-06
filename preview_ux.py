# -*- coding: utf-8 -*-
"""详情卡片：加载进度条 + 下载按钮（带次数）+ 优先加载当前内容。

原来的预览是「缩略图 → 1024 图 → 原图」三阶段，肉眼只能看到由糊变清，
现在加一条真正可见的进度条，阶段推进时从 20% → 60% → 100%。

下载按钮复用流式下载（不跳页），成功后把卡片上的次数 +1。
"""

PV_CSS = r"""
<style id="preview-ux-css">
#previewCard{position:relative; overflow:hidden}
.pv-prog{
  position:absolute; left:0; right:0; top:0; height:3px; z-index:8;
  background:rgba(255,255,255,.12); overflow:hidden; border-radius:3px 3px 0 0;
}
.pv-prog i{
  display:block; height:100%; width:0;
  background:linear-gradient(90deg, var(--green), #8fd6ad);
  box-shadow:0 0 10px rgba(120,200,150,.55);
  transition:width .45s cubic-bezier(.16,1,.3,1);
}
.pv-prog.done{opacity:0; transition:opacity .5s .3s}

.pv-row{
  display:flex; align-items:center; gap:10px;
  margin-top:12px; padding-top:12px; border-top:1px solid var(--line);
  flex-wrap:wrap;
}
.pv-count{
  font-size:12.5px; color:var(--muted);
  display:inline-flex; align-items:center; gap:5px;
}
.pv-count b{color:var(--ink); font-variant-numeric:tabular-nums}
.pv-dl{
  margin-left:auto; display:inline-flex; align-items:center; gap:7px;
  height:38px; padding:0 16px; border-radius:11px; cursor:pointer; border:0;
  background:var(--green); color:#fff; font:700 13px/1 Manrope,"Microsoft YaHei",sans-serif;
  position:relative; overflow:hidden;
}
.pv-dl:hover{filter:brightness(1.06)}
.pv-dl:disabled{opacity:.8; cursor:default}
.pv-dl .ring2{
  position:absolute; inset:0; background:rgba(255,255,255,.22);
  transform-origin:left center; transform:scaleX(0);
  transition:transform .3s cubic-bezier(.16,1,.3,1);
}
.pv-dl span{position:relative; z-index:2}
.pv-dl.busy .ring2{transition:transform .2s linear}
.pv-dl.done{background:#2f8f5b}
</style>
"""

PV_JS = r"""
<script id="preview-ux-js">
(function(){
  'use strict';
  if (typeof window.openPreview !== 'function' || window.__pvUx) return;
  window.__pvUx = true;

  var nativeOpen = window.openPreview;

  function el(tag, cls){ var e = document.createElement(tag); if (cls) e.className = cls; return e; }

  function tileMeta(tile){
    if (!tile) return {};
    var dlEl = tile.querySelector('.tile-dl');
    var n = 0;
    if (dlEl){ var m = /(\d+)/.exec(dlEl.textContent || ''); if (m) n = parseInt(m[1], 10); }
    return {
      id: tile.dataset.id || tile.getAttribute('data-id') || '',
      name: tile.dataset.name || (tile.querySelector('.tile-name') || {}).textContent || '文件',
      dl: n
    };
  }

  function ensureProg(card){
    var p = card.querySelector('.pv-prog');
    if (!p){
      p = el('div', 'pv-prog');
      p.appendChild(el('i'));
      card.appendChild(p);
    }
    return p;
  }

  function setProg(card, pct){
    var bar = card.querySelector('.pv-prog i');
    if (bar) bar.style.width = Math.max(0, Math.min(100, pct)) + '%';
  }

  function attachDownload(card, meta){
    var row = card.querySelector('.pv-row');
    if (!row){
      row = el('div', 'pv-row');
      card.appendChild(row);
    }
    var count = el('span', 'pv-count');
    count.innerHTML = '下载 <b>' + (meta.dl || 0) + '</b> 次';
    var btn = el('button', 'pv-dl');
    btn.type = 'button';
    btn.innerHTML = '<span class="ring2"></span><span>下载</span>';
    row.innerHTML = '';
    row.appendChild(count);
    row.appendChild(btn);

    btn.addEventListener('click', function(){
      if (btn.disabled || !meta.id) return;
      btn.disabled = true;
      btn.classList.add('busy');
      var url = '/download/' + encodeURIComponent(meta.id);
      var total = 0, got = 0;

      fetch(url, {credentials: 'same-origin'})
        .then(function(res){
          if (!res.ok) throw new Error('HTTP ' + res.status);
          var len = parseInt(res.headers.get('Content-Length') || '0', 10);
          total = len;
          if (!res.body || !res.body.getReader || (len && len > 400 * 1024 * 1024)){
            var a = document.createElement('a');
            a.href = url; a.download = meta.name || '';
            document.body.appendChild(a); a.click(); a.remove();
            return null;
          }
          var reader = res.body.getReader(), chunks = [];
          return (function pump(){
            return reader.read().then(function(r){
              if (r.done) return new Blob(chunks);
              chunks.push(r.value);
              got += r.value.length;
              var pct = total ? got / total * 100 : Math.min(95, got / (8 * 1024 * 1024));
              btn.querySelector('.ring2').style.transform = 'scaleX(' + (pct / 100) + ')';
              btn.querySelector('span').textContent = '下载 ' + Math.round(pct) + '%';
              return pump();
            });
          })();
        })
        .then(function(blob){
          if (blob){
            var u = URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = u; a.download = meta.name || 'download';
            document.body.appendChild(a); a.click(); a.remove();
            setTimeout(function(){ URL.revokeObjectURL(u); }, 5000);
          }
          btn.classList.remove('busy');
          btn.classList.add('done');
          btn.querySelector('span').textContent = '已下载';
          var b = count.querySelector('b');
          if (b) b.textContent = String((meta.dl || 0) + 1);
          setTimeout(function(){
            btn.disabled = false;
            btn.classList.remove('done');
            btn.querySelector('span').textContent = '下载';
            btn.querySelector('.ring2').style.transform = 'scaleX(0)';
          }, 1500);
        })
        .catch(function(){
          btn.disabled = false;
          btn.classList.remove('busy');
          btn.querySelector('span').textContent = '重试';
        });
    });
  }

  window.openPreview = function(tile){
    var r = nativeOpen.apply(this, arguments);

    setTimeout(function(){
      var card = document.getElementById('previewCard');
      var body = document.getElementById('previewBody');
      if (!card) return;

      var meta = tileMeta(tile);
      var prog = ensureProg(card);
      prog.classList.remove('done');
      setProg(card, 18);

      /* 优先加载当前这张：提高优先级、立刻解码 */
      var img = body ? body.querySelector('img') : null;
      if (img){
        try { img.loading = 'eager'; img.fetchPriority = 'high'; } catch(e){}
        if (img.complete && img.naturalWidth) setProg(card, 100);
        else {
          img.addEventListener('load', function(){ setProg(card, 100); }, {once:true});
          img.addEventListener('error', function(){ setProg(card, 100); }, {once:true});
        }
      }

      /* 阶段推进：缩略图已显示 18% → 1 秒内 60% → 加载完 100% */
      setTimeout(function(){ setProg(card, 60); }, 220);
      setTimeout(function(){ if (img && img.complete) setProg(card, 100); }, 900);
      setTimeout(function(){ prog.classList.add('done'); }, 2200);

      attachDownload(card, meta);
    }, 20);

    return r;
  };
})();
</script>
"""


def build_preview_ux():
    return PV_CSS, PV_JS
