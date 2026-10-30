# ---------------------------------------------------------------
#  Otaq rolları, mesaj tarixçəsi, GIF, izləmə statistikası, planlı seanslar, profil məlumatı, PWA
# ---------------------------------------------------------------
def _room_ctx(d):
    code = clean_room(d.get("room"))
    pid = str(d.get("id", ""))[:20]
    if not code or not pid:
        abort(400)
    return code, pid


@app.route("/api/role", methods=["POST"])
def api_role():
    """Host üzvü moderator edir / moderatorluğu alır."""
    d = request.get_json(silent=True) or {}
    code, pid = _room_ctx(d)
    target = str(d.get("target", ""))[:20]
    role = d.get("role")
    if role not in ("mod", "viewer"):
        abort(400)
    with CV:
        r = ROOMS.get(code)
        if r is None:
            abort(404)
        if r["host"] != pid:
            abort(403)
        if target == pid or target not in r["members"]:
            abort(400)
        if role == "mod":
            r["roles"][target] = "mod"
        else:
            r["roles"].pop(target, None)
        r["rv"] += 1
        CV.notify_all()
        return jsonify(ok=True)


@app.route("/api/hostonly", methods=["POST"])
def api_hostonly():
    """\"Yalnız host idarə edir\" rejimi (host + moderatorlar idarə edir, izləyicilər yalnız baxır)."""
    d = request.get_json(silent=True) or {}
    code, pid = _room_ctx(d)
    with CV:
        r = ROOMS.get(code)
        if r is None:
            abort(404)
        if r["host"] != pid:
            abort(403)
        r["hostonly"] = bool(d.get("on"))
        r["rv"] += 1
        CV.notify_all()
        return jsonify(ok=True, hostonly=r["hostonly"])


@app.route("/api/host", methods=["POST"])
def api_host():
    """Host hostluğu otaqdakı başqa üzvə verir; özü hostdan çıxır (adi izləyici olur)."""
    d = request.get_json(silent=True) or {}
    code, pid = _room_ctx(d)
    target = str(d.get("target", ""))[:20]
    now = time.time()
    with CV:
        r = ROOMS.get(code)
        if r is None:
            abort(404)
        if r["host"] != pid:
            abort(403)
        if target == pid or target not in r["members"]:
            abort(400)
        ts = r["peers"].get(target)
        if ts is None or now - ts >= PEER_TTL:
            abort(409)   # hədəf hazırda otaqda deyil
        r["host"] = target
        r["roles"].pop(target, None)
        r["roles"].pop(pid, None)
        r["rv"] += 1
        CV.notify_all()
        return jsonify(ok=True)


@app.route("/api/chat/history")
def api_chat_history():
    code = clean_room(request.args.get("room"))
    pid = str(request.args.get("id", ""))[:20]
    if not code or not pid:
        abort(400)
    with CV:
        r = ROOMS.get(code)
        if r is None:
            abort(404)
        if pid not in r["members"]:
            abort(403)
        out = []
        rx_all, mk = r.get("reacts", {}), rx_key(code, pid)
        for h in r["hist"]:
            o = {k: v for k, v in h.items() if k != "pid"}
            o["mine"] = (h["pid"] == pid)
            hid = h.get("id")
            rm = rx_all.get(hid) if isinstance(hid, str) else None
            if rm:
                o["rx"] = {k: v for k, v in rm.items() if k != mk}
                if mk in rm:
                    o["my"] = rm[mk]
            out.append(o)
    return _nostore(jsonify(msgs=out))


