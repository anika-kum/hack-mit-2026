"""
OpenAI integration layer: drawing interpretation, quest-outline writing,
world/journey image generation, and story narration.

Every function degrades gracefully when no API key is configured, so the
full game remains playable offline (important for demo reliability). The two
V2 additions follow the same contract:

    generate_storyline()  -> None  means "use an authored template"
    generate_frame()      -> None  means "keep the frame already on screen"
"""

import base64
import io
import json
import os
import random
import re
import time

# Model IDs verified current 2026-09-19. gpt-image-1 and the gpt-4o line are
# superseded; gpt-image-1 in particular shuts down 2026-12-01.
# IMAGE_QUALITY "low" is ~15x cheaper AND several times faster than "high" -
# for a crayon-drawing aesthetic the difference is near-invisible, and latency
# is the real constraint in a live demo.
VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-5.6-luna")
TEXT_MODEL = os.getenv("OPENAI_TEXT_MODEL", "gpt-5.6-luna")
IMAGE_QUALITY = os.getenv("OPENAI_IMAGE_QUALITY", "low")

# ---------------------------------------------------- IMAGES: META MUSE 1.0
#
# 2026-09-19: image generation moved off OpenAI onto Meta's Muse Image 1.0.
# TEXT AND VISION STAY ON OPENAI - only pictures changed.
#
# Muse is reachable through the OpenAI SDK by pointing base_url at the Meta
# Model API, so `images.generate` / `images.edit` keep the same shape. Three
# real differences, all measured against the live API rather than assumed:
#
#   * `background` is REJECTED ("unknown parameter"). Muse cannot cut a
#     transparent sprite for us, and the hero is composited over the painted
#     world, so a white box behind it is fatal. `_key_out_background()` below
#     does the cutout ourselves.
#   * `quality` is REJECTED. There is no cheap/fast tier to ask for.
#   * The default return is WEBP. `output_format="png"` is honoured, and we
#     ask for it because the sprite path needs an alpha channel.
#
# Falls back to OpenAI images automatically when MODEL_API_KEY is absent, so
# nothing breaks for someone who only has the OpenAI key.
META_BASE_URL = os.getenv("META_MODEL_API_BASE", "https://api.meta.ai/v1")
MUSE_IMAGE_MODEL = os.getenv("META_IMAGE_MODEL", "muse-image-1.0")
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2.5-flare")


def _muse_key():
    key = os.getenv("MODEL_API_KEY") or os.getenv("META_MODEL_API_KEY")
    return key.strip() if key and key.strip() else None


def using_muse() -> bool:
    return _muse_key() is not None


# What the rest of the module calls the image model. Kept as a module-level
# name because /api/health and the harness both report it.
IMAGE_MODEL = MUSE_IMAGE_MODEL if _muse_key() else OPENAI_IMAGE_MODEL

# ---------------------------------------------------------- art direction
#
# 2026-09-19 playtest: "we originally said cutesy and pastel, but it ended up
# very 'girly' and childish; which may not be appropriate for a slightly older
# audience (or boys)."
#
# So the whole art brief moved off sugar-pastel and onto ADVENTURE SKETCHBOOK:
# warm peach, amber and cream (which is where the Do-IT-oodle brand already
# sits), balanced against moss green, deep teal and clay. Still hand-painted,
# still friendly, still rounded - but it should read like the endpapers of an
# adventure book rather than a nursery wall. Do not put "pastel" back in here.
ART_DIRECTION = (
    "hand-painted watercolour-and-ink adventure storybook illustration; warm "
    "earthy palette of peach, amber, terracotta and warm cream balanced with "
    "moss green, deep teal and slate blue; confident inked outlines, visible "
    "paper grain, gentle depth; friendly and inviting for any child aged 4-10, "
    "adventurous rather than sugary, no glitter, no hearts, no candy colours"
)

THEMES = {
    "storybook": "classic adventure-book scenery, inked outlines, warm sunlit washes",
    "woodland": "deep woodland of moss, fern and bracken, amber light through leaves",
    "coast": "windy coastline of teal water, pale sand, weathered rope and timber",
    "canyon": "high desert canyon of terracotta rock, dry gold scrub, wide sky",
    "starlight": "quiet night country under deep indigo sky and warm lantern light",
}

_client = None
_client_checked = False
LAST_ERROR: str | None = None  # surfaced via /api/health so failures are loud


def get_client():
    """Lazily construct an OpenAI client. Returns None when unavailable."""
    global _client, _client_checked, LAST_ERROR
    if _client_checked:
        return _client
    _client_checked = True
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or api_key.startswith("sk-your-key"):
        LAST_ERROR = "No OPENAI_API_KEY set (create backend/.env)."
        return None
    try:
        import httpx
        from openai import OpenAI
        # Force gzip/deflate: this machine's zstandard build rejects the
        # `output_buffer_limit` kwarg httpx passes, so every zstd-encoded
        # response dies with an opaque Decompressor error.
        http_client = httpx.Client(
            headers={"Accept-Encoding": "gzip, deflate"},
            timeout=180.0,
        )
        _client = OpenAI(api_key=api_key, http_client=http_client)
    except Exception as exc:
        # Never fail silently here: an SDK/dependency clash looks identical to
        # "offline mode" from the UI, which cost us real debugging time once.
        LAST_ERROR = f"OpenAI client init failed: {type(exc).__name__}: {exc}"
        _client = None
    return _client


_image_client = None
_image_client_checked = False


def get_image_client():
    """The client PICTURES go through. Meta Muse when configured, else OpenAI.

    Separate from `get_client()` on purpose: questions, narration and vision
    stay on OpenAI, and a broken image provider must not take the text layer
    down with it (or vice versa).
    """
    global _image_client, _image_client_checked, LAST_ERROR
    if _image_client_checked:
        return _image_client
    _image_client_checked = True
    key = _muse_key()
    if key is None:
        _image_client = get_client()          # no Muse key: OpenAI as before
        return _image_client
    try:
        import httpx
        from openai import OpenAI
        http_client = httpx.Client(
            headers={"Accept-Encoding": "gzip, deflate"}, timeout=240.0)
        _image_client = OpenAI(api_key=key, base_url=META_BASE_URL,
                               http_client=http_client)
    except Exception as exc:
        LAST_ERROR = f"Muse client init failed: {type(exc).__name__}: {exc}"
        _image_client = None
    return _image_client


