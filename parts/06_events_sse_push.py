# ---------------------------------------------------------------
#  İstifadəçi hadisələri: SSE axını + Web Push (brauzer bağlı olsa da bildiriş)
#  SSE: /api/stream (X-Token başlığı ilə, fetch axını). Hər bağlantı ~50 san açıq qalır, sonra müştəri yenidən qoşulur.
#  Web Push: VAPID + RFC 8291 (aes128gcm), yalnız "cryptography" paketi lazımdır.
#  İstəyə görə mühit dəyişənləri: VAPID_PRIVATE_KEY (base64url, 32 bayt), VAPID_SUBJECT (mailto:...), SSE_MAX
# ---------------------------------------------------------------
USER_EV = {}          # sub -> [(seq, kind, payload)]
EV_SEQ = [0]
SSE_OPEN = [0]
SSE_MAX = int(os.environ.get("SSE_MAX", "60"))
PUSH_HOSTS = ("googleapis.com", "mozilla.com", "mozaws.net", "push.apple.com", "windows.com", "microsoft.com")
_VAPID = []


def notify_user(sub, kind, payload=None, push=None):
    """Hadisəni istifadəçinin açıq SSE axınlarına verir; push verilibsə qeydiyyatlı cihazlara da göndərir."""
    if not sub:
        return
    with USER_CV:
        EV_SEQ[0] += 1
        if len(USER_EV) > 5000:
            # köhnəni tam silmək əvəzinə ən az aktiv olanları at
            for k in list(USER_EV.keys())[:2000]:
                USER_EV.pop(k, None)
        q = USER_EV.setdefault(sub, [])
        q.append((EV_SEQ[0], kind, payload or {}))
        del q[:-30]
        USER_CV.notify_all()
    if push:
        threading.Thread(target=push_to_user, args=(sub, push), daemon=True).start()


def _b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(x):
    x = str(x)
    return base64.urlsafe_b64decode(x + "=" * (-len(x) % 4))


def vapid_keys():
    """(şəxsi açar, açıq açar base64url) və ya None (cryptography yoxdursa)."""
    if _VAPID:
        return _VAPID[0]
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
    except Exception:
        return None
    env = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    if env:
        key = ec.derive_private_key(int.from_bytes(_b64d(env), "big"), ec.SECP256R1())
    else:
        row = dbq("SELECT v FROM push_cfg WHERE k='vapid'", one=True)
        if row:
            key = ec.derive_private_key(int(row["v"]), ec.SECP256R1())
        else:
            key = ec.generate_private_key(ec.SECP256R1())
            dbq("INSERT OR IGNORE INTO push_cfg(k,v) VALUES('vapid',?)", (str(key.private_numbers().private_value),))
            row = dbq("SELECT v FROM push_cfg WHERE k='vapid'", one=True)
            key = ec.derive_private_key(int(row["v"]), ec.SECP256R1())
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    _VAPID.append((key, _b64u(pub)))
    return _VAPID[0]


def push_endpoint_ok(url):
    p = urlparse(str(url or ""))
    h = (p.hostname or "").lower()
    return p.scheme == "https" and len(str(url)) < 600 and any(h == d or h.endswith("." + d) for d in PUSH_HOSTS)


def _push_encrypt(sub_info, data):
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    ua_pub, auth = _b64d(sub_info["p256dh"]), _b64d(sub_info["auth"])
    eph = ec.generate_private_key(ec.SECP256R1())
    as_pub = eph.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = eph.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub))
    salt = os.urandom(16)
    ikm = HKDF(hashes.SHA256(), 32, auth, b"WebPush: info\x00" + ua_pub + as_pub).derive(shared)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    ct = AESGCM(cek).encrypt(nonce, data + b"\x02", None)
    return salt + struct.pack(">I", 4096) + bytes([len(as_pub)]) + as_pub + ct


def _vapid_auth(endpoint):
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    key, pub = vapid_keys()
    p = urlparse(endpoint)
    head = _b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = _b64u(json.dumps({"aud": "%s://%s" % (p.scheme, p.netloc), "exp": int(time.time()) + 12 * 3600,
                               "sub": os.environ.get("VAPID_SUBJECT", "mailto:admin@birlikde-izle.app")},
                              separators=(",", ":")).encode())
    r, s_ = decode_dss_signature(key.sign((head + "." + claims).encode(), ec.ECDSA(hashes.SHA256())))
    sig = r.to_bytes(32, "big") + s_.to_bytes(32, "big")
    return "vapid t=%s.%s.%s, k=%s" % (head, claims, _b64u(sig), pub)


