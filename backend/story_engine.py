"""
Doodle Quest - the quest arc.

A "challenge" is one math problem. A QUEST is 5-8 of them chained so that
what the child collects in beat 2 is spent in beat 3, and beat 5 is only
reachable because beat 3 built the bridge. That causal chain - plus per-beat
camera and tint - is what turns a worksheet into an adventure.

Narrative shape (Freytag, shrunk to kid size):

    0 setup       easy, guaranteed win, establishes the goal
    1 gather      collect a resource
    2 gather      collect a second resource        (optional - dropped if breezing)
    3 obstacle    SPEND what was gathered
    4 setback     EASIER math, higher stakes, darker sky
    5 climax      the hardest problem of the quest
    6 resolution  cannot fail - every quest ends in a win

Adaptation:
  * two wrong in a row  -> a HELPER beat is spliced in. A friend arrives to
    help. Easier math. Framed as company, never as failure. Arc grows to 8.
  * three right in a row -> the next optional beat is dropped. Arc tightens
    to 5 and the level goes up.
  * the resolution beat ALWAYS runs.

Everything in this file is plain Python. No LLM call is required for any of
the narration - prompts.py may embellish it, but never needs to.

V2: the arc above is now a JOURNEY to a named destination. The beat list can
come from two places:

  * an AI outline written for this child's character, world and typed
    destination (prompts.generate_storyline), fused onto JOURNEY_SKELETON so
    the model only ever supplies words; or
  * one of the authored templates below, which is what runs whenever the AI
    is unavailable, slow or malformed.

Both produce the same shape, so adaptation, the unfailable resolution beat
and the offline game are unchanged either way.
"""

import random
import string

import math_engine

# The richest arc (ages 9-10, speed/distance/time) is nine stops, and a
# struggling child can have helper beats spliced in on top of that.
MAX_BEATS = 11
MIN_BEATS = 5

FRIENDS = [
    ("Pip", "a field-mouse in a acorn-cap"),
    ("Juniper", "a very round hedgehog"),
    ("Tam", "a heron with a crooked beak"),
    ("Willow", "a mossy little turtle"),
    ("Sprout", "a frog who talks too fast"),
]

# Which mechanic each archetype plays as. story_engine asks for a MECHANIC;
# math_engine owns the actual math.
#
# DERIVED, not hand-written. After the 2026-09-19b rewrite there are only
# three mechanics left - the questions are straight multiple choice or free
# response, and the fun lives in the obstacle the answer unlocks. Deriving
# the table means a new archetype can never be added to math_engine and then
# silently have no mechanic here (which used to crash `_pick_archetype`).
#
#   "pick"    multiple choice, tapped from the answer tray
#   "type"    free response, typed
#   "group"   the one surviving visual mechanic (math_engine._k_frac_sandbars)
#   "finale"  the built-from-your-haul last gate


def _mechanic_of(name: str) -> str:
    if name in math_engine.FREE_RESPONSE_ARCHETYPES:
        return "type"
    if name in math_engine.GROUP_ARCHETYPES:
        return "group"
    return "pick"


MECHANIC_OF = {
    name: _mechanic_of(name)
    for names in math_engine.ARCHETYPE_INDEX.values() for name in names
}
MECHANIC_OF["final_gate"] = "finale"

# What each archetype is ABOUT. The beat narration names a place and a stake;
# the noun comes from whatever challenge actually got generated, so the story
# never promises planks and then hands the child a berry sum. Also derived -
# one noun per TOPIC, because after the rewrite an archetype's subject is a
# property of its topic and nothing else.
TOPIC_SUBJECT = {
    "counting_and_comparing": "pebbles",
    "addition": "berries",
    "subtraction": "lanterns",
    "time_and_money": "coins",
    "multiplication": "apples",
    "fractions": "moon-shards",
    "decimals": "raindrops",
    "geometry": "fence posts",
    "algebra": "runes",
    "speed_distance_time": "miles",
}

ARCHETYPE_SUBJECT = {
    name: TOPIC_SUBJECT.get(topic, "treasures")
    for topic, names in math_engine.ARCHETYPE_INDEX.items() for name in names
}
ARCHETYPE_SUBJECT["final_gate"] = "treasures"

# Subjects collapse onto four carryable slots so the quest can spend later
# what it gathered earlier, whatever the topic happened to be about.
SUBJECT_SLOT = {
    "berries": "berries", "apples": "berries", "seeds": "berries",
    "acorns": "berries", "pebbles": "berries",
    "planks": "planks", "rope": "planks", "tiles": "planks",
    "fence posts": "planks", "stepping stones": "planks",
    "lanterns": "lanterns", "moon-shards": "lanterns", "lily pads": "lanterns",
    "keys": "lanterns", "runes": "lanterns",
    "gems": "gems", "raindrops": "gems", "miles": "gems",
    "raft-marks": "gems", "coins": "gems", "tally marks": "gems",
    "treasures": "gems",
}

# Every subject a topic can produce must land in one of the four carry slots,
# or a quest would gather something it can never spend.
assert set(TOPIC_SUBJECT.values()) <= set(SUBJECT_SLOT), \
    "a topic subject has no carry slot"


def subject_of(challenge: dict) -> str:
    return ARCHETYPE_SUBJECT.get(challenge.get("archetype"), "treasures")


def slot_of(subject: str, default: str = "berries") -> str:
    return SUBJECT_SLOT.get(subject, default)


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


def _fmt(text: str, **kw) -> str:
    try:
        return string.Formatter().vformat(text or "", (), _SafeDict(**kw))
    except Exception:
        return text or ""


# ===================================================== authored templates

RIVER_CROSSING = {
    "id": "river_crossing",
    "title": "The Long Way Home",
    "settings": {"river", "lake", "ocean", "sea", "bridge", "island", "cliff"},
    "opening": ("{char} went out at dawn, and now the {setting} has risen right "
                "across the way home. There is only one way back: through it."),
    "epilogue": ("{char} crossed the {setting} and came out the far side with "
                 "{haul}. That is a story worth telling. The end... for now."),
    "beats": [
        {
            "key": "setup", "kind": "setup", "title": "Morning on the Near Bank",
            "camera": "wide", "tint": "day", "level_delta": -1,
            "mechanic": ["pick", "collect"],
            "intro": ("{char} stands where the grass stops and the water starts. "
                      "Home is a smudge of chimney smoke on the far side. "
                      "Shallow water first - an easy one, to find your feet."),
            "on_success": ("Solid ground. {char} bounces once, just to be sure, "
                           "and looks out at the deep part."),
        },
        {
            "key": "berries", "kind": "gather", "title": "Berry Hollow",
            "camera": "left_bank", "tint": "day", "level_delta": 0,
            "mechanic": ["collect", "set", "pick"], "gain": "berries",
            "intro": ("A crossing takes strength, and strength takes breakfast. "
                      "The hollow is full of things worth carrying - if {char} can "
                      "work out how many."),
            "on_success": ("{gain} {subject} gathered up. {char}'s stomach stops "
                           "complaining, mostly."),
        },
        {
            "key": "planks", "kind": "gather", "title": "The Fallen Boathouse",
            "camera": "left_bank", "tint": "day", "level_delta": 0,
            "mechanic": ["pick", "set"], "gain": "planks", "optional": True,
            "intro": ("The old boathouse gave up years ago, but there is still good "
                      "salvage in the wreck. {char} will need every bit of it "
                      "further out."),
            "on_success": ("{gain} {subject} hauled down to the shore. Heavy. "
                           "Worth it."),
        },
        {
            "key": "bridge", "kind": "obstacle", "title": "The Broken Span",
            "camera": "midstream", "tint": "day", "level_delta": 0,
            "mechanic": ["set", "walk", "pick"], "spend": "planks",
            "intro": ("Out in the middle, the old bridge simply stops. {char} lays "
                      "all {carried} salvaged pieces across the gap - and the "
                      "river-gate at the end still wants paying."),
            "intro_alt": ("Out in the middle, the old bridge simply stops. Nothing "
                          "left to lay across it, so {char} will have to talk the "
                          "river-gate open instead."),
            "on_success": ("The gate groans wide. Boards creak, hold, and {char} is "
                           "standing over deep water for the first time."),
        },
        {
            "key": "storm", "kind": "setback", "title": "Storm Over the Water",
            "camera": "midstream", "tint": "dusk", "level_delta": -1,
            "mechanic": ["pick", "collect"], "gain": "lanterns", "optional": True,
            "intro": ("The sky turns the colour of a bruise. Rain comes sideways and "
                      "the light goes out of the world. Don't rush - {char} just "
                      "needs enough light to see by."),
            "on_success": ("{gain} {subject} salvaged from the downpour. The storm "
                           "can howl - {char} can see the far bank now."),
        },
        {
            "key": "channel", "kind": "climax", "title": "The Deep Channel",
            "camera": "far_bank", "tint": "dusk", "level_delta": 1,
            "mechanic": ["group", "walk", "collect", "pick"],
            "intro": ("One stretch left, and it is the deep one. The current shoves. "
                      "This is the hardest thing {char} has done all day - and "
                      "everything before it was practice."),
            "on_success": ("{char} scrambles up the far bank, soaked to the ears, "
                           "laughing like a drain."),
        },
        {
            "key": "home", "kind": "resolution", "title": "Home Before Dark",
            "camera": "far_bank", "tint": "night", "level_delta": -2,
            "mechanic": ["pick", "collect"], "no_fail": True,
            "intro": ("Warm yellow windows through the trees. One last little step "
                      "and {char} is home. You cannot get this one wrong."),
            "on_success": ("The door bangs open, someone shouts {char}'s name, and "
                           "the whole house comes running. You made it."),
        },
    ],
}

