"""
Doodle Quest - deterministic math challenge generator.

Design rules (these are the whole point of this file):

1. THE MATH ARISES FROM THE FICTION.
   Every number in `prompt` refers to objects the child can SEE in the scene.
   If the prompt says "8 stepping stones", the challenge emits 8 stone props.
   Props are the fiction; stones are the things the child walks onto.

2. `prompt` IS THE QUESTION.
   Short (<= 12 words), concrete, with units. It is rendered in an
   always-visible banner. `narrative` is flavour and may be replaced by AI.

3. VARIED MECHANICS.
   Five primitives - PICK-ONE, PICK-A-SET, COLLECT-N, ORDERED-WALK and
   GROUP-SELECT - mapped onto named archetypes, at least two per topic and at
   least one that is not "walk to the numbered stone".

4. NOTHING HERE CALLS AN LLM. Correctness is guaranteed and generation is
   instant. prompts.py may *re-narrate* a challenge, never re-compute it.
"""

import math
import random

TOPICS = {
    "addition": "Addition",
    "subtraction": "Subtraction",
    "multiplication": "Multiplication",
    "fractions": "Fractions",
    "decimals": "Decimals",
    "geometry": "Shapes & Geometry",
    "algebra": "Basic Algebra",
    "speed_distance_time": "Speed, Distance & Time",
}

BANDS = {
    "k1": {"label": "Ages 4-6", "grade": "K-1"},
    "23": {"label": "Ages 7-8", "grade": "2-3"},
    "45": {"label": "Ages 9-10", "grade": "4-5"},
}

TOPIC_BANDS = {
    "addition": ["k1", "23", "45"],
    "subtraction": ["k1", "23", "45"],
    "multiplication": ["23", "45"],
    "fractions": ["23", "45"],
    "decimals": ["45"],
    "geometry": ["k1", "23", "45"],
    "algebra": ["45"],
    "speed_distance_time": ["45"],
}

CUTE_NAMES = {
    "cat": ["Mochi", "Whiskers", "Biscuit"],
    "kitten": ["Mochi", "Whiskers"],
    "dog": ["Biscuit", "Rex", "Buddy"],
    "puppy": ["Buddy", "Rex"],
    "bunny": ["Clover", "Thumper", "Coco"],
    "rabbit": ["Clover", "Thumper", "Coco"],
    "bird": ["Pip", "Sunny", "Feather"],
    "dragon": ["Ember", "Blaze", "Spark"],
    "robot": ["Bolt", "Gizmo", "Circuit"],
    "fish": ["Bubbles", "Finn", "Splash"],
    "bear": ["Honey", "Bruno", "Cocoa"],
    "unicorn": ["Sparkle", "Glimmer", "Stardust"],
    "fox": ["Ember", "Rusty", "Autumn"],
    "dinosaur": ["Rex", "Chomp", "Fern"],
    "mouse": ["Pip", "Squeak"],
    "frog": ["Lily", "Hop"],
    "turtle": ["Shelly", "Sheldon"],
}
DEFAULT_NAMES = ["Buddy", "Pip", "Nova", "Scout", "Ziggy"]

SETTING_KEYWORDS = [
    "river", "tree", "house", "bridge", "forest", "ocean", "sea",
    "mountain", "castle", "garden", "cave", "lake", "road", "field",
    "meadow", "cloud", "island", "volcano", "desert", "cliff",
]

# Visual vocabulary the frontend understands.
TINTS = ["mint", "lemon", "sky", "lav", "peach", "pink", "sage", "coral"]
SHAPES = [
    ("triangle", 3), ("square", 4), ("rectangle", 4),
    ("pentagon", 5), ("hexagon", 6), ("circle", 0),
]
PROP_KINDS = {
    "stone", "plank", "rope", "lantern", "basket", "berry", "gem", "sack",
    "signpost", "gate", "pie_gate", "raft", "clock", "fence_post", "tile",
    "droplet", "tick", "npc", "tree", "bridge",
}

# Every archetype name this module can emit, grouped by topic. story_engine
# uses these to ask for a specific mechanic on a specific beat.
ARCHETYPE_INDEX: dict[str, list[str]] = {}


def guess_character_name(objects: list[str]) -> str:
    for obj in objects or []:
        key = (obj or "").lower()
        for animal, names in CUTE_NAMES.items():
            if animal in key:
                return random.choice(names)
    return random.choice(DEFAULT_NAMES)


def pick_setting(objects: list[str]) -> str:
    for obj in objects or []:
        key = (obj or "").lower()
        for kw in SETTING_KEYWORDS:
            if kw in key:
                return kw
    return "meadow"


# --------------------------------------------------------------- primitives

def _scale(level: int, lo: float, hi: float) -> int:
    level = max(1, min(5, level))
    return round(lo + (hi - lo) * (level - 1) / 4)


def _lvl(level: int) -> int:
    return max(1, min(5, int(level or 1)))


def _stone(sid, label, value, correct, **extra) -> dict:
    s = {"id": sid, "label": "" if label is None else str(label),
         "value": value, "correct": bool(correct)}
    for k, v in extra.items():
        if v is not None:
            s[k] = v
    return s


def _prop(kind, x, y, scale=1.0, label="") -> dict:
    return {
        "kind": kind,
        "x_pct": round(float(x), 4),
        "y_pct": round(float(y), 4),
        "scale": round(float(scale), 3),
        "label": str(label),
    }


def _cluster(kind, n, cx, cy, label="", scale=1.0, cols=5,
             step_x=0.045, step_y=0.055, cap=24) -> list[dict]:
    """A tidy little pile of n identical props centred on (cx, cy)."""
    n = max(0, min(int(n), cap))
    if n == 0:
        return []
    cols = max(1, min(cols, n))
    rows = math.ceil(n / cols)
    out = []
    for i in range(n):
        r, c = divmod(i, cols)
        in_row = min(cols, n - r * cols)
        x = cx + (c - (in_row - 1) / 2) * step_x
        y = cy + (r - (rows - 1) / 2) * step_y
        out.append(_prop(kind, min(0.97, max(0.03, x)), min(0.95, max(0.08, y)), scale, label))
    return out


def _distractors(rng, correct, count, spread, allow_negative=False, cap=None):
    """Plausible near-misses: off-by-one/two/ten first, then random.

    `cap` keeps every option inside a range the age band can render (ages 4-6
    get pips, and nobody is counting 30 pips).
    """
    def ok(c):
        if c == correct:
            return False
        if not allow_negative and c < 0:
            return False
        if cap is not None and c > cap:
            return False
        return True

    vals = {correct}
    for d in rng.sample([1, -1, 2, -2, 3, -3, 5, -5, 10, -10], 10):
        if len(vals) >= count:
            break
        if ok(correct + d):
            vals.add(correct + d)
    tries = 0
    spread = max(2, int(spread))
    while len(vals) < count and tries < 400:
        tries += 1
        c = correct + rng.randint(-spread, spread)
        if ok(c):
            vals.add(c)
    # last resort: walk outward so we always return `count` options
    step = 1
    while len(vals) < count and step < 200:
        for c in (correct + step, correct - step):
            if len(vals) < count and ok(c):
                vals.add(c)
        step += 1
    return list(vals)


