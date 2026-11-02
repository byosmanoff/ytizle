/* ---------- Səsli otaq (WebRTC; siqnal mövcud otaq kanalından keçir) ---------- */
// Bəzi mobil şəbəkələr yalnız STUN ilə qoşulmur. TURN serveri serverdə (app.py /api/ice) mühit dəyişənləri ilə qoşulur.
let ICE_SERVERS = [{urls: 'stun:stun.l.google.com:19302'}, {urls: 'stun:stun1.l.google.com:19302'}];
async function loadIce(){
  const ac = new AbortController(), to = setTimeout(() => ac.abort(), 3000);
  try{
    const r = await fetch('/api/ice', {cache: 'no-store', signal: ac.signal});
    if(r.ok){ const d = await r.json(); if(Array.isArray(d.servers) && d.servers.length) ICE_SERVERS = d.servers; }
  }catch(e){} finally{ clearTimeout(to); }
}
let voiceOn = false, peerVoice = false, peerId = '', peerName = '', voiceMuted = false, peerU = '', peerAv = 0;
let rtc = null, localStream = null, pendingIce = [], vctx = null;
let vLive = false, vFail = false, vStart = 0, vTimer = null, restarted = false;
const raud = $('raudio'), levels = new Map();
let vsheet = null;

const initial = n => (String(n || '').trim()[0] || '?').toLocaleUpperCase('az');
const mmss = ms => { const t = Math.floor(ms / 1000); return String(Math.floor(t / 60)).padStart(2, '0') + ':' + String(t % 60).padStart(2, '0'); };

function setVoiceUI(){
  const st = voiceOn ? (!peerVoice ? 'wait' : vFail ? 'fail' : vLive ? 'live' : 'conn') : (peerVoice ? 'invite' : 'off');
  const box = $('voice');
  box.hidden = st === 'off'; box.dataset.st = st;
  document.body.classList.toggle('invoice', voiceOn);
  setVp($('vpMe'), $('avMe'), account && account.username, myAvVer(), myName);
  setVp($('vpPeer'), $('avPeer'), peerU, peerAv, peerName);
  $('vpMe').classList.toggle('off', !voiceOn);
  $('vpPeer').classList.toggle('off', !peerVoice);
  $('vjoin').hidden = st !== 'invite';
  $('vmute').hidden = !voiceOn; $('vleave').hidden = !voiceOn;
  if(!voiceOn || !peerVoice) $('vunmute').hidden = true;
  $('vtext').textContent = {
    invite: peerNm() + ' səsli otaqdadır',
    wait: 'Qarşı tərəfi gözləyirəm...',
    conn: 'Qoşulur...',
    live: 'Səsli söhbət ' + mmss(vStart ? Date.now() - vStart : 0),
    fail: 'Səs qoşulmadı. Çıx və yenidən gir',
    off: ''
  }[st];
  if(vsheet){ if(!voiceOn) vsheet.close(); else vsheet.update(); }
}
function watchLevel(stream, el){
  stopLevel(el);
  try{
    vctx = vctx || new (window.AudioContext || window.webkitAudioContext)();
    const src = vctx.createMediaStreamSource(stream), an = vctx.createAnalyser();
    an.fftSize = 256; src.connect(an);
    const buf = new Uint8Array(an.frequencyBinCount);
    const iv = setInterval(() => {
      an.getByteTimeDomainData(buf);
      let sum = 0; for(let i = 0; i < buf.length; i++){ const x = (buf[i] - 128) / 128; sum += x * x; }
      const tk = Math.sqrt(sum / buf.length) > 0.035;
      el.classList.toggle('talk', tk);
      if(vsheet) vsheet.setSpeak(el.id === 'vpMe' ? 'me' : 'peer', tk && !(el.id === 'vpMe' && voiceMuted));
    }, 110);
    levels.set(el, {iv, src});
  }catch(e){}
}
function stopLevel(el){
  const l = levels.get(el);
  if(l){ clearInterval(l.iv); try{ l.src.disconnect(); }catch(e){} levels.delete(el); }
  el.classList.remove('talk');
  if(vsheet) vsheet.setSpeak(el.id === 'vpMe' ? 'me' : 'peer', false);
}
function voiceHello(re){
  send({t: 'voice', on: true, from: me, name: myName, muted: voiceMuted, re: !!re, u: (account && account.username) || '', av: myAvVer()});
}