LANTERN_FESTIVAL = {
    "id": "lantern_festival",
    "title": "The Night of a Hundred Lanterns",
    "settings": {"forest", "meadow", "field", "garden", "tree", "mountain",
                 "house", "castle", "cave", "road", "desert", "volcano",
                 "cloud", "space"},
    "opening": ("Tonight the {setting} lights its lanterns - and {char} is the only "
                "one who can get the big one up the hill in time."),
    "epilogue": ("A hundred lanterns over the {setting}, and the biggest one is "
                 "{char}'s. {haul} to show for it, and one hill climbed. "
                 "Not a bad night's work."),
    "beats": [
        {
            "key": "gate", "kind": "setup", "title": "The Orchard Gate",
            "camera": "wide", "tint": "day", "level_delta": -1,
            "mechanic": ["pick", "collect"],
            "intro": ("The festival starts at moonrise and the orchard gate is shut. "
                      "It only opens for someone who can answer its little riddle. "
                      "Nice and easy, to begin."),
            "on_success": ("The latch lifts itself. {char} walks into rows and rows "
                           "of gold-leaved trees."),
        },
        {
            "key": "apples", "kind": "gather", "title": "The Golden Orchard",
            "camera": "left_bank", "tint": "day", "level_delta": 0,
            "mechanic": ["collect", "group", "pick"], "gain": "berries",
            "intro": ("Lantern-carriers get paid from the orchard, and {char} intends "
                      "to be paid well. Work the rows. Miss nothing."),
            "on_success": ("{gain} {subject}. {char}'s pockets are a disgrace."),
        },
        {
            "key": "shed", "kind": "gather", "title": "The Keeper's Shed",
            "camera": "left_bank", "tint": "day", "level_delta": 0,
            "mechanic": ["pick", "set"], "gain": "lanterns", "optional": True,
            "intro": ("The old keeper will hand over her whole stock - but only if "
                      "{char} can count it the way she counts it."),
            "on_success": ("{gain} {subject}, tucked under one arm. She nods. High "
                           "praise, from her."),
        },
        {
            "key": "thicket", "kind": "obstacle", "title": "The Tangled Thicket",
            "camera": "midstream", "tint": "day", "level_delta": 0,
            "mechanic": ["walk", "set", "pick"], "spend": "lanterns",
            "intro": ("Between the orchard and the hill is a thicket that has eaten "
                      "better travellers. {char} burns {carried} of the stock for "
                      "light and hunts for the one safe way through."),
            "intro_alt": ("Between the orchard and the hill is a thicket that has "
                          "eaten better travellers, and {char} has no light left. "
                          "By feel, then. Carefully."),
            "on_success": ("Branches snag, then give. {char} pops out the far side "
                           "with leaves in both ears."),
        },
        {
            "key": "wind", "kind": "setback", "title": "The Wind Takes One",
            "camera": "midstream", "tint": "dusk", "level_delta": -1,
            "mechanic": ["pick", "collect"], "gain": "lanterns", "optional": True,
            "intro": ("A gust snatches the big lantern and bowls it down the slope. "
                      "{char} chases it. Breathe - this one is gentler than it looks."),
            "on_success": ("Caught it. A little dented, still beautiful, and "
                           "{gain} {subject} rescued from the hedge as well."),
        },
        {
            "key": "beacon", "kind": "climax", "title": "The Hilltop Beacon",
            "camera": "high", "tint": "night", "level_delta": 1,
            "mechanic": ["group", "collect", "walk", "pick"],
            "intro": ("The beacon at the top has been dark for a hundred years and it "
                      "does not light for just anybody. Hardest puzzle of the night, "
                      "{char}. You are ready for it."),
            "on_success": ("Flame catches. The whole hilltop goes gold, and far below, "
                           "the {setting} gasps."),
        },
        {
            "key": "festival", "kind": "resolution", "title": "A Hundred Lanterns",
            "camera": "high", "tint": "night", "level_delta": -2,
            "mechanic": ["pick", "collect"], "no_fail": True,
            "intro": ("One lantern left to send up: {char}'s own. Let it go gently. "
                      "There is no wrong way to do this bit."),
            "on_success": ("Up it goes, wobbling, then steady, then a star among "
                           "stars. Everybody below is looking at {char}'s."),
        },
    ],
}

GENERIC_TRAIL = {
    "id": "generic_trail",
    "title": "The {Setting} Trail",
    "settings": set(),
    "opening": ("{char} sets out along the {setting} trail. Nobody has told {char} "
                "where it ends, which is rather the point."),
    "epilogue": ("The {setting} trail ends where {char} started, except {char} is "
                 "not the same: {haul}, and one very good day."),
    "beats": [
        {
            "key": "start", "kind": "setup", "title": "The Trailhead",
            "camera": "wide", "tint": "day", "level_delta": -1,
            "mechanic": ["pick", "collect"],
            "intro": ("A signpost at the edge of the {setting} points somewhere "
                      "interesting. {char} reads it, shrugs, and goes. First step "
                      "is an easy one."),
            "on_success": "The path opens up. {char} walks on, whistling.",
        },
        {
            "key": "gather1", "kind": "gather", "title": "The Treasure Patch",
            "camera": "left_bank", "tint": "day", "level_delta": 0,
            "mechanic": ["collect", "set", "pick"], "gain": "berries",
            "intro": ("Something is scattered all through the grass here, and {char} "
                      "is not the sort to walk past it."),
            "on_success": "{gain} {subject} pocketed. Heavier, happier.",
        },
        {
            "key": "gather2", "kind": "gather", "title": "The Old Camp",
            "camera": "left_bank", "tint": "day", "level_delta": 0,
            "mechanic": ["pick", "set"], "gain": "planks", "optional": True,
            "intro": ("Someone camped here long ago and left useful things behind. "
                      "{char} works out what is worth carrying."),
            "on_success": "{gain} {subject}, bundled up and slung on {char}'s back.",
        },
        {
            "key": "block", "kind": "obstacle", "title": "The Way Is Shut",
            "camera": "midstream", "tint": "day", "level_delta": 0,
            "mechanic": ["walk", "set", "pick"], "spend": "planks",
            "intro": ("The trail stops dead at something large and unhelpful. {char} "
                      "spreads out all {carried} pieces from the old camp and looks "
                      "for the trick of it."),
            "intro_alt": ("The trail stops dead at something large and unhelpful, and "
                          "{char} has nothing left to build with. Wits, then."),
            "on_success": "It gives way. {char} steps through, grinning.",
        },
        {
            "key": "trouble", "kind": "setback", "title": "The Sky Turns",
            "camera": "midstream", "tint": "dusk", "level_delta": -1,
            "mechanic": ["pick", "collect"], "gain": "lanterns", "optional": True,
            "intro": ("The light goes strange and the {setting} gets loud. Not the "
                      "moment to panic - just a small, careful job to do."),
            "on_success": "{gain} {subject} to see by. {char} keeps going.",
        },
        {
            "key": "peak", "kind": "climax", "title": "The Last Climb",
            "camera": "high", "tint": "night", "level_delta": 1,
            "mechanic": ["group", "collect", "walk", "pick"],
            "intro": ("Whatever the trail was leading to, it is right here, and it is "
                      "not going to be easy. Everything {char} learned today, all at "
                      "once."),
            "on_success": "{char} does it. Stands there a moment. Wow.",
        },
        {
            "key": "end", "kind": "resolution", "title": "The Way Back",
            "camera": "far_bank", "tint": "night", "level_delta": -2,
            "mechanic": ["pick", "collect"], "no_fail": True,
            "intro": ("Downhill all the way now, stars out, nothing left to prove. "
                      "One last gentle step. You cannot get this wrong."),
            "on_success": "{char} gets home just as the last light goes. Perfect day.",
        },
    ],
}

TEMPLATES = [RIVER_CROSSING, LANTERN_FESTIVAL, GENERIC_TRAIL]

# ====================================== the journey skeleton
#
# The AI writes the STORY. This module keeps the MACHINE. Every entry below
# is the mechanical half of one stop - difficulty delta, which mechanic,
# what is gathered or spent, whether it may be dropped, whether it may be
# failed - and `role` is the only thing the model ever sees.
#
# Consequence: an AI-written quest and an authored one are byte-identical in
# shape, so adaptation (helper splicing, optional dropping, the unfailable
# resolution) behaves the same on both paths and the offline game never
# regresses.
#
# Two things the playtest demanded:
#
#   OBSTACLES   Every stop ENDS at something the hero cannot pass, and the
#               question is the key that unlocks it. Which obstacle each stop
#               gets is NOT pinned here - `assign_obstacles` hands them out
#               across the whole arc so the drama escalates and the same one
#               never appears twice running. (V3 pinned a "traversal" per
#               slot and played it BEFORE the question; that read as
#               busywork and is gone.)
#
#   INTERLUDES  Stops with `no_question: True`. Pure story: no arithmetic at
#               all, just the crossing. These exist because "the story is
#               told through a series of questions" was a loud complaint.
#
# `min_complexity` gates a stop on how hard the quest should be (see
# `complexity_for`), which is how a speed/distance/time quest for a
# nine-year-old ends up with more locations and more plot turns than an
# addition quest for a four-year-old.

