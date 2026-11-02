
const $ = id => document.getElementById(id);
let myName = 'Mən', kind = null, curUrl = null;
let yt = null, ytReady = false, ytIsReady = false, ytPending = null;
let ignoreUntil = 0, lastT = null, hls = null;
let wantPlay = false, lastRemote = {time: 0, at: 0};
let freshLoad = false;   // video serverdən yenicə açılıb: öz vaxtımızı (0) qarşı tərəfə göndərmə
let room = '', me = Math.random().toString(36).slice(2, 10);
/* Gizli cihaz açarı: açıq id (me) poll ilə hamıya göndərilir, ona görə kimlik yalnız bu açarla təsdiqlənir */
let sk = (function(){
  try{ const a = new Uint8Array(16); crypto.getRandomValues(a); return Array.from(a, b => b.toString(16).padStart(2, '0')).join(''); }
  catch(e){ return (Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2) + Date.now().toString(36) + '0123456789abcdef').slice(0, 32); }
})();
(function(){
  const of = window.fetch.bind(window);
  window.fetch = function(u, o){
    try{
      const s = typeof u === 'string' ? u : ((u && u.url) || '');
      if(s.indexOf('/api/') === 0 || s.indexOf(location.origin + '/api/') === 0){
        o = Object.assign({}, o);
        const hd = new Headers(o.headers || (u && u.headers) || {});
        hd.set('X-Sk', sk);
        o.headers = hd;
      }
    }catch(e){}
    return of(u, o);
  };
})();
let since = 0, pc = 0, connected = false, offline = false, sendQ = Promise.resolve();
let isLocked = false, roomEpoch = null, leader = false, remoteSt = null, navAt = 0, lastAt = 0;
const hist = [];
let myRole = 'viewer', hostOnly = false, rv = -1, roleMap = {};
const CTRL_T = ['play', 'pause', 'seek', 'sync', 'load', 'wv', 'q'];
const canCtl = () => !hostOnly || myRole !== 'viewer';
const vid = $('vid'), web = $('web');
function mute(ms){ ignoreUntil = Math.max(ignoreUntil, Date.now() + ms); }
function ignoring(){ return Date.now() < ignoreUntil; }

if(window.__ytReady || (window.YT && YT.Player)) ytReady = true;
window.onYouTubeIframeAPIReady = () => { ytReady = true; };

function setStatus(t){ $('status').textContent = t; $('status').dataset.on = connected ? '1' : '0'; }
function sysMsg(t){
  const d = document.createElement('div'); d.className = 'sys'; d.textContent = t;
  $('chat').appendChild(d); $('chat').scrollTop = 1e9;
}

/* ---------- Server vasitəsilə əlaqə ---------- */
function send(o){
  if(!room) return;
  if(CTRL_T.includes(o.t) && !canCtl()) return;   // yalnız host idarə edir rejimi
  sendQ = sendQ.then(() => fetch('/api/send', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Token': token || ''},
    body: JSON.stringify({room, id: me, msg: o})
  })).catch(() => {});
}

function refreshStatus(){
  setStatus(connected ? 'Birlikdəsiniz' : 'Sevgilini gözləyirəm...');
}

let peerSet = new Map(), peerList = [];
function peerNm(){ return peerName || (peerList[0] && peerList[0].name) || 'Qarşı tərəf'; }
const GENERIC_NM = /^(qonaq|guest|mən|qarşı tərəf)?$/i;
const learnedNm = new Map();   // peer id -> yazışmadan öyrənilən real ad
function peerLabel(w){ return w && w.name ? String(w.name).slice(0, 20) : 'Qarşı tərəf'; }
function learnPeerName(name){
  name = String(name || '').slice(0, 20);
  if(!name || GENERIC_NM.test(name) || peerList.length !== 1) return;
  learnedNm.set(peerList[0].id, name); peerList[0].name = name;
  if(peerSet.has(peerList[0].id)) peerSet.get(peerList[0].id).name = name;
}
function announcePeers(who){
  if(!Array.isArray(who)) return;
  const list = who.filter(w => w && typeof w.id === 'string');
  list.forEach(w => { if(GENERIC_NM.test(String(w.name || '')) && learnedNm.has(w.id)) w.name = learnedNm.get(w.id); });
  const cur = new Map(list.map(w => [w.id, w]));
  cur.forEach((w, id) => {
    if(peerSet.has(id)) return;
    if(!GENERIC_NM.test(String(w.name || ''))){ sysMsg(peerLabel(w) + ' qoşuldu'); return; }
    setTimeout(() => {   // ad yazışmadan öyrənilsin deyə qısa gözləmə
      const nm = learnedNm.get(id) || (peerSet.get(id) && peerSet.get(id).name) || w.name;
      if(peerSet.has(id)) sysMsg((GENERIC_NM.test(String(nm || '')) ? 'Qarşı tərəf' : String(nm).slice(0, 20)) + ' qoşuldu');
    }, 1500);
  });
  peerSet.forEach((w, id) => { if(!cur.has(id)) sysMsg(peerLabel(w) + ' çıxdı'); });
  peerSet = cur; peerList = list;
}

function updatePeers(n){
  pc = n;
  if(n > 0 && !connected){
    connected = true;
    setStatus('Birlikdəsiniz');
    if(curUrl) send({t:'load', url: curUrl, hello: true, at: navAt});
    if(queue.length) send({t:'q', list: queue});
    if(curUrl && (kind === 'file' || kind === 'yt')) setTimeout(() => {   // qayıdan şəxs videonu cari yerdən davam etsin
      if(!connected || (kind !== 'file' && kind !== 'yt')) return;
      if(freshLoad || (kind === 'file' && vid.readyState < 1)){ freshLoad = false; return; }   // yenicə qoşulan tərəf yalnız qəbul edir, qarşı tərəfi başa atmır
      const t = getTime();
      send(isPlaying() ? {t: 'sync', time: t, playing: true} : {t: 'seek', time: t, playing: false});
    }, 2500);
    if(voiceOn) voiceHello(false);
  } else if(n === 0 && connected){
    connected = false; clearTyping(false);
    setStatus('Sevgilini gözləyirəm...');
    peerVoice = false; closeRtc(); setVoiceUI();
  }
}

/* otaqda profil şəkli (qarşı tərəfin göndərdiyi ad/versiya yoxlanılır) */
const validAv = (u, av) => /^[a-z0-9._]{3,20}$/.test(String(u || '')) && /^\d{1,12}$/.test(String(av || '')) && +av > 0;
const avUrl = (u, av) => '/api/avatar/' + u + '?v=' + av;
function myAvVer(){ const m = account && account.avatar && String(account.avatar).match(/[?&]v=(\d+)/); return m ? +m[1] : 0; }
function avEl(u, av, name){
  const e = document.createElement('i'); e.className = 'mav';
  if(validAv(u, av)) e.style.backgroundImage = 'url("' + avUrl(u, av) + '")'; else e.textContent = initial(name);
  return e;
}
function setVp(box, span, u, av, name){
  if(validAv(u, av)){ box.style.backgroundImage = 'url("' + avUrl(u, av) + '")'; span.textContent = ''; }
  else { box.style.backgroundImage = ''; span.textContent = initial(name); }
}

function handle(m){
  if(m.t === 'typing'){ learnPeerName(m.name); showTyping(m.name); }
  else if(m.t === 'fly'){ if(REACTS.includes(m.e)) flyEmoji(m.e); }
  else if(m.t === 'react'){ if(typeof m.from === 'string' && m.from) rxApply(m.id, m.from, MSG_REACTS.includes(m.e) ? m.e : ''); }
  else if(m.t === 'chat'){
    if(m.id && msgEls.has(m.id)) return;
    clearTyping(); bumpUnread(); learnPeerName(m.name);
    if(validAv(m.u, m.av)){ peerU = m.u; peerAv = +m.av; }
    const el = addMsg(m.name, typeof m.text === 'string' ? m.text : '', false, {id: m.id, reply: m.reply, u: m.u, av: m.av, img: m.img, sens: m.sens, vf: m.vf === 1, rl: m.rl});
    if(m.id){ send({t:'dlv', ids:[m.id]}); watchRead(el, m.id); }
  }
  else if(m.t === 'dlv' || m.t === 'read'){
    (Array.isArray(m.ids) ? m.ids : []).forEach(id => setReceipt(id, m.t === 'read' ? 3 : 2));
  }
  else if(m.t === 'voice' || m.t === 'vmute' || m.t === 'rtc') voiceMsg(m);
  else if(m.t === 'q'){ qSet(m.list, false); }
  else if(m.t === 'load' && typeof m.url === 'string' && m.url){
    if(m.hello && (m.url === curUrl || (m.at || 0) <= navAt)) return;
    if(m.qn && m.url === curUrl) return;   // növbədən keçidi hər iki tərəf eyni anda özü etdi
    loadPage(m.url, true);
  }
  else if(m.t === 'uploading' && typeof m.name === 'string'){
    sysMsg('Sevgilin video yükləyir: ' + m.name.slice(0, 80) + '. Hazır olanda avtomatik açılacaq.');
  }
  else if(m.t === 'wv'){
    if(kind === 'web' && web.contentWindow) web.contentWindow.postMessage({t:'cmd', e:m.e, time:m.time}, '*');
  }
  else applyRemote(m);
}

