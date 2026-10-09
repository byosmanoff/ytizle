import os, re, json, base64, time, secrets, threading, ipaddress, socket, sqlite3, smtplib, hashlib, hmac, html as htmllib
from urllib.parse import urlparse, urljoin, parse_qs
import requests
from werkzeug.exceptions import HTTPException
from email.message import EmailMessage
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, request, Response, send_file, abort, jsonify

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
             "locked": False, "members": set()}
        ROOMS[code] = r
    return r


def admit(r, pid):
    """Üzvü qəbul et. Otaq kilidlidirsə yalnız əvvəl girmiş cihazlar keçə bilər."""
    if pid in r["members"]:
        return
    if r["locked"] or len(r["members"]) >= MAX_MEMBERS:
        abort(403)
    r["members"].add(pid)


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


@app.route("/api/create", methods=["POST"])
def api_create():
    now = time.time()
    with CV:
        cleanup(now)
        code = None
        for _ in range(50):
            c = "".join(secrets.choice(CODE_CHARS) for _ in range(5))
            if c not in ROOMS:
                code = c
                break
        if not code:
            abort(503)
        get_room(code)
        return jsonify(room=code)


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
        CV.notify_all()
        return jsonify(seq=r["seq"], locked=r["locked"], epoch=r["created"])


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
    now = time.time()
    with CV:
        r = get_room(code)
        admit(r, pid)
        r["seq"] += 1
        r["msgs"].append((r["seq"], pid, msg))
        del r["msgs"][:-100]
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
            CV.notify_all()  # qarşı tərəfin long-poll-u dərhal yenilənsin
    return jsonify(ok=True)


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
CREATE TABLE IF NOT EXISTS visits(id INTEGER PRIMARY KEY AUTOINCREMENT, ip TEXT NOT NULL, path TEXT NOT NULL, ua TEXT, ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS visits_ts ON visits(ts);
CREATE TABLE IF NOT EXISTS login_log(id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, ip TEXT NOT NULL, ok INTEGER NOT NULL, kind TEXT NOT NULL, ua TEXT, ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS login_log_ts ON login_log(ts);
CREATE TABLE IF NOT EXISTS admin_sessions(token TEXT PRIMARY KEY, created REAL NOT NULL);
""")
            ucols = [r[1] for r in c.execute("PRAGMA table_info(users)")]
            if "username" not in ucols:   # köhnə baza: istifadəçi adı sonradan avtomatik verilir
                c.execute("ALTER TABLE users ADD COLUMN username TEXT")
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_username ON users(username)")
            cols = [r[1] for r in c.execute("PRAGMA table_info(creds)")]
            if "verified" not in cols:   # köhnə baza: mövcud e-poçt hesabları təsdiqsiz sayılır
                c.execute("ALTER TABLE creds ADD COLUMN verified INTEGER NOT NULL DEFAULT 0")
            c.commit()
        finally:
            c.close()


init_db()


def clean_name(s):
    s = re.sub(r"[\x00-\x1f\x7f<>]", "", str(s or ""))
    return re.sub(r"\s+", " ", s).strip()[:20]


def auth_sub():
    tok = request.headers.get("X-Token", "")[:100]
    if not tok:
        abort(401)
    row = dbq("SELECT sub, created FROM sessions WHERE token=?", (tok,), one=True)
    if not row or time.time() - row["created"] > SESSION_DAYS * 86400:
        abort(401)
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


def send_mail(to, subject, body):
    if os.environ.get("MAIL_DEBUG") == "1":
        print("[MAIL_DEBUG] to=%s subject=%s\n%s" % (to, subject, body), flush=True)
        return
    if os.environ.get("BREVO_API_KEY"):
        # HTTP API (443 portu): Render pulsuz planda SMTP portları bloklanır
        r = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": os.environ["BREVO_API_KEY"], "Content-Type": "application/json"},
            json={"sender": {"name": "Birlikdə İzlə", "email": os.environ["SMTP_FROM"]},
                  "to": [{"email": to}], "subject": subject, "textContent": body},
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


def send_code(email, purpose):
    """Kod göndərir. Soyutma müddətindədirsə göndərmir və False qaytarır."""
    now = time.time()
    row = dbq("SELECT sent FROM codes WHERE email=? AND purpose=?", (email, purpose), one=True)
    if row and now - row["sent"] < CODE_COOLDOWN:
        return False
    code = "%06d" % secrets.randbelow(10 ** 6)
    dbq("INSERT OR REPLACE INTO codes(email,purpose,h,exp,sent,tries) VALUES(?,?,?,?,?,0)",
        (email, purpose, _code_hash(email, purpose, code), now + CODE_TTL, now))
    what = "e-poçtunu təsdiq etmək" if purpose == "verify" else "şifrəni yeniləmək"
    try:
        send_mail(email, "Birlikdə İzlə: kodun " + code,
                  "Salam!\n\n%s üçün kodun: %s\n\nKod 15 dəqiqə etibarlıdır. "
                  "Bu sorğunu sən etməmisənsə, məktubu nəzərə alma.\n\nBirlikdə İzlə" % (what[0].upper() + what[1:], code))
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


def primary_cred(sub):
    return dbq("SELECT email, pw FROM creds WHERE sub=? ORDER BY verified DESC, rowid LIMIT 1", (sub,), one=True)


def profile_json(sub):
    u = dbq("SELECT name, username FROM users WHERE sub=?", (sub,), one=True)
    if not u:
        abort(401)
    uname = u["username"] or ensure_username(sub)
    cr = primary_cred(sub)
    av = dbq("SELECT ts FROM avatars WHERE sub=?", (sub,), one=True)
    return dict(name=u["name"], username=uname, email=cr["email"] if cr else "",
                has_pw=bool(cr and cr["pw"]),
                avatar=("/api/avatar/%s?v=%d" % (uname, int(av["ts"]))) if av and uname else "")


def merge_users(old, new):
    """İki hesabı birləşdirir: tarixçə və sessiyalar new hesaba keçir."""
    dbq("UPDATE activity SET sub=? WHERE sub=?", (new, old))
    dbq("UPDATE sessions SET sub=? WHERE sub=?", (new, old))
    dbq("DELETE FROM avatars WHERE sub=? AND EXISTS(SELECT 1 FROM avatars WHERE sub=?)", (old, new))
    dbq("UPDATE OR IGNORE avatars SET sub=? WHERE sub=?", (new, old))
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


def _client_ip():
    xf = request.headers.get("X-Forwarded-For", "")
    return (xf.split(",")[-1].strip() if xf else request.remote_addr) or "?"


def log_login(email, ok, kind):
    """Giriş/qeydiyyat cəhdini admin panel üçün qeyd edir (IP, UA, vaxt)."""
    try:
        dbq("INSERT INTO login_log(email,ip,ok,kind,ua,ts) VALUES(?,?,?,?,?,?)",
            (email, _client_ip(), 1 if ok else 0, kind, request.headers.get("User-Agent", "")[:300], time.time()))
    except Exception:
        pass


@app.route("/api/register", methods=["POST"])
def api_register():
    d, email = body_email()
    pw = str(d.get("password") or "")
    if not (6 <= len(pw) <= 72):
        abort(400)
    if not mail_ready():
        abort(503)
    name = clean_name(d.get("name")) or clean_name(email.split("@")[0]) or "Qonaq"
    row = dbq("SELECT sub, verified FROM creds WHERE email=?", (email,), one=True)
    ph = generate_password_hash(pw, method=PW_METHOD)
    if row and row["verified"]:
        abort(409)
    if row:   # təsdiqsiz köhnə cəhd: sahibi təsdiqləməyib, üstündən yazılır
        dbq("UPDATE creds SET pw=? WHERE email=?", (ph, email))
        dbq("UPDATE users SET name=? WHERE sub=?", (name, row["sub"]))
    else:
        sub = "e:" + secrets.token_hex(8)
        try:
            dbq("INSERT INTO creds(email,sub,pw,verified) VALUES(?,?,?,0)", (email, sub, ph))
        except sqlite3.IntegrityError:
            abort(409)
        dbq("INSERT INTO users(sub,name,created) VALUES(?,?,?)", (sub, name, time.time()))
        ensure_username(sub)
    log_login(email, True, "register")
    send_code(email, "verify")
    return jsonify(pending=True)


@app.route("/api/verify", methods=["POST"])
def api_verify():
    d, email = body_email()
    row = dbq("SELECT sub FROM creds WHERE email=?", (email,), one=True)
    if not row or not check_code(email, "verify", d.get("code")):
        abort(422)
    dbq("UPDATE creds SET verified=1 WHERE email=?", (email,))
    return jsonify(token=issue_token(row["sub"]), name=user_name(row["sub"]))


@app.route("/api/resend", methods=["POST"])
def api_resend():
    _, email = body_email()
    if not mail_ready():
        abort(503)
    row = dbq("SELECT verified FROM creds WHERE email=?", (email,), one=True)
    if row and not row["verified"] and not send_code(email, "verify"):
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
        log_login(email, False, "login")
        abort(401)
    if not row["verified"]:   # şifrə düzdür, amma e-poçt təsdiqlənməyib
        if mail_ready():
            send_code(email, "verify")
        log_login(email, False, "login")
        abort(403)
    log_login(email, True, "login")
    return jsonify(token=issue_token(row["sub"]), name=user_name(row["sub"]))


FORGOT_HITS = {}   # ip -> vaxtlar ("hesab var/yox" cavabını kütləvi yoxlamaqdan qorunma)


@app.route("/api/forgot", methods=["POST"])
def api_forgot():
    _, email = body_email()
    if not mail_ready():
        abort(503)
    now = time.time()
    if len(FORGOT_HITS) > 5000:
        FORGOT_HITS.clear()
    ip = _client_ip()
    hits = [t for t in FORGOT_HITS.get(ip, []) if now - t < 600]
    if len(hits) >= 15:
        abort(429)
    hits.append(now)
    FORGOT_HITS[ip] = hits
    if not dbq("SELECT 1 FROM creds WHERE email=?", (email,), one=True):
        abort(404)   # bu e-poçtla hesab yoxdur: kod göndərilmir, istifadəçi qeydiyyata yönləndirilir
    if not send_code(email, "reset"):
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
    LOGIN_FAILS.pop(email, None)
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


@app.route("/api/avatar/<u>")
def api_avatar_get(u):
    u = norm_username(u)
    if not username_valid(u):
        abort(404)
    row = dbq("SELECT a.data FROM avatars a JOIN users x ON x.sub=a.sub WHERE x.username=?", (u,), one=True)
    if not row:
        abort(404)
    resp = Response(bytes(row["data"]), mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "public, max-age=604800"
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
    LOGIN_FAILS.pop(key, None)
    return jsonify(ok=True)


@app.route("/api/email/request", methods=["POST"])
def api_email_request():
    sub, _ = auth_sub()
    _, email = body_email()
    if not mail_ready():
        abort(503)
    if dbq("SELECT 1 FROM creds WHERE email=?", (email,), one=True):
        abort(409)
    if not send_code(email, "chg:" + sub[:40]):
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


@app.route("/api/logout", methods=["POST"])
def api_logout():
    _, tok = auth_sub()
    dbq("DELETE FROM sessions WHERE token=?", (tok,))
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
        if legacy and gsub != sub:
            merge_users(gsub, sub)   # köhnə Google hesabı və e-poçt hesabı birləşir
    else:
        sub = gsub
        if not legacy:
            dbq("INSERT INTO users(sub,name,created) VALUES(?,?,?)", (sub, gname, time.time()))
        dbq("INSERT INTO creds(email,sub,pw,verified) VALUES(?,?,?,1)", (email, sub, ""))
    ensure_username(sub)
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
    except ValueError:
        abort(400)
    lk = request.args.get("lk") == "1"
    deadline = time.time() + 20
    with CV:
        r = get_room(code)
        admit(r, pid)
        if since > r["seq"]:  # server restart olub: köhnə "since" mesajları bloklamasın
            since = 0
        while True:
            now = time.time()
            prune_peers(r, now)
            alive_mark(r, pid, now)
            new = [m for m in r["msgs"] if m[0] > since and m[1] != pid]
            n = count_others(r, pid, now)
            left = deadline - now
            if new or n != pc or r["locked"] != lk or left <= 0:
                break
            CV.wait(timeout=left)
        resp = jsonify(seq=r["seq"], peers=n, msgs=[m[2] for m in new],
                       locked=r["locked"], epoch=r["created"])
    resp.headers["Cache-Control"] = "no-store"
    return resp


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


@app.route("/")
def index():
    try:
        dbq("INSERT INTO visits(ip,path,ua,ts) VALUES(?,?,?,?)",
            (_client_ip(), "/", request.headers.get("User-Agent", "")[:300], time.time()))
    except Exception:
        pass
    resp = send_file(os.path.join(HERE, "index.html"))
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# ---------------------------------------------------------------
#  Admin panel: ziyarətçi IP-ləri və giriş/qeydiyyat tarixçəsi.
#  ADMIN_PASSWORD mühit dəyişəni ilə qorunur — mütləq güclü dəyər seçin.
#  Parollar heç vaxt açıq mətn kimi saxlanmır/göstərilmir, yalnız hash.
# ---------------------------------------------------------------
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ADMIN_COOKIE = "admin_tok"
ADMIN_SESSION_DAYS = 7


def admin_authed():
    tok = request.cookies.get(ADMIN_COOKIE, "")
    if not tok:
        return False
    row = dbq("SELECT created FROM admin_sessions WHERE token=?", (tok,), one=True)
    return bool(row and time.time() - row["created"] <= ADMIN_SESSION_DAYS * 86400)


def admin_required(fn):
    from functools import wraps

    @wraps(fn)
    def wrapped(*a, **kw):
        if not ADMIN_PASSWORD:
            abort(503)  # ADMIN_PASSWORD təyin olunmayıb
        if not admin_authed():
            abort(401)
        return fn(*a, **kw)
    return wrapped


@app.route("/admin")
def admin_page():
    resp = send_file(os.path.join(HERE, "admin.html"))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/admin/login", methods=["POST"])
def api_admin_login():
    if not ADMIN_PASSWORD:
        abort(503)
    d = request.get_json(silent=True) or {}
    pw = str(d.get("password") or "")
    # vaxt-sabit müqayisə: timing hücumunun qarşısını alır
    if not hmac.compare_digest(pw, ADMIN_PASSWORD):
        time.sleep(0.4)  # brute-force-u yavaşlat
        abort(401)
    tok = secrets.token_urlsafe(32)
    dbq("INSERT INTO admin_sessions(token,created) VALUES(?,?)", (tok, time.time()))
    dbq("DELETE FROM admin_sessions WHERE created<?", (time.time() - ADMIN_SESSION_DAYS * 86400,))
    resp = jsonify(ok=True)
    resp.set_cookie(ADMIN_COOKIE, tok, httponly=True, secure=True, samesite="Strict",
                     max_age=ADMIN_SESSION_DAYS * 86400, path="/")
    return resp


@app.route("/api/admin/logout", methods=["POST"])
def api_admin_logout():
    tok = request.cookies.get(ADMIN_COOKIE, "")
    if tok:
        dbq("DELETE FROM admin_sessions WHERE token=?", (tok,))
    resp = jsonify(ok=True)
    resp.delete_cookie(ADMIN_COOKIE, path="/")
    return resp


@app.route("/api/admin/check")
def api_admin_check():
    return jsonify(authed=admin_authed())


@app.route("/api/admin/overview")
@admin_required
def api_admin_overview():
    now = time.time()
    rooms = people = 0
    with CV:
        for r in ROOMS.values():
            n = sum(1 for ts in r["peers"].values() if now - ts < PEER_TTL)
            if n:
                rooms += 1
                people += n
    users_total = dbq("SELECT COUNT(*) c FROM users", one=True)["c"]
    visits_24h = dbq("SELECT COUNT(*) c FROM visits WHERE ts>?", (now - 86400,), one=True)["c"]
    logins_24h = dbq("SELECT COUNT(*) c FROM login_log WHERE ts>? AND kind='login' AND ok=1", (now - 86400,), one=True)["c"]
    fails_24h = dbq("SELECT COUNT(*) c FROM login_log WHERE ts>? AND kind='login' AND ok=0", (now - 86400,), one=True)["c"]
    return jsonify(rooms_live=rooms, people_live=people, users_total=users_total,
                   visits_24h=visits_24h, logins_24h=logins_24h, fails_24h=fails_24h)


@app.route("/api/admin/users")
@admin_required
def api_admin_users():
    limit = min(max(int(request.args.get("limit", 100) or 100), 1), 500)
    q = ("SELECT u.sub, u.name, u.username, u.created, c.email, c.verified, "
         "(SELECT MAX(l.ts) FROM login_log l WHERE l.email=c.email AND l.ok=1 AND l.kind='login') last_login, "
         "(SELECT l.ip FROM login_log l WHERE l.email=c.email AND l.ok=1 AND l.kind='login' ORDER BY l.ts DESC LIMIT 1) last_ip "
         "FROM users u LEFT JOIN creds c ON c.sub=u.sub ORDER BY u.created DESC LIMIT ?")
    rows = dbq(q, (limit,), many=True)
    return jsonify(users=[dict(r) for r in rows])


@app.route("/api/admin/logins")
@admin_required
def api_admin_logins():
    limit = min(max(int(request.args.get("limit", 200) or 200), 1), 1000)
    rows = dbq("SELECT email, ip, ok, kind, ua, ts FROM login_log ORDER BY ts DESC LIMIT ?", (limit,), many=True)
    return jsonify(logins=[dict(r) for r in rows])


@app.route("/api/admin/visits")
@admin_required
def api_admin_visits():
    limit = min(max(int(request.args.get("limit", 200) or 200), 1), 1000)
    rows = dbq("SELECT ip, path, ua, ts FROM visits ORDER BY ts DESC LIMIT ?", (limit,), many=True)
    return jsonify(visits=[dict(r) for r in rows])



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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), threaded=True)
