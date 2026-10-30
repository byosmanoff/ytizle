# ---------------------------------------------------------------
#  Dostluq: istifadəçi adı ilə axtar, sorğu göndər, qəbul et
# ---------------------------------------------------------------
def _av_url(username, ts):
    return ("/api/avatar/%s?v=%d" % (username, int(ts))) if ts and username else ""


def _friend_rel(me, other):
    row = dbq("SELECT a, status FROM friends WHERE (a=? AND b=?) OR (a=? AND b=?)",
              (me, other, other, me), one=True)
    if not row:
        return "none"
    if row["status"] == 1:
        return "friend"
    return "out" if row["a"] == me else "in"


def _user_by_name(raw):
    u = norm_username(raw)
    if not username_valid(u):
        abort(400)
    row = dbq("SELECT sub FROM users WHERE username=?", (u,), one=True)
    if not row:
        abort(404)
    return row["sub"]


def _is_blocked_either(a, b):
    return bool(dbq("SELECT 1 FROM blocks WHERE (blocker=? AND blocked=?) OR (blocker=? AND blocked=?)", (a, b, b, a), one=True))


def _presence_info(viewer, target):
    u = dbq("SELECT last_seen_visibility FROM users WHERE sub=?", (target,), one=True)
    if not u:
        return dict(presence="")
    visibility = (u["last_seen_visibility"] or "everyone")
    if visibility == "nobody":
        return dict(presence="")
    if visibility == "friends" and viewer != target and _friend_rel(viewer, target) != "friend":
        return dict(presence="")
    if viewer != target and _is_blocked_either(viewer, target):
        return dict(presence="")
    row = dbq("SELECT seen FROM presence WHERE sub=?", (target,), one=True)
    if not row:
        return dict(presence="")
    seen = float(row["seen"])
    age = max(0, int(time.time() - seen))
    if age < 45:
        return dict(presence="Onlayn", online=True)
    if age < 3600:
        label = f"Son görülmə: {max(1, age // 60)} dəq. əvvəl"
    elif age < 86400:
        label = f"Son görülmə: {age // 3600} saat əvvəl"
    else:
        label = f"Son görülmə: {age // 86400} gün əvvəl"
    return dict(presence=label, online=False)


@app.route("/api/presence", methods=["POST"])
def api_presence():
    sub, _ = auth_sub()
    now = time.time()
    dbq("INSERT INTO presence(sub,seen) VALUES(?,?) ON CONFLICT(sub) DO UPDATE SET seen=excluded.seen", (sub, now))
    return jsonify(ok=True)


@app.route("/api/settings/last-seen-visibility", methods=["POST"])
def api_settings_last_seen_visibility():
    sub, _ = auth_sub()
    value = str((request.get_json(silent=True) or {}).get("visibility") or "everyone")
    if value not in ("everyone", "friends", "nobody"):
        abort(400)
    dbq("UPDATE users SET last_seen_visibility=? WHERE sub=?", (value, sub))
    return jsonify(ok=True, last_seen_visibility=value)


@app.route("/api/blocks")
def api_blocks():
    sub, _ = auth_sub()
    rows = dbq("SELECT u.name,u.username FROM blocks b JOIN users u ON u.sub=b.blocked WHERE b.blocker=? ORDER BY b.ts DESC", (sub,), many=True)
    return jsonify(blocked=[dict(name=r["name"], username=r["username"]) for r in rows if r["username"]])


@app.route("/api/block", methods=["POST"])
def api_block_user():
    sub, _ = auth_sub()
    other = _user_by_name((request.get_json(silent=True) or {}).get("username"))
    if other == sub:
        abort(400)
    dbq("INSERT OR IGNORE INTO blocks(blocker,blocked,ts) VALUES(?,?,?)", (sub, other, time.time()))
    dbq("DELETE FROM friends WHERE (a=? AND b=?) OR (a=? AND b=?)", (sub, other, other, sub))
    dbq("DELETE FROM invites WHERE (to_sub=? AND from_sub=?) OR (to_sub=? AND from_sub=?)", (sub, other, other, sub))
    other_row = dbq("SELECT username FROM users WHERE sub=?", (other,), one=True)
    me_row = dbq("SELECT username FROM users WHERE sub=?", (sub,), one=True)
    other_username = other_row["username"] if other_row else None
    my_username = me_row["username"] if me_row else None
    for recipient, items in list(GAME_INV.items()):
        for key, item in list(items.items()):
            if ((recipient == sub and item.get("username") == other_username) or
                    (recipient == other and item.get("username") == my_username)):
                items.pop(key, None)
        if not items:
            GAME_INV.pop(recipient, None)
    return jsonify(ok=True)


