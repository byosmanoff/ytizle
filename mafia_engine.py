"""Mafiya oyun mühərriki (yalnız qayda məntiqi, Flask-dan asılı deyil).

Qaydalar:
  * 4-12 oyunçu. Rollar: Mafiya, Komissar, Həkim, Dinc sakin.
  * Gecə: mafiya qurban seçir, həkim birini qoruyur, komissar birini yoxlayır.
  * Gündüz: hamı müzakirə edir və səs verir. Ən çox səs alan oyundan çıxır, rolu açılır.
  * Mafiya qalmayanda sakinlər, mafiya sayı qalanlara bərabər olanda mafiya qalib gəlir.
  * Həkim eyni adamı ardıcıl iki gecə qoruya bilməz (özünü qoruya bilər).
  * Botlar öz hərəkətlərini avtomatik edir.
"""
import random

MIN_PLAYERS = 4
MAX_PLAYERS = 12
NIGHT_SECS = 75
DAY_SECS = 150

MAFIA, SHERIFF, DOCTOR, CIVIL = "mafia", "sheriff", "doctor", "civil"
SKIP = "skip"


class GameError(Exception):
    pass


def role_plan(n):
    """n oyunçu üçün rolların siyahısı (qarışdırılmamış)."""
    if n < MIN_PLAYERS or n > MAX_PLAYERS:
        raise ValueError("4-12 oyunçu lazımdır")
    m = 1 if n <= 6 else (2 if n <= 9 else 3)
    roles = [MAFIA] * m + [SHERIFF]
    if n >= 5:
        roles.append(DOCTOR)
    roles += [CIVIL] * (n - len(roles))
    return roles


