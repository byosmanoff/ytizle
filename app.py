import os, re, json, base64, time, secrets, threading, ipaddress, socket, sqlite3, smtplib, hashlib, hmac, shutil, datetime, zlib, struct, html as htmllib
from urllib.parse import urlparse, urljoin, parse_qs
import gzip
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
#  Yaddaşda saxlanır, ona görə:  gunicorn --workers 1 --threads 64 app:app
#  (long-poll hər sorğunu 20 san tutur; --threads az olanda növbə/lag yaranır.
#   Tövsiyə: threads=32–128, workers=1 — state yaddaşdadır, multi-worker olmaz)
# ---------------------------------------------------------------
ROOMS = {}
# Köhnə tək CV thundering-herd yaradırdı (bir otaq bütün long-poll-ları oyadırdı).
# İndi: otaq/oyun/SSE ayrı Condition; hər obyektin öz _cv-si var.
META_LOCK = threading.RLock()          # ROOMS/DOMINO/... dict üzvlüyü
USER_CV = threading.Condition()        # SSE / şəxsi bildirişlər
ROOM_CV = threading.Condition()        # köhnə otaq yolu (get_room _cv ilə əvəz olunur)
CV = ROOM_CV  # geriyə uyğunluq: oyunlar mərhələli köçürülür
PEER_TTL = 45
CODE_CHARS = "abcdefghjkmnpqrstuvwxyz23456789"
MAX_ROOMS = 300
MAX_MEMBERS = 30
MAX_HTML = 5 * 1024 * 1024  # proxy ilə yüklənən HTML üçün yuxarı limit


def _ensure_cv(obj):
    """Obyektə şəxsi Condition bağla (yoxdursa yarat)."""
    cv = obj.get("_cv")
    if cv is None:
        cv = threading.Condition()
        obj["_cv"] = cv
    return cv


def _notify(obj):
    """Yalnız bu obyektin long-poll gözləyənlərini oyadır (thundering herd yoxdur)."""
    cv = _ensure_cv(obj)
    with cv:
        cv.notify_all()

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
    with META_LOCK:
        r = ROOMS.get(code)
        if r is None:
            cleanup(time.time())
            if len(ROOMS) >= MAX_ROOMS:  # send/poll ilə sonsuz otaq yaradılmasın
                abort(503)
            if not rate_hit("newroom", client_ip(), 20, 600):   # bir IP bütün otaqları tuta bilməsin
                abort(429)
            r = {"seq": 0, "msgs": [], "peers": {}, "created": time.time(),
                 "locked": False, "members": set(),
                 "host": None, "roles": {}, "hostonly": False, "rv": 0, "hist": [],
                 "_cv": threading.Condition()}
            ROOMS[code] = r
        else:
            _ensure_cv(r)
        return r


def _req_sk():
    """Cihazın gizli açarı (X-Sk başlığı, ?sk= və ya JSON gövdəsində sk)."""
    sk = request.headers.get("X-Sk", "") or request.args.get("sk", "")
    if not sk:
        j = request.get_json(silent=True)
        if isinstance(j, dict):
            sk = str(j.get("sk") or "")
    return sk[:100]


def verify_pid(r, pid, bind=True):
    """Açıq id (pid) /api/poll ilə otaqdakı hamıya göndərilir, ona görə kimlik sübutu deyil.
    Hər pid ilk gələn gizli açara bağlanır; sonrakı sorğular eyni açarı göstərməlidir.
    CV tutulu halda çağırılır."""
    sk = _req_sk()
    if len(sk) < 16:
        abort(401)
    h = hashlib.sha256(sk.encode()).hexdigest()
    sec = r.setdefault("sec", {})
    cur = sec.get(pid)
    if cur is None:
        if bind:
            sec[pid] = h
    elif not hmac.compare_digest(cur, h):
        abort(401)


def admit(r, pid):
    """Üzvü qəbul et. Otaq kilidlidirsə yalnız əvvəl girmiş cihazlar keçə bilər."""
    if pid in r["members"]:
        verify_pid(r, pid)
        return
    if r["locked"] or len(r["members"]) >= MAX_MEMBERS:
        abort(403)
    verify_pid(r, pid)
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
    # çağıran artıq META_LOCK tutmuş ola bilər; tutmayıbsa tut
    held = META_LOCK.acquire(blocking=False)
    try:
        if not held:
            META_LOCK.acquire()
        for k, r in list(ROOMS.items()):
            last = max([r["created"]] + list(r["peers"].values()) or [r["created"]])
            if now - last > 3600:
                dead.append(k)
            else:
                prune_peers(r, now)
        for k in dead:
            ROOMS.pop(k, None)
            shutil.rmtree(room_dir(k), ignore_errors=True)
    finally:
        META_LOCK.release()


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
    with META_LOCK:
        code = _new_room(pid)
        r = ROOMS[code]
    if pid:
        with _ensure_cv(r):
            verify_pid(r, pid)   # yaradanın gizli açarını dərhal bağla
    return jsonify(room=code)


@app.route("/api/join", methods=["POST"])
def api_join():
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    pid = str(d.get("id", ""))[:20]
    if not code or not pid:
        abort(400)
    now = time.time()
    with META_LOCK:
        cleanup(now)
        if code not in ROOMS:
            abort(404)
        r = ROOMS[code]
        _ensure_cv(r)
    with r["_cv"]:
        admit(r, pid)
        r.get("left", {}).pop(pid, None)
        r["peers"][pid] = now
        if not r["host"]:
            r["host"] = pid
        r["_cv"].notify_all()
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
    r = get_room(code)
    with _ensure_cv(r):
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
        r["_cv"].notify_all()
    return jsonify(ok=True)


@app.route("/api/leave", methods=["POST"])
def api_leave():
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    pid = str(d.get("id", ""))[:20]
    if not code or not pid:
        abort(400)
    with META_LOCK:
        r = ROOMS.get(code)
    if r is not None:
        with _ensure_cv(r):
            verify_pid(r, pid, bind=False)
            r["peers"].pop(pid, None)
            r.setdefault("left", {})[pid] = time.time()
            if r.get("host") == pid:
                pass_host(r, time.time())   # host çıxdı: host ilk moderatora keçir
            r["_cv"].notify_all()  # qarşı tərəfin long-poll-u dərhal yenilənsin
    return jsonify(ok=True)


import subprocess
import shutil


VIDEO_TRANSCODE = os.environ.get("VIDEO_TRANSCODE", "1") != "0"           # "0" yazsan çevirmə tam söndürülür
VIDEO_TRANSCODE_TIMEOUT = int(os.environ.get("VIDEO_TRANSCODE_TIMEOUT", "75"))   # saniyə: çatmasa orijinal fayl qalır


def _ffmpeg_bin():
    """ffmpeg yolunu tapır: sistemdə varsa onu, yoxsa imageio-ffmpeg paketindəki hazır faylı."""
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _needs_h264(path, ff):
    """Video H.264 8-bit deyilsə (məs. telefonun HEVC/10-bit videosu) True qaytarır."""
    try:
        r = subprocess.run([ff, "-hide_banner", "-i", path], capture_output=True, text=True, timeout=30)
    except Exception:
        return False
    m = re.search(r"Video:\s*(\w+)[^\n]*", r.stderr or "")
    if not m:
        return False
    line = m.group(0)
    return m.group(1) != "h264" or "yuv420p10" in line or "yuv420p" not in line


