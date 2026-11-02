
/* Sayt daxili təsdiq pəncərəsi: brauzerin confirm() əvəzinə */
(function(){
  var box = document.getElementById('dlgbox'), tt = document.getElementById('dlgTitle'), tx = document.getElementById('dlgText'),
      no = document.getElementById('dlgNo'), yes = document.getElementById('dlgYes'), res = null, last = null;
  function key(e){ if(e.key === 'Escape') close(false); }
  function close(v){
    if(!res) return; var r = res; res = null; box.hidden = true;
    document.removeEventListener('keydown', key);
    try{ if(last && last.focus) last.focus(); }catch(e){}
    r(v);
  }
  window.wtConfirm = function(msg, o){
    o = o || {}; if(res) close(false);
    return new Promise(function(r){
      res = r; last = document.activeElement;
      tt.textContent = o.title || 'Əminsən?'; tx.textContent = msg;
      no.textContent = 'Ləğv et'; yes.textContent = o.ok || 'Bəli'; yes.className = o.danger ? 'danger' : '';
      box.hidden = false; document.addEventListener('keydown', key); yes.focus();
    });
  };
  yes.onclick = function(){ close(true); };
  no.onclick = function(){ close(false); };
  box.addEventListener('click', function(e){ if(e.target === box) close(false); });
})();
