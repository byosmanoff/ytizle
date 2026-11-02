# ---------------------------------------------------------------
#  Domino otağı: 2 nəfərlik onlayn oyun (standart 28 daş, 0-6).
#  Yaddaşda saxlanır, long-poll ilə yenilənir (hər oyunun öz _cv-si).
# ---------------------------------------------------------------
DOMINO = {}
DOM_IDLE = 3 * 3600          # bu qədər hərəkətsiz qalan oyun silinir
DOM_MAX = 200
DOM_TARGET = 101             # ilk bu xala çatan oyunu qazanır
DOM_CLAIM = 60               # rəqib bu qədər saniyə yoxdursa, oyunu bitirmək olar


def _dom_other(g, sub):
    return next((p for p in g["players"] if p != sub), None)


def _dom_moves(g, hand):
    b = g["board"]
    if not b:
        first = g.get("first")
        return [{"t": list(t), "e": ["R"]} for t in hand if not first or list(t) == list(first)]
    l, r = b[0][0], b[-1][1]
    out = []
    for t in hand:
        e = []
        if l in t:
            e.append("L")
        if r in t:
            e.append("R")
        if e:
            out.append({"t": list(t), "e": e})
    return out


DOM_START_ORDER = [1, 2, 3, 4, 5, 6, 0]   # başlayan: əvvəl 1-1, sonra 2-2, 3-3 ... (ən az qoşa)


def _dom_deal(g):
    prev = g.get("winner")      # əvvəlki raundun qalibi (ilk raundda və yeni oyunda None)
    tiles = [[a, b] for a in range(7) for b in range(a, 7)]
    secrets.SystemRandom().shuffle(tiles)
    ps = g["players"]
    g["hands"] = {p: sorted(tiles[i * 7:(i + 1) * 7]) for i, p in enumerate(ps)}
    g["stock"] = tiles[7 * len(ps):]
    g["board"] = []
    g["cx"] = 0
    g["passes"] = 0
    g["winner"] = None
    g["reason"] = ""
    g["pts"] = 0
    g["last"] = None
    g["note"] = None
    g["lack"] = {}              # oyunçunun bazardan daş çəkdiyi anda açıq uclarda olan rəqəmlər (kompüter üçün)
    # İlk raund: 1-1 olan başlayır, yoxdursa 2-2 olan və s. Heç kimdə qoşa yoxdursa ən kiçik cəmli daş.
    # Sonrakı raundlar (101-ə qədər): əvvəlki raundun qalibi başlayır və istənilən daşı qoya bilir.
    # Raund bərabər bitibsə (qalib yoxdur), yenə qoşa qaydası işləyir.
    who, first = ps[0], None
    if prev in ps:
        g["first"] = None
        g["turn"] = prev
        g["status"] = "play"
        return
    for d in DOM_START_ORDER:
        owner = next((p for p in ps if [d, d] in g["hands"][p]), None)
        if owner:
            who, first = owner, [d, d]
            break
    if first is None:
        best = None
        for p in ps:
            for a, b in g["hands"][p]:
                k = (a + b, min(a, b), max(a, b))
                if best is None or k < best:
                    best, who, first = k, p, [a, b]
    g["first"] = first
    g["turn"] = who
    g["status"] = "play"


def _dom_next(g, sub):
    """Növbəti oyunçu (oturuş sırası ilə)."""
    ps = g["players"]
    return ps[(ps.index(sub) + 1) % len(ps)]


def _dom_sum(g, p):
    return sum(a + b for a, b in g["hands"].get(p, []))


def _dom_finish(g, winner, reason):
    """Raund bitir. Daşlar bitibsə qalib rəqiblərin əlindəki daşların cəmini alır;
    oyun bloklanıbsa rəqiblərin cəmi ilə öz cəmi arasındakı fərqi alır."""
    g["status"] = "over"
    g["winner"] = winner
    g["reason"] = reason
    g["pts"] = 0
    if winner:
        sw = _dom_sum(g, winner)
        so = [_dom_sum(g, p) for p in g["players"] if p != winner]
        pts = sum(so) if reason == "out" else max(sum(x - sw for x in so), 1)
        g["pts"] = pts
        g["score"][winner] = min(g["score"].get(winner, 0) + pts, DOM_TARGET)
        if g["score"][winner] >= DOM_TARGET:
            g["match"] = winner


