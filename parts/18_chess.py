# ---------------------------------------------------------------
#  Şahmat otağı: 2 nəfərlik onlayn oyun. Bütün qaydalar serverdə yoxlanılır
#  (rok, en passant, piyada yüksəlişi, şah/mat/pat, təkrar, 50 gediş).
# ---------------------------------------------------------------
CHESS = {}
CH_MAX = 200
_CN = [(-2, -1), (-2, 1), (-1, -2), (-1, 2), (1, -2), (1, 2), (2, -1), (2, 1)]
_CK = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
_CR = [(-1, 0), (1, 0), (0, -1), (0, 1)]
_CB = [(-1, -1), (-1, 1), (1, -1), (1, 1)]


def _ch_start():
    back = "RNBQKBNR"
    b = [None] * 64
    for c in range(8):
        b[c] = "b" + back[c]
        b[8 + c] = "bP"
        b[48 + c] = "wP"
        b[56 + c] = "w" + back[c]
    return b


def _ch_att(b, sq, by):
    r, c = divmod(sq, 8)
    pr = r + (1 if by == "w" else -1)
    for dc in (-1, 1):
        if 0 <= pr < 8 and 0 <= c + dc < 8 and b[pr * 8 + c + dc] == by + "P":
            return True
    for pc, offs in (("N", _CN), ("K", _CK)):
        for dr, dc in offs:
            rr, cc = r + dr, c + dc
            if 0 <= rr < 8 and 0 <= cc < 8 and b[rr * 8 + cc] == by + pc:
                return True
    for dirs, pcs in ((_CR, "RQ"), (_CB, "BQ")):
        for dr, dc in dirs:
            rr, cc = r + dr, c + dc
            while 0 <= rr < 8 and 0 <= cc < 8:
                p = b[rr * 8 + cc]
                if p:
                    if p[0] == by and p[1] in pcs:
                        return True
                    break
                rr += dr
                cc += dc
    return False


def _ch_pseudo(g):
    b, me = g["b"], g["turn"]
    op = "b" if me == "w" else "w"
    out = []
    for sq in range(64):
        p = b[sq]
        if not p or p[0] != me:
            continue
        r, c = divmod(sq, 8)
        k = p[1]
        if k == "P":
            d = -1 if me == "w" else 1
            start = 6 if me == "w" else 1
            rr = r + d
            if 0 <= rr < 8:
                if not b[rr * 8 + c]:
                    out.append((sq, rr * 8 + c))
                    if r == start and not b[(r + 2 * d) * 8 + c]:
                        out.append((sq, (r + 2 * d) * 8 + c))
                for dc in (-1, 1):
                    cc = c + dc
                    if 0 <= cc < 8:
                        t = rr * 8 + cc
                        if (b[t] and b[t][0] == op) or (t == g["ep"] and not b[t]):
                            out.append((sq, t))
        elif k in "NK":
            for dr, dc in (_CN if k == "N" else _CK):
                rr, cc = r + dr, c + dc
                if 0 <= rr < 8 and 0 <= cc < 8:
                    t = rr * 8 + cc
                    if not b[t] or b[t][0] == op:
                        out.append((sq, t))
            home = 60 if me == "w" else 4
            if k == "K" and sq == home and not _ch_att(b, sq, op):
                ks, qs = ("K", "Q") if me == "w" else ("k", "q")
                cr = g["cr"]
                if ks in cr and not b[sq + 1] and not b[sq + 2] and b[sq + 3] == me + "R" \
                        and not _ch_att(b, sq + 1, op) and not _ch_att(b, sq + 2, op):
                    out.append((sq, sq + 2))
                if qs in cr and not b[sq - 1] and not b[sq - 2] and not b[sq - 3] and b[sq - 4] == me + "R" \
                        and not _ch_att(b, sq - 1, op) and not _ch_att(b, sq - 2, op):
                    out.append((sq, sq - 2))
        else:
            dirs = _CR if k == "R" else (_CB if k == "B" else _CR + _CB)
            for dr, dc in dirs:
                rr, cc = r + dr, c + dc
                while 0 <= rr < 8 and 0 <= cc < 8:
                    t = rr * 8 + cc
                    if not b[t]:
                        out.append((sq, t))
                    else:
                        if b[t][0] == op:
                            out.append((sq, t))
                        break
                    rr += dr
                    cc += dc
    return out


