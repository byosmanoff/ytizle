
/* ================= ŞƏXSİ MESAJLAR (DM) ================= */
var DM = {peer: null, msgs: [], lastId: 0, tT: 0, lT: 0, convs: [], off: false, busy: false, tmp: 0, sig: ''};
function dmFrBtn(p){
  var b = frBtn('<i class="fa-solid fa-comment-dots" aria-hidden="true"></i>', 'dmbtn', function(){ openDm(p); }, 'Mesaj yaz');
  var c = DM.convs.find(function(x){ return x.username === p.username; });
  if(c && c.unread){ var n = document.createElement('span'); n.className = 'dmbadge'; n.textContent = c.unread > 99 ? '99+' : c.unread; b.appendChild(n); }
  return b;
}
function dmTime(ts){ var d = new Date(ts); return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); }
function dmShort(ts){
  var d = new Date(ts), n = new Date();
  if(d.toDateString() === n.toDateString()) return dmTime(ts);
  var y = new Date(n - 864e5); if(d.toDateString() === y.toDateString()) return 'Dünən';
  return String(d.getDate()).padStart(2, '0') + '.' + String(d.getMonth() + 1).padStart(2, '0');
}
function dmDayLabel(ts){
  var d = new Date(ts), n = new Date();
  if(d.toDateString() === n.toDateString()) return 'Bu gün';
  var y = new Date(n - 864e5); if(d.toDateString() === y.toDateString()) return 'Dünən';
  return String(d.getDate()).padStart(2, '0') + '.' + String(d.getMonth() + 1).padStart(2, '0') + '.' + d.getFullYear();
}
async function dmCall(path, o){
  var r = await api(path, o);
  if(r.status === 404){ DM.off = true; throw new Error('off'); }
  var j = null; try{ j = await r.json(); }catch(e){}
  if(!r.ok){ var er = new Error((j && j.error) || 'xəta'); er.status = r.status; throw er; }
  return j || {};
}
/* ---- siyahı ---- */
async function loadDmList(){
  if(!token || DM.off || (account && account.guest)) return;
  try{
    var d = await dmCall('/api/dm/list');
    var sig = JSON.stringify(d.convs || []);
    DM.convs = d.convs || [];
    if(sig !== DM.sig){ DM.sig = sig; renderDmList(); try{ if(typeof lastFr !== 'undefined'){ lastFr = ''; loadFriends(); } }catch(e){} }
    dmDot();
  }catch(e){}
}
function dmDot(){
  var tot = DM.convs.reduce(function(a, c){ return a + (c.unread || 0); }, 0);
  var tab = document.querySelector('#tabbar [data-go="friends"] .tp'); if(!tab) return;
  var dot = document.getElementById('dmNotifDot');
  if(!dot){ dot = document.createElement('span'); dot.id = 'dmNotifDot'; dot.className = 'nav-notif-dot'; dot.setAttribute('aria-label', 'Yeni mesaj'); tab.appendChild(dot); }
  dot.hidden = tot === 0;
  var sb = document.querySelector('#fseg [data-sub="dms"]'); if(sb) sb.classList.toggle('hasdot', tot > 0);
}
function renderDmList(){
  var box = document.getElementById('dmbox'); if(!box) return;
  box.textContent = '';
  var list = DM.convs.filter(function(c){ return c.last; });
  var em = document.getElementById('dmlistempty'); if(em) em.hidden = list.length > 0;
  if(!list.length) return;
  list.forEach(function(c){
    var prev = (c.last.mine ? 'Sən: ' : '') + c.last.text;
    var meta = document.createElement('div'); meta.className = 'dmmeta';
    var t = document.createElement('span'); t.textContent = dmShort(c.last.ts); meta.appendChild(t);
    if(c.unread){ var b = document.createElement('span'); b.className = 'dmbadge'; b.textContent = c.unread > 99 ? '99+' : c.unread; meta.appendChild(b); }
    var row = frRow(c, prev, [meta]);
    row.classList.add('dmr'); row.setAttribute('role', 'button'); row.tabIndex = 0;
    if(c.unread) row.querySelector('.tx span').classList.add('unr');
    row.onclick = function(){ openDm(c); };
    row.onkeydown = function(e){ if(e.key === 'Enter' || e.key === ' '){ e.preventDefault(); openDm(c); } };
    box.appendChild(row);
  });
}
/* ---- söhbət ekranı ---- */
function dmTick(m){
  var i = document.createElement('i'); i.setAttribute('aria-hidden', 'true');
  if(m.pending) i.className = 'tk fa-regular fa-clock';
  else if(m.status === 'seen') i.className = 'tk seen fa-solid fa-check-double';
  else if(m.status === 'delivered') i.className = 'tk fa-solid fa-check-double';
  else i.className = 'tk fa-solid fa-check';
  return i;
}
function dmFillTime(t, m){
  t.textContent = m.failed ? 'Göndərilmədi · toxun, yenidən cəhd et' : dmTime(m.ts);
  if(m.mine && !m.failed) t.appendChild(dmTick(m));
}
function dmBubble(m){
  var kind = m.kind || 'text', d = document.createElement('div');
  d.dataset.id = m.id;
  if(kind === 'call'){
    d.className = 'dmcallrow' + (/^(Cavabsız|Rədd)/.test(m.text) ? ' miss' : '');
    var ic = document.createElement('i'); ic.className = 'fa-solid fa-phone'; ic.setAttribute('aria-hidden', 'true'); d.appendChild(ic);
    d.appendChild(document.createTextNode(m.text + ' · '));
    var ct = document.createElement('time'); ct.textContent = dmTime(m.ts); d.appendChild(ct);
    d.setAttribute('role', 'button'); d.tabIndex = 0;
    d.onclick = function(){ if(DM.peer && typeof startCall === 'function') startCall(DM.peer); };
    return d;
  }
  d.className = 'dmb ' + (m.mine ? 'me' : 'th') + (kind !== 'text' ? ' media k-' + kind : '') + (m.pending ? ' pend' : '') + (m.failed ? ' fail' : '');
  var src = m.url || m.local || '';
  if(kind === 'image'){
    var im = document.createElement('img'); im.className = 'dmimg'; im.alt = 'Şəkil'; im.loading = 'lazy'; im.src = src;
    im.onclick = function(e){ if(m.failed) return; e.stopPropagation(); if(typeof openLightbox === 'function') openLightbox(im.src); };
    d.appendChild(im);
  } else if(kind === 'video'){
    var v = document.createElement('video'); v.className = 'dmvid'; v.controls = true; v.preload = 'metadata'; v.playsInline = true; v.src = src; d.appendChild(v);
  } else if(kind === 'audio'){
    var a = document.createElement('audio'); a.className = 'dmaud'; a.controls = true; a.preload = 'metadata'; a.src = src; d.appendChild(a);
  } else d.appendChild(document.createTextNode(m.text));
  if(m.pending && kind !== 'text'){ var pg = document.createElement('div'); pg.className = 'dmprog'; pg.textContent = (m.prog || 0) + '%'; d.appendChild(pg); }
  var t = document.createElement('time'); dmFillTime(t, m); d.appendChild(t);
  if(m.failed) d.onclick = function(){ dmRetry(m); };
  return d;
}
/* mövcud qabarcığı yerində yenilə (oynayan video/səs kəsilməsin) */
function dmPaint(el, m){
  var kind = m.kind || 'text'; if(kind === 'call') return;
  el.classList.toggle('pend', !!m.pending); el.classList.toggle('fail', !!m.failed);
  el.onclick = m.failed ? function(){ dmRetry(m); } : null;
  var t = el.querySelector('time'); if(t) dmFillTime(t, m);
  var pg = el.querySelector('.dmprog');
  if(m.pending && kind !== 'text'){
    if(!pg){ pg = document.createElement('div'); pg.className = 'dmprog'; el.insertBefore(pg, t); }
    pg.textContent = (m.prog || 0) + '%';
  } else if(pg) pg.remove();
}
function dmRender(keepScroll){
  var box = document.getElementById('dmmsgs');
  var near = box.scrollHeight - box.scrollTop - box.clientHeight < 90;
  var ids = DM.msgs.map(function(m){ return String(m.id); });
  var prev = box._ids || [];
  var inc = prev.length > 0 && ids.length >= prev.length && prev.every(function(x, i){ return ids[i] === x; });
  if(!inc){
    box.textContent = ''; prev = [];
    if(!DM.msgs.length){
      box._ids = [];
      var e = document.createElement('div'); e.className = 'dmempty';
      e.innerHTML = '<i class="fa-solid fa-comments" aria-hidden="true"></i>';
      e.appendChild(document.createTextNode('Hələ mesaj yoxdur. İlk mesajı sən yaz.')); box.appendChild(e); return;
    }
  } else {
    DM.msgs.slice(0, prev.length).forEach(function(m){ var el = box.querySelector('[data-id="' + m.id + '"]'); if(el) dmPaint(el, m); });
  }
  var old = box.querySelector('.dmstat'); if(old) old.remove();
  var day = prev.length ? dmDayLabel(DM.msgs[prev.length - 1].ts) : '';
  for(var i = prev.length; i < DM.msgs.length; i++){
    var m = DM.msgs[i], dl = dmDayLabel(m.ts);
    if(dl !== day){ day = dl; var s = document.createElement('div'); s.className = 'dmday'; s.textContent = dl; box.appendChild(s); }
    box.appendChild(dmBubble(m));
  }
  box._ids = ids;
  var last = DM.msgs[DM.msgs.length - 1];
  if(last && last.mine && !last.pending && !last.failed && (last.kind || 'text') !== 'call'){
    var st = document.createElement('div'); st.className = 'dmstat';
    st.textContent = last.status === 'seen' ? 'Görüldü' : (last.status === 'delivered' ? 'Çatdı' : 'Göndərildi');
    box.appendChild(st);
  }
  if(!keepScroll || near) box.scrollTop = box.scrollHeight;
}
async function openDm(p){
  if(!token) return;
  if(account && account.guest){ frNote('Mesaj yazmaq üçün hesabla daxil ol.'); return; }
  DM.peer = {username: p.username, name: p.name, avatar: p.avatar, badge: p.badge};
  DM.msgs = []; DM.lastId = 0;
  var av = document.getElementById('dmav'); av.style.backgroundImage = ''; av.textContent = initial(p.name);
  if(p.avatar && AV_RE.test(p.avatar)){ av.style.backgroundImage = 'url("' + p.avatar + '")'; av.textContent = ''; }
  document.getElementById('dmname').textContent = p.name;
  document.getElementById('dmuser').textContent = '@' + p.username;
  document.getElementById('dmerr').textContent = '';
  document.getElementById('dmin').value = ''; dmGrow();
  document.getElementById('dmchat').hidden = false;
  dmRender();
  await dmPull(true);
  clearInterval(DM.tT); DM.tT = setInterval(function(){ if(!document.hidden) dmPull(false); }, 2500);
  setTimeout(function(){ try{ document.getElementById('dmin').focus({preventScroll: true}); }catch(e){} }, 150);
}
function closeDm(){
  dmRecStop(false);
  document.getElementById('dmchat').hidden = true;
  clearInterval(DM.tT); DM.tT = 0; DM.peer = null;
  loadDmList();
}
async function dmPull(first){
  var peer = DM.peer; if(!peer) return;
  try{
    var d = await dmCall('/api/dm/thread?u=' + encodeURIComponent(peer.username) + '&after=' + DM.lastId);
    if(!DM.peer || DM.peer.username !== peer.username) return;
    var add = (d.msgs || []).filter(function(m){ return m.id > DM.lastId; }), ch = false;
    add.forEach(function(m){
      // geri qayıdan mətn mesajı artıq yerli (gözləyən) nüsxə ilə təkrarlanmasın
      if(m.mine && (m.kind || 'text') === 'text'){ var k = DM.msgs.findIndex(function(x){ return x.pending && x.text === m.text; }); if(k >= 0) DM.msgs.splice(k, 1); }
      DM.msgs.push(m); DM.lastId = Math.max(DM.lastId, m.id); ch = true;
    });
    // çatdı / görüldü: server "bu id-yə qədər hamısı" deyir
    var sU = d.seen_upto | 0, dU = d.deliv_upto | 0;
    DM.msgs.forEach(function(m){
      if(m.mine && typeof m.id === 'number' && !m.pending){
        var st = m.id <= sU ? 'seen' : (m.id <= dU ? 'delivered' : 'sent');
        if(m.status !== st){ m.status = st; ch = true; }
      }
    });
    if(ch || first) dmRender(!first);
    document.getElementById('dmerr').textContent = '';
  }catch(e){
    if(e.message === 'off'){ document.getElementById('dmerr').textContent = 'Mesaj xidməti serverdə hələ qoşulmayıb.'; clearInterval(DM.tT); }
    else if(first) document.getElementById('dmerr').textContent = e.message === 'xəta' ? 'Mesajlar yüklənmədi.' : e.message;
  }
}
function dmGrow(){ var t = document.getElementById('dmin'); t.style.height = 'auto'; t.style.height = Math.min(120, t.scrollHeight) + 'px'; document.getElementById('dmform').classList.toggle('has-text', !!t.value.trim()); }
async function dmSend(text, retryMsg){
  var peer = DM.peer; if(!peer) return;
  text = String(text || '').trim(); if(!text) return;
  var m = retryMsg || {id: 'tmp' + (++DM.tmp), text: text, ts: Date.now(), mine: true, kind: 'text', status: 'sent'};
  m.pending = true; m.failed = false;
  if(!retryMsg) DM.msgs.push(m);
  dmRender(); document.getElementById('dmerr').textContent = '';
  try{
    var d = await dmCall('/api/dm/send', {method: 'POST', body: JSON.stringify({to: peer.username, text: text})});
    var k = DM.msgs.indexOf(m);
    if(d.msg){
      if(k >= 0) DM.msgs.splice(k, 1);
      if(!DM.msgs.some(function(x){ return x.id === d.msg.id; })){ d.msg.mine = true; DM.msgs.push(d.msg); }
      DM.lastId = Math.max(DM.lastId, d.msg.id);
    } else { m.pending = false; }
    dmRender();
  }catch(e){
    m.pending = false; m.failed = true; dmRender();
    document.getElementById('dmerr').textContent = e.message === 'off' ? 'Mesaj xidməti serverdə hələ qoşulmayıb.' : (e.message === 'xəta' ? 'Göndərilmədi. Bağlantını yoxla.' : e.message);
  }
}
function dmRetry(m){
  if(!m.failed) return;
  if((m.kind || 'text') === 'text') dmSend(m.text, m); else dmSendMedia(m.blob, m.kind, m.dur, m);
}
function dmErr(t){ document.getElementById('dmerr').textContent = t || ''; }
/* ---- media göndərmə ---- */
function dmUpload(path, blob, onprog){
  return new Promise(function(res, rej){
    var x = new XMLHttpRequest(); x.open('POST', path);
    x.setRequestHeader('X-Token', token); x.setRequestHeader('Content-Type', 'application/octet-stream');
    x.upload.onprogress = function(e){ if(e.lengthComputable) onprog(Math.round(e.loaded / e.total * 100)); };
    x.onload = function(){
      var j = null; try{ j = JSON.parse(x.responseText); }catch(e){}
      if(x.status >= 200 && x.status < 300 && j) res(j);
      else { var er = new Error((j && j.error) || 'xəta'); er.status = x.status; rej(er); }
    };
    x.onerror = function(){ rej(new Error('xəta')); };
    x.timeout = 10 * 60 * 1000; x.ontimeout = x.onerror;
    x.send(blob);
  });
}
async function dmSendMedia(blob, kind, dur, retryMsg){
  var peer = DM.peer; if(!peer || !blob) return;
  var m = retryMsg || {id: 'tmp' + (++DM.tmp), text: '', ts: Date.now(), mine: true, kind: kind, dur: dur || 0, status: 'sent', blob: blob, local: URL.createObjectURL(blob)};
  m.pending = true; m.failed = false; m.prog = 0;
  if(!retryMsg) DM.msgs.push(m);
  dmRender(); dmErr('');
  try{
    var d = await dmUpload('/api/dm/media?to=' + encodeURIComponent(peer.username) + '&kind=' + kind + '&dur=' + Math.round(dur || 0), blob, function(p){
      m.prog = p; var el = document.querySelector('#dmmsgs [data-id="' + m.id + '"] .dmprog'); if(el) el.textContent = p + '%';
    });
    if(!DM.peer || DM.peer.username !== peer.username) return;
    var k = DM.msgs.indexOf(m); if(k >= 0) DM.msgs.splice(k, 1);
    if(d.msg){
      if(!DM.msgs.some(function(x){ return x.id === d.msg.id; })){ d.msg.mine = true; DM.msgs.push(d.msg); }
      DM.lastId = Math.max(DM.lastId, d.msg.id);
    }
    if(m.local){ try{ URL.revokeObjectURL(m.local); }catch(e){} m.local = ''; }
    dmRender();
  }catch(e){
    m.pending = false; m.failed = true;
    if(DM.peer && DM.peer.username === peer.username){ dmRender(); dmErr(e.message === 'xəta' ? 'Göndərilmədi. Bağlantını yoxla.' : e.message); }
  }
}
function dmShrink(file){
  return new Promise(function(res){
    if(file.type === 'image/gif'){ res(file); return; }
    var url = URL.createObjectURL(file), im = new Image();
    im.onload = function(){
      URL.revokeObjectURL(url);
      var w = im.naturalWidth, h = im.naturalHeight, k = Math.min(1, 1600 / Math.max(w, h));
      if(k === 1 && file.size < 1.5e6 && /jpeg|png|webp/.test(file.type)){ res(file); return; }
      var c = document.createElement('canvas'); c.width = Math.max(1, Math.round(w * k)); c.height = Math.max(1, Math.round(h * k));
      var x = c.getContext('2d'); x.fillStyle = '#fff'; x.fillRect(0, 0, c.width, c.height); x.drawImage(im, 0, 0, c.width, c.height);
      c.toBlob(function(b){ res(b || file); }, 'image/jpeg', 0.85);
    };
    im.onerror = function(){ URL.revokeObjectURL(url); res(file); };
    im.src = url;
  });
}
/* ---- səs yazma ---- */
var REC = null;
function dmRecStart(){
  if(REC) return;
  if(!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder){ dmErr('Bu cihaz səs yazmağı dəstəkləmir.'); return; }
  dmErr('');
  navigator.mediaDevices.getUserMedia({audio: true}).then(function(st){
    var mt = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'].filter(function(t){ return MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(t); })[0] || '';
    var rec; try{ rec = mt ? new MediaRecorder(st, {mimeType: mt}) : new MediaRecorder(st); }catch(e){ st.getTracks().forEach(function(t){ t.stop(); }); dmErr('Bu cihaz səs yazmağı dəstəkləmir.'); return; }
    var R = {rec: rec, st: st, chunks: [], t0: Date.now(), send: false, tmr: 0}; REC = R;
    rec.ondataavailable = function(e){ if(e.data && e.data.size) R.chunks.push(e.data); };
    rec.onstop = function(){
      clearInterval(R.tmr); R.st.getTracks().forEach(function(t){ t.stop(); });
      document.getElementById('dmchat').classList.remove('recording'); if(REC === R) REC = null;
      var dur = (Date.now() - R.t0) / 1000;
      if(!R.send) return;
      if(dur < 1 || !R.chunks.length){ dmErr('Səs çox qısadır.'); return; }
      dmSendMedia(new Blob(R.chunks, {type: rec.mimeType || mt || 'audio/webm'}), 'audio', dur);
    };
    rec.start(); document.getElementById('dmrect').textContent = '0:00';
    document.getElementById('dmchat').classList.add('recording');
    R.tmr = setInterval(function(){
      var sec = Math.floor((Date.now() - R.t0) / 1000);
      document.getElementById('dmrect').textContent = Math.floor(sec / 60) + ':' + String(sec % 60).padStart(2, '0');
      if(sec >= 300) dmRecStop(true);
    }, 250);
  }).catch(function(){ dmErr('Mikrofon icazəsi verilmədi.'); });
}
function dmRecStop(send){ if(!REC) return; REC.send = !!send; try{ REC.rec.stop(); }catch(e){ REC = null; document.getElementById('dmchat').classList.remove('recording'); } }
function dmSubmit(){
  var t = document.getElementById('dmin'), v = t.value;
  if(!v.trim()) return;
  t.value = ''; dmGrow(); dmSend(v);
}
(function(){
  var inp = document.getElementById('dmin');
  document.getElementById('dmback').onclick = closeDm;
  document.getElementById('dmsend').onclick = dmSubmit;
  document.getElementById('dmsend').addEventListener('mousedown', function(e){ e.preventDefault(); });
  inp.addEventListener('input', dmGrow);
  inp.addEventListener('keydown', function(e){
    if(e.key === 'Enter' && !e.shiftKey && !e.isComposing && window.matchMedia('(min-width:760px)').matches){ e.preventDefault(); dmSubmit(); }
  });
  document.addEventListener('keydown', function(e){ if(e.key === 'Escape' && !document.getElementById('dmchat').hidden) closeDm(); });
  document.getElementById('dmcall').onclick = function(){ if(DM.peer && typeof startCall === 'function') startCall(DM.peer); };
  document.getElementById('dmattach').onclick = function(){ document.getElementById('dmfile').click(); };
  document.getElementById('dmfile').onchange = async function(){
    var f = this.files && this.files[0]; this.value = ''; if(!f || !DM.peer) return;
    if(/^image\//.test(f.type)){ dmSendMedia(await dmShrink(f), 'image', 0); }
    else if(/^video\//.test(f.type)){ if(f.size > 40 * 1048576){ dmErr('Video çox böyükdür (maks. 40 MB).'); return; } dmSendMedia(f, 'video', 0); }
    else if(/^audio\//.test(f.type)){ if(f.size > 10 * 1048576){ dmErr('Səs faylı çox böyükdür (maks. 10 MB).'); return; } dmSendMedia(f, 'audio', 0); }
    else dmErr('Yalnız şəkil, video və ya səs göndərmək olar.');
  };
  document.getElementById('dmmic').onclick = dmRecStart;
  document.getElementById('dmreccancel').onclick = function(){ dmRecStop(false); };
  document.getElementById('dmrecsend').onclick = function(){ dmRecStop(true); };
  setInterval(function(){ if(token && !document.hidden && !DM.off) loadDmList(); }, 8000);
  document.addEventListener('visibilitychange', function(){ if(!document.hidden){ loadDmList(); if(DM.peer) dmPull(false); } });
  var t0 = setInterval(function(){ if(token){ clearInterval(t0); loadDmList(); } }, 1200);
  var dtab = document.querySelector('#tabbar [data-go="friends"]'); if(dtab) dtab.addEventListener('click', function(){ loadDmList(); });
})();
