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
# "collect" is now COUNT-OUT (take exactly as many as the answer, from a pile
# that holds more) and "type" is free response. The six touch-and-choose
# archetypes deleted on 2026-09-19 are gone from here too - see
# math_engine.DELETED_ARCHETYPES.
MECHANIC_OF = {
    "berry_baskets": "pick",
    "plank_bridge": "pick",
    "lanterns_out": "pick",
    "spend_gems": "pick",
    "rows_of_lanterns": "pick",
    "equal_baskets": "pick",
    "pie_gate": "pick",
    "number_line_leap": "pick",
    "measure_rope": "pick",
    "shape_door": "pick",
    "garden_measure": "pick",
    "mystery_sacks": "pick",
    "balance_bridge": "pick",
    "catch_the_raft": "pick",
    "clock_run": "pick",
    "biggest_pile": "pick",
    "next_in_line": "pick",
    "coin_purse": "pick",
    "toll_gate": "set",
    "rain_gauge": "set",
    "balance_scales": "set",
    "acorn_count": "collect",
    "stones_left": "collect",
    "orchard_count": "collect",
    "fraction_of_berries": "collect",
    "fence_posts": "collect",
    "mile_count": "collect",
    "count_the_lanterns": "collect",
    "market_stall": "collect",
    "countdown_path": "walk",
    "safe_sandbars": "group",
    "sum_scroll": "type",
    "tally_scroll": "type",
    "product_scroll": "type",
    "fraction_scroll": "type",
    "gauge_scroll": "type",
    "survey_scroll": "type",
    "rune_scroll": "type",
    "logbook_scroll": "type",
    "counting_scroll": "type",
    "money_scroll": "type",
    "final_gate": "finale",
}

# What each archetype is ABOUT. The beat narration names a place and a stake;
# the noun comes from whatever challenge actually got generated, so the story
# never promises planks and then hand the child a berry sum.
ARCHETYPE_SUBJECT = {
    "berry_baskets": "berries", "acorn_count": "acorns", "toll_gate": "gems",
    "plank_bridge": "planks", "sum_scroll": "tally marks",
    "lanterns_out": "lanterns", "stones_left": "stepping stones",
    "spend_gems": "gems", "countdown_path": "stepping stones",
    "tally_scroll": "tally marks",
    "orchard_count": "seeds", "rows_of_lanterns": "lanterns",
    "equal_baskets": "apples", "product_scroll": "tally marks",
    "safe_sandbars": "stepping stones", "fraction_of_berries": "berries",
    "pie_gate": "moon-shards", "fraction_scroll": "moon-shards",
    "number_line_leap": "lily pads", "rain_gauge": "raindrops",
    "measure_rope": "rope", "gauge_scroll": "raindrops",
    "shape_door": "keys", "fence_posts": "fence posts",
    "garden_measure": "fence posts", "survey_scroll": "fence posts",
    "mystery_sacks": "gems", "balance_bridge": "gems", "balance_scales": "gems",
    "rune_scroll": "runes",
    "catch_the_raft": "raft-marks", "clock_run": "miles",
    "mile_count": "miles", "logbook_scroll": "miles",
    "count_the_lanterns": "lanterns", "biggest_pile": "pebbles",
    "next_in_line": "stepping stones", "counting_scroll": "tally marks",
    "coin_purse": "coins", "market_stall": "coins", "money_scroll": "coins",
    "final_gate": "treasures",
}

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

# ====================================== V3: the journey skeleton + traversal
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
# V3 adds two things the playtest demanded:
#
#   TRAVERSAL   Every stop is reached by MOVING. `traversal` describes a short
#               keyboard journey - hop four stones, climb three ledges - that
#               the child plays with the arrow keys BEFORE the question
#               appears. Questions no longer arrive back to back; they are
#               what you find at the end of a walk.
#
#   INTERLUDES  Stops with `no_question: True`. Pure story: push the log,
#               open the gate, wade the reeds. No arithmetic at all. These
#               exist because "the story is told through a series of
#               questions" was the single loudest complaint.
#
# `min_complexity` gates a stop on how hard the quest should be (see
# `complexity_for`), which is how a speed/distance/time quest for a
# nine-year-old ends up with more locations and more plot turns than an
# addition quest for a four-year-old.

