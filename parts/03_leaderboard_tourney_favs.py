# ---------------------------------------------------------------
#  Bəyəndiklərim, reytinq, həftəlik liderlər, turnirlər, rate limit, SQLite ehtiyat nüsxəsi
#  Mühit dəyişənləri (istəyə görə): DB_BACKUP_DIR, BACKUP_HOURS (standart 6), BACKUP_KEEP (standart 8)
# ---------------------------------------------------------------
BACKUP_DIR = os.environ.get("DB_BACKUP_DIR", os.path.join(os.path.dirname(DB_PATH) or HERE, "backups"))
BACKUP_HOURS = max(1, float(os.environ.get("BACKUP_HOURS", "6")))
BACKUP_KEEP = max(1, int(os.environ.get("BACKUP_KEEP", "8")))
GAMES = ("domino", "chess", "durak", "mafia", "uno")
BAKU = 4 * 3600   # Bakı vaxtı UTC+4


def init_extra():
    with DBL:
        c = sqlite3.connect(DB_PATH, timeout=10)
        try:
            c.executescript("""
CREATE TABLE IF NOT EXISTS favs(sub TEXT NOT NULL, url TEXT NOT NULL, title TEXT, thumb TEXT, ts REAL NOT NULL, PRIMARY KEY(sub, url));
CREATE TABLE IF NOT EXISTS game_results(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, game TEXT NOT NULL, sub TEXT NOT NULL, res TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS gr_game_ts ON game_results(game, ts);
CREATE INDEX IF NOT EXISTS gr_sub ON game_results(sub, ts);
CREATE TABLE IF NOT EXISTS tourneys(id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, game TEXT NOT NULL, title TEXT NOT NULL, t0 REAL NOT NULL, t1 REAL NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tourney_players(tid INTEGER NOT NULL, sub TEXT NOT NULL, ts REAL NOT NULL, PRIMARY KEY(tid, sub));
""")
            c.commit()
        finally:
            c.close()


init_extra()


# ---- Oyun nəticələri: qalibiyyət 3 xal, bərabərlik 1, məğlubiyyət 0. Botlu oyunlar və tək insanlı oyunlar sayılmır ----
def _human(p):
    return isinstance(p, str) and p and not p.startswith("bot")


def record_game(game, winners=(), losers=(), draws=()):
    humans = {p for p in list(winners) + list(losers) + list(draws) if _human(p)}
    if game not in GAMES or len(humans) < 2:
        return
    now = time.time()
    rows = [(now, game, p, "w") for p in winners if _human(p)] + \
           [(now, game, p, "l") for p in losers if _human(p)] + \
           [(now, game, p, "d") for p in draws if _human(p)]
    try:
        with DBL:
            c = sqlite3.connect(DB_PATH, timeout=10)
            try:
                c.executemany("INSERT INTO game_results(ts,game,sub,res) VALUES(?,?,?,?)", rows)
                c.commit()
            finally:
                c.close()
    except Exception:
        pass


def rec_game(kind, g):
    """Bitmiş oyunu bir dəfə qeyd edir (raund nömrəsi ilə təkrarın qarşısı alınır). Heç vaxt istisna atmır."""
    try:
        if g.get("bot") or g.get("_rr") == g.get("round"):
            return
        pl = [p for p in g.get("players", [])]
        if kind == "domino":
            m = g.get("match")
            if not m:
                return
            g["_rr"] = g.get("round")
            record_game("domino", [m], [p for p in pl if p != m])
        elif kind == "chess":
            if g.get("status") != "over":
                return
            g["_rr"] = g.get("round")
            w = g.get("win")
            if w:
                record_game("chess", [w], [p for p in pl if p != w])
            else:
                record_game("chess", draws=pl)
        elif kind == "durak":
            eng = g.get("eng")
            if g.get("status") != "over" or not eng or not eng.over:
                return
            g["_rr"] = g.get("round")
            lo = eng.loser
            if lo:
                record_game("durak", [p for p in pl if p != lo], [lo])
            else:
                record_game("durak", draws=pl)
        elif kind == "uno":
            eng = g.get("eng")
            if g.get("status") != "over" or not eng or not eng.over:
                return
            g["_rr"] = g.get("round")
            if eng.winner:
                record_game("uno", [eng.winner], [p for p in pl if p != eng.winner])
            elif eng.quitter:
                record_game("uno", [p for p in pl if p != eng.quitter], [eng.quitter])
        elif kind == "mafia":
            w = g.get("winner")
            if g.get("status") != "over" or not w:
                return
            g["_rr"] = g.get("round")
            roles = g.get("roles") or {}
            hum = [p for p in pl if _human(p) and not (g.get("gone") or {}).get(p)]
            win_t = [p for p in hum if ("mafia" if roles.get(p) == "mafia" else "town") == w]
            record_game("mafia", win_t, [p for p in hum if p not in win_t])
    except Exception:
        pass


