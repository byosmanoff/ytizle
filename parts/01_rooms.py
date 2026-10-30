# ---------------------------------------------------------------
#  Otaq sistemi (iki telefon arasında mesaj/sinxron ötürücü)
#  Yaddaşda saxlanır, ona görə:  gunicorn --workers 1 --threads 16 app:app
#  (long-poll hər sorğunu 20 san tutur; --threads olmadan server donur)
# ---------------------------------------------------------------
ROOMS = {}
CV = threading.Condition()
PEER_TTL = 45
CODE_CHARS = "abcdefghjkmnpqrstuvwxyz23456789"
MAX_ROOMS = 300
MAX_MEMBERS = 30
MAX_HTML = 5 * 1024 * 1024  # proxy ilə yüklənən HTML üçün yuxarı limit

# Otağa yüklənən videolar (otaq silinəndə və ya server yenidən başlayanda təmizlənir).
# MAX_UPLOAD_MB: bir videonun yuxarı həddi. UPLOAD_DIR: fayl qovluğu.
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads"))
MAX_UPLOAD = int(os.environ.get("MAX_UPLOAD_MB", "5120")) * 1024 * 1024
VID_EXT = {".mp4": ".mp4", ".m4v": ".mp4", ".webm": ".webm", ".mov": ".mov", ".ogg": ".ogg"}
shutil.rmtree(UPLOAD_DIR, ignore_errors=True)   # otaqlar yaddaşda olduğu üçün köhnə fayllar yetim qalır
os.makedirs(UPLOAD_DIR, exist_ok=True)


def room_dir(code):
    return os.path.join(UPLOAD_DIR, code)


def clean_room(c):
    c = re.sub(r"[^a-z0-9-]", "", str(c or "").lower())[:30]
    return c or None


def get_room(code):
    r = ROOMS.get(code)
    if r is None:
        cleanup(time.time())
        if len(ROOMS) >= MAX_ROOMS:  # send/poll ilə sonsuz otaq yaradılmasın
            abort(503)
        r = {"seq": 0, "msgs": [], "peers": {}, "created": time.time(),
             "locked": False, "members": set(),
             "host": None, "roles": {}, "hostonly": False, "rv": 0, "hist": []}
        ROOMS[code] = r
    return r


def admit(r, pid):
    """Üzvü qəbul et. Otaq kilidlidirsə yalnız əvvəl girmiş cihazlar keçə bilər."""
    if pid in r["members"]:
        return
    if r["locked"] or len(r["members"]) >= MAX_MEMBERS:
        abort(403)
    r["members"].add(pid)


CTRL_TYPES = {"play", "pause", "seek", "sync", "load", "wv", "q"}   # videonu idarə edən mesajlar
HIST_KEEP = 300


def role_of(r, pid):
    if r.get("host") == pid:
        return "host"
    return "mod" if r["roles"].get(pid) == "mod" else "viewer"


def can_ctl(r, pid):
    return (not r["hostonly"]) or role_of(r, pid) != "viewer"


MSG_REACTS = ["\u2764\ufe0f", "😂", "😮", "😭", "👍", "🔥", "👏"]   # mesaja verilə bilən reaksiyalar (index.html ilə eyni)
RX_MAX_MSGS = 400   # otaqda eyni anda saxlanan reaksiyalı mesaj sayı


def rx_key(code, pid):
    """Reaksiya verənin otaq daxilində açıq (hash) açarı: həqiqi cihaz id-si başqalarına göndərilmir."""
    return hashlib.sha1(("rx:%s:%s" % (code, pid)).encode()).hexdigest()[:10]


def rx_canon(e):
    """Gələn emojini siyahıdakı dəqiq formaya gətirir ('' = reaksiyanı sil). Etibarsızdırsa None."""
    if e == "":
        return ""
    if not isinstance(e, str):
        return None
    t = e.replace("\ufe0f", "")
    for x in MSG_REACTS:
        if x.replace("\ufe0f", "") == t:
            return x
    return None


HOST_GRACE = 90   # host səssizcə qopubsa (tab bağlanıb, şəbəkə getdi) bu qədər saniyədən sonra host keçir


