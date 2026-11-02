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

