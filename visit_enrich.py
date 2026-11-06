# -*- coding: utf-8 -*-
"""浏览器侧访客信息回填：网络类型/屏幕/时区/机型/电量 → POST /api/visit。"""

VISIT_JS = r"""
<script id="visit-enrich-js">
(function(){
  'use strict';
  function guessModel(ua){
    var m = /Android[^;]*;\s*([^;)]+?)(?:\s+Build|\))/i.exec(ua);
    if (m && m[1]) return m[1].replace(/_/g, ' ').trim();
    if (/iPhone/i.test(ua)) return 'iPhone';
    if (/iPad/i.test(ua)) return 'iPad';
    if (/Macintosh/i.test(ua)) return 'Mac';
    if (/Windows NT/i.test(ua)) return 'Windows PC';
    return '';
  }

  function collect(){
    var nav = navigator || {};
    var c = nav.connection || nav.mozConnection || nav.webkitConnection || {};
    var net = c.effectiveType || c.type || '';
    if (c.saveData) net += ' · 省流量';
    var mem = nav.deviceMemory || '';
    var cores = nav.hardwareConcurrency || '';
    var body = {
      net: net,
      downlink: c.downlink ? c.downlink + ' Mbps' : '',
      rtt: c.rtt ? c.rtt + ' ms' : '',
      screen: (window.screen ? screen.width + '×' + screen.height : ''),
      dpr: window.devicePixelRatio || 1,
      tz: (window.Intl && Intl.DateTimeFormat().resolvedOptions().timeZone) || '',
      mem: mem ? mem + ' GB' : '',
      cores: cores ? cores + ' 线程' : '',
      model: guessModel(nav.userAgent || ''),
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

  if (document.readyState === 'complete') setTimeout(collect, 400);
  else window.addEventListener('load', function(){ setTimeout(collect, 400); });
})();
</script>
"""


def build_visit_enrich():
    return VISIT_JS