def push_to_user(sub, payload):
    """Bütün cihazlara göndərir. Ölü abunəliklər (404/410) silinir. Heç vaxt istisna atmır."""
    try:
        if not vapid_keys():
            return
        rows = dbq("SELECT endpoint,p256dh,auth FROM push_subs WHERE sub=?", (sub,), many=True)
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()[:3000]
        for r in rows:
            try:
                resp = requests.post(r["endpoint"], data=_push_encrypt(r, data), timeout=8, headers={
                    "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
                    "TTL": "3600", "Urgency": "high", "Authorization": _vapid_auth(r["endpoint"])})
                if resp.status_code in (404, 410):
                    dbq("DELETE FROM push_subs WHERE endpoint=?", (r["endpoint"],))
            except Exception:
                pass
    except Exception:
        pass


@app.route("/api/push/key")
def api_push_key():
    k = vapid_keys()
    return _nostore(jsonify(enabled=bool(k), key=k[1] if k else ""))


@app.route("/api/push/subscribe", methods=["POST"])
def api_push_subscribe():
    sub, _ = auth_sub()
    d = request.get_json(silent=True) or {}
    k = d.get("keys") or {}
    ep, p256, au = str(d.get("endpoint") or ""), str(k.get("p256dh") or ""), str(k.get("auth") or "")
    if not push_endpoint_ok(ep) or not (60 < len(p256) < 140) or not (10 < len(au) < 40):
        abort(400)
    if not rate_hit("pushsub", sub, 20, 3600):
        abort(429)
    dbq("INSERT OR REPLACE INTO push_subs(endpoint,sub,p256dh,auth,ts) VALUES(?,?,?,?,?)", (ep, sub, p256, au, time.time()))
    dbq("DELETE FROM push_subs WHERE sub=? AND endpoint NOT IN "
        "(SELECT endpoint FROM push_subs WHERE sub=? ORDER BY ts DESC LIMIT 8)", (sub, sub))
    return jsonify(ok=True)


@app.route("/api/push/unsubscribe", methods=["POST"])
def api_push_unsubscribe():
    sub, _ = auth_sub()
    dbq("DELETE FROM push_subs WHERE sub=? AND endpoint=?", (sub, str((request.get_json(silent=True) or {}).get("endpoint") or "")))
    return jsonify(ok=True)


@app.route("/api/stream")
def api_stream():
    sub, _ = auth_sub()
    try:
        last = int(request.args.get("since", "-1"))
    except ValueError:
        last = -1
    with USER_CV:
        if SSE_OPEN[0] >= SSE_MAX:
            return jsonify(error="busy"), 503
        SSE_OPEN[0] += 1
        if last < 0 or last > EV_SEQ[0]:   # ilk qoşulma və ya server yenidən başlayıb: yalnız yeni hadisələr
            last = EV_SEQ[0]

    def gen(last=last):
        deadline = time.time() + 50
        try:
            yield "retry: 3000\nid: %d\nevent: hello\ndata: {}\n\n" % last
            while True:
                with USER_CV:
                    new = [e for e in USER_EV.get(sub, []) if e[0] > last]
                    left = deadline - time.time()
                    if not new and left > 0:
                        USER_CV.wait(timeout=min(left, 15))
                        new = [e for e in USER_EV.get(sub, []) if e[0] > last]
                        left = deadline - time.time()
                if new:
                    for seq, kind, payload in new:
                        last = seq
                        yield "id: %d\nevent: %s\ndata: %s\n\n" % (seq, kind, json.dumps(payload, ensure_ascii=False))
                else:
                    yield ": ping\n\n"
                if left <= 0:
                    return
        finally:
            with USER_CV:
                SSE_OPEN[0] = max(0, SSE_OPEN[0] - 1)

    resp = Response(gen(), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache, no-transform"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@app.route("/api/invite", methods=["POST"])
def api_invite():
    sub, tok = auth_sub()
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
    with META_LOCK:
        exists = code in ROOMS
    if not exists:
        return jsonify(error="room"), 404
    dbq("INSERT OR REPLACE INTO invites(to_sub,from_sub,room,ts) VALUES(?,?,?,?)", (row["sub"], sub, code, time.time()))
    dbq("DELETE FROM invites WHERE to_sub=? AND id NOT IN "
        "(SELECT id FROM invites WHERE to_sub=? ORDER BY ts DESC LIMIT 20)", (row["sub"], row["sub"]))
    nm = ident_for(tok)["name"]
    notify_user(row["sub"], "invite", push=dict(title="Otaq dəvəti", body=nm + " səni birlikdə izləməyə çağırır", tag="invite", url="/"))
    return jsonify(ok=True)


@app.route("/api/invites")
def api_invites():
    sub, _ = auth_sub()
    dbq("DELETE FROM invites WHERE ts<?", (time.time() - 3600,))
    rows = dbq("SELECT i.id, i.room, i.ts, u.name, u.username, (SELECT ts FROM avatars a WHERE a.sub=u.sub) AS av "
               "FROM invites i JOIN users u ON u.sub=i.from_sub WHERE i.to_sub=? ORDER BY i.ts DESC",
               (sub,), many=True)
    with META_LOCK:
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


