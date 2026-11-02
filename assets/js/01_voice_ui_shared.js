
/* ---------- Ortaq səsli otaq interfeysi (bütün səsli otaqlar üçün) ---------- */
(function(){
  if(!document.getElementById('gvs-css')){
    var st = document.createElement('style'); st.id = 'gvs-css';
    st.textContent = [
      '.gvsheet{position:fixed;left:0;top:0;width:100%;height:var(--H,100dvh);transform:translateY(var(--VT,0px));z-index:200;display:flex;justify-content:center;align-items:flex-start;box-sizing:border-box;padding:calc(env(safe-area-inset-top,0px) + 62px) 12px 0;background:rgba(10,4,16,.38);-webkit-user-select:none;user-select:none;touch-action:manipulation;animation:gvsFade .18s ease both}',
      '.gvsheet[hidden]{display:none!important}',
      '.gvsheet button{background:none;border:0;padding:0;margin:0;color:inherit;font:inherit;font-size:13px}',
      '.gvs-card{width:min(100%,340px);max-height:calc(var(--H,100dvh) - 90px);overflow:hidden;display:flex;flex-direction:column;border-radius:24px;padding:14px 14px 16px;background:linear-gradient(160deg,var(--seat-2),var(--velvet-2));color:var(--text);box-shadow:inset 0 0 0 1px rgba(255,255,255,.13),0 18px 44px -12px rgba(0,0,0,.65);transform-origin:50% 0;animation:gvsPop .26s cubic-bezier(.2,.9,.3,1.2) both}',
      '.gvs-head{display:flex;align-items:center;gap:10px;flex:none;padding:0 2px 10px}',
      '.gvs-tt{min-width:0;flex:1}',
      '.gvs-tt b{display:block;font-size:17px;font-weight:800;line-height:1.2}',
      '.gvs-tt small{display:block;font-size:12.5px;color:var(--muted);margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
      '.gvsheet .gvs-x{width:34px;height:34px;border-radius:50%;display:grid;place-items:center;background:rgba(255,255,255,.09);font-size:14px;flex:none}',
      '.gvs-list{flex:0 1 auto;min-height:0;overflow-y:auto;overscroll-behavior:contain;display:flex;flex-direction:column;gap:6px}',
      '.gvs-row{display:flex;align-items:center;gap:11px;padding:8px 10px;border-radius:16px;background:rgba(255,255,255,.06);box-shadow:inset 0 0 0 1px var(--line);animation:gvsRow .28s ease both;transition:box-shadow .2s,background .2s}',
      '.gvs-row.speak{background:rgba(91,224,138,.12);box-shadow:inset 0 0 0 1.5px #5be08a}',
      '.gvs-av{width:38px;height:38px;border-radius:50%;display:grid;place-items:center;font-size:16px;font-weight:800;color:#fff;flex:none;position:relative}',
      '.gvs-row.speak .gvs-av::after{content:"";position:absolute;inset:-4px;border-radius:50%;border:2px solid #5be08a;animation:gvsRing 1.1s ease-out infinite}',
      '.gvs-nm{min-width:0;flex:1}',
      '.gvs-nm b{display:block;font-size:15px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
      '.gvs-nm small{display:block;font-size:12px;color:var(--muted);margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
      '.gvs-row.speak .gvs-nm small{color:#5be08a}',
      '.gvs-mi{font-size:15px;color:var(--rose);width:22px;text-align:center;flex:none}',
      '.gvs-mi.live{color:#5be08a}',
      '.gvsheet .gvs-inv{width:100%;text-align:left;color:var(--amber);background:rgba(255,196,107,.08);box-shadow:inset 0 0 0 1px rgba(255,196,107,.28)}',
      '.gvs-inv .gvs-av{background:rgba(255,196,107,.16);color:var(--amber);font-size:15px}',
      '.gvs-inv .gvs-nm b{font-size:14px}',
      '.gvs-ctl{display:flex;justify-content:center;gap:18px;flex:none;padding-top:14px}',
      '.gvsheet .gvs-c{display:flex;flex-direction:column;align-items:center;gap:5px;min-width:64px;color:var(--text);font-size:11.5px}',
      '.gvs-c .gvs-b{width:48px;height:48px;border-radius:50%;display:grid;place-items:center;font-size:19px;background:var(--seat);box-shadow:inset 0 0 0 1px rgba(255,255,255,.14);transition:background .2s,transform .12s,box-shadow .2s}',
      '.gvs-c:active .gvs-b{transform:scale(.92)}',
      '.gvs-c.big .gvs-b{background:#1f6b3b;box-shadow:none}',
      '.gvs-c.big.off .gvs-b{background:var(--rose-deep)}',
      '.gvs-c.spk .gvs-b{background:var(--seat-2)}',
      '.gvs-c.spk.off .gvs-b{background:rgba(255,255,255,.06);color:var(--muted)}',
      '.gvs-c.lv .gvs-b{background:#b8325a;box-shadow:none}',
      '.gvs-c.lock .gvs-b{opacity:.45}',
      '.gvs-c span.l{white-space:nowrap;color:var(--muted)}',
      '.gvs-ip{display:flex;flex-direction:column;gap:8px;min-height:0;animation:gvsRow .24s ease both}',
      '.gvs-ip[hidden]{display:none}',
      '.gvs-ir{display:flex;gap:8px;align-items:center}',
      '.gvsheet .gvs-bk{width:38px;height:42px;border-radius:14px;background:rgba(255,255,255,.09);display:grid;place-items:center;font-size:14px;flex:none}',
      '.gvsheet .gvs-in{flex:1;min-width:0;height:42px;border-radius:14px;padding:0 12px;background:rgba(0,0,0,.28);color:var(--text);box-shadow:inset 0 0 0 1px var(--line);font-size:16px;border:0;outline:none}',
      '.gvsheet .gvs-in:focus{box-shadow:inset 0 0 0 1.5px var(--amber)}',
      '.gvsheet .gvs-go{height:42px;padding:0 14px;border-radius:14px;background:linear-gradient(135deg,var(--rose),var(--rose-deep));color:#fff;font-weight:700;font-size:14px;flex:none;white-space:nowrap}',
      '.gvsheet .gvs-go.done{background:#1f6b3b}',
      '.gvsheet .gvs-go:disabled{opacity:.55}',
      '.gvs-msg{font-size:12.5px;color:var(--amber);min-height:16px;padding:0 4px}',
      '.gvs-fl{display:flex;flex-direction:column;gap:6px;overflow-y:auto;max-height:230px;overscroll-behavior:contain}',
      '.gvs-fl .gvs-row{padding:7px 8px 7px 10px}',
      '.gvs-av.img{background-size:cover;background-position:center;font-size:0}',
      '.gvs-dot{position:absolute;right:-1px;bottom:-1px;width:11px;height:11px;border-radius:50%;background:#5be08a;border:2px solid var(--seat-2)}',
      '.gvs-empty{font-size:13px;color:var(--muted);text-align:center;padding:10px 4px}',
      '@keyframes gvsFade{from{opacity:0}to{opacity:1}}',
      '@keyframes gvsPop{from{opacity:0;transform:translateY(-10px) scale(.92)}to{opacity:1;transform:none}}',
      '@keyframes gvsRow{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}',
      '@keyframes gvsRing{from{opacity:.9;transform:scale(.9)}to{opacity:0;transform:scale(1.25)}}',
      '@media (prefers-reduced-motion:reduce){.gvsheet,.gvs-card,.gvs-row,.gvs-row.speak .gvs-av::after{animation:none!important}}'
    ].join('\n');
    document.head.appendChild(st);
  }
  function hue(s){ var h = 0; s = String(s || ''); for(var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360; return h; }
  function T(s){ try{ return window.wtT ? window.wtT(s) : s; }catch(e){ return s; } }

  /* cfg: title, sub(), members() -> [{id,name,muted,me,st}], isMuted(), isSpeaker(), muteLocked(),
          onMute(), onSpeaker(), onLeave(), sendInvite(username) -> Promise<{ok,msg}> (ixtiyari), onClose() (ixtiyari) */
  window.wtVoiceSheet = function(cfg){
    var root = document.createElement('div');
    root.className = 'gvsheet'; root.hidden = true;
    root.setAttribute('role', 'dialog'); root.setAttribute('aria-modal', 'true');
    root.innerHTML = '<div class="gvs-card">' +
      '<div class="gvs-head"><div class="gvs-tt"><b></b><small></small></div>' +
      '<button type="button" class="gvs-x" data-a="x" aria-label="Kiçilt"><i class="fa-solid fa-chevron-up" aria-hidden="true"></i></button></div>' +
      '<div class="gvs-list"></div>' +
      '<div class="gvs-ip" hidden><div class="gvs-ir"><button type="button" class="gvs-bk" data-a="back" aria-label="Geri"><i class="fa-solid fa-chevron-left" aria-hidden="true"></i></button>' +
      '<input class="gvs-in" type="text" maxlength="21" placeholder="@istifadəçi adı" autocapitalize="none" autocomplete="off" autocorrect="off" spellcheck="false" aria-label="İstifadəçi adı">' +
      '<button type="button" class="gvs-go" data-a="send">Dəvət et</button></div>' +
      '<div class="gvs-msg"></div><div class="gvs-fl"></div></div>' +
      '<div class="gvs-ctl">' +
        '<button type="button" class="gvs-c big" data-a="mic"><span class="gvs-b"><i class="fa-solid fa-microphone" aria-hidden="true"></i></span><span class="l">Səssiz et</span></button>' +
        '<button type="button" class="gvs-c spk" data-a="spk"><span class="gvs-b"><i class="fa-solid fa-volume-high" aria-hidden="true"></i></span><span class="l">Dinamik</span></button>' +
        '<button type="button" class="gvs-c lv" data-a="lv"><span class="gvs-b"><i class="fa-solid fa-phone-slash" aria-hidden="true"></i></span><span class="l">Ayrıl</span></button>' +
      '</div></div>';
    document.body.appendChild(root);
    var list = root.querySelector('.gvs-list'), ttl = root.querySelector('.gvs-tt b'), sub = root.querySelector('.gvs-tt small');
    var bSpk = root.querySelector('[data-a="spk"]'), bMic = root.querySelector('[data-a="mic"]');
    var speak = {}, an = {}, actx = null, tm = 0;
    var view = 'main', fr = null, frAt = 0, frBusy = false, sent = {};
    var ip = root.querySelector('.gvs-ip'), inp = root.querySelector('.gvs-in'), msgEl = root.querySelector('.gvs-msg'), fl = root.querySelector('.gvs-fl');
    function say(t){ msgEl.textContent = t || ''; }
    function paintFriends(){
      fl.textContent = '';
      if(!fr || !fr.length){ var e = document.createElement('div'); e.className = 'gvs-empty'; e.textContent = fr ? T('Dost siyahın boşdur. İstifadəçi adını yaz.') : T('Yüklənir...'); fl.appendChild(e); return; }
      fr.forEach(function(p){
        var r = document.createElement('div'); r.className = 'gvs-row';
        var av = document.createElement('span'); av.className = 'gvs-av';
        if(p.avatar && /^\/api\/avatar\/[a-z0-9._]{3,20}\?v=\d+$/.test(p.avatar)){ av.className += ' img'; av.style.backgroundImage = 'url("' + p.avatar + '")'; }
        else { av.style.background = 'hsl(' + hue(p.name) + ',42%,40%)'; av.textContent = (String(p.name || p.username || '?').trim()[0] || '?').toLocaleUpperCase('az'); }
        if(p.online){ var d = document.createElement('i'); d.className = 'gvs-dot'; av.appendChild(d); }
        var nm = document.createElement('span'); nm.className = 'gvs-nm';
        var b = document.createElement('b'); b.textContent = p.name || p.username;
        var sm = document.createElement('small'); sm.textContent = '@' + p.username + (p.online ? ' · ' + T('onlayn') : '');
        nm.appendChild(b); nm.appendChild(sm);
        var go = document.createElement('button'); go.type = 'button'; go.className = 'gvs-go' + (sent[p.username] ? ' done' : '');
        go.textContent = sent[p.username] ? '✓' : T('Dəvət et');
        go.onclick = function(){ doSend(p.username, go); };
        r.appendChild(av); r.appendChild(nm); r.appendChild(go); fl.appendChild(r);
      });
    }
    async function loadFriends(){
      if(frBusy || (fr && Date.now() - frAt < 15000)){ paintFriends(); return; }
      frBusy = true; paintFriends();
      try{
        if(typeof token !== 'undefined' && token){ var r = await api('/api/friends'); if(r.ok){ fr = (await r.json()).friends || []; frAt = Date.now(); } }
        else fr = [];
      }catch(e){ if(!fr) fr = []; }
      frBusy = false; if(view === 'invite') paintFriends();
    }
    async function doSend(u, btn){
      u = String(u || '').trim().replace(/^@/, '').toLowerCase();
      if(!u){ say(T('İstifadəçi adını yaz')); return; }
      if(btn) btn.disabled = true;
      var res;
      try{ res = await cfg.sendInvite(u); }catch(e){ res = {ok: false, msg: 'Əlaqə problemi, yenidən cəhd et'}; }
      if(btn) btn.disabled = false;
      say(T(res.msg || (res.ok ? '@' + u + ' dəvət olundu' : 'Alınmadı')));
      if(res.ok){ sent[u] = 1; inp.value = ''; paintFriends(); }
    }
    function setView(v){
      view = v;
      list.hidden = v !== 'main'; ip.hidden = v !== 'invite';
      if(v === 'invite'){ say(''); loadFriends(); }
      update();
    }

    function paint(){
      var rows = list.querySelectorAll('.gvs-row[data-id]');
      for(var i = 0; i < rows.length; i++) rows[i].classList.toggle('speak', !!speak[rows[i].getAttribute('data-id')]);
    }
    function update(){
      ttl.textContent = T(view === 'invite' ? 'Dəvət et' : (cfg.title || 'Səsli söhbət'));
      sub.textContent = view === 'invite' ? T('Dostunu seç və ya istifadəçi adını yaz') : (cfg.sub ? cfg.sub() : '');
      if(view === 'main') paintMain();
      controls();
    }
    function paintMain(){
      var ms = cfg.members() || [];
      list.textContent = '';
      ms.forEach(function(m){
        var r = document.createElement('div'); r.className = 'gvs-row'; r.setAttribute('data-id', m.id);
        if(speak[m.id] && !m.muted) r.classList.add('speak');
        var av = document.createElement('span'); av.className = 'gvs-av';
        av.style.background = 'hsl(' + hue(m.name) + ',42%,40%)';
        av.textContent = (String(m.name || '?').trim()[0] || '?').toLocaleUpperCase('az');
        var nm = document.createElement('span'); nm.className = 'gvs-nm';
        var b = document.createElement('b'); b.textContent = String(m.name || '') + (m.me ? ' (' + T('Sən') + ')' : '');
        var s = document.createElement('small'); s.textContent = m.muted ? T('Səssiz') : (m.st || T('Səsli otaqda'));
        nm.appendChild(b); nm.appendChild(s);
        var ic = document.createElement('i'); ic.className = 'fa-solid gvs-mi ' + (m.muted ? 'fa-microphone-slash' : 'fa-microphone live');
        ic.setAttribute('aria-hidden', 'true');
        r.appendChild(av); r.appendChild(nm); r.appendChild(ic);
        list.appendChild(r);
      });
      if(cfg.sendInvite){
        var inv = document.createElement('button'); inv.type = 'button'; inv.className = 'gvs-row gvs-inv'; inv.setAttribute('data-a', 'inv');
        inv.innerHTML = '<span class="gvs-av"><i class="fa-solid fa-user-plus" aria-hidden="true"></i></span><span class="gvs-nm"><b></b></span>';
        inv.querySelector('b').textContent = T('Dostu dəvət et');
        list.appendChild(inv);
      }
    }
    function controls(){
      var muted = !!cfg.isMuted(), lock = !!(cfg.muteLocked && cfg.muteLocked());
      bMic.classList.toggle('off', muted || lock); bMic.classList.toggle('lock', lock);
      bMic.querySelector('i').className = 'fa-solid ' + (muted || lock ? 'fa-microphone-slash' : 'fa-microphone');
      bMic.querySelector('.l').textContent = lock ? T('Mikrofon bağlıdır') : T(muted ? 'Səsi aç' : 'Səssiz et');
      var sp = cfg.isSpeaker ? !!cfg.isSpeaker() : true;
      bSpk.classList.toggle('off', !sp);
      bSpk.querySelector('i').className = 'fa-solid ' + (sp ? 'fa-volume-high' : 'fa-volume-xmark');
      bSpk.style.display = cfg.onSpeaker ? '' : 'none';
    }
    root.addEventListener('click', function(e){
      if(e.target === root){ close(); return; }
      var t = e.target.closest('[data-a]'); if(!t) return;
      var a = t.getAttribute('data-a');
      if(a === 'x') close();
      else if(a === 'mic'){ if(!(cfg.muteLocked && cfg.muteLocked())) cfg.onMute(); update(); }
      else if(a === 'spk'){ cfg.onSpeaker(); update(); }
      else if(a === 'lv'){ cfg.onLeave(); }
      else if(a === 'inv') setView('invite');
      else if(a === 'back') setView('main');
      else if(a === 'send') doSend(inp.value, t);
    });
    function tick(){
      var ch = false;
      Object.keys(an).forEach(function(id){
        var x = an[id], m = 0;
        try{ x.a.getByteTimeDomainData(x.buf); }catch(e){ return; }
        for(var i = 0; i < x.buf.length; i++){ var v = Math.abs(x.buf[i] - 128); if(v > m) m = v; }
        var on = m > 12;
        if(id === 'me' && ((cfg.isMuted && cfg.isMuted()) || (cfg.muteLocked && cfg.muteLocked()))) on = false;
        if(!!speak[id] !== on){ speak[id] = on; ch = true; }
      });
      if(ch) paint();
    }
    function open(){
      if(!root.hidden){ update(); return; }
      try{ if(actx && actx.state === 'suspended') actx.resume(); }catch(e){}
      update(); root.hidden = false;
      if(!tm) tm = setInterval(tick, 120);
    }
    inp.addEventListener('keydown', function(e){ if(e.key === 'Enter'){ e.preventDefault(); doSend(inp.value, root.querySelector('[data-a="send"]')); } });
    function close(){
      if(root.hidden) return;
      root.hidden = true; view = 'main'; list.hidden = false; ip.hidden = true;
      if(tm){ clearInterval(tm); tm = 0; }
      if(cfg.onClose) try{ cfg.onClose(); }catch(e){}
    }
    return {
      open: open, close: close,
      isOpen: function(){ return !root.hidden; },
      update: function(){ if(!root.hidden) update(); },
      resetInvites: function(){ sent = {}; },
      /* konuşma göstəricisi: öz analizatoru (stream) və ya xaricdən (setSpeak) */
      watch: function(id, stream){
        this.unwatch(id);
        try{
          if(!actx){ var AC = window.AudioContext || window.webkitAudioContext; if(!AC) return; actx = new AC(); }
          var src = actx.createMediaStreamSource(stream), a = actx.createAnalyser(); a.fftSize = 256; src.connect(a);
          an[id] = {a: a, src: src, buf: new Uint8Array(a.frequencyBinCount)};
        }catch(e){}
      },
      unwatch: function(id){
        var x = an[id]; if(x){ try{ x.src.disconnect(); }catch(e){} delete an[id]; }
        if(speak[id]){ speak[id] = false; paint(); }
      },
      setSpeak: function(id, on){ if(!!speak[id] !== !!on){ speak[id] = !!on; paint(); } },
      clear: function(){ var s = this; Object.keys(an).forEach(function(id){ s.unwatch(id); }); speak = {}; }
    };
  };
})();
