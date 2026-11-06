/* GSAP official core/timeline/performance patterns; local assets, no runtime CDN. */
(function () {
  if (!window.gsap || !window.CustomEase) return;
  gsap.registerPlugin(CustomEase);
  CustomEase.create('studio', '.22,1,.36,1');
  const mm = gsap.matchMedia();
  mm.add('(prefers-reduced-motion: no-preference)', function () {
    document.documentElement.classList.add('gsap-motion');
    const drawer = document.getElementById('drawer');
    const backdrop = document.getElementById('drawerBackdrop');
    const toggle = document.getElementById('drawerBtn');
    const oldOpen = window.openDrawer, oldClose = window.closeDrawer;
    let menu;
    if (drawer && backdrop) {
      const links = drawer.querySelectorAll('#drawernav .drawer-navbtn');
      menu = gsap.timeline({paused:true, defaults:{ease:'studio',duration:.5},
        onReverseComplete:function(){ drawer.style.display='none'; backdrop.style.display='none'; }
      });
      menu.fromTo(backdrop,{autoAlpha:0},{autoAlpha:1,duration:.3},0)
        .fromTo(drawer,{x:0,xPercent:-105},{xPercent:0},0)
        .fromTo(links,{y:24,autoAlpha:0},{y:0,autoAlpha:1,stagger:.06,duration:.4},.1)
        .to(toggle,{rotation:8,duration:.5},0);
      window.openDrawer = function(){
        drawer.style.display='flex'; backdrop.style.display='block';
        drawer.classList.add('open'); backdrop.classList.add('open');
        menu.play();
      };
      window.closeDrawer = function(){
        drawer.classList.remove('open'); backdrop.classList.remove('open');
        menu.reverse();
      };
    }
    let entered=false;
    const oldShow=window.showTab;
    function enterHero(){
      if(entered || !document.querySelector('#tab-published.active .studio-hero'))return;
      entered=true;
      gsap.timeline({defaults:{ease:'studio',duration:.7}})
        .from('.studio-kicker',{y:24,autoAlpha:0,clearProps:'transform,opacity,visibility'},0)
        .from('.studio-hero h2',{y:32,autoAlpha:0,clearProps:'transform,opacity,visibility'},.08)
        .from('.studio-copy,.studio-cta,.studio-proof',{y:24,autoAlpha:0,stagger:.06,clearProps:'transform,opacity,visibility'},.16)
        .from('.studio-art',{y:32,autoAlpha:0,clearProps:'transform,opacity,visibility'},.12);
    }
    window.showTab=function(name){oldShow(name);enterHero();};
    enterHero();
    const observer=new IntersectionObserver(function(entries){
      const visible=entries.filter(function(e){return e.isIntersecting;}).map(function(e){observer.unobserve(e.target);return e.target;});
      if(visible.length)gsap.from(visible,{y:32,autoAlpha:0,duration:.7,stagger:{each:.06,amount:.3},ease:'studio',clearProps:'transform,opacity,visibility'});
    },{threshold:.15});
    document.querySelectorAll('#albumGrid .tile').forEach(function(el){observer.observe(el);});
    return function(){
      observer.disconnect(); if(menu)menu.kill();
      window.openDrawer=oldOpen;window.closeDrawer=oldClose;window.showTab=oldShow;
      document.documentElement.classList.remove('gsap-motion');
    };
  });
  window.addEventListener('pagehide',function(){mm.revert();},{once:true});
})();
