"""do-IT-oodle - question-bank mining: number ranges AND concept gating.

WHAT THIS MODULE IS FOR
-----------------------
`math_engine.py` invents its own math challenges and is the single source of
truth for the arithmetic, the answer key and the fiction. What it does *not*
know on its own is what a real grade-3 question actually looks like. Two
separate things are missing, and this module supplies both:

  1. MAGNITUDE.  Is "7 x 8" right for level 2, or should it be "18 x 4"?
     Answered by `scale_range()` - percentile ranges per (topic, band, level).

  2. KIND.  *** 2026-09-19, the big one. ***  Which CONCEPTS belong at which
     grade at all? "How many sides does a triangle have?" is grade-1 material
     and must never be handed to a ten-year-old; "two angles of a triangle
     are 62 and 37 degrees, find the third" is grade 4-5 and must never be
     handed to a five-year-old. Answered by `concept_grades()`, which
     classifies every bank row into a short CONCEPT ID and records which
     grades that concept was observed in. `math_engine` mirrors the result in
     its own `CONCEPT_GRADES` table (so the offline game gates identically)
     and the test harness asserts the two agree.

     The classifier reads question text, and question text NEVER LEAVES THE
     FRAME it was read in - only the concept id, which is one of our own
     short snake_case names, survives. See the guarantee below.

HARD GUARANTEE: NO QUESTION TEXT SURVIVES
-----------------------------------------
Nothing on `Bank`, and nothing returned by any function here, ever contains a
question, an answer string, a difficulty label, or any other prose from the
CSVs. Only numbers, our own short topic/band/concept ids, and the CSV file
stems (needed for `Bank.unmapped`). The `__main__` self-test walks the whole
Bank object recursively and fails if it finds any string longer than 40
chars. We never want to accidentally leak a bank question into a generated
challenge - children would be served the CSV, not the story.

WHERE THE FOLDER LIVES
----------------------
    <repo>/math_questions/            <- DEFAULT_DIR, i.e. Path(__file__).parent.parent
        README.txt
        grade_1/addition.csv
        grade_1/subtraction.csv
        grade_1/counting_and_comparing.csv
        grade_1/basic_geometry.csv
        grade_2/...   grade_3/...   grade_4/...   grade_5/...

Filename convention: `grade_<N>/<topic_stem>.csv` for N in 1..5. The grade
comes from the *directory* name; the topic comes from the *file* stem. Any
directory that does not match `grade_<N>` and any non-.csv file is ignored.

CSV SCHEMA
----------
Header row, then one row per question, with exactly these columns:

    question           free text, may be quoted and contain commas
                       e.g. "What is the area of a triangle with base 8 cm
                       and height 3 cm, in square cm?"
    answer             NOT always a plain number. Seen in the wild:
                       "23", "4.5", "2/3", "1 1/2", "13 remainder 2",
                       "$3.50", "1,200", "45 cm", and a few word answers
                       ("circle", "square") which are simply unparseable.
    difficulty_level   1, 2 or 3
    difficulty_label   "easy" / "medium" / "hard" (ignored - it is redundant
                       with difficulty_level, and it is prose)

Because questions are quoted and contain commas, the `csv` module is used.
Never split on commas by hand.

GRADES -> OUR AGE BANDS
-----------------------
    grade 1        -> band "k1"   (ages 4-6)
    grades 2, 3    -> band "23"   (ages 7-8)
    grades 4, 5    -> band "45"   (ages 9-10)

CSV DIFFICULTY 1-3 -> OUR LEVELS 1-5
------------------------------------
Our game has five levels per band; the bank has three difficulty steps. Rows
fan out, so every one of our levels has data:

    difficulty 1 (easy)   -> levels 1 and 2
    difficulty 2 (medium) -> level 3
    difficulty 3 (hard)   -> levels 4 and 5

TOPIC MAPPING
-------------
CSV stems do not match our topic ids one-to-one. `TOPIC_FANOUT` is the
authority (a stem may feed several of our topics); `TOPIC_ALIASES` lists the
primary target for readability, or None for stems with no home. Notably:
`division` feeds "multiplication" (we have no separate division topic) and
`addition_and_subtraction` feeds BOTH "addition" and "subtraction".
`time_and_money` and `counting_and_comparing` used to have no home and landed
in `Bank.unmapped`; since the 2026-09-19 concept-gating pass they map onto the
topics of the same name, because those ARE first-class topics in the game and
their rows are the only evidence for what a grade-1/2 money or counting
question should look like.

CONCEPT GATING
--------------
`CONCEPT_PATTERNS` is an ordered list of (concept_id, regex). The FIRST match
wins, so the list runs most-specific first ("2/3 divided by 1/3" is
`frac_divide`, not `frac_add_same`). Every one of the 773 bank rows classifies
except a single grade-1 stop-sign phrasing, and the self-test asserts coverage
stays above 99%.

Band eligibility, given a concept observed in grades G and a band spanning
grades [g_lo, g_hi]:

    not too advanced :  min(G) <= g_hi
    not outgrown     :  max(G) >= g_lo - 1     (one grade of review carry-over)

Both must hold. That single rule is what drops shape identification out of
ages 9-10 and triangle-angle-sum out of ages 4-6. `math_engine` mirrors the
mined table in `CONCEPT_GRADES` so the gate is identical with the folder
absent; `concept_grades()` is what the test harness compares it against.

WHEN THE FOLDER IS MISSING
--------------------------
That is a supported, first-class state - the demo runs on it. `load()`
returns a Bank with `available is False`, `calibration()` returns None for
every cell, and `scale_range(lo, hi, ...)` returns `(lo, hi)` unchanged, i.e.
exact identity, so `math_engine` generates precisely what it would have
generated if this file did not exist. Nothing in this module raises, ever -
every entry point is wrapped. A malformed, empty or unreadable CSV degrades
the same way.

Import is free: there is ZERO file I/O at import time. `load()` is lazy and
caches a module-level singleton; pass `force=True` to re-read from disk.
"""