async function joinVoice(){
  if(voiceOn) return;
  if(!window.RTCPeerConnection || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia){
    sysMsg('Mikrofon bu bağlantıda işləmir. Sayt https ilə açılmalıdır.'); return;
  }
  try{
    localStream = await navigator.mediaDevices.getUserMedia({audio: (window.wtAudioCons ? window.wtAudioCons() : {echoCancellation: true, noiseSuppression: true, autoGainControl: true}), video: false});
  }catch(e){
    sysMsg('Mikrofon icazəsi verilmədi. Brauzer ayarlarından icazə ver.'); return;
  }
  voiceOn = true; voiceMuted = false;
  $('vmute').classList.remove('off'); $('vpMe').classList.remove('muted');
  try{ vctx = vctx || new (window.AudioContext || window.webkitAudioContext)(); vctx.resume(); }catch(e){}
  watchLevel(localStream, $('vpMe'));
  voiceHello(false);
  sysMsg('Səsli otağa girdin. Qulaqlıq tax ki, videonun səsi mikrofona düşməsin');
  setVoiceUI();
  maybeCall();
  if(window.wtStartMuted && window.wtStartMuted()) $('vmute').click();
}
function leaveVoice(){
  if(!voiceOn) return;
  voiceOn = false;
  closeRtc();
  if(localStream){ localStream.getTracks().forEach(t => t.stop()); localStream = null; }
  stopLevel($('vpMe'));
  $('vpMe').classList.remove('muted');
  send({t: 'voice', on: false, from: me});
  sysMsg('Səsli otaqdan çıxdın');
  setVoiceUI();
}
function closeRtc(){
  if(rtc){ const r = rtc; rtc = null; try{ r.close(); }catch(e){} }
  pendingIce = []; vLive = false; vFail = false; vStart = 0; restarted = false;
  if(vTimer){ clearInterval(vTimer); vTimer = null; }
  raud.srcObject = null; stopLevel($('vpPeer')); $('vunmute').hidden = true;
}
function maybeCall(){
  if(!voiceOn || !peerVoice || !peerId || rtc || !localStream) return;
  makeRtc();
  if(me < peerId) offer(false);     // id-si kiçik olan təklif göndərir
}
function makeRtc(){
  const my = rtc = new RTCPeerConnection({iceServers: ICE_SERVERS});
  localStream.getTracks().forEach(t => my.addTrack(t, localStream));
  my.onicecandidate = e => {
    if(rtc === my && e.candidate) send({t: 'rtc', from: me, to: peerId, k: 'ice', d: e.candidate.toJSON()});
  };
  my.ontrack = e => {
    if(rtc !== my) return;
    raud.srcObject = e.streams[0];
    raud.play().then(() => { $('vunmute').hidden = true; }).catch(() => { $('vunmute').hidden = false; });
    watchLevel(e.streams[0], $('vpPeer'));
  };
  my.onconnectionstatechange = () => {
    if(rtc !== my) return;
    const st = my.connectionState;
    if(st === 'connected'){
      vLive = true; vFail = false;
      if(!vStart){ vStart = Date.now(); vTimer = setInterval(setVoiceUI, 1000); }
    } else if(st === 'disconnected'){
      vLive = false;
    } else if(st === 'failed'){
      vLive = false;
      if(me < peerId && !restarted){ restarted = true; offer(true); } else vFail = true;
    }
    setVoiceUI();
  };
}
async function offer(iceRestart){
  const my = rtc; if(!my) return;
  try{
    const o = await my.createOffer({iceRestart: !!iceRestart});
    await my.setLocalDescription(o);
    if(rtc !== my) return;
    send({t: 'rtc', from: me, to: peerId, k: 'offer', d: {type: o.type, sdp: o.sdp}});
  }catch(e){ vFail = true; setVoiceUI(); }
}
async function rtcMsg(m){
  if(!voiceOn || m.to !== me) return;
  try{
    if(m.k === 'offer'){
      peerId = String(m.from || peerId); peerVoice = true;
      if(!rtc) makeRtc();
      const my = rtc;
      await my.setRemoteDescription(m.d);
      for(const c of pendingIce.splice(0)) await my.addIceCandidate(c).catch(() => {});
      const a = await my.createAnswer();
      await my.setLocalDescription(a);
      if(rtc !== my) return;
      send({t: 'rtc', from: me, to: peerId, k: 'answer', d: {type: a.type, sdp: a.sdp}});
      setVoiceUI();
    } else if(m.k === 'answer'){
      if(!rtc) return;
      const my = rtc;
      await my.setRemoteDescription(m.d);
      for(const c of pendingIce.splice(0)) await my.addIceCandidate(c).catch(() => {});
    } else if(m.k === 'ice'){
      if(rtc && rtc.remoteDescription) await rtc.addIceCandidate(m.d).catch(() => {});
      else pendingIce.push(m.d);
    }
  }catch(e){ vFail = true; setVoiceUI(); }
}
function voiceMsg(m){
  if(m.t === 'rtc'){ rtcMsg(m); return; }
  if(m.t === 'vmute'){ $('vpPeer').classList.toggle('muted', !!m.on); if(vsheet) vsheet.update(); return; }
  if(m.on){
    if(!m.re && rtc) closeRtc();               // qarşı tərəf yenidən girib: köhnə bağlantını at
    peerVoice = true; peerId = String(m.from || ''); peerName = String(m.name || '').slice(0, 20);
    if(validAv(m.u, m.av)){ peerU = m.u; peerAv = +m.av; }
    $('vpPeer').classList.toggle('muted', !!m.muted);
    if(!m.re && !voiceOn) sysMsg(peerNm() + ' səsli otağa girdi');
    if(voiceOn && !m.re) voiceHello(true);     // mən də buradayam
    setVoiceUI();
    maybeCall();
  } else {
    if(m.from && peerId && m.from !== peerId) return;
    peerVoice = false; closeRtc();
    $('vpPeer').classList.remove('muted');
    sysMsg(peerNm() + ' səsli otaqdan çıxdı');
    setVoiceUI();
  }
}
$('vmic').onclick = joinVoice;
$('vjoin').onclick = joinVoice;
$('vleave').onclick = leaveVoice;
$('vunmute').onclick = () => { raud.play().then(() => { $('vunmute').hidden = true; }).catch(() => {}); };
$('vmute').onclick = () => {
  if(!localStream) return;
  voiceMuted = !voiceMuted;
  localStream.getAudioTracks().forEach(t => { t.enabled = !voiceMuted; });
  $('vmute').classList.toggle('off', voiceMuted);
  $('vmute').setAttribute('aria-label', voiceMuted ? 'Mikrofonu aç' : 'Mikrofonu söndür');
  $('vpMe').classList.toggle('muted', voiceMuted);
  send({t: 'vmute', on: voiceMuted});
  if(vsheet) vsheet.update();
};
vsheet = window.wtVoiceSheet({
  title: 'Səsli söhbət',
  sub: () => (voiceOn && peerVoice ? (vLive ? mmss(vStart ? Date.now() - vStart : 0) : 'Qoşulur...') : 'Qarşı tərəf gözlənilir'),
  members: () => {
    const out = [{id: 'me', name: myName || 'Mən', me: true, muted: voiceMuted}];
    if(peerVoice) out.push({id: 'peer', name: peerNm(), muted: $('vpPeer').classList.contains('muted'), st: vLive ? '' : 'Qoşulur...'});
    return out;
  },
  isMuted: () => voiceMuted,
  isSpeaker: () => !raud.muted,
  onMute: () => { $('vmute').click(); },
  onSpeaker: () => { raud.muted = !raud.muted; },
  onLeave: () => { leaveVoice(); },
  sendInvite: async (u) => {
    if(!token) return {ok: false, msg: 'Dəvət üçün hesaba daxil ol'};
    const r = await api('/api/invite', {method: 'POST', body: JSON.stringify({username: u, room})});
    if(r.ok) return {ok: true, msg: '@' + u + ' dəvət olundu. Ana menyuda görəcək.'};
    if(r.status === 403) return {ok: false, msg: 'Dəvət göndərilə bilmədi: gizlilik və ya blok məhdudiyyəti'};
    if(r.status === 404) return {ok: false, msg: 'Belə istifadəçi tapılmadı'};
    if(r.status === 429) return {ok: false, msg: 'Çox dəvət göndərdin, bir az sonra yenidən yoxla'};
    if(r.status === 400) return {ok: false, msg: 'Özünü dəvət edə bilməzsən'};
    return {ok: false, msg: 'Dəvət getmədi, yenidən cəhd et'};
  },
});
$('voice').addEventListener('click', e => { if(voiceOn && !e.target.closest('button')) vsheet.open(); });
window.addEventListener('pagehide', () => {
  if(!voiceOn || !room) return;
  try{ navigator.sendBeacon('/api/send', new Blob([JSON.stringify({room, id: me, sk, msg: {t: 'voice', on: false, from: me}})], {type: 'application/json'})); }catch(e){}
});


