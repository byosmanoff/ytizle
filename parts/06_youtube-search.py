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
