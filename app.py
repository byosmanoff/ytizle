import os, re, json, base64, time, secrets, threading, ipaddress, socket, sqlite3, smtplib, hashlib, hmac, shutil, datetime, zlib, struct, html as htmllib
from urllib.parse import urlparse, urljoin, parse_qs
import random
import copy
import requests
from werkzeug.exceptions import HTTPException
from email.message import EmailMessage
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, request, Response, send_file, send_from_directory, abort, jsonify

try:
    from requests.compat import chardet as _chardet
except Exception:  # pragma: no cover
    _chardet = None

app = Flask(__name__)
HERE = os.path.dirname(os.path.abspath(__file__))
UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36")
UA_DESKTOP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

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
        return jsonify(seq=r["seq"], locked=r["locked"], epoch=r["created"], video=r.get("video"),
                       role=role_of(r, pid), hostonly=r["hostonly"], rv=r["rv"])


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


# ---------------------------------------------------------------
#  Hesablar, sessiyalar və tarixçə (SQLite)
#  DB_PATH: bazanın yeri. Hosting diski müvəqqətidirsə daimi disk qoşub bura yaz.
# ---------------------------------------------------------------
DB_PATH = os.environ.get("DB_PATH", os.path.join(HERE, "wt.db"))
SESSION_DAYS = 180
DBL = threading.Lock()


def dbq(sql, args=(), many=False, one=False):
    with DBL:
        c = sqlite3.connect(DB_PATH, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            cur = c.execute(sql, args)
            rows = cur.fetchall() if (many or one) else None
            c.commit()
            if one:
                return rows[0] if rows else None
            return rows
        finally:
            c.close()


def init_db():
    with DBL:
        c = sqlite3.connect(DB_PATH, timeout=10)
        try:
            c.executescript("""
CREATE TABLE IF NOT EXISTS users(sub TEXT PRIMARY KEY, name TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, sub TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS activity(
  id INTEGER PRIMARY KEY AUTOINCREMENT, sub TEXT NOT NULL, kind TEXT NOT NULL, k TEXT NOT NULL,
  title TEXT, url TEXT, thumb TEXT, room TEXT, ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS act_sub ON activity(sub, kind, ts);
CREATE TABLE IF NOT EXISTS creds(email TEXT PRIMARY KEY, sub TEXT NOT NULL, pw TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS codes(email TEXT NOT NULL, purpose TEXT NOT NULL, h TEXT NOT NULL, exp REAL NOT NULL, sent REAL NOT NULL,
  tries INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(email, purpose));
CREATE TABLE IF NOT EXISTS avatars(sub TEXT PRIMARY KEY, data BLOB NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS invites(id INTEGER PRIMARY KEY AUTOINCREMENT, to_sub TEXT NOT NULL, from_sub TEXT NOT NULL,
  room TEXT NOT NULL, ts REAL NOT NULL, UNIQUE(to_sub, from_sub, room));
CREATE TABLE IF NOT EXISTS logins(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL,
  email TEXT NOT NULL, ip TEXT NOT NULL, ok INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS logins_ts ON logins(ts);
CREATE TABLE IF NOT EXISTS visits(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, ip TEXT NOT NULL, ua TEXT);
CREATE INDEX IF NOT EXISTS visits_ts ON visits(ts);
CREATE TABLE IF NOT EXISTS friends(a TEXT NOT NULL, b TEXT NOT NULL, status INTEGER NOT NULL DEFAULT 0, ts REAL NOT NULL,
  PRIMARY KEY(a, b));
CREATE INDEX IF NOT EXISTS friends_b ON friends(b);
CREATE TABLE IF NOT EXISTS blocks(blocker TEXT NOT NULL, blocked TEXT NOT NULL, ts REAL NOT NULL, PRIMARY KEY(blocker, blocked));
CREATE INDEX IF NOT EXISTS blocks_blocked ON blocks(blocked);
CREATE TABLE IF NOT EXISTS presence(sub TEXT PRIMARY KEY, seen REAL NOT NULL);
CREATE TABLE IF NOT EXISTS watch(sub TEXT NOT NULL, day TEXT NOT NULL, secs REAL NOT NULL DEFAULT 0, tog REAL NOT NULL DEFAULT 0,
  PRIMARY KEY(sub, day));
CREATE TABLE IF NOT EXISTS plans(id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, title TEXT NOT NULL, ts REAL NOT NULL,
  room TEXT, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS plans_owner ON plans(owner);
CREATE TABLE IF NOT EXISTS plan_inv(plan_id INTEGER NOT NULL, sub TEXT NOT NULL, status INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(plan_id, sub));
CREATE INDEX IF NOT EXISTS plan_inv_sub ON plan_inv(sub);
""")
            ucols = [r[1] for r in c.execute("PRAGMA table_info(users)")]
            if "username" not in ucols:   # köhnə baza: istifadəçi adı sonradan avtomatik verilir
                c.execute("ALTER TABLE users ADD COLUMN username TEXT")
            if "invites_open" not in ucols:   # otağa dəvətlər: 1 = hamı göndərə bilər (standart), 0 = bağlı
                c.execute("ALTER TABLE users ADD COLUMN invites_open INTEGER NOT NULL DEFAULT 1")
            if "bio" not in ucols:   # profildə qısa "Haqqında"
                c.execute("ALTER TABLE users ADD COLUMN bio TEXT")
            if "avatar_visibility" not in ucols:
                c.execute("ALTER TABLE users ADD COLUMN avatar_visibility TEXT NOT NULL DEFAULT 'everyone'")
            if "last_seen_visibility" not in ucols:
                c.execute("ALTER TABLE users ADD COLUMN last_seen_visibility TEXT NOT NULL DEFAULT 'everyone'")
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_username ON users(username)")
            cols = [r[1] for r in c.execute("PRAGMA table_info(creds)")]
            if "want" not in cols:   # qeydiyyatda seçilən istifadəçi adı (e-poçt təsdiqlənəndə verilir)
                c.execute("ALTER TABLE creds ADD COLUMN want TEXT")
            if "verified" not in cols:   # köhnə baza: mövcud e-poçt hesabları təsdiqsiz sayılır
                c.execute("ALTER TABLE creds ADD COLUMN verified INTEGER NOT NULL DEFAULT 0")
            c.commit()
        finally:
            c.close()


init_db()


def clean_name(s):
    s = re.sub(r"[\x00-\x1f\x7f<>]", "", str(s or ""))
    return re.sub(r"\s+", " ", s).strip()[:20]


SESS_CACHE = {}   # token -> (vaxt, sub): 30 san keş, çıxış/şifrə dəyişəndə təmizlənir


def auth_sub():
    tok = request.headers.get("X-Token", "")[:100]
    if not tok:
        abort(401)
    now = time.time()
    c = SESS_CACHE.get(tok)
    if c and now - c[0] < 30:
        return c[1], tok
    row = dbq("SELECT sub, created FROM sessions WHERE token=?", (tok,), one=True)
    if not row or now - row["created"] > SESSION_DAYS * 86400:
        SESS_CACHE.pop(tok, None)
        abort(401)
    if len(SESS_CACHE) > 5000:
        SESS_CACHE.clear()
    SESS_CACHE[tok] = (now, row["sub"])
    return row["sub"], tok


YT_RE = re.compile(r"^[\w-]{11}$")


def yt_id(u):
    p = urlparse(u)
    h = re.sub(r"^(www|m)\.", "", (p.hostname or "").lower())
    vid = None
    if h == "youtu.be":
        vid = p.path.lstrip("/")[:11]
    elif h in ("youtube.com", "youtube-nocookie.com"):
        if p.path == "/watch":
            vid = (parse_qs(p.query).get("v") or [""])[0]
        else:
            seg = p.path.split("/")
            if len(seg) > 2 and seg[1] in ("embed", "shorts", "live", "v"):
                vid = seg[2]
    return vid if vid and YT_RE.match(vid) else None


def yt_title(vid):
    try:
        r = requests.get("https://www.youtube.com/oembed", timeout=4,
                         params={"url": "https://www.youtube.com/watch?v=" + vid, "format": "json"})
        if r.ok:
            return str(r.json().get("title", ""))[:150]
    except Exception:
        pass
    return ""


RATE = {}


def client_ip():
    h = request.headers
    ip = (h.get("CF-Connecting-IP") or h.get("True-Client-IP")
          or h.get("X-Forwarded-For", "").split(",")[0].strip() or request.remote_addr)
    return (ip or "?")[:64]


def rate_hit(bucket, key, limit, window):
    """True: icazə var (və sayılır). False: limit aşılıb. Yaddaşda saxlanır."""
    now = time.time()
    k = bucket + "|" + str(key)
    if len(RATE) > 20000:
        RATE.clear()
    L = [t for t in RATE.get(k, []) if now - t < window]
    if len(L) >= limit:
        RATE[k] = L
        return False
    L.append(now)
    RATE[k] = L
    return True


def mail_guard():
    """E-poçt göndərən hər sorğu üçün IP limiti: 10 dəqiqədə 10."""
    if not rate_hit("mail", client_ip(), 10, 600):
        abort(429)


LAST_PURGE = [0.0]


def purge_unverified():
    """3 gündən köhnə, təsdiqlənməmiş və boş hesabları silir (saxta qeydiyyat zibilini təmizləyir)."""
    now = time.time()
    if now - LAST_PURGE[0] < 3600:
        return
    LAST_PURGE[0] = now
    rows = dbq("SELECT c.email, c.sub FROM creds c JOIN users u ON u.sub=c.sub WHERE c.verified=0 AND u.created<? "
               "AND NOT EXISTS(SELECT 1 FROM activity a WHERE a.sub=c.sub)", (now - 3 * 86400,), many=True)
    for r in rows:
        dbq("DELETE FROM codes WHERE email=?", (r["email"],))
        dbq("DELETE FROM creds WHERE email=?", (r["email"],))
        dbq("DELETE FROM users WHERE sub=?", (r["sub"],))


EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")
PW_METHOD = "pbkdf2:sha256"
DUMMY_HASH = generate_password_hash("dummy-password", method=PW_METHOD)
LOGIN_FAILS = {}  # email -> yanlış cəhd vaxtları (brute-force qorunması, yaddaşda)
CODE_TTL = 900     # təsdiq / bərpa kodu 15 dəqiqə etibarlıdır
CODE_COOLDOWN = 60  # eyni ünvana kodu ən tez 60 saniyədən bir göndər
CODE_TRIES = 5


# ---- E-poçt göndərmə (SMTP). Mühit dəyişənləri:
#      BREVO_API_KEY + SMTP_FROM (tövsiyə olunur, Render-də işləyir) və ya
#      SMTP_HOST, SMTP_PORT (587 və ya 465), SMTP_USER, SMTP_PASS, SMTP_FROM
#      MAIL_DEBUG=1 yalnız lokal sınaq üçündür: məktub göndərmir, kodu server jurnalına yazır.
def mail_ready():
    return bool(os.environ.get("MAIL_DEBUG") == "1"
                or os.environ.get("BREVO_API_KEY")
                or (os.environ.get("SMTP_HOST") and os.environ.get("SMTP_FROM")))


def send_mail(to, subject, body, html=None):
    if os.environ.get("MAIL_DEBUG") == "1":
        print("[MAIL_DEBUG] to=%s subject=%s\n%s" % (to, subject, body), flush=True)
        return
    if os.environ.get("BREVO_API_KEY"):
        # HTTP API (443 portu): Render pulsuz planda SMTP portları bloklanır
        r = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": os.environ["BREVO_API_KEY"], "Content-Type": "application/json"},
            json={"sender": {"name": "Birlikdə İzlə", "email": os.environ["SMTP_FROM"]},
                  "to": [{"email": to}], "subject": subject, "textContent": body,
                  **({"htmlContent": html} if html else {})},
            timeout=12)
        if r.status_code >= 300:
            print("[BREVO ERROR]", r.status_code, r.text[:300], flush=True)
        r.raise_for_status()
        return
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user, pw = os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASS", "")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = os.environ["SMTP_FROM"], to, subject
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    s = smtplib.SMTP_SSL(host, port, timeout=12) if port == 465 else smtplib.SMTP(host, port, timeout=12)
    try:
        if port != 465:
            s.ehlo()
            s.starttls()
            s.ehlo()
        if user:
            s.login(user, pw)
        s.send_message(msg)
    finally:
        try:
            s.quit()
        except Exception:
            pass


def _code_hash(email, purpose, code):
    return hashlib.sha256((email + ":" + purpose + ":" + code).encode()).hexdigest()


MAIL_L = {
    "az": {"tag": "Uzaqda olsanız da, eyni kadrdasınız.", "pre": "Kodun: ", "valid": "Kod <b style=\"color:#2b0f26;\">15 dəqiqə</b> etibarlıdır.",
           "ign": "Bu sorğunu sən etməmisənsə, bu məktubu nəzərə alma.<br>Kodu heç kimlə paylaşma.",
           "subj": "Birlikdə İzlə: kodun ", "hello": "Salam!",
           "plain": "%s üçün kodun: %s\n\nKod 15 dəqiqə etibarlıdır. Bu sorğunu sən etməmisənsə, məktubu nəzərə alma.",
           "verify": ("e-poçtunu təsdiq etmək", "E-poçtunu təsdiq et", "Qeydiyyatı tamamlamaq üçün bu kodu saytda daxil et."),
           "chg": ("yeni e-poçtu təsdiq etmək", "Yeni e-poçt ünvanı", "E-poçtunu dəyişmək üçün bu kodu saytda daxil et."),
           "reset": ("şifrəni yeniləmək", "Şifrəni yenilə", "Yeni şifrə təyin etmək üçün bu kodu saytda daxil et.")},
    "tr": {"tag": "Uzakta olsanız da, aynı karedesiniz.", "pre": "Kodun: ", "valid": "Kod <b style=\"color:#2b0f26;\">15 dakika</b> geçerlidir.",
           "ign": "Bu isteği sen yapmadıysan, bu e-postayı dikkate alma.<br>Kodu kimseyle paylaşma.",
           "subj": "Birlikdə İzlə: kodun ", "hello": "Merhaba!",
           "plain": "%s için kodun: %s\n\nKod 15 dakika geçerlidir. Bu isteği sen yapmadıysan, bu e-postayı dikkate alma.",
           "verify": ("e-postanı doğrulamak", "E-postanı doğrula", "Kaydı tamamlamak için bu kodu sitede gir."),
           "chg": ("yeni e-postayı doğrulamak", "Yeni e-posta adresi", "E-postanı değiştirmek için bu kodu sitede gir."),
           "reset": ("şifreni yenilemek", "Şifreni yenile", "Yeni şifre belirlemek için bu kodu sitede gir.")},
    "en": {"tag": "Even when you're far apart, you're in the same frame.", "pre": "Your code: ", "valid": "The code is valid for <b style=\"color:#2b0f26;\">15 minutes</b>.",
           "ign": "If you did not make this request, ignore this email.<br>Never share the code with anyone.",
           "subj": "Birlikdə İzlə: your code ", "hello": "Hi!",
           "plain": "Your code for %s: %s\n\nThe code is valid for 15 minutes. If you did not make this request, ignore this email.",
           "verify": ("verifying your email", "Verify your email", "Enter this code on the site to finish signing up."),
           "chg": ("verifying your new email", "New email address", "Enter this code on the site to change your email."),
           "reset": ("resetting your password", "Reset your password", "Enter this code on the site to set a new password.")},
}


def req_lang():
    """Müştərinin seçdiyi dil (X-Lang başlığı): az / tr / en."""
    l = (request.headers.get("X-Lang") or "").strip().lower()
    return l if l in MAIL_L else "az"


def code_email_html(code, title, intro, lang="az"):
    L = MAIL_L.get(lang) or MAIL_L["az"]
    """Saytın biletə bənzər dizaynında məktub (cədvəl + inline CSS: Gmail/Outlook-da işləsin deyə)."""
    F = "'Bricolage Grotesque',-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
    return (
        '<!DOCTYPE html><html lang="' + lang + '"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light only"><meta name="supported-color-schemes" content="light only">'
        '</head><body style="margin:0;padding:0;background:#150a1d;">'
        '<div style="display:none;max-height:0;overflow:hidden;opacity:0;">' + L["pre"] + code + '</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#150a1d" '
        'style="background:#150a1d;background-image:linear-gradient(180deg,#2b1630 0%,#150a1d 45%,#2a0f24 100%);">'
        '<tr><td align="center" style="padding:32px 14px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:380px;">'
        # yuxarı hissə
        '<tr><td bgcolor="#f8e7e1" style="background:#f8e7e1;border-radius:22px 22px 0 0;padding:28px 24px 22px;font-family:' + F + ';">'
        '<div style="font-size:46px;line-height:46px;color:#e8567d;">&#9829;</div>'
        '<div style="font-size:34px;line-height:1;font-weight:800;letter-spacing:-0.02em;color:#2b0f26;margin-top:10px;">Birlikdə İzlə</div>'
        '<div style="font-size:15px;line-height:1.4;color:#7a5469;margin-top:8px;">' + L["tag"] + '</div>'
        '</td></tr>'
        # pərforasiya xətti
        '<tr><td bgcolor="#f8e7e1" style="background:#f8e7e1;padding:0 22px;font-size:0;line-height:0;">'
        '<div style="border-top:2px dashed rgba(43,15,38,0.28);font-size:0;line-height:0;height:0;">&nbsp;</div></td></tr>'
        # aşağı hissə (kod)
        '<tr><td bgcolor="#f8e7e1" style="background:#f8e7e1;border-radius:0 0 22px 22px;padding:24px 24px 28px;font-family:' + F + ';">'
        '<div style="font-size:13px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#7a5469;">' + title + '</div>'
        '<div style="font-size:15px;line-height:1.45;color:#2b0f26;margin-top:8px;">' + intro + '</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:16px;"><tr>'
        '<td align="center" bgcolor="#fff7f4" style="background:#fff7f4;border:1.5px solid #dcc9ca;border-radius:12px;padding:16px 8px;'
        'font-family:' + F + ';font-size:38px;line-height:1;font-weight:800;letter-spacing:0.3em;text-indent:0.3em;color:#e8567d;">'
        + code + '</td></tr></table>'
        '<div style="font-size:13px;line-height:1.45;color:#7a5469;margin-top:16px;text-align:center;">'
        + L["valid"] + '</div>'
        '</td></tr></table>'
        # alt qeyd
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:380px;"><tr>'
        '<td align="center" style="padding:20px 10px 0;font-family:' + F + ';font-size:12.5px;line-height:1.5;color:#b7a2cf;">'
        + L["ign"] +
        '</td></tr></table>'
        '</td></tr></table></body></html>')


