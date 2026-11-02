# ---------------------------------------------------------------
#  DM səsli zəng: server yalnız siqnalı ötürür (WebRTC səsi birbaşa cihazlar arasında gedir)
# ---------------------------------------------------------------
CALL_TYPES = ("invite", "accept", "reject", "end", "offer", "answer")
CALL_LOCK = threading.Lock()
CALL_LOG = {}    # call_id -> dict(caller, callee, ts, ans)
CALL_PEND = {}   # callee sub -> dict(id, frm, ts)   (zəng səsləndiyi müddətdə)


def _call_label(secs):
    return "Zəng · %d:%02d" % (secs // 60, secs % 60)


@app.route("/api/call/signal", methods=["POST"])
def api_call_signal():
    sub, tok = auth_sub()
    d = request.get_json(silent=True) or {}
    other = _user_by_name(d.get("to"))
    if other == sub:
        abort(400)
    typ = str(d.get("type") or "")
    cid = re.sub(r"[^A-Za-z0-9_-]", "", str(d.get("id") or ""))[:24]
    if typ not in CALL_TYPES or not cid:
        abort(400)
    bad = _dm_check_pair(sub, other)
    if bad:
        return bad
    data = d.get("data")
    if data is not None and len(json.dumps(data)) > 24000:
        abort(413)
    if not rate_hit("callsig", sub, 120, 60):
        return jsonify(error="Çox sorğu"), 429
    idn = ident_for(tok)
    now = time.time()
    log_row = None
    with CALL_LOCK:
        if typ == "invite":
            if not rate_hit("callinv", sub, 12, 300):
                return jsonify(error="Çox zəng etdin, bir az gözlə"), 429
            for k in [k for k, v in CALL_LOG.items() if now - v["ts"] > 6 * 3600]:
                CALL_LOG.pop(k, None)
            CALL_LOG[cid] = dict(caller=sub, callee=other, ts=now, ans=0)
            CALL_PEND[other] = dict(id=cid, frm=sub, ts=now)
        else:
            rec = CALL_LOG.get(cid)
            if not rec or {rec["caller"], rec["callee"]} != {sub, other}:
                return jsonify(ok=True, gone=True)   # zəng artıq bitib
            if typ == "accept":
                rec["ans"] = now
            if typ in ("accept", "reject", "end"):
                p = CALL_PEND.get(rec["callee"])
                if p and p["id"] == cid:
                    CALL_PEND.pop(rec["callee"], None)
            if typ in ("reject", "end"):
                CALL_LOG.pop(cid, None)
                if rec["ans"]:
                    secs = int(now - rec["ans"])
                    log_row = (rec["caller"], rec["callee"], _call_label(secs), secs, False)
                elif typ == "end" and sub == rec["caller"]:
                    log_row = (rec["caller"], rec["callee"], "Cavabsız zəng", 0, True)
                elif isinstance(data, dict) and data.get("busy"):
                    log_row = (rec["caller"], rec["callee"], "Cavabsız zəng", 0, True)
                else:
                    log_row = (rec["caller"], rec["callee"], "Rədd edilmiş zəng", 0, False)
    payload = dict(type=typ, id=cid, u=idn["username"], name=idn["name"], data=data, ts=int(now * 1000))
    push = None
    if typ == "invite":
        u = dbq("SELECT (SELECT ts FROM avatars a WHERE a.sub=users.sub) AS av FROM users WHERE sub=?", (sub,), one=True)
        payload["av"] = _av_url(idn["username"], u["av"] if u else None)
        push = dict(title=idn["name"], body="\U0001F4DE Zəng edir...", tag="call-" + str(idn["username"]), url="/")
    notify_user(other, "call", payload=payload, push=push)
    if log_row:
        frm, to, label, secs, missed = log_row
        _dm_insert(frm, to, label, "call", None, secs)
        if missed:
            cu = dbq("SELECT name, username FROM users WHERE sub=?", (frm,), one=True)
            notify_user(to, "dm", payload=dict(u=(cu["username"] if cu else "")),
                        push=dict(title=(cu["name"] if cu else "Zəng"), body="\U0001F4DE Cavabsız zəng",
                                  tag="call-" + str(cu["username"] if cu else ""), url="/"))
    return jsonify(ok=True)


@app.route("/api/call/pending")
def api_call_pending():
    """Tətbiq arxa fonda olanda gəlmiş və hələ səslənən zəng (SSE yalnız ekran açıq olanda işləyir)."""
    sub, _ = auth_sub()
    with CALL_LOCK:
        p = CALL_PEND.get(sub)
        if p and time.time() - p["ts"] > 40:
            CALL_PEND.pop(sub, None)
            p = None
    if not p:
        return jsonify(call=None)
    u = dbq("SELECT name, username, (SELECT ts FROM avatars a WHERE a.sub=users.sub) AS av FROM users WHERE sub=?", (p["frm"],), one=True)
    if not u or not u["username"]:
        return jsonify(call=None)
    return jsonify(call=dict(id=p["id"], u=u["username"], name=u["name"], av=_av_url(u["username"], u["av"]), ts=int(p["ts"] * 1000)))


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
    r = get_room(code)
    with _ensure_cv(r):
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
            r["_cv"].wait(timeout=left)
        blank = dict(name="", username="", avatar="", badge=False, bio="")
        who = [dict(id=p, **r["who"].get(p, blank)) for p, ts in r["peers"].items()
               if p != pid and now - ts < PEER_TTL]
        roles = {p: role_of(r, p) for p in [pid] + [w["id"] for w in who]}
        resp = jsonify(seq=r["seq"], peers=n, msgs=[m[2] for m in new], who=who,
                       locked=r["locked"], epoch=r["created"], video=r.get("video"), vpos=vpos_now(r, now),
                       roles=roles, hostonly=r["hostonly"], rv=r["rv"])
    resp.headers["Cache-Control"] = "no-store"
    return resp


