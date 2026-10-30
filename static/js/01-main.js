const $ = id => document.getElementById(id);
let myName = 'Mən', kind = null, curUrl = null;
let yt = null, ytReady = false, ytIsReady = false, ytPending = null;
let ignoreUntil = 0, lastT = null, hls = null;
let wantPlay = false, lastRemote = {time: 0, at: 0};
let freshLoad = false;   // video serverdən yenicə açılıb: öz vaxtımızı (0) qarşı tərəfə göndərmə
let room = '', me = Math.random().toString(36).slice(2, 10);
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
      await new Promise(res => setTimeout(res, 2000));
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
  myName = account ? account.name : 'Mən';
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
      try{ const sv = localStorage.getItem('wt_me_' + code); if(sv) me = sv; }catch(e){}
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
      try{ localStorage.setItem('wt_me_' + code, me); }catch(e){}
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
function loadPage(url, remote, tries = 0){
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
  else if(m.t === 'v') send({t:'wv', e:m.e, time:m.time});
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
      el.classList.toggle('talk', Math.sqrt(sum / buf.length) > 0.035);
    }, 110);
    levels.set(el, {iv, src});
  }catch(e){}
}
function stopLevel(el){
  const l = levels.get(el);
  if(l){ clearInterval(l.iv); try{ l.src.disconnect(); }catch(e){} levels.delete(el); }
  el.classList.remove('talk');
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
  if(m.t === 'vmute'){ $('vpPeer').classList.toggle('muted', !!m.on); return; }
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
};
window.addEventListener('pagehide', () => {
  if(!voiceOn || !room) return;
  try{ navigator.sendBeacon('/api/send', new Blob([JSON.stringify({room, id: me, msg: {t: 'voice', on: false, from: me}})], {type: 'application/json'})); }catch(e){}
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
  token = ''; account = null;
  try{ localStorage.removeItem(TOKEN_KEY); }catch(e){}
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
  showMenu();                                        // menyu bilet cırılanda arxada görünsün
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

/* ---------- Ana menyu ---------- */
function showMenu(){
  initFloaties();
  startInvitePoll();
  $('app').style.display = 'none';
  $('menu').style.display = 'flex';
  renderName();
  lastFr = ''; lastInv = '';
  loadHistory(); loadStats(); showInstall();
  try{ if(window.wtVerifyDomino) window.wtVerifyDomino(); if(window.wtVerifyChess) window.wtVerifyChess(); }catch(e){}
  $('mquote').textContent = '“' + DAILY[Math.floor(Date.now() / 864e5) % DAILY.length] + '”';
  fetch('/api/stats', {cache: 'no-store'}).then(r => r.json()).then(d => { $('stl').textContent = d.people; }).catch(() => {});
}
function setAvatar(el, url, name){
  if(url){ el.style.backgroundImage = 'url("' + url + '")'; el.textContent = ''; }
  else { el.style.backgroundImage = ''; el.textContent = initial(name); }
}
function renderName(){
  $('mname').textContent = account ? account.name : '';
  if(account && account.badge) $('mname').appendChild(vbEl());
  const hr = new Date().getHours();
  $('mgreet').textContent = hr < 5 ? 'Gecən xeyir' : hr < 12 ? 'Sabahın xeyir' : hr < 18 ? 'Günün xeyir' : 'Axşamın xeyir';
  $('muser').textContent = account && account.username ? '@' + account.username : '';
  setAvatar($('mav'), account && account.avatar, account && account.name);
}
const fmtDate = ts => azDate(new Date(ts * 1000));
async function loadHistory(){
  try{
    const r = await api('/api/history');
    if(!r.ok) throw new Error('hist');
    renderHistory(await r.json());
  }catch(e){}
}
function emptyNote(box, ico, t, sub){
  const d = document.createElement('div'); d.className = 'empty';
  const i = document.createElement('span'); i.className = 'ei'; i.innerHTML = ico;
  const b = document.createElement('b'); b.textContent = t;
  const p = document.createElement('span'); p.textContent = sub;
  d.appendChild(i); d.appendChild(b); d.appendChild(p); box.appendChild(d);
}
function renderHistory(d){
  const vb = $('hvids'), rb = $('hrooms');
  vb.textContent = ''; rb.textContent = '';
  $('stv').textContent = d.videos.length; $('str').textContent = d.rooms.length;
  if(!d.videos.length) emptyNote(vb, '<i class="fa-solid fa-circle-play" aria-hidden="true"></i>', 'Hələ heç nə izləməmisən', 'Otaqda açdığın videolar burada görünəcək');
  d.videos.forEach(x => {
    const ok = /^https?:\/\//i.test(x.url || '');
    const el = document.createElement(ok ? 'a' : 'div'); el.className = 'hs';
    if(ok){ el.href = x.url; el.target = '_blank'; el.rel = 'noopener noreferrer'; }
    if(x.thumb && /^https:\/\/i\.ytimg\.com\//.test(x.thumb)){
      const im = document.createElement('img'); im.src = x.thumb; im.loading = 'lazy'; im.alt = ''; el.appendChild(im);
    } else {
      const ph = document.createElement('div'); ph.className = 'ph'; ph.dataset.nt = ''; ph.textContent = x.title || ''; el.appendChild(ph);
    }
    const tx = document.createElement('div'); tx.className = 'tx';
    const t = document.createElement('span'); t.dataset.nt = ''; t.textContent = x.title || x.url;
    const sm = document.createElement('small'); sm.textContent = fmtDate(x.ts);
    tx.appendChild(t); tx.appendChild(sm); el.appendChild(tx);
    vb.appendChild(el);
  });
  if(!d.rooms.length) emptyNote(rb, '<i class="fa-solid fa-door-open" aria-hidden="true"></i>', 'Hələ otaq yoxdur', 'İlk otağı aç, dostunu dəvət et');
  d.rooms.forEach(x => {
    const el = document.createElement('div'); el.className = 'hs hsr';
    const c = document.createElement('div'); c.className = 'rcode'; c.textContent = String(x.room).toUpperCase();
    const tx = document.createElement('div'); tx.className = 'tx';
    const t = document.createElement('span'); t.textContent = 'Otağa yenidən gir';
    const sm = document.createElement('small'); sm.textContent = fmtDate(x.ts);
    tx.appendChild(t); tx.appendChild(sm); el.appendChild(c); el.appendChild(tx);
    el.onclick = () => { if(!joinBusy) enterRoom(x.room, false); };
    rb.appendChild(el);
  });
}
function track(o){ if(token) api('/api/activity', {method: 'POST', body: JSON.stringify(o)}).catch(() => {}); }

/* ---------- İstifadəçi adı ilə dəvət (otaqdan) ---------- */
function invMsg(t, ok){ const e = $('invmsg'); e.textContent = t || ''; e.className = ok ? 'ok' : ''; }
$('invite').onclick = () => { invMsg(''); $('invuser').value = ''; $('invbox').hidden = false; fillInvFriends(); setTimeout(() => $('invuser').focus(), 60); };
$('invNo').onclick = () => { $('invbox').hidden = true; };
$('invbox').addEventListener('click', e => { if(e.target === $('invbox')) $('invbox').hidden = true; });
async function sendInvite(){
  const u = $('invuser').value.trim().replace(/^@/, '').toLowerCase();
  if(!UNAME_RE.test(u)){ invMsg('İstifadəçi adı düzgün deyil'); return; }
  if(!token){ invMsg('Dəvət üçün hesaba daxil ol'); return; }
  const b = $('invYes'); b.disabled = true; invMsg('');
  try{
    const r = await api('/api/invite', {method: 'POST', body: JSON.stringify({username: u, room})});
    if(r.ok){ invMsg('Dəvət göndərildi. @' + u + ' ana menyuda görəcək.', true); $('invuser').value = ''; setTimeout(() => { $('invbox').hidden = true; }, 1600); }
    else if(r.status === 403) invMsg('Dəvət göndərilə bilmədi: gizlilik və ya blok məhdudiyyəti');
    else if(r.status === 404){ const d = await r.json().catch(() => ({})); invMsg(d.error === 'room' ? 'Otaq tapılmadı' : 'Belə istifadəçi tapılmadı'); }
    else if(r.status === 429) invMsg('Çox dəvət göndərdin, bir az sonra yenidən yoxla');
    else if(r.status === 400) invMsg('Özünü dəvət edə bilməzsən');
    else invMsg('Dəvət getmədi, yenidən cəhd et');
  }catch(e){ invMsg('Əlaqə problemi, yenidən cəhd et'); }
  finally{ b.disabled = false; }
}
$('invYes').onclick = sendInvite;
$('invuser').addEventListener('keydown', e => { if(e.key === 'Enter') sendInvite(); if(e.key === 'Escape') $('invbox').hidden = true; });
$('invuser').addEventListener('input', () => { const el = $('invuser'), v = el.value.toLowerCase().replace(/[^a-z0-9._@]/g, ''); if(v !== el.value) el.value = v; });

/* ---------- Dostlar: axtar, sorğu göndər, qəbul et ---------- */
const AV_RE = /^\/api\/avatar\/[a-z0-9._]{3,20}\?v=\d+$/;
function frBtn(html, cls, fn, label){
  const b = document.createElement('button'); b.type = 'button'; b.className = 'fb ' + (cls || ''); b.innerHTML = html;
  if(label) b.setAttribute('aria-label', label);
  b.onclick = fn; return b;
}
function frTag(t){ const s = document.createElement('span'); s.className = 'ftag'; s.textContent = t; return s; }
function frRow(p, sub, btns){
  const r = document.createElement('div'); r.className = 'fr';
  const av = document.createElement('div'); av.className = 'av'; av.textContent = initial(p.name);
  if(p.avatar && AV_RE.test(p.avatar)){ av.style.backgroundImage = 'url("' + p.avatar + '")'; av.textContent = ''; }
  const tx = document.createElement('div'); tx.className = 'tx';
  const b = document.createElement('b'); b.dataset.nt = ''; b.textContent = p.name; if(p.badge) b.appendChild(vbEl());
  const s = document.createElement('span'); s.textContent = sub || ('@' + p.username);
  tx.appendChild(b); tx.appendChild(s);
  if(p.presence){ const ps=document.createElement('small'); ps.className = p.online ? 'presence online' : 'presence'; ps.textContent=p.presence; tx.appendChild(ps); }
  r.appendChild(av); r.appendChild(tx); (btns || []).forEach(x => r.appendChild(x));
  return r;
}
let frNoteT = null;
function frNote(t){ const e = $('menumsg'); e.textContent = t; clearTimeout(frNoteT); frNoteT = setTimeout(() => { e.textContent = ''; }, 3500); }
async function frAct(path, body){
  try{
    const r = await api(path, {method: 'POST', body: JSON.stringify(body)});
    if(r.status === 429) frNote('Çox sorğu göndərdin, bir az gözlə');
    else if(!r.ok) frNote('Alınmadı, yenidən cəhd et');
  }catch(e){ frNote('Əlaqə problemi, yenidən cəhd et'); }
  loadFriends(); if($('fq').value.trim().replace(/^@/, '').length >= 2) doSearch();
}
function searchBtns(p){
  if(p.blocked) return [frBtn('Blokdan çıxar','',async()=>{ await frAct('/api/unblock',{username:p.username}); doSearch(); loadBlocked(); },'Blokdan çıxar')];
  if(p.blocked_me) return [frTag('Səni bloklayıb')];
  const block = frBtn('Blok et','block-btn',async()=>{ const ok=await wtConfirm('@'+p.username+' bloklansın?',{ok:'Blok et',danger:true}); if(ok){ await frAct('/api/block',{username:p.username}); doSearch(); loadBlocked(); } },'Blok et');
  let main;
  if(p.rel === 'friend') main = frTag('Dostsunuz');
  else if(p.rel === 'out') main = frTag('Göndərildi');
  else if(p.rel === 'in') main = frBtn('Qəbul et', '', () => frAct('/api/friend/respond', {username: p.username, accept: true}));
  else main = frBtn('<i class="fa-solid fa-user-plus" aria-hidden="true"></i> Əlavə et', '', () => frAct('/api/friend/request', {username: p.username}));
  return [main, block];
}
let fqTimer = null, fqSeq = 0;
async function doSearch(){
  const q = $('fq').value.trim().replace(/^@/, ''), box = $('fres'), my = ++fqSeq;
  if(q.length < 2){ box.textContent = ''; return; }
  try{
    const r = await api('/api/users/search?q=' + encodeURIComponent(q));
    if(my !== fqSeq) return;
    box.textContent = '';
    if(!r.ok) return;
    const list = (await r.json()).users || [];
    if(!list.length){ const e = document.createElement('div'); e.className = 'empty fempty'; e.textContent = 'Belə istifadəçi tapılmadı'; box.appendChild(e); return; }
    list.forEach(p => box.appendChild(frRow(p, null, searchBtns(p))));
  }catch(e){}
}
$('fq').addEventListener('input', () => {
  const el = $('fq'), v = el.value.toLowerCase().replace(/[^a-z0-9._@]/g, ''); if(v !== el.value) el.value = v;
  clearTimeout(fqTimer); fqTimer = setTimeout(doSearch, 300);
});
function frSection(box, title){ const t = document.createElement('div'); t.className = 'fsub'; t.textContent = title; box.appendChild(t); }
function renderFriends(d){
  const rq = $('freqs'); rq.textContent = '';
  const inc = d.incoming || [], fr = d.friends || [], out = d.outgoing || [];
  const notifDot = $('friendNotifDot');
  if(notifDot) notifDot.hidden = inc.length === 0;
  const friendsTab = document.querySelector('#tabbar [data-go="friends"]');
  if(friendsTab) friendsTab.setAttribute('aria-label', inc.length ? `Dostlar, ${inc.length} yeni sorğu` : 'Dostlar');
  if(inc.length){
    frSection(rq, 'Dostluq sorğuları');
    inc.forEach(p => rq.appendChild(frRow(p, 'dost olmaq istəyir', [
      frBtn('Qəbul et', '', () => frAct('/api/friend/respond', {username: p.username, accept: true})),
      frBtn('<i class="fa-solid fa-xmark" aria-hidden="true"></i>', 'no', () => frAct('/api/friend/respond', {username: p.username, accept: false}), 'Rədd et')])));
  }
  const fl = $('flist'); fl.textContent = '';
  if(fr.length){
    frSection(fl, 'Dostların');
    fr.forEach(p => fl.appendChild(frRow(p, null, [
      frBtn('<i class="fa-solid fa-user-minus" aria-hidden="true"></i>', 'no', () => { wtConfirm('@' + p.username + ' dostlardan çıxarılsın?', {ok: 'Dostlardan çıxar', danger: true}).then(y => { if(y) frAct('/api/friend/remove', {username: p.username}); }); }, 'Dostlardan çıxar'),
      frBtn('Blok et','block-btn',async()=>{ const y=await wtConfirm('@'+p.username+' bloklansın?',{ok:'Blok et',danger:true}); if(y){ await frAct('/api/block',{username:p.username}); loadBlocked(); } },'Blok et')])));
  }
  if(out.length){
    frSection(fl, 'Göndərilən sorğular');
    out.forEach(p => fl.appendChild(frRow(p, 'sorğu göndərilib', [
      frBtn('<i class="fa-solid fa-xmark" aria-hidden="true"></i>', 'no', () => frAct('/api/friend/remove', {username: p.username}), 'Sorğunu ləğv et')])));
  }
  if(!fr.length && !inc.length && !out.length){
    const e = document.createElement('div'); e.className = 'empty'; e.textContent = 'Hələ dostun yoxdur. Dostunun istifadəçi adını yuxarıda axtar.'; fl.appendChild(e);
  }
}
async function loadFriends(){
  if(!token) return;
  try{
    const r = await api('/api/friends');
    if(r.ok){ const t = await r.text(); if(t !== lastFr){ lastFr = t; renderFriends(JSON.parse(t)); } }
  }catch(e){}
}
async function fillInvFriends(){
  const box = $('invfr'); box.textContent = '';
  if(!token) return;
  try{
    const r = await api('/api/friends');
    if(!r.ok) return;
    ((await r.json()).friends || []).slice(0, 12).forEach(p => {
      const b = document.createElement('button'); b.type = 'button'; b.className = 'chip'; b.textContent = p.name;
      b.onclick = () => { $('invuser').value = p.username; sendInvite(); };
      box.appendChild(b);
    });
  }catch(e){}
}
function pinvHint(){
  $('pinvhint').textContent = $('pinv').checked ? 'Açıq: hər kəs səni otağa dəvət edə bilər.' : 'Bağlı: heç kim səni otağa dəvət edə bilməz.';
}
async function sendPresence(){
  if(!token) return;
  try{ await api('/api/presence', {method:'POST', body:'{}'}); }catch(e){}
}
setInterval(() => { if(token && !document.hidden) sendPresence(); }, 20000);
document.addEventListener('visibilitychange', () => { if(!document.hidden) sendPresence(); });
$('plastseen').onchange = async () => {
  const sel = $('plastseen'), value = sel.value;
  try{
    const r = await api('/api/settings/last-seen-visibility', {method:'POST', body:JSON.stringify({visibility:value})});
    if(!r.ok) throw new Error('privacy');
    if(account) account.last_seen_visibility = value;
    pmsg('Son görülmə və onlayn statusunun gizliliyi yadda saxlandı', true);
  }catch(e){
    sel.value = account && account.last_seen_visibility || 'everyone';
    pmsg('Gizlilik dəyişmədi, yenidən cəhd et');
  }
};
async function loadBlocked(){
  if(!token || !$('blockedlist')) return;
  const box = $('blockedlist');
  try{
    const r = await api('/api/blocks'); if(!r.ok) return;
    const list = (await r.json()).blocked || []; box.textContent = '';
    if(!list.length){ const n=document.createElement('div'); n.className='hint'; n.textContent='Heç kimi bloklamamısan.'; box.appendChild(n); return; }
    list.forEach(p => {
      const b=frBtn('Blokdan çıxar','',async()=>{ await frAct('/api/unblock',{username:p.username}); loadBlocked(); },'Blokdan çıxar');
      box.appendChild(frRow(p, '@'+p.username, [b]));
    });
  }catch(e){}
}
$('pavprivacy').onchange = async () => {
  const sel = $('pavprivacy'), value = sel.value;
  try{
    const r = await api('/api/settings/avatar-visibility', {method:'POST', body:JSON.stringify({visibility:value})});
    if(!r.ok) throw new Error('privacy');
    if(account) account.avatar_visibility = value;
    pmsg('Profil şəklinin gizliliyi yadda saxlandı', true);
  }catch(e){
    sel.value = account && account.avatar_visibility || 'everyone';
    pmsg('Gizlilik dəyişmədi, yenidən cəhd et');
  }
};

$('pinv').onchange = async () => {
  const on = $('pinv').checked; pinvHint();
  try{
    const r = await api('/api/settings/invites', {method: 'POST', body: JSON.stringify({open: on})});
    if(!r.ok) throw new Error('x');
    if(account) account.invites_open = on;
    pmsg(on ? 'Dəvətlər açıqdır' : 'Dəvətlər bağlandı', true);
  }catch(e){ $('pinv').checked = !on; pinvHint(); pmsg('Dəyişmədi, yenidən cəhd et'); }
};

/* ---------- Otaqda qarşı tərəfin profili ---------- */
function peerForMsg(u, name){
  const hit = u && peerList.find(w => w.username === u);
  if(hit) return hit;
  if(u) return {username: u, name: name, avatar: '', badge: false};
  return peerList.length === 1 ? peerList[0] : {name: name, username: '', avatar: '', badge: false};
}
let pcUser = '';
function fillPeerHead(p){
  setAvatar($('pbav'), AV_RE.test(p.avatar || '') ? p.avatar : '', p.name);
  const n = $('pbname'); n.textContent = p.name || 'Qarşı tərəf'; if(p.badge) n.appendChild(vbEl());
  $('pbuser').textContent = p.username ? '@' + p.username : '';
  $('pbbio').textContent = p.bio || '';
  $('pbpresence').textContent = p.presence || '';
  $('pbpresence').className = p.online ? 'presence online' : 'presence';
}
function setPeerRel(rel){
  const a = $('pbAct'), t = $('pbrel');
  t.className = ''; a.hidden = false; a.disabled = false; a.dataset.rel = rel; t.textContent = '';
  if(rel === 'friend'){ t.textContent = 'Dostsunuz'; a.hidden = true; }
  else if(rel === 'out'){ a.textContent = 'Sorğu göndərilib'; a.disabled = true; }
  else if(rel === 'in'){ a.textContent = 'Qəbul et'; }
  else a.textContent = 'Dost əlavə et';
}
async function loadPeerRel(u){
  try{
    const r = await api('/api/users/search?q=' + encodeURIComponent(u));
    if(!r.ok || pcUser !== u) return;
    const p = ((await r.json()).users || []).find(x => x.username === u);
    if(!p) return;
    fillPeerHead(p); setPeerRel(p.rel);
    $('pbBlock').hidden = !!p.blocked_me;
    $('pbBlock').textContent = p.blocked ? 'Blokdan çıxar' : 'Blok et';
    $('pbBlock').dataset.blocked = p.blocked ? '1' : '0';
    if(p.blocked_me){ $('pbrel').textContent='Səni bloklayıb'; $('pbAct').hidden=true; }
  }catch(e){}
}
function openPeerCard(p){
  if(!p) return;
  pcUser = p.username || '';
  fillPeerHead(p);
  $('pbrel').textContent = ''; $('pbAct').hidden = true; $('pbBlock').hidden = !pcUser || !token;
  $('pbpresence').textContent = p.presence || '';
  $('pbox').hidden = false;
  if(pcUser && token) loadPeerRel(pcUser);
}
$('pbAct').onclick = async () => {
  const rel = $('pbAct').dataset.rel, u = pcUser;
  if(!u) return;
  $('pbAct').disabled = true;
  try{
    const acc = rel === 'in';
    const r = await api(acc ? '/api/friend/respond' : '/api/friend/request',
      {method: 'POST', body: JSON.stringify(acc ? {username: u, accept: true} : {username: u})});
    if(r.ok){ const d = await r.json().catch(() => ({})); setPeerRel(acc ? 'friend' : (d.rel || 'out')); loadFriends(); }
    else { $('pbAct').disabled = false; $('pbrel').className = 'err'; $('pbrel').textContent = 'Alınmadı, yenidən cəhd et'; }
  }catch(e){ $('pbAct').disabled = false; }
};
$('pbBlock').onclick = async () => {
  const u=pcUser; if(!u) return;
  const blocked=$('pbBlock').dataset.blocked === '1';
  const yes=await wtConfirm(blocked ? '@'+u+' blokdan çıxarılsın?' : '@'+u+' bloklansın?', {ok:blocked?'Blokdan çıxar':'Blok et',danger:!blocked});
  if(!yes) return;
  await frAct(blocked?'/api/unblock':'/api/block',{username:u});
  $('pbBlock').textContent=blocked?'Blok et':'Blokdan çıxar'; $('pbBlock').dataset.blocked=blocked?'0':'1';
  $('pbAct').hidden=!blocked; if(!blocked){ $('pbAct').hidden=true; $('pbrel').textContent='İstifadəçi bloklandı'; }
  loadBlocked();
};
$('pbClose').onclick = () => { $('pbox').hidden = true; };
$('pbox').addEventListener('click', e => { if(e.target === $('pbox')) $('pbox').hidden = true; });
$('status').addEventListener('click', () => { if(connected && peerList.length) openPeerCard(peerList[0]); });
$('status').style.cursor = 'pointer';
/* mesaj balonunun ixtiyari yerinə toxunanda da qarşı tərəfin profili açılsın (cavab/uzun basma ilə toqquşmasın deyə yalnız ad/avatar sahəsi + status) */
$('chat').addEventListener('click', e => {
  const sy = e.target.closest && e.target.closest('.sys');
  if(sy && peerList.length) openPeerCard(peerList[0]);
});

/* ---------- Ana menyuda gələn dəvətlər ---------- */
const seenInv = new Set();
let invTimer = null;
function startInvitePoll(){ loadInvites(); loadFriends(); loadPlans(); if(!invTimer) invTimer = setInterval(() => { loadInvites(); if(++planTick % 4 === 0) { loadPlans(); loadFriends(); } }, 4000); }
async function loadInvites(){
  if(!token || $('menu').style.display === 'none' || !$('profile').hidden || document.visibilityState !== 'visible') return;
  loadFriends();
  try{
    const r = await api('/api/invites');
    if(!r.ok) return;
    const j = await r.json(), list = j.invites || [], games = j.games || [], t = JSON.stringify([list, games]);
    if(t !== lastInv){ lastInv = t; renderInvites(list, games); }
  }catch(e){}
}
function renderInvites(list, games){
  games = games || [];
  const box = $('invites');
  let fresh = false;
  list.concat(games).forEach(i => { if(!seenInv.has(i.id)){ seenInv.add(i.id); fresh = true; } });
  if(fresh){ try{ if(navigator.vibrate) navigator.vibrate([40, 40, 40]); }catch(e){} try{ if(window.wtSfx) wtSfx.play('invite'); }catch(e){} }
  box.textContent = '';
  list.forEach(i => {
    const c = document.createElement('div'); c.className = 'invc';
    const av = document.createElement('div'); av.className = 'av'; av.textContent = initial(i.name);
    if(i.avatar && /^\/api\/avatar\/[a-z0-9._]{3,20}\?v=\d+$/.test(i.avatar)){ av.style.backgroundImage = 'url("' + i.avatar + '")'; av.textContent = ''; }
    const tx = document.createElement('div'); tx.className = 'tx';
    const b = document.createElement('b'); b.textContent = i.name + (i.username ? ' (@' + i.username + ')' : '');
    const sp = document.createElement('span'); sp.textContent = 'səni otağa dəvət edir';
    tx.appendChild(b); tx.appendChild(sp);
    const go = document.createElement('button'); go.className = 'go'; go.textContent = 'Qoşul';
    const no = document.createElement('button'); no.className = 'no'; no.innerHTML = '<i class="fa-solid fa-xmark" aria-hidden="true"></i>'; no.setAttribute('aria-label', 'Rədd et');
    const drop = () => { api('/api/invite/dismiss', {method: 'POST', body: JSON.stringify({id: i.id})}).catch(() => {}); c.remove(); };
    go.onclick = () => { if(joinBusy) return; drop(); enterRoom(i.room, false); };
    no.onclick = drop;
    c.appendChild(av); c.appendChild(tx); c.appendChild(go); c.appendChild(no);
    box.appendChild(c);
  });
  games.forEach(i => {
    const c = document.createElement('div'); c.className = 'invc';
    const av = document.createElement('div'); av.className = 'av'; av.textContent = initial(i.name);
    if(i.avatar && /^\/api\/avatar\/[a-z0-9._]{3,20}\?v=\d+$/.test(i.avatar)){ av.style.backgroundImage = 'url("' + i.avatar + '")'; av.textContent = ''; }
    const tx = document.createElement('div'); tx.className = 'tx';
    const b = document.createElement('b'); b.textContent = i.name + (i.username ? ' (@' + i.username + ')' : '');
    const sp = document.createElement('span'); sp.textContent = i.game === 'chess' ? 'səni Şahmat oyununa dəvət edir' : i.game === 'durak' ? 'səni Durak oyununa dəvət edir' : 'səni Domino oyununa dəvət edir';
    tx.appendChild(b); tx.appendChild(sp);
    const go = document.createElement('button'); go.className = 'go'; go.textContent = 'Qoşul';
    const no = document.createElement('button'); no.className = 'no'; no.innerHTML = '<i class="fa-solid fa-xmark" aria-hidden="true"></i>'; no.setAttribute('aria-label', 'Rədd et');
    const drop = () => { api('/api/game/invite/dismiss', {method: 'POST', body: JSON.stringify({id: i.id})}).catch(() => {}); c.remove(); };
    go.onclick = () => { drop(); const f = i.game === 'chess' ? window.wtJoinChess : i.game === 'durak' ? window.wtJoinDurak : window.wtJoinDomino; if(f) f(i.room); };
    no.onclick = drop;
    c.appendChild(av); c.appendChild(tx); c.appendChild(go); c.appendChild(no);
    box.appendChild(c);
  });
}

/* ---------- Profil səhifəsi ---------- */
async function loadProfile(){
  try{
    const r = await api('/api/me');
    if(r.ok) { applyProfile(await r.json()); sendPresence(); loadBlocked(); }
  }catch(e){}
}
function applyProfile(p){
  account = Object.assign(account || {}, p);
  myName = account.name;
  renderName();
  if(!$('profile').hidden) fillProfile();
}
function pmsg(t, ok){ const e = $('pmsg'); e.textContent = t || ''; e.dataset.ok = ok ? '1' : '0'; }
function hint(id, t, cls){ const e = $(id); e.textContent = t || ''; e.className = 'hint' + (cls ? ' ' + cls : ''); }
function fillProfile(){
  setAvatar($('pav'), account.avatar, account.name);
  $('pavdel').hidden = !account.avatar;
  $('pavmenu').hidden = true; $('pavEditWrap').classList.remove('editing'); $('pav').setAttribute('aria-expanded','false'); $('pavmenuopen').setAttribute('aria-expanded','false');
  if(document.activeElement !== $('pname')) $('pname').value = account.name || '';
  if(document.activeElement !== $('puser')) $('puser').value = account.username || '';
  $('pemail').value = account.email || '';
  if(document.activeElement !== $('pbio')) $('pbio').value = account.bio || '';
  $('pcurrow').hidden = !account.has_pw;
  $('pwforgot').hidden = !account.has_pw || !account.email;
  if(!account.has_pw) $('pwfgbox').hidden = true;
  $('pwtitle').textContent = account.has_pw ? 'Şifrəni dəyiş' : 'Şifrə qoy';
  $('pusersave').disabled = true;
  $('pinv').checked = account.invites_open !== false; pinvHint();
  $('pavprivacy').value = ['everyone','friends','nobody'].includes(account.avatar_visibility) ? account.avatar_visibility : 'everyone';
  $('plastseen').value = ['everyone','friends','nobody'].includes(account.last_seen_visibility) ? account.last_seen_visibility : 'everyone';
  if(typeof syncSave === 'function') syncSave();
}
function openProfile(){
  if(!account) return;
  pmsg(''); hint('puserhint', 'Hərf, rəqəm, nöqtə və _ (3-20 simvol). Hər kəsin adı fərqlidir.');
  hint('pemailhint', ''); $('pecoderow').hidden = true; $('pecode').value = '';
  $('delbox').hidden = true; $('delopen').hidden = false;
  $('pcur').value = ''; $('pnew').value = '';
  fillProfile();
  $('profile').hidden = false; $('profile').scrollTop = 0;
  loadProfile();
}
$('editname').onclick = openProfile;
$('mav').onclick = openProfile;
$('mav').addEventListener('keydown', e => { if(e.key === 'Enter' || e.key === ' '){ e.preventDefault(); openProfile(); } });
$('pback').onclick = () => { $('profile').hidden = true; };
$('profilesettings').onclick = () => { const b = document.querySelector('#tabbar [data-go="settings"]'); if(b) b.click(); };
$('settingsback').onclick = () => { const b = document.querySelector('#tabbar [data-go="profile"]'); if(b) b.click(); };

// Profil şəklini kəsmək/redaktə etmək: sürüşdürmə və yaxınlaşdırma
let cropImage = null, cropObjectUrl = '', cropScale = 1, cropBase = 1, cropX = 0, cropY = 0, cropDrag = null;
const cropCanvas = $('cropcanvas'), cropCtx = cropCanvas.getContext('2d');
function drawCrop(){
  if(!cropImage) return;
  const W=cropCanvas.width,H=cropCanvas.height;
  cropCtx.clearRect(0,0,W,H); cropCtx.fillStyle='#24132e'; cropCtx.fillRect(0,0,W,H);
  const w=cropImage.naturalWidth,h=cropImage.naturalHeight;
  const scale=cropBase*cropScale, dw=w*scale, dh=h*scale;
  cropCtx.save(); cropCtx.beginPath(); cropCtx.rect(0,0,W,H); cropCtx.clip();
  cropCtx.drawImage(cropImage,(W-dw)/2+cropX,(H-dh)/2+cropY,dw,dh);
  cropCtx.restore();
}
function closeCrop(){ $('avatarcrop').hidden=true; if(cropObjectUrl) URL.revokeObjectURL(cropObjectUrl); cropObjectUrl=''; cropImage=null; cropDrag=null; }
$('cropcancel').onclick=closeCrop;
$('cropzoom').oninput=()=>{cropScale=Number($('cropzoom').value);$('cropzoomval').textContent=cropScale.toFixed(1)+'×';drawCrop();};
cropCanvas.addEventListener('pointerdown',e=>{if(!cropImage)return;cropDrag={x:e.clientX,y:e.clientY,ox:cropX,oy:cropY};cropCanvas.setPointerCapture(e.pointerId);cropCanvas.style.cursor='grabbing';});
cropCanvas.addEventListener('pointermove',e=>{if(!cropDrag)return;const r=cropCanvas.getBoundingClientRect(),k=cropCanvas.width/r.width;cropX=cropDrag.ox+(e.clientX-cropDrag.x)*k;cropY=cropDrag.oy+(e.clientY-cropDrag.y)*k;drawCrop();});
function stopCropDrag(){cropDrag=null;cropCanvas.style.cursor='grab';}
cropCanvas.addEventListener('pointerup',stopCropDrag);cropCanvas.addEventListener('pointercancel',stopCropDrag);
function imageForCrop(file){return new Promise((resolve,reject)=>{const u=URL.createObjectURL(file),im=new Image();im.onload=()=>resolve({u,im});im.onerror=()=>{URL.revokeObjectURL(u);reject(new Error('img'));};im.src=u;});}
function closePavMenu(){
  $('pavmenu').hidden = true;
  $('pavEditWrap').classList.remove('editing');
  $('pav').setAttribute('aria-expanded','false');
  $('pavmenuopen').setAttribute('aria-expanded','false');
}
$('pav').onclick = () => {
  const wrap = $('pavEditWrap');
  const show = !wrap.classList.contains('editing');
  wrap.classList.toggle('editing', show);
  $('pav').setAttribute('aria-expanded', String(show));
  if(!show){ $('pavmenu').hidden = true; $('pavmenuopen').setAttribute('aria-expanded','false'); }
};
$('pavmenuopen').onclick = e => {
  e.stopPropagation();
  const open = $('pavmenu').hidden;
  $('pavEditWrap').classList.add('editing');
  $('pavmenu').hidden = !open;
  $('pavmenuopen').setAttribute('aria-expanded', String(open));
  $('pav').setAttribute('aria-expanded', String(open));
};
$('pavpick').onclick = () => { closePavMenu(); $('pfile').click(); };
document.addEventListener('click', e => { if(!e.target.closest('#pavEditWrap')) closePavMenu(); });
document.addEventListener('keydown', e => { if(e.key === 'Escape') closePavMenu(); });
$('pfile').onchange = async () => {
  const f = $('pfile').files[0]; $('pfile').value = '';
  if(!f) return;
  if(!f.type || !f.type.startsWith('image/')){pmsg('Şəkil faylı seç');return;}
  try{
    const loaded=await imageForCrop(f); cropObjectUrl=loaded.u; cropImage=loaded.im;
    const w=cropImage.naturalWidth,h=cropImage.naturalHeight;
    if(!w||!h) throw new Error('img');
    cropBase=320/Math.max(w,h);cropScale=1;cropX=0;cropY=0;$('cropzoom').value='1';$('cropzoomval').textContent='1.0×';
    $('avatarcrop').hidden=false;drawCrop();
  }catch(e){pmsg('Şəkil açıla bilmədi, başqa şəkil seç');}
};
$('cropapply').onclick = async () => {
  if(!cropImage)return;
  const out=document.createElement('canvas');out.width=256;out.height=256;
  const ctx=out.getContext('2d');ctx.fillStyle='#fff';ctx.fillRect(0,0,256,256);
  ctx.drawImage(cropCanvas,0,0,320,320,0,0,256,256);
  const image=out.toDataURL('image/jpeg',.86);closeCrop();pmsg('Şəkil yüklənir...',true);
  try{
    const r=await api('/api/avatar',{method:'POST',body:JSON.stringify({image})});
    if(!r.ok)throw new Error('av');applyProfile(await r.json());pmsg('Şəkil yeniləndi',true);
  }catch(e){pmsg('Şəkil yüklənmədi, başqa şəkil seç');}
};
$('pavdel').onclick = async () => {
  try{
    const r = await api('/api/avatar/remove', {method: 'POST'});
    if(!r.ok) throw new Error('av');
    applyProfile(await r.json()); closePavMenu(); pmsg('Şəkil silindi', true);
  }catch(e){ closePavMenu(); pmsg('Şəkil silinmədi, yenidən cəhd et'); }
};

// ad
async function saveName(){
  const n = $('pname').value.replace(/\s+/g, ' ').trim().slice(0, 20);
  if(!n){ pmsg('Ad boş ola bilməz'); return false; }
  try{
    const r = await api('/api/name', {method: 'POST', body: JSON.stringify({name: n})});
    if(!r.ok) throw new Error('name');
    applyProfile({name: (await r.json()).name}); pmsg('Ad yadda saxlandı', true); return true;
  }catch(e){ pmsg('Ad dəyişmədi, yenidən cəhd et'); return false; }
}
$('pnamesave').onclick = saveName;
$('pname').addEventListener('keydown', e => { if(e.key === 'Enter') saveAll(); });

// istifadəçi adı (canlı yoxlama)
let uTimer = null, uSeq = 0;
$('puser').addEventListener('input', () => {
  const el = $('puser'), v = el.value.toLowerCase().replace(/[^a-z0-9._]/g, '');
  if(v !== el.value) el.value = v;
  $('pusersave').disabled = true;
  clearTimeout(uTimer);
  if(v === (account.username || '')){ hint('puserhint', 'Hazırkı istifadəçi adın', ''); return; }
  if(!/^[a-z0-9][a-z0-9._]{1,18}[a-z0-9]$/.test(v) || v.includes('..')){
    hint('puserhint', '3-20 simvol: hərf, rəqəm, nöqtə, _ (əvvəl və sonda nöqtə/_ olmasın)', 'err'); return;
  }
  hint('puserhint', 'Yoxlanılır...', '');
  const my = ++uSeq;
  uTimer = setTimeout(async () => {
    try{
      const r = await api('/api/username/check?u=' + encodeURIComponent(v));
      const d = await r.json();
      if(my !== uSeq) return;
      if(d.ok){ hint('puserhint', '@' + v + ' boşdur', 'ok'); $('pusersave').disabled = false; }
      else hint('puserhint', d.reason === 'taken' ? 'Bu ad artıq götürülüb' : 'Format düzgün deyil', 'err');
    }catch(e){ if(my === uSeq) hint('puserhint', 'Yoxlamaq alınmadı', 'err'); }
  }, 350);
});
$('pusersave').onclick = async () => {
  const v = $('puser').value;
  try{
    const r = await api('/api/profile/username', {method: 'POST', body: JSON.stringify({username: v})});
    if(r.status === 409){ hint('puserhint', 'Bu ad artıq götürülüb', 'err'); $('pusersave').disabled = true; return false; }
    if(!r.ok) throw new Error('user');
    applyProfile(await r.json()); hint('puserhint', 'İstifadəçi adı dəyişdi', 'ok'); pmsg(''); return true;
  }catch(e){ pmsg('İstifadəçi adı dəyişmədi, yenidən cəhd et'); return false; }
};

// e-poçt (yeni ünvana kod gedir)
$('pemail').addEventListener('input', () => { $('pecoderow').hidden = true; hint('pemailhint', ''); });
$('pemailsend').onclick = async () => {
  const em = $('pemail').value.trim().toLowerCase();
  if(em === (account.email || '')){ hint('pemailhint', 'Bu, hazırkı e-poçtundur', 'err'); return; }
  if(!/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(em)){ hint('pemailhint', 'E-poçt ünvanı düzgün deyil', 'err'); return; }
  const b = $('pemailsend'); b.disabled = true;
  try{
    const r = await api('/api/email/request', {method: 'POST', body: JSON.stringify({email: em})});
    if(r.ok){ hint('pemailhint', 'Yeni ünvana kod göndərdik. Kodu yaz.', 'ok'); $('pecoderow').hidden = false; $('pecode').focus(); }
    else if(r.status === 409) hint('pemailhint', 'Bu e-poçt başqa hesabda istifadə olunur', 'err');
    else hint('pemailhint', mailErr(r), 'err');
  }catch(e){ hint('pemailhint', 'Əlaqə problemi, yenidən cəhd et', 'err'); }
  finally{ b.disabled = false; }
};
$('pemailok').onclick = async () => {
  const em = $('pemail').value.trim().toLowerCase(), code = $('pecode').value.trim();
  if(!/^\d{6}$/.test(code)){ hint('pemailhint', '6 rəqəmli kodu yaz', 'err'); return; }
  try{
    const r = await api('/api/email/confirm', {method: 'POST', body: JSON.stringify({email: em, code})});
    if(r.ok){ applyProfile(await r.json()); $('pecoderow').hidden = true; $('pecode').value = ''; hint('pemailhint', 'E-poçt dəyişdi', 'ok'); }
    else if(r.status === 422) hint('pemailhint', 'Kod səhvdir və ya vaxtı bitib', 'err');
    else if(r.status === 409) hint('pemailhint', 'Bu e-poçt başqa hesabda istifadə olunur', 'err');
    else hint('pemailhint', 'E-poçt dəyişmədi, yenidən cəhd et', 'err');
  }catch(e){ hint('pemailhint', 'Əlaqə problemi, yenidən cəhd et', 'err'); }
};

// şifrə
$('pwsave').onclick = async () => {
  const cur = $('pcur').value, nw = $('pnew').value, nw2 = $('pnew2').value;
  if(account.has_pw && !cur){ pmsg('Mövcud şifrəni yaz'); return; }
  if(nw.length < 6){ pmsg('Yeni şifrə ən azı 6 simvol olmalıdır'); return; }
  if(nw !== nw2){ pmsg('Yeni şifrələr uyğun gəlmir'); return; }
  if(account.has_pw && nw === cur){ pmsg('Yeni şifrə mövcud şifrə ilə eyni ola bilməz'); return; }
  const b = $('pwsave'); b.disabled = true;
  try{
    const r = await api('/api/password', {method: 'POST', body: JSON.stringify({current: cur, password: nw})});
    if(r.ok){ $('pcur').value = ''; $('pnew').value = ''; $('pnew2').value = ''; $('pwfgbox').hidden = true; account.has_pw = true; fillProfile(); pmsg('Şifrə dəyişdi. Digər cihazlardan çıxarıldın.', true); }
    else if(r.status === 403) pmsg('Mövcud şifrə səhvdir');
    else if(r.status === 429) pmsg('Çox cəhd olundu, bir az sonra yenidən yoxla');
    else pmsg('Şifrə dəyişmədi, yenidən cəhd et');
  }catch(e){ pmsg('Əlaqə problemi, yenidən cəhd et'); }
  finally{ b.disabled = false; }
};
// şifrəmi unutdum: hesabın e-poçtuna kod gedir, kodla yeni şifrə qoyulur (bu cihazdan çıxmırsan)
async function pwSendCode(){
  const b = $('pwforgot'), rs = $('pfresend'); b.disabled = true; rs.disabled = true; pmsg('');
  try{
    const r = await api('/api/password/forgot', {method: 'POST', body: '{}'});
    if(r.ok){ $('pwfgbox').hidden = false; hint('pwfghint', 'E-poçtuna kod göndərdik. Kodu və yeni şifrəni yaz.', 'ok'); $('pfcode').focus(); }
    else if(r.status === 429) hint('pwfghint', 'Kod 1 dəqiqədən bir göndərilə bilər, bir az gözlə', 'err');
    else if(r.status === 503) hint('pwfghint', 'E-poçt xidməti hələ qurulmayıb, Google ilə daxil ol', 'err');
    else { $('pwfgbox').hidden = false; hint('pwfghint', 'Kod göndərilmədi, bir az sonra yenidən yoxla', 'err'); }
  }catch(e){ $('pwfgbox').hidden = false; hint('pwfghint', 'Əlaqə problemi, yenidən cəhd et', 'err'); }
  finally{ b.disabled = false; rs.disabled = false; }
}
$('pwforgot').onclick = pwSendCode;
$('pfresend').onclick = pwSendCode;
$('pfsave').onclick = async () => {
  const code = $('pfcode').value.trim(), nw = $('pfnew').value, nw2 = $('pfnew2').value;
  if(!/^\d{6}$/.test(code)){ hint('pwfghint', '6 rəqəmli kodu yaz', 'err'); return; }
  if(nw.length < 6){ hint('pwfghint', 'Yeni şifrə ən azı 6 simvol olmalıdır', 'err'); return; }
  if(nw !== nw2){ hint('pwfghint', 'Yeni şifrələr uyğun gəlmir', 'err'); return; }
  const b = $('pfsave'); b.disabled = true;
  try{
    const r = await api('/api/password/reset', {method: 'POST', body: JSON.stringify({code, password: nw})});
    if(r.ok){
      ['pfcode', 'pfnew', 'pfnew2', 'pcur', 'pnew', 'pnew2'].forEach(id => { $(id).value = ''; });
      $('pwfgbox').hidden = true; hint('pwfghint', '');
      account.has_pw = true; fillProfile(); pmsg('Şifrə yeniləndi. Digər cihazlardan çıxarıldın.', true);
    }
    else if(r.status === 422) hint('pwfghint', 'Kod səhvdir və ya vaxtı bitib, yenidən göndər', 'err');
    else if(r.status === 429) hint('pwfghint', 'Çox cəhd olundu, bir az sonra yenidən yoxla', 'err');
    else hint('pwfghint', 'Şifrə dəyişmədi, yenidən cəhd et', 'err');
  }catch(e){ hint('pwfghint', 'Əlaqə problemi, yenidən cəhd et', 'err'); }
  finally{ b.disabled = false; }
};
// bütün cihazlardan çıx
$('logoutall').onclick = async () => {
  if(!(await wtConfirm('Bütün cihazlardan çıxılsın? Bu cihaz da daxil.', {ok: 'Çıx'}))) return;
  try{ await api('/api/logout/all', {method: 'POST'}); }catch(e){}
  forgetLogin();
};

// hesabı sil
$('delopen').onclick = () => {
  $('delbox').hidden = false; $('delopen').hidden = true;
  $('delpwrow').hidden = !account.has_pw; $('delcfrow').hidden = !!account.has_pw;
  $('delpw').value = ''; $('delcf').value = '';
};
$('delcancel').onclick = () => { $('delbox').hidden = true; $('delopen').hidden = false; pmsg(''); };
$('delgo').onclick = async () => {
  const body = account.has_pw ? {password: $('delpw').value} : {confirm: $('delcf').value};
  if(account.has_pw && !body.password){ pmsg('Təsdiq üçün şifrəni yaz'); return; }
  if(!account.has_pw && !body.confirm){ pmsg('Təsdiq üçün SİL yaz'); return; }
  if(!(await wtConfirm('Hesab həmişəlik silinsin?', {ok: 'Həmişəlik sil', danger: true}))) return;
  const b = $('delgo'); b.disabled = true;
  try{
    const r = await api('/api/account/delete', {method: 'POST', body: JSON.stringify(body)});
    if(r.ok){ forgetLogin(); loginMsg('Hesabın silindi.'); return; }
    pmsg(r.status === 403 ? (account.has_pw ? 'Şifrə səhvdir' : 'Təsdiq sözü səhvdir') : r.status === 429 ? 'Çox cəhd olundu, bir az sonra yenidən yoxla' : 'Hesab silinmədi, yenidən cəhd et');
  }catch(e){ pmsg('Əlaqə problemi, yenidən cəhd et'); }
  finally{ b.disabled = false; }
};

$('logout').onclick = async () => { try{ await api('/api/logout', {method: 'POST'}); }catch(e){} forgetLogin(); };
$('clearhist').onclick = async () => {
  if(!(await wtConfirm('Tarixçə silinsin?', {ok: 'Sil', danger: true}))) return;
  try{ await api('/api/history/clear', {method: 'POST'}); }catch(e){}
  loadHistory();
};

/* =====================================================================
   Rollar, stiker/GIF, mesaj tarixçəsi, statistika, planlı seanslar, bio, PWA
   ===================================================================== */

/* ---------- Azərbaycanca gün/ay adları (brauzerin 'az' dili olmasa da işləsin) ---------- */
const AZ_WD = ['Baz', 'B.e', 'Ç.a', 'Çər', 'C.a', 'Cüm', 'Şən'];
const AZ_MO = ['yan', 'fev', 'mar', 'apr', 'may', 'iyn', 'iyl', 'avq', 'sen', 'okt', 'noy', 'dek'];
const azTime = d => String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
const azDate = d => d.getDate() + ' ' + AZ_MO[d.getMonth()] + ', ' + azTime(d);

/* ---------- Otaq rolları ---------- */
function applyRoles(d){
  const old = myRole, oldHO = hostOnly;
  if(d.roles){ roleMap = d.roles; if(d.roles[me]) myRole = d.roles[me]; }
  if(typeof d.hostonly === 'boolean') hostOnly = d.hostonly;
  if(typeof d.rv === 'number') rv = d.rv;
  if(old !== myRole) sysMsg(myRole === 'mod' ? 'Sən moderator oldun' : myRole === 'host' ? 'Sən host oldun' : old === 'host' ? 'Host başqasına keçdi' : 'Moderatorluğun alındı');
  if(oldHO !== hostOnly) sysMsg(hostOnly ? 'İndi yalnız host və moderatorlar videonu idarə edir' : 'İndi hamı videonu idarə edə bilər');
  updateRoleUI();
}
function updateRoleUI(){
  $('lock').hidden = myRole === 'viewer';
  document.body.classList.toggle('viewer-lock', !canCtl() && (kind === 'yt' || kind === 'file' || kind === 'web'));
  if(!$('rolebox').hidden) renderRoles();
}
const roleName = r => r === 'host' ? 'Host' : r === 'mod' ? 'Moderator' : 'İzləyici';
function renderRoles(){
  const box = $('rolelist'); box.textContent = '';
  const mine = {id: me, name: myName, username: account && account.username, avatar: account && account.avatar, badge: account && account.badge, me: true};
  [mine].concat(peerList).forEach(p => {
    const r = p.me ? myRole : (roleMap[p.id] || 'viewer'), btns = [];
    if(myRole === 'host' && !p.me){
      const grp = document.createElement('div'); grp.className = 'rbtns';
      grp.appendChild(r === 'mod' ? frBtn('Moderatorluğu al', 'sec', () => setRole(p.id, 'viewer')) : frBtn('Moderator et', 'sec', () => setRole(p.id, 'mod')));
      const hb = frBtn('Host ver', '', () => askHost(p.id, p.name));
      grp.appendChild(hb);
      btns.push(grp);
    }
    box.appendChild(frRow({name: p.name || 'Qonaq', username: p.username || '', avatar: p.avatar || '', badge: p.badge}, roleName(r) + (p.me ? ' · sən' : ''), btns));
  });
  $('honly').checked = hostOnly; $('honly').disabled = myRole !== 'host';
  $('honlyhint').textContent = myRole === 'host'
    ? (hostOnly ? 'Açıq: yalnız sən və moderatorlar videonu idarə edir.' : 'Bağlı: hər kəs videonu idarə edə bilər.')
    : (hostOnly ? 'Açıq: videonu yalnız host və moderatorlar idarə edir.' : 'Bağlı: hər kəs videonu idarə edə bilər.');
}
async function roleCall(path, body){
  const r = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(Object.assign({room, id: me}, body))});
  if(!r.ok) throw new Error('role ' + r.status);
}
async function setRole(target, role){
  try{ await roleCall('/api/role', {target, role}); roleMap[target] = role === 'mod' ? 'mod' : 'viewer'; renderRoles(); }
  catch(e){ sysMsg('Rol dəyişmədi, yenidən cəhd et'); }
}
let hostAsk = null;
function askHost(id, name){ hostAsk = id; $('hbName').textContent = name || 'Qonaq'; $('hostbox').hidden = false; $('hostNo').focus(); }
function closeHostBox(){ $('hostbox').hidden = true; hostAsk = null; }
$('hostNo').onclick = closeHostBox;
$('hostYes').onclick = () => { const id = hostAsk; closeHostBox(); if(id) giveHost(id); };
$('hostbox').addEventListener('click', e => { if(e.target === $('hostbox')) closeHostBox(); });
document.addEventListener('keydown', e => { if(e.key === 'Escape' && !$('hostbox').hidden) closeHostBox(); });
async function giveHost(target){
  try{
    await roleCall('/api/host', {target});
    roleMap[target] = 'host'; roleMap[me] = 'viewer'; myRole = 'viewer';
    sysMsg('Host verildi, indi sən izləyicisən');
    updateRoleUI();
  }catch(e){ sysMsg(e.message === 'role 409' ? 'Həmin şəxs hazırda otaqda deyil' : 'Host verilmədi, yenidən cəhd et'); }
}
$('honly').onchange = async () => {
  const on = $('honly').checked;
  try{ await roleCall('/api/hostonly', {on}); hostOnly = on; updateRoleUI(); }
  catch(e){ $('honly').checked = !on; sysMsg('Rejim dəyişmədi, yenidən cəhd et'); }
};
$('members').onclick = () => { if(!room) return; renderRoles(); $('rolebox').hidden = false; };
$('roleClose').onclick = () => { $('rolebox').hidden = true; };
$('rolebox').addEventListener('click', e => { if(e.target === $('rolebox')) $('rolebox').hidden = true; });

