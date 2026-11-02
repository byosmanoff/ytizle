
/* ---------- Mafia fon musiqisi v2: gərgin, aşağı templi noir pulse ---------- */
window.wtMafMusic = (function(){
  var LSK = 'wt_maf_music', on = true, ctx = null, master = null, bus = null, lp = null;
  var timer = 0, running = false, beat = 0, nextT = 0, VOL = 0.42;
  try{ on = localStorage.getItem(LSK) !== '0'; }catch(e){}
  /* Em – C – G – B7sus  (daha tünd, yavaş) */
  var CH = [
    {root: 82.41, pad: [164.81, 196.00, 246.94], mel: [329.63, 392.00, 493.88, 293.66]},
    {root: 65.41, pad: [130.81, 164.81, 196.00], mel: [261.63, 329.63, 392.00, 246.94]},
    {root: 98.00, pad: [196.00, 246.94, 293.66], mel: [392.00, 493.88, 293.66, 246.94]},
    {root: 61.74, pad: [123.47, 185.00, 233.08], mel: [311.13, 369.99, 466.16, 277.18]}
  ];
  function ac(){
    if(!ctx){
      var C = window.AudioContext || window.webkitAudioContext; if(!C) return null;
      try{ ctx = new C(); }catch(e){ ctx = null; return null; }
      master = ctx.createGain(); master.gain.value = 0; master.connect(ctx.destination);
      bus = ctx.createGain();
      lp = ctx.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.value = 900; lp.Q.value = 0.5;
      var hp = ctx.createBiquadFilter(); hp.type = 'highpass'; hp.frequency.value = 60;
      bus.connect(hp); hp.connect(lp); lp.connect(master);
      var d = ctx.createDelay(1.2); d.delayTime.value = 0.48;
      var fb = ctx.createGain(); fb.gain.value = 0.28;
      var ef = ctx.createBiquadFilter(); ef.type = 'lowpass'; ef.frequency.value = 800;
      var echo = ctx.createGain(); echo.gain.value = 0.35;
      bus.connect(echo); echo.connect(d); d.connect(ef); ef.connect(fb); fb.connect(d); ef.connect(master);
    }
    return ctx;
  }
  function pad(f, t, dur){
    var o = ctx.createOscillator(), g = ctx.createGain();
    o.type = 'sine'; o.frequency.value = f;
    g.gain.setValueAtTime(0.0001, t);
    g.gain.linearRampToValueAtTime(0.045, t + 2.8);
    g.gain.linearRampToValueAtTime(0.03, t + dur - 1);
    g.gain.linearRampToValueAtTime(0.0001, t + dur + 1.5);
    o.connect(g); g.connect(bus); o.start(t); o.stop(t + dur + 1.6);
  }
  function subBass(f, t, dur){
    var o = ctx.createOscillator(), g = ctx.createGain();
    o.type = 'sine'; o.frequency.value = f * 0.5;
    g.gain.setValueAtTime(0.0001, t);
    g.gain.linearRampToValueAtTime(0.07, t + 0.4);
    g.gain.linearRampToValueAtTime(0.04, t + dur - 0.5);
    g.gain.linearRampToValueAtTime(0.0001, t + dur + 0.8);
    o.connect(g); g.connect(bus); o.start(t); o.stop(t + dur + 0.9);
  }
  function pulse(t){
    var o = ctx.createOscillator(), g = ctx.createGain();
    o.type = 'triangle'; o.frequency.value = 55;
    g.gain.setValueAtTime(0.0001, t);
    g.gain.linearRampToValueAtTime(0.06, t + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.35);
    o.connect(g); g.connect(bus); o.start(t); o.stop(t + 0.4);
  }
  function spark(f, t){
    var o = ctx.createOscillator(), g = ctx.createGain();
    o.type = 'sine'; o.frequency.value = f;
    g.gain.setValueAtTime(0.0001, t);
    g.gain.linearRampToValueAtTime(0.035, t + 0.03);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 1.4);
    o.connect(g); g.connect(bus); o.start(t); o.stop(t + 1.5);
  }
  function phase(){
    try{
      var b = document.getElementById('mafbar');
      return (b && !b.hidden) ? (b.getAttribute('data-ph') || 'wait') : 'wait';
    }catch(e){ return 'wait'; }
  }
  function sched(){
    if(!running || !ctx) return;
    var t = ctx.currentTime;
    while(nextT < t + 2.2){
      var ph = phase();
      var ci = Math.floor(beat / 8) % CH.length;
      var ch = CH[ci];
      if(beat % 8 === 0){
        subBass(ch.root, nextT, 7.5);
        ch.pad.forEach(function(f){ pad(f, nextT, 7.5); });
      }
      if(beat % 2 === 0) pulse(nextT);
      if(ph === 'night' && beat % 4 === 1){
        spark(ch.mel[beat % ch.mel.length] * 0.5, nextT + 0.15);
      } else if(ph === 'vote' && beat % 3 === 0){
        spark(ch.mel[(beat + 2) % ch.mel.length], nextT + 0.05);
      } else if(beat % 5 === 2){
        spark(ch.mel[beat % ch.mel.length], nextT + 0.2);
      }
      beat++; nextT += (ph === 'night' ? 1.15 : (ph === 'vote' ? 0.85 : 1.0));
    }
  }
  function paint(){
    [].forEach.call(document.querySelectorAll('.musb'), function(b){
      b.innerHTML = '<i class="fa-solid fa-music" aria-hidden="true"></i>';
      b.classList.toggle('off', !on);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }
  function start(){
    if(!on || running) return;
    var c = ac(); if(!c) return;
    running = true;
    try{ c.resume(); }catch(e){}
    master.gain.cancelScheduledValues(c.currentTime);
    master.gain.setTargetAtTime(VOL, c.currentTime, 1.4);
    beat = 0; nextT = c.currentTime + 0.25;
    clearInterval(timer); timer = setInterval(sched, 280); sched();
  }
  function stop(){
    if(!running) return;
    running = false; clearInterval(timer); timer = 0;
    var c = ctx; if(!c) return;
    try{ master.gain.cancelScheduledValues(c.currentTime); master.gain.setTargetAtTime(0, c.currentTime, 0.3); }catch(e){}
    setTimeout(function(){ if(!running && ctx === c){ try{ c.close(); }catch(e){} ctx = null; } }, 1600);
  }
  function toggle(){ on = !on; try{ localStorage.setItem(LSK, on ? '1' : '0'); }catch(e){} paint(); if(on) start(); else stop(); }
  ['pointerdown', 'touchend', 'click', 'keydown'].forEach(function(ev){
    document.addEventListener(ev, function(){ if(running && ctx && ctx.state !== 'running'){ try{ ctx.resume(); }catch(e){} } }, {passive: true, capture: true});
  });
  document.addEventListener('visibilitychange', function(){
    if(!running || !ctx) return;
    try{ if(document.hidden) ctx.suspend(); else ctx.resume(); }catch(e){}
  });
  document.addEventListener('DOMContentLoaded', function(){
    var b = document.getElementById('mafmusic'); if(b) b.onclick = toggle;
    paint();
  });
  if(document.readyState !== 'loading'){ var b0 = document.getElementById('mafmusic'); if(b0) b0.onclick = toggle; paint(); }
  return {start: start, stop: stop, toggle: toggle, isOn: function(){ return on; }};
})();

/* ---------- Mafia: "Necə oynanır?" pəncərəsi ---------- */
(function(){
  var box = document.getElementById('mafhow'); if(!box) return;
  function open(){ box.hidden = false; var b = box.querySelector('.hw-body'); if(b) b.scrollTop = 0; }
  function close(){ box.hidden = true; }
  var h = document.getElementById('mafhelp'), w = document.getElementById('mafwhow');
  if(h) h.onclick = open; if(w) w.onclick = open;
  document.getElementById('mafhowx').onclick = close;
  document.getElementById('mafhowok').onclick = close;
  box.addEventListener('click', function(e){ if(e.target === box) close(); });
  document.addEventListener('keydown', function(e){ if(e.key === 'Escape' && !box.hidden){ close(); e.stopPropagation(); } });
})();