def send_code(email, purpose, lang="az"):
    """Kod göndərir. Soyutma müddətindədirsə göndərmir və False qaytarır."""
    now = time.time()
    row = dbq("SELECT sent FROM codes WHERE email=? AND purpose=?", (email, purpose), one=True)
    if row and now - row["sent"] < CODE_COOLDOWN:
        return False
    if not rate_hit("mailto", email, 5, 3600):   # eyni ünvana saatda ən çox 5 kod
        return False
    code = "%06d" % secrets.randbelow(10 ** 6)
    dbq("INSERT OR REPLACE INTO codes(email,purpose,h,exp,sent,tries) VALUES(?,?,?,?,?,0)",
        (email, purpose, _code_hash(email, purpose, code), now + CODE_TTL, now))
    L = MAIL_L.get(lang) or MAIL_L["az"]
    key = "verify" if purpose == "verify" else "chg" if purpose.startswith("chg:") else "reset"
    what, title, intro = L[key]
    try:
        send_mail(email, L["subj"] + code,
                  L["hello"] + "\n\n" + (L["plain"] % (what[0].upper() + what[1:], code)) + "\n\nBirlikdə İzlə",
                  html=code_email_html(code, title, intro, lang))
    except Exception as e:
        print("[MAIL ERROR]", repr(e), flush=True)
        dbq("DELETE FROM codes WHERE email=? AND purpose=?", (email, purpose))
        abort(502)
    return True


def check_code(email, purpose, code):
    row = dbq("SELECT h, exp, tries FROM codes WHERE email=? AND purpose=?", (email, purpose), one=True)
    if not row or time.time() > row["exp"] or row["tries"] >= CODE_TRIES:
        return False
    if not hmac.compare_digest(_code_hash(email, purpose, str(code)), row["h"]):
        dbq("UPDATE codes SET tries=tries+1 WHERE email=? AND purpose=?", (email, purpose))
        return False
    dbq("DELETE FROM codes WHERE email=? AND purpose=?", (email, purpose))
    return True


def issue_token(sub):
    now = time.time()
    token = secrets.token_urlsafe(32)
    dbq("INSERT INTO sessions(token,sub,created) VALUES(?,?,?)", (token, sub, now))
    dbq("DELETE FROM sessions WHERE created<?", (now - SESSION_DAYS * 86400,))
    return token


def user_name(sub):
    u = dbq("SELECT name FROM users WHERE sub=?", (sub,), one=True)
    return u["name"] if u else "Qonaq"


USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._]{1,18}[a-z0-9]$")
RESERVED_USERNAMES = {"admin", "administrator", "root", "support", "help", "moderator", "system",
                      "birlikde", "birlikdeizle", "api", "null", "undefined", "anonymous"}


def norm_username(u):
    return str(u or "").strip().lstrip("@").lower()


def username_valid(u):
    return bool(USERNAME_RE.match(u)) and ".." not in u and u not in RESERVED_USERNAMES


def username_taken(u, except_sub=None):
    row = dbq("SELECT sub FROM users WHERE username=?", (u,), one=True)
    return bool(row and row["sub"] != except_sub)


def gen_username(base):
    b = re.sub(r"[^a-z0-9._]", "", str(base or "").lower()).strip("._")[:14]
    if len(b) < 3:
        b = "user" + b
    cand = b
    for _ in range(30):
        if username_valid(cand) and not username_taken(cand):
            return cand
        cand = b + str(secrets.randbelow(10 ** 4))
    return "user" + secrets.token_hex(5)


def ensure_username(sub):
    """İstifadəçinin adı yoxdursa (köhnə hesab və ya yeni hesab), e-poçt/addan avtomatik verir."""
    row = dbq("SELECT username, name FROM users WHERE sub=?", (sub,), one=True)
    if not row:
        return ""
    if row["username"]:
        return row["username"]
    er = dbq("SELECT email FROM creds WHERE sub=? ORDER BY verified DESC, rowid LIMIT 1", (sub,), one=True)
    base = er["email"].split("@")[0] if er else row["name"]
    for _ in range(5):
        u = gen_username(base)
        try:
            dbq("UPDATE users SET username=? WHERE sub=? AND username IS NULL", (u, sub))
        except sqlite3.IntegrityError:
            continue
        cur = dbq("SELECT username FROM users WHERE sub=?", (sub,), one=True)
        if cur and cur["username"]:
            return cur["username"]
    return ""


def assign_username(sub):
    """Qeydiyyatda seçilən ad hələ boşdursa onu verir, yoxsa avtomatik ad verir."""
    u = dbq("SELECT username FROM users WHERE sub=?", (sub,), one=True)
    if not u or u["username"]:
        return
    w = dbq("SELECT want FROM creds WHERE sub=? AND want IS NOT NULL LIMIT 1", (sub,), one=True)
    if w and username_valid(w["want"]) and not username_taken(w["want"], sub):
        try:
            dbq("UPDATE users SET username=? WHERE sub=? AND username IS NULL", (w["want"], sub))
            return
        except sqlite3.IntegrityError:
            pass
    ensure_username(sub)


def primary_cred(sub):
    return dbq("SELECT email, pw FROM creds WHERE sub=? ORDER BY verified DESC, rowid LIMIT 1", (sub,), one=True)


IDENT = {}   # sessiya tokeni -> (vaxt, kimlik): otaqda ad/profil üçün qısa müddətli keş


def ident_for(tok, fallback_name=""):
    """Otaqdakı iştirakçının kimliyi. Token etibarlıdırsa ad, istifadəçi adı, şəkil və tik bazadan gəlir
    (müştəri saxtalaşdıra bilməz); yoxsa yalnız verilən ad."""
    now = time.time()
    if tok:
        c = IDENT.get(tok)
        if c and now - c[0] < 60:
            return c[1]
    idn = dict(name=clean_name(fallback_name) or "Qonaq", username="", avatar="", avts=0, badge=False, authed=False, bio="")
    if tok:
        try:
            row = dbq("SELECT sub, created FROM sessions WHERE token=?", (tok,), one=True)
            if row and now - row["created"] <= SESSION_DAYS * 86400:
                u = dbq("SELECT name, username, bio FROM users WHERE sub=?", (row["sub"],), one=True)
                if u:
                    av = dbq("SELECT ts FROM avatars WHERE sub=?", (row["sub"],), one=True)
                    un = u["username"] or ""
                    idn = dict(name=u["name"], username=un, avts=int(av["ts"]) if av else 0,
                               avatar=_av_url(un, av["ts"]) if av and un else "",
                               badge=is_owner(row["sub"]), authed=True, bio=u["bio"] or "")
        except Exception:
            pass
        if len(IDENT) > 5000:
            IDENT.clear()
        IDENT[tok] = (now, idn)
    return idn


OWNER_EMAIL = os.environ.get("OWNER_EMAIL", "murikosmanov@gmail.com").strip().lower()


def is_owner(sub):
    """Çəhrayı tik: yalnız OWNER_EMAIL ilə (təsdiqlənmiş) açılan hesaba."""
    try:
        return bool(OWNER_EMAIL and dbq("SELECT 1 FROM creds WHERE sub=? AND email=? AND verified=1",
                                        (sub, OWNER_EMAIL), one=True))
    except Exception:
        return False


def profile_json(sub):
    u = dbq("SELECT name, username, invites_open, bio, avatar_visibility, last_seen_visibility FROM users WHERE sub=?", (sub,), one=True)
    if not u:
        abort(401)
    uname = u["username"] or ensure_username(sub)
    cr = primary_cred(sub)
    av = dbq("SELECT ts FROM avatars WHERE sub=?", (sub,), one=True)
    return dict(bio=u["bio"] or "", name=u["name"], username=uname, email=cr["email"] if cr else "", badge=is_owner(sub), invites_open=(u["invites_open"] != 0), avatar_visibility=u["avatar_visibility"] or "everyone", last_seen_visibility=u["last_seen_visibility"] or "everyone",
                has_pw=bool(cr and cr["pw"]),
                avatar=("/api/avatar/%s?v=%d" % (uname, int(av["ts"]))) if av and uname else "")


def merge_users(old, new):
    """İki hesabı birləşdirir: tarixçə və sessiyalar new hesaba keçir."""
    dbq("UPDATE activity SET sub=? WHERE sub=?", (new, old))
    dbq("UPDATE OR IGNORE watch SET sub=? WHERE sub=?", (new, old))
    dbq("DELETE FROM watch WHERE sub=?", (old,))
    dbq("UPDATE plans SET owner=? WHERE owner=?", (new, old))
    dbq("UPDATE OR IGNORE plan_inv SET sub=? WHERE sub=?", (new, old))
    dbq("DELETE FROM plan_inv WHERE sub=?", (old,))
    dbq("UPDATE sessions SET sub=? WHERE sub=?", (new, old))
    SESS_CACHE.clear()
    dbq("DELETE FROM avatars WHERE sub=? AND EXISTS(SELECT 1 FROM avatars WHERE sub=?)", (old, new))
    dbq("UPDATE OR IGNORE avatars SET sub=? WHERE sub=?", (new, old))
    dbq("UPDATE OR IGNORE friends SET a=? WHERE a=?", (new, old))
    dbq("UPDATE OR IGNORE friends SET b=? WHERE b=?", (new, old))
    dbq("DELETE FROM friends WHERE a=b OR a=? OR b=?", (old, old))
    dbq("DELETE FROM users WHERE sub=?", (old,))


def too_many_fails(email):
    now = time.time()
    if len(LOGIN_FAILS) > 5000:
        LOGIN_FAILS.clear()
    L = [t for t in LOGIN_FAILS.get(email, []) if now - t < 600]
    LOGIN_FAILS[email] = L
    return len(L) >= 8


def body_email():
    d = request.get_json(silent=True) or {}
    email = str(d.get("email") or "").strip().lower()[:254]
    if not EMAIL_RE.match(email):
        abort(400)
    return d, email


@app.route("/api/register", methods=["POST"])
def api_register():
    d, email = body_email()
    pw = str(d.get("password") or "")
    if not (6 <= len(pw) <= 72):
        abort(400)
    if not mail_ready():
        abort(503)
    ip = client_ip()
    if not rate_hit("reg", ip, 6, 600) or not rate_hit("regday", ip, 25, 86400):
        abort(429)
    purge_unverified()
    want = norm_username(d.get("username"))
    if want:
        if not username_valid(want):
            return jsonify(error="username_format"), 400
        if username_taken(want):
            return jsonify(error="username"), 409
    name = clean_name(d.get("name")) or clean_name(email.split("@")[0]) or "Qonaq"
    row = dbq("SELECT sub, verified FROM creds WHERE email=?", (email,), one=True)
    ph = generate_password_hash(pw, method=PW_METHOD)
    if row and row["verified"]:
        abort(409)
    if row:   # təsdiqsiz köhnə cəhd: sahibi təsdiqləməyib, üstündən yazılır
        dbq("UPDATE creds SET pw=?, want=? WHERE email=?", (ph, want or None, email))
        dbq("UPDATE users SET name=? WHERE sub=?", (name, row["sub"]))
    else:
        sub = "e:" + secrets.token_hex(8)
        try:
            dbq("INSERT INTO creds(email,sub,pw,verified,want) VALUES(?,?,?,0,?)", (email, sub, ph, want or None))
        except sqlite3.IntegrityError:
            abort(409)
        dbq("INSERT INTO users(sub,name,created) VALUES(?,?,?)", (sub, name, time.time()))
    send_code(email, "verify", req_lang())
    return jsonify(pending=True)


@app.route("/api/verify", methods=["POST"])
def api_verify():
    d, email = body_email()
    row = dbq("SELECT sub FROM creds WHERE email=?", (email,), one=True)
    if not row or not check_code(email, "verify", d.get("code")):
        abort(422)
    dbq("UPDATE creds SET verified=1 WHERE email=?", (email,))
    assign_username(row["sub"])
    log_login("təsdiq", email, True)
    return jsonify(token=issue_token(row["sub"]), name=user_name(row["sub"]))


@app.route("/api/resend", methods=["POST"])
def api_resend():
    _, email = body_email()
    if not mail_ready():
        abort(503)
    mail_guard()
    row = dbq("SELECT verified FROM creds WHERE email=?", (email,), one=True)
    if row and not row["verified"] and not send_code(email, "verify", req_lang()):
        abort(429)
    return jsonify(ok=True)


@app.route("/api/login", methods=["POST"])
def api_login():
    d, email = body_email()
    pw = str(d.get("password") or "")
    if not pw or len(pw) > 72:
        abort(400)
    if too_many_fails(email):
        abort(429)
    row = dbq("SELECT sub, pw, verified FROM creds WHERE email=?", (email,), one=True)
    ok = check_password_hash(row["pw"] if row and row["pw"] else DUMMY_HASH, pw)
    if not row or not row["pw"] or not ok:
        LOGIN_FAILS.setdefault(email, []).append(time.time())
        log_login("e-poçt", email, False)
        abort(401)
    if not row["verified"]:   # şifrə düzdür, amma e-poçt təsdiqlənməyib
        log_login("e-poçt", email, False)
        if mail_ready() and rate_hit("mail", client_ip(), 10, 600):
            send_code(email, "verify", req_lang())
        abort(403)
    log_login("e-poçt", email, True)
    return jsonify(token=issue_token(row["sub"]), name=user_name(row["sub"]))


@app.route("/api/forgot", methods=["POST"])
def api_forgot():
    _, email = body_email()
    if not mail_ready():
        abort(503)
    if not rate_hit("forgot", client_ip(), 15, 600):
        abort(429)
    if not dbq("SELECT 1 FROM creds WHERE email=?", (email,), one=True):
        abort(404)   # bu e-poçtla hesab yoxdur: kod göndərilmir, istifadəçi qeydiyyata yönləndirilir
    if not send_code(email, "reset", req_lang()):
        abort(429)
    return jsonify(ok=True)


@app.route("/api/reset", methods=["POST"])
def api_reset():
    d, email = body_email()
    pw = str(d.get("password") or "")
    if not (6 <= len(pw) <= 72):
        abort(400)
    row = dbq("SELECT sub FROM creds WHERE email=?", (email,), one=True)
    if not row or not check_code(email, "reset", d.get("code")):
        abort(422)
    dbq("UPDATE creds SET pw=?, verified=1 WHERE email=?", (generate_password_hash(pw, method=PW_METHOD), email))
    dbq("DELETE FROM sessions WHERE sub=?", (row["sub"],))   # köhnə cihazlar çıxarılır
    SESS_CACHE.clear()
    LOGIN_FAILS.pop(email, None)
    assign_username(row["sub"])
    log_login("bərpa", email, True)
    return jsonify(token=issue_token(row["sub"]), name=user_name(row["sub"]))


@app.route("/api/stats")
def api_stats():
    """Giriş ekranında canlı say: yalnız ümumi say, heç bir otaq kodu və ya ad verilmir."""
    now = time.time()
    rooms = people = 0
    with CV:
        for r in ROOMS.values():
            n = sum(1 for ts in r["peers"].values() if now - ts < PEER_TTL)
            if n:
                rooms += 1
                people += n
    resp = jsonify(rooms=rooms, people=people)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/me")
def api_me():
    sub, _ = auth_sub()
    resp = jsonify(profile_json(sub))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/name", methods=["POST"])
def api_name():
    sub, _ = auth_sub()
    n = clean_name((request.get_json(silent=True) or {}).get("name"))
    if not n:
        abort(400)
    dbq("UPDATE users SET name=? WHERE sub=?", (n, sub))
    return jsonify(name=n)


@app.route("/api/username/check")
def api_username_check():
    sub, _ = auth_sub()
    u = norm_username(request.args.get("u"))
    if not username_valid(u):
        return jsonify(ok=False, reason="format")
    if username_taken(u, sub):
        return jsonify(ok=False, reason="taken")
    return jsonify(ok=True)