def _image_kwargs(transparent: bool = False) -> dict:
    """Per-provider image parameters. Muse rejects `quality` and `background`."""
    if using_muse():
        # PNG so the sprite path has an alpha channel to write into; Muse
        # otherwise returns WEBP.
        return {"output_format": "png"} if transparent else {}
    kw = {"quality": IMAGE_QUALITY}
    if transparent:
        kw["background"] = "transparent"
        kw["output_format"] = "png"
    return kw


def _image_mime(raw: bytes) -> str:
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw[:2] == b"\xff\xd8":
        return "image/jpeg"
    return "image/png"


def _data_url(raw: bytes) -> str:
    return f"data:{_image_mime(raw)};base64," + base64.b64encode(raw).decode()


def _image_result(resp, transparent: bool = False) -> str | None:
    """Pull the picture out of a response, whichever provider produced it."""
    try:
        item = resp.data[0]
    except Exception:
        return None
    b64 = getattr(item, "b64_json", None)
    if not b64:
        return getattr(item, "url", None)
    try:
        raw = base64.b64decode(b64)
    except Exception:
        return None
    if transparent:
        raw = _key_out_background(raw) or raw
    return _data_url(raw)


# ------------------------------------------------------- the sprite cutout
#
# Muse has no transparent-background mode, and the hero is drawn ON TOP of
# the painted world - a white rectangle behind it is the single most visible
# way this can look broken. So we cut it out ourselves.
#
# Deliberately a FLOOD FILL FROM THE EDGES, not "delete every white pixel":
# the character's own eyes, teeth and highlights are white too, and a global
# key eats them. Only background connected to the border goes.
_CUTOUT_TOLERANCE = int(os.getenv("DQ_CUTOUT_TOLERANCE", "38"))