FULL_SKELETON = [
    {
        "slot": 0,
        "key": "setup", "kind": "setup", "camera": "wide", "tint": "day",
        "level_delta": -1, "mechanic": ["pick", "collect"],
        "min_complexity": 0,
        "traversal": {"kind": "path", "verb": "Set off", "noun": "waymark"},
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
        "traversal": {"kind": "stones", "verb": "Hop", "noun": "stepping stone"},
        "fallback_title": "The Stepping Stones",
        "role": ("A WORDLESS CROSSING. No puzzle here at all - the traveller "
                 "simply has to get across something: stepping stones, a "
                 "fallen log, a line of rocks. Describe the crossing itself."),
    },
    {
        "slot": 2,
        "key": "gather1", "kind": "gather", "camera": "left_bank", "tint": "day",
        "level_delta": 0, "mechanic": ["collect", "set", "pick"], "gain": "berries",
        "min_complexity": 0,
        "traversal": {"kind": "reeds", "verb": "Wade", "noun": "reed bank"},
        "fallback_title": "The Gathering Place",
        "role": ("GATHER. A stop a little way along where something useful is "
                 "collected - it will be needed further on. Mention picking things up."),
    },
    {
        "slot": 3,
        "key": "gather2", "kind": "gather", "camera": "left_bank", "tint": "day",
        "level_delta": 0, "mechanic": ["pick", "set", "type"], "gain": "planks",
        "optional": True, "min_complexity": 2,
        "traversal": {"kind": "path", "verb": "Follow", "noun": "cairn"},
        "fallback_title": "The Second Find",
        "role": ("GATHER again, somewhere different and further on - a second kind "
                 "of useful thing, materials rather than food."),
    },
    {
        "slot": 4,
        "key": "obstacle", "kind": "obstacle", "camera": "midstream", "tint": "day",
        "level_delta": 0, "mechanic": ["set", "walk", "pick", "type"],
        "spend": "planks", "min_complexity": 0,
        "traversal": {"kind": "log", "verb": "Push", "noun": "fallen log"},
        "fallback_title": "The Way Is Blocked",
        "role": ("OBSTACLE, roughly halfway. Something blocks the route to the "
                 "destination and the gathered materials get USED UP getting past it."),
    },
    {
        "slot": 5,
        "key": "ledge", "kind": "interlude", "camera": "midstream", "tint": "dusk",
        "level_delta": 0, "mechanic": [], "no_question": True,
        "min_complexity": 3,
        "traversal": {"kind": "ledge", "verb": "Climb", "noun": "ledge"},
        "fallback_title": "The Cliff Ledges",
        "role": ("ANOTHER WORDLESS STRETCH, harder than the first and later in "
                 "the journey: a climb, a scramble, a narrow ledge. Still no "
                 "puzzle - just the effort of getting up and over."),
    },
    {
        "slot": 6,
        "key": "setback", "kind": "setback", "camera": "midstream", "tint": "dusk",
        "level_delta": -1, "mechanic": ["pick", "collect"], "gain": "lanterns",
        "optional": True, "min_complexity": 1,
        "traversal": {"kind": "reeds", "verb": "Push through", "noun": "thicket"},
        "fallback_title": "The Light Goes",
        "role": ("SETBACK. The weather or the light turns and the destination is "
                 "briefly hard to see. Tense but never frightening, and end on hope."),
    },
    {
        "slot": 7,
        "key": "climax", "kind": "climax", "camera": "high", "tint": "night",
        "level_delta": 1, "mechanic": ["group", "collect", "walk", "pick", "type"],
        "finale": True, "min_complexity": 0,
        "traversal": {"kind": "gate", "verb": "Open", "noun": "gate latch"},
        "fallback_title": "The Last Hard Part",
        "role": ("CLIMAX. The final hard stretch, right at the foot of the "
                 "destination. The biggest challenge of the whole journey, and "
                 "it uses everything gathered along the way."),
    },
    {
        "slot": 8,
        "key": "arrival", "kind": "resolution", "camera": "far_bank", "tint": "night",
        "level_delta": -2, "mechanic": ["pick", "collect"], "no_fail": True,
        "min_complexity": 0,
        "traversal": {"kind": "path", "verb": "Walk", "noun": "last step"},
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
    """How far the child walks before the next thing happens.

    Short enough that a four-year-old does not get bored on the way to the
    question; long enough at the top end that a nine-year-old feels like
    they are actually travelling.
    """
    base = 2 + complexity // 2
    if is_interlude:
        base += 1
    return max(2, min(6, base))


def build_skeleton(band: str, topic: str) -> list[dict]:
    """The mechanical arc for THIS child, THIS topic.

    Returns a subsequence of FULL_SKELETON - same dicts, same order, same
    `slot` numbers - so prose written against the full skeleton still lines
    up however many stops got dropped.
    """
    c = complexity_for(band, topic)
    out = []
    for skel in FULL_SKELETON:
        if c < skel.get("min_complexity", 0):
            continue
        beat = {k: v for k, v in skel.items()
                if k not in ("role", "fallback_title", "traversal")}
        tv = dict(skel.get("traversal") or {})
        tv["steps"] = traversal_steps(c, bool(skel.get("no_question")))
        beat["traversal"] = tv
        beat["complexity"] = c
        out.append(beat)
    return out


# Prose for the interlude stops, in both vocabulary registers. These are the
# beats with NO MATHS IN THEM - the whole point is that the child does
# something with their hands and the story moves anyway.
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
    ("ledge", "simple"): {
        "title": "The Ledges",
        "intro": "Little shelves of rock go up like stairs. Climb them, {char}!",
        "on_success": "Up and up, and there {char} is at the top.",
    },
    ("ledge", "rich"): {
        "title": "The Cliff Ledges",
        "intro": ("The cliff is not smooth after all - it is a staircase of narrow "
                  "ledges, each one a stretch above the last. {char} reaches up."),
        "on_success": ("{char} hauls over the final lip and lies flat for a "
                       "moment, looking at how far down the world has gone."),
    },
    ("log", "simple"): {
        "title": "The Fallen Log",
        "intro": "A big log lies across the path. Push it, {char}!",
        "on_success": "The log rolls away. The path is open again.",
    },
    ("log", "rich"): {
        "title": "The Fallen Log",
        "intro": ("A storm-felled trunk lies square across the way, too high to "
                  "climb and too long to walk around. It will have to be shifted."),
        "on_success": ("It grinds, tips, and rolls off into the ferns. {char} "
                       "dusts off both hands."),
    },
}

# What the child is told to DO during a traversal, by kind. The frontend
# renders this under the story line while the arrow keys are live.
TRAVERSAL_HINT = {
    "stones": "Hop from stone to stone with the arrow keys",
    "ledge": "Climb the ledges with the arrow keys",
    "log": "Push the log along with the arrow keys",
    "gate": "Work the latches open with the arrow keys",
    "reeds": "Wade through with the arrow keys",
    "path": "Follow the trail with the arrow keys",
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
    "mechanic": ["pick", "collect"], "helper": True,
    "traversal": {"kind": "path", "verb": "Walk", "noun": "step", "steps": 2},
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


def traversal_for(quest: dict, beat: dict) -> dict:
    """The keyboard journey the child plays to REACH this beat.

    This is the answer to "the questions don't relate to each other": you no
    longer get a question, you walk somewhere and find one. The frontend
    renders `steps` nodes across the lower third of the art and only reveals
    the question once the hero has touched the last one.
    """
    tv = dict(beat.get("traversal") or {})
    kind = tv.get("kind", "stones")
    steps = int(tv.get("steps") or traversal_steps(quest.get("complexity", 1),
                                                   bool(beat.get("no_question"))))
    noun = tv.get("noun", "stepping stone")
    verb = tv.get("verb", "Hop")
    plural = noun if noun.endswith("s") else noun + "s"
    return {
        "kind": kind,
        "steps": max(1, min(8, steps)),
        "verb": verb,
        "noun": noun,
        "label": f"{verb} the {steps} {plural}",
        "hint": TRAVERSAL_HINT.get(kind, TRAVERSAL_HINT["path"]),
        "gates_question": not beat.get("no_question"),
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
                objects=[quest["setting"]], seed=rng.randrange(1 << 30),
            )
        if challenge is None:
            challenge = math_engine.generate_challenge(
                topic=quest["topic"],
                band=quest["band"],
                level=level,
                character_name=quest["char"],
                objects=[quest["setting"]],
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
    # The walk that gates this beat. Always present - even the arrival beat
    # is reached on foot.
    challenge["traversal"] = traversal_for(quest, beat)
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
    }
