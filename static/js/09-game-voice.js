/* ---------- Oyun səsli otağı (domino / şahmat): WebRTC, hər kəs hər kəslə (4 nəfərə qədər) ---------- */
(function(){
  var iceLoaded = false;
  window.wtGameVoice = function(o){
    var $ = function(id){ return document.getElementById(id); };
    var on = false, muted = false, stream = null, myId = '', since = 0, gen = 0, rc = '', needTap = false;
    var peers = {}, vc = [], vk = 0;
    var box = document.createElement('div'); box.style.display = 'none'; document.body.appendChild(box);
    function T(s){ try{ return window.wtT ? window.wtT(s) : s; }catch(e){ return s; } }
    function say(t){ try{ if(o.say) o.say(t); }catch(e){} }
    function enc(x){ return encodeURIComponent(x); }

    async function sig(k, to, d, extra){
      var r = await api('/api/gvoice/send', {method: 'POST', body: JSON.stringify(Object.assign({game: o.game, room: rc || o.code(), k: k, to: to || '', d: d == null ? null : d}, extra || {}))});
      if(!r.ok) throw new Error(String(r.status));
      return r.json();
    }

    function render(){
      var bar = $(o.p + 'vbar'), btn = $(o.p + 'mic'), mb = $(o.p + 'mute'), tx = $(o.p + 'vt');
      if(!bar) return;
      var oth = vc.filter(function(x){ return x.id !== myId; });
      btn.classList.toggle('on', on);
      btn.querySelector('span').textContent = T(on ? 'Səsli otaqdan çıx' : 'Səsli otağa gir');
      mb.hidden = !on;
      mb.classList.toggle('off', muted);
      mb.innerHTML = '<i class="fa-solid ' + (muted ? 'fa-microphone-slash' : 'fa-microphone') + '" aria-hidden="true"></i>';
      mb.setAttribute('aria-label', T(muted ? 'Mikrofonu aç' : 'Mikrofonu söndür'));
      var nm = oth.map(function(x){ var p = peers[x.id]; return x.name + (p && p.muted ? ' 🔇' : ''); }).join(', ');
      var t = '';
      if(!on) t = oth.length ? T(nm + ' səsli otaqdadır') : '';
      else if(!oth.length) t = T('Səsli otaqdasan, gözləyirəm...');
      else {
        var all = oth.every(function(x){ var p = peers[x.id]; return p && p.live; });
        t = T('Səsli: ' + nm) + (all ? '' : ' · ' + T('Qoşulur...'));
      }
      if(on && needTap) t += ' · ' + T('Səs üçün toxun');
      tx.textContent = t;
    }

    function closePeer(id){
      var p = peers[id]; if(!p) return;
      delete peers[id];
      try{ p.pc.close(); }catch(e){}
      if(p.aud){ try{ p.aud.pause(); p.aud.srcObject = null; p.aud.remove(); }catch(e){} }
    }
    function mkPeer(id, name, init){
      var pc = new RTCPeerConnection({iceServers: ICE_SERVERS});
      var p = peers[id] = {pc: pc, name: name || '', muted: false, live: false, pend: [], rs: false, init: !!init, aud: null};
      stream.getTracks().forEach(function(t){ pc.addTrack(t, stream); });
      pc.onicecandidate = function(e){
        if(peers[id] === p && e.candidate) sig('ice', id, e.candidate.toJSON()).catch(function(){});
      };
      pc.ontrack = function(e){
        if(peers[id] !== p) return;
        var a = p.aud;
        if(!a){ a = p.aud = document.createElement('audio'); a.autoplay = true; a.setAttribute('playsinline', ''); box.appendChild(a); }
        a.srcObject = e.streams[0];
        var pr = a.play(); if(pr && pr.catch) pr.catch(function(){ needTap = true; render(); });
      };
      pc.onconnectionstatechange = function(){
        if(peers[id] !== p) return;
        var st = pc.connectionState;
        p.live = st === 'connected';
        if(st === 'failed' && p.init && !p.rs){ p.rs = true; offer(id, true); }
        render();
      };
      return p;
    }
    async function offer(id, restart){
      var p = peers[id]; if(!p) return;
      try{
        var of = await p.pc.createOffer({iceRestart: !!restart});
        await p.pc.setLocalDescription(of);
        if(peers[id] !== p) return;
        await sig('offer', id, {type: of.type, sdp: of.sdp});
      }catch(e){}
    }
    async function onSig(m){
      var id = m.f, p = peers[id];
      try{
        if(m.k === 'offer'){
          if(p && p.init){ closePeer(id); p = null; }       // eyni anda təklif: yenisini qəbul et
          if(!p) p = mkPeer(id, m.n, false);
          await p.pc.setRemoteDescription(m.d);
          var q = p.pend.splice(0); for(var i = 0; i < q.length; i++) await p.pc.addIceCandidate(q[i]).catch(function(){});
          var an = await p.pc.createAnswer();
          await p.pc.setLocalDescription(an);
          if(peers[id] !== p) return;
          await sig('answer', id, {type: an.type, sdp: an.sdp});
        }else if(m.k === 'answer'){
          if(!p) return;
          await p.pc.setRemoteDescription(m.d);
          var q2 = p.pend.splice(0); for(var j = 0; j < q2.length; j++) await p.pc.addIceCandidate(q2[j]).catch(function(){});
        }else if(m.k === 'ice'){
          if(!p) return;
          if(p.pc.remoteDescription) await p.pc.addIceCandidate(m.d).catch(function(){});
          else p.pend.push(m.d);
        }
      }catch(e){}
    }
    function onMsg(m){
      if(m.k === 'hello'){ closePeer(m.f); render(); }     // qayıdıb: köhnə bağlantı atılır, o təklif göndərəcək
      else if(m.k === 'bye'){ closePeer(m.f); render(); }
      else if(m.k === 'mute'){ var p = peers[m.f]; if(p){ p.muted = !!m.d; render(); } }
      else onSig(m);
    }
    async function loop(g){
      var fails = 0;
      while(on && gen === g){
        try{
          var r = await api('/api/gvoice/poll?game=' + o.game + '&room=' + enc(rc) + '&since=' + since);
          if(!on || gen !== g) return;
          if(r.status === 404){ leave(true); return; }
          if(!r.ok) throw new Error(String(r.status));
          var d = await r.json(); fails = 0; since = d.seq;
          (d.msgs || []).forEach(onMsg);
        }catch(e){
          if(!on || gen !== g) return;
          if(!token){ leave(true); return; }
          await new Promise(function(z){ setTimeout(z, Math.min(5000, 1000 * (++fails))); });
        }
      }
    }

    async function join(){
      if(on) return;
      var code = o.code(); if(!code) return;
      if(!window.RTCPeerConnection || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia){
        say('Mikrofon bu bağlantıda işləmir. Sayt https ilə açılmalıdır.'); return;
      }
      var st;
      try{
        st = await navigator.mediaDevices.getUserMedia({audio: (window.wtAudioCons ? window.wtAudioCons() : {echoCancellation: true, noiseSuppression: true, autoGainControl: true}), video: false});
      }catch(e){ say('Mikrofon icazəsi verilmədi. Brauzer ayarlarından icazə ver.'); return; }
      if(o.code() !== code || on){ st.getTracks().forEach(function(t){ t.stop(); }); return; }
      if(!iceLoaded){ iceLoaded = true; try{ await loadIce(); }catch(e){} }
      rc = code; stream = st; muted = false; needTap = false; gen++;
      var res;
      try{ res = await sig('hello', '', null, {muted: false}); }
      catch(e){
        st.getTracks().forEach(function(t){ t.stop(); }); stream = null; rc = '';
        say('Əlaqə problemi, yenidən cəhd et'); render(); return;
      }
      on = true; myId = res.id; since = res.seq;
      (res.members || []).forEach(function(m){
        var p = mkPeer(m.id, m.name, true); p.muted = !!m.muted; offer(m.id, false);   // təklifi daxil olan göndərir
      });
      loop(gen);
      render();
      if(window.wtStartMuted && window.wtStartMuted()) toggleMute();
    }
    function leave(quiet){
      if(!on) return;
      on = false; gen++;
      Object.keys(peers).forEach(closePeer);
      if(stream){ stream.getTracks().forEach(function(t){ t.stop(); }); stream = null; }
      var c = rc; rc = ''; myId = ''; muted = false; needTap = false;
      if(c) api('/api/gvoice/send', {method: 'POST', body: JSON.stringify({game: o.game, room: c, k: 'bye', to: '', d: null})}).catch(function(){});
      render();
    }
    function toggleMute(){
      if(!on || !stream) return;
      muted = !muted;
      stream.getAudioTracks().forEach(function(t){ t.enabled = !muted; });
      sig('mute', '', muted).catch(function(){});
      render();
    }
    function playAll(){
      var ok = false;
      Object.keys(peers).forEach(function(id){ var a = peers[id].aud; if(a){ var pr = a.play(); if(pr && pr.then) pr.then(function(){ needTap = false; render(); }).catch(function(){}); ok = true; } });
      if(!ok) needTap = false;
    }

    $(o.p + 'mic').onclick = function(){ if(on) leave(); else join(); };
    $(o.p + 'mute').onclick = toggleMute;
    $(o.p + 'vbar').addEventListener('click', function(e){ if(needTap && !e.target.closest('button')) playAll(); });
    render();
    return {
      leave: leave,
      vk: function(){ return vk; },
      presence: function(list, k){
        k = +k || 0;
        if(k < vk) return;                       // köhnə cavab
        var same = k === vk && JSON.stringify(list) === JSON.stringify(vc);
        vk = k; vc = list || [];
        if(!same) render();
      }
    };
  };
})();

