# ---------------------------------------------------------------
#  Şahmat kompüteri: negamax + alfa-beta (iterativ dərinlik, vaxt limiti)
# ---------------------------------------------------------------
_CH_VAL = {"P": 100, "N": 320, "B": 330, "R": 500, "Q": 900, "K": 0}
_CH_BOT_TIME = 1.6
_CH_MATE = 30000


class _ChTimeout(Exception):
    pass


def _ch_cdist(sq):
    r, c = divmod(sq, 8)
    return max(abs(r - 3.5), abs(c - 3.5))


def _ch_eval(b, side):
    """Qiymət 'side' tərəfin xeyrinədir (sentipiyada)."""
    sc = {"w": 0, "b": 0}
    nonp = {"w": 0, "b": 0}
    ksq = {}
    for sq in range(64):
        p = b[sq]
        if not p:
            continue
        col, k = p[0], p[1]
        v = _CH_VAL[k]
        r, c = divmod(sq, 8)
        if k == "P":
            adv = (6 - r) if col == "w" else (r - 1)
            v += adv * 6 + (4 - abs(c - 3.5)) * (3 if adv < 5 else 1)
            if adv >= 5:
                v += 25 * (adv - 4)
        elif k in "NB":
            v += 14 - 5 * _ch_cdist(sq)
            home = 7 if col == "w" else 0
            if r == home:
                v -= 12          # fiqurlar evdə qalmasın
        elif k == "Q":
            v += 4 - _ch_cdist(sq)
        elif k == "R":
            v += 2 if c in (3, 4) else 0
        else:
            ksq[col] = sq
        sc[col] += v
        if k not in "PK":
            nonp[col] += _CH_VAL[k]
    # son oyun: üstün tərəf rəqib şahı kənara sıxır, öz şahını yaxınlaşdırır
    tot = sc["w"] - sc["b"]
    for col, op in (("w", "b"), ("b", "w")):
        lead = sc[col] - sc[op]
        if lead > 250 and nonp[op] <= 330 and col in ksq and op in ksq:
            rk, ck = divmod(ksq[col], 8)
            ro, co = divmod(ksq[op], 8)
            bonus = 12 * _ch_cdist(ksq[op]) + 5 * (14 - abs(rk - ro) - abs(ck - co))
            sc[col] += bonus
    # son oyunda şahı mərkəzə, orta oyunda rok yerində saxla
    if nonp["w"] + nonp["b"] <= 1400:
        for col in ("w", "b"):
            if col in ksq:
                sc[col] += 10 - 4 * _ch_cdist(ksq[col])
    else:
        for col in ("w", "b"):
            if col in ksq and ksq[col] in (62, 58, 6, 2):
                sc[col] += 30
    d = sc["w"] - sc["b"]
    return d if side == "w" else -d


def _ch_state_after(st, f, t, promo="Q"):
    p = st["b"][f]
    nb = _ch_do(st["b"], f, t, promo, st["ep"])
    cr = st["cr"]
    for sq, fl in ((60, "KQ"), (63, "K"), (56, "Q"), (4, "kq"), (7, "k"), (0, "q")):
        if f == sq or t == sq:
            for ch in fl:
                cr = cr.replace(ch, "")
    ep = (f + t) // 2 if p[1] == "P" and abs(t - f) == 16 else None
    return {"b": nb, "turn": "b" if st["turn"] == "w" else "w", "cr": cr, "ep": ep}


def _ch_order(st, moves, only_caps=False):
    b = st["b"]
    out = []
    for f, t in moves:
        v = b[t]
        cap = v is not None or (b[f][1] == "P" and f % 8 != t % 8)
        if only_caps and not cap:
            continue
        key = 0
        if cap:
            key = 10 * (_CH_VAL[v[1]] if v else 100) - _CH_VAL[b[f][1]] // 10 + 10000
        elif b[f][1] == "P" and t // 8 in (0, 7):
            key = 9000
        out.append((key, f, t))
    out.sort(reverse=True)
    return [(f, t) for _, f, t in out]


def _ch_search(st, depth, alpha, beta, dl, qd=0):
    if time.time() > dl:
        raise _ChTimeout()
    me = st["turn"]
    b = st["b"]
    moves = _ch_pseudo(st)
    for f, t in moves:                       # şah tutulur: əvvəlki gediş qanunsuz idi
        if b[t] and b[t][1] == "K":
            return _CH_MATE + depth
    if depth <= 0:
        stand = _ch_eval(b, me)
        if qd >= 3:
            return stand
        if stand >= beta:
            return stand
        if stand > alpha:
            alpha = stand
        best = stand
        for f, t in _ch_order(st, moves, True):
            ns = _ch_state_after(st, f, t)
            sc = -_ch_search(ns, 0, -beta, -alpha, dl, qd + 1)
            if sc > best:
                best = sc
                if sc > alpha:
                    alpha = sc
                if alpha >= beta:
                    break
        return best
    best = -10 ** 9
    for f, t in _ch_order(st, moves):
        ns = _ch_state_after(st, f, t)
        sc = -_ch_search(ns, depth - 1, -beta, -alpha, dl)
        if sc > best:
            best = sc
            if sc > alpha:
                alpha = sc
            if alpha >= beta:
                break
    if best <= -(_CH_MATE - 50):             # bütün gedişlər şahı itirir: mat və ya pat
        op = "b" if me == "w" else "w"
        k = b.index(me + "K") if (me + "K") in b else -1
        if k >= 0 and not _ch_att(b, k, op):
            return 0                         # pat
    return best


