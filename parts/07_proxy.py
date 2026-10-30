# ---------------------------------------------------------------
#  Proxy
# ---------------------------------------------------------------
INJECT = r"""
(function(){
  var P = location.origin + '/p?u=';
  var NESTED = window.parent !== window.top;
  var T0 = Date.now();
  function abs(u){ try { return new URL(u, document.baseURI).href; } catch(e){ return null; } }
  function go(u, auto){ if(u && /^https?:/i.test(u)) window.top.postMessage({t:'nav', url:u, auto:!!auto}, '*'); }
  function same(a, b){
    function n(u){ return String(u).split('#')[0].replace(/\/+$/, ''); }
    return n(a) === n(b);
  }

  document.addEventListener('click', function(e){
    var a = e.target.closest && e.target.closest('a[href]');
    if(!a) return;
    var h = a.getAttribute('href');
    if(!h || /^(javascript|mailto|tel):/i.test(h)) return;
    e.preventDefault();
    if(h.charAt(0) === '#'){ var t = document.getElementById(h.slice(1)); if(t) t.scrollIntoView(); return; }
    if(!NESTED) go(abs(h));
  }, true);

  document.addEventListener('submit', function(e){
    var f = e.target;
    e.preventDefault();
    var meth = (f.method || 'get').toLowerCase();
    if(NESTED) return;
    /* Proxy yalnız GET bilir. Parolsuz POST formalarını (axtarış qutuları) GET kimi göndəririk,
       parollu formaları isə link kimi paylaşılmasın deyə bloklayırıq. */
    if(meth !== 'get' && (meth !== 'post' || f.querySelector('input[type=password]'))) return;
    var u = new URL(abs(f.getAttribute('action') || '') || document.baseURI);
    new FormData(f).forEach(function(v, k){ if(typeof v === 'string') u.searchParams.set(k, v); });
    go(u.href);
  }, true);

  window.open = function(u){ if(u && !NESTED) go(abs(u)); return null; };

  /* Sayt özü başqa ünvana yönləndirəndə (location.href, meta refresh və s.)
     səhifə proxy-dən çıxmasın deyə tuturuq və proxy ilə yenidən açırıq.
     Öz-özünə yönləndirmə və tez-tez yenilənmə dövrəsi kəsilir. */
  try {
    if(!NESTED && window.navigation && navigation.addEventListener){
      navigation.addEventListener('navigate', function(e){
        try {
          if(!e.cancelable || e.hashChange || e.downloadRequest !== null) return;
          if(e.destination.sameDocument) return;
          var d = e.destination.url;
          if(!/^https?:/i.test(d)) return;
          if(e.navigationType === 'reload'){ e.preventDefault(); return; }  /* sayt özü yenilənə bilməz */
          if(d.indexOf(location.origin + '/p?') === 0) return;
          e.preventDefault();
          if(same(d, document.baseURI)) return;
          go(d, true);  /* skriptin etdiyi yönləndirmə (auto) */
        } catch(x){}
      });
    }
  } catch(x){}

  function fixFrames(){
    document.querySelectorAll('iframe[src]').forEach(function(f){
      var s = f.getAttribute('src');
      if(!s || s.indexOf(P) === 0 || /^(about|javascript|data|blob):/i.test(s)) return;
      var u = abs(s);
      f._fx = (f._fx || 0) + 1;          /* sayt src-ni təkrar dəyişirsə sonsuz yenilənmə olmasın */
      if(f._fx > 3) return;
      if(u && /^https?:/i.test(u)) f.setAttribute('src', P + encodeURIComponent(u));
    });
  }

  var ignUntil = 0, bound = new WeakSet(), lead = false, rem = null;
  function ignoring(){ return Date.now() < ignUntil; }
  /* Qarşı tərəfdən gələn əmrin əks-sədasını (echo) göndərmirik */
  function isEcho(playing, time){
    if(!rem || Date.now() - rem.at > 6000) return false;
    var exp = rem.time + (rem.playing ? (Date.now() - rem.at) / 1000 : 0);
    return rem.playing === playing && Math.abs(time - exp) < 1.5;
  }
  function bind(v){
    if(bound.has(v)) return;
    bound.add(v);
    ['play', 'pause', 'seeked'].forEach(function(ev){
      v.addEventListener(ev, function(){
        if(ignoring() || isEcho(!v.paused, v.currentTime)) return;
        lead = true;
        window.top.postMessage({t:'v', e:ev, time:v.currentTime}, '*');
      });
    });
  }
  function mainVideo(){
    var a = [].slice.call(document.querySelectorAll('video'));
    a.sort(function(x, y){ return y.clientWidth * y.clientHeight - x.clientWidth * x.clientHeight; });
    return a[0];
  }
  setInterval(function(){
    fixFrames();
    document.querySelectorAll('video').forEach(bind);
  }, 1000);
  /* Aparıcı tərəf hər 3 san vaxtı göndərir, fərq 2 san-dən çoxdursa digər tərəf düzəldir */
  setInterval(function(){
    if(!lead || ignoring()) return;
    var v = mainVideo();
    if(v && !v.paused) window.top.postMessage({t:'v', e:'tick', time:v.currentTime}, '*');
  }, 3000);

  window.addEventListener('message', function(e){
    var m = e.data;
    if(!m || m.t !== 'cmd') return;
    var v = mainVideo();
    if(v){
      ignUntil = Date.now() + 700;
      lead = false;
      var playing = m.e === 'play' || m.e === 'tick' ? true : (m.e === 'pause' ? false : !v.paused);
      rem = {playing: playing, time: m.time, at: Date.now()};
      try {
        var tick = m.e === 'tick';
        if(Math.abs(v.currentTime - m.time) > (tick ? 2 : 0.7)) v.currentTime = m.time + (tick ? 0.4 : 0);
        if(m.e === 'play' || (tick && v.paused)){
          var p = v.play(); if(p && p.catch) p.catch(function(){});
        } else if(m.e === 'pause') v.pause();
      } catch(x){}
    }
    document.querySelectorAll('iframe').forEach(function(f){
      try { f.contentWindow.postMessage(m, '*'); } catch(x){}
    });
  });
  /* Sayt xəta kodu qaytarıbsa (403 və s.) boş ekran əvəzinə səbəbi göstər */
  if(window.__UP >= 400 && !NESTED){
    document.addEventListener('DOMContentLoaded', function(){
      var b = document.createElement('div');
      b.textContent = 'Sayt ' + window.__UP + ' cavabı qaytardı (server bloklanıb və ya giriş/yaş təsdiqi tələb edir)';
      b.style.cssText = 'position:fixed;left:0;right:0;top:0;z-index:2147483647;background:#b00020;color:#fff;font:13px system-ui;padding:6px 10px;text-align:center';
      document.body.appendChild(b);
    });
  }
})();
"""


def safe(url):
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            return False
        for info in socket.getaddrinfo(p.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.version == 6 and ip.ipv4_mapped:  # ::ffff:127.0.0.1 kimi hiylələr
                ip = ip.ipv4_mapped
            if not ip.is_global or ip.is_multicast:
                return False
        return True
    except Exception:
        return False
