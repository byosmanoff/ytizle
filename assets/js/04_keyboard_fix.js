
/* Klaviatura açılanda ekran yuxarı sürüşməsin: otaq həmişə görünən sahəyə yapışır (Android + iPhone) */
(function(){
  var vv = window.visualViewport, root = document.documentElement, app = document.getElementById('app');
  function fit(){
    var zoomed = vv && vv.scale > 1.02;
    var h = (vv && !zoomed) ? vv.height : window.innerHeight;
    root.style.setProperty('--H', Math.round(h) + 'px');
    var se = document.scrollingElement;
    if(window.scrollY || (se && se.scrollTop)) window.scrollTo(0, 0);
    /* iPhone: brauzer görünən sahəni yuxarı sürüşdürür (offsetTop), otağı eyni qədər aşağı çəkirik */
    var t = (vv && !zoomed) ? Math.round(vv.offsetTop) : 0;
    if(app) app.style.transform = t > 0 ? 'translateY(' + t + 'px)' : '';
    root.style.setProperty('--VT', t + 'px');
  }
  fit();
  window.addEventListener('resize', fit);
  window.addEventListener('scroll', fit, {passive: true});
  window.addEventListener('orientationchange', function(){ setTimeout(fit, 250); });
  document.addEventListener('focusin', function(){ fit(); setTimeout(fit, 120); setTimeout(fit, 400); });
  document.addEventListener('focusout', function(){ setTimeout(fit, 120); setTimeout(fit, 400); });
  if(vv){ vv.addEventListener('resize', fit); vv.addEventListener('scroll', fit); }
})();