FULL_SKELETON = [
    {
        "slot": 0,
        "key": "setup", "kind": "setup", "camera": "wide", "tint": "day",
        "level_delta": -1, "mechanic": ["pick"],
        "min_complexity": 0,
        "fallback_title": "Setting Out",
        "role": ("SETTING OUT. The very first step of the journey, at the starting "
                 "point, with the destination visible far away. Easy and "
                 "reassuring - a guaranteed win that names the goal."),
    },
    {
        "slot": 1,
        "key": "crossing", "kind": "interlude", "camera": "left_bank", "tint": "day",
        "level_delta": 0, "mechanic": [], "no_question": True,
        "min_complexity": 0,
        "fallback_title": "The Stepping Stones",
        "role": ("A WORDLESS CROSSING. No puzzle here at all - the traveller "
                 "simply has to get across something: stepping stones, a "
                 "fallen log, a line of rocks. Describe the crossing itself."),
    },
    {
        "slot": 2,
        "key": "gather1", "kind": "gather", "camera": "left_bank", "tint": "day",
        "level_delta": 0, "mechanic": ["pick", "group"], "gain": "berries",
        "min_complexity": 0,
        "fallback_title": "The Gathering Place",
        "role": ("GATHER. A stop a little way along where something useful is "
                 "collected - it will be needed further on. Mention picking things up."),
    },
    {
        "slot": 3,
        "key": "gather2", "kind": "gather", "camera": "left_bank", "tint": "day",
        "level_delta": 0, "mechanic": ["type", "pick"], "gain": "planks",
        "optional": True, "min_complexity": 2,
        "fallback_title": "The Second Find",
        "role": ("GATHER again, somewhere different and further on - a second kind "
                 "of useful thing, materials rather than food."),
    },
    {
        "slot": 4,
        "key": "obstacle", "kind": "obstacle", "camera": "midstream", "tint": "day",
        "level_delta": 0, "mechanic": ["type", "pick"],
        "spend": "planks", "min_complexity": 0,
        "fallback_title": "The Way Is Blocked",
        "role": ("OBSTACLE, roughly halfway. Something blocks the route to the "
                 "destination and the gathered materials get USED UP getting past it."),
    },
    {
        "slot": 5,
        "key": "ledge", "kind": "interlude", "camera": "midstream", "tint": "dusk",
        "level_delta": 0, "mechanic": [], "no_question": True,
        "min_complexity": 3,
        "fallback_title": "The Cliff Ledges",
        "role": ("ANOTHER WORDLESS STRETCH, harder than the first and later in "
                 "the journey: a climb, a scramble, a narrow ledge. Still no "
                 "puzzle - just the effort of getting up and over."),
    },
    {
        "slot": 6,
        "key": "setback", "kind": "setback", "camera": "midstream", "tint": "dusk",
        "level_delta": -1, "mechanic": ["pick"], "gain": "lanterns",
        "optional": True, "min_complexity": 1,
        "fallback_title": "The Light Goes",
        "role": ("SETBACK. The weather or the light turns and the destination is "
                 "briefly hard to see. Tense but never frightening, and end on hope."),
    },
    {
        "slot": 7,
        "key": "climax", "kind": "climax", "camera": "high", "tint": "night",
        "level_delta": 1, "mechanic": ["type", "group", "pick"],
        "finale": True, "min_complexity": 0,
        "fallback_title": "The Last Hard Part",
        "role": ("CLIMAX. The final hard stretch, right at the foot of the "
                 "destination. The biggest challenge of the whole journey, and "
                 "it uses everything gathered along the way."),
    },
    {
        "slot": 8,
        "key": "arrival", "kind": "resolution", "camera": "far_bank", "tint": "night",
        "level_delta": -2, "mechanic": ["pick"], "no_fail": True,
        "min_complexity": 0,
        "fallback_title": "Arriving",
        "role": ("ARRIVAL. They REACH the destination. Gentle, joyful, and "
                 "impossible to get wrong - say so warmly."),
    },
]

# The slots that carry a question, in order. The three authored templates
# below predate interludes and hold exactly this many prose entries, so they
# map onto these slots positionally.
QUESTION_SLOTS = [s["slot"] for s in FULL_SKELETON if not s.get("no_question")]

# The model is always shown the FULL skeleton, whatever arc the child ends up
# with, so an outline written before the topic was chosen always has enough
# beats in it. `build_skeleton` then selects the stops this quest actually
# uses and picks the prose out by slot.
JOURNEY_SKELETON = FULL_SKELETON

# The authored templates are now PROSE PACKS, not beat lists: the machinery
# lives in FULL_SKELETON and only the words come from the template. Their
# seven authored beats line up with the seven question slots in order, which
# is how every word already written for this game survived the V3 rewrite.
for _tpl in TEMPLATES:
    _tpl["prose"] = {
        slot: {k: b.get(k, "") for k in ("title", "intro", "intro_alt", "on_success")}
        for slot, b in zip(QUESTION_SLOTS, _tpl["beats"])
    }

# ------------------------------------------------------------- complexity
#
# "A speed/distance/time quest for ages 9-10 should have a materially more
# complex storyline than an addition quest for ages 4-6." Complexity is a
# 0-5 score from the age band plus the topic, and it drives THREE things:
# how many stops the arc has, how long each traversal is, and which
# vocabulary register the offline prose uses.

BAND_COMPLEXITY = {"k1": 0, "23": 1, "45": 2}
TOPIC_COMPLEXITY = {
    "counting_and_comparing": 0,
    "addition": 0,
    "subtraction": 0,
    "time_and_money": 1,
    "geometry": 1,
    "multiplication": 1,
    "fractions": 2,
    "decimals": 2,
    "algebra": 2,
    "speed_distance_time": 3,
}


def complexity_for(band: str, topic: str) -> int:
    """0 (ages 4-6 doing addition) .. 5 (ages 9-10 doing speed/distance)."""
    return (BAND_COMPLEXITY.get(band, 1)
            + TOPIC_COMPLEXITY.get(topic, 1))


def register_for(complexity: int) -> str:
    """Which vocabulary the offline prose should speak in."""
    return "simple" if complexity <= 1 else "rich"


def traversal_steps(complexity: int, is_interlude: bool) -> int:
    """DEPRECATED. Obstacle length now comes from the obstacle itself.

    Kept because it is a clean way to express "how much crossing should this
    quest have", and `obstacle_for` still nudges by complexity.
    """
    base = 2 + complexity // 2
    if is_interlude:
        base += 1
    return max(2, min(6, base))


def build_skeleton(band: str, topic: str) -> list[dict]:
    """The mechanical arc for THIS child, THIS topic.

    Returns a subsequence of FULL_SKELETON - same dicts, same order, same
    `slot` numbers - so prose written against the full skeleton still lines
    up however many stops got dropped. Obstacles are NOT assigned here: they
    depend on where a stop lands in the FINAL arc, which is only known once
    the optional stops have been dropped. See `assign_obstacles`.
    """
    c = complexity_for(band, topic)
    out = []
    for skel in FULL_SKELETON:
        if c < skel.get("min_complexity", 0):
            continue
        beat = {k: v for k, v in skel.items()
                if k not in ("role", "fallback_title")}
        beat["complexity"] = c
        out.append(beat)
    return out


# Prose for the interlude stops - the beats with NO MATHS IN THEM. The
# obstacle supplies its own locked/cleared lines; this is the story wrapper
# around them, in the two vocabulary registers. Keyed by OBSTACLE kind now,
# with a generic pair so a kind without bespoke prose still reads well.
INTERLUDE_PROSE = {
    ("stones", "simple"): {
        "title": "The Stepping Stones",
        "intro": "Flat stones cross the water, one hop apart. Off you go, {char}!",
        "on_success": "{char} lands on the far side with dry paws. Easy.",
    },
    ("stones", "rich"): {
        "title": "The Stepping Stones",
        "intro": ("The water runs fast and cold here, but someone long ago laid "
                  "flat stones across it. {char} judges the gaps, and jumps."),
        "on_success": ("The last stone wobbles, holds, and {char} is across - "
                       "breathing hard, entirely pleased."),
    },
    ("ledges", "simple"): {
        "title": "The Ledges",
        "intro": "Little shelves of rock go up like stairs. Climb them, {char}!",
        "on_success": "Up and up, and there {char} is at the top.",
    },
    ("ledges", "rich"): {
        "title": "The Cliff Ledges",
        "intro": ("The cliff is not smooth after all - it is a staircase of narrow "
                  "ledges, each one a stretch above the last. {char} reaches up."),
        "on_success": ("{char} hauls over the final lip and lies flat for a "
                       "moment, looking at how far down the world has gone."),
    },
    ("hurdle", "simple"): {
        "title": "The Fallen Logs",
        "intro": "Big logs lie across the path. Jump them, {char}!",
        "on_success": "Over they go, one after another. Nothing to it.",
    },
    ("hurdle", "rich"): {
        "title": "The Fallen Logs",
        "intro": ("Storm-felled trunks lie square across the way, too high to "
                  "step over and too long to walk around."),
        "on_success": ("Up and over, up and over, and {char} comes out the far "
                       "side with bark on both knees."),
    },
    ("ford", "simple"): {
        "title": "The Shallow Crossing",
        "intro": "The water is not deep, but it is quick. In you go, {char}!",
        "on_success": "Wet to the middle and grinning about it.",
    },
    ("ford", "rich"): {
        "title": "The Rushing Ford",
        "intro": ("Shallow enough to wade, fast enough to take {char}'s feet "
                  "out from under them. One careful step at a time."),
        "on_success": ("{char} climbs out the far bank, soaked through, and "
                       "shakes like a dog."),
    },
    ("chasm", "simple"): {
        "title": "The Big Gap",
        "intro": "The ground stops! There is a gap. Get a run-up, {char}!",
        "on_success": "WHOOSH. {char} lands on the other side.",
    },
    ("chasm", "rich"): {
        "title": "The Chasm",
        "intro": ("The ground simply stops, and starts again a long way over "
                  "there. {char} backs up to get a run at it."),
        "on_success": ("One enormous heartbeat in the air - and {char} lands "
                       "rolling on the far side, entirely alive."),
    },
    ("generic", "simple"): {
        "title": "The Crossing",
        "intro": "Something is in the way. Over you go, {char}!",
        "on_success": "Across, and on we go.",
    },
    ("generic", "rich"): {
        "title": "The Crossing",
        "intro": ("The way on is blocked, and there is nothing for it but to "
                  "get across. {char} sizes it up."),
        "on_success": ("Across. {char} looks back at it once, pleased, and "
                       "keeps walking."),
    },
}

# ===================================================== OBSTACLES (2026-09-19b)
#
# The playtest, verbatim:
#
#   "dont make it like a trail u always need to go thru, do trails, walks,
#    maybe like hurdles??"
#   "make it like to jump over the cliff she needs to answer this question!!
#    thats fun"
#
# THE QUESTION IS THE KEY. The hero arrives at something they physically
# cannot pass - a chasm, a cliff, a rushing ford, a collapsed bridge - and
# stops. The question is what unlocks it. Answer correctly and the child
# PERFORMS the crossing on the keyboard: run up and leap, haul up the ledges,
# swing across on the rope. Answer wrong and nothing bad happens at all - the
# obstacle simply stays shut and they try again.
#
# What this replaces: "Set off the 4 waymarks - follow the trail with the
# arrow keys", which was four grey blobs in a line, identical every beat,
# played BEFORE the question rather than won by it.
#
# `drama` (1-5) is what makes a quest escalate. `assign_obstacles` walks the
# arc and hands out rising drama, never the same obstacle twice in a row, and
# always the biggest thing we have at the climax.

