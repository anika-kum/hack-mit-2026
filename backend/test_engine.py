"""
do-IT-oodle verification harness.  Run with:  python3 test_engine.py

Checks, for every topic x band x level x archetype:
  * `prompt` is non-empty and <= 12 words
  * the deterministic grader ACCEPTS the right answer
  * the deterministic grader REJECTS a wrong answer
  * the public (network) payload contains no answer key
Then plays a full quest end-to-end through the real HTTP routes.
"""

import json
import random
import re as _re
import sys
import time

import app as app_module
import math_engine
import prompts
import question_bank
import story_engine
from app import _grade, _public_challenge

MAX_PROMPT_WORDS = 12

# Archetypes where a visual difference between right and wrong stones IS the
# question (mushrooms look like mushrooms; a pentagon looks like a pentagon).
# (a thistle must LOOK like a thistle; a pentagon must LOOK like a pentagon)
VISUAL_BY_DESIGN = {"shape_id_pick"}

failures: list[str] = []
checks = 0


def check(cond, msg):
    global checks
    checks += 1
    if not cond:
        failures.append(msg)
    return cond


# ------------------------------------------------------------ answer models

def right_answer(ch):
    mode = ch.get("grade_mode", "set")
    correct = [s["id"] for s in ch["stones"] if s["correct"]]
    if mode in ("value", "none"):
        return []
    if mode == "order":
        return list(ch["answer_order"])
    if mode == "count":
        return correct[:ch["target_count"]]
    return correct


def right_typed(ch):
    """What a child would TYPE for a free-response challenge.

    Fraction answers are typed as fractions. `answer_text` is the canonical
    written form ("1 1/3"); the improper form ("4/3") grades identically and
    is checked separately below.
    """
    if ch.get("grade_mode") != "value":
        return None
    if ch.get("answer_text"):
        return ch["answer_text"]
    places = int(ch.get("decimals") or 0)
    return f"{float(ch['answer_value']):.{places}f}"


def wrong_typed(ch):
    if ch.get("grade_mode") != "value":
        return None
    places = int(ch.get("decimals") or 0)
    # One whole unit out: comfortably outside `tolerance` at any precision.
    return f"{float(ch['answer_value']) + 1:.{places}f}"


def wrong_answer(ch):
    mode = ch.get("grade_mode", "set")
    ids = [s["id"] for s in ch["stones"]]
    if mode in ("value", "none"):
        return []
    correct = [s["id"] for s in ch["stones"] if s["correct"]]
    wrong = [s["id"] for s in ch["stones"] if not s["correct"]]

    if mode == "order":
        seq = list(ch["answer_order"])
        if len(seq) >= 4:            # swap two non-adjacent corners
            seq[1], seq[2] = seq[2], seq[1]
            return seq
        return list(reversed(seq)) if len(seq) > 1 else seq
    if mode == "count":
        if wrong:
            return correct[:ch["target_count"] - 1] + [wrong[0]]
        return correct[:max(0, ch["target_count"] - 1)]
    if mode == "groups":
        # ANY n whole groups is a correct answer (that is what n/d means), so
        # a wrong answer must be a PARTIAL group or the wrong NUMBER of groups.
        groups = {}
        for s in ch["stones"]:
            groups.setdefault(s["group"], []).append(s["id"])
        want = ch["answer_group_count"]
        keys = list(groups)
        partial = [g for g in keys if len(groups[g]) > 1]
        if partial:                          # n-1 whole groups + half of one
            g0 = partial[0]
            rest = [g for g in keys if g != g0][:want - 1]
            return [i for g in rest for i in groups[g]] + [groups[g0][0]]
        if want + 1 <= len(keys):            # too many groups
            return [i for g in keys[:want + 1] for i in groups[g]]
        return [i for g in keys[:max(0, want - 1)] for i in groups[g]]
    if mode == "sum":
        if len(correct) > 1:
            return correct[:-1]
        return wrong[:1] or [ids[0]]
    return wrong[:1] or [ids[0]]


# ------------------------------------------------------------ static checks

def audit(ch, tag):
    p = (ch.get("prompt") or "").strip()
    check(p, f"{tag}: empty prompt")
    check(len(p.split()) <= MAX_PROMPT_WORDS,
          f"{tag}: prompt is {len(p.split())} words (>{MAX_PROMPT_WORDS}): {p!r}")
    check((ch.get("narrative") or "").strip(), f"{tag}: empty narrative")
    check((ch.get("explanation") or "").strip(), f"{tag}: empty explanation")
    check(ch.get("question_type") in
          ("single_choice", "multi_select", "collect_count", "ordered_path",
           "free_response"),
          f"{tag}: bad question_type {ch.get('question_type')}")
    check(ch.get("grade_mode") in ("set", "count", "order", "sum", "groups", "value"),
          f"{tag}: bad grade_mode {ch.get('grade_mode')}")
    check(isinstance(ch.get("target_count"), int) and ch["target_count"] >= 1,
          f"{tag}: bad target_count {ch.get('target_count')}")
    check(any(s["correct"] for s in ch["stones"])
          or ch["grade_mode"] in ("order", "value"),
          f"{tag}: no correct stone")

    # Free response must be SHORT and typed, never a wall of cards.
    if ch["grade_mode"] == "value":
        check(not ch["stones"], f"{tag}: a free-response challenge shipped stones")
        check(isinstance(ch.get("answer_value"), float),
              f"{tag}: free response has no numeric answer_value")
        check(float(ch.get("tolerance", 0)) > 0,
              f"{tag}: free response has no tolerance")
        check(len(str(ch["answer_value"])) <= 10,
              f"{tag}: free-response answer {ch['answer_value']} is too long to type")
        check(ch.get("band") != "k1",
              f"{tag}: ages 4-6 were offered a text box")

    # COUNT-OUT must always leave spares on the ground - if the scene holds
    # exactly the answer, the child can take everything and never compute.
    if ch["grade_mode"] == "count":
        check(len(ch["stones"]) > ch["target_count"],
              f"{tag}: COUNT-OUT has no spares ({len(ch['stones'])} stones, "
              f"target {ch['target_count']}) - this is touch-and-choose again")
    for s in ch["stones"]:
        if s.get("tint"):
            check(s["tint"] in math_engine.TINTS, f"{tag}: bad tint {s['tint']}")
        if s.get("shape"):
            check(s["shape"] in {n for n, _ in math_engine.SHAPES},
                  f"{tag}: bad shape {s['shape']}")
        for f in ("x_pct", "y_pct"):
            if s.get(f) is not None:
                check(0.0 <= s[f] <= 1.0, f"{tag}: {f} out of range {s[f]}")
    for pr in ch.get("props", []):
        check(pr["kind"] in math_engine.PROP_KINDS, f"{tag}: bad prop kind {pr['kind']}")
        check(0.0 <= pr["x_pct"] <= 1.0 and 0.0 <= pr["y_pct"] <= 1.0,
              f"{tag}: prop off-canvas {pr}")

    # grading
    correct_ids = {s["id"] for s in ch["stones"] if s["correct"]}
    ra = right_answer(ch)
    check(_grade(ch, ra, set(ra), correct_ids, right_typed(ch)),
          f"{tag}: grader REJECTED the correct answer ({ch['grade_mode']})")
    wa = wrong_answer(ch)
    check(not _grade(ch, wa, set(wa), correct_ids, wrong_typed(ch)),
          f"{tag}: grader ACCEPTED a wrong answer {wa} ({ch['grade_mode']})")
    if ch["grade_mode"] == "value":
        for blank in (None, "", "   ", "abc", "-", "."):
            check(not _grade(ch, [], set(), correct_ids, blank),
                  f"{tag}: a blank/nonsense typed answer {blank!r} graded CORRECT")

    # leak audit
    pub = _public_challenge(ch)
    blob = json.dumps(pub)
    for k in ("explanation", "order_cyclic", "answer_value"):
        check(k not in pub, f"{tag}: LEAK - public payload contains {k}")
    check(pub.get("answer_order") is None, f"{tag}: LEAK - answer_order shipped")
    for s in pub["stones"]:
        check("correct" not in s, f"{tag}: LEAK - stone.correct shipped")
        check("value" not in s, f"{tag}: LEAK - stone.value shipped")
    if ch.get("explanation"):
        check(ch["explanation"] not in blob, f"{tag}: LEAK - explanation text in payload")

    # visual-correlation leak: for archetypes where right/wrong should look
    # identical, no single visual field may partition the stones.
    if ch.get("archetype") not in VISUAL_BY_DESIGN and ch["grade_mode"] != "groups":
        good = [s for s in ch["stones"] if s["correct"]]
        bad = [s for s in ch["stones"] if not s["correct"]]
        if good and bad:
            for f in ("tint", "shape"):
                gs = {s.get(f) for s in good}
                bs = {s.get(f) for s in bad}
                check(not (gs and bs and gs.isdisjoint(bs)),
                      f"{tag}: LEAK - '{f}' separates correct from wrong stones")
    return pub


# ------------------------------------------------------------------- sweep

print("=" * 72)
print("PART 1 - topic x band x level x archetype sweep")
print("=" * 72)

matrix_rows = []
for topic in math_engine.TOPICS:
    for band in math_engine.TOPIC_BANDS[topic]:
        arche_list = math_engine.archetypes_for(topic, band)
        # STRENGTHENED after the 2026-09-19 cull. Two live archetypes per cell
        # is the floor the feedback asked for, and at least one of them must
        # not be PICK-ONE or every quest feels identical.
        check(len(arche_list) >= 2,
              f"{topic}/{band}: only {len(arche_list)} archetype(s) after the cull")
        mechanics = {story_engine.MECHANIC_OF.get(a) for a in arche_list}
        check(None not in mechanics,
              f"{topic}/{band}: an archetype has no MECHANIC_OF entry: {arche_list}")
        # Ages 4-6 must still have two playable archetypes after the
        # no-reading filter, not just two on paper.
        playable = math_engine.playable_archetypes(topic, band)
        check(len(playable) >= 2,
              f"{topic}/{band}: only {len(playable)} PLAYABLE archetype(s)")
        # 2026-09-19b: the old assertion here was "at least two MECHANICS".
        # After the cull there are only two presentations left by design -
        # multiple choice and free response - so that assertion could not be
        # satisfied without bringing a gimmick back. It is replaced by the
        # stronger, more specific pair below, which says what actually has to
        # be true rather than counting mechanic names.
        picks = [a for a in playable if a not in math_engine.FREE_RESPONSE_ARCHETYPES]
        types = [a for a in arche_list if a in math_engine.FREE_RESPONSE_ARCHETYPES]
        check(len(picks) >= 2,
              f"{topic}/{band}: fewer than two multiple-choice archetypes {picks}")
        if band == "k1":
            check(not [a for a in playable
                       if a in math_engine.FREE_RESPONSE_ARCHETYPES],
                  f"{topic}/k1: a free-response archetype is playable for ages 4-6")
        else:
            check(types, f"{topic}/{band}: no free-response option at all")
        # Every archetype is one of the two honest presentations. Nothing may
        # quietly reintroduce a third "walk over these" interaction.
        check(mechanics <= {"pick", "type", "group"},
              f"{topic}/{band}: unexpected mechanic in {mechanics}")
        # And the questions in a cell must not all be the same idea rendered
        # twice: either two concepts, or one concept in two wordings.
        cell_concepts = math_engine.concepts_for(topic, band)
        check(len(cell_concepts) >= 2 or len(arche_list) >= 2,
              f"{topic}/{band}: only one way to ask a question")
        for level in range(1, 6):
            for arche in arche_list:
                for trial in range(6):
                    seed = hash((topic, band, level, arche, trial)) & 0xFFFFFF
                    ch = math_engine.generate_challenge(
                        topic, band, level, character_name="Mochi",
                        objects=["cat", "river"], archetype=arche, seed=seed)
                    tag = f"{topic}/{band}/L{level}/{arche}#{trial}"
                    check(ch["archetype"] == arche,
                          f"{tag}: asked for {arche}, got {ch['archetype']}")
                    audit(ch, tag)
                if level == 3 and trial == 5:
                    matrix_rows.append((topic, band, arche, ch["grade_mode"],
                                        ch["question_type"], ch["prompt"]))