const ICON_COPY = '<i class="fa-solid fa-copy" aria-hidden="true"></i>', ICON_CHECK = '<i class="fa-solid fa-check" aria-hidden="true"></i>', ICON_OPEN = '<i class="fa-solid fa-lock-open" aria-hidden="true"></i>', ICON_LOCK = '<i class="fa-solid fa-lock" aria-hidden="true"></i>';
const ICON_SHARE = '<i class="fa-solid fa-arrow-up-from-bracket" aria-hidden="true"></i>';
function updateLockUI(){
  $('lock').innerHTML = isLocked ? ICON_LOCK : ICON_OPEN;
  const t = isLocked ? 'Otaq kilidlidir, açmaq üçün bas' : 'Otağı kilidlə';
  $('lock').setAttribute('aria-label', t); $('lock').title = t;
}
function applyLock(v){
  if(v === isLocked) return;
  isLocked = v; updateLockUI();
  sysMsg(v ? 'Otaq kilidləndi, yeni nəfər girə bilməz' : 'Otaq açıldı');
}
async function lockCall(v){
  try{
    const r = await fetch('/api/lock', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({room, id: me, locked: v})
    });
    if(!r.ok) throw new Error('lock ' + r.status);
    applyLock(!!(await r.json()).locked);
  }catch(e){ sysMsg('Kilid dəyişmədi, yenidən cəhd et'); }
}

let pollGen = 0, pollAbort = null;
async function pollLoop(){
  const gen = ++pollGen;
  while(gen === pollGen){
    const ac = new AbortController(); pollAbort = ac;
    const to = setTimeout(() => ac.abort(), 35000); // server 20 san gözləyir; ondan uzun asılıbsa kəs
    try{
      const r = await fetch('/api/poll?room=' + encodeURIComponent(room) + '&id=' + me +
        '&since=' + since + '&pc=' + pc + '&rv=' + rv + '&lk=' + (isLocked ? 1 : 0) + '&nm=' + encodeURIComponent(String(myName || '').slice(0, 20)),
        {cache: 'no-store', signal: ac.signal, headers: {'X-Token': token || ''}});
      if(r.status === 403){
        setStatus('Otaq kilidlidir, bu cihaz üzv deyil');
        sysMsg('Otaq kilidlənib, bu cihaz içəri buraxılmadı');
        return;
      }
      if(!r.ok) throw new Error('http ' + r.status);
      const d = await r.json();
      if(gen !== pollGen) return;
      since = d.seq;
      applyRoles(d);
      if(typeof d.video === 'string' && !curUrl && /^\/vf\/[a-z0-9-]+\/[0-9a-f]{16}\.(mp4|webm|mov|ogg)$/.test(d.video)) loadServerVideo(d.video, d.vpos);
      if(offline){ offline = false; refreshStatus(); }
      if(roomEpoch !== null && d.epoch !== roomEpoch){
        roomEpoch = d.epoch;               // server yenidən başlayıb, kilid itib: bərpa et
        if(isLocked) lockCall(true);
      } else {
        roomEpoch = d.epoch;
        applyLock(!!d.locked);
      }
      (d.msgs || []).forEach(handle);
      announcePeers(d.who);
      updatePeers(d.peers);
    }catch(e){
      if(gen !== pollGen) return;
      // connected/pc sıfırlanmır: əks halda hər qısa kəsilmədə səhifə qarşı tərəfdə yenidən yüklənir
      offline = true;
      setStatus('Əlaqə problemi, yenidən cəhd edilir...');
      // kiçik jitter: bütün klientlər eyni anda vurmasın (thundering herd azalır)
      await new Promise(res => setTimeout(res, 1500 + Math.floor(Math.random() * 1500)));
    }finally{
      clearTimeout(to);
    }
  }
}


/* ---------- Bilet cırılır ---------- */
function tearTicket(){
  const j = $('join'), tk = j.querySelector('.ticket'), top = j.querySelector('.t-main'), bot = j.querySelector('.t-stub');
  const reduce = (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches) || document.body.classList.contains('lite');
  if(reduce || !tk || !bot || !bot.animate){ j.style.display = 'none'; return; }
  if(!document.body.classList.contains('mob')){   // kompüter: ağır cırılma əvəzinə qısa solma (donmanın qarşısı)
    j.style.transition = 'opacity .22s ease'; j.style.opacity = '0';
    setTimeout(() => { j.style.display = 'none'; j.style.opacity = ''; j.style.transition = ''; }, 240);
    return;
  }
  if(document.activeElement && document.activeElement.blur) document.activeElement.blur();
  const seam = top.offsetHeight, w = tk.offsetWidth;
  j.classList.add('tearing');
  const scraps = [];
  for(let i = 0; i < 16; i++){
    const p = document.createElement('i'); p.className = 'scrap';
    p.style.cssText = 'left:' + (8 + Math.random() * (w - 16)) + 'px;top:' + (seam - 2) + 'px;width:' + (4 + Math.random() * 8) + 'px;height:' + (3 + Math.random() * 5) + 'px';
    tk.appendChild(p); scraps.push(p);
    p.animate([
      {transform: 'translate(0,0) rotate(0deg)', opacity: 1},
      {transform: 'translate(' + ((Math.random() - .5) * 150) + 'px,' + (80 + Math.random() * 260) + 'px) rotate(' + ((Math.random() - .5) * 600) + 'deg)', opacity: 0}
    ], {duration: 650 + Math.random() * 500, delay: 80 + Math.random() * 140, easing: 'cubic-bezier(.25,.6,.4,1)', fill: 'both'});
  }
  try{ if(navigator.vibrate) navigator.vibrate([18, 30, 12]); }catch(e){}
  let done = false;
  const fin = () => {
    if(done) return; done = true;
    j.style.display = 'none'; j.classList.remove('tearing');
    scraps.forEach(p => p.remove());
  };
  bot.addEventListener('animationend', fin, {once: true});
  setTimeout(fin, 1500);
}

/* ---------- Pərdə açılır (otağa qoşulanda, kinoteatr kimi) ---------- */
function tearMenuTicket(){
  const reduce = (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches) || document.body.classList.contains('lite');
  if(reduce || !document.body.classList.contains('mob') || !document.body.animate || $('menu').style.display === 'none' || $('join').style.display !== 'none') return;
  const box = document.createElement('div'); box.className = 'cfx'; box.setAttribute('aria-hidden', 'true');
  box.innerHTML = '<i class="cl"></i><i class="cr"></i><i class="cv"></i>';
  document.body.appendChild(box);
  const ease = 'cubic-bezier(.6,.05,.25,1)', dur = 1500, delay = 350;
  box.querySelector('.cl').animate([{transform: 'translateX(0)'}, {transform: 'translateX(-102%)'}], {duration: dur, delay, easing: ease, fill: 'both'});
  box.querySelector('.cr').animate([{transform: 'translateX(0)'}, {transform: 'translateX(102%)'}], {duration: dur, delay, easing: ease, fill: 'both'});
  box.querySelector('.cv').animate([{transform: 'translateY(0)', opacity: 1}, {transform: 'translateY(0)', opacity: 1, offset: .7}, {transform: 'translateY(-100%)', opacity: 0}], {duration: delay + dur + 500, easing: 'ease-in', fill: 'both'});
  try{ if(navigator.vibrate) navigator.vibrate([18, 30, 12]); }catch(e){}
  setTimeout(() => box.remove(), delay + dur + 700);
}