/* ---------- Mesaj tarixçəsi ---------- */
async function loadChatHistory(){
  try{
    const r = await fetch('/api/chat/history?room=' + encodeURIComponent(room) + '&id=' + encodeURIComponent(me), {cache: 'no-store'});
    if(!r.ok) return;
    const list = (await r.json()).msgs || [];
    if(!list.length) return;
    list.forEach(m => {
      if(m.id && msgEls.has(m.id)) return;
      const mine = !!m.mine;
      addMsg(String(m.name || 'Qonaq').slice(0, 20), typeof m.text === 'string' ? m.text : '', mine,
        {id: m.id, reply: m.reply, u: m.u, av: m.av, img: m.img, sens: m.sens, vf: m.vf === 1, rl: m.rl, ts: m.ts, hist: true});
      if(mine && m.id) ownState.set(m.id, 3);
      if(m.id) rxLoad(m.id, m.rx, m.my);
    });
    sysMsg('Əvvəlki söhbət burada bitir');
  }catch(e){}
}

/* ---------- Mesaj reaksiyaları (Apple emoji) ---------- */
const MSG_REACTS = ['\u2764\ufe0f', '😂', '😮', '😭', '👍', '🔥', '👏'];
const rxs = new Map();   // mesaj id -> Map(açar -> emoji); öz reaksiyam 'me' açarı ilə saxlanır
let rxPick = null, rxFor = '';
function rxApply(mid, key, e){
  if(typeof mid !== 'string' || !mid) return;
  let m = rxs.get(mid);
  if(!m){ if(!e) return; m = new Map(); rxs.set(mid, m); }
  if(e) m.set(key, e); else m.delete(key);
  if(!m.size) rxs.delete(mid);
  rxRender(mid);
}
function rxLoad(mid, others, my){
  if(others && typeof others === 'object') Object.keys(others).forEach(k => { if(MSG_REACTS.includes(others[k])) rxApply(mid, k, others[k]); });
  if(MSG_REACTS.includes(my)) rxApply(mid, 'me', my);
}
function rxRender(mid){
  const el = msgEls.get(mid); if(!el) return;
  const old = el.querySelector(':scope > .rxs'); if(old) old.remove();
  const m = rxs.get(mid);
  el.classList.toggle('hasrx', !!(m && m.size));
  if(!m || !m.size) return;
  const cnt = new Map(); m.forEach(e => cnt.set(e, (cnt.get(e) || 0) + 1));
  const mine = m.get('me');
  const row = document.createElement('div'); row.className = 'rxs';
  MSG_REACTS.forEach(e => {
    const n = cnt.get(e); if(!n) return;
    const c = document.createElement('button'); c.type = 'button'; c.className = 'rx' + (mine === e ? ' mine' : '');
    c.appendChild(appleImg(e));
    if(n > 1){ const s = document.createElement('span'); s.textContent = String(n); c.appendChild(s); }
    c.setAttribute('aria-label', e + ' ' + n);
    c.onclick = ev => { ev.stopPropagation(); toggleReact(mid, e); };
    row.appendChild(c);
  });
  el.appendChild(row);
}
function toggleReact(mid, e){
  if(!room || !mid) return;
  const m = rxs.get(mid), ne = (m && m.get('me')) === e ? '' : e;   // eyni emojiyə yenidən toxunmaq reaksiyanı silir
  rxApply(mid, 'me', ne);
  send({t: 'react', id: mid, e: ne});
}
function rxClose(){ if(rxPick) rxPick.hidden = true; rxFor = ''; }
function rxOpen(mid, anchor){
  if(!room || !mid) return;
  if(!rxPick){
    rxPick = document.createElement('div'); rxPick.id = 'rxpick'; rxPick.hidden = true;
    rxPick.setAttribute('role', 'group'); rxPick.setAttribute('aria-label', 'Reaksiya seç');
    MSG_REACTS.forEach(e => {
      const b = document.createElement('button'); b.type = 'button'; b.dataset.e = e; b.setAttribute('aria-label', 'Reaksiya ' + e);
      b.appendChild(appleImg(e));
      b.onclick = () => { const id = rxFor; rxClose(); toggleReact(id, e); };
      rxPick.appendChild(b);
    });
    document.body.appendChild(rxPick);
  }
  if(!rxPick.hidden && rxFor === mid){ rxClose(); return; }
  rxFor = mid;
  const m = rxs.get(mid), mine = m && m.get('me');
  rxPick.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.e === mine));
  rxPick.hidden = false;
  const r = anchor.getBoundingClientRect(), pw = rxPick.offsetWidth, ph = rxPick.offsetHeight;
  let top = r.top - ph - 6;
  if(top < 8) top = Math.min(r.bottom + 6, window.innerHeight - ph - 8);
  let left = anchor.classList.contains('me') ? r.right - pw : r.left;
  left = Math.max(8, Math.min(left, window.innerWidth - pw - 8));
  rxPick.style.top = top + 'px'; rxPick.style.left = left + 'px';
}
document.addEventListener('pointerdown', e => { if(rxPick && !rxPick.hidden && !e.target.closest('#rxpick')) rxClose(); }, true);
function bindHold(d, id){
  let t = null, sx = 0, sy = 0, fired = false;
  const stop = () => { if(t){ clearTimeout(t); t = null; } };
  d.addEventListener('pointerdown', e => {
    if(e.pointerType === 'mouse' && e.button !== 0) return;
    if(e.target.closest('.rp, .q, .rx, a')) return;
    stop(); fired = false; sx = e.clientX; sy = e.clientY;
    t = setTimeout(() => {
      t = null; fired = true;
      try{ window.getSelection().removeAllRanges(); }catch(_){}
      try{ if(navigator.vibrate) navigator.vibrate(12); }catch(_){}
      rxOpen(id, d);
    }, 420);
  });
  d.addEventListener('pointermove', e => { if(t && Math.hypot(e.clientX - sx, e.clientY - sy) > 10) stop(); });
  ['pointerup', 'pointercancel', 'pointerleave'].forEach(n => d.addEventListener(n, stop));
  d.addEventListener('contextmenu', e => e.preventDefault());   // uzun basanda brauzer menyusu çıxmasın
  d.addEventListener('click', e => { if(fired){ fired = false; e.stopPropagation(); e.preventDefault(); } }, true);   // buraxanda şəkil/klik açılmasın
}
$('chat').addEventListener('scroll', rxClose, {passive: true});
document.addEventListener('keydown', e => { if(e.key === 'Escape') rxClose(); });