def _lay_row(rng, stones, y_lo=0.60, y_hi=0.72, x_lo=0.13, x_hi=0.87):
    """Place stones across the play area: one row, or a gentle zig-zag."""
    n = len(stones)
    if n == 0:
        return
    if n == 1:
        stones[0]["x_pct"], stones[0]["y_pct"] = 0.5, (y_lo + y_hi) / 2
        return
    zig = n > 5
    for i, s in enumerate(stones):
        s["x_pct"] = round(x_lo + (x_hi - x_lo) * i / (n - 1), 4)
        if zig:
            s["y_pct"] = round(y_lo if i % 2 == 0 else y_hi, 4)
        else:
            s["y_pct"] = round(rng.uniform(y_lo, y_hi), 4)


# Ages 4-6 cannot read numerals reliably, so every k1 answer is also emitted
# as `dots` (pips to count). k1 generators are range-limited below so that no
# option - correct or distractor - ever exceeds this.
K1_DOTS_MAX = 15


def _dots_max_for(band: str) -> int:
    return K1_DOTS_MAX if band == "k1" else 0


def _choice_stones(rng, correct, spread, count=4, formatter=str,
                   band="23", dots_max=None, allow_negative=False):
    """PICK-ONE: numbered stones, one of which is right.

    For ages 4-6 (`dots_max` > 0) values are ALSO emitted as `dots` so a
    pre-reader can count pips instead of decoding a numeral.
    """
    if dots_max is None:
        dots_max = _dots_max_for(band)
    vals = _distractors(rng, correct, count, spread, allow_negative,
                        cap=dots_max or None)
    rng.shuffle(vals)
    stones = []
    for i, v in enumerate(vals):
        dots = v if (dots_max and isinstance(v, int) and 0 <= v <= dots_max) else None
        stones.append(_stone(i, formatter(v), v, v == correct, dots=dots))
    _lay_row(rng, stones)
    return stones


def _base(question_type, grade_mode, prompt, narrative, explanation,
          stones, target_count, archetype, **extra) -> dict:
    ch = {
        "question_type": question_type,
        "grade_mode": grade_mode,
        "prompt": prompt,
        "narrative": narrative,
        "explanation": explanation,
        "target_count": target_count,
        "stones": stones,
        "answer_order": None,
        "answer_sum": None,
        "answer_group_count": None,
        "tolerance": 1e-4,
        "props": [],
        "play_area": {"top_pct": 0.55, "bottom_pct": 0.92},
        "archetype": archetype,
    }
    ch.update({k: v for k, v in extra.items() if v is not None})
    return ch


# ============================================================== ADDITION

