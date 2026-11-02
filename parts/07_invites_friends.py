# ---------------------------------------------------------------
#  Oyun dəvətləri (domino / şahmat): istifadəçi adı ilə və ya dost siyahısından
#  Yaddaşda saxlanır (oyunların özü kimi), 10 dəqiqə etibarlıdır.
# ---------------------------------------------------------------
GAME_INV = {}        # to_sub -> {(game, code, from_sub): dəvət}
GAME_INV_TTL = 600


def _game_store(kind):
    if kind == "durak":
        return DURAK
    if kind == "uno":
        return UNO
    if kind == "mafia":
        return MAFIA
    return DOMINO if kind == "domino" else CHESS


def _game_invites_for(sub):
    now = time.time()
    lst = GAME_INV.get(sub)
    if not lst:
        return []
    out = []
    with META_LOCK:
        for k, i in list(lst.items()):
            g = _game_store(i["game"]).get(i["room"])
            if (now - i["ts"] > GAME_INV_TTL or not g or g["status"] != "wait" or sub in g["players"]
                    or len(g["players"]) >= g.get("size", 2)):
                lst.pop(k, None)
                continue
            out.append(dict(id=i["id"], game=i["game"], room=i["room"], name=i["name"],
                            username=i["username"], avatar=i["avatar"], ts=i["ts"], voice=bool(i.get("voice"))))
    if not lst:
        GAME_INV.pop(sub, None)
    out.sort(key=lambda x: -x["ts"])
    return out


@app.route("/api/game/invite", methods=["POST"])
def api_game_invite():
    sub, tok = auth_sub()
    d = request.get_json(silent=True) or {}
    kind = d.get("game")
    u = norm_username(d.get("username"))
    code = clean_room(d.get("room"))
    if kind not in ("domino", "chess", "durak", "mafia", "uno") or not username_valid(u) or not code:
        abort(400)
    if not rate_hit("ginv", sub, 20, 600):
        abort(429)
    row = dbq("SELECT sub, invites_open FROM users WHERE username=?", (u,), one=True)
    if not row:
        return jsonify(error="user"), 404
    if row["sub"] == sub:
        abort(400)
    if _is_blocked_either(sub, row["sub"]):
        return jsonify(error="blocked"), 403
    if row["invites_open"] == 0:
        return jsonify(error="closed"), 403
    with META_LOCK:
        g = _game_store(kind).get(code)
        if not g or sub not in g["players"] or g["status"] != "wait":
            return jsonify(error="room"), 404
        if row["sub"] in g["players"]:
            return jsonify(error="in"), 409
        if len(g["players"]) >= g.get("size", 2):
            return jsonify(error="full"), 409
    idn = ident_for(tok)
    if len(GAME_INV) > 5000:
        GAME_INV.clear()
    GAME_INV.setdefault(row["sub"], {})[(kind, code, sub)] = dict(
        id=secrets.token_hex(6), game=kind, room=code, ts=time.time(), name=idn["name"],
        username=idn["username"], avatar=idn["avatar"], voice=bool(d.get("voice")))
    notify_user(row["sub"], "game", push=dict(title="Oyun dəvəti", body=idn["name"] + (" səni səsli söhbətə çağırır" if d.get("voice") else " səni oyuna çağırır"), tag="game", url="/"))
    return jsonify(ok=True)


@app.route("/api/game/invite/dismiss", methods=["POST"])
def api_game_invite_dismiss():
    sub, _ = auth_sub()
    iid = str((request.get_json(silent=True) or {}).get("id") or "")[:20]
    lst = GAME_INV.get(sub, {})
    for k, i in list(lst.items()):
        if i["id"] == iid:
            lst.pop(k, None)
    return jsonify(ok=True)


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
    row = dbq("SELECT seen, status FROM presence WHERE sub=?", (target,), one=True)
    if not row:
        return dict(presence="")
    seen = float(row["seen"])
    age = max(0, int(time.time() - seen))
    st = (row["status"] or "").strip()[:40]
    # 90 san: klient hər ~12-20 saniyədən bir presence göndərir
    if age < 90:
        label = st if st else "Onlayn"
        return dict(presence=label, online=True, status=st)
    if age < 3600:
        label = f"Son görülmə: {max(1, age // 60)} dəq. əvvəl"
    elif age < 86400:
        label = f"Son görülmə: {age // 3600} saat əvvəl"
    else:
        label = f"Son görülmə: {age // 86400} gün əvvəl"
    return dict(presence=label, online=False, status="")


