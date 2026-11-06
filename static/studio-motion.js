/* Exchange Station motion: GSAP core, CustomEase and timelines. */
(function () {
  if (!window.gsap || !window.CustomEase) return;
  gsap.registerPlugin(CustomEase);
  CustomEase.create('studio', '.22,1,.36,1');
  const switcher = document.getElementById('motionBtn');
  const enabled = localStorage.getItem('exchange_motion') !== 'off';
  if (switcher) {
    switcher.textContent = enabled ? '动效：开启 · 点击关闭' : '动效：关闭 · 点击开启';
    switcher.setAttribute('aria-pressed', String(enabled));
    switcher.addEventListener('click', function () {
      localStorage.setItem('exchange_motion', enabled ? 'off' : 'on');
      location.reload();
    });
  }
  if (!enabled) return;
  document.documentElement.classList.add('gsap-motion');
  const drawer = document.getElementById('drawer');
  const backdrop = document.getElementById('drawerBackdrop');
  const toggle = document.getElementById('drawerBtn');
  const oldOpen = window.openDrawer, oldClose = window.closeDrawer;
  const oldShow = window.showTab, oldFilter = window.applyFilter;
  let menu, entered = false;
  if (drawer && backdrop && toggle) {
    const links = drawer.querySelectorAll('.drawer-navbtn');
    menu = gsap.timeline({ paused: true, defaults: { ease: 'studio', duration: .55 },
      onReverseComplete: function () { drawer.style.display = 'none'; backdrop.style.display = 'none'; }
    });
    menu.fromTo(backdrop, { autoAlpha: 0 }, { autoAlpha: 1, duration: .3 }, 0)
      .fromTo(drawer, { xPercent: -105 }, { xPercent: 0 }, 0)
      .fromTo(links, { y: 32, autoAlpha: 0 }, { y: 0, autoAlpha: 1, stagger: .055, duration: .42 }, .12)
      .to(toggle, { rotation: 8, scale: .94, duration: .55 }, 0);
    window.openDrawer = function () {
      drawer.style.display = 'flex'; backdrop.style.display = 'block';
      drawer.classList.add('open'); backdrop.classList.add('open'); menu.play();
    };
    window.closeDrawer = function () {
      drawer.classList.remove('open'); backdrop.classList.remove('open'); menu.reverse();
    };
  }
  function enterHero() {
    if (entered || !document.querySelector('#tab-published.active .studio-hero')) return;
    entered = true;
    gsap.timeline({ defaults: { ease: 'studio' } })
      .from('.studio-kicker', { y: 24, autoAlpha: 0, duration: .65 }, 0)
      .from('.studio-title-line span', { yPercent: 110, autoAlpha: 0, stagger: .12, duration: .9 }, .08)
      .from('.studio-copy', { y: 32, autoAlpha: 0, duration: .7 }, .34)
      .from('.studio-cta', { y: 28, scale: .92, autoAlpha: 0, duration: .7 }, .43)
      .from('.studio-proof span', { y: 18, autoAlpha: 0, stagger: .09, duration: .55 }, .54)
      .from('.studio-art-caption', { autoAlpha: 0, duration: .5 }, .2)
      .from('.studio-stack', { y: 100, rotation: -26, scale: .72, autoAlpha: 0, duration: 1.05 }, .24)
      .from('.studio-stamp', { x: 90, rotation: 24, scale: .7, autoAlpha: 0, duration: .8 }, .65);
  }
  if (typeof oldShow === 'function') window.showTab = function (name) { oldShow(name); enterHero(); };
  enterHero();
  function visibleTiles() {
    return Array.from(document.querySelectorAll('#albumGrid .tile')).filter(function (tile) {
      return tile.style.display !== 'none' && tile.getBoundingClientRect().top < innerHeight + 160;
    }).slice(0, 16);
  }
  if (typeof oldFilter === 'function') window.applyFilter = function () {
    const grid = document.getElementById('albumGrid');
    if (grid) gsap.killTweensOf(grid.querySelectorAll('.tile'));
    oldFilter();
    const tiles = visibleTiles();
    if (tiles.length) gsap.fromTo(tiles, { y: 42, scale: .94, autoAlpha: 0 },
      { y: 0, scale: 1, autoAlpha: 1, stagger: { each: .045, amount: .36 }, duration: .68,
        ease: 'studio', clearProps: 'transform,opacity,visibility' });
  };
  const seen = new WeakSet();
  const observer = new IntersectionObserver(function (entries) {
    const arriving = entries.filter(function (entry) { return entry.isIntersecting && !seen.has(entry.target); })
      .map(function (entry) { observer.unobserve(entry.target); seen.add(entry.target); return entry.target; });
    if (arriving.length) gsap.from(arriving.slice(0, 12), { y: 40, scale: .96, autoAlpha: 0,
      duration: .72, stagger: .055, ease: 'studio', clearProps: 'transform,opacity,visibility' });
  }, { threshold: .08 });
  document.querySelectorAll('#albumGrid .tile').forEach(function (tile) { observer.observe(tile); });
  const grid = document.getElementById('albumGrid');
  if (grid) new MutationObserver(function (mutations) {
    mutations.forEach(function (mutation) { mutation.addedNodes.forEach(function (node) {
      if (node.nodeType === 1 && node.classList.contains('tile')) observer.observe(node);
    }); });
  }).observe(grid, { childList: true });
  window.addEventListener('pagehide', function () {
    observer.disconnect(); if (menu) menu.kill();
    window.openDrawer = oldOpen; window.closeDrawer = oldClose;
    window.showTab = oldShow; window.applyFilter = oldFilter;
  }, { once: true });
})();