/* ---------- Otaqdan çıxış ---------- */
const wait = (p, ms) => Promise.race([p, new Promise(res => setTimeout(res, ms))]);
function openLeaveBox(){
  if(!room) return;
  $('lvCode').textContent = room.toUpperCase();
  const row = $('lvHostRow'), sel = $('lvHost'); sel.textContent = '';
  if(myRole === 'host' && peerList.length){   // host çıxarkən hostluğu istədiyi adama verə bilər
    const a = document.createElement('option'); a.value = ''; a.textContent = 'Avtomatik (ilk moderator)'; sel.appendChild(a);
    peerList.forEach(p => {
      const o = document.createElement('option'); o.value = p.id;
      o.textContent = (p.name || 'Qonaq') + (roleMap[p.id] === 'mod' ? ' · moderator' : ''); sel.appendChild(o);
    });
    row.hidden = false;
  } else row.hidden = true;
  $('leavebox').hidden = false; $('leaveNo').focus();
}
function closeLeaveBox(){ $('leavebox').hidden = true; }
async function leaveRoom(){
  if(!room) return;
  const code = room;
  const hostTo = (myRole === 'host' && !$('lvHostRow').hidden) ? $('lvHost').value : '';
  closeLeaveBox();
  if(voiceOn) leaveVoice();                      // səs otağına "çıxdım" xəbəri getsin
  await wait(sendQ, 1500);                       // növbədəki mesajlar göndərilsin
  pollGen++; try{ if(pollAbort) pollAbort.abort(); }catch(e){}
  if(hostTo){
    try{
      await wait(fetch('/api/host', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({room: code, id: me, target: hostTo}), keepalive: true
      }), 2000);
    }catch(e){}
  }
  try{
    await wait(fetch('/api/leave', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({room: code, id: me}), keepalive: true
    }), 2000);
  }catch(e){}
  // vəziyyəti sıfırla
  flushWatch(); myRole = 'viewer'; hostOnly = false; rv = -1; roleMap = {};
  $('rolebox').hidden = true; document.body.classList.remove('viewer-lock'); $('lock').hidden = false;
  room = ''; since = 0; pc = 0; connected = false; offline = false; isLocked = false; roomEpoch = null; peerSet = new Map(); peerList = [];
  queue = []; qRender(); $('qbox').hidden = true;
  curUrl = null; navAt = 0; lastAt = 0; ytPending = null; hist.length = 0; autoNavs = []; autoWarned = false;
  showMode('home');                              // video / sayt dayansın
  document.body.classList.remove('big');
  $('chat').textContent = ''; msgEls.clear(); rxs.clear(); rxClose(); ownState.clear(); pendingRead.clear(); clearReply();
  $('msg').value = ''; $('url').value = ''; clearUnread(); clearTyping(false);
  peerVoice = false; peerId = ''; peerName = ''; peerU = ''; peerAv = 0; closeRtc(); setVoiceUI();
  $('status').dataset.on = '0'; $('status').textContent = 'Qoşulur...';
  $('codein').value = ''; joinMsg(''); lockJoin(false);
  showMenu();                                    // ana menyuya qayıt
}
$('leave').onclick = openLeaveBox;
$('leaveNo').onclick = closeLeaveBox;
$('leaveYes').onclick = leaveRoom;
$('leavebox').addEventListener('click', e => { if(e.target === $('leavebox')) closeLeaveBox(); });
document.addEventListener('keydown', e => { if(e.key === 'Escape' && !$('leavebox').hidden) closeLeaveBox(); });