@app.route("/api/presence", methods=["POST"])
def api_presence():
    sub, _ = auth_sub()
    now = time.time()
    d = request.get_json(silent=True) or {}
    st = re.sub(r"\s+", " ", str(d.get("status") or "")).strip()[:40]
    dbq("INSERT INTO presence(sub,seen,status) VALUES(?,?,?) ON CONFLICT(sub) DO UPDATE SET seen=excluded.seen, status=excluded.status",
        (sub, now, st or None))
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


@app.route("/api/report", methods=["POST"])
def api_report():
    sub, _ = auth_sub()
    d = request.get_json(silent=True) or {}
    if not rate_hit("report", sub, 10, 3600):
        return jsonify(error="rate"), 429
    other = _user_by_name(d.get("username"))
    if other == sub:
        return jsonify(error="self"), 400
    kind = "message" if d.get("kind") == "message" else "user"
    reason = str(d.get("reason") or "other")
    if reason not in ("spam", "abuse", "inappropriate", "impersonation", "other"):
        reason = "other"
    row = dbq("SELECT name,username FROM users WHERE sub=?", (other,), one=True)
    msg_text = clean_text(d.get("msg_text"), 500) if kind == "message" else ""
    dbq("INSERT INTO reports(ts,reporter,target,tname,tuser,kind,room,msg_id,msg_text,reason,details) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (time.time(), sub, other, row["name"] if row else "", row["username"] if row else "", kind,
         clean_text(d.get("room"), 40), clean_text(d.get("msg_id"), 60), msg_text, reason, clean_text(d.get("details"), 300)))
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
    friends.sort(key=lambda x: (0 if x.get("online") else 1, (x.get("name") or "").lower()))
    resp = jsonify(friends=friends, incoming=incoming, outgoing=outgoing)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/friend/request", methods=["POST"])
def api_friend_request():
    sub, tok = auth_sub()
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
        notify_user(other, "friend", push=dict(title="Yeni dost", body=ident_for(tok)["name"] + " dostluq sorğunu qəbul etdi", tag="friend", url="/"))
        return jsonify(ok=True, rel="friend")
    if dbq("SELECT COUNT(*) FROM friends WHERE a=? AND status=0", (sub,), one=True)[0] >= 50:
        abort(429)
    dbq("INSERT OR IGNORE INTO friends(a,b,status,ts) VALUES(?,?,0,?)", (sub, other, time.time()))
    notify_user(other, "friend", push=dict(title="Dostluq sorğusu", body=ident_for(tok)["name"] + " səni dost əlavə etmək istəyir", tag="friend", url="/"))
    return jsonify(ok=True, rel="out")


@app.route("/api/friend/respond", methods=["POST"])
def api_friend_respond():
    sub, tok = auth_sub()
    d = request.get_json(silent=True) or {}
    other = _user_by_name(d.get("username"))
    if _is_blocked_either(sub, other):
        abort(403)
    if d.get("accept"):
        dbq("UPDATE friends SET status=1, ts=? WHERE a=? AND b=? AND status=0", (time.time(), other, sub))
        notify_user(other, "friend", push=dict(title="Yeni dost", body=ident_for(tok)["name"] + " dostluq sorğunu qəbul etdi", tag="friend", url="/"))
    else:
        dbq("DELETE FROM friends WHERE a=? AND b=? AND status=0", (other, sub))
    return jsonify(ok=True)


@app.route("/api/friend/remove", methods=["POST"])
def api_friend_remove():
    sub, _ = auth_sub()
    other = _user_by_name((request.get_json(silent=True) or {}).get("username"))
    dbq("DELETE FROM friends WHERE (a=? AND b=?) OR (a=? AND b=?)", (sub, other, other, sub))
    return jsonify(ok=True)