def _ch_bot_move(g):
    """g: {'b','turn','cr','ep','rep'...} nüsxəsi. (f, t) qaytarır."""
    legal = _ch_legal(g)
    if not legal:
        return None
    if len(legal) == 1:
        return legal[0]
    st = {"b": g["b"], "turn": g["turn"], "cr": g["cr"], "ep": g["ep"]}
    dl = time.time() + _CH_BOT_TIME
    npcs = sum(1 for x in st["b"] if x)
    maxd = 4 if npcs <= 10 else 3
    best = None
    scores = {}
    ordered = _ch_order(st, legal)
    for depth in range(1, maxd + 1):
        cur = {}
        try:
            alpha = -10 ** 9
            for f, t in ordered:
                ns = _ch_state_after(st, f, t)
                sc = -_ch_search(ns, depth - 1, -10 ** 9, -alpha, dl)
                key = (tuple(ns["b"]), ns["turn"], ns["cr"], ns["ep"])
                if g["rep"].get(key, 0) >= 1 and sc > 80:
                    sc -= 60                 # üstün olanda təkrardan qaç
                cur[(f, t)] = sc
                if sc > alpha:
                    alpha = sc
        except _ChTimeout:
            break
        scores = cur
        ordered = sorted(cur, key=lambda m: -cur[m])
        if scores[ordered[0]] >= _CH_MATE - 50:
            break
    if not scores:
        return random.choice(legal)
    top = max(scores.values())
    near = [m for m, v in scores.items() if v >= top - 6]
    return random.choice(near)


@app.route("/api/chess/bot", methods=["POST"])
def api_chess_bot():
    """Kompüterin növbəsi. Hesablama kilidin xaricində aparılır."""
    sub, tok = auth_sub()
    if not rate_hit("chb", sub, 240, 60):
        abort(429)
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    with CV:
        g = _ch_get(code, sub)
        g["seen"][sub] = time.time()
        bc = g["col"].get("bot")
        if not (g.get("bot") and g["status"] == "play" and g["turn"] == bc):
            return jsonify(_ch_view(g, sub))
        v0 = g["v"]
        snap = {k: copy.deepcopy(g[k]) for k in ("b", "turn", "cr", "ep", "hm", "rep")}
    mv = None
    try:
        mv = _ch_bot_move(snap)
    except Exception:
        mv = None
    with CV:
        g = CHESS.get(code)
        if not g or sub not in g["players"]:
            abort(404)
        if g["v"] == v0 and g["status"] == "play" and g["turn"] == g["col"].get("bot"):
            legal = _ch_legal(g)
            if legal:
                if mv is None or tuple(mv) not in legal:
                    mv = random.choice(legal)
                _ch_play(g, mv[0], mv[1], "Q")
                _dom_touch(g)
        return jsonify(_ch_view(g, sub))


@app.route("/api/chess/leave", methods=["POST"])
def api_chess_leave():
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    with CV:
        g = CHESS.get(code)
        if g and sub in g["players"] and (g["status"] == "wait" or g.get("bot")):
            CHESS.pop(code, None)
        elif g and sub in g["players"]:
            g["seen"][sub] = 0
            g.setdefault("gone", {})[sub] = time.time()
            _dom_touch(g)
    return jsonify(ok=True)


@app.route("/api/domino/bot", methods=["POST"])
def api_domino_bot():
    """Kompüterin növbəsi. Hesablama kilidin xaricində aparılır ki, digər sorğular gözləməsin."""
    sub, tok = auth_sub()
    if not rate_hit("domb", sub, 240, 60):
        abort(429)
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    with CV:
        g = _dom_get(code, sub)
        g["seen"][sub] = time.time()
        if not (g.get("bot") and g["status"] == "play" and g["turn"] == "bot"):
            return jsonify(_dom_view(g, sub))
        v0 = g["v"]
        snap = copy.deepcopy(g)
    res = None
    try:
        mv = _dom_moves(snap, snap["hands"]["bot"])
        if mv:
            try:
                t, e = _dom_bot_choose(snap, mv)
            except Exception:
                t, e = _dom_bot_pick_simple(snap, snap["hands"]["bot"], mv)
            res = ("play", t, e)
        else:
            res = ("draw",)
    except Exception:
        res = None
    with CV:
        g = DOMINO.get(code)
        if not g or sub not in g["players"]:
            abort(404)
        if res and g["v"] == v0 and g["status"] == "play" and g["turn"] == "bot":
            g["note"] = None
            if res[0] == "play":
                _dom_do_play(g, "bot", res[1], res[2])
            else:
                _dom_do_draw(g, "bot")   # bir daş; daş çıxmasa klient yenidən çağırır
            _dom_touch(g)
        return jsonify(_dom_view(g, sub))


@app.route("/api/domino/claim", methods=["POST"])
def api_domino_claim():
    """Növbədəki oyunçu DOM_CLAIM saniyədən çox yoxdursa: 2 nəfərdə oyun bitir, 3-4 nəfərdə o, oyundan çıxarılır."""
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    with CV:
        g = _dom_get(code, sub)
        now = time.time()
        t = g["turn"]
        if g["status"] == "play" and t and t != sub and t != "bot":
            last = g["seen"].get(t, 0) or g.get("gone", {}).get(t, 0)
            if now - last > DOM_CLAIM:
                if len(g["players"]) <= 2:
                    _dom_finish(g, sub, "left")
                else:
                    _dom_kick(g, t)
                _dom_touch(g)
        g["seen"][sub] = now
        return jsonify(_dom_view(g, sub))