def _dom_settle(g):
    """Növbədəki oyunçunun gedişi yoxdursa və bazar boşdursa, avtomatik keçir; hamı keçərsə oyun bloklanır."""
    n = len(g["players"])
    for _ in range(n + 1):
        if g["status"] != "play":
            return
        cur = g["turn"]
        if _dom_moves(g, g["hands"][cur]) or g["stock"]:
            return
        _dom_lack(g, cur)
        g["passes"] += 1
        g["note"] = {"who": cur, "k": "pass"}
        if g["passes"] >= n:
            sums = {p: _dom_sum(g, p) for p in g["players"]}
            m = min(sums.values())
            low = [p for p in g["players"] if sums[p] == m]
            _dom_finish(g, low[0] if len(low) == 1 else None, "blocked")
            return
        g["turn"] = _dom_next(g, cur)


def _dom_kick(g, p):
    """Uzun müddət yoxdur: oyunçu oyundan çıxarılır, daşları bazara qayıdır."""
    nxt = _dom_next(g, p)
    g["stock"].extend(g["hands"].pop(p, []))
    g["players"].remove(p)
    g["passes"] = 0
    g["note"] = None
    if len(g["players"]) == 1:
        _dom_finish(g, g["players"][0], "left")
    else:
        if g["turn"] == p:
            g["turn"] = nxt
        _dom_settle(g)


def _dom_do_play(g, who, t, end):
    """Daşı qoyur (server qaydaları ilə). Uğurludursa True."""
    hand = g["hands"][who]
    mv = next((m for m in _dom_moves(g, hand) if m["t"] == t), None)
    if not mv:
        return False
    if end not in mv["e"]:
        end = mv["e"][0]
    a, b = t
    bd = g["board"]
    if not bd:
        bd.append([a, b])
        g["cx"] = 0
    elif end == "L":
        bd.insert(0, [a, b] if b == bd[0][0] else [b, a])
        g["cx"] = g.get("cx", 0) + 1
    else:
        bd.append([a, b] if a == bd[-1][1] else [b, a])
    hand.remove(t)
    g["passes"] = 0
    g["last"] = {"who": who, "t": t}
    if not hand:
        _dom_finish(g, who, "out")
    else:
        g["turn"] = _dom_next(g, who)
        _dom_settle(g)
    return True


def _dom_do_draw(g, who):
    hand = g["hands"][who]
    if _dom_moves(g, hand) or not g["stock"]:
        return False
    _dom_lack(g, who)
    hand.append(g["stock"].pop())
    hand.sort()
    g["note"] = {"who": who, "k": "draw"}
    _dom_settle(g)
    return True


def _dom_lack(g, who):
    """Oyunçu bazardan daş çəkəndə/keçəndə açıq uclardakı rəqəmlər onda yox idi: kompüter bunu yadda saxlayır."""
    bd = g.get("board")
    if who == "bot" or not bd:
        return
    g.setdefault("lack", {}).setdefault(who, set()).update((bd[0][0], bd[-1][1]))


DOM_BOT_BUDGET = 0.12    # saniyə: kompüterin bir gediş üçün "düşünmə" vaxtının yuxarı həddi
DOM_BOT_MIN_K = 8        # nə qədər yavaş olsa da, ən azı bu qədər ehtimal ssenarisi sınanır
DOM_BOT_MAX_K = 60


def _dsim_opts(hand, l, r):
    """Əlimizdəki daşların variantları: (daş, tərəf, yeni açıq uc); tərəf 0 = sol, 1 = sağ."""
    out = []
    for t in hand:
        a, b = t
        if a == l or b == l:
            out.append((t, 0, b if a == l else a))
        if l != r and (a == r or b == r):
            out.append((t, 1, b if a == r else a))
    return out