def _key_out_background(raw: bytes) -> bytes | None:
    """Make the flat border colour transparent. Returns PNG bytes, or None."""
    try:
        from PIL import Image
    except Exception:
        return None                           # no Pillow: ship it opaque
    try:
        import collections
        im = Image.open(io.BytesIO(raw)).convert("RGBA")
        w, h = im.size
        px = im.load()

        # The background colour is whatever dominates the border.
        edge = collections.Counter()
        for x in range(0, w, max(1, w // 64)):
            edge[px[x, 0][:3]] += 1
            edge[px[x, h - 1][:3]] += 1
        for y in range(0, h, max(1, h // 64)):
            edge[px[0, y][:3]] += 1
            edge[px[w - 1, y][:3]] += 1
        bg = edge.most_common(1)[0][0]

        def near(c):
            return (abs(c[0] - bg[0]) + abs(c[1] - bg[1]) + abs(c[2] - bg[2])
                    <= _CUTOUT_TOLERANCE)

        seen = bytearray(w * h)
        stack = []
        for x in range(w):
            stack.append((x, 0)); stack.append((x, h - 1))
        for y in range(h):
            stack.append((0, y)); stack.append((w - 1, y))
        while stack:
            x, y = stack.pop()
            if x < 0 or y < 0 or x >= w or y >= h:
                continue
            i = y * w + x
            if seen[i]:
                continue
            c = px[x, y]
            if c[3] == 0:
                seen[i] = 1
                continue
            if not near(c):
                continue
            seen[i] = 1
            px[x, y] = (c[0], c[1], c[2], 0)
            stack.append((x + 1, y)); stack.append((x - 1, y))
            stack.append((x, y + 1)); stack.append((x, y - 1))

        out = io.BytesIO()
        im.save(out, format="PNG")
        return out.getvalue()
    except Exception as exc:
        note_error(exc)
        return None


# gpt-5.6-luna thinks before it answers, and for storybook prose that thinking
# is pure latency: the same quest outline takes 23.5s at the default effort and
# 10.7s at "low", with no drop in quality we could see. Latency is the whole
# game here - a child waiting is a child who wandered off - so every text call
# asks for "low" and silently drops the parameter on any model that rejects it.
REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "low")
_effort_supported = True


def chat(client, **kwargs):
    """chat.completions.create with a low reasoning budget where supported."""
    global _effort_supported
    if _effort_supported and REASONING_EFFORT:
        try:
            return client.chat.completions.create(
                reasoning_effort=REASONING_EFFORT, **kwargs)
        except Exception as exc:
            if "reasoning_effort" not in str(exc):
                raise
            _effort_supported = False
    return client.chat.completions.create(**kwargs)


def note_error(exc: Exception) -> None:
    global LAST_ERROR
    LAST_ERROR = f"{type(exc).__name__}: {exc}"


def ai_available() -> bool:
    return get_client() is not None


# ---------------------------------------------------------------- interpret

_INTERPRET_SYSTEM = """You look at a young child's rough drawing (ages 4-10) and identify what they drew.
Be generous and imaginative - a few wobbly lines meant to be a cat IS a cat.

Respond with ONLY valid JSON, no markdown fences:
{
  "objects": ["cat", "tree", "river"],
  "main_character": "cat",
  "character_name": "a cute two-syllable name fitting the character",
  "setting": "one short phrase describing the environment, e.g. 'a river meadow with a big tree'",
  "relationships": ["cat beside tree", "house across river"],
  "layout": ["tree on the far left", "river across the right half", "house in the top right"],
  "story_hook": "one warm, exciting sentence to open the adventure, addressed to the child"
}

The first item in "objects" must be the main character.

"layout" is important: describe WHERE each non-character thing sits using
explicit left / centre / right and top / bottom, exactly as it appears in the
drawing. The game re-paints the child's world and must keep their arrangement,
so if the tree is on the left it must stay on the left."""


def _fallback_interpretation(text_description: str | None = None):
    desc = (text_description or "").lower()
    from math_engine import CUTE_NAMES, SETTING_KEYWORDS, guess_character_name

    objects = [w for w in CUTE_NAMES if w in desc]
    objects += [w for w in SETTING_KEYWORDS if w in desc]
    if not objects:
        objects = ["cat", "tree", "river"]

    name = guess_character_name(objects)
    return {
        "objects": objects,
        "main_character": objects[0],
        "character_name": name,
        "setting": text_description or "a sunny meadow beside a sparkling river",
        "relationships": [],
        "story_hook": f"{name} is ready for an adventure - let's go!",
        "source": "fallback",
    }


def _parse_json_loose(raw: str):
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.lstrip().lower().startswith("json"):
            raw = raw.lstrip()[4:]
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found")
    return json.loads(raw[start:end + 1])


def interpret_creation(image_data_url: str | None = None, text_description: str | None = None):
    """Understand the child's drawing and/or text description of their world."""
    client = get_client()
    if client is None:
        return _fallback_interpretation(text_description)

    content = []
    if text_description:
        content.append({
            "type": "text",
            "text": f"The child described their world: \"{text_description}\"",
        })
    if image_data_url:
        content.append({"type": "text", "text": "Here is the child's drawing:"})
        content.append({"type": "image_url", "image_url": {"url": image_data_url}})
    if not content:
        return _fallback_interpretation(None)

    try:
        resp = chat(
            client,
            model=VISION_MODEL,
            messages=[
                {"role": "system", "content": _INTERPRET_SYSTEM},
                {"role": "user", "content": content},
            ],
            # gpt-5.6 renamed max_tokens and accepts only the default
            # temperature, so we no longer pass one.
            max_completion_tokens=800,
        )
        data = _parse_json_loose(resp.choices[0].message.content)
        data.setdefault("objects", ["cat"])
        data.setdefault("character_name", "Mochi")
        data.setdefault("setting", "a sunny meadow")
        data.setdefault("relationships", [])
        data.setdefault("story_hook", f"{data['character_name']} is ready for an adventure!")
        data["source"] = "openai"
        return data
    except Exception as exc:
        fb = _fallback_interpretation(text_description)
        fb["error"] = str(exc)
        return fb


# ------------------------------------------------------------- storyline
#
# V2: the quest arc is written FOR this child, from their character, their
# world and the destination they typed. What the model may decide is prose
# and place: titles, what happens, how each stop moves closer to the goal.
#
# What it may NOT decide is the machinery - beat kind, difficulty delta,
# which mechanic, what is gathered/spent, which beats are optional, which
# beat cannot be failed. story_engine owns all of that, and hands the model
# a skeleton to write into. That split is deliberate: a hallucinated
# `no_fail` or a missing resolution beat would break adaptation, and the
# offline templates must stay a drop-in substitute.

_STORY_SYSTEM = """You write short adventure quests for children aged 4-10, starring a character the child invented.

A quest is a JOURNEY toward ONE destination. Every stop must visibly get closer to it. The last stop ARRIVES there.

You will be given the character, their world, the destination, and a SKELETON of stops.
The skeleton is fixed: same number of stops, same order, same "role" for each. Write the words only.

Respond with ONLY valid JSON, no markdown fences:
{
  "title": "the quest title, 2-6 words, no character name needed",
  "goal_text": "the destination as a short phrase the child can read on a progress bar, e.g. 'the top of Cloud Mountain'",
  "treasure": "ONE plural noun for the thing this quest is about collecting or carrying, 1-2 words, lower case, concrete and countable, e.g. 'rods', 'moonstones', 'acorns', 'lantern-oil jars'. The MATH QUESTIONS will be written about this exact noun, so it must be something you can have four of.",
  "opening": "2 sentences that set up the journey and name the destination, warm and exciting",
  "epilogue": "2 sentences for arriving, past tense, celebrating. May end with {haul}.",
  "beats": [
    {
      "title": "the name of this place, 2-5 words",
      "location": "a purely VISUAL description of this place for a background painter. It must be a DIFFERENT PLACE from the stop before: say what new scenery is in the foreground, what from earlier is now small behind, how big the destination now looks, the weather and the time of day. 20-30 words. NO characters, NO creatures, NO people - scenery only.",
      "intro": "2-3 short sentences: where {char} is now, what is in the way, and why it matters for reaching the destination. Present tense.",
      "on_success": "1-2 short sentences: what {char} just accomplished and how much closer the destination is now. Past tense.",
      "advances": "one short clause naming the progress made toward the destination"
    }
  ]
}

SOME STOPS HAVE NO PUZZLE AT ALL. Their role says so ("A WORDLESS CROSSING", "ANOTHER WORDLESS STRETCH"). At those stops the child simply MOVES - hops stones, climbs ledges, pushes a log - and the story goes on. Write them as pure action: what is in the way, and what it feels like to get over it. Never ask a question there and never mention counting.

RULES
- Write {char} instead of the character's name. This is the only placeholder allowed in "title", "location", "intro" and "advances".
- In "on_success" for a stop whose role mentions GATHER, you may also write "{gain} {subject}" - it becomes e.g. "7 berries". Use it at most once.
- "epilogue" may use {char} and {haul}. Nothing else.
- Never write any other curly-brace token. Never use the character's literal name.
- "treasure" must be a plain countable plural. Not a place, not an abstraction, not a person.
- Never mention, state or hint at any number the child has to work out. No math in the prose.
- Simple words, short sentences, warm and brave. No scary violence, no death, no failure framing.
- The LAST beat must arrive at the destination and must feel like a guaranteed, gentle win.
- Keep every string under 45 words."""

# Braces the story text may legitimately contain. Anything else is stripped
# before the text ever reaches string.Formatter, so a creative model cannot
# blow up narration with `{the door}`.
_ALLOWED_TOKENS = {"char", "setting", "Setting", "gain", "subject", "carried",
                   "spent", "haul", "friend", "friend_desc", "title", "goal"}
_TOKEN_RE = re.compile(r"\{([^{}]*)\}")


def sanitize_story_text(text) -> str:
    """Keep known placeholders, delete unknown ones, drop stray braces."""
    if not isinstance(text, str):
        return ""

    def _sub(m):
        return m.group(0) if m.group(1).strip() in _ALLOWED_TOKENS else ""

    cleaned = _TOKEN_RE.sub(_sub, text)
    # Any brace that survived is unpaired: string.Formatter would raise on it.
    if cleaned.count("{") != cleaned.count("}"):
        cleaned = cleaned.replace("{", "").replace("}", "")
    return cleaned.strip()


# How ornate the prose should be. Driven by story_engine.complexity_for(),
# which is band + topic: a speed/distance/time quest for a nine-year-old gets
# a materially richer story than an addition quest for a four-year-old, and
# that has to show up in the WORDS as well as in the number of stops.
_REGISTER_BRIEF = {
    "simple": ("Very short sentences, five to nine words. Only words a "
               "five-year-old hears every day. One idea per sentence. Name "
               "things plainly: the water, the big rock, the gate. No "
               "subclauses, no metaphors."),
    "rich": ("Longer, more varied sentences with real texture. Specific "
             "nouns (scree, boat-house, lantern-post, switchback) and strong "
             "verbs. One vivid image per beat. Two or three plot turns across "
             "the journey - something noticed early that matters later. Still "
             "warm and readable for a nine-year-old."),
}


def generate_storyline(interpretation: dict, band: str, destination: str,
                       skeleton: list[dict], topic: str = "",
                       complexity: int | None = None) -> dict | None:
    """Write a bespoke quest outline for this child. None => use a template."""
    client = get_client()
    if client is None:
        return None

    char = interpretation.get("character_name") or "our hero"
    hero = interpretation.get("main_character") or "a friendly creature"
    setting = interpretation.get("setting") or "a sunny meadow"
    objects = ", ".join(interpretation.get("objects", [])[:6])
    age = {"k1": "4-6 (cannot read much yet - keep it very simple)",
           "23": "7-8", "45": "9-10"}.get(band, "7-8")

    if complexity is None:
        complexity = {"k1": 0, "23": 1, "45": 2}.get(band, 1) + 1
    register = "simple" if complexity <= 1 else "rich"

    stops = "\n".join(
        f'  {i + 1}. role: {s["role"]}' for i, s in enumerate(skeleton))

    user = (
        f"Character: {char}, who is {hero}\n"
        f"Their world: {setting}\n"
        f"Things in their world: {objects}\n"
        f"DESTINATION (the goal of the whole quest): {destination}\n"
        f"Child's age: {age}\n"
        f"Math topic woven through it: {topic or 'number puzzles'}\n"
        f"STORY COMPLEXITY: {complexity} out of 5. {_REGISTER_BRIEF[register]}\n\n"
        f"The skeleton has exactly {len(skeleton)} stops, in this order:\n{stops}\n\n"
        f"Return exactly {len(skeleton)} beats, one per stop, in the same order."
    )

    try:
        resp = chat(
            client,
            model=TEXT_MODEL,
            messages=[{"role": "system", "content": _STORY_SYSTEM},
                      {"role": "user", "content": user}],
            max_completion_tokens=3000,
        )
        data = _parse_json_loose(resp.choices[0].message.content)
    except Exception as exc:
        note_error(exc)
        return None

    beats = data.get("beats")
    if not isinstance(beats, list) or len(beats) < len(skeleton):
        # A short outline would silently truncate the arc - drop the whole
        # thing and let the authored template run instead.
        note_error(ValueError(
            f"storyline returned {len(beats) if isinstance(beats, list) else 0} "
            f"beats, needed {len(skeleton)}"))
        return None

    out_beats = []
    for i, src in enumerate(beats[:len(skeleton)]):
        src = src if isinstance(src, dict) else {}
        out_beats.append({
            "title": sanitize_story_text(src.get("title")) or skeleton[i]["fallback_title"],
            "location": sanitize_story_text(src.get("location")) or setting,
            "intro": sanitize_story_text(src.get("intro")),
            "on_success": sanitize_story_text(src.get("on_success")),
            "advances": sanitize_story_text(src.get("advances")),
        })
        if not out_beats[-1]["intro"]:
            return None  # an empty beat is worse than an authored one

    return {
        "title": sanitize_story_text(data.get("title")) or "The Long Way There",
        "goal_text": sanitize_story_text(data.get("goal_text")) or destination,
        "opening": sanitize_story_text(data.get("opening")),
        "epilogue": sanitize_story_text(data.get("epilogue")),
        # The noun the quest is ABOUT. math_engine writes its word problems
        # around this, so a quest about collecting rods asks about rods.
        "treasure": clean_treasure(data.get("treasure")),
        "beats": out_beats,
        "source": "openai",
    }


# --------------------------------------------------- the quest's own noun
#
# "if the story is about collecting rods, make it such that bun bun the main
#  bunny is getting rods and doing multiplication - the context is the same"
#
# The model names the thing the quest is about; math_engine writes its word
# problems around that noun. The NUMBERS and the ANSWER KEY never leave
# Python - this is wording only, which is why it is safe to let a model pick
# it. A noun that is not a plain countable plural is rejected outright.

_TREASURE_BAD = {
    "treasure", "treasures", "things", "stuff", "items", "objects", "points",
    "friends", "people", "children", "memories", "adventures", "dreams",
}


def clean_treasure(raw) -> str:
    """Normalise the model's noun, or "" if it is unusable."""
    text = " ".join(str(raw or "").lower().split())
    text = text.strip(" .!,'\"")
    if not text or len(text) > 24:
        return ""
    if len(text.split()) > 2:
        return ""
    if not all(c.isalpha() or c in "- " for c in text):
        return ""
    if text in _TREASURE_BAD:
        return ""
    if not text.endswith("s"):                 # must be countable and plural
        return ""
    return text


# ------------------------------------------------------------- world images

def _scenery_objects(interpretation: dict) -> str:
    """Objects for a BACKGROUND, with the hero removed.

    The hero is composited on top as a sprite, so if they also appear painted
    into the scenery the player sees two of their character at once.
    """
    hero = (interpretation.get("main_character") or "").lower().strip()
    hero_words = {w for w in hero.split() if len(w) > 2}
    keep = []
    for obj in interpretation.get("objects", []):
        low = (obj or "").lower()
        if hero and (low in hero or hero in low):
            continue
        if hero_words and any(w in low for w in hero_words):
            continue
        keep.append(obj)
    return ", ".join(keep[:6]) or "a tree, a winding path"


_ARTICLES = {"a", "an", "the", "some", "one"}


def strip_hero(text: str, interpretation: dict) -> str:
    """Remove the hero from a phrase headed for a BACKGROUND prompt.

    A child who types "a bunny by a river" makes `setting` literally contain
    their character, and `setting` is pasted into the background prompt - so
    without this the sprite gets a painted-on twin. The no-characters rule in
    the prompt mostly holds the line, but naming the species right next to it
    is asking for trouble.
    """
    hero = (interpretation.get("main_character") or "").lower()
    words = {w.strip(".,!?'\"") for w in hero.split() if len(w) > 2} - _ARTICLES
    if not words or not text:
        return text
    out = []
    for token in text.split():
        bare = token.lower().strip(".,!?'\"")
        if bare in words or any(w in bare for w in words if len(w) > 3):
            while out and out[-1].lower().strip(".,") in _ARTICLES:
                out.pop()
            continue
        out.append(token)
    cleaned = " ".join(out).strip(" ,")
    return cleaned if len(cleaned.split()) >= 2 else ""


def _world_prompt(interpretation: dict, theme: str, scene_kind: str,
                  scene_brief: str | None = None) -> str:
    style = THEMES.get(theme, THEMES["storybook"])
    setting = interpretation.get("setting", "a meadow")

    if scene_kind == "character":
        hero = interpretation.get("main_character") or "a friendly animal"
        return (
            "A single full-body game character sprite on a COMPLETELY TRANSPARENT "
            "background - no scenery, no ground, no sky, no backdrop of any kind, "
            "just the character cut out. Facing the viewer, centered, brave and "
            f"likeable. The character is: {hero}. Style: {style}. {ART_DIRECTION}. "
            "Thick confident outlines, no text or words anywhere."
        )

    # Background / story frame.
    rels = "; ".join([
        r for r in interpretation.get("relationships", [])[:4]
        if not _mentions_hero(r, interpretation)
    ])
    rel_txt = f" Composition: {rels}." if rels else ""

    # The child's spatial arrangement is the whole point of "your drawing
    # becomes the game" - without an explicit left/right instruction the
    # image model mirrors or reshuffles their layout.
    layout = "; ".join([
        l for l in interpretation.get("layout", [])[:5]
        if l and not _mentions_hero(l, interpretation)
    ])
    layout_txt = (
        f" CRITICAL - keep the child's exact arrangement, do not mirror or "
        f"rearrange it: {layout}." if layout else ""
    )
    rel_txt += layout_txt
    setting = strip_hero(setting, interpretation) or _scenery_objects(interpretation)
    scene_brief = strip_hero(scene_brief or "", interpretation)
    scene_txt = f" This particular view shows: {scene_brief}." if scene_brief else ""

    return (
        "A wide 2D side-scrolling video game background - EMPTY SCENERY ONLY. "
        "ABSOLUTELY NO characters, no people, no animals, no creatures anywhere "
        "in the image: the player's character is drawn separately on top. "
        "Leave the lower third open and uncluttered so a character can walk across it "
        "- the game draws the question over that band, so keep it simple there. "
        f"Style: {style}. {ART_DIRECTION}. No text or words anywhere. "
        f"The scene contains: {_scenery_objects(interpretation)}. "
        f"Setting: {setting}.{rel_txt}{scene_txt} "
        "Whimsical and hand-made in spirit, but polished."
    )


def _mentions_hero(text: str, interpretation: dict) -> bool:
    hero = (interpretation.get("main_character") or "").lower()
    if not hero:
        return False
    low = (text or "").lower()
    return any(w in low for w in hero.split() if len(w) > 2)


def generate_world_image(interpretation: dict, theme: str = "storybook", scene_kind: str = "background"):
    """Generate one polished game-world image. Returns a data URL or None."""
    client = get_image_client()
    if client is None:
        return None
    try:
        size = "1536x1024" if scene_kind == "background" else "1024x1024"
        # A sprite is composited onto the world, so it MUST be a cutout. On
        # OpenAI that is `background="transparent"`; Muse has no such mode, so
        # `_image_result` keys the flat background out with Pillow instead.
        transparent = scene_kind == "character"
        prompt = _world_prompt(interpretation, theme, scene_kind)
        if transparent and using_muse():
            prompt += (" The character must sit ALONE on a completely plain, "
                       "flat, pure white background - no scene, no ground, no "
                       "shadow, no border, no frame, nothing behind them.")
        resp = client.images.generate(
            model=IMAGE_MODEL, prompt=prompt, size=size, n=1,
            **_image_kwargs(transparent),
        )
        return _image_result(resp, transparent)
    except Exception as exc:
        note_error(exc)
        return None


# --------------------------------------------------------- journey frames
#
# V2 item 5: the world repaints every ~2 beats so the child can SEE the
# journey advancing. Frame 1 is a plain generation; every frame after it is
# an images.edit seeded with the frame before it.
#
# Why edit-on-previous rather than generate-with-a-matching-prompt: measured
# side by side, a fresh generation from a style description produces the
# right *genre* but a different *place* - new palette, new horizon, new
# buildings - and the crossfade reads as teleporting to another game. Seeding
# with the previous PNG keeps the far-shore village, the hill line, the
# water colour and the brush texture, and moves the camera down the path.
# Cost is identical and latency only ~2s worse (see REPORT in the task log).
#
# Drift is real over long chains, which is one more reason frames are capped.

_CONTINUITY_RULES = (
    "Continue this EXACT illustrated world into the NEXT PLACE along the same "
    "journey. Keep the identical art style, colour palette, brush texture, line "
    "weight and world - a child must read it as the same storybook world. "
    "But this is a DIFFERENT LOCATION: the traveller has walked a long way since "
    "the previous picture, so the camera has MOVED with them. Do not reproduce "
    "the previous composition. Change the vantage point and the foreground. "
    "Whatever was close before is now small, behind, or out of frame entirely, "
    "and the destination must be visibly NEARER and LARGER than it was. "
)

# How big the destination should look, by how far along the journey we are.
# Without this the edit model faithfully preserves the previous composition -
# the style stays perfect and the child never actually gets anywhere.
_DISTANCE_CUES = (
    (0.20, "small and far away near the horizon"),
    (0.45, "clearly nearer than before, about half the distance, noticeably bigger"),
    (0.70, "close now, large in the middle distance, details starting to show"),
    (0.90, "very close and big, dominating the background"),
    (1.01, "RIGHT HERE in the foreground, filling the frame - this is the arrival"),
)


def _distance_cue(progress: float) -> str:
    for upto, cue in _DISTANCE_CUES:
        if progress < upto:
            return cue
    return _DISTANCE_CUES[-1][1]


def _frame_prompt(interpretation: dict, theme: str, scene_brief: str | None,
                  goal_text: str | None, continuing: bool,
                  progress: float | None = None) -> str:
    base = _world_prompt(interpretation, theme, "background", scene_brief)
    goal_txt = ""
    if goal_text:
        goal_txt = f" The journey's destination is {goal_text}."
        if progress is not None:
            goal_txt += (f" The traveller is about {round(progress * 100)}% of the "
                         f"way there, so {goal_text} must appear "
                         f"{_distance_cue(progress)}.")
    if continuing:
        return _CONTINUITY_RULES + base + goal_txt
    return base + goal_txt


def _data_url_to_file(data_url: str, name: str = "previous_frame.png"):
    """data: URL -> a named BytesIO the images API will accept as an upload.

    The bytes are re-encoded to real PNG first. Muse returns webp by default,
    and uploading webp bytes under a .png name fails the edit call with
    "MIME type `image/webp` does not match declared MIME type `image/png`" -
    which silently kills frame continuity and accessory art.
    """
    if not data_url or not data_url.startswith("data:"):
        return None
    try:
        raw = base64.b64decode(data_url.split(",", 1)[1])
    except Exception:
        return None

    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
        out = io.BytesIO()
        im.save(out, format="PNG")
        raw = out.getvalue()
    except Exception:
        # Pillow missing or bytes already fine - fall back to as-is rather
        # than losing the edit entirely.
        pass

    buf = io.BytesIO(raw)
    buf.name = name if name.endswith(".png") else f"{name}.png"
    return buf


def generate_frame(interpretation: dict, theme: str = "storybook",
                   scene_brief: str | None = None,
                   previous_frame: str | None = None,
                   goal_text: str | None = None,
                   progress: float | None = None) -> str | None:
    """One journey background. Returns a data URL, or None if AI is down.

    `previous_frame` is the data URL of the frame before this one. When it is
    present the frame is produced by editing it, which is what makes the
    sequence look like one continuous world. `progress` (0-1) is how far along
    the journey this frame sits, and decides how close the destination looks.
    """
    client = get_image_client()
    if client is None:
        return None

    prev = _data_url_to_file(previous_frame) if previous_frame else None
    prompt = _frame_prompt(interpretation, theme, scene_brief, goal_text,
                           continuing=prev is not None, progress=progress)
    try:
        if prev is not None:
            resp = client.images.edit(
                model=IMAGE_MODEL, image=prev, prompt=prompt,
                size="1536x1024", n=1, **_image_kwargs())
        else:
            resp = client.images.generate(
                model=IMAGE_MODEL, prompt=prompt,
                size="1536x1024", n=1, **_image_kwargs())
        return _image_result(resp)
    except Exception as exc:
        note_error(exc)
        # A failed EDIT must not cost the child their frame - fall back to a
        # plain generation, which at least keeps the style family.
        if previous_frame:
            try:
                resp = client.images.generate(
                    model=IMAGE_MODEL,
                    prompt=_frame_prompt(interpretation, theme, scene_brief,
                                         goal_text, continuing=False,
                                         progress=progress),
                    size="1536x1024", n=1, **_image_kwargs())
                got = _image_result(resp)
                if got:
                    return got
            except Exception as exc2:
                note_error(exc2)
        return None


# ------------------------------------------------------------- narration

_NARRATE_SYSTEM = """You are a warm, playful storyteller for children aged 4-10.
Rewrite a math problem as 1-2 short sentences of adventure story starring their character.
Rules: keep ALL numbers exactly as given, never state or hint at the answer, use simple words,
stay under 30 words, and sound excited. Return only the sentences, no quotes."""


def narrate_challenge(challenge: dict, interpretation: dict) -> str:
    """Wrap a math challenge in story narration. Falls back to the built-in text."""
    client = get_client()
    fallback = challenge.get("narrative", challenge.get("prompt", ""))
    if client is None:
        return fallback

    char = challenge.get("character_name", "our hero")
    setting = interpretation.get("setting", "a meadow")
    try:
        resp = chat(
            client,
            model=TEXT_MODEL,
            messages=[
                {"role": "system", "content": _NARRATE_SYSTEM},
                {"role": "user", "content": (
                    f"Character: {char}\nWorld: {setting}\n"
                    f"Math problem: {challenge.get('prompt')}\n"
                    f"Challenge type: {challenge.get('question_type')}"
                )},
            ],
            max_completion_tokens=300,
        )
        text = (resp.choices[0].message.content or "").strip()
        return text or fallback
    except Exception:
        return fallback


_CHEERS = [
    "You did it! {char} is so proud!",
    "Amazing work! {char} cheers for you!",
    "Yes! {char} hops with joy!",
    "Perfect! {char} found the way!",
    "Brilliant! {char} is glowing with happiness!",
]
_ENCOURAGE = [
    "So close! {char} believes in you. Let's try again!",
    "Not quite - but {char} says brave explorers always retry!",
    "Ooh, almost! {char} is right here with you. One more go!",
    "That's okay! Every try makes {char} stronger. Again!",
]


def feedback_line(was_correct: bool, character_name: str) -> str:
    pool = _CHEERS if was_correct else _ENCOURAGE
    return random.choice(pool).format(char=character_name)


# ============================================================= ACCESSORIES
#
# "every 5 problems u get right, you should be able to go and add an accessory
#  to the character, like draw one (backpack, hat, sunglasses, earrings,
#  necklace, feather boa, tie, etc)!!! and the kid would draw it on a small
#  screen."
#
# The child draws the thing; vision turns their doodle into a short phrase;
# images.edit puts it ON the character. Two decisions worth knowing about:
#
# ALWAYS EDIT FROM THE ORIGINAL SPRITE, never from the last edited one.
#   Chaining edit-on-edit drifts: by the third accessory the character has
#   quietly changed colour and lost its face. Editing the ORIGINAL every time
#   with the full cumulative list ("wearing a red pointy hat and round
#   sunglasses") keeps the child's character recognisably theirs however many
#   accessories they earn. Cost is identical - one edit either way.
#
# NOTHING HERE IS ALLOWED TO BLOCK A CHILD.
#   Every entry point returns None on any failure, the caller keeps the
#   sprite it already had, and the frontend has already drawn the child's own
#   strokes on the character anyway - so the reward lands instantly and the
#   polished version swaps in ~10s later if it arrives at all.

# Where an accessory sits, so the offline path can anchor the doodle and the
# prompt can be specific. The child picks one; the value is a short id only.
ACCESSORY_SLOTS = {
    "hat": "on top of the head",
    "face": "over the eyes",
    "neck": "around the neck",
    "back": "worn on the back",
    "hand": "held in one hand",
    "body": "worn on the body",
}
DEFAULT_ACCESSORY_SLOT = "hat"

_ACCESSORY_VISION = (
    "A child has drawn a single accessory for their character. In 12 words or "
    "fewer, describe ONLY the object: what it is, its colours and any pattern. "
    "Examples: 'a red pointy wizard hat with yellow stars', 'round blue "
    "sunglasses', 'a green stripy backpack'. If you cannot tell what it is, "
    "describe its shape and colours instead. Return the phrase only - no "
    "sentence, no quotes, no mention of the character."
)


def describe_accessory(image_data_url: str | None, slot: str = "") -> str | None:
    """A child's accessory doodle -> a short phrase. None if AI is unavailable."""
    client = get_client()
    if client is None or not image_data_url:
        return None
    where = ACCESSORY_SLOTS.get(slot or "", "")
    try:
        resp = chat(
            client,
            model=VISION_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text",
                     "text": _ACCESSORY_VISION + (f" It is worn {where}." if where else "")},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            }],
            max_completion_tokens=60,
        )
        text = (resp.choices[0].message.content or "").strip()
        text = " ".join(text.replace("\n", " ").split())[:90].strip(' ."\'')
        return text or None
    except Exception as exc:
        note_error(exc)
        return None