def week_start(now=None):
    """Bazar ertəsi 00:00 (Bakı vaxtı) UTC timestamp kimi."""
    now = now or time.time()
    local = now + BAKU
    day0 = local - (local % 86400)
    wd = datetime.datetime.utcfromtimestamp(day0).weekday()   # 0 = bazar ertəsi
    return day0 - wd * 86400 - BAKU


def _board(game, t0, t1, limit=20, subs=None):
    q = "SELECT sub, SUM(res='w') AS w, SUM(res='l') AS l, SUM(res='d') AS d FROM game_results WHERE ts>=? AND ts<?"
    a = [t0, t1]
    if game in GAMES:
        q += " AND game=?"
        a.append(game)
    if subs is not None:
        if not subs:
            return []
        q += " AND sub IN (%s)" % ",".join("?" * len(subs))
        a += list(subs)
    q += " GROUP BY sub"
    rows = [dict(r) for r in dbq(q, tuple(a), many=True)]
    for r in rows:
        r["pts"] = r["w"] * 3 + r["d"]
        r["n"] = r["w"] + r["l"] + r["d"]
    rows.sort(key=lambda r: (-r["pts"], -r["w"], r["n"]))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows


def _who(sub):
    u = dbq("SELECT name, username, (SELECT ts FROM avatars a WHERE a.sub=users.sub) AS av FROM users WHERE sub=?", (sub,), one=True)
    if not u or not u["username"]:
        return None
    return dict(name=u["name"], username=u["username"], avatar=_av_url(u["username"], u["av"]), badge=is_owner(sub))


def _decorate(rows, limit=None):
    out = []
    for r in rows:
        w = _who(r["sub"])
        if not w:
            continue
        out.append(dict(w, rank=r["rank"], w=r["w"], l=r["l"], d=r["d"], pts=r["pts"], n=r["n"], sub=r["sub"]))
        if limit and len(out) >= limit:
            break
    return out


@app.route("/api/leaderboard")
def api_leaderboard():
    sub, _ = auth_sub()
    game = request.args.get("game", "all")
    period = request.args.get("period", "week")
    now = time.time()
    ws = week_start(now)
    if period == "all":
        t0, t1 = 0, now + 1
    else:
        t0, t1 = ws, now + 1
    rows = _board(game, t0, t1)
    top = _decorate(rows, 20)
    me = next((dict(rank=r["rank"], w=r["w"], l=r["l"], d=r["d"], pts=r["pts"], n=r["n"]) for r in rows if r["sub"] == sub), None)
    last = _decorate(_board(game, ws - 7 * 86400, ws), 3) if period == "week" else []
    for x in top + last:
        x["me"] = (x.pop("sub") == sub)
    return _nostore(jsonify(top=top, me=me, last_week=last, week_ends=ws + 7 * 86400, now=now))


# ---- Turnirlər: iştirakçıların seçilmiş oyunda, turnir müddətində topladığı xallar ----
def _t_json(t, sub):
    now = time.time()
    n = dbq("SELECT COUNT(*) AS c FROM tourney_players WHERE tid=?", (t["id"],), one=True)["c"]
    joined = bool(dbq("SELECT 1 FROM tourney_players WHERE tid=? AND sub=?", (t["id"], sub), one=True))
    ow = _who(t["owner"])
    return dict(id=t["id"], game=t["game"], title=t["title"], t0=t["t0"], t1=t["t1"], players=n, joined=joined,
                active=now < t["t1"], owner=(ow or {}).get("name", ""), mine=(t["owner"] == sub))


