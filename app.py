import os, re, json, time, secrets, threading, ipaddress, socket, html as htmllib
from urllib.parse import urlparse
import requests
from flask import Flask, request, Response, send_file, abort, jsonify

app = Flask(__name__)
HERE = os.path.dirname(os.path.abspath(__file__))
UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36")
UA_DESKTOP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# ---------------------------------------------------------------
#  Otaq sistemi (iki telefon arasında mesaj/sinxron ötürücü)
#  Yaddaşda saxlanır, ona görə gunicorn --workers 1 olmalıdır
# ---------------------------------------------------------------
ROOMS = {}
CV = threading.Condition()
PEER_TTL = 45
CODE_CHARS = "abcdefghjkmnpqrstuvwxyz23456789"


def clean_room(c):
    c = re.sub(r"[^a-z0-9-]", "", str(c or "").lower())[:30]
    return c or None


def get_room(code):
    r = ROOMS.get(code)
    if r is None:
        r = {"seq": 0, "msgs": [], "peers": {}, "created": time.time()}
        ROOMS[code] = r
    return r


def count_others(r, me, now):
    return sum(1 for pid, ts in r["peers"].items() if pid != me and now - ts < PEER_TTL)


def cleanup(now):
    dead = []
    for k, r in ROOMS.items():
        last = max([r["created"]] + list(r["peers"].values()))
        if now - last > 3600:
            dead.append(k)
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
        r["peers"][pid] = now
        CV.notify_all()
        return jsonify(seq=r["seq"])


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
        r["seq"] += 1
        r["msgs"].append((r["seq"], pid, msg))
        del r["msgs"][:-100]
        r["peers"][pid] = now
        CV.notify_all()
    return jsonify(ok=True)


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
    deadline = time.time() + 20
    with CV:
        r = get_room(code)
        while True:
            now = time.time()
            r["peers"][pid] = now
            new = [m for m in r["msgs"] if m[0] > since and m[1] != pid]
            n = count_others(r, pid, now)
            left = deadline - now
            if new or n != pc or left <= 0:
                break
            CV.wait(timeout=left)
        resp = jsonify(seq=r["seq"], peers=n, msgs=[m[2] for m in new])
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
  function go(u){ if(u && /^https?:/i.test(u)) window.top.postMessage({t:'nav', url:u}, '*'); }
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
    if(NESTED || (f.method || 'get').toLowerCase() !== 'get') return;
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
          if(e.navigationType === 'reload'){
            if(Date.now() - T0 < 4000) e.preventDefault();
            return;
          }
          if(d.indexOf(location.origin + '/p?') === 0) return;
          e.preventDefault();
          if(same(d, document.baseURI)) return;
          go(d);
        } catch(x){}
      });
    }
  } catch(x){}

  function fixFrames(){
    document.querySelectorAll('iframe[src]').forEach(function(f){
      var s = f.getAttribute('src');
      if(!s || s.indexOf(P) === 0 || /^(about|javascript|data|blob):/i.test(s)) return;
      var u = abs(s);
      if(u && /^https?:/i.test(u)) f.setAttribute('src', P + encodeURIComponent(u));
    });
  }

  var ign = false, bound = new WeakSet();
  function bind(v){
    if(bound.has(v)) return;
    bound.add(v);
    ['play', 'pause', 'seeked'].forEach(function(ev){
      v.addEventListener(ev, function(){
        if(ign) return;
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

  window.addEventListener('message', function(e){
    var m = e.data;
    if(!m || m.t !== 'cmd') return;
    var v = mainVideo();
    if(v){
      ign = true;
      try {
        if(Math.abs(v.currentTime - m.time) > 1.5) v.currentTime = m.time;
        if(m.e === 'play') v.play();
        else if(m.e === 'pause') v.pause();
      } catch(x){}
      setTimeout(function(){ ign = false; }, 900);
    }
    document.querySelectorAll('iframe').forEach(function(f){
      try { f.contentWindow.postMessage(m, '*'); } catch(x){}
    });
  });
})();
"""


def safe(url):
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            return False
        for info in socket.getaddrinfo(p.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast):
                return False
        return True
    except Exception:
        return False


@app.route("/")
def index():
    return send_file(os.path.join(HERE, "index.html"))


@app.route("/p")
def proxy():
    url = request.args.get("u", "").strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    if not safe(url):
        abort(400)

    h = {
        "User-Agent": UA,
        "Accept": request.headers.get("Accept", "*/*"),
        "Accept-Language": "az,en;q=0.8,ru;q=0.6",
    }
    if request.headers.get("Range"):
        h["Range"] = request.headers["Range"]

    try:
        r = requests.get(url, headers=h, stream=True, timeout=20, allow_redirects=True)
    except Exception as e:
        return Response("Açıla bilmədi: " + str(e), 502)
    if not safe(r.url):
        abort(400)

    ct = r.headers.get("Content-Type", "")
    if "text/html" in ct.lower():
        raw = r.content
        enc = r.encoding if "charset" in ct.lower() else (r.apparent_encoding or "utf-8")
        try:
            page = raw.decode(enc or "utf-8", "replace")
        except LookupError:
            page = raw.decode("utf-8", "replace")
        page = re.sub(r'<meta[^>]+http-equiv=["\']?content-security-policy[^>]*>', "", page, flags=re.I)
        page = re.sub(r"<meta[^>]+charset[^>]*>", "", page, flags=re.I)
        tag = ('<meta charset="utf-8"><base href="' + htmllib.escape(r.url, quote=True) + '">'
               '<meta name="referrer" content="no-referrer"><script>' + INJECT + "</script>")
        m = re.search(r"<head(\s[^>]*)?>", page, re.I)
        page = page[:m.end()] + tag + page[m.end():] if m else tag + page
        return Response(page.encode("utf-8"), content_type="text/html; charset=utf-8")

    keep = ("content-type", "content-range", "accept-ranges")
    hdrs = {k: v for k, v in r.headers.items() if k.lower() in keep}
    if not r.headers.get("Content-Encoding") and r.headers.get("Content-Length"):
        hdrs["Content-Length"] = r.headers["Content-Length"]
    return Response(r.iter_content(65536), status=r.status_code, headers=hdrs)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), threaded=True)