/* ---------- Şahmat otağı (2 nəfər, onlayn) ---------- */
(function(){
  var $ = function(id){ return document.getElementById(id); };
  var LS = 'wt_chess';
  var ch = {code: '', v: 0, s: null, sel: null, gen: 0, flashT: null, flashTx: '', to: null, skip: null, bt: null};
  var busy = false, pending = '';
  var voice = window.wtGameVoice({game: 'chess', p: 'c', code: function(){ return ch.code; }, say: function(t){ flash(t); }});
  var inv = window.wtInviteBox({game: 'chess', user: 'cinvU', go: 'cinvGo', fr: 'cinvFr', ft: 'cinvFrT', msg: 'cinvMsg', code: function(){ return ch.code; }});
  var GL = {K: '\u265A', Q: '\u265B', R: '\u265C', B: '\u265D', N: '\u265E', P: '\u265F'};
  var REASON = {mate: 'Mat', stalemate: 'Pat: gediş yoxdur, bərabərlik', resign: 'Təslim oldu', fifty: '50 gediş qaydası: bərabərlik', repetition: 'Üç dəfə təkrar: bərabərlik', material: 'Mat üçün fiqur çatmır: bərabərlik'};

  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){ return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }
  function clean(c){ return String(c || '').toLowerCase().replace(/[^a-z0-9-]/g, '').slice(0, 30); }
  function avatar(p){ return p && p.avatar ? '<img src="' + esc(p.avatar) + '" alt="">' : esc(((p && p.name) || '?').slice(0, 1).toUpperCase()); }
  function fm(n){ return n % 1 ? n.toFixed(1) : String(n); }
  function remember(c){ try{ if(c) localStorage.setItem(LS, c); else localStorage.removeItem(LS); }catch(e){} refreshResume(); }
  function saved(){ try{ return localStorage.getItem(LS) || ''; }catch(e){ return ''; } }
  function refreshResume(){ var b = $('chResume'); if(b) b.hidden = !saved(); }
  var vrBusy = false;
  async function verifyResume(){
    var c = saved();
    if(!c || vrBusy || !token || ch.code === c) return;
    vrBusy = true;
    try{
      var r = await api('/api/chess/check?room=' + encodeURIComponent(c));
      if(r.status === 404 && saved() === c && ch.code !== c) remember('');
    }catch(e){}
    vrBusy = false;
  }
  window.wtVerifyChess = verifyResume;
  document.addEventListener('visibilitychange', function(){ if(document.visibilityState === 'visible') verifyResume(); });
  function cardMsg(t){ var e = $('chMsg'); if(e) e.textContent = t || ''; }
  function flash(t){
    ch.flashTx = t; clearTimeout(ch.flashT);
    ch.flashT = setTimeout(function(){ ch.flashTx = ''; if(ch.s) renderMsg(); }, 2600);
    renderMsg();
  }
  function colName(c){ return c === 'w' ? 'Ağ' : 'Qara'; }

  var VAL = {P: 1, N: 3, B: 3, R: 5, Q: 9}, STARTN = {P: 8, N: 2, B: 2, R: 2, Q: 1}, ORD = ['P', 'N', 'B', 'R', 'Q'];
  /* col rənginin lövhədən itən daşları (təcrid olunmuş piyada çevrilməsi nəzərə alınır) */
  function lostOf(b, col){
    var cnt = {P: 0, N: 0, B: 0, R: 0, Q: 0}, promo = 0, out = {}, i, p;
    for(i = 0; i < 64; i++){ p = b[i]; if(p && p.charAt(0) === col && cnt[p.charAt(1)] !== undefined) cnt[p.charAt(1)]++; }
    ['N', 'B', 'R', 'Q'].forEach(function(k){ if(cnt[k] > STARTN[k]) promo += cnt[k] - STARTN[k]; });
    out.P = Math.max(0, STARTN.P - cnt.P - promo);
    ['N', 'B', 'R', 'Q'].forEach(function(k){ out[k] = Math.max(0, STARTN[k] - cnt[k]); });
    return out;
  }
  function matOf(b, col){
    var t = 0, i, p;
    for(i = 0; i < 64; i++){ p = b[i]; if(p && p.charAt(0) === col && VAL[p.charAt(1)]) t += VAL[p.charAt(1)]; }
    return t;
  }
  function capHtml(lost, col, adv){
    var h = '';
    ORD.forEach(function(k){
      if(!lost[k]) return;
      var g = '', n;
      for(n = 0; n < lost[k]; n++) g += '<span class="cp ' + col + '">' + GL[k] + '\uFE0E</span>';
      h += '<span class="cg">' + g + '</span>';
    });
    return (h ? '<span class="ccap">' + h + '</span>' : '') + (adv > 0 ? '<span class="cadv">+' + adv + '</span>' : '');
  }

  function renderStrips(s){
    var o = s.opp, oc = s.color === 'w' ? 'b' : 'w';
    var oppCap = '', meCap = '';
    if(s.color && s.b && s.b.length === 64){
      var dmat = matOf(s.b, s.color) - matOf(s.b, oc);
      oppCap = capHtml(lostOf(s.b, s.color), s.color, dmat < 0 ? -dmat : 0);
      meCap = capHtml(lostOf(s.b, oc), oc, dmat > 0 ? dmat : 0);
    }
    $('copp').classList.toggle('turn', s.turn === 'opp');
    $('cme').classList.toggle('turn', s.turn === 'me');
    if(!o){
      $('copp').innerHTML = '<div class="cav">?</div><div class="cwho"><b>Rəqib</b><small>Rəqib gözlənilir...</small></div>';
    }else{
      $('copp').innerHTML = '<div class="cav">' + avatar(o) + '</div><div class="cwho"><b data-nt>' + esc(o.name) + '</b><small><span class="cdot' + (s.opp_online ? ' on' : '') + '"></span>' + colName(oc) + oppCap + '</small></div><div class="csc">' + fm(s.score.opp) + '</div>';
    }
    var m = s.me || {};
    $('cme').innerHTML = '<div class="cav">' + avatar(m) + '</div><div class="cwho"><b data-nt>' + esc(m.name || 'Sən') + '</b><small>' + (s.color ? colName(s.color) : '') + meCap + '</small></div><div class="csc">' + fm(s.score.me) + '</div>';
  }

  function renderBoard(s){
    var bd = $('cbd'), wait = $('cwait');
    var isWait = s.status === 'wait';
    wait.hidden = !isWait; bd.hidden = isWait;
    if(isWait){ $('cwcode').textContent = s.code.toUpperCase(); inv.refresh(); return; }
    bd.innerHTML = '';
    var flip = s.color === 'b';
    var tg = {}, mv = {};
    s.moves.forEach(function(m){ mv[m[0]] = 1; if(ch.sel === m[0]) tg[m[1]] = 1; });
    var turnCol = s.turn === 'me' ? s.color : (s.color === 'w' ? 'b' : 'w');
    if(s.status === 'over') turnCol = s.result === 'me' ? (s.color === 'w' ? 'b' : 'w') : s.color;
    var ks = (s.check && (s.status === 'play' || s.reason === 'mate')) ? s.b.indexOf(turnCol + 'K') : -1;
    /* yeni gediş: fiqur köhnə xanadan sürüşür, tutulanda xanada dalğa effekti */
    var cnt = pieceCount(s.b), mk = s.last ? (s.last.join('-') + '|' + s.b.join(',')) : '';
    var isNew = !!mk && ch.mk != null && mk !== ch.mk, boomSq = (isNew && ch.cnt != null && cnt < ch.cnt) ? s.last[1] : -1;
    var dX = 0, dY = 0;
    if(isNew){
      var ia = flip ? 63 - s.last[0] : s.last[0], ib = flip ? 63 - s.last[1] : s.last[1];
      dX = (ib % 8) - (ia % 8); dY = Math.floor(ib / 8) - Math.floor(ia / 8);
    }
    ch.mk = mk; ch.cnt = cnt;
    for(var i = 0; i < 64; i++){
      var sq = flip ? 63 - i : i, r = Math.floor(sq / 8), c = sq % 8, p = s.b[sq];
      var d = document.createElement('div');
      d.className = 'csq ' + ((r + c) % 2 ? 'dk' : 'lt') +
        (ch.sel === sq ? ' sel' : '') +
        (s.last && (s.last[0] === sq || s.last[1] === sq) ? ' last' : '') +
        (sq === ks ? ' chk' : '') +
        (tg[sq] ? ' tgt' + (p ? ' cap' : '') : '') +
        (sq === boomSq ? ' boom' : '');
      d.setAttribute('data-sq', sq);
      var h = '';
      if(i % 8 === 0) h += '<i class="rk">' + (8 - r) + '</i>';
      if(i >= 56) h += '<i class="fl">' + 'abcdefgh'.charAt(c) + '</i>';
      var mvA = isNew && sq === s.last[1];
      if(p) h += '<span class="pc ' + p.charAt(0) + (mvA ? ' mv' : '') + '"' + (mvA ? ' style="--dx:' + dX + ';--dy:' + dY + '"' : '') + '>' + GL[p.charAt(1)] + '\uFE0E</span>';
      d.innerHTML = h;
      bd.appendChild(d);
    }
  }

  function renderMsg(){
    var s = ch.s, e = $('cmsg');
    if(!s){ e.textContent = ''; return; }
    if(ch.flashTx){ e.textContent = ch.flashTx; return; }
    var t = '';
    if(s.status === 'play'){
      if(s.turn === 'me') t = s.check ? 'Şah! Sənin növbən' : 'Sənin növbən';
      else if(s.bot) t = s.check ? 'Rəqibə şah! Rəqibin növbəsi...' : 'Kompüter düşünür...';
      else t = s.opp_online ? (s.check ? 'Rəqibə şah! Rəqibin növbəsi...' : 'Rəqibin növbəsi...') : 'Rəqib əlaqədə deyil';
    }
    e.textContent = t;
  }

  function renderRes(s){
    var box = $('cres'), over = s.status === 'over';
    box.hidden = !over;
    $('cresign').hidden = s.status !== 'play';
    if(!over){ box.innerHTML = ''; return; }
    var title = s.result === 'me' ? 'Qazandın!' : (s.result === 'opp' ? 'Rəqib qazandı' : 'Bərabərlik');
    box.innerHTML = '<h3>' + title + '</h3><p>' + esc(REASON[s.reason] || '') + '</p><button class="pbtn" id="cagain">Yenidən oyna (rənglər dəyişir)</button>';
    $('cagain').onclick = function(){ again(); };
  }

  function render(){
    var s = ch.s; if(!s) return;
    $('ccode').textContent = 'KOD: ' + s.code.toUpperCase();
    renderStrips(s); renderBoard(s); renderMsg(); renderRes(s);
    $('cshare').hidden = !!s.bot; $('cvbar').hidden = !!s.bot;
    if(!ch.to) $('cpro').hidden = true;
  }

  function pieceCount(b){ var n = 0; for(var i = 0; i < b.length; i++) if(b[i]) n++; return n; }
  /* Gedişin səsi: öz gedişimiz dərhal çalınır (serveri gözləmir), cavab gələndə təkrar çalınmır */
  function optSfx(f, t, promo){
    var S = window.wtSfx, s = ch.s; if(!S || !s || !s.b || s.b.length !== 64) return;
    var p = s.b[f]; if(!p) return;
    var cap = !!s.b[t] || (p.charAt(1) === 'P' && f % 8 !== t % 8);
    var n = (p.charAt(1) === 'K' && Math.abs(t - f) === 2) ? 'castle' : (cap ? 'capture' : 'move');
    if(p.charAt(1) === 'P' && (Math.floor(t / 8) === 0 || Math.floor(t / 8) === 7)) n = 'promote';
    S.play(n); ch.skip = [f, t];
  }
  function sfxChess(prev, s){
    var S = window.wtSfx; if(!S) return;
    if(prev.status === 'wait' || s.round !== prev.round){ if(s.status === 'play') S.play('start'); return; }
    var mv = s.last && (!prev.last || prev.last[0] !== s.last[0] || prev.last[1] !== s.last[1]);
    if(mv){
      var own = ch.skip && ch.skip[0] === s.last[0] && ch.skip[1] === s.last[1];
      ch.skip = null;
      var pc = s.b[s.last[1]], pp = prev.b[s.last[0]];
      var cap = pieceCount(s.b) < pieceCount(prev.b);
      var n = (pc && pc.charAt(1) === 'K' && Math.abs(s.last[1] - s.last[0]) === 2) ? 'castle' : (cap ? 'capture' : 'move');
      if(pc && pp && pp.charAt(1) === 'P' && pc.charAt(1) !== 'P') n = 'promote';
      if(s.check && s.status === 'play') n = own ? 'alert' : 'check';
      else if(own) n = '';
      if(n) S.play(n);
    }
    if(s.status === 'over' && prev.status !== 'over'){
      setTimeout(function(){ S.play(s.result === 'me' ? 'win' : (s.result === 'opp' ? 'lose' : 'tie')); }, mv ? 380 : 0);
    }
  }
  function apply(s){
    voice.presence(s.vc, s.vk);
    var prev = ch.s;
    if(prev && s.v < prev.v) return;
    var changed = !prev || s.v !== prev.v || s.opp_online !== prev.opp_online;
    ch.v = s.v; ch.s = s;
    if(prev && s.round !== prev.round){ ch.sel = null; ch.to = null; }
    if(prev && s.v !== prev.v){ try{ sfxChess(prev, s); }catch(e){} }
    if(s.turn !== 'me'){ ch.sel = null; ch.to = null; }
    if(prev && s.v !== prev.v && prev.turn !== 'me' && s.turn === 'me'){ try{ navigator.vibrate && navigator.vibrate(40); }catch(e){} }
    if(changed) render();
    remember(s.status === 'wait' ? '' : s.code);
    if(s.bot && s.status === 'play' && s.turn === 'opp') scheduleBot();
  }

  function scheduleBot(){
    clearTimeout(ch.bt);
    var g = ch.gen;
    ch.bt = setTimeout(async function(){
      if(ch.gen !== g || !ch.code) return;
      if(busy){ scheduleBot(); return; }
      busy = true;
      try{
        var r = await api('/api/chess/bot', {method: 'POST', body: JSON.stringify({room: ch.code})});
        if(ch.gen === g && r.ok) apply(await r.json());
      }catch(e){ if(ch.gen === g) setTimeout(scheduleBot, 1500); }
      busy = false;
    }, 450 + Math.random() * 350);
  }

  async function post(path, body){
    if(busy || !ch.code) return;
    busy = true;
    try{
      var r = await api('/api/chess/' + path, {method: 'POST', body: JSON.stringify(Object.assign({room: ch.code}, body || {}))});
      if(r.ok) apply(await r.json()); else { ch.skip = null; flash('Alınmadı, yenidən cəhd et'); }
    }catch(e){ ch.skip = null; flash('Əlaqə problemi, yenidən cəhd et'); }
    busy = false;
  }
  function send(f, t, promo){ ch.sel = null; ch.to = null; $('cpro').hidden = true; try{ optSfx(f, t, promo); }catch(e){} post('move', {action: 'move', f: f, t: t, promo: promo}); }
  function again(){ if(ch.s) post('again', {round: ch.s.round}); }

  function tap(sq){
    var s = ch.s;
    if(!s || s.status !== 'play' || s.turn !== 'me' || busy) return;
    if(ch.sel != null && s.moves.some(function(m){ return m[0] === ch.sel && m[1] === sq; })){
      var p = s.b[ch.sel], row = Math.floor(sq / 8);
      if(p && p.charAt(1) === 'P' && (row === 0 || row === 7)){
        ch.to = sq;
        var box = $('cpro'); box.innerHTML = '';
        ['Q', 'R', 'B', 'N'].forEach(function(k){
          var b = document.createElement('button');
          b.innerHTML = '<span class="pc ' + s.color + '" style="font-size:44px">' + GL[k] + '\uFE0E</span>';
          b.style.background = s.color === 'w' ? '#9b5b78' : '#f0dccd';
          b.onclick = function(){ send(ch.sel, ch.to, k); };
          box.appendChild(b);
        });
        box.hidden = false; return;
      }
      send(ch.sel, sq, 'Q'); return;
    }
    if(s.b[sq] && s.b[sq].charAt(0) === s.color && s.moves.some(function(m){ return m[0] === sq; })) ch.sel = (ch.sel === sq ? null : sq);
    else ch.sel = null;
    ch.to = null; $('cpro').hidden = true;
    renderBoard(s);
  }
  $('cbd').addEventListener('click', function(e){ var q = e.target.closest('.csq'); if(q) tap(+q.getAttribute('data-sq')); });

  async function poll(gen){
    var fails = 0;
    while(ch.gen === gen){
      try{
        var r = await api('/api/chess/poll?room=' + encodeURIComponent(ch.code) + '&v=' + ch.v + '&vk=' + voice.vk());
        if(ch.gen !== gen) return;
        if(r.status === 404){ close(false); remember(''); cardMsg('Oyun bitib və ya tapılmadı'); return; }
        if(!r.ok) throw new Error(String(r.status));
        apply(await r.json()); fails = 0;
      }catch(e){
        if(ch.gen !== gen) return;
        if(!token){ close(false); return; }
        await new Promise(function(z){ setTimeout(z, Math.min(5000, 1000 * (++fails))); });
      }
    }
  }

  function open(s){
    clearTimeout(ch.bt);
    ch.gen++; ch.code = s.code; ch.v = 0; ch.s = null; ch.sel = null; ch.to = null; ch.flashTx = ''; busy = false;
    $('chess').hidden = false;
    apply(s);
    poll(ch.gen);
  }
  function close(leave){
    var c = ch.code, st = ch.s && ch.s.status;
    voice.leave();
    clearTimeout(ch.bt);
    ch.gen++; ch.code = ''; ch.s = null;
    $('chess').hidden = true;
    if(leave && c) api('/api/chess/leave', {method: 'POST', body: JSON.stringify({room: c})}).catch(function(){});
    if(st === 'wait' || st === 'over') remember('');
  }

  async function enter(code, isCreate, bot){
    cardMsg('');
    try{
      var r = isCreate
        ? await api('/api/chess/create', {method: 'POST', body: bot ? '{"bot":true}' : '{}'})
        : await api('/api/chess/join', {method: 'POST', body: JSON.stringify({room: code})});
      if(r.ok){ open(await r.json()); return true; }
      if(r.status === 404){ remember(''); cardMsg('Belə oyun tapılmadı. Kodu yoxla.'); }
      else if(r.status === 403) cardMsg('Bu otaq doludur');
      else if(r.status === 429) cardMsg('Çox sorğu göndərdin, bir az gözlə');
      else cardMsg('Əlaqə problemi, yenidən cəhd et');
    }catch(e){ cardMsg('Əlaqə problemi, yenidən cəhd et'); }
    return false;
  }

  async function share(){
    var s = ch.s; if(!s) return;
    var url = location.origin + '/?chess=' + encodeURIComponent(s.code.toUpperCase());
    var text = wtT('Şahmat oynayaq! Kod: ' + s.code.toUpperCase());
    try{ if(navigator.share){ await navigator.share({title: wtT('Şahmat'), text: text, url: url}); return; } }
    catch(e){ if(e && e.name === 'AbortError') return; }
    try{ await navigator.clipboard.writeText(url); flash('Link kopyalandı'); }
    catch(e){ flash('Kod: ' + s.code.toUpperCase()); }
  }

  $('chNew').onclick = function(){ enter('', true); };
  $('chBot').onclick = function(){ enter('', true, true); };
  $('chEnter').onclick = function(){
    var c = clean($('chCodeIn').value);
    if(!c){ cardMsg('Şahmat kodunu yaz'); return; }
    enter(c, false);
  };
  $('chCodeIn').addEventListener('keydown', function(e){ if(e.key === 'Enter'){ e.preventDefault(); $('chEnter').click(); } });
  $('chResume').onclick = function(){ var c = saved(); if(c) enter(c, false); };
  $('cback').onclick = function(){ close(ch.s && (ch.s.status === 'wait' || ch.s.bot)); };
  $('cshare').onclick = share;
  $('cwshare').onclick = share;
  $('cresign').onclick = function(){ wtConfirm('Təslim olmaq istəyirsən?', {ok: 'Təslim ol', danger: true}).then(function(y){ if(y) post('move', {action: 'resign'}); }); };
  $('cpro').addEventListener('click', function(e){ if(e.target === this){ ch.to = null; this.hidden = true; } });
  document.addEventListener('visibilitychange', function(){
    if(document.visibilityState === 'visible' && ch.code && !$('chess').hidden){ ch.gen++; var g = ch.gen; ch.v = 0; poll(g); }
  });

  try{
    var q = new URLSearchParams(location.search).get('chess');
    if(q){ pending = clean(q); history.replaceState(null, '', location.pathname); }
  }catch(e){}
  if(pending){
    var tries = 0, iv = setInterval(function(){
      tries++;
      var menuOn = false;
      try{ menuOn = getComputedStyle($('menu')).display !== 'none'; }catch(e){}
      if(token && account && menuOn){ clearInterval(iv); var c = pending; pending = ''; enter(c, false); }
      else if(tries > 120) clearInterval(iv);
    }, 500);
  }
  window.wtJoinChess = function(c){ enter(clean(c), false); };
  refreshResume();
})();

