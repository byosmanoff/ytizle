
/* ================= DM SƏSLİ ZƏNG (WebRTC; server yalnız siqnalı ötürür) ================= */
var CALL = null;
function ccEl(id){ return document.getElementById(id); }
function ccToast(t){ if(t && window.wtToast) window.wtToast(t); }
function ccRand(){ return Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-6); }
async function callSig(to, id, type, data){
  try{
    var r = await api('/api/call/signal', {method: 'POST', body: JSON.stringify({to: to, id: id, type: type, data: data})});
    var j = null; try{ j = await r.json(); }catch(e){}
    if(!r.ok) return {error: (j && j.error) || 'xəta'};
    return j || {};
  }catch(e){ return {error: 'xəta'}; }
}
function ccMic(){
  var cons = window.wtAudioCons ? window.wtAudioCons() : {echoCancellation: true, noiseSuppression: true, autoGainControl: true};
  return navigator.mediaDevices.getUserMedia({audio: cons, video: false});
}
function ccPaint(c){
  var av = ccEl('ccav'); av.style.backgroundImage = ''; av.textContent = initial(c.peer.name);
  if(c.peer.avatar && AV_RE.test(c.peer.avatar)){ av.style.backgroundImage = 'url("' + c.peer.avatar + '")'; av.textContent = ''; }
  ccEl('ccname').textContent = c.peer.name || ('@' + c.peer.username);
}
function ccUi(state, text){
  var ui = ccEl('ccst') && ccEl('dmcallui'); ui.hidden = false; ui.dataset.s = state;
  var txt = text || ({out: 'Zəng edilir...', 'in': 'Zəng gəlir', conn: 'Qoşulur...', active: '0:00'})[state] || '';
  ccEl('ccst').textContent = txt;
  var mute = ccEl('ccmute'); mute.classList.toggle('on', !!(CALL && CALL.muted));
  mute.firstElementChild.className = 'fa-solid ' + (CALL && CALL.muted ? 'fa-microphone-slash' : 'fa-microphone');
  ccEl('ccend').setAttribute('aria-label', state === 'in' ? 'Rədd et' : 'Zəngi bitir');
}
/* zəng səsi: WebAudio ilə sadə siqnal (fayl yoxdur) */
function ringStart(kind){
  var AC = window.AudioContext || window.webkitAudioContext, r = {tmr: 0, ctx: null};
  try{ r.ctx = new AC(); }catch(e){}
  function beep(){
    if(r.ctx){
      try{
        if(r.ctx.state === 'suspended') r.ctx.resume();
        var o = r.ctx.createOscillator(), g = r.ctx.createGain(), t = r.ctx.currentTime;
        o.frequency.value = kind === 'in' ? 523 : 425; o.connect(g); g.connect(r.ctx.destination);
        g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(.15, t + .05); g.gain.setValueAtTime(.15, t + .5); g.gain.linearRampToValueAtTime(0, t + .6);
        o.start(t); o.stop(t + .65);
      }catch(e){}
    }
    if(kind === 'in' && navigator.vibrate){ try{ navigator.vibrate([400, 200, 400]); }catch(e){} }
  }
  beep(); r.tmr = setInterval(beep, 2500); return r;
}
function ringStop(c){
  var r = c && c.ring; if(!r) return;
  clearInterval(r.tmr); try{ if(r.ctx) r.ctx.close(); }catch(e){}
  c.ring = null; try{ if(navigator.vibrate) navigator.vibrate(0); }catch(e){}
}
function callClose(msg){
  var c = CALL; CALL = null;
  if(c){
    ringStop(c); clearTimeout(c.to); clearTimeout(c.dis); clearInterval(c.tick);
    try{ if(c.stream) c.stream.getTracks().forEach(function(t){ t.stop(); }); }catch(e){}
    try{ if(c.pc) c.pc.close(); }catch(e){}
  }
  var a = ccEl('dmcallaud'); try{ a.pause(); a.srcObject = null; }catch(e){}
  var ui = ccEl('dmcallui');
  if(msg && c && !ui.hidden){
    ui.dataset.s = 'ended'; ccEl('ccst').textContent = msg;
    setTimeout(function(){ if(!CALL) ui.hidden = true; }, 1400);
  } else ui.hidden = true;
  try{ if(typeof loadDmList === 'function') loadDmList(); if(typeof DM !== 'undefined' && DM.peer) setTimeout(function(){ dmPull(false); }, 600); }catch(e){}
}
function callEnd(msg){
  var c = CALL; if(!c) return;
  callSig(c.peer.username, c.id, 'end');
  callClose(msg);
}
async function startCall(peer){
  if(!peer || !peer.username) return;
  if(CALL){ ccToast('Artıq zəngdəsən'); return; }
  if(!token || (account && account.guest)){ ccToast('Zəng üçün hesabla daxil ol.'); return; }
  if(!window.RTCPeerConnection || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia){ ccToast('Bu brauzer zəngi dəstəkləmir (https lazımdır).'); return; }
  var c = CALL = {id: ccRand(), peer: {username: peer.username, name: peer.name, avatar: peer.avatar}, role: 'caller', state: 'out', muted: false};
  ccPaint(c); ccUi('out');
  try{ c.stream = await ccMic(); }
  catch(e){ if(CALL === c) callClose('Mikrofon icazəsi verilmədi'); return; }
  if(CALL !== c){ c.stream.getTracks().forEach(function(t){ t.stop(); }); return; }
  await loadIce();
  if(CALL !== c) return;
  var r = await callSig(peer.username, c.id, 'invite');
  if(CALL !== c) return;
  if(r.error){ callClose(r.error === 'xəta' ? 'Zəng alınmadı' : r.error); return; }
  c.ring = ringStart('out');
  c.to = setTimeout(function(){ if(CALL === c && c.state === 'out') callEnd('Cavab verilmədi'); }, 45000);
}
function callIncoming(info){
  var c = CALL = {id: info.id, peer: info.peer, role: 'callee', state: 'in', muted: false};
  ccPaint(c); ccUi('in');
  c.ring = ringStart('in');
  c.to = setTimeout(function(){ if(CALL === c && c.state === 'in') callClose('Cavabsız zəng'); }, 45000);
}
async function callAccept(){
  var c = CALL; if(!c || c.state !== 'in') return;
  ringStop(c); clearTimeout(c.to); c.state = 'conn'; ccUi('conn');
  try{ c.stream = await ccMic(); }
  catch(e){ if(CALL === c){ callSig(c.peer.username, c.id, 'reject'); callClose('Mikrofon icazəsi verilmədi'); } return; }
  if(CALL !== c){ c.stream.getTracks().forEach(function(t){ t.stop(); }); return; }
  await loadIce();
  if(CALL !== c) return;
  await callSig(c.peer.username, c.id, 'accept');
  c.to = setTimeout(function(){ if(CALL === c && c.state !== 'active') callEnd('Qoşulmaq alınmadı'); }, 25000);
}
function callPc(c){
  var pc = new RTCPeerConnection({iceServers: ICE_SERVERS}); c.pc = pc;
  c.stream.getTracks().forEach(function(t){ pc.addTrack(t, c.stream); });
  pc.ontrack = function(e){
    var a = ccEl('dmcallaud'); a.srcObject = e.streams[0] || new MediaStream([e.track]);
    var pr = a.play(); if(pr && pr.catch) pr.catch(function(){});
  };
  pc.onconnectionstatechange = function(){
    if(CALL !== c) return;
    var st = pc.connectionState;
    if(st === 'connected'){ clearTimeout(c.dis); callActive(c); }
    else if(st === 'failed'){ callEnd('Bağlantı kəsildi'); }
    else if(st === 'disconnected'){ clearTimeout(c.dis); c.dis = setTimeout(function(){ if(CALL === c && pc.connectionState !== 'connected') callEnd('Bağlantı kəsildi'); }, 6000); }
  };
  return pc;
}
function iceDone(pc){
  return new Promise(function(res){
    if(pc.iceGatheringState === 'complete'){ res(); return; }
    var t = setTimeout(done, 3500);
    function done(){ clearTimeout(t); pc.removeEventListener('icegatheringstatechange', ch); res(); }
    function ch(){ if(pc.iceGatheringState === 'complete') done(); }
    pc.addEventListener('icegatheringstatechange', ch);
  });
}
async function callMakeOffer(){
  var c = CALL; if(!c || c.pc || c.role !== 'caller') return;
  ringStop(c); clearTimeout(c.to); c.state = 'conn'; ccUi('conn');
  try{
    var pc = callPc(c), o = await pc.createOffer(); await pc.setLocalDescription(o); await iceDone(pc);
    if(CALL !== c) return;
    await callSig(c.peer.username, c.id, 'offer', {sdp: pc.localDescription.sdp});
    c.to = setTimeout(function(){ if(CALL === c && c.state !== 'active') callEnd('Qoşulmaq alınmadı'); }, 25000);
  }catch(e){ if(CALL === c) callEnd('Zəng alınmadı'); }
}
async function callOnOffer(data){
  var c = CALL; if(!c || c.role !== 'callee' || c.pc || !c.stream || !data || !data.sdp) return;
  try{
    var pc = callPc(c); await pc.setRemoteDescription({type: 'offer', sdp: data.sdp});
    var a = await pc.createAnswer(); await pc.setLocalDescription(a); await iceDone(pc);
    if(CALL !== c) return;
    await callSig(c.peer.username, c.id, 'answer', {sdp: pc.localDescription.sdp});
  }catch(e){ if(CALL === c) callEnd('Zəng alınmadı'); }
}
async function callOnAnswer(data){
  var c = CALL; if(!c || !c.pc || !data || !data.sdp) return;
  try{ await c.pc.setRemoteDescription({type: 'answer', sdp: data.sdp}); }catch(e){ if(CALL === c) callEnd('Zəng alınmadı'); }
}
function callActive(c){
  if(c.state === 'active') return;
  c.state = 'active'; clearTimeout(c.to); ringStop(c); c.t0 = Date.now(); ccUi('active');
  c.tick = setInterval(function(){
    var s = Math.floor((Date.now() - c.t0) / 1000);
    ccEl('ccst').textContent = Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  }, 500);
}
function dmCallEvent(p){
  if(!p || !p.id) return;
  if(p.type === 'invite'){
    if(CALL && CALL.id === p.id) return;
    if(CALL){ callSig(p.u, p.id, 'reject', {busy: true}); return; }
    if(Date.now() - (p.ts || 0) > 45000) return;
    callIncoming({id: p.id, peer: {username: p.u, name: p.name, avatar: p.av}});
    return;
  }
  if(!CALL || CALL.id !== p.id) return;
  if(p.type === 'accept') callMakeOffer();
  else if(p.type === 'offer') callOnOffer(p.data);
  else if(p.type === 'answer') callOnAnswer(p.data);
  else if(p.type === 'reject') callClose(p.data && p.data.busy ? 'Məşğuldur' : 'Zəngi rədd etdi');
  else if(p.type === 'end') callClose(CALL.state === 'in' ? 'Cavabsız zəng' : 'Zəng bitdi');
}
async function callPending(){
  if(!token || CALL || (account && account.guest)) return;
  try{
    var r = await api('/api/call/pending'); if(!r.ok) return;
    var j = await r.json();
    if(j && j.call && !CALL) dmCallEvent({type: 'invite', id: j.call.id, u: j.call.u, name: j.call.name, av: j.call.av, ts: j.call.ts});
  }catch(e){}
}
(function(){
  ccEl('ccacc').onclick = callAccept;
  ccEl('ccend').onclick = function(){
    var c = CALL; if(!c) return;
    if(c.state === 'in'){ callSig(c.peer.username, c.id, 'reject'); callClose(''); }
    else callEnd('');
  };
  ccEl('ccmute').onclick = function(){
    var c = CALL; if(!c || !c.stream) return;
    c.muted = !c.muted; c.stream.getAudioTracks().forEach(function(t){ t.enabled = !c.muted; });
    ccUi(c.state === 'active' ? 'active' : c.state, ccEl('ccst').textContent);
  };
  document.addEventListener('visibilitychange', function(){ if(!document.hidden) callPending(); });
  var t1 = setInterval(function(){ if(token){ clearInterval(t1); callPending(); } }, 1500);
})();