def _dsim_pick(hand, opts):
    """Simulyasiyada hər iki tərəfin sürətli siyasəti: ağır daşı at, qoşanı sevin, açıq ucda daş saxla."""
    best, bs = None, None
    for o in opts:
        t, _, ne = o
        keep = sum(1 for x in hand if x != t and ne in x)
        sc = t[0] + t[1] + (1.5 if t[0] == t[1] else 0.0) + keep
        if bs is None or sc > bs:
            best, bs = o, sc
    return best


def _dsim_rollout(hands, stock, l, r, turn, passes):
    """Raundu sona qədər oynayır. hands[0] kompüterindir. Kompüter üçün xal fərqini qaytarır (+ udur, - uduzur)."""
    while True:
        h = hands[turn]
        opts = _dsim_opts(h, l, r)
        while not opts and stock:
            h.append(stock.pop())
            opts = _dsim_opts(h, l, r)
        if not opts:
            passes += 1
            if passes >= 2:
                sm = sum(a + b for a, b in hands[0])
                so = sum(a + b for a, b in hands[1])
                return so - sm
            turn ^= 1
            continue
        passes = 0
        t, side, ne = _dsim_pick(h, opts)
        h.remove(t)
        if side == 0:
            l = ne
        else:
            r = ne
        if not h:
            pts = sum(a + b for a, b in hands[turn ^ 1])
            return pts if turn == 0 else -pts
        turn ^= 1


def _dom_bot_pick_simple(g, hand, mv):
    """Sadə qayda (ehtiyat variant): ağır daş + açıq ucda davam edən daşlar."""
    bd = g["board"]
    best = None
    for m in mv:
        t = m["t"]
        for e in m["e"]:
            rest = [x for x in hand if x != t]
            if not bd:
                new_end = t[1]
            else:
                end_val = bd[0][0] if e == "L" else bd[-1][1]
                new_end = t[1] if t[0] == end_val else t[0]
            keep = sum(1 for x in rest if new_end in x)           # açıq ucda əlimizdə neçə daş davam edir
            score = t[0] + t[1] + (3 if t[0] == t[1] else 0) + 2 * keep + random.random()
            if best is None or score > best[0]:
                best = (score, t, e)
    return best[1], best[2]


def _dom_bot_choose(g, mv):
    """Kompüterin gedişi: görünməyən daşların mümkün bölgüsünü təsadüfi seçib (rəqibin əlini DEYİL, yalnız
    saydığımız daşları və rəqibin çəkdiyi anları bilirik), hər variantı bu ssenarilərdə sona qədər oynayır
    və orta nəticəsi ən yaxşı olanı seçir."""
    hand = [tuple(t) for t in g["hands"]["bot"]]
    bd = g["board"]
    opp = _dom_other(g, "bot")
    opp_n = len(g["hands"][opp])
    cands = []
    for m in mv:
        t = tuple(m["t"])
        a, b = t
        for e in m["e"]:
            if not bd:
                l2, r2 = a, b
            else:
                lv, rv = bd[0][0], bd[-1][1]
                if e == "L":
                    l2, r2 = (a if b == lv else b), rv
                else:
                    l2, r2 = lv, (b if a == rv else a)
            cands.append((t, e, l2, r2, [x for x in hand if x != t]))
    if len(cands) == 1:
        return list(cands[0][0]), cands[0][1]
    for c in cands:
        if not c[4]:                                    # son daşdır: dərhal qazan
            return list(c[0]), c[1]
    seen = set(hand) | {(min(x), max(x)) for x in bd}
    unseen = [(a, b) for a in range(7) for b in range(a, 7) if (a, b) not in seen]
    if len(unseen) != opp_n + len(g["stock"]):
        raise ValueError("daş sayı uyğun gəlmir")
    lack = g.get("lack", {}).get(opp, set())
    wts = [0.12 if (t[0] in lack or t[1] in lack) else 1.0 for t in unseen]
    tot = [0.0] * len(cands)
    n = 0
    t0 = time.perf_counter()
    while n < DOM_BOT_MAX_K and (n < DOM_BOT_MIN_K or time.perf_counter() - t0 < DOM_BOT_BUDGET):
        order = sorted(range(len(unseen)), key=lambda i: -(random.random() ** (1.0 / wts[i])))
        opp_h = [unseen[i] for i in order[:opp_n]]
        st = [unseen[i] for i in order[opp_n:]]
        random.shuffle(st)
        for ci, c in enumerate(cands):
            tot[ci] += _dsim_rollout([c[4][:], opp_h[:]], st[:], c[2], c[3], 1, 0)
        n += 1
    bi = max(range(len(cands)), key=lambda i: tot[i] + random.random() * 1e-6)
    return list(cands[bi][0]), cands[bi][1]


