# -*- coding: utf-8 -*-
"""浏览器侧访客信息：能采的全采，一次性 POST /api/visit。

采不到的都留空，不会因为某个 API 不存在而报错。
"""

VISIT_JS = r"""
<script id="visit-enrich-js">
(function(){
  'use strict';
  function safe(fn, d){ try { var v = fn(); return v == null ? d : v; } catch(e){ return d; } }

  function guessModel(ua){
    var m = /Android[^;]*;\s*([^;)]+?)(?:\s+Build|\))/i.exec(ua || '');
    if (m && m[1]) return m[1].replace(/_/g, ' ').trim();
    if (/iPhone/i.test(ua)) return 'iPhone';
    if (/iPad/i.test(ua)) return 'iPad';
    if (/Macintosh/i.test(ua)) return 'Mac';
    if (/Windows NT/i.test(ua)) return 'Windows PC';
    if (/Linux/i.test(ua)) return 'Linux PC';
    return '';
  }

  function gpu(){
    try {
      var c = document.createElement('canvas');
      var gl = c.getContext('webgl') || c.getContext('experimental-webgl');
      if (!gl) return '';
      var ext = gl.getExtension('WEBGL_debug_renderer_info');
      if (!ext) return '';
      return String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) || '');
    } catch(e){ return ''; }
  }

  function collect(){
    var nav = window.navigator || {};
    var sc = window.screen || {};
    var c = nav.connection || nav.mozConnection || nav.webkitConnection || {};
    var tz = safe(function(){ return Intl.DateTimeFormat().resolvedOptions().timeZone; }, '');
    var off = safe(function(){ return -new Date().getTimezoneOffset() / 60; }, '');
    var langs = safe(function(){ return (nav.languages || [nav.language]).join('/'); }, nav.language || '');

    var body = {
      net: c.effectiveType || c.type || '',
      downlink: c.downlink ? c.downlink + ' Mbps' : '',
      rtt: c.rtt ? c.rtt + ' ms' : '',
      saveData: c.saveData ? '省流量开' : '',
      screen: (sc.width || 0) + '×' + (sc.height || 0),
      window: (window.innerWidth || 0) + '×' + (window.innerHeight || 0),
      avail: (sc.availWidth || 0) + '×' + (sc.availHeight || 0),
      dpr: window.devicePixelRatio || 1,
      depth: sc.colorDepth || '',
      touch: (nav.maxTouchPoints || 0) + ' 点',
      tz: tz + (off !== '' ? ' (UTC' + (off >= 0 ? '+' : '') + off + ')' : ''),
      mem: nav.deviceMemory ? nav.deviceMemory + ' GB' : '',
      cores: nav.hardwareConcurrency ? nav.hardwareConcurrency + ' 线程' : '',
      lang: langs,
      model: guessModel(nav.userAgent || ''),
      gpu: gpu(),
      dnt: nav.doNotTrack || '',
      cookies: nav.cookieEnabled ? '开' : '关',
      battery: ''
    };

    function send(){
      try {
        fetch('/api/visit', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          credentials: 'same-origin',
          body: JSON.stringify(body)
        }).catch(function(){});
      } catch(e){}
    }

    if (nav.getBattery){
      nav.getBattery().then(function(b){
        body.battery = Math.round(b.level * 100) + '%' + (b.charging ? ' 充电中' : '');
        send();
      }).catch(send);
    } else {
      send();
    }
  }

  if (document.readyState === 'complete') setTimeout(collect, 500);
  else window.addEventListener('load', function(){ setTimeout(collect, 500); });
})();
</script>
"""


def build_visit_enrich():
    return VISIT_JS