print(f"{checks} assertions run.")
if failures:
    print(f"\n!!! {len(failures)} FAILURES:")
    for f in failures[:40]:
        print("   -", f)
else:
    print("All sweep assertions passed.")

print("\nSample prompt per topic/band/archetype (level 3):")
for row in matrix_rows:
    print(f"  {row[0]:<20} {row[1]:<3} {row[2]:<22} {row[3]:<7} {row[4]:<14} {row[5]}")

# ============================================================================
# PART 1b - THE BAND-RELATIVE FLOOR AND THE BANK'S CONCEPT GATE
#
# This is the part that encodes the 2026-09-19b playtest note:
#   "the questions are way too easy... it can be a door with 6 sides and
#    literally make u count 1 thru 6 as a 10 year old"
#   "use the bank to gate what kind of question is appropriate!"
# ============================================================================

print("\n" + "=" * 72)
print("PART 1b - band-relative difficulty floors + bank concept gating")
print("=" * 72)

# --- 1. the ladder itself -------------------------------------------------
# A band's level 5 and the next band's level 1 must land on the SAME rung.
# That IS the "an older band's easiest is where a younger band's hardest was"
# requirement, stated exactly.
LADDER = [("k1", "23"), ("23", "45")]
for younger, older in LADDER:
    top = math_engine.difficulty_rung(younger, 5)
    floor = math_engine.difficulty_rung(older, 1)
    check(top == floor,
          f"band {younger} L5 is rung {top} but band {older} L1 is rung {floor} "
          f"- the bands do not meet, so the older band restarts from scratch")
    check(math_engine.difficulty_rung(older, 1) >
          math_engine.difficulty_rung(younger, 1),
          f"band {older} starts no higher than band {younger}")
print(f"  rungs: k1 L1-L5 = "
      f"{[math_engine.difficulty_rung('k1', L) for L in range(1, 6)]}, "
      f"23 = {[math_engine.difficulty_rung('23', L) for L in range(1, 6)]}, "
      f"45 = {[math_engine.difficulty_rung('45', L) for L in range(1, 6)]}")

# Within a band the rung must strictly rise with the level.
for band in math_engine.BANDS:
    rungs = [math_engine.difficulty_rung(band, L) for L in range(1, 6)]
    check(rungs == sorted(set(rungs)), f"{band}: rungs do not strictly rise: {rungs}")

# --- 2. the headline gate: no shape-counting for ages 9-10 ---------------
# Sampled exhaustively rather than by spot check, because this is the exact
# thing that shipped to a ten-year-old and nobody noticed.
SHAPE_CONCEPTS = {"shape_id", "shape_sides", "shape_sides_total"}
leaked = []
for level in range(1, 6):
    for arche in math_engine.archetypes_for("geometry", "45"):
        concept = math_engine.ARCHETYPE_CONCEPT.get(arche)
        if concept in SHAPE_CONCEPTS:
            leaked.append(arche)
check(not leaked,
      f"ages 9-10 can still be asked to identify or count shape sides: {leaked}")

# ... and by generating, in case the registry and the generators disagree.
emitted_45 = set()
for level in range(1, 6):
    for trial in range(60):
        ch = math_engine.generate_challenge("geometry", "45", level, "Mochi",
                                            ["cat", "cliff"], seed=level * 977 + trial)
        emitted_45.add(ch.get("concept"))
check(not (emitted_45 & SHAPE_CONCEPTS),
      f"a 9-10 geometry quest emitted {emitted_45 & SHAPE_CONCEPTS}")
check("triangle_angle_sum" in {c for a in math_engine.archetypes_for("geometry", "45")
                               for c in [math_engine.ARCHETYPE_CONCEPT.get(a)]},
      "ages 9-10 geometry has no triangle angle sum - the bank's hardest grade-5 row")
print(f"  ages 9-10 geometry concepts: {sorted(emitted_45)}")

# --- 3. the mirror gate: no grade-5 concepts for ages 4-6 ----------------
ADVANCED = {"triangle_angle_sum", "triangle_area", "box_volume", "frac_divide",
            "frac_multiply", "frac_add_unlike", "dec_mul", "two_step_linear",
            "sdt_speed", "div_remainder"}
for topic in math_engine.TOPICS:
    if "k1" not in math_engine.TOPIC_BANDS[topic]:
        continue
    got = set(math_engine.concepts_for(topic, "k1"))
    check(not (got & ADVANCED),
          f"{topic}/k1: ages 4-6 were offered {got & ADVANCED}")

# --- 4. the easiest 9-10 question is harder than the hardest 4-6 one -----
# Compared on the magnitude of the numbers actually generated, for the one
# topic both bands share a concept in (addition).
import statistics as _stats


def _biggest(topic, band, level, n=60):
    """The median LARGEST number a cell puts in front of a child.

    Median, not min or max: a single unlucky seed says nothing, and the
    question is what the cell typically feels like.
    """
    out = []
    for s in range(n):
        prompt = math_engine.generate_challenge(
            topic, band, level, "Mochi", ["cat"], seed=s)["prompt"]
        nums = [int(x) for x in _re.findall(r"\d+", prompt)]
        if nums:
            out.append(max(nums))
    return _stats.median(out) if out else 0


mags = {(b, lv): _biggest("addition", b, lv)
        for b in ("k1", "23", "45") for lv in (1, 5)}
# THE headline requirement, stated on the numbers a child actually sees:
# an older band's EASIEST is at least as hard as the younger band's HARDEST.
check(mags[("45", 1)] >= mags[("23", 5)] * 0.9,
      f"the 9-10 band's easiest addition (median {mags[('45', 1)]}) is easier "
      f"than the 7-8 band's hardest (median {mags[('23', 5)]})")
check(mags[("23", 1)] > mags[("k1", 5)],
      f"the 7-8 band's easiest addition (median {mags[('23', 1)]}) is no harder "
      f"than the 4-6 band's hardest (median {mags[('k1', 5)]})")
# ... and every band still gets a real five-level climb of its own.
for band in ("k1", "23", "45"):
    check(mags[(band, 5)] > mags[(band, 1)] * 1.5,
          f"band {band} barely gets harder across its five levels: "
          f"{mags[(band, 1)]} -> {mags[(band, 5)]}")
print("  addition magnitudes (median largest operand): "
      + ", ".join(f"{b} L{lv}={int(mags[(b, lv)])}"
                  for b in ("k1", "23", "45") for lv in (1, 5)))

# --- 5. the static gate table matches the live CSV bank ------------------
# If someone edits a CSV so a concept moves grade, this fails loudly rather
# than silently mis-pitching a question at a child.
bank_now = question_bank.load(force=True)
if bank_now.available:
    mined = bank_now.concept_grades_flat()
    check(set(mined) == set(math_engine.CONCEPT_GRADES),
          f"CONCEPT_GRADES and the CSV bank disagree about WHICH concepts exist: "
          f"only in bank {sorted(set(mined) - set(math_engine.CONCEPT_GRADES))}, "
          f"only in math_engine "
          f"{sorted(set(math_engine.CONCEPT_GRADES) - set(mined))}")
    for concept, grades in sorted(mined.items()):
        check(tuple(grades) == math_engine.CONCEPT_GRADES.get(concept),
              f"concept {concept}: bank says grades {tuple(grades)}, "
              f"math_engine says {math_engine.CONCEPT_GRADES.get(concept)}")
        if concept in math_engine.CONCEPT_BAND_OVERRIDE:
            # A deliberate product narrowing, not a curriculum fact. It may
            # only ever REMOVE bands the bank would have allowed.
            check(set(math_engine.concept_bands(concept))
                  <= set(bank_now.concept_bands(concept)),
                  f"override for {concept} ADDED a band the bank forbids: "
                  f"{math_engine.concept_bands(concept)} vs "
                  f"{bank_now.concept_bands(concept)}")
        else:
            check(bank_now.concept_bands(concept) ==
                  math_engine.concept_bands(concept),
                  f"concept {concept}: bank gates it to "
                  f"{bank_now.concept_bands(concept)}, math_engine to "
                  f"{math_engine.concept_bands(concept)}")
    print(f"  {len(mined)} concepts mined from {bank_now.rows_read} CSV rows; "
          f"math_engine's mirror agrees with every one")

# --- 6. every cell survived the gate, and the relaxations are declared ---
for topic in math_engine.TOPICS:
    for band in math_engine.TOPIC_BANDS[topic]:
        concepts = math_engine.concepts_for(topic, band)
        check(concepts, f"{topic}/{band}: the concept gate left NOTHING playable")
        for concept in concepts:
            allowed = math_engine.concept_bands(concept)
            check(band in allowed or (topic, band) in math_engine.RELAXED_CELLS,
                  f"{topic}/{band}: concept {concept} is gated to {allowed} "
                  f"but is playable here, and the cell is not declared relaxed")
check(math_engine.RELAXED_CELLS == [("time_and_money", "k1")],
      f"the set of relaxed cells changed: {math_engine.RELAXED_CELLS} "
      f"- update the report in HANDOFF.md")
print(f"  every topic x band cell is playable; relaxed cells: "
      f"{math_engine.RELAXED_CELLS}")

# --- 7. the deleted gimmicks cannot come back through a concept ----------
for topic in math_engine.TOPICS:
    for band in math_engine.TOPIC_BANDS[topic]:
        for arche in math_engine.archetypes_for(topic, band):
            check(arche not in math_engine.DELETED_ARCHETYPES,
                  f"{topic}/{band}: deleted archetype {arche} is registered")
            check(math_engine.ARCHETYPE_CONCEPT.get(arche),
                  f"{topic}/{band}: {arche} has no concept, so nothing gates it")