def _ch_do(b, f, t, promo, ep):
    b = b[:]
    p = b[f]
    if p[1] == "P" and t == ep and f % 8 != t % 8 and not b[t]:
        b[(f // 8) * 8 + t % 8] = None
    if p[1] == "K" and abs(t - f) == 2:
        if t > f:
            b[f + 1], b[f + 3] = b[f + 3], None
        else:
            b[f - 1], b[f - 4] = b[f - 4], None
    b[t] = p
    b[f] = None
    if p[1] == "P" and t // 8 in (0, 7):
        b[t] = p[0] + (promo if promo in ("Q", "R", "B", "N") else "Q")
    return b


def _ch_legal(g):
    me = g["turn"]
    op = "b" if me == "w" else "w"
    res = []
    for f, t in _ch_pseudo(g):
        nb = _ch_do(g["b"], f, t, "Q", g["ep"])
        if not _ch_att(nb, nb.index(me + "K"), op):
            res.append((f, t))
    return res


def _ch_sub(g, col):
    return next((s for s, c in g["col"].items() if c == col), None)


def _ch_end(g, win, reason):
    g["status"] = "over"
    g["reason"] = reason
    g["win"] = win
    sc = g["score"]
    if win:
        sc[win] = sc.get(win, 0) + 1
    else:
        for p in g["players"]:
            sc[p] = sc.get(p, 0) + 0.5
    rec_game("chess", g)


def _ch_play(g, f, t, promo):
    b, me = g["b"], g["turn"]
    p = b[f]
    cap = b[t] is not None or (p[1] == "P" and t == g["ep"] and f % 8 != t % 8)
    nb = _ch_do(b, f, t, promo, g["ep"])
    cr = g["cr"]
    for sq, fl in ((60, "KQ"), (63, "K"), (56, "Q"), (4, "kq"), (7, "k"), (0, "q")):
        if f == sq or t == sq:
            for ch in fl:
                cr = cr.replace(ch, "")
    g["cr"] = cr
    g["ep"] = (f + t) // 2 if p[1] == "P" and abs(t - f) == 16 else None
    g["hm"] = 0 if (p[1] == "P" or cap) else g["hm"] + 1
    g["b"] = nb
    g["last"] = [f, t]
    g["turn"] = nxt = "b" if me == "w" else "w"
    key = (tuple(nb), nxt, cr, g["ep"])
    g["rep"][key] = g["rep"].get(key, 0) + 1
    chk = _ch_att(nb, nb.index(nxt + "K"), me)
    g["check"] = chk
    if not _ch_legal(g):
        if chk:
            _ch_end(g, _ch_sub(g, me), "mate")
        else:
            _ch_end(g, None, "stalemate")
        return
    pcs = [x for x in nb if x and x[1] != "K"]
    if g["hm"] >= 100:
        _ch_end(g, None, "fifty")
    elif g["rep"][key] >= 3:
        _ch_end(g, None, "repetition")
    elif not pcs or (len(pcs) == 1 and pcs[0][1] in "NB"):
        _ch_end(g, None, "material")


def _ch_reset(g, swap=False):
    a, b = g["players"][0], g["players"][1]
    if swap:
        g["col"] = {a: g["col"][b], b: g["col"][a]}
    else:
        x = secrets.choice("wb")
        g["col"] = {a: x, b: "b" if x == "w" else "w"}
    g.update(b=_ch_start(), turn="w", cr="KQkq", ep=None, hm=0, rep={}, last=None,
             check=False, win=None, reason="", status="play")


def _ch_cleanup(now):
    for c in [c for c, g in CHESS.items() if now - g["upd"] > DOM_IDLE]:
        CHESS.pop(c, None)


def _ch_touch(g):
    g["v"] += 1
    g["upd"] = time.time()
    rec_game("chess", g)
    _notify(g)


def _ch_get(code, sub):
    g = CHESS.get(code)
    if not g or sub not in g["players"]:
        abort(404)
    return g


def _ch_view(g, sub):
    opp = _dom_other(g, sub)
    now = time.time()
    st = g["status"]
    col = g["col"].get(sub)
    mine = st == "play" and g["turn"] == col
    out = dict(
        v=g["v"], code=g["code"], status=st, round=g["round"], color=col,
        b=g["b"] if st != "wait" else [], check=g["check"] if st != "wait" else False,
        moves=[list(m) for m in _ch_legal(g)] if mine else [],
        turn=(("me" if mine else "opp") if st == "play" else None),
        me=g["names"].get(sub), opp=g["names"].get(opp) if opp else None,
        opp_online=bool(opp and (opp == "bot" or now - g["seen"].get(opp, 0) < 35)),
        score=dict(me=g["score"].get(sub, 0), opp=g["score"].get(opp, 0) if opp else 0),
        last=g["last"], reason=g["reason"], bot=bool(g.get("bot")),
    )
    out["vc"], out["vk"] = _gv_view("chess", g)
    if st == "over":
        out["result"] = "draw" if not g["win"] else ("me" if g["win"] == sub else "opp")
    return out


@app.route("/api/chess/create", methods=["POST"])
def api_chess_create():
    sub, tok = auth_sub()
    if not rate_hit("chc", sub, 12, 60):
        abort(429)
    vs_bot = bool((request.get_json(silent=True) or {}).get("bot"))
    nm = _dom_names(tok)
    now = time.time()
    with META_LOCK:
        _ch_cleanup(now)
        if len(CHESS) >= CH_MAX:
            abort(503)
        for _ in range(50):
            c = "".join(secrets.choice(CODE_CHARS) for _ in range(5))
            if c not in CHESS:
                break
        else:
            abort(503)
        g = dict(code=c, players=[sub], names={sub: nm}, col={}, b=[], turn="w", cr="", ep=None, hm=0,
                 rep={}, last=None, check=False, win=None, reason="", status="wait", v=1, round=1,
                 score={}, seen={sub: now}, upd=now)
        if vs_bot:
            g["players"].append("bot")
            g["names"]["bot"] = dict(name="Kompüter", username="bot", avatar="", badge=None)
            g["seen"]["bot"] = now
            g["bot"] = True
            _ch_reset(g)
        _ensure_cv(g); CHESS[c] = g
        return jsonify(_ch_view(g, sub))


@app.route("/api/chess/join", methods=["POST"])
def api_chess_join():
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    if not code:
        abort(400)
    nm = _dom_names(tok)
    now = time.time()
    with META_LOCK:
        g = CHESS.get(code)
    with META_LOCK:
        g = CHESS.get(code)
        if not g:
            abort(404)
        if sub not in g["players"]:
            if len(g["players"]) >= 2:
                return jsonify(error="full"), 403
            g["players"].append(sub)
            g["names"][sub] = nm
            _ch_reset(g)
            _ch_touch(g)
        else:
            g["names"][sub] = nm
        g["seen"][sub] = now
        return jsonify(_ch_view(g, sub))


@app.route("/api/chess/poll")
def api_chess_poll():
    sub, tok = auth_sub()
    code = clean_room(request.args.get("room"))
    try:
        v = int(request.args.get("v", "0"))
    except ValueError:
        abort(400)
    try:
        vk = int(request.args["vk"]) if "vk" in request.args else None
    except ValueError:
        abort(400)
    deadline = time.time() + 20
    g = _ch_get(code, sub)
    with _ensure_cv(g):
        now = time.time()
        if now - g["seen"].get(sub, 0) > 30:
            g["seen"][sub] = now
            _ch_touch(g)
        g["seen"][sub] = now
        while g["v"] == v and (vk is None or g.get("vk", 0) == vk):
            left = deadline - time.time()
            if left <= 0:
                break
            g["_cv"].wait(timeout=left)
            if CHESS.get(code) is not g:
                abort(404)
            g["seen"][sub] = time.time()
        resp = jsonify(_ch_view(g, sub))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/chess/check")
def api_chess_check():
    """Saxlanmış şahmat oyunu hələ varmı? Heç nəyi dəyişmir."""
    sub, tok = auth_sub()
    code = clean_room(request.args.get("room"))
    with META_LOCK:
        g = CHESS.get(code)
    with META_LOCK:
        g = CHESS.get(code)
        if not g or sub not in g["players"]:
            abort(404)
        return jsonify(ok=True, status=g["status"])


@app.route("/api/chess/move", methods=["POST"])
def api_chess_move():
    sub, tok = auth_sub()
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    act = str(d.get("action", "move"))
    g = _ch_get(code, sub)
    with _ensure_cv(g):
        g["seen"][sub] = time.time()
        if g["status"] != "play":
            return jsonify(_ch_view(g, sub))
        me = g["col"][sub]
        if act == "resign":
            _ch_end(g, _ch_sub(g, "b" if me == "w" else "w"), "resign")
        elif act == "move":
            if g["turn"] != me:
                return jsonify(_ch_view(g, sub))
            try:
                f, t = int(d.get("f")), int(d.get("t"))
            except (TypeError, ValueError):
                abort(400)
            if (f, t) not in _ch_legal(g):
                abort(400)
            _ch_play(g, f, t, str(d.get("promo") or "Q")[:1].upper())
        else:
            abort(400)
        _ch_touch(g)
        return jsonify(_ch_view(g, sub))


@app.route("/api/chess/again", methods=["POST"])
def api_chess_again():
    sub, tok = auth_sub()
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    g = _ch_get(code, sub)
    with _ensure_cv(g):
        g["seen"][sub] = time.time()
        try:
            rd = int(d.get("round", 0))
        except (TypeError, ValueError):
            rd = 0
        if g["status"] == "over" and rd == g["round"] and len(g["players"]) == 2:
            g["round"] += 1
            _ch_reset(g, swap=True)
            _ch_touch(g)
        return jsonify(_ch_view(g, sub))