class Mafia:
    def __init__(self, players, bots=(), rng=None, now=0.0):
        n = len(players)
        if not MIN_PLAYERS <= n <= MAX_PLAYERS or len(set(players)) != n:
            raise ValueError("4-12 fərqli oyunçu lazımdır")
        self.rng = rng or random.Random()
        self.players = list(players)
        self.bots = set(bots) & set(players)
        roles = role_plan(n)
        self.rng.shuffle(roles)
        self.roles = dict(zip(self.players, roles))
        self.alive = list(self.players)
        self.dead = []                     # ölənlər (ölmə sırası ilə), rolları hamıya açıqdır
        self.left = set()                  # oyundan çıxanlar
        self.phase = "night"
        self.day = 1
        self.deadline = now + NIGHT_SECS
        self.kills = {}                    # mafiya: {oyunçu: hədəf}
        self.save = None                   # həkimin gecə seçimi
        self.check = None                  # komissarın gecə seçimi
        self.last_save = None
        self.checked = {}                  # komissarın yoxladıqları {oyunçu: mafiyadır?}
        self.votes = {}                    # gündüz səsləri {oyunçu: hədəf və ya SKIP}
        self.last_votes = []               # son səsvermənin açıqlanmış nəticəsi [(kim, kimə)]
        self.log = []
        self.chat = []                     # (idx, kanal, mətn)  kanal: "all" | "mafia"
        self.over = False
        self.winner = None                 # "town" | "mafia"
        self.say("1-ci gecə. Şəhər yatır, mafiya oyanır.")
        self.auto_bots()

    # ---------------- köməkçi ----------------
    def say(self, text):
        self.log.append(text)
        if len(self.log) > 60:
            del self.log[:len(self.log) - 60]

    def idx(self, p):
        return self.players.index(p) if p in self.players else -1

    def is_alive(self, p):
        return p in self.alive

    def mafia_alive(self):
        return [p for p in self.alive if self.roles[p] == MAFIA]

    def actors(self):
        """Hazırkı gecədə hərəkət etməli olan canlı oyunçular {oyunçu: növ}."""
        out = {}
        for p in self.alive:
            r = self.roles[p]
            if r == MAFIA:
                out[p] = "kill"
            elif r == DOCTOR:
                out[p] = "save"
            elif r == SHERIFF:
                out[p] = "check"
        return out

    def action_kind(self, p):
        if self.over or self.phase != "night" or p not in self.alive:
            return None
        return self.actors().get(p)

    def valid_targets(self, p):
        k = self.action_kind(p)
        if k == "kill":
            return [q for q in self.alive if self.roles[q] != MAFIA]
        if k == "save":
            return [q for q in self.alive if q != self.last_save]
        if k == "check":
            return [q for q in self.alive if q != p and q not in self.checked]
        return []

    def my_choice(self, p):
        k = self.action_kind(p)
        if k == "kill":
            return self.kills.get(p)
        if k == "save":
            return self.save
        if k == "check":
            return self.check
        return None

    # ---------------- hərəkətlər ----------------
    def night_action(self, p, target, now):
        if self.over or self.phase != "night":
            raise GameError("Hazırda gecə deyil")
        k = self.action_kind(p)
        if not k:
            raise GameError("Bu gecə sənin hərəkətin yoxdur")
        if target not in self.valid_targets(p):
            raise GameError("Bu hədəfi seçmək olmaz")
        if k == "kill":
            self.kills[p] = target
        elif k == "save":
            self.save = target
        else:
            self.check = target
        self.progress(now)

    def vote(self, p, target, now):
        if self.over or self.phase != "day":
            raise GameError("Hazırda səsvermə yoxdur")
        if p not in self.alive:
            raise GameError("Ölənlər səs verə bilməz")
        if target != SKIP and target not in self.alive:
            raise GameError("Bu oyunçuya səs vermək olmaz")
        if target == p:
            raise GameError("Özünə səs verə bilməzsən")
        self.votes[p] = target
        self.progress(now)

    def add_chat(self, p, text, now=0.0):
        text = " ".join(str(text or "").split())[:200]
        if not text:
            return False
        ch = None
        if self.over:
            ch = "all"
        elif p in self.alive:
            if self.phase == "day":
                ch = "all"
            elif self.phase == "night" and self.roles[p] == MAFIA:
                ch = "mafia"
        if ch is None:
            raise GameError("İndi yaza bilmirsən")
        self.chat.append((self.idx(p), ch, text))
        if len(self.chat) > 120:
            del self.chat[:len(self.chat) - 120]
        return True

    def leave_game(self, p, now):
        """Oyunçu oyundan həmişəlik çıxdı: ölmüş sayılır."""
        if self.over or p not in self.alive:
            self.left.add(p)
            return
        self.left.add(p)
        self.alive.remove(p)
        self.dead.append(p)
        self.say("{%d} oyundan çıxdı (%s)." % (self.idx(p), role_name(self.roles[p])))
        self.kills.pop(p, None)
        self.votes.pop(p, None)
        self.votes = {a: b for a, b in self.votes.items() if b != p}
        if self.check == p:
            self.check = None
        if self.save == p:
            self.save = None
        self.kills = {a: b for a, b in self.kills.items() if b != p}
        if not self.check_win():
            self.progress(now)

    # ---------------- gedişat ----------------
    def tick(self, now):
        """Vaxt bitibsə mərhələni irəlilət. True qayıdırsa vəziyyət dəyişib."""
        changed = False
        guard = 0
        while not self.over and now >= self.deadline and guard < 4:
            guard += 1
            changed = True
            if self.phase == "night":
                self.resolve_night(now)
            else:
                self.resolve_day(now)
        return changed

    def progress(self, now):
        """Hamı hərəkətini edibsə vaxtı gözləmədən mərhələni bitir."""
        if self.over:
            return
        if self.phase == "night":
            if self.night_done():
                self.resolve_night(now)
        else:
            if all(p in self.votes for p in self.alive):
                self.resolve_day(now)

    def night_done(self):
        for p, k in self.actors().items():
            if k == "kill" and p not in self.kills:
                return False
            if k == "save" and self.save is None:
                return False
            if k == "check" and self.check is None:
                return False
        return True

    def resolve_night(self, now):
        victim = None
        if self.kills:
            cnt = {}
            for t in self.kills.values():
                cnt[t] = cnt.get(t, 0) + 1
            top = max(cnt.values())
            victim = self.rng.choice([t for t, c in cnt.items() if c == top])
        saved = self.save
        # komissar nəticəsi yoxlananlar siyahısına yazılır
        if self.check is not None and self.check in self.players:
            self.checked[self.check] = (self.roles[self.check] == MAFIA)
        self.last_save = saved
        self.kills, self.save, self.check = {}, None, None
        n = self.day
        if victim is not None and victim != saved:
            self.alive.remove(victim)
            self.dead.append(victim)
            self.say("%d-ci səhər: {%d} öldürüldü. O, %s idi." % (
                n, self.idx(victim), role_name(self.roles[victim])))
        elif victim is not None:
            self.say("%d-ci səhər: Mafiya hücum etdi, amma həkim qurbanı xilas etdi. Heç kim ölmədi." % n)
        else:
            self.say("%d-ci səhər: Bu gecə heç kim ölmədi." % n)
        if self.check_win():
            return
        self.phase = "day"
        self.votes = {}
        self.deadline = now + DAY_SECS
        self.auto_bots()
        self.progress(now)

    def resolve_day(self, now):
        cnt = {}
        skips = 0
        reveal = []
        for p in self.alive:
            t = self.votes.get(p)
            if t is None:
                continue
            reveal.append((self.idx(p), -1 if t == SKIP else self.idx(t)))
            if t == SKIP:
                skips += 1
            else:
                cnt[t] = cnt.get(t, 0) + 1
        self.last_votes = reveal
        out = None
        if cnt:
            top = max(cnt.values())
            leaders = [t for t, c in cnt.items() if c == top]
            if len(leaders) == 1 and top > skips:
                out = leaders[0]
        if out is not None:
            self.alive.remove(out)
            self.dead.append(out)
            self.say("%d-ci gün: {%d} oyundan çıxarıldı. O, %s idi." % (
                self.day, self.idx(out), role_name(self.roles[out])))
        else:
            self.say("%d-ci gün: Razılıq olmadı, heç kim çıxarılmadı." % self.day)
        self.votes = {}
        if self.check_win():
            return
        self.day += 1
        self.phase = "night"
        self.deadline = now + NIGHT_SECS
        self.say("%d-ci gecə. Şəhər yatır." % self.day)
        self.auto_bots()
        self.progress(now)

    def check_win(self):
        m = len(self.mafia_alive())
        others = len(self.alive) - m
        if m == 0:
            self.finish("town", "Bütün mafiya tutuldu. Sakinlər qalib gəldi!")
            return True
        if m >= others:
            self.finish("mafia", "Mafiya şəhəri ələ keçirdi. Mafiya qalib gəldi!")
            return True
        return False

    def finish(self, who, text):
        self.over = True
        self.winner = who
        self.phase = "over"
        self.say(text)

    # ---------------- botlar ----------------
    def auto_bots(self):
        """Botlar hərəkətlərini mərhələnin əvvəlində edir (səslər gizli qalır)."""
        if self.over:
            return
        if self.phase == "night":
            for p in self.alive:
                if p not in self.bots or self.action_kind(p) is None:
                    continue
                t = self.valid_targets(p)
                if not t:
                    continue
                k = self.action_kind(p)
                if k == "kill":
                    # mafiya botları eyni hədəfdə razılaşır
                    prev = [x for x in self.kills.values()]
                    choice = prev[0] if prev and prev[0] in t else self.rng.choice(t)
                    self.kills[p] = choice
                elif k == "save":
                    self.save = self.rng.choice(t)
                else:
                    self.check = self.rng.choice(t)
        else:
            known = {a for a, m in self.checked.items() if m and a in self.alive}
            for p in self.alive:
                if p not in self.bots or p in self.votes:
                    continue
                cand = [q for q in self.alive if q != p]
                if self.roles[p] == MAFIA:
                    cand = [q for q in cand if self.roles[q] != MAFIA] or cand
                elif self.roles[p] == SHERIFF and known:
                    cand = [q for q in cand if q in known] or cand
                self.votes[p] = self.rng.choice(cand) if cand and self.rng.random() < 0.85 else SKIP

    # ---------------- görünüş ----------------
    def view(self, sub, now):
        me = sub
        mine = self.roles.get(me)
        mafia_team = (mine == MAFIA)
        reveal_all = self.over
        left = max(0, int(self.deadline - now + 0.99)) if not self.over else 0
        players = []
        for i, p in enumerate(self.players):
            alive = p in self.alive
            show_role = reveal_all or (not alive) or p == me or (mafia_team and self.roles[p] == MAFIA)
            d = dict(alive=alive, left=p in self.left,
                     role=self.roles[p] if show_role else None)
            if self.phase == "day" and alive:
                d["voted"] = p in self.votes
            players.append(d)
        kind = self.action_kind(me)
        out = dict(phase=self.phase, day=self.day, left=left, over=self.over, winner=self.winner,
                   role=mine, alive=me in self.alive, players=players,
                   kind=kind, targets=[self.idx(t) for t in self.valid_targets(me)] if kind else [],
                   choice=(self.idx(self.my_choice(me)) if self.my_choice(me) else -1),
                   log=list(self.log[-40:]), lastvotes=[list(x) for x in self.last_votes])
        if kind == "kill":
            out["mates"] = {str(self.idx(a)): self.idx(b) for a, b in self.kills.items()}
        if mine == SHERIFF:
            out["checked"] = {str(self.idx(a)): m for a, m in self.checked.items()}
        if self.phase == "day" and me in self.alive:
            v = self.votes.get(me)
            out["vote"] = None if v is None else (-1 if v == SKIP else self.idx(v))
            out["votes_in"] = sum(1 for p in self.alive if p in self.votes)
            out["votes_all"] = len(self.alive)
        elif self.phase == "night":
            acted = sum(1 for p, k in self.actors().items() if self._acted(p, k))
            out["acted"] = acted
        chat = []
        for (i, ch, text) in self.chat:
            if ch == "all" or mafia_team or reveal_all:
                chat.append([i, ch, text])
        out["chat"] = chat[-60:]
        out["canchat"] = self.can_chat(me)
        return out

    def _acted(self, p, k):
        return (k == "kill" and p in self.kills) or (k == "save" and self.save is not None) \
            or (k == "check" and self.check is not None)

    def can_chat(self, p):
        if self.over:
            return True
        if p not in self.alive:
            return False
        if self.phase == "day":
            return True
        return self.phase == "night" and self.roles[p] == MAFIA


def role_name(r):
    return {MAFIA: "Mafiya", SHERIFF: "Komissar", DOCTOR: "Həkim", CIVIL: "Dinc sakin"}.get(r, "?")