def _accessory_prompt(interpretation: dict, accessories: list[dict]) -> str:
    """One prompt describing the character wearing EVERY accessory so far."""
    who = (interpretation or {}).get("main_character") or "the character"
    bits = []
    for acc in accessories or []:
        desc = (acc or {}).get("description") or ""
        where = ACCESSORY_SLOTS.get((acc or {}).get("slot") or "", "")
        if desc:
            bits.append(f"{desc} {where}".strip())
    worn = "; ".join(bits) if bits else "a small accessory"
    return (
        f"The SAME {who} from this image, completely unchanged - identical "
        f"pose, proportions, colours, face and art style - now wearing: {worn}. "
        f"Add ONLY the accessories; do not redraw, restyle or replace the "
        f"character, and do not change its expression. Keep it a clean cutout "
        f"on a fully transparent background with no scene, no ground, no "
        f"shadow and no border."
    )


def generate_accessorised_sprite(interpretation: dict,
                                 original_sprite: str | None,
                                 accessories: list[dict]) -> str | None:
    """The child's character wearing everything it has earned. None on failure.

    `original_sprite` is the sprite as first generated, NOT the last
    accessorised one - see the note at the top of this section. `accessories`
    is the full cumulative list, so nothing earned ever gets dropped.
    """
    client = get_image_client()
    if client is None or not original_sprite or not accessories:
        return None
    base = _data_url_to_file(original_sprite, "character.png")
    if base is None:
        return None
    try:
        resp = client.images.edit(
            model=IMAGE_MODEL, image=base,
            prompt=_accessory_prompt(interpretation, accessories),
            size="1024x1024", n=1, **_image_kwargs(transparent=True))
        return _image_result(resp, transparent=True)
    except Exception as exc:
        note_error(exc)
        return None