@app.route("/api/tourneys")
def api_tourneys():
    sub, _ = auth_sub()
    rows = dbq("SELECT * FROM tourneys WHERE t1>? ORDER BY t1 ASC LIMIT 30", (time.time() - 3 * 86400,), many=True)
    return _nostore(jsonify(items=[_t_json(t, sub) for t in rows]))


@app.route("/api/tourney")
def api_tourney():
    sub, _ = auth_sub()
    try:
        t = dbq("SELECT * FROM tourneys WHERE id=?", (int(request.args.get("id", 0)),), one=True)
    except (TypeError, ValueError):
        abort(400)
    if not t:
        abort(404)
    subs = [r["sub"] for r in dbq("SELECT sub FROM tourney_players WHERE tid=?", (t["id"],), many=True)]
    rows = _board(t["game"], t["t0"], min(time.time(), t["t1"]) + 1, subs=subs)
    have = {r["sub"] for r in rows}
    for s in subs:   # hələ oynamayanlar da siyahıda görünsün
        if s not in have:
            rows.append(dict(sub=s, w=0, l=0, d=0, pts=0, n=0, rank=len(rows) + 1))
    top = _decorate(rows, 50)
    for x in top:
        x["me"] = (x.pop("sub") == sub)
    return _nostore(jsonify(t=_t_json(t, sub), top=top))


@app.route("/api/tourney/create", methods=["POST"])
def api_tourney_create():
    sub, _ = auth_sub()
    if not rate_hit("tcreate", sub, 3, 3600):
        abort(429)
    d = request.get_json(silent=True) or {}
    game = str(d.get("game", ""))
    if game not in GAMES:
        abort(400)
    title = clean_text(d.get("title"), 40) or "Turnir"
    try:
        days = max(1, min(14, int(d.get("days") or 3)))
    except (TypeError, ValueError):
        days = 3
    now = time.time()
    if dbq("SELECT COUNT(*) AS c FROM tourneys WHERE owner=? AND t1>?", (sub, now), one=True)["c"] >= 3:
        return jsonify(error="Eyni anda ən çox 3 aktiv turnirin ola bilər"), 400
    dbq("INSERT INTO tourneys(owner,game,title,t0,t1,ts) VALUES(?,?,?,?,?,?)", (sub, game, title, now, now + days * 86400, now))
    tid = dbq("SELECT MAX(id) AS m FROM tourneys WHERE owner=?", (sub,), one=True)["m"]
    dbq("INSERT OR IGNORE INTO tourney_players(tid,sub,ts) VALUES(?,?,?)", (tid, sub, now))
    return jsonify(ok=True, id=tid)


@app.route("/api/tourney/join", methods=["POST"])
def api_tourney_join():
    sub, _ = auth_sub()
    if not rate_hit("tjoin", sub, 30, 600):
        abort(429)
    try:
        tid = int((request.get_json(silent=True) or {}).get("id"))
    except (TypeError, ValueError):
        abort(400)
    t = dbq("SELECT * FROM tourneys WHERE id=?", (tid,), one=True)
    if not t or time.time() >= t["t1"]:
        return jsonify(error="Turnir bitib"), 400
    if dbq("SELECT COUNT(*) AS c FROM tourney_players WHERE tid=?", (tid,), one=True)["c"] >= 200:
        return jsonify(error="Turnir doludur"), 400
    dbq("INSERT OR IGNORE INTO tourney_players(tid,sub,ts) VALUES(?,?,?)", (tid, sub, time.time()))
    return jsonify(ok=True)


@app.route("/api/tourney/leave", methods=["POST"])
def api_tourney_leave():
    sub, _ = auth_sub()
    try:
        tid = int((request.get_json(silent=True) or {}).get("id"))
    except (TypeError, ValueError):
        abort(400)
    t = dbq("SELECT * FROM tourneys WHERE id=?", (tid,), one=True)
    if t and time.time() < t["t1"]:
        dbq("DELETE FROM tourney_players WHERE tid=? AND sub=?", (tid, sub))
    return jsonify(ok=True)


