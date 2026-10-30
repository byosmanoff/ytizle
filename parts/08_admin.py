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
