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

# Model IDs verified current 2026-09-19. gpt-image-1 and the gpt-4o line are
# superseded; gpt-image-1 in particular shuts down 2026-12-01.
# IMAGE_QUALITY "low" is ~15x cheaper AND several times faster than "high" -
# for a crayon-drawing aesthetic the difference is near-invisible, and latency
# is the real constraint in a live demo.
VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-5.6-luna")
IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2.5-flare")
TEXT_MODEL = os.getenv("OPENAI_TEXT_MODEL", "gpt-5.6-luna")
IMAGE_QUALITY = os.getenv("OPENAI_IMAGE_QUALITY", "low")

THEMES = {
    "storybook": "soft pastel storybook illustration, watercolor textures, gentle rounded shapes",
    "candy": "candy-colored dreamland, cotton-candy clouds, glossy pastel sweets",
    "forest": "cozy pastel woodland, soft moss greens and warm creams, dappled light",
    "ocean": "gentle pastel underwater world, soft aquas and corals, floating bubbles",
    "space": "dreamy pastel outer space, soft lavender nebulas, friendly little stars",
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
  "story_hook": "one warm, exciting sentence to open the adventure, addressed to the child"
}

The first item in "objects" must be the main character."""


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

RULES
- Write {char} instead of the character's name. This is the only placeholder allowed in "title", "location", "intro" and "advances".
- In "on_success" for a stop whose role mentions GATHER, you may also write "{gain} {subject}" - it becomes e.g. "7 berries". Use it at most once.
- "epilogue" may use {char} and {haul}. Nothing else.
- Never write any other curly-brace token. Never use the character's literal name.
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


def generate_storyline(interpretation: dict, band: str, destination: str,
                       skeleton: list[dict], topic: str = "") -> dict | None:
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

    stops = "\n".join(
        f'  {i + 1}. role: {s["role"]}' for i, s in enumerate(skeleton))

    user = (
        f"Character: {char}, who is {hero}\n"
        f"Their world: {setting}\n"
        f"Things in their world: {objects}\n"
        f"DESTINATION (the goal of the whole quest): {destination}\n"
        f"Child's age: {age}\n"
        f"Math topic woven through it: {topic or 'number puzzles'}\n\n"
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
        "beats": out_beats,
        "source": "openai",
    }


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
            "just the character cut out. Facing the viewer, centered, friendly and "
            f"huggable. The character is: {hero}. Style: {style}. Cute cartoony "
            "children's game art for ages 4-10, soft pastel palette, thick friendly "
            "outlines, no text or words anywhere."
        )

    # Background / story frame.
    rels = "; ".join([
        r for r in interpretation.get("relationships", [])[:4]
        if not _mentions_hero(r, interpretation)
    ])
    rel_txt = f" Composition: {rels}." if rels else ""
    setting = strip_hero(setting, interpretation) or _scenery_objects(interpretation)
    scene_brief = strip_hero(scene_brief or "", interpretation)
    scene_txt = f" This particular view shows: {scene_brief}." if scene_brief else ""

    return (
        "A wide 2D side-scrolling video game background - EMPTY SCENERY ONLY. "
        "ABSOLUTELY NO characters, no people, no animals, no creatures anywhere "
        "in the image: the player's character is drawn separately on top. "
        "Leave the lower third open and uncluttered so a character can walk across it. "
        f"Style: {style}. Cute cartoony children's game art for ages 4-10, soft "
        "pastel palette, thick friendly outlines, no text or words anywhere. "
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
    client = get_client()
    if client is None:
        return None
    try:
        size = "1536x1024" if scene_kind == "background" else "1024x1024"
        kwargs = {}
        if scene_kind == "character":
            # A sprite gets composited onto the world, so it MUST be a cutout.
            # Without this the model paints a full scene behind the character
            # and the game canvas ends up with a picture-in-picture.
            kwargs["background"] = "transparent"
            kwargs["output_format"] = "png"
        resp = client.images.generate(
            model=IMAGE_MODEL,
            prompt=_world_prompt(interpretation, theme, scene_kind),
            size=size,
            quality=IMAGE_QUALITY,
            n=1,
            **kwargs,
        )
        item = resp.data[0]
        b64 = getattr(item, "b64_json", None)
        if b64:
            return f"data:image/png;base64,{b64}"
        url = getattr(item, "url", None)
        return url
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


def _data_url_to_file(data_url: str):
    """data: URL -> a named BytesIO the images API will accept as an upload."""
    if not data_url or not data_url.startswith("data:"):
        return None
    try:
        raw = base64.b64decode(data_url.split(",", 1)[1])
    except Exception:
        return None
    buf = io.BytesIO(raw)
    buf.name = "previous_frame.png"
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
    client = get_client()
    if client is None:
        return None

    prev = _data_url_to_file(previous_frame) if previous_frame else None
    prompt = _frame_prompt(interpretation, theme, scene_brief, goal_text,
                           continuing=prev is not None, progress=progress)
    try:
        if prev is not None:
            resp = client.images.edit(
                model=IMAGE_MODEL, image=prev, prompt=prompt,
                size="1536x1024", quality=IMAGE_QUALITY, n=1,
            )
        else:
            resp = client.images.generate(
                model=IMAGE_MODEL, prompt=prompt,
                size="1536x1024", quality=IMAGE_QUALITY, n=1,
            )
        item = resp.data[0]
        b64 = getattr(item, "b64_json", None)
        if b64:
            return f"data:image/png;base64,{b64}"
        return getattr(item, "url", None)
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
                    size="1536x1024", quality=IMAGE_QUALITY, n=1,
                )
                b64 = getattr(resp.data[0], "b64_json", None)
                if b64:
                    return f"data:image/png;base64,{b64}"
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