OBSTACLES = {
    # ---------------------------------------------------------- WATER
    "stones": {
        "toll": "Pay the stone-keeper",
        "terrain": ("water",), "action": "hop", "drama": 1, "visual": "stones",
        "title": "The Stepping Stones", "noun": "stepping stone",
        "blocked": "Fast water, and a line of flat stones across it. {char} "
                   "tests the first one with a toe.",
        "cleared": "Stone to stone to stone - and {char} is across, with dry "
                   "paws and a very smug face.",
        "hint": "Walk onto each stone with the arrow keys",
        "keys": ["←", "↑", "↓", "→"], "steps": 4,
    },
    "ford": {
        "toll": "Pay the ferry-keeper",
        "terrain": ("water",), "action": "ford", "drama": 2, "visual": "water",
        "title": "The Rushing Ford", "noun": "step",
        "blocked": "Shallow enough to wade, and quick enough to take {char}'s "
                   "feet out from under them.",
        "cleared": "Soaked to the middle, but across. {char} shakes like a dog.",
        "hint": "Hold → to push against the current",
        "keys": ["→"], "steps": 5,
    },
    "boat": {
        "toll": "Pay for the boat",
        "terrain": ("water",), "action": "pole", "drama": 3, "visual": "boat",
        "title": "The Little Boat", "noun": "push",
        "blocked": "Too deep to wade and too wide to jump - but there is a "
                   "flat-bottomed boat pulled up in the reeds, and a pole.",
        "cleared": "{char} poles out, wobbles horribly, and grounds the boat "
                   "on the far bank with a crunch.",
        "hint": "Press SPACE to push off the bottom, → to steer",
        "keys": ["SPACE", "→"], "steps": 4,
    },
    "log_cross": {
        "toll": "Pay the log-toll",
        "terrain": ("water", "wood"), "action": "balance", "drama": 2,
        "visual": "logcross", "title": "The Fallen Log", "noun": "step",
        "blocked": "One old trunk lies right across the water, slick with "
                   "moss and no wider than {char}'s two feet together.",
        "cleared": "Arms out, one foot in front of the other, and over. "
                   "{char} does not look down once.",
        "hint": "Step along the log with → (↑ ↓ to keep your balance)",
        "keys": ["→", "↑", "↓"], "steps": 4,
    },
    "bridge": {
        "toll": "Pay for the planks",
        "terrain": ("water", "height"), "action": "rebuild", "drama": 3,
        "visual": "bridge", "title": "The Collapsed Bridge", "noun": "plank",
        "blocked": "The bridge is a row of empty posts and a long drop. The "
                   "planks are all here - just not where they should be.",
        "cleared": "Plank by plank it comes back together, and {char} walks "
                   "across a bridge that was not there a minute ago.",
        "hint": "Press SPACE to lay each plank, then → to cross",
        "keys": ["SPACE", "→"], "steps": 4,
    },
    # --------------------------------------------------------- HEIGHT
    "ledges": {
        "toll": "Pay for the climbing rope",
        "terrain": ("height", "coast", "cave"), "action": "climb", "drama": 3,
        "visual": "ledges", "title": "The Cliff Ledges", "noun": "ledge",
        "blocked": "The cliff goes up and up. There are ledges - narrow ones, "
                   "a good stretch apart - and no other way on.",
        "cleared": "{char} hauls over the last lip and lies flat a moment, "
                   "looking at how far down the world has gone.",
        "hint": "Press ↑ to pull up to the next ledge",
        "keys": ["↑"], "steps": 4,
    },
    "rope_bridge": {
        "toll": "Pay the bridge-keeper",
        "terrain": ("height",), "action": "cross", "drama": 4,
        "visual": "ropebridge", "title": "The Swaying Rope Bridge",
        "noun": "board",
        "blocked": "Two ropes, a handful of boards, and an awful lot of air "
                   "underneath. The whole thing moves in the wind.",
        "cleared": "{char} steps off onto solid rock and lets go of the rope "
                   "one finger at a time.",
        "hint": "Press → for each board (↑ ↓ to steady yourself)",
        "keys": ["→", "↑", "↓"], "steps": 5,
    },
    "chasm": {
        "toll": "Pay the leap-toll",
        "terrain": ("height", "arid"), "action": "leap", "drama": 5,
        "visual": "gap", "title": "The Chasm", "noun": "leap",
        "blocked": "The ground simply stops. A chasm, wider than {char} is "
                   "brave, and the far edge waiting on the other side.",
        "cleared": "{char} hangs in the air for one enormous heartbeat - and "
                   "lands, rolling, on the far side.",
        "hint": "Hold → to run up, then SPACE to LEAP",
        "keys": ["→", "SPACE"], "steps": 2,
    },
    # ----------------------------------------------------------- WOOD
    "hurdle": {
        "toll": "Pay the woodcutter",
        "terrain": ("wood", "open"), "action": "hurdle", "drama": 2,
        "visual": "logs", "title": "The Fallen Logs", "noun": "log",
        "blocked": "Storm-felled trunks lie across the path, one after "
                   "another, each too high to step over.",
        "cleared": "Up and over, up and over. {char} lands running.",
        "hint": "Run with →, then SPACE to vault each log",
        "keys": ["→", "SPACE"], "steps": 3,
    },
    "branches": {
        "toll": "Pay the branch-toll",
        "terrain": ("wood",), "action": "duck", "drama": 2, "visual": "branches",
        "title": "The Low Branches", "noun": "branch",
        "blocked": "The way on is a tunnel of branches, every one of them at "
                   "exactly {char}'s eye height.",
        "cleared": "{char} comes out the far end with leaves in both ears and "
                   "an entirely straight face.",
        "hint": "Press ↓ to duck under each branch, → to go on",
        "keys": ["↓", "→"], "steps": 4,
    },
    "vine": {
        "toll": "Pay for the vine",
        "terrain": ("wood",), "action": "swing", "drama": 4, "visual": "vine",
        "title": "The Hanging Vine", "noun": "swing",
        "blocked": "A single vine hangs over the gap, swinging gently, well "
                   "out of reach of anyone standing still.",
        "cleared": "{char} sails across with both feet out, whooping, and "
                   "lands in a heap of leaves.",
        "hint": "Press SPACE to grab the vine, → to swing across",
        "keys": ["SPACE", "→"], "steps": 3,
    },
    # ---------------------------------------------------------- COAST
    "tide_pool": {
        "toll": "Pay the tide-keeper",
        "terrain": ("coast",), "action": "wade", "drama": 2, "visual": "water",
        "title": "The Tide Pool", "noun": "step",
        "blocked": "A wide cold pool left behind by the tide, full of things "
                   "with more legs than {char} is comfortable with.",
        "cleared": "Across, with wet knees and one small crab as a passenger.",
        "hint": "Wade across with the arrow keys",
        "keys": ["←", "↑", "↓", "→"], "steps": 4,
    },
    "sea_rocks": {
        "toll": "Pay the rock-pilot",
        "terrain": ("coast",), "action": "scramble", "drama": 3,
        "visual": "rocks", "title": "The Black Rocks", "noun": "rock",
        "blocked": "Great wet slabs of rock, tilted every way, with the sea "
                   "banging away underneath them.",
        "cleared": "{char} picks the last gap, jumps it, and is on sand again.",
        "hint": "Pick your way across with the arrow keys, SPACE to jump a gap",
        "keys": ["←", "↑", "↓", "→", "SPACE"], "steps": 5,
    },
    # ----------------------------------------------------------- ARID
    "dunes": {
        "toll": "Pay the sand-guide",
        "terrain": ("arid",), "action": "trudge", "drama": 2, "visual": "dunes",
        "title": "The Dunes", "noun": "dune",
        "blocked": "Sand, and then more sand, heaped into ridges that slide "
                   "backwards under every step.",
        "cleared": "{char} crests the last ridge and there, finally, is the "
                   "way on.",
        "hint": "Push up each dune with ↑ and →",
        "keys": ["↑", "→"], "steps": 5,
    },
    # ----------------------------------------------------------- CAVE
    "squeeze": {
        "toll": "Pay the cave-toll",
        "terrain": ("cave",), "action": "squeeze", "drama": 2,
        "visual": "squeeze", "title": "The Narrow Gap", "noun": "squeeze",
        "blocked": "The passage pinches down to a crack you could post a "
                   "letter through. It is the only way on.",
        "cleared": "{char} pops out the far side like a cork, covered in dust.",
        "hint": "Press → to wriggle through, ↓ to flatten yourself",
        "keys": ["→", "↓"], "steps": 4,
    },
    "under_stream": {
        "toll": "Pay the water-toll",
        "terrain": ("cave",), "action": "wade", "drama": 3,
        "visual": "water", "title": "The Underground Stream", "noun": "step",
        "blocked": "Black water runs across the cave floor, and there is no "
                   "telling how deep it goes.",
        "cleared": "Knee deep, then ankle deep, then out. Colder than {char} "
                   "expected and twice as exciting.",
        "hint": "Feel your way across with the arrow keys",
        "keys": ["←", "↑", "↓", "→"], "steps": 4,
    },
    # ----------------------------------------------------------- OPEN
    "gate": {
        "toll": "Pay the gate",
        "terrain": ("open", "wood", "height"), "action": "unlock", "drama": 2,
        "visual": "gate", "title": "The Locked Gate", "noun": "bolt",
        "blocked": "An iron gate, taller than {char}, with three heavy bolts "
                   "and not a gap anywhere in the wall beside it.",
        "cleared": "The last bolt grinds back and the gate swings wide.",
        "hint": "Press SPACE to throw each bolt, then → to walk through",
        "keys": ["SPACE", "→"], "steps": 3,
    },
    "hoops": {
        "toll": "Pay the hoop-master",
        "terrain": ("open", "arid"), "action": "hoop", "drama": 3,
        "visual": "hoops", "title": "The Ring of Hoops", "noun": "hoop",
        "blocked": "Great hoops hang in a line above the path, turning "
                   "slowly. The way on goes straight through the middle.",
        "cleared": "Through the middle of every one, clean as anything. "
                   "{char} lands to a sound like applause.",
        "hint": "Line up with ↑ ↓, then SPACE to jump through each hoop",
        "keys": ["↑", "↓", "SPACE"], "steps": 4,
    },
    "hedge": {
        "toll": "Pay the hedge-keeper",
        "terrain": ("open", "wood"), "action": "hurdle", "drama": 2,
        "visual": "hedge", "title": "The Thorn Hedge", "noun": "gap",
        "blocked": "A hedge twice {char}'s height, thorns the size of "
                   "fingernails, running as far as either eye can see.",
        "cleared": "One gap, one wriggle, one small tear in one ear. Through.",
        "hint": "Find the gaps with ↑ ↓, then → to push through",
        "keys": ["↑", "↓", "→"], "steps": 4,
    },
}