/* Apple emoji: reaksiyalar hər cihazda eyni görünsün deyə Apple şəkilləri ilə göstərilir.
   Şəkil yüklənməsə (CDN əlçatmazdırsa) adi emoji simvoluna qayıdır. */
const APPLE_EMOJI = 'https://cdn.jsdelivr.net/npm/emoji-datasource-apple@15.0.1/img/apple/64/';
function appleImg(s){
  const cps = Array.from(s).map(c => c.codePointAt(0).toString(16));
  const bare = cps.filter(x => x !== 'fe0f');
  const tries = Array.from(new Set([cps.join('-'), bare.join('-'), bare.concat('fe0f').join('-')]));
  const im = document.createElement('img'); im.alt = s; im.draggable = false; im.decoding = 'async';
  let i = 0;
  im.onerror = () => {
    i++;
    if(i < tries.length) im.src = APPLE_EMOJI + tries[i] + '.png';
    else im.replaceWith(document.createTextNode(s));
  };
  im.src = APPLE_EMOJI + tries[0] + '.png';
  return im;
}
/* ---------- İzləmə statistikası ---------- */
let wAcc = 0, wTog = 0;
const localDay = () => { const d = new Date(), p = n => String(n).padStart(2, '0'); return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()); };
function flushWatch(){
  if(!wAcc || !token) return;
  const body = JSON.stringify({secs: wAcc, tog: wTog, day: localDay()});
  wAcc = 0; wTog = 0;
  api('/api/watch', {method: 'POST', body, keepalive: true}).catch(() => {});
}
setInterval(() => {
  if(!token || !room || (kind !== 'yt' && kind !== 'file') || document.visibilityState !== 'visible' || !isPlaying()) return;
  wAcc++; if(connected && pc > 0) wTog++;
  if(wAcc >= 30) flushWatch();
}, 1000);
document.addEventListener('visibilitychange', () => { if(document.visibilityState === 'hidden') flushWatch(); });
function fmtDur(s){
  const m = Math.round(s / 60);
  if(m < 60) return m + ' dəq';
  const h = Math.floor(m / 60), mm = m % 60;
  return h + ' saat' + (mm && h < 10 ? ' ' + mm + ' dəq' : '');
}
async function loadStats(){
  if(!token) return;
  try{
    const r = await api('/api/watch/stats?today=' + localDay());
    if(r.ok) renderStats(await r.json());
  }catch(e){}
}
function renderStats(d){
  const s = $('wsum'); s.textContent = '';
  const add = (t, em) => { const n = em ? document.createElement('em') : document.createTextNode(t); if(em) n.textContent = t; s.appendChild(n); };
  if(d.month_tog >= 60){ add('Bu ay birlikdə '); add(fmtDur(d.month_tog), 1); add(' izlədiniz'); }
  else if(d.month >= 60){ add('Bu ay '); add(fmtDur(d.month), 1); add(' izlədin'); }
  else add('Bu ay hələ birlikdə izləməmisiniz. İlk filmi başlat!');
  const bars = $('wbars'), lab = $('wlab'); bars.textContent = ''; lab.textContent = '';
  const mx = Math.max(60, ...d.days.map(x => x.s));
  d.days.forEach(x => {
    const c = document.createElement('div'), i = document.createElement('i');
    i.style.height = Math.max(5, Math.round(x.s / mx * 100)) + '%'; i.title = fmtDur(x.s);
    c.appendChild(i); bars.appendChild(c);
    const l = document.createElement('span'); l.textContent = AZ_WD[new Date(x.d + 'T12:00:00').getDay()]; lab.appendChild(l);
  });
  const m = $('wmini'); m.textContent = '';
  const b1 = document.createElement('b'); b1.textContent = fmtDur(d.total);
  const b2 = document.createElement('b'); b2.textContent = fmtDur(d.total_tog);
  const b3 = document.createElement('b'); b3.textContent = d.videos30;
  m.append('Cəmi ', b1, ' izləmisən, ', b2, ' birlikdə. Son 30 gündə ', b3, ' video açmısan.');
}

