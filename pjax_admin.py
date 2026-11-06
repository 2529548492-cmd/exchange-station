# -*- coding: utf-8 -*-
"""站长区登录不再整页刷新：fetch 提交 → 淡入替换 → 立即能用。

原来点「进入站长区」是一次表单 POST，服务器整页重渲染，所以会白屏半天。
这里改成：拦截提交、后台拿到页面、原地替换 body，带淡入淡出，跟切 tab 一样快。
"""

ADMIN_CSS = r"""
<style id="pjax-admin-css">
body.pjax-out{opacity:0; transform:translateY(6px); transition:opacity .18s ease, transform .18s ease}
body.pjax-in{animation:pjaxFade .34s cubic-bezier(.16,1,.3,1)}
@keyframes pjaxFade{ from{opacity:0; transform:translateY(6px)} to{opacity:1; transform:none} }
#adminLoginBusy{
  display:none; margin-top:10px; font-size:12px; color:var(--muted);
}
#adminLoginBusy.on{display:block}
</style>
"""

ADMIN_JS = r"""
<script id="pjax-admin-js">
(function(){
  'use strict';

  function runScripts(root){
    var list = root.querySelectorAll('script');
    for (var i = 0; i < list.length; i++){
      var old = list[i];
      if (old.dataset.ran) continue;
      old.dataset.ran = '1';
      var s = document.createElement('script');
      if (old.src) s.src = old.src;
      else s.textContent = old.textContent;
      old.parentNode.replaceChild(s, old);
    }
  }

  function swapIn(html){
    var doc = new DOMParser().parseFromString(html, 'text/html');
    var next = doc.body;
    if (!next) { location.href = '/admin'; return; }

    document.body.classList.add('pjax-out');
    setTimeout(function(){
      document.body.innerHTML = next.innerHTML;
      if (doc.title) document.title = doc.title;
      document.body.classList.remove('pjax-out');
      document.body.classList.add('pjax-in');
      setTimeout(function(){ document.body.classList.remove('pjax-in'); }, 400);
      window.scrollTo(0, 0);
      runScripts(document.body);
    }, 170);
  }

  function showBusy(form, on){
    var el = form.querySelector('#adminLoginBusy');
    if (!el){
      el = document.createElement('div');
      el.id = 'adminLoginBusy';
      el.textContent = '正在验证站长口令…';
      form.appendChild(el);
    }
    el.classList.toggle('on', !!on);
  }

  document.addEventListener('submit', function(e){
    var form = e.target;
    if (!form || form.getAttribute('action') !== '/admin/login') return;
    e.preventDefault();
    e.stopPropagation();

    var btn = form.querySelector('button[type="submit"]');
    if (btn){ btn.disabled = true; }
    showBusy(form, true);
    window.__pjaxBusy = true;

    fetch('/admin/login', {
      method: 'POST',
      body: new FormData(form),
      credentials: 'same-origin',
      redirect: 'follow'
    })
      .then(function(r){ return r.text(); })
      .then(function(html){
        if (html.indexOf('id="admin-main"') > -1 || html.indexOf('待审核') > -1){
          swapIn(html);
        } else {
          // 口令不对：就地提示，不跳页
          showBusy(form, false);
          if (btn) btn.disabled = false;
          var tip = form.querySelector('.admin-tip');
          if (!tip){
            tip = document.createElement('div');
            tip.className = 'admin-tip';
            tip.style.cssText = 'margin-top:10px;color:#b42318;font-size:12.5px';
            form.appendChild(tip);
          }
          tip.textContent = '站长口令不对，请再试一次';
        }
      })
      .catch(function(){
        showBusy(form, false);
        if (btn) btn.disabled = false;
      })
      .then(function(){ window.__pjaxBusy = false; });
  }, true);
})();
</script>
"""


def build_admin_pjax():
    return ADMIN_CSS, ADMIN_JS
