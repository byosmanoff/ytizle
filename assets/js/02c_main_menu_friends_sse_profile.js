/* ---------- Ana menyu ---------- */
function showMenu(){
  initFloaties();
  startInvitePoll();
  $('app').style.display = 'none';
  $('menu').style.display = 'flex';
  if(!token && !account){
    try{
      if(localStorage.getItem('wt_guest') === '1'){
        account = {name: localStorage.getItem('wt_guest_name') || 'Qonaq', username: '', guest: true};
      }
    }catch(e){}
  }
  try{ document.body.classList.toggle('guest-mode', !!(account && account.guest && !token)); }catch(e){}
  renderName();
  lastFr = ''; lastInv = '';
  loadHistory(); loadStats(); showInstall(); loadFavs(); loadBoard(); loadTourneys();
  try{ if(window.wtVerifyDomino) window.wtVerifyDomino(); if(window.wtVerifyChess) window.wtVerifyChess(); }catch(e){}
  $('mquote').textContent = '“' + DAILY[Math.floor(Date.now() / 864e5) % DAILY.length] + '”';
  fetch('/api/stats', {cache: 'no-store'}).then(r => r.json()).then(d => { $('stl').textContent = d.people; }).catch(() => {});
  setTimeout(function(){ try{ window.wtShowGuide(false); }catch(e){} }, 600);
  if(token) setTimeout(function(){ try{ window.wtAskNotif(); }catch(e){} }, 2500);
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
    if(ok) el.appendChild(favButton(x.url, x.title));
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
  var presTxt = p.presence || (p.online ? 'Onlayn' : '');
  if(presTxt){ const ps=document.createElement('small'); ps.className = p.online ? 'presence online' : 'presence'; ps.textContent=presTxt; tx.appendChild(ps); }
  r.appendChild(av); r.appendChild(tx); (btns || []).forEach(x => r.appendChild(x));
  return r;
}
let frNoteT = null;
function frNote(t){ const e = $('menumsg'); e.textContent = t; clearTimeout(frNoteT); frNoteT = setTimeout(() => { e.textContent = ''; }, 3500); }
async function frAct(path, body){
  try{
    const r = await api(path, {method: 'POST', body: JSON.stringify(body)});
    if(r.status === 429) frNote('Çox sorğu göndərdin, bir az gözlə');
    else if(!r.ok) frNote((window.wtErr && window.wtErr(r)) || 'Alınmadı');
  }catch(e){ frNote('Əlaqə problemi, internetini yoxla'); }
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
      dmFrBtn(p),
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
  var st = '';
  try{
    if(document.getElementById('domino') && !document.getElementById('domino').hidden) st = 'Domino oynayır';
    else if(document.getElementById('chess') && !document.getElementById('chess').hidden) st = 'Şahmat oynayır';
    else if(document.getElementById('mafia') && !document.getElementById('mafia').hidden) st = 'Mafia oynayır';
    else if(document.getElementById('app') && document.getElementById('app').style.display !== 'none' && typeof room === 'string' && room) st = 'Birlikdə izləyir';
  }catch(e){}
  try{ await api('/api/presence', {method:'POST', body: JSON.stringify({status: st})}); }catch(e){}
}
setInterval(() => { if(token && !document.hidden) sendPresence(); }, 12000);
document.addEventListener('visibilitychange', () => { if(!document.hidden) sendPresence(); });
window.addEventListener('offline', function(){ try{ window.wtReconnectBanner('İnternet yoxdur...'); }catch(e){} });
window.addEventListener('online', function(){
  try{ window.wtReconnectBanner('İnternet qayıtdı', true); setTimeout(function(){ window.wtReconnectBanner(''); }, 2000); }catch(e){}
  try{ sendPresence(); }catch(e){}
});

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
  $('pbpresence').textContent = p.presence || (p.online ? 'Onlayn' : '');
  $('pbpresence').className = p.online ? 'presence online' : 'presence';
  $('pbpresence').hidden = !($('pbpresence').textContent);
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
    $('pbReport').hidden = false;
    $('pbBlock').textContent = p.blocked ? 'Blokdan çıxar' : 'Blok et';
    $('pbBlock').dataset.blocked = p.blocked ? '1' : '0';
    if(p.blocked_me){ $('pbrel').textContent='Səni bloklayıb'; $('pbAct').hidden=true; }
  }catch(e){}
}
function openPeerCard(p){
  if(!p) return;
  pcUser = p.username || '';
  fillPeerHead(p);
  $('pbrel').textContent = ''; $('pbAct').hidden = true; $('pbBlock').hidden = !pcUser || !token; $('pbReport').hidden = !pcUser || !token;
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
let repCtx = null;
function openReport(c){
  repCtx = c; $('repwho').textContent = '@' + c.username + (c.name ? ' (' + c.name + ')' : '');
  $('repq').textContent = c.kind === 'message' ? (c.msg_text || '') : '';
  $('repdet').value = ''; $('repreason').value = 'spam'; $('repmsg').textContent = ''; $('repmsg').className = '';
  $('repYes').disabled = false; $('repbox').hidden = false;
}
$('pbReport').onclick = () => { if(pcUser) openReport({username: pcUser, name: $('pbname').textContent, kind: 'user'}); };
$('repNo').onclick = () => { $('repbox').hidden = true; };
$('repbox').addEventListener('click', e => { if(e.target === $('repbox')) $('repbox').hidden = true; });
$('repYes').onclick = async () => {
  if(!repCtx) return;
  $('repYes').disabled = true; $('repmsg').className = ''; $('repmsg').textContent = '';
  try{
    const r = await api('/api/report', {method: 'POST', body: JSON.stringify({username: repCtx.username, kind: repCtx.kind,
      reason: $('repreason').value, details: $('repdet').value.trim(), room: room || '', msg_id: repCtx.msg_id || '', msg_text: repCtx.msg_text || ''})});
    if(r.ok){ $('repmsg').textContent = 'Şikayət göndərildi. Təşəkkür edirik!'; setTimeout(() => { $('repbox').hidden = true; }, 1200); return; }
    $('repmsg').className = 'err';
    $('repmsg').textContent = r.status === 429 ? 'Çox şikayət göndərdin, bir az sonra yenidən cəhd et' :
      r.status === 400 ? 'Özünü şikayət edə bilməzsən' : r.status === 404 ? 'İstifadəçi tapılmadı' : 'Göndərilmədi, yenidən cəhd et';
  }catch(e){ $('repmsg').className = 'err'; $('repmsg').textContent = 'Göndərilmədi, yenidən cəhd et'; }
  $('repYes').disabled = false;
};
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
/* ---------- SSE (anlıq hadisələr) + Web Push ---------- */
let sseGen = 0, sseSeq = -1, sseLive = false, sseAbort = null, sseOn = false, lastPushSync = 0;
async function sseLoop(){
  const gen = ++sseGen; let fails = 0;
  while(gen === sseGen && token){
    if(document.visibilityState !== 'visible'){ sseLive = false; await new Promise(r => setTimeout(r, 1500)); continue; }
    const ac = new AbortController(); sseAbort = ac;
    try{
      const r = await fetch('/api/stream?since=' + sseSeq, {cache: 'no-store', signal: ac.signal, headers: {'X-Token': token}});
      if(!r.ok || !r.body) throw new Error('http ' + r.status);
      sseLive = true; fails = 0;
      const rd = r.body.getReader(), dec = new TextDecoder(); let buf = '';
      for(;;){
        const {value, done} = await rd.read();
        if(done) break;
        buf += dec.decode(value, {stream: true});
        let i;
        while((i = buf.indexOf('\n\n')) >= 0){ const blk = buf.slice(0, i); buf = buf.slice(i + 2); sseEvent(blk); }
      }
    }catch(e){ if(document.visibilityState === 'visible') fails++; }
    sseLive = false;
    if(gen !== sseGen) return;
    await new Promise(r => setTimeout(r, fails ? Math.min(30000, 2000 * fails) : 200));
  }
}
function sseEvent(blk){
  let ev = 'message', id = NaN, data = '';
  blk.split('\n').forEach(l => {
    if(l.startsWith('event:')) ev = l.slice(6).trim();
    else if(l.startsWith('id:')) id = parseInt(l.slice(3), 10);
    else if(l.startsWith('data:')) data = l.slice(5).trim();
  });
  if(!isNaN(id)) sseSeq = id;
  if(ev === 'invite' || ev === 'game' || ev === 'friend'){ lastInv = ''; loadInvites(); loadFriends(); }
  if(ev === 'dm'){
    var du = ''; try{ du = (JSON.parse(data || '{}').u) || ''; }catch(e){}
    if(typeof loadDmList === 'function') loadDmList();
    if(typeof DM !== 'undefined' && DM.peer && DM.peer.username === du){ dmPull(false); }
    else if(window.wtToast && (!document.getElementById('dmchat') || document.getElementById('dmchat').hidden)) window.wtToast('Yeni mesaj' + (du ? ': @' + du : ''));
  }
  if(ev === 'call'){ try{ if(typeof dmCallEvent === 'function') dmCallEvent(JSON.parse(data || '{}')); }catch(e){} }
  if(ev === 'invite') window.wtToast && window.wtToast('Yeni otaq dəvəti');
  else if(ev === 'game') window.wtToast && window.wtToast('Oyun dəvəti gəldi');
  else if(ev === 'friend') window.wtToast && window.wtToast('Yeni dostluq sorğusu');
  else if(ev === 'plan') window.wtToast && window.wtToast('Planlı seans yeniləndi');
}
function startStream(){ if(sseOn || !token) return; sseOn = true; sseLoop().finally(() => { sseOn = false; }); }
function stopStream(){ sseGen++; sseLive = false; try{ if(sseAbort) sseAbort.abort(); }catch(e){} }
document.addEventListener('visibilitychange', () => {
  if(document.visibilityState === 'hidden'){ try{ if(sseAbort) sseAbort.abort(); }catch(e){} }
  else if(token){ lastInv = ''; loadInvites(); }
});
const u8FromB64u = x => { x = x.replace(/-/g, '+').replace(/_/g, '/'); x += '='.repeat((4 - x.length % 4) % 4); return Uint8Array.from(atob(x), c => c.charCodeAt(0)); };
const b64uFromBuf = b => btoa(String.fromCharCode.apply(null, new Uint8Array(b))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
async function pushSync(force){
  try{
    if(!token || !('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) return;
    if(!force && Date.now() - lastPushSync < 6 * 3600e3) return;
    const reg = await navigator.serviceWorker.ready;
    let sub = await reg.pushManager.getSubscription();
    const off = Notification.permission !== 'granted' || (window.wtNotifOff && window.wtNotifOff());
    if(off){
      if(sub){ api('/api/push/unsubscribe', {method: 'POST', body: JSON.stringify({endpoint: sub.endpoint})}).catch(() => {}); sub.unsubscribe().catch(() => {}); }
      return;
    }
    const kr = await api('/api/push/key'); if(!kr.ok) return;
    const k = await kr.json(); if(!k.enabled) return;
    const cur = sub && sub.options && sub.options.applicationServerKey;
    if(sub && cur && b64uFromBuf(cur) !== k.key){ await sub.unsubscribe(); sub = null; }   // server açarı dəyişib
    if(!sub) sub = await reg.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: u8FromB64u(k.key)});
    const r = await api('/api/push/subscribe', {method: 'POST', body: JSON.stringify(sub.toJSON())});
    if(r.ok) lastPushSync = Date.now();
  }catch(e){}
}
function startInvitePoll(){ startStream(); pushSync(); loadInvites(); loadFriends(); loadPlans(); if(!invTimer) invTimer = setInterval(() => { if(!sseLive || planTick % 8 === 0) loadInvites(); if(++planTick % 4 === 0) { loadPlans(); loadFriends(); } }, 4000); }
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
    const sp = document.createElement('span'); sp.textContent = i.voice ? 'səni səsli söhbətə dəvət edir' : i.game === 'chess' ? 'səni Şahmat oyununa dəvət edir' : i.game === 'durak' ? 'səni Durak oyununa dəvət edir' : i.game === 'mafia' ? 'səni Mafia oyununa dəvət edir' : i.game === 'uno' ? 'səni Uno oyununa dəvət edir' : 'səni Domino oyununa dəvət edir';
    tx.appendChild(b); tx.appendChild(sp);
    const go = document.createElement('button'); go.className = 'go'; go.textContent = 'Qoşul';
    const no = document.createElement('button'); no.className = 'no'; no.innerHTML = '<i class="fa-solid fa-xmark" aria-hidden="true"></i>'; no.setAttribute('aria-label', 'Rədd et');
    const drop = () => { api('/api/game/invite/dismiss', {method: 'POST', body: JSON.stringify({id: i.id})}).catch(() => {}); c.remove(); };
    go.onclick = () => { drop(); const f = i.game === 'chess' ? window.wtJoinChess : i.game === 'durak' ? window.wtJoinDurak : i.game === 'mafia' ? window.wtJoinMafia : i.game === 'uno' ? window.wtJoinUno : window.wtJoinDomino; if(f) f(i.room); };
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
function pmsg(t, ok){
  const e = $('pmsg'); if(e){ e.textContent = t || ''; e.dataset.ok = ok ? '1' : '0'; }
  const s = $('smsg'); if(s){ s.textContent = t || ''; s.dataset.ok = ok ? '1' : '0'; }
}
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
  pmsg('');
  try{ document.body.classList.toggle('guest-mode', !!(account.guest && !token)); }catch(e){}
  if(account.guest && !token){
    $('profile').hidden = false; $('profile').scrollTop = 0;
    return;
  }
  try{ hint('puserhint', 'Hərf, rəqəm, nöqtə və _ (3-20 simvol). Hər kəsin adı fərqlidir.'); }catch(e){}
  try{ hint('pemailhint', ''); $('pecoderow').hidden = true; $('pecode').value = ''; }catch(e){}
  try{ if($('delbox')) $('delbox').hidden = true; if($('delopen')) $('delopen').hidden = false; }catch(e){}
  try{ if($('pcur')) $('pcur').value = ''; if($('pnew')) $('pnew').value = ''; if($('pnew2')) $('pnew2').value = ''; }catch(e){}
  fillProfile();
  $('profile').hidden = false; $('profile').scrollTop = 0;
  loadProfile();
  try{ loadHistory(); loadFavs(); }catch(e){}
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
    else if(r.status === 400 && ((await r.clone().json().catch(() => ({}))).error || '') === 'disposable') hint('pemailhint', 'Bir dəfəlik e-poçt ünvanları qəbul olunmur', 'err');
    else if(r.status === 400 && ((await r.clone().json().catch(() => ({}))).error || '') === 'domain') hint('pemailhint', 'Bu e-poçt domeni mövcud deyil', 'err');
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
if($('logoutall')) $('logoutall').onclick = async () => {
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

