"""Mafiya otaqları: Flask marşrutları. app.py-dan register(app, ctx) ilə qoşulur.
Durak ilə eyni üsul: yaddaşda otaq, long-poll, CV şərti."""
import os
import time
import secrets

from flask import request, jsonify, abort, send_file

import mafia_engine as ME

MAF = {}
MAF_IDLE = 3 * 3600
MAF_MAX = 100


def register(app, ctx):
    CV = ctx["CV"]
    auth_sub = ctx["auth_sub"]
    clean_room = ctx["clean_room"]
    rate_hit = ctx["rate_hit"]
    ident_for = ctx["ident_for"]
    CODE_CHARS = ctx["CODE_CHARS"]
    HERE = ctx["HERE"]

    # ---------------- köməkçilər ----------------
    def touch(g):
        g["v"] += 1
        g["upd"] = time.time()
        CV.notify_all()

    def drop(code):
        MAF.pop(code, None)

    def cleanup(now):
        for c in [c for c, g in MAF.items() if now - g["upd"] > MAF_IDLE]:
            drop(c)

    def names(tok):
        i = ident_for(tok, "")
        return dict(name=i["name"], username=i["username"], avatar=i["avatar"], badge=i["badge"])

    def get(code, sub):
        g = MAF.get(code)
        if not g or sub not in g["players"]:
            abort(404)
        return g

    def tick(g):
        """Vaxtı bitən mərhələni irəlilət (kilid daxilində çağırılır)."""
        eng = g["eng"]
        if g["status"] == "play" and eng and eng.tick(time.time()):
            if eng.over:
                g["status"] = "over"
            touch(g)

    def next_deadline(g):
        eng = g["eng"]
        if g["status"] == "play" and eng and not eng.over:
            return eng.deadline
        return None

    def humans_left(g):
        eng = g["eng"]
        for p in g["players"]:
            if p in g["bots"] or p in g.get("gone", ()):
                continue
            if eng is None or p in eng.alive:
                return True
        return False

    def view(g, sub):
        now = time.time()
        ps = g["players"]
        eng = g["eng"]
        out = dict(v=g["v"], code=g["code"], status=g["status"], size=g["size"], joined=len(ps),
                   round=g["round"], host=bool(ps and ps[0] == sub),
                   me=ps.index(sub) if sub in ps else -1, min=ME.MIN_PLAYERS)
        ev = eng.view(sub, now) if eng else None
        if ev:
            out.update(ev)
        pl = []
        for i, p in enumerate(ps):
            nm = g["names"].get(p) or {}
            d = dict(name=nm.get("name", ""), avatar=nm.get("avatar", ""), username=nm.get("username", ""),
                     me=(p == sub), bot=(p in g["bots"]),
                     online=bool(p in g["bots"] or now - g["seen"].get(p, 0) < 35))
            if ev and i < len(ev["players"]):
                d.update(ev["players"][i])
            pl.append(d)
        out["players"] = pl
        if not eng:
            out["chat"] = [list(x) for x in g.get("lobby_chat", [])][-60:]
            out["canchat"] = True
        return out

    # ---------------- marşrutlar ----------------
    @app.route("/mafia")
    def mafia_page():
        resp = send_file(os.path.join(HERE, "mafia.html"))
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.route("/api/mafia/create", methods=["POST"])
    def api_mafia_create():
        sub, tok = auth_sub()
        if not rate_hit("mafc", sub, 12, 60):
            abort(429)
        nm = names(tok)
        now = time.time()
        with CV:
            cleanup(now)
            if len(MAF) >= MAF_MAX:
                abort(503)
            for _ in range(50):
                c = "".join(secrets.choice(CODE_CHARS) for _ in range(5))
                if c not in MAF:
                    break
            else:
                abort(503)
            g = dict(code=c, players=[sub], names={sub: nm}, seen={sub: now}, size=ME.MAX_PLAYERS,
                     status="wait", eng=None, v=1, upd=now, round=1, bots=set(), gone=set(), nbots=0)
            MAF[c] = g
            return jsonify(view(g, sub))

    @app.route("/api/mafia/join", methods=["POST"])
    def api_mafia_join():
        sub, tok = auth_sub()
        code = clean_room((request.get_json(silent=True) or {}).get("room"))
        if not code:
            abort(400)
        nm = names(tok)
        now = time.time()
        with CV:
            g = MAF.get(code)
            if not g:
                abort(404)
            if sub not in g["players"]:
                if g["status"] != "wait":
                    return jsonify(error="started"), 403
                if len(g["players"]) >= g["size"]:
                    return jsonify(error="full"), 403
                g["players"].append(sub)
                g["names"][sub] = nm
                g["seen"][sub] = now
                touch(g)
            else:
                g["names"][sub] = nm
            g["seen"][sub] = now
            return jsonify(view(g, sub))

    @app.route("/api/mafia/poll")
    def api_mafia_poll():
        sub, tok = auth_sub()
        code = clean_room(request.args.get("room"))
        try:
            v = int(request.args.get("v", "0"))
        except ValueError:
            abort(400)
        deadline = time.time() + 20
        with CV:
            g = get(code, sub)
            now = time.time()
            if now - g["seen"].get(sub, 0) > 30:
                g["seen"][sub] = now
                touch(g)
            g["seen"][sub] = now
            tick(g)
            while g["v"] == v:
                now = time.time()
                left = deadline - now
                if left <= 0:
                    break
                nd = next_deadline(g)
                if nd is not None:
                    left = min(left, max(nd - now, 0) + 0.05)
                CV.wait(timeout=left)
                g = MAF.get(code)
                if not g:
                    abort(404)
                g["seen"][sub] = time.time()
                tick(g)
            resp = jsonify(view(g, sub))
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.route("/api/mafia/check")
    def api_mafia_check():
        sub, tok = auth_sub()
        code = clean_room(request.args.get("room"))
        with CV:
            g = MAF.get(code)
            if not g or sub not in g["players"]:
                abort(404)
            return jsonify(ok=True, status=g["status"])

    @app.route("/api/mafia/bot", methods=["POST"])
    def api_mafia_bot():
        """Yalnız gözləmə otağında, yalnız otaq sahibi: bot əlavə et / sil."""
        sub, tok = auth_sub()
        d = request.get_json(silent=True) or {}
        code = clean_room(d.get("room"))
        act = str(d.get("action", "add"))
        with CV:
            g = get(code, sub)
            if g["status"] != "wait" or g["players"][0] != sub:
                return jsonify(error="host"), 403
            now = time.time()
            if act == "add":
                try:
                    cnt = max(1, min(11, int(d.get("count") or 1)))
                except (TypeError, ValueError):
                    cnt = 1
                for _ in range(cnt):
                    if len(g["players"]) >= g["size"]:
                        break
                    g["nbots"] += 1
                    b = "bot%d" % g["nbots"]
                    g["players"].append(b)
                    g["bots"].add(b)
                    g["names"][b] = dict(name="Bot %d" % g["nbots"], username="bot", avatar="", badge=None)
                    g["seen"][b] = now
            elif act == "remove":
                bots = [p for p in g["players"] if p in g["bots"]]
                if bots:
                    b = bots[-1]
                    g["players"].remove(b)
                    g["bots"].discard(b)
                    g["names"].pop(b, None)
                    g["seen"].pop(b, None)
            else:
                abort(400)
            touch(g)
            return jsonify(view(g, sub))

    @app.route("/api/mafia/start", methods=["POST"])
    def api_mafia_start():
        sub, tok = auth_sub()
        code = clean_room((request.get_json(silent=True) or {}).get("room"))
        with CV:
            g = get(code, sub)
            if g["players"][0] != sub:
                return jsonify(error="host"), 403
            if g["status"] == "wait":
                if len(g["players"]) < ME.MIN_PLAYERS:
                    return jsonify(error="few"), 400
                g["gone"] = set()
                g["lobby_chat"] = []
                g["eng"] = ME.Mafia(list(g["players"]), bots=g["bots"], now=time.time())
                g["status"] = "play"
                touch(g)
            return jsonify(view(g, sub))

    @app.route("/api/mafia/act", methods=["POST"])
    def api_mafia_act():
        sub, tok = auth_sub()
        d = request.get_json(silent=True) or {}
        code = clean_room(d.get("room"))
        with CV:
            g = get(code, sub)
            g["seen"][sub] = time.time()
            eng = g["eng"]
            tick(g)
            if g["status"] != "play" or eng is None:
                return jsonify(view(g, sub))
            try:
                t = int(d.get("target"))
                if not 0 <= t < len(g["players"]):
                    raise ValueError
            except (TypeError, ValueError):
                abort(400)
            try:
                eng.night_action(sub, g["players"][t], time.time())
            except ME.GameError as e:
                return jsonify(error=str(e)), 400
            if eng.over:
                g["status"] = "over"
            touch(g)
            return jsonify(view(g, sub))

    @app.route("/api/mafia/vote", methods=["POST"])
    def api_mafia_vote():
        sub, tok = auth_sub()
        d = request.get_json(silent=True) or {}
        code = clean_room(d.get("room"))
        with CV:
            g = get(code, sub)
            g["seen"][sub] = time.time()
            eng = g["eng"]
            tick(g)
            if g["status"] != "play" or eng is None:
                return jsonify(view(g, sub))
            try:
                t = int(d.get("target"))
            except (TypeError, ValueError):
                abort(400)
            if t == -1:
                target = ME.SKIP
            elif 0 <= t < len(g["players"]):
                target = g["players"][t]
            else:
                abort(400)
            try:
                eng.vote(sub, target, time.time())
            except ME.GameError as e:
                return jsonify(error=str(e)), 400
            if eng.over:
                g["status"] = "over"
            touch(g)
            return jsonify(view(g, sub))

    @app.route("/api/mafia/chat", methods=["POST"])
    def api_mafia_chat():
        sub, tok = auth_sub()
        d = request.get_json(silent=True) or {}
        code = clean_room(d.get("room"))
        if not rate_hit("mafm", sub, 25, 30):
            abort(429)
        with CV:
            g = get(code, sub)
            g["seen"][sub] = time.time()
            eng = g["eng"]
            if eng is None:                       # gözləmə otağı: hamı yaza bilir
                eng_chat = g.setdefault("lobby_chat", [])
                text = " ".join(str(d.get("text") or "").split())[:200]
                if text:
                    eng_chat.append((g["players"].index(sub), "all", text))
                    del eng_chat[:-60]
                    touch(g)
                return jsonify(view(g, sub))
            tick(g)
            try:
                if eng.add_chat(sub, d.get("text"), time.time()):
                    touch(g)
            except ME.GameError as e:
                return jsonify(error=str(e)), 400
            return jsonify(view(g, sub))

    @app.route("/api/mafia/again", methods=["POST"])
    def api_mafia_again():
        sub, tok = auth_sub()
        d = request.get_json(silent=True) or {}
        code = clean_room(d.get("room"))
        with CV:
            g = get(code, sub)
            g["seen"][sub] = time.time()
            try:
                rd = int(d.get("round", 0))
            except (TypeError, ValueError):
                rd = 0
            if g["status"] == "over" and rd == g["round"]:
                g["round"] += 1
                # çıxanlar silinir, oyun gözləmə otağına qayıdır
                for p in list(g["players"]):
                    if p in g.get("gone", ()) and p not in g["bots"]:
                        g["players"].remove(p)
                        g["names"].pop(p, None)
                        g["seen"].pop(p, None)
                g["gone"] = set()
                g["eng"] = None
                g["status"] = "wait"
                touch(g)
            return jsonify(view(g, sub))

    @app.route("/api/mafia/leave", methods=["POST"])
    def api_mafia_leave():
        """Geri düyməsi. Gözləmədə yer boşalır (sahib çıxsa otaq bağlanır), oyunda 'əlaqədə deyil' görünür."""
        sub, tok = auth_sub()
        code = clean_room((request.get_json(silent=True) or {}).get("room"))
        with CV:
            g = MAF.get(code)
            if g and sub in g["players"] and g["status"] == "wait":
                if g["players"][0] == sub:
                    drop(code)
                else:
                    g["players"].remove(sub)
                    g["names"].pop(sub, None)
                    g["seen"].pop(sub, None)
                    touch(g)
            elif g and sub in g["players"]:
                g["seen"][sub] = 0
                touch(g)
        return jsonify(ok=True)

    @app.route("/api/mafia/quit", methods=["POST"])
    def api_mafia_quit():
        """Oyundan həmişəlik çıx: oyunçu ölmüş sayılır, rolu açılır."""
        sub, tok = auth_sub()
        code = clean_room((request.get_json(silent=True) or {}).get("room"))
        with CV:
            g = MAF.get(code)
            if not g or sub not in g["players"]:
                return jsonify(ok=True)
            if g["status"] == "play" and g["eng"]:
                g["gone"].add(sub)
                g["eng"].leave_game(sub, time.time())
                if g["eng"].over:
                    g["status"] = "over"
                elif not humans_left(g):
                    drop(code)
                    return jsonify(ok=True)
                touch(g)
            elif g["status"] == "over":
                g["gone"].add(sub)
                touch(g)
            return jsonify(ok=True)