# ---- İzləmə statistikası ----
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@app.route("/api/watch", methods=["POST"])
def api_watch():
    sub, _ = auth_sub()
    d = request.get_json(silent=True) or {}
    try:
        secs, tog = float(d.get("secs", 0)), float(d.get("tog", 0))
        day = str(d.get("day", ""))
        dd = datetime.date.fromisoformat(day)
    except (TypeError, ValueError):
        abort(400)
    if not DAY_RE.match(day) or abs((dd - datetime.date.today()).days) > 2:
        abort(400)
    secs = max(0.0, min(secs, 120.0))
    tog = max(0.0, min(tog, secs))
    if secs <= 0:
        return jsonify(ok=True)
    if not rate_hit("watch", sub, 8, 60):
        abort(429)
    dbq("INSERT INTO watch(sub,day,secs,tog) VALUES(?,?,?,?) "
        "ON CONFLICT(sub,day) DO UPDATE SET secs=secs+excluded.secs, tog=tog+excluded.tog", (sub, day, secs, tog))
    return jsonify(ok=True)


@app.route("/api/watch/stats")
def api_watch_stats():
    sub, _ = auth_sub()
    today = request.args.get("today", "")
    try:
        t = datetime.date.fromisoformat(today)
    except ValueError:
        abort(400)
    if not DAY_RE.match(today):
        abort(400)
    m = dbq("SELECT COALESCE(SUM(secs),0) AS s, COALESCE(SUM(tog),0) AS t FROM watch WHERE sub=? AND day LIKE ?",
            (sub, today[:7] + "%"), one=True)
    a = dbq("SELECT COALESCE(SUM(secs),0) AS s, COALESCE(SUM(tog),0) AS t FROM watch WHERE sub=?", (sub,), one=True)
    days = [(t - datetime.timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    rows = dbq("SELECT day, secs FROM watch WHERE sub=? AND day>=? AND day<=?", (sub, days[0], days[-1]), many=True)
    mp = {r["day"]: r["secs"] for r in rows}
    vids = dbq("SELECT COUNT(*) AS c FROM activity WHERE sub=? AND kind='video' AND ts>?",
               (sub, time.time() - 30 * 86400), one=True)["c"]
    return _nostore(jsonify(month=m["s"], month_tog=m["t"], total=a["s"], total_tog=a["t"], videos30=vids,
                            days=[dict(d=x, s=mp.get(x, 0)) for x in days]))


# ---- Planlı seanslar ----
def clean_text(s, n):
    s = re.sub(r"[\x00-\x1f\x7f<>]", "", str(s or ""))
    return re.sub(r"\s+", " ", s).strip()[:n]


@app.route("/api/plan/create", methods=["POST"])
def api_plan_create():
    sub, _ = auth_sub()
    d = request.get_json(silent=True) or {}
    title = clean_text(d.get("title"), 40)
    try:
        ts = float(d.get("ts"))
    except (TypeError, ValueError):
        abort(400)
    now = time.time()
    if not title or ts < now - 300 or ts > now + 365 * 86400:
        abort(400)
    if not rate_hit("plan", sub, 10, 600):
        abort(429)
    if dbq("SELECT COUNT(*) AS c FROM plans WHERE owner=? AND ts>?", (sub, now - 6 * 3600), one=True)["c"] >= 20:
        abort(429)
    subs = []
    fr = d.get("friends")
    for u in (fr if isinstance(fr, list) else [])[:10]:
        u = norm_username(u)
        if not username_valid(u):
            continue
        row = dbq("SELECT sub FROM users WHERE username=?", (u,), one=True)
        if row and row["sub"] != sub and row["sub"] not in subs and _friend_rel(sub, row["sub"]) == "friend":
            subs.append(row["sub"])
    created = time.time()
    dbq("INSERT INTO plans(owner,title,ts,room,created) VALUES(?,?,?,NULL,?)", (sub, title, ts, created))
    pid = dbq("SELECT id FROM plans WHERE owner=? AND created=?", (sub, created), one=True)["id"]
    for s in subs:
        dbq("INSERT OR IGNORE INTO plan_inv(plan_id,sub,status) VALUES(?,?,0)", (pid, s))
    return jsonify(ok=True, id=pid)


@app.route("/api/plans")
def api_plans():
    sub, _ = auth_sub()
    now = time.time()
    dbq("DELETE FROM plans WHERE ts<?", (now - 6 * 3600,))
    dbq("DELETE FROM plan_inv WHERE plan_id NOT IN (SELECT id FROM plans)")
    rows = dbq("SELECT p.id, p.title, p.ts, p.room, p.owner, u.name AS oname, u.username AS ouser "
               "FROM plans p JOIN users u ON u.sub=p.owner "
               "WHERE p.owner=? OR p.id IN (SELECT plan_id FROM plan_inv WHERE sub=?) ORDER BY p.ts",
               (sub, sub), many=True)
    out = []
    for p in rows:
        inv = dbq("SELECT i.sub, i.status, u.name, u.username FROM plan_inv i JOIN users u ON u.sub=i.sub "
                  "WHERE i.plan_id=?", (p["id"],), many=True)
        room = p["room"] or ""
        if room:
            with CV:
                if room not in ROOMS:
                    room = ""
        mine = p["owner"] == sub
        my = -1 if mine else next((i["status"] for i in inv if i["sub"] == sub), 0)
        out.append(dict(id=p["id"], title=p["title"], ts=p["ts"], mine=mine, room=room, my=my,
                        owner=dict(name=p["oname"], username=p["ouser"] or ""),
                        guests=[dict(name=i["name"], username=i["username"] or "", st=i["status"]) for i in inv] if mine else []))
    return _nostore(jsonify(plans=out))


@app.route("/api/plan/respond", methods=["POST"])
def api_plan_respond():
    sub, _ = auth_sub()
    d = request.get_json(silent=True) or {}
    try:
        pid = int(d.get("id"))
    except (TypeError, ValueError):
        abort(400)
    dbq("UPDATE plan_inv SET status=? WHERE plan_id=? AND sub=?", (1 if d.get("going") else 2, pid, sub))
    return jsonify(ok=True)


@app.route("/api/plan/delete", methods=["POST"])
def api_plan_delete():
    sub, _ = auth_sub()
    try:
        pid = int((request.get_json(silent=True) or {}).get("id"))
    except (TypeError, ValueError):
        abort(400)
    if dbq("SELECT 1 FROM plans WHERE id=? AND owner=?", (pid, sub), one=True):
        dbq("DELETE FROM plan_inv WHERE plan_id=?", (pid,))
        dbq("DELETE FROM plans WHERE id=?", (pid,))
    return jsonify(ok=True)


@app.route("/api/plan/start", methods=["POST"])
def api_plan_start():
    """Seans sahibi otağı açır, gələcəyini bildirən (və cavab verməyən) dostlara dəvət gedir."""
    sub, _ = auth_sub()
    d = request.get_json(silent=True) or {}
    try:
        plan_id = int(d.get("id"))
    except (TypeError, ValueError):
        abort(400)
    pid = str(d.get("pid", ""))[:20]
    if not pid:
        abort(400)
    p = dbq("SELECT id, room FROM plans WHERE id=? AND owner=?", (plan_id, sub), one=True)
    if not p:
        abort(404)
    with CV:
        code = p["room"] if p["room"] in ROOMS else None
        if code:
            ROOMS[code]["host"] = pid
        else:
            code = _new_room(pid)
    dbq("UPDATE plans SET room=? WHERE id=?", (code, plan_id))
    now = time.time()
    for i in dbq("SELECT sub FROM plan_inv WHERE plan_id=? AND status<>2", (plan_id,), many=True):
        dbq("INSERT OR REPLACE INTO invites(to_sub,from_sub,room,ts) VALUES(?,?,?,?)", (i["sub"], sub, code, now))
    return jsonify(room=code)


# ---- Profil: qısa məlumat ----
@app.route("/api/profile/bio", methods=["POST"])
def api_profile_bio():
    sub, _ = auth_sub()
    b = clean_text((request.get_json(silent=True) or {}).get("bio"), 80)
    dbq("UPDATE users SET bio=? WHERE sub=?", (b or None, sub))
    return jsonify(profile_json(sub))


# ---- PWA: manifest, service worker, ikonlar (ikonlar şəkil faylı olmadan kodla çəkilir) ----
SW_JS = """const C='wt-shell-v1';
self.addEventListener('install',e=>{self.skipWaiting();e.waitUntil(caches.open(C).then(c=>c.add('/')).catch(()=>{}));});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(k=>Promise.all(k.filter(x=>x!==C).map(x=>caches.delete(x)))).then(()=>self.clients.claim()));});
self.addEventListener('fetch',e=>{
  const r=e.request;
  if(r.method!=='GET'||r.mode!=='navigate')return;
  const u=new URL(r.url);
  if(u.origin!==location.origin||u.pathname!=='/')return;
  e.respondWith(fetch(r).then(res=>{const cp=res.clone();caches.open(C).then(c=>c.put('/',cp)).catch(()=>{});return res;})
    .catch(()=>caches.match('/').then(m=>m||new Response('Əlaqə yoxdur. İnternetə qoşulub yenidən yoxla.',{status:503,headers:{'Content-Type':'text/plain; charset=utf-8'}}))));
});
self.addEventListener('notificationclick',e=>{
  e.notification.close();
  e.waitUntil(clients.matchAll({type:'window',includeUncontrolled:true}).then(l=>l.length?l[0].focus():clients.openWindow('/')));
});
"""

MANIFEST = {
    "name": "Birlikdə İzlə", "short_name": "Birlikdə", "description": "Uzaqda olsanız da, eyni kadrdasınız.",
    "lang": "az", "start_url": "/", "scope": "/", "display": "standalone", "orientation": "any",
    "background_color": "#150a1d", "theme_color": "#150a1d",
    "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
              {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
}
ICONS = {}


def make_icon(n):
    """Tünd fonda ürək + play üçbucağı (loqo ilə eyni), saf Python ilə PNG."""
    if n in ICONS:
        return ICONS[n]
    bg, heart, tri = (21, 10, 29), (232, 86, 125), (248, 231, 225)
    S, sc, cy = 2, 0.27, 0.534
    A, B, C = (0.45, 0.41), (0.45, 0.61), (0.62, 0.51)

    def inside_tri(px, py):
        def sg(p1, p2):
            return (px - p2[0]) * (p1[1] - p2[1]) - (p1[0] - p2[0]) * (py - p2[1])
        d1, d2, d3 = sg(A, B), sg(B, C), sg(C, A)
        return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))

    raw = bytearray()
    for y in range(n):
        raw.append(0)
        for x in range(n):
            acc = [0, 0, 0]
            for sy in range(S):
                for sx in range(S):
                    px, py = (x + (sx + .5) / S) / n, (y + (sy + .5) / S) / n
                    u, v = (px - .5) / sc, -(py - cy) / sc
                    if inside_tri(px, py):
                        c = tri
                    elif (u * u + v * v - 1) ** 3 - u * u * v ** 3 <= 0:
                        c = heart
                    else:
                        c = bg
                    acc[0] += c[0]; acc[1] += c[1]; acc[2] += c[2]
            k = S * S
            raw += bytes((acc[0] // k, acc[1] // k, acc[2] // k))

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))
    ICONS[n] = png
    return png


@app.route("/manifest.webmanifest")
def pwa_manifest():
    resp = Response(json.dumps(MANIFEST, ensure_ascii=False), content_type="application/manifest+json; charset=utf-8")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@app.route("/sw.js")
def pwa_sw():
    resp = Response(SW_JS, content_type="application/javascript; charset=utf-8")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@app.route("/icon-<int:n>.png")
def pwa_icon(n):
    if n not in (180, 192, 512):
        abort(404)
    resp = Response(make_icon(n), mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=604800"
    return resp


@app.route("/apple-touch-icon.png")
def pwa_apple_icon():
    return pwa_icon(180)
