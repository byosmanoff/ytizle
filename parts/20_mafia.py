# ---------------------------------------------------------------
#  Mafia otağı: 4–12 nəfərlik onlayn oyun. Bütün qaydalar və rollar serverdədir:
#  rolu yalnız sahibi (və qaydalara görə komanda yoldaşları / ölülər) görür.
#  Mərhələlər: gecə (mafiya öldürür, həkim qoruyur, komissar yoxlayır) -> gündüz (müzakirə)
#  -> səsvermə -> gecə ... Mafia sayı sakinlərə çatanda mafiya, mafiya qalmayanda sakinlər qalib gəlir.
#  Yaddaşda saxlanır (domino kimi), long-poll ilə yenilənir (hər oyunun öz _cv-si).
# ---------------------------------------------------------------
MAFIA = {}
MAF_IDLE = 3 * 3600
MAF_MAX = 100          # eyni anda maksimum otaq
MAF_MIN = 4            # başlamaq üçün ən az oyunçu
MAF_MAXP = 12          # otaqda ən çox oyunçu
MAF_NIGHT = 35         # gecə (san)
MAF_DAY = 90           # gündüz müzakirəsi (san)
MAF_DAY_MIN = 30       # bu müddət keçməmiş "səsverməyə keç" çoxluğu mərhələni dəyişmir (san)
MAF_VOTE = 45          # səsvermə (san)
MAF_MSG_LEN = 200
MAF_CHAT_KEEP = 200
MAF_ROLES = ("mafia", "doctor", "sheriff", "civil")
_MA_RND = random.SystemRandom()


def _ma_touch(g):
    g["v"] += 1
    g["upd"] = time.time()
    rec_game("mafia", g)
    _notify(g)


def _ma_names(tok):
    i = ident_for(tok, "")
    return dict(name=i["name"], username=i["username"], avatar=i["avatar"], badge=i["badge"])


def _ma_cleanup(now):
    for c in [c for c, g in MAFIA.items() if now - g["upd"] > MAF_IDLE]:
        MAFIA.pop(c, None)


def _ma_get(code, sub):
    g = MAFIA.get(code)
    if not g or sub not in g["players"]:
        abort(404)
    return g


def _ma_alive(g):
    return [p for p in g["players"] if g["alive"].get(p)]


def _ma_idx(g, p):
    return g["players"].index(p) if p in g["players"] else -1


def _ma_win(g):
    al = _ma_alive(g)
    m = sum(1 for p in al if g["roles"].get(p) == "mafia")
    if m == 0:
        return "town"
    if m >= len(al) - m:
        return "mafia"
    return None


def _ma_reset_night(g):
    g["n_kill"] = {}
    g["n_save"] = None
    g["n_check"] = False


def _ma_reset_game(g):
    """Gözləmə vəziyyətinə qaytar (yeni oyun üçün)."""
    g.update(status="wait", phase=None, day=0, roles={}, alive={}, gone={}, revealed=[], log=[], chat=[],
             skip=[], votes={}, checks={}, last_save=None, winner=None, deadline=0, total=0, bot_said={})
    _ma_reset_night(g)


def _ma_over(g, w):
    g["status"] = "over"
    g["phase"] = None
    g["winner"] = w
    g["deadline"] = 0