# ===================================================== STORY-FRAMED QUESTIONS
#
# "have the questions be part of the story (in order to get past the gate the
#  cat must pay 2 dimes and 2 quarters, how much is that)"
# "the questions are not story relevant (use the open ai api to generate those
#  questions with your story!!)"
#
# math_engine generates "2 x 12 = ?" and owns the answer key. This asks the
# model to say the SAME SUM in the language of the scene the child is looking
# at - "Bun Bun needs 2 bundles of 12 rods to pay the gate. How many rods?"
#
# THE MODEL NEVER COMPUTES ANYTHING, and that is enforced rather than hoped
# for. `_verify_restyle` rejects the rewrite outright unless:
#   * it contains EXACTLY the same multiset of numbers as the original;
#   * it does not contain the answer (unless the original already did);
#   * it still asks a question, in <= 14 words, with no placeholders.
# A rejected rewrite costs nothing - the deterministic prompt is used, which
# is what happens anyway with no API key.

# A story-framed question legitimately needs a few more words than a bare
# sum ('2 x 12 = ?' is 5 words; naming the scene costs about ten more).
# The question box is full-width, so 16 still fits on one line.
RESTYLE_MAX_WORDS = 16
# Only retry a rejected rewrite if the first call came back inside this.
RESTYLE_RETRY_BUDGET_S = float(os.getenv("DQ_RESTYLE_RETRY_BUDGET_S", "1.2"))

