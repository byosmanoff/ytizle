# ---------------------------------------------------------------
#  Şəxsi mesajlar (DM): yalnız dostlar arasında, SQLite-da saxlanır
# ---------------------------------------------------------------
DM_MAX = 1000
DM_IMG_MAX = 10 * 1024 * 1024
DM_AUDIO_MAX = 10 * 1024 * 1024
DM_VIDEO_MAX = int(os.environ.get("DM_VIDEO_MB", "40")) * 1024 * 1024
# UPLOAD_DIR hər başlanğıcda silinir, ona görə DM faylları bazanın yanında (daimi diskdə) saxlanır
DM_MEDIA_DIR = os.environ.get("DM_MEDIA_DIR", os.path.join(os.path.dirname(DB_PATH) or HERE, "dm_media"))
os.makedirs(DM_MEDIA_DIR, exist_ok=True)
DM_MIME = {".jpg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
           ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
           ".weba": "audio/webm", ".ogg": "audio/ogg", ".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav"}
DM_COLS = "id, from_sub, text, ts, read, delivered, kind, media, dur"


def _dm_clean(raw):
    t = str(raw or "").replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t[:DM_MAX]


def _dm_status(r):
    return "seen" if r["read"] else ("delivered" if r["delivered"] else "sent")


def _dm_preview(kind, text):
    if kind == "image":
        return "\U0001F4F7 Şəkil"
    if kind == "video":
        return "\U0001F3AC Video"
    if kind == "audio":
        return "\U0001F3A4 Səs mesajı"
    if kind == "call":
        return "\U0001F4DE " + text
    return text


def _dm_msg(r, me):
    kind = r["kind"] or "text"
    m = dict(id=r["id"], text=r["text"], ts=int(r["ts"] * 1000), mine=(r["from_sub"] == me), kind=kind,
             status=_dm_status(r))
    if kind in ("image", "video", "audio") and r["media"]:
        m["url"] = "/dmf/" + r["media"]
    if kind in ("audio", "video", "call"):
        m["dur"] = int(r["dur"] or 0)
    return m