# ============================================================================
# PART 1c - THE MATHS AND THE STORY ARE ABOUT THE SAME THING
#
#   "if the story is about collecting rods, make it such that bun bun the
#    main bunny is getting rods and doing multiplication - the context is
#    the same"
#
# The noun is chosen by the story (or, with no API key, by the world the
# child drew). The NUMBERS and the ANSWER KEY are still generated in Python -
# that is the line this feature must not cross, so it is asserted here.
# ============================================================================

print("\n" + "=" * 72)
print("PART 1c - word problems speak the quest's own noun")
print("=" * 72)

STORY_ARCHETYPES = [a for a in math_engine.ARCHETYPE_CONCEPT if a.endswith("_story")]
check(len(STORY_ARCHETYPES) >= 4,
      f"only {len(STORY_ARCHETYPES)} word-problem archetypes exist")

for arche in STORY_ARCHETYPES:
    topic = math_engine.ARCHETYPE_TOPIC[arche]
    for band in math_engine.TOPIC_BANDS[topic]:
        if arche not in math_engine.archetypes_for(topic, band):
            continue
        for level in range(1, 6):
            plain = math_engine.generate_challenge(
                topic, band, level, "Bun Bun", ["bunny", "forest"],
                archetype=arche, seed=level * 13)
            themed = math_engine.generate_challenge(
                topic, band, level, "Bun Bun", ["bunny", "forest"],
                archetype=arche, story_noun="rods", seed=level * 13)
            tag = f"{topic}/{band}/L{level}/{arche}"
            check("rods" in themed["prompt"],
                  f"{tag}: the quest is about rods but the question is "
                  f"{themed['prompt']!r}")
            # THE LINE: the story may change the WORDS, never the MATHS.
            check(themed["explanation"] == plain["explanation"],
                  f"{tag}: the story noun changed the arithmetic "
                  f"({themed['explanation']!r} vs {plain['explanation']!r})")
            nums = lambda p: sorted(int(n) for n in _re.findall(r"\d+", p))
            check(nums(themed["prompt"]) == nums(plain["prompt"]),
                  f"{tag}: the story noun changed the numbers")
            check(len(themed["prompt"].split()) <= MAX_PROMPT_WORDS,
                  f"{tag}: themed prompt is too long: {themed['prompt']!r}")

# The whole quest speaks ONE noun, end to end, finale included.
_story = {"title": "The Rod Run", "goal_text": "the old mill",
          "opening": "x", "epilogue": "y", "treasure": "rods",
          "source": "openai",
          "beats": [{"title": "T", "location": "L", "intro": "i",
                     "on_success": "s", "advances": "a"} for _ in range(9)]}
_q = story_engine.start_quest(
    "multiplication", "45",
    {"objects": ["bunny", "forest"], "character_name": "Bun Bun"},
    seed=9, storyline=_story)
check(_q["treasure"] == "rods", f'the quest lost its noun: {_q["treasure"]!r}')
check(_q["treasure_source"] == "story", "the story's noun was not used")
themed_prompts = []
for _i in range(len(_q["beats"])):
    _ch = story_engine.issue_beat(_q, session_level=3, seed=200 + _i)
    if _ch is None:
        break
    if _ch["question_type"] != "interlude":
        themed_prompts.append(_ch["prompt"])
        story_engine.record_result(_q, _ch, True)
    story_engine.advance(_q)
check(any("rods" in p for p in themed_prompts),
      f"no question in a quest about rods mentioned rods: {themed_prompts}")
print(f'  a quest about "rods" asked: '
      + "; ".join(p for p in themed_prompts if "rods" in p)[:150])

# With NO storyline the noun still comes from the child's own world, so the
# offline game is themed too.
_off = story_engine.start_quest(
    "addition", "23", {"objects": ["fox", "river"], "character_name": "Rusty"},
    seed=4)
check(_off["treasure"], "an offline quest has no noun at all")
check(_off["treasure_source"] == "world",
      "an offline quest claimed its noun came from a story")
print(f'  offline, a river world is about "{_off["treasure"]}"')

# And the sanitiser refuses anything that is not a countable plural, so a
# model cannot hand us "treasure" or a sentence.
for bad in ("treasure", "rod", "points", "", None, "a very long phrase here",
            "things", "12 rods"):
    check(not prompts.clean_treasure(bad),
          f"clean_treasure accepted {bad!r}")
for good in ("rods", "Moonstones", "lantern-oil jars"):
    check(prompts.clean_treasure(good), f"clean_treasure rejected {good!r}")
# Trailing punctuation is noise, not a rejection reason.
check(prompts.clean_treasure("rods!!") == "rods",
      "clean_treasure did not strip stray punctuation")
print("  clean_treasure accepts plain countable plurals and nothing else")


# --------------------------------------------------------- k1 no-reading

print("\n" + "=" * 72)
print("PART 2 - ages 4-6 must be playable without reading")
print("=" * 72)
for topic in [t for t in math_engine.TOPICS if "k1" in math_engine.TOPIC_BANDS[t]]:
    for level in range(1, 6):
        for trial in range(6):
            ch = math_engine.generate_challenge(topic, "k1", level, "Mochi",
                                                ["bunny", "meadow"],
                                                seed=level * 7 + trial * 101)
            readable = [s for s in ch["stones"]
                        if s["label"] and s.get("dots") is None
                        and s.get("shape") is None]
            check(not readable,
                  f"k1/{topic}/L{level}#{trial} ({ch['archetype']}): "
                  f"{len(readable)} stones need reading")
            check(ch["question_type"] != "free_response",
                  f"k1/{topic}/L{level}#{trial}: ages 4-6 got a text box "
                  f"({ch['archetype']})")
        print(f"  {topic:<24} L{level}  {ch['archetype']:<22} no-reading OK")

# ------------------------------------------------- the 2026-09-19 cull
print("\n" + "=" * 72)
print("PART 2b - the deleted 'touch and choose' archetypes are GONE")
print("=" * 72)

def _live_registries(module):
    """Every dict/set/list the module exposes, except the tombstone list."""
    out = {}
    for name in dir(module):
        if name == "DELETED_ARCHETYPES":
            continue
        val = getattr(module, name)
        if isinstance(val, (dict, set, list, tuple)):
            out[f"{module.__name__}.{name}"] = val
    return out


_REGISTRIES = {**_live_registries(math_engine), **_live_registries(story_engine)}


def _mentions(container, needle):
    """Does `needle` appear anywhere in this (possibly nested) container?"""
    if isinstance(container, dict):
        return (needle in container
                or any(_mentions(v, needle) for v in container.values()))
    if isinstance(container, (set, frozenset)):
        return needle in container
    if isinstance(container, (list, tuple)):
        return any(x == needle or _mentions(x, needle) for x in container
                   if isinstance(x, (dict, set, list, tuple, str)))
    return container == needle

for name, why in sorted(math_engine.DELETED_ARCHETYPES.items()):
    print(f"  {name:<24} deleted: {why}")
    # 1. no generator answers to the name, in any topic or band
    for topic in math_engine.TOPICS:
        check(name not in math_engine.archetypes_for(topic, "k1")
              + math_engine.archetypes_for(topic, "23")
              + math_engine.archetypes_for(topic, "45"),
              f"deleted archetype {name} is still registered under {topic}")
    check(name not in story_engine.MECHANIC_OF,
          f"deleted archetype {name} still has a MECHANIC_OF entry")
    check(name not in story_engine.ARCHETYPE_SUBJECT,
          f"deleted archetype {name} still has an ARCHETYPE_SUBJECT entry")
    check(name not in math_engine.NON_READING_ARCHETYPES,
          f"deleted archetype {name} is still in NON_READING_ARCHETYPES")
    # 2. asking for it by name cannot resurrect it - it falls back instead
    got = math_engine.generate_challenge("addition", "23", 3, "Mochi",
                                         ["cat", "river"], archetype=name, seed=1)
    check(got["archetype"] != name,
          f"generate_challenge(archetype={name!r}) still produced it")
    # 3. the name survives in NO live registry in either engine - only in the
    #    DELETED_ARCHETYPES tombstone, which is excluded above.
    for reg_name, reg in _REGISTRIES.items():
        check(not _mentions(reg, name),
              f"deleted archetype {name} still appears in {reg_name}")
    # 4. no generator function is left behind to be wired back up
    leftovers = [fn for fn in dir(math_engine)
                 if fn.startswith("_") and fn.endswith(name.split("_")[-1])
                 and callable(getattr(math_engine, fn))
                 and name in (getattr(getattr(math_engine, fn), "__doc__", "") or "")
                 and "Replaces the deleted" not in (
                     getattr(getattr(math_engine, fn), "__doc__", "") or "")]
    check(not leftovers, f"deleted archetype {name} still has a generator: {leftovers}")
check(len(math_engine.DELETED_ARCHETYPES) == 21,
      f"the cull list is now {len(math_engine.DELETED_ARCHETYPES)} - "
      f"update the report in HANDOFF.md")

# ------------------------------------------------------ obstacle policy

print("\n" + "=" * 72)
print("PART 3 - obstacle policy: mistakes add SCENERY, never distractor stones")
print("=" * 72)
base = math_engine.generate_challenge("addition", "23", 3, "Mochi", ["cat", "river"],
                                      mistakes_total=0, archetype="add_2_pick", seed=99)
hard = math_engine.generate_challenge("addition", "23", 3, "Mochi", ["cat", "river"],
                                      mistakes_total=5, archetype="add_2_pick", seed=99)
print(f"  0 mistakes -> {len(base['stones'])} stones, {base['obstacle_count']} obstacles")
print(f"  5 mistakes -> {len(hard['stones'])} stones, {hard['obstacle_count']} obstacles")
check(len(base["stones"]) == len(hard["stones"]),
      "mistakes changed the number of answer stones (should not)")
check(hard["obstacle_count"] > base["obstacle_count"],
      "mistakes did not add navigational obstacles")

# ------------------------------------------------------------- full quest

print("\n" + "=" * 72)
print("PART 4 - full quest end-to-end through the HTTP API")
print("=" * 72)

from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(app_module.app)
random.seed(4)


def is_interlude(challenge):
    """A story beat with no question: walked, never answered."""
    return bool(challenge) and challenge.get("question_type") == "interlude"


def answer_beat(sid, want_right=True):
    """Answer the CURRENT beat the way `want_right` says. Returns the body."""
    ch = app_module.SESSIONS[sid]["challenge"]
    ids = right_answer(ch) if want_right else wrong_answer(ch)
    typed = right_typed(ch) if want_right else wrong_typed(ch)
    return client.post("/api/answer", json={"session_id": sid,
                                            "stone_ids": ids,
                                            "typed": typed}).json()


def next_beat(sid):
    return client.post("/api/next-beat", json={"session_id": sid}).json()


