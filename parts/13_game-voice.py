#  Oyun səsli otağı (domino / şahmat): WebRTC siqnalı. Səsin özü cihazlar arasında gedir,
#  server yalnız offer/answer/ice mesajlarını ötürür. Domino-da 4 nəfərə qədər (hər kəs hər kəslə).
#  Oyunun öz dict-indən ayrı saxlanır (kompüter hərəkəti deepcopy edir, SDP mesajları ora düşməsin).
# ---------------------------------------------------------------
GV = {}              # (oyun, kod) -> {"mem": {vid: {name, muted, ts}}, "q": [mesajlar], "s": say}
GV_TTL = 45          # long-poll bu müddətdən uzun yoxdursa, iştirakçı "səsdə" sayılmır
GV_KINDS = ("domino", "chess", "durak")
GV_SIG = ("offer", "answer", "ice")


def _gv_id(code, sub):
    """Oyunçunun səs otağındakı açıq id-si (həqiqi hesab id-si başqalarına göndərilmir)."""
    return hashlib.sha1(("gv:%s:%s" % (code, sub)).encode()).hexdigest()[:10]


def _gv_data(kind, code):
    if len(GV) > 400:   # bitmiş oyunların səs məlumatını at
        for k in [k for k in GV if k[1] not in _game_store(k[0])]:
            GV.pop(k, None)
    return GV.setdefault((kind, code), {"mem": {}, "q": [], "s": 0})


def _gv_view(kind, g):
    """Oyun vəziyyətinə əlavə: hazırda səs otağında olanlar və dəyişiklik sayğacı."""
    d = GV.get((kind, g["code"]))
    now = time.time()
    vc = [dict(id=i, name=m["name"]) for i, m in d["mem"].items() if now - m["ts"] < GV_TTL] if d else []
    return vc, g.get("vk", 0)


def _gv_game(kind, code, sub):
    g = _game_store(kind).get(code) if kind in GV_KINDS else None
    if not g or sub not in g["players"]:
        abort(404)
    return g


@app.route("/api/gvoice/send", methods=["POST"])
def api_gvoice_send():
    sub, tok = auth_sub()
    d = request.get_json(silent=True) or {}
    kind = d.get("game")
    code = clean_room(d.get("room"))
    k = str(d.get("k") or "")
    if kind not in GV_KINDS or not code or k not in ("hello", "bye", "mute") + GV_SIG:
        abort(400)
    payload = d.get("d")
    if len(json.dumps(payload)) > 12000:
        abort(400)
    if not rate_hit("gvs", sub, 600, 60):
        abort(429)
    to = str(d.get("to") or "")[:12] if k in GV_SIG else ""
    me = _gv_id(code, sub)
    now = time.time()
    members = []
    with CV:
        g = _gv_game(kind, code, sub)
        v = _gv_data(kind, code)
        nm = (g["names"].get(sub) or {}).get("name") or "Qonaq"
        if k == "hello":
            members = [dict(id=i, name=m["name"], muted=m["muted"]) for i, m in v["mem"].items()
                       if i != me and now - m["ts"] < GV_TTL]
            v["mem"][me] = dict(name=nm, muted=bool(d.get("muted")), ts=now)
            g["vk"] = g.get("vk", 0) + 1
        elif k == "bye":
            if v["mem"].pop(me, None) is not None:
                g["vk"] = g.get("vk", 0) + 1
        elif me not in v["mem"]:
            abort(403)   # offer/answer/ice/mute yalnız səs otağındakı oyunçudan
        elif k == "mute":
            v["mem"][me]["muted"] = bool(payload)
        v["s"] += 1
        v["q"].append(dict(s=v["s"], f=me, to=to, k=k, d=payload, n=nm))
        del v["q"][:-300]
        CV.notify_all()
        return jsonify(seq=v["s"], id=me, members=members)


@app.route("/api/gvoice/poll")
def api_gvoice_poll():
    sub, tok = auth_sub()
    kind = request.args.get("game")
    code = clean_room(request.args.get("room"))
    try:
        since = int(request.args.get("since", "0"))
    except ValueError:
        abort(400)
    me = _gv_id(code or "", sub)
    deadline = time.time() + 20
    with CV:
        _gv_game(kind, code, sub)
        v = _gv_data(kind, code)
        if since > v["s"]:   # server yenidən başlayıb
            since = 0
        while True:
            now = time.time()
            m = v["mem"].get(me)
            if m:
                m["ts"] = now
            new = [x for x in v["q"] if x["s"] > since and x["f"] != me and x["to"] in ("", me)]
            left = deadline - now
            if new or left <= 0:
                break
            CV.wait(timeout=left)
            if code not in _game_store(kind):
                abort(404)
        resp = jsonify(seq=v["s"], msgs=new)
    resp.headers["Cache-Control"] = "no-store"
    return resp
