/* Domino və şahmat açıqkən iPhone-da ekran sürüşməsin (rubber-band, iki barmaqla zoom) */
(function(){
  var y0 = 0;
  function isOpen(){
    var a = document.getElementById('domino'), b = document.getElementById('chess');
    return !!((a && !a.hidden) || (b && !b.hidden));
  }
  document.addEventListener('touchstart', function(e){ if(e.touches && e.touches.length) y0 = e.touches[0].clientY; }, {passive: true});
  document.addEventListener('touchmove', function(e){
    if(!isOpen()) return;
    var t = e.target;
    if(!t || !t.closest || !t.closest('#domino, #chess')) return;
    if(e.touches.length > 1){ e.preventDefault(); return; }
    var sc = t.closest('#dhand');
    if(sc && sc.scrollHeight > sc.clientHeight + 1){
      var dy = e.touches[0].clientY - y0;
      var atTop = sc.scrollTop <= 0, atBot = sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 1;
      if(!((dy > 0 && atTop) || (dy < 0 && atBot))) return;
    }
    e.preventDefault();
  }, {passive: false});
  document.addEventListener('gesturestart', function(e){ if(isOpen()) e.preventDefault(); });
  ['domino', 'chess'].forEach(function(id){
    var el = document.getElementById(id);
    if(el) el.addEventListener('scroll', function(){ el.scrollTop = 0; el.scrollLeft = 0; });
  });
})();