def _a_berry_baskets(level, band, rng, char, setting):
    """PICK-ONE. Two visible piles of berries; the prompt counts THOSE piles."""
    if band == "k1":
        hi = _scale(level, 3, 6)          # keeps every option countable as pips
        a, b = rng.randint(1, hi), rng.randint(1, hi)
    elif band == "23":
        hi = _scale(level, 12, 60)
        a, b = rng.randint(5, hi), rng.randint(3, hi)
    else:
        hi = _scale(level, 120, 899)
        a, b = rng.randint(50, hi), rng.randint(40, hi)
    total = a + b

    props = [_prop("basket", 0.26, 0.40, 1.1, "red"), _prop("basket", 0.70, 0.40, 1.1, "blue")]
    if a + b <= 20:
        props += _cluster("berry", a, 0.26, 0.29, "red", 0.8)
        props += _cluster("berry", b, 0.70, 0.29, "blue", 0.8)
    else:
        props += [_prop("sack", 0.26, 0.29, 1.0, str(a)), _prop("sack", 0.70, 0.29, 1.0, str(b))]

    ch = _base(
        "single_choice", "set",
        f"{a} red berries and {b} blue berries. How many?",
        f"{char} tipped two berry baskets together beside the {setting}. "
        f"Count them all, then hop onto the right stone!",
        f"{a} + {b} = {total}.",
        _choice_stones(rng, total, max(2, total // 8 + 2), 4, band=band),
        1, "berry_baskets",
    )
    ch["props"] = props
    return ch


def _a_toll_gate(level, band, rng, char, setting):
    """PICK-A-SET (grade_mode sum). Pay a gate with gems that add to the toll."""
    n = 2 if level <= 2 else 3
    if band == "k1":
        parts = [rng.randint(1, 5) for _ in range(n)]
    elif band == "23":
        parts = [rng.randint(3, _scale(level, 9, 25)) for _ in range(n)]
    else:
        parts = [rng.randint(15, _scale(level, 40, 120)) for _ in range(n)]
    toll = sum(parts)

    # NB: every gem gets the SAME tint. Tinting the payable ones differently
    # would ship the answer key straight to the canvas.
    stones = [_stone(i, v, v, True, dots=v if (band == "k1" and v <= K1_DOTS_MAX) else None,
                     tint="lemon")
              for i, v in enumerate(parts)]
    for _ in range(3):
        d = rng.randint(1, max(2, toll // 2))
        stones.append(_stone(len(stones), d, d, False,
                             dots=d if (band == "k1" and d <= K1_DOTS_MAX) else None, tint="lemon"))
    rng.shuffle(stones)
    for i, s in enumerate(stones):
        s["id"] = i
    _lay_row(rng, stones, 0.58, 0.78)

    ch = _base(
        "multi_select", "sum",
        f"Pick gems that add up to exactly {toll}.",
        f"A stone gate blocks the path. Its toll is {toll} gems - "
        f"{char} must pick gems that add up to exactly that.",
        f"{' + '.join(str(p) for p in parts)} = {toll}.",
        stones, len(parts), "toll_gate",
        answer_sum=float(toll), simulate="gate_open",
    )
    ch["props"] = [_prop("gate", 0.5, 0.30, 1.4, str(toll))]
    return ch


def _a_plank_bridge(level, band, rng, char, setting):
    """PICK-ONE with a visible gap: planks you have + planks you found."""
    if band == "k1":
        a, b = rng.randint(2, _scale(level, 3, 6)), rng.randint(1, _scale(level, 3, 6))
    elif band == "23":
        a, b = rng.randint(8, _scale(level, 25, 95)), rng.randint(6, _scale(level, 20, 80))
    else:
        a, b = rng.randint(100, _scale(level, 300, 950)), rng.randint(80, _scale(level, 250, 900))
    total = a + b
    props = [_prop("bridge", 0.5, 0.52, 1.5, "gap")]
    if total <= 18:
        props += _cluster("plank", a, 0.22, 0.32, "carried", 0.85, cols=4)
        props += _cluster("plank", b, 0.76, 0.32, "found", 0.85, cols=4)
    else:
        props += [_prop("sack", 0.22, 0.32, 1.0, str(a)), _prop("sack", 0.76, 0.32, 1.0, str(b))]

    ch = _base(
        "single_choice", "set",
        f"{a} planks plus {b} more. How many planks?",
        f"{char} carried {a} planks to the broken bridge and found {b} more in the reeds.",
        f"{a} + {b} = {total}.",
        _choice_stones(rng, total, max(3, total // 8 + 2), 4, band=band),
        1, "plank_bridge", simulate="bridge_build",
    )
    ch["props"] = props
    return ch


def _a_berry_harvest(level, band, rng, char, setting):
    """COLLECT-N. Walk over a red patch AND a blue patch: a + b, embodied.

    The counter filling to a+b IS the addition - the child joins two sets by
    physically walking both of them, which is what joining two sets means.
    """
    if band == "k1":
        a, b = rng.randint(2, _scale(level, 3, 5)), rng.randint(1, _scale(level, 3, 5))
    else:
        a, b = rng.randint(3, _scale(level, 5, 9)), rng.randint(2, _scale(level, 4, 9))
    total = a + b

    stones = []
    for i in range(a):
        stones.append(_stone(len(stones), "", "red", True, tint="pink", group=0))
    for i in range(b):
        stones.append(_stone(len(stones), "", "blue", True, tint="sky", group=1))
    for _ in range(2):                       # thistles: do NOT pick
        stones.append(_stone(len(stones), "", "thistle", False, tint="sage", group=2))

    for s in stones:
        g = s["group"]
        members = [t for t in stones if t["group"] == g]
        k = members.index(s)
        cx = {0: 0.26, 1: 0.62, 2: 0.88}[g]
        r, c = divmod(k, 3)
        s["x_pct"] = round(cx + (c - 1) * 0.055 + rng.uniform(-0.01, 0.01), 4)
        s["y_pct"] = round(0.62 + r * 0.10, 4)

    ch = _base(
        "multi_select", "count",
        f"Pick all {a} red and {b} blue berries.",
        f"Two berry patches side by side in the {setting} - {a} red, {b} blue. "
        f"{char} wants every one of them, and none of the thistles.",
        f"{a} + {b} = {total} berries.",
        stones, total, "berry_harvest",
    )
    ch["props"] = [_prop("basket", 0.07, 0.68, 1.2, "basket"),
                   _prop("berry", 0.26, 0.55, 0.8, "red patch"),
                   _prop("berry", 0.62, 0.55, 0.8, "blue patch")]
    ch["play_area"] = {"top_pct": 0.56, "bottom_pct": 0.95}
    return ch


# =========================================================== SUBTRACTION

def _s_lanterns_out(level, band, rng, char, setting):
    """PICK-ONE. N lanterns are drawn; b of them are dark. How many still glow?"""
    if band == "k1":
        a = rng.randint(4, _scale(level, 6, 10))
        b = rng.randint(1, a - 1)
    elif band == "23":
        a = rng.randint(12, _scale(level, 30, 120))
        b = rng.randint(4, a - 1)
    else:
        a = rng.randint(150, _scale(level, 400, 950))
        b = rng.randint(40, a - 1)
    left = a - b

    props = []
    if a <= 16:
        props += _cluster("lantern", a - b, 0.33, 0.34, "lit", 0.9, cols=4)
        props += _cluster("lantern", b, 0.70, 0.34, "dark", 0.9, cols=4)
    else:
        props += [_prop("lantern", 0.33, 0.34, 1.2, f"{a} lit"),
                  _prop("lantern", 0.70, 0.34, 1.2, f"{b} out")]

    ch = _base(
        "single_choice", "set",
        f"{a} lanterns. {b} blew out. How many still glow?",
        f"A gust swept across the {setting} and snuffed {b} of {char}'s {a} lanterns.",
        f"{a} - {b} = {left}.",
        _choice_stones(rng, left, max(2, left // 6 + 2), 4, band=band),
        1, "lanterns_out", simulate="lantern_light",
    )
    ch["props"] = props
    return ch


def _s_pick_around_mushrooms(level, band, rng, char, setting):
    """COLLECT-N. Total items on the ground minus the bad ones = what to pick."""
    if band == "k1":
        total = rng.randint(5, _scale(level, 7, 11))
        bad = rng.randint(1, max(1, total // 3))
    else:
        total = rng.randint(8, _scale(level, 11, 16))
        bad = rng.randint(2, max(2, total // 3))
    good = total - bad

    stones = []
    for i in range(total):
        is_good = i < good
        stones.append(_stone(
            i, "", "berry" if is_good else "mushroom", is_good,
            tint="pink" if is_good else "sage",
        ))
    rng.shuffle(stones)
    for i, s in enumerate(stones):
        s["id"] = i
    # scatter them over the whole play area, not a neat row
    cols = 5
    for i, s in enumerate(stones):
        r, c = divmod(i, cols)
        s["x_pct"] = round(0.15 + 0.70 * (c / max(1, cols - 1)) + rng.uniform(-0.03, 0.03), 4)
        s["y_pct"] = round(0.60 + 0.11 * r + rng.uniform(-0.02, 0.02), 4)

    ch = _base(
        "multi_select", "count",
        f"{total} things grew. Skip {bad} mushroom{'s' if bad != 1 else ''}, pick berries.",
        f"{char} found {total} little things sprouting by the {setting} - "
        f"but {bad} of them are mushrooms. Only berries go in the basket!",
        f"{total} - {bad} = {good} berries.",
        stones, good, "pick_around_mushrooms",
    )
    ch["props"] = [_prop("basket", 0.08, 0.66, 1.2, "basket")]
    ch["play_area"] = {"top_pct": 0.55, "bottom_pct": 0.95}
    return ch


def _s_spend_gems(level, band, rng, char, setting):
    """PICK-ONE. A purse you can see, a price you can see."""
    if band == "k1":
        a = rng.randint(5, _scale(level, 8, 12))
        b = rng.randint(1, a - 1)
    elif band == "23":
        a = rng.randint(20, _scale(level, 60, 200))
        b = rng.randint(5, a - 1)
    else:
        a = rng.randint(200, _scale(level, 500, 990))
        b = rng.randint(60, a - 1)
    left = a - b

    ch = _base(
        "single_choice", "set",
        f"You have {a} gems. Spend {b}. How many left?",
        f"The ferry-keeper of the {setting} wants {b} gems from {char}'s purse of {a}.",
        f"{a} - {b} = {left}.",
        _choice_stones(rng, left, max(2, left // 6 + 2), 4, band=band),
        1, "spend_gems",
    )
    ch["props"] = [_prop("sack", 0.25, 0.34, 1.2, str(a)),
                   _prop("npc", 0.72, 0.36, 1.2, "ferry-keeper"),
                   _prop("gem", 0.60, 0.34, 0.9, str(b))]
    return ch


def _s_countdown_path(level, band, rng, char, setting):
    """ORDERED-WALK. Skip-count backwards across scattered stones.

    Not "walk to the numbered stone" - the child has to plan a route, and the
    decoys are only wrong because of where they fall in the sequence.
    """
    step = rng.randint(2, _scale(level, 3, 9))
    hops = 5
    start = step * rng.randint(hops, hops + _scale(level, 3, 14))
    seq = [start - step * i for i in range(hops)]

    values = list(seq)
    while len(values) < hops + 3:
        d = rng.choice([-1, 1, 2, -2, step + 1, -(step + 1)])
        cand = rng.choice(seq) + d
        if cand > 0 and cand not in values:
            values.append(cand)

    order = list(range(len(values)))
    rng.shuffle(order)                       # scramble ids so order != layout
    stones = []
    for new_id, idx in enumerate(order):
        stones.append(_stone(new_id, values[idx], values[idx], values[idx] in seq))
    id_of = {values[idx]: new_id for new_id, idx in enumerate(order)}

    slots = [(0.14, 0.62), (0.35, 0.86), (0.55, 0.60), (0.76, 0.84),
             (0.88, 0.62), (0.24, 0.76), (0.46, 0.70), (0.66, 0.90)]
    rng.shuffle(slots)
    for s, (x, y) in zip(stones, slots):
        s["x_pct"], s["y_pct"] = x, y

    ch = _base(
        "ordered_path", "order",
        f"Start at {start}. Step back by {step} each time.",
        f"The tide is coming in over the {setting}. Only the counting stones stay "
        f"above water - {char} must take them in the right order.",
        f"{' -> '.join(str(v) for v in seq)} (take {step} away each hop).",
        stones, hops, "countdown_path",
        answer_order=[id_of[v] for v in seq], simulate="water_rise",
    )
    ch["props"] = [_prop("signpost", 0.06, 0.55, 1.1, f"-{step}")]
    ch["play_area"] = {"top_pct": 0.55, "bottom_pct": 0.95}
    return ch


# ======================================================== MULTIPLICATION

def _m_plant_orchard(level, band, rng, char, setting):
    """COLLECT-N over an r x c grid: the array IS the multiplication."""
    if band == "23":
        r = rng.randint(2, _scale(level, 3, 5))
        c = rng.randint(2, _scale(level, 4, 7))
    else:
        r = rng.randint(3, _scale(level, 4, 6))
        c = rng.randint(4, _scale(level, 6, 9))
    total = r * c

    stones = []
    for i in range(r):
        for j in range(c):
            s = _stone(len(stones), "", (i, j), True, tint="sage")
            s["x_pct"] = round(0.16 + (0.68 * (j / max(1, c - 1)) if c > 1 else 0.34), 4)
            s["y_pct"] = round(0.58 + (0.30 * (i / max(1, r - 1)) if r > 1 else 0.15), 4)
            stones.append(s)

    ch = _base(
        "multi_select", "count",
        f"Plant every seed spot: {r} rows of {c}.",
        f"{char} is planting the orchard by the {setting}. Walk over all "
        f"{r} rows of {c} seed spots - miss none!",
        f"{r} x {c} = {total} seeds.",
        stones, total, "plant_orchard", simulate="seed_grow",
    )
    ch["props"] = [_prop("signpost", 0.06, 0.55, 1.1, f"{r} x {c}"),
                   _prop("tree", 0.93, 0.44, 1.2, "orchard")]
    ch["play_area"] = {"top_pct": 0.52, "bottom_pct": 0.95}
    return ch


def _m_rows_of_lanterns(level, band, rng, char, setting):
    """PICK-ONE, but the r rows of c lanterns are actually drawn."""
    if band == "23":
        r = rng.randint(2, _scale(level, 4, 9))
        c = rng.randint(2, 9)
    else:
        r = rng.randint(3, _scale(level, 9, 20))
        c = rng.randint(4, _scale(level, 9, 25))
    total = r * c

    props = []
    if total <= 40 and r <= 6 and c <= 8:
        for i in range(r):
            props += _cluster("lantern", c, 0.5, 0.20 + i * 0.055, "row", 0.62,
                              cols=c, step_x=0.06)
    else:
        props = [_prop("lantern", 0.5, 0.30, 1.4, f"{r} rows of {c}")]

    ch = _base(
        "single_choice", "set",
        f"{r} rows of {c} lanterns. How many lanterns?",
        f"The festival ropes above the {setting} hold {r} rows with {c} lanterns in each.",
        f"{r} x {c} = {total}.",
        _choice_stones(rng, total, max(3, total // 7 + 2), 4, band=band),
        1, "rows_of_lanterns",
    )
    ch["props"] = props
    return ch


def _m_equal_baskets(level, band, rng, char, setting):
    if band == "23":
        a = rng.randint(2, _scale(level, 4, 9))
        b = rng.randint(2, 9)
    else:
        a = rng.randint(4, _scale(level, 12, 30))
        b = rng.randint(5, _scale(level, 11, 22))
    total = a * b

    ch = _base(
        "single_choice", "set",
        f"{a} baskets hold {b} apples each. How many apples?",
        f"{char} lined up {a} baskets along the {setting}, and every single one holds {b} apples.",
        f"{a} x {b} = {total}.",
        _choice_stones(rng, total, max(3, total // 7 + 2), 4, band=band),
        1, "equal_baskets",
    )
    ch["props"] = _cluster("basket", min(a, 10), 0.5, 0.33, str(b), 0.9, cols=5, step_x=0.075)
    return ch


# ============================================================= FRACTIONS

def _f_safe_sandbars(level, band, rng, char, setting):
    """GROUP-SELECT. A fraction is n of d EQUAL groups - so draw d groups.

    Total is always d*k, so n/d of it is never a fractional number of stones.
    ANY n complete sandbars is a correct answer - that is what "n of d equal
    parts" means - so the fiction says "weigh down n/d of the crossing", not
    "find the secret safe ones". The grader in app.py agrees: it counts whole
    groups, it does not check which ones. A part-covered sandbar fails.
    """
    denom_options = {1: [2], 2: [2, 3], 3: [3, 4], 4: [4, 5, 6], 5: [6, 8]}[_lvl(level)]
    d = rng.choice(denom_options)
    n = rng.randint(1, d - 1)
    k = rng.choice([1, 2]) if level <= 2 else 2
    total = d * k
    safe_count = n * k

    stones = []
    for g in range(d):
        for m in range(k):
            s = _stone(len(stones), "", g, False, group=g, tint=TINTS[g % len(TINTS)])
            cx = 0.13 + 0.74 * (g / max(1, d - 1)) if d > 1 else 0.5
            s["x_pct"] = round(cx + (m - (k - 1) / 2) * 0.05, 4)
            s["y_pct"] = round(0.66 + (0.05 if (g % 2) else 0.0), 4)
            stones.append(s)

    # `correct` marks ONE valid answer, used only to show a worked example if
    # the child gets it wrong. Any n whole sandbars grades as correct.
    example_groups = set(rng.sample(range(d), n))
    for s in stones:
        s["correct"] = s["group"] in example_groups

    ch = _base(
        "multi_select", "groups",
        f"Step on {n} whole sandbar{'s' if n != 1 else ''} - that is {n}/{d}.",
        f"{char} must cross the {setting}! The crossing is {d} sandbars holding "
        f"{total} stones, and it only sinks level if {n}/{d} of it is weighed down.",
        f"{n}/{d} of {total} stones = {safe_count} stones - any {n} whole sandbars.",
        stones, safe_count, "safe_sandbars",
        answer_group_count=n, group_count=d,
    )
    ch["props"] = [_prop("signpost", 0.06, 0.56, 1.1, f"{n}/{d}")]
    return ch


def _f_fraction_of_berries(level, band, rng, char, setting):
    """COLLECT-N: pick n/d OF a visible pile. The pile size is in the prompt."""
    d = rng.choice({1: [2], 2: [2, 4], 3: [3, 4], 4: [4, 5], 5: [5, 6, 8]}[_lvl(level)])
    n = rng.randint(1, d - 1)
    k = rng.randint(2, 3)
    total = d * k
    want = n * k

    stones = []
    for i in range(total):
        s = _stone(i, "", "berry", True, tint="pink")
        r, c = divmod(i, 6)
        s["x_pct"] = round(0.16 + 0.68 * (c / 5) + rng.uniform(-0.02, 0.02), 4)
        s["y_pct"] = round(0.62 + 0.12 * r, 4)
        stones.append(s)

    ch = _base(
        "multi_select", "count",
        f"Pick {n}/{d} of these {total} berries.",
        f"{char} may take only {n}/{d} of the {total} moonberries growing by the {setting} - "
        f"the rest belong to the birds.",
        f"{n}/{d} of {total} = {want} berries ({total} / {d} = {k}, then {k} x {n} = {want}).",
        stones, want, "fraction_of_berries",
    )
    ch["props"] = [_prop("basket", 0.07, 0.68, 1.2, f"{n}/{d}")]
    ch["play_area"] = {"top_pct": 0.56, "bottom_pct": 0.95}
    return ch


def _f_pie_gate(level, band, rng, char, setting):
    """PICK-ONE. A gate split into d wedges with n glowing - name the fraction."""
    d = rng.choice({1: [2], 2: [2, 4], 3: [3, 4, 6], 4: [4, 6, 8], 5: [5, 6, 8, 10]}[_lvl(level)])
    n = rng.randint(1, d - 1)

    options = {(n, d)}
    while len(options) < 4:
        dd = rng.choice([d, d, max(2, d + rng.choice([-2, -1, 1, 2]))])
        nn = rng.randint(1, dd - 1)
        options.add((nn, dd))
    opts = list(options)
    rng.shuffle(opts)
    stones = [_stone(i, f"{a}/{b}", a / b, (a, b) == (n, d)) for i, (a, b) in enumerate(opts)]
    _lay_row(rng, stones)

    ch = _base(
        "single_choice", "set",
        f"{n} of the gate's {d} wedges glow. Which fraction?",
        f"A round moon-gate bars the {setting}. {char} must name the glowing part to open it.",
        f"{n} glowing out of {d} equal wedges = {n}/{d}.",
        stones, 1, "pie_gate", simulate="gate_open",
    )
    ch["props"] = [_prop("pie_gate", 0.5, 0.30, 1.6, f"{n}/{d}")]
    return ch


# ============================================================== DECIMALS

def _d_number_line_leap(level, band, rng, char, setting):
    """PICK-ONE by POSITION. Unlabelled lily pads along a labelled number line."""
    span = _scale(level, 3, 6)
    places = 1 if level <= 3 else 2
    step = 0.1 if places == 1 else 0.05
    choices = []
    guard = 0
    while len(choices) < 5 and guard < 500:
        guard += 1
        v = round(rng.randrange(1, int(span / step)) * step, places)
        if v <= 0 or v >= span:
            continue
        if any(abs(v - c) < step * 2 for c in choices):
            continue
        choices.append(v)
    choices.sort()
    target = rng.choice(choices)

    stones = []
    for i, v in enumerate(choices):
        s = _stone(i, "", v, abs(v - target) < 1e-9, tint="mint")
        s["x_pct"] = round(0.10 + 0.80 * (v / span), 4)
        s["y_pct"] = 0.70
        stones.append(s)

    props = [_prop("tick", 0.10 + 0.80 * (t / span), 0.60, 1.0, f"{t}")
             for t in range(span + 1)]

    ch = _base(
        "single_choice", "set",
        f"Leap to {target:.{places}f} on the number line.",
        f"Lily pads float along a measuring rope across the {setting}. "
        f"{char} must land on exactly the right spot.",
        f"{target:.{places}f} sits between {math.floor(target)} and {math.floor(target) + 1}.",
        stones, 1, "number_line_leap",
    )
    ch["props"] = props
    ch["play_area"] = {"top_pct": 0.64, "bottom_pct": 0.80}
    return ch


def _d_rain_gauge(level, band, rng, char, setting):
    """PICK-A-SET (sum) with decimal droplets."""
    places = 1 if level <= 3 else 2
    n = 2 if level <= 2 else 3
    unit = 10 ** places
    parts = [round(rng.randrange(3, 30 * (unit // 10)) / unit, places) for _ in range(n)]
    target = round(sum(parts), places)

    # Same tint for payable and decoy droplets - see _a_toll_gate.
    stones = [_stone(i, f"{v:.{places}f}", v, True, tint="sky") for i, v in enumerate(parts)]
    for _ in range(3):
        d = round(rng.randrange(3, 30 * (unit // 10)) / unit, places)
        stones.append(_stone(len(stones), f"{d:.{places}f}", d, False, tint="sky"))
    rng.shuffle(stones)
    for i, s in enumerate(stones):
        s["id"] = i
    _lay_row(rng, stones, 0.58, 0.78)

    ch = _base(
        "multi_select", "sum",
        f"Fill the gauge to exactly {target:.{places}f} litres.",
        f"{char}'s rain gauge must read exactly {target:.{places}f} litres before the "
        f"flood gate on the {setting} will open.",
        f"{' + '.join(f'{p:.{places}f}' for p in parts)} = {target:.{places}f}.",
        stones, len(parts), "rain_gauge",
        answer_sum=float(target), tolerance=0.5 / unit, simulate="water_rise",
    )
    ch["props"] = [_prop("droplet", 0.5, 0.28, 1.5, f"{target:.{places}f} L")]
    return ch


def _d_measure_rope(level, band, rng, char, setting):
    places = 1 if level <= 2 else 2
    unit = 10 ** places
    a = round(rng.randrange(2, 20 * unit) / unit, places)
    b = round(rng.randrange(2, 20 * unit) / unit, places)
    total = round(a + b, places)

    def fmt(v):
        return f"{v / unit:.{places}f}"

    stones = _choice_stones(rng, int(round(total * unit)),
                            spread=max(3, unit // 4), count=4, formatter=fmt, band=band)
    ch = _base(
        "single_choice", "set",
        f"Ropes {a:.{places}f} m and {b:.{places}f} m. Total length?",
        f"{char} knotted two ropes together to swing across the {setting}.",
        f"{a:.{places}f} + {b:.{places}f} = {total:.{places}f} m.",
        stones, 1, "measure_rope",
    )
    ch["props"] = [_prop("rope", 0.30, 0.36, 1.2, f"{a:.{places}f} m"),
                   _prop("rope", 0.68, 0.36, 1.2, f"{b:.{places}f} m")]
    return ch


# ============================================================== GEOMETRY

def _g_shape_door(level, band, rng, char, setting):
    """PICK-ONE with DRAWN polygons - a 4-year-old counts sides, reads nothing."""
    pool_size = max(3, min(len(SHAPES), _scale(level, 3, 5)))
    pool = rng.sample(SHAPES, pool_size)
    name, sides = rng.choice(pool)

    stones = []
    for i, (nm, sd) in enumerate(pool):
        stones.append(_stone(i, "", nm, nm == name, shape=nm,
                             tint=TINTS[i % len(TINTS)], dots=sd or None))
    rng.shuffle(stones)
    for i, s in enumerate(stones):
        s["id"] = i
    _lay_row(rng, stones, 0.62, 0.72)

    if sides == 0:
        prompt = "Which door is round with no corners?"
        expl = "A circle has no straight sides at all."
    else:
        prompt = f"Which door has exactly {sides} sides?"
        expl = f"A {name} has {sides} sides."

    ch = _base(
        "single_choice", "set", prompt,
        f"{char} found a row of carved doors in the {setting} wall. "
        f"Only one will swing open!",
        expl, stones, 1, "shape_door", simulate="gate_open",
    )
    ch["props"] = [_prop("gate", 0.5, 0.28, 1.3, "shape door")]
    return ch


def _g_walk_perimeter(level, band, rng, char, setting):
    """ORDERED-WALK. Four corner posts; walk the edge, rope pays out."""
    length = rng.randint(3, _scale(level, 5, 12))
    width = rng.randint(2, _scale(level, 4, 9))
    perim = 2 * (length + width)

    # corners in clockwise order: TL, TR, BR, BL
    xs = (0.22, 0.78)
    ys = (0.60, 0.88)
    corners = [(xs[0], ys[0]), (xs[1], ys[0]), (xs[1], ys[1]), (xs[0], ys[1])]
    edge_labels = [length, width, length, width]
    stones = []
    for i, (x, y) in enumerate(corners):
        s = _stone(i, "", i, True, tint="peach")
        s["x_pct"], s["y_pct"] = x, y
        stones.append(s)

    props = [_prop("fence_post", x, y, 1.0, f"corner {i + 1}")
             for i, (x, y) in enumerate(corners)]
    props += [
        _prop("rope", 0.50, ys[0], 1.0, f"{edge_labels[0]}"),
        _prop("rope", xs[1], (ys[0] + ys[1]) / 2, 1.0, f"{edge_labels[1]}"),
        _prop("rope", 0.50, ys[1], 1.0, f"{edge_labels[2]}"),
        _prop("rope", xs[0], (ys[0] + ys[1]) / 2, 1.0, f"{edge_labels[3]}"),
    ]

    ch = _base(
        "ordered_path", "order",
        f"Walk the fence: touch all 4 corners in order.",
        f"{char} is roping off a {length} by {width} garden by the {setting}. "
        f"Walk the whole edge without skipping a corner - the rope pays out as you go!",
        f"Perimeter = {length} + {width} + {length} + {width} = {perim} units.",
        stones, 4, "walk_perimeter",
        answer_order=[0, 1, 2, 3], order_cyclic=True, simulate="rope_pay",
        rope_lengths=edge_labels, perimeter=perim,
    )
    ch["props"] = props
    ch["play_area"] = {"top_pct": 0.55, "bottom_pct": 0.95}
    return ch


def _g_tile_the_floor(level, band, rng, char, setting):
    """COLLECT-N over an r x c tile floor - area you can feel underfoot."""
    r = rng.randint(2, _scale(level, 3, 5))
    c = rng.randint(2, _scale(level, 4, 7))
    total = r * c
    stones = []
    for i in range(r):
        for j in range(c):
            s = _stone(len(stones), "", (i, j), True, tint="lav", shape="square")
            s["x_pct"] = round(0.18 + (0.64 * (j / max(1, c - 1)) if c > 1 else 0.32), 4)
            s["y_pct"] = round(0.60 + (0.30 * (i / max(1, r - 1)) if r > 1 else 0.15), 4)
            stones.append(s)

    ch = _base(
        "multi_select", "count",
        f"Tile the whole floor: {r} rows of {c}.",
        f"The old bath-house floor by the {setting} needs every tile stepped into place.",
        f"Area = {r} x {c} = {total} tiles.",
        stones, total, "tile_the_floor",
    )
    ch["props"] = [_prop("signpost", 0.07, 0.56, 1.1, f"{r} x {c}"),
                   _prop("tile", 0.93, 0.56, 1.0, "spare")]
    ch["play_area"] = {"top_pct": 0.55, "bottom_pct": 0.95}
    return ch


def _g_garden_measure(level, band, rng, char, setting):
    """PICK-ONE: area or perimeter of a rectangle that is drawn on screen."""
    length = rng.randint(3, _scale(level, 7, 20))
    width = rng.randint(2, _scale(level, 5, 15))
    mode = rng.choice(["perimeter", "area"]) if level >= 2 else "perimeter"
    correct = 2 * (length + width) if mode == "perimeter" else length * width

    ch = _base(
        "single_choice", "set",
        f"Garden is {length} by {width}. What is the {mode}?",
        f"{char} paced out a garden plot beside the {setting}: {length} long, {width} wide.",
        (f"Perimeter = 2 x ({length} + {width}) = {correct}."
         if mode == "perimeter" else f"Area = {length} x {width} = {correct}."),
        _choice_stones(rng, correct, max(3, correct // 6), 4, band=band),
        1, "garden_measure",
    )
    ch["props"] = [_prop("fence_post", 0.30, 0.30, 1.0, str(length)),
                   _prop("fence_post", 0.70, 0.30, 1.0, str(width)),
                   _prop("rope", 0.50, 0.34, 1.4, f"{length} x {width}")]
    return ch


# =============================================================== ALGEBRA

def _al_mystery_sacks(level, band, rng, char, setting):
    """PICK-ONE. n identical sacks + c loose gems = b total. Sacks are drawn."""
    x = rng.randint(2, _scale(level, 6, 20))
    n = rng.randint(2, min(5, 2 + level))
    c = rng.randint(0, _scale(level, 4, 15))
    b = n * x + c

    if c:
        prompt = f"{n} equal sacks plus {c} gems make {b}. Sack size?"
        expl = f"({b} - {c}) / {n} = {n * x} / {n} = {x} gems per sack."
    else:
        prompt = f"{n} equal sacks hold {b} gems. Sack size?"
        expl = f"{b} / {n} = {x} gems per sack."

    ch = _base(
        "single_choice", "set", prompt,
        f"A troll by the {setting} shows {char} {n} sacks that all weigh the same"
        + (f", plus {c} loose gems" if c else "")
        + f". Altogether: {b} gems.",
        expl,
        _choice_stones(rng, x, max(2, x // 3 + 2), 4, band=band),
        1, "mystery_sacks",
    )
    props = _cluster("sack", n, 0.36, 0.33, "?", 1.0, cols=5, step_x=0.07)
    props += _cluster("gem", min(c, 12), 0.72, 0.33, "loose", 0.7, cols=4)
    props.append(_prop("signpost", 0.90, 0.33, 1.1, f"= {b}"))
    ch["props"] = props
    return ch


def _al_balance_bridge(level, band, rng, char, setting):
    """PICK-ONE. A see-saw bridge: both sides must weigh the same."""
    x = rng.randint(2, _scale(level, 8, 22))
    a = rng.randint(1, _scale(level, 8, 25))
    b = x + a
    ch = _base(
        "single_choice", "set",
        f"x + {a} = {b}. What is x?",
        f"A balance bridge over the {setting} holds {a} stones and one mystery crate on "
        f"the left, {b} stones on the right. {char} must match them.",
        f"x = {b} - {a} = {x}.",
        _choice_stones(rng, x, max(2, x // 3 + 2), 4, band=band),
        1, "balance_bridge",
    )
    ch["props"] = [_prop("bridge", 0.5, 0.40, 1.6, "balance"),
                   _prop("sack", 0.32, 0.30, 1.1, "x"),
                   _prop("gem", 0.42, 0.31, 0.8, str(a)),
                   _prop("gem", 0.68, 0.31, 0.9, str(b))]
    return ch


def _al_balance_scales(level, band, rng, char, setting):
    """PICK-A-SET (sum). Find the missing addend by loading the light pan."""
    a = rng.randint(3, _scale(level, 12, 40))
    parts = [rng.randint(2, _scale(level, 8, 25)) for _ in range(2 if level <= 3 else 3)]
    x = sum(parts)
    b = a + x

    stones = [_stone(i, v, v, True, tint="coral") for i, v in enumerate(parts)]
    for _ in range(3):
        d = rng.randint(2, max(3, x))
        stones.append(_stone(len(stones), d, d, False, tint="coral"))
    rng.shuffle(stones)
    for i, s in enumerate(stones):
        s["id"] = i
    _lay_row(rng, stones, 0.60, 0.80)

    ch = _base(
        "multi_select", "sum",
        f"Left pan holds {a}. Add gems to reach {b}.",
        f"A stone scale guards the {setting}. {char} must load the light pan until "
        f"both sides balance - not a gem over, not a gem under.",
        f"x = {b} - {a} = {x}, and {' + '.join(str(p) for p in parts)} = {x}.",
        stones, len(parts), "balance_scales",
        answer_sum=float(x), simulate="gate_open",
    )
    ch["props"] = [_prop("bridge", 0.5, 0.36, 1.6, "scales"),
                   _prop("gem", 0.32, 0.28, 1.0, str(a)),
                   _prop("signpost", 0.70, 0.28, 1.1, f"= {b}")]
    return ch


# ============================================== SPEED / DISTANCE / TIME

def _sdt_mile_markers(level, band, rng, char, setting):
    """COLLECT-N. Distance = speed x time, walked out one mile marker at a time."""
    speed = rng.randint(2, max(2, min(6, _scale(level, 2, 6))))
    t = rng.randint(2, max(2, min(5, _scale(level, 2, 5))))
    while speed * t > 24:
        t -= 1
    total = speed * t

    stones = []
    cols = min(total, 8)
    for i in range(total):
        r, c = divmod(i, cols)
        in_row = min(cols, total - r * cols)
        s = _stone(i, "", i + 1, True, tint="lemon")
        s["x_pct"] = round(0.12 + 0.76 * (c / max(1, in_row - 1)) if in_row > 1 else 0.5, 4)
        s["y_pct"] = round(0.62 + 0.11 * r, 4)
        stones.append(s)

    ch = _base(
        "multi_select", "count",
        f"Run {t} hours at {speed} mph. Touch every mile.",
        f"{char} is running the {setting} road. Every mile has a marker stone - "
        f"step on all of them and no more.",
        f"distance = {speed} x {t} = {total} miles.",
        stones, total, "mile_markers", simulate="raft_drift",
    )
    ch["props"] = [_prop("clock", 0.06, 0.52, 1.2, f"{t} h"),
                   _prop("signpost", 0.94, 0.52, 1.1, f"{speed} mph")]
    ch["play_area"] = {"top_pct": 0.56, "bottom_pct": 0.95}
    return ch



def _sdt_catch_the_raft(level, band, rng, char, setting):
    """PICK-ONE by POSITION. Predict where a drifting raft will be."""
    # Keep speed * t inside the bank so a landing post always exists.
    speed = rng.randint(1, max(1, min(3, _scale(level, 1, 3))))
    t = rng.randint(2, _scale(level, 3, 5))
    while speed * t > 10:
        t -= 1
    drift = speed * t
    posts = min(12, drift + 1 + rng.randint(0, 2))
    start = rng.randint(0, posts - 1 - drift)
    landing = start + drift

    stones = []
    for i in range(posts):
        s = _stone(i, str(i), i, i == landing, tint="sky")
        s["x_pct"] = round(0.08 + 0.84 * (i / (posts - 1)), 4)
        s["y_pct"] = 0.78
        stones.append(s)

    ch = _base(
        "single_choice", "set",
        f"Raft drifts {speed} posts a beat. Where after {t}?",
        f"The raft slipped loose at post {start} and drifts down the {setting}. "
        f"{char} must be standing where it arrives!",
        f"{start} + {speed} x {t} = {landing}. Stand at post {landing}.",
        stones, 1, "catch_the_raft", simulate="raft_drift",
    )
    ch["props"] = [_prop("raft", 0.08 + 0.84 * (start / (posts - 1)), 0.62, 1.2, f"post {start}")]
    ch["props"] += [_prop("fence_post", s["x_pct"], 0.70, 0.7, str(i))
                    for i, s in enumerate(stones)]
    ch["play_area"] = {"top_pct": 0.72, "bottom_pct": 0.88}
    return ch


def _sdt_clock_run(level, band, rng, char, setting):
    speed = rng.randint(2, _scale(level, 5, 15))
    time = rng.randint(2, _scale(level, 4, 12))
    distance = speed * time
    mode = rng.choice(["distance", "time", "speed"]) if level >= 3 else "distance"

    if mode == "distance":
        prompt = f"Running {speed} mph for {time} hours. How far?"
        correct, expl = distance, f"{speed} x {time} = {distance} miles."
    elif mode == "time":
        prompt = f"{distance} miles at {speed} mph. How many hours?"
        correct, expl = time, f"{distance} / {speed} = {time} hours."
    else:
        prompt = f"{distance} miles in {time} hours. What speed?"
        correct, expl = speed, f"{distance} / {time} = {speed} mph."

    ch = _base(
        "single_choice", "set", prompt,
        f"{char} is racing the sunset across the {setting}. Work it out and stand on the answer!",
        expl,
        _choice_stones(rng, correct, max(2, correct // 5 + 2), 4, band=band),
        1, "clock_run",
    )
    ch["props"] = [_prop("clock", 0.5, 0.28, 1.4, f"{time} h"),
                   _prop("signpost", 0.80, 0.34, 1.1, f"{speed} mph")]
    return ch


# ------------------------------------------------------------- registry

_ARCHETYPES: dict[str, list[tuple[str, tuple[str, ...], object]]] = {
    "addition": [
        ("berry_baskets", ("k1", "23", "45"), _a_berry_baskets),
        ("toll_gate", ("k1", "23", "45"), _a_toll_gate),
        ("plank_bridge", ("k1", "23", "45"), _a_plank_bridge),
        ("berry_harvest", ("k1", "23"), _a_berry_harvest),
    ],
    "subtraction": [
        ("lanterns_out", ("k1", "23", "45"), _s_lanterns_out),
        ("pick_around_mushrooms", ("k1", "23"), _s_pick_around_mushrooms),
        ("spend_gems", ("k1", "23", "45"), _s_spend_gems),
        ("countdown_path", ("23", "45"), _s_countdown_path),
    ],
    "multiplication": [
        ("plant_orchard", ("23", "45"), _m_plant_orchard),
        ("rows_of_lanterns", ("23", "45"), _m_rows_of_lanterns),
        ("equal_baskets", ("23", "45"), _m_equal_baskets),
    ],
    "fractions": [
        ("safe_sandbars", ("23", "45"), _f_safe_sandbars),
        ("fraction_of_berries", ("23", "45"), _f_fraction_of_berries),
        ("pie_gate", ("23", "45"), _f_pie_gate),
    ],
    "decimals": [
        ("number_line_leap", ("45",), _d_number_line_leap),
        ("rain_gauge", ("45",), _d_rain_gauge),
        ("measure_rope", ("45",), _d_measure_rope),
    ],
    "geometry": [
        ("shape_door", ("k1", "23", "45"), _g_shape_door),
        ("walk_perimeter", ("k1", "23", "45"), _g_walk_perimeter),
        ("tile_the_floor", ("k1", "23", "45"), _g_tile_the_floor),
        ("garden_measure", ("23", "45"), _g_garden_measure),
    ],
    "algebra": [
        ("mystery_sacks", ("45",), _al_mystery_sacks),
        ("balance_bridge", ("45",), _al_balance_bridge),
        ("balance_scales", ("45",), _al_balance_scales),
    ],
    "speed_distance_time": [
        ("catch_the_raft", ("45",), _sdt_catch_the_raft),
        ("clock_run", ("45",), _sdt_clock_run),
        ("mile_markers", ("45",), _sdt_mile_markers),
    ],
}

# Ages 4-6 must be playable without reading. These archetypes carry no words
# on the stones (dots, shapes, positions only).
NON_READING_ARCHETYPES = {
    "berry_baskets", "toll_gate", "plank_bridge", "berry_harvest", "lanterns_out",
    "pick_around_mushrooms", "spend_gems", "shape_door", "walk_perimeter",
    "tile_the_floor",
}

for _t, _lst in _ARCHETYPES.items():
    ARCHETYPE_INDEX[_t] = [nm for nm, _b, _f in _lst]


def archetypes_for(topic: str, band: str) -> list[str]:
    return [nm for nm, bands, _f in _ARCHETYPES.get(topic, []) if band in bands]


# ------------------------------------------------- navigational obstacles

_OBSTACLE_KINDS = ["tree", "fence_post", "rope", "gate"]


def _add_navigation_obstacles(challenge: dict, rng: random.Random, count: int) -> None:
    """Product brief: "more obstacles when the child makes more mistakes."

    Reconciliation: the obstacles are NAVIGATIONAL (scenery to walk around),
    never extra wrong answers. A struggling child gets a longer, twistier walk
    but an EASIER sum. Difficulty of the *math* is handled separately, and it
    moves DOWN on mistakes. See app.py / story_engine.py.
    """
    if count <= 0:
        return
    area = challenge.get("play_area") or {"top_pct": 0.55, "bottom_pct": 0.92}
    taken = [(s.get("x_pct"), s.get("y_pct")) for s in challenge["stones"]]
    placed = 0
    guard = 0
    while placed < count and guard < 200:
        guard += 1
        x = rng.uniform(0.10, 0.90)
        y = rng.uniform(area["top_pct"] + 0.02, area["bottom_pct"] - 0.02)
        if any(sx is not None and abs(sx - x) < 0.09 and abs((sy or 0) - y) < 0.07
               for sx, sy in taken):
            continue
        taken.append((x, y))
        challenge["props"].append(
            _prop(rng.choice(_OBSTACLE_KINDS), x, y, 0.9, "obstacle"))
        placed += 1
    challenge["obstacle_count"] = placed


# ------------------------------------------------------------ public API

def generate_challenge(topic, band, level, character_name=None, objects=None,
                       mistakes_total=0, seed=None, archetype=None,
                       nav_obstacles=None, exclude_archetypes=()):
    """Build one fully-specified challenge.

    `archetype` asks for a specific mechanic (story_engine uses this so a beat
    always plays the way its narration promises). Unknown/unavailable names
    fall back to a random valid archetype for the band.
    """
    if topic not in TOPICS:
        raise ValueError(f"Unknown topic: {topic}")
    if band not in TOPIC_BANDS.get(topic, []):
        band = TOPIC_BANDS[topic][0]

    rng = random.Random(seed)
    char = character_name or guess_character_name(objects or [])
    setting = pick_setting(objects or [])
    level = _lvl(level)

    available = [(nm, fn) for nm, bands, fn in _ARCHETYPES[topic] if band in bands]
    if band == "k1":
        pre = [(nm, fn) for nm, fn in available if nm in NON_READING_ARCHETYPES]
        if pre:
            available = pre
    chosen = None
    if archetype:
        chosen = next(((nm, fn) for nm, fn in available if nm == archetype), None)
    if chosen is None:
        pool = [(nm, fn) for nm, fn in available if nm not in set(exclude_archetypes)]
        chosen = rng.choice(pool or available)

    challenge = chosen[1](level, band, rng, char, setting)

    if nav_obstacles is None:
        nav_obstacles = min(int(mistakes_total or 0), 5)
    _add_navigation_obstacles(challenge, rng, nav_obstacles)

    challenge["topic"] = topic
    challenge["band"] = band
    challenge["level"] = level
    challenge["character_name"] = char
    challenge["setting"] = setting
    challenge.setdefault("obstacle_count", 0)
    return challenge


# ---------------------------------------------------------- adaptation

def next_level(current_level, was_correct, consecutive_correct, consecutive_wrong):
    """Legacy signature, kept so existing callers keep working."""
    lvl, _reason = adapt_level(current_level, was_correct,
                               consecutive_correct, consecutive_wrong)
    return lvl


def adapt_level(current_level, was_correct, consecutive_correct, consecutive_wrong):
    """Return (new_level, reason_code).

    Reason codes are turned into kid-facing sentences in app.py, so the game
    can narrate its own adaptation instead of silently changing.
    """
    current_level = _lvl(current_level)
    if was_correct and consecutive_correct >= 3:
        return min(5, current_level + 1), "streak_up"
    if was_correct and consecutive_correct >= 2:
        return min(5, current_level + 1), "two_right_up"
    if not was_correct and consecutive_wrong >= 2:
        return max(1, current_level - 1), "two_wrong_down"
    if not was_correct:
        return current_level, "hold_after_slip"
    return current_level, "hold"