_RESTYLE_SYSTEM = """You rewrite a maths question so it belongs to the story a child is playing.

You are given: the scene, the character, the obstacle in their way, and a MATHS QUESTION.
Rewrite the question so it is about that scene and that obstacle.

ABSOLUTE RULES - breaking any one of them makes your answer useless and it is thrown away:
1. NUMBERS: use exactly the numbers in the original, every one of them, and no others. Copy them digit for digit. Never add a number (not even "1" or "each"), never drop one, never change one. Do not write any number as a word.
2. NEVER state, compute or hint at the answer.
3. LENGTH: 16 words MAXIMUM, including the question. Count them. Shorter is better. Do not name the character if you are close to the limit.
4. End with "?" and make it answerable with one number.
5. Plain words a child can read. No curly braces, no quotes, no markdown, no units the original did not use.

GOOD (original "2 x 12 = ?", scene: a gate, collecting rods):
  The gate wants 2 bundles of 12 rods. How many rods?
GOOD (original "4 pennies and 2 nickels. How many cents?"):
  Pay the gate 4 pennies and 2 nickels. How many cents?
BAD - added a number: The gate wants 2 bundles of 12 rods each. How many 1 total?
BAD - gave the answer: The gate wants 24 rods, which is 2 bundles of 12.
BAD - too long: Bun Bun the brave little bunny needs to tie together 2 whole bundles of 12 rods to pay the gate-keeper. How many rods is that?

Return ONLY the rewritten question."""


