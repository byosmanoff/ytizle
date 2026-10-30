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
CREATE TABLE IF NOT EXISTS ip_bans(ip TEXT PRIMARY KEY, note TEXT, ts REAL NOT NULL);
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
