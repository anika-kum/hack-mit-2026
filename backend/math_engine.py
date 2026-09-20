"""
do-IT-oodle - deterministic math challenge generator.

Design rules (these are the whole point of this file):

1. THE QUESTION IS A REAL MATH QUESTION.
   *** 2026-09-19b REWRITE. *** The previous generation dressed arithmetic up
   as spatial errands - "fence a 2 by 5 plot, one post per step", "take one
   marker stone per mile", "which door has exactly 3 sides". Playtesters were
   blunt: the questions were far too easy and the mechanics were busywork, not
   maths. Every one of those archetypes is now in DELETED_ARCHETYPES.

   What replaced them: straight questions in the register of the team's own
   CSV bank ("Two angles of a triangle are 62 and 37 degrees. Third?",
   "2/3 divided by 1/3 = ? Simplify.", "A train covers 300 km in 4 hours.
   Its speed?"). Two presentations only - MULTIPLE CHOICE and FREE RESPONSE.
   The fun lives in the story and in the obstacle the answer unlocks, not in
   contorting the arithmetic into a walking puzzle.

   ONE exception survives, deliberately: `frac_sandbars`, where "step on n of
   these d equal sandbars" genuinely IS what n/d means. It is restricted to
   ages 7-8, the youngest band that meets fractions at all.

2. `prompt` IS THE QUESTION.
   Short (<= 12 words), concrete, WITH UNITS AND CORRECT PLURALS. It is
   rendered in an always-visible banner. `narrative` is flavour and may be
   replaced by AI.

3. THE BANK GATES BOTH MAGNITUDE AND KIND.
   `question_bank.py` mines the CSVs for (a) operand magnitudes per
   (topic, band, level) and (b) which CONCEPT each row is, and which school
   grades that concept lives in. CONCEPT_GRADES below mirrors (b) so the
   offline game gates identically; test_engine asserts the two agree.

   Consequence: shape identification is a grade-1 concept and is unavailable
   to ages 9-10. Triangle angle sum is a grade 4-5 concept and is unavailable
   to ages 4-6. Nobody has to remember to enforce that by hand.

4. DIFFICULTY FLOORS ARE BAND-RELATIVE.
   One 13-rung ladder spans the whole game. Band k1 starts at rung 1, band 23
   at rung 5, band 45 at rung 9 - so a band's level 5 and the next band's
   level 1 sit on the SAME rung. An older child's easiest question is where a
   younger child's hardest was. See `difficulty_rung` / `_rs`.

5. NOTHING HERE CALLS AN LLM. Correctness is guaranteed and generation is
   instant. prompts.py may *re-narrate* a challenge, never re-compute it.
"""

import math
import random
import threading
from fractions import Fraction

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
    # 2026-09-19b: a drawn polygon, so a pre-reader can SEE the shape the
    # question is about instead of having to decode the word "hexagon".
    "shape",
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


# Deleted after playtest feedback. Kept as a name list so nothing - registry,
# story engine, tests - can quietly resurrect one, and so the reason survives
# the next person to read this file.
DELETED_ARCHETYPES = {
    # --- 2026-09-19a: "not actually math" ---------------------------------
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

    # --- 2026-09-19b: "the questions are way too easy", "just math" --------
    # The COUNT-OUT replacements from the 'a' cull turned out to be the same
    # disease: the answer had to be small enough to count out by hand, which
    # capped every one of them at a number a five-year-old could reach. They
    # are gone, and with them the rest of the spatial gimmicks.
    "shape_door":             "pick the door with N sides; a 10-year-old "
                              "counting to 3, and the fiction added nothing",
    "fence_posts":            "'one post per step' round a 2 by 5 plot; the "
                              "counting cap made every perimeter tiny",
    "orchard_count":          "take r x c seeds from a pile; capped at 14, so "
                              "the hardest product was 3 x 4",
    "mile_count":             "'take one stone per mile'; same cap, and the "
                              "walking was the whole interaction",
    "acorn_count":            "take a then b more acorns; addition capped at "
                              "what a child will patiently tap",
    "stones_left":            "step on the stones the tide left; subtraction "
                              "capped the same way",
    "count_the_lanterns":     "one stone per lantern; one-to-one counting "
                              "dressed as a quest, and never harder than 14",
    "market_stall":           "count out pennies for n buns; price capped at 4",
    "fraction_of_berries":    "tap n/d of a visible pile; the pile size gave "
                              "the answer away once you counted it",
    "catch_the_raft":         "stand where the raft lands; also the source of "
                              "'Raft drifts 1 posts a beat'",
    "countdown_path":         "ordered walk over scattered stones; route "
                              "planning, not arithmetic",
    "number_line_leap":       "leap to 0.35 on a drawn number line; reading a "
                              "position off an axis we had already drawn",
    "toll_gate":              "pick gems summing to the toll; a subset-sum "
                              "puzzle a child solved by trial and error",
    "rain_gauge":             "same as toll_gate with decimal droplets",
    "balance_scales":         "same as toll_gate with weights",
}


# ====================================================== THE DIFFICULTY LADDER
#
# One ladder, thirteen rungs, spanning ages 4 to 10. A band's level 5 and the
# next band's level 1 land on the SAME rung, which is the whole point:
#
#     k1  levels 1..5  ->  rungs 1  2  3  4  5
#     23  levels 1..5  ->  rungs        5  6  7  8  9
#     45  levels 1..5  ->  rungs              9 10 11 12 13
#
# Before this, level 1 generated identical numbers for a four-year-old and a
# ten-year-old, which is how "Which door has exactly 3 sides?" ended up in
# front of the 9-10 band.

BAND_RUNG = {"k1": 1, "23": 5, "45": 9}
MAX_RUNG = 13


def difficulty_rung(band: str, level: int) -> int:
    """Where (band, level) sits on the global 1..13 ladder."""
    return BAND_RUNG.get(band, BAND_RUNG["23"]) + _lvl(level) - 1


def topic_rung_span(topic: str | None) -> tuple[int, int]:
    """The rungs a topic actually uses, given which bands offer it.

    Decimals only exist for ages 9-10, so its five levels must span its OWN
    range (rungs 9-13) rather than being squeezed into the top third of a
    ladder that starts at four-year-old addition. Without this, every
    decimals level would generate near-identical numbers.
    """
    bands = TOPIC_BANDS.get(topic or "", None) or list(BAND_RUNG)
    lo = min(BAND_RUNG.get(b, 5) for b in bands)
    hi = max(BAND_RUNG.get(b, 5) for b in bands) + 4
    return lo, max(lo + 1, hi)


def _lvl(level: int) -> int:
    return max(1, min(5, int(level or 1)))


def _scale(level: int, lo: float, hi: float) -> int:
    """Linear level-only scale. Band-blind: prefer `_rs` in new code."""
    level = _lvl(level)
    return round(lo + (hi - lo) * (level - 1) / 4)


# ------------------------------------------------------- CSV calibration
#
# The generators below are called as `fn(level, band, rng, char, setting)` and
# have been for the life of the project. Rather than thread `topic` through
# twenty signatures, generate_challenge parks the current (topic, band, level)
# on a thread-local and `_cal()` reads it. Thread-local, not a plain global,
# because challenges are generated on FastAPI's request threads and two
# children must never calibrate each other's numbers.
#
# When math_questions/ is absent - a supported state - `_cal` is the exact
# identity function and `_cal_value` returns its argument unchanged.

_CTX = threading.local()


def story_noun(default: str = "berries") -> str:
    """The noun THIS quest is about, e.g. "rods".

    "if the story is about collecting rods, make it such that bun bun the
     main bunny is getting rods and doing multiplication - the context is
     the same"

    Word problems are written about this noun so the maths and the story are
    about the same thing. It is WORDING ONLY: every number and the answer key
    are still generated right here. story_engine parks it on `_CTX` alongside
    the topic; with no quest running the caller's default is used, which is
    what keeps the sweep in test_engine deterministic.
    """
    noun = getattr(_CTX, "noun", None)
    return noun if isinstance(noun, str) and noun.strip() else default


def story_char(default: str = "our hero") -> str:
    name = getattr(_CTX, "char", None)
    return name if isinstance(name, str) and name.strip() else default


def story_toll(default: str = "") -> str:
    """The obstacle's short imperative, e.g. "Pay the gate".

    "Have the questions be part of the story (in order to get past the gate
     the cat must pay 2 dimes and 2 quarters, how much is that)"

    Used to frame a word problem around the thing actually blocking the way,
    so the sum IS the toll rather than a puzzle that happens to be nearby.
    """
    toll = getattr(_CTX, "toll", None)
    return toll if isinstance(toll, str) and toll.strip() else default


def _framed(lead: str, tail: str, limit: int = 12) -> str:
    """`lead: tail`, but only while it still fits the 12-word prompt budget."""
    if not lead:
        return tail
    joined = f"{lead}: {tail}"
    return joined if len(joined.split()) <= limit else tail


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