@app.route("/api/tourney/delete", methods=["POST"])
def api_tourney_delete():
    sub, _ = auth_sub()
    try:
        tid = int((request.get_json(silent=True) or {}).get("id"))
    except (TypeError, ValueError):
        abort(400)
    if dbq("SELECT 1 FROM tourneys WHERE id=? AND owner=?", (tid, sub), one=True):
        dbq("DELETE FROM tourney_players WHERE tid=?", (tid,))
        dbq("DELETE FROM tourneys WHERE id=?", (tid,))
    return jsonify(ok=True)


# ---- Bəyəndiklərim (video siyahısı) ----
@app.route("/api/favs")
def api_favs():
    sub, _ = auth_sub()
    rows = dbq("SELECT url,title,thumb,ts FROM favs WHERE sub=? ORDER BY ts DESC LIMIT 200", (sub,), many=True)
    return _nostore(jsonify(items=[dict(r) for r in rows]))


@app.route("/api/fav/add", methods=["POST"])
def api_fav_add():
    sub, _ = auth_sub()
    if not rate_hit("fav", sub, 60, 600):
        abort(429)
    d = request.get_json(silent=True) or {}
    url = str(d.get("url") or "")[:1000]
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        abort(400)
    if dbq("SELECT COUNT(*) AS c FROM favs WHERE sub=?", (sub,), one=True)["c"] >= 200:
        return jsonify(error="Siyahı doludur (200)"), 400
    vid = yt_id(url)
    title = clean_text(d.get("title"), 120) or ((yt_title(vid) if vid else "") or p.hostname)
    thumb = "https://i.ytimg.com/vi/" + vid + "/mqdefault.jpg" if vid else ""
    dbq("INSERT OR REPLACE INTO favs(sub,url,title,thumb,ts) VALUES(?,?,?,?,?)", (sub, url, title, thumb, time.time()))
    return jsonify(ok=True)


@app.route("/api/fav/remove", methods=["POST"])
def api_fav_remove():
    sub, _ = auth_sub()
    url = str((request.get_json(silent=True) or {}).get("url") or "")[:1000]
    dbq("DELETE FROM favs WHERE sub=? AND url=?", (sub, url))
    return jsonify(ok=True)


# ---- Ümumi rate limit (yazan sorğular üçün IP başına; polling və axınlara toxunmur) ----
RL_RULES = (("/api/upload", 20, 3600), ("/api/dm/media", 60, 3600), ("/api/report", 20, 3600),
            ("/api/friend/request", 40, 600), ("/api/invite", 40, 600), ("/api/fav/", 90, 600),
            ("/api/tourney", 60, 600), ("/api/leaderboard", 120, 60))
RL_SKIP = ("/poll", "/stream", "/presence", "/check", "/api/gvoice", "/api/domino/move", "/api/chess/move",
           "/api/durak/move", "/api/mafia/act", "/api/mafia/chat", "/api/durak/bot", "/api/domino/claim",
           "/api/uno/move", "/api/uno/bot")


@app.before_request
def rate_limit_gate():
    p = request.path
    if not p.startswith("/api/"):
        return None
    if p == "/api/send":   # hesab başına (token), tokensiz qonaq üçün IP başına
        key = request.headers.get("X-Token", "")[:24] or client_ip()
        if not rate_hit("rl:send", key, 300, 60):
            return jsonify(error="Çox sorğu. Bir az gözlə."), 429
        return None
    if any(x in p for x in RL_SKIP):
        return None
    ip = client_ip()
    for pre, lim, win in RL_RULES:
        if p.startswith(pre):
            if not rate_hit("rl:" + pre, ip, lim, win):
                return jsonify(error="Çox sorğu. Bir az gözlə."), 429
            break
    if request.method == "POST" and not rate_hit("rl:post", ip, 300, 60):
        return jsonify(error="Çox sorğu. Bir az gözlə."), 429
    return None