def _dom_bot_step(g):
    """Kompüterin bir hərəkəti: ən yaxşı daşı qoyur, yoxdursa bazardan çəkir."""
    hand = g["hands"]["bot"]
    mv = _dom_moves(g, hand)
    if not mv:
        _dom_do_draw(g, "bot")
        return
    try:
        t, e = _dom_bot_choose(g, mv)
    except Exception:
        t, e = _dom_bot_pick_simple(g, hand, mv)
    _dom_do_play(g, "bot", t, e)


def _dom_touch(g):
    g["v"] += 1
    g["upd"] = time.time()
    rec_game("domino", g)
    _notify(g)


def _dom_view(g, sub):
    now = time.time()
    st = g["status"]
    ps = g["players"]
    i = ps.index(sub) if sub in ps else 0
    order = ps[i + 1:] + ps[:i]
    hand = [list(t) for t in g["hands"].get(sub, [])] if st != "wait" else []
    mv = _dom_moves(g, hand) if st == "play" and g["turn"] == sub else []
    opps = []
    for p in order:
        opps.append(dict(g["names"].get(p) or {}, n=len(g["hands"].get(p, [])),
                         online=bool(p == "bot" or now - g["seen"].get(p, 0) < 35),
                         score=g["score"].get(p, 0), turn=bool(st == "play" and g["turn"] == p)))
    tn = g["names"].get(g["turn"]) if st == "play" and g["turn"] != sub else None
    out = dict(
        v=g["v"], code=g["code"], status=st, round=g["round"], board=g["board"], cx=g.get("cx"), hand=hand,
        moves=mv, stock=len(g["stock"]),
        can_draw=bool(st == "play" and g["turn"] == sub and not mv and g["stock"]),
        turn=(("me" if g["turn"] == sub else "opp") if st == "play" else None),
        turn_name=(tn or {}).get("name") if tn else None,
        turn_online=bool(tn and (g["turn"] == "bot" or now - g["seen"].get(g["turn"], 0) < 35)),
        me=g["names"].get(sub), opps=opps, size=g.get("size", 2), joined=len(ps), host=bool(ps and ps[0] == sub),
        my_score=g["score"].get(sub, 0),
        last=({"t": g["last"]["t"]} if g["last"] else None), reason=g["reason"], target=DOM_TARGET, bot=bool(g.get("bot")),
    )
    out["vc"], out["vk"] = _gv_view("domino", g)
    if g["note"]:
        nn = g["names"].get(g["note"]["who"]) or {}
        out["note"] = {"mine": g["note"]["who"] == sub, "k": g["note"]["k"], "name": nn.get("name", "")}
    if st == "over":
        w = g["winner"]
        out["result"] = "draw" if not w else ("me" if w == sub else "opp")
        out["winner_name"] = (g["names"].get(w) or {}).get("name", "") if w else ""
        out["pts"] = g.get("pts", 0)
        m = g.get("match")
        out["match"] = None if not m else ("me" if m == sub else "opp")
        out["match_name"] = (g["names"].get(m) or {}).get("name", "") if m else ""
        out["opp_hands"] = [dict(name=(g["names"].get(p) or {}).get("name", ""),
                                 hand=[list(t) for t in g["hands"].get(p, [])]) for p in order]
    return out


