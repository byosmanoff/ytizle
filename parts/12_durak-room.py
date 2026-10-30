# ---------------------------------------------------------------
#  Durak otağı: 2–4 nəfərlik onlayn kart oyunu (insanlarla və ya kompüterə qarşı).
#  Qayda mühərriki yuxarıdakı Durak sinfidir. Səsli otaq (GV) və dəvət sistemi
#  domino ilə eyni yolla işləyir.
# ---------------------------------------------------------------
DURAK = {}
DUR_IDLE = 3 * 3600
DUR_MAX = 200


def _du_touch(g):
    g["v"] += 1
    g["upd"] = time.time()
    CV.notify_all()
    _du_save(g)


# ---- Durak oyunlarının bazada saxlanması: server yenilənəndə/yenidən başlayanda "Oyuna qayıt" işləsin ----
def _du_db_init():
    dbq("CREATE TABLE IF NOT EXISTS durak_games(code TEXT PRIMARY KEY, data TEXT NOT NULL, ts REAL NOT NULL)")


def _du_pack(g):
    d = {k: v for k, v in g.items() if k not in ("eng", "seen")}
    d["eng"] = g["eng"].to_dict() if g.get("eng") else None
    return json.dumps(d, separators=(",", ":"), default=str)


def _du_save(g):
    try:
        dbq("INSERT OR REPLACE INTO durak_games(code, data, ts) VALUES(?,?,?)",
            (g["code"], _du_pack(g), g["upd"]))
    except Exception:
        pass                                    # saxlama alınmasa da oyun yaddaşda davam edir


def _du_drop(code):
    DURAK.pop(code, None)
    try:
        dbq("DELETE FROM durak_games WHERE code=?", (code,))
    except Exception:
        pass


def _du_load():
    now = time.time()
    try:
        _du_db_init()
        dbq("DELETE FROM durak_games WHERE ts<?", (now - DUR_IDLE,))
        rows = dbq("SELECT code, data FROM durak_games", many=True) or []
    except Exception:
        return
    for r in rows:
        try:
            g = json.loads(r["data"])
            g["eng"] = Durak.from_dict(g["eng"]) if g.get("eng") else None
            bots = g.get("bots") or []
            g["seen"] = {p: (now if p in bots else 0) for p in g["players"]}
            g["upd"] = now
            g.setdefault("vk", 0)
            g.setdefault("quit", None)
            DURAK[g["code"]] = g
        except Exception:
            continue


def _du_names(tok):
    i = ident_for(tok, "")
    return dict(name=i["name"], username=i["username"], avatar=i["avatar"], badge=i["badge"])


def _du_cleanup(now):
    for c in [c for c, g in DURAK.items() if now - g["upd"] > DUR_IDLE]:
        _du_drop(c)


def _du_get(code, sub):
    g = DURAK.get(code)
    if not g or sub not in g["players"]:
        abort(404)
    return g


def _du_idx(g, x):
    return g["players"].index(x) if x in g["players"] else -1


def _du_card(x):
    r, s = x
    r = int(r)
    s = str(s)
    if r not in RANKS or s not in SUITS:
        raise ValueError("card")
    return (r, s)


def _du_start(g):
    g["eng"] = Durak(list(g["players"]), perevod=g["perevod"])
    g["status"] = "play"


def _du_view(g, sub):
    now = time.time()
    ps = g["players"]
    eng = g["eng"]
    out = dict(v=g["v"], code=g["code"], status=g["status"], size=g["size"], perevod=g["perevod"],
               joined=len(ps), round=g["round"], host=bool(ps and ps[0] == sub),
               me=ps.index(sub) if sub in ps else -1, started=False, actions=[], hand=[], table=[],
               quit=None, attacker=-1, defender=-1, took=False, cap=0, deck=0, discard=0, trump="")
    evp = []
    if eng:
        ev = eng.view(sub)
        evp = ev["players"]
        out.update(trump=ev["trump"], deck=ev["deck"], discard=ev["discard"],
                   attacker=_du_idx(g, eng.attacker), defender=_du_idx(g, eng.defender),
                   tcard=(list(eng.deck[0]) if eng.deck else None),
                   took=ev["took"], cap=ev["cap"], table=ev["table"], hand=ev["hand"],
                   actions=ev["actions"], started=eng.started,
                   passed=[_du_idx(g, p) for p in ev["passed"]])
        if eng.over:
            out["over"] = True
            out["loser"] = _du_idx(g, eng.loser)
    bots = g.get("bots") or ()
    players = []
    for i, p in enumerate(ps):
        nm = g["names"].get(p) or {}
        ep = evp[i] if i < len(evp) else {}
        players.append(dict(name=nm.get("name", ""), avatar=nm.get("avatar", ""),
                            username=nm.get("username", ""), me=(p == sub),
                            online=bool(p in bots or now - g["seen"].get(p, 0) < 35),
                            n=ep.get("n", 0), out=bool(ep.get("out", False))))
    out["players"] = players
    if g.get("quit"):
        out["quit"] = _du_idx(g, g["quit"])
    if g.get("bot"):
        out["bot"] = True
        out["botturn"] = bool(eng and not eng.over and g["status"] == "play" and du_bot_pick(eng, set(bots)))
    out["vc"], out["vk"] = _gv_view("durak", g)
    return out