def pass_host(r, now):
    """Host otaqdan çıxanda host ilk moderatora keçir (moderatorluq verilmə sırası ilə).
    Yalnız hazırda otaqda olan moderator seçilir. Moderator yoxdursa host dəyişmir.
    CV tutulu halda çağırılır. Keçid olubsa True qaytarır."""
    old = r.get("host")
    for p, role in list(r["roles"].items()):
        if role != "mod" or p == old:
            continue
        ts = r["peers"].get(p)
        if ts is None or now - ts >= PEER_TTL:
            continue   # bu moderator hazırda otaqda deyil
        r["host"] = p
        r["roles"].pop(p, None)
        r["rv"] += 1
        return True
    return False


LEFT_GRACE = 25  # çıxandan sonra gec qalan long-poll sorğusu üzvü yenidən "içəridə" göstərməsin


def alive_mark(r, pid, now):
    """Üzvün aktivlik vaxtını yenilə (yalnız yeni çıxmayıbsa)."""
    if now - r.get("left", {}).get(pid, 0) > LEFT_GRACE:
        r["peers"][pid] = now


def count_others(r, me, now):
    return sum(1 for pid, ts in r["peers"].items() if pid != me and now - ts < PEER_TTL)


def prune_peers(r, now):
    # köhnə (bağlanmış) bağlantı id-ləri yaddaşda yığılmasın
    for pid in [p for p, ts in r["peers"].items() if now - ts > PEER_TTL * 4]:
        del r["peers"][pid]
        r.get("who", {}).pop(pid, None)


def cleanup(now):
    dead = []
    for k, r in ROOMS.items():
        last = max([r["created"]] + list(r["peers"].values()))
        if now - last > 3600:
            dead.append(k)
        else:
            prune_peers(r, now)
    for k in dead:
        del ROOMS[k]
        shutil.rmtree(room_dir(k), ignore_errors=True)


def _new_room(host):
    """CV tutulu halda çağırılır. Yeni otaq yaradır, yaradan (host) təyin olunur."""
    cleanup(time.time())
    for _ in range(50):
        c = "".join(secrets.choice(CODE_CHARS) for _ in range(5))
        if c not in ROOMS:
            get_room(c)["host"] = host or None
            return c
    abort(503)


@app.route("/api/create", methods=["POST"])
def api_create():
    pid = str((request.get_json(silent=True) or {}).get("id", ""))[:20]
    with CV:
        return jsonify(room=_new_room(pid))


@app.route("/api/join", methods=["POST"])
def api_join():
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    pid = str(d.get("id", ""))[:20]
    if not code or not pid:
        abort(400)
    now = time.time()
    with CV:
        cleanup(now)
        if code not in ROOMS:
            abort(404)
        r = ROOMS[code]
        admit(r, pid)
        r.get("left", {}).pop(pid, None)
        r["peers"][pid] = now
        if not r["host"]:
            r["host"] = pid
        CV.notify_all()
        return jsonify(seq=r["seq"], locked=r["locked"], epoch=r["created"], video=r.get("video"), vpos=vpos_now(r, now),
                       role=role_of(r, pid), hostonly=r["hostonly"], rv=r["rv"])


def vpos_now(r, now):
    """Videonun serverdə yadda qalan cari vaxtı (oynayırsa keçən müddət əlavə olunur)."""
    v = r.get("vpos")
    if not v or not r.get("video") or now - v["at"] > 6 * 3600:
        return None
    t = v["time"] + ((now - v["at"]) if v["playing"] else 0)
    return {"time": round(t, 2), "playing": v["playing"]}