# ----------------------------------------------------- setting -> terrain
#
# 2026-09-19c, from the playtest: "i dont see the different obstacle types???
# like they should be part of river if ur in a river u should take a boat or
# jump over a pond or something like cool!!!!"
#
# Obstacles are chosen from the CHILD'S OWN WORLD, not from one generic list.
# A rope bridge belongs on a mountain; a boat belongs on a river; neither
# belongs in an open meadow. The setting comes from the drawing the child
# made (math_engine.pick_setting), so the obstacles are theirs too.

SETTING_TERRAIN = {
    "river": ("water", "wood"),
    "lake": ("water", "coast"),
    "ocean": ("coast", "water"),
    "sea": ("coast", "water"),
    "bridge": ("water", "height"),
    "island": ("coast", "water"),
    "cliff": ("height", "coast"),
    "mountain": ("height", "arid"),
    "volcano": ("height", "arid"),
    "forest": ("wood", "open"),
    "tree": ("wood", "open"),
    "garden": ("wood", "open"),
    "cave": ("cave", "height"),
    "desert": ("arid", "open"),
    "meadow": ("open", "wood"),
    "field": ("open", "wood"),
    "road": ("open", "wood"),
    "house": ("open", "wood"),
    "castle": ("open", "height"),
    "cloud": ("height", "open"),
}
DEFAULT_TERRAIN = ("open", "wood")

# Stable order so obstacle choice is deterministic given a seed.
OBSTACLE_ORDER = tuple(sorted(OBSTACLES))

# The gentlest thing we have, for any fallback.
DEFAULT_OBSTACLE = "stones"


def terrain_for_setting(setting: str, objects=()) -> tuple[str, ...]:
    """Which terrain families this child's world is made of."""
    key = (setting or "").lower().strip()
    if key in SETTING_TERRAIN:
        return SETTING_TERRAIN[key]
    for obj in objects or []:
        low = (obj or "").lower()
        for name, terrain in SETTING_TERRAIN.items():
            if name in low:
                return terrain
    return DEFAULT_TERRAIN


def obstacles_for_setting(setting: str, objects=()) -> list[str]:
    """Every obstacle that belongs in this world, best-fitting first.

    Never returns fewer than three, so `assign_obstacles` always has room to
    avoid repeating itself even in a world with a narrow terrain.
    """
    terrain = terrain_for_setting(setting, objects)
    ranked = []
    for kind in OBSTACLE_ORDER:
        tags = OBSTACLES[kind].get("terrain", ())
        for rank, want in enumerate(terrain):
            if want in tags:
                ranked.append((rank, kind))
                break
    out = [k for _r, k in sorted(ranked)]
    if len(out) < 3:
        # A world we do not recognise still gets a varied quest.
        out += [k for k in OBSTACLE_ORDER if k not in out]
    return out


def obstacle_spec(kind: str) -> dict:
    return OBSTACLES.get(kind) or OBSTACLES[DEFAULT_OBSTACLE]


def assign_obstacles(beats: list[dict], rng: random.Random,
                     setting: str = "", objects=()) -> None:
    """Hand every beat an obstacle: in-world, escalating, never twice running.

    Four rules, in order of how much they matter:
      1. every obstacle must BELONG in the child's world - no boats in a
         meadow, no rope bridges in a garden;
      2. the climax gets the most dramatic obstacle that world can offer;
      3. no two consecutive beats use the same obstacle (the old trail beat
         was the same four blobs every time, which is what made it filler);
      4. otherwise drama rises smoothly across the arc.
    """
    pool = obstacles_for_setting(setting, objects)
    n = max(1, len(beats))

    # The climax is chosen FIRST and gets the most dramatic thing this world
    # can offer. Chosen last, it kept losing the biggest obstacle to an
    # earlier beat and the quest fizzled at the top.
    climax = next((b for b in beats if b.get("finale")), None)
    reserved = None
    if climax is not None:
        reserved = max(pool, key=lambda k: (OBSTACLES[k]["drama"], -rng.random()))
        climax["obstacle"] = reserved

    previous = None
    for i, beat in enumerate(beats):
        if beat is climax:
            previous = beat["obstacle"]
            continue
        t = i / max(1, n - 1)
        target = 1 + round(t * 4)
        if beat.get("kind") == "resolution":
            target = 1              # arriving home is not an assault course
        elif beat.get("helper"):
            target = 1              # a friend just turned up; keep it gentle
        # Avoid the previous obstacle, and avoid spending the climax's
        # obstacle early - but never at the cost of having nothing to pick.
        choices = [k for k in pool if k != previous and k != reserved]
        choices = choices or [k for k in pool if k != previous] or list(pool)
        # nearest drama to the target; ties broken by the seeded rng, so the
        # same quest always plays the same obstacles.
        pick = min(choices, key=lambda k: (abs(OBSTACLES[k]["drama"] - target),
                                           rng.random()))
        beat["obstacle"] = pick
        previous = pick

    # The no-repeat rule is the one a child actually notices. Enforce it once
    # more at the end, because reserving the climax can put two of a kind
    # next to each other.
    for i in range(1, len(beats)):
        if beats[i]["obstacle"] != beats[i - 1]["obstacle"]:
            continue
        alt = [k for k in pool if k not in
               (beats[i - 1]["obstacle"],
                beats[i + 1]["obstacle"] if i + 1 < len(beats) else None)]
        if alt:
            want = OBSTACLES[beats[i]["obstacle"]]["drama"]
            beats[i]["obstacle"] = min(
                alt, key=lambda k: (abs(OBSTACLES[k]["drama"] - want), rng.random()))



# Legacy: the V3 "walk to the question" vocabulary. Kept only so an older
# frontend build still renders SOMETHING rather than nothing. New code reads
# `challenge["obstacle"]`.
TRAVERSAL_HINT = {
    "stones": "Press → to hop from stone to stone",
    "ledges": "Press ↑ to pull up to the next ledge",
    "bridge": "Press SPACE to lay each plank, then → to cross",
    "gate": "Press SPACE to throw each bolt",
    "ford": "Hold → to push against the current",
    "hurdle": "Run with →, then SPACE to vault",
    "swing": "Press SPACE to grab the rope, → to swing",
    "chasm": "Hold → to run up, then SPACE to LEAP",
}

# When no destination was typed, one is inferred from the setting the child's
# drawing implies. Deliberately concrete: "somewhere nice" is not a goal a
# progress bar can mean anything about.
DESTINATION_BY_SETTING = {
    "river": "the warm little house on the far bank of the river",
    "lake": "the island in the middle of the lake",
    "ocean": "the bright shore on the other side of the ocean",
    "sea": "the lighthouse across the sea",
    "bridge": "the far end of the great bridge",
    "island": "the hidden cove on the far side of the island",
    "cliff": "the ledge at the very top of the cliff",
    "mountain": "the snowy peak at the top of the mountain",
    "volcano": "the quiet green valley beyond the volcano",
    "forest": "the sunlit clearing at the heart of the forest",
    "tree": "the treehouse at the top of the biggest tree",
    "garden": "the secret gate at the end of the garden",
    "meadow": "the old windmill at the far end of the meadow",
    "field": "the harvest fair on the other side of the fields",
    "cave": "the glittering crystal chamber deep in the cave",
    "castle": "the tower room at the top of the castle",
    "house": "home, with the lamp lit in the window",
    "road": "the town at the end of the long road",
    "desert": "the palm oasis across the desert",
    "cloud": "the floating castle high above the clouds",
}
DEFAULT_DESTINATION = "the golden signpost at the end of the trail"

# ------------------------------------------------- the quest's own noun
#
# "if the story is about collecting rods, make it such that bun bun the main
#  bunny is getting rods and doing multiplication - the context is the same"
#
# Every word problem in the quest is written about ONE noun, so the maths and
# the story are about the same thing. The AI storyline names it ("treasure");
# with no API key it comes from the world the child drew. Either way it is
# WORDING ONLY - math_engine still owns every number and every answer.

TREASURE_BY_SETTING = {
    "river": "reeds", "lake": "river-stones", "ocean": "shells",
    "sea": "shells", "bridge": "planks", "island": "coconuts",
    "cliff": "climbing-pegs", "mountain": "climbing-pegs",
    "volcano": "fire-stones", "forest": "acorns", "tree": "acorns",
    "garden": "seeds", "cave": "crystals", "desert": "date-stones",
    "meadow": "clover-leaves", "field": "wheat-sheaves", "road": "milestones",
    "house": "candles", "castle": "keys", "cloud": "star-drops",
}
DEFAULT_TREASURE = "lanterns"


def infer_treasure(setting: str, objects=()) -> str:
    key = (setting or "").lower().strip()
    if key in TREASURE_BY_SETTING:
        return TREASURE_BY_SETTING[key]
    for obj in objects or []:
        low = (obj or "").lower()
        for name, noun in TREASURE_BY_SETTING.items():
            if name in low:
                return noun
    return DEFAULT_TREASURE


def infer_destination(setting: str, objects=()) -> str:
    """A concrete goal for a child who did not type one."""
    setting = (setting or "").lower().strip()
    if setting in DESTINATION_BY_SETTING:
        return DESTINATION_BY_SETTING[setting]
    for obj in objects or []:
        low = (obj or "").lower()
        for key, dest in DESTINATION_BY_SETTING.items():
            if key in low:
                return dest
    return DEFAULT_DESTINATION


def clean_destination(raw: str | None, setting: str, objects=()) -> str:
    """Normalise whatever the child typed. Blank => inferred from the setting."""
    text = " ".join((raw or "").split())[:120].strip(" .!")
    if len(text) < 3:
        return infer_destination(setting, objects)
    return text


# Children type destinations as instructions - "cross the river", "get to the
# top of the mountain". A progress bar needs the PLACE, not the instruction,
# or it reads "Closer to cross the river: 3 stops to go".
_GOAL_VERBS = (
    "make it back to", "make it to", "get back to", "arrive at", "travel to",
    "journey to", "return to", "go back to", "climb up to", "climb to",
    "get to", "go to", "reach", "cross", "climb", "find", "escape", "visit",
)