def _dom_cleanup(now):
    for c in [c for c, g in DOMINO.items() if now - g["upd"] > DOM_IDLE]:
        DOMINO.pop(c, None)


def _dom_names(tok):
    i = ident_for(tok, "")
    return dict(name=i["name"], username=i["username"], avatar=i["avatar"], badge=i["badge"])


def _dom_get(code, sub):
    g = DOMINO.get(code)
    if not g or sub not in g["players"]:
        abort(404)
    return g


@app.route("/api/domino/create", methods=["POST"])
def api_domino_create():
    sub, tok = auth_sub()
    if not rate_hit("domc", sub, 12, 60):
        abort(429)
    d0 = request.get_json(silent=True) or {}
    vs_bot = bool(d0.get("bot"))
    try:
        size = max(2, min(4, int(d0.get("size") or 2)))
    except (TypeError, ValueError):
        size = 2
    if vs_bot:
        size = 2
    nm = _dom_names(tok)
    now = time.time()
    with META_LOCK:
        _dom_cleanup(now)
        if len(DOMINO) >= DOM_MAX:
            abort(503)
        for _ in range(50):
            c = "".join(secrets.choice(CODE_CHARS) for _ in range(5))
            if c not in DOMINO:
                break
        else:
            abort(503)
        g = dict(code=c, players=[sub], names={sub: nm}, hands={}, stock=[], board=[], turn=None,
                 status="wait", winner=None, reason="", passes=0, v=1, round=1, score={},
                 seen={sub: now}, upd=now, last=None, note=None, pts=0, match=None, size=size)
        if vs_bot:
            g["players"].append("bot")
            g["names"]["bot"] = dict(name="Kompüter", username="bot", avatar="", badge=None)
            g["seen"]["bot"] = now
            g["bot"] = True
            _dom_deal(g)
        _ensure_cv(g); DOMINO[c] = g
        return jsonify(_dom_view(g, sub))


@app.route("/api/domino/join", methods=["POST"])
def api_domino_join():
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    if not code:
        abort(400)
    nm = _dom_names(tok)
    now = time.time()
    with META_LOCK:
        g = DOMINO.get(code)
    with META_LOCK:
        g = DOMINO.get(code)
        if not g:
            abort(404)
        if sub not in g["players"]:
            if len(g["players"]) >= g.get("size", 2) or g["status"] != "wait":
                return jsonify(error="full"), 403
            g["players"].append(sub)
            g["names"][sub] = nm
            g["seen"][sub] = now
            if len(g["players"]) >= g.get("size", 2):
                _dom_deal(g)
            _dom_touch(g)
        else:
            g["names"][sub] = nm
        g["seen"][sub] = now
        return jsonify(_dom_view(g, sub))


@app.route("/api/domino/poll")
def api_domino_poll():
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
    g = _dom_get(code, sub)
    with _ensure_cv(g):
        now = time.time()
        if now - g["seen"].get(sub, 0) > 30:     # qayıdıb: rəqib "onlayn" görsün
            g["seen"][sub] = now
            _dom_touch(g)
        g["seen"][sub] = now
        while g["v"] == v and (vk is None or g.get("vk", 0) == vk):
            left = deadline - time.time()
            if left <= 0:
                break
            g["_cv"].wait(timeout=left)
            if DOMINO.get(code) is not g:
                abort(404)
            g["seen"][sub] = time.time()
        resp = jsonify(_dom_view(g, sub))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/domino/check")
def api_domino_check():
    """Saxlanmış oyun hələ varmı? Heç nəyi dəyişmir; server yenilənəndə oyunlar silinir."""
    sub, tok = auth_sub()
    code = clean_room(request.args.get("room"))
    with META_LOCK:
        g = DOMINO.get(code)
    with META_LOCK:
        g = DOMINO.get(code)
        if not g or sub not in g["players"]:
            abort(404)
        return jsonify(ok=True, status=g["status"])