@app.route("/api/profile/username", methods=["POST"])
def api_profile_username():
    sub, _ = auth_sub()
    u = norm_username((request.get_json(silent=True) or {}).get("username"))
    if not username_valid(u):
        abort(400)
    if username_taken(u, sub):
        abort(409)
    try:
        dbq("UPDATE users SET username=? WHERE sub=?", (u, sub))
    except sqlite3.IntegrityError:
        abort(409)
    return jsonify(profile_json(sub))


AVATAR_MAX = 150 * 1024


@app.route("/api/avatar", methods=["POST"])
def api_avatar_set():
    sub, _ = auth_sub()
    img = str((request.get_json(silent=True) or {}).get("image") or "")
    pre = "data:image/jpeg;base64,"
    if not img.startswith(pre) or len(img) > AVATAR_MAX * 2:
        abort(400)
    try:
        raw = base64.b64decode(img[len(pre):], validate=True)
    except Exception:
        abort(400)
    if len(raw) > AVATAR_MAX or raw[:3] != b"\xff\xd8\xff":
        abort(400)
    ensure_username(sub)
    dbq("INSERT OR REPLACE INTO avatars(sub,data,ts) VALUES(?,?,?)", (sub, raw, time.time()))
    return jsonify(profile_json(sub))


@app.route("/api/avatar/remove", methods=["POST"])
def api_avatar_remove():
    sub, _ = auth_sub()
    dbq("DELETE FROM avatars WHERE sub=?", (sub,))
    return jsonify(profile_json(sub))


@app.after_request
def remember_avatar_viewer(response):
    tok = request.headers.get("X-Token", "")[:100]
    if tok and dbq("SELECT 1 FROM sessions WHERE token=?", (tok,), one=True):
        response.set_cookie("wt_session", tok, max_age=SESSION_DAYS * 86400, httponly=True,
                            secure=request.is_secure, samesite="Lax", path="/")
    return response


