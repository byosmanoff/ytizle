/* Instagram tipli naviqasiya: alt panel (ani keçid) + yanaşı səhifələr (barmağı izləyən sürüşdürmə) */
(function(){
  var menu = document.getElementById('menu'), prof = document.getElementById('profile');
  var ORDER = ['home','plans','friends','games','hist','settings','profile'], PAGES = ORDER.slice(0,6);
  var map = [['.mticket','home'],['#menumsg','home'],['.stats','home'],['#pwacard','home'],['#domCard','games'],['#durCard','games'],['#chCard','games'],
    ['#plansCard','plans'],['#statsCard','plans'],['#friendsCard','friends'],
    ['#hvids','hist','.card'],['#hrooms','hist','.card'],['#mquote','hist'],['#clearhist','hist'],['#setCards','settings']];
  map.forEach(function(m){
    var el = menu.querySelector(m[0]); if(!el) return;
    if(m[2]) el = el.closest(m[2]) || el;
    el.setAttribute('data-t', m[1]);
  });
  var btns = [].slice.call(document.querySelectorAll('#tabbar button')), cur = 'home', prev = 'home';
  var pager = null, track = null, setPg = null;

  /* telefon/planşet: səhifələri yanaşı düzürük; kompüter: köhnə kimi göstər/gizlət */
  var isMobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent) ||
    (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1) ||
    window.matchMedia('(hover:none) and (pointer:coarse)').matches;
  if(isMobile){
    document.body.classList.add('mob');
    pager = document.createElement('div'); pager.className = 'pager';
    track = document.createElement('div'); track.className = 'track';
    var pgs = {};
    PAGES.forEach(function(n){
      var pg = document.createElement('div'); pg.className = n === 'settings' ? 'pg pg-set' : 'pg'; pg.setAttribute('data-p', n);
      if(n !== 'settings') track.appendChild(pg);
      pgs[n] = pg;
    });
    [].slice.call(menu.querySelectorAll('[data-t]')).forEach(function(el){ pgs[el.getAttribute('data-t')].appendChild(el); });
    pager.appendChild(track);
    setPg = pgs.settings; pager.appendChild(setPg);
    menu.insertBefore(pager, document.getElementById('tabbar'));
    menu.classList.add('pager-on');
  } else {
    /* kompüter: naviqasiya yuxarıda sekmə zolağı olur, kartlar sekməyə görə göstərilir */
    var inv0 = document.getElementById('invites'), tb0 = document.getElementById('tabbar');
    if(inv0 && tb0) inv0.parentNode.insertBefore(tb0, inv0.nextSibling);
    menu.classList.add('tabs-on');
  }
  var idx = function(){ return cur === 'profile' ? 6 : PAGES.indexOf(cur); };
  function go(i, anim){
    if(!track || i > 4) return;
    track.style.transition = anim ? 'transform .26s cubic-bezier(.22,.9,.3,1)' : 'none';
    track.style.transform = 'translate3d(' + (-i * 100) + '%,0,0)';
  }
  function mark(t){ btns.forEach(function(b){ b.classList.toggle('on', b.getAttribute('data-go') === t); }); }
  function setTab(t, anim){
    if(t === 'profile'){
      if(cur !== 'profile' && cur !== 'settings') prev = cur;
      cur = 'profile'; mark('profile');
      if(prof.hidden && typeof openProfile === 'function') openProfile();
      return;
    }
    cur = t; menu.setAttribute('data-tab', t); mark(t);
    if(!prof.hidden) prof.hidden = true;
    if(pager) go(PAGES.indexOf(t), !!anim); else menu.scrollTop = 0;
  }
  btns.forEach(function(b){ b.addEventListener('click', function(){ setTab(b.getAttribute('data-go'), false); }); });
  menu.setAttribute('data-tab', 'home'); go(0, false);

  /* profil başqa yolla açılıb-bağlananda naviqasiya sinxron qalır */
  new MutationObserver(function(){
    if(!prof.hidden){ if(cur !== 'profile'){ if(cur !== 'settings') prev = cur; cur = 'profile'; mark('profile'); } }
    else if(cur === 'profile'){ cur = prev || 'home'; mark(cur); menu.setAttribute('data-tab', cur); if(pager) go(PAGES.indexOf(cur), false); }
  }).observe(prof, {attributes: true, attributeFilter: ['hidden']});
  var mav = document.getElementById('mav'), tav = document.getElementById('tav');
  function syncAv(){ tav.style.backgroundImage = mav.style.backgroundImage; tav.textContent = mav.style.backgroundImage ? '' : mav.textContent; }
  new MutationObserver(syncAv).observe(mav, {attributes: true, childList: true, characterData: true, subtree: true});
  syncAv();

  /* sürüşdürmə */
  if(pager){
    var sx = 0, sy = 0, st = 0, sOk = false, lock = 0, mode = '', d = 0, W = 360, raf = 0, i0 = 0;
    var hScroller = function(el){
      while(el && el !== document.body){
        if(el.scrollWidth > el.clientWidth + 4){
          var ox = getComputedStyle(el).overflowX;
          if(ox === 'auto' || ox === 'scroll') return true;
        }
        el = el.parentElement;
      }
      return false;
    };
    var paint = function(){
      raf = 0; if(!lock) return;
      if(mode === 'pager') track.style.transform = 'translate3d(calc(' + (-i0 * 100) + '% + ' + d + 'px),0,0)';
      else if(mode === 'set') setPg.style.transform = 'translate3d(' + d + 'px,0,0)';
      else prof.style.transform = 'translate3d(' + d + 'px,0,0)';
    };
    var onStart = function(e){
      var t = e.touches[0], tg = e.target;
      sOk = e.touches.length === 1 && !(tg.closest && tg.closest('input,textarea,select,[contenteditable]')) && !hScroller(tg);
      sx = t.clientX; sy = t.clientY; st = Date.now(); lock = 0; d = 0; W = Math.max(280, window.innerWidth);
    };
    var onMove = function(e){
      if(!sOk) return;
      var t = e.touches[0], dx = t.clientX - sx, dy = t.clientY - sy;
      if(!lock){
        if(Math.abs(dy) > 12 && Math.abs(dy) > Math.abs(dx)){ sOk = false; return; }
        if(!(Math.abs(dx) > 10 && Math.abs(dx) > Math.abs(dy) * 1.2)) return;
        lock = 1; mode = cur === 'profile' ? 'prof' : (cur === 'settings' ? 'set' : 'pager'); i0 = idx();
        track.style.transition = 'none'; prof.style.transition = 'none'; if(setPg) setPg.style.transition = 'none';
      }
      if(mode === 'pager'){
        /* Ayarlar sürüşdürmə zolağında deyil: Tarixçədən sola çəkəndə heç nə açılmır (yalnız yüngül rezin effekti) */
        var edge = (i0 === 0 && dx > 0) || (i0 === 4 && dx < 0);
        d = edge ? dx * .3 : dx;
      } else d = Math.max(0, dx);
      if(!raf) raf = requestAnimationFrame(paint);
    };
    var onEnd = function(){
      if(raf){ cancelAnimationFrame(raf); raf = 0; }
      if(!sOk || !lock){ sOk = false; return; }
      sOk = false; lock = 0;
      var v = Math.abs(d) / Math.max(1, Date.now() - st), far = Math.abs(d) > W * .25 || (v > .4 && Math.abs(d) > 30);
      if(mode === 'pager'){
        var dir = d < 0 ? 1 : -1, j = i0 + dir;
        if(far && j >= 0 && j <= 4) setTab(PAGES[j], true);
        else go(i0, true);
      } else if(mode === 'set'){
        /* Ayarlardan sağa sürüşdürmə = profilə qayıt */
        if(far){
          setPg.style.transition = 'none'; setPg.style.transform = '';
          setTab('profile', false);
          prof.style.transition = 'none'; prof.style.transform = 'translate3d(' + (-W) + 'px,0,0)';
          void prof.offsetWidth;
          prof.style.transition = 'transform .26s cubic-bezier(.22,.9,.3,1)'; prof.style.transform = '';
        } else {
          setPg.style.transition = 'transform .22s cubic-bezier(.22,.9,.3,1)'; setPg.style.transform = '';
          setTimeout(function(){ setPg.style.transition = ''; }, 240);
        }
      } else {
        if(far){
          prof.style.transition = 'transform .2s ease-out'; prof.style.transform = 'translate3d(' + W + 'px,0,0)';
          setTimeout(function(){ prof.style.transition = ''; prof.style.transform = ''; setTab(prev || 'hist', false); }, 200);
        } else {
          prof.style.transition = 'transform .22s cubic-bezier(.22,.9,.3,1)'; prof.style.transform = '';
          setTimeout(function(){ prof.style.transition = ''; }, 240);
        }
      }
    };
    [pager, prof].forEach(function(el){
      el.addEventListener('touchstart', onStart, {passive: true});
      el.addEventListener('touchmove', onMove, {passive: true});
      el.addEventListener('touchend', onEnd, {passive: true});
      el.addEventListener('touchcancel', function(){ if(raf){ cancelAnimationFrame(raf); raf = 0; } if(lock){ if(mode === 'pager') go(i0, true); else if(mode === 'set'){ setPg.style.transition = 'transform .2s ease-out'; setPg.style.transform = ''; } else { prof.style.transition = 'transform .2s ease-out'; prof.style.transform = ''; } } sOk = false; lock = 0; }, {passive: true});
    });
  }

  /* ilişmə olmasın: pəncərə dəyişəndə və ya tətbiqə qayıdanda səhifəni dəqiq yerinə otuzdur */
  var snap = function(){ if(pager && cur !== 'profile'){ go(PAGES.indexOf(cur), false); } };
  window.addEventListener('resize', snap);
  document.addEventListener('visibilitychange', snap);
  window.addEventListener('pageshow', snap);

  /* menyuya hər qayıdışda ana səhifədən başla */
  var _sm = window.showMenu;
  if(typeof _sm === 'function') window.showMenu = showMenu = function(){ _sm.apply(this, arguments); setTab('home', false); };
})();