@app.route("/api/domino/move", methods=["POST"])
def api_domino_move():
    sub, tok = auth_sub()
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    act = str(d.get("action", ""))
    g = _dom_get(code, sub)
    with _ensure_cv(g):
        g["seen"][sub] = time.time()
        if g["status"] != "play" or g["turn"] != sub:
            return jsonify(_dom_view(g, sub))      # köhnə klik: cari vəziyyəti qaytar
        hand = g["hands"][sub]
        g["note"] = None
        if act == "play":
            try:
                t = sorted(int(x) for x in d.get("t", []))
                end = "L" if d.get("end") == "L" else "R"
            except (TypeError, ValueError):
                abort(400)
            if len(t) != 2 or not all(0 <= x <= 6 for x in t) or t not in hand:
                abort(400)
            if not _dom_do_play(g, sub, t, end):
                abort(400)
        elif act == "draw":
            if not _dom_do_draw(g, sub):   # hər sorğuda yalnız bir daş çəkilir
                abort(400)
        else:
            abort(400)
        _dom_touch(g)
        return jsonify(_dom_view(g, sub))


@app.route("/api/domino/again", methods=["POST"])
def api_domino_again():
    sub, tok = auth_sub()
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    g = _dom_get(code, sub)
    with _ensure_cv(g):
        g["seen"][sub] = time.time()
        try:
            rd = int(d.get("round", 0))
        except (TypeError, ValueError):
            rd = 0
        if g["status"] == "over" and rd == g["round"] and len(g["players"]) >= 2:
            g["round"] += 1
            if g.get("match"):                 # 101-ə çatılıb: yeni oyun, xal sıfırdan
                g["score"] = {}
                g["match"] = None
                g["winner"] = None             # yeni oyun yenə qoşa ilə başlayır
            _dom_deal(g)
            _dom_touch(g)
        return jsonify(_dom_view(g, sub))


@app.route("/api/domino/leave", methods=["POST"])
def api_domino_leave():
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    with META_LOCK:
        g = DOMINO.get(code)
    with META_LOCK:
        g = DOMINO.get(code)
        if g and sub in g["players"] and g["status"] == "wait":
            if g["players"][0] == sub:
                DOMINO.pop(code, None)      # yaradan çıxdı: otaq bağlanır
            else:                           # qonaq çıxdı: yer boşalır
                g["players"].remove(sub)
                g["names"].pop(sub, None)
                g["seen"].pop(sub, None)
                g["score"].pop(sub, None)
                _dom_touch(g)
        elif g and sub in g["players"]:
            g["seen"][sub] = 0
            _dom_touch(g)
    return jsonify(ok=True)


@app.route("/api/domino/quit", methods=["POST"])
def api_domino_quit():
    """Oyundan həqiqətən çıx (geri düyməsindən fərqli olaraq oyuna qayıtmaq olmur).
    Gözləmə: yer boşalır / otaq bağlanır. Gedən oyun: 2 nəfərdə rəqib qalib sayılır,
    3-4 nəfərdə oyunçu çıxarılır, daşları bazara qayıdır. Kompüterlə oyun silinir."""
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    with META_LOCK:
        g = DOMINO.get(code)
    with META_LOCK:
        g = DOMINO.get(code)
        if not g or sub not in g["players"]:
            return jsonify(ok=True)
        st = g["status"]
        if g.get("bot") or len([p for p in g["players"] if p != "bot"]) <= 1:
            DOMINO.pop(code, None)
            return jsonify(ok=True)
        if st == "play":
            if len(g["players"]) <= 2:
                _dom_finish(g, _dom_other(g, sub), "left")
            else:
                _dom_kick(g, sub)      # özü players-dən çıxarır
        if st == "wait" and g["players"][0] == sub:
            DOMINO.pop(code, None)     # yaradan çıxdı: otaq bağlanır
            return jsonify(ok=True)
        if sub in g["players"]:
            g["players"].remove(sub)
        g["names"].pop(sub, None)
        g["seen"].pop(sub, None)
        if st == "wait":
            g["score"].pop(sub, None)
        _dom_touch(g)
    return jsonify(ok=True)


