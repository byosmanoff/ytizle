/* Ayarlar tabı: bildirişlər, səs və video, hesab */
(function(){
  var $ = function(i){ return document.getElementById(i); };
  function get(k, d){ try{ var v = localStorage.getItem(k); return v === null ? d : v; }catch(e){ return d; } }
  function put(k, v){ try{ localStorage.setItem(k, v); }catch(e){} }

  /* xəta/uğur mesajı: profil açıqdırsa orada, yoxsa Ayarlarda göstərilir */
  var _pm = window.pmsg;
  if(typeof _pm === 'function') window.pmsg = function(t, ok){
    var p = $('profile');
    if(p && !p.hidden) return _pm(t, ok);
    var e = $('smsg'); if(e){ e.textContent = t || ''; e.dataset.ok = ok ? '1' : '0'; }
  };

  /* bildirişlər */
  var nt = $('setnotif'), nh = $('setnotifhint');
  window.wtNotifOff = function(){ return get('wt_notif', '1') === '0'; };
  function notifState(){
    if(!('Notification' in window)){ nt.checked = false; nt.disabled = true; nh.textContent = 'Bu brauzer bildirişləri dəstəkləmir.'; return; }
    var perm = Notification.permission;
    if(perm === 'denied'){ nt.checked = false; nh.textContent = 'Brauzerdə bağlanıb. Brauzer ayarlarından icazə ver.'; return; }
    nt.checked = perm === 'granted' && get('wt_notif', '1') !== '0';
    nh.textContent = nt.checked ? 'Yeni seans dəvətləri və tezliklə başlayacaq seanslar barədə xəbər alırsan.' : 'Söndürülüb. Seans xatırlatmaları gəlmir.';
  }
  nt.addEventListener('change', async function(){
    if(nt.checked){
      if(Notification.permission === 'default'){ try{ await Notification.requestPermission(); }catch(e){} }
      if(Notification.permission === 'granted') put('wt_notif', '1');
    } else put('wt_notif', '0');
    notifState();
    var b = $('plnotif'); if(b && 'Notification' in window) b.hidden = Notification.permission !== 'default';
  });
  document.addEventListener('click', function(e){
    if(e.target.closest && e.target.closest('#tabbar,#plnotif')) setTimeout(notifState, 300);
  });
  notifState();

  /* səs səviyyələri */
  function bindVol(id, labId, key, getEl){
    var r = $(id), l = $(labId);
    function apply(){ var el = getEl(); if(el){ try{ el.volume = Math.min(1, Math.max(0, (+r.value) / 100)); }catch(e){} } l.textContent = r.value + '%'; }
    r.value = get(key, '100'); apply();
    r.addEventListener('input', function(){ put(key, r.value); apply(); });
    return apply;
  }
  var av = bindVol('setvvol', 'setvvolv', 'wt_voicevol', function(){ return $('raudio'); });
  var vv = bindVol('setvidvol', 'setvidvolv', 'wt_vidvol', function(){ return $('vid'); });
  document.addEventListener('play', function(e){ if(e.target && (e.target.id === 'vid' || e.target.id === 'raudio')){ av(); vv(); } }, true);

  /* mikrofon */
  var sm = $('setmuted'), sc = $('setclean');
  sm.checked = get('wt_micmuted', '0') === '1';
  sc.checked = get('wt_micclean', '1') !== '0';
  sm.addEventListener('change', function(){ put('wt_micmuted', sm.checked ? '1' : '0'); });
  sc.addEventListener('change', function(){ put('wt_micclean', sc.checked ? '1' : '0'); });
  window.wtAudioCons = function(){ var c = sc.checked; return {echoCancellation: c, noiseSuppression: c, autoGainControl: c}; };
  window.wtStartMuted = function(){ return sm.checked; };

  /* hesab */
  $('setlogout').onclick = function(){ $('logout').click(); };
})();
