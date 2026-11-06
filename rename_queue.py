# -*- coding: utf-8 -*-
"""发布时改名：上传队列里点文件名就能改，改完连上传用的名字一起变。"""

RENAME_CSS = r"""
<style id="rename-queue-css">
#filelist .qname{cursor:text; border-bottom:1px dashed transparent}
#filelist .qname:hover{border-bottom-color:var(--line)}
#filelist .qname.renaming{border-bottom-color:var(--green)}
.qname-edit{
  flex:1 1 auto; min-width:0; padding:6px 8px; font-size:14px;
  border-radius:8px; border:1px solid var(--green);
  background:var(--surface); color:var(--ink); outline:none;
}
.qname-tip{
  margin-top:6px; font-size:11.5px; color:var(--muted);
}
.qname-tip b{color:var(--green); font-weight:700}
</style>
"""

RENAME_JS = r"""
<script id="rename-queue-js">
(function(){
  'use strict';

  function queueArr(){
    return (typeof window.queue !== 'undefined' && window.queue) ? window.queue : null;
  }
  function refresh(){
    if (typeof window.syncInput === 'function') window.syncInput();
    if (typeof window.show === 'function') window.show();
  }

  function keepExt(oldName, newName){
    var m = /\.([a-z0-9]{1,8})$/i.exec(oldName || '');
    if (!m) return newName;
    var ext = m[1];
    if (new RegExp('\\.' + ext + '$', 'i').test(newName)) return newName;
    return newName + '.' + ext;
  }

  function startEdit(span){
    if (span.dataset.editing) return;
    var row = span.closest('.qrow');
    var idx = Array.prototype.indexOf.call(span.parentNode.parentNode.children, row);
    var q = queueArr();
    if (!q || !q[idx]) return;

    span.dataset.editing = '1';
    var oldName = q[idx].name;
    var input = document.createElement('input');
    input.className = 'qname-edit';
    input.value = oldName;
    input.spellcheck = false;

    span.style.display = 'none';
    span.parentNode.insertBefore(input, span);
    input.focus();
    input.select();

    function commit(save){
      var val = (input.value || '').trim();
      span.style.display = '';
      span.removeAttribute('data-editing');
      if (input.parentNode) input.parentNode.removeChild(input);
      if (!save || !val || val === oldName) return;
      var f = q[idx];
      var finalName = keepExt(oldName, val);
      q[idx] = new File([f], finalName, {type: f.type, lastModified: f.lastModified});
      refresh();
    }

    input.addEventListener('keydown', function(e){
      if (e.key === 'Enter'){ e.preventDefault(); commit(true); }
      else if (e.key === 'Escape'){ e.preventDefault(); commit(false); }
    });
    input.addEventListener('blur', function(){ commit(true); });
  }

  document.addEventListener('click', function(e){
    var span = e.target.closest('#filelist .qname');
    if (!span) return;
    e.preventDefault();
    e.stopPropagation();
    startEdit(span);
  }, true);

  /* 队列渲染出来后加一句提示 */
  function tip(){
    var list = document.getElementById('filelist');
    if (!list || !list.children.length) return;
    if (document.getElementById('qnameTip')) return;
    var p = document.createElement('div');
    p.id = 'qnameTip';
    p.className = 'qname-tip';
    p.innerHTML = '想改名？直接<b>点文件名</b>就能改，上传后用的就是新名字。';
    list.parentNode.insertBefore(p, list.nextSibling);
  }
  var t = setInterval(tip, 800);
  setTimeout(function(){ clearInterval(t); }, 12000);
})();
</script>
"""


def build_rename_queue():
    return RENAME_CSS, RENAME_JS
