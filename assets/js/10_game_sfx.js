
/* ---------- Oyun səs effektləri (fayl yoxdur, Web Audio ilə yaradılır) ---------- */
window.wtSfx = (function(){
  var LSK = 'wt_sfx', ctx = null, master = null, nbuf = null, on = true, lastAt = {};
  try{ on = localStorage.getItem(LSK) !== '0'; }catch(e){}
  function ac(){
    if(!ctx){
      var C = window.AudioContext || window.webkitAudioContext; if(!C) return null;
      try{ ctx = new C(); master = ctx.createGain(); master.gain.value = .9; master.connect(ctx.destination); }catch(e){ ctx = null; return null; }
    }
    if(ctx.state === 'suspended'){ try{ ctx.resume(); }catch(e){} }
    return ctx;
  }
  ['pointerdown', 'touchend', 'click', 'keydown'].forEach(function(ev){ document.addEventListener(ev, function(){ if(on) ac(); }, {passive: true, capture: true}); });
  function noise(){
    if(nbuf) return nbuf;
    var n = Math.floor(ctx.sampleRate * .5); nbuf = ctx.createBuffer(1, n, ctx.sampleRate);
    var d = nbuf.getChannelData(0); for(var i = 0; i < n; i++) d[i] = Math.random() * 2 - 1;
    return nbuf;
  }
  function tone(f, t, dur, type, vol, f2){
    var o = ctx.createOscillator(), g = ctx.createGain();
    o.type = type || 'sine'; o.frequency.setValueAtTime(f, t);
    if(f2) o.frequency.exponentialRampToValueAtTime(f2, t + dur);
    g.gain.setValueAtTime(.0001, t); g.gain.exponentialRampToValueAtTime(vol, t + .008); g.gain.exponentialRampToValueAtTime(.0001, t + dur);
    o.connect(g); g.connect(master); o.start(t); o.stop(t + dur + .03);
  }
  function click(t, vol, freq, q, dur){
    var s = ctx.createBufferSource(); s.buffer = noise();
    var f = ctx.createBiquadFilter(); f.type = 'bandpass'; f.frequency.value = freq; f.Q.value = q;
    var g = ctx.createGain(); g.gain.setValueAtTime(vol, t); g.gain.exponentialRampToValueAtTime(.0001, t + dur);
    s.connect(f); f.connect(g); g.connect(master); s.start(t, Math.random() * .2); s.stop(t + dur + .03);
  }
  function knock(t, v, f){ click(t, .9 * v, f, 1.3, .06); tone(190, t, .11, 'sine', .45 * v, 95); }
  function clack(t){ click(t, 1.2, 2400, 2.2, .035); click(t + .028, .8, 1000, 1.4, .07); tone(300, t, .07, 'sine', .3, 170); }
  function arp(list, t, step, type, vol, len){ list.forEach(function(f, i){ tone(f, t + i * step, len || .24, type || 'triangle', vol || .22); }); }
  var SND = {
    move: function(t){ knock(t, 1, 1500); },
    capture: function(t){ knock(t, 1.4, 1000); knock(t + .075, .9, 1300); },
    castle: function(t){ knock(t, 1, 1500); knock(t + .16, 1, 1200); },
    alert: function(t){ tone(880, t, .12, 'square', .08); tone(1175, t + .11, .18, 'square', .08); },
    check: function(t){ knock(t, 1, 1500); SND.alert(t + .09); },
    promote: function(t){ knock(t, 1, 1500); arp([523, 659, 784, 1047], t + .1, .07, 'triangle', .2, .22); },
    start: function(t){ knock(t, .7, 1800); knock(t + .09, .7, 1400); },
    place: function(t){ clack(t); },
    take: function(t){
      var s = ctx.createBufferSource(); s.buffer = noise(); s.loop = true;
      var f = ctx.createBiquadFilter(); f.type = 'bandpass'; f.Q.value = 2.5;
      f.frequency.setValueAtTime(500, t); f.frequency.exponentialRampToValueAtTime(2600, t + .2);
      var g = ctx.createGain(); g.gain.setValueAtTime(.0001, t); g.gain.exponentialRampToValueAtTime(.55, t + .04); g.gain.exponentialRampToValueAtTime(.0001, t + .22);
      s.connect(f); f.connect(g); g.connect(master); s.start(t); s.stop(t + .26);
    },
    pass: function(t){ tone(230, t, .24, 'sine', .35, 140); },
    shuffle: function(t){ for(var i = 0; i < 6; i++) click(t + i * .055, .5, 1200 + Math.random() * 1500, 1, .05); },
    turn: function(t){ tone(660, t, .09, 'sine', .15); tone(990, t + .09, .12, 'sine', .15); },
    join: function(t){ tone(587, t, .1, 'sine', .22); tone(784, t + .1, .16, 'sine', .22); },
    invite: function(t){ tone(784, t, .14, 'sine', .25); tone(1047, t + .14, .2, 'sine', .25); tone(1319, t + .3, .3, 'sine', .2); },
    win: function(t){ arp([523, 659, 784, 1047], t, .11, 'triangle', .24, .3); tone(1319, t + .5, .5, 'triangle', .22); },
    lose: function(t){ arp([392, 349, 311, 262], t, .17, 'sine', .3, .32); },
    tie: function(t){ tone(440, t, .2, 'sine', .25); tone(440, t + .24, .3, 'sine', .25); },
    skip: function(t){ tone(320, t, .12, 'square', .1, 190); tone(190, t + .11, .2, 'square', .1, 110); },
    reverse: function(t){ tone(480, t, .16, 'sine', .25, 960); tone(960, t + .15, .18, 'sine', .22, 480); },
    wild: function(t){ arp([523, 659, 784, 988, 1319], t, .055, 'triangle', .18, .2); },
    plus4: function(t){ SND.take(t); SND.take(t + .13); tone(150, t, .45, 'sawtooth', .1, 70); },
    uno: function(t){ tone(659, t, .1, 'square', .12); tone(659, t + .12, .1, 'square', .12); tone(988, t + .26, .3, 'square', .13); }
  };
  function icon(){
    [].forEach.call(document.querySelectorAll('.sfxb'), function(b){
      b.innerHTML = '<i class="fa-solid ' + (on ? 'fa-volume-high' : 'fa-volume-xmark') + '" aria-hidden="true"></i>';
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }
  function toggle(){ on = !on; try{ localStorage.setItem(LSK, on ? '1' : '0'); }catch(e){} icon(); if(on){ ac(); setTimeout(function(){ api_play('turn'); }, 60); } }
  function api_play(n){
    if(!on) return;
    var c = ac(); if(!c || !SND[n]) return;
    if(c.state !== 'running'){ try{ c.resume(); }catch(e){} return; }
    var now = c.currentTime;
    if(lastAt[n] && now - lastAt[n] < .05) return;
    lastAt[n] = now;
    try{ SND[n](now + .005); }catch(e){}
  }
  [].forEach.call(document.querySelectorAll('.sfxb'), function(b){ b.onclick = toggle; });
  icon();
  return {play: api_play, isOn: function(){ return on; }, toggle: toggle};
})();

/* ---------- Oyuna dəvət: istifadəçi adı ilə və ya dost siyahısından ---------- */
window.wtInviteBox = function(o){
  var $ = function(id){ return document.getElementById(id); };
  var sent = {}, fr = null, frAt = 0, loading = false, lastCode = '';
  function msg(t){ var e = $(o.msg); if(e) e.textContent = t || ''; }
  function paint(){
    var box = $(o.fr); if(!box) return;
    box.textContent = '';
    (fr || []).forEach(function(p){
      var b = document.createElement('button'); b.type = 'button';
      if(sent[p.username]) b.className = 'sent';
      var a = document.createElement('span'); a.className = 'ga';
      if(p.avatar && /^\/api\/avatar\/[a-z0-9._]{3,20}\?v=\d+$/.test(p.avatar)) a.style.backgroundImage = 'url("' + p.avatar + '")';
      else a.textContent = String(p.name || '?').slice(0, 1).toUpperCase();
      var n = document.createElement('span'); n.textContent = (p.name || p.username) + (sent[p.username] ? ' ✓' : '');
      b.appendChild(a); b.appendChild(n);
      b.onclick = function(){ send(p.username); };
      box.appendChild(b);
    });
    var t = $(o.ft); if(t) t.hidden = !(fr && fr.length);
  }
  async function send(u, btn){
    u = String(u || '').trim().replace(/^@/, '').toLowerCase();
    if(!u){ msg('İstifadəçi adını yaz'); return; }
    var code = o.code(); if(!code) return;
    if(btn) btn.disabled = true;
    try{
      var r = await api('/api/game/invite', {method: 'POST', body: JSON.stringify({game: o.game, room: code, username: u})});
      if(r.ok){ sent[u] = 1; msg('@' + u + ' dəvət olundu'); var inp = $(o.user); if(inp) inp.value = ''; paint(); }
      else if(r.status === 404){ var j = {}; try{ j = await r.json(); }catch(e){} msg(j.error === 'room' ? 'Oyun tapılmadı' : 'Belə istifadəçi tapılmadı'); }
      else if(r.status === 403) msg('Bu istifadəçi dəvətləri bağlayıb');
      else if(r.status === 409) msg('Oyunçu artıq içəridədir və ya otaq doludur');
      else if(r.status === 429) msg('Çox sorğu göndərdin, bir az gözlə');
      else if(r.status === 400) msg('İstifadəçi adı düzgün deyil');
      else msg('Alınmadı, yenidən cəhd et');
    }catch(e){ msg('Əlaqə problemi, yenidən cəhd et'); }
    if(btn) btn.disabled = false;
  }
  async function refresh(){
    var code = o.code();
    if(code !== lastCode){ lastCode = code; sent = {}; msg(''); }
    if(!token) return;
    if(fr && Date.now() - frAt < 15000){ paint(); return; }
    if(loading) return; loading = true;
    try{ var r = await api('/api/friends'); if(r.ok){ fr = (await r.json()).friends || []; frAt = Date.now(); paint(); } }catch(e){}
    loading = false;
  }
  var go = $(o.go), inp = $(o.user);
  if(go) go.onclick = function(){ send(inp ? inp.value : '', go); };
  if(inp) inp.addEventListener('keydown', function(e){ if(e.key === 'Enter'){ e.preventDefault(); send(inp.value, go); } });
  return {refresh: refresh};
};
