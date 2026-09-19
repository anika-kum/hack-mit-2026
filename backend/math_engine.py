"""
do-IT-oodle - deterministic math challenge generator.

Design rules (these are the whole point of this file):

1. THE MATH ARISES FROM THE FICTION.
   Every number in `prompt` refers to objects the child can SEE in the scene.
   If the prompt says "8 stepping stones", the challenge emits 8 stone props.
   Props are the fiction; stones are the things the child walks onto.

2. `prompt` IS THE QUESTION.
   Short (<= 12 words), concrete, with units. It is rendered in an
   always-visible banner. `narrative` is flavour and may be replaced by AI.

3. EVERY ARCHETYPE MUST REQUIRE ARITHMETIC.
   Six primitives - PICK-ONE, PICK-A-SET, COUNT-OUT, ORDERED-WALK,
   GROUP-SELECT and TYPE-IT (free response) - mapped onto named archetypes,
   at least two per topic x band and at least one that is not PICK-ONE.

   *** 2026-09-19 CULL. *** Six archetypes were deleted because they were
   "touch and choose": the child walked onto or tapped visible things and
   never computed anything. They are listed in DELETED_ARCHETYPES below and
   must not come back. The replacement pattern is COUNT-OUT: the scene holds
   MORE collectables than the answer, so the child has to work out how many
   to take. Counting out 3 x 4 = 12 seeds from a patch of 16 IS
   multiplication; walking over a 3 x 4 grid of exactly 12 seeds is not.

4. NOTHING HERE CALLS AN LLM. Correctness is guaranteed and generation is
   instant. prompts.py may *re-narrate* a challenge, never re-compute it.

5. NUMBER RANGES MAY BE CALIBRATED from the team's CSV question bank (see
   question_bank.py). That only ever nudges the magnitude of the operands -
   the arithmetic, the answer key and the fiction stay right here.
"""

import math
import random
import threading

try:                                   # the bank is optional by design
    import question_bank
except Exception:                      # pragma: no cover - import guard only
    question_bank = None