def goal_phrase(destination: str) -> str:
    """Turn an instruction into the noun phrase a progress bar can name."""
    text = " ".join((destination or "").split()).strip(" .!")
    if not text:
        return text
    # "cross the river AND reach the lighthouse" - the goal is the last leg.
    for joiner in (" and then ", " and "):
        if joiner in text.lower():
            tail = text[text.lower().rindex(joiner) + len(joiner):].strip()
            if any(tail.lower().startswith(v + " ") for v in _GOAL_VERBS):
                text = tail
                break
    low = text.lower()
    for verb in _GOAL_VERBS:
        if low.startswith(verb + " "):
            return text[len(verb) + 1:].strip() or text
    return text


# Visual brief per camera position, used for the journey frames whenever the
# AI did not write one (offline storyline, authored template, spliced helper).
_CAMERA_BRIEF = {
    "wide": ("the very beginning of the journey, looking out across {setting}, "
             "{goal} tiny and far away on the horizon"),
    "left_bank": ("a good way along the journey through {setting}; new scenery in "
                  "the foreground, the starting point now small and behind, "
                  "{goal} bigger than before but still ahead"),
    "midstream": ("halfway along the journey through {setting}; the first half of "
                  "the route is far behind and out of frame, {goal} now large in "
                  "the middle distance"),
    "high": ("almost at the end of the journey, high above {setting}, looking back "
             "down over everything already crossed; {goal} looming close ahead"),
    "far_bank": ("standing AT {goal} at the very end of the journey, {goal} filling "
                 "the frame, {setting} spread out far below and behind"),
}
_TINT_BRIEF = {
    "day": "bright midday sunlight, clear sky",
    "dusk": "golden low sunset light, long shadows, deepening sky",
    "night": "calm moonlit night, deep blue sky, warm little lights",
}


def default_location(beat: dict, setting: str, goal: str) -> str:
    brief = _CAMERA_BRIEF.get(beat.get("camera", "wide"), _CAMERA_BRIEF["wide"])
    brief = _fmt(brief, setting=setting, goal=goal)
    return f"{brief}. {_TINT_BRIEF.get(beat.get('tint', 'day'), _TINT_BRIEF['day'])}"

HELPER_BEAT = {
    "key": "helper", "kind": "helper", "title": "A Friend Catches Up",
    "slot": 2, "camera": "left_bank", "tint": "day", "level_delta": -2,
    "mechanic": ["pick"], "helper": True,
    "intro": ("Something comes bounding over the rise - it's {friend}, {friend_desc}! "
              "\"Budge up,\" says {friend}. \"Two heads. Let's do an easy one "
              "together first.\""),
    "on_success": ("{friend} thumps {char} on the back. \"Told you. You had it all "
                   "along.\" Off you both go."),
}


def pick_template(setting: str, rng: random.Random) -> dict:
    for tpl in TEMPLATES:
        if setting in tpl["settings"]:
            return tpl
    return GENERIC_TRAIL


# ============================================================ quest state

def new_state() -> dict:
    return {
        "berries": 0, "planks": 0, "lanterns": 0, "gems": 0,
        "rope": 0, "friend_found": False,
    }


def _fallback_prose(skel: dict, register: str) -> dict:
    """Authored prose for a stop the writer did not cover.

    Interludes have their own two-register table; everything else falls back
    to the generic trail, which has a line for every question slot.
    """
    if skel.get("no_question"):
        kind = (skel.get("traversal") or {}).get("kind", "stones")
        prose = (INTERLUDE_PROSE.get((kind, register))
                 or INTERLUDE_PROSE.get((kind, "simple"))
                 or INTERLUDE_PROSE[("stones", "simple")])
        return dict(prose)
    src = GENERIC_TRAIL["prose"].get(skel["slot"], {})
    return {"title": src.get("title") or FULL_SKELETON[skel["slot"]]["fallback_title"],
            "intro": src.get("intro", ""),
            "intro_alt": src.get("intro_alt", ""),
            "on_success": src.get("on_success", "")}


def _apply_prose(beat: dict, prose: dict) -> None:
    beat["title"] = prose.get("title") or ""
    beat["intro"] = prose.get("intro") or ""
    beat["on_success"] = prose.get("on_success") or ""
    if prose.get("intro_alt"):
        beat["intro_alt"] = prose["intro_alt"]
    if prose.get("advances"):
        beat["advances"] = prose["advances"]
    if prose.get("location"):
        beat["location"] = prose["location"]


def _beats_from_storyline(storyline: dict, skeleton: list[dict],
                          register: str) -> list[dict]:
    """Fuse AI prose onto the built skeleton. Machinery always wins.

    The model is always asked for the FULL skeleton, so `slot` indexes
    straight into what it wrote. A short or ragged outline no longer costs
    the child the whole quest - the missing stops fall back to authored
    prose one at a time.
    """
    written = storyline.get("beats") or []
    beats = []
    for skel in skeleton:
        beat = dict(skel)
        slot = skel["slot"]
        src = written[slot] if slot < len(written) and isinstance(written[slot], dict) else None
        if src and (src.get("intro") or "").strip():
            _apply_prose(beat, src)
            beat["advances"] = src.get("advances", "")
            beat["location"] = src.get("location", "")
        else:
            _apply_prose(beat, _fallback_prose(skel, register))
        beats.append(beat)
    return beats


def _beats_from_template(tpl: dict, skeleton: list[dict],
                         register: str) -> list[dict]:
    beats = []
    for skel in skeleton:
        beat = dict(skel)
        prose = tpl["prose"].get(skel["slot"]) or _fallback_prose(skel, register)
        _apply_prose(beat, prose)
        beats.append(beat)
    return beats


def start_quest(topic: str, band: str, interpretation: dict, seed=None,
                destination: str | None = None, storyline: dict | None = None) -> dict:
    """Build a quest.

    `storyline` is an AI-written outline (see prompts.generate_storyline). When
    it is None - no API key, a timeout, a malformed reply - an authored
    template is used instead and nothing else about the game changes.
    """
    rng = random.Random(seed)
    objects = interpretation.get("objects", []) or []
    char = interpretation.get("character_name") or math_engine.guess_character_name(objects)
    setting = math_engine.pick_setting(objects)
    friend, friend_desc = rng.choice(FRIENDS)
    # `goal` is what the child asked for, verbatim-ish ("cross the river and
    # reach the lighthouse"). `goal_text` is the PLACE inside it ("the
    # lighthouse"), which is what the progress bar and the frame briefs name.
    goal = clean_destination(destination, setting, objects)
    goal_noun = goal_phrase(goal)
    scenery = interpretation.get("setting") or setting

    complexity = complexity_for(band, topic)
    register = register_for(complexity)
    skeleton = build_skeleton(band, topic)

    if storyline:
        beats = _beats_from_storyline(storyline, skeleton, register)
        template_id = "ai_generated"
        title = storyline["title"]
        # The model sometimes echoes the instruction back ("cross the river")
        # rather than naming the place; the same reduction fixes both paths.
        goal_text = goal_phrase(storyline.get("goal_text") or "") or goal_noun
        treasure = (storyline.get("treasure") or "").strip()
        opening = storyline.get("opening") or (
            f"{char} is setting out for {goal_text}. It is a long way - "
            f"but every puzzle solved is a step closer.")
        epilogue_tpl = storyline.get("epilogue") or (
            "{char} made it all the way to " + goal_text + ", carrying {haul}. "
            "What a journey.")
        story_source = storyline.get("source", "openai")
    else:
        tpl = pick_template(setting, rng)
        beats = _beats_from_template(tpl, skeleton, register)
        template_id = tpl["id"]
        title = _fmt(tpl["title"], char=char, setting=setting, Setting=setting.title())
        goal_text = goal_noun
        treasure = ""
        opening = _fmt(tpl["opening"], char=char, setting=setting)
        epilogue_tpl = tpl["epilogue"]
        story_source = "template"

    # AI-written prose carries {char}/{haul} placeholders exactly like the
    # authored templates do, so the opening goes through the same formatter.
    # (Forgetting this shipped a literal "{char}" to a child once.)
    opening = _fmt(opening, char=char, setting=setting, Setting=setting.title(),
                   goal=goal_text)

    # Every beat needs a visual brief - the journey frames are painted from
    # it - and the authored templates predate the idea.
    for beat in beats:
        beat.setdefault("location", default_location(beat, scenery, goal_text))

    # ★ THE OBSTACLES. Chosen from the child's OWN world (a boat on a river,
    # ledges on a cliff), escalating across the arc, never the same twice in
    # a row. Assigned here, after the arc is built, because which obstacle a
    # stop deserves depends on where it lands in the finished quest.
    assign_obstacles(beats, rng, setting, objects)

    return {
        "template_id": template_id,
        "title": title,
        "topic": topic,
        "band": band,
        "char": char,
        "setting": setting,
        "scenery": scenery,
        "destination": goal,
        "goal_text": goal_text,
        "destination_source": "child" if (destination or "").strip() else "inferred",
        "story_source": story_source,
        # The noun every word problem in this quest is written about.
        "treasure": treasure or infer_treasure(setting, objects),
        "treasure_source": "story" if treasure else "world",
        "friend": friend,
        "friend_desc": friend_desc,
        "beats": beats,
        "index": 0,
        "state": new_state(),
        "log": [],
        "complete": False,
        "last_archetype": None,
        "helpers_added": 0,
        "optional_dropped": 0,
        "opening": opening,
        "epilogue_tpl": epilogue_tpl,
        "awaiting_answer": False,
        # V3: how ornate this quest is allowed to be, and how it talks.
        "complexity": complexity,
        "register": register,
        "interludes": sum(1 for b in beats if b.get("no_question")),
    }


_HAUL_NOUN = {"berries": "berries", "planks": "planks",
              "lanterns": "lanterns", "gems": "gems"}


def _haul(state: dict) -> str:
    """A natural-language list of what the child actually carried home.

    Never prints a zero - "carrying 0 planks" reads as a bug, not a story.
    """
    bits = [f"{state[k]} {noun}" for k, noun in _HAUL_NOUN.items() if state.get(k)]
    if not bits:
        return "nothing but muddy paws and a very good story"
    if len(bits) == 1:
        return bits[0]
    return ", ".join(bits[:-1]) + " and " + bits[-1]