@app.route("/api/unblock", methods=["POST"])
def api_unblock_user():
    sub, _ = auth_sub()
    other = _user_by_name((request.get_json(silent=True) or {}).get("username"))
    dbq("DELETE FROM blocks WHERE blocker=? AND blocked=?", (sub, other))
    return jsonify(ok=True)


@app.route("/api/users/search")
def api_users_search():
    sub, _ = auth_sub()
    q = re.sub(r"[^a-z0-9._]", "", str(request.args.get("q") or "").lower().lstrip("@"))[:20]
    if len(q) < 2:
        return jsonify(users=[])
    if not rate_hit("usearch", sub, 40, 60):
        abort(429)
    like = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    rows = dbq("SELECT u.sub, u.name, u.username, u.bio, (SELECT ts FROM avatars a WHERE a.sub=u.sub) AS av \
FROM users u WHERE u.username LIKE ? ESCAPE '\\' AND u.sub<>? ORDER BY (u.username=?) DESC, u.username LIMIT 8",
               (like, sub, q), many=True)
    out = []
    for r in rows:
        blocked_by_me = bool(dbq("SELECT 1 FROM blocks WHERE blocker=? AND blocked=?", (sub, r["sub"]), one=True))
        blocked_me = bool(dbq("SELECT 1 FROM blocks WHERE blocker=? AND blocked=?", (r["sub"], sub), one=True))
        pres = _presence_info(sub, r["sub"])
        out.append(dict(name=r["name"], username=r["username"], avatar=_av_url(r["username"], r["av"]),
                        badge=is_owner(r["sub"]), rel=_friend_rel(sub, r["sub"]), bio=r["bio"] or "",
                        blocked=blocked_by_me, blocked_me=blocked_me, **pres))
    return jsonify(users=out)


@app.route("/api/friends")
def api_friends():
    sub, _ = auth_sub()
    rows = dbq("SELECT f.a, f.b, f.status, u.sub AS osub, u.name, u.username, "
               "(SELECT ts FROM avatars a WHERE a.sub=u.sub) AS av "
               "FROM friends f JOIN users u ON u.sub = CASE WHEN f.a=? THEN f.b ELSE f.a END "
               "WHERE f.a=? OR f.b=? ORDER BY f.ts DESC", (sub, sub, sub), many=True)
    friends, incoming, outgoing = [], [], []
    for r in rows:
        if not r["username"]:
            continue
        pres = _presence_info(sub, r["osub"])
        blocked = bool(dbq("SELECT 1 FROM blocks WHERE blocker=? AND blocked=?", (sub, r["osub"]), one=True))
        it = dict(name=r["name"], username=r["username"], avatar=_av_url(r["username"], r["av"]),
                  badge=is_owner(r["osub"]), blocked=blocked, **pres)
        if r["status"] == 1:
            friends.append(it)
        elif r["b"] == sub:
            incoming.append(it)
        else:
            outgoing.append(it)
    friends.sort(key=lambda x: x["name"].lower())
    resp = jsonify(friends=friends, incoming=incoming, outgoing=outgoing)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/friend/request", methods=["POST"])
def api_friend_request():
    sub, _ = auth_sub()
    other = _user_by_name((request.get_json(silent=True) or {}).get("username"))
    if other == sub:
        abort(400)
    if _is_blocked_either(sub, other):
        abort(403)
    if not rate_hit("freq", sub, 20, 600):
        abort(429)
    rel = _friend_rel(sub, other)
    if rel in ("friend", "out"):
        return jsonify(ok=True, rel=rel)
    if rel == "in":   # o artıq sənə sorğu göndərib: dərhal dost olursunuz
        dbq("UPDATE friends SET status=1, ts=? WHERE a=? AND b=?", (time.time(), other, sub))
        return jsonify(ok=True, rel="friend")
    if dbq("SELECT COUNT(*) FROM friends WHERE a=? AND status=0", (sub,), one=True)[0] >= 50:
        abort(429)
    dbq("INSERT OR IGNORE INTO friends(a,b,status,ts) VALUES(?,?,0,?)", (sub, other, time.time()))
    return jsonify(ok=True, rel="out")