/* ---------- Domino otağı (2-4 nəfər, onlayn) ---------- */
(function(){
  var $ = function(id){ return document.getElementById(id); };
  var LS = 'wt_dom';
  var dom = {code: '', v: 0, s: null, sel: null, gen: 0, flashT: null, flashTx: '', boardN: -1, sfxDraw: false};
  var busy = false, pending = '';
  var domSize = 2;
  try{ domSize = Math.max(2, Math.min(4, parseInt(localStorage.getItem('wt_domsz') || '2', 10) || 2)); }catch(e){}
  var voice = window.wtGameVoice({game: 'domino', p: 'd', code: function(){ return dom.code; }, say: function(t){ flash(t); }});
  var inv = window.wtInviteBox({game: 'domino', user: 'dinvU', go: 'dinvGo', fr: 'dinvFr', ft: 'dinvFrT', msg: 'dinvMsg', code: function(){ return dom.code; }});
  var PIPS = [[], [4], [0, 8], [0, 4, 8], [0, 2, 6, 8], [0, 2, 4, 6, 8], [0, 2, 3, 5, 6, 8]];

  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){ return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }
  function clean(c){ return String(c || '').toLowerCase().replace(/[^a-z0-9-]/g, '').slice(0, 30); }
  var SIX_H = [0, 1, 2, 6, 7, 8];
  function half(n, horiz){ var pp = (n === 6 && horiz) ? SIX_H : PIPS[n]; var h = '<div class="hf">'; for(var i = 0; i < 9; i++) h += '<i' + (pp.indexOf(i) >= 0 ? ' class="on"' : '') + '></i>'; return h + '</div>'; }
  function tile(t, o){
    o = o || {};
    var el = document.createElement('div');
    el.className = 'tile ' + (o.v ? 'v' : 'h') + (o.cls ? ' ' + o.cls : '');
    el.innerHTML = o.back ? '<div class="hf"></div><div class="hf"></div>' : half(t[0], !o.v) + half(t[1], !o.v);
    if(o.s) el.style.setProperty('--s', o.s + 'px');
    return el;
  }
  function same(a, b){ return a && b && a[0] === b[0] && a[1] === b[1]; }
  function avatar(p){ if(p && p.username === 'bot') return '\uD83E\uDD16'; return p && p.avatar ? '<img src="' + esc(p.avatar) + '" alt="">' : esc(((p && p.name) || '?').slice(0, 1).toUpperCase()); }
  function remember(c){ try{ if(c) localStorage.setItem(LS, c); else localStorage.removeItem(LS); }catch(e){} refreshResume(); }
  function saved(){ try{ return localStorage.getItem(LS) || ''; }catch(e){ return ''; } }
  function refreshResume(){
    var c = saved(), b = $('domResume');
    if(!b) return;
    b.hidden = !c;
    if(c) b.firstChild && (b.textContent = 'Oyuna qayıt');
  }
  /* server yenilənəndə oyunlar silinir: saxlanmış oyun yoxdursa düyməni gizlət */
  var vrBusy = false;
  async function verifyResume(){
    var c = saved();
    if(!c || vrBusy || !token || dom.code === c) return;
    vrBusy = true;
    try{
      var r = await api('/api/domino/check?room=' + encodeURIComponent(c));
      if(r.status === 404 && saved() === c && dom.code !== c) remember('');
    }catch(e){}
    vrBusy = false;
  }
  window.wtVerifyDomino = verifyResume;
  document.addEventListener('visibilitychange', function(){ if(document.visibilityState === 'visible') verifyResume(); });
  function cardMsg(t){ var e = $('domMsg'); if(e) e.textContent = t || ''; }

  function flash(t){
    dom.flashTx = t; clearTimeout(dom.flashT);
    dom.flashT = setTimeout(function(){ dom.flashTx = ''; if(dom.s) renderMsg(); }, 2600);
    renderMsg();
  }

  /* ---- çəkmə ---- */
  function chip(o, me, s){
    var nm = me ? 'Sən' : (o.name || '?');
    var cnt = me ? s.hand.length : o.n;
    return '<div class="dchip' + (o.turn ? ' turn' : '') + '"><div class="dav">' + avatar(me ? s.me : o) + '</div><div class="dwho"><b data-nt>' + esc(nm) + '</b><small>' +
      (me ? '' : '<span class="donl' + (o.online ? ' on' : '') + '"></span>') + cnt + ' daş · ' + (o.score || 0) + ' xal</small></div></div>';
  }
  function renderStrip(s){
    var box = $('dpl'), os = s.opps || [], multi = s.size > 2 || os.length > 1;
    box.classList.toggle('multi', multi);
    if(multi){
      box.classList.remove('turn');
      var h = '';
      os.forEach(function(o){ h += chip(o, false, s); });
      for(var k = os.length; k < s.size - 1; k++) h += '<div class="dchip"><div class="dav">?</div><div class="dwho"><b>Gözlənilir</b></div></div>';
      h += chip({turn: s.turn === 'me', score: s.my_score}, true, s);
      box.innerHTML = h;
      return;
    }
    var o = os[0];
    box.classList.toggle('turn', s.turn === 'opp');
    if(!o){
      box.innerHTML = '<div class="dav">?</div><div class="dwho"><b>Rəqib</b><small><span>Rəqib gözlənilir...</span></small></div>';
      return;
    }
    box.innerHTML = '<div class="dav">' + avatar(o) + '</div>' +
      '<div class="dwho"><b data-nt>' + esc(o.name) + '</b><small><span class="donl' + (o.online ? ' on' : '') + '"></span>' +
      '<span>' + o.n + '</span> <span>daş</span> · <span>Bazar</span> <span>' + s.stock + '</span></small></div>' +
      '<div class="dsc"><small><span>Xal</span> · ' + s.target + '</small>' + s.my_score + ' : ' + o.score + '</div>';
  }

  function renderBoard(s){
    var ch = $('dchain'), wait = $('dwait');
    var isWait = s.status === 'wait';
    wait.hidden = !isWait; ch.hidden = isWait;
    if(isWait){
      $('dwcode').textContent = s.code.toUpperCase();
      $('dwcnt').textContent = s.size > 2 ? ('Oyunçular: ' + s.joined + ' / ' + s.size) : '';
      inv.refresh();
      return;
    }
    ch.innerHTML = '';
    var sm = dom.sel && s.moves.filter(function(m){ return same(m.t, dom.sel); })[0];
    var both = !!(sm && sm.e.length > 1);
    function endBtn(side){
      var b = document.createElement('button');
      b.className = 'dend'; b.setAttribute('aria-label', side === 'L' ? 'Sola' : 'Sağa');
      b.innerHTML = '<i class="fa-solid fa-plus" aria-hidden="true"></i>';
      b.onclick = function(){ var t = dom.sel; dom.sel = null; act('play', {t: t, end: side}); };
      return {endBtn: true, el: b, dbl: false};
    }
    if(!s.board.length){
      var d = document.createElement('div'); d.className = 'dempty';
      d.textContent = s.turn === 'me' ? 'Sən başlayırsan: daş seç' : ((s.opps || []).length > 1 ? (s.turn_name || '') + ' başlayır...' : 'Rəqib başlayır...');
      ch.appendChild(d);
    }
    var n = s.board.length;
    var ci = (typeof s.cx === 'number' && s.cx >= 0 && s.cx < n) ? s.cx : Math.floor((n - 1) / 2);
    var grew = dom.boardN >= 0 && n > dom.boardN; dom.boardN = n;
    function mk(i){
      var t = s.board[i], dbl = t[0] === t[1];
      var isLast = s.last && same(s.last.t, [Math.min(t[0], t[1]), Math.max(t[0], t[1])]);
      return {t: t, dbl: dbl, cls: isLast ? 'last' + (grew ? ' drop' : '') : ''};
    }
    var arms = {C: n ? mk(ci) : null, L: [], R: []};
    for(var i = ci - 1; i >= 0; i--) arms.L.push(mk(i));
    for(var j = ci + 1; j < n; j++) arms.R.push(mk(j));
    if(both){
      arms.L.push(endBtn('L'));
      arms.R.push(endBtn('R'));
    }
    dom.arms = n ? arms : null;
    fit();
  }

  /* Düzülüş: ortada düz cərgə, kənara çatanda 90° dönən qollar.
     Sağ qol: sağa → yuxarı → sola → yuxarı → sağa ...
     Sol qol: sola → aşağı → sağa → aşağı → sola ... (qoşa daş həmişə hərəkət istiqamətinə dikdir) */
  function layout(arms, s, W){
    var th = s * .78, G = 4, hw = W / 2, VRUN = 2, recs = [];
    function box(it, vert){ return vert ? {w: th, h: 2 * s} : {w: 2 * s, h: th}; }
    function rec(it, cx, cy, vert, flip){
      var b = box(it, vert), r = {it: it, cx: cx, cy: cy, w: b.w, h: b.h, vert: vert, flip: flip};
      r.l = cx - b.w / 2; r.r = cx + b.w / 2; r.t = cy - b.h / 2; r.b = cy + b.h / 2;
      recs.push(r); return r;
    }
    var c = rec(arms.C, 0, 0, !!arms.C.dbl, false);
    function walk(list, side){
      var dx = side === 'R' ? 1 : -1, dy = 0, vdir = side === 'R' ? -1 : 1, hdir = dx;
      var prev = c, far, axis = 0, run = 0;
      far = dx > 0 ? c.r : c.l;
      for(var i = 0; i < list.length; i++){
        var it = list[i], dbl = !!it.dbl, vert = (dy !== 0) !== dbl, b = box(it, vert), cx, cy, turn = false;
        if(i > 0){
          if(dy === 0){
            var cxs = far + dx * (G + b.w / 2);
            if(Math.abs(cxs + dx * b.w / 2) > hw + .5) turn = true;
          }else if(run >= VRUN) turn = true;
        }
        if(turn){
          if(dy === 0){            /* üfüqidən şaquliyə: keçən daşın üst/alt kənarına, ucuna düz */
            dx = 0; dy = vdir;
            vert = !dbl; b = box(it, vert);
            cy = dy < 0 ? prev.t - G - b.h / 2 : prev.b + G + b.h / 2;
            cx = hdir > 0 ? prev.r - b.w / 2 : prev.l + b.w / 2;
            axis = cx; far = cy + dy * b.h / 2; run = 1;
          }else{                   /* şaquli → üfüqi: əks tərəfə */
            hdir = -hdir; dx = hdir; var pdy = dy; dy = 0;
            vert = dbl; b = box(it, vert);
            cx = dx > 0 ? prev.r + G + b.w / 2 : prev.l - G - b.w / 2;
            cy = pdy < 0 ? prev.t + b.h / 2 : prev.b - b.h / 2;
            axis = cy; far = cx + dx * b.w / 2; run = 0;
          }
        }else if(dy === 0){
          cx = far + dx * (G + b.w / 2); cy = axis; far = cx + dx * b.w / 2;
        }else{
          cy = far + dy * (G + b.h / 2); cx = axis; far = cy + dy * b.h / 2; run++;
        }
        /* a→b istiqaməti: sağ qolda hərəkət istiqaməti, sol qolda əksi */
        var ox = side === 'R' ? dx : -dx, oy = side === 'R' ? dy : -dy;
        var flip = dbl ? false : (vert ? oy < 0 : ox < 0);
        prev = rec(it, cx, cy, vert, flip);
      }
    }
    walk(arms.R, 'R'); walk(arms.L, 'L');
    return recs;
  }
  function fit(){
    var arms = dom.arms, bd = $('dboard'), ch = $('dchain');
    if(!arms || !bd || !ch) return;
    var CW = bd.clientWidth, CH = bd.clientHeight;
    var W = CW - 24, H = CH - 24;
    if(W <= 0 || H <= 0) return;
    var best = 12, recs = null;
    for(var sz = 38; sz >= 12; sz--){
      var rs = layout(arms, sz, W), ok = true;
      for(var i = 0; i < rs.length; i++){
        if(Math.max(Math.abs(rs[i].l), Math.abs(rs[i].r)) > W / 2 + 1 || Math.max(Math.abs(rs[i].t), Math.abs(rs[i].b)) > H / 2){ ok = false; break; }
      }
      if(ok){ best = sz; recs = rs; break; }
    }
    if(!recs) recs = layout(arms, best, W);
    ch.innerHTML = '';
    recs.forEach(function(r){
      var it = r.it, el;
      if(it.endBtn){
        el = it.el;
      }else{
        el = tile(it.t, {v: r.vert, cls: it.cls, s: best});
        if(r.flip) el.classList.add('flip');
      }
      el.style.left = Math.round(CW / 2 + r.cx - r.w / 2) + 'px';
      el.style.top = Math.round(CH / 2 + r.cy - r.h / 2) + 'px';
      if(it.endBtn){ el.style.width = Math.round(r.w) + 'px'; el.style.height = Math.round(r.h) + 'px'; }
      ch.appendChild(el);
    });
  }
  window.addEventListener('resize', function(){ if(dom.s && !$('domino').hidden) fit(); });
  if(window.ResizeObserver){
    try{ new ResizeObserver(function(){ if(dom.s && !$('domino').hidden) fit(); }).observe($('dboard')); }catch(e){}
  }

  function renderHand(s){
    var h = $('dhand'); h.innerHTML = '';
    var mine = s.status === 'play' && s.turn === 'me';
    var old = dom.handKeys, keys = {};
    s.hand.forEach(function(t){
      var ok = mine && s.moves.some(function(m){ return same(m.t, t); });
      var key = t[0] + '-' + t[1]; keys[key] = 1;
      var el = tile(t, {v: true, s: 40, cls: (mine ? (ok ? 'ok' : 'dim') : '') + (dom.sel && same(dom.sel, t) ? ' sel' : '') + (old && !old[key] ? ' pop' : '')});
      el.onclick = function(){ pick(t); };
      h.appendChild(el);
    });
    dom.handKeys = keys;
  }

  function renderMsg(){
    var s = dom.s, e = $('dmsg');
    if(!s){ e.textContent = ''; return; }
    if(dom.flashTx){ e.textContent = dom.flashTx; return; }
    var t = '', multi = (s.opps || []).length > 1;
    if(s.status === 'play'){
      if(s.turn === 'me') t = s.moves.length ? 'Sənin növbən: daş seç' : (s.can_draw ? 'Gedişin yoxdur, bazardan daş çək' : '');
      else if(s.bot) t = 'Kompüter düşünür...';
      else if(multi) t = (s.turn_name || 'Rəqib') + (s.turn_online ? ' oynayır...' : ' əlaqədə deyil');
      else t = s.turn_online ? 'Rəqibin növbəsi...' : 'Rəqib əlaqədə deyil';
    }
    e.textContent = t;
  }

  function renderRes(s){
    var box = $('dres'), over = s.status === 'over', multi = (s.opps || []).length > 1;
    box.hidden = !over;
    $('dhand').style.display = over ? 'none' : '';
    if(!over){ box.innerHTML = ''; return; }
    var wn = multi ? esc(s.winner_name || '?') : 'Rəqib';
    var title = s.match ? (s.match === 'me' ? 'Oyunu qazandın!' : (multi ? esc(s.match_name || '?') + ' oyunu qazandı' : 'Rəqib oyunu qazandı'))
      : (s.result === 'me' ? 'Qazandın!' : (s.result === 'opp' ? (multi ? wn + ' qazandı' : 'Rəqib qazandı') : 'Bərabərlik'));
    var why = s.reason === 'out' ? 'Bütün daşlar bitdi' : (s.reason === 'left' ? (s.result === 'me' ? 'Rəqib oyundan çıxdı' : 'Oyundan çıxdın') : 'Oyun bloklandı, daşların cəminə görə');
    var pts = s.pts ? '<div class="dpts">' + (s.result === 'opp' ? '<span>' + wn + '</span> ' : '') + '+' + s.pts + ' <span>xal</span></div>' : '';
    box.innerHTML = '<h3>' + title + '</h3>' + pts + '<p>' + why + '</p><div class="dleftw"></div><button class="pbtn" id="dagain">' + (s.match ? 'Yeni oyun' : 'Növbəti raund') + '</button>';
    var L = box.querySelector('.dleftw');
    (s.opp_hands || []).forEach(function(oh){
      if(multi){ var lb = document.createElement('div'); lb.className = 'dlab'; lb.textContent = oh.name; L.appendChild(lb); }
      var row = document.createElement('div'); row.className = 'dleft';
      oh.hand.forEach(function(t){ row.appendChild(tile(t, {v: true, s: 22})); });
      L.appendChild(row);
    });
    $('dagain').onclick = function(){ again(); };
  }

  /* ---- Bazar: masanın kənarında üzü aşağı daş yığını; hər toxunuşda bir daş götürülür ---- */
  function rnd(i){ var x = Math.sin(i * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); }
  function renderPile(s){
    var p = $('dpile'); if(!p) return;
    if(s.status !== 'play'){ p.hidden = true; p.innerHTML = ''; return; }
    p.hidden = false;
    var n = s.stock || 0, can = !!s.can_draw;
    p.classList.toggle('can', can);
    var h = '<div class="pl"><span>' + (can ? 'Bazardan daş çək' : 'Bazar') + '</span> <b>' + n + '</b></div>';
    var cnt = Math.min(n, 26);
    for(var i = 0; i < cnt; i++){
      var f = rnd(i + 1), top = 4 + rnd(i + 50) * 16, rot = Math.round((rnd(i + 99) - .5) * 120);
      h += '<div class="pt" style="--k:' + (i % 9) + ';left:calc(84px + (100% - 84px - 34px) * ' + f.toFixed(3) + ');top:' + top.toFixed(1) + 'px;z-index:' + (i + 1) + ';transform:rotate(' + rot + 'deg)"></div>';
    }
    p.innerHTML = h;
  }
  $('dpile').addEventListener('click', function(e){
    var el = e.target.closest ? e.target.closest('.pt') : null;
    if(!el) return;
    var s = dom.s;
    if(!s || s.status !== 'play') return;
    if(!s.can_draw || busy){ if($('ddraw').onclick) $('ddraw').onclick({target: el}); return; }
    el.classList.add('taken');
    try{ navigator.vibrate && navigator.vibrate(15); }catch(x){}
    $('ddraw').onclick({target: el});
  });

  function renderAct(s){
    var b = $('ddraw');
    var show = s.status === 'play' && s.stock > 0;      /* bazar həmişə görünür: özün bir-bir götürürsən */
    b.hidden = !show;
    b.classList.remove('busy');
    b.classList.toggle('on', !!s.can_draw);
    b.classList.toggle('dim', !s.can_draw);
    b.setAttribute('aria-label', 'Bazar: ' + s.stock);
    var stk = dom.stk || (dom.stk = []);
    while(stk.length > s.stock){ var ri = (dom.tapIdx != null && dom.tapIdx < stk.length) ? dom.tapIdx : stk.length - 1; stk.splice(ri, 1); dom.tapIdx = null; }
    while(stk.length < s.stock) stk.push({});
    b.innerHTML = '<div class="dstk-l"><small>Bazar</small><b>' + s.stock + '</b></div><div class="dstk-p"></div>';
    var pp = b.lastChild;
    var ANG = [-32, 18, -12, 40, -26, 8, -38, 22, -6, 30];   /* sabit, səpələnmiş bucaqlar: şəkildəki kimi */
    if(show) stk.forEach(function(o, i){
      /* iki yığın, şəkildəki kimi: solda 4 daş, sağda qalanları bir-birinin üstündə */
      var tl = tile(null, {v: true, back: true}), L, T;
      if(i < 4){ L = 4 + i * 11; T = 6 + (i % 2) * 12; }
      else { var j = i - 4; L = 56 + (j % 5) * 8 + Math.floor(j / 5) * 6; T = 4 + ((j * 7) % 4) * 5; }
      tl.style.left = L + '%';
      tl.style.top = T + 'px';
      tl.style.rotate = ANG[i % ANG.length] + 'deg';
      tl.style.zIndex = i;
      tl.setAttribute('role', 'button'); pp.appendChild(tl);
    });
    $('dclaim').hidden = !(s.status === 'play' && s.turn === 'opp' && !s.turn_online && !s.bot);
  }

  /* açıq uclar + əlindəki xal */
  function renderExtra(s){
    var e = $('dends'), t = '';
    if(s.status === 'play' || s.status === 'over'){
      if(s.board.length) t += 'Açıq uclar: <b>' + s.board[0][0] + '</b> · <b>' + s.board[s.board.length - 1][1] + '</b>';
      if((s.opps || []).length > 1) t += (t ? ' &nbsp;|&nbsp; ' : '') + 'Bazar: <b>' + s.stock + '</b>';
      if(s.status === 'play' && s.hand.length){
        var sum = s.hand.reduce(function(a, x){ return a + x[0] + x[1]; }, 0);
        t += (t ? ' &nbsp;|&nbsp; ' : '') + 'Əlindəki xal: <b>' + sum + '</b>';
      }
    }
    e.innerHTML = t;
  }

  function render(){
    var s = dom.s; if(!s) return;
    $('dcode').textContent = 'KOD: ' + s.code.toUpperCase();
    renderStrip(s); renderBoard(s); renderHand(s); renderPile(s); renderMsg(); renderRes(s); renderAct(s); renderExtra(s);
    $('dshare').hidden = !!s.bot;
  }

  /* ---- vəziyyət ---- */
  function onlKey(s){ return (s.opps || []).map(function(o){ return o.online ? 1 : 0; }).join('') + '|' + (s.joined || 0); }
  function drawSfx(n){ for(var i = 0; i < n; i++) setTimeout(function(){ if(window.wtSfx) wtSfx.play('take'); }, i * 150); }
  function sfxDom(prev, s){
    var S = window.wtSfx; if(!S) return;
    if(s.round !== prev.round || (prev.status === 'wait' && s.status === 'play')){ S.play('shuffle'); return; }
    if(prev.status === 'wait'){ if((s.joined || 0) > (prev.joined || 0)) S.play('join'); return; }
    var snd = false, pb = (prev.board || []).length, nb = (s.board || []).length;
    if(nb > pb){ S.play('place'); snd = true; }
    else{
      var grew = Math.max(0, s.hand.length - prev.hand.length);
      var mineGrew = grew > 0;
      (s.opps || []).forEach(function(o, i){ var po = prev.opps && prev.opps[i]; if(po && o.n > po.n) grew += o.n - po.n; });
      if(grew > 0){
        if(mineGrew && dom.sfxDraw) dom.sfxDraw = false; else drawSfx(Math.min(grew, 3));
        snd = true;
      }else if(s.note && s.note.k === 'pass'){ S.play('pass'); snd = true; }
    }
    if(s.status === 'over' && prev.status !== 'over'){
      var res = s.match ? s.match : s.result;
      setTimeout(function(){ S.play(res === 'me' ? 'win' : (res === 'opp' ? 'lose' : 'tie')); }, snd ? 380 : 0);
    }else if(prev.turn !== 'me' && s.turn === 'me' && s.status === 'play'){
      setTimeout(function(){ S.play('turn'); }, snd ? 260 : 0);
    }
  }
  function apply(s, force){
    voice.presence(s.vc, s.vk);
    var prev = dom.s;
    if(prev && s.v < prev.v) return;                         // köhnə cavab
    var changed = force || !prev || s.v !== prev.v || onlKey(s) !== onlKey(prev);
    dom.v = s.v; dom.s = s;
    if(prev && s.round !== prev.round){ dom.sel = null; dom.boardN = -1; dom.handKeys = null; dom.stk = null; }
    if(dom.sel && !s.hand.some(function(t){ return same(t, dom.sel); })) dom.sel = null;
    if(s.turn !== 'me') dom.sel = null;
    if(prev && s.v !== prev.v){
      if(prev.turn !== 'me' && s.turn === 'me'){ try{ navigator.vibrate && navigator.vibrate(40); }catch(e){} }
      try{ sfxDom(prev, s); }catch(e){}
      if(s.note){
        var mp = (s.opps || []).length > 1;
        if(s.note.k === 'pass') flash(s.note.mine ? 'Gedişin olmadığı üçün keçdin' : (mp ? s.note.name + ' keçdi' : 'Rəqib keçdi'));
        else if(s.note.k === 'draw' && !s.note.mine) flash(mp ? s.note.name + ' bazardan daş çəkdi' : 'Rəqib bazardan daş çəkdi');
      }
    }
    if(s.bot && s.status === 'play' && s.turn === 'opp') scheduleBot();
    if(changed) render();
    remember(s.status === 'wait' ? '' : s.code);
  }

  /* Daş qoyanda ekran dərhal yenilənir (serverin cavabını gözləmir); cavab gələndə təsdiqlənir və ya geri qaytarılır */
  function predictPlay(s, t, end){
    if(!s || s.hand.length < 2) return null;                 // son daş: nəticəni server bildirsin
    var n = JSON.parse(JSON.stringify(s)), a = t[0], b = t[1], bd = n.board;
    if(!bd.length){ bd.push([a, b]); n.cx = 0; }
    else if(end === 'L'){ bd.unshift(b === bd[0][0] ? [a, b] : [b, a]); n.cx = (n.cx || 0) + 1; }
    else bd.push(a === bd[bd.length - 1][1] ? [a, b] : [b, a]);
    n.hand = n.hand.filter(function(x){ return !same(x, t); });
    n.moves = []; n.can_draw = false; n.turn = 'opp'; n.note = null;
    (n.opps || []).forEach(function(o, i){ o.turn = i === 0; });
    if(n.opps && n.opps[0]){ n.turn_name = n.opps[0].name; n.turn_online = true; }
    n.last = {t: [Math.min(a, b), Math.max(a, b)]};
    return n;
  }
  async function act(kind, extra){
    if(busy || !dom.code) return;
    busy = true;
    var snap = dom.s, pred = null;
    if(kind === 'play' && snap){
      pred = predictPlay(snap, extra.t, extra.end);
      if(pred){ dom.s = pred; dom.sel = null; if(window.wtSfx) wtSfx.play('place'); render(); }
    }else if(kind === 'draw'){
      dom.sfxDraw = true;
      $('ddraw').classList.add('busy');
      if(window.wtSfx) wtSfx.play('take');
    }
    var ok = false;
    try{
      var r = await api('/api/domino/move', {method: 'POST', body: JSON.stringify(Object.assign({room: dom.code, action: kind}, extra || {}))});
      if(r.ok){ ok = true; apply(await r.json(), true); } else flash('Alınmadı, yenidən cəhd et');
    }catch(e){ flash('Əlaqə problemi, yenidən cəhd et'); }
    if(!ok && dom.s){ if(pred) dom.s = snap; dom.sfxDraw = false; render(); }
    dom.sfxDraw = false;
    busy = false;
  }
  function scheduleBot(){
    clearTimeout(dom.bt);
    var g = dom.gen;
    dom.bt = setTimeout(async function(){
      if(dom.gen !== g || !dom.code) return;
      if(busy){ scheduleBot(); return; }
      busy = true;
      try{
        var r = await api('/api/domino/bot', {method: 'POST', body: JSON.stringify({room: dom.code})});
        if(dom.gen === g && r.ok) apply(await r.json());
      }catch(e){ if(dom.gen === g) setTimeout(scheduleBot, 1500); }
      busy = false;
    }, 450 + Math.random() * 350);
  }
  async function again(){
    if(!dom.s || busy) return;
    busy = true;
    try{
      var r = await api('/api/domino/again', {method: 'POST', body: JSON.stringify({room: dom.code, round: dom.s.round})});
      if(r.ok) apply(await r.json());
    }catch(e){ flash('Əlaqə problemi, yenidən cəhd et'); }
    busy = false;
  }

  function pick(t){
    var s = dom.s;
    if(!s || s.status !== 'play' || s.turn !== 'me') return;
    var m = s.moves.filter(function(m){ return same(m.t, t); })[0];
    if(!m){ flash('Bu daş yerinə düşmür'); return; }
    if(m.e.length === 1){ dom.sel = null; act('play', {t: t, end: m.e[0]}); return; }
    dom.sel = same(dom.sel, t) ? null : t;
    renderBoard(s); renderHand(s);
  }

  /* ---- canlı yenilənmə ---- */
  async function poll(gen){
    var fails = 0;
    while(dom.gen === gen){
      try{
        var r = await api('/api/domino/poll?room=' + encodeURIComponent(dom.code) + '&v=' + dom.v + '&vk=' + voice.vk());
        if(dom.gen !== gen) return;
        if(r.status === 404){ close(false); remember(''); cardMsg('Oyun bitib və ya tapılmadı'); return; }
        if(!r.ok) throw new Error(String(r.status));
        apply(await r.json()); fails = 0;
      }catch(e){
        if(dom.gen !== gen) return;
        if(!token){ close(false); return; }
        await new Promise(function(z){ setTimeout(z, Math.min(5000, 1000 * (++fails))); });
      }
    }
  }

  function open(s){
    dom.gen++; dom.code = s.code; dom.v = 0; dom.s = null; dom.sel = null; dom.boardN = -1; dom.handKeys = null; dom.stk = null;
    dom.flashTx = ''; busy = false;
    $('domino').hidden = false;
    $('dhand').style.display = '';
    apply(s);
    poll(dom.gen);
  }
  function close(leave){
    var c = dom.code, st = dom.s && dom.s.status;
    voice.leave();
    dom.gen++; dom.code = ''; dom.s = null; clearTimeout(dom.bt);
    $('domino').hidden = true;
    if(leave && c){
      api('/api/domino/leave', {method: 'POST', body: JSON.stringify({room: c})}).catch(function(){});
    }
    if(st === 'wait' || st === 'over') remember('');
  }

  async function enter(code, isCreate, bot){
    cardMsg('');
    try{
      var r = isCreate
        ? await api('/api/domino/create', {method: 'POST', body: bot ? '{"bot":true}' : JSON.stringify({size: domSize})})
        : await api('/api/domino/join', {method: 'POST', body: JSON.stringify({room: code})});
      if(r.ok){ open(await r.json()); return true; }
      if(r.status === 404){ remember(''); cardMsg('Belə oyun tapılmadı. Kodu yoxla.'); }
      else if(r.status === 403) cardMsg('Bu otaq doludur');
      else if(r.status === 429) cardMsg('Çox sorğu göndərdin, bir az gözlə');
      else cardMsg('Əlaqə problemi, yenidən cəhd et');
    }catch(e){ cardMsg('Əlaqə problemi, yenidən cəhd et'); }
    return false;
  }

  async function share(){
    var s = dom.s; if(!s) return;
    var url = location.origin + '/?domino=' + encodeURIComponent(s.code.toUpperCase());
    var text = wtT('Domino oynayaq! Kod: ' + s.code.toUpperCase());
    try{
      if(navigator.share){ await navigator.share({title: 'Domino', text: text, url: url}); return; }
    }catch(e){ if(e && e.name === 'AbortError') return; }
    try{ await navigator.clipboard.writeText(url); flash('Link kopyalandı'); }
    catch(e){ flash('Kod: ' + s.code.toUpperCase()); }
  }

  /* ---- düymələr ---- */
  function paintSize(){ [].forEach.call(document.querySelectorAll('#domSize button'), function(b){ b.classList.toggle('on', +b.getAttribute('data-n') === domSize); }); }
  [].forEach.call(document.querySelectorAll('#domSize button'), function(b){
    b.onclick = function(){ domSize = +b.getAttribute('data-n'); try{ localStorage.setItem('wt_domsz', String(domSize)); }catch(e){} paintSize(); };
  });
  paintSize();
  $('domNew').onclick = function(){ enter('', true); };
  $('domBot').onclick = function(){ enter('', true, true); };
  $('domEnter').onclick = function(){
    var c = clean($('domCodeIn').value);
    if(!c){ cardMsg('Domino kodunu yaz'); return; }
    enter(c, false);
  };
  $('domCodeIn').addEventListener('keydown', function(e){ if(e.key === 'Enter'){ e.preventDefault(); $('domEnter').click(); } });
  $('domResume').onclick = function(){ var c = saved(); if(c) enter(c, false); };
  $('dback').onclick = function(){
    close(true);      /* oyundan çıxanda server rəqibə dərhal "əlaqədə deyil" göstərsin */
  };
  $('dshare').onclick = share;
  $('dquit').onclick = function(){
    var s = dom.s, c = dom.code;
    if(!s || !c) return;
    var msg = s.status === 'play' ? 'Oyundan çıxmaq istəyirsən? Oyun sənin üçün bitəcək.' : 'Oyundan çıxmaq istəyirsən?';
    wtConfirm(msg, {ok: 'Oyundan çıx', danger: true}).then(function(y){
      if(!y || dom.code !== c) return;
      close(false); remember('');
      api('/api/domino/quit', {method: 'POST', body: JSON.stringify({room: c})}).catch(function(){});
    });
  };
  $('dwshare').onclick = share;
  $('ddraw').onclick = function(ev){      /* hər basışda bir daş */
    if(!ev || !ev.target || !ev.target.closest) ev = {target: document.body};
    if(!ev.target.closest('.tile') && !ev.target.closest('.pt')) return;
    var s = dom.s;
    if(!s || s.status !== 'play') return;
    if(!s.can_draw){ flash(s.turn !== 'me' ? 'Hələ sənin növbən deyil' : (s.moves && s.moves.length ? 'Qoyacaq daşın var, bazardan çəkmək olmaz' : 'Bazar boşdur')); return; }
    var tp = ev.target.closest('.tile'); if(tp){ dom.tapIdx = Array.prototype.indexOf.call(tp.parentNode.children, tp); tp.classList.add('taken'); }
    act('draw');
  };
  $('dclaim').onclick = async function(){
    if(!dom.code || busy) return;
    busy = true;
    try{
      var r = await api('/api/domino/claim', {method: 'POST', body: JSON.stringify({room: dom.code})});
      if(r.ok){ apply(await r.json()); if(dom.s && dom.s.status === 'play') flash('Rəqibə 1 dəqiqə vaxt ver, sonra yenə bas'); }
    }catch(e){ flash('Əlaqə problemi, yenidən cəhd et'); }
    busy = false;
  };
  document.addEventListener('visibilitychange', function(){
    if(document.visibilityState === 'visible' && dom.code && !$('domino').hidden){
      var c = dom.code; dom.gen++; var g = dom.gen; dom.v = 0; poll(g);      // telefon yatıbsa, yenidən qoşul
    }
  });

  /* ---- ?domino=KOD linki ---- */
  try{
    var q = new URLSearchParams(location.search).get('domino');
    if(q){ pending = clean(q); history.replaceState(null, '', location.pathname); }
  }catch(e){}
  if(pending){
    var tries = 0, iv = setInterval(function(){
      tries++;
      var menuOn = false;
      try{ menuOn = getComputedStyle($('menu')).display !== 'none'; }catch(e){}
      if(token && account && menuOn){ clearInterval(iv); var c = pending; pending = ''; enter(c, false); }
      else if(tries > 120) clearInterval(iv);
    }, 500);
  }
  window.wtJoinDomino = function(c){ enter(clean(c), false); };
  refreshResume();
})();
