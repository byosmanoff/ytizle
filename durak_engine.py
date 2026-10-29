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
import random

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