@app.route("/api/send", methods=["POST"])
def api_send():
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    pid = str(d.get("id", ""))[:20]
    msg = d.get("msg")
    if not code or not pid or not isinstance(msg, dict):
        abort(400)
    if len(json.dumps(msg)) > 4000:
        abort(400)
    msg.pop("vf", None)   # tik yalnız serverdə təyin olunur, müştəridən gələn nəzərə alınmır
    tok = request.headers.get("X-Token", "")[:100]
    if tok:
        idn = ident_for(tok)
        if idn["authed"]:
            if idn["badge"]:
                msg["vf"] = 1
            if msg.get("t") in ("chat", "voice"):   # istifadəçi adı və şəkil də serverdən: başqasının adı ilə yazmaq olmur
                msg["u"] = idn["username"]
                msg["av"] = idn["avts"]
    now = time.time()
    with CV:
        r = get_room(code)
        admit(r, pid)
        if msg.get("t") in CTRL_TYPES and not can_ctl(r, pid):
            return jsonify(ok=True, dropped=True)   # "yalnız host idarə edir" rejimi
        if msg.get("t") == "react":
            mid, e = msg.get("id"), rx_canon(msg.get("e"))
            if not isinstance(mid, str) or not (0 < len(mid) <= 40) or e is None:
                abort(400)
            if not any(h.get("id") == mid for h in r["hist"]):
                return jsonify(ok=True, dropped=True)   # mesaj tarixçədə yoxdur (köhnəlib)
            key = rx_key(code, pid)
            rx_all = r.setdefault("reacts", {})
            rm = rx_all.setdefault(mid, {})
            if e:
                rm[key] = e
            else:
                rm.pop(key, None)
            if not rm:
                rx_all.pop(mid, None)
            while len(rx_all) > RX_MAX_MSGS:
                rx_all.pop(next(iter(rx_all)))
            msg = {"t": "react", "id": mid, "e": e, "from": key}   # yalnız yoxlanmış sahələr yayılır
        if msg.get("t") == "chat":
            msg.pop("rl", None)   # rol nişanı yalnız serverdən gəlir, saxtalaşdırmaq olmur
            if role_of(r, pid) in ("host", "mod"):
                msg["rl"] = role_of(r, pid)
            msg.pop("gif", None)   # GIF və stiker özəlliyi silinib
            msg.pop("stk", None)
        if msg.get("t") == "load" and "/vf/" not in str(msg.get("url") or ""):
            r["video"] = None   # otaq başqa səhifəyə keçdi: yenidən girənə köhnə video açılmasın
        if msg.get("t") in ("play", "pause", "seek", "sync") and isinstance(msg.get("time"), (int, float)) \
                and not isinstance(msg.get("time"), bool) and 0 <= msg["time"] < 86400:
            if msg["t"] == "pause":
                pl = False
            elif msg["t"] == "seek":
                pl = bool(msg.get("playing"))
            else:
                pl = True
            r["vpos"] = {"time": float(msg["time"]), "playing": pl, "at": now}   # yenidən girən buradan davam edir
        if msg.get("t") == "load" and not msg.get("hello"):
            r["vpos"] = None   # yeni video/səhifə açıldı: köhnə vaxt keçərsizdir
        r["seq"] += 1
        r["msgs"].append((r["seq"], pid, msg))
        del r["msgs"][:-100]
        if msg.get("t") == "chat":   # mesaj tarixçəsi (otaq yaşayanda saxlanır)
            h = dict(msg)
            h["seq"], h["ts"], h["pid"] = r["seq"], now, pid
            r["hist"].append(h)
            del r["hist"][:-HIST_KEEP]
        alive_mark(r, pid, now)
        CV.notify_all()
    return jsonify(ok=True)


@app.route("/api/leave", methods=["POST"])
def api_leave():
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    pid = str(d.get("id", ""))[:20]
    if not code or not pid:
        abort(400)
    with CV:
        r = ROOMS.get(code)
        if r is not None:
            r["peers"].pop(pid, None)
            r.setdefault("left", {})[pid] = time.time()
            if r.get("host") == pid:
                pass_host(r, time.time())   # host çıxdı: host ilk moderatora keçir
            CV.notify_all()  # qarşı tərəfin long-poll-u dərhal yenilənsin
    return jsonify(ok=True)