/* ---------- UI ---------- */
function openInput(){
  const raw = $('url').value.trim();
  if(!raw) return;
  const m = raw.match(/^(?:yt|youtube)\s+(.+)$/i);
  if(m){ ytSearch(m[1]); return; }
  if(ytSearchFromUrl(raw)) return;
  const u = norm(raw);
  if(u) loadPage(u, false); else flagBad(['url']);
}
function flagBad(ids){
  ids.forEach(id => { const el = $(id); el.classList.remove('bad'); void el.offsetWidth; el.classList.add('bad'); });
  $(ids[0]).focus();
}
$('codein').addEventListener('input', () => {
  $('codein').classList.remove('bad');
  syncJoin();
  joinMsg(codeVal() ? 'Kod yazılıb, "Otaq aç" bağlıdır. Yeni otaq üçün kodu sil.' : '');
});
function enterByCode(){
  if(joinBusy) return;
  const c = codeVal();
  if(!c){ joinMsg('Otaq kodunu yaz'); flagBad(['codein']); return; }
  enterRoom(c, false);
}
function createByName(){
  if(joinBusy) return;
  if(codeVal()){
    joinMsg('Kod yazılıb, "Otaq aç" bağlıdır. Girmək üçün "Kodla gir" bas, yeni otaq üçün kodu sil.');
    flagBad(['codein']); return;
  }
  enterRoom(null, true);
}
$('create').onclick = createByName;
$('enter').onclick = enterByCode;
$('codein').addEventListener('keydown', e => { if(e.key === 'Enter') enterByCode(); });
$('lock').onclick = () => lockCall(!isLocked);
$('copy').onclick = async () => {
  if(!room) return;
  const link = location.origin + '/?kod=' + encodeURIComponent(room.toUpperCase());
  if(navigator.share){
    try{ await navigator.share({title: 'Birlikdə İzlə', text: wtT('Otağa gəl, birlikdə izləyək'), url: link}); return; }
    catch(e){ if(e && e.name === 'AbortError') return; }
  }
  try{
    await navigator.clipboard.writeText(link);
    $('copy').innerHTML = ICON_CHECK; $('copy').classList.add('ok');
    setTimeout(() => { $('copy').innerHTML = ICON_SHARE; $('copy').classList.remove('ok'); }, 1500);
    sysMsg('Dəvət linki kopyalandı');
  }catch(e){
    let ok = false;
    try{
      const ta = document.createElement('textarea');
      ta.value = link; ta.setAttribute('readonly', ''); ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
      document.body.appendChild(ta); ta.select(); ta.setSelectionRange(0, link.length);
      ok = document.execCommand('copy'); ta.remove();
    }catch(_){}
    if(ok) sysMsg('Dəvət linki kopyalandı');
    else wtConfirm(link, {title: 'Linki kopyala', ok: 'Bağla'});
  }
};
$('load').onclick = openInput;
$('back').onclick = goBack;
$('homebtn').onclick = showHome;
$('big').onclick = () => document.body.classList.toggle('big');
$('url').addEventListener('keydown', e => { if(e.key === 'Enter') openInput(); });
$('send').onclick = sendChat;
$('msg').addEventListener('keydown', e => { if(e.key === 'Enter') sendChat(); });

/* ---------- Hesab (Google ilə giriş) ---------- */
const TOKEN_KEY = 'wt_token';
let account = null, token = '', pendingCode = '', gInit = false;
try{ token = localStorage.getItem(TOKEN_KEY) || ''; }catch(e){}
try{ pendingCode = (new URLSearchParams(location.search).get('kod') || '').toLowerCase().replace(/[^a-z0-9-]/g, '').slice(0, 30); }catch(e){}
if(token) $('join').style.display = 'none';        // giriş edilib: bilet bir anlıq da görünməsin
try{ if(!token && localStorage.getItem('wt_guest')==='1'){ account={name:localStorage.getItem('wt_guest_name')||'Qonaq',username:'',guest:true}; $('join').style.display='none'; /* wt_guest_boot */ } }catch(e){}


/* ---- Toast / reconnect / qonaq rejimi ---- */
window.wtToast = function(msg, ms){
  var el = document.getElementById('wtToast'); if(!el) return;
  el.textContent = String(msg || '');
  el.hidden = false;
  clearTimeout(window._wtToastT);
  window._wtToastT = setTimeout(function(){ el.hidden = true; }, ms || 3200);
};
window.wtReconnectBanner = function(txt, ok){
  var el = document.getElementById('wtReconnect'); if(!el) return;
  if(!txt){ el.hidden = true; return; }
  el.textContent = txt;
  el.classList.toggle('ok', !!ok);
  el.hidden = false;
};

