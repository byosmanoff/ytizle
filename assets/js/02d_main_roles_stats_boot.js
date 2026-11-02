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
  pushSync(true);
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

/* ---------- Bəyəndiklərim ---------- */
let FAVS = new Map();
function favButton(url, title){
  const b = document.createElement('button'); b.type = 'button'; b.className = 'favbtn' + (FAVS.has(url) ? ' on' : '');
  b.dataset.url = url; b.setAttribute('aria-label', 'Bəyən');
  b.innerHTML = '<i class="fa-' + (FAVS.has(url) ? 'solid' : 'regular') + ' fa-heart" aria-hidden="true"></i>';
  b.onclick = e => { e.preventDefault(); e.stopPropagation(); toggleFav(url, title); };
  return b;
}
async function toggleFav(url, title){
  if(!token){ return; }
  const on = FAVS.has(url);
  try{
    const r = await api(on ? '/api/fav/remove' : '/api/fav/add', {method: 'POST', body: JSON.stringify({url, title: title || ''})});
    if(!r.ok){ const d = await r.json().catch(() => ({})); if(d.error) alert(window.wtT ? window.wtT(d.error) : d.error); return; }
  }catch(e){ return; }
  await loadFavs();
}
function paintFavButtons(){
  document.querySelectorAll('.favbtn[data-url]').forEach(b => {
    const on = FAVS.has(b.dataset.url);
    b.classList.toggle('on', on);
    b.innerHTML = '<i class="fa-' + (on ? 'solid' : 'regular') + ' fa-heart" aria-hidden="true"></i>';
  });
  paintRoomFav();
}
function paintRoomFav(){
  const b = $('favroom'); if(!b) return;
  const on = !!curFavUrl && FAVS.has(curFavUrl);
  b.innerHTML = '<i class="fa-' + (on ? 'solid' : 'regular') + ' fa-heart" aria-hidden="true"></i>';
  b.style.color = on ? '#ff5c8a' : '';
}
$('favroom').onclick = () => { if(curFavUrl) toggleFav(curFavUrl, ''); };
async function loadFavs(){
  if(!token) return;
  try{
    const r = await api('/api/favs'); if(!r.ok) return;
    const items = (await r.json()).items || [];
    FAVS = new Map(items.map(x => [x.url, x]));
    const box = $('favList'); box.textContent = '';
    if(!items.length) emptyNote(box, '<i class="fa-solid fa-heart" aria-hidden="true"></i>', 'Hələ bəyəndiyin yoxdur', 'Videonun üstündəki ürəyə bas');
    items.forEach(x => {
      const row = document.createElement('div'); row.className = 'favrow';
      if(x.thumb && /^https:\/\/i\.ytimg\.com\//.test(x.thumb)){ const im = document.createElement('img'); im.src = x.thumb; im.loading = 'lazy'; im.alt = ''; row.appendChild(im); }
      else { const ph = document.createElement('div'); ph.className = 'ph'; row.appendChild(ph); }
      const a = document.createElement('a'); a.textContent = x.title || x.url; a.target = '_blank'; a.rel = 'noopener noreferrer';
      if(/^https?:\/\//i.test(x.url)) a.href = x.url;
      row.appendChild(a); row.appendChild(favButton(x.url, x.title)); box.appendChild(row);
    });
    paintFavButtons();
  }catch(e){}
}

/* ---------- Reytinq ---------- */
const GAME_AZ = {domino: 'Domino', chess: 'Şahmat', durak: 'Durak', mafia: 'Mafia', uno: 'Uno', all: 'Hamısı'};
let lbGame = 'all', lbPer = 'week';
function Lx(az, tr, en){ const l = window.wtLang ? window.wtLang() : 'az'; return l === 'tr' ? tr : l === 'en' ? en : az; }
const GN = {domino: ['Domino', 'Domino', 'Domino'], chess: ['Şahmat', 'Satranç', 'Chess'], durak: ['Durak', 'Durak', 'Durak'], mafia: ['Mafia', 'Mafya', 'Mafia'], uno: ['Uno', 'Uno', 'Uno']};
function gname(g){ const a = GN[g]; return a ? Lx(a[0], a[1], a[2]) : g; }
function segBind(id, key, fn){
  $(id).addEventListener('click', e => {
    const b = e.target.closest('button'); if(!b) return;
    [].forEach.call($(id).children, x => x.classList.toggle('on', x === b));
    fn(b.dataset[key]);
  });
}
segBind('lbGame', 'g', v => { lbGame = v; loadBoard(); });
segBind('lbPer', 'p', v => { lbPer = v; loadBoard(); });
function leftTxt(sec){
  sec = Math.max(0, Math.floor(sec));
  const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60);
  const u = Lx(['gün', 'saat', 'dəq'], ['gün', 'sa', 'dk'], ['d', 'h', 'min']);
  return d ? d + ' ' + u[0] + ' ' + h + ' ' + u[1] : h ? h + ' ' + u[1] + ' ' + m + ' ' + u[2] : m + ' ' + u[2];
}
function lbRow(x, big){
  const r = document.createElement('div'); r.className = 'lbrow' + (x.me ? ' me' : '');
  const rk = document.createElement('div'); rk.className = 'rk'; rk.textContent = x.rank <= 3 ? ['🥇', '🥈', '🥉'][x.rank - 1] : x.rank;
  const av = document.createElement('div'); av.className = 'av'; setAvatar(av, x.avatar, x.name);
  const nm = document.createElement('div'); nm.className = 'nm';
  const nn = document.createElement('span'); nn.dataset.nt = ''; nn.textContent = x.name; nm.appendChild(nn);
  const sm = document.createElement('small'); sm.dataset.nt = ''; sm.textContent = '@' + x.username + ' · ' + Lx(x.w + 'Q ' + x.d + 'B ' + x.l + 'M', x.w + 'G ' + x.d + 'B ' + x.l + 'M', x.w + 'W ' + x.d + 'D ' + x.l + 'L'); nm.appendChild(sm);
  const pt = document.createElement('div'); pt.className = 'pt'; pt.textContent = x.pts;
  r.appendChild(rk); r.appendChild(av); r.appendChild(nm); r.appendChild(pt);
  return r;
}
async function loadBoard(){
  if(!token) return;
  try{
    const r = await api('/api/leaderboard?game=' + lbGame + '&period=' + lbPer); if(!r.ok) return;
    const d = await r.json(), box = $('lbList'); box.textContent = '';
    if(!d.top.length) emptyNote(box, '<i class="fa-solid fa-trophy" aria-hidden="true"></i>', 'Hələ nəticə yoxdur', 'Dostunla oyna, ilk xalları sən qazan');
    d.top.forEach(x => box.appendChild(lbRow(x)));
    const lt = leftTxt(d.week_ends - d.now);
    const note = [];
    if(lbPer === 'week') note.push(Lx('Həftə sıfırlanır: ' + lt + ' sonra', 'Hafta sıfırlanır: ' + lt + ' sonra', 'Week resets in ' + lt));
    if(d.me && !d.top.some(x => x.me)) note.push(Lx('Sənin yerin: ' + d.me.rank + ' (' + d.me.pts + ' xal)', 'Sıran: ' + d.me.rank + ' (' + d.me.pts + ' puan)', 'Your rank: ' + d.me.rank + ' (' + d.me.pts + ' pts)'));
    const ln = $('lbNote'); ln.dataset.nt = ''; ln.textContent = note.join(' · ');
    const ll = $('lbLast'); ll.dataset.nt = '';
    ll.textContent = d.last_week && d.last_week.length ? Lx('Keçən həftənin liderləri: ', 'Geçen haftanın liderleri: ', 'Last week\'s leaders: ') + d.last_week.map((x, i) => ['🥇', '🥈', '🥉'][i] + ' ' + x.name).join('  ') : '';
  }catch(e){}
}

/* ---------- Turnirlər ---------- */
$('trNew').onclick = () => { $('trForm').hidden = !$('trForm').hidden; };
$('trMake').onclick = async () => {
  const msg = $('trMsg'); msg.textContent = '';
  try{
    const r = await api('/api/tourney/create', {method: 'POST', body: JSON.stringify({title: $('trTitle').value.trim(), game: $('trGame').value, days: +$('trDays').value})});
    const d = await r.json().catch(() => ({}));
    if(!r.ok){ msg.textContent = d.error || 'Alınmadı'; return; }
    $('trTitle').value = ''; $('trForm').hidden = true; loadTourneys();
  }catch(e){ msg.textContent = 'Alınmadı'; }
};
async function trAct(path, id){
  try{
    const r = await api(path, {method: 'POST', body: JSON.stringify({id})});
    const d = await r.json().catch(() => ({})); if(!r.ok) $('trMsg').textContent = d.error || 'Alınmadı';
  }catch(e){}
  loadTourneys();
}
async function trDetail(t, box){
  if(box.firstChild){ box.textContent = ''; return; }
  try{
    const r = await api('/api/tourney?id=' + t.id); if(!r.ok) return;
    const d = await r.json(); box.textContent = '';
    d.top.forEach(x => box.appendChild(lbRow(x)));
  }catch(e){}
}
async function loadTourneys(){
  if(!token) return;
  try{
    const r = await api('/api/tourneys'); if(!r.ok) return;
    const items = (await r.json()).items || [], box = $('trList'); box.textContent = '';
    if(!items.length) emptyNote(box, '<i class="fa-solid fa-medal" aria-hidden="true"></i>', 'Aktiv turnir yoxdur', 'Özün yarat, dostlarını çağır');
    const now = Date.now() / 1000;
    items.forEach(t => {
      const row = document.createElement('div'); row.className = 'trrow';
      const tx = document.createElement('div'); tx.className = 'tx';
      const tn = document.createElement('span'); tn.dataset.nt = ''; tn.textContent = t.title; tx.appendChild(tn);
      const lt = leftTxt(t.t1 - now);
      const sm = document.createElement('small'); sm.dataset.nt = '';
      sm.textContent = gname(t.game) + ' · ' + Lx(t.players + ' nəfər', t.players + ' kişi', t.players + ' players') + ' · ' +
        (t.active ? Lx(lt + ' qalıb', lt + ' kaldı', lt + ' left') : Lx('bitib', 'bitti', 'ended'));
      tx.appendChild(sm); tx.style.cursor = 'pointer';
      const det = document.createElement('div'); det.className = 'trdet';
      tx.onclick = () => trDetail(t, det);
      row.appendChild(tx);
      if(t.active){
        const b = document.createElement('button'); b.type = 'button'; b.className = 'pbtn' + (t.joined ? ' alt' : '');
        b.textContent = t.joined ? 'Turnirdən çıx' : 'Qoşul'; b.onclick = () => trAct(t.joined ? '/api/tourney/leave' : '/api/tourney/join', t.id);
        row.appendChild(b);
      }
      if(t.mine){
        const x = document.createElement('button'); x.type = 'button'; x.className = 'favbtn'; x.setAttribute('aria-label', 'Sil');
        x.innerHTML = '<i class="fa-solid fa-trash" aria-hidden="true"></i>';
        x.onclick = async () => { if(await wtConfirm('Turnir silinsin?', {danger: true, ok: 'Sil'})) trAct('/api/tourney/delete', t.id); };
        row.appendChild(x);
      }
      row.appendChild(det); box.appendChild(row);
    });
  }catch(e){}
}
window.addEventListener('wt:languagechange', () => { if(token){ loadBoard(); loadTourneys(); } });
document.addEventListener('click', function(e){
  if(token || !(window.wtIsGuest && window.wtIsGuest())) return;
  var t = e.target.closest && e.target.closest('#domBot,#domEnter,#chBot,#chEnter,#mafNew,#mafBot,#mafEnter,#durEnter');
  if(!t) return;
  e.preventDefault(); e.stopPropagation();
  window.wtToast && window.wtToast('Oyunlar üçün hesaba daxil ol');
}, true);
/* wtGuestGameGate */

document.querySelectorAll('#tabbar [data-go="games"]').forEach(b => b.addEventListener('click', () => { loadBoard(); loadTourneys(); }));

async function boot(){
  if(token){
    try{
      const r = await api('/api/me');
      if(r.ok){ account = await r.json(); afterLogin(false); return; }
    }catch(e){}
  }
  try{
    if(localStorage.getItem('wt_guest') === '1'){
      account = {name: localStorage.getItem('wt_guest_name') || 'Qonaq', username: '', guest: true};
      document.getElementById('join').style.display = 'none';
      showMenu();
      return;
    }
  }catch(e){}
  showLogin();
}
boot();