def _ctx(quest: dict, **extra) -> dict:
    ctx = dict(quest["state"])
    ctx["haul"] = _haul(quest["state"])
    ctx.update({
        "char": quest["char"],
        "setting": quest["setting"],
        "Setting": quest["setting"].title(),
        "friend": quest["friend"],
        "friend_desc": quest["friend_desc"],
        "title": quest["title"],
        "goal": quest.get("goal_text") or quest.get("destination", ""),
    })
    ctx.update(extra)
    return ctx


# ------------------------------------------------------- journey progress
#
# V2 item 4: the quest is a distance, not a list. Progress is measured in
# beats ANSWERED over beats in the arc - both of which move (a helper beat
# is spliced in, an optional beat is cut), so it is always recomputed and
# never cached.

_DISTANCE_WORDS = {0: "you're here!", 1: "one last stop",
                   2: "two stops to go", 3: "three stops to go"}


def beats_done(quest: dict) -> int:
    return len(quest.get("log", []))


def journey_progress(quest: dict) -> float:
    total = max(1, len(quest.get("beats", [])))
    if quest.get("complete"):
        return 1.0
    return round(min(1.0, beats_done(quest) / total), 4)


def distance_remaining(quest: dict) -> int:
    if quest.get("complete"):
        return 0
    return max(0, len(quest.get("beats", [])) - beats_done(quest))


def distance_text(quest: dict) -> str:
    n = distance_remaining(quest)
    return _DISTANCE_WORDS.get(n, f"{n} stops to go")


def progress_line(quest: dict, advances: str = "") -> str:
    """One sentence tying what just happened to the destination."""
    goal = quest.get("goal_text") or quest.get("destination") or "the end of the trail"
    if quest.get("complete") or distance_remaining(quest) == 0:
        return f"{quest['char']} has reached {goal}."
    lead = (advances or "").strip().rstrip(".")
    dist = distance_text(quest)
    if lead:
        return f"{lead[0].upper()}{lead[1:]} - {goal} is closer now: {dist}."
    return f"Closer to {goal}: {dist}."


def _pick_archetype(topic, band, mechanics, used=(), rng=None) -> str | None:
    """Honour the beat's preferred mechanic, then prefer a mechanic the child
    has not played yet this quest - that is what kills "it's only stepping
    stones"."""
    # `playable_archetypes`, not `archetypes_for`: ages 4-6 must never be
    # handed an archetype whose answers are words.
    available = math_engine.playable_archetypes(topic, band)
    if not available:
        return None
    rng = rng or random
    used = list(used)

    # Pass 1: the first PREFERRED mechanic that still has something the child
    # has not played. Taking the first mechanic that merely *exists* is what
    # made a k1 geometry quest four shape-doors in a row - "pick" was always
    # first in the beat's list and always had a pool.
    for mech in (mechanics or []):
        unseen = [a for a in available
                  if MECHANIC_OF.get(a) == mech and a not in used]
        if unseen:
            return rng.choice(unseen)

    # Pass 2: anything at all the child has not played yet, preferred
    # mechanics first so the narration still roughly matches.
    unseen = [a for a in available if a not in used]
    if unseen:
        return rng.choice(unseen)

    # Pass 3: everything has been played - take the least recently used, so
    # repeats are at least spread out.
    return min(available, key=lambda a: len(used) - 1 - used[::-1].index(a)
               if a in used else -1)


def current_beat(quest: dict) -> dict | None:
    if quest["index"] >= len(quest["beats"]):
        return None
    return quest["beats"][quest["index"]]


def obstacle_for(quest: dict, beat: dict, ctx: dict | None = None) -> dict:
    """The thing blocking the way, and what unlocks it.

    Contract with the frontend:

      locked_text   shown the moment the hero arrives. They CANNOT pass.
      unlocked_by   "question" -> the answer is the key (most beats)
                    "effort"   -> an interlude; just do the crossing
      hint / keys   what to press, once it is unlocked
      steps         how many input beats the crossing takes
      cleared_text  the payoff line after the last input

    Ordering matters and is the whole point: the question comes FIRST and the
    traversal is the reward. The old V3 flow was the other way round - walk
    four grey blobs, then get a question - which playtesters read as
    busywork, correctly.
    """
    kind = beat.get("obstacle") or DEFAULT_OBSTACLE
    spec = obstacle_spec(kind)
    ctx = ctx or _ctx(quest)
    gated = not beat.get("no_question")
    complexity = quest.get("complexity", 1)
    # A nine-year-old on a speed/distance quest gets a longer crossing than a
    # four-year-old doing addition - but never so long it becomes a chore.
    steps = int(spec.get("steps", 3)) + (1 if complexity >= 4 else 0)
    steps = max(2, min(6, steps))
    noun = spec.get("noun", "step")
    return {
        "kind": kind,
        "action": spec.get("action", "hop"),
        # Which family of art the canvas should paint. Two obstacles may
        # share a visual (a tide pool and a ford are both water) but a rope
        # bridge and a locked gate never can - the playtest note was "i dont
        # see the different obstacle types???".
        "visual": spec.get("visual", "stones"),
        # A short imperative the QUESTION can be framed around, so the maths
        # is the thing standing between the child and the crossing:
        # "Pay the gate: 2 dimes and 2 quarters. How many cents?"
        "toll": spec.get("toll", "Pay the toll"),
        "terrain": list(spec.get("terrain", ())),
        "drama": int(spec.get("drama", 1)),
        "title": spec.get("title", "The Crossing"),
        "noun": noun,
        "plural": noun if noun.endswith("s") else noun + "s",
        "steps": steps,
        "keys": list(spec.get("keys") or ["→"]),
        "hint": spec.get("hint", TRAVERSAL_HINT.get(kind, "Press → to go on")),
        "locked_text": _fmt(spec.get("blocked", ""), **ctx),
        "cleared_text": _fmt(spec.get("cleared", ""), **ctx),
        "unlocked_by": "question" if gated else "effort",
        "gates_question": False,     # the QUESTION gates the OBSTACLE now
    }


def traversal_for(quest: dict, beat: dict) -> dict:
    """DEPRECATED legacy view of `obstacle_for`, kept for older frontends.

    Same `steps` / `hint` / `kind` keys the V3 build read. `gates_question`
    is now always False: nothing gates the question any more, because the
    question is what gates everything else.
    """
    ob = obstacle_for(quest, beat)
    return {
        "kind": ob["kind"],
        "steps": ob["steps"],
        "verb": ob["action"].title(),
        "noun": ob["noun"],
        "label": ob["title"],
        "hint": ob["hint"],
        "gates_question": False,
    }


def _interlude_challenge(quest: dict, beat: dict, ctx: dict) -> dict:
    """A stop with NO MATHS IN IT. Pure story, played with the arrow keys."""
    return {
        "question_type": "interlude",
        "grade_mode": "none",
        "prompt": "",
        "narrative": _fmt(beat.get("intro", ""), **ctx),
        "explanation": "",
        "target_count": 0,
        "stones": [],
        "props": [],
        "answer_order": None,
        "answer_sum": None,
        "answer_group_count": None,
        "tolerance": 0,
        "play_area": {"top_pct": 0.55, "bottom_pct": 0.95},
        "archetype": "interlude",
        "topic": quest["topic"],
        "band": quest["band"],
        "level": 0,
        "character_name": quest["char"],
        "setting": quest["setting"],
        "obstacle_count": 0,
    }


def issue_beat(quest: dict, session_level: int, mistakes_total: int = 0,
               seed=None) -> dict:
    """Generate the challenge (or the interlude) for the current beat."""
    beat = current_beat(quest)
    if beat is None:
        return None
    rng = random.Random(seed)

    level = max(1, min(5, int(session_level) + int(beat.get("level_delta", 0))))
    is_interlude = bool(beat.get("no_question"))
    ctx = _ctx(quest, subject="", carried=0)

    if is_interlude:
        challenge = _interlude_challenge(quest, beat, ctx)
        archetype = "interlude"
    else:
        archetype = _pick_archetype(quest["topic"], quest["band"],
                                    beat.get("mechanic"),
                                    quest.setdefault("used_archetypes", []), rng)

        # --- obstacle reconciliation ---------------------------------
        # Mistakes buy NAVIGATIONAL obstacles (things to walk around) and
        # extra beats - never extra wrong answers. The math gets easier, the
        # journey gets longer. See _add_navigation_obstacles in math_engine.
        nav = min(int(mistakes_total or 0), 3)
        if beat.get("kind") == "obstacle":
            nav += 2
        if beat.get("kind") in ("resolution", "helper"):
            nav = 0
        nav = min(nav, 5)

        challenge = None
        if beat.get("finale"):
            # THE FINAL CHALLENGE. Its numbers are the child's own haul, so
            # it is only answerable because of the stops that came before it.
            # None means they gathered nothing - fall through to a normal
            # (hard) climax rather than inventing a haul they never had.
            challenge = math_engine.generate_finale(
                topic=quest["topic"], band=quest["band"], level=level,
                carried=quest["state"], character_name=quest["char"],
                objects=[quest["setting"]], story_noun=quest.get("treasure"),
                seed=rng.randrange(1 << 30),
            )
        if challenge is None:
            challenge = math_engine.generate_challenge(
                topic=quest["topic"],
                band=quest["band"],
                level=level,
                character_name=quest["char"],
                objects=[quest["setting"]],
                # ★ the story and the maths are about the SAME THING ★
                story_noun=quest.get("treasure"),
                # ...and the question is what stands between the child and
                # the obstacle in front of them.
                story_toll=obstacle_spec(beat.get("obstacle") or "").get("toll"),
                archetype=archetype,
                nav_obstacles=nav,
                exclude_archetypes=([quest.get("last_archetype")]
                                    if quest.get("last_archetype") else ()),
                seed=rng.randrange(1 << 30),
            )
        quest["last_archetype"] = challenge.get("archetype")
        quest.setdefault("used_archetypes", []).append(challenge.get("archetype"))

        # The story names the place and the stakes; the NOUN comes from the
        # challenge that actually got generated, so narration never promises
        # planks and then hand over a berry sum.
        subject = subject_of(challenge)
        spend = beat.get("spend")
        carried = quest["state"].get(spend, 0) if spend else 0
        ctx = _ctx(quest, subject=subject, carried=carried)

    intro_src = beat.get("intro", "")
    spend = beat.get("spend")
    if spend and not quest["state"].get(spend, 0) and beat.get("intro_alt"):
        intro_src = beat["intro_alt"]

    challenge["beat"] = {
        "index": quest["index"],
        "total": len(quest["beats"]),
        "title": _fmt(beat.get("title", ""), **ctx),
        "camera": beat.get("camera", "wide"),
        "tint": beat.get("tint", "day"),
        "intro": _fmt(intro_src, **ctx),
        "on_success": "",  # filled in after the answer, when gains are known
        "kind": beat.get("kind", "gather"),
        "quest_title": quest["title"],
        # --- the journey ------------------------------------------------
        "goal_text": quest.get("goal_text", ""),
        "location": _fmt(beat.get("location", ""), **ctx),
        "advances": _fmt(beat.get("advances", ""), **ctx),
        "journey_progress": journey_progress(quest),
        "distance_remaining": distance_remaining(quest),
        "distance_text": distance_text(quest),
        "is_final": quest["index"] >= len(quest["beats"]) - 1,
        # --- V3 -----------------------------------------------------------
        "is_interlude": is_interlude,
        "is_finale": bool(challenge.get("is_finale")),
        "complexity": quest.get("complexity", 1),
    }
    # ★ THE OBSTACLE. The hero has arrived at something they cannot pass;
    # answering the question is what unlocks it, and then the child performs
    # the crossing on the keyboard. Always present - even the arrival beat
    # has a last little step to take.
    challenge["obstacle"] = obstacle_for(quest, beat, ctx)
    challenge["traversal"] = traversal_for(quest, beat)   # deprecated alias
    challenge["no_fail"] = bool(beat.get("no_fail"))
    challenge["quest_state"] = dict(quest["state"])

    if is_interlude:
        # Nothing to answer, so nothing to wait for: the beat is already
        # spent the moment it is issued, and the journey moves on when the
        # child finishes walking it.
        quest["awaiting_answer"] = False
        quest["log"].append({
            "index": quest["index"],
            "title": challenge["beat"]["title"],
            "kind": "interlude",
            "archetype": "interlude",
            "prompt": "",
            "correct": True,
        })
    else:
        quest["awaiting_answer"] = True
    return challenge