window.wtErr = function(r, fallback){
  var f = fallback || 'Alınmadı, yenidən cəhd et';
  if(!r) return 'Əlaqə problemi, internetini yoxla';
  var s = r.status;
  if(s === 0 || s === undefined) return 'Əlaqə problemi, internetini yoxla';
  if(s === 400) return 'Məlumatlar düzgün deyil';
  if(s === 401) return 'Sessiya bitib, yenidən daxil ol';
  if(s === 403) return 'İcazə yoxdur və ya məhdudiyyət var';
  if(s === 404) return 'Tapılmadı';
  if(s === 409) return 'Artıq mövcuddur və ya ziddiyyət var';
  if(s === 429) return 'Çox sorğu. Bir az gözlə, sonra yenidən yoxla';
  if(s === 503) return 'Xidmət müvəqqəti əlçatmazdır';
  if(s >= 500) return 'Server xətası, bir az sonra yenidən cəhd et';
  return f;
};
window.wtShowGuide = function(force){
  try{ if(!force && localStorage.getItem('wt_guide_v1') === '1') return; }catch(e){}
  var el = document.getElementById('wtGuide'); if(!el) return;
  el.hidden = false;
};
window.wtHideGuide = function(save){
  var el = document.getElementById('wtGuide'); if(el) el.hidden = true;
  if(save){ try{ localStorage.setItem('wt_guide_v1', '1'); }catch(e){} }
};
/* Düymələr HTML-in sonunda ola bilər — delegation ilə bağlanır */
document.addEventListener('click', function(e){
  var t = e.target && e.target.closest ? e.target.closest('#wtGuideOk, #wtGuideSkip') : null;
  if(!t) return;
  e.preventDefault();
  window.wtHideGuide(true);
});
/* fon klikı ilə də bağlansın */
document.addEventListener('click', function(e){
  var g = document.getElementById('wtGuide');
  if(!g || g.hidden) return;
  if(e.target === g) window.wtHideGuide(true);
});
window.wtAskNotif = function(){
  if(!('Notification' in window)) return;
  if(Notification.permission !== 'default') return;
  try{ if(localStorage.getItem('wt_notif_ask') === '1') return; }catch(e){}
  var el = document.getElementById('wtNotifAsk'); if(!el) return;
  el.hidden = false;
};
window.wtNotifDone = function(allow){
  var el = document.getElementById('wtNotifAsk'); if(el) el.hidden = true;
  try{ localStorage.setItem('wt_notif_ask','1'); }catch(e){}
  if(allow){
    (async function(){
      try{ await Notification.requestPermission(); }catch(e){}
      try{ if(typeof pushSync === 'function') pushSync(true); }catch(e){}
    })();
  }
};
document.addEventListener('click', function(e){
  var t = e.target && e.target.closest ? e.target.closest('#wtNotifYes, #wtNotifNo') : null;
  if(!t) return;
  e.preventDefault();
  window.wtNotifDone(t.id === 'wtNotifYes');
});

window.wtIsGuest = function(){
  try{ return !token && localStorage.getItem('wt_guest') === '1'; }catch(e){ return false; }
};
window.wtSetGuest = function(on, name){
  try{
    if(on){
      localStorage.setItem('wt_guest', '1');
      if(name) localStorage.setItem('wt_guest_name', String(name).slice(0, 20));
    }else{
      localStorage.removeItem('wt_guest');
    }
  }catch(e){}
};
function wtGuestEnter(){
  var inp = document.getElementById('guestName');
  var nm = ((inp && inp.value) || '').trim().slice(0, 20) || 'Qonaq';
  if(nm.length < 1) nm = 'Qonaq';
  window.wtSetGuest(true, nm);
  token = '';
  account = {name: nm, username: '', guest: true};
  try{ localStorage.removeItem(TOKEN_KEY); }catch(e){}
  try{ document.body.classList.add('guest-mode'); }catch(e){}
  var j = document.getElementById('join');
  if(j) j.style.display = 'none';
  myName = nm;
  showMenu();
  try{ window.wtToast('Qonaq rejimindəsən — otaq aça bilərsən', 3500); }catch(e){}
}
(function(){
  try{
    var gn = localStorage.getItem('wt_guest_name');
    var inp = document.getElementById('guestName');
    if(gn && inp && !inp.value) inp.value = gn;
  }catch(e){}
  var btn = document.getElementById('guestGo');
  if(btn) btn.addEventListener('click', function(e){ e.preventDefault(); wtGuestEnter(); });
  var gin = document.getElementById('guestName');
  if(gin) gin.addEventListener('keydown', function(e){ if(e.key === 'Enter'){ e.preventDefault(); wtGuestEnter(); } });
})();