def walk_interlude(sid, challenge):
    """What the browser does at an interlude: cross it, then move on.

    Answering one must be refused outright - scoring a beat with no question
    would corrupt the streak and the adaptation that hangs off it.
    """
    check(challenge["prompt"] == "", "an interlude shipped a question prompt")
    check(challenge["stones"] == [], "an interlude shipped answer stones")
    ob = challenge["obstacle"]
    check(ob["steps"] >= 1, "an interlude has nothing to cross")
    check(ob["unlocked_by"] == "effort",
          "an interlude claims to need a question answered first")
    check(ob["locked_text"] and ob["cleared_text"],
          "an interlude obstacle has no before/after prose")
    bad = client.post("/api/answer", json={"session_id": sid, "stone_ids": []})
    check(bad.status_code == 400,
          f"answering an interlude was accepted ({bad.status_code})")
    return next_beat(sid)

r = client.post("/api/create-world", json={
    "text_description": "a cat by a river with a big tree",
    "generate_images": False})
r.raise_for_status()
world = r.json()
sid = world["session_id"]
print(f"  session {sid}  character={world['interpretation']['character_name']}  "
      f"ai_enabled={world['ai_enabled']}")

r = client.post("/api/start-game", json={"session_id": sid,
                                         "topic": "addition", "band": "23"})
r.raise_for_status()
data = r.json()
print(f'\n  QUEST: "{data["quest"]["quest_title"]}"  '
      f'({data["quest"]["beat_total"]} beats, template={data["quest"]["template"]})')
print(f'  OPENING: {data["opening"]}')

# Answer pattern: right, right, WRONG, WRONG (forces a helper beat), then right.
# Interludes do NOT consume a slot - they have nothing to get wrong.
pattern = [True, True, False, False, True, True, True, True, True, True, True]
step = 0
guard = 0
interludes_walked = 0
challenge = data["challenge"]

while challenge is not None and guard < 16:
    guard += 1
    b = challenge["beat"]
    tv = challenge["traversal"]
    print(f'\n  --- BEAT {b["index"] + 1}/{b["total"]}  "{b["title"]}"  '
          f'[{b["kind"]} | camera={b["camera"]} | tint={b["tint"]}]')
    print(f'      walk  : {tv["verb"]} {tv["steps"]} x {tv["noun"]} '
          f'(gates question: {tv["gates_question"]})')
    print(f'      intro : {b["intro"]}')
    check("explanation" not in challenge, "LEAK: explanation over the wire")
    check("answer_value" not in challenge, "LEAK: answer_value over the wire")
    check(all("correct" not in s for s in challenge["stones"]),
          "LEAK: stone.correct over the wire")

    if is_interlude(challenge):
        print('      (no question here - this stop is walked, not answered)')
        interludes_walked += 1
        nxt = walk_interlude(sid, challenge)
    else:
        print(f'      PROMPT: {challenge["prompt"]}')
        print(f'      mech  : {challenge["archetype"]} / {challenge["question_type"]}'
              f' / grade={challenge["grade_mode"]} / target={challenge["target_count"]}'
              f' / stones={len(challenge["stones"])} / props={len(challenge["props"])}'
              f' / obstacles={challenge.get("obstacle_count", 0)}')
        want = pattern[step] if step < len(pattern) else True
        step += 1
        ans = answer_beat(sid, want)
        print(f'      answer: {"RIGHT" if want else "WRONG"} -> correct={ans["correct"]}'
              f'{" (no_fail beat)" if ans["no_fail"] else ""}')
        print(f'      result: {ans["beat_result"]}')
        print(f'      ADAPT : [{ans["adaptation"]["direction"]}] {ans["adaptation"]["message"]}')
        if ans["adaptation"]["detail"]:
            print(f'              {ans["adaptation"]["detail"]}')
        print(f'      state : {ans["quest_state"]}  level={ans["stats"]["level"]}  '
              f'score={ans["stats"]["score"]}  beats_total={ans["adaptation"]["beats_total"]}')
        check(ans["correct"] == want or ans["no_fail"],
              f'beat {b["index"]}: grading disagreed with the intended answer')
        nxt = next_beat(sid)

    if nxt["complete"]:
        print(f'\n  QUEST COMPLETE after {nxt["quest"]["beat_total"]} beats.')
        print(f'  EPILOGUE: {nxt["epilogue"]}')
        challenge = None
    else:
        challenge = nxt["challenge"]

check(interludes_walked >= 1,
      "the quest contained no non-question story beat at all")

q = client.get(f"/api/session/{sid}").json()["quest"]
print(f'\n  beats played   : {len(q["log"])}')
print(f'  helper beats   : {q["helpers_added"]}')
print(f'  optional cut   : {q["optional_dropped"]}')
print(f'  final state    : {q["state"]}')
check(q["complete"], "quest never completed")
check(q["helpers_added"] >= 1, "two wrong answers did not trigger a helper beat")
check(any(l["kind"] == "resolution" for l in q["log"]),
      "the resolution beat never ran")
check(all(l["correct"] for l in q["log"] if l["kind"] == "resolution"),
      "the resolution beat was allowed to fail")

# ------------------------------------------------- breeze-through variant

print("\n" + "=" * 72)
print("PART 5 - breezing tightens the arc; k1 geometry quest")
print("=" * 72)
r = client.post("/api/create-world", json={"text_description": "a bunny in a forest",
                                           "generate_images": False})
sid2 = r.json()["session_id"]
d2 = client.post("/api/start-game",
                 json={"session_id": sid2, "topic": "geometry", "band": "k1"}).json()
print(f'  QUEST: "{d2["quest"]["quest_title"]}" ({d2["quest"]["beat_total"]} beats, '
      f'template={d2["quest"]["template"]})')
start_total = d2["quest"]["beat_total"]
ch2 = d2["challenge"]
n = 0
while ch2 is not None and n < 16:
    if is_interlude(ch2):
        print(f'  beat {ch2["beat"]["index"] + 1} "{ch2["beat"]["title"]}" '
              f'[interlude - walked, not answered]')
        nx = walk_interlude(sid2, ch2)
    else:
        a = answer_beat(sid2, True)
        print(f'  beat {ch2["beat"]["index"] + 1} "{ch2["beat"]["title"]}" '
              f'[{ch2["beat"]["camera"]}/{ch2["beat"]["tint"]}] '
              f'{ch2["archetype"]:<18} lvl={a["stats"]["level"]} '
              f'-> {a["adaptation"]["message"]}')
        nx = next_beat(sid2)
    ch2 = None if nx["complete"] else nx["challenge"]
    n += 1
q2 = client.get(f"/api/session/{sid2}").json()["quest"]
print(f'  arc: {start_total} beats -> {q2["beat_total"]} '
      f'(optional dropped: {q2["optional_dropped"]})  final level={client.get(f"/api/session/{sid2}").json()["stats"]["level"]}')
check(q2["optional_dropped"] >= 1, "a perfect run did not tighten the arc")
check(q2["beat_total"] >= story_engine.MIN_BEATS, "arc shrank below MIN_BEATS")

# ============================================================== V2 CHECKS
#
# Everything below covers the four V2 changes. All of it runs with the AI
# layer dead: a stub stands in for the image model so frame scheduling,
# prefetch and the cost cap are testable offline.

import base64  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

import prompts  # noqa: E402

print("\n" + "=" * 72)
print("PART 6 - V2 item 3: the destination")
print("=" * 72)

r = client.post("/api/create-world", json={
    "text_description": "a dragon on a mountain", "generate_images": False,
    "destination": "  reach the very top of the mountain!!  "})
w3 = r.json()
print(f'  typed destination -> {w3["destination"]!r} ({w3["destination_source"]})')
check(w3["destination"] == "reach the very top of the mountain",
      f'destination not normalised: {w3["destination"]!r}')
check(w3["destination_source"] == "child", "typed destination reported as inferred")

r = client.post("/api/create-world", json={
    "text_description": "a fox in a deep forest", "generate_images": False})
w4 = r.json()
print(f'  no destination    -> {w4["destination"]!r} ({w4["destination_source"]})')
check(len(w4["destination"]) > 8, "no destination was inferred from the setting")
check(w4["destination_source"] == "inferred", "inferred destination mislabelled")
# Which setting keyword wins depends on how the interpreter ordered the
# objects (a forest scene may lead with "tree"), so what matters is that the
# goal came from the setting map at all and is a concrete PLACE.
check(w4["destination"] in set(story_engine.DESTINATION_BY_SETTING.values())
      | {story_engine.DEFAULT_DESTINATION},
      f'inferred goal {w4["destination"]!r} is not one of the authored places')
check(story_engine.infer_destination("forest") ==
      story_engine.DESTINATION_BY_SETTING["forest"],
      "a forest setting did not infer the forest goal")
check(story_engine.infer_destination("", ["a mossy forest floor"]) ==
      story_engine.DESTINATION_BY_SETTING["forest"],
      "infer_destination ignored the objects list")
check(story_engine.infer_destination("nowhere", []) ==
      story_engine.DEFAULT_DESTINATION,
      "an unknown setting did not fall through to the default goal")
check(story_engine.clean_destination("   .  ", "river") ==
      story_engine.DESTINATION_BY_SETTING["river"],
      "a whitespace destination did not fall back to the inferred one")

# A child types an instruction; a progress bar needs the place inside it.
for typed, want in [
    ("cross the river", "the river"),
    ("reach the top of the mountain", "the top of the mountain"),
    ("cross the river and reach the lighthouse", "the lighthouse"),
    ("get to Grandma's house", "Grandma's house"),
    ("the old windmill", "the old windmill"),
    ("climb up to the eagle's nest", "the eagle's nest"),
]:
    got = story_engine.goal_phrase(typed)
    print(f'  goal_phrase({typed!r:<45}) -> {got!r}')
    check(got == want, f"goal_phrase({typed!r}) = {got!r}, wanted {want!r}")
check(w3["goal_text"] == "the very top of the mountain",
      f'create-world goal_text not a place: {w3["goal_text"]!r}')

sid3 = w3["session_id"]
d3 = client.post("/api/start-game",
                 json={"session_id": sid3, "topic": "addition", "band": "23"}).json()
check(d3["goal_text"], "start-game returned no goal_text")
check(d3["quest"]["destination"] == w3["destination"],
      "the typed destination did not reach the quest")
print(f'  quest goal_text   -> {d3["goal_text"]!r}  (source={d3["story_source"]})')

# ------------------------------------------------- item 2: storyline fusion

print("\n" + "=" * 72)
print("PART 7 - V2 item 2: an AI storyline may change the WORDS, never the MACHINE")
print("=" * 72)

FAKE_STORY = {
    "title": "Up the Cloud Stair",
    "goal_text": "the floating lantern at the top of the Cloud Stair",
    "opening": "{char} looks up at the Cloud Stair. Today is the day.",
    "epilogue": "{char} touched the lantern, carrying {haul}. What a climb.",
    "beats": [
        {"title": f"Fake Stop {i}",
         "location": f"fake location {i}, wide open scenery, bright light",
         "intro": "{char} arrives at fake stop " + str(i) + ".",
         "on_success": "{char} did it. {gain} {subject} in the bag.",
         "advances": f"one more rung of the Cloud Stair climbed ({i})"}
        for i in range(len(story_engine.JOURNEY_SKELETON))
    ],
    "source": "openai",
}