from __future__ import annotations

import csv
import math
import pathlib
import re
import statistics
import threading

# ----------------------------------------------------------------- constants

# Resolved from this file, not from cwd: the server is started from the repo
# root, from backend/, and from pytest, and all three must find the folder.
# Deliberately NOT .resolve() - resolve() stats the filesystem, and this
# module promises zero import-time I/O.
DEFAULT_DIR: pathlib.Path = pathlib.Path(__file__).parent.parent / "math_questions"

# Our topic ids. Must stay in sync with math_engine.TOPICS.
OUR_TOPICS: tuple[str, ...] = (
    "counting_and_comparing", "addition", "subtraction", "time_and_money",
    "multiplication", "fractions", "decimals", "geometry", "algebra",
    "speed_distance_time",
)

OUR_BANDS: tuple[str, ...] = ("k1", "23", "45")
OUR_LEVELS: tuple[int, ...] = (1, 2, 3, 4, 5)

# The authority for topic mapping: one CSV stem -> zero or more of our topics.
# The two "combined" stems feed BOTH constituents, because a row like
# "527 + 217 - 200 = ?" is equally good evidence for the magnitude of an
# addition operand and of a subtraction operand.
TOPIC_FANOUT: dict[str, tuple[str, ...]] = {
    "addition": ("addition",),
    "subtraction": ("subtraction",),
    "addition_and_subtraction": ("addition", "subtraction"),
    "intro_multiplication": ("multiplication",),
    "multiplication": ("multiplication",),
    "division": ("multiplication",),          # no separate division topic
    "multiplication_and_division": ("multiplication",),
    "fractions": ("fractions",),
    "decimals": ("decimals",),
    "basic_algebra": ("algebra",),
    "basic_geometry": ("geometry",),
    "speed_distance_time": ("speed_distance_time",),
    # Mapped 2026-09-19. Both ARE first-class topics in the game, and their
    # rows are the only evidence we have for what a grade-1 counting question
    # or a grade-2 money question should actually look like.
    "time_and_money": ("time_and_money",),
    "counting_and_comparing": ("counting_and_comparing",),
}

# Human-facing view of the same decision: the PRIMARY target, or None.
# `TOPIC_ALIASES` defers to `TOPIC_FANOUT` - never map from this dict.
TOPIC_ALIASES: dict[str, str | None] = {
    stem: (targets[0] if targets else None)
    for stem, targets in TOPIC_FANOUT.items()
}

GRADE_TO_BAND: dict[int, str] = {1: "k1", 2: "23", 3: "23", 4: "45", 5: "45"}

# The inverse: which school grades each of our bands actually spans. Used by
# the concept gate, never by the magnitude code.
BAND_GRADES: dict[str, tuple[int, int]] = {"k1": (1, 1), "23": (2, 3), "45": (4, 5)}

# How many grades of REVIEW a band is allowed. A concept last seen in grade 3
# is still fair game for a grade-4 child (that is revision); a concept last
# seen in grade 2 is not (that is babyish). Set to 0 to gate harder.
REVIEW_CARRY_GRADES = 1


# ------------------------------------------------------ concept classifier
#
# ORDERED. The first regex that matches wins, so the specific forms come
# before the general ones: "2/3 divided by 1/3" must land on `frac_divide`
# and not on the bare-fraction pattern, and "0.4 + 0.6" must land on
# `dec_add` and not on `add_2`.
#
# The ONLY thing that escapes this table is the concept id - one of our own
# short snake_case names. The question text it was matched against stays in
# the local that read it. See the no-text guarantee in the module docstring.