@app.route("/api/avatar/<u>")
def api_avatar_get(u):
    u = norm_username(u)
    if not username_valid(u):
        abort(404)
    owner = dbq("SELECT sub, avatar_visibility FROM users WHERE username=?", (u,), one=True)
    if not owner:
        abort(404)
    visibility = owner["avatar_visibility"] or "everyone"
    if visibility != "everyone":
        tok = request.cookies.get("wt_session", "")[:100]
        viewer = dbq("SELECT sub, created FROM sessions WHERE token=?", (tok,), one=True) if tok else None
        viewer_sub = viewer["sub"] if viewer and time.time() - viewer["created"] <= SESSION_DAYS * 86400 else None
        allowed = viewer_sub == owner["sub"]
        if not allowed and viewer_sub and visibility == "friends":
            allowed = bool(dbq("SELECT 1 FROM friends WHERE status=1 AND ((a=? AND b=?) OR (a=? AND b=?))", (viewer_sub, owner["sub"], owner["sub"], viewer_sub), one=True))
        if not allowed:
            abort(404)
    row = dbq("SELECT data FROM avatars WHERE sub=?", (owner["sub"],), one=True)
    if not row:
        abort(404)
    resp = Response(bytes(row["data"]), mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["Vary"] = "Cookie"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@app.route("/api/password", methods=["POST"])
def api_password():
    sub, tok = auth_sub()
    d = request.get_json(silent=True) or {}
    cur, new = str(d.get("current") or ""), str(d.get("password") or "")
    if not (6 <= len(new) <= 72) or len(cur) > 72:
        abort(400)
    cr = primary_cred(sub)
    if not cr:
        abort(400)
    key = "pw:" + sub
    if too_many_fails(key):
        abort(429)
    if cr["pw"]:   # Google ilə açılmış hesabın şifrəsi yoxdur: ilk dəfə cari şifrəsiz qoyula bilər
        if not cur or not check_password_hash(cr["pw"], cur):
            LOGIN_FAILS.setdefault(key, []).append(time.time())
            abort(403)
    ph = generate_password_hash(new, method=PW_METHOD)
    dbq("UPDATE creds SET pw=? WHERE sub=?", (ph, sub))
    dbq("DELETE FROM sessions WHERE sub=? AND token<>?", (sub, tok))   # digər cihazlar çıxarılır
    SESS_CACHE.clear()
    LOGIN_FAILS.pop(key, None)
    return jsonify(ok=True)


@app.route("/api/password/forgot", methods=["POST"])
def api_password_forgot():
    """Giriş etmiş istifadəçi şifrəsini unudub: hesabın e-poçtuna kod göndərilir."""
    sub, _ = auth_sub()
    cr = primary_cred(sub)
    if not cr or not cr["email"]:
        abort(400)
    if not mail_ready():
        abort(503)
    mail_guard()
    if too_many_fails("pwr:" + sub):
        abort(429)
    if not send_code(cr["email"], "reset", req_lang()):
        abort(429)
    return jsonify(ok=True)


@app.route("/api/password/reset", methods=["POST"])
def api_password_reset():
    """Kodu yoxlayır və yeni şifrə qoyur. Bu cihaz girişdə qalır, digər cihazlar çıxarılır."""
    sub, tok = auth_sub()
    d = request.get_json(silent=True) or {}
    new = str(d.get("password") or "")
    if not (6 <= len(new) <= 72):
        abort(400)
    cr = primary_cred(sub)
    if not cr or not cr["email"]:
        abort(400)
    key = "pwr:" + sub
    if too_many_fails(key):
        abort(429)
    if not check_code(cr["email"], "reset", d.get("code")):
        LOGIN_FAILS.setdefault(key, []).append(time.time())
        abort(422)
    dbq("UPDATE creds SET pw=?, verified=1 WHERE sub=?", (generate_password_hash(new, method=PW_METHOD), sub))
    dbq("DELETE FROM sessions WHERE sub=? AND token<>?", (sub, tok))
    SESS_CACHE.clear()
    LOGIN_FAILS.pop(key, None)
    LOGIN_FAILS.pop("pw:" + sub, None)
    LOGIN_FAILS.pop(cr["email"], None)
    log_login("bərpa", cr["email"], True)
    return jsonify(ok=True)


@app.route("/api/email/request", methods=["POST"])
def api_email_request():
    sub, _ = auth_sub()
    _, email = body_email()
    if not mail_ready():
        abort(503)
    mail_guard()
    if dbq("SELECT 1 FROM creds WHERE email=?", (email,), one=True):
        abort(409)
    if not send_code(email, "chg:" + sub[:40], req_lang()):
        abort(429)
    return jsonify(ok=True)


@app.route("/api/email/confirm", methods=["POST"])
def api_email_confirm():
    sub, _ = auth_sub()
    d, email = body_email()
    if not check_code(email, "chg:" + sub[:40], d.get("code")):
        abort(422)
    cr = primary_cred(sub)
    if not cr:
        abort(400)
    try:
        dbq("UPDATE creds SET email=?, verified=1 WHERE email=?", (email, cr["email"]))
    except sqlite3.IntegrityError:
        abort(409)
    return jsonify(profile_json(sub))


@app.route("/api/username/available")
def api_username_available():
    """Qeydiyyat zamanı (girişsiz) istifadəçi adının boş olub-olmadığını yoxlayır."""
    if not rate_hit("uavail", client_ip(), 60, 600):
        abort(429)
    u = norm_username(request.args.get("u"))
    if not username_valid(u):
        return jsonify(ok=False, reason="format")
    return jsonify(ok=not username_taken(u), reason="taken" if username_taken(u) else "")


@app.route("/api/logout/all", methods=["POST"])
def api_logout_all():
    sub, _ = auth_sub()
    dbq("DELETE FROM sessions WHERE sub=?", (sub,))
    SESS_CACHE.clear()
    return jsonify(ok=True)


@app.route("/api/account/delete", methods=["POST"])
def api_account_delete():
    sub, _ = auth_sub()
    d = request.get_json(silent=True) or {}
    cr = primary_cred(sub)
    key = "pw:" + sub
    if too_many_fails(key):
        abort(429)
    if cr and cr["pw"]:
        pw = str(d.get("password") or "")[:72]
        if not pw or not check_password_hash(cr["pw"], pw):
            LOGIN_FAILS.setdefault(key, []).append(time.time())
            abort(403)
    elif str(d.get("confirm") or "").strip().replace("İ", "i").replace("I", "i").lower().replace("\u0307", "") != "sil":   # Google ilə açılmış, şifrəsiz hesab
        abort(403)
    for r in dbq("SELECT email FROM creds WHERE sub=?", (sub,), many=True):
        dbq("DELETE FROM codes WHERE email=?", (r["email"],))
    dbq("DELETE FROM codes WHERE purpose=?", ("chg:" + sub[:40],))
    SESS_CACHE.clear()
    for tbl in ("creds", "sessions", "activity", "avatars", "watch"):
        dbq("DELETE FROM %s WHERE sub=?" % tbl, (sub,))
    dbq("DELETE FROM invites WHERE to_sub=? OR from_sub=?", (sub, sub))
    dbq("DELETE FROM plan_inv WHERE sub=? OR plan_id IN (SELECT id FROM plans WHERE owner=?)", (sub, sub))
    dbq("DELETE FROM plans WHERE owner=?", (sub,))
    dbq("DELETE FROM friends WHERE a=? OR b=?", (sub, sub))
    dbq("DELETE FROM users WHERE sub=?", (sub,))
    LOGIN_FAILS.pop(key, None)
    return jsonify(ok=True)


@app.route("/api/invite", methods=["POST"])
def api_invite():
    sub, _ = auth_sub()
    d = request.get_json(silent=True) or {}
    u = norm_username(d.get("username"))
    code = clean_room(d.get("room"))
    if not username_valid(u) or not code:
        abort(400)
    if not rate_hit("invite", sub, 10, 600):
        abort(429)
    row = dbq("SELECT sub, invites_open FROM users WHERE username=?", (u,), one=True)
    if not row:
        return jsonify(error="user"), 404
    if row["sub"] == sub:
        abort(400)
    if _is_blocked_either(sub, row["sub"]):
        return jsonify(error="blocked"), 403
    if row["invites_open"] == 0:
        return jsonify(error="closed"), 403   # istifadəçi otaq dəvətlərini bağlayıb
    with CV:
        exists = code in ROOMS
    if not exists:
        return jsonify(error="room"), 404
    dbq("INSERT OR REPLACE INTO invites(to_sub,from_sub,room,ts) VALUES(?,?,?,?)", (row["sub"], sub, code, time.time()))
    dbq("DELETE FROM invites WHERE to_sub=? AND id NOT IN "
        "(SELECT id FROM invites WHERE to_sub=? ORDER BY ts DESC LIMIT 20)", (row["sub"], row["sub"]))
    return jsonify(ok=True)


@app.route("/api/invites")
def api_invites():
    sub, _ = auth_sub()
    dbq("DELETE FROM invites WHERE ts<?", (time.time() - 3600,))
    rows = dbq("SELECT i.id, i.room, i.ts, u.name, u.username, (SELECT ts FROM avatars a WHERE a.sub=u.sub) AS av "
               "FROM invites i JOIN users u ON u.sub=i.from_sub WHERE i.to_sub=? ORDER BY i.ts DESC",
               (sub,), many=True)
    with CV:
        live = [r for r in rows if r["room"] in ROOMS]
    out = [dict(id=r["id"], room=r["room"], ts=r["ts"], name=r["name"], username=r["username"] or "",
                avatar=("/api/avatar/%s?v=%d" % (r["username"], int(r["av"]))) if r["av"] and r["username"] else "")
           for r in live]
    resp = jsonify(invites=out, games=_game_invites_for(sub))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/invite/dismiss", methods=["POST"])
def api_invite_dismiss():
    sub, _ = auth_sub()
    try:
        iid = int((request.get_json(silent=True) or {}).get("id"))
    except (TypeError, ValueError):
        abort(400)
    dbq("DELETE FROM invites WHERE id=? AND to_sub=?", (iid, sub))
    return jsonify(ok=True)


@app.route("/api/settings/avatar-visibility", methods=["POST"])
def api_settings_avatar_visibility():
    sub, _ = auth_sub()
    value = str((request.get_json(silent=True) or {}).get("visibility") or "everyone")
    if value not in ("everyone", "nobody", "friends"):
        abort(400)
    dbq("UPDATE users SET avatar_visibility=? WHERE sub=?", (value, sub))
    return jsonify(ok=True, avatar_visibility=value)


@app.route("/api/settings/invites", methods=["POST"])
def api_settings_invites():
    sub, _ = auth_sub()
    on = bool((request.get_json(silent=True) or {}).get("open"))
    dbq("UPDATE users SET invites_open=? WHERE sub=?", (1 if on else 0, sub))
    if not on:
        dbq("DELETE FROM invites WHERE to_sub=?", (sub,))   # bağlananda gözləyən dəvətlər də silinir
        GAME_INV.pop(sub, None)
    return jsonify(ok=True, invites_open=on)


# ---------------------------------------------------------------
#  Oyun dəvətləri (domino / şahmat): istifadəçi adı ilə və ya dost siyahısından
#  Yaddaşda saxlanır (oyunların özü kimi), 10 dəqiqə etibarlıdır.
# ---------------------------------------------------------------
GAME_INV = {}        # to_sub -> {(game, code, from_sub): dəvət}
GAME_INV_TTL = 600


def _game_store(kind):
    if kind == "durak":
        return DURAK
    return DOMINO if kind == "domino" else CHESS


def _game_invites_for(sub):
    now = time.time()
    lst = GAME_INV.get(sub)
    if not lst:
        return []
    out = []
    with CV:
        for k, i in list(lst.items()):
            g = _game_store(i["game"]).get(i["room"])
            if (now - i["ts"] > GAME_INV_TTL or not g or g["status"] != "wait" or sub in g["players"]
                    or len(g["players"]) >= g.get("size", 2)):
                lst.pop(k, None)
                continue
            out.append(dict(id=i["id"], game=i["game"], room=i["room"], name=i["name"],
                            username=i["username"], avatar=i["avatar"], ts=i["ts"]))
    if not lst:
        GAME_INV.pop(sub, None)
    out.sort(key=lambda x: -x["ts"])
    return out


@app.route("/api/game/invite", methods=["POST"])
def api_game_invite():
    sub, tok = auth_sub()
    d = request.get_json(silent=True) or {}
    kind = d.get("game")
    u = norm_username(d.get("username"))
    code = clean_room(d.get("room"))
    if kind not in ("domino", "chess", "durak") or not username_valid(u) or not code:
        abort(400)
    if not rate_hit("ginv", sub, 20, 600):
        abort(429)
    row = dbq("SELECT sub, invites_open FROM users WHERE username=?", (u,), one=True)
    if not row:
        return jsonify(error="user"), 404
    if row["sub"] == sub:
        abort(400)
    if _is_blocked_either(sub, row["sub"]):
        return jsonify(error="blocked"), 403
    if row["invites_open"] == 0:
        return jsonify(error="closed"), 403
    with CV:
        g = _game_store(kind).get(code)
        if not g or sub not in g["players"] or g["status"] != "wait":
            return jsonify(error="room"), 404
        if row["sub"] in g["players"]:
            return jsonify(error="in"), 409
        if len(g["players"]) >= g.get("size", 2):
            return jsonify(error="full"), 409
    idn = ident_for(tok)
    if len(GAME_INV) > 5000:
        GAME_INV.clear()
    GAME_INV.setdefault(row["sub"], {})[(kind, code, sub)] = dict(
        id=secrets.token_hex(6), game=kind, room=code, ts=time.time(), name=idn["name"],
        username=idn["username"], avatar=idn["avatar"])
    return jsonify(ok=True)


@app.route("/api/game/invite/dismiss", methods=["POST"])
def api_game_invite_dismiss():
    sub, _ = auth_sub()
    iid = str((request.get_json(silent=True) or {}).get("id") or "")[:20]
    lst = GAME_INV.get(sub, {})
    for k, i in list(lst.items()):
        if i["id"] == iid:
            lst.pop(k, None)
    return jsonify(ok=True)


# ---------------------------------------------------------------
#  Dostluq: istifadəçi adı ilə axtar, sorğu göndər, qəbul et
# ---------------------------------------------------------------
def _av_url(username, ts):
    return ("/api/avatar/%s?v=%d" % (username, int(ts))) if ts and username else ""


def _friend_rel(me, other):
    row = dbq("SELECT a, status FROM friends WHERE (a=? AND b=?) OR (a=? AND b=?)",
              (me, other, other, me), one=True)
    if not row:
        return "none"
    if row["status"] == 1:
        return "friend"
    return "out" if row["a"] == me else "in"


def _user_by_name(raw):
    u = norm_username(raw)
    if not username_valid(u):
        abort(400)
    row = dbq("SELECT sub FROM users WHERE username=?", (u,), one=True)
    if not row:
        abort(404)
    return row["sub"]


def _is_blocked_either(a, b):
    return bool(dbq("SELECT 1 FROM blocks WHERE (blocker=? AND blocked=?) OR (blocker=? AND blocked=?)", (a, b, b, a), one=True))


def _presence_info(viewer, target):
    u = dbq("SELECT last_seen_visibility FROM users WHERE sub=?", (target,), one=True)
    if not u:
        return dict(presence="")
    visibility = (u["last_seen_visibility"] or "everyone")
    if visibility == "nobody":
        return dict(presence="")
    if visibility == "friends" and viewer != target and _friend_rel(viewer, target) != "friend":
        return dict(presence="")
    if viewer != target and _is_blocked_either(viewer, target):
        return dict(presence="")
    row = dbq("SELECT seen FROM presence WHERE sub=?", (target,), one=True)
    if not row:
        return dict(presence="")
    seen = float(row["seen"])
    age = max(0, int(time.time() - seen))
    if age < 45:
        return dict(presence="Onlayn", online=True)
    if age < 3600:
        label = f"Son görülmə: {max(1, age // 60)} dəq. əvvəl"
    elif age < 86400:
        label = f"Son görülmə: {age // 3600} saat əvvəl"
    else:
        label = f"Son görülmə: {age // 86400} gün əvvəl"
    return dict(presence=label, online=False)


@app.route("/api/presence", methods=["POST"])
def api_presence():
    sub, _ = auth_sub()
    now = time.time()
    dbq("INSERT INTO presence(sub,seen) VALUES(?,?) ON CONFLICT(sub) DO UPDATE SET seen=excluded.seen", (sub, now))
    return jsonify(ok=True)


@app.route("/api/settings/last-seen-visibility", methods=["POST"])
def api_settings_last_seen_visibility():
    sub, _ = auth_sub()
    value = str((request.get_json(silent=True) or {}).get("visibility") or "everyone")
    if value not in ("everyone", "friends", "nobody"):
        abort(400)
    dbq("UPDATE users SET last_seen_visibility=? WHERE sub=?", (value, sub))
    return jsonify(ok=True, last_seen_visibility=value)


@app.route("/api/blocks")
def api_blocks():
    sub, _ = auth_sub()
    rows = dbq("SELECT u.name,u.username FROM blocks b JOIN users u ON u.sub=b.blocked WHERE b.blocker=? ORDER BY b.ts DESC", (sub,), many=True)
    return jsonify(blocked=[dict(name=r["name"], username=r["username"]) for r in rows if r["username"]])


@app.route("/api/block", methods=["POST"])
def api_block_user():
    sub, _ = auth_sub()
    other = _user_by_name((request.get_json(silent=True) or {}).get("username"))
    if other == sub:
        abort(400)
    dbq("INSERT OR IGNORE INTO blocks(blocker,blocked,ts) VALUES(?,?,?)", (sub, other, time.time()))
    dbq("DELETE FROM friends WHERE (a=? AND b=?) OR (a=? AND b=?)", (sub, other, other, sub))
    dbq("DELETE FROM invites WHERE (to_sub=? AND from_sub=?) OR (to_sub=? AND from_sub=?)", (sub, other, other, sub))
    other_row = dbq("SELECT username FROM users WHERE sub=?", (other,), one=True)
    me_row = dbq("SELECT username FROM users WHERE sub=?", (sub,), one=True)
    other_username = other_row["username"] if other_row else None
    my_username = me_row["username"] if me_row else None
    for recipient, items in list(GAME_INV.items()):
        for key, item in list(items.items()):
            if ((recipient == sub and item.get("username") == other_username) or
                    (recipient == other and item.get("username") == my_username)):
                items.pop(key, None)
        if not items:
            GAME_INV.pop(recipient, None)
    return jsonify(ok=True)


@app.route("/api/unblock", methods=["POST"])
def api_unblock_user():
    sub, _ = auth_sub()
    other = _user_by_name((request.get_json(silent=True) or {}).get("username"))
    dbq("DELETE FROM blocks WHERE blocker=? AND blocked=?", (sub, other))
    return jsonify(ok=True)


@app.route("/api/users/search")
def api_users_search():
    sub, _ = auth_sub()
    q = re.sub(r"[^a-z0-9._]", "", str(request.args.get("q") or "").lower().lstrip("@"))[:20]
    if len(q) < 2:
        return jsonify(users=[])
    if not rate_hit("usearch", sub, 40, 60):
        abort(429)
    like = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    rows = dbq("SELECT u.sub, u.name, u.username, u.bio, (SELECT ts FROM avatars a WHERE a.sub=u.sub) AS av \
FROM users u WHERE u.username LIKE ? ESCAPE '\\' AND u.sub<>? ORDER BY (u.username=?) DESC, u.username LIMIT 8",
               (like, sub, q), many=True)
    out = []
    for r in rows:
        blocked_by_me = bool(dbq("SELECT 1 FROM blocks WHERE blocker=? AND blocked=?", (sub, r["sub"]), one=True))
        blocked_me = bool(dbq("SELECT 1 FROM blocks WHERE blocker=? AND blocked=?", (r["sub"], sub), one=True))
        pres = _presence_info(sub, r["sub"])
        out.append(dict(name=r["name"], username=r["username"], avatar=_av_url(r["username"], r["av"]),
                        badge=is_owner(r["sub"]), rel=_friend_rel(sub, r["sub"]), bio=r["bio"] or "",
                        blocked=blocked_by_me, blocked_me=blocked_me, **pres))
    return jsonify(users=out)


@app.route("/api/friends")
def api_friends():
    sub, _ = auth_sub()
    rows = dbq("SELECT f.a, f.b, f.status, u.sub AS osub, u.name, u.username, "
               "(SELECT ts FROM avatars a WHERE a.sub=u.sub) AS av "
               "FROM friends f JOIN users u ON u.sub = CASE WHEN f.a=? THEN f.b ELSE f.a END "
               "WHERE f.a=? OR f.b=? ORDER BY f.ts DESC", (sub, sub, sub), many=True)
    friends, incoming, outgoing = [], [], []
    for r in rows:
        if not r["username"]:
            continue
        pres = _presence_info(sub, r["osub"])
        blocked = bool(dbq("SELECT 1 FROM blocks WHERE blocker=? AND blocked=?", (sub, r["osub"]), one=True))
        it = dict(name=r["name"], username=r["username"], avatar=_av_url(r["username"], r["av"]),
                  badge=is_owner(r["osub"]), blocked=blocked, **pres)
        if r["status"] == 1:
            friends.append(it)
        elif r["b"] == sub:
            incoming.append(it)
        else:
            outgoing.append(it)
    friends.sort(key=lambda x: x["name"].lower())
    resp = jsonify(friends=friends, incoming=incoming, outgoing=outgoing)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/friend/request", methods=["POST"])
def api_friend_request():
    sub, _ = auth_sub()
    other = _user_by_name((request.get_json(silent=True) or {}).get("username"))
    if other == sub:
        abort(400)
    if _is_blocked_either(sub, other):
        abort(403)
    if not rate_hit("freq", sub, 20, 600):
        abort(429)
    rel = _friend_rel(sub, other)
    if rel in ("friend", "out"):
        return jsonify(ok=True, rel=rel)
    if rel == "in":   # o artıq sənə sorğu göndərib: dərhal dost olursunuz
        dbq("UPDATE friends SET status=1, ts=? WHERE a=? AND b=?", (time.time(), other, sub))
        return jsonify(ok=True, rel="friend")
    if dbq("SELECT COUNT(*) FROM friends WHERE a=? AND status=0", (sub,), one=True)[0] >= 50:
        abort(429)
    dbq("INSERT OR IGNORE INTO friends(a,b,status,ts) VALUES(?,?,0,?)", (sub, other, time.time()))
    return jsonify(ok=True, rel="out")


@app.route("/api/friend/respond", methods=["POST"])
def api_friend_respond():
    sub, _ = auth_sub()
    d = request.get_json(silent=True) or {}
    other = _user_by_name(d.get("username"))
    if _is_blocked_either(sub, other):
        abort(403)
    if d.get("accept"):
        dbq("UPDATE friends SET status=1, ts=? WHERE a=? AND b=? AND status=0", (time.time(), other, sub))
    else:
        dbq("DELETE FROM friends WHERE a=? AND b=? AND status=0", (other, sub))
    return jsonify(ok=True)


@app.route("/api/friend/remove", methods=["POST"])
def api_friend_remove():
    sub, _ = auth_sub()
    other = _user_by_name((request.get_json(silent=True) or {}).get("username"))
    dbq("DELETE FROM friends WHERE (a=? AND b=?) OR (a=? AND b=?)", (sub, other, other, sub))
    return jsonify(ok=True)


@app.route("/api/logout", methods=["POST"])
def api_logout():
    _, tok = auth_sub()
    dbq("DELETE FROM sessions WHERE token=?", (tok,))
    SESS_CACHE.clear()
    return jsonify(ok=True)


@app.route("/api/activity", methods=["POST"])
def api_activity():
    sub, _ = auth_sub()
    d = request.get_json(silent=True) or {}
    now = time.time()
    if d.get("kind") == "room":
        code = clean_room(d.get("room"))
        if not code:
            abort(400)
        if not dbq("SELECT 1 FROM activity WHERE sub=? AND kind='room' AND k=? AND ts>?",
                   (sub, code, now - 3600), one=True):
            dbq("INSERT INTO activity(sub,kind,k,room,ts) VALUES(?,?,?,?,?)", (sub, "room", code, code, now))
        return jsonify(ok=True)
    url = str(d.get("url") or "")[:1000]
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        abort(400)
    vid = yt_id(url)
    k, win = ("yt:" + vid, 600) if vid else ("host:" + p.hostname.lower(), 1800)
    if dbq("SELECT 1 FROM activity WHERE sub=? AND kind='video' AND k=? AND ts>?", (sub, k, now - win), one=True):
        return jsonify(ok=True)
    title = (yt_title(vid) or "YouTube videosu") if vid else p.hostname
    thumb = "https://i.ytimg.com/vi/" + vid + "/mqdefault.jpg" if vid else ""
    dbq("INSERT INTO activity(sub,kind,k,title,url,thumb,ts) VALUES(?,?,?,?,?,?,?)",
        (sub, "video", k, title, url, thumb, now))
    return jsonify(ok=True)


@app.route("/api/history")
def api_history():
    sub, _ = auth_sub()
    vids = dbq("SELECT title,url,thumb,ts FROM activity WHERE sub=? AND kind='video' ORDER BY ts DESC LIMIT 60",
               (sub,), many=True)
    rooms = dbq("SELECT room, MAX(ts) AS ts FROM activity WHERE sub=? AND kind='room' "
                "GROUP BY room ORDER BY ts DESC LIMIT 20", (sub,), many=True)
    resp = jsonify(videos=[dict(r) for r in vids], rooms=[dict(r) for r in rooms])
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/history/clear", methods=["POST"])
def api_history_clear():
    sub, _ = auth_sub()
    dbq("DELETE FROM activity WHERE sub=?", (sub,))
    return jsonify(ok=True)


@app.route("/api/config")
def api_config():
    resp = jsonify(google_client_id=os.environ.get("GOOGLE_CLIENT_ID", ""))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/google", methods=["POST"])
def api_google():
    """Google ID token-i yoxlayır; e-poçt eyni olan hesablar birləşir (Google + e-poçt = bir hesab). Sessiya tokeni qaytarır."""
    cid = os.environ.get("GOOGLE_CLIENT_ID", "")
    tok = str((request.get_json(silent=True) or {}).get("credential", ""))[:4000]
    if not cid or not tok:
        abort(400)
    try:
        r = requests.get("https://oauth2.googleapis.com/tokeninfo",
                         params={"id_token": tok}, timeout=8)
        d = r.json()
    except Exception:
        abort(502)
    if (r.status_code != 200 or d.get("aud") != cid
            or d.get("iss") not in ("accounts.google.com", "https://accounts.google.com")):
        abort(401)
    gsub = str(d.get("sub") or "")
    email = str(d.get("email") or "").strip().lower()
    if not gsub or not EMAIL_RE.match(email) or str(d.get("email_verified")).lower() != "true":
        abort(401)
    gname = clean_name(d.get("given_name") or d.get("name")) or "Qonaq"
    row = dbq("SELECT sub, verified FROM creds WHERE email=?", (email,), one=True)
    legacy = dbq("SELECT 1 FROM users WHERE sub=?", (gsub,), one=True)
    if row:
        sub = row["sub"]
        if not row["verified"]:
            # Google e-poçtun sahibini təsdiqləyib: qeydiyyatçının qoyduğu şifrə silinir (sahibi "Şifrəmi unutdum" ilə yenisini qoyar)
            dbq("UPDATE creds SET verified=1, pw='' WHERE email=?", (email,))
            dbq("UPDATE users SET name=? WHERE sub=?", (gname, sub))
            assign_username(sub)
        if legacy and gsub != sub:
            merge_users(gsub, sub)   # köhnə Google hesabı və e-poçt hesabı birləşir
    else:
        sub = gsub
        if not legacy:
            dbq("INSERT INTO users(sub,name,created) VALUES(?,?,?)", (sub, gname, time.time()))
        dbq("INSERT INTO creds(email,sub,pw,verified) VALUES(?,?,?,1)", (email, sub, ""))
    ensure_username(sub)
    log_login("google", email, True)
    return jsonify(token=issue_token(sub), name=user_name(sub))


@app.route("/api/ice")
def api_ice():
    """Səs üçün ICE serverləri. TURN ətraf mühit dəyişənlərindən gəlir (açar HTML-də durmasın):
       TURN_URLS="turn:host:3478,turns:host:5349"  TURN_USERNAME=...  TURN_CREDENTIAL=..."""
    servers = [{"urls": ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302"]}]
    urls = [u.strip() for u in os.environ.get("TURN_URLS", "").split(",") if u.strip()]
    if urls:
        servers.append({"urls": urls,
                        "username": os.environ.get("TURN_USERNAME", ""),
                        "credential": os.environ.get("TURN_CREDENTIAL", "")})
    resp = jsonify(servers=servers, turn=bool(urls))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/poll")
def api_poll():
    code = clean_room(request.args.get("room"))
    pid = request.args.get("id", "")[:20]
    if not code or not pid:
        abort(400)
    try:
        since = int(request.args.get("since", "0"))
        pc = int(request.args.get("pc", "0"))
        rv = int(request.args.get("rv", "-1"))
    except ValueError:
        abort(400)
    lk = request.args.get("lk") == "1"
    idn = ident_for(request.headers.get("X-Token", "")[:100], request.args.get("nm", ""))
    deadline = time.time() + 20
    with CV:
        r = get_room(code)
        admit(r, pid)
        r.setdefault("who", {})[pid] = dict(name=idn["name"], username=idn["username"],
                                            avatar=idn["avatar"], badge=idn["badge"], bio=idn.get("bio", ""))
        if since > r["seq"]:  # server restart olub: köhnə "since" mesajları bloklamasın
            since = 0
        while True:
            now = time.time()
            prune_peers(r, now)
            alive_mark(r, pid, now)
            h = r.get("host")
            if h and h != pid and now - r["peers"].get(h, 0) > HOST_GRACE:
                pass_host(r, now)   # host uzun müddət yoxdur: host ilk moderatora keçir
            new = [m for m in r["msgs"] if m[0] > since and m[1] != pid]
            n = count_others(r, pid, now)
            left = deadline - now
            if new or n != pc or r["locked"] != lk or rv != r["rv"] or left <= 0:
                break
            CV.wait(timeout=left)
        blank = dict(name="", username="", avatar="", badge=False, bio="")
        who = [dict(id=p, **r["who"].get(p, blank)) for p, ts in r["peers"].items()
               if p != pid and now - ts < PEER_TTL]
        roles = {p: role_of(r, p) for p in [pid] + [w["id"] for w in who]}
        resp = jsonify(seq=r["seq"], peers=n, msgs=[m[2] for m in new], who=who,
                       locked=r["locked"], epoch=r["created"], video=r.get("video"),
                       roles=roles, hostonly=r["hostonly"], rv=r["rv"])
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------------------------------------------------------------
#  Otaq rolları, mesaj tarixçəsi, GIF, izləmə statistikası, planlı seanslar, profil məlumatı, PWA
# ---------------------------------------------------------------
def _room_ctx(d):
    code = clean_room(d.get("room"))
    pid = str(d.get("id", ""))[:20]
    if not code or not pid:
        abort(400)
    return code, pid


@app.route("/api/role", methods=["POST"])
def api_role():
    """Host üzvü moderator edir / moderatorluğu alır."""
    d = request.get_json(silent=True) or {}
    code, pid = _room_ctx(d)
    target = str(d.get("target", ""))[:20]
    role = d.get("role")
    if role not in ("mod", "viewer"):
        abort(400)
    with CV:
        r = ROOMS.get(code)
        if r is None:
            abort(404)
        if r["host"] != pid:
            abort(403)
        if target == pid or target not in r["members"]:
            abort(400)
        if role == "mod":
            r["roles"][target] = "mod"
        else:
            r["roles"].pop(target, None)
        r["rv"] += 1
        CV.notify_all()
        return jsonify(ok=True)


@app.route("/api/hostonly", methods=["POST"])
def api_hostonly():
    """\"Yalnız host idarə edir\" rejimi (host + moderatorlar idarə edir, izləyicilər yalnız baxır)."""
    d = request.get_json(silent=True) or {}
    code, pid = _room_ctx(d)
    with CV:
        r = ROOMS.get(code)
        if r is None:
            abort(404)
        if r["host"] != pid:
            abort(403)
        r["hostonly"] = bool(d.get("on"))
        r["rv"] += 1
        CV.notify_all()
        return jsonify(ok=True, hostonly=r["hostonly"])


@app.route("/api/host", methods=["POST"])
def api_host():
    """Host hostluğu otaqdakı başqa üzvə verir; özü hostdan çıxır (adi izləyici olur)."""
    d = request.get_json(silent=True) or {}
    code, pid = _room_ctx(d)
    target = str(d.get("target", ""))[:20]
    now = time.time()
    with CV:
        r = ROOMS.get(code)
        if r is None:
            abort(404)
        if r["host"] != pid:
            abort(403)
        if target == pid or target not in r["members"]:
            abort(400)
        ts = r["peers"].get(target)
        if ts is None or now - ts >= PEER_TTL:
            abort(409)   # hədəf hazırda otaqda deyil
        r["host"] = target
        r["roles"].pop(target, None)
        r["roles"].pop(pid, None)
        r["rv"] += 1
        CV.notify_all()
        return jsonify(ok=True)


@app.route("/api/chat/history")
def api_chat_history():
    code = clean_room(request.args.get("room"))
    pid = str(request.args.get("id", ""))[:20]
    if not code or not pid:
        abort(400)
    with CV:
        r = ROOMS.get(code)
        if r is None:
            abort(404)
        if pid not in r["members"]:
            abort(403)
        out = []
        rx_all, mk = r.get("reacts", {}), rx_key(code, pid)
        for h in r["hist"]:
            o = {k: v for k, v in h.items() if k != "pid"}
            o["mine"] = (h["pid"] == pid)
            hid = h.get("id")
            rm = rx_all.get(hid) if isinstance(hid, str) else None
            if rm:
                o["rx"] = {k: v for k, v in rm.items() if k != mk}
                if mk in rm:
                    o["my"] = rm[mk]
            out.append(o)
    return _nostore(jsonify(msgs=out))


# ---- İzləmə statistikası ----
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@app.route("/api/watch", methods=["POST"])
def api_watch():
    sub, _ = auth_sub()
    d = request.get_json(silent=True) or {}
    try:
        secs, tog = float(d.get("secs", 0)), float(d.get("tog", 0))
        day = str(d.get("day", ""))
        dd = datetime.date.fromisoformat(day)
    except (TypeError, ValueError):
        abort(400)
    if not DAY_RE.match(day) or abs((dd - datetime.date.today()).days) > 2:
        abort(400)
    secs = max(0.0, min(secs, 120.0))
    tog = max(0.0, min(tog, secs))
    if secs <= 0:
        return jsonify(ok=True)
    if not rate_hit("watch", sub, 8, 60):
        abort(429)
    dbq("INSERT INTO watch(sub,day,secs,tog) VALUES(?,?,?,?) "
        "ON CONFLICT(sub,day) DO UPDATE SET secs=secs+excluded.secs, tog=tog+excluded.tog", (sub, day, secs, tog))
    return jsonify(ok=True)


@app.route("/api/watch/stats")
def api_watch_stats():
    sub, _ = auth_sub()
    today = request.args.get("today", "")
    try:
        t = datetime.date.fromisoformat(today)
    except ValueError:
        abort(400)
    if not DAY_RE.match(today):
        abort(400)
    m = dbq("SELECT COALESCE(SUM(secs),0) AS s, COALESCE(SUM(tog),0) AS t FROM watch WHERE sub=? AND day LIKE ?",
            (sub, today[:7] + "%"), one=True)
    a = dbq("SELECT COALESCE(SUM(secs),0) AS s, COALESCE(SUM(tog),0) AS t FROM watch WHERE sub=?", (sub,), one=True)
    days = [(t - datetime.timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    rows = dbq("SELECT day, secs FROM watch WHERE sub=? AND day>=? AND day<=?", (sub, days[0], days[-1]), many=True)
    mp = {r["day"]: r["secs"] for r in rows}
    vids = dbq("SELECT COUNT(*) AS c FROM activity WHERE sub=? AND kind='video' AND ts>?",
               (sub, time.time() - 30 * 86400), one=True)["c"]
    return _nostore(jsonify(month=m["s"], month_tog=m["t"], total=a["s"], total_tog=a["t"], videos30=vids,
                            days=[dict(d=x, s=mp.get(x, 0)) for x in days]))


# ---- Planlı seanslar ----
def clean_text(s, n):
    s = re.sub(r"[\x00-\x1f\x7f<>]", "", str(s or ""))
    return re.sub(r"\s+", " ", s).strip()[:n]


@app.route("/api/plan/create", methods=["POST"])
def api_plan_create():
    sub, _ = auth_sub()
    d = request.get_json(silent=True) or {}
    title = clean_text(d.get("title"), 40)
    try:
        ts = float(d.get("ts"))
    except (TypeError, ValueError):
        abort(400)
    now = time.time()
    if not title or ts < now - 300 or ts > now + 365 * 86400:
        abort(400)
    if not rate_hit("plan", sub, 10, 600):
        abort(429)
    if dbq("SELECT COUNT(*) AS c FROM plans WHERE owner=? AND ts>?", (sub, now - 6 * 3600), one=True)["c"] >= 20:
        abort(429)
    subs = []
    fr = d.get("friends")
    for u in (fr if isinstance(fr, list) else [])[:10]:
        u = norm_username(u)
        if not username_valid(u):
            continue
        row = dbq("SELECT sub FROM users WHERE username=?", (u,), one=True)
        if row and row["sub"] != sub and row["sub"] not in subs and _friend_rel(sub, row["sub"]) == "friend":
            subs.append(row["sub"])
    created = time.time()
    dbq("INSERT INTO plans(owner,title,ts,room,created) VALUES(?,?,?,NULL,?)", (sub, title, ts, created))
    pid = dbq("SELECT id FROM plans WHERE owner=? AND created=?", (sub, created), one=True)["id"]
    for s in subs:
        dbq("INSERT OR IGNORE INTO plan_inv(plan_id,sub,status) VALUES(?,?,0)", (pid, s))
    return jsonify(ok=True, id=pid)


@app.route("/api/plans")
def api_plans():
    sub, _ = auth_sub()
    now = time.time()
    dbq("DELETE FROM plans WHERE ts<?", (now - 6 * 3600,))
    dbq("DELETE FROM plan_inv WHERE plan_id NOT IN (SELECT id FROM plans)")
    rows = dbq("SELECT p.id, p.title, p.ts, p.room, p.owner, u.name AS oname, u.username AS ouser "
               "FROM plans p JOIN users u ON u.sub=p.owner "
               "WHERE p.owner=? OR p.id IN (SELECT plan_id FROM plan_inv WHERE sub=?) ORDER BY p.ts",
               (sub, sub), many=True)
    out = []
    for p in rows:
        inv = dbq("SELECT i.sub, i.status, u.name, u.username FROM plan_inv i JOIN users u ON u.sub=i.sub "
                  "WHERE i.plan_id=?", (p["id"],), many=True)
        room = p["room"] or ""
        if room:
            with CV:
                if room not in ROOMS:
                    room = ""
        mine = p["owner"] == sub
        my = -1 if mine else next((i["status"] for i in inv if i["sub"] == sub), 0)
        out.append(dict(id=p["id"], title=p["title"], ts=p["ts"], mine=mine, room=room, my=my,
                        owner=dict(name=p["oname"], username=p["ouser"] or ""),
                        guests=[dict(name=i["name"], username=i["username"] or "", st=i["status"]) for i in inv] if mine else []))
    return _nostore(jsonify(plans=out))


@app.route("/api/plan/respond", methods=["POST"])
def api_plan_respond():
    sub, _ = auth_sub()
    d = request.get_json(silent=True) or {}
    try:
        pid = int(d.get("id"))
    except (TypeError, ValueError):
        abort(400)
    dbq("UPDATE plan_inv SET status=? WHERE plan_id=? AND sub=?", (1 if d.get("going") else 2, pid, sub))
    return jsonify(ok=True)


@app.route("/api/plan/delete", methods=["POST"])
def api_plan_delete():
    sub, _ = auth_sub()
    try:
        pid = int((request.get_json(silent=True) or {}).get("id"))
    except (TypeError, ValueError):
        abort(400)
    if dbq("SELECT 1 FROM plans WHERE id=? AND owner=?", (pid, sub), one=True):
        dbq("DELETE FROM plan_inv WHERE plan_id=?", (pid,))
        dbq("DELETE FROM plans WHERE id=?", (pid,))
    return jsonify(ok=True)


@app.route("/api/plan/start", methods=["POST"])
def api_plan_start():
    """Seans sahibi otağı açır, gələcəyini bildirən (və cavab verməyən) dostlara dəvət gedir."""
    sub, _ = auth_sub()
    d = request.get_json(silent=True) or {}
    try:
        plan_id = int(d.get("id"))
    except (TypeError, ValueError):
        abort(400)
    pid = str(d.get("pid", ""))[:20]
    if not pid:
        abort(400)
    p = dbq("SELECT id, room FROM plans WHERE id=? AND owner=?", (plan_id, sub), one=True)
    if not p:
        abort(404)
    with CV:
        code = p["room"] if p["room"] in ROOMS else None
        if code:
            ROOMS[code]["host"] = pid
        else:
            code = _new_room(pid)
    dbq("UPDATE plans SET room=? WHERE id=?", (code, plan_id))
    now = time.time()
    for i in dbq("SELECT sub FROM plan_inv WHERE plan_id=? AND status<>2", (plan_id,), many=True):
        dbq("INSERT OR REPLACE INTO invites(to_sub,from_sub,room,ts) VALUES(?,?,?,?)", (i["sub"], sub, code, now))
    return jsonify(room=code)


# ---- Profil: qısa məlumat ----
@app.route("/api/profile/bio", methods=["POST"])
def api_profile_bio():
    sub, _ = auth_sub()
    b = clean_text((request.get_json(silent=True) or {}).get("bio"), 80)
    dbq("UPDATE users SET bio=? WHERE sub=?", (b or None, sub))
    return jsonify(profile_json(sub))


# ---- PWA: manifest, service worker, ikonlar (ikonlar şəkil faylı olmadan kodla çəkilir) ----
SW_JS = """const C='wt-shell-v1';
self.addEventListener('install',e=>{self.skipWaiting();e.waitUntil(caches.open(C).then(c=>c.add('/')).catch(()=>{}));});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(k=>Promise.all(k.filter(x=>x!==C).map(x=>caches.delete(x)))).then(()=>self.clients.claim()));});
self.addEventListener('fetch',e=>{
  const r=e.request;
  if(r.method!=='GET'||r.mode!=='navigate')return;
  const u=new URL(r.url);
  if(u.origin!==location.origin||u.pathname!=='/')return;
  e.respondWith(fetch(r).then(res=>{const cp=res.clone();caches.open(C).then(c=>c.put('/',cp)).catch(()=>{});return res;})
    .catch(()=>caches.match('/').then(m=>m||new Response('Əlaqə yoxdur. İnternetə qoşulub yenidən yoxla.',{status:503,headers:{'Content-Type':'text/plain; charset=utf-8'}}))));
});
self.addEventListener('notificationclick',e=>{
  e.notification.close();
  e.waitUntil(clients.matchAll({type:'window',includeUncontrolled:true}).then(l=>l.length?l[0].focus():clients.openWindow('/')));
});
"""

MANIFEST = {
    "name": "Birlikdə İzlə", "short_name": "Birlikdə", "description": "Uzaqda olsanız da, eyni kadrdasınız.",
    "lang": "az", "start_url": "/", "scope": "/", "display": "standalone", "orientation": "any",
    "background_color": "#150a1d", "theme_color": "#150a1d",
    "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
              {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
}
ICONS = {}


def make_icon(n):
    """Tünd fonda ürək + play üçbucağı (loqo ilə eyni), saf Python ilə PNG."""
    if n in ICONS:
        return ICONS[n]
    bg, heart, tri = (21, 10, 29), (232, 86, 125), (248, 231, 225)
    S, sc, cy = 2, 0.27, 0.534
    A, B, C = (0.45, 0.41), (0.45, 0.61), (0.62, 0.51)

    def inside_tri(px, py):
        def sg(p1, p2):
            return (px - p2[0]) * (p1[1] - p2[1]) - (p1[0] - p2[0]) * (py - p2[1])
        d1, d2, d3 = sg(A, B), sg(B, C), sg(C, A)
        return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))

    raw = bytearray()
    for y in range(n):
        raw.append(0)
        for x in range(n):
            acc = [0, 0, 0]
            for sy in range(S):
                for sx in range(S):
                    px, py = (x + (sx + .5) / S) / n, (y + (sy + .5) / S) / n
                    u, v = (px - .5) / sc, -(py - cy) / sc
                    if inside_tri(px, py):
                        c = tri
                    elif (u * u + v * v - 1) ** 3 - u * u * v ** 3 <= 0:
                        c = heart
                    else:
                        c = bg
                    acc[0] += c[0]; acc[1] += c[1]; acc[2] += c[2]
            k = S * S
            raw += bytes((acc[0] // k, acc[1] // k, acc[2] // k))

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))
    ICONS[n] = png
    return png


@app.route("/manifest.webmanifest")
def pwa_manifest():
    resp = Response(json.dumps(MANIFEST, ensure_ascii=False), content_type="application/manifest+json; charset=utf-8")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@app.route("/sw.js")
def pwa_sw():
    resp = Response(SW_JS, content_type="application/javascript; charset=utf-8")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@app.route("/icon-<int:n>.png")
def pwa_icon(n):
    if n not in (180, 192, 512):
        abort(404)
    resp = Response(make_icon(n), mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=604800"
    return resp


@app.route("/apple-touch-icon.png")
def pwa_apple_icon():
    return pwa_icon(180)


# ---------------------------------------------------------------
#  YouTube axtarışı (server tərəfdən)
# ---------------------------------------------------------------
@app.route("/api/yt")
def api_yt():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    try:
        r = requests.get(
            "https://www.youtube.com/results",
            params={"search_query": q},
            headers={
                "User-Agent": UA_DESKTOP,
                "Accept-Language": "en-US,en;q=0.9",
                "Cookie": "CONSENT=YES+cb.20210328-17-p0.en+FX+000; SOCS=CAI",
            },
            timeout=15,
        )
        m = re.search(r"var ytInitialData\s*=\s*(\{.*?\});\s*</script>", r.text, re.S)
        if not m:
            return jsonify(error="parse"), 502
        data = json.loads(m.group(1))
    except Exception as e:
        return jsonify(error=str(e)), 502

    out, seen = [], set()

    def walk(o):
        if len(out) >= 20:
            return
        if isinstance(o, dict):
            vr = o.get("videoRenderer")
            if isinstance(vr, dict) and vr.get("videoId") and vr["videoId"] not in seen:
                vid = vr["videoId"]
                seen.add(vid)
                try:
                    title = vr["title"]["runs"][0]["text"]
                except Exception:
                    title = vid
                try:
                    channel = vr["ownerText"]["runs"][0]["text"]
                except Exception:
                    channel = ""
                length = (vr.get("lengthText") or {}).get("simpleText", "")
                out.append({
                    "id": vid,
                    "title": title,
                    "channel": channel,
                    "len": length,
                    "thumb": "https://i.ytimg.com/vi/" + vid + "/mqdefault.jpg",
                })
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return jsonify(out)


# ---------------------------------------------------------------
#  Proxy
# ---------------------------------------------------------------
INJECT = r"""
(function(){
  var P = location.origin + '/p?u=';
  var NESTED = window.parent !== window.top;
  var T0 = Date.now();
  function abs(u){ try { return new URL(u, document.baseURI).href; } catch(e){ return null; } }
  function go(u, auto){ if(u && /^https?:/i.test(u)) window.top.postMessage({t:'nav', url:u, auto:!!auto}, '*'); }
  function same(a, b){
    function n(u){ return String(u).split('#')[0].replace(/\/+$/, ''); }
    return n(a) === n(b);
  }

  document.addEventListener('click', function(e){
    var a = e.target.closest && e.target.closest('a[href]');
    if(!a) return;
    var h = a.getAttribute('href');
    if(!h || /^(javascript|mailto|tel):/i.test(h)) return;
    e.preventDefault();
    if(h.charAt(0) === '#'){ var t = document.getElementById(h.slice(1)); if(t) t.scrollIntoView(); return; }
    if(!NESTED) go(abs(h));
  }, true);

  document.addEventListener('submit', function(e){
    var f = e.target;
    e.preventDefault();
    var meth = (f.method || 'get').toLowerCase();
    if(NESTED) return;
    /* Proxy yalnız GET bilir. Parolsuz POST formalarını (axtarış qutuları) GET kimi göndəririk,
       parollu formaları isə link kimi paylaşılmasın deyə bloklayırıq. */
    if(meth !== 'get' && (meth !== 'post' || f.querySelector('input[type=password]'))) return;
    var u = new URL(abs(f.getAttribute('action') || '') || document.baseURI);
    new FormData(f).forEach(function(v, k){ if(typeof v === 'string') u.searchParams.set(k, v); });
    go(u.href);
  }, true);

  window.open = function(u){ if(u && !NESTED) go(abs(u)); return null; };

  /* Sayt özü başqa ünvana yönləndirəndə (location.href, meta refresh və s.)
     səhifə proxy-dən çıxmasın deyə tuturuq və proxy ilə yenidən açırıq.
     Öz-özünə yönləndirmə və tez-tez yenilənmə dövrəsi kəsilir. */
  try {
    if(!NESTED && window.navigation && navigation.addEventListener){
      navigation.addEventListener('navigate', function(e){
        try {
          if(!e.cancelable || e.hashChange || e.downloadRequest !== null) return;
          if(e.destination.sameDocument) return;
          var d = e.destination.url;
          if(!/^https?:/i.test(d)) return;
          if(e.navigationType === 'reload'){ e.preventDefault(); return; }  /* sayt özü yenilənə bilməz */
          if(d.indexOf(location.origin + '/p?') === 0) return;
          e.preventDefault();
          if(same(d, document.baseURI)) return;
          go(d, true);  /* skriptin etdiyi yönləndirmə (auto) */
        } catch(x){}
      });
    }
  } catch(x){}

  function fixFrames(){
    document.querySelectorAll('iframe[src]').forEach(function(f){
      var s = f.getAttribute('src');
      if(!s || s.indexOf(P) === 0 || /^(about|javascript|data|blob):/i.test(s)) return;
      var u = abs(s);
      f._fx = (f._fx || 0) + 1;          /* sayt src-ni təkrar dəyişirsə sonsuz yenilənmə olmasın */
      if(f._fx > 3) return;
      if(u && /^https?:/i.test(u)) f.setAttribute('src', P + encodeURIComponent(u));
    });
  }

  var ignUntil = 0, bound = new WeakSet(), lead = false, rem = null;
  function ignoring(){ return Date.now() < ignUntil; }
  /* Qarşı tərəfdən gələn əmrin əks-sədasını (echo) göndərmirik */
  function isEcho(playing, time){
    if(!rem || Date.now() - rem.at > 6000) return false;
    var exp = rem.time + (rem.playing ? (Date.now() - rem.at) / 1000 : 0);
    return rem.playing === playing && Math.abs(time - exp) < 1.5;
  }
  function bind(v){
    if(bound.has(v)) return;
    bound.add(v);
    ['play', 'pause', 'seeked'].forEach(function(ev){
      v.addEventListener(ev, function(){
        if(ignoring() || isEcho(!v.paused, v.currentTime)) return;
        lead = true;
        window.top.postMessage({t:'v', e:ev, time:v.currentTime}, '*');
      });
    });
  }
  function mainVideo(){
    var a = [].slice.call(document.querySelectorAll('video'));
    a.sort(function(x, y){ return y.clientWidth * y.clientHeight - x.clientWidth * x.clientHeight; });
    return a[0];
  }
  setInterval(function(){
    fixFrames();
    document.querySelectorAll('video').forEach(bind);
  }, 1000);
  /* Aparıcı tərəf hər 3 san vaxtı göndərir, fərq 2 san-dən çoxdursa digər tərəf düzəldir */
  setInterval(function(){
    if(!lead || ignoring()) return;
    var v = mainVideo();
    if(v && !v.paused) window.top.postMessage({t:'v', e:'tick', time:v.currentTime}, '*');
  }, 3000);

  window.addEventListener('message', function(e){
    var m = e.data;
    if(!m || m.t !== 'cmd') return;
    var v = mainVideo();
    if(v){
      ignUntil = Date.now() + 700;
      lead = false;
      var playing = m.e === 'play' || m.e === 'tick' ? true : (m.e === 'pause' ? false : !v.paused);
      rem = {playing: playing, time: m.time, at: Date.now()};
      try {
        var tick = m.e === 'tick';
        if(Math.abs(v.currentTime - m.time) > (tick ? 2 : 0.7)) v.currentTime = m.time + (tick ? 0.4 : 0);
        if(m.e === 'play' || (tick && v.paused)){
          var p = v.play(); if(p && p.catch) p.catch(function(){});
        } else if(m.e === 'pause') v.pause();
      } catch(x){}
    }
    document.querySelectorAll('iframe').forEach(function(f){
      try { f.contentWindow.postMessage(m, '*'); } catch(x){}
    });
  });
  /* Sayt xəta kodu qaytarıbsa (403 və s.) boş ekran əvəzinə səbəbi göstər */
  if(window.__UP >= 400 && !NESTED){
    document.addEventListener('DOMContentLoaded', function(){
      var b = document.createElement('div');
      b.textContent = 'Sayt ' + window.__UP + ' cavabı qaytardı (server bloklanıb və ya giriş/yaş təsdiqi tələb edir)';
      b.style.cssText = 'position:fixed;left:0;right:0;top:0;z-index:2147483647;background:#b00020;color:#fff;font:13px system-ui;padding:6px 10px;text-align:center';
      document.body.appendChild(b);
    });
  }
})();
"""


def safe(url):
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            return False
        for info in socket.getaddrinfo(p.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.version == 6 and ip.ipv4_mapped:  # ::ffff:127.0.0.1 kimi hiylələr
                ip = ip.ipv4_mapped
            if not ip.is_global or ip.is_multicast:
                return False
        return True
    except Exception:
        return False



# ---------------------------------------------------------------
#  Admin panel: giriş əsas saytın şifrə xanasından (e-poçt boş, şifrə = ADMIN_PASSWORD)
#  Mühit dəyişəni:  ADMIN_PASSWORD=güclü-şifrə   (təyin olunmayıbsa panel söndürülüb)
#  Giriş, uğursuz cəhd və ziyarətlər bazada saxlanır, 90 gündən köhnələr silinir.
# ---------------------------------------------------------------
ADMIN_TTL = 12 * 3600
LOG_KEEP_DAYS = 90
LAST_LOG_PURGE = [0.0]


def log_login(kind, email, ok):
    try:
        dbq("INSERT INTO logins(ts,kind,email,ip,ok) VALUES(?,?,?,?,?)",
            (time.time(), kind, str(email or "")[:254], client_ip(), 1 if ok else 0))
        purge_logs()
    except Exception:
        pass   # jurnal xətası girişi pozmasın


def log_visit():
    try:
        ip = client_ip()
        if not rate_hit("visit", ip, 1, 1800):   # eyni IP yarım saatda bir dəfə sayılır
            return
        dbq("INSERT INTO visits(ts,ip,ua) VALUES(?,?,?)",
            (time.time(), ip, (request.headers.get("User-Agent") or "")[:200]))
    except Exception:
        pass


def purge_logs():
    now = time.time()
    if now - LAST_LOG_PURGE[0] < 3600:
        return
    LAST_LOG_PURGE[0] = now
    cut = now - LOG_KEEP_DAYS * 86400
    dbq("DELETE FROM logins WHERE ts<?", (cut,))
    dbq("DELETE FROM visits WHERE ts<?", (cut,))


def _admin_pw():
    return os.environ.get("ADMIN_PASSWORD", "")


def _admin_sign(exp):
    key = hashlib.sha256(("admin-session|" + _admin_pw()).encode()).digest()
    return hmac.new(key, str(exp).encode(), hashlib.sha256).hexdigest()


def admin_ok():
    if not _admin_pw():
        return False
    c = request.cookies.get("adm", "")
    try:
        exp, sig = c.split(".", 1)
        return int(exp) > time.time() and hmac.compare_digest(sig, _admin_sign(int(exp)))
    except Exception:
        return False


def admin_guard():
    if not admin_ok():
        abort(401)


def _nostore(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/admin/panel")
def admin_page():
    if not admin_ok():
        abort(404)   # giriş olmadan heç nə göstərmir; /admin ünvanı yoxdur
    resp = send_file(os.path.join(HERE, "admin.html"))
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@app.route("/api/admin/check")
def admin_check():
    return _nostore(jsonify(authed=admin_ok()))


@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    pw = _admin_pw()
    if not pw:
        abort(404)   # ADMIN_PASSWORD təyin olunmayıb
    ip = client_ip()
    if not rate_hit("admfail", ip, 8, 600):
        abort(429)
    given = str((request.get_json(silent=True) or {}).get("password") or "")[:200]
    if not hmac.compare_digest(hashlib.sha256(given.encode()).digest(), hashlib.sha256(pw.encode()).digest()):
        log_login("admin", "", False)
        abort(401)
    exp = int(time.time()) + ADMIN_TTL
    resp = jsonify(ok=True)
    secure = request.is_secure or request.headers.get("X-Forwarded-Proto", "") == "https"
    resp.set_cookie("adm", "%d.%s" % (exp, _admin_sign(exp)), max_age=ADMIN_TTL, httponly=True,
                    samesite="Strict", secure=secure, path="/")
    log_login("admin", "", True)
    return _nostore(resp)


@app.route("/api/admin/logout", methods=["POST"])
def admin_logout():
    resp = jsonify(ok=True)
    resp.delete_cookie("adm", path="/")
    return _nostore(resp)


def _lim(default=200):
    try:
        return max(1, min(int(request.args.get("limit", default)), 500))
    except ValueError:
        return default


@app.route("/api/admin/overview")
def admin_overview():
    admin_guard()
    now = time.time()
    rooms = people = 0
    with CV:
        for r in ROOMS.values():
            n = sum(1 for ts in r["peers"].values() if now - ts < PEER_TTL)
            if n:
                rooms += 1
                people += n
    day = now - 86400
    one = lambda q, a=(): dbq(q, a, one=True)[0]
    return _nostore(jsonify(
        people_live=people, rooms_live=rooms,
        users_total=one("SELECT COUNT(*) FROM users"),
        visits_24h=one("SELECT COUNT(*) FROM visits WHERE ts>?", (day,)),
        logins_24h=one("SELECT COUNT(*) FROM logins WHERE ts>? AND ok=1 AND kind<>'admin'", (day,)),
        fails_24h=one("SELECT COUNT(*) FROM logins WHERE ts>? AND ok=0 AND kind<>'admin'", (day,))))


@app.route("/api/admin/users")
def admin_users():
    admin_guard()
    rows = dbq("SELECT u.sub, u.name, u.username, u.created, "
               "(SELECT email FROM creds c WHERE c.sub=u.sub ORDER BY c.verified DESC, c.rowid LIMIT 1) AS email, "
               "(SELECT MAX(c.verified) FROM creds c WHERE c.sub=u.sub) AS verified "
               "FROM users u ORDER BY u.created DESC LIMIT ?", (_lim(),), many=True)
    out = []
    for r in rows:
        last = None
        if r["email"]:
            last = dbq("SELECT ts, ip FROM logins WHERE email=? AND ok=1 AND kind<>'admin' ORDER BY ts DESC LIMIT 1",
                       (r["email"],), one=True)
        out.append(dict(name=r["name"], username=r["username"], email=r["email"] or "",
                        verified=bool(r["verified"]), created=r["created"],
                        last_login=last["ts"] if last else None, last_ip=last["ip"] if last else None))
    return _nostore(jsonify(users=out))


@app.route("/api/admin/logins")
def admin_logins():
    admin_guard()
    rows = dbq("SELECT ts, kind, email, ip, ok FROM logins ORDER BY ts DESC LIMIT ?", (_lim(),), many=True)
    return _nostore(jsonify(logins=[dict(ts=r["ts"], kind=r["kind"], email=r["email"], ip=r["ip"], ok=bool(r["ok"]))
                                    for r in rows]))


@app.route("/api/admin/visits")
def admin_visits():
    admin_guard()
    rows = dbq("SELECT ts, ip, ua FROM visits ORDER BY ts DESC LIMIT ?", (_lim(),), many=True)
    return _nostore(jsonify(visits=[dict(ts=r["ts"], ip=r["ip"], ua=r["ua"]) for r in rows]))


@app.route("/")
def index():
    log_visit()
    resp = send_file(os.path.join(HERE, "index.html"))
    resp.headers["Cache-Control"] = "no-cache"
    return resp



def fetch_safe(url, headers, hops=5):
    """Yönləndirmələri əl ilə izləyir: hər addım əvvəlcədən yoxlanır (SSRF qorunması)."""
    for _ in range(hops + 1):
        if not safe(url):
            abort(400)
        r = requests.get(url, headers=headers, stream=True, timeout=20, allow_redirects=False)
        if r.is_redirect and r.headers.get("Location"):
            url = urljoin(url, r.headers["Location"])
            r.close()
            continue
        return r
    abort(502)


def pick_encoding(raw, ct):
    m = re.search(r"charset=[\"']?([\w.\-:]+)", ct, re.I)
    if m:
        return m.group(1)
    m = re.search(rb"<meta[^>]+charset=[\"']?([\w.\-:]+)", raw[:4096], re.I)
    if m:
        return m.group(1).decode("ascii", "ignore")
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    if _chardet:
        try:
            return _chardet.detect(raw[:50000]).get("encoding") or "utf-8"
        except Exception:
            pass
    return "utf-8"


@app.route("/p")
def proxy():
    url = request.args.get("u", "").strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url

    h = {
        "User-Agent": UA,
        "Accept": request.headers.get("Accept", "*/*"),
        "Accept-Language": "az,en;q=0.8,ru;q=0.6",
    }
    if request.headers.get("Range"):
        h["Range"] = request.headers["Range"]

    try:
        r = fetch_safe(url, h)
    except HTTPException:
        raise
    except Exception as e:
        # text/plain: xəta mətnində URL ola bilər, HTML kimi göstərilsə XSS olardı
        return Response("Açıla bilmədi: " + str(e), 502, mimetype="text/plain")

    ct = r.headers.get("Content-Type", "")
    if "text/html" in ct.lower():
        raw = b""
        try:
            for chunk in r.iter_content(65536):
                raw += chunk
                if len(raw) > MAX_HTML:
                    break
        except Exception as e:
            return Response("Açıla bilmədi: " + str(e), 502, mimetype="text/plain")
        finally:
            r.close()
        enc = pick_encoding(raw, ct)
        try:
            page = raw.decode(enc or "utf-8", "replace")
        except LookupError:
            page = raw.decode("utf-8", "replace")
        page = re.sub(r'<meta[^>]+http-equiv=["\']?content-security-policy[^>]*>', "", page, flags=re.I)
        page = re.sub(r'<meta[^>]+http-equiv=["\']?refresh[^>]*>', "", page, flags=re.I)
        page = re.sub(r"<meta[^>]+charset[^>]*>", "", page, flags=re.I)
        tag = ('<meta charset="utf-8"><base href="' + htmllib.escape(r.url, quote=True) + '">'
               '<meta name="referrer" content="no-referrer">'
               '<script>window.__UP=' + str(int(r.status_code)) + ';</script><script>' + INJECT + "</script>")
        m = re.search(r"<head(\s[^>]*)?>", page, re.I)
        page = page[:m.end()] + tag + page[m.end():] if m else tag + page
        return Response(page.encode("utf-8"), content_type="text/html; charset=utf-8")

    keep = ("content-type", "content-range", "accept-ranges")
    hdrs = {k: v for k, v in r.headers.items() if k.lower() in keep}
    if not r.headers.get("Content-Encoding") and r.headers.get("Content-Length"):
        hdrs["Content-Length"] = r.headers["Content-Length"]
    resp = Response(r.iter_content(65536), status=r.status_code, headers=hdrs)
    resp.call_on_close(r.close)  # bağlantı sızmasın
    return resp


# ---------------------------------------------------------------
#  Domino otağı: 2 nəfərlik onlayn oyun (standart 28 daş, 0-6).
#  Yaddaşda saxlanır, long-poll ilə yenilənir (otaq sistemi ilə eyni CV).
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
    CV.notify_all()


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
    with CV:
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
        DOMINO[c] = g
        return jsonify(_dom_view(g, sub))


@app.route("/api/domino/join", methods=["POST"])
def api_domino_join():
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    if not code:
        abort(400)
    nm = _dom_names(tok)
    now = time.time()
    with CV:
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
    with CV:
        g = _dom_get(code, sub)
        now = time.time()
        if now - g["seen"].get(sub, 0) > 30:     # qayıdıb: rəqib "onlayn" görsün
            g["seen"][sub] = now
            _dom_touch(g)
        g["seen"][sub] = now
        while g["v"] == v and (vk is None or g.get("vk", 0) == vk):
            left = deadline - time.time()
            if left <= 0:
                break
            CV.wait(timeout=left)
            g = DOMINO.get(code)
            if not g:
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
    with CV:
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
    with CV:
        g = _dom_get(code, sub)
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
    with CV:
        g = _dom_get(code, sub)
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
    with CV:
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
    with CV:
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


# ---------------------------------------------------------------
#  --- Durak mühərriki (durak_engine.py ilə eyni) ---
"""Durak (Dürak) oyun mühərriki. Kompüter (bot) hərəkətləri du_bot_pick ilə seçilir.

Qaydalar:
  * 2–4 oyunçu, 36 kartlıq dəst, hər kəsə 6 kart.
  * Açılan son kart kozır (trump) rəngini göstərir.
  * İlk hücum edən: ən kiçik kozırı olan (yoxdursa təsadüfi).
  * Hücum edən kart atır; digər hücumçular eyni rəqəmli (rank) kartları əlavə edə bilər.
  * Müdafiəçi hər kartı daha yüksək eyni rəngli və ya kozır kartla bağlayır.
  * perevod=True: müdafiəçi, hələ heç bir kart bağlamadan, eyni rəqəmli kartla hücumu
    növbəti oyunçuya ötürə bilər.
  * Müdafiəçi götürə bilər: bütün masadakı kartlar ona gedir, hücum davam edir, növbə
    ondan sonrakıya keçir.
  * Bağlanmış hər şey atılır; oyunçular 6 karta qədər çəkir (əvvəl hücumçu, sonda müdafiəçi).
  * Kartı bitən oyunçu çıxır. Son kartı qalan oyunçu "durak" (axmaq) olur.

Ev qaydası: bir raundda hücum kartlarının sayı müdafiəçinin raund başında kart sayından
və 6-dan çox ola bilməz.
"""

SUITS = "SHDC"
RANKS = tuple(range(6, 15))  # 6..10, J=11, Q=12, K=13, A=14
HAND = 6
MAX_BOUT = 6


class GameError(Exception):
    pass


def _beats(d, a, trump):
    """d kartı a kartını bağlayırmı?"""
    if d[1] == a[1]:
        return d[0] > a[0]
    return d[1] == trump and a[1] != trump


class Durak:
    def __init__(self, players, perevod=True, rng=None):
        if not 2 <= len(players) <= 4 or len(set(players)) != len(players):
            raise ValueError("2–4 fərqli oyunçu lazımdır")
        self.players = list(players)
        self.perevod = bool(perevod)
        self.rng = rng or random.Random()

        deck = [(r, s) for s in SUITS for r in RANKS]
        self.rng.shuffle(deck)
        self.deck = deck              # kartlar sondan çəkilir
        self.trump = deck[0][1]       # ən alt kart kozırı göstərir (deck[0] dəstdə qalır)
        self.trump_card = deck[0]     # hamı görür: onu çəkən oyunçunun əlində olduğu bilinir

        self.hands = {p: [] for p in self.players}
        self.discard = []
        self.out = []                 # çıxan oyunçular, çıxma sırası ilə
        self.table = []               # {"a": hücum kartı, "d": bağlayan kart və ya None}
        self.over = False
        self.loser = None             # durak; None = heç kim (bərabərlik)
        self.known = {p: set() for p in self.players}   # oyunçunun əlində olduğu AÇIQ bilinən kartlar (bot üçün)

        for _ in range(HAND):
            for p in self.players:
                self.hands[p].append(self.deck.pop())

        low = [(min(c[0] for c in self.hands[p] if c[1] == self.trump), p)
               for p in self.players if any(c[1] == self.trump for c in self.hands[p])]
        first = min(low)[1] if low else self.rng.choice(self.players)
        self._start_round(first)

    # ------------------------------------------------------------------ yardımçı
    def _seat_from(self, p):
        i = self.players.index(p)
        seq = self.players[i:] + self.players[:i]
        return [q for q in seq if q not in self.out]

    def _next_active(self, p):
        i = self.players.index(p)
        n = len(self.players)
        for k in range(1, n + 1):
            q = self.players[(i + k) % n]
            if q not in self.out:
                return q
        return None

    def _adders(self):
        return [p for p in self._seat_from(self.attacker)
                if p != self.defender and self.hands[p]]

    def _can_add(self, p):
        return self.bout_n < self.cap and any(c[0] in self.bout_ranks for c in self.hands[p])

    def _guard(self):
        if self.over:
            raise GameError("oyun bitib")

    def _start_round(self, att):
        self.attacker = att
        self.defender = self._next_active(att)
        self.started = False
        self.took = False
        self.passed = set()
        self.bout_ranks = set()
        self.bout_n = 0
        self.cap = min(MAX_BOUT, len(self.hands[self.defender]))

    # ------------------------------------------------------------------ hərəkətlər
    def attack(self, pid, card):
        self._guard()
        card = tuple(card)
        if pid not in self.players or pid in self.out:
            raise GameError("oyunçu deyil")
        if not self.started:
            if pid != self.attacker:
                raise GameError("hücum sənin növbən deyil")
        else:
            if pid == self.defender:
                raise GameError("müdafiəçi hücum edə bilməz")
            if card[0] not in self.bout_ranks:
                raise GameError("bu rəqəm masada yoxdur")
            if self.bout_n >= self.cap:
                raise GameError("hücum limiti dolub")
        if card not in self.hands[pid]:
            raise GameError("bu kart əlində yoxdur")
        self.hands[pid].remove(card)
        self.known[pid].discard(card)
        self.started = True
        self.bout_ranks.add(card[0])
        self.bout_n += 1
        if self.took:
            self.hands[self.defender].append(card)
            self.known[self.defender].add(card)
        else:
            self.table.append({"a": card, "d": None})
        self.passed = set()
        self._settle()

    def defend(self, pid, attack, card):
        self._guard()
        attack, card = tuple(attack), tuple(card)
        if pid != self.defender or self.took:
            raise GameError("müdafiə sənin növbən deyil")
        pair = next((p for p in self.table if p["a"] == attack and p["d"] is None), None)
        if pair is None:
            raise GameError("bu kart artıq bağlanıb və ya masada yoxdur")
        if card not in self.hands[pid]:
            raise GameError("bu kart əlində yoxdur")
        if not _beats(card, attack, self.trump):
            raise GameError("bu kart onu bağlamır")
        self.hands[pid].remove(card)
        self.known[pid].discard(card)
        pair["d"] = card
        self._settle()

    def transfer(self, pid, card):
        self._guard()
        card = tuple(card)
        if not self.perevod:
            raise GameError("perevod qaydası söndürülüb")
        if pid != self.defender or self.took or not self.table:
            raise GameError("perevod etmək olmaz")
        if any(p["d"] is not None for p in self.table):
            raise GameError("perevod yalnız heç nə bağlanmamışkən olar")
        if {p["a"][0] for p in self.table} != {card[0]}:
            raise GameError("perevod üçün eyni rəqəmli kart lazımdır")
        if card not in self.hands[pid]:
            raise GameError("bu kart əlində yoxdur")
        nd = self._next_active(pid)
        if len(self.hands[nd]) < self.bout_n + 1 or self.bout_n + 1 > MAX_BOUT:
            raise GameError("yeni müdafiəçinin kartı çatmır")
        self.hands[pid].remove(card)
        self.known[pid].discard(card)
        self.table.append({"a": card, "d": None})
        self.bout_n += 1
        self.defender = nd
        self.cap = min(MAX_BOUT, len(self.hands[nd]))
        self.passed = set()
        self._settle()

    def take(self, pid):
        self._guard()
        if pid != self.defender or self.took or not any(p["d"] is None for p in self.table):
            raise GameError("götürmək olmaz")
        for p in self.table:
            self.hands[pid].append(p["a"])
            self.known[pid].add(p["a"])
            if p["d"]:
                self.hands[pid].append(p["d"])
                self.known[pid].add(p["d"])
        self.table = []
        self.took = True
        self.passed = set()
        self._settle()

    def pass_turn(self, pid):
        self._guard()
        if not self.started or any(p["d"] is None for p in self.table):
            raise GameError("pas keçmək olmaz")
        if pid not in self._adders():
            raise GameError("pas keçə bilməzsən")
        self.passed.add(pid)
        self._settle()

    def apply(self, pid, action):
        kind = action[0]
        if kind == "attack":
            return self.attack(pid, action[1])
        if kind == "defend":
            return self.defend(pid, action[1], action[2])
        if kind == "transfer":
            return self.transfer(pid, action[1])
        if kind == "take":
            return self.take(pid)
        if kind == "pass":
            return self.pass_turn(pid)
        raise GameError("naməlum hərəkət")

    # ------------------------------------------------------------------ dövrü bağlamaq
    def _settle(self):
        if self.over or not self.started:
            return
        if any(p["d"] is None for p in self.table):
            return                                   # müdafiəçi hələ cavab verməlidir
        if not self.took and not self.hands[self.defender]:
            return self._end_round()                 # müdafiəçi hamısını bağladı, kartı bitdi
        if self.took:
            if self.bout_n >= self.cap:
                return self._end_round()
            adders = [p for p in self._adders() if self._can_add(p)]
        else:
            adders = self._adders()                  # bitaya getmək üçün hücumçular təsdiqləməlidir (Bita düyməsi)
        if not adders or all(p in self.passed for p in adders):
            self._end_round()

    def _end_round(self):
        for p in self.table:                         # masadakı bütün kartlar atılır
            self.discard.append(p["a"])
            if p["d"]:
                self.discard.append(p["d"])
        self.table = []

        order = [p for p in self._seat_from(self.attacker) if p != self.defender]
        order.append(self.defender)                  # müdafiəçi ən sonda çəkir
        for p in order:
            while len(self.hands[p]) < HAND and self.deck:
                c = self.deck.pop()
                self.hands[p].append(c)
                if c == self.trump_card:
                    self.known[p].add(c)
        for p in self.players:
            if p not in self.out and not self.hands[p] and not self.deck:
                self.out.append(p)

        active = [p for p in self.players if p not in self.out]
        if len(active) <= 1:
            self.over = True
            self.loser = active[0] if active else None
            self.attacker = self.defender = None
            self.started = False
            return
        # uğurlu müdafiədən sonra müdafiəçi hücum edən olur; götürübsə növbə ondan sonrakıya keçir
        if not self.took and self.defender not in self.out:
            self._start_round(self.defender)
        else:
            self._start_round(self._next_active(self.defender))

    # ------------------------------------------------------------------ mümkün hərəkətlər
    def actions(self, pid):
        if self.over or pid not in self.players or pid in self.out:
            return []
        acts = []
        if not self.started:
            if pid == self.attacker:
                acts = [("attack", c) for c in self.hands[pid]]
            return acts

        undef = [p["a"] for p in self.table if p["d"] is None]
        if pid == self.defender:
            if self.took:
                return []
            for a in undef:
                for c in self.hands[pid]:
                    if _beats(c, a, self.trump):
                        acts.append(("defend", a, c))
            if undef:
                acts.append(("take",))
            if self.perevod and self.table and not any(p["d"] for p in self.table):
                ranks = {p["a"][0] for p in self.table}
                if len(ranks) == 1:
                    nd = self._next_active(pid)
                    if len(self.hands[nd]) >= self.bout_n + 1 and self.bout_n + 1 <= MAX_BOUT:
                        acts += [("transfer", c) for c in self.hands[pid] if c[0] in ranks]
            return acts

        if pid in self._adders():
            if self.bout_n < self.cap:
                acts += [("attack", c) for c in self.hands[pid] if c[0] in self.bout_ranks]
            if not undef:
                acts.append(("pass",))
        return acts

    # ------------------------------------------------------------------ görünüş (client üçün)
    def view(self, pid):
        return {
            "trump": self.trump,
            "deck": len(self.deck),
            "discard": len(self.discard),
            "attacker": self.attacker,
            "defender": self.defender,
            "took": self.took,
            "cap": self.cap,
            "table": [{"a": list(p["a"]), "d": list(p["d"]) if p["d"] else None} for p in self.table],
            "hand": [list(c) for c in self.hands.get(pid, [])],
            "players": [{"id": q, "n": len(self.hands[q]), "out": q in self.out} for q in self.players],
            "passed": sorted(self.passed),
            "actions": [[a[0], *[list(x) if isinstance(x, tuple) else x for x in a[1:]]]
                        for a in self.actions(pid)],
            "over": self.over,
            "loser": self.loser,
        }

    # ------------------------------------------------------------------ saxlama / bərpa (server yenilənəndə oyun qalsın)
    def to_dict(self):
        t = lambda c: list(c)
        return {
            "players": list(self.players), "perevod": self.perevod,
            "deck": [t(c) for c in self.deck], "trump": self.trump, "trump_card": t(self.trump_card),
            "hands": {p: [t(c) for c in h] for p, h in self.hands.items()},
            "discard": [t(c) for c in self.discard], "out": list(self.out),
            "table": [{"a": t(x["a"]), "d": t(x["d"]) if x["d"] else None} for x in self.table],
            "over": self.over, "loser": self.loser,
            "known": {p: [t(c) for c in sorted(s)] for p, s in self.known.items()},
            "attacker": self.attacker, "defender": self.defender, "started": self.started, "took": self.took,
            "passed": sorted(self.passed), "bout_ranks": sorted(self.bout_ranks), "bout_n": self.bout_n, "cap": self.cap,
        }

    @classmethod
    def from_dict(cls, d):
        e = cls.__new__(cls)
        c = lambda x: (int(x[0]), str(x[1]))
        e.players = list(d["players"])
        e.perevod = bool(d["perevod"])
        e.rng = random.Random()
        e.deck = [c(x) for x in d["deck"]]
        e.trump = d["trump"]
        e.trump_card = c(d["trump_card"])
        e.hands = {p: [c(x) for x in h] for p, h in d["hands"].items()}
        e.discard = [c(x) for x in d["discard"]]
        e.out = list(d["out"])
        e.table = [{"a": c(x["a"]), "d": c(x["d"]) if x["d"] else None} for x in d["table"]]
        e.over = bool(d["over"])
        e.loser = d["loser"]
        e.known = {p: {c(x) for x in s} for p, s in d.get("known", {}).items()}
        for p in e.players:
            e.known.setdefault(p, set())
        e.attacker = d["attacker"]
        e.defender = d["defender"]
        e.started = bool(d["started"])
        e.took = bool(d["took"])
        e.passed = set(d["passed"])
        e.bout_ranks = set(d["bout_ranks"])
        e.bout_n = int(d["bout_n"])
        e.cap = int(d["cap"])
        return e

    def card_count(self):
        """Test üçün: bütün kartların sayı (36 olmalıdır)."""
        table = sum(1 + (1 if p["d"] else 0) for p in self.table)
        return sum(len(h) for h in self.hands.values()) + len(self.deck) + len(self.discard) + table


# ------------------------------------------------------------------ kompüter (bot)
# Qiymətlər: kartı xərcləməyin "xərci". Kozır baha, böyük kart baha.
BOT = {
    "trump_base": 7.0,    # ən kiçik kozırın xərci
    "trump_step": 1.2,    # kozır hər rəqəm yuxarı çıxdıqca
    "plain_step": 0.7,    # adi kart hər rəqəm yuxarı çıxdıqca
    "liab": 5.0,          # əlimdə artıq hər kartın "yükü" (götürəndə)
    "take_base": 2.0,     # götürəndə təşəbbüsü itirmək
    "use_trump": 0.55,    # götürülən kozırın faydası (xərcinin hissəsi)
    "use_plain": 0.15,    # götürülən adi kartın faydası
    "extra": 0.8,         # götürəndə əlavə atılacaq kartların gözlənilən sayı (dəstə varkən)
    "k_defend": 2.0,      # bağlama xərci <= götürmə xərci * k_defend olsa bağla
    "tr_off": -20.0,       # ötürmənin əlavə xərci (mənfi = ötürməyə meyl)
    "tr_trump": 1.0,      # 1 olsa kozırla da ötürə bilər (xərc hesabı ilə)
    "add_lim": 14,        # müdafiəçi bağlayarkən bu rəqəmdən yüksək adi kartı əlavə etmir (14 = məhdudiyyət yoxdur)
    "add_lim_end": 14,    # dəstə bitəndə
    "tr_max": 99.0,       # ötürmə kartının xərci bundan çox olmasın
    "lead_pair": 5.0,     # eyni rəqəmli kart sayına verilən üstünlük
}


def _cw(c, trump):
    if c[1] == trump:
        return BOT["trump_base"] + (c[0] - 6) * BOT["trump_step"]
    return (c[0] - 5) * BOT["plain_step"]


def _best_cover(hand, attacks, trump):
    """Bütün attacks kartlarını ən ucuz yolla bağlayan [(hücum, kart)] və xərc; mümkün deyilsə None."""
    atk = sorted(attacks, key=lambda a: (a[1] == trump, -a[0]))
    opts = []
    for a in atk:
        o = sorted((c for c in hand if _beats(c, a, trump)), key=lambda c: _cw(c, trump))
        if not o:
            return None
        opts.append(o)
    best = [None, 1e9]
    nodes = [0]

    def rec(i, used, cost, plan):
        if cost >= best[1] or nodes[0] > 6000:
            return
        nodes[0] += 1
        if i == len(atk):
            best[0], best[1] = list(plan), cost
            return
        for c in opts[i]:
            if c in used:
                continue
            used.add(c)
            plan.append((atk[i], c))
            rec(i + 1, used, cost + _cw(c, trump), plan)
            plan.pop()
            used.discard(c)

    rec(0, set(), 0.0, [])
    return (best[0], best[1]) if best[0] else None


def _take_cost(eng):
    trump = eng.trump
    tot = BOT["take_base"]
    for pr in eng.table:
        for c in (pr["a"], pr["d"]):
            if c is None:
                continue
            use = (BOT["use_trump"] if c[1] == trump else BOT["use_plain"]) * _cw(c, trump)
            tot += BOT["liab"] - use
    if eng.deck:
        tot += BOT["extra"] * BOT["liab"]
    else:
        tot *= 2.0                              # dəstə bitib: əlavə kart ölümcül ola bilər
    return tot


def _unseen(eng, p):
    seen = set(eng.hands[p]) | set(eng.discard)
    for pr in eng.table:
        seen.add(pr["a"])
        if pr["d"]:
            seen.add(pr["d"])
    return {(r, s) for s in SUITS for r in RANKS} - seen


def _opp_hand(eng, p):
    """2 nəfər qalıb və dəstə bitibsə rəqibin əli dəqiq bəllidir; yoxsa yalnız açıq bilinən kartlar."""
    act = [q for q in eng.players if q not in eng.out and q != p]
    if len(act) == 1 and not eng.deck:
        return _unseen(eng, p)
    if len(act) == 1:
        return set(eng.known.get(act[0], ()))
    return None


def _bot_lead(eng, p):
    """İlk hücum: ucuz, rəqibin bağlaya bilməyəcəyi, eyni rəqəmli çox olan kart."""
    hand = eng.hands[p]
    trump = eng.trump
    cnt = {}
    for c in hand:
        cnt[c[0]] = cnt.get(c[0], 0) + 1
    d = eng.defender
    known = eng.known.get(d, set())
    exact = _opp_hand(eng, p) if len([q for q in eng.players if q not in eng.out]) == 2 else None
    foe = exact if exact is not None else known

    def score(c):
        s = _cw(c, trump)
        if c[1] == trump and eng.deck:
            s += 30
        s -= BOT['lead_pair'] * (cnt[c[0]] - 1)
        if foe:
            if not any(_beats(k, c, trump) for k in foe):
                s -= 2.0 if exact is None else 3.5
            if eng.perevod and any(k[0] == c[0] for k in foe):
                s += 2.5
        return s

    return ("attack", min(hand, key=score))


def _bot_defend(eng, p, acts):
    trump = eng.trump
    hand = list(eng.hands[p])
    undef = [x["a"] for x in eng.table if x["d"] is None]
    spent = sum(_cw(x["d"], trump) for x in eng.table if x["d"])
    take_c = _take_cost(eng)

    cover = _best_cover(hand, undef, trump)
    def_c = (cover[1] + spent) if cover else None

    # perevod: ən ucuz ötürmə kartı
    tr = [a for a in acts if a[0] == "transfer"]
    tr_c = None
    tr_best = None
    if tr:
        pool = tr if BOT["tr_trump"] else [a for a in tr if a[1][1] != trump]
        pool = [a for a in pool if _cw(a[1], trump) <= BOT["tr_max"]]
        if pool:
            tr_best = min(pool, key=lambda a: _cw(a[1], trump))
            tr_c = _cw(tr_best[1], trump) + BOT["tr_off"]

    # seçimlər: (xərc, hərəkət)
    options = [(take_c, ("take",))]
    if cover is not None:
        k = BOT["k_defend"]
        if not eng.deck:
            k = 99.0                               # dəstə bitib: bağlaya bilirsə həmişə bağlasın
        options.append((def_c / max(k, 0.01), ("defend", cover[0][0][0], cover[0][0][1])))
    if tr_best is not None:
        options.append((tr_c, tr_best))
    return min(options, key=lambda o: o[0])[1]


def _bot_add(eng, p, acts):
    cs = [a[1] for a in acts if a[0] == "attack"]
    if not cs:
        return None
    trump = eng.trump
    d = eng.defender
    plain = [c for c in cs if c[1] != trump]
    if eng.took:
        # müdafiəçi götürür: yüngülləşmək üçün hər şeyi at (dəstə bitibsə kozırı da)
        pool = cs if not eng.deck else plain
        return ("attack", min(pool, key=lambda c: _cw(c, trump))) if pool else None
    # müdafiəçi hələ bağlayır: yalnız ucuz, adi kart əlavə et
    lim = BOT['add_lim'] if eng.deck else BOT['add_lim_end']
    pool = [c for c in plain if c[0] <= lim]
    foe = eng.known.get(d, set())
    if foe:
        pool += [c for c in plain if c[0] > lim and not any(_beats(k, c, trump) for k in foe)]
    if not pool:
        return None
    return ("attack", min(pool, key=lambda c: _cw(c, trump)))


def du_bot_pick(eng, bots):
    """Botlardan biri üçün bir hərəkət seç: (oyunçu, hərəkət) və ya None."""
    if eng.over:
        return None
    undef = any(x["d"] is None for x in eng.table)
    d = eng.defender
    if d in bots and not eng.took and undef:
        acts = eng.actions(d)
        if acts:
            return d, _bot_defend(eng, d, acts)
    if not eng.started:
        if eng.attacker in bots:
            return eng.attacker, _bot_lead(eng, eng.attacker)
        return None
    if undef and not eng.took:
        return None                           # müdafiəçi cavab verməyib
    for p in eng._seat_from(eng.attacker):
        if p in bots and p != d and p not in eng.passed:
            acts = eng.actions(p)
            if ("pass",) not in acts:
                continue
            return p, (_bot_add(eng, p, acts) or ("pass",))
    return None


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


# ---------------------------------------------------------------
#  Şahmat otağı: 2 nəfərlik onlayn oyun. Bütün qaydalar serverdə yoxlanılır
#  (rok, en passant, piyada yüksəlişi, şah/mat/pat, təkrar, 50 gediş).
# ---------------------------------------------------------------
CHESS = {}
CH_MAX = 200
_CN = [(-2, -1), (-2, 1), (-1, -2), (-1, 2), (1, -2), (1, 2), (2, -1), (2, 1)]
_CK = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
_CR = [(-1, 0), (1, 0), (0, -1), (0, 1)]
_CB = [(-1, -1), (-1, 1), (1, -1), (1, 1)]


def _ch_start():
    back = "RNBQKBNR"
    b = [None] * 64
    for c in range(8):
        b[c] = "b" + back[c]
        b[8 + c] = "bP"
        b[48 + c] = "wP"
        b[56 + c] = "w" + back[c]
    return b


def _ch_att(b, sq, by):
    r, c = divmod(sq, 8)
    pr = r + (1 if by == "w" else -1)
    for dc in (-1, 1):
        if 0 <= pr < 8 and 0 <= c + dc < 8 and b[pr * 8 + c + dc] == by + "P":
            return True
    for pc, offs in (("N", _CN), ("K", _CK)):
        for dr, dc in offs:
            rr, cc = r + dr, c + dc
            if 0 <= rr < 8 and 0 <= cc < 8 and b[rr * 8 + cc] == by + pc:
                return True
    for dirs, pcs in ((_CR, "RQ"), (_CB, "BQ")):
        for dr, dc in dirs:
            rr, cc = r + dr, c + dc
            while 0 <= rr < 8 and 0 <= cc < 8:
                p = b[rr * 8 + cc]
                if p:
                    if p[0] == by and p[1] in pcs:
                        return True
                    break
                rr += dr
                cc += dc
    return False


def _ch_pseudo(g):
    b, me = g["b"], g["turn"]
    op = "b" if me == "w" else "w"
    out = []
    for sq in range(64):
        p = b[sq]
        if not p or p[0] != me:
            continue
        r, c = divmod(sq, 8)
        k = p[1]
        if k == "P":
            d = -1 if me == "w" else 1
            start = 6 if me == "w" else 1
            rr = r + d
            if 0 <= rr < 8:
                if not b[rr * 8 + c]:
                    out.append((sq, rr * 8 + c))
                    if r == start and not b[(r + 2 * d) * 8 + c]:
                        out.append((sq, (r + 2 * d) * 8 + c))
                for dc in (-1, 1):
                    cc = c + dc
                    if 0 <= cc < 8:
                        t = rr * 8 + cc
                        if (b[t] and b[t][0] == op) or (t == g["ep"] and not b[t]):
                            out.append((sq, t))
        elif k in "NK":
            for dr, dc in (_CN if k == "N" else _CK):
                rr, cc = r + dr, c + dc
                if 0 <= rr < 8 and 0 <= cc < 8:
                    t = rr * 8 + cc
                    if not b[t] or b[t][0] == op:
                        out.append((sq, t))
            home = 60 if me == "w" else 4
            if k == "K" and sq == home and not _ch_att(b, sq, op):
                ks, qs = ("K", "Q") if me == "w" else ("k", "q")
                cr = g["cr"]
                if ks in cr and not b[sq + 1] and not b[sq + 2] and b[sq + 3] == me + "R" \
                        and not _ch_att(b, sq + 1, op) and not _ch_att(b, sq + 2, op):
                    out.append((sq, sq + 2))
                if qs in cr and not b[sq - 1] and not b[sq - 2] and not b[sq - 3] and b[sq - 4] == me + "R" \
                        and not _ch_att(b, sq - 1, op) and not _ch_att(b, sq - 2, op):
                    out.append((sq, sq - 2))
        else:
            dirs = _CR if k == "R" else (_CB if k == "B" else _CR + _CB)
            for dr, dc in dirs:
                rr, cc = r + dr, c + dc
                while 0 <= rr < 8 and 0 <= cc < 8:
                    t = rr * 8 + cc
                    if not b[t]:
                        out.append((sq, t))
                    else:
                        if b[t][0] == op:
                            out.append((sq, t))
                        break
                    rr += dr
                    cc += dc
    return out


def _ch_do(b, f, t, promo, ep):
    b = b[:]
    p = b[f]
    if p[1] == "P" and t == ep and f % 8 != t % 8 and not b[t]:
        b[(f // 8) * 8 + t % 8] = None
    if p[1] == "K" and abs(t - f) == 2:
        if t > f:
            b[f + 1], b[f + 3] = b[f + 3], None
        else:
            b[f - 1], b[f - 4] = b[f - 4], None
    b[t] = p
    b[f] = None
    if p[1] == "P" and t // 8 in (0, 7):
        b[t] = p[0] + (promo if promo in ("Q", "R", "B", "N") else "Q")
    return b


def _ch_legal(g):
    me = g["turn"]
    op = "b" if me == "w" else "w"
    res = []
    for f, t in _ch_pseudo(g):
        nb = _ch_do(g["b"], f, t, "Q", g["ep"])
        if not _ch_att(nb, nb.index(me + "K"), op):
            res.append((f, t))
    return res


def _ch_sub(g, col):
    return next((s for s, c in g["col"].items() if c == col), None)


def _ch_end(g, win, reason):
    g["status"] = "over"
    g["reason"] = reason
    g["win"] = win
    sc = g["score"]
    if win:
        sc[win] = sc.get(win, 0) + 1
    else:
        for p in g["players"]:
            sc[p] = sc.get(p, 0) + 0.5


def _ch_play(g, f, t, promo):
    b, me = g["b"], g["turn"]
    p = b[f]
    cap = b[t] is not None or (p[1] == "P" and t == g["ep"] and f % 8 != t % 8)
    nb = _ch_do(b, f, t, promo, g["ep"])
    cr = g["cr"]
    for sq, fl in ((60, "KQ"), (63, "K"), (56, "Q"), (4, "kq"), (7, "k"), (0, "q")):
        if f == sq or t == sq:
            for ch in fl:
                cr = cr.replace(ch, "")
    g["cr"] = cr
    g["ep"] = (f + t) // 2 if p[1] == "P" and abs(t - f) == 16 else None
    g["hm"] = 0 if (p[1] == "P" or cap) else g["hm"] + 1
    g["b"] = nb
    g["last"] = [f, t]
    g["turn"] = nxt = "b" if me == "w" else "w"
    key = (tuple(nb), nxt, cr, g["ep"])
    g["rep"][key] = g["rep"].get(key, 0) + 1
    chk = _ch_att(nb, nb.index(nxt + "K"), me)
    g["check"] = chk
    if not _ch_legal(g):
        if chk:
            _ch_end(g, _ch_sub(g, me), "mate")
        else:
            _ch_end(g, None, "stalemate")
        return
    pcs = [x for x in nb if x and x[1] != "K"]
    if g["hm"] >= 100:
        _ch_end(g, None, "fifty")
    elif g["rep"][key] >= 3:
        _ch_end(g, None, "repetition")
    elif not pcs or (len(pcs) == 1 and pcs[0][1] in "NB"):
        _ch_end(g, None, "material")


def _ch_reset(g, swap=False):
    a, b = g["players"][0], g["players"][1]
    if swap:
        g["col"] = {a: g["col"][b], b: g["col"][a]}
    else:
        x = secrets.choice("wb")
        g["col"] = {a: x, b: "b" if x == "w" else "w"}
    g.update(b=_ch_start(), turn="w", cr="KQkq", ep=None, hm=0, rep={}, last=None,
             check=False, win=None, reason="", status="play")


def _ch_cleanup(now):
    for c in [c for c, g in CHESS.items() if now - g["upd"] > DOM_IDLE]:
        CHESS.pop(c, None)


def _ch_get(code, sub):
    g = CHESS.get(code)
    if not g or sub not in g["players"]:
        abort(404)
    return g


def _ch_view(g, sub):
    opp = _dom_other(g, sub)
    now = time.time()
    st = g["status"]
    col = g["col"].get(sub)
    mine = st == "play" and g["turn"] == col
    out = dict(
        v=g["v"], code=g["code"], status=st, round=g["round"], color=col,
        b=g["b"] if st != "wait" else [], check=g["check"] if st != "wait" else False,
        moves=[list(m) for m in _ch_legal(g)] if mine else [],
        turn=(("me" if mine else "opp") if st == "play" else None),
        me=g["names"].get(sub), opp=g["names"].get(opp) if opp else None,
        opp_online=bool(opp and (opp == "bot" or now - g["seen"].get(opp, 0) < 35)),
        score=dict(me=g["score"].get(sub, 0), opp=g["score"].get(opp, 0) if opp else 0),
        last=g["last"], reason=g["reason"], bot=bool(g.get("bot")),
    )
    out["vc"], out["vk"] = _gv_view("chess", g)
    if st == "over":
        out["result"] = "draw" if not g["win"] else ("me" if g["win"] == sub else "opp")
    return out


@app.route("/api/chess/create", methods=["POST"])
def api_chess_create():
    sub, tok = auth_sub()
    if not rate_hit("chc", sub, 12, 60):
        abort(429)
    vs_bot = bool((request.get_json(silent=True) or {}).get("bot"))
    nm = _dom_names(tok)
    now = time.time()
    with CV:
        _ch_cleanup(now)
        if len(CHESS) >= CH_MAX:
            abort(503)
        for _ in range(50):
            c = "".join(secrets.choice(CODE_CHARS) for _ in range(5))
            if c not in CHESS:
                break
        else:
            abort(503)
        g = dict(code=c, players=[sub], names={sub: nm}, col={}, b=[], turn="w", cr="", ep=None, hm=0,
                 rep={}, last=None, check=False, win=None, reason="", status="wait", v=1, round=1,
                 score={}, seen={sub: now}, upd=now)
        if vs_bot:
            g["players"].append("bot")
            g["names"]["bot"] = dict(name="Kompüter", username="bot", avatar="", badge=None)
            g["seen"]["bot"] = now
            g["bot"] = True
            _ch_reset(g)
        CHESS[c] = g
        return jsonify(_ch_view(g, sub))


@app.route("/api/chess/join", methods=["POST"])
def api_chess_join():
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    if not code:
        abort(400)
    nm = _dom_names(tok)
    now = time.time()
    with CV:
        g = CHESS.get(code)
        if not g:
            abort(404)
        if sub not in g["players"]:
            if len(g["players"]) >= 2:
                return jsonify(error="full"), 403
            g["players"].append(sub)
            g["names"][sub] = nm
            _ch_reset(g)
            _dom_touch(g)
        else:
            g["names"][sub] = nm
        g["seen"][sub] = now
        return jsonify(_ch_view(g, sub))


@app.route("/api/chess/poll")
def api_chess_poll():
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
        g = _ch_get(code, sub)
        now = time.time()
        if now - g["seen"].get(sub, 0) > 30:
            g["seen"][sub] = now
            _dom_touch(g)
        g["seen"][sub] = now
        while g["v"] == v and (vk is None or g.get("vk", 0) == vk):
            left = deadline - time.time()
            if left <= 0:
                break
            CV.wait(timeout=left)
            g = CHESS.get(code)
            if not g:
                abort(404)
            g["seen"][sub] = time.time()
        resp = jsonify(_ch_view(g, sub))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/chess/check")
def api_chess_check():
    """Saxlanmış şahmat oyunu hələ varmı? Heç nəyi dəyişmir."""
    sub, tok = auth_sub()
    code = clean_room(request.args.get("room"))
    with CV:
        g = CHESS.get(code)
        if not g or sub not in g["players"]:
            abort(404)
        return jsonify(ok=True, status=g["status"])


@app.route("/api/chess/move", methods=["POST"])
def api_chess_move():
    sub, tok = auth_sub()
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    act = str(d.get("action", "move"))
    with CV:
        g = _ch_get(code, sub)
        g["seen"][sub] = time.time()
        if g["status"] != "play":
            return jsonify(_ch_view(g, sub))
        me = g["col"][sub]
        if act == "resign":
            _ch_end(g, _ch_sub(g, "b" if me == "w" else "w"), "resign")
        elif act == "move":
            if g["turn"] != me:
                return jsonify(_ch_view(g, sub))
            try:
                f, t = int(d.get("f")), int(d.get("t"))
            except (TypeError, ValueError):
                abort(400)
            if (f, t) not in _ch_legal(g):
                abort(400)
            _ch_play(g, f, t, str(d.get("promo") or "Q")[:1].upper())
        else:
            abort(400)
        _dom_touch(g)
        return jsonify(_ch_view(g, sub))


@app.route("/api/chess/again", methods=["POST"])
def api_chess_again():
    sub, tok = auth_sub()
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    with CV:
        g = _ch_get(code, sub)
        g["seen"][sub] = time.time()
        try:
            rd = int(d.get("round", 0))
        except (TypeError, ValueError):
            rd = 0
        if g["status"] == "over" and rd == g["round"] and len(g["players"]) == 2:
            g["round"] += 1
            _ch_reset(g, swap=True)
            _dom_touch(g)
        return jsonify(_ch_view(g, sub))


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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), threaded=True)