@app.route("/api/friend/respond", methods=["POST"])
def api_friend_respond():
    sub, _ = auth_sub()
    d = request.get_json(silent=True) or {}
    other = _user_by_name(d.get("username"))
    if _is_blocked_either(sub, other):
        abort(403)
    if d.get("accept"):
        dbq("UPDATE friends SET status=1, ts=? WHERE a=? AND b=? AND status=0", (time.time(), other, sub))
    else:
        dbq("DELETE FROM friends WHERE a=? AND b=? AND status=0", (other, sub))
    return jsonify(ok=True)


@app.route("/api/friend/remove", methods=["POST"])
def api_friend_remove():
    sub, _ = auth_sub()
    other = _user_by_name((request.get_json(silent=True) or {}).get("username"))
    dbq("DELETE FROM friends WHERE (a=? AND b=?) OR (a=? AND b=?)", (sub, other, other, sub))
    return jsonify(ok=True)


@app.route("/api/logout", methods=["POST"])
def api_logout():
    _, tok = auth_sub()
    dbq("DELETE FROM sessions WHERE token=?", (tok,))
    SESS_CACHE.clear()
    return jsonify(ok=True)


@app.route("/api/activity", methods=["POST"])
def api_activity():
    sub, _ = auth_sub()
    d = request.get_json(silent=True) or {}
    now = time.time()
    if d.get("kind") == "room":
        code = clean_room(d.get("room"))
        if not code:
            abort(400)
        if not dbq("SELECT 1 FROM activity WHERE sub=? AND kind='room' AND k=? AND ts>?",
                   (sub, code, now - 3600), one=True):
            dbq("INSERT INTO activity(sub,kind,k,room,ts) VALUES(?,?,?,?,?)", (sub, "room", code, code, now))
        return jsonify(ok=True)
    url = str(d.get("url") or "")[:1000]
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        abort(400)
    vid = yt_id(url)
    k, win = ("yt:" + vid, 600) if vid else ("host:" + p.hostname.lower(), 1800)
    if dbq("SELECT 1 FROM activity WHERE sub=? AND kind='video' AND k=? AND ts>?", (sub, k, now - win), one=True):
        return jsonify(ok=True)
    title = (yt_title(vid) or "YouTube videosu") if vid else p.hostname
    thumb = "https://i.ytimg.com/vi/" + vid + "/mqdefault.jpg" if vid else ""
    dbq("INSERT INTO activity(sub,kind,k,title,url,thumb,ts) VALUES(?,?,?,?,?,?,?)",
        (sub, "video", k, title, url, thumb, now))
    return jsonify(ok=True)


@app.route("/api/history")
def api_history():
    sub, _ = auth_sub()
    vids = dbq("SELECT title,url,thumb,ts FROM activity WHERE sub=? AND kind='video' ORDER BY ts DESC LIMIT 60",
               (sub,), many=True)
    rooms = dbq("SELECT room, MAX(ts) AS ts FROM activity WHERE sub=? AND kind='room' "
                "GROUP BY room ORDER BY ts DESC LIMIT 20", (sub,), many=True)
    resp = jsonify(videos=[dict(r) for r in vids], rooms=[dict(r) for r in rooms])
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/history/clear", methods=["POST"])
def api_history_clear():
    sub, _ = auth_sub()
    dbq("DELETE FROM activity WHERE sub=?", (sub,))
    return jsonify(ok=True)


@app.route("/api/config")
def api_config():
    resp = jsonify(google_client_id=os.environ.get("GOOGLE_CLIENT_ID", ""))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/google", methods=["POST"])