@app.route("/api/durak/create", methods=["POST"])
def api_durak_create():
    sub, tok = auth_sub()
    if not rate_hit("durc", sub, 12, 60):
        abort(429)
    d0 = request.get_json(silent=True) or {}
    try:
        size = max(2, min(4, int(d0.get("size") or 2)))
    except (TypeError, ValueError):
        size = 2
    perevod = bool(d0.get("perevod", True))
    vs_bot = bool(d0.get("bot"))
    nm = _du_names(tok)
    now = time.time()
    with CV:
        _du_cleanup(now)
        if len(DURAK) >= DUR_MAX:
            abort(503)
        for _ in range(50):
            c = "".join(secrets.choice(CODE_CHARS) for _ in range(5))
            if c not in DURAK:
                break
        else:
            abort(503)
        g = dict(code=c, players=[sub], names={sub: nm}, seen={sub: now}, size=size, perevod=perevod,
                 status="wait", eng=None, v=1, vk=0, upd=now, round=1, quit=None)
        if vs_bot:
            bots = ["bot%d" % i for i in range(1, size)]
            for i, b in enumerate(bots, 1):
                g["players"].append(b)
                g["names"][b] = dict(name=("Bot %d" % i) if size > 2 else "Kompüter",
                                     username="bot", avatar="", badge=None)
                g["seen"][b] = now
            g["bot"] = True
            g["bots"] = bots
            _du_start(g)
        DURAK[c] = g
        _du_save(g)                                 # yaradılan kimi bazaya yaz: deploy/restartdan sonra "Oyuna qayıt" işləsin
        return jsonify(_du_view(g, sub))


@app.route("/api/durak/join", methods=["POST"])
def api_durak_join():
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    if not code:
        abort(400)
    nm = _du_names(tok)
    now = time.time()
    with CV:
        g = DURAK.get(code)
        if not g:
            abort(404)
        if sub not in g["players"]:
            if len(g["players"]) >= g["size"] or g["status"] != "wait":
                return jsonify(error="full"), 403
            g["players"].append(sub)
            g["names"][sub] = nm
            g["seen"][sub] = now
            if len(g["players"]) >= g["size"]:
                _du_start(g)
            _du_touch(g)
        else:
            g["names"][sub] = nm
        g["seen"][sub] = now
        return jsonify(_du_view(g, sub))


@app.route("/api/durak/poll")
def api_durak_poll():
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
    with CV:
        g = _du_get(code, sub)
        now = time.time()
        if now - g["seen"].get(sub, 0) > 30:
            g["seen"][sub] = now
            _du_touch(g)
        g["seen"][sub] = now
        while g["v"] == v and (vk is None or g.get("vk", 0) == vk):
            left = deadline - time.time()
            if left <= 0:
                break
            CV.wait(timeout=left)
            g = DURAK.get(code)
            if not g:
                abort(404)
            g["seen"][sub] = time.time()
        resp = jsonify(_du_view(g, sub))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/durak/check")
def api_durak_check():
    sub, tok = auth_sub()
    code = clean_room(request.args.get("room"))
    with CV:
        g = DURAK.get(code)
        if not g or sub not in g["players"]:
            abort(404)
        return jsonify(ok=True, status=g["status"])