def _dm_sniff(head, kind):
    """Faylın ilk baytlarına görə real formatı təyin edir (yalnız təhlükəsiz media formatları)."""
    if len(head) < 12:
        return None
    if kind == "image":
        if head[:3] == b"\xff\xd8\xff":
            return ".jpg"
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return ".png"
        if head[:4] == b"GIF8":
            return ".gif"
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            return ".webp"
    elif kind == "video":
        if head[4:8] == b"ftyp":
            return ".mov" if head[8:12] == b"qt  " else ".mp4"
        if head[:4] == b"\x1a\x45\xdf\xa3":
            return ".webm"
    elif kind == "audio":
        if head[:4] == b"\x1a\x45\xdf\xa3":
            return ".weba"
        if head[:4] == b"OggS":
            return ".ogg"
        if head[4:8] == b"ftyp":
            return ".m4a"
        if head[:3] == b"ID3" or (head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
            return ".mp3"
        if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
            return ".wav"
    return None


def _dm_insert(frm, to, text, kind="text", media=None, dur=0):
    dbq("INSERT INTO dm(from_sub,to_sub,text,ts,read,delivered,kind,media,dur) VALUES(?,?,?,?,0,0,?,?,?)",
        (frm, to, text, time.time(), kind, media, int(dur or 0)))
    return dbq("SELECT " + DM_COLS + " FROM dm WHERE from_sub=? AND to_sub=? ORDER BY id DESC LIMIT 1", (frm, to), one=True)


@app.route("/api/dm/list")
def api_dm_list():
    sub, _ = auth_sub()
    dbq("UPDATE dm SET delivered=1 WHERE to_sub=? AND delivered=0", (sub,))   # cihaza çatdı
    rows = dbq("SELECT peer, MAX(id) AS last_id, SUM(un) AS unread FROM ("
               "SELECT CASE WHEN from_sub=? THEN to_sub ELSE from_sub END AS peer, id, "
               "CASE WHEN to_sub=? AND read=0 THEN 1 ELSE 0 END AS un "
               "FROM dm WHERE from_sub=? OR to_sub=?) GROUP BY peer ORDER BY last_id DESC LIMIT 60",
               (sub, sub, sub, sub), many=True)
    convs = []
    for r in rows:
        peer = r["peer"]
        u = dbq("SELECT name, username, (SELECT ts FROM avatars a WHERE a.sub=users.sub) AS av FROM users WHERE sub=?", (peer,), one=True)
        if not u or not u["username"] or _is_blocked_either(sub, peer):
            continue
        last = dbq("SELECT " + DM_COLS + " FROM dm WHERE id=?", (r["last_id"],), one=True)
        if not last:
            continue
        pres = _presence_info(sub, peer)
        mine = (last["from_sub"] == sub)
        convs.append(dict(username=u["username"], name=u["name"], avatar=_av_url(u["username"], u["av"]),
                          badge=is_owner(peer), online=bool(pres.get("online")),
                          last=dict(text=_dm_preview(last["kind"] or "text", last["text"])[:120], ts=int(last["ts"] * 1000),
                                    mine=mine, kind=last["kind"] or "text", status=(_dm_status(last) if mine else "")),
                          unread=int(r["unread"] or 0)))
    resp = jsonify(convs=convs)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/dm/thread")
def api_dm_thread():
    sub, _ = auth_sub()
    other = _user_by_name(request.args.get("u"))
    if other == sub:
        abort(400)
    if _is_blocked_either(sub, other):
        return jsonify(error="Bu istifadəçi ilə yazışmaq mümkün deyil"), 403
    try:
        after = max(0, int(request.args.get("after") or 0))
    except ValueError:
        after = 0
    rows = dbq("SELECT * FROM (SELECT " + DM_COLS + " FROM dm WHERE id>? AND "
               "((from_sub=? AND to_sub=?) OR (from_sub=? AND to_sub=?)) ORDER BY id DESC LIMIT 200) ORDER BY id",
               (after, sub, other, other, sub), many=True)
    if any(r["from_sub"] == other for r in rows) or after == 0:
        dbq("UPDATE dm SET read=1, delivered=1 WHERE to_sub=? AND from_sub=? AND read=0", (sub, other))
    # göndərdiyim mesajların vəziyyəti: bu id-yə qədər hamısı çatıb / görülüb
    st = dbq("SELECT MAX(CASE WHEN read=1 THEN id END) AS seen, "
             "MAX(CASE WHEN delivered=1 OR read=1 THEN id END) AS dlv FROM dm WHERE from_sub=? AND to_sub=?",
             (sub, other), one=True)
    msgs = [_dm_msg(r, sub) for r in rows]
    resp = jsonify(msgs=msgs, seen_upto=int((st and st["seen"]) or 0), deliv_upto=int((st and st["dlv"]) or 0))
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _dm_check_pair(sub, other):
    if _is_blocked_either(sub, other):
        return jsonify(error="Bu istifadəçiyə yazmaq mümkün deyil"), 403
    if _friend_rel(sub, other) != "friend":
        return jsonify(error="Yalnız dostlara mesaj yaza bilərsən"), 403
    return None


@app.route("/api/dm/send", methods=["POST"])
def api_dm_send():
    sub, tok = auth_sub()
    d = request.get_json(silent=True) or {}
    other = _user_by_name(d.get("to"))
    if other == sub:
        abort(400)
    text = _dm_clean(d.get("text"))
    if not text:
        return jsonify(error="Mesaj boşdur"), 400
    bad = _dm_check_pair(sub, other)
    if bad:
        return bad
    if not rate_hit("dm", sub, 40, 60):
        return jsonify(error="Çox tez göndərirsən, bir az gözlə"), 429
    row = _dm_insert(sub, other, text)
    idn = ident_for(tok)
    notify_user(other, "dm", payload=dict(u=idn["username"]),
                push=dict(title=idn["name"], body=text[:100], tag="dm-" + str(idn["username"]), url="/"))
    return jsonify(msg=_dm_msg(row, sub))


@app.route("/api/dm/media", methods=["POST"])
def api_dm_media():
    """Şəkil / video / səs mesajı: xam bədən (raw body). ?to=istifadəçi&kind=image|video|audio&dur=saniyə"""
    sub, tok = auth_sub()
    other = _user_by_name(request.args.get("to"))
    if other == sub:
        abort(400)
    kind = str(request.args.get("kind") or "")
    if kind not in ("image", "video", "audio"):
        abort(400)
    limit = {"image": DM_IMG_MAX, "video": DM_VIDEO_MAX, "audio": DM_AUDIO_MAX}[kind]
    size = request.content_length or 0
    if size <= 0:
        abort(411)
    if size > limit:
        return jsonify(error="Fayl çox böyükdür (maks. %d MB)" % (limit // 1048576)), 413
    bad = _dm_check_pair(sub, other)
    if bad:
        return bad
    if not rate_hit("dmmedia", sub, 30, 3600):
        return jsonify(error="Çox fayl göndərdin, bir az sonra yenidən yoxla"), 429
    try:
        dur = max(0, min(3600, int(float(request.args.get("dur") or 0))))
    except ValueError:
        dur = 0
    tmp = os.path.join(DM_MEDIA_DIR, "tmp-" + secrets.token_hex(8))
    got, head = 0, b""
    try:
        with open(tmp, "wb") as f:
            while True:
                chunk = request.stream.read(256 * 1024)
                if not chunk:
                    break
                if len(head) < 16:
                    head += chunk[:16 - len(head)]
                got += len(chunk)
                if got > limit:
                    raise ValueError("big")
                f.write(chunk)
        ext = _dm_sniff(head, kind) if got == size else None
        if not ext:
            os.remove(tmp)
            return jsonify(error="Bu format dəstəklənmir"), 415
        name = secrets.token_hex(16) + ext
        os.replace(tmp, os.path.join(DM_MEDIA_DIR, name))
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return jsonify(error="Yükləmə alınmadı"), 400
    row = _dm_insert(sub, other, "", kind, name, dur)
    idn = ident_for(tok)
    notify_user(other, "dm", payload=dict(u=idn["username"]),
                push=dict(title=idn["name"], body=_dm_preview(kind, ""), tag="dm-" + str(idn["username"]), url="/"))
    return jsonify(msg=_dm_msg(row, sub))


@app.route("/dmf/<name>")
def serve_dm_media(name):
    m = re.fullmatch(r"[0-9a-f]{32}(\.[a-z0-9]{3,4})", name)
    if not m or m.group(1) not in DM_MIME:
        abort(404)
    resp = send_from_directory(DM_MEDIA_DIR, name, conditional=True, mimetype=DM_MIME[m.group(1)])   # Range: video/səs irəli-geri çəkilir
    resp.headers["Cache-Control"] = "private, max-age=86400"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