def _cal_value(v: int) -> int:
    """Nudge ONE generated magnitude toward the bank's observed operands.

    Opens a window of (v/2, v*2) around the value, lets `scale_range` blend
    that window with what the CSVs actually contain for this cell, then
    clamps `v` into the result. With no bank the window comes back unchanged
    and `v` is already inside it, so this is the exact identity - which is
    what keeps the offline demo bit-for-bit what it would be without the
    bank at all.
    """
    v = int(v)
    if v < 2:
        return max(1, v)
    lo, hi = _cal(max(1, v // 2), max(2, v * 2))
    return max(1, min(max(lo, hi), max(min(lo, hi), v)))


def _span(hi, frac: float = 0.55, floor: int = 2) -> tuple[int, int]:
    """A tight draw range just BELOW a ladder magnitude.

    `rng.randint(2, hi)` was the bug behind half the "still too easy"
    complaints: at level 5 it happily rolled a 2, so a ten-year-old's hardest
    multiplication could come out as "2 x 2". Drawing from [0.55*hi, hi]
    keeps some variety while guaranteeing the level actually moved.
    """
    hi = max(floor, int(hi))
    lo = max(floor, int(round(hi * frac)))
    return min(lo, hi), hi


def _rs(band: str, level: int, lo: float, hi: float, cal: bool = True) -> int:
    """A magnitude on the band-relative ladder, between `lo` and `hi`.

    `lo` is what the EASIEST level of the easiest band that offers this topic
    should see; `hi` is what the hardest level of the oldest band should see.
    Interpolation is geometric, because arithmetic difficulty grows by orders
    of magnitude (3 -> 30 -> 300), not by constant steps.

    `cal=False` for STRUCTURAL numbers - denominators, how many parts, how
    many hours. Those are not magnitudes and must not be dragged around by a
    bank percentile mined from a different kind of operand.
    """
    lo = max(1.0, float(lo))
    hi = max(lo + 1.0, float(hi))
    r0, r1 = topic_rung_span(getattr(_CTX, "topic", None))
    r = difficulty_rung(band, level)
    t = (r - r0) / float(r1 - r0)
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    v = int(round(lo * (hi / lo) ** t))
    return _cal_value(v) if cal else max(1, v)


# --------------------------------------------------------------- primitives

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
        out.append(_prop(kind, min(0.97, max(0.03, x)),
                         min(0.95, max(0.08, y)), scale, label))
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
    while len(vals) < count and step < 400:
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
    """MULTIPLE CHOICE: numbered stones, one of which is right.

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


def _option_stones(rng, options):
    """MULTIPLE CHOICE from an explicit option list.

    `options` is [(label, value, is_correct, extra_fields_dict)] - used when
    the answers are not plain integers (fractions, drawn shapes, pip piles).
    """
    opts = list(options)
    rng.shuffle(opts)
    stones = []
    for i, (label, value, correct, extra) in enumerate(opts):
        stones.append(_stone(i, label, value, correct, **(extra or {})))
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


# ------------------------------------------------------------ FREE RESPONSE
#
# Typed numeric answers, graded deterministically in app.py against
# `answer_value` with `tolerance`. Never offered to ages 4-6: a pre-reader
# hunting for the 7 key is a child who has stopped doing maths.
#
# `form="fraction"` means the answer is a fraction or a mixed number. The
# value is still a float and grading is still numeric, but `answer_text`
# records the canonical written form ("1 1/3") and app.parse_typed_number
# accepts BOTH "1 1/3" and "4/3". `answer_text` is a SECRET FIELD.

def _free_response(prompt, narrative, explanation, value, archetype,
                   places=0, unit="", props=None, form=None, answer_text=None):
    if form == "fraction":
        # Wide enough that float(Fraction) round-tripped through six decimal
        # places still matches; far narrower than the 1/12 gap between any
        # two fractions we ever generate.
        tol = 5e-5
    else:
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
    if form:
        ch["answer_form"] = form           # public: a formatting HINT only
    if answer_text:
        ch["answer_text"] = str(answer_text)   # SECRET - stripped in app.py
    return ch


# ----------------------------------------------------------------- fractions

def _frac_label(fr: Fraction) -> str:
    """'3/4', '2', '1 1/3' - the way a child is taught to write it."""
    n, d = fr.numerator, fr.denominator
    if d == 1:
        return str(n)
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n > d:
        whole, rem = divmod(n, d)
        return f"{sign}{whole} {rem}/{d}"
    return f"{sign}{n}/{d}"


def _frac_options(rng, fr: Fraction, count: int = 4):
    """`fr` plus plausible wrong fractions - the errors a child actually makes."""
    seen = {fr}
    out = [fr]
    guard = 0
    while len(out) < count and guard < 400:
        guard += 1
        n, d = fr.numerator, fr.denominator
        try:
            cand = rng.choice([
                Fraction(n + rng.choice([1, -1, 2]), d),
                Fraction(n, d + rng.choice([1, -1, 2])) if d > 3 else Fraction(n * 2, d),
                Fraction(n * 2, d),
                Fraction(n, d * 2),
                Fraction(d, n) if n else fr,
            ])
        except (ZeroDivisionError, ValueError):
            continue
        if cand <= 0 or cand in seen or cand.denominator > 64 or cand.numerator > 99:
            continue
        seen.add(cand)
        out.append(cand)
    # last resort so the tray is always full
    step = 1
    while len(out) < count and step < 40:
        cand = fr + Fraction(step, max(2, fr.denominator))
        if cand > 0 and cand not in seen:
            seen.add(cand)
            out.append(cand)
        step += 1
    return out


def _plural(n, one, many=None) -> str:
    """'1 hour' / '2 hours'. The playtest caught 'Raft drifts 1 posts a beat'."""
    return one if abs(n) == 1 else (many or f"{one}s")


def _singular(word: str) -> str:
    """'berries' -> 'berry', 'rods' -> 'rod'. Good enough for a story noun."""
    w = str(word or "").strip()
    if len(w) > 3 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("ses"):
        return w[:-2]
    if len(w) > 1 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _count(n, noun) -> str:
    """'1 berry' / '4 berries', given the PLURAL form of the noun."""
    return f"{n} {noun if abs(n) != 1 else _singular(noun)}"


# ================================================== THE CONCEPT GRADE TABLE
#
# MIRRORED FROM THE CSV BANK. Each entry is the set of school grades in which
# math_questions/ actually contains that concept. Regenerate with:
#
#     python3 -c "import question_bank as q; print(q.load().concept_grades_flat())"
#
# and test_engine asserts this table and the live bank agree, so editing a CSV
# that moves a concept between grades fails the build rather than silently
# mis-pitching a question at a child.
#
# It lives HERE, not only in question_bank, so that the gate is identical when
# math_questions/ is absent - the offline demo path.

CONCEPT_GRADES: dict[str, tuple[int, ...]] = {
    "add_2": (1, 2, 3),
    "add_3": (2,),
    "add_sub_chain": (3,),
    "box_volume": (5,),
    "clock_add_hours": (2,),
    "coin_total": (2,),
    "compare_size": (1,),
    "count_next": (1,),
    "dec_add": (4, 5),
    "dec_mul": (5,),
    "dec_sub": (4,),
    "div_exact": (3, 4, 5),
    "div_remainder": (3, 4),
    "frac_add_same": (3, 4, 5),
    "frac_add_unlike": (5,),
    "frac_divide": (5,),
    "frac_halves": (3,),
    "frac_multiply": (5,),
    "frac_of_whole": (3, 4),
    "frac_remaining": (3,),
    "frac_simplify": (4,),
    "missing_addend": (4, 5),
    "missing_factor": (4, 5),
    "money_add_cents": (2,),
    "mul_2": (2, 3, 4, 5),
    "rect_area": (3, 4),
    "rect_perimeter": (2,),
    "rect_side_from_perimeter": (3,),
    "sdt_distance": (5,),
    "sdt_speed": (5,),
    "sdt_time": (5,),
    "shape_id": (1,),
    "shape_sides": (1, 2),
    "shape_sides_total": (1,),
    "solid_faces": (1, 2),
    "square_perimeter": (2, 3),
    "square_side_from_area": (4,),
    "sub_2": (1, 2, 3),
    "triangle_angle_sum": (4, 5),
    "triangle_area": (5,),
    "two_step_linear": (4, 5),
}

# Which school grades each of our age bands spans.
BAND_GRADES = {"k1": (1, 1), "23": (2, 3), "45": (4, 5)}
# How many grades of REVIEW a band gets. A concept last taught in grade 3 is
# fair revision for a grade-4 child; one last taught in grade 2 is not.
REVIEW_CARRY_GRADES = 1

# PRODUCT overrides, not curriculum ones. The bank's grades would also permit
# these at the next band up; we do not, and the reason is not pedagogy.
#
#   shape_id  is the one archetype whose ANSWER is a picture rather than a
#             number. It exists so a child who cannot read still gets a shape
#             question. A seven-year-old can read, so they get the real
#             question ("How many sides does a hexagon have?") instead - and
#             "which door has 3 sides" was the single loudest playtest
#             complaint, so it gets the narrowest possible home.
CONCEPT_BAND_OVERRIDE: dict[str, tuple[str, ...]] = {
    "shape_id": ("k1",),
}


def concept_bands(concept: str) -> list[str]:
    """Which bands a concept is appropriate for, per the bank's grades.

    Two-sided and both sides matter:
      * TOO ADVANCED - the concept's earliest grade is above the band's top.
        ("Two angles of a triangle..." for a five-year-old.)
      * OUTGROWN - the concept's latest grade is more than
        REVIEW_CARRY_GRADES below the band's bottom.
        ("How many sides does a triangle have?" for a ten-year-old.)
    """
    grades = CONCEPT_GRADES.get(concept)
    if not grades:
        return []
    out = []
    for band, (g_lo, g_hi) in BAND_GRADES.items():
        if min(grades) <= g_hi and max(grades) >= g_lo - REVIEW_CARRY_GRADES:
            out.append(band)
    override = CONCEPT_BAND_OVERRIDE.get(concept)
    if override is not None:
        out = [b for b in out if b in override]
    return [b for b in ("k1", "23", "45") if b in out]


# ===================================================== CONCEPT GENERATORS
#
# Each returns a SPEC: the maths, plus both wordings of it. `_as_pick` and
# `_as_type` turn a spec into a multiple-choice or free-response challenge.
# No generator knows or cares which presentation it will get.

def _q(bare, expl, value, *, story=None, places=0, unit="", spread=None,
       options=None, props=None, form=None, answer_text=None, narr=None):
    return {
        "bare": bare,            # "4.24 + 0.59 = ?"
        "story": story,          # "4.24 litres, then 0.59 more. How much?"
        "expl": expl,
        "value": float(value),
        "places": int(places),
        "unit": unit or "",
        "spread": spread,
        "options": options,      # explicit [(label, value, correct, extra)]
        "props": props or [],
        "form": form,            # None | "fraction"
        "answer_text": answer_text,
        "narr": narr,
    }


# --------------------------------------------------- counting & comparing

def _k_count_next(level, band, rng, char, setting):
    hi = _rs(band, level, 5, 480)
    if band == "k1":
        hi = min(hi, _scale(level, 4, 13))     # ramped inside the pip cap
    start = rng.randint(max(2, hi // 2), max(3, hi))
    forward = start <= 2 or rng.random() < 0.5
    value = start + 1 if forward else start - 1
    return _q(
        f"What number comes {'after' if forward else 'before'} {start}?",
        f"{start} {'+' if forward else '-'} 1 = {value}.",
        value, spread=max(2, start // 8 + 2),
        props=[_prop("signpost", 0.5, 0.32, 1.35, f"{start} ?")],
        narr=f"A line of numbered milestones runs along the {setting}, and one "
             f"of them is where {char} must stand next.")


def _k_compare_size(level, band, rng, char, setting):
    hi = _rs(band, level, 9, 9000)
    if band == "k1":
        hi = min(hi, _scale(level, 5, K1_DOTS_MAX))   # ramped inside the cap
    lo = max(1, hi // 4)
    pool = range(lo, max(lo + 4, hi) + 1)
    vals = rng.sample(list(pool), 3)
    bigger = level <= 2 or rng.random() < 0.5
    want = max(vals) if bigger else min(vals)
    if band == "k1":
        bare = f"Which pile has the {'most' if bigger else 'fewest'}?"
        options = [("", v, v == want, {"dots": v, "tint": "sage"}) for v in vals]
    else:
        listed = ", ".join(str(v) for v in vals[:-1]) + f" or {vals[-1]}"
        bare = f"Which is {'bigger' if bigger else 'smaller'}: {listed}?"
        options = [(str(v), v, v == want, {}) for v in vals]
    return _q(bare,
              f"{', '.join(str(v) for v in sorted(vals))} - "
              f"the {'biggest' if bigger else 'smallest'} is {want}.",
              want, options=options,
              props=[_prop("signpost", 0.06, 0.34, 1.0, "?")],
              narr=f"Three heaps sit on the path beside the {setting}. {char} "
                   f"may only carry one of them away.")


# ------------------------------------------------------ addition & subtraction

def _k_add_2(level, band, rng, char, setting):
    hi = _rs(band, level, 4, 900)
    if band == "k1":
        # The pip cap (every answer countable, so <= K1_DOTS_MAX) squeezes
        # this band flat unless the five levels are ramped INSIDE it.
        hi = min(hi, _scale(level, 2, 7))
    lo, hi = _span(hi, 0.45, 1)
    a, b = rng.randint(lo, hi), rng.randint(lo, hi)
    if band == "k1":
        while a + b > 13 and b > 1:
            b -= 1
        while a + b > 13 and a > 1:
            a -= 1
    total = a + b
    noun = story_noun("berries")
    return _q(f"{a} + {b} = ?", f"{a} + {b} = {total}.", total,
              story=_framed(story_toll(),
                            f"{_count(a, noun)} and {b} more. How many?"),
              spread=max(2, total // 7 + 2),
              props=[_prop("basket", 0.30, 0.36, 1.1, str(a)),
                     _prop("basket", 0.70, 0.36, 1.1, str(b))],
              narr=f"Two baskets sit tipped together beside the {setting}, and "
                   f"{char} has to know the total before moving on.")


def _k_add_3(level, band, rng, char, setting):
    lo, hi = _span(_rs(band, level, 6, 140), 0.45, 1)
    a, b, c = (rng.randint(lo, hi) for _ in range(3))
    total = a + b + c
    return _q(f"{a} + {b} + {c} = ?", f"{a} + {b} + {c} = {total}.", total,
              story=_framed(story_toll(),
                            f"Sacks of {a}, {b} and {c} {story_noun('gems')}. Total?"),
              spread=max(3, total // 7 + 2),
              props=[_prop("sack", 0.26, 0.36, 1.0, str(a)),
                     _prop("sack", 0.50, 0.36, 1.0, str(b)),
                     _prop("sack", 0.74, 0.36, 1.0, str(c))],
              narr=f"Three sacks were left on the bank of the {setting}. "
                   f"{char} counts what is in all of them.")


def _k_add_sub_chain(level, band, rng, char, setting):
    lo, hi = _span(_rs(band, level, 20, 900), 0.5)
    a = rng.randint(lo, hi)
    b = rng.randint(lo, hi)
    c = rng.randint(lo, max(lo + 1, min(a + b - 1, hi)))
    total = a + b - c
    return _q(f"{a} + {b} - {c} = ?",
              f"{a} + {b} = {a + b}, then {a + b} - {c} = {total}.", total,
              spread=max(3, total // 7 + 2),
              props=[_prop("signpost", 0.5, 0.30, 1.4, f"{a}+{b}-{c}")],
              narr=f"A tally slate is wedged in the rocks beside the {setting}. "
                   f"{char} must work it through in order.")


def _k_sub_2(level, band, rng, char, setting):
    hi = _rs(band, level, 5, 900)
    if band == "k1":
        hi = min(hi, _scale(level, 4, 14))     # ramped inside the pip cap
    lo, hi = _span(hi, 0.55)
    a = rng.randint(lo, hi)
    b = rng.randint(max(1, int(a * 0.25)), max(1, a - 1))
    left = a - b
    noun = story_noun("lanterns")
    return _q(f"{a} - {b} = ?", f"{a} - {b} = {left}.", left,
              story=_framed(story_toll(),
                            f"{_count(a, noun)}, {b} lost. How many left?"),
              spread=max(2, left // 6 + 2),
              props=[_prop("lantern", 0.32, 0.34, 1.2, str(a)),
                     _prop("lantern", 0.68, 0.34, 1.2, f"-{b}")],
              narr=f"A gust came off the {setting} and swept along {char}'s "
                   f"line of lanterns.")


# --------------------------------------------------------------- time & money

_COIN_VALUE = {"penny": 1, "nickel": 5, "dime": 10, "quarter": 25}
_COIN_PLURAL = {"penny": "pennies", "nickel": "nickels",
                "dime": "dimes", "quarter": "quarters"}


def _coin_phrase(counts: dict) -> str:
    bits = [f"{n} {_plural(n, k, _COIN_PLURAL[k])}" for k, n in counts.items()]
    if len(bits) == 1:
        return bits[0]
    return ", ".join(bits[:-1]) + f" and {bits[-1]}"


def _k_coin_total(level, band, rng, char, setting):
    if band == "k1":
        # Every option must stay countable as pips, so the biggest purse a
        # five-year-old can be handed is 3 pennies and 2 nickels.
        counts = {"penny": rng.randint(1, 3)}
        if level >= 3:
            counts["nickel"] = rng.randint(1, 2)
    else:
        kinds = rng.sample(["penny", "nickel", "dime", "quarter"],
                           2 if level <= 3 else 3)
        clo, chi = _span(min(9, _rs(band, level, 2, 9, cal=False)), 0.5, 1)
        counts = {k: rng.randint(clo, chi) for k in kinds}
    total = sum(_COIN_VALUE[k] * n for k, n in counts.items())
    props = []
    for i, (k, n) in enumerate(counts.items()):
        props += _cluster("gem", n, 0.26 + i * 0.22, 0.34, k, 0.7, cols=3)
    return _q(_framed(story_toll(), f"{_coin_phrase(counts)}. How many cents?"),
              " + ".join(f"{n} x {_COIN_VALUE[k]}" for k, n in counts.items())
              + f" = {total} cents.",
              total, spread=max(3, total // 4 + 2), unit="c", props=props,
              narr=f"{char} tips a purse out on the stall counter by the "
                   f"{setting}, and the stall-keeper is not a patient soul.")


def _k_money_add_cents(level, band, rng, char, setting):
    hi = _rs(band, level, 5, 95)
    if band == "k1":
        hi = min(hi, _scale(level, 2, 7))      # ramped inside the pip cap
    lo, hi = _span(hi, 0.45, 1)
    a, b = rng.randint(lo, hi), rng.randint(lo, hi)
    if band == "k1":
        while a + b > 14 and b > 1:
            b -= 1
    total = a + b
    return _q(_framed(story_toll(),
                      f"{_count(a, 'cents')} and {b} more. How many cents?"),
              f"{a} + {b} = {total} cents.", total,
              spread=max(2, total // 5 + 2), unit="c",
              props=[_prop("sack", 0.30, 0.38, 1.1, f"{a}c"),
                     _prop("gem", 0.68, 0.36, 0.9, f"{b}c")],
              narr=f"{char} finds another few coins glinting in the mud at "
                   f"the edge of the {setting}.")


def _k_clock_add_hours(level, band, rng, char, setting):
    now = rng.randint(1, 12)
    add = rng.randint(*_span(min(9, _rs(band, level, 2, 9, cal=False)), 0.6, 1))
    value = (now + add - 1) % 12 + 1
    return _q(f"It is {now} o'clock. What time in {add} {_plural(add, 'hour')}?",
              f"{now} o'clock plus {add} {_plural(add, 'hour')} is {value} o'clock.",
              value, spread=3, props=[_prop("clock", 0.5, 0.32, 1.5, f"{now}")],
              narr=f"The bell tower over the {setting} reads {now}. {char} has "
                   f"to be somewhere, and needs to know when.")


# ------------------------------------------------------------- multiplication

def _k_mul_2(level, band, rng, char, setting):
    a = rng.randint(*_span(_rs(band, level, 3, 600)))
    b = rng.randint(*_span(_rs(band, level, 2, 70), 0.5))
    total = a * b
    noun = story_noun("apples")
    return _q(f"{a} x {b} = ?", f"{a} x {b} = {total}.", total,
              story=_framed(story_toll(),
                            f"{_count(a, 'bundles')} of {b} {noun}. How many?"),
              spread=max(3, total // 7 + 2),
              props=_cluster("basket", min(a, 8), 0.5, 0.33, str(b), 0.85,
                             cols=4, step_x=0.075),
              narr=f"{char} lines the baskets up along the {setting} and counts "
                   f"what the whole row comes to.")


def _k_div_exact(level, band, rng, char, setting):
    b = rng.randint(*_span(min(30, _rs(band, level, 2, 25)), 0.5))
    q = rng.randint(*_span(min(140, _rs(band, level, 3, 95))))
    a = b * q
    return _q(f"{a} / {b} = ?", f"{a} / {b} = {q}.", q,
              story=_framed(story_toll(),
                            f"{a} {story_noun('apples')} into {b} equal shares. Each?"),
              spread=max(2, q // 5 + 2),
              props=[_prop("signpost", 0.5, 0.30, 1.4, f"{a}/{b}")],
              narr=f"The whole haul has to be split evenly before {char} can "
                   f"go on past the {setting}.")


def _k_div_remainder(level, band, rng, char, setting):
    b = rng.randint(*_span(min(12, _rs(band, level, 3, 9, cal=False)), 0.6, 3))
    q = rng.randint(*_span(min(140, _rs(band, level, 3, 90))))
    r = rng.randint(1, b - 1)
    a = b * q + r
    if rng.random() < 0.5:
        bare = f"{a} divided by {b}. What is the remainder?"
        value = r
    else:
        bare = f"{a} divided by {b}. How many whole groups?"
        value = q
    return _q(bare, f"{a} = {b} x {q} + {r}.", value,
              spread=max(2, value // 4 + 2),
              props=[_prop("signpost", 0.5, 0.30, 1.4, f"{a}/{b}"),
                     _prop("basket", 0.78, 0.40, 1.0, "left over")],
              narr=f"They will not divide evenly, and {char} has to know "
                   f"exactly what is left on the {setting} path.")


# ------------------------------------------------------------------ fractions

_DENOM_LADDER = [2, 3, 4, 5, 6, 8, 10, 12]


def _denoms(band, level, cap=8):
    """How adventurous the denominators may get at this rung."""
    n = max(1, min(len(_DENOM_LADDER), cap,
                   _rs(band, level, 2, len(_DENOM_LADDER), cal=False)))
    return _DENOM_LADDER[:n]


def _k_frac_of_whole(level, band, rng, char, setting):
    d = rng.choice(_denoms(band, level))
    n = rng.choice([i for i in range(1, d) if math.gcd(i, d) == 1] or [1])
    k = rng.randint(*_span(min(16, _rs(band, level, 2, 14)), 0.6))
    total = d * k
    value = n * k
    return _q(f"What is {n}/{d} of {total}?",
              f"{total} / {d} = {k}, then {k} x {n} = {value}.", value,
              spread=max(2, value // 4 + 2),
              props=[_prop("pie_gate", 0.5, 0.30, 1.5, f"{n}/{d}")],
              narr=f"A miller's ledger hangs by the {setting}, and the share "
                   f"written on it is the only way {char} gets past.")


def _k_frac_add_same(level, band, rng, char, setting):
    d = rng.choice(_denoms(band, level)[1:] or [3])
    a = rng.randint(1, d - 1)
    # Improper sums (and therefore mixed-number answers) only once the rung
    # is high enough; below that the sum stays inside one whole.
    top = (d - 1) if difficulty_rung(band, level) >= 8 else max(1, d - a)
    b = rng.randint(1, max(1, top))
    fr = Fraction(a + b, d)
    return _q(f"{a}/{d} + {b}/{d} = ? Simplify.",
              f"{a}/{d} + {b}/{d} = {a + b}/{d} = {_frac_label(fr)}.",
              float(fr), form="fraction", answer_text=_frac_label(fr),
              options=[(_frac_label(f), float(f), f == fr, {})
                       for f in _frac_options(rng, fr)],
              props=[_prop("pie_gate", 0.32, 0.32, 1.2, f"{a}/{d}"),
                     _prop("pie_gate", 0.68, 0.32, 1.2, f"{b}/{d}")],
              narr=f"Two moon-shards lie on the stone table by the {setting}. "
                   f"{char} must say what they make together.")


def _k_frac_add_unlike(level, band, rng, char, setting):
    pool = _denoms(band, level)
    if len(pool) < 2:
        pool = [2, 3]
    d1, d2 = rng.sample(pool, 2)
    a = rng.randint(1, d1 - 1)
    b = rng.randint(1, d2 - 1)
    fr = Fraction(a, d1) + Fraction(b, d2)
    return _q(f"{a}/{d1} + {b}/{d2} = ? Simplify.",
              f"{a}/{d1} + {b}/{d2} = {_frac_label(fr)}.",
              float(fr), form="fraction", answer_text=_frac_label(fr),
              options=[(_frac_label(f), float(f), f == fr, {})
                       for f in _frac_options(rng, fr)],
              props=[_prop("pie_gate", 0.32, 0.32, 1.2, f"{a}/{d1}"),
                     _prop("pie_gate", 0.68, 0.32, 1.2, f"{b}/{d2}")],
              narr=f"The two shards are cut to different sizes. {char} has to "
                   f"find the measure that fits both before the {setting} gate opens.")


def _k_frac_multiply(level, band, rng, char, setting):
    pool = _denoms(band, level)
    d1 = rng.choice(pool)
    d2 = rng.choice(pool)
    a = rng.randint(1, max(1, d1 - 1))
    b = rng.randint(1, max(1, d2 - 1))
    fr = Fraction(a, d1) * Fraction(b, d2)
    return _q(f"{a}/{d1} x {b}/{d2} = ? Simplify.",
              f"{a} x {b} = {a * b} over {d1} x {d2} = {d1 * d2}, "
              f"which is {_frac_label(fr)}.",
              float(fr), form="fraction", answer_text=_frac_label(fr),
              options=[(_frac_label(f), float(f), f == fr, {})
                       for f in _frac_options(rng, fr)],
              props=[_prop("pie_gate", 0.5, 0.30, 1.5, f"{a}/{d1}")],
              narr=f"A share of a share. {char} works it out on the flat rock "
                   f"above the {setting}.")


def _k_frac_divide(level, band, rng, char, setting):
    pool = _denoms(band, level)
    d1 = rng.choice(pool)
    d2 = rng.choice(pool)
    a = rng.randint(1, max(1, d1 - 1))
    b = rng.randint(1, max(1, d2 - 1))
    fr = Fraction(a, d1) / Fraction(b, d2)
    return _q(f"{a}/{d1} divided by {b}/{d2} = ? Simplify.",
              f"Turn it upside down and multiply: {a}/{d1} x {d2}/{b} "
              f"= {_frac_label(fr)}.",
              float(fr), form="fraction", answer_text=_frac_label(fr),
              options=[(_frac_label(f), float(f), f == fr, {})
                       for f in _frac_options(rng, fr)],
              props=[_prop("pie_gate", 0.32, 0.32, 1.2, f"{a}/{d1}"),
                     _prop("pie_gate", 0.68, 0.32, 1.2, f"{b}/{d2}")],
              narr=f"The hardest kind of share there is. {char} takes a long "
                   f"breath and works it through beside the {setting}.")


def _k_frac_simplify(level, band, rng, char, setting):
    d = rng.choice(_denoms(band, level)[1:] or [3])
    n = rng.randint(1, d - 1)
    base = Fraction(n, d)
    m = rng.randint(2, max(2, min(6, _rs(band, level, 2, 6, cal=False))))
    num, den = base.numerator * m, base.denominator * m
    return _q(f"Write {num}/{den} in its simplest form.",
              f"{num} and {den} both divide by {m}: {_frac_label(base)}.",
              float(base), form="fraction", answer_text=_frac_label(base),
              options=[(_frac_label(f), float(f), f == base, {})
                       for f in _frac_options(rng, base)],
              props=[_prop("pie_gate", 0.5, 0.30, 1.5, f"{num}/{den}")],
              narr=f"The carving on the {setting} gate is written the long way "
                   f"round. {char} has to write it the short way.")


_PART_NAMES = [("halves", 2), ("thirds", 3), ("quarters", 4), ("fifths", 5),
               ("sixths", 6), ("eighths", 8)]


def _k_frac_halves(level, band, rng, char, setting):
    pool = _PART_NAMES[:max(1, min(len(_PART_NAMES),
                                   _rs(band, level, 1, 6, cal=False)))]
    name, d = rng.choice(pool)
    w = rng.randint(*_span(min(14, _rs(band, level, 3, 12, cal=False)), 0.6))
    value = d * w
    return _q(f"How many {name} make {w} {_plural(w, 'whole')}?",
              f"{w} x {d} = {value} {name}.", value,
              spread=max(2, value // 4 + 2),
              props=[_prop("pie_gate", 0.5, 0.30, 1.5, f"1/{d}")],
              narr=f"The bridge over the {setting} is planked in {name}, and "
                   f"{char} needs the full count before crossing.")


def _k_frac_remaining(level, band, rng, char, setting):
    d = rng.choice(_denoms(band, level)[1:] or [3])
    eaten = rng.randint(1, d - 1)
    fr = Fraction(d - eaten, d)
    return _q(f"You eat {eaten} of {d} equal slices. What fraction is left?",
              f"{d} - {eaten} = {d - eaten} slices of {d}, "
              f"which is {_frac_label(fr)}.",
              float(fr), form="fraction", answer_text=_frac_label(fr),
              options=[(_frac_label(f), float(f), f == fr, {})
                       for f in _frac_options(rng, fr)],
              props=[_prop("pie_gate", 0.5, 0.30, 1.6, f"{d}")],
              narr=f"Supper on the bank of the {setting}, and {char} is "
                   f"working out what there is left for tomorrow.")


def _k_frac_sandbars(level, band, rng, char, setting):
    """THE ONE KEPT VISUAL MECHANIC. Ages 7-8 only.

    "Step on n of these d equal sandbars" is not a gimmick wrapped round a
    fraction - it IS the definition of n/d, acted out. It survives the
    2026-09-19b cull on that basis, restricted to the youngest band that
    meets fractions at all, and every other fraction archetype is a straight
    question. Any n complete sandbars grades as correct, which is what "n of
    d equal parts" means; the grader in app.py counts whole groups and does
    not care which ones.
    """
    d = rng.choice({1: [2], 2: [2, 3], 3: [3, 4], 4: [4, 5, 6], 5: [6, 8]}[_lvl(level)])
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
        f"Step on {n} whole {_plural(n, 'sandbar')} - that is {n}/{d}.",
        f"{char} must cross the {setting}! The crossing is {d} equal sandbars "
        f"holding {total} stones, and it only sinks level if {n}/{d} of it is "
        f"weighed down.",
        f"{n}/{d} of {total} stones = {safe_count} stones - any {n} whole sandbars.",
        stones, safe_count, "frac_sandbars",
        answer_group_count=n, group_count=d,
    )
    ch["props"] = [_prop("signpost", 0.06, 0.56, 1.1, f"{n}/{d}")]
    return ch


# ------------------------------------------------------------------- decimals

def _k_dec_add(level, band, rng, char, setting):
    places = 1 if level <= 2 else 2
    hi = max(1, _rs(band, level, 1, 12))
    unit = 10 ** places
    flo = max(1, int(hi * unit * 0.35))
    a = round(rng.randrange(flo, max(flo + 1, hi * unit)) / unit, places)
    b = round(rng.randrange(flo, max(flo + 1, hi * unit)) / unit, places)
    total = round(a + b, places)
    return _q(f"{a:.{places}f} + {b:.{places}f} = ?",
              f"{a:.{places}f} + {b:.{places}f} = {total:.{places}f}.",
              total, places=places, unit="L",
              spread=max(3, int(total * unit) // 6 + 2),
              props=[_prop("droplet", 0.33, 0.32, 1.3, f"{a:.{places}f}"),
                     _prop("droplet", 0.67, 0.32, 1.3, f"{b:.{places}f}")],
              narr=f"The flood gauge on the {setting} wants a reading, and "
                   f"{char} is the only one here who can give it one.")


def _k_dec_sub(level, band, rng, char, setting):
    places = 1 if level <= 2 else 2
    hi = max(2, _rs(band, level, 2, 14))
    unit = 10 ** places
    flo = max(unit, int(hi * unit * 0.45))
    a = round(rng.randrange(flo, max(flo + 1, hi * unit)) / unit, places)
    b = round(rng.randrange(max(1, int(a * unit * 0.25)),
                            max(2, int(a * unit))) / unit, places)
    left = round(a - b, places)
    return _q(f"{a:.{places}f} - {b:.{places}f} = ?",
              f"{a:.{places}f} - {b:.{places}f} = {left:.{places}f}.",
              left, places=places, unit="L",
              spread=max(3, int(left * unit) // 6 + 2),
              props=[_prop("droplet", 0.33, 0.32, 1.3, f"{a:.{places}f}"),
                     _prop("droplet", 0.67, 0.32, 1.3, f"-{b:.{places}f}")],
              narr=f"The water has dropped overnight. {char} reads the marks "
                   f"on the {setting} post and works out how far.")


def _k_dec_mul(level, band, rng, char, setting):
    hi = max(2, _rs(band, level, 3, 10))
    a = round(rng.randrange(max(10, int(hi * 10 * 0.45)),
                            max(11, hi * 10)) / 10, 1)
    if level <= 3:
        b = rng.randint(*_span(min(9, _rs(band, level, 2, 9, cal=False)), 0.6))
        value = round(a * b, 1)
        bare = f"{a:.1f} x {b} = ?"
        expl = f"{a:.1f} x {b} = {value:.1f}."
        places = 1
    else:
        b = round(rng.randrange(11, 99) / 10, 1)
        value = round(a * b, 2)
        bare = f"{a:.1f} x {b:.1f} = ?"
        expl = f"{a:.1f} x {b:.1f} = {value:.2f}."
        places = 2
    return _q(bare, expl, value, places=places,
              spread=max(4, int(value * 10 ** places) // 7 + 2),
              props=[_prop("droplet", 0.33, 0.32, 1.3, f"{a:.1f}"),
                     _prop("signpost", 0.67, 0.34, 1.1, f"x {b}")],
              narr=f"Every barrel along the {setting} holds the same, and "
                   f"{char} needs the total before the cart will move.")


# ------------------------------------------------------------------- geometry

_POLYGONS = [("triangle", 3), ("square", 4), ("rectangle", 4),
             ("pentagon", 5), ("hexagon", 6), ("octagon", 8)]
_SOLIDS = [("cube", {"faces": 6, "edges": 12, "corners": 8}),
           ("rectangular box", {"faces": 6, "edges": 12, "corners": 8}),
           ("square pyramid", {"faces": 5, "edges": 8, "corners": 5}),
           ("triangular prism", {"faces": 5, "edges": 9, "corners": 6})]


def _k_shape_sides(level, band, rng, char, setting):
    pool = _POLYGONS[:max(2, min(len(_POLYGONS),
                                 _rs(band, level, 2, 6, cal=False)))]
    name, sides = rng.choice(pool)
    part = "corners" if (level >= 3 and rng.random() < 0.4) else "sides"
    if band == "k1":
        # A pre-reader should not have to decode the word "hexagon", so the
        # shape itself is drawn on the plate and the prompt points at it.
        bare = f"How many {part} does this shape have?"
    else:
        bare = f"How many {part} does a {name} have?"
    return _q(bare, f"A {name} has {sides} {part}.", sides, spread=3,
              props=[_prop("shape", 0.5, 0.32, 1.8, name)],
              narr=f"A shape is carved deep into the rock face above the "
                   f"{setting}, and {char} is counting.")


def _k_shape_sides_total(level, band, rng, char, setting):
    cap = 6 if band == "k1" else 8
    pool = [p for p in _POLYGONS if p[1] <= cap]
    name, sides = rng.choice(pool)
    n = rng.randint(2, 3 if band == "k1"
                    else max(2, min(6, _rs(band, level, 2, 6, cal=False))))
    if band == "k1":
        while n * sides > K1_DOTS_MAX and n > 2:
            n -= 1
        if n * sides > K1_DOTS_MAX:
            name, sides = "triangle", 3
    value = n * sides
    return _q(f"How many sides do {n} {name}s have altogether?",
              f"{n} x {sides} = {value} sides.", value,
              spread=max(2, value // 4 + 2),
              props=_cluster("shape", n, 0.5, 0.32, name, 1.1, cols=3, step_x=0.15),
              narr=f"A whole row of them, cut into the {setting} wall. {char} "
                   f"counts every edge.")


def _k_solid_faces(level, band, rng, char, setting):
    pool = _SOLIDS[:2] if band == "k1" else _SOLIDS
    name, parts = rng.choice(pool)
    if band == "k1" or level <= 2:
        part = "faces"
    else:
        part = rng.choice(["faces", "edges", "corners"])
    value = parts[part]
    return _q(f"How many {part} does a {name} have?",
              f"A {name} has {value} {part}.", value, spread=4,
              props=[_prop("tile", 0.5, 0.32, 1.6, name)],
              narr=f"A solid block of stone sits in the path beside the "
                   f"{setting}, and it is not moving until {char} answers.")


def _k_shape_id(level, band, rng, char, setting):
    """The only archetype where the PICTURE is the answer. Ages 4-6 only.

    Kept, narrowly, because "Which shape has 3 sides?" is a verbatim grade-1
    concept in the team's own bank and because a child who cannot read has no
    other way to be asked a shape question. It is gated to k1 by
    CONCEPT_GRADES["shape_id"] = (1,), so a ten-year-old can never see it -
    which was the actual complaint. The door fiction is gone.
    """
    pool = rng.sample(SHAPES, min(len(SHAPES), 3 if level <= 3 else 4))
    name, sides = rng.choice(pool)
    if sides == 0:
        bare = "Which shape is round with no corners?"
        expl = "A circle has no straight sides at all."
    else:
        bare = f"Which shape has {sides} sides?"
        expl = f"A {name} has {sides} sides."
    options = [(("" if band == "k1" else nm), nm, nm == name,
                {"shape": nm, "tint": TINTS[i % len(TINTS)],
                 "dots": sd or None})
               for i, (nm, sd) in enumerate(pool)]
    return _q(bare, expl, 0, options=options,
              props=[_prop("signpost", 0.06, 0.34, 1.0, "?")],
              narr=f"Shapes are chalked along the wall beside the {setting}. "
                   f"Only one of them is the one {char} wants.")


def _k_square_perimeter(level, band, rng, char, setting):
    s = rng.randint(*_span(min(40, _rs(band, level, 4, 30))))
    value = 4 * s
    return _q(f"A square has {s} cm sides. What is its perimeter?",
              f"4 x {s} = {value} cm.", value, unit="cm",
              spread=max(3, value // 6 + 2),
              props=[_prop("rope", 0.5, 0.34, 1.5, f"{s} cm")],
              narr=f"A square plot is roped off beside the {setting}, and "
                   f"{char} has to walk the whole edge of it.")


def _k_rect_perimeter(level, band, rng, char, setting):
    lo, hi = _span(min(40, _rs(band, level, 4, 30)), 0.45)
    length = rng.randint(lo, hi)
    width = rng.randint(lo, hi)
    value = 2 * (length + width)
    return _q(f"A rectangle is {length} cm by {width} cm. Perimeter?",
              f"2 x ({length} + {width}) = {value} cm.", value, unit="cm",
              spread=max(3, value // 6 + 2),
              props=[_prop("fence_post", 0.32, 0.32, 1.0, f"{length} cm"),
                     _prop("fence_post", 0.68, 0.32, 1.0, f"{width} cm")],
              narr=f"{char} paces out the plot beside the {setting} and needs "
                   f"the distance all the way round.")


def _k_rect_area(level, band, rng, char, setting):
    lo, hi = _span(min(40, _rs(band, level, 4, 26)), 0.45)
    length = rng.randint(lo, hi)
    width = rng.randint(lo, hi)
    value = length * width
    return _q(f"A rectangle is {length} cm by {width} cm. Area?",
              f"{length} x {width} = {value} square cm.", value,
              unit="cm", spread=max(4, value // 6 + 2),
              props=[_prop("tile", 0.5, 0.32, 1.6, f"{length}x{width}")],
              narr=f"The flagstone by the {setting} has to be measured across "
                   f"and along before {char} can cut a new one.")


def _k_rect_side_from_perimeter(level, band, rng, char, setting):
    lo, hi = _span(min(40, _rs(band, level, 4, 26)), 0.45)
    length = rng.randint(lo, hi)
    width = rng.randint(lo, hi)
    perim = 2 * (length + width)
    return _q(f"A rectangle has perimeter {perim} cm and length {length} cm. Width?",
              f"{perim} / 2 = {length + width}, minus {length} = {width} cm.",
              width, unit="cm", spread=max(3, width // 3 + 2),
              props=[_prop("rope", 0.5, 0.34, 1.5, f"{perim} cm")],
              narr=f"The rope round the plot is the right length, but one side "
                   f"is missing from the {setting} surveyor's slate.")


def _k_square_side_from_area(level, band, rng, char, setting):
    s = rng.randint(*_span(min(30, _rs(band, level, 3, 20)), 0.5))
    area = s * s
    return _q(f"A square has area {area} square cm. How long is each side?",
              f"{s} x {s} = {area}, so each side is {s} cm.", s, unit="cm",
              spread=max(2, s // 2 + 2),
              props=[_prop("tile", 0.5, 0.32, 1.6, f"{area}")],
              narr=f"The paving stone by the {setting} is square, and only its "
                   f"area is written on it.")


def _k_triangle_area(level, band, rng, char, setting):
    lo, hi = _span(min(40, _rs(band, level, 5, 26)), 0.45, 3)
    base = rng.randrange(max(2, lo - lo % 2), max(4, hi + 1), 2)  # even: half is whole
    height = rng.randint(lo, hi)
    value = base * height // 2
    return _q(f"A triangle has base {base} cm and height {height} cm. Area?",
              f"{base} x {height} / 2 = {value} square cm.", value,
              unit="cm", spread=max(4, value // 6 + 2),
              props=[_prop("shape", 0.5, 0.32, 1.8, "triangle")],
              narr=f"A wedge of sailcloth lies on the rocks by the {setting}, "
                   f"and {char} needs to know how much of it there is.")


def _k_box_volume(level, band, rng, char, setting):
    lo, hi = _span(min(20, _rs(band, level, 3, 14)), 0.5)
    l = rng.randint(lo, hi)
    w = rng.randint(lo, hi)
    h = rng.randint(lo, hi)
    value = l * w * h
    return _q(f"A box is {l} by {w} by {h} cm. Its volume?",
              f"{l} x {w} x {h} = {value} cubic cm.", value, unit="cm",
              spread=max(5, value // 6 + 2),
              props=[_prop("tile", 0.5, 0.32, 1.7, f"{l}x{w}x{h}")],
              narr=f"A crate has washed up on the {setting} shore and {char} "
                   f"wants to know what it would hold.")


def _k_triangle_angle_sum(level, band, rng, char, setting):
    if level <= 2:
        a = 90                                    # the bank's easiest form
        b = rng.randint(20, 65)
    else:
        a = rng.randint(20, 100)
        b = rng.randint(20, max(21, 155 - a))
    value = 180 - a - b
    if value < 5:                                 # never ask for a sliver
        b = max(20, b - (5 - value))
        value = 180 - a - b
    return _q(f"Two angles of a triangle are {a} and {b} degrees. Third?",
              f"180 - {a} - {b} = {value} degrees.", value, unit="degrees",
              spread=max(4, value // 5 + 2),
              props=[_prop("shape", 0.5, 0.32, 1.9, "triangle"),
                     _prop("signpost", 0.82, 0.40, 1.0, f"{a}/{b}")],
              narr=f"The ledge above the {setting} cuts a triangle against the "
                   f"sky, and {char} must read the last corner of it.")


# -------------------------------------------------------------------- algebra

def _k_missing_addend(level, band, rng, char, setting):
    x = rng.randint(*_span(min(200, _rs(band, level, 12, 95)), 0.45))
    c = rng.randint(*_span(min(200, _rs(band, level, 10, 95)), 0.45))
    b = x + c
    return _q(f"x + {c} = {b}. What is x?", f"x = {b} - {c} = {x}.", x,
              spread=max(2, x // 3 + 2),
              props=[_prop("sack", 0.34, 0.36, 1.1, "x"),
                     _prop("signpost", 0.68, 0.34, 1.2, f"= {b}")],
              narr=f"A rune-stone blocks the path past the {setting}. One mark "
                   f"on it has worn away and {char} must chalk it back.")


def _k_missing_factor(level, band, rng, char, setting):
    m = rng.randint(*_span(min(12, _rs(band, level, 2, 12, cal=False)), 0.5))
    x = rng.randint(*_span(min(120, _rs(band, level, 8, 50)), 0.45))
    b = m * x
    return _q(f"{m}x = {b}. What is x?", f"x = {b} / {m} = {x}.", x,
              spread=max(2, x // 3 + 2),
              props=_cluster("sack", m, 0.5, 0.34, "x", 0.95, cols=5, step_x=0.08)
              + [_prop("signpost", 0.9, 0.34, 1.1, f"= {b}")],
              narr=f"Identical sacks, all the same weight, stacked against the "
                   f"{setting} gate. {char} has to work out what one holds.")


def _k_two_step_linear(level, band, rng, char, setting):
    m = rng.randint(*_span(min(9, _rs(band, level, 2, 9, cal=False)), 0.5))
    x = rng.randint(*_span(min(90, _rs(band, level, 6, 45)), 0.45))
    c = rng.randint(*_span(min(90, _rs(band, level, 8, 45)), 0.45))
    b = m * x + c
    return _q(f"{m}x + {c} = {b}. What is x?",
              f"{b} - {c} = {m * x}, then {m * x} / {m} = {x}.", x,
              spread=max(2, x // 3 + 2),
              props=[_prop("sack", 0.30, 0.36, 1.1, "x"),
                     _prop("gem", 0.50, 0.34, 0.9, str(c)),
                     _prop("signpost", 0.74, 0.34, 1.2, f"= {b}")],
              narr=f"Two steps to undo, in the right order. The rune-gate over "
                   f"the {setting} will not open for a guess.")


# ------------------------------------------------------ speed, distance, time

def _k_sdt_distance(level, band, rng, char, setting):
    speed = rng.randint(*_span(min(140, _rs(band, level, 32, 110)), 0.6, 5))
    t = rng.randint(*_span(min(9, _rs(band, level, 2, 7, cal=False)), 0.6))
    dist = speed * t
    return _q(f"A train runs {speed} km/h for {t} hours. How far in km?",
              f"{speed} x {t} = {dist} km.", dist, unit="km",
              spread=max(5, dist // 6 + 2),
              props=[_prop("clock", 0.32, 0.32, 1.3, f"{t} h"),
                     _prop("signpost", 0.68, 0.34, 1.1, f"{speed} km/h")],
              narr=f"The line runs straight out across the {setting}, and "
                   f"{char} is working out where it ends up.")


def _k_sdt_time(level, band, rng, char, setting):
    speed = rng.randint(*_span(min(140, _rs(band, level, 32, 110)), 0.6, 5))
    t = rng.randint(*_span(min(9, _rs(band, level, 2, 7, cal=False)), 0.6))
    dist = speed * t
    return _q(f"A cyclist rides {dist} km at {speed} km/h. How many hours?",
              f"{dist} / {speed} = {t} hours.", t, unit="h",
              spread=max(2, t + 2),
              props=[_prop("clock", 0.5, 0.32, 1.5, "?"),
                     _prop("signpost", 0.80, 0.36, 1.1, f"{speed} km/h")],
              narr=f"{char} has to be at the far side of the {setting} before "
                   f"dark, and the light is already going.")


def _k_sdt_speed(level, band, rng, char, setting):
    speed = rng.randint(*_span(min(140, _rs(band, level, 32, 110)), 0.6, 5))
    t = rng.randint(*_span(min(9, _rs(band, level, 2, 7, cal=False)), 0.6))
    dist = speed * t
    return _q(f"A train covers {dist} km in {t} hours. Its speed in km/h?",
              f"{dist} / {t} = {speed} km/h.", speed, unit="km/h",
              spread=max(3, speed // 4 + 2),
              props=[_prop("clock", 0.32, 0.32, 1.3, f"{t} h"),
                     _prop("signpost", 0.68, 0.34, 1.1, f"{dist} km")],
              narr=f"The stationmaster's log by the {setting} has the distance "
                   f"and the hours, and a blank where the speed should be.")


# ================================================== SPEC -> CHALLENGE

_TOPIC_PROMPT_LEAD = {
    "counting_and_comparing": "counting",
    "addition": "sum",
    "subtraction": "difference",
    "time_and_money": "money",
    "multiplication": "product",
    "fractions": "fraction",
    "decimals": "measure",
    "geometry": "measure",
    "algebra": "rune",
    "speed_distance_time": "reckoning",
}


def _spec_narrative(spec, char, setting) -> str:
    return spec.get("narr") or (
        f"{char} stops where the path meets the {setting}. There is a "
        f"reckoning to be done here before anything else happens.")


def _as_pick(spec, band, rng, char, setting, archetype, use_story=False):
    """MULTIPLE CHOICE. The answers are DOM cards; the picture stays a picture."""
    prompt = (spec.get("story") if use_story else None) or spec["bare"]
    places = int(spec.get("places") or 0)
    if spec.get("options"):
        stones = _option_stones(rng, spec["options"])
    else:
        value = spec["value"]
        if places:
            unit_mul = 10 ** places
            scaled = int(round(value * unit_mul))
            stones = _choice_stones(
                rng, scaled, spec.get("spread") or max(3, abs(scaled) // 6 + 2),
                4, formatter=lambda v: f"{v / unit_mul:.{places}f}", band=band)
        else:
            iv = int(round(value))
            stones = _choice_stones(
                rng, iv, spec.get("spread") or max(2, abs(iv) // 6 + 2),
                4, band=band)
    ch = _base("single_choice", "set", prompt,
               _spec_narrative(spec, char, setting), spec["expl"],
               stones, 1, archetype)
    ch["props"] = list(spec.get("props") or [])
    return ch


def _as_type(spec, band, rng, char, setting, archetype):
    """FREE RESPONSE. Typed, graded against answer_value with a tolerance."""
    return _free_response(
        spec["bare"], _spec_narrative(spec, char, setting), spec["expl"],
        spec["value"], archetype,
        places=int(spec.get("places") or 0), unit=spec.get("unit") or "",
        props=list(spec.get("props") or []),
        form=spec.get("form"), answer_text=spec.get("answer_text"))


def _make_generator(concept, fn, presentation):
    """Bind one concept generator to one presentation."""
    name = f"{concept}_{presentation}"

    def gen(level, band, rng, char, setting):
        spec = fn(level, band, rng, char, setting)
        if presentation == "type":
            return _as_type(spec, band, rng, char, setting, name)
        return _as_pick(spec, band, rng, char, setting, name,
                        use_story=(presentation == "story"))
    gen.__name__ = f"_gen_{name}"
    gen.__doc__ = f"{presentation} presentation of the {concept} concept."
    return gen


# =============================================================== THE REGISTRY
#
# (concept, topic, generator, presentations). Bands come from CONCEPT_GRADES
# via `concept_bands`, intersected with the bands that offer the topic -
# nobody hand-maintains a band list any more, which is exactly how
# "shape_door" survived in the 9-10 band for so long.
#
#   "pick"   multiple choice, bare computation      ("4.24 + 0.59 = ?")
#   "story"  multiple choice, word problem          ("3 red and 4 blue...")
#   "type"   free response, bare computation        (never for ages 4-6)

_CONCEPT_SPECS: tuple[tuple, ...] = (
    # counting & comparing
    ("count_next", "counting_and_comparing", _k_count_next, ("pick", "type")),
    ("compare_size", "counting_and_comparing", _k_compare_size, ("pick", "type")),
    # addition
    ("add_2", "addition", _k_add_2, ("pick", "story", "type")),
    ("add_3", "addition", _k_add_3, ("pick", "story", "type")),
    ("add_sub_chain", "addition", _k_add_sub_chain, ("pick", "type")),
    # subtraction
    ("sub_2", "subtraction", _k_sub_2, ("pick", "story", "type")),
    ("add_sub_chain", "subtraction", _k_add_sub_chain, ("pick", "type")),
    # time & money
    ("coin_total", "time_and_money", _k_coin_total, ("pick", "type")),
    ("money_add_cents", "time_and_money", _k_money_add_cents, ("pick", "type")),
    ("clock_add_hours", "time_and_money", _k_clock_add_hours, ("pick", "type")),
    # multiplication (and division, which has no separate topic)
    ("mul_2", "multiplication", _k_mul_2, ("pick", "story", "type")),
    ("div_exact", "multiplication", _k_div_exact, ("pick", "story", "type")),
    ("div_remainder", "multiplication", _k_div_remainder, ("pick", "type")),
    # fractions
    ("frac_of_whole", "fractions", _k_frac_of_whole, ("pick", "type")),
    ("frac_add_same", "fractions", _k_frac_add_same, ("pick", "type")),
    ("frac_add_unlike", "fractions", _k_frac_add_unlike, ("pick", "type")),
    ("frac_multiply", "fractions", _k_frac_multiply, ("pick", "type")),
    ("frac_divide", "fractions", _k_frac_divide, ("pick", "type")),
    ("frac_simplify", "fractions", _k_frac_simplify, ("pick", "type")),
    ("frac_halves", "fractions", _k_frac_halves, ("pick", "type")),
    ("frac_remaining", "fractions", _k_frac_remaining, ("pick", "type")),
    # decimals
    ("dec_add", "decimals", _k_dec_add, ("pick", "type")),
    ("dec_sub", "decimals", _k_dec_sub, ("pick", "type")),
    ("dec_mul", "decimals", _k_dec_mul, ("pick", "type")),
    # geometry
    ("shape_sides", "geometry", _k_shape_sides, ("pick", "type")),
    ("shape_sides_total", "geometry", _k_shape_sides_total, ("pick", "type")),
    ("solid_faces", "geometry", _k_solid_faces, ("pick", "type")),
    ("shape_id", "geometry", _k_shape_id, ("pick",)),
    ("square_perimeter", "geometry", _k_square_perimeter, ("pick", "type")),
    ("rect_perimeter", "geometry", _k_rect_perimeter, ("pick", "type")),
    ("rect_area", "geometry", _k_rect_area, ("pick", "type")),
    ("rect_side_from_perimeter", "geometry", _k_rect_side_from_perimeter, ("pick", "type")),
    ("square_side_from_area", "geometry", _k_square_side_from_area, ("pick", "type")),
    ("triangle_area", "geometry", _k_triangle_area, ("pick", "type")),
    ("box_volume", "geometry", _k_box_volume, ("pick", "type")),
    ("triangle_angle_sum", "geometry", _k_triangle_angle_sum, ("pick", "type")),
    # algebra
    ("missing_addend", "algebra", _k_missing_addend, ("pick", "type")),
    ("missing_factor", "algebra", _k_missing_factor, ("pick", "type")),
    ("two_step_linear", "algebra", _k_two_step_linear, ("pick", "type")),
    # speed / distance / time
    ("sdt_distance", "speed_distance_time", _k_sdt_distance, ("pick", "type")),
    ("sdt_time", "speed_distance_time", _k_sdt_time, ("pick", "type")),
    ("sdt_speed", "speed_distance_time", _k_sdt_speed, ("pick", "type")),
)

# The single visual mechanic that survived the cull, registered by hand
# because it is not a (concept, presentation) pair - see _k_frac_sandbars.
_HANDMADE = (
    ("frac_sandbars", "fractions", ("23",), _k_frac_sandbars, "frac_of_whole"),
)

# Below this many archetypes a (topic, band) cell is unplayable, so the gate
# is relaxed for it - by at most one grade in EITHER direction, and only for
# that cell. In practice this fires for exactly one cell: time_and_money for
# ages 4-6, where the bank's only money rows are grade 2 but its easiest ones
# ("5 cents and find 5 more") are plainly fine for a five-year-old. It is
# reported in RELAXED_CELLS so the compromise stays visible in /api/health
# rather than turning into folklore.
MIN_ARCHETYPES_PER_CELL = 2
RELAXED_REACH_GRADES = 1
RELAXED_CELLS: list[tuple[str, str]] = []

_ARCHETYPES: dict[str, list[tuple[str, tuple[str, ...], object]]] = {
    t: [] for t in TOPICS}
ARCHETYPE_CONCEPT: dict[str, str] = {}
ARCHETYPE_TOPIC: dict[str, str] = {}
NON_READING_ARCHETYPES: set[str] = set()
FREE_RESPONSE_ARCHETYPES: set[str] = set()
GROUP_ARCHETYPES: set[str] = {"frac_sandbars"}


def _register(name, topic, bands, fn, concept, presentation):
    bands = tuple(b for b in ("k1", "23", "45") if b in bands)
    if not bands:
        return
    _ARCHETYPES[topic].append((name, bands, fn))
    ARCHETYPE_CONCEPT[name] = concept
    ARCHETYPE_TOPIC[name] = topic
    if presentation == "type":
        FREE_RESPONSE_ARCHETYPES.add(name)
    elif "k1" in bands:
        # Every k1 answer card is pips, a drawn shape or a pip pile - never a
        # word. The k1 generators above are range-limited to guarantee it and
        # test_engine re-checks every one of them.
        NON_READING_ARCHETYPES.add(name)


def _build_registry():
    for concept, topic, fn, presentations in _CONCEPT_SPECS:
        allowed = set(concept_bands(concept)) & set(TOPIC_BANDS.get(topic, ()))
        for presentation in presentations:
            bands = allowed - ({"k1"} if presentation == "type" else set())
            _register(f"{concept}_{presentation}", topic, bands,
                      _make_generator(concept, fn, presentation),
                      concept, presentation)
    for name, topic, bands, fn, concept in _HANDMADE:
        _register(name, topic, set(bands) & set(TOPIC_BANDS.get(topic, ())),
                  fn, concept, "group")

    # --- the relaxation valve -------------------------------------------
    for topic, bands in TOPIC_BANDS.items():
        for band in bands:
            if len([1 for _n, bs, _f in _ARCHETYPES[topic] if band in bs]) \
                    >= MIN_ARCHETYPES_PER_CELL:
                continue
            RELAXED_CELLS.append((topic, band))
            for concept, t, fn, presentations in _CONCEPT_SPECS:
                if t != topic or band not in TOPIC_BANDS.get(topic, ()):
                    continue
                grades = CONCEPT_GRADES.get(concept) or ()
                # The relaxation reaches at most ONE grade in either
                # direction. A five-year-old must never be handed a grade-5
                # concept just to keep a menu entry alive - the sole point of
                # this valve is the grade-2/band-k1 money gap.
                if not grades:
                    continue
                if min(grades) > BAND_GRADES[band][1] + RELAXED_REACH_GRADES:
                    continue
                if concept in CONCEPT_BAND_OVERRIDE:
                    continue                 # a product decision, not a gap
                for presentation in presentations:
                    if presentation == "type" and band == "k1":
                        continue
                    name = f"{concept}_{presentation}"
                    existing = next((i for i, (n, _b, _f) in
                                     enumerate(_ARCHETYPES[topic]) if n == name), None)
                    if existing is None:
                        _register(name, topic, {band},
                                  _make_generator(concept, fn, presentation),
                                  concept, presentation)
                    else:
                        n, bs, f = _ARCHETYPES[topic][existing]
                        if band not in bs:
                            merged = tuple(b for b in ("k1", "23", "45")
                                           if b in set(bs) | {band})
                            _ARCHETYPES[topic][existing] = (n, merged, f)
                            if band == "k1" and name not in FREE_RESPONSE_ARCHETYPES:
                                NON_READING_ARCHETYPES.add(name)

    for topic, entries in _ARCHETYPES.items():
        ARCHETYPE_INDEX[topic] = [n for n, _b, _f in entries]


_build_registry()


def archetypes_for(topic: str, band: str) -> list[str]:
    return [nm for nm, bands, _f in _ARCHETYPES.get(topic, []) if band in bands]


def playable_archetypes(topic: str, band: str) -> list[str]:
    """What the game may CHOOSE for this cell.

    Same as `archetypes_for` except that ages 4-6 only ever get archetypes
    whose answer cards carry no words. `archetypes_for` stays unfiltered so
    that an explicit request (and the test sweep) can still reach every
    generator.
    """
    names = archetypes_for(topic, band)
    if band != "k1":
        return names
    readable = [n for n in names if n in NON_READING_ARCHETYPES]
    return readable or names


# Where each school grade sits on the 13-rung ladder, used to give every
# CONCEPT a rung of its own. Without this, level 5 was as likely to roll
# "how many sides do 2 triangles have" as it was a perimeter - the level
# controlled the NUMBERS but not which idea got asked about.
_GRADE_RUNG = {1: 3, 2: 6, 3: 8, 4: 10, 5: 12}


def concept_rung(concept: str) -> float:
    """The rung a concept naturally belongs on, from the grades it lives in."""
    grades = CONCEPT_GRADES.get(concept) or ()
    if not grades:
        return 7.0
    return sum(_GRADE_RUNG.get(int(g), 7) for g in grades) / float(len(grades))


def _concept_weights(names, band, level):
    """Prefer archetypes whose CONCEPT matches the rung we are playing at.

    Soft, not absolute: an easier concept at level 5 is still reachable (it
    keeps a long quest from repeating itself), just much less likely.
    """
    here = difficulty_rung(band, level)
    return [1.0 / (1.0 + abs(concept_rung(ARCHETYPE_CONCEPT.get(n, "")) - here) ** 2.2)
            for n in names]


def concepts_for(topic: str, band: str) -> list[str]:
    """The distinct CONCEPTS playable in a cell - what the bank actually gated."""
    seen = []
    for name in archetypes_for(topic, band):
        c = ARCHETYPE_CONCEPT.get(name)
        if c and c not in seen:
            seen.append(c)
    return seen


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
                    objects=None, seed=None, story_noun=None):
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
    # The finale quotes the child's own haul back at them, so it speaks in
    # the quest's noun like every other question does.
    prev_noun = getattr(_CTX, "noun", None)
    _CTX.noun = story_noun

    if band == "k1":
        # Ages 4-6 answer with countable pips, so every number in the finale -
        # including the haul the story quotes back at them - has to stay
        # inside K1_DOTS_MAX. Clamp the HAUL, not the answer, so the prose and
        # the arithmetic still agree with each other.
        items = [(k, min(v, 3)) for k, v in items][:2]

    # The haul is kept in four generic slots (berries / planks / lanterns /
    # gems). The child never heard those words - they heard the quest's own
    # noun - so the BIGGEST slot is renamed to it. The others keep their own
    # names, because "3 rods and 2 rods" is not a question.
    display = {}
    if story_noun:
        display[items[0][0]] = story_noun

    def _name(slot):
        return display.get(slot, slot)

    a_name, a = _name(items[0][0]), items[0][1]
    if len(items) > 1:
        b_name, b = _name(items[1][0]), items[1][1]
        pair = f"{a} {a_name} and {b} {b_name}"
    else:
        # Only one kind carried: "3 berries and 1 berries" is a bug a child
        # can read, so the second amount is phrased as "more".
        b_name, b = a_name, max(1, a // 2)
        pair = f"{a} {a_name} and {b} more"
    total = sum(v for _, v in items)
    haul = ", ".join(f"{v} {_name(k)}" for k, v in items)

    lead = (f"Everything {char} gathered on the way here comes down to this "
            f"one last gate: {haul}.")

    unit = ""
    if topic == "addition":
        value = a + b
        prompt = f"{pair}. How many altogether?"
        expl = f"{a} + {b} = {value}."
    elif topic == "subtraction":
        take = rng.randint(1, max(1, total - 1))
        value = total - take
        prompt = f"You carry {total} {a_name}. The gate keeps {take}. Left?"
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
        prompt = f"Split {used} {a_name} into {d} equal piles. How many each?"
        expl = f"{used} / {d} = {share} in each pile."
    elif topic == "decimals":
        per = rng.choice([0.25, 0.5, 1.5, 2.5])
        value = round(total * per, 2)
        prompt = f"Your {total} lanterns hold {per} litres each. Total litres?"
        expl = f"{total} x {per} = {value} L."
        unit = "L"
    elif topic == "geometry":
        value = 2 * (a + b)
        prompt = f"A plot {a} by {b} metres. Perimeter in metres?"
        expl = f"2 x ({a} + {b}) = {value} metres."
        unit = "m"
    elif topic == "algebra":
        keep = rng.randint(1, max(1, total - 1))
        value = total - keep
        prompt = f"x + {keep} = {total}. What is x?"
        expl = f"x = {total} - {keep} = {value}."
    elif topic == "speed_distance_time":
        hours = rng.randint(2, 5)
        dist, speed = _divisor_split(total, hours)
        value = speed
        prompt = f"{dist} km in {hours} hours. Speed in km/h?"
        expl = f"{dist} / {hours} = {speed} km/h."
        unit = "km/h"
    elif topic == "time_and_money":
        per = 2 if band == "k1" else rng.choice([2, 5, 10])
        value = total * per
        prompt = f"Your {total} tokens are worth {per} cents each. Total?"
        expl = f"{total} x {per} = {value} cents."
        unit = "c"
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
                            places=places, unit=unit)

    ch["props"] = [_prop("gate", 0.5, 0.30, 1.6, "final"),
                   _prop("lantern", 0.18, 0.40, 1.2, "last light"),
                   _prop("lantern", 0.82, 0.40, 1.2, "last light")]
    _CTX.noun = prev_noun
    ch["is_finale"] = True
    ch["finale_haul"] = dict((k, v) for k, v in items)
    ch["topic"] = topic
    ch["band"] = band
    ch["level"] = level
    ch["character_name"] = char
    ch["setting"] = setting
    ch.setdefault("obstacle_count", 0)
    return ch


# ------------------------------------------------- navigational obstacles

_OBSTACLE_KINDS = ["tree", "fence_post", "rope", "gate"]


def _add_navigation_obstacles(challenge: dict, rng: random.Random, count: int) -> None:
    """Product brief: "more obstacles when the child makes more mistakes."

    Reconciliation: the obstacles are NAVIGATIONAL (scenery to move around),
    never extra wrong answers. A struggling child gets a longer, twistier
    journey but an EASIER sum. Difficulty of the *math* is handled separately,
    and it moves DOWN on mistakes. See app.py / story_engine.py.
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
                       nav_obstacles=None, exclude_archetypes=(),
                       story_noun=None, story_toll=None):
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
        pool = pool or safe
        # Weighted, not uniform: the LEVEL should decide which idea gets asked
        # about, not only how big its numbers are. See `_concept_weights`.
        weights = _concept_weights([nm for nm, _f in pool], band, level)
        chosen = rng.choices(pool, weights=weights, k=1)[0]

    # Park the cell the generator is about to build so `_cal` and `_rs` can
    # look up the CSV bank's observed operand range for it, and so `_rs` knows
    # which rungs this topic spans. Always cleared, even on error: a leaked
    # context would calibrate the NEXT challenge against the wrong topic.
    prev = (getattr(_CTX, "topic", None), getattr(_CTX, "band", None),
            getattr(_CTX, "level", None), getattr(_CTX, "noun", None),
            getattr(_CTX, "char", None), getattr(_CTX, "toll", None))
    _CTX.topic, _CTX.band, _CTX.level = topic, band, level
    _CTX.noun, _CTX.char, _CTX.toll = story_noun, char, story_toll
    try:
        challenge = chosen[1](level, band, rng, char, setting)
    finally:
        (_CTX.topic, _CTX.band, _CTX.level, _CTX.noun, _CTX.char,
         _CTX.toll) = prev

    if nav_obstacles is None:
        nav_obstacles = min(int(mistakes_total or 0), 5)
    _add_navigation_obstacles(challenge, rng, nav_obstacles)

    challenge["topic"] = topic
    challenge["band"] = band
    challenge["level"] = level
    challenge["concept"] = ARCHETYPE_CONCEPT.get(challenge.get("archetype"), "")
    challenge["story_noun"] = story_noun or ""
    challenge["story_toll"] = story_toll or ""
    challenge["difficulty_rung"] = difficulty_rung(band, level)
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


# -------------------------------------------------------- difficulty table
#
# `python3 math_engine.py` prints every topic x band x level 1-5 prompt. This
# is the artefact the team eyeballs to judge the curve, and the reason the
# band-relative ladder is checkable by a human rather than only by a test.

def difficulty_table(seed: int = 7, per_cell: int = 1) -> list[dict]:
    rows = []
    for topic in TOPICS:
        for band in TOPIC_BANDS[topic]:
            for level in range(1, 6):
                for i in range(per_cell):
                    ch = generate_challenge(
                        topic, band, level, "Mochi", ["cat", "river"],
                        seed=seed + level * 101 + i * 7717)
                    rows.append({
                        "topic": topic, "band": band, "level": level,
                        "rung": ch["difficulty_rung"],
                        "archetype": ch["archetype"],
                        "concept": ch.get("concept", ""),
                        "type": ch["question_type"],
                        "prompt": ch["prompt"],
                    })
    return rows


if __name__ == "__main__":                        # pragma: no cover
    print(f"{'topic':<22}{'band':<5}{'lv':<3}{'rung':<5}"
          f"{'concept':<26}{'kind':<15}prompt")
    print("-" * 132)
    last = None
    for row in difficulty_table():
        if last and last != (row["topic"], row["band"]):
            print()
        last = (row["topic"], row["band"])
        kind = "free response" if row["type"] == "free_response" else row["type"]
        print(f'{row["topic"]:<22}{row["band"]:<5}{row["level"]:<3}'
              f'{row["rung"]:<5}{row["concept"]:<26}{kind:<15}{row["prompt"]}')