def _to_h264(src, dst, ff):
    vf = "scale='if(gt(iw,ih),min(1280,iw),-2)':'if(gt(iw,ih),-2,min(1280,ih))'"
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", src,
           "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
           "-pix_fmt", "yuv420p", "-vf", vf, "-c:a", "aac", "-b:a", "96k",
           "-movflags", "+faststart", "-f", "mp4", dst]
    subprocess.run(cmd, check=True, capture_output=True, timeout=VIDEO_TRANSCODE_TIMEOUT)


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
    with META_LOCK:
        r = ROOMS.get(code)
    if r is None:
        abort(404)
    with _ensure_cv(r):
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
    ff = _ffmpeg_bin() if VIDEO_TRANSCODE else None
    if ff and ext in (".mp4", ".mov") and _needs_h264(path, ff):
        # HEVC/10-bit video bəzi kompüterlərdə qara ekran verir: hamı üçün H.264-ə çeviririk
        base = name.rsplit(".", 1)[0]
        conv = os.path.join(d, base + ".conv")
        try:
            _to_h264(path, conv, ff)
            final = os.path.join(d, base + ".mp4")
            os.replace(conv, final)
            if final != path:
                try:
                    os.remove(path)
                except OSError:
                    pass
            name, path = base + ".mp4", final
        except Exception:
            try:
                os.remove(conv)
            except OSError:
                pass   # çevirmə alınmadısa orijinal qalır
    for old in os.listdir(d):   # otaqda həmişə yalnız ən son video qalır
        if old != name:
            try:
                os.remove(os.path.join(d, old))
            except OSError:
                pass
    url = "/vf/%s/%s" % (code, name)
    with META_LOCK:
        r = ROOMS.get(code)
    if r is not None:
        with _ensure_cv(r):
            r["vpos"] = None
            r["video"] = url   # otağa sonradan girən (və ya yenidən girən) hər kəs bunu avtomatik açır
            r["_cv"].notify_all()
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
    with META_LOCK:
        r = ROOMS.get(code)
    if r is None:
        abort(404)
    with _ensure_cv(r):
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
    if len(os.listdir(d)) >= 200:   # otaq başına şəkil limiti (disk dolmasın)
        abort(429)
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
    with META_LOCK:
        r = ROOMS.get(code)
    if r is None:
        abort(404)
    with _ensure_cv(r):
        if pid not in r["members"]:
            abort(403)
        verify_pid(r, pid)
        if r["host"] and role_of(r, pid) == "viewer":
            abort(403)   # kilidi yalnız host və moderator dəyişir
        r["locked"] = bool(d.get("locked"))
        r["_cv"].notify_all()
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
CREATE TABLE IF NOT EXISTS ip_bans(ip TEXT PRIMARY KEY, note TEXT, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS friends(a TEXT NOT NULL, b TEXT NOT NULL, status INTEGER NOT NULL DEFAULT 0, ts REAL NOT NULL,
  PRIMARY KEY(a, b));
CREATE INDEX IF NOT EXISTS friends_b ON friends(b);
CREATE TABLE IF NOT EXISTS blocks(blocker TEXT NOT NULL, blocked TEXT NOT NULL, ts REAL NOT NULL, PRIMARY KEY(blocker, blocked));
CREATE INDEX IF NOT EXISTS blocks_blocked ON blocks(blocked);
CREATE TABLE IF NOT EXISTS dm(id INTEGER PRIMARY KEY AUTOINCREMENT, from_sub TEXT NOT NULL, to_sub TEXT NOT NULL,
  text TEXT NOT NULL, ts REAL NOT NULL, read INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS dm_pair ON dm(from_sub, to_sub, id);
CREATE INDEX IF NOT EXISTS dm_to ON dm(to_sub, read);
CREATE TABLE IF NOT EXISTS presence(sub TEXT PRIMARY KEY, seen REAL NOT NULL, status TEXT);
CREATE TABLE IF NOT EXISTS watch(sub TEXT NOT NULL, day TEXT NOT NULL, secs REAL NOT NULL DEFAULT 0, tog REAL NOT NULL DEFAULT 0,
  PRIMARY KEY(sub, day));
CREATE TABLE IF NOT EXISTS plans(id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, title TEXT NOT NULL, ts REAL NOT NULL,
  room TEXT, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS plans_owner ON plans(owner);
CREATE TABLE IF NOT EXISTS plan_inv(plan_id INTEGER NOT NULL, sub TEXT NOT NULL, status INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(plan_id, sub));
CREATE INDEX IF NOT EXISTS plan_inv_sub ON plan_inv(sub);
CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, reporter TEXT NOT NULL,
  target TEXT, tname TEXT, tuser TEXT, kind TEXT NOT NULL, room TEXT, msg_id TEXT, msg_text TEXT,
  reason TEXT NOT NULL, details TEXT, status INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS reports_ts ON reports(status, ts);
CREATE TABLE IF NOT EXISTS push_subs(endpoint TEXT PRIMARY KEY, sub TEXT NOT NULL, p256dh TEXT NOT NULL, auth TEXT NOT NULL, ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS push_subs_sub ON push_subs(sub);
CREATE TABLE IF NOT EXISTS push_cfg(k TEXT PRIMARY KEY, v TEXT NOT NULL);
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
            
            pcols = [r[1] for r in c.execute("PRAGMA table_info(presence)")]
            if "status" not in pcols:
                c.execute("ALTER TABLE presence ADD COLUMN status TEXT")
            dcols = [r[1] for r in c.execute("PRAGMA table_info(dm)")]
            if "kind" not in dcols:   # text | image | video | audio | call
                c.execute("ALTER TABLE dm ADD COLUMN kind TEXT NOT NULL DEFAULT 'text'")
            if "media" not in dcols:
                c.execute("ALTER TABLE dm ADD COLUMN media TEXT")
            if "dur" not in dcols:   # səs/video/zəng müddəti (saniyə)
                c.execute("ALTER TABLE dm ADD COLUMN dur INTEGER NOT NULL DEFAULT 0")
            if "delivered" not in dcols:   # alıcının cihazına çatıb
                c.execute("ALTER TABLE dm ADD COLUMN delivered INTEGER NOT NULL DEFAULT 0")
            if "reply_to" not in dcols:   # yanıt verilən mesajın id-si (WhatsApp tipli reply)
                c.execute("ALTER TABLE dm ADD COLUMN reply_to INTEGER")
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_username ON users(username)")
            cols = [r[1] for r in c.execute("PRAGMA table_info(creds)")]
            if "want" not in cols:   # qeydiyyatda seçilən istifadəçi adı (e-poçt təsdiqlənəndə verilir)
                c.execute("ALTER TABLE creds ADD COLUMN want TEXT")
            if "verified" not in cols:   # köhnə baza: mövcud e-poçt hesabları təsdiqsiz sayılır
                c.execute("ALTER TABLE creds ADD COLUMN verified INTEGER NOT NULL DEFAULT 0")
            c.commit()
        finally:
            c.close()


def _auto_restore():
    """Render-də wt.db silinibsə, ən son ehtiyat nüsxədən bərpa et (nüsxələr silinməyən diskdədirsə)."""
    try:
        bdir = os.environ.get("DB_BACKUP_DIR", os.path.join(os.path.dirname(DB_PATH) or HERE, "backups"))
        if os.path.exists(DB_PATH) or not os.path.isdir(bdir):
            return
        fs = sorted(f for f in os.listdir(bdir) if f.startswith("wt-") and f.endswith(".db") and "before-restore" not in f)
        if fs:
            shutil.copyfile(os.path.join(bdir, fs[-1]), DB_PATH)
    except Exception:
        pass


_auto_restore()
init_db()

# ---------------------------------------------------------------
#  Bəyəndiklərim, reytinq, həftəlik liderlər, turnirlər, rate limit, SQLite ehtiyat nüsxəsi
#  Mühit dəyişənləri (istəyə görə): DB_BACKUP_DIR, BACKUP_HOURS (standart 6), BACKUP_KEEP (standart 8)
# ---------------------------------------------------------------
BACKUP_DIR = os.environ.get("DB_BACKUP_DIR", os.path.join(os.path.dirname(DB_PATH) or HERE, "backups"))
BACKUP_HOURS = max(1, float(os.environ.get("BACKUP_HOURS", "6")))
BACKUP_KEEP = max(1, int(os.environ.get("BACKUP_KEEP", "8")))
GAMES = ("domino", "chess", "durak", "mafia", "uno")
BAKU = 4 * 3600   # Bakı vaxtı UTC+4


def init_extra():
    with DBL:
        c = sqlite3.connect(DB_PATH, timeout=10)
        try:
            c.executescript("""
CREATE TABLE IF NOT EXISTS favs(sub TEXT NOT NULL, url TEXT NOT NULL, title TEXT, thumb TEXT, ts REAL NOT NULL, PRIMARY KEY(sub, url));
CREATE TABLE IF NOT EXISTS game_results(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, game TEXT NOT NULL, sub TEXT NOT NULL, res TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS gr_game_ts ON game_results(game, ts);
CREATE INDEX IF NOT EXISTS gr_sub ON game_results(sub, ts);
CREATE TABLE IF NOT EXISTS tourneys(id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, game TEXT NOT NULL, title TEXT NOT NULL, t0 REAL NOT NULL, t1 REAL NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tourney_players(tid INTEGER NOT NULL, sub TEXT NOT NULL, ts REAL NOT NULL, PRIMARY KEY(tid, sub));
""")
            c.commit()
        finally:
            c.close()


init_extra()


# ---- Oyun nəticələri: qalibiyyət 3 xal, bərabərlik 1, məğlubiyyət 0. Botlu oyunlar və tək insanlı oyunlar sayılmır ----
def _human(p):
    return isinstance(p, str) and p and not p.startswith("bot") and not p.startswith("guest:")


def record_game(game, winners=(), losers=(), draws=()):
    humans = {p for p in list(winners) + list(losers) + list(draws) if _human(p)}
    if game not in GAMES or len(humans) < 2:
        return
    now = time.time()
    rows = [(now, game, p, "w") for p in winners if _human(p)] + \
           [(now, game, p, "l") for p in losers if _human(p)] + \
           [(now, game, p, "d") for p in draws if _human(p)]
    try:
        with DBL:
            c = sqlite3.connect(DB_PATH, timeout=10)
            try:
                c.executemany("INSERT INTO game_results(ts,game,sub,res) VALUES(?,?,?,?)", rows)
                c.commit()
            finally:
                c.close()
    except Exception:
        pass


def rec_game(kind, g):
    """Bitmiş oyunu bir dəfə qeyd edir (raund nömrəsi ilə təkrarın qarşısı alınır). Heç vaxt istisna atmır."""
    try:
        if g.get("bot") or g.get("_rr") == g.get("round"):
            return
        pl = [p for p in g.get("players", [])]
        if kind == "domino":
            m = g.get("match")
            if not m:
                return
            g["_rr"] = g.get("round")
            if g.get("team") and m in g["team"]:
                wt = g["team"][m]
                record_game("domino", [p for p in pl if g["team"].get(p) == wt], [p for p in pl if g["team"].get(p) != wt])
            else:
                record_game("domino", [m], [p for p in pl if p != m])
        elif kind == "chess":
            if g.get("status") != "over":
                return
            g["_rr"] = g.get("round")
            w = g.get("win")
            if w:
                record_game("chess", [w], [p for p in pl if p != w])
            else:
                record_game("chess", draws=pl)
        elif kind == "durak":
            eng = g.get("eng")
            if g.get("status") != "over" or not eng or not eng.over:
                return
            g["_rr"] = g.get("round")
            lo = eng.loser
            if lo:
                record_game("durak", [p for p in pl if p != lo], [lo])
            else:
                record_game("durak", draws=pl)
        elif kind == "uno":
            eng = g.get("eng")
            if g.get("status") != "over" or not eng or not eng.over:
                return
            g["_rr"] = g.get("round")
            if eng.winner:
                record_game("uno", [eng.winner], [p for p in pl if p != eng.winner])
            elif eng.quitter:
                record_game("uno", [p for p in pl if p != eng.quitter], [eng.quitter])
        elif kind == "mafia":
            w = g.get("winner")
            if g.get("status") != "over" or not w:
                return
            g["_rr"] = g.get("round")
            roles = g.get("roles") or {}
            hum = [p for p in pl if _human(p) and not (g.get("gone") or {}).get(p)]
            win_t = [p for p in hum if ("mafia" if roles.get(p) == "mafia" else "town") == w]
            record_game("mafia", win_t, [p for p in hum if p not in win_t])
    except Exception:
        pass


def week_start(now=None):
    """Bazar ertəsi 00:00 (Bakı vaxtı) UTC timestamp kimi."""
    now = now or time.time()
    local = now + BAKU
    day0 = local - (local % 86400)
    wd = datetime.datetime.utcfromtimestamp(day0).weekday()   # 0 = bazar ertəsi
    return day0 - wd * 86400 - BAKU


def _board(game, t0, t1, limit=20, subs=None):
    q = "SELECT sub, SUM(res='w') AS w, SUM(res='l') AS l, SUM(res='d') AS d FROM game_results WHERE ts>=? AND ts<?"
    a = [t0, t1]
    if game in GAMES:
        q += " AND game=?"
        a.append(game)
    if subs is not None:
        if not subs:
            return []
        q += " AND sub IN (%s)" % ",".join("?" * len(subs))
        a += list(subs)
    q += " GROUP BY sub"
    rows = [dict(r) for r in dbq(q, tuple(a), many=True)]
    for r in rows:
        r["pts"] = r["w"] * 3 + r["d"]
        r["n"] = r["w"] + r["l"] + r["d"]
    rows.sort(key=lambda r: (-r["pts"], -r["w"], r["n"]))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows


def _who(sub):
    u = dbq("SELECT name, username, (SELECT ts FROM avatars a WHERE a.sub=users.sub) AS av FROM users WHERE sub=?", (sub,), one=True)
    if not u or not u["username"]:
        return None
    return dict(name=u["name"], username=u["username"], avatar=_av_url(u["username"], u["av"]), badge=is_owner(sub))


def _decorate(rows, limit=None):
    out = []
    for r in rows:
        w = _who(r["sub"])
        if not w:
            continue
        out.append(dict(w, rank=r["rank"], w=r["w"], l=r["l"], d=r["d"], pts=r["pts"], n=r["n"], sub=r["sub"]))
        if limit and len(out) >= limit:
            break
    return out


@app.route("/api/leaderboard")
def api_leaderboard():
    sub, _ = auth_sub()
    game = request.args.get("game", "all")
    period = request.args.get("period", "week")
    now = time.time()
    ws = week_start(now)
    if period == "all":
        t0, t1 = 0, now + 1
    else:
        t0, t1 = ws, now + 1
    rows = _board(game, t0, t1)
    top = _decorate(rows, 20)
    me = next((dict(rank=r["rank"], w=r["w"], l=r["l"], d=r["d"], pts=r["pts"], n=r["n"]) for r in rows if r["sub"] == sub), None)
    last = _decorate(_board(game, ws - 7 * 86400, ws), 3) if period == "week" else []
    for x in top + last:
        x["me"] = (x.pop("sub") == sub)
    return _nostore(jsonify(top=top, me=me, last_week=last, week_ends=ws + 7 * 86400, now=now))


# ---- Turnirlər: iştirakçıların seçilmiş oyunda, turnir müddətində topladığı xallar ----
def _t_json(t, sub):
    now = time.time()
    n = dbq("SELECT COUNT(*) AS c FROM tourney_players WHERE tid=?", (t["id"],), one=True)["c"]
    joined = bool(dbq("SELECT 1 FROM tourney_players WHERE tid=? AND sub=?", (t["id"], sub), one=True))
    ow = _who(t["owner"])
    return dict(id=t["id"], game=t["game"], title=t["title"], t0=t["t0"], t1=t["t1"], players=n, joined=joined,
                active=now < t["t1"], owner=(ow or {}).get("name", ""), mine=(t["owner"] == sub))


@app.route("/api/tourneys")
def api_tourneys():
    sub, _ = auth_sub()
    rows = dbq("SELECT * FROM tourneys WHERE t1>? ORDER BY t1 ASC LIMIT 30", (time.time() - 3 * 86400,), many=True)
    return _nostore(jsonify(items=[_t_json(t, sub) for t in rows]))


@app.route("/api/tourney")
def api_tourney():
    sub, _ = auth_sub()
    try:
        t = dbq("SELECT * FROM tourneys WHERE id=?", (int(request.args.get("id", 0)),), one=True)
    except (TypeError, ValueError):
        abort(400)
    if not t:
        abort(404)
    subs = [r["sub"] for r in dbq("SELECT sub FROM tourney_players WHERE tid=?", (t["id"],), many=True)]
    rows = _board(t["game"], t["t0"], min(time.time(), t["t1"]) + 1, subs=subs)
    have = {r["sub"] for r in rows}
    for s in subs:   # hələ oynamayanlar da siyahıda görünsün
        if s not in have:
            rows.append(dict(sub=s, w=0, l=0, d=0, pts=0, n=0, rank=len(rows) + 1))
    top = _decorate(rows, 50)
    for x in top:
        x["me"] = (x.pop("sub") == sub)
    return _nostore(jsonify(t=_t_json(t, sub), top=top))


@app.route("/api/tourney/create", methods=["POST"])
def api_tourney_create():
    sub, _ = auth_sub()
    if not rate_hit("tcreate", sub, 3, 3600):
        abort(429)
    d = request.get_json(silent=True) or {}
    game = str(d.get("game", ""))
    if game not in GAMES:
        abort(400)
    title = clean_text(d.get("title"), 40) or "Turnir"
    try:
        days = max(1, min(14, int(d.get("days") or 3)))
    except (TypeError, ValueError):
        days = 3
    now = time.time()
    if dbq("SELECT COUNT(*) AS c FROM tourneys WHERE owner=? AND t1>?", (sub, now), one=True)["c"] >= 3:
        return jsonify(error="Eyni anda ən çox 3 aktiv turnirin ola bilər"), 400
    dbq("INSERT INTO tourneys(owner,game,title,t0,t1,ts) VALUES(?,?,?,?,?,?)", (sub, game, title, now, now + days * 86400, now))
    tid = dbq("SELECT MAX(id) AS m FROM tourneys WHERE owner=?", (sub,), one=True)["m"]
    dbq("INSERT OR IGNORE INTO tourney_players(tid,sub,ts) VALUES(?,?,?)", (tid, sub, now))
    return jsonify(ok=True, id=tid)


@app.route("/api/tourney/join", methods=["POST"])
def api_tourney_join():
    sub, _ = auth_sub()
    if not rate_hit("tjoin", sub, 30, 600):
        abort(429)
    try:
        tid = int((request.get_json(silent=True) or {}).get("id"))
    except (TypeError, ValueError):
        abort(400)
    t = dbq("SELECT * FROM tourneys WHERE id=?", (tid,), one=True)
    if not t or time.time() >= t["t1"]:
        return jsonify(error="Turnir bitib"), 400
    if dbq("SELECT COUNT(*) AS c FROM tourney_players WHERE tid=?", (tid,), one=True)["c"] >= 200:
        return jsonify(error="Turnir doludur"), 400
    dbq("INSERT OR IGNORE INTO tourney_players(tid,sub,ts) VALUES(?,?,?)", (tid, sub, time.time()))
    return jsonify(ok=True)


@app.route("/api/tourney/leave", methods=["POST"])
def api_tourney_leave():
    sub, _ = auth_sub()
    try:
        tid = int((request.get_json(silent=True) or {}).get("id"))
    except (TypeError, ValueError):
        abort(400)
    t = dbq("SELECT * FROM tourneys WHERE id=?", (tid,), one=True)
    if t and time.time() < t["t1"]:
        dbq("DELETE FROM tourney_players WHERE tid=? AND sub=?", (tid, sub))
    return jsonify(ok=True)


@app.route("/api/tourney/delete", methods=["POST"])
def api_tourney_delete():
    sub, _ = auth_sub()
    try:
        tid = int((request.get_json(silent=True) or {}).get("id"))
    except (TypeError, ValueError):
        abort(400)
    if dbq("SELECT 1 FROM tourneys WHERE id=? AND owner=?", (tid, sub), one=True):
        dbq("DELETE FROM tourney_players WHERE tid=?", (tid,))
        dbq("DELETE FROM tourneys WHERE id=?", (tid,))
    return jsonify(ok=True)


# ---- Bəyəndiklərim (video siyahısı) ----
@app.route("/api/favs")
def api_favs():
    sub, _ = auth_sub()
    rows = dbq("SELECT url,title,thumb,ts FROM favs WHERE sub=? ORDER BY ts DESC LIMIT 200", (sub,), many=True)
    return _nostore(jsonify(items=[dict(r) for r in rows]))


@app.route("/api/fav/add", methods=["POST"])
def api_fav_add():
    sub, _ = auth_sub()
    if not rate_hit("fav", sub, 60, 600):
        abort(429)
    d = request.get_json(silent=True) or {}
    url = str(d.get("url") or "")[:1000]
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        abort(400)
    if dbq("SELECT COUNT(*) AS c FROM favs WHERE sub=?", (sub,), one=True)["c"] >= 200:
        return jsonify(error="Siyahı doludur (200)"), 400
    vid = yt_id(url)
    title = clean_text(d.get("title"), 120) or ((yt_title(vid) if vid else "") or p.hostname)
    thumb = "https://i.ytimg.com/vi/" + vid + "/mqdefault.jpg" if vid else ""
    dbq("INSERT OR REPLACE INTO favs(sub,url,title,thumb,ts) VALUES(?,?,?,?,?)", (sub, url, title, thumb, time.time()))
    return jsonify(ok=True)


@app.route("/api/fav/remove", methods=["POST"])
def api_fav_remove():
    sub, _ = auth_sub()
    url = str((request.get_json(silent=True) or {}).get("url") or "")[:1000]
    dbq("DELETE FROM favs WHERE sub=? AND url=?", (sub, url))
    return jsonify(ok=True)


# ---- Ümumi rate limit (yazan sorğular üçün IP başına; polling və axınlara toxunmur) ----
RL_RULES = (("/api/upload", 20, 3600), ("/api/dm/media", 60, 3600), ("/api/report", 20, 3600),
            ("/api/friend/request", 40, 600), ("/api/invite", 40, 600), ("/api/fav/", 90, 600),
            ("/api/tourney", 60, 600), ("/api/leaderboard", 120, 60))
RL_SKIP = ("/poll", "/stream", "/presence", "/check", "/api/gvoice", "/api/domino/move", "/api/chess/move",
           "/api/durak/move", "/api/mafia/act", "/api/mafia/chat", "/api/durak/bot", "/api/domino/claim",
           "/api/uno/move", "/api/uno/bot")


@app.before_request
def rate_limit_gate():
    p = request.path
    if not p.startswith("/api/"):
        return None
    if p == "/api/send":   # hesab başına (token), tokensiz qonaq üçün IP başına
        key = request.headers.get("X-Token", "")[:24] or client_ip()
        if not rate_hit("rl:send", key, 300, 60):
            return jsonify(error="Çox sorğu. Bir az gözlə."), 429
        return None
    if any(x in p for x in RL_SKIP):
        return None
    ip = client_ip()
    for pre, lim, win in RL_RULES:
        if p.startswith(pre):
            if not rate_hit("rl:" + pre, ip, lim, win):
                return jsonify(error="Çox sorğu. Bir az gözlə."), 429
            break
    if request.method == "POST" and not rate_hit("rl:post", ip, 300, 60):
        return jsonify(error="Çox sorğu. Bir az gözlə."), 429
    return None


# ---- SQLite ehtiyat nüsxəsi: tutarlı snapshot (backup API), dövri + admin paneldən ----
def make_backup(path=None):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    path = path or os.path.join(BACKUP_DIR, time.strftime("wt-%Y%m%d-%H%M%S.db", time.gmtime()))
    with DBL:
        src = sqlite3.connect(DB_PATH, timeout=10)
        dst = sqlite3.connect(path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
    return path


def list_backups():
    try:
        fs = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith("wt-") and f.endswith(".db"))
    except OSError:
        return []
    return fs


def prune_backups():
    fs = list_backups()
    for f in fs[:-BACKUP_KEEP]:
        try:
            os.remove(os.path.join(BACKUP_DIR, f))
        except OSError:
            pass


def _backup_loop():
    time.sleep(60)
    while True:
        try:
            make_backup()
            prune_backups()
        except Exception:
            pass
        time.sleep(BACKUP_HOURS * 3600)


if not os.environ.get("NO_BACKUP_THREAD"):
    threading.Thread(target=_backup_loop, daemon=True).start()


def _valid_db(path):
    try:
        c = sqlite3.connect(path)
        try:
            ok = c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            has = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
            return bool(ok and has)
        finally:
            c.close()
    except Exception:
        return False


@app.route("/api/admin/backup", methods=["GET", "POST"])
def admin_backup():
    admin_guard()
    if request.method == "POST":
        try:
            make_backup()
            prune_backups()
        except Exception:
            abort(500)
    fs = list_backups()[::-1]
    return _nostore(jsonify(items=[dict(name=f, size=os.path.getsize(os.path.join(BACKUP_DIR, f)),
                                         ts=os.path.getmtime(os.path.join(BACKUP_DIR, f))) for f in fs]))


@app.route("/api/admin/backup/download")
def admin_backup_download():
    """?name=... saxlanmış nüsxə; adsız: indiki bazanın təzə snapshot-ı (Render-də diski itirsən də səndə qalır)."""
    admin_guard()
    name = request.args.get("name", "")
    if name:
        if name not in list_backups():
            abort(404)
        return send_file(os.path.join(BACKUP_DIR, name), as_attachment=True, download_name=name)
    tmp = os.path.join(BACKUP_DIR, "tmp-dl.db")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    if os.path.exists(tmp):
        os.remove(tmp)
    make_backup(tmp)
    resp = send_file(tmp, as_attachment=True, download_name=time.strftime("wt-%Y%m%d-%H%M%S.db", time.gmtime()))
    return resp


@app.route("/api/admin/backup/restore", methods=["POST"])
def admin_backup_restore():
    """Yüklənən .db faylından bazanı bərpa edir (əvvəlcə cari bazanın nüsxəsi alınır)."""
    admin_guard()
    f = request.files.get("file")
    if not f:
        abort(400)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    tmp = os.path.join(BACKUP_DIR, "tmp-upload.db")
    f.save(tmp)
    if not _valid_db(tmp):
        os.remove(tmp)
        return jsonify(error="Fayl düzgün SQLite bazası deyil"), 400
    try:
        make_backup(os.path.join(BACKUP_DIR, time.strftime("wt-%Y%m%d-%H%M%S-before-restore.db", time.gmtime())))
        with DBL:
            src = sqlite3.connect(tmp)
            dst = sqlite3.connect(DB_PATH, timeout=10)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
        SESS_CACHE.clear()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    init_extra()
    return jsonify(ok=True)


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


# Real müştəri IP-si: müştərinin özünün göndərdiyi başlığa inanmırıq.
#   TRUSTED_PROXIES  - sizin önünüzdəki etibarlı proxy sayı (Render tək: 1, Cloudflare + Render: 2)
#   USE_CF_HEADER=1  - yalnız sayt həqiqətən Cloudflare arxasındadırsa CF-Connecting-IP-ə inan
try:
    TRUSTED_PROXIES = max(0, int(os.environ.get("TRUSTED_PROXIES", "1")))
except ValueError:
    TRUSTED_PROXIES = 1
USE_CF_HEADER = os.environ.get("USE_CF_HEADER") == "1"


def client_ip():
    h = request.headers
    ip = None
    if USE_CF_HEADER and h.get("CF-Connecting-IP"):
        ip = h.get("CF-Connecting-IP").strip()
    elif TRUSTED_PROXIES:
        parts = [p.strip() for p in h.get("X-Forwarded-For", "").split(",") if p.strip()]
        if len(parts) >= TRUSTED_PROXIES:
            ip = parts[-TRUSTED_PROXIES]   # sağdan sayırıq: soldakı hissəni müştəri saxtalaşdıra bilər
    return (ip or request.remote_addr or "?")[:64]


_RATE_LAST_PURGE = [0.0]

def rate_hit(bucket, key, limit, window):
    """True: icazə var (və sayılır). False: limit aşılıb. Yaddaşda saxlanır.
    Köhnə bütün RATE.clear() əvəzinə vaxtı keçmiş qeydləri təmizləyir — limit bypass olmur."""
    now = time.time()
    k = bucket + "|" + str(key)
    # dövri təmizlik: 60 saniyədə bir, və ya 25k-dan çox olanda
    if len(RATE) > 25000 or (now - _RATE_LAST_PURGE[0] > 60 and len(RATE) > 2000):
        _RATE_LAST_PURGE[0] = now
        dead = []
        for rk, times in list(RATE.items()):
            alive = [t for t in times if now - t < 3600]
            if alive:
                RATE[rk] = alive
            else:
                dead.append(rk)
        for rk in dead:
            RATE.pop(rk, None)
        # hələ də çoxdursa ən köhnə açarları at (FIFO-ya yaxın)
        if len(RATE) > 20000:
            for rk in list(RATE.keys())[: len(RATE) - 15000]:
                RATE.pop(rk, None)
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

# ---- Bir dəfəlik (müvəqqəti) e-poçt domenləri. Siyahını genişləndirmək üçün:
#      pip install disposable-email-domains  (aşağıda avtomatik istifadə olunur)
#      və ya mühit dəyişəni: EXTRA_BLOCKED_DOMAINS="a.com,b.com"
DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "guerrillamail.net", "guerrillamail.org", "guerrillamail.biz",
    "guerrillamail.de", "guerrillamailblock.com", "sharklasers.com", "grr.la", "pokemail.net", "spam4.me",
    "10minutemail.com", "10minutemail.net", "10minutemail.org", "20minutemail.com", "tempmail.com",
    "temp-mail.org", "temp-mail.io", "tempmail.net", "tempmail.dev", "tempmailo.com", "tempail.com",
    "tempr.email", "temporary-mail.net", "throwawaymail.com", "trashmail.com", "trashmail.net", "trashmail.de",
    "trashmail.io", "trash-mail.com", "yopmail.com", "yopmail.net", "yopmail.fr", "cool.fr.nf", "jetable.org",
    "dispostable.com", "getnada.com", "nada.email", "maildrop.cc", "mailnesia.com", "mailcatch.com",
    "mailtemp.net", "mohmal.com", "emailondeck.com", "fakeinbox.com", "fakemail.net", "fakemailgenerator.com",
    "getairmail.com", "mintemail.com", "mytemp.email", "mytrashmail.com", "spambox.us", "spamgourmet.com",
    "spamex.com", "tempinbox.com", "burnermail.io", "inboxkitten.com", "emailfake.com", "email-fake.com",
    "crazymailing.com", "dropmail.me", "discard.email", "discardmail.com", "mailforspam.com", "moakt.com",
    "tmpmail.org", "tmpmail.net", "tmail.ws", "tmailor.com", "1secmail.com", "1secmail.net", "1secmail.org",
    "mail.tm", "mailpoof.com", "minuteinbox.com", "luxusmail.org", "linshiyouxiang.net", "bccto.me",
    "mailsac.com", "harakirimail.com", "armyspy.com", "cuvox.de", "dayrep.com", "einrot.com", "fleckens.hu",
    "gustr.com", "jourrapide.com", "rhyta.com", "superrito.com", "teleworm.us", "mvrht.net", "wegwerfmail.de",
    "byom.de", "spamfree24.org", "mailto.plus", "fexpost.com", "fexbox.org", "rover.info", "inpwa.com",
    "any.pink", "tempmailaddress.com", "emltmp.com", "smailpro.com", "internxt.com", "hi2.in", "vomoto.com",
}
try:  # quraşdırılıbsa tam siyahı (~5000+ domen)
    from disposable_email_domains import blocklist as _dd_blocklist
    DISPOSABLE_DOMAINS |= set(_dd_blocklist)
except Exception:
    pass
DISPOSABLE_DOMAINS |= {x.strip().lower() for x in os.environ.get("EXTRA_BLOCKED_DOMAINS", "").split(",") if x.strip()}


def is_disposable(email):
    """e-poçtun domeni (və ya üst domeni) bir dəfəlik siyahıdadırsa True."""
    dom = email.rsplit("@", 1)[-1].strip().lower().rstrip(".")
    parts = dom.split(".")
    return any(".".join(parts[i:]) in DISPOSABLE_DOMAINS for i in range(len(parts) - 1))


_MX_CACHE = {}   # domen -> (vaxt, qəbul edir?)


def domain_can_receive(email):
    """Domen mövcud deyilsə və ya poçt qəbul etmirsə False (MX, yoxdursa A/AAAA yoxlanır).
    pip install dnspython lazımdır. Quraşdırılmayıbsa və ya DNS xətası olarsa True qaytarır:
    yəni yalnız əminlik olanda rədd edir, real istifadəçini bloklamır."""
    dom = email.rsplit("@", 1)[-1].strip().lower().rstrip(".")
    now = time.time()
    c = _MX_CACHE.get(dom)
    if c and now - c[0] < 86400:
        return c[1]
    ok = True
    try:
        import dns.resolver
        res = dns.resolver.Resolver()
        res.lifetime = res.timeout = 3
        try:
            ans = res.resolve(dom, "MX")
            ok = any(str(r.exchange).rstrip(".") for r in ans)   # \".\" = null MX: poçt qəbul etmir
        except dns.resolver.NoAnswer:
            ok = False
            for rt in ("A", "AAAA"):
                try:
                    res.resolve(dom, rt)
                    ok = True
                    break
                except dns.resolver.NoAnswer:
                    continue
        except dns.resolver.NXDOMAIN:
            ok = False
    except Exception:
        ok = True
    if len(_MX_CACHE) > 5000:
        _MX_CACHE.clear()
    _MX_CACHE[dom] = (now, ok)
    return ok


def email_reject(email):
    """None = qəbul; 'disposable' = müvəqqəti poçt; 'domain' = belə poçt domeni yoxdur."""
    if is_disposable(email):
        return "disposable"
    if not domain_can_receive(email):
        return "domain"
    return None
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
        '<!DOCTYPE html><html lang="' + lang + '" style="background:#150a1d;"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light dark"><meta name="supported-color-schemes" content="light dark">'
        '<style>:root{color-scheme:light dark;supported-color-schemes:light dark}'
        'html,body{margin:0!important;padding:0!important;width:100%!important;min-width:100%!important;background:#150a1d!important}'
        '.gmail_default,u+.body{background:#150a1d!important;margin:0!important}</style>'
        '</head><body class="body" bgcolor="#150a1d" style="margin:0;padding:0;background:#150a1d;width:100%;min-width:100%;">'
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


# ---- Qonaq rejimi: hesabsız, müvəqqəti sessiya. Yalnız oyunlara icazə verilir ----
GUEST_PREFIX = "guest:"          # sub prefiksi (reytinqə düşmür)
GUEST_TOK = "gst."               # token prefiksi ('.' urlsafe əlifbada yoxdur, real tokenlə toqquşmur)
GUEST_TTL = 2 * 86400
GUEST_OK = ("/api/guest/", "/api/domino/", "/api/chess/", "/api/durak/", "/api/uno/", "/api/mafia/", "/api/gvoice/")


@app.route("/api/guest/start", methods=["POST"])
def api_guest_start():
    if not rate_hit("guest", client_ip(), 30, 600):
        return jsonify(error="Çox sorğu. Bir az gözlə."), 429
    name = clean_name((request.get_json(silent=True) or {}).get("name")) or "Qonaq"
    now = time.time()
    sub = GUEST_PREFIX + secrets.token_hex(8)
    tok = GUEST_TOK + secrets.token_urlsafe(24)
    old = now - GUEST_TTL
    dbq("DELETE FROM sessions WHERE sub LIKE 'guest:%' AND created<?", (old,))
    dbq("DELETE FROM users WHERE sub LIKE 'guest:%' AND created<?", (old,))
    dbq("INSERT INTO users(sub,name,created) VALUES(?,?,?)", (sub, name, now))
    dbq("INSERT INTO sessions(token,sub,created) VALUES(?,?,?)", (tok, sub, now))
    return jsonify(token=tok, name=name)


@app.before_request
def guest_fence():
    """Qonaq tokeni oyunlardan başqa heç bir API-yə (dost, mesaj, profil, yükləmə...) girə bilməz."""
    if not request.headers.get("X-Token", "").startswith(GUEST_TOK):
        return None
    p = request.path
    if p.startswith("/api/") and not p.startswith(GUEST_OK):
        return jsonify(error="guest"), 403
    return None


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
    dbq("UPDATE dm SET from_sub=? WHERE from_sub=?", (new, old))
    dbq("UPDATE dm SET to_sub=? WHERE to_sub=?", (new, old))
    dbq("DELETE FROM dm WHERE from_sub=to_sub")
    dbq("DELETE FROM users WHERE sub=?", (old,))


def too_many_fails(email):
    now = time.time()
    if len(LOGIN_FAILS) > 5000:
        # tam clear əvəzinə vaxtı keçmişləri sil
        for k in list(LOGIN_FAILS.keys()):
            L = [t for t in LOGIN_FAILS[k] if now - t < 600]
            if L:
                LOGIN_FAILS[k] = L
            else:
                LOGIN_FAILS.pop(k, None)
        if len(LOGIN_FAILS) > 4000:
            for k in list(LOGIN_FAILS.keys())[: len(LOGIN_FAILS) - 3000]:
                LOGIN_FAILS.pop(k, None)
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
    if is_disposable(email):
        return jsonify(error="disposable"), 400
    if not mail_ready():
        abort(503)
    ip = client_ip()
    if not rate_hit("reg", ip, 6, 600) or not rate_hit("regday", ip, 25, 86400):
        abort(429)
    bad = email_reject(email)
    if bad:
        return jsonify(error=bad), 400
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
    if too_many_fails(email) or not rate_hit("loginip", client_ip(), 40, 600):
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
    with META_LOCK:
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
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("X-Frame-Options", "SAMEORIGIN")
    h.setdefault("Permissions-Policy", "geolocation=(), payment=(), usb=()")
    if request.is_secure or request.headers.get("X-Forwarded-Proto", "") == "https":
        h.setdefault("Strict-Transport-Security", "max-age=31536000")
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
    # URL-də ?v=<vaxt> var: şəkil dəyişəndə link də dəyişir. Ona görə brauzer keşi təhlükəsizdir və şəkillər dərhal açılır.
    resp.headers["Cache-Control"] = "private, max-age=2592000, immutable"
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
    if is_disposable(email):
        return jsonify(error="disposable"), 400
    if not mail_ready():
        abort(503)
    mail_guard()
    bad = email_reject(email)
    if bad:
        return jsonify(error=bad), 400
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
    dbq("DELETE FROM dm WHERE from_sub=? OR to_sub=?", (sub, sub))
    dbq("DELETE FROM users WHERE sub=?", (sub,))
    LOGIN_FAILS.pop(key, None)
    return jsonify(ok=True)


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


# ---------------------------------------------------------------
#  Oyun dəvətləri (domino / şahmat): istifadəçi adı ilə və ya dost siyahısından
#  Yaddaşda saxlanır (oyunların özü kimi), 10 dəqiqə etibarlıdır.
# ---------------------------------------------------------------
GAME_INV = {}        # to_sub -> {(game, code, from_sub): dəvət}
GAME_INV_TTL = 600


def _game_store(kind):
    if kind == "durak":
        return DURAK
    if kind == "uno":
        return UNO
    if kind == "mafia":
        return MAFIA
    return DOMINO if kind == "domino" else CHESS


def _game_invites_for(sub):
    now = time.time()
    lst = GAME_INV.get(sub)
    if not lst:
        return []
    out = []
    with META_LOCK:
        for k, i in list(lst.items()):
            g = _game_store(i["game"]).get(i["room"])
            if (now - i["ts"] > GAME_INV_TTL or not g or g["status"] != "wait" or sub in g["players"]
                    or len(g["players"]) >= g.get("size", 2)):
                lst.pop(k, None)
                continue
            out.append(dict(id=i["id"], game=i["game"], room=i["room"], name=i["name"],
                            username=i["username"], avatar=i["avatar"], ts=i["ts"], voice=bool(i.get("voice"))))
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
    if kind not in ("domino", "chess", "durak", "mafia", "uno") or not username_valid(u) or not code:
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
    with META_LOCK:
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
        username=idn["username"], avatar=idn["avatar"], voice=bool(d.get("voice")))
    notify_user(row["sub"], "game", push=dict(title="Oyun dəvəti", body=idn["name"] + (" səni səsli söhbətə çağırır" if d.get("voice") else " səni oyuna çağırır"), tag="game", url="/"))
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
    row = dbq("SELECT seen, status FROM presence WHERE sub=?", (target,), one=True)
    if not row:
        return dict(presence="")
    seen = float(row["seen"])
    age = max(0, int(time.time() - seen))
    st = (row["status"] or "").strip()[:40]
    # 90 san: klient hər ~12-20 saniyədən bir presence göndərir
    if age < 90:
        label = st if st else "Onlayn"
        return dict(presence=label, online=True, status=st)
    if age < 3600:
        label = f"Son görülmə: {max(1, age // 60)} dəq. əvvəl"
    elif age < 86400:
        label = f"Son görülmə: {age // 3600} saat əvvəl"
    else:
        label = f"Son görülmə: {age // 86400} gün əvvəl"
    return dict(presence=label, online=False, status="")


@app.route("/api/presence", methods=["POST"])
def api_presence():
    sub, _ = auth_sub()
    now = time.time()
    d = request.get_json(silent=True) or {}
    st = re.sub(r"\s+", " ", str(d.get("status") or "")).strip()[:40]
    dbq("INSERT INTO presence(sub,seen,status) VALUES(?,?,?) ON CONFLICT(sub) DO UPDATE SET seen=excluded.seen, status=excluded.status",
        (sub, now, st or None))
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


@app.route("/api/report", methods=["POST"])
def api_report():
    sub, _ = auth_sub()
    d = request.get_json(silent=True) or {}
    if not rate_hit("report", sub, 10, 3600):
        return jsonify(error="rate"), 429
    other = _user_by_name(d.get("username"))
    if other == sub:
        return jsonify(error="self"), 400
    kind = "message" if d.get("kind") == "message" else "user"
    reason = str(d.get("reason") or "other")
    if reason not in ("spam", "abuse", "inappropriate", "impersonation", "other"):
        reason = "other"
    row = dbq("SELECT name,username FROM users WHERE sub=?", (other,), one=True)
    msg_text = clean_text(d.get("msg_text"), 500) if kind == "message" else ""
    dbq("INSERT INTO reports(ts,reporter,target,tname,tuser,kind,room,msg_id,msg_text,reason,details) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (time.time(), sub, other, row["name"] if row else "", row["username"] if row else "", kind,
         clean_text(d.get("room"), 40), clean_text(d.get("msg_id"), 60), msg_text, reason, clean_text(d.get("details"), 300)))
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
    friends.sort(key=lambda x: (0 if x.get("online") else 1, (x.get("name") or "").lower()))
    resp = jsonify(friends=friends, incoming=incoming, outgoing=outgoing)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/friend/request", methods=["POST"])
def api_friend_request():
    sub, tok = auth_sub()
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
        notify_user(other, "friend", push=dict(title="Yeni dost", body=ident_for(tok)["name"] + " dostluq sorğunu qəbul etdi", tag="friend", url="/"))
        return jsonify(ok=True, rel="friend")
    if dbq("SELECT COUNT(*) FROM friends WHERE a=? AND status=0", (sub,), one=True)[0] >= 50:
        abort(429)
    dbq("INSERT OR IGNORE INTO friends(a,b,status,ts) VALUES(?,?,0,?)", (sub, other, time.time()))
    notify_user(other, "friend", push=dict(title="Dostluq sorğusu", body=ident_for(tok)["name"] + " səni dost əlavə etmək istəyir", tag="friend", url="/"))
    return jsonify(ok=True, rel="out")


@app.route("/api/friend/respond", methods=["POST"])
def api_friend_respond():
    sub, tok = auth_sub()
    d = request.get_json(silent=True) or {}
    other = _user_by_name(d.get("username"))
    if _is_blocked_either(sub, other):
        abort(403)
    if d.get("accept"):
        dbq("UPDATE friends SET status=1, ts=? WHERE a=? AND b=? AND status=0", (time.time(), other, sub))
        notify_user(other, "friend", push=dict(title="Yeni dost", body=ident_for(tok)["name"] + " dostluq sorğunu qəbul etdi", tag="friend", url="/"))
    else:
        dbq("DELETE FROM friends WHERE a=? AND b=? AND status=0", (other, sub))
    return jsonify(ok=True)


@app.route("/api/friend/remove", methods=["POST"])
def api_friend_remove():
    sub, _ = auth_sub()
    other = _user_by_name((request.get_json(silent=True) or {}).get("username"))
    dbq("DELETE FROM friends WHERE (a=? AND b=?) OR (a=? AND b=?)", (sub, other, other, sub))
    return jsonify(ok=True)


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
DM_COLS = "id, from_sub, text, ts, read, delivered, kind, media, dur, reply_to"


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
    if r["reply_to"]:
        q = dbq("SELECT from_sub, text, kind FROM dm WHERE id=?", (r["reply_to"],), one=True)
        if q:
            m["reply"] = dict(id=r["reply_to"], mine=(q["from_sub"] == me),
                              text=_dm_preview(q["kind"] or "text", q["text"])[:80])
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


DM_TYPING = {}   # (yazan, alan) -> son "yazır" siqnalının vaxtı
_RKEY = []


def _reply_key():
    if _RKEY:
        return _RKEY[0]
    row = dbq("SELECT v FROM push_cfg WHERE k='replykey'", one=True)
    if not row:
        dbq("INSERT OR IGNORE INTO push_cfg(k,v) VALUES('replykey',?)", (secrets.token_hex(32),))
        row = dbq("SELECT v FROM push_cfg WHERE k='replykey'", one=True)
    _RKEY.append(row["v"].encode())
    return _RKEY[0]


def _reply_token(me, peer):
    """Bildirişdən cavab üçün imzalı bilet: yalnız 'me' adından 'peer'ə mətn göndərməyə yarayır, 24 saat etibarlıdır."""
    body = _b64u(json.dumps([me, peer, int(time.time()) + 86400], separators=(",", ":")).encode())
    return body + "." + hmac.new(_reply_key(), body.encode(), hashlib.sha256).hexdigest()[:32]


def _reply_parse(t):
    try:
        body, sig = str(t or "").split(".", 1)
        good = hmac.new(_reply_key(), body.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, good):
            return None
        me, peer, exp = json.loads(_b64d(body))
        if int(exp) < time.time():
            return None
        return str(me), str(peer)
    except Exception:
        return None


def _dm_push(to_sub, frm_sub, idn, body):
    u = str(idn["username"])
    return dict(title=idn["name"], body=body, tag="dm-" + u, url="/?dm=" + u, u=u, rt=_reply_token(to_sub, frm_sub))


def _dm_reply_id(sub, other, raw):
    """Yanıt verilən mesaj yalnız bu iki nəfərin yazışmasından olmalıdır (zəng qeydinə yanıt verilmir)."""
    try:
        rid = int(raw)
    except (TypeError, ValueError):
        return None
    q = dbq("SELECT from_sub, to_sub, kind FROM dm WHERE id=?", (rid,), one=True)
    if not q or {q["from_sub"], q["to_sub"]} != {sub, other} or (q["kind"] or "text") == "call":
        return None
    return rid


def _dm_insert(frm, to, text, kind="text", media=None, dur=0, reply_to=None):
    DM_TYPING.pop((frm, to), None)
    dbq("INSERT INTO dm(from_sub,to_sub,text,ts,read,delivered,kind,media,dur,reply_to) VALUES(?,?,?,?,0,0,?,?,?,?)",
        (frm, to, text, time.time(), kind, media, int(dur or 0), reply_to))
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
    typing = (time.time() - DM_TYPING.get((other, sub), 0)) < 5
    resp = jsonify(msgs=msgs, seen_upto=int((st and st["seen"]) or 0), deliv_upto=int((st and st["dlv"]) or 0), typing=typing)
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
    row = _dm_insert(sub, other, text, reply_to=_dm_reply_id(sub, other, d.get("reply_to")))
    idn = ident_for(tok)
    notify_user(other, "dm", payload=dict(u=idn["username"]), push=_dm_push(other, sub, idn, text[:100]))
    return jsonify(msg=_dm_msg(row, sub))


@app.route("/api/dm/typing", methods=["POST"])
def api_dm_typing():
    sub, _ = auth_sub()
    other = _user_by_name((request.get_json(silent=True) or {}).get("to"))
    if other == sub:
        abort(400)
    if _dm_check_pair(sub, other) or not rate_hit("dmtyp", sub, 60, 60):
        return jsonify(ok=True)
    now = time.time()
    if len(DM_TYPING) > 3000:
        for k in [k for k, v in DM_TYPING.items() if now - v > 10]:
            DM_TYPING.pop(k, None)
    DM_TYPING[(sub, other)] = now
    return jsonify(ok=True)


@app.route("/api/dm/quickreply", methods=["POST"])
def api_dm_quickreply():
    """Bildirişdən cavab: service worker-də X-Token yoxdur, ona görə imzalı bilet ilə."""
    d = request.get_json(silent=True) or {}
    pr = _reply_parse(d.get("t"))
    if not pr:
        return jsonify(error="Bilet etibarsızdır və ya vaxtı bitib"), 401
    me, other = pr
    text = _dm_clean(d.get("text"))
    if not text:
        return jsonify(error="Mesaj boşdur"), 400
    bad = _dm_check_pair(me, other)
    if bad:
        return bad
    if not rate_hit("dm", me, 40, 60):
        return jsonify(error="Çox tez göndərirsən, bir az gözlə"), 429
    row = _dm_insert(me, other, text)
    dbq("UPDATE dm SET read=1, delivered=1 WHERE to_sub=? AND from_sub=? AND read=0", (me, other))
    u = dbq("SELECT name, username FROM users WHERE sub=?", (me,), one=True)
    if u and u["username"]:
        notify_user(other, "dm", payload=dict(u=u["username"]),
                    push=_dm_push(other, me, dict(name=u["name"], username=u["username"]), text[:100]))
    return jsonify(ok=True, msg=_dm_msg(row, me))


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
    notify_user(other, "dm", payload=dict(u=idn["username"]), push=_dm_push(other, sub, idn, _dm_preview(kind, "")))
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


# ---------------------------------------------------------------
#  DM səsli zəng: server yalnız siqnalı ötürür (WebRTC səsi birbaşa cihazlar arasında gedir)
# ---------------------------------------------------------------
CALL_TYPES = ("invite", "accept", "reject", "end", "offer", "answer")
CALL_LOCK = threading.Lock()
CALL_LOG = {}    # call_id -> dict(caller, callee, ts, ans)
CALL_PEND = {}   # callee sub -> dict(id, frm, ts)   (zəng səsləndiyi müddətdə)


def _call_label(secs):
    return "Zəng · %d:%02d" % (secs // 60, secs % 60)


@app.route("/api/call/signal", methods=["POST"])
def api_call_signal():
    sub, tok = auth_sub()
    d = request.get_json(silent=True) or {}
    other = _user_by_name(d.get("to"))
    if other == sub:
        abort(400)
    typ = str(d.get("type") or "")
    cid = re.sub(r"[^A-Za-z0-9_-]", "", str(d.get("id") or ""))[:24]
    if typ not in CALL_TYPES or not cid:
        abort(400)
    bad = _dm_check_pair(sub, other)
    if bad:
        return bad
    data = d.get("data")
    if data is not None and len(json.dumps(data)) > 24000:
        abort(413)
    if not rate_hit("callsig", sub, 120, 60):
        return jsonify(error="Çox sorğu"), 429
    idn = ident_for(tok)
    now = time.time()
    log_row = None
    with CALL_LOCK:
        if typ == "invite":
            if not rate_hit("callinv", sub, 12, 300):
                return jsonify(error="Çox zəng etdin, bir az gözlə"), 429
            for k in [k for k, v in CALL_LOG.items() if now - v["ts"] > 6 * 3600]:
                CALL_LOG.pop(k, None)
            CALL_LOG[cid] = dict(caller=sub, callee=other, ts=now, ans=0)
            CALL_PEND[other] = dict(id=cid, frm=sub, ts=now)
        else:
            rec = CALL_LOG.get(cid)
            if not rec or {rec["caller"], rec["callee"]} != {sub, other}:
                return jsonify(ok=True, gone=True)   # zəng artıq bitib
            if typ == "accept":
                rec["ans"] = now
            if typ in ("accept", "reject", "end"):
                p = CALL_PEND.get(rec["callee"])
                if p and p["id"] == cid:
                    CALL_PEND.pop(rec["callee"], None)
            if typ in ("reject", "end"):
                CALL_LOG.pop(cid, None)
                if rec["ans"]:
                    secs = int(now - rec["ans"])
                    log_row = (rec["caller"], rec["callee"], _call_label(secs), secs, False)
                elif typ == "end" and sub == rec["caller"]:
                    log_row = (rec["caller"], rec["callee"], "Cavabsız zəng", 0, True)
                elif isinstance(data, dict) and data.get("busy"):
                    log_row = (rec["caller"], rec["callee"], "Cavabsız zəng", 0, True)
                else:
                    log_row = (rec["caller"], rec["callee"], "Rədd edilmiş zəng", 0, False)
    payload = dict(type=typ, id=cid, u=idn["username"], name=idn["name"], data=data, ts=int(now * 1000))
    push = None
    if typ == "invite":
        u = dbq("SELECT (SELECT ts FROM avatars a WHERE a.sub=users.sub) AS av FROM users WHERE sub=?", (sub,), one=True)
        payload["av"] = _av_url(idn["username"], u["av"] if u else None)
        push = dict(title=idn["name"], body="\U0001F4DE Zəng edir...", tag="call-" + str(idn["username"]), url="/")
    notify_user(other, "call", payload=payload, push=push)
    if log_row:
        frm, to, label, secs, missed = log_row
        _dm_insert(frm, to, label, "call", None, secs)
        if missed:
            cu = dbq("SELECT name, username FROM users WHERE sub=?", (frm,), one=True)
            notify_user(to, "dm", payload=dict(u=(cu["username"] if cu else "")),
                        push=dict(title=(cu["name"] if cu else "Zəng"), body="\U0001F4DE Cavabsız zəng",
                                  tag="call-" + str(cu["username"] if cu else ""), url="/"))
    return jsonify(ok=True)


@app.route("/api/call/pending")
def api_call_pending():
    """Tətbiq arxa fonda olanda gəlmiş və hələ səslənən zəng (SSE yalnız ekran açıq olanda işləyir)."""
    sub, _ = auth_sub()
    with CALL_LOCK:
        p = CALL_PEND.get(sub)
        if p and time.time() - p["ts"] > 40:
            CALL_PEND.pop(sub, None)
            p = None
    if not p:
        return jsonify(call=None)
    u = dbq("SELECT name, username, (SELECT ts FROM avatars a WHERE a.sub=users.sub) AS av FROM users WHERE sub=?", (p["frm"],), one=True)
    if not u or not u["username"]:
        return jsonify(call=None)
    return jsonify(call=dict(id=p["id"], u=u["username"], name=u["name"], av=_av_url(u["username"], u["av"]), ts=int(p["ts"] * 1000)))


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
    r = get_room(code)
    with _ensure_cv(r):
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
            r["_cv"].wait(timeout=left)
        blank = dict(name="", username="", avatar="", badge=False, bio="")
        who = [dict(id=p, **r["who"].get(p, blank)) for p, ts in r["peers"].items()
               if p != pid and now - ts < PEER_TTL]
        roles = {p: role_of(r, p) for p in [pid] + [w["id"] for w in who]}
        resp = jsonify(seq=r["seq"], peers=n, msgs=[m[2] for m in new], who=who,
                       locked=r["locked"], epoch=r["created"], video=r.get("video"), vpos=vpos_now(r, now),
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
    with META_LOCK:
        r = ROOMS.get(code)
    if r is None:
        abort(404)
    with _ensure_cv(r):
        verify_pid(r, pid)
        if r["host"] != pid:
            abort(403)
        if target == pid or target not in r["members"]:
            abort(400)
        if role == "mod":
            r["roles"][target] = "mod"
        else:
            r["roles"].pop(target, None)
        r["rv"] += 1
        r["_cv"].notify_all()
        return jsonify(ok=True)


@app.route("/api/hostonly", methods=["POST"])
def api_hostonly():
    """\"Yalnız host idarə edir\" rejimi (host + moderatorlar idarə edir, izləyicilər yalnız baxır)."""
    d = request.get_json(silent=True) or {}
    code, pid = _room_ctx(d)
    with META_LOCK:
        r = ROOMS.get(code)
    if r is None:
        abort(404)
    with _ensure_cv(r):
        verify_pid(r, pid)
        if r["host"] != pid:
            abort(403)
        r["hostonly"] = bool(d.get("on"))
        r["rv"] += 1
        r["_cv"].notify_all()
        return jsonify(ok=True, hostonly=r["hostonly"])


@app.route("/api/host", methods=["POST"])
def api_host():
    """Host hostluğu otaqdakı başqa üzvə verir; özü hostdan çıxır (adi izləyici olur)."""
    d = request.get_json(silent=True) or {}
    code, pid = _room_ctx(d)
    target = str(d.get("target", ""))[:20]
    now = time.time()
    with META_LOCK:
        r = ROOMS.get(code)
    if r is None:
        abort(404)
    with _ensure_cv(r):
        verify_pid(r, pid)
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
        r["_cv"].notify_all()
        return jsonify(ok=True)


@app.route("/api/chat/history")
def api_chat_history():
    code = clean_room(request.args.get("room"))
    pid = str(request.args.get("id", ""))[:20]
    if not code or not pid:
        abort(400)
    with META_LOCK:
        r = ROOMS.get(code)
    if r is None:
        abort(404)
    with _ensure_cv(r):
        if pid not in r["members"]:
            abort(403)
        verify_pid(r, pid)
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
            with META_LOCK:
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
    with META_LOCK:
        code = p["room"] if p["room"] in ROOMS else None
        if code:
            r = ROOMS[code]
        else:
            code = _new_room(pid)
            r = ROOMS[code]
    with _ensure_cv(r):
        r["host"] = pid
        verify_pid(r, pid)
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
self.addEventListener('push',e=>{
  let d={};try{d=e.data.json();}catch(x){}
  e.waitUntil(clients.matchAll({type:'window',includeUncontrolled:true}).then(l=>{
    if(l.some(c=>c.visibilityState==='visible'))return;
    const o={body:d.body||'',icon:'/icon-192.png',badge:'/icon-192.png',tag:d.tag||'wt',renotify:true,data:{url:d.url||'/',u:d.u||'',rt:d.rt||''}};
    if(d.rt&&d.u)o.actions=[{action:'reply',type:'text',title:'Cavab ver',placeholder:'Mesaj yaz...'}];
    return self.registration.showNotification(d.title||'Birlikdə İzlə',o);
  }));
});
function openChat(dt,u){
  return clients.matchAll({type:'window',includeUncontrolled:true}).then(l=>{
    if(l.length){const c=l[0];if(dt.u)c.postMessage({type:'open-dm',u:dt.u});return c.focus();}
    return clients.openWindow(u);
  });
}
self.addEventListener('notificationclick',e=>{
  const n=e.notification,dt=n.data||{},u=dt.url||'/';
  n.close();
  if(e.action==='reply'&&e.reply&&dt.rt){
    e.waitUntil(fetch('/api/dm/quickreply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({t:dt.rt,text:e.reply})})
      .then(r=>{if(!r.ok)throw 0;return self.registration.showNotification(n.title,{body:'Sən: '+e.reply,icon:'/icon-192.png',badge:'/icon-192.png',tag:n.tag,silent:true,data:{url:u,u:dt.u||''}});})
      .catch(()=>openChat(dt,u)));
    return;
  }
  e.waitUntil(openChat(dt,u));
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
    q = request.args.get("q", "").strip()[:200]
    if not q:
        return jsonify([])
    if not rate_hit("yt", client_ip(), 30, 60):
        abort(429)
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
    except Exception:
        return jsonify(error="upstream"), 502

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


@app.route("/api/admin/reports")
def admin_reports():
    admin_guard()
    show = request.args.get("status")
    q = "SELECT r.*, u.name AS rname, u.username AS ruser FROM reports r LEFT JOIN users u ON u.sub=r.reporter"
    args = ()
    if show in ("0", "1"):
        q += " WHERE r.status=?"; args = (int(show),)
    rows = dbq(q + " ORDER BY r.status ASC, r.ts DESC LIMIT 500", args, many=True)
    return _nostore(jsonify(reports=[dict(
        id=r["id"], ts=r["ts"], kind=r["kind"], reason=r["reason"], details=r["details"] or "",
        room=r["room"] or "", msg_text=r["msg_text"] or "", status=r["status"],
        target=dict(name=r["tname"] or "", username=r["tuser"] or ""),
        reporter=dict(name=r["rname"] or "", username=r["ruser"] or "")) for r in rows],
        open=(dbq("SELECT COUNT(*) c FROM reports WHERE status=0", one=True) or {"c": 0})["c"]))


@app.route("/api/admin/report/resolve", methods=["POST"])
def admin_report_resolve():
    admin_guard()
    d = request.get_json(silent=True) or {}
    dbq("UPDATE reports SET status=? WHERE id=?", (0 if d.get("reopen") else 1, int(d.get("id") or 0)))
    return _nostore(jsonify(ok=True))


@app.route("/api/admin/report/delete", methods=["POST"])
def admin_report_delete():
    admin_guard()
    dbq("DELETE FROM reports WHERE id=?", (int((request.get_json(silent=True) or {}).get("id") or 0),))
    return _nostore(jsonify(ok=True))


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
    with META_LOCK:
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


_INDEX_GZ = {}


@app.route("/")
def index():
    log_visit()
    path = os.path.join(HERE, "index.html")
    if "gzip" in request.headers.get("Accept-Encoding", "").lower():
        try:
            mt = os.path.getmtime(path)
            ent = _INDEX_GZ.get("e")
            if not ent or ent[0] != mt:
                with open(path, "rb") as f:
                    ent = (mt, gzip.compress(f.read(), 6))
                _INDEX_GZ["e"] = ent
            resp = Response(ent[1], mimetype="text/html")
            resp.headers["Content-Encoding"] = "gzip"
            resp.headers["Vary"] = "Accept-Encoding"
            resp.headers["Content-Length"] = str(len(ent[1]))
            resp.set_etag("%s-gz" % int(mt))
            resp.headers["Cache-Control"] = "no-cache"
            return resp.make_conditional(request)
        except OSError:
            pass
    resp = send_file(os.path.join(HERE, "index.html"))
    resp.headers["Cache-Control"] = "no-cache"
    return resp



def fetch_safe(url, headers, hops=5):
    """Yönləndirmələri əl ilə izləyir: hər addım əvvəlcədən yoxlanır (SSRF qorunması)."""
    for _ in range(hops + 1):
        if not safe(url):
            abort(400)
        r = requests.get(url, headers=headers, stream=True, timeout=20, allow_redirects=False)
        # DNS rebinding: qoşulmuş real IP-ni yenidən yoxla
        try:
            peer = r.raw._connection.sock.getpeername()[0]
            pip = ipaddress.ip_address(peer)
            if pip.version == 6 and pip.ipv4_mapped:
                pip = pip.ipv4_mapped
            if not pip.is_global or pip.is_multicast:
                r.close()
                abort(400)
        except HTTPException:
            raise
        except Exception:
            pass   # sock əlçatan deyilsə (məs. keşlənmiş bağlantı) safe() yoxlaması qüvvədə qalır
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


PROXY_CSP = "sandbox allow-scripts allow-forms allow-presentation allow-popups"


def _proxy_headers(resp):
    # Proxy-dən gələn məzmun heç vaxt saytın origin-i kimi işləməsin (token/admin oğurluğunun qarşısı)
    resp.headers["Content-Security-Policy"] = PROXY_CSP
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@app.route("/p")
def proxy():
    if not rate_hit("proxy", client_ip(), 120, 60):
        abort(429)
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
        return _proxy_headers(Response(page.encode("utf-8"), content_type="text/html; charset=utf-8"))

    keep = ("content-type", "content-range", "accept-ranges")
    hdrs = {k: v for k, v in r.headers.items() if k.lower() in keep}
    if not r.headers.get("Content-Encoding") and r.headers.get("Content-Length"):
        hdrs["Content-Length"] = r.headers["Content-Length"]
    resp = Response(r.iter_content(65536), status=r.status_code, headers=hdrs)
    resp.call_on_close(r.close)  # bağlantı sızmasın
    return _proxy_headers(resp)


# ---------------------------------------------------------------
#  Domino otağı: 2-4 nəfərlik onlayn oyun (kompüterlə də 2-4) (standart 28 daş, 0-6).
#  Yaddaşda saxlanır, long-poll ilə yenilənir (hər oyunun öz _cv-si).
# ---------------------------------------------------------------
DOMINO = {}
DOM_IDLE = 3 * 3600          # bu qədər hərəkətsiz qalan oyun silinir
DOM_MAX = 200
DOM_TARGET = 101             # ilk bu xala çatan oyunu qazanır
DOM_CLAIM = 60               # rəqib bu qədər saniyə yoxdursa, oyunu bitirmək olar


def _dom_isbot(p):
    return isinstance(p, str) and p.startswith("bot")


def _dom_other(g, sub):
    return next((p for p in g["players"] if p != sub), None)


def _dom_team_mode(g):
    """4 nəfərlik oyun 2 komandaya bölünür: oturuş sırası 1-3 və 2-4 tərəfdaşdır (tərəfdaşlar üz-üzə oturur)."""
    return g.get("size", 2) == 4


def _dom_team(g, p):
    """Oyunçunun komandası (0/1). Oyun başlayanda g["team"] dondurulur; gözləmə zamanı oturuş sırasına görə."""
    t = g.get("team")
    if t and p in t:
        return t[p]
    ps = g["players"]
    return (ps.index(p) % 2) if p in ps else 0


def _dom_tscore(g, team):
    return g.setdefault("tscore", {0: 0, 1: 0}).get(team, 0)


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


DOM_SAME_MAX = 3   # bir əldə eyni rəqəmli daş bu qədərdən çox olmaz (məs. 5-1, 5-2, 5-3, 5-4 eyni vaxtda olmaz)


def _dom_hand_ok(hand):
    """Əldə eyni rəqəmi daşıyan daşların sayı DOM_SAME_MAX-dan çox deyilsə düzgündür (qoşa daş bir dəfə sayılır)."""
    cnt = [0] * 7
    for a, b in hand:
        cnt[a] += 1
        if b != a:
            cnt[b] += 1
    return max(cnt) <= DOM_SAME_MAX


def _dom_deal(g):
    prev = g.get("winner")      # əvvəlki raundun qalibi (ilk raundda və yeni oyunda None)
    tiles = [[a, b] for a in range(7) for b in range(a, 7)]
    ps = g["players"]
    rng = secrets.SystemRandom()
    for _ in range(1000):       # uyğun olmayan əl düşsə, daşlar yenidən qarışdırılır
        rng.shuffle(tiles)
        if all(_dom_hand_ok(tiles[i * 7:(i + 1) * 7]) for i in range(len(ps))):
            break
    g["hands"] = {p: sorted(tiles[i * 7:(i + 1) * 7]) for i, p in enumerate(ps)}
    if _dom_team_mode(g) and not g.get("team"):
        g["team"] = {p: i % 2 for i, p in enumerate(ps)}
        g["tscore"] = {0: 0, 1: 0}
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
    if winner and _dom_team_mode(g) and g.get("team"):
        # komanda oyunu: xal komandaya yazılır. Daşlar bitibsə rəqib komandanın əlindəki bütün daşların cəmi,
        # bloklanıbsa komandaların cəmləri arasındakı fərq (ən azı 1)
        wt = _dom_team(g, winner)
        mine = sum(_dom_sum(g, p) for p in g["players"] if _dom_team(g, p) == wt)
        theirs = sum(_dom_sum(g, p) for p in g["players"] if _dom_team(g, p) != wt)
        pts = theirs if reason == "out" else max(theirs - mine, 1)
        g["pts"] = pts
        ts = g.setdefault("tscore", {0: 0, 1: 0})
        ts[wt] = min(ts.get(wt, 0) + pts, DOM_TARGET)
        if ts[wt] >= DOM_TARGET:
            g["match"] = winner
        return
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
        if _dom_moves(g, g["hands"][cur]) or _dom_draw_n(g):
            return
        _dom_lack(g, cur)
        g["passes"] += 1
        g["note"] = {"who": cur, "k": "pass"}
        if g["passes"] >= n:
            sums = {p: _dom_sum(g, p) for p in g["players"]}
            if _dom_team_mode(g) and g.get("team"):
                tsum = {0: 0, 1: 0}
                for p in g["players"]:
                    tsum[_dom_team(g, p)] += sums[p]
                if tsum[0] == tsum[1]:
                    _dom_finish(g, None, "blocked")
                else:
                    wt = 0 if tsum[0] < tsum[1] else 1
                    mem = [p for p in g["players"] if _dom_team(g, p) == wt]
                    _dom_finish(g, min(mem, key=lambda p: sums[p]) if mem else None, "blocked")
                return
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
    elif _dom_team_mode(g) and g.get("team") and len({_dom_team(g, x) for x in g["players"]}) == 1:
        _dom_finish(g, g["players"][0], "left")      # bir komandada heç kim qalmadı
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


DOM_KEEP2 = 2    # 2 nəfərlik oyunda bazarda həmişə bu qədər daş qalır (çəkilmir)


def _dom_draw_n(g):
    """Bir dəfəyə çəkiləcək daş sayı. 2 nəfərdə: 2-2 çəkilir, bazarda 2 daş qalır. 3-4 nəfərdə: tək-tək, bazar tam boşalana qədər."""
    n = len(g["stock"])
    if g.get("size", 2) == 2:
        return max(0, min(2, n - DOM_KEEP2))
    return 1 if n else 0


def _dom_do_draw(g, who):
    hand = g["hands"][who]
    k = _dom_draw_n(g)
    has_mv = bool(_dom_moves(g, hand))
    if not k:                           # gedişin olsa belə bazara getmək olar (2, 3 nəfər; 4 nəfərdə bazar yoxdur)
        return False
    if not has_mv:
        _dom_lack(g, who)               # yalnız məcburən çəkəndə "bu rəqəmlər yoxdur" yadda saxlanır
    for _ in range(k):
        hand.append(g["stock"].pop())
    hand.sort()
    g["note"] = {"who": who, "k": "draw"}
    _dom_settle(g)
    return True


def _dom_lack(g, who):
    """Oyunçu bazardan daş çəkəndə/keçəndə açıq uclardakı rəqəmlər onda yox idi: kompüter bunu yadda saxlayır."""
    bd = g.get("board")
    if _dom_isbot(who) or not bd:
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
        while not opts and len(stock) > DOM_KEEP2:      # 2-2 çəkir, bazarda 2 daş qalır
            for _ in range(min(2, len(stock) - DOM_KEEP2)):
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


def _dom_bot_choose(g, mv, me="bot"):
    """Kompüterin gedişi: görünməyən daşların mümkün bölgüsünü təsadüfi seçib (rəqibin əlini DEYİL, yalnız
    saydığımız daşları və rəqibin çəkdiyi anları bilirik), hər variantı bu ssenarilərdə sona qədər oynayır
    və orta nəticəsi ən yaxşı olanı seçir."""
    hand = [tuple(t) for t in g["hands"][me]]
    bd = g["board"]
    opp = _dom_other(g, me)
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


def _dom_bot_decide(g, me, mv):
    """2 nəfərdə simulyasiyalı güclü seçim, 3-4 nəfərdə sadə qayda."""
    hand = g["hands"][me]
    if len(g["players"]) == 2:
        try:
            return _dom_bot_choose(g, mv, me)
        except Exception:
            pass
    return _dom_bot_pick_simple(g, hand, mv)


def _dom_bot_step(g):
    """Kompüterin bir hərəkəti: ən yaxşı daşı qoyur, yoxdursa bazardan çəkir."""
    me = g["turn"]
    hand = g["hands"][me]
    mv = _dom_moves(g, hand)
    if not mv:
        _dom_do_draw(g, me)
        return
    t, e = _dom_bot_decide(g, me, mv)
    _dom_do_play(g, me, t, e)


def _dom_touch(g):
    g["v"] += 1
    g["upd"] = time.time()
    rec_game("domino", g)
    _notify(g)


def _dom_view(g, sub):
    now = time.time()
    st = g["status"]
    ps = g["players"]
    i = ps.index(sub) if sub in ps else 0
    order = ps[i + 1:] + ps[:i]
    hand = [list(t) for t in g["hands"].get(sub, [])] if st != "wait" else []
    mv = _dom_moves(g, hand) if st == "play" and g["turn"] == sub else []
    opps = []
    tm = _dom_team_mode(g)
    for p in order:
        opps.append(dict(g["names"].get(p) or {}, n=len(g["hands"].get(p, [])),
                         online=bool(_dom_isbot(p) or now - g["seen"].get(p, 0) < 35),
                         score=(_dom_tscore(g, _dom_team(g, p)) if tm else g["score"].get(p, 0)),
                         mate=bool(tm and _dom_team(g, p) == _dom_team(g, sub)),
                         turn=bool(st == "play" and g["turn"] == p)))
    tn = g["names"].get(g["turn"]) if st == "play" and g["turn"] != sub else None
    out = dict(
        v=g["v"], code=g["code"], status=st, round=g["round"], board=g["board"], cx=g.get("cx"), hand=hand,
        moves=mv, stock=len(g["stock"]),
        can_draw=bool(st == "play" and g["turn"] == sub and _dom_draw_n(g)),
        turn=(("me" if g["turn"] == sub else "opp") if st == "play" else None),
        turn_name=(tn or {}).get("name") if tn else None,
        turn_online=bool(tn and (_dom_isbot(g["turn"]) or now - g["seen"].get(g["turn"], 0) < 35)),
        me=g["names"].get(sub), opps=opps, size=g.get("size", 2), joined=len(ps), host=bool(ps and ps[0] == sub),
        my_score=(_dom_tscore(g, _dom_team(g, sub)) if tm else g["score"].get(sub, 0)), teams=tm,
        last=({"t": g["last"]["t"]} if g["last"] else None), reason=g["reason"], target=DOM_TARGET, bot=bool(g.get("bot")),
    )
    out["vc"], out["vk"] = _gv_view("domino", g)
    if g["note"]:
        nn = g["names"].get(g["note"]["who"]) or {}
        out["note"] = {"mine": g["note"]["who"] == sub, "k": g["note"]["k"], "name": nn.get("name", "")}
    if st == "over":
        w = g["winner"]
        if tm and w:
            out["result"] = "me" if _dom_team(g, w) == _dom_team(g, sub) else "opp"
        else:
            out["result"] = "draw" if not w else ("me" if w == sub else "opp")
        out["winner_name"] = (g["names"].get(w) or {}).get("name", "") if w else ""
        out["pts"] = g.get("pts", 0)
        m = g.get("match")
        if tm and m:
            out["match"] = "me" if _dom_team(g, m) == _dom_team(g, sub) else "opp"
        else:
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
    nm = _dom_names(tok)
    now = time.time()
    with META_LOCK:
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
            for i in range(size - 1):          # seçilən say qədər oyunçu: 1 insan + (size-1) kompüter
                bid = "bot" if i == 0 else "bot%d" % (i + 1)
                g["players"].append(bid)
                g["names"][bid] = dict(name="Kompüter" if size == 2 else "Kompüter %d" % (i + 1),
                                       username=bid, avatar="", badge=None)
                g["seen"][bid] = now
            g["bot"] = True
            _dom_deal(g)
        _ensure_cv(g); DOMINO[c] = g
        return jsonify(_dom_view(g, sub))


@app.route("/api/domino/join", methods=["POST"])
def api_domino_join():
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    if not code:
        abort(400)
    nm = _dom_names(tok)
    now = time.time()
    with META_LOCK:
        g = DOMINO.get(code)
    with META_LOCK:
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
    g = _dom_get(code, sub)
    with _ensure_cv(g):
        now = time.time()
        if now - g["seen"].get(sub, 0) > 30:     # qayıdıb: rəqib "onlayn" görsün
            g["seen"][sub] = now
            _dom_touch(g)
        g["seen"][sub] = now
        while g["v"] == v and (vk is None or g.get("vk", 0) == vk):
            left = deadline - time.time()
            if left <= 0:
                break
            g["_cv"].wait(timeout=left)
            if DOMINO.get(code) is not g:
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
    with META_LOCK:
        g = DOMINO.get(code)
    with META_LOCK:
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
    g = _dom_get(code, sub)
    with _ensure_cv(g):
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
    g = _dom_get(code, sub)
    with _ensure_cv(g):
        g["seen"][sub] = time.time()
        try:
            rd = int(d.get("round", 0))
        except (TypeError, ValueError):
            rd = 0
        if g["status"] == "over" and rd == g["round"] and len(g["players"]) >= 2:
            g["round"] += 1
            if g.get("match"):                 # 101-ə çatılıb: yeni oyun, xal sıfırdan
                g["score"] = {}
                g["tscore"] = {0: 0, 1: 0}
                g["match"] = None
                g["winner"] = None             # yeni oyun yenə qoşa ilə başlayır
            _dom_deal(g)
            _dom_touch(g)
        return jsonify(_dom_view(g, sub))


@app.route("/api/domino/leave", methods=["POST"])
def api_domino_leave():
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    with META_LOCK:
        g = DOMINO.get(code)
    with META_LOCK:
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
    with META_LOCK:
        g = DOMINO.get(code)
    with META_LOCK:
        g = DOMINO.get(code)
        if not g or sub not in g["players"]:
            return jsonify(ok=True)
        st = g["status"]
        if g.get("bot") or len([p for p in g["players"] if not _dom_isbot(p)]) <= 1:
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
        self.bout_ranks.add(card[0])      # bağlayan kartın rəqəmi də masadadır: həmin rəqəmi əlavə etmək olar
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
        for _p in e.table:                # köhnə yazılmış oyunlarda bağlayan kartların rəqəmi də sayılsın
            e.bout_ranks.add(_p["a"][0])
            if _p["d"]:
                e.bout_ranks.add(_p["d"][0])
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
    rec_game("durak", g)
    _notify(g)
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
            _ensure_cv(g)
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
    with META_LOCK:
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
        _ensure_cv(g); DURAK[c] = g
        _du_save(g)                                 # yaradılan kimi bazaya yaz: deploy/restartdan sonra "Oyuna qayıt" işləsin
        return jsonify(_du_view(g, sub))


@app.route("/api/durak/join", methods=["POST"])
def api_durak_join():
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    if not code:
        abort(400)
    nm = _du_names(tok)
    now = time.time()
    with META_LOCK:
        g = DURAK.get(code)
    with META_LOCK:
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


@app.route("/api/durak/perevod", methods=["POST"])
def api_durak_perevod():
    """Perevod qaydası: yalnız otağı açan, oyun başlamazdan əvvəl dəyişə bilər."""
    sub, tok = auth_sub()
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    if not isinstance(d.get("perevod"), bool):
        abort(400)
    g = _du_get(code, sub)
    with _ensure_cv(g):
        g["seen"][sub] = time.time()
        if g["players"][0] != sub:
            return jsonify(error="Bunu yalnız otağı açan dəyişə bilər"), 403
        if g["status"] != "wait" or g.get("eng") is not None:
            return jsonify(error="Oyun artıq başlayıb"), 409
        if g["perevod"] != d["perevod"]:
            g["perevod"] = d["perevod"]
            _du_touch(g)
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
    g = _du_get(code, sub)
    with _ensure_cv(g):
        now = time.time()
        if now - g["seen"].get(sub, 0) > 30:
            g["seen"][sub] = now
            _du_touch(g)
        g["seen"][sub] = now
        while g["v"] == v and (vk is None or g.get("vk", 0) == vk):
            left = deadline - time.time()
            if left <= 0:
                break
            g["_cv"].wait(timeout=left)
            if DURAK.get(code) is not g:
                abort(404)
            g["seen"][sub] = time.time()
        resp = jsonify(_du_view(g, sub))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/durak/check")
def api_durak_check():
    sub, tok = auth_sub()
    code = clean_room(request.args.get("room"))
    with META_LOCK:
        g = DURAK.get(code)
    with META_LOCK:
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
    g = _du_get(code, sub)
    with _ensure_cv(g):
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
    g = _du_get(code, sub)
    with _ensure_cv(g):
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
    with META_LOCK:
        g = DURAK.get(code)
    with META_LOCK:
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
    with META_LOCK:
        g = DURAK.get(code)
    with META_LOCK:
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
    g = _du_get(code, sub)
    with _ensure_cv(g):
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


# ---------------------------------------------------------------
#  UNO: 2–4 nəfərlik onlayn kart oyunu (insanlarla və ya kompüterə qarşı).
#  Kartlar [rəng, dəyər]: rəng R/Y/G/B (qırmızı/sarı/yaşıl/mavi) və ya K (qara, joker).
#  Dəyər: 0-9, S (keç), V (istiqamət), D (+2), W (rəng seç), F (+4 və rəng seç).
#  Qaydalar: 7 kart paylanır; rəngə və ya dəyərə uyğun kart atılır; uyğun kart yoxdursa 1 kart çəkilir,
#  çəkilən kart uyğundursa atmaq və ya pas keçmək olar. +2/+4 yığılmır. Kartı ilk bitirən qalib olur.
# ---------------------------------------------------------------
UNO_COLORS = "RYGB"


class UnoEngine:
    def __init__(self, players, first=0):
        self.players = list(players)
        self.hands = {p: [] for p in self.players}
        self.deck = self._fresh()
        random.shuffle(self.deck)
        for _ in range(7):
            for p in self.players:
                self.hands[p].append(self.deck.pop())
        while True:                                   # başlanğıc kart yalnız rəqəmli ola bilər
            c = self.deck.pop()
            if c[0] != "K" and c[1].isdigit():
                break
            self.deck.insert(0, c)
        self.discard = [c]
        self.color = c[0]
        self.dir = 1
        self.turn = first % len(self.players)
        self.drawn = None
        self.over = False
        self.winner = None
        self.quitter = None
        self.seq = 0
        self.last = None

    @staticmethod
    def _fresh():
        d = []
        for c in UNO_COLORS:
            d.append([c, "0"])
            for v in "123456789SVD":
                d.append([c, v]); d.append([c, v])
        for _ in range(4):
            d.append(["K", "W"]); d.append(["K", "F"])
        return d

    # --- köməkçi ---
    def cur(self):
        return self.players[self.turn]

    def can(self, card):
        top = self.discard[-1]
        return card[0] == "K" or card[0] == self.color or card[1] == top[1]

    def _adv(self, k=1):
        self.turn = (self.turn + self.dir * k) % len(self.players)

    def _take(self, p, k):
        got = 0
        for _ in range(k):
            if not self.deck:
                if len(self.discard) <= 1:
                    break
                top = self.discard.pop()
                self.deck = self.discard
                self.discard = [top]
                random.shuffle(self.deck)
            self.hands[p].append(self.deck.pop())
            got += 1
        return got

    def _ev(self, t, p, **kw):
        self.seq += 1
        self.last = dict(k=self.seq, t=t, p=self.players.index(p), **kw)

    def _check(self, p):
        if self.over:
            raise GameError("Oyun bitib")
        if self.cur() != p:
            raise GameError("Növbə sənin deyil")

    # --- hərəkətlər ---
    def play(self, p, card, color=None):
        self._check(p)
        card = [str(card[0]), str(card[1])]
        hand = self.hands[p]
        if card not in hand:
            raise GameError("Bu kart səndə yoxdur")
        if self.drawn is not None and card != self.drawn:
            raise GameError("Çəkdiyin kartı at və ya pas keç")
        if not self.can(card):
            raise GameError("Bu kartı atmaq olmaz")
        if card[0] == "K" and color not in tuple(UNO_COLORS):
            raise GameError("Rəng seç")
        hand.remove(card)
        self.discard.append(card)
        self.color = color if card[0] == "K" else card[0]
        self.drawn = None
        n = len(self.players)
        fx, victim, took = "", -1, 0
        v = card[1]
        if not hand:
            self.over = True
            self.winner = p
            self._ev("play", p, card=card, color=self.color, fx="", victim=-1, took=0, uno=False)
            return
        if v == "V":
            if n == 2:
                fx = "skip"                           # 2 nəfərdə istiqamət dəyişmək = rəqibi keçmək
                self._adv(2)
            else:
                fx = "rev"
                self.dir *= -1
                self._adv(1)
        elif v == "S":
            fx = "skip"
            victim = (self.turn + self.dir) % n
            self._adv(2)
        elif v in ("D", "F"):
            victim = (self.turn + self.dir) % n
            took = self._take(self.players[victim], 2 if v == "D" else 4)
            fx = "d2" if v == "D" else "d4"
            self._adv(2)
        else:
            self._adv(1)
        self._ev("play", p, card=card, color=self.color, fx=fx, victim=victim, took=took, uno=(len(hand) == 1))

    def draw(self, p):
        self._check(p)
        if self.drawn is not None:
            raise GameError("Artıq kart çəkmisən")
        got = self._take(p, 1)
        if got and self.can(self.hands[p][-1]):
            self.drawn = list(self.hands[p][-1])
            self._ev("draw", p, n=1, playable=True)
        else:
            self._ev("draw", p, n=got, playable=False)
            self._adv(1)

    def pass_turn(self, p):
        self._check(p)
        if self.drawn is None:
            raise GameError("Əvvəl kart çək")
        self.drawn = None
        self._ev("pass", p)
        self._adv(1)

    def actions(self, p):
        if self.over or self.cur() != p:
            return dict(play=[], draw=False, pass_=False)
        if self.drawn is not None:
            return dict(play=[list(self.drawn)], draw=False, pass_=True)
        return dict(play=[c for c in self.hands[p] if self.can(c)], draw=True, pass_=False)

    # --- kompüter ---
    def bot_step(self, bots):
        p = self.cur()
        if self.over or p not in bots:
            return False
        hand = self.hands[p]
        if self.drawn is not None:
            c = self.drawn
            self.play(p, c, self._bot_color(hand, c))
            return True
        ok = [c for c in hand if self.can(c)]
        if not ok:
            self.draw(p)
            return True
        nxt = self.hands[self.players[(self.turn + self.dir) % len(self.players)]]
        def score(c):
            s = random.random()
            if c[0] == "K":
                s -= 2.0                                  # jokeri saxla
                if len(nxt) <= 2 or len(hand) <= 3:
                    s += 3.0
            elif c[1] in "SVD":
                s += 1.2 + (1.5 if len(nxt) <= 2 else 0)
            elif c[1].isdigit():
                s += int(c[1]) / 12.0
            return s
        c = max(ok, key=score)
        self.play(p, c, self._bot_color(hand, c))
        return True

    @staticmethod
    def _bot_color(hand, c):
        if c[0] != "K":
            return None
        cnt = {k: 0 for k in UNO_COLORS}
        for x in hand:
            if x[0] in cnt:
                cnt[x[0]] += 1
        best = max(cnt.values())
        return random.choice([k for k, n in cnt.items() if n == best])

    # --- yadda saxlama ---
    def to_dict(self):
        return dict(players=self.players, hands=self.hands, deck=self.deck, discard=self.discard, color=self.color,
                    dir=self.dir, turn=self.turn, drawn=self.drawn, over=self.over, winner=self.winner,
                    quitter=self.quitter, seq=self.seq, last=self.last)

    @classmethod
    def from_dict(cls, d):
        e = cls.__new__(cls)
        for k, v in d.items():
            setattr(e, k, v)
        return e


UNO = {}
UNO_IDLE = 3 * 3600
UNO_MAX = 200


def _un_touch(g):
    g["v"] += 1
    g["upd"] = time.time()
    rec_game("uno", g)
    _notify(g)
    _un_save(g)


def _un_db_init():
    dbq("CREATE TABLE IF NOT EXISTS uno_games(code TEXT PRIMARY KEY, data TEXT NOT NULL, ts REAL NOT NULL)")


def _un_save(g):
    try:
        d = {k: v for k, v in g.items() if k not in ("eng", "seen")}
        d["eng"] = g["eng"].to_dict() if g.get("eng") else None
        dbq("INSERT OR REPLACE INTO uno_games(code, data, ts) VALUES(?,?,?)",
            (g["code"], json.dumps(d, separators=(",", ":"), default=str), g["upd"]))
    except Exception:
        pass


def _un_drop(code):
    UNO.pop(code, None)
    try:
        dbq("DELETE FROM uno_games WHERE code=?", (code,))
    except Exception:
        pass


def _un_load():
    now = time.time()
    try:
        _un_db_init()
        dbq("DELETE FROM uno_games WHERE ts<?", (now - UNO_IDLE,))
        rows = dbq("SELECT code, data FROM uno_games", many=True) or []
    except Exception:
        return
    for r in rows:
        try:
            g = json.loads(r["data"])
            g["eng"] = UnoEngine.from_dict(g["eng"]) if g.get("eng") else None
            bots = g.get("bots") or []
            g["seen"] = {p: (now if p in bots else 0) for p in g["players"]}
            g["upd"] = now
            g.setdefault("vk", 0)
            g.setdefault("quit", None)
            _ensure_cv(g)
            UNO[g["code"]] = g
        except Exception:
            continue


def _un_cleanup(now):
    for c in [c for c, g in UNO.items() if now - g["upd"] > UNO_IDLE]:
        _un_drop(c)


def _un_get(code, sub):
    g = UNO.get(code)
    if not g or sub not in g["players"]:
        abort(404)
    return g


def _un_start(g):
    first = (g["round"] - 1) % max(1, len(g["players"]))
    g["eng"] = UnoEngine(list(g["players"]), first)
    g["status"] = "play"


def _un_view(g, sub):
    now = time.time()
    ps = g["players"]
    eng = g["eng"]
    bots = g.get("bots") or ()
    out = dict(v=g["v"], code=g["code"], status=g["status"], size=g["size"], joined=len(ps), round=g["round"],
               host=bool(ps and ps[0] == sub), me=ps.index(sub) if sub in ps else -1, hand=[], top=None, color="",
               dir=1, turn=-1, deck=0, playable=[], can_draw=False, can_pass=False, drawn=None, last=None, quit=None)
    if eng:
        act = eng.actions(sub)
        out.update(hand=eng.hands.get(sub, []), top=eng.discard[-1], color=eng.color, dir=eng.dir,
                   turn=eng.turn if not eng.over else -1, deck=len(eng.deck), playable=act["play"],
                   can_draw=act["draw"], can_pass=act["pass_"], drawn=eng.drawn if eng.cur() == sub else None,
                   last=eng.last)
        if eng.over:
            out["over"] = True
            out["winner"] = ps.index(eng.winner) if eng.winner in ps else -1
            out["loser"] = ps.index(eng.quitter) if eng.quitter in ps else -1
    players = []
    for i, p in enumerate(ps):
        nm = g["names"].get(p) or {}
        players.append(dict(name=nm.get("name", ""), avatar=nm.get("avatar", ""), username=nm.get("username", ""),
                            me=(p == sub), online=bool(p in bots or now - g["seen"].get(p, 0) < 35),
                            n=len(eng.hands.get(p, [])) if eng else 0))
    out["players"] = players
    if g.get("quit"):
        out["quit"] = ps.index(g["quit"]) if g["quit"] in ps else None
    if g.get("bot"):
        out["bot"] = True
        out["botturn"] = bool(eng and not eng.over and g["status"] == "play" and eng.cur() in bots)
    out["vc"], out["vk"] = _gv_view("uno", g)
    return out


@app.route("/api/uno/create", methods=["POST"])
def api_uno_create():
    sub, tok = auth_sub()
    if not rate_hit("unoc", sub, 12, 60):
        abort(429)
    d0 = request.get_json(silent=True) or {}
    try:
        size = max(2, min(4, int(d0.get("size") or 2)))
    except (TypeError, ValueError):
        size = 2
    vs_bot = bool(d0.get("bot"))
    nm = _du_names(tok)
    now = time.time()
    with META_LOCK:
        _un_cleanup(now)
        if len(UNO) >= UNO_MAX:
            abort(503)
        for _ in range(50):
            c = "".join(secrets.choice(CODE_CHARS) for _ in range(5))
            if c not in UNO and c not in DURAK and c not in DOMINO and c not in CHESS:
                break
        else:
            abort(503)
        g = dict(code=c, players=[sub], names={sub: nm}, seen={sub: now}, size=size, status="wait", eng=None,
                 v=1, vk=0, upd=now, round=1, quit=None)
        if vs_bot:
            bots = ["bot%d" % i for i in range(1, size)]
            for i, b in enumerate(bots, 1):
                g["players"].append(b)
                g["names"][b] = dict(name=("Bot %d" % i) if size > 2 else "Kompüter", username="bot", avatar="", badge=None)
                g["seen"][b] = now
            g["bot"] = True
            g["bots"] = bots
            _un_start(g)
        _ensure_cv(g); UNO[c] = g
        _un_save(g)
        return jsonify(_un_view(g, sub))


@app.route("/api/uno/join", methods=["POST"])
def api_uno_join():
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    if not code:
        abort(400)
    nm = _du_names(tok)
    now = time.time()
    with META_LOCK:
        g = UNO.get(code)
        if not g:
            abort(404)
        if sub not in g["players"]:
            if len(g["players"]) >= g["size"] or g["status"] != "wait":
                return jsonify(error="full"), 403
            g["players"].append(sub)
            g["names"][sub] = nm
            g["seen"][sub] = now
            if len(g["players"]) >= g["size"]:
                _un_start(g)
            _un_touch(g)
        else:
            g["names"][sub] = nm
        g["seen"][sub] = now
        return jsonify(_un_view(g, sub))


@app.route("/api/uno/poll")
def api_uno_poll():
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
    g = _un_get(code, sub)
    with _ensure_cv(g):
        now = time.time()
        if now - g["seen"].get(sub, 0) > 30:
            g["seen"][sub] = now
            _un_touch(g)
        g["seen"][sub] = now
        while g["v"] == v and (vk is None or g.get("vk", 0) == vk):
            left = deadline - time.time()
            if left <= 0:
                break
            g["_cv"].wait(timeout=left)
            if UNO.get(code) is not g:
                abort(404)
            g["seen"][sub] = time.time()
        resp = jsonify(_un_view(g, sub))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/uno/check")
def api_uno_check():
    sub, tok = auth_sub()
    code = clean_room(request.args.get("room"))
    with META_LOCK:
        g = UNO.get(code)
        if not g or sub not in g["players"]:
            abort(404)
        return jsonify(ok=True, status=g["status"])


@app.route("/api/uno/move", methods=["POST"])
def api_uno_move():
    sub, tok = auth_sub()
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    act = str(d.get("action", ""))
    g = _un_get(code, sub)
    with _ensure_cv(g):
        g["seen"][sub] = time.time()
        eng = g["eng"]
        if g["status"] != "play" or eng is None:
            return jsonify(_un_view(g, sub))
        try:
            if act == "play":
                c = d.get("card")
                col = d.get("color")
                eng.play(sub, [str(c[0]), str(c[1])], str(col) if col else None)
            elif act == "draw":
                eng.draw(sub)
            elif act == "pass":
                eng.pass_turn(sub)
            else:
                abort(400)
        except GameError as e:
            return jsonify(error=str(e)), 400
        except (TypeError, ValueError, IndexError, KeyError):
            abort(400)
        if eng.over:
            g["status"] = "over"
        _un_touch(g)
        return jsonify(_un_view(g, sub))


@app.route("/api/uno/again", methods=["POST"])
def api_uno_again():
    sub, tok = auth_sub()
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    g = _un_get(code, sub)
    with _ensure_cv(g):
        g["seen"][sub] = time.time()
        try:
            rd = int(d.get("round", 0))
        except (TypeError, ValueError):
            rd = 0
        if g["status"] == "over" and rd == g["round"]:
            g["round"] += 1
            q = g.get("quit")
            if q:
                g["quit"] = None
                if q in g["players"]:
                    g["players"].remove(q)
                g["names"].pop(q, None)
                g["seen"].pop(q, None)
                g["eng"] = None
                g["status"] = "wait"
            elif len(g["players"]) == g["size"]:
                _un_start(g)
            else:
                g["eng"] = None
                g["status"] = "wait"
            _un_touch(g)
        return jsonify(_un_view(g, sub))


@app.route("/api/uno/leave", methods=["POST"])
def api_uno_leave():
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    with META_LOCK:
        g = UNO.get(code)
        if g and sub in g["players"] and g["status"] == "wait":
            if g["players"][0] == sub:
                _un_drop(code)
            else:
                g["players"].remove(sub)
                g["names"].pop(sub, None)
                g["seen"].pop(sub, None)
                _un_touch(g)
        elif g and sub in g["players"]:
            g["seen"][sub] = 0
            _un_touch(g)
    return jsonify(ok=True)


@app.route("/api/uno/quit", methods=["POST"])
def api_uno_quit():
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    with META_LOCK:
        g = UNO.get(code)
        if not g or sub not in g["players"]:
            return jsonify(ok=True)
        if g.get("bot"):
            _un_drop(code)
            return jsonify(ok=True)
        if g["status"] == "wait":
            if g["players"][0] == sub:
                _un_drop(code)
                return jsonify(ok=True)
            g["players"].remove(sub)
            g["names"].pop(sub, None)
            g["seen"].pop(sub, None)
        elif g["status"] == "play" and g["eng"] and not g["eng"].over:
            eng = g["eng"]
            eng.over = True
            eng.winner = None
            eng.quitter = sub
            g["status"] = "over"
            g["quit"] = sub
        elif g["status"] == "over" and not g.get("quit"):
            g["quit"] = sub
        _un_touch(g)
    return jsonify(ok=True)


@app.route("/api/uno/bot", methods=["POST"])
def api_uno_bot():
    """Kompüterin bir hərəkəti. Klient növbə kompüterdə olanda çağırır (botturn=true)."""
    sub, tok = auth_sub()
    if not rate_hit("unob", sub, 300, 60):
        abort(429)
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    g = _un_get(code, sub)
    with _ensure_cv(g):
        g["seen"][sub] = time.time()
        eng = g["eng"]
        if not g.get("bot") or g["status"] != "play" or eng is None:
            return jsonify(_un_view(g, sub))
        try:
            eng.bot_step(set(g.get("bots") or ()))
        except GameError:
            pass
        if eng.over:
            g["status"] = "over"
        _un_touch(g)
        return jsonify(_un_view(g, sub))


_un_load()



#  Oyun səsli otağı (domino / şahmat): WebRTC siqnalı. Səsin özü cihazlar arasında gedir,
#  server yalnız offer/answer/ice mesajlarını ötürür. Domino-da 4 nəfərə qədər (hər kəs hər kəslə).
#  Oyunun öz dict-indən ayrı saxlanır (kompüter hərəkəti deepcopy edir, SDP mesajları ora düşməsin).
# ---------------------------------------------------------------
GV = {}              # (oyun, kod) -> {"mem": {vid: {name, muted, ts}}, "q": [mesajlar], "s": say}
GV_TTL = 45          # long-poll bu müddətdən uzun yoxdursa, iştirakçı "səsdə" sayılmır
GV_KINDS = ("domino", "chess", "durak", "mafia", "uno")
GV_SIG = ("offer", "answer", "ice")


def _gv_id(code, sub):
    """Oyunçunun səs otağındakı açıq id-si (həqiqi hesab id-si başqalarına göndərilmir)."""
    return hashlib.sha1(("gv:%s:%s" % (code, sub)).encode()).hexdigest()[:10]


def _gv_data(kind, code):
    if len(GV) > 400:   # bitmiş oyunların səs məlumatını at
        for k in [k for k in GV if k[1] not in _game_store(k[0])]:
            GV.pop(k, None)
    v = GV.get((kind, code))
    if v is None:
        v = {"mem": {}, "q": [], "s": 0, "_cv": threading.Condition()}
        GV[(kind, code)] = v
    else:
        _ensure_cv(v)
    return v


def _gv_view(kind, g):
    """Oyun vəziyyətinə əlavə: hazırda səs otağında olanlar və dəyişiklik sayğacı."""
    d = GV.get((kind, g["code"]))
    now = time.time()
    vc = [dict(id=i, name=m["name"], muted=bool(m.get("muted"))) for i, m in d["mem"].items() if now - m["ts"] < GV_TTL] if d else []
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
    g = _gv_game(kind, code, sub)
    with _ensure_cv(g):
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
            g["vk"] = g.get("vk", 0) + 1
        v["s"] += 1
        v["q"].append(dict(s=v["s"], f=me, to=to, k=k, d=payload, n=nm))
        del v["q"][:-300]
        _notify(v)
        _notify(g)   # oyun long-poll-u dərhal oyansın: səsli otağa giriş/çıxış, mikrofon dəyişikliyi qarşı tərəfdə gecikmədən görünsün
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
    _gv_game(kind, code, sub)
    v = _gv_data(kind, code)
    with _ensure_cv(v):
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
            v["_cv"].wait(timeout=left)
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
    rec_game("chess", g)


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


def _ch_touch(g):
    g["v"] += 1
    g["upd"] = time.time()
    rec_game("chess", g)
    _notify(g)


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
    with META_LOCK:
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
        _ensure_cv(g); CHESS[c] = g
        return jsonify(_ch_view(g, sub))


@app.route("/api/chess/join", methods=["POST"])
def api_chess_join():
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    if not code:
        abort(400)
    nm = _dom_names(tok)
    now = time.time()
    with META_LOCK:
        g = CHESS.get(code)
    with META_LOCK:
        g = CHESS.get(code)
        if not g:
            abort(404)
        if sub not in g["players"]:
            if len(g["players"]) >= 2:
                return jsonify(error="full"), 403
            g["players"].append(sub)
            g["names"][sub] = nm
            _ch_reset(g)
            _ch_touch(g)
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
    g = _ch_get(code, sub)
    with _ensure_cv(g):
        now = time.time()
        if now - g["seen"].get(sub, 0) > 30:
            g["seen"][sub] = now
            _ch_touch(g)
        g["seen"][sub] = now
        while g["v"] == v and (vk is None or g.get("vk", 0) == vk):
            left = deadline - time.time()
            if left <= 0:
                break
            g["_cv"].wait(timeout=left)
            if CHESS.get(code) is not g:
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
    with META_LOCK:
        g = CHESS.get(code)
    with META_LOCK:
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
    g = _ch_get(code, sub)
    with _ensure_cv(g):
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
        _ch_touch(g)
        return jsonify(_ch_view(g, sub))


@app.route("/api/chess/again", methods=["POST"])
def api_chess_again():
    sub, tok = auth_sub()
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    g = _ch_get(code, sub)
    with _ensure_cv(g):
        g["seen"][sub] = time.time()
        try:
            rd = int(d.get("round", 0))
        except (TypeError, ValueError):
            rd = 0
        if g["status"] == "over" and rd == g["round"] and len(g["players"]) == 2:
            g["round"] += 1
            _ch_reset(g, swap=True)
            _ch_touch(g)
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
    g = _ch_get(code, sub)
    with _ensure_cv(g):
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
    with META_LOCK:
        g = CHESS.get(code)
    with META_LOCK:
        g = CHESS.get(code)
        if not g or sub not in g["players"]:
            abort(404)
        if g["v"] == v0 and g["status"] == "play" and g["turn"] == g["col"].get("bot"):
            legal = _ch_legal(g)
            if legal:
                if mv is None or tuple(mv) not in legal:
                    mv = random.choice(legal)
                _ch_play(g, mv[0], mv[1], "Q")
                _ch_touch(g)
        return jsonify(_ch_view(g, sub))


@app.route("/api/chess/leave", methods=["POST"])
def api_chess_leave():
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    with META_LOCK:
        g = CHESS.get(code)
    with META_LOCK:
        g = CHESS.get(code)
        if g and sub in g["players"] and (g["status"] == "wait" or g.get("bot")):
            CHESS.pop(code, None)
        elif g and sub in g["players"]:
            g["seen"][sub] = 0
            g.setdefault("gone", {})[sub] = time.time()
            _ch_touch(g)
    return jsonify(ok=True)


@app.route("/api/domino/bot", methods=["POST"])
def api_domino_bot():
    """Kompüterin növbəsi. Hesablama kilidin xaricində aparılır ki, digər sorğular gözləməsin."""
    sub, tok = auth_sub()
    if not rate_hit("domb", sub, 240, 60):
        abort(429)
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    g = _dom_get(code, sub)
    with _ensure_cv(g):
        g["seen"][sub] = time.time()
        if not (g.get("bot") and g["status"] == "play" and _dom_isbot(g["turn"])):
            return jsonify(_dom_view(g, sub))
        me = g["turn"]
        v0 = g["v"]
        # _cv (Condition) deepcopy olunmur — yalnız oyun vəziyyətini kopyala
        snap = copy.deepcopy({k: v for k, v in g.items() if k != "_cv"})
    res = None
    try:
        mv = _dom_moves(snap, snap["hands"][me])
        if mv:
            t, e = _dom_bot_decide(snap, me, mv)
            res = ("play", t, e)
        else:
            res = ("draw",)
    except Exception:
        res = None
    with META_LOCK:
        g = DOMINO.get(code)
    if not g or sub not in g["players"]:
        abort(404)
    with _ensure_cv(g):
        if res and g["v"] == v0 and g["status"] == "play" and g["turn"] == me:
            g["note"] = None
            if res[0] == "play":
                _dom_do_play(g, me, res[1], res[2])
            else:
                _dom_do_draw(g, me)   # bir daş; daş çıxmasa klient yenidən çağırır
            _dom_touch(g)
        return jsonify(_dom_view(g, sub))


@app.route("/api/domino/claim", methods=["POST"])
def api_domino_claim():
    """Növbədəki oyunçu DOM_CLAIM saniyədən çox yoxdursa: 2 nəfərdə oyun bitir, 3-4 nəfərdə o, oyundan çıxarılır."""
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    g = _dom_get(code, sub)
    with _ensure_cv(g):
        now = time.time()
        t = g["turn"]
        if g["status"] == "play" and t and t != sub and not _dom_isbot(t):
            last = g["seen"].get(t, 0) or g.get("gone", {}).get(t, 0)
            if now - last > DOM_CLAIM:
                if len(g["players"]) <= 2:
                    _dom_finish(g, sub, "left")
                else:
                    _dom_kick(g, t)
                _dom_touch(g)
        g["seen"][sub] = now
        return jsonify(_dom_view(g, sub))


# ---------------------------------------------------------------
#  Mafia otağı: 4–12 nəfərlik onlayn oyun. Bütün qaydalar və rollar serverdədir:
#  rolu yalnız sahibi (və qaydalara görə komanda yoldaşları / ölülər) görür.
#  Mərhələlər: gecə (mafiya öldürür, həkim qoruyur, komissar yoxlayır) -> gündüz (müzakirə)
#  -> səsvermə -> gecə ... Mafia sayı sakinlərə çatanda mafiya, mafiya qalmayanda sakinlər qalib gəlir.
#  Yaddaşda saxlanır (domino kimi), long-poll ilə yenilənir (hər oyunun öz _cv-si).
# ---------------------------------------------------------------
MAFIA = {}
MAF_IDLE = 3 * 3600
MAF_MAX = 100          # eyni anda maksimum otaq
MAF_MIN = 4            # başlamaq üçün ən az oyunçu
MAF_MAXP = 12          # otaqda ən çox oyunçu
MAF_NIGHT = 35         # gecə (san)
MAF_DAY = 90           # gündüz müzakirəsi (san)
MAF_DAY_MIN = 30       # bu müddət keçməmiş "səsverməyə keç" çoxluğu mərhələni dəyişmir (san)
MAF_VOTE = 45          # səsvermə (san)
MAF_MSG_LEN = 200
MAF_CHAT_KEEP = 200
MAF_ROLES = ("mafia", "doctor", "sheriff", "civil")
_MA_RND = random.SystemRandom()


def _ma_touch(g):
    g["v"] += 1
    g["upd"] = time.time()
    rec_game("mafia", g)
    _notify(g)


def _ma_names(tok):
    i = ident_for(tok, "")
    return dict(name=i["name"], username=i["username"], avatar=i["avatar"], badge=i["badge"])


def _ma_cleanup(now):
    for c in [c for c, g in MAFIA.items() if now - g["upd"] > MAF_IDLE]:
        MAFIA.pop(c, None)


def _ma_get(code, sub):
    g = MAFIA.get(code)
    if not g or sub not in g["players"]:
        abort(404)
    return g


def _ma_alive(g):
    return [p for p in g["players"] if g["alive"].get(p)]


def _ma_idx(g, p):
    return g["players"].index(p) if p in g["players"] else -1


def _ma_win(g):
    al = _ma_alive(g)
    m = sum(1 for p in al if g["roles"].get(p) == "mafia")
    if m == 0:
        return "town"
    if m >= len(al) - m:
        return "mafia"
    return None


def _ma_reset_night(g):
    g["n_kill"] = {}
    g["n_save"] = None
    g["n_check"] = False


def _ma_reset_game(g):
    """Gözləmə vəziyyətinə qaytar (yeni oyun üçün)."""
    g.update(status="wait", phase=None, day=0, roles={}, alive={}, gone={}, revealed=[], log=[], chat=[],
             skip=[], votes={}, checks={}, last_save=None, winner=None, deadline=0, total=0, bot_said={})
    _ma_reset_night(g)


def _ma_over(g, w):
    g["status"] = "over"
    g["phase"] = None
    g["winner"] = w
    g["deadline"] = 0


def _ma_start(g, now):
    ps = list(g["players"])
    n = len(ps)
    nm = max(1, n // 4)      # 4-7 nəfər: 1 mafiya, 8-11: 2, 12: 3 (n//3 oyunu çox tez bitirirdi)
    roles = ["mafia"] * nm + ["doctor", "sheriff"] + ["civil"] * (n - nm - 2)
    _MA_RND.shuffle(roles)
    g["roles"] = dict(zip(ps, roles))
    g["alive"] = {p: True for p in ps}
    g["gone"] = {}
    g["revealed"] = []
    g["log"] = []
    g["chat"] = []
    g["skip"] = []
    g["votes"] = {}
    g["checks"] = {}
    g["last_save"] = None
    g["winner"] = None
    g["status"] = "play"
    g["phase"] = "night"
    g["day"] = 1
    g["total"] = MAF_NIGHT
    g["deadline"] = now + MAF_NIGHT
    _ma_reset_night(g)


def _ma_kill(g, p, how):
    g["alive"][p] = False
    if p not in g["revealed"]:
        g["revealed"].append(p)


def _ma_to_night(g, now):
    g["phase"] = "night"
    g["day"] += 1
    g["total"] = MAF_NIGHT
    g["deadline"] = now + MAF_NIGHT
    g["votes"] = {}
    g["skip"] = []
    _ma_reset_night(g)


def _ma_end_night(g, now):
    tally = {}
    for s, t in g["n_kill"].items():
        if g["alive"].get(s) and g["alive"].get(t):
            tally[t] = tally.get(t, 0) + 1
    victim = None
    if tally:
        top = max(tally.values())
        victim = _MA_RND.choice([t for t, c in tally.items() if c == top])
    saved = g["n_save"]
    if victim and victim == saved:
        victim = None
    g["last_save"] = saved
    if victim:
        _ma_kill(g, victim, "night")
        g["log"].append(dict(k="night_dead", d=g["day"], p=_ma_idx(g, victim), r=g["roles"][victim], c=g["cid"]))
    else:
        g["log"].append(dict(k="night_none", d=g["day"], c=g["cid"]))
    _ma_reset_night(g)
    w = _ma_win(g)
    if w:
        _ma_over(g, w)
        return
    g["phase"] = "day"
    g["total"] = MAF_DAY
    g["deadline"] = now + MAF_DAY
    g["skip"] = []
    g["votes"] = {}


def _ma_to_vote(g, now):
    g["phase"] = "vote"
    g["total"] = MAF_VOTE
    g["deadline"] = now + MAF_VOTE
    g["votes"] = {}


def _ma_end_vote(g, now):
    tally, skips = {}, 0
    for s, t in g["votes"].items():
        if not g["alive"].get(s):
            continue
        if t == "skip":
            skips += 1
        elif g["alive"].get(t):
            tally[t] = tally.get(t, 0) + 1
    elim, n = None, 0
    if tally:
        top = max(tally.values())
        cands = [t for t, c in tally.items() if c == top]
        if len(cands) == 1 and top > skips:      # bərabərlik və ya "ötür" çoxluğu: heç kim çıxmır
            elim, n = cands[0], top
    if elim:
        _ma_kill(g, elim, "vote")
        g["log"].append(dict(k="vote_dead", d=g["day"], p=_ma_idx(g, elim), r=g["roles"][elim], n=n, c=g["cid"]))
    else:
        g["log"].append(dict(k="vote_none", d=g["day"], c=g["cid"]))
    w = _ma_win(g)
    if w:
        _ma_over(g, w)
        return
    _ma_to_night(g, now)


# ---- test botları (yalnız otaq sahibi əlavə edir; "bot:" ilə başlayan sub) ----
MAF_BOT_NAMES = ["Aysel", "Elçin", "Nigar", "Rəşad", "Leyla", "Tural", "Səbinə", "Kamran", "Günel", "Orxan", "Fidan", "Vüsal"]
MAF_BOT_SAY = ["Mənə şübhəli gəlmir, gəlin diqqətlə baxaq", "Kim gecə sakit idi?", "Mən günahsızam!", "Hələ tez xülasə etməyək",
               "Bir az daha müzakirə edək", "Mənim şübhəm var, amma əminlik yoxdur", "Kimsə özünü çox müdafiə edir", "Mən sakinəm, inanın"]


def _ma_is_bot(p):
    return isinstance(p, str) and p.startswith("bot:")


def _ma_humans(g):
    return [p for p in g["players"] if not _ma_is_bot(p) and not g["gone"].get(p)]


def _ma_bots_act(g, now):
    """Botlar gecikmə ilə hərəkət edir. True qayıdırsa dəyişiklik olub."""
    if g["status"] != "play":
        return False
    ph = g["phase"]
    start = g["deadline"] - g["total"]
    el = now - start
    changed = False
    ps = g["players"]
    al = _ma_alive(g)
    for i, b in enumerate(ps):
        if not _ma_is_bot(b) or not g["alive"].get(b):
            continue
        g["seen"][b] = now
        wait = 2.0 + (i * 1.7) % 7.0           # hər botun öz gecikməsi
        if el < wait:
            continue
        role = g["roles"].get(b)
        if ph == "night":
            others = [p for p in al if p != b]
            if role == "mafia" and b not in g["n_kill"]:
                t = [p for p in others if g["roles"].get(p) != "mafia"]
                if t:
                    g["n_kill"][b] = _MA_RND.choice(t)
                    changed = True
            elif role == "doctor" and g["n_save"] is None:
                t = [p for p in al if p != g["last_save"]]
                if t:
                    g["n_save"] = _MA_RND.choice(t)
                    changed = True
            elif role == "sheriff" and not g["n_check"]:
                seen = {c["t"] for c in g["checks"].get(b, [])}
                t = [p for p in others if p not in seen] or others
                if t:
                    tg = _MA_RND.choice(t)
                    g["n_check"] = True
                    g["checks"].setdefault(b, []).append(dict(t=tg, m=(g["roles"][tg] == "mafia"), d=g["day"]))
                    changed = True
        elif ph == "day":
            if not g.setdefault("bot_said", {}).get(b) == g["day"] and _MA_RND.random() < 0.5:
                g["bot_said"][b] = g["day"]
                g["cid"] += 1
                g["chat"].append(dict(id=g["cid"], ch="all", f=i, t=_MA_RND.choice(MAF_BOT_SAY)))
                changed = True
            if el > MAF_DAY * 0.6 and b not in g["skip"] and _MA_RND.random() < 0.05:
                g["skip"].append(b)
                changed = True
        elif ph == "vote" and b not in g["votes"]:
            tgt = None
            if role == "sheriff":
                tgt = next((c["t"] for c in g["checks"].get(b, []) if c["m"] and g["alive"].get(c["t"])), None)
            if tgt is None:
                pool = [p for p in al if p != b and not (role == "mafia" and g["roles"].get(p) == "mafia")]
                tgt = _MA_RND.choice(pool) if pool and _MA_RND.random() < 0.85 else "skip"
            g["votes"][b] = tgt
            changed = True
    return changed


def _ma_tick(g, now):
    """Vaxt bitibsə və ya hamı hərəkətini edibsə mərhələni irəli aparır. CV tutulu halda çağırılır."""
    if g["status"] != "play":
        return False
    bot_ch = _ma_bots_act(g, now)
    ph = g["phase"]
    al = _ma_alive(g)
    if ph == "night":
        if now < g["deadline"]:
            if bot_ch:
                _ma_touch(g)
            return bot_ch
        _ma_end_night(g, now)
    elif ph == "day":
        nskip = sum(1 for p in g["skip"] if g["alive"].get(p))
        early = al and nskip * 2 > len(al) and now - (g["deadline"] - g["total"]) >= MAF_DAY_MIN
        if now < g["deadline"] and not early:
            if bot_ch:
                _ma_touch(g)
            return bot_ch
        _ma_to_vote(g, now)
    elif ph == "vote":
        if now < g["deadline"] and not (al and all(p in g["votes"] for p in al)):
            if bot_ch:
                _ma_touch(g)
            return bot_ch
        _ma_end_vote(g, now)
    else:
        return False
    _ma_touch(g)
    return True


def _ma_remove_play(g, sub, now):
    """Gedən oyundan həqiqətən çıxan oyunçu: ölü sayılır, rolu açılır."""
    if not g["alive"].get(sub) and g["gone"].get(sub):
        return
    was_alive = g["alive"].get(sub)
    g["gone"][sub] = True
    if was_alive:
        _ma_kill(g, sub, "left")
        g["log"].append(dict(k="left", d=g["day"], p=_ma_idx(g, sub), r=g["roles"].get(sub), c=g["cid"]))
    g["n_kill"].pop(sub, None)
    if g["roles"].get(sub) == "doctor":
        g["n_save"] = None
    if sub in g["skip"]:
        g["skip"].remove(sub)
    g["votes"].pop(sub, None)
    if g.get("host") == sub:
        nxt = next((p for p in g["players"] if not g["gone"].get(p) and not _ma_is_bot(p)), None)
        if nxt:
            g["host"] = nxt
    if g["status"] == "play":
        w = _ma_win(g)
        if w:
            _ma_over(g, w)
        else:
            _ma_tick(g, now)


def _ma_visible(g, sub, ch):
    if ch == "all":
        return True
    if g["status"] == "over":
        return True
    if g["status"] == "play" and not g["alive"].get(sub):
        return True                         # ölülər hər şeyi oxuyur
    if ch == "maf":
        return g["roles"].get(sub) == "mafia"
    return False                            # "dead": yalnız ölülər (yuxarıda keçdi)


def _ma_view(g, sub):
    now = time.time()
    st = g["status"]
    ps = g["players"]
    me = ps.index(sub) if sub in ps else -1
    play = st in ("play", "over")
    role = g["roles"].get(sub) if play else None
    alive_me = bool(g["alive"].get(sub)) if play else True
    see_all = st == "over" or (st == "play" and not alive_me)
    is_maf = role == "mafia"
    phase = g.get("phase")
    out = dict(v=g["v"], code=g["code"], status=st, phase=phase, day=g.get("day", 0), size=g["size"], min=MAF_MIN,
               joined=len(ps), host=(g.get("host") == sub), me=me, round=g["round"], role=role, alive=alive_me,
               left=max(0, int(g["deadline"] - now + 0.99)) if st == "play" else 0, total=g.get("total", 0))
    votes = g["votes"] if phase == "vote" else {}
    players = []
    for i, p in enumerate(ps):
        nm = g["names"].get(p) or {}
        r = None
        if play:
            if p == sub or see_all or p in g["revealed"] or (is_maf and g["roles"].get(p) == "mafia"):
                r = g["roles"].get(p)
        d = dict(name=nm.get("name", ""), avatar=nm.get("avatar", ""), username=nm.get("username", ""), me=(p == sub),
                 online=(_ma_is_bot(p) or bool(now - g["seen"].get(p, 0) < 35)), alive=(bool(g["alive"].get(p)) if play else True),
                 left=bool(g["gone"].get(p)), role=r)
        if phase == "vote" and p in votes:
            t = votes[p]
            d["vote"] = -1 if t == "skip" else _ma_idx(g, t)
        if phase == "night" and is_maf and alive_me and g["roles"].get(p) == "mafia" and p in g["n_kill"]:
            d["pick"] = _ma_idx(g, g["n_kill"][p])
        players.append(d)
    out["players"] = players
    # --- mənim hərəkət imkanlarım ---
    act = None
    if st == "play" and alive_me:
        al = [i for i, p in enumerate(ps) if g["alive"].get(p)]
        if phase == "night":
            if role == "mafia":
                act = dict(kind="kill", targets=[i for i in al if g["roles"][ps[i]] != "mafia"],
                           pick=_ma_idx(g, g["n_kill"].get(sub)) if sub in g["n_kill"] else -1)
            elif role == "doctor":
                blk = _ma_idx(g, g["last_save"]) if g["last_save"] else -1
                act = dict(kind="save", targets=[i for i in al if i != blk], block=blk,
                           pick=_ma_idx(g, g["n_save"]) if g["n_save"] else -1)
            elif role == "sheriff":
                act = dict(kind="check", targets=([] if g["n_check"] else [i for i in al if i != me]), done=bool(g["n_check"]))
        elif phase == "day":
            out["skips"] = sum(1 for p in g["skip"] if g["alive"].get(p))
            act = dict(kind="skip", done=(sub in g["skip"]))
        elif phase == "vote":
            act = dict(kind="vote", targets=[i for i in al if i != me],
                       pick=(-1 if g["votes"].get(sub) == "skip" else _ma_idx(g, g["votes"][sub])) if sub in g["votes"] else None)
    out["act"] = act
    if role == "sheriff":
        out["checks"] = [dict(p=_ma_idx(g, c["t"]), m=c["m"], d=c["d"]) for c in g["checks"].get(sub, [])]
    out["log"] = g["log"][-40:]
    out["chat"] = [dict(id=c["id"], ch=c["ch"], f=c["f"], t=c["t"]) for c in g["chat"]
                   if _ma_visible(g, sub, c["ch"])][-80:]
    if st == "over":
        out["winner"] = g["winner"]
    out["vc"], out["vk"] = _gv_view("mafia", g)
    return out


@app.route("/api/mafia/create", methods=["POST"])
def api_mafia_create():
    sub, tok = auth_sub()
    if not rate_hit("mafc", sub, 12, 60):
        abort(429)
    nm = _ma_names(tok)
    now = time.time()
    with META_LOCK:
        _ma_cleanup(now)
        if len(MAFIA) >= MAF_MAX:
            abort(503)
        for _ in range(50):
            c = "".join(secrets.choice(CODE_CHARS) for _ in range(5))
            if c not in MAFIA:
                break
        else:
            abort(503)
        g = dict(code=c, players=[sub], names={sub: nm}, seen={sub: now}, size=MAF_MAXP, host=sub,
                 v=1, upd=now, round=1, cid=0)
        _ma_reset_game(g)
        _ensure_cv(g); MAFIA[c] = g
        return jsonify(_ma_view(g, sub))


@app.route("/api/mafia/join", methods=["POST"])
def api_mafia_join():
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    if not code:
        abort(400)
    nm = _ma_names(tok)
    now = time.time()
    with META_LOCK:
        g = MAFIA.get(code)
    with META_LOCK:
        g = MAFIA.get(code)
        if not g:
            abort(404)
        _ma_tick(g, now)
        if sub not in g["players"]:
            if len(g["players"]) >= g["size"] or g["status"] != "wait":
                return jsonify(error="full"), 403
            g["players"].append(sub)
            g["names"][sub] = nm
            g["seen"][sub] = now
            _ma_touch(g)
        else:
            g["names"][sub] = nm
            if now - g["seen"].get(sub, 0) > 30:
                _ma_touch(g)                    # qayıdıb: digərləri onlayn görsün
        g["seen"][sub] = now
        return jsonify(_ma_view(g, sub))


@app.route("/api/mafia/poll")
def api_mafia_poll():
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
    g = _ma_get(code, sub)
    with _ensure_cv(g):
        now = time.time()
        _ma_tick(g, now)
        if now - g["seen"].get(sub, 0) > 30:
            g["seen"][sub] = now
            _ma_touch(g)
        g["seen"][sub] = now
        while g["v"] == v and (vk is None or g.get("vk", 0) == vk):
            now = time.time()
            left = deadline - now
            if left <= 0:
                break
            wt = left
            if g["status"] == "play":
                wt = min(left, max(0.05, g["deadline"] - now + 0.05))   # mərhələ vaxtı bitəndə oyan
                if g.get("bots"):
                    wt = min(wt, 1.0)               # botlar üçün tez-tez yoxla
            g["_cv"].wait(timeout=wt)
            if MAFIA.get(code) is not g:
                abort(404)
            now = time.time()
            g["seen"][sub] = now
            _ma_tick(g, now)
        resp = jsonify(_ma_view(g, sub))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/mafia/check")
def api_mafia_check():
    sub, tok = auth_sub()
    code = clean_room(request.args.get("room"))
    with META_LOCK:
        g = MAFIA.get(code)
    with META_LOCK:
        g = MAFIA.get(code)
        if not g or sub not in g["players"] or g["gone"].get(sub):
            abort(404)
        return jsonify(ok=True, status=g["status"])


@app.route("/api/mafia/start", methods=["POST"])
def api_mafia_start():
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    g = _ma_get(code, sub)
    with _ensure_cv(g):
        now = time.time()
        g["seen"][sub] = now
        if g["status"] == "wait" and g.get("host") == sub:
            if len(g["players"]) < MAF_MIN:
                return jsonify(error="Başlamaq üçün ən azı %d oyunçu lazımdır" % MAF_MIN), 409
            _ma_start(g, now)
            _ma_touch(g)
        return jsonify(_ma_view(g, sub))


@app.route("/api/mafia/bots", methods=["POST"])
def api_mafia_bots():
    """Test üçün: otaq sahibi gözləmə otağına bot əlavə edir / botları silir."""
    sub, tok = auth_sub()
    if not rate_hit("mafb", sub, 60, 60):
        abort(429)
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    g = _ma_get(code, sub)
    with _ensure_cv(g):
        now = time.time()
        g["seen"][sub] = now
        if g["status"] == "wait" and g.get("host") == sub:
            if d.get("clear"):
                for b in [p for p in g["players"] if _ma_is_bot(p)]:
                    g["players"].remove(b)
                    g["names"].pop(b, None)
                    g["seen"].pop(b, None)
                g["bots"] = False
                _ma_touch(g)
            else:
                try:
                    n = max(1, min(int(d.get("n", 1)), MAF_MAXP))
                except (TypeError, ValueError):
                    abort(400)
                used = {(g["names"].get(p) or {}).get("name") for p in g["players"]}
                for _ in range(n):
                    if len(g["players"]) >= g["size"]:
                        break
                    free = [x for x in MAF_BOT_NAMES if "🤖 " + x not in used]
                    nmx = ("🤖 " + (free[0] if free else "Bot%d" % (len(g["players"]) + 1)))
                    b = "bot:" + secrets.token_hex(4)
                    g["players"].append(b)
                    g["names"][b] = dict(name=nmx, username="", avatar="", badge=False)
                    g["seen"][b] = now
                    used.add(nmx)
                g["bots"] = True
                _ma_touch(g)
        return jsonify(_ma_view(g, sub))


@app.route("/api/mafia/act", methods=["POST"])
def api_mafia_act():
    sub, tok = auth_sub()
    if not rate_hit("mafa", sub, 120, 60):
        abort(429)
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    kind = str(d.get("kind", ""))
    g = _ma_get(code, sub)
    with _ensure_cv(g):
        now = time.time()
        g["seen"][sub] = now
        _ma_tick(g, now)
        if g["status"] != "play" or not g["alive"].get(sub):
            return jsonify(_ma_view(g, sub))        # köhnə klik: cari vəziyyət
        ps = g["players"]
        ph = g["phase"]
        role = g["roles"][sub]
        tgt = None
        if kind in ("kill", "save", "check", "vote") and not (kind == "vote" and d.get("t") == -1):
            try:
                ti = int(d.get("t"))
            except (TypeError, ValueError):
                abort(400)
            if not 0 <= ti < len(ps) or not g["alive"].get(ps[ti]):
                abort(400)
            tgt = ps[ti]
        if kind == "kill" and ph == "night" and role == "mafia":
            if tgt == sub or g["roles"][tgt] == "mafia":
                abort(400)
            g["n_kill"][sub] = tgt
        elif kind == "save" and ph == "night" and role == "doctor":
            if tgt == g["last_save"]:
                abort(400)                          # eyni adamı ardıcıl iki gecə qorumaq olmaz
            g["n_save"] = tgt
        elif kind == "check" and ph == "night" and role == "sheriff":
            if tgt == sub or g["n_check"]:
                abort(400)
            g["n_check"] = True
            g["checks"].setdefault(sub, []).append(dict(t=tgt, m=(g["roles"][tgt] == "mafia"), d=g["day"]))
        elif kind == "skip" and ph == "day":
            if sub not in g["skip"]:
                g["skip"].append(sub)
        elif kind == "vote" and ph == "vote":
            if tgt is None:
                g["votes"][sub] = "skip"
            elif tgt == sub:
                abort(400)
            else:
                g["votes"][sub] = tgt
        else:
            return jsonify(_ma_view(g, sub))        # bu mərhələdə belə hərəkət yoxdur
        _ma_touch(g)
        _ma_tick(g, now)
        return jsonify(_ma_view(g, sub))


@app.route("/api/mafia/chat", methods=["POST"])
def api_mafia_chat():
    sub, tok = auth_sub()
    if not rate_hit("mafm", sub, 20, 30):
        abort(429)
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    text = re.sub(r"\s+", " ", str(d.get("text") or "")).strip()[:MAF_MSG_LEN]
    if not text:
        abort(400)
    g = _ma_get(code, sub)
    with _ensure_cv(g):
        now = time.time()
        g["seen"][sub] = now
        _ma_tick(g, now)
        if g["status"] != "play":
            ch = "all"
        elif not g["alive"].get(sub):
            ch = "dead"
        elif g["phase"] == "night":
            if g["roles"].get(sub) != "mafia":
                return jsonify(error="Gecə yalnız mafiya yaza bilər"), 403
            ch = "maf"
        else:
            ch = "all"
        g["cid"] += 1
        g["chat"].append(dict(id=g["cid"], ch=ch, f=_ma_idx(g, sub), t=text))
        del g["chat"][:-MAF_CHAT_KEEP]
        _ma_touch(g)
        return jsonify(_ma_view(g, sub))


@app.route("/api/mafia/again", methods=["POST"])
def api_mafia_again():
    sub, tok = auth_sub()
    d = request.get_json(silent=True) or {}
    code = clean_room(d.get("room"))
    g = _ma_get(code, sub)
    with _ensure_cv(g):
        g["seen"][sub] = time.time()
        if g["status"] == "over" and g.get("host") == sub:
            for p in [p for p in g["players"] if g["gone"].get(p)]:
                g["players"].remove(p)
                g["names"].pop(p, None)
                g["seen"].pop(p, None)
            g["round"] += 1
            g["cid"] = 0
            _ma_reset_game(g)
            _ma_touch(g)
        return jsonify(_ma_view(g, sub))


@app.route("/api/mafia/leave", methods=["POST"])
def api_mafia_leave():
    """Geri düyməsi: oyunda qalır (qayıtmaq olur). Gözləmə otağında yer boşalır."""
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    with META_LOCK:
        g = MAFIA.get(code)
    with META_LOCK:
        g = MAFIA.get(code)
        if g and sub in g["players"]:
            if g["status"] == "wait":
                _ma_drop_wait(g, code, sub)
            else:
                g["seen"][sub] = 0
                _ma_touch(g)
    return jsonify(ok=True)


def _ma_drop_wait(g, code, sub):
    g["players"].remove(sub)
    g["names"].pop(sub, None)
    g["seen"].pop(sub, None)
    hum = [x for x in g["players"] if not _ma_is_bot(x)]
    if not hum:
        MAFIA.pop(code, None)
        return
    if g.get("host") == sub:
        g["host"] = hum[0]
    _ma_touch(g)


@app.route("/api/mafia/quit", methods=["POST"])
def api_mafia_quit():
    """Oyundan həqiqətən çıx: gedən oyunda ölü sayılırsan və rolun açılır."""
    sub, tok = auth_sub()
    code = clean_room((request.get_json(silent=True) or {}).get("room"))
    with META_LOCK:
        g = MAFIA.get(code)
    with META_LOCK:
        g = MAFIA.get(code)
        if not g or sub not in g["players"]:
            return jsonify(ok=True)
        now = time.time()
        if g["status"] == "wait":
            _ma_drop_wait(g, code, sub)
        elif g["status"] == "play":
            _ma_remove_play(g, sub, now)
            if not _ma_humans(g):
                MAFIA.pop(code, None)
            else:
                _ma_touch(g)
        else:                                       # over
            g["gone"][sub] = True
            if g.get("host") == sub:
                nxt = next((p for p in g["players"] if not g["gone"].get(p) and not _ma_is_bot(p)), None)
                if nxt:
                    g["host"] = nxt
            if not _ma_humans(g):
                MAFIA.pop(code, None)
            else:
                _ma_touch(g)
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), threaded=True)
