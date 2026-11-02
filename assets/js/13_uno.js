
/* ---------- UNO otağı (2-4 nəfər, onlayn və ya kompüterə qarşı) ---------- */
(function(){
  var $ = function(id){ return document.getElementById(id); };
  var LS = 'wt_uno';
  var un = {code: '', v: 0, vk: 0, s: null, hs: [], pend: null, flashT: null, flashTx: '', sizeN: 2, botT: 0, landK: 0, pile: [], prevCnt: null, conf: ''};
  var busy = false, gen = 0, pendingUno = '';
  try{ un.sizeN = Math.max(2, Math.min(4, parseInt(localStorage.getItem('wt_unosz') || '2', 10) || 2)); }catch(e){}
  var voice = window.wtGameVoice({game: 'uno', p: 'un', code: function(){ return un.code; }, say: function(t){ flash(t); }});
  var inv = window.wtInviteBox({game: 'uno', user: 'uninvU', go: 'uninvGo', fr: 'uninvFr', ft: 'uninvFrT', msg: 'uninvMsg', code: function(){ return un.code; }});
  var CN = {R: 'cR', Y: 'cY', G: 'cG', B: 'cB', K: 'cK'};
  var CNAME = {R: 'Qırmızı', Y: 'Sarı', G: 'Yaşıl', B: 'Mavi'};
  var RC = {R: '#e53935', Y: '#f9c10d', G: '#2fa44f', B: '#1f78d1'};
  var CORD = 'RYGBK', VORD = '0123456789SVDWF';
  var SK = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.2" fill="none" stroke="currentColor" stroke-width="3.2"/><path d="M6.2 17.8L17.8 6.2" stroke="currentColor" stroke-width="3.2" stroke-linecap="round"/></svg>';
  var RV = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 8.5h14M13.5 4l4.5 4.5-4.5 4.5"/><path d="M20.5 15.5h-14M10.5 11l-4.5 4.5 4.5 4.5"/></svg>';
  var COR = {S: '\u2298', V: '\u21C4', D: '+2', F: '+4', W: '\u2605'};

  function T(s){ try{ return window.wtT ? window.wtT(s) : s; }catch(e){ return s; } }
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){ return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]; }); }
  function clean(c){ return String(c || '').toLowerCase().replace(/[^a-z0-9-]/g, '').slice(0, 30); }
  function enc(x){ return encodeURIComponent(x); }
  function sleep(ms){ return new Promise(function(r){ setTimeout(r, ms); }); }
  function key(c){ return c[0] + c[1]; }
  function cardHtml(c, cls){
    var v = c[1], mid = v === 'S' ? SK : v === 'V' ? RV : v === 'D' ? '+2' : v === 'F' ? '+4' : v === 'W' ? '' : v;
    var cor = COR[v] || v;
    return '<span class="ukc ' + (CN[c[0]] || 'cK') + (v === 'F' || v === 'W' ? ' wild' : '') + (cls ? ' ' + cls : '') + '"><span class="ov"></span><span class="mid' +
      ((v === '6' || v === '9') ? ' u' : '') + '">' + mid + '</span><i class="cn tl">' + cor + '</i><i class="cn br">' + cor + '</i></span>';
  }
  function backHtml(){ return '<span class="ukc back"><span class="ov"></span><span class="mid">UNO</span></span>'; }
  function av(p){ return p && p.avatar ? '<img src="' + esc(p.avatar) + '" alt="">' : esc(((p && p.name) || '?').slice(0, 1).toUpperCase()); }
  function saved(){ try{ return localStorage.getItem(LS) || ''; }catch(e){ return ''; } }
  function remember(c){ try{ if(c) localStorage.setItem(LS, c); else localStorage.removeItem(LS); }catch(e){} refreshResume(); }
  function refreshResume(){ var b = $('unoResume'); if(b) b.hidden = !saved(); }
  function lobbyMsg(t){ var e = $('unoMsg'); if(e) e.textContent = T(t || ''); }
  function flash(t){
    un.flashTx = t; clearTimeout(un.flashT);
    un.flashT = setTimeout(function(){ un.flashTx = ''; if(un.s) renderMsg(); }, 2600);
    renderMsg();
  }
  function errOf(r, j){
    if(r.status === 404) return 'Belə oyun tapılmadı. Kodu yoxla.';
    if(r.status === 403) return 'Bu otaq doludur';
    if(r.status === 429) return 'Çox sorğu göndərdin, bir az gözlə';
    if(j && j.error) return j.error;
    return 'Əlaqə problemi, yenidən cəhd et';
  }
  function show(on){ $('uno').hidden = !on; }
  function close(leave){
    var c = un.code, st = un.s && un.s.status;
    try{ voice.leave(); }catch(e){}
    gen++; clearTimeout(un.botT); un.botT = 0;
    un.code = ''; un.s = null; un.pend = null; clearTimeout(un.flashT); un.flashTx = '';
    $('uncolor').hidden = true;
    show(false);
    if(leave && c) api('/api/uno/leave', {method: 'POST', body: JSON.stringify({room: c})}).catch(function(){});
    if(st === 'wait' || st === 'over') remember('');
  }
  function gone(){ gen++; clearTimeout(un.botT); un.botT = 0; un.code = ''; un.s = null; remember(''); show(false); lobbyMsg('Oyun bitib və ya tapılmadı'); }

  /* ---- lobbi ---- */
  function paintSize(){ [].forEach.call(document.querySelectorAll('#unoSize button'), function(b){ b.classList.toggle('on', +b.getAttribute('data-n') === un.sizeN); }); }
  [].forEach.call(document.querySelectorAll('#unoSize button'), function(b){
    b.onclick = function(){ un.sizeN = +b.getAttribute('data-n'); try{ localStorage.setItem('wt_unosz', String(un.sizeN)); }catch(e){} paintSize(); };
  });
  paintSize();

  /* ---- effektlər ---- */
  var SKB = SK.replace('<svg ', '<svg class="fxs" '), RVB = RV.replace('<svg ', '<svg class="fxs" ');
  function board(){ return $('unboard'); }
  function burst(inner, cls, color, sub){
    var b = board(); if(!b) return;
    var e = document.createElement('div'); e.className = 'ufx ' + (cls || '');
    if(color) e.style.setProperty('--fc', color);
    e.innerHTML = '<span class="big">' + inner + '</span>' + (sub ? '<small>' + esc(sub) + '</small>' : '');
    b.appendChild(e); setTimeout(function(){ if(e.parentNode) e.parentNode.removeChild(e); }, 1150);
  }
  function ripple(color){
    var b = board(); if(!b) return;
    var e = document.createElement('div'); e.className = 'uripple'; e.style.setProperty('--fc', color);
    b.appendChild(e); setTimeout(function(){ if(e.parentNode) e.parentNode.removeChild(e); }, 950);
  }
  function confetti(){
    var host = $('uno'), cols = ['#e53935', '#f9c10d', '#2fa44f', '#1f78d1', '#ffffff', '#ff7a9c'];
    var wrap = document.createElement('div'); wrap.className = 'uconf';
    for(var i = 0; i < 44; i++){
      var p = document.createElement('i');
      p.style.left = (Math.random() * 100) + '%';
      p.style.background = cols[i % cols.length];
      p.style.animationDuration = (1.8 + Math.random() * 1.8) + 's';
      p.style.animationDelay = (Math.random() * .6) + 's';
      p.style.setProperty('--dx', (Math.random() * 120 - 60) + 'px');
      p.style.setProperty('--rot', (Math.random() * 900 - 450) + 'deg');
      wrap.appendChild(p);
    }
    host.appendChild(wrap); setTimeout(function(){ if(wrap.parentNode) wrap.parentNode.removeChild(wrap); }, 4200);
  }
  function fx(L, s){
    var v = L.card ? L.card[1] : '', victim = (s.players[L.victim] || {}).name || '';
    if(L.t !== 'play') return;
    if(L.color) ripple(RC[L.color] || '#fff');
    if(v === 'S') burst(SKB, 'fxskip', '#ffb3c1', victim);
    else if(v === 'V') burst(RVB, 'fxrev', '#8fd3ff');
    else if(v === 'D') burst('+2', 'fxd', RC[L.color], victim);
    else if(v === 'F') burst('+4', 'fxd fx4', RC[L.color], victim);
    else if(v === 'W') burst('\u2605', 'fxw', RC[L.color]);
    if(L.uno) setTimeout(function(){ burst('UNO!', 'uno'); }, 260);
  }

  /* ---- sinxronizasiya və səslər ---- */
  function myTurn(s){ return !!s && s.status === 'play' && s.turn === s.me; }
  function notify(p, s){
    var S = window.wtSfx; if(!S) return;
    if(!p){ if(s.status === 'play') S.play('shuffle'); return; }
    if(s.over && !p.over){
      if(s.quit != null){ S.play(s.quit === s.me ? 'lose' : 'win'); }
      else S.play(s.winner === s.me ? 'win' : 'lose');
      return;
    }
    if(p.status === 'wait' && s.status === 'play') S.play('shuffle');
    var L = s.last, PL = p.last;
    if(L && (!PL || L.k > PL.k)){
      fx(L, s);
      if(L.t === 'play'){
        S.play('place');
        if(L.card[0] === 'K') setTimeout(function(){ S.play(L.card[1] === 'F' ? 'plus4' : 'wild'); }, 110);
        else if(L.fx === 'skip') setTimeout(function(){ S.play('skip'); }, 110);
        else if(L.fx === 'rev') setTimeout(function(){ S.play('reverse'); }, 110);
        else if(L.fx === 'd2'){ setTimeout(function(){ S.play('take'); }, 130); setTimeout(function(){ S.play('take'); }, 300); }
        if(L.uno){
          setTimeout(function(){ S.play('uno'); }, 420);
          var who = s.players[L.p] || {};
          flash((L.p === s.me ? T('Sən') : (who.name || '')) + ': UNO!');
        }
      } else if(L.t === 'draw') S.play('take');
      else if(L.t === 'pass') S.play('pass');
    }
    if(myTurn(s) && !myTurn(p)) S.play('turn');
    if(s.joined > p.joined) S.play('join');
  }
  function trackPile(prev, s){
    var L = s.last;
    if(!prev || prev.round !== s.round || s.status !== 'play'){
      un.pile = s.top ? [{c: s.top, r: 0, k: L ? L.k : 0, by: L ? L.p : -1}] : [];
      if(!prev || prev.round !== s.round) un.prevCnt = null;
      return;
    }
    var top = un.pile[un.pile.length - 1];
    if(s.top && L && L.t === 'play' && (!top || top.k !== L.k)){
      un.pile.push({c: s.top, r: ((L.k * 37) % 25) - 12, k: L.k, by: L.p});
      if(un.pile.length > 4) un.pile.shift();
    }
  }
  function sync(s){
    if(!s || s.code !== un.code) return;
    if(s.v < un.v) return;
    var prev = un.s;
    un.v = s.v; un.vk = s.vk || 0; un.s = s;
    if(!prev && s.last) un.landK = s.last.k;
    trackPile(prev, s);
    notify(prev, s);
    render();
  }

  /* ---- uzun sorğu ---- */
  function startPoll(){
    var g = ++gen, fails = 0;
    (async function(){
      while(g === gen && un.code){
        try{
          var r = await api('/api/uno/poll?room=' + enc(un.code) + '&v=' + un.v + '&vk=' + un.vk);
          if(g !== gen) return;
          if(r.status === 404){ gone(); return; }
          if(!r.ok){ fails++; try{ window.wtReconnectBanner('Əlaqə kəsildi, yenidən cəhd...'); }catch(x){} await sleep(2000); continue; }
          if(fails > 0){ try{ window.wtReconnectBanner('Yenidən qoşuldu', true); setTimeout(function(){ window.wtReconnectBanner(''); }, 1800); }catch(x){} }
          fails = 0; sync(await r.json());
        }catch(e){ if(g !== gen) return; fails++; try{ window.wtReconnectBanner('Əlaqə kəsildi, yenidən cəhd...'); }catch(x){} await sleep(2000); }
      }
    })();
  }

  async function enter(code, isCreate, vsBot){
    if(busy) return; busy = true;
    try{
      var r = isCreate
        ? await api('/api/uno/create', {method: 'POST', body: JSON.stringify({size: un.sizeN, bot: !!vsBot})})
        : await api('/api/uno/join', {method: 'POST', body: JSON.stringify({room: code})});
      var j = {}; try{ j = await r.json(); }catch(e){}
      if(r.ok){
        gen++; clearTimeout(un.botT); un.botT = 0;
        un.code = j.code; un.v = 0; un.vk = 0; un.s = null; un.pend = null; un.flashTx = ''; un.pile = []; un.prevCnt = null;
        remember(j.code); show(true);
        startPoll(); sync(j); lobbyMsg('');
      } else if(!isCreate && r.status === 404 && code === saved()){
        remember(''); lobbyMsg('Oyun bitib və ya tapılmadı');
      } else lobbyMsg(errOf(r, j));
    }catch(e){ lobbyMsg('Əlaqə problemi, yenidən cəhd et'); }
    busy = false;
  }

  /* ---- hərəkət göndərmək ---- */
  function send(body){
    if(!un.code) return;
    un.pend = null; $('uncolor').hidden = true;
    api('/api/uno/move', {method: 'POST', body: JSON.stringify(Object.assign({room: un.code}, body))}).then(async function(r){
      var j = {}; try{ j = await r.json(); }catch(e){}
      if(!r.ok){ flash(j.error || 'Bu hərəkət mümkün deyil'); render(); return; }
      sync(j);
    }).catch(function(){ flash('Əlaqə problemi, yenidən cəhd et'); });
  }
  function onHand(c){
    var s = un.s; if(!s || s.status !== 'play') return;
    if(!myTurn(s)){ flash('Növbə sənin deyil'); return; }
    var ok = (s.playable || []).some(function(x){ return key(x) === key(c); });
    if(!ok){ flash(s.drawn ? 'Yalnız çəkdiyin kartı ata bilərsən' : 'Bu kart uyğun deyil: rəng və ya rəqəm eyni olmalıdır'); return; }
    if(c[0] === 'K'){ un.pend = c; $('uncolor').hidden = false; return; }
    send({action: 'play', card: c});
  }
  function doDraw(){
    var s = un.s; if(!s || s.status !== 'play') return;
    if(!myTurn(s)){ flash('Növbə sənin deyil'); return; }
    if(!s.can_draw){ flash('Artıq kart çəkmisən'); return; }
    send({action: 'draw'});
  }

  /* ---- kompüter ---- */
  function botDrive(){
    var s = un.s;
    if(!s || !s.bot || s.status !== 'play' || !s.botturn || !un.code || un.botT) return;
    var c = un.code, g = gen, v0 = un.v;
    function again(ms){ un.botT = setTimeout(function(){ un.botT = 0; botDrive(); }, ms); }
    un.botT = setTimeout(function(){
      un.botT = 0;
      if(un.code !== c) return;
      if(g !== gen){ botDrive(); return; }
      api('/api/uno/bot', {method: 'POST', body: JSON.stringify({room: c})}).then(function(r){ return r.ok ? r.json() : null; }).then(function(j){
        if(g !== gen) return;
        if(!j){ again(2500); return; }
        var same = j.v === v0;
        sync(j);
        if(same && !un.botT) again(1200);
      }).catch(function(){ if(g === gen && !un.botT) again(2500); });
    }, 700 + Math.floor(Math.random() * 500));
  }

  /* ---- göstərmə ---- */
  function render(){
    var s = un.s; if(!s) return;
    $('uncode').textContent = 'KOD ' + s.code.toUpperCase();
    renderStrip(s); renderTable(s); renderHand(s); renderAct(s); renderExtra(s); renderMsg(); renderWait(s); renderRes(s);
    $('unshare').hidden = !!s.bot;
    try{ voice.presence(s.vc, s.vk); }catch(e){}
    botDrive();
  }
  function chip(s, i){
    var p = s.players[i], play = s.status !== 'wait', turn = s.status === 'play' && s.turn === i;
    var mini = '';
    if(play && !p.me){ for(var q = 0, m = Math.min(p.n, 9); q < m; q++) mini += '<span class="umb"></span>'; }
    var small = (p.me ? '' : '<span class="donl' + (p.online ? ' on' : '') + '"></span>') +
      (!play ? '' : p.n + ' ' + esc(T('kart')) + (p.n === 1 ? ' <b class="uuno">UNO!</b>' : ''));
    return '<div class="dchip' + (turn ? ' turn' : '') + '"><div class="dav">' + av(p) + '</div><div class="dwho">' +
      (p.me ? '<b>' + esc(T('Sən')) + '</b>' : '<b data-nt>' + esc(p.name) + '</b>') + '<small>' + small + '</small>' + (mini ? '<span class="umini">' + mini + '</span>' : '') + '</div></div>';
  }
  function renderStrip(s){
    var box = $('unpl'), n = s.players.length, me = s.me;
    box.className = 'dpl multi' + (s.size > 3 ? ' m4' : '');
    var h = '';
    for(var k = 1; k < n; k++) h += chip(s, (me + k) % n);
    for(var q = n; q < s.size; q++) h += '<div class="dchip"><div class="dav">?</div><div class="dwho"><b>' + esc(T('Gözlənilir')) + '</b></div></div>';
    h += chip(s, me);
    box.innerHTML = h;
  }
  function renderTable(s){
    var t = $('untable'), bd = $('unboard');
    t.hidden = s.status === 'wait';
    bd.setAttribute('data-c', s.status === 'play' ? s.color : '');
    if(s.status === 'wait'){ t.innerHTML = ''; return; }
    var L = s.last, land = !!(L && L.t === 'play' && L.k !== un.landK);
    var landCls = land ? (L.p === s.me ? 'land-me' : 'land-op') : '';
    if(L) un.landK = L.k;
    var rc = RC[s.color] || '#888', last = un.pile.length - 1, stk = '';
    un.pile.forEach(function(it, i){
      var top = i === last;
      stk += '<span class="ustkc' + (top ? ' top' : '') + '" style="transform:rotate(' + (top ? it.r * .5 : it.r) + 'deg) translate(' + (top ? 0 : (i % 2 ? 5 : -5)) + 'px,' + (top ? 0 : -3) + 'px);z-index:' + (i + 1) + '">' + cardHtml(it.c, top ? landCls : 'old') + '</span>';
    });
    var deckN = s.deck || 0, layers = deckN > 20 ? 3 : (deckN > 6 ? 2 : (deckN > 0 ? 1 : 0)), dk = '';
    for(var i = 0; i < layers; i++) dk += '<span class="ustkc" style="transform:translate(' + (i - layers + 1) * 2 + 'px,' + (layers - 1 - i) * 2 + 'px);z-index:' + i + '">' + backHtml() + '</span>';
    if(!layers) dk = '<span class="ustkc"><span class="ukc empty"></span></span>';
    t.innerHTML =
      '<div class="uring" style="--rc:' + rc + '"></div>' +
      '<div class="upile udraw' + (myTurn(s) && s.can_draw ? ' can' : '') + '" id="undeck"><span class="ustk">' + dk + '</span><b class="ucnt">' + deckN + '</b><small>' + esc(T('Kart bazarı')) + '</small></div>' +
      '<div class="udir ' + (s.dir > 0 ? 'cw' : 'ccw') + '" style="--rc:' + rc + '"><i class="fa-solid fa-rotate-right" aria-hidden="true"></i></div>' +
      '<div class="upile udisc" style="--rc:' + rc + '"><span class="ustk">' + stk + '</span>' +
      '<small><span class="udot" style="background:' + rc + '"></span>' + esc(T(CNAME[s.color] || '')) + '</small></div>';
  }
  function renderHand(s){
    var box = $('unhand'), mine = myTurn(s), ok = {};
    (s.playable || []).forEach(function(c){ ok[key(c)] = 1; });
    un.hs = (s.hand || []).slice().sort(function(a, b){
      var d = CORD.indexOf(a[0]) - CORD.indexOf(b[0]);
      return d || VORD.indexOf(a[1]) - VORD.indexOf(b[1]);
    });
    box.style.display = s.status === 'over' ? 'none' : '';
    $('uno').classList.toggle('myturn', mine);
    var n = un.hs.length, ov = n <= 6 ? .06 : (n <= 9 ? .3 : (n <= 13 ? .44 : .54));
    var step = Math.min(4.2, 40 / Math.max(n, 1)), prev = un.prevCnt, cnt = {};
    box.innerHTML = '<div class="uhin" style="--ov:' + ov + '">' + un.hs.map(function(c, i){
      var k = key(c), isNew = false;
      cnt[k] = (cnt[k] || 0) + 1;
      if(prev && cnt[k] > (prev[k] || 0)) isNew = true;
      var d = i - (n - 1) / 2, r = (d * step).toFixed(1), y = Math.min(14, d * d * .32).toFixed(1);
      var cls = mine ? (ok[k] ? ' ok' : ' dim') : '';
      return '<button type="button" class="ukh' + cls + (isNew ? ' new' : '') + '" style="--r:' + r + 'deg;--y:' + y + 'px" data-i="' + i + '" aria-label="' + esc((CNAME[c[0]] || '') + ' ' + c[1]) + '">' + cardHtml(c) + '</button>';
    }).join('') + '</div>';
    un.prevCnt = cnt;
    unFitHand();
  }
  function unFitHand(){
    var box = $('unhand'), inner = box && box.firstElementChild;
    if(!inner || !inner.firstElementChild || !box.clientWidth) return;
    var n = inner.children.length;
    inner.style.removeProperty('--cw');
    var cw = inner.firstElementChild.offsetWidth || 62;
    var avail = box.clientWidth - 16 - 20 - 14, ov;
    if(n > 1 && n * cw > avail){
      var minVis = 17;                       // each overlapped card keeps at least this much visible
      ov = (n * cw - avail) / ((n - 1) * cw);
      if(ov > .72){                          // too many cards: shrink them so everything fits without scrolling
        cw = Math.max(26, Math.min(cw, (avail - (n - 1) * minVis) / 1));
        ov = Math.max(0, (n * cw - avail) / ((n - 1) * cw));
      }
    } else ov = n > 1 ? .06 : 0;
    inner.style.setProperty('--cw', cw + 'px');
    inner.style.setProperty('--ov', ov.toFixed(3));
  }
  window.addEventListener('resize', unFitHand);
  function renderAct(s){
    var mine = myTurn(s);
    $('undraw').hidden = !(mine && s.can_draw);
    $('unpass').hidden = !(mine && s.can_pass);
    $('unact').hidden = $('undraw').hidden && $('unpass').hidden;
  }
  function renderExtra(s){ $('uninfo').textContent = s.status === 'play' ? T('Rəngə və ya rəqəmə uyğun kart at') : ''; }
  function msgText(s){
    if(s.status !== 'play') return '';
    if(myTurn(s)){
      if(s.drawn) return T('Çəkdiyin kart uyğundur: at və ya pas keç');
      return T('Sənin növbən: kart at və ya bazardan çək');
    }
    if(s.bot && s.botturn) return T('Kompüter düşünür...');
    var p = s.players[s.turn] || {};
    return (p.name || T('Rəqib')) + ' ' + (p.online ? T('oynayır...') : T('əlaqədə deyil'));
  }
  function renderMsg(){
    var s = un.s, e = $('unmsg');
    if(!s){ e.textContent = ''; return; }
    e.textContent = un.flashTx ? T(un.flashTx) : msgText(s);
  }
  function renderWait(s){
    var w = $('unwait'), wait = s.status === 'wait';
    w.hidden = !wait;
    if(!wait) return;
    $('unwcode').textContent = s.code.toUpperCase();
    $('unwcnt').textContent = T('Oyunçu') + ': ' + s.joined + ' / ' + s.size;
    try{ inv.refresh(); }catch(e){}
  }
  function renderRes(s){
    var box = $('unres'), over = s.status === 'over';
    box.hidden = !over;
    if(!over){ box.innerHTML = ''; box.className = ''; return; }
    var title, why, win = false, lose = false;
    if(s.winner === s.me){ title = 'Qazandın!'; why = T('Bütün kartlarını bitirdin'); win = true; }
    else { title = 'Uduzdun'; why = ((s.players[s.winner] || {}).name || '') + ' ' + T('kartlarını bitirdi'); lose = true; }
    if(s.quit != null){
      why = s.quit === s.me ? T('Oyundan çıxdın') : ((s.players[s.quit] || {}).name || '') + ' ' + T('oyundan çıxdı');
      if(s.quit === s.me){ lose = true; win = false; title = 'Uduzdun'; } else { win = true; lose = false; title = 'Qazandın!'; }
    }
    box.className = 'res-show ' + (win ? 'win' : 'lose');
    var ck = s.code + ':' + s.round;
    if(win && un.conf !== ck){ un.conf = ck; setTimeout(confetti, 250); }
    box.innerHTML = '<div class="res-ic"><i class="fa-solid ' + (win ? 'fa-trophy' : 'fa-heart-crack') + '" aria-hidden="true"></i></div><h3>' + esc(T(title)) + '</h3><p>' + esc(why) + '</p><button class="pbtn" id="unAgain" type="button">' + esc(T('Növbəti raund')) + '</button>';
    $('unAgain').onclick = async function(){
      if(!un.code) return;
      try{
        var r = await api('/api/uno/again', {method: 'POST', body: JSON.stringify({room: un.code, round: s.round})});
        if(r.ok) sync(await r.json()); else flash((window.wtErr && window.wtErr(r)) || 'Alınmadı');
      }catch(e){ flash('Əlaqə problemi, internetini yoxla'); }
    };
  }

  /* ---- paylaş, çıx ---- */
  async function share(){
    var s = un.s; if(!s) return;
    var url = location.origin + '/?uno=' + encodeURIComponent(s.code.toUpperCase());
    var text = T('Uno oynayaq! Kod: ' + s.code.toUpperCase());
    try{ if(navigator.share){ await navigator.share({title: 'Uno', text: text, url: url}); return; } }catch(e){ if(e && e.name === 'AbortError') return; }
    try{ await navigator.clipboard.writeText(url); flash('Link kopyalandı'); }
    catch(e){ flash('Kod: ' + s.code.toUpperCase()); }
  }

  /* ---- düymələr ---- */
  $('unoNew').onclick = function(){ enter('', true); };
  $('unoEnter').onclick = function(){
    var c = clean($('unoCodeIn').value);
    if(!c){ lobbyMsg('Uno kodunu yaz'); return; }
    enter(c, false);
  };
  $('unoCodeIn').addEventListener('keydown', function(e){ if(e.key === 'Enter'){ e.preventDefault(); $('unoEnter').click(); } });
  $('unoResume').onclick = function(){ var c = saved(); if(c) enter(c, false); };
  (function(){
    var c = saved(); if(!c) return;
    api('/api/uno/check?room=' + enc(c)).then(function(r){ if(r.status === 404 && saved() === c) remember(''); }).catch(function(){});
  })();
  $('unoBot').onclick = function(){ enter('', true, true); };
  $('unback').onclick = function(){ close(true); };
  $('unshare').onclick = share;
  $('unwshare').onclick = share;
  $('unquit').onclick = function(){
    var s = un.s, c = un.code;
    if(!s || !c) return;
    var m = s.status === 'play' ? 'Oyundan çıxmaq istəyirsən? Oyun sənin üçün bitəcək.' : 'Oyundan çıxmaq istəyirsən?';
    wtConfirm(m, {ok: 'Oyundan çıx', danger: true}).then(function(y){
      if(!y || un.code !== c) return;
      close(false); remember('');
      api('/api/uno/quit', {method: 'POST', body: JSON.stringify({room: c})}).catch(function(){});
    });
  };
  $('undraw').onclick = doDraw;
  $('unpass').onclick = function(){ send({action: 'pass'}); };
  $('unboard').addEventListener('click', function(e){
    if(e.target.closest && e.target.closest('#undeck')) doDraw();
  });
  $('uncolor').addEventListener('click', function(e){
    var b = e.target.closest && e.target.closest('[data-c]');
    if(b && un.pend){ send({action: 'play', card: un.pend, color: b.getAttribute('data-c')}); return; }
    if(e.target === $('uncolor') || (e.target.closest && e.target.closest('[data-x]'))){ un.pend = null; $('uncolor').hidden = true; }
  });
  $('unhand').onclick = function(e){
    var b = e.target.closest && e.target.closest('[data-i]'); if(!b) return;
    var c = un.hs[+b.getAttribute('data-i')]; if(c) onHand(c);
  };
  document.addEventListener('visibilitychange', function(){
    if(document.visibilityState === 'visible' && un.code && !$('uno').hidden){ un.v = 0; startPoll(); }
  });

  /* ---- ?uno=KOD linki ---- */
  try{
    var q = new URLSearchParams(location.search).get('uno');
    if(q){ pendingUno = clean(q); history.replaceState(null, '', location.pathname); }
  }catch(e){}
  if(pendingUno){
    var tries = 0, iv = setInterval(function(){
      tries++;
      var menuOn = false;
      try{ menuOn = getComputedStyle($('menu')).display !== 'none'; }catch(e){}
      if(token && account && menuOn){ clearInterval(iv); var c = pendingUno; pendingUno = ''; enter(c, false); }
      else if(tries > 120) clearInterval(iv);
    }, 500);
  }
  window.wtJoinUno = function(c){ enter(clean(c), false); };
  refreshResume();
})();
