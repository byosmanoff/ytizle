# ---------------------------------------------------------------
#  UNO: 2–4 nəfərlik onlayn kart oyunu (insanlarla və ya kompüterə qarşı).
#  Kartlar [rəng, dəyər]: rəng R/Y/G/B (qırmızı/sarı/yaşıl/mavi) və ya K (qara, joker).
#  Dəyər: 0-9, S (keç), V (istiqamət), D (+2), W (rəng seç), F (+4 və rəng seç).
#  Qaydalar: 7 kart paylanır; rəngə və ya dəyərə uyğun kart atılır; uyğun kart yoxdursa 1 kart çəkilir,
#  çəkilən kart uyğundursa atmaq və ya pas keçmək olar. +2/+4 yığılmır. Kartı ilk bitirən qalib olur.
# ---------------------------------------------------------------
UNO_COLORS = "RYGB"


class UnoEngine:
    def __init__(self, players, first=0):
        self.players = list(players)
        self.hands = {p: [] for p in self.players}
        self.deck = self._fresh()
        random.shuffle(self.deck)
        for _ in range(7):
            for p in self.players:
                self.hands[p].append(self.deck.pop())
        while True:                                   # başlanğıc kart yalnız rəqəmli ola bilər
            c = self.deck.pop()
            if c[0] != "K" and c[1].isdigit():
                break
            self.deck.insert(0, c)
        self.discard = [c]
        self.color = c[0]
        self.dir = 1
        self.turn = first % len(self.players)
        self.drawn = None
        self.over = False
        self.winner = None
        self.quitter = None
        self.seq = 0
        self.last = None

    @staticmethod
    def _fresh():
        d = []
        for c in UNO_COLORS:
            d.append([c, "0"])
            for v in "123456789SVD":
                d.append([c, v]); d.append([c, v])
        for _ in range(4):
            d.append(["K", "W"]); d.append(["K", "F"])
        return d

    # --- köməkçi ---
    def cur(self):
        return self.players[self.turn]

    def can(self, card):
        top = self.discard[-1]
        return card[0] == "K" or card[0] == self.color or card[1] == top[1]

    def _adv(self, k=1):
        self.turn = (self.turn + self.dir * k) % len(self.players)

    def _take(self, p, k):
        got = 0
        for _ in range(k):
            if not self.deck:
                if len(self.discard) <= 1:
                    break
                top = self.discard.pop()
                self.deck = self.discard
                self.discard = [top]
                random.shuffle(self.deck)
            self.hands[p].append(self.deck.pop())
            got += 1
        return got

    def _ev(self, t, p, **kw):
        self.seq += 1
        self.last = dict(k=self.seq, t=t, p=self.players.index(p), **kw)

    def _check(self, p):
        if self.over:
            raise GameError("Oyun bitib")
        if self.cur() != p:
            raise GameError("Növbə sənin deyil")

    # --- hərəkətlər ---
    def play(self, p, card, color=None):
        self._check(p)
        card = [str(card[0]), str(card[1])]
        hand = self.hands[p]
        if card not in hand:
            raise GameError("Bu kart səndə yoxdur")
        if self.drawn is not None and card != self.drawn:
            raise GameError("Çəkdiyin kartı at və ya pas keç")
        if not self.can(card):
            raise GameError("Bu kartı atmaq olmaz")
        if card[0] == "K" and color not in tuple(UNO_COLORS):
            raise GameError("Rəng seç")
        hand.remove(card)
        self.discard.append(card)
        self.color = color if card[0] == "K" else card[0]
        self.drawn = None
        n = len(self.players)
        fx, victim, took = "", -1, 0
        v = card[1]
        if not hand:
            self.over = True
            self.winner = p
            self._ev("play", p, card=card, color=self.color, fx="", victim=-1, took=0, uno=False)
            return
        if v == "V":
            if n == 2:
                fx = "skip"                           # 2 nəfərdə istiqamət dəyişmək = rəqibi keçmək
                self._adv(2)
            else:
                fx = "rev"
                self.dir *= -1
                self._adv(1)
        elif v == "S":
            fx = "skip"
            victim = (self.turn + self.dir) % n
            self._adv(2)
        elif v in ("D", "F"):
            victim = (self.turn + self.dir) % n
            took = self._take(self.players[victim], 2 if v == "D" else 4)
            fx = "d2" if v == "D" else "d4"
            self._adv(2)
        else:
            self._adv(1)
        self._ev("play", p, card=card, color=self.color, fx=fx, victim=victim, took=took, uno=(len(hand) == 1))

    def draw(self, p):
        self._check(p)
        if self.drawn is not None:
            raise GameError("Artıq kart çəkmisən")
        got = self._take(p, 1)
        if got and self.can(self.hands[p][-1]):
            self.drawn = list(self.hands[p][-1])
            self._ev("draw", p, n=1, playable=True)
        else:
            self._ev("draw", p, n=got, playable=False)
            self._adv(1)

    def pass_turn(self, p):
        self._check(p)
        if self.drawn is None:
            raise GameError("Əvvəl kart çək")
        self.drawn = None
        self._ev("pass", p)
        self._adv(1)

    def actions(self, p):
        if self.over or self.cur() != p:
            return dict(play=[], draw=False, pass_=False)
        if self.drawn is not None:
            return dict(play=[list(self.drawn)], draw=False, pass_=True)
        return dict(play=[c for c in self.hands[p] if self.can(c)], draw=True, pass_=False)

    # --- kompüter ---
    def bot_step(self, bots):
        p = self.cur()
        if self.over or p not in bots:
            return False
        hand = self.hands[p]
        if self.drawn is not None:
            c = self.drawn
            self.play(p, c, self._bot_color(hand, c))
            return True
        ok = [c for c in hand if self.can(c)]
        if not ok:
            self.draw(p)
            return True
        nxt = self.hands[self.players[(self.turn + self.dir) % len(self.players)]]
        def score(c):
            s = random.random()
            if c[0] == "K":
                s -= 2.0                                  # jokeri saxla
                if len(nxt) <= 2 or len(hand) <= 3:
                    s += 3.0
            elif c[1] in "SVD":
                s += 1.2 + (1.5 if len(nxt) <= 2 else 0)
            elif c[1].isdigit():
                s += int(c[1]) / 12.0
            return s
        c = max(ok, key=score)
        self.play(p, c, self._bot_color(hand, c))
        return True

    @staticmethod
    def _bot_color(hand, c):
        if c[0] != "K":
            return None
        cnt = {k: 0 for k in UNO_COLORS}
        for x in hand:
            if x[0] in cnt:
                cnt[x[0]] += 1
        best = max(cnt.values())
        return random.choice([k for k, n in cnt.items() if n == best])

    # --- yadda saxlama ---
    def to_dict(self):
        return dict(players=self.players, hands=self.hands, deck=self.deck, discard=self.discard, color=self.color,
                    dir=self.dir, turn=self.turn, drawn=self.drawn, over=self.over, winner=self.winner,
                    quitter=self.quitter, seq=self.seq, last=self.last)

    @classmethod
    def from_dict(cls, d):
        e = cls.__new__(cls)
        for k, v in d.items():
            setattr(e, k, v)
        return e