def _ma_start(g, now):
    ps = list(g["players"])
    n = len(ps)
    nm = max(1, n // 4)      # 4-7 nəfər: 1 mafiya, 8-11: 2, 12: 3 (n//3 oyunu çox tez bitirirdi)
    roles = ["mafia"] * nm + ["doctor", "sheriff"] + ["civil"] * (n - nm - 2)
    _MA_RND.shuffle(roles)
    g["roles"] = dict(zip(ps, roles))
    g["alive"] = {p: True for p in ps}
    g["gone"] = {}
    g["revealed"] = []
    g["log"] = []
    g["chat"] = []
    g["skip"] = []
    g["votes"] = {}
    g["checks"] = {}
    g["last_save"] = None
    g["winner"] = None
    g["status"] = "play"
    g["phase"] = "night"
    g["day"] = 1
    g["total"] = MAF_NIGHT
    g["deadline"] = now + MAF_NIGHT
    _ma_reset_night(g)


def _ma_kill(g, p, how):
    g["alive"][p] = False
    if p not in g["revealed"]:
        g["revealed"].append(p)


def _ma_to_night(g, now):
    g["phase"] = "night"
    g["day"] += 1
    g["total"] = MAF_NIGHT
    g["deadline"] = now + MAF_NIGHT
    g["votes"] = {}
    g["skip"] = []
    _ma_reset_night(g)


def _ma_end_night(g, now):
    tally = {}
    for s, t in g["n_kill"].items():
        if g["alive"].get(s) and g["alive"].get(t):
            tally[t] = tally.get(t, 0) + 1
    victim = None
    if tally:
        top = max(tally.values())
        victim = _MA_RND.choice([t for t, c in tally.items() if c == top])
    saved = g["n_save"]
    if victim and victim == saved:
        victim = None
    g["last_save"] = saved
    if victim:
        _ma_kill(g, victim, "night")
        g["log"].append(dict(k="night_dead", d=g["day"], p=_ma_idx(g, victim), r=g["roles"][victim], c=g["cid"]))
    else:
        g["log"].append(dict(k="night_none", d=g["day"], c=g["cid"]))
    _ma_reset_night(g)
    w = _ma_win(g)
    if w:
        _ma_over(g, w)
        return
    g["phase"] = "day"
    g["total"] = MAF_DAY
    g["deadline"] = now + MAF_DAY
    g["skip"] = []
    g["votes"] = {}


def _ma_to_vote(g, now):
    g["phase"] = "vote"
    g["total"] = MAF_VOTE
    g["deadline"] = now + MAF_VOTE
    g["votes"] = {}


def _ma_end_vote(g, now):
    tally, skips = {}, 0
    for s, t in g["votes"].items():
        if not g["alive"].get(s):
            continue
        if t == "skip":
            skips += 1
        elif g["alive"].get(t):
            tally[t] = tally.get(t, 0) + 1
    elim, n = None, 0
    if tally:
        top = max(tally.values())
        cands = [t for t, c in tally.items() if c == top]
        if len(cands) == 1 and top > skips:      # bərabərlik və ya "ötür" çoxluğu: heç kim çıxmır
            elim, n = cands[0], top
    if elim:
        _ma_kill(g, elim, "vote")
        g["log"].append(dict(k="vote_dead", d=g["day"], p=_ma_idx(g, elim), r=g["roles"][elim], n=n, c=g["cid"]))
    else:
        g["log"].append(dict(k="vote_none", d=g["day"], c=g["cid"]))
    w = _ma_win(g)
    if w:
        _ma_over(g, w)
        return
    _ma_to_night(g, now)


# ---- test botları (yalnız otaq sahibi əlavə edir; "bot:" ilə başlayan sub) ----
MAF_BOT_NAMES = ["Aysel", "Elçin", "Nigar", "Rəşad", "Leyla", "Tural", "Səbinə", "Kamran", "Günel", "Orxan", "Fidan", "Vüsal"]
MAF_BOT_SAY = ["Mənə şübhəli gəlmir, gəlin diqqətlə baxaq", "Kim gecə sakit idi?", "Mən günahsızam!", "Hələ tez xülasə etməyək",
               "Bir az daha müzakirə edək", "Mənim şübhəm var, amma əminlik yoxdur", "Kimsə özünü çox müdafiə edir", "Mən sakinəm, inanın"]


def _ma_is_bot(p):
    return isinstance(p, str) and p.startswith("bot:")


def _ma_humans(g):
    return [p for p in g["players"] if not _ma_is_bot(p) and not g["gone"].get(p)]


def _ma_bots_act(g, now):
    """Botlar gecikmə ilə hərəkət edir. True qayıdırsa dəyişiklik olub."""
    if g["status"] != "play":
        return False
    ph = g["phase"]
    start = g["deadline"] - g["total"]
    el = now - start
    changed = False
    ps = g["players"]
    al = _ma_alive(g)
    for i, b in enumerate(ps):
        if not _ma_is_bot(b) or not g["alive"].get(b):
            continue
        g["seen"][b] = now
        wait = 2.0 + (i * 1.7) % 7.0           # hər botun öz gecikməsi
        if el < wait:
            continue
        role = g["roles"].get(b)
        if ph == "night":
            others = [p for p in al if p != b]
            if role == "mafia" and b not in g["n_kill"]:
                t = [p for p in others if g["roles"].get(p) != "mafia"]
                if t:
                    g["n_kill"][b] = _MA_RND.choice(t)
                    changed = True
            elif role == "doctor" and g["n_save"] is None:
                t = [p for p in al if p != g["last_save"]]
                if t:
                    g["n_save"] = _MA_RND.choice(t)
                    changed = True
            elif role == "sheriff" and not g["n_check"]:
                seen = {c["t"] for c in g["checks"].get(b, [])}
                t = [p for p in others if p not in seen] or others
                if t:
                    tg = _MA_RND.choice(t)
                    g["n_check"] = True
                    g["checks"].setdefault(b, []).append(dict(t=tg, m=(g["roles"][tg] == "mafia"), d=g["day"]))
                    changed = True
        elif ph == "day":
            if not g.setdefault("bot_said", {}).get(b) == g["day"] and _MA_RND.random() < 0.5:
                g["bot_said"][b] = g["day"]
                g["cid"] += 1
                g["chat"].append(dict(id=g["cid"], ch="all", f=i, t=_MA_RND.choice(MAF_BOT_SAY)))
                changed = True
            if el > MAF_DAY * 0.6 and b not in g["skip"] and _MA_RND.random() < 0.05:
                g["skip"].append(b)
                changed = True
        elif ph == "vote" and b not in g["votes"]:
            tgt = None
            if role == "sheriff":
                tgt = next((c["t"] for c in g["checks"].get(b, []) if c["m"] and g["alive"].get(c["t"])), None)
            if tgt is None:
                pool = [p for p in al if p != b and not (role == "mafia" and g["roles"].get(p) == "mafia")]
                tgt = _MA_RND.choice(pool) if pool and _MA_RND.random() < 0.85 else "skip"
            g["votes"][b] = tgt
            changed = True
    return changed


def _ma_tick(g, now):
    """Vaxt bitibsə və ya hamı hərəkətini edibsə mərhələni irəli aparır. CV tutulu halda çağırılır."""
    if g["status"] != "play":
        return False
    bot_ch = _ma_bots_act(g, now)
    ph = g["phase"]
    al = _ma_alive(g)
    if ph == "night":
        if now < g["deadline"]:
            if bot_ch:
                _ma_touch(g)
            return bot_ch
        _ma_end_night(g, now)
    elif ph == "day":
        nskip = sum(1 for p in g["skip"] if g["alive"].get(p))
        early = al and nskip * 2 > len(al) and now - (g["deadline"] - g["total"]) >= MAF_DAY_MIN
        if now < g["deadline"] and not early:
            if bot_ch:
                _ma_touch(g)
            return bot_ch
        _ma_to_vote(g, now)
    elif ph == "vote":
        if now < g["deadline"] and not (al and all(p in g["votes"] for p in al)):
            if bot_ch:
                _ma_touch(g)
            return bot_ch
        _ma_end_vote(g, now)
    else:
        return False
    _ma_touch(g)
    return True


def _ma_remove_play(g, sub, now):
    """Gedən oyundan həqiqətən çıxan oyunçu: ölü sayılır, rolu açılır."""
    if not g["alive"].get(sub) and g["gone"].get(sub):
        return
    was_alive = g["alive"].get(sub)
    g["gone"][sub] = True
    if was_alive:
        _ma_kill(g, sub, "left")
        g["log"].append(dict(k="left", d=g["day"], p=_ma_idx(g, sub), r=g["roles"].get(sub), c=g["cid"]))
    g["n_kill"].pop(sub, None)
    if g["roles"].get(sub) == "doctor":
        g["n_save"] = None
    if sub in g["skip"]:
        g["skip"].remove(sub)
    g["votes"].pop(sub, None)
    if g.get("host") == sub:
        nxt = next((p for p in g["players"] if not g["gone"].get(p) and not _ma_is_bot(p)), None)
        if nxt:
            g["host"] = nxt
    if g["status"] == "play":
        w = _ma_win(g)
        if w:
            _ma_over(g, w)
        else:
            _ma_tick(g, now)


def _ma_visible(g, sub, ch):
    if ch == "all":
        return True
    if g["status"] == "over":
        return True
    if g["status"] == "play" and not g["alive"].get(sub):
        return True                         # ölülər hər şeyi oxuyur
    if ch == "maf":
        return g["roles"].get(sub) == "mafia"
    return False                            # "dead": yalnız ölülər (yuxarıda keçdi)


def _ma_view(g, sub):
    now = time.time()
    st = g["status"]
    ps = g["players"]
    me = ps.index(sub) if sub in ps else -1
    play = st in ("play", "over")
    role = g["roles"].get(sub) if play else None
    alive_me = bool(g["alive"].get(sub)) if play else True
    see_all = st == "over" or (st == "play" and not alive_me)
    is_maf = role == "mafia"
    phase = g.get("phase")
    out = dict(v=g["v"], code=g["code"], status=st, phase=phase, day=g.get("day", 0), size=g["size"], min=MAF_MIN,
               joined=len(ps), host=(g.get("host") == sub), me=me, round=g["round"], role=role, alive=alive_me,
               left=max(0, int(g["deadline"] - now + 0.99)) if st == "play" else 0, total=g.get("total", 0))
    votes = g["votes"] if phase == "vote" else {}
    players = []
    for i, p in enumerate(ps):
        nm = g["names"].get(p) or {}
        r = None
        if play:
            if p == sub or see_all or p in g["revealed"] or (is_maf and g["roles"].get(p) == "mafia"):
                r = g["roles"].get(p)
        d = dict(name=nm.get("name", ""), avatar=nm.get("avatar", ""), username=nm.get("username", ""), me=(p == sub),
                 online=(_ma_is_bot(p) or bool(now - g["seen"].get(p, 0) < 35)), alive=(bool(g["alive"].get(p)) if play else True),
                 left=bool(g["gone"].get(p)), role=r)
        if phase == "vote" and p in votes:
            t = votes[p]
            d["vote"] = -1 if t == "skip" else _ma_idx(g, t)
        if phase == "night" and is_maf and alive_me and g["roles"].get(p) == "mafia" and p in g["n_kill"]:
            d["pick"] = _ma_idx(g, g["n_kill"][p])
        players.append(d)
    out["players"] = players
    # --- mənim hərəkət imkanlarım ---
    act = None
    if st == "play" and alive_me:
        al = [i for i, p in enumerate(ps) if g["alive"].get(p)]
        if phase == "night":
            if role == "mafia":
                act = dict(kind="kill", targets=[i for i in al if g["roles"][ps[i]] != "mafia"],
                           pick=_ma_idx(g, g["n_kill"].get(sub)) if sub in g["n_kill"] else -1)
            elif role == "doctor":
                blk = _ma_idx(g, g["last_save"]) if g["last_save"] else -1
                act = dict(kind="save", targets=[i for i in al if i != blk], block=blk,
                           pick=_ma_idx(g, g["n_save"]) if g["n_save"] else -1)
            elif role == "sheriff":
                act = dict(kind="check", targets=([] if g["n_check"] else [i for i in al if i != me]), done=bool(g["n_check"]))
        elif phase == "day":
            out["skips"] = sum(1 for p in g["skip"] if g["alive"].get(p))
            act = dict(kind="skip", done=(sub in g["skip"]))
        elif phase == "vote":
            act = dict(kind="vote", targets=[i for i in al if i != me],
                       pick=(-1 if g["votes"].get(sub) == "skip" else _ma_idx(g, g["votes"][sub])) if sub in g["votes"] else None)
    out["act"] = act
    if role == "sheriff":
        out["checks"] = [dict(p=_ma_idx(g, c["t"]), m=c["m"], d=c["d"]) for c in g["checks"].get(sub, [])]
    out["log"] = g["log"][-40:]
    out["chat"] = [dict(id=c["id"], ch=c["ch"], f=c["f"], t=c["t"]) for c in g["chat"]
                   if _ma_visible(g, sub, c["ch"])][-80:]
    if st == "over":
        out["winner"] = g["winner"]
    out["vc"], out["vk"] = _gv_view("mafia", g)
    return out


@app.route("/api/mafia/create", methods=["POST"])
def api_mafia_create():
    sub, tok = auth_sub()
    if not rate_hit("mafc", sub, 12, 60):
        abort(429)
    nm = _ma_names(tok)
    now = time.time()
    with META_LOCK:
        _ma_cleanup(now)
        if len(MAFIA) >= MAF_MAX:
            abort(503)
        for _ in range(50):
            c = "".join(secrets.choice(CODE_CHARS) for _ in range(5))
            if c not in MAFIA:
                break
        else:
            abort(503)
        g = dict(code=c, players=[sub], names={sub: nm}, seen={sub: now}, size=MAF_MAXP, host=sub,
                 v=1, upd=now, round=1, cid=0)
        _ma_reset_game(g)
        _ensure_cv(g); MAFIA[c] = g
        return jsonify(_ma_view(g, sub))


@app.route("/api/mafia/join", methods=["POST"])
def api_mafia_join():
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    if not code:
        abort(400)
    nm = _ma_names(tok)
    now = time.time()
    with META_LOCK:
        g = MAFIA.get(code)
    with META_LOCK:
        g = MAFIA.get(code)
        if not g:
            abort(404)
        _ma_tick(g, now)
        if sub not in g["players"]:
            if len(g["players"]) >= g["size"] or g["status"] != "wait":
                return jsonify(error="full"), 403
            g["players"].append(sub)
            g["names"][sub] = nm
            g["seen"][sub] = now
            _ma_touch(g)
        else:
            g["names"][sub] = nm
            if now - g["seen"].get(sub, 0) > 30:
                _ma_touch(g)                    # qayıdıb: digərləri onlayn görsün
        g["seen"][sub] = now
        return jsonify(_ma_view(g, sub))


@app.route("/api/mafia/poll")
def api_mafia_poll():
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
    g = _ma_get(code, sub)
    with _ensure_cv(g):
        now = time.time()
        _ma_tick(g, now)
        if now - g["seen"].get(sub, 0) > 30:
            g["seen"][sub] = now
            _ma_touch(g)
        g["seen"][sub] = now
        while g["v"] == v and (vk is None or g.get("vk", 0) == vk):
            now = time.time()
            left = deadline - now
            if left <= 0:
                break
            wt = left
            if g["status"] == "play":
                wt = min(left, max(0.05, g["deadline"] - now + 0.05))   # mərhələ vaxtı bitəndə oyan
                if g.get("bots"):
                    wt = min(wt, 1.0)               # botlar üçün tez-tez yoxla
            g["_cv"].wait(timeout=wt)
            if MAFIA.get(code) is not g:
                abort(404)
            now = time.time()
            g["seen"][sub] = now
            _ma_tick(g, now)
        resp = jsonify(_ma_view(g, sub))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/mafia/check")
def api_mafia_check():
    sub, tok = auth_sub()
    code = clean_room(request.args.get("room"))
    with META_LOCK:
        g = MAFIA.get(code)
    with META_LOCK:
        g = MAFIA.get(code)
        if not g or sub not in g["players"] or g["gone"].get(sub):
            abort(404)
        return jsonify(ok=True, status=g["status"])


@app.route("/api/mafia/start", methods=["POST"])
def api_mafia_start():
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    g = _ma_get(code, sub)
    with _ensure_cv(g):
        now = time.time()
        g["seen"][sub] = now
        if g["status"] == "wait" and g.get("host") == sub:
            if len(g["players"]) < MAF_MIN:
                return jsonify(error="Başlamaq üçün ən azı %d oyunçu lazımdır" % MAF_MIN), 409
            _ma_start(g, now)
            _ma_touch(g)
        return jsonify(_ma_view(g, sub))


@app.route("/api/mafia/bots", methods=["POST"])
def api_mafia_bots():
    """Test üçün: otaq sahibi gözləmə otağına bot əlavə edir / botları silir."""
    sub, tok = auth_sub()
    if not rate_hit("mafb", sub, 60, 60):
        abort(429)
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    g = _ma_get(code, sub)
    with _ensure_cv(g):
        now = time.time()
        g["seen"][sub] = now
        if g["status"] == "wait" and g.get("host") == sub:
            if d.get("clear"):
                for b in [p for p in g["players"] if _ma_is_bot(p)]:
                    g["players"].remove(b)
                    g["names"].pop(b, None)
                    g["seen"].pop(b, None)
                g["bots"] = False
                _ma_touch(g)
            else:
                try:
                    n = max(1, min(int(d.get("n", 1)), MAF_MAXP))
                except (TypeError, ValueError):
                    abort(400)
                used = {(g["names"].get(p) or {}).get("name") for p in g["players"]}
                for _ in range(n):
                    if len(g["players"]) >= g["size"]:
                        break
                    free = [x for x in MAF_BOT_NAMES if "🤖 " + x not in used]
                    nmx = ("🤖 " + (free[0] if free else "Bot%d" % (len(g["players"]) + 1)))
                    b = "bot:" + secrets.token_hex(4)
                    g["players"].append(b)
                    g["names"][b] = dict(name=nmx, username="", avatar="", badge=False)
                    g["seen"][b] = now
                    used.add(nmx)
                g["bots"] = True
                _ma_touch(g)
        return jsonify(_ma_view(g, sub))


@app.route("/api/mafia/act", methods=["POST"])
def api_mafia_act():
    sub, tok = auth_sub()
    if not rate_hit("mafa", sub, 120, 60):
        abort(429)
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    kind = str(d.get("kind", ""))
    g = _ma_get(code, sub)
    with _ensure_cv(g):
        now = time.time()
        g["seen"][sub] = now
        _ma_tick(g, now)
        if g["status"] != "play" or not g["alive"].get(sub):
            return jsonify(_ma_view(g, sub))        # köhnə klik: cari vəziyyət
        ps = g["players"]
        ph = g["phase"]
        role = g["roles"][sub]
        tgt = None
        if kind in ("kill", "save", "check", "vote") and not (kind == "vote" and d.get("t") == -1):
            try:
                ti = int(d.get("t"))
            except (TypeError, ValueError):
                abort(400)
            if not 0 <= ti < len(ps) or not g["alive"].get(ps[ti]):
                abort(400)
            tgt = ps[ti]
        if kind == "kill" and ph == "night" and role == "mafia":
            if tgt == sub or g["roles"][tgt] == "mafia":
                abort(400)
            g["n_kill"][sub] = tgt
        elif kind == "save" and ph == "night" and role == "doctor":
            if tgt == g["last_save"]:
                abort(400)                          # eyni adamı ardıcıl iki gecə qorumaq olmaz
            g["n_save"] = tgt
        elif kind == "check" and ph == "night" and role == "sheriff":
            if tgt == sub or g["n_check"]:
                abort(400)
            g["n_check"] = True
            g["checks"].setdefault(sub, []).append(dict(t=tgt, m=(g["roles"][tgt] == "mafia"), d=g["day"]))
        elif kind == "skip" and ph == "day":
            if sub not in g["skip"]:
                g["skip"].append(sub)
        elif kind == "vote" and ph == "vote":
            if tgt is None:
                g["votes"][sub] = "skip"
            elif tgt == sub:
                abort(400)
            else:
                g["votes"][sub] = tgt
        else:
            return jsonify(_ma_view(g, sub))        # bu mərhələdə belə hərəkət yoxdur
        _ma_touch(g)
        _ma_tick(g, now)
        return jsonify(_ma_view(g, sub))


@app.route("/api/mafia/chat", methods=["POST"])
def api_mafia_chat():
    sub, tok = auth_sub()
    if not rate_hit("mafm", sub, 20, 30):
        abort(429)
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    text = re.sub(r"\s+", " ", str(d.get("text") or "")).strip()[:MAF_MSG_LEN]
    if not text:
        abort(400)
    g = _ma_get(code, sub)
    with _ensure_cv(g):
        now = time.time()
        g["seen"][sub] = now
        _ma_tick(g, now)
        if g["status"] != "play":
            ch = "all"
        elif not g["alive"].get(sub):
            ch = "dead"
        elif g["phase"] == "night":
            if g["roles"].get(sub) != "mafia":
                return jsonify(error="Gecə yalnız mafiya yaza bilər"), 403
            ch = "maf"
        else:
            ch = "all"
        g["cid"] += 1
        g["chat"].append(dict(id=g["cid"], ch=ch, f=_ma_idx(g, sub), t=text))
        del g["chat"][:-MAF_CHAT_KEEP]
        _ma_touch(g)
        return jsonify(_ma_view(g, sub))


@app.route("/api/mafia/again", methods=["POST"])
def api_mafia_again():
    sub, tok = auth_sub()
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    g = _ma_get(code, sub)
    with _ensure_cv(g):
        g["seen"][sub] = time.time()
        if g["status"] == "over" and g.get("host") == sub:
            for p in [p for p in g["players"] if g["gone"].get(p)]:
                g["players"].remove(p)
                g["names"].pop(p, None)
                g["seen"].pop(p, None)
            g["round"] += 1
            g["cid"] = 0
            _ma_reset_game(g)
            _ma_touch(g)
        return jsonify(_ma_view(g, sub))


@app.route("/api/mafia/leave", methods=["POST"])
def api_mafia_leave():
    """Geri düyməsi: oyunda qalır (qayıtmaq olur). Gözləmə otağında yer boşalır."""
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    with META_LOCK:
        g = MAFIA.get(code)
    with META_LOCK:
        g = MAFIA.get(code)
        if g and sub in g["players"]:
            if g["status"] == "wait":
                _ma_drop_wait(g, code, sub)
            else:
                g["seen"][sub] = 0
                _ma_touch(g)
    return jsonify(ok=True)


def _ma_drop_wait(g, code, sub):
    g["players"].remove(sub)
    g["names"].pop(sub, None)
    g["seen"].pop(sub, None)
    hum = [x for x in g["players"] if not _ma_is_bot(x)]
    if not hum:
        MAFIA.pop(code, None)
        return
    if g.get("host") == sub:
        g["host"] = hum[0]
    _ma_touch(g)


@app.route("/api/mafia/quit", methods=["POST"])
def api_mafia_quit():
    """Oyundan həqiqətən çıx: gedən oyunda ölü sayılırsan və rolun açılır."""
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    with META_LOCK:
        g = MAFIA.get(code)
    with META_LOCK:
        g = MAFIA.get(code)
        if not g or sub not in g["players"]:
            return jsonify(ok=True)
        now = time.time()
        if g["status"] == "wait":
            _ma_drop_wait(g, code, sub)
        elif g["status"] == "play":
            _ma_remove_play(g, sub, now)
            if not _ma_humans(g):
                MAFIA.pop(code, None)
            else:
                _ma_touch(g)
        else:                                       # over
            g["gone"][sub] = True
            if g.get("host") == sub:
                nxt = next((p for p in g["players"] if not g["gone"].get(p) and not _ma_is_bot(p)), None)
                if nxt:
                    g["host"] = nxt
            if not _ma_humans(g):
                MAFIA.pop(code, None)
            else:
                _ma_touch(g)
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), threaded=True)