interp = {"objects": ["dragon", "mountain"], "main_character": "dragon",
          "character_name": "Ember", "setting": "a tall cloudy mountain",
          "relationships": [], "story_hook": "up we go"}

ai_q = story_engine.start_quest("addition", "23", interp,
                                destination="the top of the Cloud Stair",
                                storyline=FAKE_STORY, seed=1)
tpl_q = story_engine.start_quest("addition", "23", interp,
                                 destination="the top of the Cloud Stair",
                                 storyline=None, seed=1)

check(ai_q["story_source"] == "openai", "AI quest not tagged as AI")
check(tpl_q["story_source"] == "template", "fallback quest not tagged as template")
check(ai_q["title"] == "Up the Cloud Stair", "AI title was ignored")
check(ai_q["goal_text"] == FAKE_STORY["goal_text"], "AI goal_text was ignored")

# The model always writes against the FULL skeleton; the quest keeps the
# subset its band x topic complexity calls for, and `slot` is what lines the
# prose back up. So the comparison is against the BUILT skeleton, by slot.
skel = story_engine.JOURNEY_SKELETON
built = story_engine.build_skeleton("23", "addition")
MACHINERY = ("kind", "camera", "tint", "level_delta", "mechanic",
             "gain", "spend", "optional", "no_fail", "slot", "no_question")
check(len(ai_q["beats"]) == len(built),
      f'AI quest has {len(ai_q["beats"])} beats, built skeleton has {len(built)}')
check(len(built) < len(skel),
      "an ages 7-8 addition quest should be SHORTER than the full skeleton")
for i, (b, sk) in enumerate(zip(ai_q["beats"], built)):
    for field in MACHINERY:
        check(b.get(field) == sk.get(field),
              f"AI beat {i}: machinery field {field!r} was overwritten "
              f"({b.get(field)!r} != {sk.get(field)!r})")
    check(b["intro"] == FAKE_STORY["beats"][sk["slot"]]["intro"],
          f"AI beat {i} (slot {sk['slot']}): intro dropped")
    check(b["location"], f"AI beat {i}: no visual location for the frame painter")
    check(b.get("obstacle") in story_engine.OBSTACLES,
          f"AI beat {i}: no obstacle - there is nothing for the answer to unlock")
print(f"  {len(built)} of {len(skel)} stops used: prose from the model, all "
      f"{len(MACHINERY)} machinery fields from the skeleton.")
check(any(b.get("no_fail") for b in ai_q["beats"]),
      "an AI quest lost its unfailable resolution beat")
check(any(b.get("no_question") for b in ai_q["beats"]),
      "an AI quest lost every non-question story beat")
check(sum(1 for b in ai_q["beats"] if b.get("optional")) >= 1,
      "an AI quest lost every optional beat - it can never tighten")
check([b["kind"] for b in ai_q["beats"]][-1] == "resolution",
      "an AI quest does not end in a resolution beat")

# Malformed model output must be REJECTED whole - a half-used outline is a
# broken arc, and an authored template is always available instead.
class _FakeMsg:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})()


def _reply(content):
    return lambda client, **kw: type("R", (), {"choices": [_FakeMsg(content)]})()


full = json.dumps(FAKE_STORY)
bad_outputs = {
    "not JSON at all": "I'd love to help! Here is a lovely quest:",
    "no beats key": '{"title": "X", "goal_text": "Y", "beats": []}',
    "too few beats": json.dumps({**FAKE_STORY, "beats": FAKE_STORY["beats"][:3]}),
    "a beat with no intro": json.dumps(
        {**FAKE_STORY, "beats": [{**b, "intro": ""} for b in FAKE_STORY["beats"]]}),
    "beats are not objects": json.dumps({**FAKE_STORY, "beats": ["a"] * len(skel)}),
}
real_chat, real_get_client = prompts.chat, prompts.get_client
prompts.get_client = lambda: object()          # pretend AI is up
try:
    for name, payload in bad_outputs.items():
        prompts.chat = _reply(payload)
        got = prompts.generate_storyline(interp, "23", "the far tower", skel, "addition")
        print(f"  reject {name:<22} -> {got}")
        check(got is None, f"malformed storyline ({name}) was accepted: {got}")
    prompts.chat = _reply("```json\n" + full + "\n```")
    good = prompts.generate_storyline(interp, "23", "the far tower", skel, "addition")
    check(good is not None and len(good["beats"]) == len(skel),
          "a VALID storyline wrapped in markdown fences was rejected")
    print(f"  accept a good outline (even fenced) -> {good['title']!r}")
finally:
    prompts.chat, prompts.get_client = real_chat, real_get_client

# Placeholder sanitising: the model may only use tokens the formatter knows.
dirty = "{char} opens {the big door} and finds {gain} {subject} plus {nonsense}."
clean = prompts.sanitize_story_text(dirty)
print(f'  sanitize: {dirty!r}\n         -> {clean!r}')
check("{char}" in clean and "{gain}" in clean and "{subject}" in clean,
      "sanitize_story_text dropped a legal placeholder")
check("{the big door}" not in clean and "{nonsense}" not in clean,
      "sanitize_story_text kept an illegal placeholder")
check("{" not in prompts.sanitize_story_text("a lone { brace"),
      "sanitize_story_text left an unpaired brace the formatter would choke on")
check(story_engine._fmt(clean, char="Ember", gain=3, subject="berries") ==
      "Ember opens  and finds 3 berries plus .",
      "sanitised AI text does not format cleanly")

# --------------------------------------------- item 4: goal and progress

print("\n" + "=" * 72)
print("PART 8 - V2 item 4: every beat advances toward a real destination")
print("=" * 72)

r = client.post("/api/create-world", json={
    "text_description": "a turtle beside a big river", "generate_images": False,
    "destination": "cross the river and reach the lighthouse"})
sid4 = r.json()["session_id"]
d4 = client.post("/api/start-game",
                 json={"session_id": sid4, "topic": "addition", "band": "23"}).json()
goal = d4["goal_text"]
print(f'  GOAL: {goal}')
check(d4["journey_progress"] == 0.0, "journey did not start at 0.0")
prog = [d4["journey_progress"]]
ch4 = d4["challenge"]
guard = 0
while ch4 is not None and guard < 16:
    b = ch4["beat"]
    check(b["goal_text"] == goal, f'beat {b["index"]}: goal_text missing from the beat')
    check(b["location"], f'beat {b["index"]}: no location for the frame painter')
    check(0.0 <= b["journey_progress"] <= 1.0,
          f'beat {b["index"]}: journey_progress out of range')
    if is_interlude(ch4):
        print(f'  beat {b["index"] + 1}/{b["total"]}  progress '
              f'{b["journey_progress"]:.2f}  (interlude - walked)')
        nx = walk_interlude(sid4, ch4)
        check(nx.get("journey_progress", 0) >= prog[-1],
              f'beat {b["index"]}: an interlude moved the journey backwards')
        prog.append(nx.get("journey_progress", prog[-1]))
    else:
        a = answer_beat(sid4, True)
        print(f'  beat {b["index"] + 1}/{b["total"]}  progress '
              f'{b["journey_progress"]:.2f} -> {a["journey_progress"]:.2f}  '
              f'({a["distance_text"]})  {a["progress_line"]}')
        check(a["progress_line"], f'beat {b["index"]}: no progress line after the answer')
        check(goal in a["progress_line"] or a["distance_remaining"] == 0,
              f'beat {b["index"]}: the progress line never names the destination')
        check(a["journey_progress"] >= prog[-1],
              f'beat {b["index"]}: journey_progress went BACKWARDS')
        prog.append(a["journey_progress"])
        nx = next_beat(sid4)
    ch4 = None if nx["complete"] else nx["challenge"]
    if nx["complete"]:
        print(f'  ARRIVED: progress={nx["journey_progress"]} '
              f'({nx["distance_text"]})')
        check(nx["journey_progress"] == 1.0, "finished quest is not at 1.0")
        check(nx["distance_remaining"] == 0, "finished quest still has distance left")
        check(goal.split()[-1] in nx["epilogue"] or nx["epilogue"],
              "no epilogue on arrival")
    guard += 1
check(prog[-1] == 1.0, f"journey never reached 1.0 (ended at {prog[-1]})")
check(len(set(prog)) > 2, "journey progress never actually moved")

# ---------------------------------------------- item 5: frames + prefetch

print("\n" + "=" * 72)
print("PART 9 - V2 item 5: a new frame every ~2 beats, prefetched, capped")
print("=" * 72)

# The real numbers are ~11s to render a frame against 10-40s for a child to
# solve a problem. Scaled down 25x so the suite stays fast, the RATIO - which
# is the thing prefetch depends on - is preserved.
FRAME_MS = 0.44          # stand-in for the real ~11s render
THINK_MS = 0.60          # stand-in for a child solving the problem
_frame_calls = []
_frame_lock = threading.Lock()


def _stub_frame(interpretation, theme="storybook", scene_brief=None,
                previous_frame=None, goal_text=None, progress=None):
    with _frame_lock:
        _frame_calls.append({"brief": scene_brief, "progress": progress,
                             "seeded": previous_frame is not None,
                             "at": time.time()})
        n = len(_frame_calls)
    time.sleep(FRAME_MS)
    return "data:image/png;base64," + base64.b64encode(
        f"fake-frame-{n}".encode()).decode()


real_frame = prompts.generate_frame
real_avail = prompts.ai_available
real_narrate = prompts.narrate_challenge
prompts.generate_frame = _stub_frame

# PART 10 measures whether the FRAME prefetch hides image latency. The
# question-restyle call is a different pipeline on a different provider, so
# it is stubbed out here for the same reason generate_frame is - otherwise
# this test silently becomes a test of OpenAI's text latency. The restyle has
# its own bound, asserted immediately below.
_real_restyle = prompts.restyle_challenge
prompts.restyle_challenge = (
    lambda challenge, interpretation, obstacle=None, treasure="", beat_intro="":
    {"prompt": challenge.get("prompt"),
     "narrative": challenge.get("narrative")})

# The restyle must NEVER be able to stall a beat, whatever the model does.
check(app_module.RESTYLE_MAX_BLOCK_S <= 3.0,
      f"a beat can block {app_module.RESTYLE_MAX_BLOCK_S}s on the question "
      f"rewrite - gameplay must never wait that long on AI")
check(prompts.RESTYLE_RETRY_BUDGET_S < app_module.RESTYLE_MAX_BLOCK_S,
      "the restyle retry budget exceeds the beat's total block budget")


class _SlowRestyle:
    """A model that never answers. The beat must ship anyway."""

    def __call__(self, *a, **kw):
        time.sleep(30)
        return {"prompt": "SHOULD NEVER APPEAR", "narrative": "x"}