UNO = {}
UNO_IDLE = 3 * 3600
UNO_MAX = 200


def _un_touch(g):
    g["v"] += 1
    g["upd"] = time.time()
    rec_game("uno", g)
    _notify(g)
    _un_save(g)


def _un_db_init():
    dbq("CREATE TABLE IF NOT EXISTS uno_games(code TEXT PRIMARY KEY, data TEXT NOT NULL, ts REAL NOT NULL)")


def _un_save(g):
    try:
        d = {k: v for k, v in g.items() if k not in ("eng", "seen")}
        d["eng"] = g["eng"].to_dict() if g.get("eng") else None
        dbq("INSERT OR REPLACE INTO uno_games(code, data, ts) VALUES(?,?,?)",
            (g["code"], json.dumps(d, separators=(",", ":"), default=str), g["upd"]))
    except Exception:
        pass


def _un_drop(code):
    UNO.pop(code, None)
    try:
        dbq("DELETE FROM uno_games WHERE code=?", (code,))
    except Exception:
        pass


def _un_load():
    now = time.time()
    try:
        _un_db_init()
        dbq("DELETE FROM uno_games WHERE ts<?", (now - UNO_IDLE,))
        rows = dbq("SELECT code, data FROM uno_games", many=True) or []
    except Exception:
        return
    for r in rows:
        try:
            g = json.loads(r["data"])
            g["eng"] = UnoEngine.from_dict(g["eng"]) if g.get("eng") else None
            bots = g.get("bots") or []
            g["seen"] = {p: (now if p in bots else 0) for p in g["players"]}
            g["upd"] = now
            g.setdefault("vk", 0)
            g.setdefault("quit", None)
            _ensure_cv(g)
            UNO[g["code"]] = g
        except Exception:
            continue


def _un_cleanup(now):
    for c in [c for c, g in UNO.items() if now - g["upd"] > UNO_IDLE]:
        _un_drop(c)


def _un_get(code, sub):
    g = UNO.get(code)
    if not g or sub not in g["players"]:
        abort(404)
    return g


def _un_start(g):
    first = (g["round"] - 1) % max(1, len(g["players"]))
    g["eng"] = UnoEngine(list(g["players"]), first)
    g["status"] = "play"