CONCEPT_PATTERNS: tuple[tuple[str, str], ...] = (
    # counting & comparing (grade 1)
    ("count_next",              r"what number comes (?:after|before)"),
    ("compare_size",            r"which is (?:bigger|smaller|greater|less)"),
    # decimals - BEFORE the bare arithmetic forms, which would swallow them
    ("dec_mul",                 r"\d\.\d.*\bx\b|\bx\b.*\d\.\d"),
    ("dec_add",                 r"\d\.\d\s*\+|\+\s*\d*\.\d"),
    ("dec_sub",                 r"\d\.\d\s*-|-\s*\d*\.\d"),
    # whole-number arithmetic
    ("add_3",                   r"^\s*\d[\d,]*\s*\+\s*\d[\d,]*\s*\+\s*\d[\d,]*\s*="),
    ("add_sub_chain",           r"^\s*\d[\d,]*\s*[+\-]\s*\d[\d,]*\s*[+\-]\s*\d[\d,]*\s*="),
    ("add_2",                   r"^\s*\d[\d,]*\s*\+\s*\d[\d,]*\s*="),
    ("sub_2",                   r"^\s*\d[\d,]*\s*-\s*\d[\d,]*\s*="),
    ("div_remainder",           r"remainder"),
    ("mul_2",                   r"^\s*\d[\d,]*\s*x\s*\d[\d,]*\s*="),
    ("div_exact",               r"^\s*\d[\d,]*\s*/\s*\d[\d,]*\s*="),
    # fractions
    ("frac_halves",             r"how many (?:halves|thirds|quarters|fourths|fifths) make"),
    ("frac_remaining",          r"what fraction of a \w+ is left"),
    ("frac_of_whole",           r"what is \d+\s*/\s*\d+ of \d"),
    ("frac_simplify",           r"^simplify the fraction"),
    ("frac_divide",             r"\d\s*/\s*\d\s*divided by"),
    ("frac_multiply",           r"\d\s*/\s*\d\s*x\s*\d\s*/\s*\d"),
    # backreference: same denominator on both sides, checked before "unlike"
    ("frac_add_same",           r"^\s*\d+\s*/\s*(\d+)\s*\+\s*\d+\s*/\s*\1\b"),
    ("frac_add_unlike",         r"^\s*\d+\s*/\s*\d+\s*\+\s*\d+\s*/\s*\d+"),
    # geometry
    ("triangle_angle_sum",      r"angles? of a triangle"),
    ("triangle_area",           r"area of a triangle"),
    ("box_volume",              r"volume of a (?:box|cube|rectangular)"),
    ("square_side_from_area",   r"square has an area of .*how long is each side"),
    ("rect_side_from_perimeter", r"rectangle has a perimeter of .*(?:width|length)"),
    ("rect_area",               r"area of a rectangle"),
    ("square_perimeter",        r"perimeter of a square|each side of a square is .*perimeter"),
    ("rect_perimeter",          r"rectangle is .*perimeter|perimeter of a rectangle"),
    ("solid_faces",             r"(?:faces|edges|corners \(vertices\)|vertices) does a "
                                r"(?:cube|box|rectangular)"),
    ("shape_id",                r"which shape"),
    ("shape_sides_total",       r"how many (?:sides|corners) do (?:\d+ |a )"),
    ("shape_sides",             r"how many (?:equal )?(?:sides|corners|edges|faces) "
                                r"does (?:an? \w+|it)"),
    # algebra
    ("two_step_linear",         r"find the missing number:\s*\d+\s*x\s*\?\s*\+|"
                                r"solve for x:\s*\d+x\s*\+"),
    ("missing_factor",          r"find the missing number:\s*\?\s*x\s*\d|"
                                r"solve for x:\s*\d+x\s*="),
    ("missing_addend",          r"find the missing number:\s*\d+\s*\+|solve for x:\s*x\s*\+"),
    # speed / distance / time
    ("sdt_speed",               r"what is (?:its|the) speed"),
    ("sdt_time",                r"how many hours"),
    ("sdt_distance",            r"how far"),
    # time & money
    ("coin_total",              r"nickels?|dimes?|quarters?|pennies|penny"),
    ("clock_add_hours",         r"o'clock"),
    ("money_add_cents",         r"cents"),
)

_CONCEPT_RE: tuple[tuple[str, "re.Pattern[str]"], ...] = tuple(
    (name, re.compile(pat, re.IGNORECASE)) for name, pat in CONCEPT_PATTERNS)

# Every concept id this module can ever emit, for cheap validation elsewhere.
CONCEPT_IDS: frozenset[str] = frozenset(name for name, _ in CONCEPT_PATTERNS)


def classify_question(text) -> str | None:
    """One CSV question -> a short concept id, or None if nothing matches.

    `text` is read and discarded. Nothing derived from it but the concept id
    - which comes from CONCEPT_PATTERNS, not from the text - ever escapes.
    """
    try:
        flat = " ".join(str(text or "").split())
    except Exception:                          # pragma: no cover - paranoia
        return None
    if not flat:
        return None
    for name, rx in _CONCEPT_RE:
        try:
            if rx.search(flat):
                return name
        except Exception:                      # pragma: no cover
            continue
    return None


def band_allows_grades(band: str, grades) -> bool:
    """Is a concept observed in `grades` appropriate for `band`?

    Two-sided, and both sides matter:
      * too advanced - the concept's EARLIEST grade is above the band's top
        ("two angles of a triangle" for a five-year-old);
      * outgrown - the concept's LATEST grade is more than
        REVIEW_CARRY_GRADES below the band's bottom ("how many sides does a
        triangle have" for a ten-year-old).
    """
    try:
        gs = sorted(int(g) for g in grades or ())
    except Exception:                          # pragma: no cover
        return False
    if not gs:
        return False
    g_lo, g_hi = BAND_GRADES.get(str(band), (2, 3))
    return gs[0] <= g_hi and gs[-1] >= g_lo - REVIEW_CARRY_GRADES

# CSV difficulty 1-3 fanned out over our five levels.
DIFFICULTY_LEVELS: dict[int, tuple[int, ...]] = {1: (1, 2), 2: (3,), 3: (4, 5)}

# Percentiles, not min/max: one "1,200 marbles" outlier in a grade-2 file must
# not drag every generated addition into four digits.
_P_LO = 0.10
_P_HI = 0.90

# Blend weight for scale_range: half ours, half the bank's. Conservative on
# purpose - the bank informs the magnitude, it does not dictate it.
_BLEND = 0.5

_GRADE_DIR_RE = re.compile(r"^grade[_\- ]?(\d+)$", re.IGNORECASE)

# Unsigned only, on purpose. "53 - 36 = ?" must yield operands 53 and 36, not
# 53 and -36: the minus is an operator, not a sign. Signed literals are
# essentially absent from a K-5 bank, and a stray negative would poison the
# 10th percentile for a whole cell.
_QUESTION_NUM_RE = re.compile(r"\d+(?:\.\d+)?")