def _nums(text: str) -> list:
    """Every number in a string, as a sorted multiset of strings."""
    return sorted(re.findall(r"\d+(?:\.\d+)?", text or ""))


def _verify_restyle(original: str, rewrite: str, answer) -> str | None:
    """Return the rewrite only if it is provably the same question."""
    text = " ".join(str(rewrite or "").split()).strip().strip('"“”')
    if not text or "{" in text or "}" in text:
        return None
    if len(text.split()) > RESTYLE_MAX_WORDS:
        return None
    if "?" not in text:
        return None
    if _nums(text) != _nums(original):
        return None
    # The answer must not have been handed over. Only checked when the answer
    # is not already one of the numbers in the question (it sometimes is, e.g.
    # "which is bigger: 32 or 74?").
    if answer is not None:
        try:
            shown = _nums(original)
            for form in (f"{float(answer):g}", str(int(round(float(answer))))):
                if form not in shown and form in _nums(text):
                    return None
        except (TypeError, ValueError):
            pass
    return text


def restyle_challenge(challenge: dict, interpretation: dict,
                      obstacle: dict | None = None,
                      treasure: str = "", beat_intro: str = "") -> dict:
    """Say the same sum in the language of the scene. Never changes the maths.

    Returns {"prompt": str, "narrative": str}. Both fall back to what
    math_engine already produced, so this is safe to call unconditionally.
    """
    original = challenge.get("prompt") or ""
    out = {"prompt": original,
           "narrative": challenge.get("narrative") or original}
    client = get_client()
    if client is None or not original:
        return out

    char = challenge.get("character_name", "our hero")
    setting = (interpretation or {}).get("setting", "a meadow")
    ob = obstacle or {}
    scene = (
        f"Character: {char}\n"
        f"World: {setting}\n"
        f"What the quest is about collecting: {treasure or 'supplies'}\n"
        f"The obstacle in their way right now: {ob.get('title') or 'the way ahead'}"
        f" - {ob.get('locked_text') or 'it blocks the path'}\n"
        f"What just happened: {beat_intro or ''}\n"
        f"MATHS QUESTION (rewrite this): {original}"
    )
    messages = [{"role": "system", "content": _RESTYLE_SYSTEM},
                {"role": "user", "content": scene}]
    # A beat is only allowed a couple of seconds of model time in total (see
    # app.RESTYLE_MAX_BLOCK_S). Spend it on a retry only if the first reply
    # left room for one.
    started = time.time()
    # Up to two goes. Most rejections are one fixable slip - a stray "each",
    # three words over - and telling the model exactly what it broke fixes it
    # far more often than not. The second call only happens when the first
    # was going to be thrown away anyway.
    for attempt in range(2):
        try:
            resp = chat(client, model=TEXT_MODEL, messages=messages,
                        max_completion_tokens=200)
            candidate = (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            note_error(exc)
            return out

        good = _verify_restyle(original, candidate, challenge.get("answer_value"))
        if good:
            out["prompt"] = good
            out["restyled"] = True
            return out
        if attempt == 0 and (time.time() - started) < RESTYLE_RETRY_BUDGET_S:
            messages += [
                {"role": "assistant", "content": candidate},
                {"role": "user", "content": (
                    f"Rejected. It must contain exactly these numbers and no "
                    f"others: {', '.join(_nums(original)) or 'none'}. It must be "
                    f"{RESTYLE_MAX_WORDS} words or fewer and end with '?'. "
                    f"Try again, shorter.")},
            ]
    return out


# ============================================== OBSTACLE PROSE, WRITTEN BY AI
#
# "i told you to remove the hard coded words in the phrases like 'thorns the
#  size of a fingernail', can we please get rid of this, and instead have
#  generation with the api for everything"
#
# Every obstacle used to carry one authored blocked/cleared line, so two
# children in two different worlds read the identical sentence. Now the model
# writes them - ALL of them, for the whole quest, in ONE call fired in the
# background while the child is still on beat one. By the time they reach the
# second obstacle it has landed.
#
# The authored lines survive ONLY as the no-API-key fallback. That path is a
# first-class supported state (the whole game is playable offline) so it
# cannot be deleted - but with a key, nothing hardcoded reaches a child.

_OBSTACLE_SYSTEM = """You write the two lines a child reads at an obstacle in their own adventure.

For each obstacle you are given: what KIND of thing it is, and the world the child drew.

Write, for each:
  "blocked" - 1-2 short sentences. What is in the way, and why they cannot simply walk past. Present tense. End on the problem, not the solution.
  "cleared" - 1 short sentence. The moment they get past it. Past tense. Warm, a little triumphant.

RULES
- Write {char} instead of the character's name. It is the only placeholder allowed.
- Make every obstacle sound like it belongs in THIS child's world, not a generic one.
- Never mention numbers, maths, questions or answers.
- Never repeat a phrase between obstacles. Each one gets its own images.
- Age-appropriate for 4-10: exciting, never frightening. Nothing is scary, nobody gets hurt.
- Short words. No metaphors a seven-year-old would not use.

Respond with ONLY valid JSON, no markdown fences:
{"obstacles": {"<kind>": {"blocked": "...", "cleared": "..."}, ...}}
Use exactly the kind strings you were given as the keys."""


def generate_obstacle_prose(interpretation: dict, kinds, obstacles: dict,
                            register: str = "rich") -> dict:
    """Bespoke blocked/cleared lines for every obstacle in one quest.

    Returns {kind: {"blocked":..., "cleared":...}}, or {} when unavailable -
    and {} means "keep the authored fallback", never "leave it blank".
    """
    client = get_client()
    kinds = [k for k in dict.fromkeys(kinds or []) if k]
    if client is None or not kinds:
        return {}

    char = (interpretation or {}).get("character_name") or "our hero"
    hero = (interpretation or {}).get("main_character") or "a small creature"
    setting = (interpretation or {}).get("setting") or "a green valley"
    objects = ", ".join((interpretation or {}).get("objects", [])[:6])
    listed = "\n".join(
        f'  - {k}: {(obstacles.get(k) or {}).get("title", k)} '
        f'({(obstacles.get(k) or {}).get("action", "cross")})'
        for k in kinds)
    user = (
        f"Character: {char}, who is {hero}\n"
        f"Their world: {setting}\n"
        f"Things in their world: {objects}\n"
        f"Voice: {_REGISTER_BRIEF.get(register, _REGISTER_BRIEF['rich'])}\n\n"
        f"Write lines for these {len(kinds)} obstacles:\n{listed}\n"
    )
    try:
        resp = chat(
            client, model=TEXT_MODEL,
            messages=[{"role": "system", "content": _OBSTACLE_SYSTEM},
                      {"role": "user", "content": user}],
            max_completion_tokens=2000,
        )
        data = _parse_json_loose(resp.choices[0].message.content)
    except Exception as exc:
        note_error(exc)
        return {}

    raw = (data or {}).get("obstacles")
    if not isinstance(raw, dict):
        return {}
    out = {}
    for kind in kinds:
        entry = raw.get(kind)
        if not isinstance(entry, dict):
            continue
        blocked = sanitize_story_text(entry.get("blocked"))
        cleared = sanitize_story_text(entry.get("cleared"))
        # A half-written obstacle is worse than the authored one.
        if len(blocked) > 12 and len(cleared) > 8:
            out[kind] = {"blocked": blocked, "cleared": cleared}
    return out