/* ---------- Planlı seanslar ---------- */
let plansCache = [], planTick = 0, plSel = new Set();
let seenPl = new Set();
try{ seenPl = new Set(JSON.parse(localStorage.getItem('wt_seen_plans') || '[]')); }catch(e){}
function markSeen(k){ seenPl.add(k); try{ localStorage.setItem('wt_seen_plans', JSON.stringify([...seenPl].slice(-200))); }catch(e){} }
const fmtPlan = ts => { const d = new Date(ts * 1000); return AZ_WD[d.getDay()] + ', ' + azDate(d); };
function relTime(ts){
  const d = ts - Date.now() / 1000;
  if(d <= -60) return 'başlayıb';
  if(d < 60) return 'indi';
  if(d < 3600) return Math.round(d / 60) + ' dəq sonra';
  if(d < 86400) return Math.round(d / 3600) + ' saat sonra';
  return Math.round(d / 86400) + ' gün sonra';
}
async function loadPlans(){
  if(!token || $('menu').style.display === 'none' || document.visibilityState !== 'visible') return;
  try{
    const r = await api('/api/plans');
    if(!r.ok) return;
    plansCache = (await r.json()).plans || [];
    renderPlans(); notifyPlans();
  }catch(e){}
}
function renderPlans(){
  const box = $('plans'); box.textContent = '';
  const now = Date.now() / 1000;
  const list = plansCache.filter(p => p.ts > now - 3 * 3600 || p.room);
  $('plnotif').hidden = !('Notification' in window) || Notification.permission !== 'default';
  if(!list.length){ emptyNote(box, '<i class="fa-solid fa-calendar-days" aria-hidden="true"></i>', 'Planlı seans yoxdur', 'Tarix seç, dostlarını dəvət et'); return; }
  list.forEach(p => {
    const d = new Date(p.ts * 1000);
    const el = document.createElement('div'); el.className = 'pl' + (p.ts - now < 900 ? ' soon' : '');
    const h = document.createElement('div'); h.className = 'pl-h';
    const dd = document.createElement('div'); dd.className = 'pl-d';
    const b = document.createElement('b'); b.textContent = d.getDate();
    dd.appendChild(b); dd.appendChild(document.createTextNode(AZ_MO[d.getMonth()]));
    const t = document.createElement('div'); t.className = 'pl-t';
    const pt = document.createElement('span'); pt.dataset.nt = ''; pt.textContent = p.title; t.appendChild(pt);
    const sm = document.createElement('small');
    sm.textContent = fmtPlan(p.ts) + ' · ' + relTime(p.ts) + (p.mine ? '' : ' · @' + p.owner.username);
    t.appendChild(sm); h.appendChild(dd); h.appendChild(t); el.appendChild(h);
    if(p.guests && p.guests.length){
      const g = document.createElement('div'); g.className = 'pl-g'; g.dataset.nt = '';
      g.textContent = p.guests.map(x => x.name + (x.st === 1 ? ' ✓' : x.st === 2 ? ' ✗' : ' …')).join(' · ');
      el.appendChild(g);
    }
    const bt = document.createElement('div'); bt.className = 'pl-b';
    if(p.mine){
      bt.appendChild(frBtn(p.room ? 'Otağa gir' : 'Başlat', '', () => startPlan(p)));
      bt.appendChild(frBtn('Sil', 'sec', () => { wtConfirm('Seans silinsin?', {ok: 'Sil', danger: true}).then(y => { if(y) planAct('/api/plan/delete', {id: p.id}); }); }));
    } else {
      if(p.room) bt.appendChild(frBtn('Qoşul', '', () => { if(!joinBusy) enterRoom(p.room, false); }));
      if(p.my !== 1) bt.appendChild(frBtn('Gəlirəm', '', () => planAct('/api/plan/respond', {id: p.id, going: true})));
      if(p.my !== 2) bt.appendChild(frBtn('Gələ bilmirəm', 'sec', () => planAct('/api/plan/respond', {id: p.id, going: false})));
    }
    el.appendChild(bt); box.appendChild(el);
  });
}
async function planAct(path, body){
  try{ const r = await api(path, {method: 'POST', body: JSON.stringify(body)}); if(!r.ok) throw new Error('plan'); }
  catch(e){ frNote('Alınmadı, yenidən cəhd et'); }
  loadPlans();
}
async function startPlan(p){
  if(joinBusy) return;
  try{
    const r = await api('/api/plan/start', {method: 'POST', body: JSON.stringify({id: p.id, pid: me})});
    if(!r.ok) throw new Error('start');
    enterRoom((await r.json()).room, false);
  }catch(e){ frNote('Seans başlamadı, yenidən cəhd et'); }
}
async function pushNote(t, body){
  try{ if(navigator.vibrate) navigator.vibrate([40, 40, 40]); }catch(e){}
  if(!('Notification' in window) || Notification.permission !== 'granted' || (window.wtNotifOff && window.wtNotifOff())) return;
  try{
    const reg = navigator.serviceWorker && await navigator.serviceWorker.getRegistration();
    if(reg) reg.showNotification(t, {body, icon: '/icon-192.png', badge: '/icon-192.png', tag: 'plan'});
    else new Notification(t, {body});
  }catch(e){}
}
function notifyPlans(){
  const now = Date.now() / 1000;
  plansCache.forEach(p => {
    if(!p.mine && p.my === 0 && !seenPl.has('i' + p.id)){ markSeen('i' + p.id); pushNote(wtT('Yeni seans dəvəti'), p.owner.name + ': ' + p.title + ' · ' + wtT(fmtPlan(p.ts))); }
    const dt = p.ts - now;
    if((p.mine || p.my === 1) && dt < 600 && dt > -600 && !seenPl.has('s' + p.id)){ markSeen('s' + p.id); pushNote(wtT('Seans tezliklə başlayır'), p.title + ' · ' + wtT(relTime(p.ts))); }
  });
}
$('plnotif').onclick = async () => {
  try{ await Notification.requestPermission(); }catch(e){}
  $('plnotif').hidden = !('Notification' in window) || Notification.permission !== 'default';
};
const toLocalInput = d => { const p = n => String(n).padStart(2, '0'); return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + 'T' + p(d.getHours()) + ':' + p(d.getMinutes()); };
function plmsg(t){ $('plmsg').textContent = t || ''; }
$('plnew').onclick = async () => {
  $('pltitle').value = ''; plmsg(''); plSel = new Set();
  const d = new Date(Date.now() + 3600e3); d.setMinutes(0, 0, 0);
  $('pldate').value = toLocalInput(d); $('pldate').min = toLocalInput(new Date());
  $('planbox').hidden = false;
  const box = $('plfr'); box.textContent = '';
  try{
    const r = await api('/api/friends');
    const fr = r.ok ? ((await r.json()).friends || []) : [];
    if(!fr.length){ const e = document.createElement('div'); e.className = 'hint'; e.textContent = 'Hələ dostun yoxdur. Dostsuz da seans qura bilərsən.'; box.appendChild(e); }
    fr.forEach(p => {
      const c = document.createElement('button'); c.type = 'button'; c.className = 'chip'; c.textContent = p.name;
      c.onclick = () => { if(plSel.has(p.username)){ plSel.delete(p.username); c.classList.remove('on'); } else { plSel.add(p.username); c.classList.add('on'); } };
      box.appendChild(c);
    });
  }catch(e){}
  setTimeout(() => $('pltitle').focus(), 60);
};
$('plNo').onclick = () => { $('planbox').hidden = true; };
$('planbox').addEventListener('click', e => { if(e.target === $('planbox')) $('planbox').hidden = true; });
$('plYes').onclick = async () => {
  const title = $('pltitle').value.trim(), v = $('pldate').value;
  if(!title){ plmsg('Seansa ad ver'); return; }
  const ts = v ? new Date(v).getTime() / 1000 : NaN;
  if(!isFinite(ts) || ts < Date.now() / 1000 - 60){ plmsg('Gələcək bir tarix seç'); return; }
  const b = $('plYes'); b.disabled = true; plmsg('');
  try{
    const r = await api('/api/plan/create', {method: 'POST', body: JSON.stringify({title, ts, friends: [...plSel]})});
    if(r.ok){ $('planbox').hidden = true; frNote(plSel.size ? 'Seans planlandı, dostlara bildiriş getdi' : 'Seans planlandı'); loadPlans(); }
    else if(r.status === 429) plmsg('Çox seans yaratdın, bir az sonra yenidən yoxla');
    else plmsg('Seans yaradılmadı, yenidən cəhd et');
  }catch(e){ plmsg('Əlaqə problemi, yenidən cəhd et'); }
  finally{ b.disabled = false; }
};