_ANSWER_NUM = r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)"
_RE_REMAINDER = re.compile(
    rf"^({_ANSWER_NUM})\s*(?:r|rem|rmdr|remainder)\b", re.IGNORECASE)
_RE_MIXED = re.compile(rf"^({_ANSWER_NUM})\s+(\d+)\s*/\s*(\d+)\s*$")
_RE_FRACTION = re.compile(rf"^({_ANSWER_NUM})\s*/\s*({_ANSWER_NUM})\s*$")
_RE_LEADING = re.compile(rf"^({_ANSWER_NUM})(.*)$", re.DOTALL)

# Currency/percent/degree glyphs and thousands separators are noise around a
# perfectly good number.
_STRIP_CHARS = str.maketrans({c: "" for c in "$£€¥%°,"})


# ------------------------------------------------------------ number parsing

def _parse_answer(s) -> float | None:
    """Parse one CSV `answer` cell into a float, or None if it is not numeric.

    Handles every form the bank actually contains:
        "23" -> 23.0        "4.5" -> 4.5          "-3" -> -3.0
        "2/3" -> 0.666...   "1 1/2" -> 1.5        "13 remainder 2" -> 13.0
        "$3.50" -> 3.5      "1,200" -> 1200.0     "45 cm" -> 45.0
        "circle" -> None    "" -> None            None -> None

    For remainder answers we keep the QUOTIENT: it is the number whose
    magnitude tells us what a level-5 division looks like. The remainder is
    bounded by the divisor and carries no scale information.
    """
    if s is None:
        return None
    try:
        text = str(s).strip().translate(_STRIP_CHARS).strip()
    except Exception:                          # pragma: no cover - paranoia
        return None
    if not text:
        return None

    m = _RE_REMAINDER.match(text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:                     # pragma: no cover
            return None

    m = _RE_MIXED.match(text)                  # "1 1/2" -> 1.5
    if m:
        try:
            whole, num, den = float(m.group(1)), float(m.group(2)), float(m.group(3))
            if den == 0:
                return None
            frac = num / den
            # "-1 1/2" means -(1 + 1/2), not -1 + 1/2.
            return whole - frac if whole < 0 else whole + frac
        except (ValueError, ZeroDivisionError):  # pragma: no cover
            return None

    m = _RE_FRACTION.match(text)               # "2/3" -> 0.666...
    if m:
        try:
            num, den = float(m.group(1)), float(m.group(2))
            return None if den == 0 else num / den
        except (ValueError, ZeroDivisionError):  # pragma: no cover
            return None

    m = _RE_LEADING.match(text)                # "45 cm", "3.50 dollars", "23"
    if m:
        tail = m.group(2)
        # A leftover digit in the tail means this is a shape we do not
        # understand ("3 or 4", "2 and 8"); refuse rather than guess.
        if any(ch.isdigit() for ch in tail):
            return None
        try:
            return float(m.group(1))
        except ValueError:                     # pragma: no cover
            return None

    return None


def _question_numbers(text: str) -> list[float]:
    """Every unsigned numeric literal in a question, in order of appearance.

    No attempt is made to tell operands from incidentals ("in 4 hours",
    "2 rectangles") - at the population level the percentiles wash that out,
    and guessing which literal is "the" operand would be far more fragile.
    """
    if not text:
        return []
    out: list[float] = []
    for raw in _QUESTION_NUM_RE.findall(text):
        try:
            val = float(raw)
        except ValueError:                     # pragma: no cover
            continue
        out.append(val)
    return out


def _percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile. Plain and dependency-free."""
    if not values:
        raise ValueError("empty")
    ordered = sorted(values)
    n = len(ordered)
    if n == 1:
        return float(ordered[0])
    pos = (n - 1) * q
    lo_i = math.floor(pos)
    hi_i = math.ceil(pos)
    if lo_i == hi_i:
        return float(ordered[lo_i])
    frac = pos - lo_i
    return float(ordered[lo_i] + (ordered[hi_i] - ordered[lo_i]) * frac)


# ------------------------------------------------------------- accumulators

class _Cell:
    """Numbers only. Never a string, never a question."""

    __slots__ = ("operands", "answers", "grades", "rows")

    def __init__(self) -> None:
        self.operands: list[float] = []
        self.answers: list[float] = []
        self.grades: set[int] = set()
        self.rows: int = 0

    def add(self, grade: int, operands: list[float], answer: float | None) -> None:
        self.rows += 1
        self.grades.add(int(grade))
        self.operands.extend(operands)
        if answer is not None:
            self.answers.append(answer)

    def stats(self) -> dict | None:
        """Percentile summary, or None when there is nothing numeric at all."""
        ops = self.operands or self.answers      # geometry level-1 questions
        ans = self.answers or self.operands      # ("How many sides does a
        if not ops or not ans:                   #  triangle have?") carry no
            return None                          #  literals at all.
        op_lo, op_hi = _int_band(_percentile(ops, _P_LO), _percentile(ops, _P_HI))
        an_lo, an_hi = _int_band(_percentile(ans, _P_LO), _percentile(ans, _P_HI))
        typical = int(round(statistics.median(ans)))
        typical = max(an_lo, min(an_hi, typical))
        return {
            "operand_lo": op_lo,
            "operand_hi": op_hi,
            "answer_lo": an_lo,
            "answer_hi": an_hi,
            "answer_typical": typical,
            "samples": int(self.rows),
            "grades": sorted(int(g) for g in self.grades),
        }


def _int_band(lo: float, hi: float) -> tuple[int, int]:
    """Floor the low end, ceil the high end, keep 0 <= lo < hi.

    Widening outwards (rather than rounding) keeps fractional cells such as
    "1/4 of 32" from collapsing to a zero-width range.
    """
    ilo = max(0, int(math.floor(lo)))
    ihi = int(math.ceil(hi))
    if ihi < ilo + 1:
        ihi = ilo + 1
    return ilo, ihi


# --------------------------------------------------------------------- Bank

class Bank:
    """Calibration data mined from the CSVs. Constructing one does no I/O."""

    def __init__(self) -> None:
        self.available: bool = False
        self.source_dir: pathlib.Path | None = None
        self.files_read: int = 0
        self.rows_read: int = 0
        self.unmapped: dict[str, int] = {}
        # (topic, band, level) -> _Cell and (topic, band) -> _Cell. Keys are
        # our own short ids; values hold numbers only.
        self._cells: dict[tuple[str, str, int], _Cell] = {}
        self._pools: dict[tuple[str, str], _Cell] = {}
        self._calib_cache: dict[tuple[str, str, int], dict | None] = {}
        # (topic, concept) -> {grade: row count}. Concept ids are ours, not
        # the CSV's; see CONCEPT_PATTERNS.
        self._concepts: dict[tuple[str, str], dict[int, int]] = {}
        self.rows_classified: int = 0

    # -- loading ----------------------------------------------------------

    def _ingest(self, directory: pathlib.Path) -> None:
        """Read every grade_N/*.csv under `directory`. Never raises."""
        try:
            if not directory.is_dir():
                return
            grade_dirs = sorted(
                p for p in directory.iterdir()
                if p.is_dir() and _GRADE_DIR_RE.match(p.name)
            )
        except Exception:
            return

        for gdir in grade_dirs:
            m = _GRADE_DIR_RE.match(gdir.name)
            if not m:                            # pragma: no cover
                continue
            try:
                grade = int(m.group(1))
            except ValueError:                   # pragma: no cover
                continue
            if grade not in GRADE_TO_BAND:
                continue                         # grade 6+ has no band yet
            band = GRADE_TO_BAND[grade]
            try:
                files = sorted(p for p in gdir.iterdir()
                               if p.is_file() and p.suffix.lower() == ".csv")
            except Exception:                    # pragma: no cover
                continue
            for path in files:
                self._ingest_file(path, grade, band)

        self.available = self.rows_read > 0
        if self.available:
            self.source_dir = directory

    def _ingest_file(self, path: pathlib.Path, grade: int, band: str) -> None:
        stem = path.stem.strip().lower()
        targets = TOPIC_FANOUT.get(stem)
        try:
            # utf-8-sig: a BOM from a spreadsheet export would otherwise turn
            # the first header into "﻿question" and silently blank every
            # question column.
            with path.open("r", encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.DictReader(fh))
        except Exception:
            return                               # unreadable file -> skip it

        self.files_read += 1
        if targets is None:
            # Unknown stem: count it as unmapped so it shows up in /api/health
            # rather than vanishing.
            self.unmapped[stem] = self.unmapped.get(stem, 0) + len(rows)
            self.rows_read += len(rows)
            return
        if not targets:
            self.unmapped[stem] = self.unmapped.get(stem, 0) + len(rows)
            self.rows_read += len(rows)
            return

        for row in rows:
            self.rows_read += 1
            try:
                self._ingest_row(row, grade, band, targets)
            except Exception:                    # pragma: no cover
                continue

    def _ingest_row(self, row: dict, grade: int, band: str,
                    targets: tuple[str, ...]) -> None:
        # The question string lives in this local only; nothing derived from
        # it but numbers and a concept id ever leaves this frame.
        question = row.get("question") or ""
        operands = _question_numbers(question)
        answer = _parse_answer(row.get("answer"))

        # Concept gating is independent of the numeric signal: "How many
        # sides does a triangle have?" carries no operand worth mining but is
        # the single best piece of evidence for where shape_sides belongs.
        concept = classify_question(question)
        if concept:
            self.rows_classified += 1
            for topic in targets:
                per_grade = self._concepts.setdefault((topic, concept), {})
                per_grade[int(grade)] = per_grade.get(int(grade), 0) + 1

        if not operands and answer is None:
            return                               # no numeric signal at all

        try:
            difficulty = int(str(row.get("difficulty_level") or "").strip())
        except (TypeError, ValueError):
            difficulty = 0
        levels = DIFFICULTY_LEVELS.get(difficulty, ())

        for topic in targets:
            for level in levels:
                self._cells.setdefault((topic, band, level), _Cell()).add(
                    grade, operands, answer)
            # The pool gets the row exactly once per topic even though the row
            # fans out over two levels - otherwise `samples` would double-count.
            self._pools.setdefault((topic, band), _Cell()).add(
                grade, operands, answer)

    # -- queries ----------------------------------------------------------

    def calibration(self, topic: str, band: str, level: int) -> dict | None:
        """Percentile profile for one cell, or None when we have no data.

        Inheritance: an exact (topic, band, level) cell wins; otherwise we
        fall back to the (topic, band) pool across all levels, which is much
        better than nothing - the band is the part that matters most for
        magnitude. No data at all -> None.
        """
        try:
            topic = str(topic)
            band = str(band)
            level = int(level)
        except Exception:
            return None
        if not self.available:
            return None

        key = (topic, band, level)
        if key in self._calib_cache:
            cached = self._calib_cache[key]
            return dict(cached) if cached is not None else None

        result: dict | None = None
        cell = self._cells.get(key)
        if cell is not None:
            result = cell.stats()
        if result is None:
            pool = self._pools.get((topic, band))
            if pool is not None:
                result = pool.stats()

        self._calib_cache[key] = result
        return dict(result) if result is not None else None

    # -- concept queries ---------------------------------------------------

    def concept_grades(self, topic: str | None = None) -> dict:
        """What the CSVs actually contain, as {(topic, concept): [grades]}.

        With `topic` given, the keys collapse to plain concept ids. This is
        the table `math_engine.CONCEPT_GRADES` mirrors, and the harness
        asserts the two agree - so editing a CSV that moves a concept between
        grades fails the build instead of silently mis-pitching a question.
        """
        if topic is None:
            return {key: sorted(per.keys())
                    for key, per in sorted(self._concepts.items())}
        topic = str(topic)
        return {c: sorted(per.keys())
                for (t, c), per in sorted(self._concepts.items()) if t == topic}

    def concept_grades_flat(self) -> dict[str, list[int]]:
        """{concept: [grades]}, merged across topics.

        The two "combined" CSV stems fan one row out over two of our topics,
        so a subtraction row shows up under `addition` as well. Concept ids
        are globally unique in MEANING, so merging is the honest view: it is
        the concept's grade footprint in the curriculum, independent of which
        of our topic buckets the file happened to feed. This is the table
        `math_engine.CONCEPT_GRADES` mirrors.
        """
        out: dict[str, set[int]] = {}
        for (_topic, concept), per in self._concepts.items():
            out.setdefault(concept, set()).update(int(g) for g in per)
        return {c: sorted(g) for c, g in sorted(out.items())}

    def concept_bands(self, concept: str) -> list[str]:
        """Which of our bands this concept is appropriate for. [] = unknown."""
        grades = self.concept_grades_flat().get(str(concept))
        if not grades:
            return []
        return [b for b in OUR_BANDS if band_allows_grades(b, grades)]

    def concept_rows(self, topic: str, concept: str) -> int:
        return sum(self._concepts.get((str(topic), str(concept)), {}).values())

    def concepts_for_band(self, topic: str, band: str) -> list[str]:
        """Concept ids the bank says are appropriate for this (topic, band).

        Empty when the bank is unavailable or has nothing for the topic - the
        caller (math_engine) reads that as "no opinion" and keeps its own
        mirrored table, which is how the offline game gates identically.
        """
        if not self.available:
            return []
        return sorted(
            c for (t, c), per in self._concepts.items()
            if t == str(topic) and band_allows_grades(band, per.keys()))

    def summary(self) -> dict:
        """Small json-safe dict for /api/health. No prose, no question text."""
        covered = sorted(
            {f"{t}/{b}" for (t, b, _lv) in self._cells}
        )
        return {
            "available": bool(self.available),
            "source_dir": str(self.source_dir) if self.source_dir else None,
            "files_read": int(self.files_read),
            "rows_read": int(self.rows_read),
            "rows_classified": int(self.rows_classified),
            "cells": len(self._cells),
            "concepts": len({c for (_t, c) in self._concepts}),
            "topic_bands": covered,
            "unmapped": dict(sorted(self.unmapped.items())),
        }


# ------------------------------------------------------------ cached loader

_LOCK = threading.Lock()
_CACHE: Bank | None = None
_CACHE_DIR: pathlib.Path | None = None


def load(directory=None, force: bool = False) -> Bank:
    """Return the cached Bank, reading from disk on first use.

    `directory` defaults to DEFAULT_DIR. Pass `force=True` to re-read (the
    self-test uses it to swap in a non-existent directory and back). Passing
    an explicit `directory` that differs from the cached one also reloads.

    A bare `load()` returns whatever is cached, even if it was loaded from a
    different directory - otherwise the convenience wrappers below would
    silently reload DEFAULT_DIR and undo a deliberate `load("/nonexistent")`,
    which is exactly how the degrade-perfectly path gets tested.

    Never raises: a blown-up load yields an unavailable Bank.
    """
    global _CACHE, _CACHE_DIR
    try:
        target = pathlib.Path(directory) if directory is not None else DEFAULT_DIR
    except Exception:
        target = DEFAULT_DIR

    with _LOCK:
        if not force and _CACHE is not None:
            if directory is None or _CACHE_DIR == target:
                return _CACHE
        bank = Bank()
        try:
            bank._ingest(target)
        except Exception:                        # pragma: no cover - paranoia
            bank = Bank()
        _CACHE = bank
        _CACHE_DIR = target
        return bank


def calibration(topic: str, band: str, level: int) -> dict | None:
    """`Bank.calibration` on the cached singleton. Never raises."""
    try:
        return load().calibration(topic, band, level)
    except Exception:
        return None


def concept_grades(topic: str | None = None) -> dict:
    """`Bank.concept_grades` on the cached singleton. Never raises."""
    try:
        return load().concept_grades(topic)
    except Exception:
        return {}


def concept_grades_flat() -> dict[str, list[int]]:
    """`Bank.concept_grades_flat` on the cached singleton. Never raises."""
    try:
        return load().concept_grades_flat()
    except Exception:
        return {}


def concepts_for_band(topic: str, band: str) -> list[str]:
    """`Bank.concepts_for_band` on the cached singleton. Never raises.

    Empty list == "the bank has no opinion", never "nothing is allowed".
    """
    try:
        return load().concepts_for_band(topic, band)
    except Exception:
        return []


def concept_bands(concept: str) -> list[str]:
    """`Bank.concept_bands` on the cached singleton. Never raises.

    Empty list == "the bank has never seen this concept", which the caller
    must read as "no opinion" - NOT as "forbidden everywhere". That is what
    keeps the missing-folder path identical to having no bank at all.
    """
    try:
        return load().concept_bands(concept)
    except Exception:
        return []


def scale_range(lo: int, hi: int, topic: str, band: str, level: int) -> tuple[int, int]:
    """Nudge a caller's hard-coded operand range toward the observed one.

    This is the ONLY function math_engine calls. Contract:

    * No bank / no data for the cell -> `(lo, hi)` unchanged, exact identity.
      That is the path the demo runs on and it must be bit-for-bit the same
      as not having this module at all.
    * Otherwise blend 50/50 with the bank's operand percentiles, then clamp:
      the result may never exceed 3x the caller's `hi` nor fall below a third
      of it. A corrupt or wildly mis-scaled CSV can therefore shift difficulty
      a bit, but can never explode it (a 900-marble question for a 5-year-old)
      or collapse it (1 + 1 for a 10-year-old).
    * Structural guarantees (`lo >= 1`, `hi >= lo + 1`) are applied LAST, so a
      degenerate caller range can never yield an empty or inverted range. They
      are satisfied by lowering `lo`, never by raising `hi` past its cap - a
      blown cap is the dangerous direction (a 900-marble k1 question), a
      narrow range is merely boring. The single exception is a caller range
      whose `hi` is 0 or negative, where no valid range exists inside the cap
      and we return (1, 2).
    * Never raises.
    """
    try:
        base_lo = int(lo)
        base_hi = int(hi)
    except Exception:
        return lo, hi

    try:
        cal = load().calibration(topic, band, level)
        if not cal:
            return lo, hi                        # identity, on purpose

        new_lo = int(round(_BLEND * base_lo + (1.0 - _BLEND) * cal["operand_lo"]))
        new_hi = int(round(_BLEND * base_hi + (1.0 - _BLEND) * cal["operand_hi"]))

        # Guard rails relative to what the caller asked for.
        cap_hi = base_hi * 3
        floor_hi = base_hi // 3
        if floor_hi > cap_hi:                    # only when base_hi < 0
            floor_hi, cap_hi = cap_hi, floor_hi
        new_hi = max(floor_hi, min(cap_hi, new_hi))

        # Structural guarantees, satisfied from below so the cap holds.
        new_lo = max(1, new_lo)
        if new_lo + 1 > new_hi:
            if new_hi - 1 >= 1:
                new_lo = new_hi - 1
            else:
                new_lo, new_hi = 1, 2          # degenerate caller range
        return new_lo, new_hi
    except Exception:
        return lo, hi


# ------------------------------------------------------------------ selftest

def _selftest() -> int:
    """Coverage table + the four assertions from the spec. 0 = pass."""
    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)
            print(f"  FAIL  {msg}")
        else:
            print(f"  ok    {msg}")

    bank = load(force=True)
    print("=" * 78)
    print("QUESTION BANK CALIBRATION - SELF TEST")
    print("=" * 78)
    print(f"source_dir : {bank.source_dir}")
    print(f"available  : {bank.available}   files_read: {bank.files_read}   "
          f"rows_read: {bank.rows_read}")
    print()

    # --- coverage table -------------------------------------------------
    header = (f"{'topic':<20}{'band':<6}{'lv':<4}{'samples':>8}"
              f"{'operands':>14}{'answers':>16}{'typ':>7}  grades")
    print(header)
    print("-" * len(header))
    empty: list[str] = []
    for topic in OUR_TOPICS:
        for band in OUR_BANDS:
            for level in OUR_LEVELS:
                cal = bank.calibration(topic, band, level)
                if cal is None:
                    empty.append(f"{topic}/{band}/{level}")
                    continue
                ops = f"{cal['operand_lo']}-{cal['operand_hi']}"
                ans = f"{cal['answer_lo']}-{cal['answer_hi']}"
                print(f"{topic:<20}{band:<6}{level:<4}{cal['samples']:>8}"
                      f"{ops:>14}{ans:>16}{cal['answer_typical']:>7}  "
                      f"{cal['grades']}")
    print()
    print(f"empty cells ({len(empty)}): "
          + (", ".join(empty) if empty else "none"))
    print()
    print("unmapped csv topics (no home in our topic list):")
    if bank.unmapped:
        for stem, count in sorted(bank.unmapped.items()):
            print(f"  {stem:<28} {count} rows")
    else:
        print("  none")
    print()

    # --- 1. the real folder loads --------------------------------------
    print("[1] real folder loads")
    check(bank.available, "bank.available is True")
    check(bank.rows_read > 700, f"rows_read > 700 (got {bank.rows_read})")
    check(bank.files_read == 26, f"files_read == 26 (got {bank.files_read})")

    # --- 2. degrade-perfectly identity ---------------------------------
    print("[2] missing folder -> exact identity")
    missing = load(directory="/nonexistent", force=True)
    check(missing.available is False, "available is False for missing dir")
    check(missing.source_dir is None, "source_dir is None for missing dir")
    check(missing.files_read == 0 and missing.rows_read == 0, "nothing read")
    check(calibration("addition", "23", 2) is None, "calibration() -> None")
    identity_ok = True
    for args in [(2, 6, "addition", "k1", 1), (8, 40, "multiplication", "23", 3),
                 (100, 900, "decimals", "45", 5), (0, 0, "geometry", "k1", 1),
                 (-5, -1, "algebra", "45", 4)]:
        got = scale_range(*args)
        if got != (args[0], args[1]):
            identity_ok = False
            print(f"        identity broken: {args} -> {got}")
    check(identity_ok, "scale_range is exact identity with no bank")
    bank = load(force=True)                      # restore the real bank
    check(bank.available and bank.rows_read > 700, "real bank restored")

    # sanity: with the bank present, scale_range stays inside its guard rails
    rails_ok = True
    for topic in OUR_TOPICS:
        for band in OUR_BANDS:
            for level in OUR_LEVELS:
                for (blo, bhi) in [(1, 6), (8, 40), (100, 900), (2, 3)]:
                    nlo, nhi = scale_range(blo, bhi, topic, band, level)
                    if not (isinstance(nlo, int) and isinstance(nhi, int)):
                        rails_ok = False
                    if nlo < 1 or nhi < nlo + 1 or nhi > bhi * 3 or nhi < bhi // 3:
                        rails_ok = False
                        print(f"        rail broken: {topic}/{band}/{level} "
                              f"({blo},{bhi}) -> ({nlo},{nhi})")
    check(rails_ok, "scale_range respects its clamps for every cell")

    # --- 3. no question text retained ----------------------------------
    print("[3] no question text on the Bank")
    long_strings: list[str] = []

    def walk(obj, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(obj, pathlib.PurePath):
            return                               # a path is not question text
        if isinstance(obj, str):
            if len(obj) > 40:
                long_strings.append(obj[:60])
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(k, depth + 1)
                walk(v, depth + 1)
            return
        if isinstance(obj, (list, tuple, set, frozenset)):
            for v in obj:
                walk(v, depth + 1)
            return
        slots = getattr(type(obj), "__slots__", None)
        if slots:
            for name in slots:
                walk(getattr(obj, name, None), depth + 1)

    for name in dir(bank):
        if name.startswith("__"):
            continue
        value = getattr(bank, name, None)
        if callable(value):
            continue
        walk(value)
    # summary() legitimately reports the folder path - that is a path, not
    # question text, so it is the one string allowed to be long.
    summary = bank.summary()
    summary.pop("source_dir", None)
    walk(summary)
    walk(bank.calibration("addition", "23", 3))
    check(not long_strings,
          f"no string > 40 chars anywhere on the Bank (found {len(long_strings)})")
    if long_strings:
        for s in long_strings[:5]:
            print(f"        {s!r}")

    # --- 3b. concept gating ---------------------------------------------
    print("[3b] concept classification + band gate")
    flat = bank.concept_grades_flat()
    header2 = f"{'concept':<28}{'grades':<16}bands"
    print(header2)
    print("-" * len(header2))
    for concept, grades in flat.items():
        print(f"  {concept:<26}{str(grades):<16}{bank.concept_bands(concept)}")
    pct = 100.0 * bank.rows_classified / max(1, bank.rows_read)
    print(f"\n  classified {bank.rows_classified}/{bank.rows_read} rows ({pct:.1f}%)")
    check(pct >= 99.0, f"concept coverage fell to {pct:.1f}% (want >= 99%)")
    check(len(flat) >= 30, f"only {len(flat)} concepts mined from the bank")
    check(set(flat) <= CONCEPT_IDS, "a concept id escaped that is not in CONCEPT_PATTERNS")
    # The two headline gates the playtest asked for, asserted directly.
    check("45" not in bank.concept_bands("shape_sides"),
          "ages 9-10 can still be asked how many sides a shape has")
    check("45" not in bank.concept_bands("shape_id"),
          "ages 9-10 can still be asked to identify a shape")
    check("k1" not in bank.concept_bands("triangle_angle_sum"),
          "ages 4-6 can be asked for a triangle's third angle")
    check("k1" not in bank.concept_bands("frac_divide"),
          "ages 4-6 can be asked to divide fractions")
    check(bank.concept_bands("triangle_angle_sum") == ["45"],
          "triangle angle sum is not exclusively an ages 9-10 concept")
    # Gating must be inert with no bank - that is the offline path.
    load(directory="/nonexistent-question-bank", force=True)
    check(concept_bands("shape_sides") == [],
          "a missing bank still has an opinion about concepts")
    check(concepts_for_band("geometry", "45") == [],
          "a missing bank still gates concepts")
    bank = load(force=True)

    # --- 4. answer parsing ---------------------------------------------
    print("[4] _parse_answer")
    cases: list[tuple[object, float | None]] = [
        ("23", 23.0), ("4.5", 4.5), ("-3", -3.0), ("+7", 7.0),
        ("2/3", 2.0 / 3.0), ("1/2", 0.5),
        ("1 1/2", 1.5), ("2 2/3", 2.0 + 2.0 / 3.0),
        ("13 remainder 2", 13.0), ("9 remainder 1", 9.0), ("74 remainder 3", 74.0),
        ("$3.50", 3.5), ("1,200", 1200.0), ("45 cm", 45.0),
        ("30 degrees", 30.0), ("  12  ", 12.0), ("1.0", 1.0),
        ("circle", None), ("square", None), ("", None), ("   ", None),
        (None, None), ("3 or 4", None), ("1/0", None), ("n/a", None),
    ]
    parse_ok = True
    for raw, want in cases:
        got = _parse_answer(raw)
        same = (got is None and want is None) or (
            got is not None and want is not None and abs(got - want) < 1e-9)
        if not same:
            parse_ok = False
            print(f"        {raw!r}: expected {want!r}, got {got!r}")
    check(parse_ok, f"all {len(cases)} answer forms parse correctly")

    print()
    if failures:
        print(f"SELF TEST FAILED ({len(failures)} problem(s))")
        return 1
    print("SELF TEST PASSED")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