def _un_view(g, sub):
    now = time.time()
    ps = g["players"]
    eng = g["eng"]
    bots = g.get("bots") or ()
    out = dict(v=g["v"], code=g["code"], status=g["status"], size=g["size"], joined=len(ps), round=g["round"],
               host=bool(ps and ps[0] == sub), me=ps.index(sub) if sub in ps else -1, hand=[], top=None, color="",
               dir=1, turn=-1, deck=0, playable=[], can_draw=False, can_pass=False, drawn=None, last=None, quit=None)
    if eng:
        act = eng.actions(sub)
        out.update(hand=eng.hands.get(sub, []), top=eng.discard[-1], color=eng.color, dir=eng.dir,
                   turn=eng.turn if not eng.over else -1, deck=len(eng.deck), playable=act["play"],
                   can_draw=act["draw"], can_pass=act["pass_"], drawn=eng.drawn if eng.cur() == sub else None,
                   last=eng.last)
        if eng.over:
            out["over"] = True
            out["winner"] = ps.index(eng.winner) if eng.winner in ps else -1
            out["loser"] = ps.index(eng.quitter) if eng.quitter in ps else -1
    players = []
    for i, p in enumerate(ps):
        nm = g["names"].get(p) or {}
        players.append(dict(name=nm.get("name", ""), avatar=nm.get("avatar", ""), username=nm.get("username", ""),
                            me=(p == sub), online=bool(p in bots or now - g["seen"].get(p, 0) < 35),
                            n=len(eng.hands.get(p, [])) if eng else 0))
    out["players"] = players
    if g.get("quit"):
        out["quit"] = ps.index(g["quit"]) if g["quit"] in ps else None
    if g.get("bot"):
        out["bot"] = True
        out["botturn"] = bool(eng and not eng.over and g["status"] == "play" and eng.cur() in bots)
    out["vc"], out["vk"] = _gv_view("uno", g)
    return out


@app.route("/api/uno/create", methods=["POST"])
def api_uno_create():
    sub, tok = auth_sub()
    if not rate_hit("unoc", sub, 12, 60):
        abort(429)
    d0 = request.get_json(silent=True) or {}
    try:
        size = max(2, min(4, int(d0.get("size") or 2)))
    except (TypeError, ValueError):
        size = 2
    vs_bot = bool(d0.get("bot"))
    nm = _du_names(tok)
    now = time.time()
    with META_LOCK:
        _un_cleanup(now)
        if len(UNO) >= UNO_MAX:
            abort(503)
        for _ in range(50):
            c = "".join(secrets.choice(CODE_CHARS) for _ in range(5))
            if c not in UNO and c not in DURAK and c not in DOMINO and c not in CHESS:
                break
        else:
            abort(503)
        g = dict(code=c, players=[sub], names={sub: nm}, seen={sub: now}, size=size, status="wait", eng=None,
                 v=1, vk=0, upd=now, round=1, quit=None)
        if vs_bot:
            bots = ["bot%d" % i for i in range(1, size)]
            for i, b in enumerate(bots, 1):
                g["players"].append(b)
                g["names"][b] = dict(name=("Bot %d" % i) if size > 2 else "Kompüter", username="bot", avatar="", badge=None)
                g["seen"][b] = now
            g["bot"] = True
            g["bots"] = bots
            _un_start(g)
        _ensure_cv(g); UNO[c] = g
        _un_save(g)
        return jsonify(_un_view(g, sub))


@app.route("/api/uno/join", methods=["POST"])
def api_uno_join():
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    if not code:
        abort(400)
    nm = _du_names(tok)
    now = time.time()
    with META_LOCK:
        g = UNO.get(code)
        if not g:
            abort(404)
        if sub not in g["players"]:
            if len(g["players"]) >= g["size"] or g["status"] != "wait":
                return jsonify(error="full"), 403
            g["players"].append(sub)
            g["names"][sub] = nm
            g["seen"][sub] = now
            if len(g["players"]) >= g["size"]:
                _un_start(g)
            _un_touch(g)
        else:
            g["names"][sub] = nm
        g["seen"][sub] = now
        return jsonify(_un_view(g, sub))


@app.route("/api/uno/poll")
def api_uno_poll():
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
    g = _un_get(code, sub)
    with _ensure_cv(g):
        now = time.time()
        if now - g["seen"].get(sub, 0) > 30:
            g["seen"][sub] = now
            _un_touch(g)
        g["seen"][sub] = now
        while g["v"] == v and (vk is None or g.get("vk", 0) == vk):
            left = deadline - time.time()
            if left <= 0:
                break
            g["_cv"].wait(timeout=left)
            if UNO.get(code) is not g:
                abort(404)
            g["seen"][sub] = time.time()
        resp = jsonify(_un_view(g, sub))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/uno/check")