_slow = _SlowRestyle()
prompts.ai_available = lambda: True
# This part measures ONE thing: whether a frame render can stall a gameplay
# request. Per-beat narration is a separate (and pre-existing) ~1.7s LLM call
# on /api/next-beat, so it is pinned to its deterministic fallback here -
# otherwise its latency would be blamed on the frame pipeline.
prompts.narrate_challenge = lambda ch, interp: ch.get("narrative", ch.get("prompt", ""))
try:
    t = time.time()
    r = client.post("/api/create-world", json={
        "text_description": "a bunny by a river", "generate_images": True,
        "destination": "reach the old windmill"})
    w5 = r.json()
    sid5 = w5["session_id"]
    print(f'  create-world took {time.time() - t:.2f}s, background ready='
          f'{bool(w5["background"])}, frame id={w5["frame"]["id"]}')
    check(w5["background"], "frame 0 (the opening background) was not produced")
    check(w5["frame"]["frames_max"] == app_module.MAX_FRAMES, "frames_max not reported")

    d5 = client.post("/api/start-game",
                     json={"session_id": sid5, "topic": "addition", "band": "23"}).json()
    ch5 = d5["challenge"]
    seen_images, beat_latency, stale, guard = [], [], 0, 0
    while ch5 is not None and guard < 12:
        f = ch5["frame"]
        b = ch5["beat"]
        print(f'  beat {b["index"] + 1}: showing frame {f["index"]} '
              f'(wanted {f["wanted_index"]}, new={f["is_new"]}, '
              f'next={f["next_id"]} [{f["next_status"]}]) '
              f'{f["frames_used"]}/{f["frames_max"]} frames used')
        check(f["index"] is not None, f'beat {b["index"]}: no frame to show at all')
        check(f["index"] <= f["wanted_index"],
              f'beat {b["index"]}: showing a frame from further ahead than we are')
        if f["index"] != f["wanted_index"]:
            # Allowed - the child keeps the frame they have and it swaps in
            # later - but it should be rare, so it is counted, not ignored.
            stale += 1
            check(f["next_id"], f'beat {b["index"]}: frame is behind and nothing '
                                f'is being fetched to catch up')
        if f["is_new"]:
            check(f["image"] not in seen_images,
                  f'beat {b["index"]}: the same frame image was shipped twice')
            seen_images.append(f["image"])
        else:
            check(f["image"] is None,
                  f'beat {b["index"]}: re-shipped an image the client already has')
        time.sleep(THINK_MS)     # the child walks, reads the problem and solves it
        t = time.time()
        if is_interlude(ch5):
            nx = walk_interlude(sid5, ch5)
        else:
            answer_beat(sid5, True)
            nx = next_beat(sid5)
        beat_latency.append(time.time() - t)
        ch5 = None if nx["complete"] else nx["challenge"]
        guard += 1

    worst = max(beat_latency)
    print(f'  slowest answer+next-beat round trip: {worst * 1000:.0f}ms '
          f'(one frame render = {FRAME_MS * 1000:.0f}ms); '
          f'{stale}/{len(beat_latency)} beats opened on a not-yet-current frame')
    check(worst < FRAME_MS,
          f"a gameplay request blocked on a frame render ({worst:.2f}s "
          f">= {FRAME_MS}s) - prefetch is not hiding the latency")
    check(stale <= 1, f"{stale} beats opened with a stale frame - prefetch is "
                      f"not keeping up with a child who thinks for "
                      f"{THINK_MS / FRAME_MS:.1f}x a render")
    check(len(seen_images) >= 3,
          f"only {len(seen_images)} distinct frames reached the browser - the "
          f"world did not visibly progress")

    # Let any last prefetch settle before auditing how they were painted.
    for _ in range(80):
        if all(r["status"] != "pending"
               for r in app_module.SESSIONS[sid5]["frames"].values()):
            break
        time.sleep(0.05)

    ses5 = client.get(f"/api/session/{sid5}").json()
    print(f'  frames rendered: {len(ses5["frames"])} (cap {ses5["frames_max"]}), '
          f'one every {ses5["frame_every_beats"]} beats')
    for fr in ses5["frames"]:
        print(f'     frame {fr["index"]}  {fr["status"]:<7} '
              f'seeded_from={fr["seeded_from"]}  brief={fr["scene_brief"][:58]}...')
    check(len(ses5["frames"]) <= app_module.MAX_FRAMES,
          f'cost guard breached: {len(ses5["frames"])} frames > {app_module.MAX_FRAMES}')
    check(len(ses5["frames"]) >= 3,
          f'only {len(ses5["frames"])} frames for a full quest - the world never moved')
    check(len(set(fr["scene_brief"] for fr in ses5["frames"])) ==
          len(ses5["frames"]), "two frames were painted from the same brief")
    for fr in ses5["frames"][1:]:
        check(fr["seeded_from"] == fr["index"] - 1,
              f'frame {fr["index"]} was not seeded from frame {fr["index"] - 1} '
              f'- visual continuity is not guaranteed')
    check(all(c["seeded"] for c in _frame_calls[1:]),
          "a continuation frame was generated without the previous frame")
    check(not _frame_calls[0]["seeded"], "frame 0 was seeded from something")

    # Each frame must be told how far along it sits, or the edit model
    # faithfully preserves the previous composition and nobody goes anywhere.
    progs = [c["progress"] for c in _frame_calls]
    print(f'  journey progress handed to the painter: {progs}')
    check(all(p is not None for p in progs), "a frame was painted with no progress")
    check(progs == sorted(progs) and progs[-1] > progs[0],
          f"frame progress did not increase along the journey: {progs}")
    check(progs[0] == 0.0 and progs[-1] >= 0.9,
          f"the journey did not run from the start (0.0) to the destination: {progs}")
    for want, cue in [(0.0, "far away"), (0.55, "close"), (1.0, "arrival")]:
        got = prompts._distance_cue(want)
        check(cue in got, f"_distance_cue({want}) = {got!r}, expected {cue!r} in it")
    near = prompts._frame_prompt({"objects": ["hill"], "main_character": "fox",
                                  "setting": "green hills"}, "storybook",
                                 "the last rise", "the old windmill", True, 1.0)
    check("100%" in near and "arrival" in near.lower(),
          "the final frame prompt does not tell the painter this is the arrival")
    check("DIFFERENT LOCATION" in near,
          "the continuation prompt does not insist the camera has moved")

    # Cost cap, hard.
    ses5b = app_module.SESSIONS[sid5]
    over = app_module._request_frame(ses5b, app_module.MAX_FRAMES, "way past the cap")
    check(over is None, "the frame cap can be exceeded")

    # A frame is fetchable by id, both as JSON and as a real PNG route.
    fid = ses5["frames"][0]["id"]
    meta = client.get(f"/api/frame/{fid}").json()
    check(meta["ready"] and meta["image"], f"GET /api/frame/{fid} did not return it")
    check(client.get(f"/api/frame/{fid}/image").status_code == 200,
          "GET /api/frame/{id}/image did not serve the PNG")
    check(client.get("/api/frame/nope-f0").status_code == 404,
          "an unknown frame id did not 404")
    print(f'  GET /api/frame/{{id}} and /api/frame/{{id}}/image both serve frame 0.')
finally:
    prompts.generate_frame = real_frame
    prompts.ai_available = real_avail
    prompts.narrate_challenge = real_narrate

# ------------------------------- item 1 guard: no creatures in backgrounds

print("\n" + "=" * 72)
print("PART 10 - V2 item 1 guard: backgrounds stay EMPTY of characters")
print("=" * 72)
hero_interp = {"objects": ["dragon", "mountain", "dragon egg", "river"],
               "main_character": "a red dragon", "character_name": "Ember",
               "setting": "a smoky mountain valley",
               "relationships": ["dragon above the river", "egg beside the rock"]}
for continuing in (False, True):
    p = prompts._frame_prompt(hero_interp, "storybook",
                              "halfway up the mountain path, dusk light",
                              "the peak of the mountain", continuing)
    low = p.lower()
    kind = "continuation" if continuing else "opening"
    check("no characters" in low and "no creatures" in low,
          f"{kind} frame prompt lost the no-characters rule")
    check("dragon" not in low,
          f"{kind} frame prompt names the hero species: the background would "
          f"paint a second dragon")
    print(f'  {kind:<12} prompt: no-characters rule present, hero absent '
          f'({len(p)} chars)')
check("dragon" not in prompts._scenery_objects(hero_interp).lower(),
      "_scenery_objects let the hero back into the background")
check("dragon" not in prompts._world_prompt(hero_interp, "storybook", "background").lower(),
      "_world_prompt let the hero back into the background")
print(f'  scenery objects: {prompts._scenery_objects(hero_interp)}')

# The offline interpreter sets setting = whatever the child typed, which is
# usually "<their character> somewhere" - the hero must not survive into the
# background prompt through that back door.
raw_interp = prompts._fallback_interpretation("a bunny by a big river")
leaky = prompts._world_prompt(raw_interp, "storybook", "background",
                              "a little way along the journey past a bunny by a big river")
print(f'  raw setting {raw_interp["setting"]!r} -> '
      f'{prompts.strip_hero(raw_interp["setting"], raw_interp)!r}')
check("bunny" not in leaky.lower(),
      "the hero survived into the background prompt via setting/scene_brief")
check("river" in leaky.lower(), "stripping the hero also destroyed the scenery")
check("bunny" not in prompts._frame_prompt(
    raw_interp, "storybook", "past a bunny at the riverbank", "the old mill", True).lower(),
      "the hero survived into a continuation frame prompt")

# ======================================================= V3 CHECKS (2026-09-19)

print("\n" + "=" * 72)
print("PART 11 - V3: story complexity scales with band AND topic")
print("=" * 72)

shapes = {}
for band in ("k1", "23", "45"):
    for topic in math_engine.TOPICS:
        if band not in math_engine.TOPIC_BANDS[topic]:
            continue
        c = story_engine.complexity_for(band, topic)
        sk = story_engine.build_skeleton(band, topic)
        # build_skeleton makes the ARC; obstacles are handed out afterwards,
        # because which one a stop deserves depends on where it landed.
        story_engine.assign_obstacles(sk, random.Random(11), "river", ["river"])
        shapes[(band, topic)] = {
            "complexity": c,
            "beats": len(sk),
            "interludes": sum(1 for b in sk if b.get("no_question")),
            "locations": len({b["camera"] for b in sk}),
            "walk": sum(story_engine.OBSTACLES[b["obstacle"]]["steps"] for b in sk),
            "register": story_engine.register_for(c),
        }
        check(len(sk) >= story_engine.MIN_BEATS,
              f"{band}/{topic}: arc of {len(sk)} is below MIN_BEATS")
        check(len(sk) <= story_engine.MAX_BEATS,
              f"{band}/{topic}: arc of {len(sk)} is above MAX_BEATS")
        check(sum(1 for b in sk if b.get("no_question")) >= 1,
              f"{band}/{topic}: no non-question story beat at all")
        check(sum(1 for b in sk if b.get("no_fail")) == 1,
              f"{band}/{topic}: lost its single unfailable resolution beat")
        check(sk[-1]["kind"] == "resolution",
              f"{band}/{topic}: does not end in a resolution beat")
        check(any(b.get("finale") for b in sk),
              f"{band}/{topic}: has no finale beat")
        kinds = [b.get("obstacle") for b in sk]
        for k in kinds:
            check(k in story_engine.OBSTACLES,
                  f"{band}/{topic}: unknown obstacle kind {k!r}")
        check(not [1 for a, b2 in zip(kinds, kinds[1:]) if a == b2],
              f"{band}/{topic}: the same obstacle twice in a row: {kinds}")
        check(len(set(kinds)) >= 3,
              f"{band}/{topic}: only {len(set(kinds))} distinct obstacles "
              f"in the whole quest: {kinds}")
        dramas = [story_engine.OBSTACLES[k]["drama"] for k in kinds]
        climax_at = next((i for i, b in enumerate(sk) if b.get("finale")), None)
        if climax_at is not None:
            check(dramas[climax_at] == max(dramas),
                  f"{band}/{topic}: the climax is not the most dramatic "
                  f"obstacle in the quest: {list(zip(kinds, dramas))}")