/* ---------- Otaq aç / kodla gir ---------- */
function joinMsg(t){ $('menumsg').textContent = t; }
let joinBusy = false;
function codeVal(){ return $('codein').value.trim().toLowerCase().replace(/[^a-z0-9-]/g, ''); }
function syncJoin(){   /* kod yazılıbsa "Otaq aç" solğunlaşır */
  const has = !!codeVal();
  $('create').classList.toggle('dim', has);
  $('create').setAttribute('aria-disabled', has ? 'true' : 'false');
}
function lockJoin(v){ joinBusy = v; $('create').disabled = v; $('enter').disabled = v; if(!v) syncJoin(); }

async function enterRoom(code, create){
  myName = (account && account.name) || (function(){ try{ return localStorage.getItem('wt_guest_name') || 'Qonaq'; }catch(e){ return 'Qonaq'; } })();
  lockJoin(true);
  loadIce();
  joinMsg('Qoşulur... ilk dəfə 30-50 saniyə çəkə bilər');
  let joinVideo = null, joinVpos = null;
  while(true){
    try{
      if(create){
        const rc = await fetch('/api/create', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: me})});
        if(!rc.ok) throw new Error('create ' + rc.status);
        code = (await rc.json()).room;
        create = false;
      }
      try{ const sv = localStorage.getItem('wt_me_' + code), ss = localStorage.getItem('wt_sk_' + code); if(sv && ss){ me = sv; sk = ss; } }catch(e){}
      const r = await fetch('/api/join', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({room: code, id: me})
      });
      if(r.status === 404){
        joinMsg('Belə otaq tapılmadı. Kodu yoxla.');
        lockJoin(false);
        return;
      }
      if(r.status === 403){
        joinMsg('Otaq kilidlidir, girmək olmur.');
        lockJoin(false);
        return;
      }
      if(!r.ok) throw new Error('join ' + r.status);
      const d = await r.json();
      since = d.seq;
      roomEpoch = d.epoch;
      isLocked = !!d.locked;
      myRole = d.role || 'viewer'; hostOnly = !!d.hostonly; rv = typeof d.rv === 'number' ? d.rv : -1; roleMap = {};
      joinVideo = typeof d.video === 'string' ? d.video : null; joinVpos = d.vpos || null;
      room = code;
      try{ localStorage.setItem('wt_me_' + code, me); localStorage.setItem('wt_sk_' + code, sk); }catch(e){}
      try{ history.replaceState(null, '', location.pathname); }catch(e){}  // ?kod= linkdən təmizlə
      break;
    }catch(e){
      await new Promise(res => setTimeout(res, 2500));
    }
  }
  joinMsg(''); track({kind: 'room', room});
  tearMenuTicket();                                  // bilet cırılır, altında otaq açılır
  $('menu').style.display = 'none';
  $('app').style.display = 'flex';
  $('code').textContent = room.toUpperCase();
  updateLockUI();
  showHome();
  setStatus('Sevgilini gözləyirəm...');
  if(joinVideo && /^\/vf\/[a-z0-9-]+\/[0-9a-f]{16}\.(mp4|webm|mov|ogg)$/.test(joinVideo)) loadServerVideo(joinVideo, joinVpos);
  await wait(loadChatHistory(), 4000);
  pollLoop();
}