function wtGuestGoAuth(mode){
  try{ window.wtSetGuest(false); }catch(e){}
  try{ localStorage.removeItem('wt_guest'); }catch(e){}
  try{ document.body.classList.remove('guest-mode'); }catch(e){}
  account = null; token = '';
  try{ if($('profile')) $('profile').hidden = true; }catch(e){}
  showLogin();
  try{
    if(mode === 'reg') setEmode('reg');
    else if(mode === 'login') setEmode('login');
    else setEmode('login');
  }catch(e){}
  try{ initGoogle(); }catch(e){}
  if(mode === 'google'){
    try{ window.wtToast && window.wtToast('Aşağıda Google düyməsindən davam et'); }catch(e){}
    setTimeout(function(){
      var g = document.getElementById('gbtn');
      if(g) try{ g.scrollIntoView({behavior:'smooth', block:'center'}); }catch(e){}
    }, 300);
  }
}
function wtBindGuestAuth(){
  function bind(id, mode){
    var el = document.getElementById(id);
    if(el && !el._guestBound){ el._guestBound = 1; el.addEventListener('click', function(e){ e.preventDefault(); wtGuestGoAuth(mode); }); }
  }
  bind('guestToGoogle', 'google');
  bind('guestToReg', 'reg');
  bind('guestToLogin', 'login');
  bind('setGuestGoogle', 'google');
  bind('setGuestReg', 'reg');
  bind('setGuestLogin', 'login');
}
wtBindGuestAuth();

function loginMsg(t){ $('joinmsg').textContent = t; }
const AUTH_PATHS = ['/api/google', '/api/login', '/api/register', '/api/verify', '/api/resend', '/api/forgot', '/api/reset'];
async function api(path, o){
  o = o || {};
  const hd = Object.assign({'X-Token': token, 'X-Lang': (window.wtLang ? window.wtLang() : 'az')}, o.headers || {});
  if(o.body) hd['Content-Type'] = 'application/json';
  const r = await fetch(path, Object.assign({}, o, {headers: hd, cache: 'no-store'}));
  if(r.status === 401 && token && !AUTH_PATHS.includes(path)){ forgetLogin(); throw new Error('401'); }
  return r;
}
function forgetLogin(){
  try{ stopStream(); }catch(e){}
  token = ''; account = null;
  try{ localStorage.removeItem(TOKEN_KEY); localStorage.removeItem('wt_guest'); }catch(e){}
  try{ document.body.classList.remove('guest-mode'); }catch(e){}
  try{ if(window.google && google.accounts) google.accounts.id.disableAutoSelect(); }catch(e){}
  if(!room) showLogin();                             // otaqdadırsa söhbət kəsilməsin
}
function showLogin(){
  $('app').style.display = 'none'; $('menu').style.display = 'none'; $('profile').hidden = true;
  const j = $('join'); j.classList.remove('tearing'); j.style.display = '';
  setEmode('login'); $('pw').value = ''; $('ecode').value = '';
  $('dq').textContent = DAILY[Math.floor(Date.now() / 864e5) % DAILY.length];
  initFloaties(); startLive();
  initGoogle();
}
async function initGoogle(){
  if(gInit) return;
  gInit = true;
  let cid = '';
  try{ const r = await fetch('/api/config', {cache: 'no-store'}); cid = (await r.json()).google_client_id || ''; }catch(e){}
  if(!cid){ gInit = false; loginMsg('Google girişi hələ qurulmayıb, e-poçt ilə daxil ola bilərsən.'); return; }
  const s = document.createElement('script');
  s.src = 'https://accounts.google.com/gsi/client'; s.async = true;
  s.onload = () => {
    if(!window.google || !google.accounts || !google.accounts.id){ gInit = false; loginMsg('Google yüklənmədi, yenidən yoxla.'); return; }
    google.accounts.id.initialize({client_id: cid, callback: onGoogle, auto_select: false});
    google.accounts.id.renderButton($('gbtn'), {theme: 'outline', size: 'large', shape: 'pill', text: 'continue_with',
      width: Math.min(316, $('gbtn').parentElement.clientWidth || 316)});
  };
  s.onerror = () => { gInit = false; s.remove(); loginMsg('Google yüklənmədi, internetini yoxla.'); };
  document.head.appendChild(s);
}
async function onGoogle(resp){
  loginMsg('Daxil olunur...');
  try{
    const r = await api('/api/google', {method: 'POST', body: JSON.stringify({credential: resp.credential})});
    if(!r.ok) throw new Error('google ' + r.status);
    finishLogin(await r.json());
  }catch(e){ loginMsg('Giriş alınmadı, yenidən cəhd et.'); }
}
function finishLogin(d){
  token = d.token; account = {name: d.name};
  try{ localStorage.setItem(TOKEN_KEY, token); }catch(e){}
  $('pw').value = '';
  afterLogin(true);
  loadProfile();
}
function afterLogin(tear){
  myName = account.name;
  try{ localStorage.removeItem('wt_guest'); document.body.classList.remove('guest-mode'); }catch(e){}
  showMenu();                                        // menyu bilet cırılanda arxada görünsün
  try{ sendPresence(); }catch(e){}
  if(tear) tearTicket(); else $('join').style.display = 'none';
  if(pendingCode){                                   // dəvət linki ilə gəlib: birbaşa otağa
    const c = pendingCode; pendingCode = '';
    try{ history.replaceState(null, '', location.pathname); }catch(e){}
    enterRoom(c, false);
  }
}

