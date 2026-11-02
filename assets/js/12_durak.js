
/* ---------- Durak otağı (2-4 nəfər, onlayn, kompüter yoxdur) ---------- */
(function(){
  var $ = function(id){ return document.getElementById(id); };
  var LS = 'wt_dur';
  var du = {code: '', v: 0, vk: 0, s: null, sel: null, hs: [], flashT: null, flashTx: '', sizeN: 2};
  var busy = false, gen = 0, pendingDur = '';
  try{ du.sizeN = Math.max(2, Math.min(4, parseInt(localStorage.getItem('wt_dursz') || '2', 10) || 2)); }catch(e){}
  var SUIT = {S: '\u2660\uFE0E', H: '\u2665\uFE0E', D: '\u2666\uFE0E', C: '\u2663\uFE0E'};
  var RNK = {11: 'B', 12: 'D', 13: 'K', 14: 'A'};
  var voice = window.wtGameVoice({game: 'durak', p: 'du', code: function(){ return du.code; }, say: function(t){ flash(t); }});
  var inv = window.wtInviteBox({game: 'durak', user: 'duinvU', go: 'duinvGo', fr: 'duinvFr', ft: 'duinvFrT', msg: 'duinvMsg', code: function(){ return du.code; }});

  function T(s){ try{ return window.wtT ? window.wtT(s) : s; }catch(e){ return s; } }
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){ return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]; }); }
  function clean(c){ return String(c || '').toLowerCase().replace(/[^a-z0-9-]/g, '').slice(0, 30); }
  function enc(x){ return encodeURIComponent(x); }
  function sleep(ms){ return new Promise(function(r){ setTimeout(r, ms); }); }
  function key(c){ return c[0] + c[1]; }
  function same(a, b){ return !!a && !!b && a[0] === b[0] && a[1] === b[1]; }
  function isRed(su){ return su === 'H' || su === 'D'; }
  var PIP = {
    2: [[50,0],[50,100]], 3: [[50,0],[50,50],[50,100]],
    4: [[0,0],[100,0],[0,100],[100,100]], 5: [[0,0],[100,0],[50,50],[0,100],[100,100]],
    6: [[0,0],[100,0],[0,50],[100,50],[0,100],[100,100]],
    7: [[0,0],[100,0],[50,25],[0,50],[100,50],[0,100],[100,100]],
    8: [[0,0],[100,0],[50,25],[0,50],[100,50],[50,75],[0,100],[100,100]],
    9: [[0,0],[100,0],[0,33],[100,33],[50,50],[0,67],[100,67],[0,100],[100,100]],
    10: [[0,0],[100,0],[50,17],[0,33],[100,33],[0,67],[100,67],[50,83],[0,100],[100,100]]
  };
  /* klassik rus durak kartları: qoşabaşlı Kral (K), Dama (Q), Valet (J) */
  function court(r, st, su){
    var pal = {H: ['#d3213b', '#2b6cb8'], D: ['#e8a317', '#d3213b'], S: ['#2b6cb8', '#2b2b36'], C: ['#2f9e5b', '#2b6cb8']}[st] || ['#d3213b', '#2b6cb8'];
    var a = pal[0], b = pal[1], ink = '#2b1a0a', skin = '#f3cba6', gold = '#f2b705';
    var S = ' stroke="' + ink + '" stroke-width=".6" stroke-linejoin="round"', g = '';
    function eyes(){ return '<circle cx="27.4" cy="20.5" r=".9" fill="#222"/><circle cx="32.6" cy="20.5" r=".9" fill="#222"/>'; }
    if(r === 11){                                     /* B: gənc, lələkli papaq, halbert */
      g += '<path d="M9 56Q9 34 30 31Q51 34 51 56Z" fill="' + a + '"' + S + '/>';
      g += '<path d="M30 31Q42 34 44 56H30Z" fill="' + b + '" opacity=".85"/>';
      g += '<path d="M23 33L30 40L37 33Z" fill="#fff" stroke="' + ink + '" stroke-width=".5"/>';
      g += '<path d="M23 19Q23 12 30 12Q37 12 37 19Q34 14.5 30 14.5Q26 14.5 23 19Z" fill="#5a3420"/>';
      g += '<ellipse cx="30" cy="21" rx="6.6" ry="7.6" fill="' + skin + '"' + S + '/>' + eyes();
      g += '<path d="M19 15Q20 5 30 5Q41 5 42 15Q36 9 30 9Q24 9 19 15Z" fill="' + b + '"' + S + '/>';
      g += '<path d="M35 7Q47 0 48 11Q42 6 38 9Z" fill="' + a + '"' + S + '/>';
      g += '<path d="M27 26Q30 28 33 26" stroke="#a03a2a" stroke-width=".9" fill="none"/>';
      g += '<rect x="45.6" y="4" width="1.8" height="52" fill="#8a5a2b"/><path d="M46.5 4L53 9L46.5 14Z" fill="#c9ced6" stroke="#555" stroke-width=".5"/>';
      g += '<circle cx="46" cy="40" r="2.4" fill="' + skin + '"' + S + '/>';
    }else if(r === 12){                               /* D: tacda qadın, tac, çiçək */
      g += '<path d="M19 19Q16 38 21 46Q30 41 39 46Q44 38 41 19Q30 5 19 19Z" fill="#7a3b1e"' + S + '/>';
      g += '<path d="M9 56Q9 32 30 29Q51 32 51 56Z" fill="' + a + '"' + S + '/>';
      g += '<path d="M24 31Q30 39 36 31L36 40Q30 47 24 40Z" fill="' + b + '"' + S + '/>';
      g += '<ellipse cx="30" cy="20" rx="6.4" ry="7.4" fill="' + skin + '"' + S + '/>' + eyes();
      g += '<path d="M23 9L25 3.5L30 7L35 3.5L37 9Z" fill="' + gold + '"' + S + '/>';
      g += '<path d="M27.5 24Q30 25.6 32.5 24" stroke="#c0243a" stroke-width="1" fill="none"/>';
      g += '<path d="M12 56Q13 47 14 42" stroke="#2f7a3a" stroke-width="1.2" fill="none"/>';
      g += '<circle cx="14" cy="38.5" r="3.6" fill="#d3213b"' + S + '/><circle cx="14" cy="38.5" r="1.3" fill="#f5c6d0"/>';
      g += '<circle cx="15" cy="46" r="2.2" fill="' + skin + '"' + S + '/>';
    }else{                                            /* K: saqqallı, tac, skiptr */
      g += '<path d="M7 56Q7 31 30 28Q53 31 53 56Z" fill="' + a + '"' + S + '/>';
      g += '<path d="M22 29L30 37L38 29Z" fill="#fff" stroke="' + ink + '" stroke-width=".5"/>';
      g += '<path d="M10 56Q12 42 17 36M50 56Q48 42 43 36" stroke="' + b + '" stroke-width="2" fill="none"/>';
      g += '<path d="M22 22Q23 34 30 35Q37 34 38 22Q34 28 30 28Q26 28 22 22Z" fill="#9aa0aa"' + S + '/>';
      g += '<ellipse cx="30" cy="19" rx="6.6" ry="7.4" fill="' + skin + '"' + S + '/>' + eyes();
      g += '<path d="M22.5 15Q23 7 30 7Q37 7 37.5 15Q34 11 30 11Q26 11 22.5 15Z" fill="#9aa0aa"' + S + '/>';
      g += '<path d="M21 11V4.5L25.5 8.5L30 2.5L34.5 8.5L39 4.5V11Z" fill="' + gold + '"' + S + '/>';
      g += '<circle cx="30" cy="7.6" r="1.2" fill="#d3213b"/>';
      g += '<rect x="48.2" y="6" width="2" height="50" fill="' + gold + '" stroke="#7a4a00" stroke-width=".4"/>';
      g += '<circle cx="49.2" cy="6" r="2.6" fill="#5bb6ea" stroke="#1d5f8a" stroke-width=".5"/>';
      g += '<circle cx="11" cy="42" r="2.4" fill="' + skin + '"' + S + '/>';
    }
    return '<svg viewBox="0 0 60 112" preserveAspectRatio="xMidYMid meet" aria-hidden="true"><rect width="60" height="112" fill="#fff"/><g>' + g + '</g><path d="M0 56H60" stroke="#c9b9a0" stroke-width=".5"/><g transform="rotate(180 30 56)">' + g + '</g></svg>';
  }
  function cardHtml(c){
    var r = c[0], su = SUIT[c[1]], rk = RNK[r] || String(r);
    var ix = '<span class="ix"><b' + (rk.length > 1 ? ' class="t"' : '') + '>' + rk + '</b><i>' + su + '</i></span>';
    var h = '<span class="dkc' + (isRed(c[1]) ? ' red' : '') + '">' + ix;
    if(r === 14) h += '<span class="pp"><i class="big" style="left:50%;top:50%">' + su + '</i></span>';
    else if(r >= 11) h += '<span class="fc">' + court(r, c[1], su) + '</span>';
    else h += '<span class="pp">' + (PIP[r] || []).map(function(q){
      return '<i' + (q[1] > 50 ? ' class="f"' : '') + ' style="left:' + q[0] + '%;top:' + q[1] + '%">' + su + '</i>';
    }).join('') + '</span>';
    return h + ix.replace('class="ix"', 'class="ix b"') + '</span>';
  }
  function av(p){ return p && p.avatar ? '<img src="' + esc(p.avatar) + '" alt="">' : esc(((p && p.name) || '?').slice(0, 1).toUpperCase()); }
  function saved(){ try{ return localStorage.getItem(LS) || ''; }catch(e){ return ''; } }
  function remember(c){ try{ if(c) localStorage.setItem(LS, c); else localStorage.removeItem(LS); }catch(e){} refreshResume(); }
  function refreshResume(){ var b = $('durResume'); if(b) b.hidden = !saved(); }
  function lobbyMsg(t){ var e = $('durMsg'); if(e) e.textContent = T(t || ''); }
  function flash(t){
    du.flashTx = t; clearTimeout(du.flashT);
    du.flashT = setTimeout(function(){ du.flashTx = ''; if(du.s) renderMsg(); }, 2600);
    renderMsg();
  }
  function errOf(r, j){
    if(r.status === 404) return 'Belə oyun tapılmadı. Kodu yoxla.';
    if(r.status === 403) return 'Bu otaq doludur';
    if(r.status === 429) return 'Çox sorğu göndərdin, bir az gözlə';
    if(j && j.error) return j.error;
    return 'Əlaqə problemi, yenidən cəhd et';
  }
  function show(on){ $('durak').hidden = !on; }
  function close(leave){
    var c = du.code, st = du.s && du.s.status;
    try{ voice.leave(); }catch(e){}
    gen++; clearTimeout(du.botT); du.botT = 0;
    du.code = ''; du.s = null; clearTimeout(du.flashT); du.flashTx = '';
    show(false);
    if(leave && c) api('/api/durak/leave', {method: 'POST', body: JSON.stringify({room: c})}).catch(function(){});
    if(st === 'wait' || st === 'over') remember('');
  }
  function gone(){ gen++; clearTimeout(du.botT); du.botT = 0; du.code = ''; du.s = null; remember(''); show(false); lobbyMsg('Oyun bitib və ya tapılmadı'); }

  /* ---- lobbi ---- */
  function paintSize(){ [].forEach.call(document.querySelectorAll('#durSize button'), function(b){ b.classList.toggle('on', +b.getAttribute('data-n') === du.sizeN); }); }
  [].forEach.call(document.querySelectorAll('#durSize button'), function(b){
    b.onclick = function(){ du.sizeN = +b.getAttribute('data-n'); try{ localStorage.setItem('wt_dursz', String(du.sizeN)); }catch(e){} paintSize(); };
  });
  paintSize();
  function perevodOn(){ try{ return localStorage.getItem('wt_durperevod') !== '0'; }catch(e){ return true; } }

  /* ---- sinxronizasiya və səslər ---- */
  function myTurn(s){ return !!s && s.status === 'play' && (s.actions || []).length > 0; }
  function notify(p, s){
    var S = window.wtSfx; if(!S) return;
    if(!p){ if(s.status === 'play') S.play('shuffle'); return; }
    if(s.over && !p.over){
      if(s.loser === -1) S.play('tie'); else if(s.loser === s.me) S.play('lose'); else S.play('win');
      return;
    }
    if(p.status === 'wait' && s.status === 'play'){ S.play('shuffle'); }
    if(s.table.length > p.table.length) S.play('place');
    else if(s.table.length < p.table.length) S.play((s.took || !(s.discard > p.discard)) ? 'take' : 'pass');
    else if(s.table.length === p.table.length && s.table.some(function(x, i){ return x.d && p.table[i] && !p.table[i].d; })) S.play('place');
    if(myTurn(s) && !myTurn(p)) S.play('turn');
    if(s.joined > p.joined) S.play('join');
  }
  function sync(s){
    if(!s || s.code !== du.code) return;
    if(s.v < du.v) return;
    var prev = du.s;
    du.v = s.v; du.vk = s.vk || 0; du.s = s;
    var ok = {};
    notify(prev, s);
    render();
  }

  /* ---- uzun sorğu ---- */
  function startPoll(){
    var g = ++gen, fails = 0;
    (async function(){
      while(g === gen && du.code){
        try{
          var r = await api('/api/durak/poll?room=' + enc(du.code) + '&v=' + du.v + '&vk=' + du.vk);
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
        ? await api('/api/durak/create', {method: 'POST', body: JSON.stringify({size: du.sizeN, perevod: perevodOn(), bot: !!vsBot})})
        : await api('/api/durak/join', {method: 'POST', body: JSON.stringify({room: code})});
      var j = {}; try{ j = await r.json(); }catch(e){}
      if(r.ok){
        gen++; clearTimeout(du.botT); du.botT = 0;
        du.code = j.code; du.v = 0; du.vk = 0; du.s = null; du.sel = null; du.flashTx = '';
        remember(j.code); show(true);
        $('durhand').style.display = '';
        startPoll(); sync(j); lobbyMsg('');
      } else if(!isCreate && r.status === 404 && code === saved()){
        remember('');                                  // köhnə kod: oyun artıq yoxdur, "Oyuna qayıt" düyməsi yox olsun
        lobbyMsg('Oyun bitib və ya tapılmadı');
      } else lobbyMsg(errOf(r, j));
    }catch(e){ lobbyMsg('Əlaqə problemi, yenidən cəhd et'); }
    busy = false;
  }

  /* ---- hərəkət göndərmək ---- */
  function send(body){
    if(!du.code) return;
    du.sel = null;
    api('/api/durak/move', {method: 'POST', body: JSON.stringify(Object.assign({room: du.code}, body))}).then(async function(r){
      var j = {}; try{ j = await r.json(); }catch(e){}
      if(!r.ok){ flash(j.error || 'Bu hərəkət mümkün deyil'); render(); return; }
      sync(j);
    }).catch(function(){ flash('Əlaqə problemi, yenidən cəhd et'); });
  }
  function onHand(c){
    var s = du.s; if(!s) return;
    var acts = s.actions || [];
    if(!acts.length){ if(s.status === 'play') flash('Növbə sənin deyil'); return; }
    if(acts.some(function(a){ return a[0] === 'attack' && same(a[1], c); })){ send({action: 'attack', card: c}); return; }
    if(du.sel && same(du.sel, c)){ du.sel = null; render(); return; }
    var opts = acts.filter(function(a){ return a[0] === 'defend' && same(a[2], c); });
    var canTr = acts.some(function(a){ return a[0] === 'transfer' && same(a[1], c); });
    if(opts.length === 1 && !canTr){ send({action: 'defend', attack: opts[0][1], card: c}); return; }
    if(opts.length === 0 && !canTr){
      if(!acts.some(function(a){ return a[0] === 'defend'; }) && acts.some(function(a){ return a[0] === 'pass'; })){
        var inTable = s.table.some(function(p){ return p.a[0] === c[0] || (p.d && p.d[0] === c[0]); });
        if(!inTable) flash('Bu rəqəm masada yoxdur');
        else if(s.cap && s.table.length >= s.cap) flash('Limit dolub: müdafiəçinin raund başındakı kart sayı qədər (' + s.cap + ') atmaq olar');
        else flash('Bu kartı indi atmaq olmaz');
      } else flash('Bu kart onu bağlamır');
      return;
    }
    du.sel = c; render();
  }
  function onTable(i){
    var s = du.s, p = s && s.table[i];
    if(!p || p.d || !du.sel) return;
    var ok = (s.actions || []).some(function(a){ return a[0] === 'defend' && same(a[1], p.a) && same(a[2], du.sel); });
    if(ok) send({action: 'defend', attack: p.a, card: du.sel});
    else flash('Bu kart onu bağlamır');
  }

  /* ---- kimin növbəsidir ---- */
  function turnSet(s){
    if(s.status !== 'play') return [];
    if(!s.started) return s.attacker >= 0 ? [s.attacker] : [];
    var pend = !s.took && s.table.some(function(p){ return !p.d; });
    if(pend) return [s.defender];
    var r = [];
    s.players.forEach(function(p, i){
      if(i !== s.defender && !p.out && p.n > 0 && (s.passed || []).indexOf(i) < 0) r.push(i);
    });
    return r;
  }
  function roleOf(s, i){
    if(s.status !== 'play' || (s.players[i] || {}).out) return '';
    return i === s.attacker ? 'att' : (i === s.defender ? 'def' : '');
  }

  /* ---- göstərmə ---- */
  /* ---- kompüter: növbə ondadırsa serverdən bir hərəkət istə ---- */
  function botDrive(){
    var s = du.s;
    if(!s || !s.bot || s.status !== 'play' || !s.botturn || !du.code || du.botT) return;
    var c = du.code, g = gen, v0 = du.v;
    function again(ms){ du.botT = setTimeout(function(){ du.botT = 0; botDrive(); }, ms); }
    du.botT = setTimeout(function(){
      du.botT = 0;
      if(du.code !== c) return;
      if(g !== gen){ botDrive(); return; }   // oyun yenicə açılanda gen dəyişir: dayanma, yenidən qur
      api('/api/durak/bot', {method: 'POST', body: JSON.stringify({room: c})}).then(function(r){ return r.ok ? r.json() : null; }).then(function(j){
        if(g !== gen) return;
        if(!j){ again(2500); return; }
        var same = j.v === v0;
        sync(j);
        if(same && !du.botT) again(1200);
      }).catch(function(){ if(g === gen && !du.botT) again(2500); });
    }, 280 + Math.floor(Math.random() * 220));
  }
  function render(){
    var s = du.s; if(!s) return;
    $('durcode').textContent = 'KOD ' + s.code.toUpperCase();
    renderStrip(s); renderPiles(s); renderTable(s); renderHand(s); renderAct(s); renderExtra(s); renderMsg(); renderWait(s); renderRes(s);
    $('dushare').hidden = !!s.bot;
    try{ voice.presence(s.vc, s.vk); }catch(e){}
    botDrive();
  }
  function chip(s, i, ts){
    var p = s.players[i], role = roleOf(s, i);
    var play = s.status !== 'wait';
    var icon = role === 'att' ? ' \u2694\uFE0F' : (role === 'def' ? ' \uD83D\uDEE1\uFE0F' : '');
    var small = (p.me ? '' : '<span class="donl' + (p.online ? ' on' : '') + '"></span>') +
      (!play ? '' : (p.out ? esc(T('Çıxıb')) : icon.trim() + ' ' + p.n + (s.size > 3 ? '' : ' ' + esc(T('Kart').toLowerCase()))));
    return '<div class="dchip' + (ts.indexOf(i) >= 0 ? ' turn' : '') + (p.out ? ' out' : '') + '"><div class="dav">' + av(p) + '</div><div class="dwho">' +
      (p.me ? '<b>' + esc(T('Sən')) + '</b>' : '<b data-nt>' + esc(p.name) + '</b>') + '<small>' + small + '</small></div></div>';
  }
  function renderStrip(s){
    var box = $('durpl'), n = s.players.length, me = s.me, multi = s.size > 2, ts = turnSet(s);
    box.classList.toggle('multi', multi);
    box.classList.toggle('m4', s.size > 3);
    if(multi){
      box.classList.remove('turn');
      var h = '';
      for(var k = 1; k < n; k++) h += chip(s, (me + k) % n, ts);
      for(var q = n; q < s.size; q++) h += '<div class="dchip"><div class="dav">?</div><div class="dwho"><b>' + esc(T('Gözlənilir')) + '</b></div></div>';
      h += chip(s, me, ts);
      box.innerHTML = h;
      return;
    }
    var oi = n > 1 ? (me + 1) % n : -1, o = oi >= 0 ? s.players[oi] : null;
    box.classList.toggle('turn', oi >= 0 && ts.indexOf(oi) >= 0);
    if(!o){
      box.innerHTML = '<div class="dav">?</div><div class="dwho"><b>' + esc(T('Rəqib')) + '</b><small><span>' + esc(T('Rəqib gözlənilir...')) + '</span></small></div>';
      return;
    }
    var role = roleOf(s, oi);
    var rt = role === 'att' ? ' \u00B7 ' + T('Hücum') : (role === 'def' ? ' \u00B7 ' + T('Müdafiə') : '');
    box.innerHTML = '<div class="dav">' + av(o) + '</div><div class="dwho"><b data-nt>' + esc(o.name) + '</b><small><span class="donl' + (o.online ? ' on' : '') + '"></span>' +
      '<span>' + (o.out ? esc(T('Çıxıb')) : o.n + ' ' + esc(T('Kart').toLowerCase()) + esc(rt)) + '</span></small></div>' +
      (s.trump ? '<div class="dsc"><small>' + esc(T('Koz')) + '</small><span class="' + (isRed(s.trump) ? 'rd' : '') + '">' + SUIT[s.trump] + '</span></div>' : '');
  }
  function pickable(s, i){
    if(!du.sel) return false;
    var p = s.table[i];
    return !!p && !p.d && (s.actions || []).some(function(a){ return a[0] === 'defend' && same(a[1], p.a) && same(a[2], du.sel); });
  }
  function renderTable(s){
    var t = $('durtable');
    t.hidden = s.status === 'wait';
    if(s.status === 'wait'){ t.innerHTML = ''; return; }
    var h = s.table.map(function(p, i){
      return '<div class="dkp' + (pickable(s, i) ? ' pick' : '') + '" data-i="' + i + '">' + cardHtml(p.a) + (p.d ? '<span class="dko">' + cardHtml(p.d) + '</span>' : '') + '</div>';
    }).join('');
    t.innerHTML = h || (s.status === 'play' ? '<div class="dempty">' + esc(T('Masa boşdur')) + '</div>' : '');
  }
  function renderHand(s){
    var box = $('durhand');
    var mine = myTurn(s), acts = s.actions || [], ok = {};
    acts.forEach(function(a){
      if(a[0] === 'attack') ok[key(a[1])] = 1;
      if(a[0] === 'defend') ok[key(a[2])] = 1;
      if(a[0] === 'transfer') ok[key(a[1])] = 1;
    });
    var order = 'SCDH';
    du.hs = (s.hand || []).slice().sort(function(a, b){
      var ta = a[1] === s.trump ? 1 : 0, tb = b[1] === s.trump ? 1 : 0;
      if(ta !== tb) return ta - tb;
      if(a[1] !== b[1]) return order.indexOf(a[1]) - order.indexOf(b[1]);
      return a[0] - b[0];
    });
    box.style.display = s.status === 'over' ? 'none' : '';
    box.innerHTML = du.hs.map(function(c, i){
      var cls = (mine ? (ok[key(c)] ? ' ok' : ' dim') : '') + (same(du.sel, c) ? ' sel' : '');
      return '<button type="button" class="dkh' + cls + '" data-i="' + i + '">' + cardHtml(c) + '</button>';
    }).join('');
  }
  function renderAct(s){
    var acts = s.actions || [];
    var has = function(k){ return acts.some(function(a){ return a[0] === k; }); };
    $('dutake').hidden = !has('take');
    $('dupass').hidden = !has('pass');
    $('dupass').textContent = T((!s.took && s.table.length && s.table.every(function(p){ return p.d; })) ? 'Bita' : 'Pas');
    $('dutrans').hidden = !has('transfer');
    $('dutrans').disabled = !(du.sel && acts.some(function(a){ return a[0] === 'transfer' && same(a[1], du.sel); }));
    $('duract').hidden = !(has('take') || has('pass') || has('transfer'));
  }
  function backs(n){ var k = n >= 12 ? 3 : (n >= 4 ? 2 : (n > 0 ? 1 : 0)), o = ''; for(var i = 0; i < k; i++) o += '<span class="dkb"></span>'; return o; }
  function renderPiles(s){
    var box = $('durpiles'), on = s.status === 'play' || s.status === 'over';
    box.hidden = !on;
    if(!on){ box.innerHTML = ''; return; }
    var deckN = s.deck || 0, disN = s.discard || 0, h = '<div class="dpile dstock">';
    if(deckN > 0){
      h += (s.tcard ? '<span class="dtr">' + cardHtml(s.tcard) + '</span>' : '') +
        '<span class="dstack">' + backs(deckN) + '<b class="dcnt">' + deckN + '</b></span>';
    } else {
      h += '<span class="dsuit' + (isRed(s.trump) ? ' rd' : '') + '">' + (SUIT[s.trump] || '') + '</span>';
    }
    h += '<small>' + esc(T('Kart bazarı')) + '</small></div>' +
      '<div class="dpile ddisc"><span class="dstack' + (disN ? '' : ' empty') + '">' + backs(disN) + (disN ? '<b class="dcnt">' + disN + '</b>' : '') + '</span>' +
      '<small>' + esc(T('Bita')) + '</small></div>';
    box.innerHTML = h;
  }
  function renderExtra(s){
    var e = $('durinfo'), t = '';
    var rule = esc(T('Perevod')) + ': <b>' + esc(T(s.perevod ? 'açıq' : 'bağlı')) + '</b>';
    if(s.status === 'play' || s.status === 'over'){
      t = esc(T('Koz')) + ': <b>' + (SUIT[s.trump] || '') + '</b> \u00B7 ' + rule;
    } else t = rule;
    e.innerHTML = t;
  }
  function msgText(s){
    if(s.status !== 'play') return '';
    var acts = s.actions || [];
    var has = function(k){ return acts.some(function(a){ return a[0] === k; }); };
    var mp = s.players[s.me] || {};
    if(acts.length){
      if(has('defend')) return T('Müdafiə et: kartı seç, sonra bağlayacağın kartı bas');
      if(has('take')) return T('Bağlaya bilmirsən? Götür');
      if(has('attack') && !s.started) return T('Sənin növbən: kart at');
      if(!s.took && s.table.length && s.table.every(function(p){ return p.d; })) return T('Hamısı bağlandı: bitaya getsin?');
      return T('Əlavə et və ya pas keç');
    }
    if(s.bot && s.botturn) return T('Kompüter düşünür...');
    if(mp.out) return T('Kartların bitdi: oyunu izləyirsən');
    var ts = turnSet(s).filter(function(i){ return i !== s.me; });
    if(ts.length === 1){
      var p = s.players[ts[0]] || {};
      if(!s.started) return (p.name || T('Rəqib')) + ' ' + (p.online ? T('oynayır...') : T('əlaqədə deyil'));
      return (p.name || T('Rəqib')) + ' ' + (p.online ? T('oynayır...') : T('əlaqədə deyil'));
    }
    if(ts.length > 1) return T('Əlavə kartlar gözlənilir...');
    return '';
  }
  function renderMsg(){
    var s = du.s, e = $('durmsg');
    if(!s){ e.textContent = ''; return; }
    if(du.flashTx){ e.textContent = T(du.flashTx); return; }
    e.textContent = msgText(s);
  }
  function renderWait(s){
    var w = $('durwait'), wait = s.status === 'wait';
    w.hidden = !wait;
    if(!wait) return;
    $('durwcode').textContent = s.code.toUpperCase();
    $('durwcnt').textContent = T('Oyunçu') + ': ' + s.joined + ' / ' + s.size;
    try{ inv.refresh(); }catch(e){}
  }
  function renderRes(s){
    var box = $('durres'), over = s.status === 'over';
    box.hidden = !over;
    if(!over){ box.innerHTML = ''; box.className = ''; return; }
    var title, why, win = false, lose = false, draw = false;
    if(s.loser === -1){ title = 'Bərabərlik'; why = T('Hamının kartı bitdi'); draw = true; }
    else if(s.loser === s.me){ title = 'Durak sənsən'; why = T('Kartlar sənin əlində qaldı'); lose = true; }
    else { title = 'Qazandın!'; why = 'Durak: ' + ((s.players[s.loser] || {}).name || ''); win = true; }
    if(s.quit != null){
      why = s.quit === s.me ? T('Oyundan çıxdın') : ((s.players[s.quit] || {}).name || '') + ' ' + T('oyundan çıxdı');
      if(s.quit === s.me){ lose = true; win = false; }
      else { win = true; lose = false; }
    }
    var ic = win ? 'fa-trophy' : (lose ? 'fa-heart-crack' : 'fa-handshake');
    box.className = 'res-show ' + (win ? 'win' : (lose ? 'lose' : 'draw'));
    box.innerHTML = '<div class="res-ic"><i class="fa-solid ' + ic + '" aria-hidden="true"></i></div><h3>' + esc(T(title)) + '</h3><p>' + esc(why) + '</p><button class="pbtn" id="durAgain" type="button">' + esc(T('Növbəti raund')) + '</button>';
    $('durAgain').onclick = async function(){
      if(!du.code) return;
      try{
        var r = await api('/api/durak/again', {method: 'POST', body: JSON.stringify({room: du.code, round: s.round})});
        if(r.ok) sync(await r.json()); else flash((window.wtErr && window.wtErr(r)) || 'Alınmadı');
      }catch(e){ flash('Əlaqə problemi, internetini yoxla'); }
    };
  }

  /* ---- paylaş, çıx ---- */
  async function share(){
    var s = du.s; if(!s) return;
    var url = location.origin + '/?durak=' + encodeURIComponent(s.code.toUpperCase());
    var text = T('Durak oynayaq! Kod: ' + s.code.toUpperCase());
    try{ if(navigator.share){ await navigator.share({title: 'Durak', text: text, url: url}); return; } }catch(e){ if(e && e.name === 'AbortError') return; }
    try{ await navigator.clipboard.writeText(url); flash('Link kopyalandı'); }
    catch(e){ flash('Kod: ' + s.code.toUpperCase()); }
  }

  /* ---- düymələr ---- */
  $('durNew').onclick = function(){ enter('', true); };
  $('durEnter').onclick = function(){
    var c = clean($('durCodeIn').value);
    if(!c){ lobbyMsg('Durak kodunu yaz'); return; }
    enter(c, false);
  };
  $('durCodeIn').addEventListener('keydown', function(e){ if(e.key === 'Enter'){ e.preventDefault(); $('durEnter').click(); } });
  $('durResume').onclick = function(){ var c = saved(); if(c) enter(c, false); };
  (function(){                                          // saxlanan oyun serverdə qalmayıbsa düyməni gizlət
    var c = saved(); if(!c) return;
    api('/api/durak/check?room=' + enc(c)).then(function(r){ if(r.status === 404 && saved() === c) remember(''); }).catch(function(){});
  })();
  $('durBot').onclick = function(){ enter('', true, true); };
  $('durback').onclick = function(){ close(true); };
  $('dushare').onclick = share;
  $('durwshare').onclick = share;
  $('durquit').onclick = function(){
    var s = du.s, c = du.code;
    if(!s || !c) return;
    var m = s.status === 'play' ? 'Oyundan çıxmaq istəyirsən? Oyun sənin üçün bitəcək.' : 'Oyundan çıxmaq istəyirsən?';
    wtConfirm(m, {ok: 'Oyundan çıx', danger: true}).then(function(y){
      if(!y || du.code !== c) return;
      close(false); remember('');
      api('/api/durak/quit', {method: 'POST', body: JSON.stringify({room: c})}).catch(function(){});
    });
  };
  $('dutake').onclick = function(){ send({action: 'take'}); };
  $('dupass').onclick = function(){ send({action: 'pass'}); };
  $('dutrans').onclick = function(){
    if(du.sel) send({action: 'transfer', card: du.sel});
    else flash('Ötürmək üçün kart seç');
  };
  $('durhand').onclick = function(e){
    var b = e.target.closest && e.target.closest('[data-i]'); if(!b) return;
    var c = du.hs[+b.getAttribute('data-i')]; if(c) onHand(c);
  };
  $('durtable').onclick = function(e){
    var b = e.target.closest && e.target.closest('[data-i]'); if(b) onTable(+b.getAttribute('data-i'));
  };
  document.addEventListener('visibilitychange', function(){
    if(document.visibilityState === 'visible' && du.code && !$('durak').hidden){ du.v = 0; startPoll(); }
  });

  /* ---- ?durak=KOD linki ---- */
  try{
    var q = new URLSearchParams(location.search).get('durak');
    if(q){ pendingDur = clean(q); history.replaceState(null, '', location.pathname); }
  }catch(e){}
  if(pendingDur){
    var tries = 0, iv = setInterval(function(){
      tries++;
      var menuOn = false;
      try{ menuOn = getComputedStyle($('menu')).display !== 'none'; }catch(e){}
      if(token && account && menuOn){ clearInterval(iv); var c = pendingDur; pendingDur = ''; enter(c, false); }
      else if(tries > 120) clearInterval(iv);
    }, 500);
  }
  window.wtJoinDurak = function(c){ enter(clean(c), false); };
  refreshResume();

  /* ayar açarı */
  (function(){
    var a = $('setperevod'), b = $('durperevod'); if(!a && !b) return;
    function paint(){ var on = perevodOn(); if(a) a.checked = on; if(b) b.checked = on; }
    function set(on){ try{ localStorage.setItem('wt_durperevod', on ? '1' : '0'); }catch(e){} paint(); }
    if(a) a.onchange = function(){ set(a.checked); };
    if(b) b.onchange = function(){ set(b.checked); };
    paint();
  })();
})();