@app.route("/api/upload", methods=["POST"])
def api_upload():
    """Videonu birbaşa diskə axıdır (multipart və əlavə kopya yoxdur): sürətli və yaddaşa az yük."""
    code = clean_room(request.args.get("room"))
    pid = str(request.args.get("id", ""))[:20]
    ext = VID_EXT.get(os.path.splitext(str(request.args.get("fn", "")))[1].lower())
    if not code or not pid or not ext:
        abort(400)
    size = request.content_length or 0
    if size <= 0:
        abort(411)
    if size > MAX_UPLOAD:
        abort(413)
    with CV:
        r = ROOMS.get(code)
        if r is None:
            abort(404)
        admit(r, pid)
        if not can_ctl(r, pid):
            abort(403)
    if not rate_hit("upload", client_ip(), 10, 3600):
        abort(429)
    d = room_dir(code)
    os.makedirs(d, exist_ok=True)
    name = secrets.token_hex(8) + ext
    path = os.path.join(d, name)
    tmp = path + ".part"
    got = 0
    try:
        with open(tmp, "wb") as out:
            while True:
                chunk = request.stream.read(1 << 20)
                if not chunk:
                    break
                got += len(chunk)
                if got > MAX_UPLOAD:
                    raise OverflowError
                out.write(chunk)
        if got != size:
            raise ValueError("yarımçıq")
    except OverflowError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        abort(413)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        abort(400)
    os.replace(tmp, path)
    for old in os.listdir(d):   # otaqda həmişə yalnız ən son video qalır
        if old != name:
            try:
                os.remove(os.path.join(d, old))
            except OSError:
                pass
    url = "/vf/%s/%s" % (code, name)
    with CV:
        r = ROOMS.get(code)
        if r is not None:
            r["vpos"] = None
            r["video"] = url   # otağa sonradan girən (və ya yenidən girən) hər kəs bunu avtomatik açır
    return jsonify(url=url)


@app.route("/vf/<code>/<name>")
def serve_video(code, name):
    code = clean_room(code)
    if not code or not re.fullmatch(r"[0-9a-f]{16}\.(mp4|webm|mov|ogg)", name):
        abort(404)
    resp = send_from_directory(room_dir(code), name, conditional=True)   # Range: irəli-geri çəkmək işləyir
    resp.headers["Cache-Control"] = "private, max-age=3600"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


IMG_MAX = 8 * 1024 * 1024


@app.route("/api/img", methods=["POST"])
def api_img():
    """Çat şəklini qəbul edir (yalnız JPEG, brauzer kiçiltdikdən sonra) və otaq qovluğunun img/ alt qovluğuna yazır."""
    code = clean_room(request.args.get("room"))
    pid = str(request.args.get("id", ""))[:20]
    if not code or not pid:
        abort(400)
    size = request.content_length or 0
    if size <= 0:
        abort(411)
    if size > IMG_MAX:
        abort(413)
    with CV:
        r = ROOMS.get(code)
        if r is None:
            abort(404)
        admit(r, pid)
    if not rate_hit("img", client_ip(), 40, 600):
        abort(429)
    data = request.get_data(cache=False)
    if len(data) != size:
        abort(400)
    if data[:3] == b"\xff\xd8\xff":
        ext = ".jpg"
    elif data[:4] == b"GIF8":   # GIF: animasiya itməsin deyə olduğu kimi saxlanır
        ext = ".gif"
    else:
        abort(400)
    d = os.path.join(room_dir(code), "img")
    os.makedirs(d, exist_ok=True)
    name = secrets.token_hex(8) + ext
    with open(os.path.join(d, name), "wb") as f:
        f.write(data)
    return jsonify(url="/if/%s/%s" % (code, name))


@app.route("/if/<code>/<name>")
def serve_img(code, name):
    code = clean_room(code)
    if not code or not re.fullmatch(r"[0-9a-f]{16}\.(jpg|gif)", name):
        abort(404)
    resp = send_from_directory(os.path.join(room_dir(code), "img"), name)
    resp.headers["Cache-Control"] = "private, max-age=3600"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@app.route("/api/lock", methods=["POST"])
def api_lock():
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    pid = str(d.get("id", ""))[:20]
    if not code or not pid:
        abort(400)
    with CV:
        r = ROOMS.get(code)
        if r is None:
            abort(404)
        if pid not in r["members"]:
            abort(403)
        if r["host"] and role_of(r, pid) == "viewer":
            abort(403)   # kilidi yalnız host və moderator dəyişir
        r["locked"] = bool(d.get("locked"))
        CV.notify_all()
        return jsonify(locked=r["locked"])