@app.route("/api/durak/move", methods=["POST"])
def api_durak_move():
    sub, tok = auth_sub()
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    act = str(d.get("action", ""))
    with CV:
        g = _du_get(code, sub)
        g["seen"][sub] = time.time()
        eng = g["eng"]
        if g["status"] != "play" or eng is None:
            return jsonify(_du_view(g, sub))
        try:
            if act == "attack":
                eng.attack(sub, _du_card(d.get("card")))
            elif act == "defend":
                eng.defend(sub, _du_card(d.get("attack")), _du_card(d.get("card")))
            elif act == "transfer":
                eng.transfer(sub, _du_card(d.get("card")))
            elif act == "take":
                eng.take(sub)
            elif act == "pass":
                eng.pass_turn(sub)
            else:
                abort(400)
        except GameError as e:
            return jsonify(error=str(e)), 400
        except (TypeError, ValueError, IndexError):
            abort(400)
        if eng.over:
            g["status"] = "over"
        _du_touch(g)
        return jsonify(_du_view(g, sub))


@app.route("/api/durak/again", methods=["POST"])
def api_durak_again():
    sub, tok = auth_sub()
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    with CV:
        g = _du_get(code, sub)
        g["seen"][sub] = time.time()
        try:
            rd = int(d.get("round", 0))
        except (TypeError, ValueError):
            rd = 0
        if g["status"] == "over" and rd == g["round"]:
            g["round"] += 1
            q = g.get("quit")
            if q:                                  # çıxan oyunçu silinir, yer boşalır
                g["quit"] = None
                if q in g["players"]:
                    g["players"].remove(q)
                g["names"].pop(q, None)
                g["seen"].pop(q, None)
                g["eng"] = None
                g["status"] = "wait"
            elif len(g["players"]) == g["size"]:
                _du_start(g)
            else:
                g["eng"] = None
                g["status"] = "wait"
            _du_touch(g)
        return jsonify(_du_view(g, sub))


@app.route("/api/durak/leave", methods=["POST"])
def api_durak_leave():
    """Geri düyməsi: oyundan çıxmadan ekranı bağla. Gözləmədə yer boşalır (yaradan çıxsa otaq bağlanır),
    oyunda isə oyunçu qısa müddət 'əlaqədə deyil' görünür."""
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    with CV:
        g = DURAK.get(code)
        if g and sub in g["players"] and g["status"] == "wait":
            if g["players"][0] == sub:
                _du_drop(code)
            else:
                g["players"].remove(sub)
                g["names"].pop(sub, None)
                g["seen"].pop(sub, None)
                _du_touch(g)
        elif g and sub in g["players"]:
            g["seen"][sub] = 0
            _du_touch(g)
    return jsonify(ok=True)


@app.route("/api/durak/quit", methods=["POST"])
def api_durak_quit():
    """Oyundan həqiqətən çıx. Gözləmədə: yer boşalır, yaradan çıxsa otaq bağlanır.
    Gedən oyunda: oyun çıxana görə bitir, çıxan durak sayılır."""
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    with CV:
        g = DURAK.get(code)
        if not g or sub not in g["players"]:
            return jsonify(ok=True)
        if g.get("bot"):
            _du_drop(code)
            return jsonify(ok=True)
        if g["status"] == "wait":
            if g["players"][0] == sub:
                _du_drop(code)
                return jsonify(ok=True)
            g["players"].remove(sub)
            g["names"].pop(sub, None)
            g["seen"].pop(sub, None)
        elif g["status"] == "play" and g["eng"] and not g["eng"].over:
            eng = g["eng"]
            eng.over = True
            eng.loser = sub
            eng.started = False
            g["status"] = "over"
            g["quit"] = sub
        elif g["status"] == "over" and not g.get("quit"):
            g["quit"] = sub
        _du_touch(g)
    return jsonify(ok=True)

@app.route("/api/durak/bot", methods=["POST"])
def api_durak_bot():
    """Kompüterin bir hərəkəti. Klient növbə kompüterdə olanda çağırır (botturn=true)."""
    sub, tok = auth_sub()
    if not rate_hit("durb", sub, 300, 60):
        abort(429)
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    with CV:
        g = _du_get(code, sub)
        g["seen"][sub] = time.time()
        eng = g["eng"]
        if not g.get("bot") or g["status"] != "play" or eng is None:
            return jsonify(_du_view(g, sub))
        bots = set(g.get("bots") or ())
        pk = du_bot_pick(eng, bots)
        if pk:
            for _ in range(7):                     # müdafiəçi götürəndə atmalar bir sorğuda gedir (sürətli)
                try:
                    eng.apply(pk[0], pk[1])
                except GameError:
                    break
                if eng.over or not eng.took:
                    break
                pk = du_bot_pick(eng, bots)
                if not pk:
                    break
            if eng.over:
                g["status"] = "over"
            _du_touch(g)
        return jsonify(_du_view(g, sub))



_du_load()