/* ---------- Profil: qısa məlumat ---------- */
$('pbiosave').onclick = async () => {
  try{
    const r = await api('/api/profile/bio', {method: 'POST', body: JSON.stringify({bio: $('pbio').value})});
    if(!r.ok) throw new Error('bio');
    applyProfile(await r.json()); pmsg('Məlumat yadda saxlandı', true); return true;
  }catch(e){ pmsg('Saxlanmadı, yenidən cəhd et'); return false; }
};
$('pbio').addEventListener('keydown', e => { if(e.key === 'Enter') saveAll(); });
$('puser').addEventListener('keydown', e => { if(e.key === 'Enter') saveAll(); });

/* ---------- Profil: hamısı üçün tək "Saxla" ---------- */
function profDirty(){
  if(!account) return false;
  const n = $('pname').value.replace(/\s+/g, ' ').trim();
  return n !== (account.name || '') || $('puser').value !== (account.username || '') || $('pbio').value !== (account.bio || '');
}
function syncSave(){ const b = $('psaveall'); if(b) b.disabled = !profDirty(); }
async function saveAll(){
  const b = $('psaveall'); if(!account || b.disabled) return;
  if($('puser').value !== (account.username || '') && $('pusersave').disabled){
    pmsg('İstifadəçi adı düzgün deyil, ya da hələ yoxlanılır'); return;
  }
  b.disabled = true;
  let ok = true;
  if($('pname').value.replace(/\s+/g, ' ').trim() !== (account.name || '')) ok = (await saveName()) && ok;
  if($('puser').value !== (account.username || '') && !$('pusersave').disabled) ok = (await $('pusersave').onclick()) && ok;
  if($('pbio').value !== (account.bio || '')) ok = (await $('pbiosave').onclick()) && ok;
  if(ok) pmsg('Yadda saxlandı', true);
  syncSave();
}
$('psaveall').onclick = saveAll;
['pname', 'puser', 'pbio'].forEach(id => $(id).addEventListener('input', syncSave));