def _reward_amount(challenge: dict) -> int:
    mode = challenge.get("grade_mode", "set")
    if mode == "count":
        n = int(challenge.get("target_count") or 1)
    elif mode == "sum":
        n = int(round(float(challenge.get("answer_sum") or 1)))
    elif mode == "groups":
        n = int(challenge.get("target_count") or 1)
    elif mode == "order":
        n = int(challenge.get("perimeter") or challenge.get("target_count") or 1)
    elif mode == "value":
        n = int(round(abs(float(challenge.get("answer_value") or 1))))
    elif mode == "none":
        n = 1
    else:
        n = 1
        for s in challenge.get("stones", []):
            if s.get("correct") and isinstance(s.get("value"), (int, float)):
                n = int(abs(s["value"]))
                break
    return max(1, min(n, 99))


def record_result(quest: dict, challenge: dict, was_correct: bool) -> dict:
    """Apply the beat's outcome to quest state. Returns narration + changes."""
    beat = current_beat(quest) or {}
    ctx_extra = {}
    changes: dict[str, int] = {}

    subject = subject_of(challenge)
    ctx_extra["subject"] = subject

    if was_correct:
        gain = beat.get("gain")
        if gain:
            slot = slot_of(subject, gain)
            amount = _reward_amount(challenge)
            quest["state"][slot] = quest["state"].get(slot, 0) + amount
            changes[slot] = amount
            ctx_extra["gain"] = amount
        spend = beat.get("spend")
        if spend:
            have = quest["state"].get(spend, 0)
            used = have if have else 0
            if used:
                quest["state"][spend] = 0
                changes[spend] = -used
            ctx_extra["spent"] = used
        if beat.get("helper"):
            quest["state"]["friend_found"] = True

    ctx = _ctx(quest, **ctx_extra)
    text = _fmt(beat.get("on_success", ""), **ctx) if was_correct else ""
    if not was_correct:
        text = _fmt(
            "{char} slips back a step - no harm done. The {setting} is patient.",
            **ctx)

    quest["log"].append({
        "index": quest["index"],
        "title": _fmt(beat.get("title", ""), **ctx),
        "kind": beat.get("kind"),
        "archetype": challenge.get("archetype"),
        "prompt": challenge.get("prompt"),
        "correct": bool(was_correct),
    })
    quest["awaiting_answer"] = False

    # The log has just grown, so progress is now measured AFTER this beat -
    # which is what the child should see: the step they just took.
    line = progress_line(quest, _fmt(beat.get("advances", ""), **ctx) if was_correct else "")
    if text:
        text = f"{text} {line}"
    else:
        text = line

    return {
        "text": text,
        "changes": changes,
        "state": dict(quest["state"]),
        "progress_line": line,
        "journey_progress": journey_progress(quest),
        "distance_remaining": distance_remaining(quest),
        "distance_text": distance_text(quest),
        "goal_text": quest.get("goal_text", ""),
    }


# ----------------------------------------------------------- adaptation

def _resolution_index(quest: dict) -> int:
    for i, b in enumerate(quest["beats"]):
        if b.get("kind") == "resolution":
            return i
    return len(quest["beats"]) - 1


def insert_helper_beat(quest: dict) -> bool:
    """Splice a friend-arrives beat in after the current one. Never a scolding."""
    if len(quest["beats"]) >= MAX_BEATS:
        return False
    at = quest["index"] + 1
    if at > _resolution_index(quest):
        return False
    if quest["beats"][quest["index"]].get("helper"):
        return False
    beat = dict(HELPER_BEAT)
    cur = quest["beats"][quest["index"]]
    beat["camera"] = cur.get("camera", "wide")
    beat["tint"] = cur.get("tint", "day")
    # A helper beat happens in the SAME place as the beat it follows - the
    # journey pauses, it does not rewind.
    beat["location"] = cur.get("location") or default_location(
        beat, quest.get("scenery") or quest["setting"], quest.get("goal_text", ""))
    # A helper beat arrives mid-quest, so it never got one from
    # assign_obstacles. Give it the gentlest thing this world offers that is
    # not what the child is standing in front of right now.
    pool = obstacles_for_setting(quest.get("setting", ""), [quest.get("setting", "")])
    here = cur.get("obstacle")
    choices = [k for k in pool if k != here] or pool
    beat["obstacle"] = min(choices, key=lambda k: OBSTACLES[k]["drama"])
    quest["beats"].insert(at, beat)
    quest["helpers_added"] += 1
    return True


def drop_optional_beat(quest: dict) -> bool:
    """Tighten the arc when the child is breezing. Never drops the resolution."""
    if len(quest["beats"]) <= MIN_BEATS:
        return False
    for i in range(quest["index"] + 1, len(quest["beats"])):
        b = quest["beats"][i]
        if b.get("optional") and b.get("kind") != "resolution":
            quest["beats"].pop(i)
            quest["optional_dropped"] += 1
            return True
    return False


def advance(quest: dict) -> bool:
    """Step to the next beat. Returns False when the quest is finished."""
    quest["index"] += 1
    if quest["index"] >= len(quest["beats"]):
        quest["complete"] = True
        return False
    return True


def epilogue(quest: dict) -> str:
    return _fmt(quest["epilogue_tpl"], **_ctx(quest))


# ------------------------------------------------------- journey frames
#
# One repaint of the world every FRAME_EVERY_BEATS beats. The frame INDEX is
# derived from the beat index rather than stored, so splicing a helper beat
# in or cutting an optional one simply shifts when the next repaint is due -
# there is no separate frame timeline to keep in sync.

FRAME_EVERY_BEATS = 2


def frame_index_for_beat(beat_index: int, frame_every: int = FRAME_EVERY_BEATS) -> int:
    return max(0, int(beat_index)) // max(1, int(frame_every))


def frame_progress(quest: dict, frame_index: int,
                   frame_every: int = FRAME_EVERY_BEATS) -> float:
    """How far along the journey a frame sits, 0-1.

    This is what tells the image model how big the destination should look.
    Without it the edit model preserves the previous composition perfectly and
    the child travels for six beats without the horizon ever changing.
    """
    beats = quest.get("beats") or []
    if not beats:
        return 0.0
    at = min(max(0, frame_index) * max(1, frame_every), len(beats) - 1)
    return round(at / max(1, len(beats) - 1), 4)


def scene_brief_for_frame(quest: dict, frame_index: int,
                          frame_every: int = FRAME_EVERY_BEATS) -> str:
    """The visual brief for a frame: the location of the beat it opens on."""
    beats = quest.get("beats") or []
    if not beats:
        return quest.get("setting", "a meadow")
    at = min(max(0, frame_index) * max(1, frame_every), len(beats) - 1)
    beat = beats[at]
    brief = beat.get("location") or default_location(
        beat, quest.get("scenery") or quest.get("setting", "a meadow"),
        quest.get("goal_text", ""))
    return _fmt(brief, **_ctx(quest))


def summary(quest: dict) -> dict:
    beat = current_beat(quest)
    return {
        "quest_title": quest["title"],
        "template": quest["template_id"],
        "story_source": quest.get("story_source", "template"),
        "beat_index": quest["index"],
        "beat_total": len(quest["beats"]),
        "beat_title": _fmt(beat["title"], **_ctx(quest)) if beat else "",
        "state": dict(quest["state"]),
        "complete": quest["complete"],
        "helpers_added": quest["helpers_added"],
        "optional_dropped": quest["optional_dropped"],
        "log": quest["log"],
        # --- the journey ---------------------------------------------
        "goal_text": quest.get("goal_text", ""),
        "destination": quest.get("destination", ""),
        "destination_source": quest.get("destination_source", "inferred"),
        "journey_progress": journey_progress(quest),
        "distance_remaining": distance_remaining(quest),
        "distance_text": distance_text(quest),
        "beats_done": beats_done(quest),
        "complexity": quest.get("complexity", 1),
        "register": quest.get("register", "simple"),
        "interludes": quest.get("interludes", 0),
        "treasure": quest.get("treasure", ""),
        "treasure_source": quest.get("treasure_source", "world"),
    }
