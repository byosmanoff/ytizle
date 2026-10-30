# ---------------------------------------------------------------
#  IP ban: admin paneldən bloklanan IP saytın heç bir səhifəsinə girə bilmir,
#  ona nəzakətli xəbərdarlıq səhifəsi göstərilir. Admin (giriş etmiş) özünü kilidləyə bilməz.
# ---------------------------------------------------------------
BAN_CACHE = {"ts": 0.0, "ips": {}}
BAN_REFRESH = 10   # saniyə: bazadan yenidən oxunma aralığı (hər sorğuda baza sorğusu olmasın)
BAN_MSG = "Bu IP ünvanından saytımıza giriş məhdudlaşdırılıb."
BAN_HTML = """<!doctype html>
<html lang="az"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Giriş məhdudlaşdırılıb</title>
<style>
*{box-sizing:border-box}
body{margin:0;min-height:100dvh;display:flex;align-items:center;justify-content:center;padding:20px;
background:#150a1d;background-image:linear-gradient(180deg,#2b1630 0%,#150a1d 50%,#2a0f24 100%);
color:#f5e9f0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.box{max-width:400px;width:100%;background:#f8e7e1;color:#2b0f26;border-radius:22px;padding:30px 26px;text-align:center;
box-shadow:0 18px 50px rgba(0,0,0,.45)}
.ic{font-size:46px;line-height:1;color:#e8567d}
h1{font-size:22px;margin:14px 0 10px}
p{font-size:15px;line-height:1.55;color:#5c3a4f;margin:0 0 12px}
.ip{display:inline-block;margin-top:6px;padding:7px 14px;border-radius:10px;background:#fff7f4;
border:1.5px solid #dcc9ca;font:600 14px ui-monospace,Menlo,Consolas,monospace;color:#2b0f26}
.sm{font-size:12.5px;color:#7a5469;margin-top:16px}
</style></head><body>
<div class="box">
<div class="ic">&#9829;</div>
<h1>Giriş məhdudlaşdırılıb</h1>
<p>Hörmətli istifadəçi, bu IP ünvanından saytımıza giriş idarəçi tərəfindən məhdudlaşdırılıb.</p>
<p>Əgər bunun səhv olduğunu düşünürsünüzsə, zəhmət olmasa sayt sahibi ilə əlaqə saxlayın. Anlayışınıza görə təşəkkür edirik.</p>
<div class="ip">{{IP}}</div>
<div class="sm">Birlikdə İzlə</div>
</div></body></html>"""


def norm_ip(raw):
    """IP-ni standart formaya salır (IPv6 qısaldılır, ::ffff:1.2.3.4 -> 1.2.3.4). Etibarsızdırsa None."""
    try:
        ip = ipaddress.ip_address(str(raw or "").strip().strip("[]"))
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return str(ip)


def ban_map(force=False):
    """Bloklu IP-lər (yaddaş keşi, 10 saniyədən bir bazadan yenilənir)."""
    now = time.time()
    if force or now - BAN_CACHE["ts"] > BAN_REFRESH:
        try:
            rows = dbq("SELECT ip, note, ts FROM ip_bans", many=True)
            BAN_CACHE["ips"] = {r["ip"]: (r["note"] or "", r["ts"]) for r in rows}
            BAN_CACHE["ts"] = now
        except Exception:
            pass   # baza xətası saytı dayandırmasın: köhnə siyahı qalır
    return BAN_CACHE["ips"]


@app.before_request
def ip_ban_gate():
    ip = norm_ip(client_ip())
    if not ip or ip not in ban_map():
        return None
    if admin_ok():
        return None   # giriş etmiş admin heç vaxt bloklanmır
    if request.path.startswith("/api/"):
        resp = jsonify(error="banned", message=BAN_MSG)
        resp.status_code = 403
    else:
        resp = Response(BAN_HTML.replace("{{IP}}", htmllib.escape(ip)), 403, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@app.route("/api/admin/bans")
def admin_bans():
    admin_guard()
    rows = dbq("SELECT ip, note, ts FROM ip_bans ORDER BY ts DESC", many=True)
    return _nostore(jsonify(bans=[dict(ip=r["ip"], note=r["note"] or "", ts=r["ts"]) for r in rows]))


@app.route("/api/admin/ban", methods=["POST"])
def admin_ban():
    admin_guard()
    d = request.get_json(silent=True) or {}
    ip = norm_ip(d.get("ip"))
    if not ip:
        return jsonify(error="format"), 400
    # Öz IP-ni bloklamaq üçün icazə verilir (admin giriş etmiş olduqda ban tətbiq olunmur).
    dbq("INSERT OR REPLACE INTO ip_bans(ip,note,ts) VALUES(?,?,?)", (ip, clean_text(d.get("note"), 100), time.time()))
    ban_map(force=True)
    return _nostore(jsonify(ok=True, ip=ip))


@app.route("/api/admin/unban", methods=["POST"])
def admin_unban():
    admin_guard()
    ip = norm_ip((request.get_json(silent=True) or {}).get("ip"))
    if not ip:
        return jsonify(error="format"), 400
    dbq("DELETE FROM ip_bans WHERE ip=?", (ip,))
    ban_map(force=True)
    return _nostore(jsonify(ok=True))


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