/* ---------- PWA: quraşdırma və service worker ---------- */
let deferredInstall = null;
const isStandalone = () => (window.matchMedia && matchMedia('(display-mode: standalone)').matches) || navigator.standalone === true;
function showInstall(){
  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent) && !isStandalone();
  $('pwacard').hidden = isStandalone() || !(deferredInstall || ios);
  $('pwainst').hidden = !deferredInstall;
  $('pwahint').textContent = deferredInstall ? 'Ana ekrandan bir toxunuşla aç, tam ekranda işləyir.' : 'iPhone-da: Paylaş düyməsinə bas, sonra "Ana ekrana əlavə et" seç.';
}
window.addEventListener('beforeinstallprompt', e => { e.preventDefault(); deferredInstall = e; showInstall(); });
window.addEventListener('appinstalled', () => { deferredInstall = null; showInstall(); });
$('pwainst').onclick = async () => {
  if(!deferredInstall) return;
  deferredInstall.prompt();
  try{ await deferredInstall.userChoice; }catch(e){}
  deferredInstall = null; showInstall();
};
if('serviceWorker' in navigator) window.addEventListener('load', () => { navigator.serviceWorker.register('/sw.js').catch(() => {}); });
document.addEventListener('keydown', e => {
  if(e.key !== 'Escape') return;
  if(!$('rolebox').hidden) $('rolebox').hidden = true;
  if(!$('planbox').hidden) $('planbox').hidden = true;
});

let lastFr = '', lastInv = '';
/* köhnə "yüngül rejim" qalıqlarını təmizlə: effektlər həmişə açıqdır */
try{ localStorage.removeItem('wt_lite'); }catch(e){}
document.body.classList.remove('lite');

async function boot(){
  if(token){
    try{
      const r = await api('/api/me');
      if(r.ok){ account = await r.json(); afterLogin(false); return; }
    }catch(e){}
  }
  showLogin();
}
boot();