/* ---------- E-poçt ilə giriş / qeydiyyat / təsdiq / şifrə bərpası ---------- */
let emode = 'login';
function setEmode(m, msg){
  emode = m;
  const show = (id, v) => { $(id).hidden = !v; };
  const codeStep = m === 'verify' || m === 'reset';
  show('ename', m === 'reg');
  show('euser', m === 'reg'); show('euserhint', m === 'reg');
  show('ecode', codeStep);
  show('pw', m === 'login' || m === 'reg' || m === 'reset');
  show('eforgot', m === 'login');
  show('eresend', codeStep);
  $('em').readOnly = codeStep;
  $('esubmit').textContent = {login: 'Daxil ol', reg: 'Qeydiyyatdan keç', verify: 'Təsdiq et', forgot: 'Kod göndər', reset: 'Şifrəni yenilə'}[m];
  $('etoggle').textContent = m === 'login' ? 'Hesabın yoxdur? Qeydiyyatdan keç' : m === 'reg' ? 'Artıq hesabın var? Daxil ol' : 'Geri qayıt';
  $('pw').autocomplete = m === 'login' ? 'current-password' : 'new-password';
  $('pw').placeholder = m === 'reset' ? 'Yeni şifrə (ən azı 6 simvol)' : m === 'reg' ? 'Şifrə (ən azı 6 simvol)' : 'Şifrə';
  if(!codeStep) $('ecode').value = '';
  if(m === 'reg') euserCheck(); else ehint('');
  loginMsg(msg || '');
}
$('etoggle').onclick = () => setEmode(emode === 'login' ? 'reg' : 'login');
$('eforgot').onclick = () => setEmode('forgot');
['ename', 'euser', 'em', 'ecode', 'pw'].forEach(id => $(id).addEventListener('input', () => $(id).classList.remove('bad')));
/* qeydiyyatda istifadəçi adı: canlı yoxlama */
const UNAME_RE = /^[a-z0-9][a-z0-9._]{1,18}[a-z0-9]$/;
let euTimer = null, euSeq = 0, euOk = false;
function ehint(t, cls){ const e = $('euserhint'); e.textContent = t || ''; e.className = 'ehint' + (cls ? ' ' + cls : ''); }
function euserCheck(){
  const el = $('euser'), v = el.value.toLowerCase().replace(/[^a-z0-9._]/g, '');
  if(v !== el.value) el.value = v;
  euOk = false; clearTimeout(euTimer);
  if(!v){ ehint('Dostların səni bu adla tapacaq. Sonra dəyişə bilərsən.'); return; }
  if(!UNAME_RE.test(v) || v.includes('..')){ ehint('3-20 simvol: hərf, rəqəm, nöqtə, _ (əvvəl və sonda nöqtə/_ olmasın)', 'err'); return; }
  ehint('Yoxlanılır...');
  const my = ++euSeq;
  euTimer = setTimeout(async () => {
    try{
      const r = await fetch('/api/username/available?u=' + encodeURIComponent(v), {cache: 'no-store'});
      if(!r.ok) throw new Error('avail');
      const d = await r.json();
      if(my !== euSeq) return;
      euOk = !!d.ok;
      ehint(d.ok ? '@' + v + ' boşdur' : d.reason === 'taken' ? 'Bu ad artıq götürülüb' : 'Format düzgün deyil', d.ok ? 'ok' : 'err');
    }catch(e){ if(my === euSeq) ehint(''); }
  }, 350);
}
$('euser').addEventListener('input', euserCheck);
function mailErr(r){
  if(r.status === 429) return 'Çox cəhd olundu, bir az sonra yenidən yoxla';
  if(r.status === 503) return 'E-poçt xidməti hələ qurulmayıb, Google ilə daxil ol';
  if(r.status === 502) return 'Məktub göndərilmədi, bir az sonra yenidən yoxla';
  return 'Məlumatları yoxla və yenidən cəhd et';
}
$('eform').addEventListener('submit', async e => {
  e.preventDefault();
  const m = emode;
  const em = $('em').value.trim().toLowerCase(), pw = $('pw').value, code = $('ecode').value.trim();
  if(m === 'login' && !em && pw){   // admin girişi: e-poçt boş, şifrə xanasına admin şifrəsi
    try{
      const ar = await fetch('/api/admin/login', {method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({password: pw})});
      if(ar.ok){ location.href = '/api/admin/panel'; return; }
    }catch(_){}
    loginMsg('E-poçt ünvanı düzgün deyil'); flagBad(['em']); return;
  }
  if(!/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(em)){ loginMsg('E-poçt ünvanı düzgün deyil'); flagBad(['em']); return; }
  if(m === 'reg'){
    const u = $('euser').value;
    if(!UNAME_RE.test(u) || u.includes('..')){ loginMsg('İstifadəçi adı seç: 3-20 simvol, hərf, rəqəm, nöqtə, _'); flagBad(['euser']); return; }
  }
  if((m === 'reg' || m === 'reset') && pw.length < 6){ loginMsg('Şifrə ən azı 6 simvol olmalıdır'); flagBad(['pw']); return; }
  if(m === 'login' && !pw){ loginMsg('Şifrəni yaz'); flagBad(['pw']); return; }
  if((m === 'verify' || m === 'reset') && !/^\d{6}$/.test(code)){ loginMsg('6 rəqəmli kodu yaz'); flagBad(['ecode']); return; }
  const path = {login: '/api/login', reg: '/api/register', verify: '/api/verify', forgot: '/api/forgot', reset: '/api/reset'}[m];
  const btn = $('esubmit'); btn.disabled = true; loginMsg('');
  try{
    const r = await api(path, {method: 'POST', body: JSON.stringify({email: em, password: pw, code, name: $('ename').value, username: $('euser').value})});
    if(r.ok){
      if(m === 'reg') setEmode('verify', 'E-poçtuna 6 rəqəmli kod göndərdik. Kodu yaz.');
      else if(m === 'forgot') setEmode('reset', 'E-poçtuna 6 rəqəmli kod göndərdik. Gəlməsə 1 dəqiqə sonra "Kodu yenidən göndər" bas.');
      else finishLogin(await r.json());
    }
    else if(r.status === 403 && m === 'login') setEmode('verify', 'E-poçtun hələ təsdiqlənməyib. Ünvanına kod göndərdik (gəlməsə 1 dəqiqə sonra yenidən göndər).');
    else if(r.status === 404 && m === 'forgot') setEmode('reg', 'Bu e-poçtla hesab tapılmadı. Hesab yaratmaq üçün ad və şifrə yaz.');
    else if(m === 'reg' && (r.status === 409 || r.status === 400) && ((await r.clone().json().catch(() => ({}))).error || '').startsWith('username')){
      loginMsg('Bu istifadəçi adı götürülüb və ya düzgün deyil. Başqa ad seç.'); flagBad(['euser']); euserCheck();
    }
    else if(m === 'reg' && r.status === 400 && ((await r.clone().json().catch(() => ({}))).error || '') === 'disposable'){
      loginMsg('Bir dəfəlik e-poçt ünvanları qəbul olunmur. Daimi e-poçtunu yaz.'); flagBad(['em']);
    }
    else if(m === 'reg' && r.status === 400 && ((await r.clone().json().catch(() => ({}))).error || '') === 'domain'){
      loginMsg('Bu e-poçt domeni mövcud deyil. Ünvanı yoxla (məs. gmail.com).'); flagBad(['em']);
    }
    else if(r.status === 409) setEmode('login', 'Bu e-poçtla hesab var, daxil ol. Şifrəni unutmusansa "Şifrəmi unutdum" bas.');
    else if(r.status === 401) loginMsg('E-poçt və ya şifrə səhvdir. Google ilə qeydiyyatdan keçmisənsə, "Şifrəmi unutdum" ilə şifrə qoya bilərsən.');
    else if(r.status === 422) loginMsg('Kod səhvdir və ya vaxtı bitib');
    else loginMsg(mailErr(r));
  }catch(err){ loginMsg('Əlaqə problemi, yenidən cəhd et'); }
  finally{ btn.disabled = false; }
});
$('eresend').onclick = async () => {
  const em = $('em').value.trim().toLowerCase();
  try{
    const r = await api(emode === 'verify' ? '/api/resend' : '/api/forgot', {method: 'POST', body: JSON.stringify({email: em})});
    loginMsg(r.ok ? 'Kod yenidən göndərildi' : r.status === 429 ? 'Bir az gözlə, sonra yenidən göndər' : mailErr(r));
  }catch(e){ loginMsg('Əlaqə problemi, yenidən cəhd et'); }
};