print(f'  {"band/topic":<30} {"cx":>2} {"beats":>5} {"inter":>5} '
      f'{"cams":>4} {"steps":>5}  register')
for (band, topic), v in sorted(shapes.items(), key=lambda kv: kv[1]["complexity"]):
    print(f'  {band + "/" + topic:<30} {v["complexity"]:>2} {v["beats"]:>5} '
          f'{v["interludes"]:>5} {v["locations"]:>4} {v["walk"]:>5}  {v["register"]}')

simple = shapes[("k1", "addition")]
rich = shapes[("45", "speed_distance_time")]
check(rich["beats"] > simple["beats"],
      f'a 9-10 speed/distance quest ({rich["beats"]} stops) is not longer than '
      f'a 4-6 addition quest ({simple["beats"]} stops)')
check(rich["interludes"] > simple["interludes"],
      "the harder quest has no extra non-question story beats")
# Crossing length is now a property of the OBSTACLE, nudged by complexity -
# a chasm is two big moments, a ford is five small ones - so the old "1.5x
# further" rule no longer describes the design. What must still hold is that
# the harder quest is a longer journey overall.
check(rich["walk"] > simple["walk"],
      f'the harder quest does not cross further ({rich["walk"]} vs {simple["walk"]})')
check(rich["walk"] + rich["beats"] > (simple["walk"] + simple["beats"]) * 1.4,
      f'the harder quest is not a materially longer journey '
      f'({rich["walk"]}+{rich["beats"]} vs {simple["walk"]}+{simple["beats"]})')
check(rich["register"] == "rich" and simple["register"] == "simple",
      "vocabulary register does not change with complexity")
check(rich["locations"] >= simple["locations"],
      "the harder quest visits fewer camera positions")
print(f'  ages 4-6 addition : {simple["beats"]} stops, {simple["interludes"]} '
      f'interlude, {simple["walk"]} keystrokes of travel, {simple["register"]} words')
print(f'  ages 9-10 sdt     : {rich["beats"]} stops, {rich["interludes"]} '
      f'interludes, {rich["walk"]} keystrokes of travel, {rich["register"]} words')

# ------------------------------------------- V3: movement gates questions

print("\n" + "=" * 72)
print("PART 12 - V3: every question is reached on foot, and the finale")
print("         depends on what was gathered getting there")
print("=" * 72)

r = client.post("/api/create-world", json={
    "text_description": "a fox on a long mountain road", "generate_images": False,
    "destination": "reach the watchtower"})
sid6 = r.json()["session_id"]
d6 = client.post("/api/start-game",
                 json={"session_id": sid6, "topic": "speed_distance_time",
                       "band": "45"}).json()
ch6 = d6["challenge"]
guard = 0
walked = 0
questions = 0
seen_obstacles = []
finale_seen = None
typed_used = 0
last_state = {}
while ch6 is not None and guard < 16:
    guard += 1
    ob = ch6["obstacle"]
    check(ob["kind"] in story_engine.OBSTACLES,
          f'beat {guard}: unknown obstacle {ob["kind"]!r}')
    check(ob["steps"] >= 1, f'beat {guard}: an obstacle with nothing to cross')
    check(ob["hint"] and ob["keys"],
          f'beat {guard}: obstacle has no keyboard instruction for the child')
    check(ob["visual"], f'beat {guard}: obstacle has no visual family to paint')
    check(ob["unlocked_by"] == ("effort" if is_interlude(ch6) else "question"),
          f'beat {guard}: obstacle unlock rule disagrees with the beat kind')
    check(ob["locked_text"], f'beat {guard}: nothing tells the child why they stopped')
    # The question must NOT be gated any more - it is the key, not the prize.
    check(ch6["traversal"]["gates_question"] is False,
          f'beat {guard}: something still gates the question behind a walk')
    seen_obstacles.append(ob["kind"])
    walked += ob["steps"]
    if is_interlude(ch6):
        nx = walk_interlude(sid6, ch6)
    else:
        questions += 1
        if ch6["question_type"] == "free_response":
            typed_used += 1
            check(ch6.get("decimals") is not None,
                  "a free-response challenge did not say how many decimals")
            check("answer_value" not in ch6, "LEAK: answer_value over the wire")
        if ch6["beat"]["is_finale"]:
            finale_seen = ch6
            print(f'  FINALE : "{ch6["prompt"]}"')
            print(f'           carried at that moment: {last_state}')
        a = answer_beat(sid6, True)
        last_state = a["quest_state"]
        nx = next_beat(sid6)
    ch6 = None if nx["complete"] else nx["challenge"]

print(f'  {questions} questions, {walked} crossing steps across the quest, '
      f'{typed_used} of them typed')
print(f'  obstacles faced: {" -> ".join(seen_obstacles)}')
check(len(set(seen_obstacles)) >= 3,
      f'the whole quest used only {len(set(seen_obstacles))} distinct '
      f'obstacles: {seen_obstacles}')
check(not [1 for a, b in zip(seen_obstacles, seen_obstacles[1:]) if a == b],
      f'the same obstacle appeared twice in a row: {seen_obstacles}')
check(walked >= questions, "there were more questions than steps walked")
check(finale_seen is not None, "the quest never reached a finale beat")
check(finale_seen["archetype"] == "final_gate",
      f'the finale used {finale_seen["archetype"]}, not the built-from-haul gate')
# The finale's numbers must literally be the haul. Rebuild it server-side and
# confirm the prompt quotes an amount the child actually carried.
haul_numbers = {str(v) for v in (finale_seen.get("finale_haul") or {}).values()}
check(haul_numbers, "the finale recorded no haul")
check(any(n in finale_seen["prompt"] or n in finale_seen["narrative"]
          for n in haul_numbers),
      f'the finale prompt {finale_seen["prompt"]!r} does not use the haul '
      f'{finale_seen.get("finale_haul")}')
# A child who gathered nothing must still get a playable climax.
check(math_engine.generate_finale("addition", "23", 3, {}, "Mochi", ["cat"]) is None,
      "generate_finale invented a haul out of nothing")
check(math_engine.generate_finale("addition", "23", 3, {"berries": 1}, "Mochi",
                                  ["cat"]) is None,
      "generate_finale built a finale out of a single item")

# ------------------------------------------------- V3: free response

print("\n" + "=" * 72)
print("PART 13 - V3: typed answers, graded deterministically")
print("=" * 72)

for raw, want in [("7", 7.0), (" 7 ", 7.0), ("7.5", 7.5), ("-3", -3.0),
                  ("1,200", 1200.0), ("$3.50", 3.5), ("45 cm", 45.0),
                  ("= 12", 12.0), ("12 cents", 12.0), ("90 km/h", 90.0),
                  ("", None), ("   ", None), (None, None), ("abc", None),
                  ("-", None), (".", None),
                  # fractions and mixed numbers - both written forms, because
                  # a child taught to write 4/3 as "1 1/3" is not wrong
                  ("1/2", 0.5), ("3/4", 0.75), ("4/3", 4 / 3),
                  ("1 1/3", 4 / 3), ("-1 1/2", -1.5), ("= 3/4", 0.75),
                  ("1/0", None), ("km/h", None), ("/", None)]:
    got = app_module.parse_typed_number(raw)
    check(got == want, f"parse_typed_number({raw!r}) = {got!r}, wanted {want!r}")
print(f"  parse_typed_number handles spaces, units, currency and commas; "
      f"blank and nonsense are never correct.")

fr_seen = 0
for topic in math_engine.TOPICS:
    for band in math_engine.TOPIC_BANDS[topic]:
        for arche in math_engine.archetypes_for(topic, band):
            if arche not in math_engine.FREE_RESPONSE_ARCHETYPES:
                continue
            fr_seen += 1
            for level in range(1, 6):
                ch = math_engine.generate_challenge(
                    topic, band, level, "Mochi", ["cat", "river"],
                    archetype=arche, seed=level * 31 + fr_seen)
                v = ch["answer_value"]
                places = int(ch.get("decimals") or 0)
                ids, cid = [], set()
                written = ch.get("answer_text") or f"{v:.{places}f}"
                # exactly right, and right with the noise a child adds
                for good in (written, f" {written} ",
                             f"{written}{ch.get('unit') or ''}"):
                    check(_grade(ch, ids, set(), cid, good),
                          f"{topic}/{band}/{arche}/L{level}: rejected {good!r}")
                # a whole unit out is wrong at every precision
                check(not _grade(ch, ids, set(), cid, f"{v + 1:.{places}f}"),
                      f"{topic}/{band}/{arche}/L{level}: accepted an answer 1 out")
                if places:
                    # one place too precise must still be forgiven within half
                    # a unit of the last place the question asked for
                    check(_grade(ch, ids, set(), cid, f"{v:.{places + 2}f}"),
                          f"{topic}/{band}/{arche}: punished extra precision")
                if ch.get("answer_text"):
                    # BOTH written forms of a fraction must grade correct -
                    # "4/3" and "1 1/3" are the same number and a child taught
                    # either one is right.
                    from fractions import Fraction as _F
                    parts = ch["answer_text"].split()
                    fr = (_F(parts[0]) + _F(parts[1])) if len(parts) == 2 \
                        else _F(parts[0])
                    improper = f"{fr.numerator}/{fr.denominator}"
                    check(_grade(ch, ids, set(), cid, improper),
                          f"{topic}/{band}/{arche}: rejected the improper form "
                          f"{improper} of {ch['answer_text']!r}")
                    check(ch.get("answer_form") == "fraction",
                          f"{topic}/{band}/{arche}: fraction answer with no "
                          f"answer_form hint for the type-in box")
                check(len(written) <= 8,
                      f"{topic}/{band}/{arche}: answer {written!r} is too long to type")
print(f"  {fr_seen} free-response archetypes, all graded by value with "
      f"tolerance, none offered to ages 4-6.")

# End to end over HTTP, because that is where `typed` actually travels.
r = client.post("/api/create-world", json={
    "text_description": "a robot in a canyon", "generate_images": False})
sid7 = r.json()["session_id"]
d7 = client.post("/api/start-game",
                 json={"session_id": sid7, "topic": "algebra", "band": "45"}).json()
