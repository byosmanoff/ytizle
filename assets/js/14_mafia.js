
/* ---------- Mafia otağı (4-12 nəfər, onlayn) ---------- */
(function(){
  var $ = function(id){ return document.getElementById(id); };
  var LS = 'wt_maf';
  var ma = {code: '', v: 0, s: null, at: 0, flashT: 0, flashTx: '', tmr: 0, csig: ''};
  var busy = false, gen = 0, pending = '';
  var voice = window.wtGameVoice({game: 'mafia', p: 'maf', code: function(){ return ma.code; }, say: function(t){ flash(t); },
    allow: function(){ var s = ma.s; if(!s || s.status !== 'play') return true; return !!s.alive && s.phase !== 'night'; }});
  var inv = window.wtInviteBox({game: 'mafia', user: 'mafinvU', go: 'mafinvGo', fr: 'mafinvFr', ft: 'mafinvFrT', msg: 'mafinvMsg', code: function(){ return ma.code; }});

  var ROLE = {mafia: 'Mafiya', doctor: 'Həkim', sheriff: 'Komissar', civil: 'Sakin'};
  var RICON = {mafia: 'fa-user-secret', doctor: 'fa-user-doctor', sheriff: 'fa-star', civil: 'fa-house-user'};
  var RINFO = {
    mafia:  {team:'Mafiya komandası',   goal:'Bütün sakinləri aradan qaldır', night:'Komandanla bir nəfəri seç və öldür', day:'Özünü sakin kimi apar, şübhəni başqasına yönəlt'},
    doctor: {team:'Sakinlər komandası', goal:'Bütün mafiyanı tapıb oyundan çıxarın', night:'Bir nəfəri qoru (ardıcıl iki gecə eyni adam olmaz)', day:'Müzakirə et və səs ver'},
    sheriff:{team:'Sakinlər komandası', goal:'Bütün mafiyanı tapıb oyundan çıxarın', night:'Bir nəfərin mafiya olub-olmadığını yoxla', day:'Öyrəndiklərini ehtiyatla paylaş, özünü gizlət'},
    civil:  {team:'Sakinlər komandası', goal:'Bütün mafiyanı tapıb oyundan çıxarın', night:'Yatırsan, gecə heç nə etmirsən', day:'Müzakirə et, mafiyanı tap və səs ver'}
  };
  function ric(r){ return '<i class="fa-solid ' + (RICON[r] || 'fa-user') + ' ri-' + r + '" aria-hidden="true"></i> '; }
  var RDESC = {
    mafia: 'Gecə komanda yoldaşlarınla bir sakini seç və öldür. Gündüz özünü sakin kimi apar.',
    doctor: 'Hər gecə bir nəfəri qoruyursan. Eyni adamı ardıcıl iki gecə qoruya bilməzsən.',
    sheriff: 'Hər gecə bir nəfərin mafiya olub-olmadığını yoxlayırsan.',
    civil: 'Gündüz müzakirə et, mafiyanı tap və səsvermədə oyundan çıxar.'
  };

  function T(s){ try{ return window.wtT ? window.wtT(s) : s; }catch(e){ return s; } }
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){ return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]; }); }
  function clean(c){ return String(c || '').toLowerCase().replace(/[^a-z0-9-]/g, '').slice(0, 30); }
  function enc(x){ return encodeURIComponent(x); }
  function sleep(ms){ return new Promise(function(r){ setTimeout(r, ms); }); }
  function av(p){ return p && p.avatar ? '<img src="' + esc(p.avatar) + '" alt="">' : esc(((p && p.name) || '?').slice(0, 1).toUpperCase()); }
  function rname(r){ return T(ROLE[r] || ''); }
  function pname(s, i){ return ((s.players[i] || {}).name) || '?'; }

  function saved(){ try{ return localStorage.getItem(LS) || ''; }catch(e){ return ''; } }
  function remember(c){ try{ if(c) localStorage.setItem(LS, c); else localStorage.removeItem(LS); }catch(e){} refreshResume(); }
  function refreshResume(){ var b = $('mafResume'); if(b) b.hidden = !saved(); }
  function lobbyMsg(t){ var e = $('mafMsg'); if(e) e.textContent = T(t || ''); }
  function flash(t){
    ma.flashTx = t; clearTimeout(ma.flashT);
    ma.flashT = setTimeout(function(){ ma.flashTx = ''; paintMsg(); }, 2800);
    paintMsg();
  }
  function paintMsg(){ var e = $('mafmsg'); if(e) e.textContent = ma.flashTx ? T(ma.flashTx) : ''; }
  function errOf(r, j){
    if(r.status === 404) return 'Belə oyun tapılmadı. Kodu yoxla.';
    if(r.status === 403) return 'Bu otaq doludur';
    if(r.status === 429) return 'Çox sorğu göndərdin, bir az gözlə';
    if(j && j.error) return j.error;
    return 'Əlaqə problemi, yenidən cəhd et';
  }
  function show(on){
    $('mafia').hidden = !on;
    try{ if(window.wtMafMusic){ if(on) wtMafMusic.start(); else wtMafMusic.stop(); } }catch(e){}
  }
  function stopTimer(){ clearInterval(ma.tmr); ma.tmr = 0; }
  function close(leave){
    var c = ma.code;
    try{ voice.leave(); }catch(e){}
    gen++; stopTimer();
    ma.code = ''; ma.s = null; ma.csig = ''; clearTimeout(ma.flashT); ma.flashTx = '';
    show(false);
    if(leave && c) api('/api/mafia/leave', {method: 'POST', body: JSON.stringify({room: c})}).catch(function(){});
    remember('');
  }
  function gone(){ try{ voice.leave(); }catch(e){} gen++; stopTimer(); ma.code = ''; ma.s = null; ma.csig = ''; remember(''); show(false); lobbyMsg('Oyun bitib və ya tapılmadı'); }

  /* ---- səslər ---- */
  function notify(p, s){
    var S = window.wtSfx; if(!S || !p) return;
    try{
      if(s.status === 'over' && p.status !== 'over'){
        var mw = s.winner === 'mafia';
        S.play(((s.role === 'mafia') === mw) ? 'win' : 'lose');
        return;
      }
      if(p.status === 'wait' && s.status === 'play'){ S.play('shuffle'); return; }
      if(s.joined > p.joined) S.play('join');
      if(s.phase !== p.phase || s.day !== p.day) S.play('turn');
      else if(s.log.length > p.log.length) S.play('take');
    }catch(e){}
  }

  /* ---- sinxronizasiya ---- */
  function sync(s){
    if(!s || s.code !== ma.code) return;
    if(s.v < ma.v) return;
    var prev = ma.s;
    ma.v = s.v; ma.s = s; ma.at = Date.now();
    notify(prev, s);
    render();
  }
  function startPoll(){
    var g = ++gen, fails = 0;
    (async function(){
      while(g === gen && ma.code){
        try{
          var r = await api('/api/mafia/poll?room=' + enc(ma.code) + '&v=' + ma.v + '&vk=' + voice.vk());
          if(g !== gen) return;
          if(r.status === 404){ gone(); return; }
          if(!r.ok){ fails++; try{ window.wtReconnectBanner('Əlaqə kəsildi, yenidən cəhd...'); }catch(x){} await sleep(2000); continue; }
          if(fails > 0){ try{ window.wtReconnectBanner('Yenidən qoşuldu', true); setTimeout(function(){ window.wtReconnectBanner(''); }, 1800); }catch(x){} }
          fails = 0; sync(await r.json());
        }catch(e){ if(g !== gen) return; fails++; try{ window.wtReconnectBanner('Əlaqə kəsildi, yenidən cəhd...'); }catch(x){} await sleep(2000); }
      }
    })();
  }
  async function enter(code, isCreate){
    if(busy) return false; busy = true; var okk = false;
    try{
      var r = isCreate
        ? await api('/api/mafia/create', {method: 'POST', body: '{}'})
        : await api('/api/mafia/join', {method: 'POST', body: JSON.stringify({room: code})});
      var j = {}; try{ j = await r.json(); }catch(e){}
      if(r.ok){
        gen++; stopTimer();
        ma.code = j.code; ma.v = 0; ma.s = null; ma.flashTx = ''; ma.csig = ''; ma.sky = undefined;
        okk = true; remember(j.code); show(true);
        $('mafmsgs').textContent = '';
        startPoll(); sync(j); lobbyMsg('');
        ma.tmr = setInterval(paintTimer, 500);
      } else if(!isCreate && r.status === 404 && code === saved()){
        remember(''); lobbyMsg('Oyun bitib və ya tapılmadı');
      } else lobbyMsg(errOf(r, j));
    }catch(e){ lobbyMsg('Əlaqə problemi, yenidən cəhd et'); }
    busy = false;
    return okk;
  }
  function post(path, body){
    var c = ma.code; if(!c) return Promise.resolve();
    return api(path, {method: 'POST', body: JSON.stringify(Object.assign({room: c}, body || {}))}).then(async function(r){
      if(c !== ma.code) return;
      var j = {}; try{ j = await r.json(); }catch(e){}
      if(r.status === 404){ gone(); return; }
      if(r.ok) sync(j); else flash((j && j.error) || errOf(r, j));
    }).catch(function(){ if(c === ma.code) flash('Əlaqə problemi, yenidən cəhd et'); });
  }

  /* ---- taymer ---- */
  function paintTimer(){
    var e = $('maftm'), s = ma.s; if(!e) return;
    if(!s || s.status !== 'play'){ e.textContent = ''; return; }
    var left = Math.max(0, s.left - Math.floor((Date.now() - ma.at) / 1000));
    e.textContent = Math.floor(left / 60) + ':' + ('0' + (left % 60)).slice(-2);
    e.classList.toggle('low', left <= 10);
  }

  /* ---- səma: hər faza dəyişəndə orbit 180° dönür (günəş batır, ay doğur və əksinə) ---- */
  function paintSky(n){
    var el = $('mafsky'); if(!el) return;
    if(ma.sky === undefined){
      ma.sky = n; ma.ang = n ? 168 : -12;
      el.classList.add('nt'); el.style.setProperty('--ang', ma.ang + 'deg');
      requestAnimationFrame(function(){ requestAnimationFrame(function(){ el.classList.remove('nt'); }); });
      return;
    }
    if(n === ma.sky) return;
    ma.sky = n; ma.ang += 180;
    el.style.setProperty('--ang', ma.ang + 'deg');
    var g = $('mafglow'); g.classList.remove('go'); void g.offsetWidth; g.classList.add('go');
  }

  /* ---- aparıcı: oyunu aparan səs ---- */
  var host = {code: '', st: null, q: [], busy: false, tm: 0};
    function pick(a){ return a[Math.floor(Math.random() * a.length)]; }
  function hostPaint(txt, n){
    var e = $('mafhsay'); if(!e) return;
    e.textContent = '';
    var a = document.createElement('span'), b = document.createElement('span');
    a.textContent = txt.slice(0, n); b.textContent = txt.slice(n); b.style.visibility = 'hidden';
    e.appendChild(a); e.appendChild(b);
  }

  function hostNext(){
    var box = $('mafhost'), t = host.q.shift();
    if(t === undefined){ host.busy = false; box.classList.remove('talk'); return; }
    host.busy = true; box.classList.add('talk');
    var reduce = false; try{ reduce = matchMedia('(prefers-reduced-motion:reduce)').matches; }catch(e){}
    var n = reduce ? t.length : 0;
    clearInterval(host.tm); clearTimeout(host.tm);
    hostPaint(t, n);
    host.tm = setInterval(function(){
      n += 2; hostPaint(t, n);
      if(n >= t.length){
        clearInterval(host.tm); box.classList.remove('talk');
        if(host.q.length) host.tm = setTimeout(hostNext, 1800); else host.busy = false;
      }
    }, 28);
  }
  function hostSay(lines){ host.q = host.q.concat(lines).slice(-4); if(!host.busy) hostNext(); }
  function hostRoleNight(s){
    if(!s.alive) return 'Sən artıq tamaşaçısan, səssizcə izlə.';
    if(s.role === 'mafia') return 'Mafiya oyanır... Səssizcə qurbanınızı seçin.';
    if(s.role === 'doctor') return 'Həkim oyanır. Bu gecə kimi xilas edirsən?';
    if(s.role === 'sheriff') return 'Komissar oyanır. Kimdən şübhələnirsən?';
    return 'Sən yatırsan. Səhərə qədər səs çıxarma...';
  }
  function hostEvent(s, e){
    var n = pname(s, e.p), r = e.r ? rname(e.r) : '';
    switch(e.k){
      case 'night_dead': return pick([
        'Səhər açılır... amma bu səhər hamı yerində deyil. ' + n + ' gecəni çıxara bilmədi.' + (r ? ' O, ' + r + ' idi.' : ''),
        'Şəhər oyanır və dəhşətli xəbər gəlir: ' + n + ' bu gecə öldürüldü.' + (r ? ' Rolu: ' + r + '.' : '')]);
      case 'night_none': return pick([
        'Möcüzə! Bu gecə heç kim ölmədi. Ya mafiya yatdı, ya da həkim işini bildi.',
        'Gecə sakit keçdi. Hamı sağdır... hələlik.']);
      case 'vote_dead': return 'Xalq qərarını verdi: ' + n + ' şəhəri tərk edir. ' +
        (e.r === 'mafia' ? 'O, mafiya idi. Düz tapdınız!' : 'Təəssüf... o, günahsız ' + (r ? r.toLowerCase() : 'sakin') + ' idi.');
      case 'vote_none': return 'Səslər bölündü, bu gün heç kim getmir. Mafiya rahat nəfəs alır.';
      case 'left': return n + ' masanı tərk etdi.' + (r ? ' O, ' + r + ' idi.' : '');
    }
    return '';
  }
  function hostLines(s, p){
    var L = [], logn = (s.log || []).length;
    if(s.status === 'wait'){
      var nw = s.joined, cnt = nw >= s.min
        ? ' Kifayət qədər oyunçu var, oyunu başlada bilərsiniz.'
        : ' Başlamaq üçün hələ ' + (s.min - nw) + ' nəfər çatmır.';
      if(!p || p.status !== 'wait') L.push('Xoş gəlmisiniz, cənablar və xanımlar! Mafiya masası qurulur, ' + nw + ' oyunçu hazırdır.' + cnt);
      else if(nw > p.joined) L.push(pname(s, s.players.length - 1) + ' masaya əyləşdi. Cəmi ' + nw + ' nəfər.' + cnt);
      else if(nw < p.joined) L.push('Kimsə masadan qalxdı. İndi ' + nw + ' nəfərik.');
      return L;
    }
    var fresh = p && p.status === 'play' && p.logn <= logn ? s.log.slice(p.logn) : [];
    var started = p && p.status === 'wait';
    if(started){
      L.push(s.role
        ? 'Masa hazırdır, kartlar paylandı. Sənin rolun: ' + rname(s.role) + '. Sirrini yaxşı saxla!'
        : 'Masa hazırdır, kartlar paylandı. Oyun başlayır!');
    }
    fresh.forEach(function(e){ var t = hostEvent(s, e); if(t) L.push(t); });
    var changed = !p || p.status !== s.status || p.phase !== s.phase || p.day !== s.day;
    if(s.status === 'over'){
      if(changed || fresh.length) L.push(s.winner === 'mafia'
        ? 'Oyun bitdi! Mafiya şəhəri ələ keçirdi. Gecənin sahibləri onlar oldu.'
        : 'Oyun bitdi! Mafiya ifşa olundu, şəhər xilas oldu. Əhsən, sakinlər!');
      return L;
    }
    if(changed){
      if(s.phase === 'night') L.push(pick([
        'Gecə ' + s.day + ' çökür. Şəhər yuxuya gedir, hamı gözlərini yumur... ',
        'Qaranlıq düşür. Gecə ' + s.day + ', hamı yatır... ']) + hostRoleNight(s));
      else if(s.phase === 'day') L.push(fresh.length ? pick([
        'İndi müzakirə vaxtıdır. Kim şübhəli görünür? Danışın!',
        'Söz sizindir. Kim yalan danışır? Fikrinizi deyin.']) : 'Gündüz ' + s.day + '. Müzakirə vaxtıdır, şübhəlini tapın!');
      else if(s.phase === 'vote') L.push(pick([
        'Müzakirə bitdi. Səsvermə vaxtıdır: kimi şəhərdən qovaq?',
        'Vaxt doldu! İndi hər kəs bir ad seçir. Bir səs, bir qərar.']));
    }
    return L;
  }
  function renderHost(s){
    var box = $('mafhost'); if(!box) return;
    box.hidden = false;
    if(host.code !== s.code){
      host.code = s.code; host.st = null; host.q = []; host.busy = false;
      clearInterval(host.tm); clearTimeout(host.tm);
    }
    var p = host.st;
    host.st = {status: s.status, phase: s.phase, day: s.day, logn: (s.log || []).length, joined: s.joined};
    var L = hostLines(s, p);
    if(L.length) hostSay(L);
  }

  /* ---- çəkmə ---- */
  function render(){
    var s = ma.s; if(!s) return;
    var isNight = s.status === 'play' && s.phase === 'night';
    $('mafia').classList.toggle('night', isNight);
    paintSky(isNight);
    $('mafcode').textContent = s.code.toUpperCase();
    renderBar(s); renderPlayers(s); renderMain(s); renderChat(s); paintTimer(); paintMsg();
    try{ voice.presence(s.vc, s.vk); voice.refresh(); }catch(e){}
    if(s.status === 'wait'){ try{ inv.refresh(); }catch(e){} }
  }
  function renderBar(s){
    var bar = $('mafbar');
    if(s.status === 'wait'){ bar.hidden = true; return; }
    bar.hidden = false;
    var t = '', h = '', ic = 'fa-flag';
    if(s.status === 'over'){ t = 'Oyun bitdi'; h = ''; ic = 'fa-flag-checkered'; }
    else if(s.phase === 'night'){
      ic = 'fa-moon'; t = T('Gecə') + ' ' + s.day;
      h = !s.alive ? 'Sən oyundan çıxmısan: izləyirsən' :
          s.role === 'mafia' ? 'Qurbanı seç' : s.role === 'doctor' ? 'Kimi qoruyursan?' :
          s.role === 'sheriff' ? 'Kimi yoxlayırsan?' : 'Şəhər yatır...';
    } else if(s.phase === 'day'){
      ic = 'fa-sun'; t = T('Gündüz') + ' ' + s.day; h = 'Müzakirə: şübhəlini tap';
    } else if(s.phase === 'vote'){
      ic = 'fa-gavel'; t = T('Səsvermə'); h = !s.alive ? 'Sən oyundan çıxmısan: izləyirsən' : 'Kimi oyundan çıxarırıq?';
    }
    $('mafph').innerHTML = '<i class="fa-solid ' + ic + '" aria-hidden="true"></i>' + esc(T(t));
    $('mafbar').setAttribute('data-ph', s.status === 'over' ? 'over' : s.phase);
    $('mafphs').textContent = T(h);
  }
  function renderPlayers(s){
    var act = s.act, tg = (act && act.targets) || [];
    var html = s.players.map(function(p, i){
      var cls = 'mchip' + (p.alive ? '' : ' dead') + (p.me ? ' me' : '');
      var canTap = tg.indexOf(i) >= 0;
      if(p.alive && p.role) cls += ' rl-' + p.role;
      if(canTap) cls += ' tgt';
      if(act && act.pick === i) cls += ' pk';
      var sub = '', sc = 'ms';
      if(s.status === 'wait'){
        sub = '<span class="monl' + (p.online ? ' on' : '') + '"></span>' + (p.me ? T('Sən') : '');
      } else {
        if(!p.alive){ sub = '<i class="fa-solid fa-skull-crossbones" aria-hidden="true"></i> ' + esc((p.left ? T('oyundan çıxdı') : '') + (p.role ? ' ' + rname(p.role) : '')); }
        else if(p.role){ sub = ric(p.role) + esc(rname(p.role)); if(p.role === 'mafia') sc += ' r-mafia'; }
        if(s.phase === 'vote' && p.vote !== undefined){ sub = '<i class="fa-solid fa-hand-point-right" aria-hidden="true"></i> ' + esc(p.vote === -1 ? T('Pas') : pname(s, p.vote)); }
        else if(s.phase === 'night' && p.pick !== undefined && p.pick >= 0){ sub = '<i class="fa-solid fa-crosshairs" aria-hidden="true"></i> ' + esc(pname(s, p.pick)); }
        if(!sub && p.alive) sub = '<span class="monl' + (p.online ? ' on' : '') + '"></span>';
      }
      var tag = canTap ? 'button' : 'div';
      return '<' + tag + (canTap ? ' type="button" data-i="' + i + '"' : '') + ' class="' + cls + '"><span class="mav">' + av(p) + '</span><span class="mwho"><span class="mn">' + esc(p.name) + '</span><span class="' + sc + '">' + (sub.charAt(0) === '<' ? sub : esc(sub)) + '</span></span></' + tag + '>';
    }).join('');
    $('mafplayers').innerHTML = html;
  }
  function evText(s, e){
    var nm = esc(e.p !== undefined ? pname(s, e.p) : '');
    var r = e.r ? ' (' + esc(rname(e.r)) + ')' : '';
    switch(e.k){
      case 'night_none': return esc(T('Gecə')) + ' ' + e.d + ': ' + esc(T('heç kim ölmədi'));
      case 'night_dead': return esc(T('Gecə')) + ' ' + e.d + ': ' + nm + ' ' + esc(T('öldürüldü')) + r;
      case 'vote_dead': return esc(T('Gün')) + ' ' + e.d + ': ' + nm + ' ' + esc(T('oyundan çıxarıldı')) + r + ' \u2014 ' + e.n + ' ' + esc(T('səs'));
      case 'vote_none': return esc(T('Gün')) + ' ' + e.d + ': ' + esc(T('səs bölündü, heç kim çıxarılmadı'));
      case 'left': return nm + ' ' + esc(T('oyundan çıxdı')) + r;
    }
    return '';
  }
  function renderMain(s){
    var wait = s.status === 'wait', play = s.status === 'play', over = s.status === 'over';
    $('mafwait').hidden = !wait;
    if(wait){
      $('mafwcode').textContent = s.code.toUpperCase();
      $('mafwcnt').textContent = s.joined + ' / ' + s.size + ' \u00B7 ' + T('Başlamaq üçün ən azı') + ' ' + s.min;
      var st = $('mafstart');
      st.hidden = !s.host;
      st.disabled = s.joined < s.min;
      st.style.opacity = s.joined < s.min ? '.5' : '';
      $('mafbots').hidden = !s.host;
    }
    /* rol kartı */
    var rb = $('mafrole');
    if(play && s.role){
      rb.hidden = false; rb.className = 'r-' + s.role;
      var d = RDESC[s.role];
      var ri = RINFO[s.role];
      function rrow(ic, lbl, tx){ return '<div class="rrow"><span class="rri"><i class="fa-solid ' + ic + '" aria-hidden="true"></i></span><span><small>' + esc(T(lbl)) + '</small><b>' + esc(T(tx)) + '</b></span></div>'; }
      rb.innerHTML = '<div class="rtop"><i class="fa-solid ' + RICON[s.role] + '" aria-hidden="true"></i><div><small>' + esc(T('Sənin rolun')) + '</small><b>' + esc(rname(s.role)) + '</b><em>' + esc(T(ri.team)) + '</em></div></div>';
    } else rb.hidden = true;
    /* komissarın yoxlama nəticələri */
    var cb = $('mafchecks');
    if(play && s.role === 'sheriff' && s.checks && s.checks.length){
      cb.hidden = false;
      cb.innerHTML = s.checks.map(function(c){
        return '<div>' + esc(T('Gecə')) + ' ' + c.d + ': <b>' + esc(pname(s, c.p)) + '</b> \u2014 ' + (c.m ? '<span class="bad"><i class="fa-solid fa-circle-xmark" aria-hidden="true"></i> ' + esc(T('Mafiyadır')) + '</span>' : '<span class="good"><i class="fa-solid fa-circle-check" aria-hidden="true"></i> ' + esc(T('Mafiya deyil')) + '</span>') + '</div>';
      }).join('');
    } else cb.hidden = true;
    renderAct(s, play);
    /* nəticə */
    var rs = $('mafres');
    if(over){
      var mw = s.winner === 'mafia', mine = (s.role === 'mafia') === mw;
      rs.hidden = false;
      rs.className = mine ? 'win' : 'lose';
      rs.innerHTML = '<span class="mres-ic"><i class="fa-solid ' + (mine ? 'fa-trophy' : 'fa-skull') + '" aria-hidden="true"></i></span><b>' + esc(T(mw ? 'Mafiya qalib gəldi' : 'Sakinlər qalib gəldi')) + '</b><small>' + esc(T(mine ? 'Qazandın!' : 'Uduzdun')) + '</small>' +
        (s.host ? '<button class="pbtn" id="mafagain">' + esc(T('Yenidən oyna')) + '</button>' : '<small style="margin:0">' + esc(T('Ev sahibinin yenidən başlatması gözlənilir')) + '</small>');
      var b = $('mafagain'); if(b) b.onclick = function(){ post('/api/mafia/again'); };
    } else rs.hidden = true;
    /* hadisələr (yeni üstdə) */
    $('maflog').innerHTML = (s.log || []).slice().reverse().map(function(e){
      return '<div' + ((e.k === 'night_dead' || e.k === 'vote_dead') ? ' class="bad"' : '') + '>' + evText(s, e) + '</div>';
    }).join('');
  }
  function renderAct(s, play){
    var box = $('mafact'), a = s.act;
    if(!play || !a){ box.hidden = true; box.textContent = ''; return; }
    box.hidden = false;
    var h = '';
    if(a.kind === 'kill'){
      h = '<div class="mhint"><i class="fa-solid fa-crosshairs" aria-hidden="true"></i> ' + esc(T('Qurbanı seç (yuxarıdakı siyahıdan toxun)')) + '</div>' +
          (a.pick >= 0 ? '<div class="mnote">' + esc(T('Seçdiyin:')) + ' ' + esc(pname(s, a.pick)) + '</div>' : '');
    } else if(a.kind === 'save'){
      h = '<div class="mhint"><i class="fa-solid fa-shield-heart" aria-hidden="true"></i> ' + esc(T('Kimi qoruyursan? (siyahıdan toxun)')) + '</div>' +
          (a.pick >= 0 ? '<div class="mnote">' + esc(T('Seçdiyin:')) + ' ' + esc(pname(s, a.pick)) + '</div>' : '') +
          (a.block >= 0 ? '<div class="mnote">' + esc(T('Dünən qoruduğun adamı bu gecə qoruya bilməzsən')) + ': ' + esc(pname(s, a.block)) + '</div>' : '');
    } else if(a.kind === 'check'){
      h = a.done ? '<div class="mnote">' + esc(T('Bu gecə yoxlama etdin')) + '</div>' : '<div class="mhint">' + '<i class="fa-solid fa-magnifying-glass" aria-hidden="true"></i> ' + esc(T('Yoxlamaq üçün oyunçu seç')) + '</div>';
    } else if(a.kind === 'skip'){
      var al = s.players.filter(function(p){ return p.alive; }).length, need = Math.floor(al / 2) + 1;
      h = '<button type="button" class="pbtn' + (a.done ? ' on' : '') + '" id="mafskip"' + (a.done ? ' disabled' : '') + '>' +
          '<i class="fa-solid fa-gavel" aria-hidden="true"></i> ' + esc(T(a.done ? 'Gözlənilir' : 'Səsverməyə keç')) + ' (' + (s.skips || 0) + '/' + need + ')</button>';
    } else if(a.kind === 'vote'){
      h = '<div class="mhint"><i class="fa-solid fa-hand-point-up" aria-hidden="true"></i> ' + esc(T('Oyundan çıxarmaq istədiyin oyunçunu seç')) + '</div>' +
          '<button type="button" class="pbtn alt' + (a.pick === -1 ? ' on' : '') + '" id="mafnone"><i class="fa-solid fa-ban" aria-hidden="true"></i> ' + esc(T('Heç kim (ötür)')) + '</button>';
    }
    box.innerHTML = h;
    var sk = $('mafskip'); if(sk) sk.onclick = function(){ post('/api/mafia/act', {kind: 'skip'}); };
    var nn = $('mafnone'); if(nn) nn.onclick = function(){ post('/api/mafia/act', {kind: 'vote', t: -1}); };
  }
  function canTalk(s){ if(s.status !== 'play') return true; if(!s.alive) return true; if(s.phase === 'night') return s.role === 'mafia'; return true; }
  function renderChat(s){
    var inp = $('mafin'), ok = canTalk(s);
    inp.disabled = !ok; $('mafsend').disabled = !ok;
    var ph = !ok ? 'Gecə yalnız mafiya yaza bilər' :
      (s.status === 'play' && !s.alive) ? 'Ölülər çatı (yalnız ölülər görür)' :
      (s.status === 'play' && s.phase === 'night') ? 'Mafiya çatı (yalnız mafiya görür)' : 'Mesaj yaz...';
    inp.placeholder = T(ph);
    var last = s.chat.length ? s.chat[s.chat.length - 1].id : 0;
    var lg = s.log || [];
    var sig = s.chat.length + ':' + last + ':' + lg.length + ':' + s.players.map(function(p){ return p.name; }).join('|');
    if(sig === ma.csig) return;
    ma.csig = sig;
    var box = $('mafmsgs');
    var atBot = box.scrollHeight - box.scrollTop - box.clientHeight < 50;
    var parts = [], li = 0;
    function sys(e){ return '<div class="mcm sys' + ((e.k === 'night_dead' || e.k === 'vote_dead' || e.k === 'left') ? ' bad' : '') + '">' + evText(s, e) + '</div>'; }
    s.chat.forEach(function(m){
      while(li < lg.length && (lg[li].c || 0) < m.id) parts.push(sys(lg[li++]));
      var p = s.players[m.f] || {};
      var mark = m.ch === 'maf' ? ' <i class="fa-solid fa-user-secret" aria-hidden="true"></i>' : m.ch === 'dead' ? ' <i class="fa-solid fa-ghost" aria-hidden="true"></i>' : '';
      parts.push('<div class="mcm ' + (m.ch === 'maf' ? 'maf' : m.ch === 'dead' ? 'dead' : '') + '"><b>' + esc(p.name || '?') + mark + ':</b> ' + esc(m.t) + '</div>');
    });
    while(li < lg.length) parts.push(sys(lg[li++]));
    box.innerHTML = parts.length ? parts.join('') : '<div class="mempty">' + esc(T('Hələ mesaj yoxdur')) + '</div>';
    if(atBot) box.scrollTop = box.scrollHeight;
  }

  /* ---- hərəkətlər ---- */
  $('mafplayers').onclick = function(e){
    var b = e.target.closest && e.target.closest('[data-i]'); if(!b || !ma.s || !ma.s.act) return;
    var i = +b.getAttribute('data-i'), a = ma.s.act;
    if((a.targets || []).indexOf(i) < 0) return;
    post('/api/mafia/act', {kind: a.kind, t: i});
  };
  function sendChat(){
    var inp = $('mafin'), t = inp.value.trim();
    if(!t || inp.disabled) return;
    inp.value = '';
    post('/api/mafia/chat', {text: t});
  }
  $('mafsend').onclick = sendChat;
  $('mafin').addEventListener('keydown', function(e){ if(e.key === 'Enter'){ e.preventDefault(); sendChat(); } });
  $('mafstart').onclick = function(){ post('/api/mafia/start'); };
  $('mafbotadd').onclick = function(){ post('/api/mafia/bots', {n: 1}); };
  $('mafbotdel').onclick = function(){ post('/api/mafia/bots', {clear: true}); };

  /* ---- paylaş, çıx ---- */
  async function share(){
    var s = ma.s; if(!s) return;
    var url = location.origin + '/?mafia=' + encodeURIComponent(s.code.toUpperCase());
    var text = T('Mafia oynayaq! Kod: ' + s.code.toUpperCase());
    try{ if(navigator.share){ await navigator.share({title: 'Mafia', text: text, url: url}); return; } }catch(e){ if(e && e.name === 'AbortError') return; }
    try{ await navigator.clipboard.writeText(url); flash('Link kopyalandı'); }
    catch(e){ flash('Kod: ' + s.code.toUpperCase()); }
  }
  $('mafshare').onclick = share;
  $('mafwshare').onclick = share;

  /* ---- lobbi düymələri ---- */
  $('mafNew').onclick = function(){ enter('', true); };
  $('mafBot').onclick = async function(){
    if(!(await enter('', true))) return;
    await post('/api/mafia/bots', {n: 5});
    await post('/api/mafia/start');
  };
  $('mafEnter').onclick = function(){
    var c = clean($('mafCodeIn').value);
    if(!c){ lobbyMsg('Mafia kodunu yaz'); return; }
    enter(c, false);
  };
  $('mafCodeIn').addEventListener('keydown', function(e){ if(e.key === 'Enter'){ e.preventDefault(); $('mafEnter').click(); } });
  $('mafResume').onclick = function(){ var c = saved(); if(c) enter(c, false); };
  (function(){
    var c = saved(); if(!c) return;
    api('/api/mafia/check?room=' + enc(c)).then(function(r){ if(r.status === 404 && saved() === c) remember(''); }).catch(function(){});
  })();
  $('mafback').onclick = function(){ close(true); };
  $('mafquit').onclick = function(){
    var s = ma.s, c = ma.code;
    if(!s || !c) return;
    var m = s.status === 'play' ? 'Oyundan çıxmaq istəyirsən? Oyun sənin üçün bitəcək.' : 'Oyundan çıxmaq istəyirsən?';
    wtConfirm(m, {ok: 'Oyundan çıx', danger: true}).then(function(y){
      if(!y || ma.code !== c) return;
      close(false);
      api('/api/mafia/quit', {method: 'POST', body: JSON.stringify({room: c})}).catch(function(){});
    });
  };
  document.addEventListener('visibilitychange', function(){
    if(document.visibilityState === 'visible' && ma.code && !$('mafia').hidden){ ma.v = 0; startPoll(); }
  });

  /* ---- ?mafia=KOD linki ---- */
  try{
    var q = new URLSearchParams(location.search).get('mafia');
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
  window.wtJoinMafia = function(c){ enter(clean(c), false); };
  refreshResume();
})();