def api_uno_check():
    sub, tok = auth_sub()
    code = clean_room(request.args.get("room"))
    with META_LOCK:
        g = UNO.get(code)
        if not g or sub not in g["players"]:
            abort(404)
        return jsonify(ok=True, status=g["status"])


@app.route("/api/uno/move", methods=["POST"])
def api_uno_move():
    sub, tok = auth_sub()
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    act = str(d.get("action", ""))
    g = _un_get(code, sub)
    with _ensure_cv(g):
        g["seen"][sub] = time.time()
        eng = g["eng"]
        if g["status"] != "play" or eng is None:
            return jsonify(_un_view(g, sub))
        try:
            if act == "play":
                c = d.get("card")
                col = d.get("color")
                eng.play(sub, [str(c[0]), str(c[1])], str(col) if col else None)
            elif act == "draw":
                eng.draw(sub)
            elif act == "pass":
                eng.pass_turn(sub)
            else:
                abort(400)
        except GameError as e:
            return jsonify(error=str(e)), 400
        except (TypeError, ValueError, IndexError, KeyError):
            abort(400)
        if eng.over:
            g["status"] = "over"
        _un_touch(g)
        return jsonify(_un_view(g, sub))


@app.route("/api/uno/again", methods=["POST"])
def api_uno_again():
    sub, tok = auth_sub()
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    g = _un_get(code, sub)
    with _ensure_cv(g):
        g["seen"][sub] = time.time()
        try:
            rd = int(d.get("round", 0))
        except (TypeError, ValueError):
            rd = 0
        if g["status"] == "over" and rd == g["round"]:
            g["round"] += 1
            q = g.get("quit")
            if q:
                g["quit"] = None
                if q in g["players"]:
                    g["players"].remove(q)
                g["names"].pop(q, None)
                g["seen"].pop(q, None)
                g["eng"] = None
                g["status"] = "wait"
            elif len(g["players"]) == g["size"]:
                _un_start(g)
            else:
                g["eng"] = None
                g["status"] = "wait"
            _un_touch(g)
        return jsonify(_un_view(g, sub))


@app.route("/api/uno/leave", methods=["POST"])
def api_uno_leave():
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    with META_LOCK:
        g = UNO.get(code)
        if g and sub in g["players"] and g["status"] == "wait":
            if g["players"][0] == sub:
                _un_drop(code)
            else:
                g["players"].remove(sub)
                g["names"].pop(sub, None)
                g["seen"].pop(sub, None)
                _un_touch(g)
        elif g and sub in g["players"]:
            g["seen"][sub] = 0
            _un_touch(g)
    return jsonify(ok=True)


@app.route("/api/uno/quit", methods=["POST"])
def api_uno_quit():
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    with META_LOCK:
        g = UNO.get(code)
        if not g or sub not in g["players"]:
            return jsonify(ok=True)
        if g.get("bot"):
            _un_drop(code)
            return jsonify(ok=True)
        if g["status"] == "wait":
            if g["players"][0] == sub:
                _un_drop(code)
                return jsonify(ok=True)
            g["players"].remove(sub)
            g["names"].pop(sub, None)
            g["seen"].pop(sub, None)
        elif g["status"] == "play" and g["eng"] and not g["eng"].over:
            eng = g["eng"]
            eng.over = True
            eng.winner = None
            eng.quitter = sub
            g["status"] = "over"
            g["quit"] = sub
        elif g["status"] == "over" and not g.get("quit"):
            g["quit"] = sub
        _un_touch(g)
    return jsonify(ok=True)


@app.route("/api/uno/bot", methods=["POST"])
def api_uno_bot():
    """Kompüterin bir hərəkəti. Klient növbə kompüterdə olanda çağırır (botturn=true)."""
    sub, tok = auth_sub()
    if not rate_hit("unob", sub, 300, 60):
        abort(429)
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    g = _un_get(code, sub)
    with _ensure_cv(g):
        g["seen"][sub] = time.time()
        eng = g["eng"]
        if not g.get("bot") or g["status"] != "play" or eng is None:
            return jsonify(_un_view(g, sub))
        try:
            eng.bot_step(set(g.get("bots") or ()))
        except GameError:
            pass
        if eng.over:
            g["status"] = "over"
        _un_touch(g)
        return jsonify(_un_view(g, sub))


_un_load()



#  Oyun səsli otağı (domino / şahmat): WebRTC siqnalı. Səsin özü cihazlar arasında gedir,
#  server yalnız offer/answer/ice mesajlarını ötürür. Domino-da 4 nəfərə qədər (hər kəs hər kəslə).
#  Oyunun öz dict-indən ayrı saxlanır (kompüter hərəkəti deepcopy edir, SDP mesajları ora düşməsin).