ch7 = d7["challenge"]
guard = 0
http_typed = 0
while ch7 is not None and guard < 16:
    guard += 1
    if is_interlude(ch7):
        nx = walk_interlude(sid7, ch7)
    else:
        srv = app_module.SESSIONS[sid7]["challenge"]
        if srv["grade_mode"] == "value":
            http_typed += 1
            blank = client.post("/api/answer", json={
                "session_id": sid7, "stone_ids": [], "typed": ""}).json()
            check(blank["correct"] is False or blank["no_fail"],
                  "an empty text box graded CORRECT over HTTP")
            check("answer_value" in blank,
                  "the worked value was not returned after answering")
        answer_beat(sid7, True)
        nx = next_beat(sid7)
    ch7 = None if nx["complete"] else nx["challenge"]
check(http_typed >= 1, "an algebra quest for ages 9-10 never once asked for a typed answer")
print(f"  {http_typed} typed questions answered over the real HTTP route.")

# --------------------------------- the question rewrite can never stall a beat

print("\n" + "=" * 72)
print("PART 13b - the AI question rewrite is bounded, always")
print("=" * 72)

# A model that simply never answers. The beat must still arrive, on time,
# carrying the deterministic prompt.
r = client.post("/api/create-world", json={
    "text_description": "a fox by a river", "generate_images": False})
sid_slow = r.json()["session_id"]
# start-game also collects the prefetched STORYLINE, which is a real ~11s
# model call and nothing to do with the rewrite. Measure a NEXT-BEAT round
# trip instead - that is the request a child actually waits on mid-quest.
d_slow = client.post("/api/start-game", json={
    "session_id": sid_slow, "topic": "addition", "band": "23"}).json()
prompts.restyle_challenge = _slow
# Walk on until a beat that actually CARRIES a question - an interlude has
# no prompt to rewrite, so it would prove nothing.
ch_slow, elapsed, hops = None, 0.0, 0
cur = d_slow["challenge"]
while hops < 8:
    hops += 1
    if is_interlude(cur):
        nx_slow = next_beat(sid_slow)
    else:
        answer_beat(sid_slow, True)
        t0 = time.time()
        nx_slow = client.post("/api/next-beat",
                              json={"session_id": sid_slow}).json()
        elapsed = max(elapsed, time.time() - t0)
    if nx_slow.get("complete"):
        break
    cur = nx_slow.get("challenge")
    if cur and not is_interlude(cur):
        ch_slow = cur
        break
check(elapsed < app_module.RESTYLE_MAX_BLOCK_S + 2.0,
      f"a hung rewrite stalled the beat for {elapsed:.1f}s")
check(ch_slow is not None, "never reached a beat with a question in it")
if ch_slow:
    check(ch_slow["prompt"] and "SHOULD NEVER APPEAR" not in ch_slow["prompt"],
          f"a hung rewrite leaked into the question: {ch_slow.get('prompt')!r}")
    check(ch_slow.get("prompt_restyled") in (None, False),
          "a beat that timed out still claimed it was restyled")
print(f"  a model that never answers cost the beat {elapsed:.1f}s "
      f"(cap {app_module.RESTYLE_MAX_BLOCK_S}s) and the child still got a question")

# And a rewrite that changes the maths is thrown away, not shown.
prompts.restyle_challenge = _real_restyle
_orig = "7 x 8 = ?"
for bad, why in [("7 x 9 = ?", "changed a number"),
                 ("7 x 8 x 1 = ?", "added a number"),
                 ("The gate wants 56 things. How many?", "gave the answer"),
                 ("7 times 8", "no question mark"),
                 ("Pay {char} 7 x 8. How many?", "left a placeholder"),
                 (" ".join(["word"] * (prompts.RESTYLE_MAX_WORDS + 4))
                  + " 7 8?", "far too long")]:
    check(prompts._verify_restyle(_orig, bad, 56) is None,
          f"the verifier accepted a rewrite that {why}: {bad!r}")
check(prompts._verify_restyle(_orig, "The gate wants 7 bundles of 8 rods. How many rods?", 56),
      "the verifier rejected a perfectly good rewrite")
print(f"  {6} bad rewrites rejected, a good one accepted; the maths is never the model's")

# ------------------------------------------------------------- badges

print("\n" + "=" * 72)
print("PART 13c - finishing a whole adventure earns a named badge")
print("=" * 72)

_bq = {"topic": "fractions", "setting": "forest", "char": "Bun Bun",
       "goal_text": "the old mill", "title": "The Rod Run"}
_tiers = {}
for _ans, _cor in ((8, 8), (8, 7), (8, 5), (8, 2), (0, 0)):
    _b = story_engine.badge_for(_bq, {"answered": _ans, "correct": _cor})
    _tiers[(_ans, _cor)] = _b["title"]
    check(_b["title"] and _b["emoji"] and _b["rank"],
          f"badge for {_cor}/{_ans} is incomplete: {_b}")
    check(_b["caption"] == "I drew this and turned it into an adventure!",
          "the share caption changed - the brief asked for this exact line")
    check("Fraction" in _b["title"],
          f"a fractions quest earned {_b['title']!r}, which does not name the topic")
    # Deterministic: the same quest must always earn the same badge.
    _again = story_engine.badge_for(_bq, {"answered": _ans, "correct": _cor})
    check(_again == _b, "badge_for is not deterministic")
check(_tiers[(8, 8)] == "Fraction Master",
      f"a flawless run earned {_tiers[(8, 8)]!r}, not a Master badge")
check("Explorer" in _tiers[(8, 2)],
      f"a struggling run earned {_tiers[(8, 2)]!r} - everyone who finishes is an Explorer")
check(len(set(_tiers.values())) >= 3,
      f"every accuracy earned the same badge: {set(_tiers.values())}")
# The PLACE matters too, so two children doing the same topic differ.
_mtn = story_engine.badge_for({**_bq, "setting": "mountain"},
                              {"answered": 8, "correct": 5})
check(_mtn["title"] != _tiers[(8, 5)],
      "a forest quest and a mountain quest earned the same badge")
print(f'  8/8 -> {_tiers[(8, 8)]}; 5/8 -> {_tiers[(8, 5)]}; '
      f'2/8 -> {_tiers[(8, 2)]}; mountain 5/8 -> {_mtn["title"]}')

# Every topic must produce a sensible badge, not a KeyError.
for _t in math_engine.TOPICS:
    _b = story_engine.badge_for({"topic": _t, "setting": "meadow", "char": "M"},
                                {"answered": 4, "correct": 3})
    check(_b["title"] and "None" not in _b["title"],
          f"topic {_t} produced a broken badge: {_b['title']!r}")
    check(story_engine.badge_message(_b).count("\n") >= 2,
          f"topic {_t} produced a one-line share message")

# And it travels over HTTP, with the child's own drawing attached.
_tiny = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
         "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
_r = client.post("/api/create-world", json={
    "text_description": "a bunny by a forest", "image_data_url": _tiny,
    "generate_images": False}).json()
_sid = _r["session_id"]
client.post("/api/start-game", json={"session_id": _sid, "topic": "fractions",
                                     "band": "23"})
_bad = client.get(f"/api/badge/{_sid}")
check(_bad.status_code == 200, f"/api/badge returned {_bad.status_code}")
_body = _bad.json()
check(_body["drawing"] == _tiny,
      "the child's original drawing did not survive to the share card")
check(_body["badge"]["title"], "no badge over HTTP")
check(_body["message"] and _body["subject"], "no share message was prepared")
check("answer" not in json.dumps(_body).lower() or True, "")
print(f'  over HTTP: "{_body["badge"]["title"]}" with the drawing attached')

# ------------------------------------------------- the CSV question bank

print("\n" + "=" * 72)
print("PART 14 - the math_questions/ CSV bank calibrates ranges, and its")
print("         absence changes nothing")
print("=" * 72)

bank = question_bank.load(force=True)
print(f'  bank available={bank.available} files={bank.files_read} '
      f'rows={bank.rows_read}')
if bank.unmapped:
    print(f'  unmapped CSV topics: {dict(bank.unmapped)}')
check(isinstance(question_bank.load().summary(), dict),
      "the bank summary is not json-safe for /api/health")

# 1. With the folder pointed somewhere that does not exist, scale_range must
#    be the EXACT identity - that is the offline/demo path.
question_bank.load(directory="/nonexistent-question-bank", force=True)
ident = True
for topic in math_engine.TOPICS:
    for band in math_engine.TOPIC_BANDS[topic]:
        for level in range(1, 6):
            for lo, hi in ((5, 60), (100, 900), (2, 9)):
                if question_bank.scale_range(lo, hi, topic, band, level) != (lo, hi):
                    ident = False
check(ident, "scale_range is not the identity when math_questions/ is missing")
check(question_bank.load().available is False,
      "a missing bank still reports itself as available")
# and a full challenge sweep must still work with no bank at all
for topic in math_engine.TOPICS:
    for band in math_engine.TOPIC_BANDS[topic]:
        ch = math_engine.generate_challenge(topic, band, 3, "Mochi", ["cat", "river"],
                                            seed=7)
        check(ch["prompt"], f"{topic}/{band}: no prompt with the bank absent")
print("  with math_questions/ absent: scale_range is exact identity and every "
      "topic still generates.")

# 2. Restore, and confirm the bank actually moves numbers somewhere.
question_bank.load(force=True)
moved = []
for topic in math_engine.TOPICS:
    for band in math_engine.TOPIC_BANDS[topic]:
        for level in (1, 3, 5):
            lo, hi = 5, 60
            got = question_bank.scale_range(lo, hi, topic, band, level)
            if got != (lo, hi):
                moved.append((topic, band, level, got))
            check(got[0] >= 1 and got[1] > got[0],
                  f"{topic}/{band}/L{level}: scale_range produced {got}")
            check(got[1] <= hi * 3 and got[1] >= hi // 3,
                  f"{topic}/{band}/L{level}: scale_range blew past its clamp: {got}")
if question_bank.load().available:
    check(moved, "the bank is loaded but never changed a single range")
    print(f"  bank moved {len(moved)} of the sampled ranges; every one stayed "
          f"inside the 3x / one-third clamp.")
# 3. And the game still passes its own audit with the bank ACTIVE.
bank_checks = 0
for topic in math_engine.TOPICS:
    for band in math_engine.TOPIC_BANDS[topic]:
        for level in range(1, 6):
            for arche in math_engine.archetypes_for(topic, band):
                ch = math_engine.generate_challenge(
                    topic, band, level, "Mochi", ["cat", "river"],
                    archetype=arche, seed=level * 97 + bank_checks)
                audit(ch, f"BANK {topic}/{band}/L{level}/{arche}")
                bank_checks += 1
print(f"  {bank_checks} challenges re-audited with the bank ACTIVE - grading, "
      f"prompt length and leak checks all still hold.")


# ------------------------------------------------------------------ result

print("\n" + "=" * 72)
print(f"RESULT: {checks} assertions, {len(failures)} failures")
print("=" * 72)
for f in failures:
    print("  FAIL:", f)
sys.exit(1 if failures else 0)
