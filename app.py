import os, re, json, time, secrets, threading, ipaddress, socket, html as htmllib
from urllib.parse import urlparse, urljoin
import requests
from werkzeug.exceptions import HTTPException
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
    return send_file(os.path.join(HERE, "index.html"))


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
