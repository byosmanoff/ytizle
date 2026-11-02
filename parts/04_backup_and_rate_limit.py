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