/* ---------- Link / axtarış ---------- */
function ytId(u){
  let x;
  try{ x = new URL(String(u)); }catch(e){ return null; }
  const h = x.hostname.toLowerCase().replace(/^(www|m)\./, '');
  let id = null;
  if(h === 'youtu.be') id = x.pathname.split('/')[1];
  else if(h === 'youtube.com' || h === 'youtube-nocookie.com'){
    const p = x.pathname.split('/');
    if(x.pathname === '/watch') id = x.searchParams.get('v');
    else if(['embed', 'shorts', 'live', 'v'].includes(p[1])) id = p[2];
  }
  return id && /^[\w-]{11}$/.test(id) ? id : null;
}
function norm(s){
  s = s.trim();
  if(!s) return null;
  if(/^https?:\/\//i.test(s)) return s;
  if(!/\s/.test(s) && /\.[a-z]{2,}(:\d+)?([\/?#]|$)/i.test(s)) return 'https://' + s;
  return null;   // adi söz üçün Google axtarışı yoxdur
}
function ytSearchFromUrl(raw){
  if(!/youtu\.?be|youtube\.com/i.test(raw)) return false;
  if(ytId(raw)) return false;
  let u;
  try{ u = new URL(/^https?:\/\//i.test(raw) ? raw : 'https://' + raw); }catch(e){ return false; }
  if(!/(^|\.)youtube\.com$/i.test(u.hostname)) return false;
  if(u.pathname === '/results'){ ytSearch(u.searchParams.get('search_query') || ''); return true; }
  // YouTube-un öz saytı (ana səhifə, kanal, pleylist...) açılmır: əvəzinə öz axtarış səhifəmiz açılır
  ytOpen();
  if(u.pathname !== '/' && u.pathname !== '') sysMsg('YouTube-un öz səhifəsi açılmır, videonu burada axtar');
  return true;
}

/* ---------- Rejim ---------- */
function showMode(m){
  mute(700);
  lastT = null; remoteSt = null; leader = false;
  wantPlay = false;
  $('tap').style.display = 'none';
  if(m !== 'yt'){ try{ yt && yt.pauseVideo && ytIsReady && yt.pauseVideo(); }catch(e){} }
  if(m !== 'file'){
    vid.pause();
    if(hls){ hls.destroy(); hls = null; }
    vid.removeAttribute('src'); vid.load();
  }
  if(m !== 'web') web.src = 'about:blank';
  $('ytbox').style.display = m === 'yt' ? 'block' : 'none';
  vid.style.display = m === 'file' ? 'block' : 'none';
  web.style.display = m === 'web' ? 'block' : 'none';
  $('ytres').style.display = m === 'ytres' ? 'block' : 'none';
  $('home').style.display = m === 'home' ? 'block' : 'none';
  kind = m;
  updateRoleUI();
}
function setFile(url){
  if(hls){ hls.destroy(); hls = null; }
  if(/\.m3u8/i.test(url) && !vid.canPlayType('application/vnd.apple.mpegurl') && window.Hls && Hls.isSupported()){
    hls = new Hls(); hls.loadSource(url); hls.attachMedia(vid);
  } else {
    vid.src = url;
  }
}

/* ---------- Telefondan video: serverə yüklənir, qarşı tərəfdə avtomatik açılır ---------- */
let upXhr = null;
function uploadLocal(f){
  if(!f) return;
  if(!room){ sysMsg('Əvvəlcə otağa qoşul'); return; }
  if(!canCtl()){ sysMsg('Yalnız host və moderatorlar video yükləyə bilər'); return; }
  if(upXhr){ sysMsg('Başqa video hələ yüklənir'); return; }
  const b = $('pickfile'), xhr = new XMLHttpRequest();
  upXhr = xhr;
  xhr.open('POST', '/api/upload?room=' + encodeURIComponent(room) + '&id=' + encodeURIComponent(me) + '&fn=' + encodeURIComponent(f.name));
  xhr.setRequestHeader('X-Sk', sk);
  xhr.setRequestHeader('Content-Type', 'application/octet-stream');
  b.textContent = 'Yüklənir… 0%'; b.style.display = 'block';
  send({t: 'uploading', name: String(f.name).slice(0, 80)});
  xhr.upload.onprogress = e => {
    if(e.lengthComputable) b.textContent = 'Yüklənir… ' + Math.round(e.loaded / e.total * 100) + '%';
  };
  xhr.upload.onload = () => { b.textContent = 'Serverdə saxlanır…'; };
  const done = () => { upXhr = null; b.style.display = 'none'; };
  xhr.onload = () => {
    done();
    if(xhr.status === 200){
      try{ loadPage(location.origin + JSON.parse(xhr.responseText).url, false); }
      catch(e){ sysMsg('Yükləmə alınmadı'); }
    } else if(xhr.status === 413) sysMsg('Fayl çox böyükdür');
    else if(xhr.status === 429) sysMsg('Çox yükləmə etdin, bir az sonra yenidən yoxla');
    else sysMsg('Yükləmə alınmadı');
  };
  xhr.onerror = () => { done(); sysMsg('Yükləmə alınmadı (bağlantı kəsildi)'); };
  xhr.send(f);
}
vid.addEventListener('error', () => {
  if(curUrl && curUrl.indexOf('/vf/') > -1) sysMsg('Bu video formatı brauzerdə oynamır. mp4 (H.264) formatında yüklə');
});
$('lfile').addEventListener('change', e => { const f = e.target.files && e.target.files[0]; e.target.value = ''; uploadLocal(f); });


/* ---------- Video növbəsi: biri bitəndə növbəti avtomatik açılır, hər iki tərəfdə eyni siyahı ---------- */
const Q_MAX = 12;
let queue = [], qAt = 0;
function qClean(list){
  const out = [];
  (Array.isArray(list) ? list : []).forEach(x => {
    if(!x || typeof x.u !== 'string' || !/^https?:\/\//i.test(x.u) || x.u.length > 200 || out.length >= Q_MAX) return;
    out.push({u: x.u, t: typeof x.t === 'string' ? x.t.slice(0, 60) : ''});
  });
  return out;
}
function qSet(list, push){
  queue = qClean(list);
  qRender();
  if(push) send({t: 'q', list: queue});
}
function qAdd(url, title){
  if(!room){ sysMsg('Əvvəlcə otağa qoşul'); return false; }
  if(!canCtl()){ sysMsg('Yalnız host və moderatorlar növbəyə əlavə edə bilər'); return false; }
  if(queue.length >= Q_MAX){ sysMsg('Növbə doludur (' + Q_MAX + ' video)'); return false; }
  url = String(url || '').trim();
  if(!/^https?:\/\//i.test(url)) url = 'https://' + url;
  if(url.length > 200){ sysMsg('Link çox uzundur'); return false; }
  let host = '';
  try{ host = new URL(url).hostname.replace(/^www\./, ''); }catch(e){ sysMsg('Link düzgün deyil'); return false; }
  qSet(queue.concat([{u: url, t: title || host}]), true);
  return true;
}
function qThumb(u){ const id = ytId(u); return id && YT_ID_RE.test(id) ? 'https://i.ytimg.com/vi/' + id + '/mqdefault.jpg' : ''; }
function qRender(){
  const n = queue.length, bd = $('qbtn').querySelector('b');
  bd.textContent = String(n); bd.hidden = !n;
  const list = $('qlist'); list.textContent = '';
  if(!n){ const e = document.createElement('div'); e.className = 'qe'; e.textContent = 'Növbə boşdur'; list.appendChild(e); }
  queue.forEach((it, i) => {
    const row = document.createElement('div'); row.className = 'qi';
    const th = qThumb(it.u);
    if(th){ const im = document.createElement('img'); im.src = th; im.alt = ''; im.loading = 'lazy'; row.appendChild(im); }
    else { const ph = document.createElement('span'); ph.className = 'qph'; ph.innerHTML = '<i class="fa-solid fa-film" aria-hidden="true"></i>'; row.appendChild(ph); }
    const t = document.createElement('div'); t.className = 'qt'; t.dataset.nt = ''; t.textContent = it.t || it.u;
    t.onclick = () => qPlayAt(i);   // adına toxun: indi aç
    row.appendChild(t);
    const rm = document.createElement('button'); rm.type = 'button'; rm.setAttribute('aria-label', 'Sil');
    rm.innerHTML = '<i class="fa-solid fa-xmark" aria-hidden="true"></i>';
    rm.onclick = () => { if(!canCtl()){ sysMsg('Yalnız host və moderatorlar növbəni dəyişə bilər'); return; } qSet(queue.filter((_, j) => j !== i), true); };
    row.appendChild(rm);
    list.appendChild(row);
  });
  $('qskip').disabled = !n; $('qskip').style.opacity = n ? '' : '.45';
}
function qPlayAt(i){
  if(!canCtl()){ sysMsg('Yalnız host və moderatorlar video seçə bilər'); return; }
  const it = queue[i]; if(!it) return;
  qSet(queue.filter((_, j) => j !== i), true);
  $('qbox').hidden = true;
  loadPage(it.u, false);
}
function qAdvance(){
  if(!queue.length) return;
  const now = Date.now();
  if(now - qAt < 4000) return;   // "ended" hadisəsi iki dəfə gələ bilər
  qAt = now;
  const it = queue[0];
  queue = queue.slice(1); qRender();
  if(canCtl()){ send({t: 'q', list: queue}); send({t: 'load', url: it.u, qn: 1}); }
  sysMsg('Növbədəki video açılır');
  loadPage(it.u, true);
}
$('qbtn').onclick = () => { qRender(); $('qbox').hidden = false; };
$('qclose').onclick = () => { $('qbox').hidden = true; };
$('qbox').addEventListener('click', e => { if(e.target === $('qbox')) $('qbox').hidden = true; });
$('qskip').onclick = () => {
  if(!canCtl()){ sysMsg('Yalnız host və moderatorlar növbəni dəyişə bilər'); return; }
  qAt = 0; $('qbox').hidden = true; qAdvance();
};
function qAddFromInput(){
  const v = $('qurl').value.trim(); if(!v) return;
  if(qAdd(v, '')) $('qurl').value = '';
}
$('qadd').onclick = qAddFromInput;
$('qurl').addEventListener('keydown', e => { if(e.key === 'Enter'){ e.preventDefault(); qAddFromInput(); } });
qRender();

/* ---------- Başlanğıc səhifə ---------- */
const TILES = [
  {i: '<i class="fa-brands fa-youtube" aria-hidden="true"></i>', t: 'YouTube axtarışı', f: () => ytOpen()},
  {i: '<i class="fa-solid fa-film" aria-hidden="true"></i>', t: 'Telefondan video', f: () => $('lfile').click()}
];
function showHome(){
  showMode('home');
  const box = $('home');
  box.textContent = '';
  const hd = document.createElement('div'); hd.className = 'hhead';
  const ht = document.createElement('b'); ht.textContent = 'Birlikdə nə izləyək?';
  const hs = document.createElement('span'); hs.textContent = 'Birini seç və ya aşağıdakı xanaya yaz';
  hd.appendChild(ht); hd.appendChild(hs); box.appendChild(hd);
  const g = document.createElement('div'); g.className = 'tiles';
  TILES.forEach(x => {
    const el = document.createElement('div'); el.className = 'htile';
    const ic = document.createElement('span'); ic.innerHTML = x.i;
    el.appendChild(ic); el.appendChild(document.createTextNode(x.t));
    el.onclick = () => { if(x.f) x.f(); else loadPage(x.u, false); };
    g.appendChild(el);
  });
  box.appendChild(g);
  const tips = document.createElement('div'); tips.className = 'tips';
  [
    ['fa-link', 'Sayt linki', 'yaz, həmin sayta girir'],
    ['fa-magnifying-glass', 'YouTube axtarışı', 'düyməyə bas, sözü yaz, çıxan videolardan birini seç'],
    ['fa-mobile-screen', 'Telefondan video', 'bir nəfər yükləyir, ikiniz birlikdə izləyirsiniz']
  ].forEach(t => {
    const row = document.createElement('div'); row.className = 'tip';
    const ic = document.createElement('i'); ic.className = 'fa-solid ' + t[0]; ic.setAttribute('aria-hidden', 'true');
    const tx = document.createElement('div');
    const st = document.createElement('strong'); st.textContent = t[1];
    tx.appendChild(st); tx.appendChild(document.createTextNode(t[2]));
    row.appendChild(ic); row.appendChild(tx); tips.appendChild(row);
  });
  box.appendChild(tips);
}

/* YouTube pleyeri: hazır olmamış gələn video növbəyə qoyulur */
function ytLoad(id){
  if(yt && ytIsReady){
    yt.loadVideoById(id);
  } else if(yt){
    ytPending = id;
  } else {
    yt = new YT.Player('yt', {
      videoId: id,
      playerVars: { playsinline: 1, rel: 0 },
      events: {
        onReady: () => {
          ytIsReady = true;
          if(ytPending){ yt.loadVideoById(ytPending); ytPending = null; }
        },
        onStateChange: ytState
      }
    });
  }
}

/* ---------- YouTube axtarışı: öz səhifəmizdə (YouTube-un öz saytı açılmır) ---------- */
const YT_REC_KEY = 'wt_ytrec';
const YT_ID_RE = /^[\w-]{11}$/;
function ytRecent(){
  try{ const a = JSON.parse(localStorage.getItem(YT_REC_KEY) || '[]'); return Array.isArray(a) ? a.filter(x => typeof x === 'string' && x).slice(0, 8) : []; }
  catch(e){ return []; }
}
function ytRecentAdd(q){
  try{
    const a = ytRecent().filter(x => x.toLowerCase() !== q.toLowerCase()); a.unshift(q.slice(0, 60));
    localStorage.setItem(YT_REC_KEY, JSON.stringify(a.slice(0, 8)));
  }catch(e){}
}
let ytSeq = 0, ytCache = null;
function ytOpen(){   // "YouTube axtarışı" düyməsi
  if(ytCache && ytCache.d.length){
    ++ytSeq; showMode('ytres'); $('ytq').value = ytCache.q; ytRender(ytCache.d); $('ytres').scrollTop = 0;
  } else {
    ytSearch(''); $('ytq').focus();
  }
}
function ytEmpty(){
  const list = $('ytlist'); list.className = ''; list.textContent = '';
  const hero = document.createElement('div'); hero.className = 'yhero';
  const ic = document.createElement('span'); ic.innerHTML = '<i class="fa-brands fa-youtube" aria-hidden="true"></i>';
  const t = document.createElement('b'); t.textContent = 'Birlikdə izləyəcəyin videonu tap';
  const p = document.createElement('p'); p.textContent = 'Sözü yaz, çıxan videolardan birini seç. İkiniz də eyni anda izləyəcəksiniz.';
  hero.appendChild(ic); hero.appendChild(t); hero.appendChild(p); list.appendChild(hero);
  const rec = ytRecent();
  if(!rec.length) return;
  const lab = document.createElement('div'); lab.className = 'ylab';
  const lt = document.createElement('span'); lt.textContent = 'Son axtarışlar';
  const clr = document.createElement('button'); clr.type = 'button'; clr.textContent = 'Təmizlə';
  clr.onclick = () => { try{ localStorage.removeItem(YT_REC_KEY); }catch(e){} ytEmpty(); };
  lab.appendChild(lt); lab.appendChild(clr); list.appendChild(lab);
  const row = document.createElement('div'); row.className = 'yrec';
  rec.forEach(q => {
    const b = document.createElement('button'); b.type = 'button'; b.textContent = q;
    b.onclick = () => ytSearch(q); row.appendChild(b);
  });
  list.appendChild(row);
}
function ytRender(d){
  const list = $('ytlist'); list.className = ''; list.textContent = '';
  d.forEach(v => {
    if(!v || !YT_ID_RE.test(String(v.id))) return;
    const el = document.createElement('div'); el.className = 'r'; el.tabIndex = 0; el.setAttribute('role', 'button');
    const th = document.createElement('span'); th.className = 'rt';
    const img = document.createElement('img'); img.loading = 'lazy'; img.alt = '';
    if(/^https:\/\/i\.ytimg\.com\//.test(String(v.thumb))) img.src = v.thumb;
    th.appendChild(img);
    if(v.len){ const du = document.createElement('em'); du.className = 'dur'; du.textContent = String(v.len); th.appendChild(du); }
    const t = document.createElement('div'); t.dataset.nt = ''; t.textContent = String(v.title || '');
    const s = document.createElement('small'); s.textContent = String(v.channel || '');
    t.appendChild(s);
    el.appendChild(th); el.appendChild(t);
    const play = () => loadPage('https://www.youtube.com/watch?v=' + v.id, false);
    const qa = document.createElement('button'); qa.type = 'button'; qa.className = 'qadd';
    qa.innerHTML = '<i class="fa-solid fa-plus" aria-hidden="true"></i>';
    qa.setAttribute('aria-label', 'Növbəyə əlavə et');
    qa.onclick = ev => {
      ev.stopPropagation();
      if(qAdd('https://www.youtube.com/watch?v=' + v.id, String(v.title || ''))){ qa.classList.add('on'); qa.innerHTML = '<i class="fa-solid fa-check" aria-hidden="true"></i>'; setTimeout(() => { qa.classList.remove('on'); qa.innerHTML = '<i class="fa-solid fa-plus" aria-hidden="true"></i>'; }, 1400); }
    };
    el.appendChild(qa);
    el.onclick = play;
    el.onkeydown = e => { if(e.key === 'Enter' || e.key === ' '){ e.preventDefault(); play(); } };
    list.appendChild(el);
  });
}
async function ytSearch(q){
  const my = ++ytSeq;
  q = String(q || '').trim();
  showMode('ytres');
  $('ytres').scrollTop = 0;
  $('ytq').value = q;
  const list = $('ytlist');
  if(!q){ ytEmpty(); return; }
  ytRecentAdd(q);
  list.className = 'hint'; list.textContent = 'Axtarılır...';
  try{
    const r = await fetch('/api/yt?q=' + encodeURIComponent(q));
    const d = await r.json();
    if(my !== ytSeq) return;
    if(!r.ok) throw new Error('yt ' + r.status);
    if(!Array.isArray(d) || !d.length){ list.className = 'hint'; list.textContent = 'Nəticə tapılmadı'; return; }
    ytCache = {q, d};
    ytRender(d);
  }catch(e){
    if(my !== ytSeq) return;
    list.className = 'hint';
    list.textContent = 'Axtarış alınmadı, bir az sonra yenidən yoxla';
  }
}
$('ytform').addEventListener('submit', e => {
  e.preventDefault();
  const q = $('ytq').value.trim();
  if(q){ ytSearch(q); $('ytq').blur(); }
});

let navSeq = 0;
let curFavUrl = '';
function loadPage(url, remote, tries = 0){
  if(/^https?:\/\//i.test(url) && url.indexOf('/vf/') < 0){ curFavUrl = url; try{ paintRoomFav(); }catch(e){} }
  if(typeof url !== 'string' || !url) return;
  if(!remote && !canCtl()){ sysMsg('Yalnız host və moderatorlar video seçə bilər'); return; }
  const my = tries ? navSeq : ++navSeq;
  const id = ytId(url);
  curUrl = url;
  navAt = Date.now();
  $('url').value = url;
  if(hist[hist.length - 1] !== url) hist.push(url);

  if(id){
    if(!ytReady){
      if(tries >= 20){ sysMsg('YouTube pleyeri yüklənmədi (bloklanmış ola bilər)'); return; }
      setTimeout(() => { if(my === navSeq) loadPage(url, remote, tries + 1); }, 500);
      return;
    }
    showMode('yt');
    ytLoad(id);
  } else if(/\.(mp4|webm|ogg|mov|m3u8)(\?|#|$)/i.test(url)){
    showMode('file');
    setFile(url);
  } else {
    showMode('web');
    web.src = '/p?u=' + encodeURIComponent(url);
  }
  if(!remote){ send({t:'load', url}); sysMsg('Səhifə açdın'); }
  else sysMsg('Səhifə dəyişdi');
  if(!/^https?:\/\/(www\.)?google\.[a-z.]+\/search/i.test(url) && url.indexOf('/vf/') < 0) track({kind: 'video', url});
}
/* Otağa girəndə serverdəki video: serverin yadda saxladığı vaxtdan davam et (0-dan yox) */
function loadServerVideo(url, vp){
  freshLoad = true;
  loadPage(location.origin + url, true);
  if(vp && typeof vp.time === 'number' && isFinite(vp.time)){   // loadPage → showMode vəziyyəti sıfırlayır, ona görə sonra təyin edirik
    const now = Date.now();
    wantPlay = !!vp.playing;
    lastRemote = {time: vp.time, at: now};
    remoteSt = {playing: !!vp.playing, time: vp.time, at: now};
  }
}
function goBack(){
  if(kind === 'ytres' || kind === 'home'){
    if(hist.length) loadPage(hist[hist.length - 1], false);
    else if(kind === 'ytres') showHome();
    return;
  }
  if(hist.length < 2){ showHome(); return; }
  hist.pop();
  loadPage(hist.pop(), false);
}

/* ---------- Video sinxron (YouTube / fayl) ---------- */
function getTime(){ try{ return kind === 'yt' ? yt.getCurrentTime() : vid.currentTime; }catch(e){ return 0; } }
function seekTo(t){ try{ kind === 'yt' ? yt.seekTo(t, true) : (vid.currentTime = t); }catch(e){} }
function doPlay(){ try{ kind === 'yt' ? yt.playVideo() : vid.play(); }catch(e){} }
function doPause(){ try{ kind === 'yt' ? yt.pauseVideo() : vid.pause(); }catch(e){} }

function isPlaying(){
  try{ return kind === 'yt' ? yt.getPlayerState() === 1 : !vid.paused; }catch(e){ return false; }
}
/* Brauzer avtomatik oynatmanı bloklayıbsa, toxunma düyməsi göstər */
function checkPlaying(){
  if(!wantPlay || (kind !== 'yt' && kind !== 'file')) return;
  if(!isPlaying()) $('tap').style.display = 'block';
}
$('tap').onclick = () => {
  $('tap').style.display = 'none';
  mute(900);
  const t = lastRemote.time + (Date.now() - lastRemote.at) / 1000;
  if(Math.abs(getTime() - t) > 1.5) seekTo(t);
  doPlay();
};

/* Qarşı tərəfdən gələn əmrin əks-sədasını (echo) geri göndərmirik: vaxt+vəziyyət uyğundursa */
function isEcho(playing, time){
  if(!remoteSt || Date.now() - remoteSt.at > 6000) return false;
  const exp = remoteSt.time + (remoteSt.playing ? (Date.now() - remoteSt.at) / 1000 : 0);
  return remoteSt.playing === playing && Math.abs(time - exp) < 1.5;
}
function sendLocal(o){ leader = true; freshLoad = false; send(o); }

function applyRemote(m){
  if(kind !== 'yt' && kind !== 'file') return;
  mute(600);
  leader = false;
  lastT = null;
  const now = Date.now();
  if(m.t === 'play'){
    wantPlay = true;
    lastRemote = {time: m.time, at: now};
    remoteSt = {playing: true, time: m.time, at: now};
    if(Math.abs(getTime() - m.time) > 0.8) seekTo(m.time);
    doPlay();
    setTimeout(checkPlaying, 1500);
  }
  else if(m.t === 'pause'){
    wantPlay = false;
    $('tap').style.display = 'none';
    remoteSt = {playing: false, time: m.time, at: now};
    doPause();
    if(Math.abs(getTime() - m.time) > 0.5) seekTo(m.time);
  }
  else if(m.t === 'seek'){
    const pl = typeof m.playing === 'boolean' ? m.playing : isPlaying();
    wantPlay = pl;
    lastRemote = {time: m.time, at: now};
    remoteSt = {playing: pl, time: m.time, at: now};
    seekTo(m.time);
  }
  else if(m.t === 'sync' && m.playing){
    wantPlay = true;
    lastRemote = {time: m.time, at: now};
    remoteSt = {playing: true, time: m.time, at: now};
    const buffering = kind === 'file' && (vid.seeking || vid.readyState < 3);   // yüklənən videoya təkrar seek etmə (donmanın səbəbi)
    if(!buffering && Math.abs(getTime() - m.time) > (kind === 'file' ? 3 : 2)) seekTo(m.time + 0.4);
    if(!isPlaying()){ doPlay(); setTimeout(checkPlaying, 1500); }
  }
}
function ytState(e){
  if(e.data === 1) $('tap').style.display = 'none';
  if(e.data === 0){ qAdvance(); return; }   // video bitdi: növbədəki açılsın
  if(ignoring()) return;
  if(e.data !== 1 && e.data !== 2) return;
  const t = yt.getCurrentTime(), pl = e.data === 1;
  if(isEcho(pl, t)) return;
  sendLocal({t: pl ? 'play' : 'pause', time: t});
}
vid.addEventListener('playing', () => { $('tap').style.display = 'none'; });
vid.addEventListener('ended', () => { if(kind === 'file') qAdvance(); });
vid.addEventListener('loadedmetadata', () => {   // video gec yüklənəndə qarşı tərəfin son vaxtına keç
  if(kind !== 'file' || !lastRemote.at || Date.now() - lastRemote.at > 90000) return;
  const t = lastRemote.time + (wantPlay ? (Date.now() - lastRemote.at) / 1000 : 0);
  mute(700);
  if(Math.abs(vid.currentTime - t) > 1) vid.currentTime = t;
  if(wantPlay){ const p = vid.play(); if(p && p.catch) p.catch(() => { $('tap').style.display = 'block'; }); }
});
['play', 'pause', 'seeked'].forEach(ev => vid.addEventListener(ev, () => {
  if(ignoring()) return;
  const pl = !vid.paused, t = vid.currentTime;
  if(isEcho(pl, t)) return;
  sendLocal(ev === 'seeked' ? {t: 'seek', time: t, playing: pl} : {t: ev, time: t});
}));

/* YouTube-da "seek" hadisəsi yoxdur: vaxtın gözlənilməz tullanmasını özümüz tuturuq (oynayanda da, pauzada da) */
setInterval(() => {
  if(kind !== 'yt' || !yt || !ytIsReady || !yt.getPlayerState) return;
  const st = yt.getPlayerState();
  if(st !== 1 && st !== 2){ lastT = null; return; }
  const t = yt.getCurrentTime(), now = Date.now();
  if(lastT !== null && !ignoring()){
    const rate = st === 1 ? (yt.getPlaybackRate ? yt.getPlaybackRate() : 1) : 0;
    const exp = lastT + (now - lastAt) / 1000 * rate;
    if(Math.abs(t - exp) > 1.2 && !isEcho(st === 1, t)) sendLocal({t: 'seek', time: t, playing: st === 1});
  }
  lastT = t; lastAt = now;
}, 500);

/* Aparıcı tərəf (son toxunan) hər 3 san vaxtı göndərir, digər tərəf 2 san-dən çox fərq olsa düzəldir */
setInterval(() => {
  if(!leader || !connected || ignoring() || (kind !== 'yt' && kind !== 'file') || !isPlaying()) return;
  send({t: 'sync', time: getTime(), playing: true});
}, 3000);

/* ---------- Veb səhifədən gələn hadisələr ---------- */
let autoNavs = [], autoWarned = false;
window.addEventListener('message', e => {
  const m = e.data;
  if(!m || kind !== 'web') return;
  if(m.t === 'nav' && m.url && e.source === web.contentWindow){
    if(!m.auto){ autoNavs = []; autoWarned = false; loadPage(m.url, false); return; }
    // Saytın öz skriptinin yönləndirməsi: qarşı tərəfə göndərilmir (o da eyni yönləndirməni özü edir),
    // qısa müddətdə 3-dən çox olarsa döngü sayılıb dayandırılır
    const now = Date.now();
    autoNavs = autoNavs.filter(t => now - t < 10000);
    if(m.url === curUrl) return;
    if(autoNavs.length >= 3){
      if(!autoWarned){ autoWarned = true; sysMsg('Sayt öz-özünə yönləndirir, dayandırıldı'); }
      return;
    }
    autoNavs.push(now);
    loadPage(m.url, true);
  }
  else if(m.t === 'v' && e.source === web.contentWindow) send({t:'wv', e:m.e, time:m.time});
});


/* ---------- Mesajlar: yanıt, saat, görüldü / oxundu ---------- */
const msgEls = new Map(), ownState = new Map();
const LABELS = {1: 'Göndərildi', 2: 'Görüldü', 3: 'Oxundu'};
let replyTo = null, mseq = 0;
const newId = () => me.slice(0, 4) + Date.now().toString(36) + (mseq++).toString(36);
const fmtTime = ts => { const d = ts ? new Date(ts * 1000) : new Date(); return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); };
function tickSvg(n){
  return n < 2 ? '<i class="fa-solid fa-check" aria-hidden="true"></i>' : '<i class="fa-solid fa-check-double" aria-hidden="true"></i>';
}
const REPLY_ICON = '<i class="fa-solid fa-reply" aria-hidden="true"></i>';

const VB_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12.00 0.10 L14.61 2.24 L17.95 1.69 L19.14 4.86 L22.31 6.05 L21.76 9.39 L23.90 12.00 L21.76 14.61 L22.31 17.95 L19.14 19.14 L17.95 22.31 L14.61 21.76 L12.00 23.90 L9.39 21.76 L6.05 22.31 L4.86 19.14 L1.69 17.95 L2.24 14.61 L0.10 12.00 L2.24 9.39 L1.69 6.05 L4.86 4.86 L6.05 1.69 L9.39 2.24Z" fill="currentColor"/><path d="M7.1 12.4l3.4 3.4 6.6-6.8" fill="none" stroke="#fff" stroke-width="2.5" stroke-linecap="butt" stroke-linejoin="miter"/></svg>';
function vbEl(){
  const s = document.createElement('span'); s.className = 'vb'; s.title = 'Təsdiqlənmiş hesab'; s.innerHTML = VB_SVG;
  return s;
}
function roleIcon(r){
  const s = document.createElement('span'); s.className = 'rolei ' + r;
  s.title = r === 'host' ? 'Host' : 'Moderator';
  s.innerHTML = r === 'host' ? '<i class="fa-solid fa-crown" aria-hidden="true"></i>' : '<i class="fa-solid fa-shield-halved" aria-hidden="true"></i>';
  return s;
}
function addMsg(name, text, mine, o){
  o = o || {};
  const d = document.createElement('div'); d.className = 'm' + (mine ? ' me' : '');
  d.dataset.name = name; d.dataset.text = text || (o.img ? '📷 Şəkil' : '');
  if(mine) d.dataset.st = o.hist ? '3' : '1';
  if(o.id){ d.dataset.mid = o.id; msgEls.set(o.id, d); }
  const b = document.createElement('b'); b.dataset.nt = ''; b.appendChild(avEl(o.u, o.av, name)); b.appendChild(document.createTextNode(name)); if(o.vf) b.appendChild(vbEl()); if(o.rl === 'host' || o.rl === 'mod') b.appendChild(roleIcon(o.rl)); d.appendChild(b);
  if(!mine) b.onclick = () => openPeerCard(peerForMsg(o.u, name));
  if(o.reply && o.reply.id){
    const q = document.createElement('div'); q.className = 'q'; q.dataset.nt = '';
    const qn = document.createElement('strong'); qn.textContent = String(o.reply.name || '').slice(0, 20);
    const qt = document.createElement('span'); qt.textContent = String(o.reply.text || '').slice(0, 90);
    q.appendChild(qn); q.appendChild(qt);
    q.onclick = () => jumpTo(o.reply.id);
    d.appendChild(q);
  }
  if(o.img && (o.local || IMG_URL_RE.test(o.img))) d.appendChild(imgBox(o.img, !!o.sens, mine));
  if(text){ const s = document.createElement('span'); s.dataset.nt = ''; s.textContent = text; d.appendChild(s); }
  const meta = document.createElement('div'); meta.className = 'meta';
  const tm = document.createElement('span'); tm.textContent = fmtTime(o.ts); meta.appendChild(tm);
  if(mine){
    const lb = document.createElement('span'); lb.className = 'lab';
    const tk = document.createElement('span'); tk.className = 'tick'; tk.innerHTML = tickSvg(o.hist ? 3 : 1);
    meta.appendChild(lb); meta.appendChild(tk);
  }
  if(!mine && o.u && token && !o.sys){
    const fl = document.createElement('button'); fl.className = 'rpt'; fl.type = 'button';
    fl.innerHTML = '<i class="fa-solid fa-flag" aria-hidden="true"></i>'; fl.setAttribute('aria-label', 'Şikayət et');
    fl.onclick = e => { e.stopPropagation(); openReport({username: o.u, name: name, kind: 'message', msg_id: o.id || '', msg_text: d.dataset.text || ''}); };
    meta.appendChild(fl);
  }
  d.appendChild(meta);
  if(o.id){
    const rp = document.createElement('button'); rp.className = 'rp'; rp.innerHTML = REPLY_ICON;
    rp.setAttribute('aria-label', 'Yanıt ver'); rp.onclick = () => setReply(o.id);
    d.appendChild(rp);
    bindSwipe(d, o.id);
    bindHold(d, o.id);   // basıb saxlayanda reaksiya paneli çıxır
    if(rxs.has(o.id)) rxRender(o.id);
  }
  $('chat').appendChild(d); $('chat').scrollTop = 1e9;
  refreshLabels();
  return d;
}
function refreshLabels(){
  const mine = $('chat').querySelectorAll('.m.me');
  mine.forEach((el, i) => {
    const lb = el.querySelector('.lab');
    if(lb) lb.textContent = i === mine.length - 1 ? LABELS[el.dataset.st || 1] : '';
  });
}
function setReceipt(id, st){
  const cur = ownState.get(id);
  if(cur === undefined || cur >= st) return;
  ownState.set(id, st);
  const el = msgEls.get(id); if(!el) return;
  el.dataset.st = String(st);
  const tk = el.querySelector('.tick'); if(tk) tk.innerHTML = tickSvg(st);
  refreshLabels();
}
function jumpTo(id){
  const el = msgEls.get(id); if(!el) return;
  el.scrollIntoView({block: 'center', behavior: 'smooth'});
  el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash');
}
function setReply(id){
  const el = msgEls.get(id); if(!el) return;
  replyTo = {id, name: el.dataset.name, text: el.dataset.text.slice(0, 80)};
  $('rbName').textContent = replyTo.name; $('rbText').textContent = replyTo.text;
  $('replybar').hidden = false; $('msg').focus();
}
function clearReply(){ replyTo = null; $('replybar').hidden = true; }
$('rbX').onclick = clearReply;

/* sağa sürüşdür: yanıt ver */
function bindSwipe(d, id){
  let sx = 0, sy = 0, act = false, drag = false, pid = null;
  d.addEventListener('pointerdown', e => {
    if(e.target.closest('.rp, .q, .rx')) return;
    act = true; drag = false; sx = e.clientX; sy = e.clientY; pid = e.pointerId;
  });
  d.addEventListener('pointermove', e => {
    if(!act || e.pointerId !== pid) return;
    const mx = e.clientX - sx, my = e.clientY - sy;
    if(!drag){
      if(mx > 10 && mx > Math.abs(my) * 1.5){ drag = true; d.classList.add('drag'); try{ d.setPointerCapture(pid); }catch(_){} }
      else { if(Math.abs(my) > 12) act = false; return; }
    }
    const dx = Math.max(0, Math.min(mx, 72));
    d.style.transform = 'translateX(' + dx + 'px)';
    d.classList.toggle('armed', dx >= 56);
  });
  const end = () => {
    if(!act) return;
    act = false; d.classList.remove('drag'); d.style.transform = '';
    const fire = drag && d.classList.contains('armed');
    d.classList.remove('armed'); drag = false;
    if(fire) setReply(id);
  };
  d.addEventListener('pointerup', end); d.addEventListener('pointercancel', end);
}

/* "Oxundu": mesaj ekranda görünəndə və səhifə aktiv olanda göndərilir */
const pendingRead = new Set();
const pageActive = () => document.visibilityState === 'visible';   // hasFocus() mobildə tez-tez yanlış false verir (video/iframe/klaviatura) və "Oxundu" heç göndərilmirdi
function flushRead(){
  if(!pendingRead.size || !pageActive()) return;
  send({t: 'read', ids: [...pendingRead]}); pendingRead.clear();
}
const readIO = 'IntersectionObserver' in window ? new IntersectionObserver(ents => {
  ents.forEach(e => {
    if(e.isIntersecting && (e.intersectionRatio >= .5 || e.intersectionRect.height >= 100)){
      readIO.unobserve(e.target); pendingRead.add(e.target.dataset.mid); flushRead();
    }
  });
}, {root: $('chat'), threshold: [0, .25, .5, .75, 1]}) : null;
function watchRead(el, id){ if(readIO) readIO.observe(el); else { pendingRead.add(id); flushRead(); } }
window.addEventListener('focus', () => setTimeout(flushRead, 150));
setInterval(flushRead, 1500);   // gözləyən "oxundu"lar heç vaxt ilişib qalmasın
document.addEventListener('visibilitychange', () => setTimeout(flushRead, 150));

/* ---------- Şəkil göndərmə (həssas məzmun xəbərdarlığı ilə) ---------- */
const IMG_URL_RE = /^\/if\/[a-z0-9-]+\/[0-9a-f]{16}\.(jpg|gif)$/;
function openLightbox(url){ const lb = $('lightbox'); lb.querySelector('img').src = url; lb.hidden = false; }
$('lightbox').onclick = () => { $('lightbox').hidden = true; $('lightbox').querySelector('img').src = ''; };
document.addEventListener('keydown', e => { if(e.key === 'Escape' && !$('lightbox').hidden) $('lightbox').onclick(); });
function imgBox(url, sens, mine){
  const w = document.createElement('div'); w.className = 'mimg';
  const im = document.createElement('img'); im.src = url; im.alt = 'Şəkil'; im.draggable = false; w.appendChild(im);
  if(sens && !mine){
    w.classList.add('sens');
    const ov = document.createElement('button'); ov.type = 'button'; ov.className = 'sensov';
    ov.innerHTML = '<i class="fa-solid fa-eye-slash" aria-hidden="true"></i><span class="t1">Həssas məzmun</span><span class="t2">Açmaq üçün toxun</span>';
    ov.onclick = e => { e.stopPropagation(); w.classList.remove('sens'); ov.remove(); };
    w.appendChild(ov);
  } else if(sens){
    const b = document.createElement('span'); b.className = 'sensbadge'; b.textContent = 'Həssas'; w.appendChild(b);
  }
  im.onclick = () => { if(!w.classList.contains('sens')) openLightbox(url); };
  return w;
}
function shrinkImage(file){
  return new Promise((res, rej) => {
    const u = URL.createObjectURL(file), im = new Image();
    im.onload = () => {
      const k = Math.min(1, 1600 / Math.max(im.naturalWidth, im.naturalHeight));
      const c = document.createElement('canvas');
      c.width = Math.max(1, Math.round(im.naturalWidth * k)); c.height = Math.max(1, Math.round(im.naturalHeight * k));
      const g = c.getContext('2d'); g.fillStyle = '#fff'; g.fillRect(0, 0, c.width, c.height); g.drawImage(im, 0, 0, c.width, c.height);
      URL.revokeObjectURL(u);
      c.toBlob(b => b ? res(b) : rej(new Error('blob')), 'image/jpeg', 0.85);
    };
    im.onerror = () => { URL.revokeObjectURL(u); rej(new Error('img')); };
    im.src = u;
  });
}
/* Həssas şəkilin avtomatik tanınması: model telefonda işləyir, şəkil yoxlanana qədər serverə getmir */
let nsfwModel = null, nsfwLoading = null;
const NSFW_LIMIT = {pornHentai: 0.35, sexy: 0.55};   // həssaslığı buradan tənzimləmək olar
function loadScript(src){
  return new Promise((res, rej) => { const el = document.createElement('script'); el.src = src; el.onload = res; el.onerror = () => rej(new Error('script')); document.head.appendChild(el); });
}
function getNsfw(){
  if(nsfwModel) return Promise.resolve(nsfwModel);
  if(!nsfwLoading) nsfwLoading = (async () => {
    if(!window.tf) await loadScript('https://cdn.jsdelivr.net/npm/@tensorflow/tfjs@4.22.0/dist/tf.min.js');
    if(!window.nsfwjs) await loadScript('https://cdn.jsdelivr.net/npm/nsfwjs@4.2.1/dist/browser/nsfwjs.min.js');
    nsfwModel = await window.nsfwjs.load();
    return nsfwModel;
  })().catch(e => { nsfwLoading = null; throw e; });
  return nsfwLoading;
}
$('imgbtn').onclick = () => { if(room){ getNsfw().catch(() => {}); $('imgfile').click(); } };   // model əvvəlcədən yüklənməyə başlayır
/* şəkil həssasdırmı: heç vaxt xəta atmır, yoxlama alınmasa "həssas deyil" sayılır */
async function detectSens(url){
  try{
    const model = await Promise.race([getNsfw(), new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), 20000))]);
    const im = new Image(); im.src = url; await im.decode();
    const pr = await model.classify(im), sc = {};
    pr.forEach(x => { sc[x.className] = x.probability; });
    return ((sc.Porn || 0) + (sc.Hentai || 0) >= NSFW_LIMIT.pornHentai) || ((sc.Sexy || 0) >= NSFW_LIMIT.sexy);
  }catch(e){ return false; }
}
/* Instagram kimi: şəkil seçilən kimi çatda görünür və göndərilir; həssaslıq arxa planda avtomatik təyin olunur */
$('imgfile').onchange = async () => {
  const f = $('imgfile').files[0]; $('imgfile').value = '';
  if(!f || !room) return;
  let blob;
  try{ blob = (f.type === 'image/gif' && f.size <= 6 * 1024 * 1024) ? f : await shrinkImage(f); }catch(e){ sysMsg('Şəkil açıla bilmədi'); return; }
  const id = newId(), local = URL.createObjectURL(blob);
  const rp = replyTo ? {id: replyTo.id, name: replyTo.name, text: replyTo.text} : null;
  const myU = (account && account.username) || '', myV = myAvVer();
  const el = addMsg(myName, '', true, {id, reply: rp, u: myU, av: myV, img: local, local: true, rl: myRole});
  ownState.set(id, 1); clearReply();
  el.classList.add('sending');
  try{
    const upload = fetch('/api/img?room=' + encodeURIComponent(room) + '&id=' + encodeURIComponent(me),
      {method: 'POST', headers: {'Content-Type': 'image/jpeg'}, body: blob})
      .then(r => { if(!r.ok) throw new Error('http ' + r.status); return r.json(); });
    const [d, sens] = await Promise.all([upload, detectSens(local)]);
    if(!IMG_URL_RE.test(d.url || '')) throw new Error('url');
    send({t: 'chat', id, name: myName, text: '', img: d.url, sens: sens ? 1 : 0, reply: rp, u: myU, av: myV});
    if(sens){ const w = el.querySelector('.mimg'); if(w){ const b = document.createElement('span'); b.className = 'sensbadge'; b.textContent = 'Həssas'; w.appendChild(b); } }
  }catch(e){ sysMsg('Şəkil göndərilmədi, yenidən cəhd et'); }
  el.classList.remove('sending');
};

function sendChat(){
  const t = $('msg').value.trim();
  if(!t) return;
  const id = newId();
  const rp = replyTo ? {id: replyTo.id, name: replyTo.name, text: replyTo.text} : null;
  const myU = (account && account.username) || '', myV = myAvVer();
  addMsg(myName, t, true, {id, reply: rp, u: myU, av: myV, vf: !!(account && account.badge), rl: myRole});
  ownState.set(id, 1);
  send({t: 'chat', id, name: myName, text: t, reply: rp, u: myU, av: myV});
  $('msg').value = ''; clearReply();
}

/* ---------- "Yazır..." göstəricisi ---------- */
let typingTimer = null, typingOn = false, lastTypingSent = 0;
function showTyping(name){
  typingOn = true;
  setStatus(String(name || peerNm()).slice(0, 20) + ' yazır...');
  clearTimeout(typingTimer);
  typingTimer = setTimeout(() => clearTyping(), 4000);
}
function clearTyping(refresh = true){
  clearTimeout(typingTimer);
  if(!typingOn) return;
  typingOn = false;
  if(refresh && !offline) refreshStatus();
}
$('msg').addEventListener('input', () => {
  if(!connected || !$('msg').value.trim()) return;
  const now = Date.now();
  if(now - lastTypingSent > 2500){ lastTypingSent = now; send({t: 'typing', name: myName}); }
});

/* ---------- Oxunmamış mesaj sayı (sekmənin adında) ---------- */
const BASE_TITLE = document.title;
let unread = 0;
function bumpUnread(){
  if(document.visibilityState !== 'hidden') return;
  unread++; document.title = '(' + unread + ') ' + BASE_TITLE;
}
function clearUnread(){ if(!unread) return; unread = 0; document.title = BASE_TITLE; }
window.addEventListener('focus', clearUnread);
document.addEventListener('visibilitychange', () => { if(document.visibilityState === 'visible') clearUnread(); });

/* ---------- Videonun üstündə uçan emoji ---------- */
const REACTS = ['❤️', '😂', '😭', '😮', '🔥', '👏'];
const REACT_FA = {'❤️': ['fa-heart', '#ff5a7e'], '😂': ['fa-face-laugh-squint', '#ffc46b'], '😭': ['fa-face-sad-cry', '#ffc46b'],
  '😮': ['fa-face-surprise', '#ffc46b'], '🔥': ['fa-fire', '#ff9a3c'], '👏': ['fa-hands-clapping', '#ffc46b']};
function flyEmoji(e){
  const p = $('player'); if(!p) return;
  const el = document.createElement('span'); el.className = 'fly';
  const fa = REACT_FA[e] || REACT_FA['❤️'];
  el.innerHTML = '<i class="fa-solid ' + fa[0] + '" style="color:' + fa[1] + '" aria-hidden="true"></i>';
  el.style.left = (6 + Math.random() * 80) + '%';
  el.style.setProperty('--rise', Math.round((p.clientHeight || 200) * 0.85) + 'px');
  el.style.setProperty('--dx', Math.round((Math.random() - .5) * 70) + 'px');
  p.appendChild(el);
  el.addEventListener('animationend', () => el.remove());
  setTimeout(() => el.remove(), 3000);
  const all = p.querySelectorAll('.fly'); if(all.length > 24) all[0].remove();
}
let lastFly = 0;
document.querySelectorAll('#reacts button').forEach(b => b.onclick = () => {
  if(!room) return;
  const now = Date.now(); if(now - lastFly < 120) return; lastFly = now;
  flyEmoji(b.dataset.e); send({t: 'fly', e: b.dataset.e});
});