/* ---------- Giriş ekranı: uçan emojilər, günün sözü, canlı say ---------- */
const DAILY = [
  'Bir film, iki ekran, tək ürək.',
  'Məsafə uzaq, kadr eyni.',
  'Popcorn hazırdır, sən də hazırsan?',
  'Gözlərin başqa şəhərdə, ürəyin eyni salonda.',
  'Işıqlar sönür, söhbət başlayır.',
  'Bu axşamın filmini kim seçir?',
  'Ən yaxşı yer, ekranın qarşısında qoşa gülmək.',
  'Eyni saniyədə başla, eyni anda gül.'
];
let fxInit = false, liveTimer = null;
function initFloaties(){
  if(fxInit) return; fxInit = true;
  fillFloaties($('floaties'), 14);
  fillFloaties($('mfloat'), 9);
}
function fillFloaties(box, n){
  const em = [['fa-heart', '#ff7a9c'], ['fa-film', '#ffc46b'], ['fa-clapperboard', '#b7a2cf'], ['fa-star', '#ffc46b'], ['fa-ticket', '#ff7a9c'], ['fa-heart', '#e8567d']];
  for(let i = 0; i < n; i++){
    const s = document.createElement('span'); const ic = em[i % em.length]; s.innerHTML = '<i class="fa-solid ' + ic[0] + '" style="color:' + ic[1] + '" aria-hidden="true"></i>';
    s.style.left = (Math.random() * 94) + '%';
    s.style.fontSize = (16 + Math.random() * 18) + 'px';
    s.style.animationDuration = (9 + Math.random() * 9) + 's';
    s.style.animationDelay = (-Math.random() * 14) + 's';
    box.appendChild(s);
  }
}
async function loadLive(){
  if($('join').style.display === 'none') return;
  try{
    const r = await fetch('/api/stats', {cache: 'no-store'});
    if(!r.ok) throw new Error('stats');
    const d = await r.json();
    $('livetxt').textContent = d.rooms > 0
      ? 'İndi ' + d.people + ' nəfər ' + d.rooms + ' otaqda birlikdə izləyir'
      : 'İndi otaqlar boşdur, ilk otağı sən aç';
    $('live').hidden = false;
  }catch(e){ $('live').hidden = true; }
}
function startLive(){ loadLive(); if(!liveTimer) liveTimer = setInterval(loadLive, 20000); }