def api_google():
    """Google ID token-i yoxlayır; e-poçt eyni olan hesablar birləşir (Google + e-poçt = bir hesab). Sessiya tokeni qaytarır."""
    cid = os.environ.get("GOOGLE_CLIENT_ID", "")
    tok = str((request.get_json(silent=True) or {}).get("credential", ""))[:4000]
    if not cid or not tok:
        abort(400)
    try:
        r = requests.get("https://oauth2.googleapis.com/tokeninfo",
                         params={"id_token": tok}, timeout=8)
        d = r.json()
    except Exception:
        abort(502)
    if (r.status_code != 200 or d.get("aud") != cid
            or d.get("iss") not in ("accounts.google.com", "https://accounts.google.com")):
        abort(401)
    gsub = str(d.get("sub") or "")
    email = str(d.get("email") or "").strip().lower()
    if not gsub or not EMAIL_RE.match(email) or str(d.get("email_verified")).lower() != "true":
        abort(401)
    gname = clean_name(d.get("given_name") or d.get("name")) or "Qonaq"
    row = dbq("SELECT sub, verified FROM creds WHERE email=?", (email,), one=True)
    legacy = dbq("SELECT 1 FROM users WHERE sub=?", (gsub,), one=True)
    if row:
        sub = row["sub"]
        if not row["verified"]:
            # Google e-poçtun sahibini təsdiqləyib: qeydiyyatçının qoyduğu şifrə silinir (sahibi "Şifrəmi unutdum" ilə yenisini qoyar)
            dbq("UPDATE creds SET verified=1, pw='' WHERE email=?", (email,))
            dbq("UPDATE users SET name=? WHERE sub=?", (gname, sub))
            assign_username(sub)
        if legacy and gsub != sub:
            merge_users(gsub, sub)   # köhnə Google hesabı və e-poçt hesabı birləşir
    else:
        sub = gsub
        if not legacy:
            dbq("INSERT INTO users(sub,name,created) VALUES(?,?,?)", (sub, gname, time.time()))
        dbq("INSERT INTO creds(email,sub,pw,verified) VALUES(?,?,?,1)", (email, sub, ""))
    ensure_username(sub)
    log_login("google", email, True)
    return jsonify(token=issue_token(sub), name=user_name(sub))


@app.route("/api/ice")
def api_ice():
    """Səs üçün ICE serverləri. TURN ətraf mühit dəyişənlərindən gəlir (açar HTML-də durmasın):
       TURN_URLS="turn:host:3478,turns:host:5349"  TURN_USERNAME=...  TURN_CREDENTIAL=..."""
    servers = [{"urls": ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302"]}]
    urls = [u.strip() for u in os.environ.get("TURN_URLS", "").split(",") if u.strip()]
    if urls:
        servers.append({"urls": urls,
                        "username": os.environ.get("TURN_USERNAME", ""),
                        "credential": os.environ.get("TURN_CREDENTIAL", "")})
    resp = jsonify(servers=servers, turn=bool(urls))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/poll")
def api_poll():
    code = clean_room(request.args.get("room"))
    pid = request.args.get("id", "")[:20]
    if not code or not pid:
        abort(400)
    try:
        since = int(request.args.get("since", "0"))
        pc = int(request.args.get("pc", "0"))
        rv = int(request.args.get("rv", "-1"))
    except ValueError:
        abort(400)
    lk = request.args.get("lk") == "1"
    idn = ident_for(request.headers.get("X-Token", "")[:100], request.args.get("nm", ""))
    deadline = time.time() + 20
    with CV:
        r = get_room(code)
        admit(r, pid)
        r.setdefault("who", {})[pid] = dict(name=idn["name"], username=idn["username"],
                                            avatar=idn["avatar"], badge=idn["badge"], bio=idn.get("bio", ""))
        if since > r["seq"]:  # server restart olub: köhnə "since" mesajları bloklamasın
            since = 0
        while True:
            now = time.time()
            prune_peers(r, now)
            alive_mark(r, pid, now)
            h = r.get("host")
            if h and h != pid and now - r["peers"].get(h, 0) > HOST_GRACE:
                pass_host(r, now)   # host uzun müddət yoxdur: host ilk moderatora keçir
            new = [m for m in r["msgs"] if m[0] > since and m[1] != pid]
            n = count_others(r, pid, now)
            left = deadline - now
            if new or n != pc or r["locked"] != lk or rv != r["rv"] or left <= 0:
                break
            CV.wait(timeout=left)
        blank = dict(name="", username="", avatar="", badge=False, bio="")
        who = [dict(id=p, **r["who"].get(p, blank)) for p, ts in r["peers"].items()
               if p != pid and now - ts < PEER_TTL]
        roles = {p: role_of(r, p) for p in [pid] + [w["id"] for w in who]}
        resp = jsonify(seq=r["seq"], peers=n, msgs=[m[2] for m in new], who=who,
                       locked=r["locked"], epoch=r["created"], video=r.get("video"), vpos=vpos_now(r, now),
                       roles=roles, hostonly=r["hostonly"], rv=r["rv"])
    resp.headers["Cache-Control"] = "no-store"
    return resp
