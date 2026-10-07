import os, re, ipaddress, socket, html as htmllib
from urllib.parse import urlparse
import requests
from flask import Flask, request, Response, send_file, abort

app = Flask(__name__)
HERE = os.path.dirname(os.path.abspath(__file__))
UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36")

INJECT = r"""
(function(){
  var P = location.origin + '/p?u=';
  var NESTED = window.parent !== window.top;
  function abs(u){ try { return new URL(u, document.baseURI).href; } catch(e){ return null; } }
  function go(u){ if(u && /^https?:/i.test(u)) window.top.postMessage({t:'nav', url:u}, '*'); }

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
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
