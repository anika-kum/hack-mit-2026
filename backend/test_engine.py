"""
Doodle Quest verification harness.  Run with:  python3 test_engine.py

Checks, for every topic x band x level x archetype:
  * `prompt` is non-empty and <= 12 words
  * the deterministic grader ACCEPTS the right answer
  * the deterministic grader REJECTS a wrong answer
  * the public (network) payload contains no answer key
Then plays a full quest end-to-end through the real HTTP routes.
"""

import json
import random
import sys

import app as app_module
import math_engine
import story_engine
from app import _grade, _public_challenge

MAX_PROMPT_WORDS = 12

# Archetypes where a visual difference between right and wrong stones IS the
# question (mushrooms look like mushrooms; a pentagon looks like a pentagon).
# (a thistle must LOOK like a thistle; a pentagon must LOOK like a pentagon)
VISUAL_BY_DESIGN = {"pick_around_mushrooms", "shape_door", "berry_harvest"}

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
    if mode == "order":
        return list(ch["answer_order"])
    if mode == "count":
        return correct[:ch["target_count"]]
    return correct


def wrong_answer(ch):
    mode = ch.get("grade_mode", "set")
    ids = [s["id"] for s in ch["stones"]]
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
          ("single_choice", "multi_select", "collect_count", "ordered_path"),
          f"{tag}: bad question_type {ch.get('question_type')}")
    check(ch.get("grade_mode") in ("set", "count", "order", "sum", "groups"),
          f"{tag}: bad grade_mode {ch.get('grade_mode')}")
    check(isinstance(ch.get("target_count"), int) and ch["target_count"] >= 1,
          f"{tag}: bad target_count {ch.get('target_count')}")
    check(any(s["correct"] for s in ch["stones"]) or ch["grade_mode"] == "order",
          f"{tag}: no correct stone")
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
    check(_grade(ch, right_answer(ch), set(right_answer(ch)),
                 {s["id"] for s in ch["stones"] if s["correct"]}),
          f"{tag}: grader REJECTED the correct answer ({ch['grade_mode']})")
    wa = wrong_answer(ch)
    check(not _grade(ch, wa, set(wa), {s["id"] for s in ch["stones"] if s["correct"]}),
          f"{tag}: grader ACCEPTED a wrong answer {wa} ({ch['grade_mode']})")

    # leak audit
    pub = _public_challenge(ch)
    blob = json.dumps(pub)
    for k in ("explanation", "order_cyclic"):
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
        check(arche_list, f"{topic}/{band}: no archetypes at all")
        mechanics = {story_engine.MECHANIC_OF.get(a) for a in arche_list}
        check(mechanics - {"pick"}, f"{topic}/{band}: only PICK-ONE mechanics available")
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

# --------------------------------------------------------- k1 no-reading

print("\n" + "=" * 72)
print("PART 2 - ages 4-6 must be playable without reading")
print("=" * 72)
for topic in ("addition", "subtraction", "geometry"):
    for level in range(1, 6):
        ch = math_engine.generate_challenge(topic, "k1", level, "Mochi",
                                            ["bunny", "meadow"], seed=level * 7)
        readable = [s for s in ch["stones"]
                    if s["label"] and s.get("dots") is None and s.get("shape") is None]
        ok = not readable
        print(f"  {topic:<14} L{level}  {ch['archetype']:<22} "
              f"{'no-reading OK' if ok else f'{len(readable)} word/numeral-only stones'}")
        check(ok, f"k1/{topic}/L{level}: {len(readable)} stones need reading")

# ------------------------------------------------------ obstacle policy

print("\n" + "=" * 72)
print("PART 3 - obstacle policy: mistakes add SCENERY, never distractor stones")
print("=" * 72)
base = math_engine.generate_challenge("addition", "23", 3, "Mochi", ["cat", "river"],
                                      mistakes_total=0, archetype="berry_baskets", seed=99)
hard = math_engine.generate_challenge("addition", "23", 3, "Mochi", ["cat", "river"],
                                      mistakes_total=5, archetype="berry_baskets", seed=99)
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
pattern = [True, True, False, False, True, True, True, True, True, True, True]
step = 0
challenge = data["challenge"]

