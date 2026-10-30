# ---------------------------------------------------------------
#  Oyun dəvətləri (domino / şahmat): istifadəçi adı ilə və ya dost siyahısından
#  Yaddaşda saxlanır (oyunların özü kimi), 10 dəqiqə etibarlıdır.
# ---------------------------------------------------------------
GAME_INV = {}        # to_sub -> {(game, code, from_sub): dəvət}
GAME_INV_TTL = 600


def _game_store(kind):
    if kind == "durak":
        return DURAK
    return DOMINO if kind == "domino" else CHESS


def _game_invites_for(sub):
    now = time.time()
    lst = GAME_INV.get(sub)
    if not lst:
        return []
    out = []
    with CV:
        for k, i in list(lst.items()):
            g = _game_store(i["game"]).get(i["room"])
            if (now - i["ts"] > GAME_INV_TTL or not g or g["status"] != "wait" or sub in g["players"]
                    or len(g["players"]) >= g.get("size", 2)):
                lst.pop(k, None)
                continue
            out.append(dict(id=i["id"], game=i["game"], room=i["room"], name=i["name"],
                            username=i["username"], avatar=i["avatar"], ts=i["ts"]))
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
    if kind not in ("domino", "chess", "durak") or not username_valid(u) or not code:
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
    with CV:
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
        username=idn["username"], avatar=idn["avatar"])
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