TOPICS = {
    "counting_and_comparing": "Counting & Comparing",
    "addition": "Addition",
    "subtraction": "Subtraction",
    "time_and_money": "Time & Money",
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
    # The team's question bank ships grade-1 counting/comparing and grade-2
    # time/money. Both are real curriculum topics for the younger bands, so
    # they are first-class here rather than being dropped on the floor.
    "counting_and_comparing": ["k1", "23"],
    "time_and_money": ["k1", "23"],
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


# Deleted 2026-09-19 after playtest feedback ("not actually math"). Kept as a
# name list so nothing - registry, story engine, tests - can quietly resurrect
# one, and so the reason survives the next person to read this file.
DELETED_ARCHETYPES = {
    "berry_harvest":          "collect-N over two berry patches; tapping every "
                              "visible berry, no addition performed",
    "pick_around_mushrooms":  "tap the berries, skip the mushrooms; visual "
                              "sorting, no subtraction performed",
    "plant_orchard":          "walk every cell of an r x c grid that already "
                              "had exactly r*c cells; no multiplication",
    "tile_the_floor":         "same as plant_orchard with tiles; no area work",
    "walk_perimeter":         "touch 4 corner posts in order; the perimeter "
                              "only ever appeared in the explanation",
    "mile_markers":           "touch every mile stone that was drawn for you; "
                              "distance = speed x time was never computed",
}


# --------------------------------------------------------------- primitives

def _scale(level: int, lo: float, hi: float) -> int:
    level = max(1, min(5, level))
    return round(lo + (hi - lo) * (level - 1) / 4)


def _lvl(level: int) -> int:
    return max(1, min(5, int(level or 1)))


# ------------------------------------------------------- CSV calibration
#
# The generators below are called as `fn(level, band, rng, char, setting)` and
# have been for the life of the project. Rather than thread `topic` through
# twenty signatures, generate_challenge parks the current (topic, band, level)
# on a thread-local and `_cal()` reads it. Thread-local, not a plain global,
# because challenges are generated on FastAPI's request threads and two
# children must never calibrate each other's numbers.
#
# When math_questions/ is absent - which is the normal case, and the one the
# demo runs on - `_cal` is the exact identity function.

_CTX = threading.local()


def _cal(lo: int, hi: int) -> tuple[int, int]:
    """Nudge a hard-coded operand range toward the real question bank."""
    if question_bank is None:
        return lo, hi
    topic = getattr(_CTX, "topic", None)
    band = getattr(_CTX, "band", None)
    level = getattr(_CTX, "level", None)
    if not topic or not band or not level:
        return lo, hi
    try:
        return question_bank.scale_range(lo, hi, topic, band, level)
    except Exception:
        return lo, hi


def _cal_scale(level: int, lo: float, hi: float) -> int:
    """`_scale` with the top of the range calibrated against the bank."""
    clo, chi = _cal(int(lo), int(hi))
    return _scale(level, clo, chi)


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


# --------------------------------------------------------------- COUNT-OUT
#
# The replacement for the deleted collect-N archetypes. The scene holds MORE
# collectables than the answer, and every one of them is collectable - so
# there is nothing to spot, only something to work out. "Take 3 x 4 acorns"
# from a patch of sixteen is multiplication; walking a 3 x 4 grid is not.

# Counting out twenty things is tedious, not hard, and the answer tray has to
# render one card per item. These caps keep both honest.
_COUNT_OUT_ANSWER_CAP = {"k1": 8, "23": 12, "45": 14}
_COUNT_OUT_TOTAL_CAP = {"k1": 12, "23": 16, "45": 18}


def count_out_cap(band: str) -> int:
    """The largest answer a COUNT-OUT archetype may ask a band to count."""
    return _COUNT_OUT_ANSWER_CAP.get(band, 12)


def _count_out_stones(rng, want, band, tint="sage"):
    want = max(1, int(want))
    spare = rng.randint(3, 5)
    total = min(want + spare, _COUNT_OUT_TOTAL_CAP.get(band, 16))
    total = max(total, want + 2)          # there must ALWAYS be spares to leave
    stones = [_stone(i, "", "item", True, tint=tint) for i in range(total)]
    cols = min(6, max(3, math.ceil(math.sqrt(total * 1.6))))
    for i, s in enumerate(stones):
        r, c = divmod(i, cols)
        in_row = min(cols, total - r * cols)
        s["x_pct"] = round(0.17 + 0.66 * (c / max(1, in_row - 1))
                           if in_row > 1 else 0.5, 4)
        s["y_pct"] = round(0.60 + 0.105 * r + rng.uniform(-0.014, 0.014), 4)
    return stones


def _count_out(prompt, narrative, explanation, want, archetype, band, rng,
               tint="sage", props=None):
    ch = _base(
        "collect_count", "count", prompt, narrative, explanation,
        _count_out_stones(rng, want, band, tint), int(want), archetype,
    )
    ch["props"] = props or []
    ch["play_area"] = {"top_pct": 0.55, "bottom_pct": 0.95}
    return ch


# ------------------------------------------------------------ FREE RESPONSE
#
# Typed numeric answers, graded deterministically in app.py against
# `answer_value` with `tolerance`. Never offered to ages 4-6: a pre-reader
# hunting for the 7 key is a child who has stopped doing maths. Answers are
# always short - one number, at most two decimal places.

def _free_response(prompt, narrative, explanation, value, archetype,
                   places=0, unit="", props=None):
    tol = (0.5 * 10 ** (-places)) if places else 1e-6
    ch = _base(
        "free_response", "value", prompt, narrative, explanation,
        [], 1, archetype,
        answer_value=round(float(value), 6),
        tolerance=tol,
        decimals=int(places),
        unit=unit or "",
    )
    ch["props"] = props or []
    ch["play_area"] = {"top_pct": 0.55, "bottom_pct": 0.92}
    return ch


# ============================================================== ADDITION

def _a_berry_baskets(level, band, rng, char, setting):
    """PICK-ONE. Two visible piles of berries; the prompt counts THOSE piles."""
    if band == "k1":
        hi = _scale(level, 3, 6)          # keeps every option countable as pips
        a, b = rng.randint(1, hi), rng.randint(1, hi)
    elif band == "23":
        hi = _cal_scale(level, 12, 60)
        a, b = rng.randint(5, max(6, hi)), rng.randint(3, max(4, hi))
    else:
        hi = _cal_scale(level, 120, 899)
        a, b = rng.randint(50, max(51, hi)), rng.randint(40, max(41, hi))
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
        a = rng.randint(8, max(9, _cal_scale(level, 25, 95)))
        b = rng.randint(6, max(7, _cal_scale(level, 20, 80)))
    else:
        a = rng.randint(100, max(101, _cal_scale(level, 300, 950)))
        b = rng.randint(80, max(81, _cal_scale(level, 250, 900)))
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


def _a_acorn_count(level, band, rng, char, setting):
    """COUNT-OUT. The patch holds more acorns than the answer.

    Replaces the deleted `berry_harvest`. There, every berry on screen was a
    berry you wanted, so the child tapped all of them and never added
    anything. Here the pile is deliberately too big: to stop at a + b you
    have to know what a + b is.
    """
    cap = count_out_cap(band)
    hi = max(2, min(cap - 2, _scale(level, 3, cap - 2)))
    a = rng.randint(1, hi)
    b = rng.randint(1, max(1, min(hi, cap - a)))
    total = a + b

    return _count_out(
        f"Take {a} acorns, then {b} more.",
        f"{char} found an acorn patch beside the {setting} - far more acorns "
        f"than one traveller needs. Take {a}, then {b} more, and leave the rest.",
        f"{a} + {b} = {total} acorns.",
        total, "acorn_count", band, rng, tint="lemon",
        props=[_prop("basket", 0.07, 0.68, 1.2, "basket"),
               _prop("tree", 0.92, 0.42, 1.1, "oak")],
    )


def _a_sum_scroll(level, band, rng, char, setting):
    """TYPE-IT. Free response: the child types the total."""
    if band == "23":
        lo, hi = _cal(6, _scale(level, 20, 90))
    else:
        lo, hi = _cal(40, _scale(level, 150, 900))
    a = rng.randint(lo, max(lo + 1, hi))
    b = rng.randint(lo, max(lo + 1, hi))
    total = a + b
    return _free_response(
        f"{a} + {b} = ?",
        f"A ferryman's tally-scroll bars the {setting}. {char} must write the "
        f"total in the empty box before the ferry will move.",
        f"{a} + {b} = {total}.",
        total, "sum_scroll",
        props=[_prop("signpost", 0.5, 0.30, 1.3, f"{a} + {b}"),
               _prop("npc", 0.80, 0.40, 1.1, "ferryman")],
    )


# =========================================================== SUBTRACTION

def _s_lanterns_out(level, band, rng, char, setting):
    """PICK-ONE. N lanterns are drawn; b of them are dark. How many still glow?"""
    if band == "k1":
        a = rng.randint(4, _scale(level, 6, 10))
        b = rng.randint(1, a - 1)
    elif band == "23":
        a = rng.randint(12, max(13, _cal_scale(level, 30, 120)))
        b = rng.randint(4, a - 1)
    else:
        a = rng.randint(150, max(151, _cal_scale(level, 400, 950)))
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


def _s_stones_left(level, band, rng, char, setting):
    """COUNT-OUT. Take away b from a, then count out what is LEFT.

    Replaces the deleted `pick_around_mushrooms`, where the mushrooms simply
    looked like mushrooms and the child never subtracted anything.
    """
    cap = count_out_cap(band)
    a = rng.randint(3, max(4, min(cap, _scale(level, 5, cap))))
    b = rng.randint(1, max(1, a - 1))
    left = a - b

    return _count_out(
        f"{a} stones. The tide took {b}. Step on the rest.",
        f"{char} counted {a} stepping stones across the {setting} at dawn. "
        f"The tide has swallowed {b} of them. Step on every stone still dry - "
        f"and only those.",
        f"{a} - {b} = {left} stones left.",
        left, "stones_left", band, rng, tint="sky",
        props=[_prop("signpost", 0.07, 0.55, 1.1, f"{a}-{b}")],
    )


def _s_tally_scroll(level, band, rng, char, setting):
    """TYPE-IT. Free response subtraction."""
    if band == "23":
        hi = max(12, _cal_scale(level, 30, 120))
        a = rng.randint(12, hi)
    else:
        hi = max(150, _cal_scale(level, 400, 950))
        a = rng.randint(150, hi)
    b = rng.randint(2, max(3, a - 1))
    left = a - b
    return _free_response(
        f"{a} - {b} = ?",
        f"A toll-keeper's slate stands at the edge of the {setting}. {char} "
        f"must write what is left before the path will open.",
        f"{a} - {b} = {left}.",
        left, "tally_scroll",
        props=[_prop("signpost", 0.5, 0.30, 1.3, f"{a} - {b}"),
               _prop("gate", 0.5, 0.60, 1.0, "toll")],
    )


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

def _m_orchard_count(level, band, rng, char, setting):
    """COUNT-OUT. Take r x c seeds from a sack that holds far more.

    Replaces the deleted `plant_orchard`, which laid out exactly r x c holes
    and asked the child to step in all of them - a walk, not a product.
    """
    cap = count_out_cap(band)
    r = rng.randint(2, max(2, min(4, _scale(level, 2, 4))))
    c = rng.randint(2, max(2, min(cap // r, _scale(level, 3, 6))))
    total = r * c

    return _count_out(
        f"Plant {r} rows of {c} seeds. Take that many.",
        f"The seed sack by the {setting} is heavy with far more than {char} "
        f"needs. Count out exactly enough for {r} rows of {c} - no more.",
        f"{r} x {c} = {total} seeds.",
        total, "orchard_count", band, rng, tint="sage",
        props=[_prop("sack", 0.07, 0.60, 1.2, "seed"),
               _prop("signpost", 0.93, 0.55, 1.0, f"{r}x{c}")],
    )


def _m_product_scroll(level, band, rng, char, setting):
    """TYPE-IT. Free response multiplication."""
    if band == "23":
        a = rng.randint(2, max(3, min(12, _cal_scale(level, 4, 10))))
        b = rng.randint(2, max(3, min(12, _cal_scale(level, 5, 12))))
    else:
        a = rng.randint(4, max(5, min(40, _cal_scale(level, 8, 25))))
        b = rng.randint(3, max(4, min(20, _cal_scale(level, 6, 15))))
    total = a * b
    return _free_response(
        f"{a} x {b} = ?",
        f"A miller's counting-board blocks the mill door on the {setting}. "
        f"{char} must write the product to get inside.",
        f"{a} x {b} = {total}.",
        total, "product_scroll",
        props=[_prop("signpost", 0.5, 0.30, 1.3, f"{a} x {b}"),
               _prop("gate", 0.5, 0.62, 1.0, "mill")],
    )


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


def _f_fraction_scroll(level, band, rng, char, setting):
    """TYPE-IT. "What is n/d of T?" - always a whole-number answer."""
    d = rng.choice({1: [2], 2: [2, 4], 3: [3, 4], 4: [4, 5, 6], 5: [5, 6, 8]}[_lvl(level)])
    n = rng.randint(1, d - 1)
    k = rng.randint(2, max(2, min(12, _cal_scale(level, 3, 9))))
    total = d * k
    want = n * k
    return _free_response(
        f"What is {n}/{d} of {total}?",
        f"A miller's ledger hangs by the {setting}. {char} must write the "
        f"share exactly, or the wheel stays still.",
        f"{total} / {d} = {k}, then {k} x {n} = {want}.",
        want, "fraction_scroll",
        props=[_prop("pie_gate", 0.5, 0.30, 1.5, str(d)),
               _prop("signpost", 0.82, 0.40, 1.0, f"{n}/{d}")],
    )


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


def _d_gauge_scroll(level, band, rng, char, setting):
    """TYPE-IT. Decimal addition, typed - tolerance handles the rounding."""
    places = 1 if level <= 3 else 2
    unit = 10 ** places
    a = round(rng.randrange(5, 40 * unit) / unit, places)
    b = round(rng.randrange(5, 40 * unit) / unit, places)
    total = round(a + b, places)
    return _free_response(
        f"{a:.{places}f} + {b:.{places}f} = ?",
        f"The flood-gauge on the {setting} needs a reading. {char} must write "
        f"the total depth in litres.",
        f"{a:.{places}f} + {b:.{places}f} = {total:.{places}f}.",
        total, "gauge_scroll", places=places, unit="L",
        props=[_prop("droplet", 0.34, 0.32, 1.3, f"{a:.{places}f}"),
               _prop("droplet", 0.66, 0.32, 1.3, f"{b:.{places}f}")],
    )


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


def _g_fence_posts(level, band, rng, char, setting):
    """COUNT-OUT the PERIMETER from a pile that holds more posts than needed.

    Replaces the deleted `walk_perimeter` (touch four corners in order - the
    perimeter only ever appeared in the explanation) and `tile_the_floor`
    (step on every tile that was already drawn for you). Here the arithmetic
    is unavoidable: nothing on screen tells you 2 x (3 + 2) is ten.
    """
    cap = count_out_cap(band)
    # Keep 2*(L+W) inside the band's counting cap.
    half = max(2, cap // 2)
    length = rng.randint(1, max(1, min(half - 1, _scale(level, 2, half - 1))))
    width = rng.randint(1, max(1, half - length))
    perim = 2 * (length + width)

    return _count_out(
        f"Fence a {length} by {width} plot. One post per step.",
        f"{char} is roping off a {length} by {width} garden beside the "
        f"{setting}. Take one post for every step around the edge - the pile "
        f"holds plenty more than that.",
        f"Perimeter = {length} + {width} + {length} + {width} = {perim} posts.",
        perim, "fence_posts", band, rng, tint="peach",
        props=[_prop("fence_post", 0.07, 0.58, 1.1, "pile"),
               _prop("rope", 0.92, 0.55, 1.0, f"{length}x{width}")],
    )


def _g_survey_scroll(level, band, rng, char, setting):
    """TYPE-IT. Area or perimeter of a rectangle, typed."""
    length = rng.randint(3, max(4, min(40, _cal_scale(level, 7, 20))))
    width = rng.randint(2, max(3, min(30, _cal_scale(level, 5, 15))))
    mode = rng.choice(["perimeter", "area"]) if level >= 2 else "perimeter"
    value = 2 * (length + width) if mode == "perimeter" else length * width
    return _free_response(
        f"A {length} by {width} plot. What is the {mode}?",
        f"A surveyor's slate leans on the wall by the {setting}. {char} must "
        f"write the answer in chalk before the gate unlocks.",
        (f"Perimeter = 2 x ({length} + {width}) = {value}."
         if mode == "perimeter" else f"Area = {length} x {width} = {value}."),
        value, "survey_scroll",
        props=[_prop("fence_post", 0.32, 0.32, 1.0, str(length)),
               _prop("fence_post", 0.68, 0.32, 1.0, str(width)),
               _prop("rope", 0.50, 0.38, 1.4, f"{length} x {width}")],
    )


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


def _al_rune_scroll(level, band, rng, char, setting):
    """TYPE-IT. Solve for the rune and write its value."""
    x = rng.randint(2, max(3, min(40, _cal_scale(level, 8, 25))))
    m = rng.randint(2, max(2, min(6, 1 + level)))
    c = rng.randint(1, max(2, _cal_scale(level, 5, 20)))
    if level <= 2:
        b = x + c
        prompt = f"? + {c} = {b}. What is the missing number?"
        expl = f"{b} - {c} = {x}."
    else:
        b = m * x + c
        prompt = f"{m} x ? + {c} = {b}. Find the number."
        expl = f"({b} - {c}) / {m} = {m * x} / {m} = {x}."
    return _free_response(
        prompt,
        f"A carved rune-stone blocks the way past the {setting}. {char} must "
        f"chalk the missing number onto it to make it roll aside.",
        expl, x, "rune_scroll",
        props=[_prop("signpost", 0.5, 0.30, 1.4, "?"),
               _prop("sack", 0.30, 0.40, 1.0, "?")],
    )


# ============================================== SPEED / DISTANCE / TIME

def _sdt_mile_count(level, band, rng, char, setting):
    """COUNT-OUT. Take one marker per mile from a pile holding more.

    Replaces the deleted `mile_markers`, which drew exactly speed x time
    stones and asked the child to touch all of them - the answer was the
    layout, so nobody ever multiplied.
    """
    cap = count_out_cap(band)
    speed = rng.randint(2, max(2, min(6, _scale(level, 2, 6))))
    t = rng.randint(2, max(2, min(5, _scale(level, 2, 5))))
    while speed * t > cap and t > 1:
        t -= 1
    while speed * t > cap and speed > 1:
        speed -= 1
    total = speed * t

    return _count_out(
        f"Run {t} hours at {speed} mph. Take one stone per mile.",
        f"{char} is running the {setting} road. The cairn beside the start "
        f"holds plenty of marker stones - take exactly one for each mile of "
        f"the run ahead.",
        f"distance = {speed} x {t} = {total} miles.",
        total, "mile_count", band, rng, tint="lemon",
        props=[_prop("clock", 0.07, 0.52, 1.2, f"{t} h"),
               _prop("signpost", 0.93, 0.52, 1.1, f"{speed} mph")],
    )


def _sdt_logbook_scroll(level, band, rng, char, setting):
    """TYPE-IT. Distance, time or speed - written into the logbook."""
    speed = rng.randint(2, max(3, min(120, _cal_scale(level, 10, 80))))
    time = rng.randint(2, max(3, min(12, _cal_scale(level, 3, 9))))
    distance = speed * time
    mode = rng.choice(["distance", "time", "speed"]) if level >= 3 else "distance"
    if mode == "distance":
        prompt = f"{speed} km/h for {time} hours. How far in km?"
        value, expl = distance, f"{speed} x {time} = {distance} km."
    elif mode == "time":
        prompt = f"{distance} km at {speed} km/h. How many hours?"
        value, expl = time, f"{distance} / {speed} = {time} hours."
    else:
        prompt = f"{distance} km in {time} hours. What speed in km/h?"
        value, expl = speed, f"{distance} / {time} = {speed} km/h."
    return _free_response(
        prompt,
        f"The stationmaster's logbook lies open beside the {setting}. {char} "
        f"must write the figure in before the signal will drop.",
        expl, value, "logbook_scroll",
        props=[_prop("clock", 0.32, 0.30, 1.3, f"{time} h"),
               _prop("signpost", 0.70, 0.34, 1.1, f"{speed} km/h")],
    )



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


# ================================================= COUNTING & COMPARING
#
# Grade-1 material from the team's bank: cardinality, successors and
# comparison. Every archetype below still requires a judgement about
# quantity - none of them is "tap the thing that looks different".

def _c_count_the_lanterns(level, band, rng, char, setting):
    """COUNT-OUT. One stone for every lantern hanging in the scene.

    One-to-one correspondence IS the grade-1 skill. The pile deliberately
    holds more stones than there are lanterns.
    """
    cap = count_out_cap(band)
    n = rng.randint(2, max(3, min(cap, _scale(level, 4, cap))))
    props = _cluster("lantern", n, 0.5, 0.30, "lit", 0.75, cols=6, step_x=0.075)
    return _count_out(
        "Take one stone for each lantern above.",
        f"{char} counts the lanterns strung over the {setting}. The cairn "
        f"holds plenty of stones - take exactly one for every lantern.",
        f"There are {n} lanterns, so {n} stones.",
        n, "count_the_lanterns", band, rng, tint="lemon", props=props,
    )


def _c_biggest_pile(level, band, rng, char, setting):
    """PICK-ONE. Which pile is biggest - three of them, so no coin-flip."""
    if band == "k1":
        hi = max(4, min(K1_DOTS_MAX, _scale(level, 5, K1_DOTS_MAX)))
        vals = rng.sample(range(1, hi + 1), 3)
    else:
        hi = max(12, _cal_scale(level, 30, 400))
        vals = rng.sample(range(2, max(6, hi)), 3)
    biggest = max(vals)
    mode = "biggest" if (level <= 2 or rng.random() < 0.5) else "smallest"
    want = biggest if mode == "biggest" else min(vals)

    stones = []
    for i, v in enumerate(vals):
        dots = v if (band == "k1" and v <= K1_DOTS_MAX) else None
        label = "" if band == "k1" else str(v)
        stones.append(_stone(i, label, v, v == want, dots=dots, tint="sage"))
    _lay_row(rng, stones, 0.62, 0.74)

    return _base(
        "single_choice", "set",
        f"Which pile is the {mode}?",
        f"Three little heaps sit on the path beside the {setting}. {char} may "
        f"only carry one of them.",
        f"{', '.join(str(v) for v in sorted(vals))} - the {mode} is {want}.",
        stones, 1, "biggest_pile",
    )


def _c_next_in_line(level, band, rng, char, setting):
    """PICK-ONE. Successor, predecessor, or the next step of a skip-count."""
    if band == "k1":
        step = 1
        start = rng.randint(1, max(2, min(K1_DOTS_MAX - 2, _scale(level, 4, 12))))
    else:
        step = rng.choice([1, 2, 5, 10]) if level >= 3 else rng.choice([1, 2])
        start = rng.randint(2, max(3, _cal_scale(level, 20, 200)))
    back = level >= 4 and band != "k1"
    want = start - step if back else start + step
    if want < 1:
        back, want = False, start + step

    if step == 1:
        prompt = f"What number comes {'before' if back else 'after'} {start}?"
        expl = f"{start} {'-' if back else '+'} 1 = {want}."
    else:
        prompt = f"Count {'back' if back else 'on'} by {step} from {start}."
        expl = f"{start} {'-' if back else '+'} {step} = {want}."

    return _base(
        "single_choice", "set", prompt,
        f"A row of numbered stones runs along the {setting}, and one of them "
        f"is the next place {char} must stand.",
        expl,
        _choice_stones(rng, want, max(2, step + 2), 4, band=band),
        1, "next_in_line",
    )


def _c_counting_scroll(level, band, rng, char, setting):
    """TYPE-IT. Ten (or a hundred) more than a number, written down."""
    jump = rng.choice([10, 100]) if level >= 4 else 10
    start = rng.randint(5, max(6, _cal_scale(level, 60, 500)))
    more = rng.random() < 0.5 or start <= jump
    want = start + jump if more else start - jump
    return _free_response(
        f"What is {jump} {'more' if more else 'less'} than {start}?",
        f"A tally post stands where the path forks past the {setting}. {char} "
        f"must chalk the right number onto it.",
        f"{start} {'+' if more else '-'} {jump} = {want}.",
        want, "counting_scroll",
        props=[_prop("signpost", 0.5, 0.32, 1.4, f"{start}?")],
    )


# ======================================================== TIME & MONEY

_COIN_VALUE = {"penny": 1, "nickel": 5, "dime": 10, "quarter": 25}


def _t_coin_purse(level, band, rng, char, setting):
    """PICK-ONE. Count a handful of mixed coins."""
    if band == "k1":
        # Every option must stay countable as pips (K1_DOTS_MAX), so the
        # biggest purse a 5-year-old can be handed is 3 pennies + 2 nickels.
        counts = {"penny": rng.randint(1, 3)}
        if level >= 3:
            counts["nickel"] = rng.randint(1, 2)
    else:
        kinds = rng.sample(["penny", "nickel", "dime", "quarter"],
                           2 if level <= 3 else 3)
        counts = {k: rng.randint(1, 5) for k in kinds}
    total = sum(_COIN_VALUE[k] * n for k, n in counts.items())
    bits = " and ".join(f"{n} {k}{'s' if n != 1 else ''}" for k, n in counts.items())

    props = []
    for i, (k, n) in enumerate(counts.items()):
        props += _cluster("gem", n, 0.28 + i * 0.22, 0.34, k, 0.7, cols=3)

    ch = _base(
        "single_choice", "set",
        f"{bits}. How many cents?",
        f"{char} tips out a purse at the little stall by the {setting}. "
        f"Count it up before the stall-keeper loses patience.",
        " + ".join(f"{n} x {_COIN_VALUE[k]}" for k, n in counts.items())
        + f" = {total} cents.",
        _choice_stones(rng, total, max(3, total // 4 + 2), 4, band=band),
        1, "coin_purse",
    )
    ch["props"] = props
    return ch


def _t_market_stall(level, band, rng, char, setting):
    """COUNT-OUT. Pay for n buns at c cents each, one penny at a time."""
    cap = count_out_cap(band)
    price = rng.randint(2, max(2, min(4, _scale(level, 2, 4))))
    n = rng.randint(2, max(2, min(cap // price, _scale(level, 2, 5))))
    total = price * n

    return _count_out(
        f"Buns cost {price} cents. Buy {n}. Take that many pennies.",
        f"The bun stall by the {setting} will not give change. {char} must "
        f"count out exactly the right money from a jar holding far more.",
        f"{n} x {price} = {total} cents.",
        total, "market_stall", band, rng, tint="lemon",
        props=[_prop("basket", 0.08, 0.62, 1.2, "buns"),
               _prop("signpost", 0.92, 0.55, 1.0, f"{price}c")],
    )


def _t_money_scroll(level, band, rng, char, setting):
    """TYPE-IT. Money totals or change, typed into the shopkeeper's slate."""
    if rng.random() < 0.5 or level <= 2:
        kinds = rng.sample(["nickel", "dime", "quarter"], 2)
        counts = {k: rng.randint(1, 6) for k in kinds}
        want = sum(_COIN_VALUE[k] * v for k, v in counts.items())
        bits = " and ".join(f"{v} {k}{'s' if v != 1 else ''}" for k, v in counts.items())
        prompt = f"{bits}. How many cents?"
        expl = " + ".join(f"{v} x {_COIN_VALUE[k]}" for k, v in counts.items()) + f" = {want}."
    else:
        paid = rng.choice([25, 50, 100])
        cost = rng.randint(5, paid - 5)
        want = paid - cost
        prompt = f"You pay {paid} cents for a {cost} cent bun. Change?"
        expl = f"{paid} - {cost} = {want} cents."
    return _free_response(
        prompt,
        f"The shopkeeper's slate hangs by the stall at the {setting}. {char} "
        f"chalks the answer and the little door swings open.",
        expl, want, "money_scroll", unit="c",
        props=[_prop("npc", 0.72, 0.40, 1.2, "shopkeeper"),
               _prop("sack", 0.28, 0.40, 1.1, "purse")],
    )


# ================================================================ FINALE
#
# The climax has to depend on what came before, or the quest is just seven
# equal questions in a row. `final_gate` builds its numbers out of what the
# child ACTUALLY gathered on the way - the berries, planks, lanterns and gems
# sitting in quest["state"] - so the last sum is only answerable because of
# the ones before it. It is still 100% deterministic: the carried totals come
# from story_engine._reward_amount, never from an LLM.
#
# Returns None when there is nothing carried (a child who got everything
# wrong), and the caller falls back to an ordinary hard challenge.

_FINALE_SLOTS = ("berries", "planks", "lanterns", "gems")


def _carried_items(carried: dict | None) -> list[tuple[str, int]]:
    items = [(k, int(carried.get(k) or 0)) for k in _FINALE_SLOTS] if carried else []
    items = [(k, v) for k, v in items if v > 0]
    items.sort(key=lambda kv: -kv[1])
    return items


def _divisor_split(total: int, d: int) -> tuple[int, int]:
    """Largest multiple of d that fits inside `total`, and the share."""
    d = max(2, int(d))
    k = max(1, int(total) // d)
    return d * k, k


def generate_finale(topic, band, level, carried, character_name=None,
                    objects=None, seed=None):
    """The last challenge of a quest, computed from the child's own haul."""
    items = _carried_items(carried)
    if not items or sum(v for _, v in items) < 2:
        return None
    if topic not in TOPICS:
        return None

    rng = random.Random(seed)
    char = character_name or guess_character_name(objects or [])
    setting = pick_setting(objects or [])
    level = _lvl(level)

    if band == "k1":
        # Ages 4-6 answer with countable pips, so every number in the finale -
        # including the haul the story quotes back at them - has to stay
        # inside K1_DOTS_MAX. Clamp the HAUL, not the answer, so the prose and
        # the arithmetic still agree with each other.
        items = [(k, min(v, 3)) for k, v in items][:2]

    a_name, a = items[0]
    if len(items) > 1:
        b_name, b = items[1]
        pair = f"{a} {a_name} and {b} {b_name}"
    else:
        # Only one kind carried: "3 berries and 1 berries" is a bug a child
        # can read, so the second amount is phrased as "more".
        b_name, b = a_name, max(1, a // 2)
        pair = f"{a} {a_name} and {b} more"
    total = sum(v for _, v in items)
    haul = ", ".join(f"{v} {k}" for k, v in items)

    lead = (f"Everything {char} gathered on the way here comes down to this "
            f"one last gate: {haul}.")

    if topic == "addition":
        value = a + b
        prompt = f"{pair}. How many altogether?"
        expl = f"{a} + {b} = {value}."
    elif topic == "subtraction":
        take = rng.randint(1, max(1, total - 1))
        value = total - take
        prompt = f"You carry {total}. The gate keeps {take}. How many left?"
        expl = f"{total} - {take} = {value}."
    elif topic == "multiplication":
        m = rng.randint(2, 5)
        value = a * m
        prompt = f"{m} gates, each wanting {a} {a_name}. How many?"
        expl = f"{m} x {a} = {value}."
    elif topic == "fractions":
        d = rng.choice([2, 3, 4])
        used, share = _divisor_split(total, d)
        value = share
        prompt = f"Split {used} of your {total} into {d} equal piles."
        expl = f"{used} / {d} = {share} in each pile."
    elif topic == "decimals":
        per = rng.choice([0.25, 0.5, 1.5, 2.5])
        value = round(total * per, 2)
        prompt = f"Your {total} lanterns hold {per} L each. Total litres?"
        expl = f"{total} x {per} = {value} L."
    elif topic == "geometry":
        value = 2 * (a + b)
        prompt = f"A plot {a} by {b} paces. How many posts around?"
        expl = f"2 x ({a} + {b}) = {value} posts."
    elif topic == "algebra":
        keep = rng.randint(1, max(1, total - 1))
        value = total - keep
        prompt = f"? + {keep} = {total}. Find the missing number."
        expl = f"{total} - {keep} = {value}."
    elif topic == "speed_distance_time":
        hours = rng.randint(2, 5)
        dist, speed = _divisor_split(total, hours)
        value = speed
        prompt = f"{dist} km in {hours} hours. What is the speed?"
        expl = f"{dist} / {hours} = {speed} km/h."
    elif topic == "time_and_money":
        per = 2 if band == "k1" else rng.choice([2, 5, 10])
        value = total * per
        prompt = f"Your {total} tokens are worth {per} cents each. Total?"
        expl = f"{total} x {per} = {value} cents."
    else:  # counting_and_comparing
        value = a - b if a != b else a + b
        if a != b:
            prompt = f"{pair}. How many more?"
            expl = f"{a} - {b} = {value}."
        else:
            prompt = f"{pair}. How many altogether?"
            expl = f"{a} + {b} = {value}."

    places = 2 if topic == "decimals" else 0
    if band == "k1":
        # Ages 4-6 never get a text box. Multiple choice, pips and all.
        ch = _base(
            "single_choice", "set", prompt, lead, expl,
            _choice_stones(rng, int(value), max(2, int(value) // 3 + 2), 4, band=band),
            1, "final_gate",
        )
    else:
        ch = _free_response(prompt, lead, expl, value, "final_gate",
                            places=places, unit="L" if places else "")

    ch["props"] = [_prop("gate", 0.5, 0.30, 1.6, "final"),
                   _prop("lantern", 0.18, 0.40, 1.2, "last light"),
                   _prop("lantern", 0.82, 0.40, 1.2, "last light")]
    ch["is_finale"] = True
    ch["finale_haul"] = dict((k, v) for k, v in items)
    ch["topic"] = topic
    ch["band"] = band
    ch["level"] = level
    ch["character_name"] = char
    ch["setting"] = setting
    ch.setdefault("obstacle_count", 0)
    return ch


# ------------------------------------------------------------- registry

_ARCHETYPES: dict[str, list[tuple[str, tuple[str, ...], object]]] = {
    "counting_and_comparing": [
        ("count_the_lanterns", ("k1", "23"), _c_count_the_lanterns),
        ("biggest_pile", ("k1", "23"), _c_biggest_pile),
        ("next_in_line", ("k1", "23"), _c_next_in_line),
        ("counting_scroll", ("23",), _c_counting_scroll),
    ],
    "addition": [
        ("berry_baskets", ("k1", "23", "45"), _a_berry_baskets),
        ("toll_gate", ("k1", "23", "45"), _a_toll_gate),
        ("plank_bridge", ("k1", "23", "45"), _a_plank_bridge),
        ("acorn_count", ("k1", "23", "45"), _a_acorn_count),
        ("sum_scroll", ("23", "45"), _a_sum_scroll),
    ],
    "subtraction": [
        ("lanterns_out", ("k1", "23", "45"), _s_lanterns_out),
        ("stones_left", ("k1", "23", "45"), _s_stones_left),
        ("spend_gems", ("k1", "23", "45"), _s_spend_gems),
        ("countdown_path", ("23", "45"), _s_countdown_path),
        ("tally_scroll", ("23", "45"), _s_tally_scroll),
    ],
    "time_and_money": [
        ("coin_purse", ("k1", "23"), _t_coin_purse),
        ("market_stall", ("k1", "23"), _t_market_stall),
        ("money_scroll", ("23",), _t_money_scroll),
    ],
    "multiplication": [
        ("orchard_count", ("23", "45"), _m_orchard_count),
        ("rows_of_lanterns", ("23", "45"), _m_rows_of_lanterns),
        ("equal_baskets", ("23", "45"), _m_equal_baskets),
        ("product_scroll", ("23", "45"), _m_product_scroll),
    ],
    "fractions": [
        ("safe_sandbars", ("23", "45"), _f_safe_sandbars),
        ("fraction_of_berries", ("23", "45"), _f_fraction_of_berries),
        ("pie_gate", ("23", "45"), _f_pie_gate),
        ("fraction_scroll", ("23", "45"), _f_fraction_scroll),
    ],
    "decimals": [
        ("number_line_leap", ("45",), _d_number_line_leap),
        ("rain_gauge", ("45",), _d_rain_gauge),
        ("measure_rope", ("45",), _d_measure_rope),
        ("gauge_scroll", ("45",), _d_gauge_scroll),
    ],
    "geometry": [
        ("shape_door", ("k1", "23", "45"), _g_shape_door),
        ("fence_posts", ("k1", "23", "45"), _g_fence_posts),
        ("garden_measure", ("23", "45"), _g_garden_measure),
        ("survey_scroll", ("23", "45"), _g_survey_scroll),
    ],
    "algebra": [
        ("mystery_sacks", ("45",), _al_mystery_sacks),
        ("balance_bridge", ("45",), _al_balance_bridge),
        ("balance_scales", ("45",), _al_balance_scales),
        ("rune_scroll", ("45",), _al_rune_scroll),
    ],
    "speed_distance_time": [
        ("catch_the_raft", ("45",), _sdt_catch_the_raft),
        ("clock_run", ("45",), _sdt_clock_run),
        ("mile_count", ("45",), _sdt_mile_count),
        ("logbook_scroll", ("45",), _sdt_logbook_scroll),
    ],
}

# Ages 4-6 must be playable without reading. These archetypes carry no words
# on the stones (dots, shapes, positions only). Free-response archetypes are
# NEVER in here - a pre-reader should not be hunting for the 7 key.
NON_READING_ARCHETYPES = {
    "berry_baskets", "toll_gate", "plank_bridge", "acorn_count", "lanterns_out",
    "stones_left", "spend_gems", "shape_door", "fence_posts",
    "count_the_lanterns", "biggest_pile", "next_in_line",
    "coin_purse", "market_stall",
}

# Every archetype that asks for a typed number. app.py grades these against
# `answer_value`, and `answer_value` never leaves the server.
FREE_RESPONSE_ARCHETYPES = {
    "sum_scroll", "tally_scroll", "product_scroll", "fraction_scroll",
    "gauge_scroll", "survey_scroll", "rune_scroll", "logbook_scroll",
    "counting_scroll", "money_scroll",
}

for _t, _lst in _ARCHETYPES.items():
    ARCHETYPE_INDEX[_t] = [nm for nm, _b, _f in _lst]


def archetypes_for(topic: str, band: str) -> list[str]:
    return [nm for nm, bands, _f in _ARCHETYPES.get(topic, []) if band in bands]


def playable_archetypes(topic: str, band: str) -> list[str]:
    """What the game may CHOOSE for this cell.

    Same as `archetypes_for` except that ages 4-6 only ever get archetypes
    whose stones carry no words. `archetypes_for` stays unfiltered so that an
    explicit request (and the test sweep) can still reach every generator.
    """
    names = archetypes_for(topic, band)
    if band != "k1":
        return names
    readable = [n for n in names if n in NON_READING_ARCHETYPES]
    return readable or names


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
    # An explicit request is honoured from the FULL list (story_engine already
    # asked `playable_archetypes` what it was allowed to pick); only the
    # random fallback is filtered down to the no-reading set for ages 4-6.
    chosen = None
    if archetype:
        chosen = next(((nm, fn) for nm, fn in available if nm == archetype), None)
    if chosen is None:
        allowed = set(playable_archetypes(topic, band))
        safe = [(nm, fn) for nm, fn in available if nm in allowed] or available
        pool = [(nm, fn) for nm, fn in safe if nm not in set(exclude_archetypes)]
        chosen = rng.choice(pool or safe)

    # Park the cell the generator is about to build so `_cal` can look up the
    # CSV bank's observed operand range for it. Always cleared, even on error:
    # a leaked context would calibrate the NEXT challenge against the wrong
    # topic. See the note beside `_CTX`.
    prev = (getattr(_CTX, "topic", None), getattr(_CTX, "band", None),
            getattr(_CTX, "level", None))
    _CTX.topic, _CTX.band, _CTX.level = topic, band, level
    try:
        challenge = chosen[1](level, band, rng, char, setting)
    finally:
        _CTX.topic, _CTX.band, _CTX.level = prev

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