while challenge is not None and step < 12:
    server_ch = app_module.SESSIONS[sid]["challenge"]
    b = challenge["beat"]
    print(f'\n  --- BEAT {b["index"] + 1}/{b["total"]}  "{b["title"]}"  '
          f'[{b["kind"]} | camera={b["camera"]} | tint={b["tint"]}]')
    print(f'      intro : {b["intro"]}')
    print(f'      PROMPT: {challenge["prompt"]}')
    print(f'      narr  : {challenge["narrative"]}')
    print(f'      mech  : {challenge["archetype"]} / {challenge["question_type"]}'
          f' / grade={challenge["grade_mode"]} / target={challenge["target_count"]}'
          f' / stones={len(challenge["stones"])} / props={len(challenge["props"])}'
          f' / obstacles={challenge.get("obstacle_count", 0)}')
    check("explanation" not in challenge, "LEAK: explanation over the wire")
    check(all("correct" not in s for s in challenge["stones"]),
          "LEAK: stone.correct over the wire")

    want = pattern[step] if step < len(pattern) else True
    ids = right_answer(server_ch) if want else wrong_answer(server_ch)
    ans = client.post("/api/answer", json={"session_id": sid, "stone_ids": ids}).json()
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

    nxt = client.post("/api/next-beat", json={"session_id": sid}).json()
    if nxt["complete"]:
        print(f'\n  QUEST COMPLETE after {nxt["quest"]["beat_total"]} beats.')
        print(f'  EPILOGUE: {nxt["epilogue"]}')
        challenge = None
    else:
        challenge = nxt["challenge"]
    step += 1

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
while ch2 is not None and n < 12:
    sch = app_module.SESSIONS[sid2]["challenge"]
    a = client.post("/api/answer", json={"session_id": sid2,
                                         "stone_ids": right_answer(sch)}).json()
    print(f'  beat {ch2["beat"]["index"] + 1} "{ch2["beat"]["title"]}" '
          f'[{ch2["beat"]["camera"]}/{ch2["beat"]["tint"]}] '
          f'{ch2["archetype"]:<16} lvl={a["stats"]["level"]} '
          f'-> {a["adaptation"]["message"]}')
    nx = client.post("/api/next-beat", json={"session_id": sid2}).json()
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

skel = story_engine.JOURNEY_SKELETON
MACHINERY = ("kind", "camera", "tint", "level_delta", "mechanic",
             "gain", "spend", "optional", "no_fail")
check(len(ai_q["beats"]) == len(skel),
      f'AI quest has {len(ai_q["beats"])} beats, skeleton has {len(skel)}')
for i, (b, s) in enumerate(zip(ai_q["beats"], skel)):
    for field in MACHINERY:
        check(b.get(field) == s.get(field),
              f"AI beat {i}: machinery field {field!r} was overwritten "
              f"({b.get(field)!r} != {s.get(field)!r})")
    check(b["intro"] == FAKE_STORY["beats"][i]["intro"], f"AI beat {i}: intro dropped")
    check(b["location"], f"AI beat {i}: no visual location for the frame painter")
print(f"  {len(skel)} beats fused: prose from the model, all "
      f"{len(MACHINERY)} machinery fields from the skeleton.")
check(any(b.get("no_fail") for b in ai_q["beats"]),
      "an AI quest lost its unfailable resolution beat")
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
while ch4 is not None and guard < 12:
    b = ch4["beat"]
    check(b["goal_text"] == goal, f'beat {b["index"]}: goal_text missing from the beat')
    check(b["location"], f'beat {b["index"]}: no location for the frame painter')
    check(0.0 <= b["journey_progress"] <= 1.0,
          f'beat {b["index"]}: journey_progress out of range')
    a = client.post("/api/answer", json={
        "session_id": sid4,
        "stone_ids": right_answer(app_module.SESSIONS[sid4]["challenge"])}).json()
    print(f'  beat {b["index"] + 1}/{b["total"]}  progress '
          f'{b["journey_progress"]:.2f} -> {a["journey_progress"]:.2f}  '
          f'({a["distance_text"]})  {a["progress_line"]}')
    check(a["progress_line"], f'beat {b["index"]}: no progress line after the answer')
    check(goal in a["progress_line"] or a["distance_remaining"] == 0,
          f'beat {b["index"]}: the progress line never names the destination')
    check(a["journey_progress"] >= prog[-1],
          f'beat {b["index"]}: journey_progress went BACKWARDS')
    prog.append(a["journey_progress"])
    nx = client.post("/api/next-beat", json={"session_id": sid4}).json()
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
        time.sleep(THINK_MS)     # the child reads the problem and solves it
        t = time.time()
        a = client.post("/api/answer", json={
            "session_id": sid5,
            "stone_ids": right_answer(app_module.SESSIONS[sid5]["challenge"])}).json()
        nx = client.post("/api/next-beat", json={"session_id": sid5}).json()
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

# ------------------------------------------------------------------ result

print("\n" + "=" * 72)
print(f"RESULT: {checks} assertions, {len(failures)} failures")
print("=" * 72)
for f in failures:
    print("  FAIL:", f)
sys.exit(1 if failures else 0)
