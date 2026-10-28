"""Durak (Dürak) oyun mühərriki — yalnız insanlar üçün, kompüter (bot) yoxdur.

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

        self.hands = {p: [] for p in self.players}
        self.discard = []
        self.out = []                 # çıxan oyunçular, çıxma sırası ilə
        self.table = []               # {"a": hücum kartı, "d": bağlayan kart və ya None}
        self.over = False
        self.loser = None             # durak; None = heç kim (bərabərlik)

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
        self.started = True
        self.bout_ranks.add(card[0])
        self.bout_n += 1
        if self.took:
            self.hands[self.defender].append(card)
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
            if p["d"]:
                self.hands[pid].append(p["d"])
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
        if self.bout_n >= self.cap:
            return self._end_round()
        adders = [p for p in self._adders() if self._can_add(p)]
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
                self.hands[p].append(self.deck.pop())
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

    def card_count(self):
        """Test üçün: bütün kartların sayı (36 olmalıdır)."""
        table = sum(1 + (1 if p["d"] else 0) for p in self.table)
        return sum(len(h) for h in self.hands.values()) + len(self.deck) + len(self.discard) + table
