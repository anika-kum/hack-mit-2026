"""
do-IT-oodle - FastAPI backend.

Serves the API and the static frontend from a single process:
    uvicorn app:app --reload --port 8000   (run from the backend/ directory)

Three layers, in order of how much we trust them:
    math_engine.py   deterministic math + visual layout   (always correct)
    story_engine.py  deterministic quest arc + narration  (always available)
    prompts.py       OpenAI embellishment                 (optional, degrades)

The game is fully playable with the AI layer completely dead.


V2 API CONTRACT (what the frontend consumes)
--------------------------------------------
POST /api/create-world
  in : image_data_url?, text_description?, theme, generate_images,
       destination?   <- NEW, free text, e.g. "cross the river"
       band?          <- NEW, optional hint so the storyline is pitched right
  out: session_id, interpretation, background, character_sprite,
       destination     the goal as asked for ("cross the river and reach X")
       goal_text       the PLACE inside it ("X") - use this in the UI
       destination_source  "child" | "inferred"
       frame {id, index:0, ready, frames_max, frame_every_beats}

POST /api/start-game   (unchanged inputs)
  out: ... plus goal_text, journey_progress, distance_remaining,
       distance_text, story_source ("openai" | "template")

Every challenge payload (start-game, next-beat) now carries:
  challenge.beat.goal_text / location / advances
  challenge.beat.journey_progress (0-1) / distance_remaining / distance_text
  challenge.frame {
     id, index, wanted_index, ready, is_new,
     image      base64 data URL, sent ONCE - on the beat where this frame
                first becomes current. null means "keep showing what you have"
     image_url  /api/frame/{id}/image - re-fetch any time, cacheable
     next_id / next_status / next_url
                non-null => a newer frame is rendering. Poll next_url and
                CROSSFADE to it when status flips to "ready". Never blank the
                screen: the current frame stays up until the new one lands.
     frames_used / frames_max
  }

POST /api/answer  -> ... plus progress_line, journey_progress,
                     distance_remaining, distance_text, goal_text
POST /api/next-beat on completion -> ... plus arrival_frame (same shape as
                     challenge.frame) and the journey block

GET /api/frame/{id}        {id,index,status,ready,image,image_url,
                            scene_brief,gen_seconds,error}
GET /api/frame/{id}/image  the raw PNG (200), or 409 while pending/failed
GET /api/session/{id}      ... plus frames[], frames_max, frame_every_beats,
                           goal_text, storyline_source, storyline_wait_s
"""

import base64
import os
import pathlib
import random
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv(pathlib.Path(__file__).parent / ".env")

import math_engine  # noqa: E402
import prompts  # noqa: E402
import question_bank  # noqa: E402
import story_engine  # noqa: E402

BASE_DIR = pathlib.Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"

app = FastAPI(title="Do-IT-oodle API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session store. Fine for a hackathon demo; swap for Redis to scale.
SESSIONS: dict[str, dict] = {}

# Fields that must never reach the browser: they are, or reconstruct, the
# answer key. Everything else in the challenge is renderable data.
#
# `answer_text` is the written form of a fraction answer ("1 1/3"). It is as
# much of a leak as answer_value is, so it is stripped here. `answer_form`
# ("fraction") is NOT secret - it is a formatting hint so the type-in box can
# tell the child to write a fraction, and it narrows nothing down.
SECRET_FIELDS = ("explanation", "answer_order", "order_cyclic", "answer_value",
                 "answer_text")
# Per-stone fields that ARE safe to ship - they are what makes the puzzle
# solvable by looking at it.
STONE_VISUAL_FIELDS = ("group", "tint", "shape", "dots", "x_pct", "y_pct")


# ------------------------------------------------------- V2 configuration
#
# Cost guard. At gpt-image-2.5-flare "low" a 1536x1024 frame is a few cents;
# MAX_FRAMES is the hard ceiling on how many a single session can ever buy,
# including the opening background (frame 0).
MAX_FRAMES = int(os.getenv("DQ_MAX_FRAMES", "6"))
# Repaint the world every N beats.
FRAME_EVERY_BEATS = int(os.getenv("DQ_FRAME_EVERY_BEATS", "2"))
# The bespoke storyline is prefetched at create-world and collected at
# start-game. It measures ~11s; create-world spends ~11-20s painting images
# right after firing it, and the child then spends a few seconds choosing a
# topic, so in the real flow start-game usually waits ZERO.
#   BUDGET  - total wall clock from the request; later than this and the
#             outline is abandoned for an authored template.
#   BLOCK   - the most /api/start-game will ever stall waiting for it, even
#             if the budget still had room. Gameplay never hangs on AI.
STORYLINE_BUDGET_S = float(os.getenv("DQ_STORYLINE_BUDGET_S", "30"))
STORYLINE_MAX_BLOCK_S = float(os.getenv("DQ_STORYLINE_MAX_BLOCK_S", "18"))
# How long a frame render will wait for the frame it is seeded from.
FRAME_SEED_WAIT_S = float(os.getenv("DQ_FRAME_SEED_WAIT_S", "40"))

# ------------------------------------------------------------ accessories
# "every 5 problems u get right, you should be able to go and add an
#  accessory to the character, like draw one!!!"
#
# Every ACCESSORY_EVERY CUMULATIVE correct answers - not per quest, so a
# child who restarts does not lose their progress toward the next one - the
# child is offered a small canvas and draws something their character then
# wears for the rest of the session. MAX_ACCESSORIES caps the cost and the
# sprite drift the same way MAX_FRAMES caps the journey art.
ACCESSORY_EVERY = int(os.getenv("DQ_ACCESSORY_EVERY", "5"))
MAX_ACCESSORIES = int(os.getenv("DQ_MAX_ACCESSORIES", "6"))

# Background work: image frames, sprites and storyline prefetch. One session
# occupies at most three workers, one of which may be parked waiting for the
# frame it continues from - so keep enough headroom that a handful of
# simultaneous children cannot starve each other.
_POOL = ThreadPoolExecutor(
    max_workers=int(os.getenv("DQ_WORKERS", "12")), thread_name_prefix="dq-bg")


# ------------------------------------------------------------------ models

class CreateWorldRequest(BaseModel):
    image_data_url: str | None = None
    text_description: str | None = None
    theme: str = "storybook"
    generate_images: bool = True
    # V2 item 3: where the child wants to GET to. Optional - when it is blank
    # a concrete destination is inferred from their setting.
    destination: str | None = None
    # Optional hint so the prefetched storyline can be pitched at the right
    # age. The authoritative band still arrives with /api/start-game.
    band: str | None = None


class StartGameRequest(BaseModel):
    session_id: str
    topic: str
    band: str


class AnswerRequest(BaseModel):
    session_id: str
    stone_ids: list[int] = []
    # Free response: whatever the child typed, exactly as typed. Graded
    # deterministically against `answer_value`, which never leaves the server.
    typed: str | None = None


class NextChallengeRequest(BaseModel):
    session_id: str


class AccessoryRequest(BaseModel):
    session_id: str
    # The child's own drawing, exactly as they drew it. The browser paints
    # this on the character IMMEDIATELY - the reward must land instantly and
    # must work with no API key at all - and the edited sprite, if the AI is
    # up, swaps in when it is ready.
    image_data_url: str | None = None
    slot: str = "hat"
    skipped: bool = False


# ------------------------------------------------------------------ routes

@app.get("/api/health")
def health():
    return {
        "ok": True,
        "ai_enabled": prompts.ai_available(),
        "vision_model": prompts.VISION_MODEL,
        "image_model": prompts.IMAGE_MODEL,
        "last_error": prompts.LAST_ERROR,
        "max_frames": MAX_FRAMES,
        "frame_every_beats": FRAME_EVERY_BEATS,
        "storyline_budget_s": STORYLINE_BUDGET_S,
        # The CSV question bank is optional. When it is absent every number
        # range falls back to the hard-coded one in math_engine, which is
        # exactly how the game shipped before the bank existed.
        "question_bank": question_bank.load().summary(),
        # Which CONCEPTS the bank gated in for each cell, and the one cell
        # where the gate had to be relaxed to keep a topic playable. Both are
        # here so the compromise is visible rather than folklore.
        "concepts": {
            f"{t}/{b}": math_engine.concepts_for(t, b)
            for t in math_engine.TOPICS
            for b in math_engine.TOPIC_BANDS.get(t, [])
        },
        "relaxed_cells": [f"{t}/{b}" for t, b in math_engine.RELAXED_CELLS],
    }


@app.get("/api/topics")
def topics():
    return {
        "topics": [
            {
                "id": tid,
                "label": label,
                "bands": [
                    {"id": b, **math_engine.BANDS[b],
                     "archetypes": math_engine.archetypes_for(tid, b)}
                    for b in math_engine.TOPIC_BANDS.get(tid, [])
                ],
            }
            for tid, label in math_engine.TOPICS.items()
        ],
        "themes": list(prompts.THEMES.keys()),
    }


# ------------------------------------------------------------ frame store
#
# V2 item 5. The world repaints every FRAME_EVERY_BEATS beats, and frame N+1
# is seeded with frame N so the child sees ONE world advancing rather than a
# slideshow of unrelated pictures.
#
# The whole point of this store is that no request ever waits on an image.
# The moment a frame is handed to the browser, the next one starts rendering
# in a worker thread; a beat that arrives before its frame is ready simply
# keeps showing the frame it already has and picks the new one up later. The
# child solves a problem in 10-40s, a frame takes ~11s, so in practice the
# next frame is always waiting.

def _frame_id(session_id: str, index: int) -> str:
    return f"{session_id}-f{index}"


def _new_frame_record(session_id: str, index: int, brief: str,
                      progress: float = 0.0) -> dict:
    return {
        "id": _frame_id(session_id, index),
        "index": index,
        "status": "pending",
        "image": None,
        "scene_brief": brief,
        "progress": progress,
        "requested_at": time.time(),
        "ready_at": None,
        "gen_seconds": None,
        "sent": False,
        "error": None,
    }


def _ready_image(session: dict, index: int) -> str | None:
    rec = session["frames"].get(index)
    return rec["image"] if rec and rec["status"] == "ready" else None


def _await_seed(session: dict, index: int) -> str | None:
    """Wait (bounded) for frame index-1, which this frame is painted from."""
    if index <= 0:
        return None
    deadline = time.time() + FRAME_SEED_WAIT_S
    while time.time() < deadline:
        prev = session["frames"].get(index - 1)
        if prev is None:
            return None            # nothing to continue from; paint fresh
        if prev["status"] == "ready":
            return prev["image"]
        if prev["status"] == "failed":
            # Reach further back rather than giving up on continuity.
            for j in range(index - 2, -1, -1):
                img = _ready_image(session, j)
                if img:
                    return img
            return None
        time.sleep(0.25)
    return None


def _render_frame(session: dict, rec: dict) -> None:
    seed = _await_seed(session, rec["index"])
    started = time.time()
    image = prompts.generate_frame(
        session["interpretation"],
        theme=session.get("theme", "storybook"),
        scene_brief=rec["scene_brief"],
        previous_frame=seed,
        goal_text=session.get("goal_text"),
        progress=rec.get("progress"),
    )
    rec["gen_seconds"] = round(time.time() - started, 2)
    rec["ready_at"] = time.time()
    rec["seeded_from"] = rec["index"] - 1 if seed else None
    if image:
        rec["image"] = image
        rec["status"] = "ready"
    else:
        rec["status"] = "failed"
        rec["error"] = prompts.LAST_ERROR


def _request_frame(session: dict, index: int, brief: str,
                   progress: float = 0.0) -> dict | None:
    """Kick off one frame if it is wanted, affordable and not already going."""
    if not session.get("frames_enabled") or not prompts.ai_available():
        return None
    if index < 0 or index >= MAX_FRAMES:
        return None                 # cost guard
    with session["frames_lock"]:
        if index in session["frames"]:
            return session["frames"][index]
        rec = _new_frame_record(session["id"], index, brief, progress)
        session["frames"][index] = rec
    _POOL.submit(_render_frame, session, rec)
    return rec


def _prefetch_next_frame(session: dict) -> None:
    """Start the frame AFTER the one the child is about to be shown."""
    quest = session.get("quest")
    if quest is None:
        return
    here = story_engine.frame_index_for_beat(quest["index"], FRAME_EVERY_BEATS)
    nxt = here + 1
    if nxt >= MAX_FRAMES or nxt in session["frames"]:
        return
    _request_frame(
        session, nxt,
        story_engine.scene_brief_for_frame(quest, nxt, FRAME_EVERY_BEATS),
        story_engine.frame_progress(quest, nxt, FRAME_EVERY_BEATS))


def _frame_payload(session: dict, beat_index: int) -> dict:
    """What the renderer should be showing right now, and what is coming.

    `image` is the base64 PNG, and it is sent EXACTLY ONCE per frame - on the
    beat where that frame first becomes current. Every later beat sends
    image=null, which means "keep showing what you have". Re-fetch any frame
    any time from image_url.
    """
    wanted = min(story_engine.frame_index_for_beat(beat_index, FRAME_EVERY_BEATS),
                 MAX_FRAMES - 1)
    with session["frames_lock"]:
        ready = [i for i, r in session["frames"].items()
                 if i <= wanted and r["status"] == "ready"]
        show = max(ready) if ready else None
        rec = session["frames"].get(show) if show is not None else None
        image = None
        if rec is not None and not rec["sent"]:
            image = rec["image"]
            rec["sent"] = True
        pending = session["frames"].get(wanted) if wanted != show else None

    return {
        "id": rec["id"] if rec else None,
        "index": show,
        "wanted_index": wanted,
        "ready": rec is not None,
        "is_new": image is not None,
        "image": image,
        "image_url": f"/api/frame/{rec['id']}/image" if rec else None,
        "scene_brief": rec["scene_brief"] if rec else None,
        # Poll this when it is not null: a newer frame is on its way and the
        # browser should crossfade to it the moment it lands.
        "next_id": pending["id"] if pending else None,
        "next_status": pending["status"] if pending else None,
        "next_url": f"/api/frame/{pending['id']}" if pending else None,
        "frames_used": len(session["frames"]),
        "frames_max": MAX_FRAMES,
    }


def _lookup_frame(frame_id: str) -> dict:
    sid, _, idx = (frame_id or "").rpartition("-f")
    session = SESSIONS.get(sid)
    if session is None or not idx.isdigit():
        raise HTTPException(404, "Unknown frame.")
    rec = session["frames"].get(int(idx))
    if rec is None:
        raise HTTPException(404, "Unknown frame.")
    return rec


@app.get("/api/frame/{frame_id}")
def get_frame(frame_id: str):
    """Frame metadata + the PNG as a data URL once it is ready."""
    rec = _lookup_frame(frame_id)
    return {
        "id": rec["id"],
        "index": rec["index"],
        "status": rec["status"],
        "ready": rec["status"] == "ready",
        "image": rec["image"],
        "image_url": f"/api/frame/{rec['id']}/image" if rec["image"] else None,
        "scene_brief": rec["scene_brief"],
        "gen_seconds": rec["gen_seconds"],
        "error": rec["error"],
    }


@app.get("/api/frame/{frame_id}/image")
def get_frame_image(frame_id: str):
    """The raw PNG, for dropping straight into an <img src>."""
    rec = _lookup_frame(frame_id)
    if rec["status"] != "ready" or not rec["image"]:
        raise HTTPException(409, f"Frame is {rec['status']}.")
    url = rec["image"]
    if not url.startswith("data:"):
        # Some image deployments hand back a hosted URL instead of base64.
        return RedirectResponse(url)
    raw = base64.b64decode(url.split(",", 1)[1])
    return Response(content=raw, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.post("/api/create-world")
def create_world(req: CreateWorldRequest):
    if not req.image_data_url and not req.text_description:
        raise HTTPException(400, "Provide a drawing or a description.")

    interpretation = prompts.interpret_creation(
        image_data_url=req.image_data_url,
        text_description=req.text_description,
    )

    objects = interpretation.get("objects", []) or []
    setting = math_engine.pick_setting(objects)
    # Two forms, deliberately: what the child asked for ("cross the river and
    # reach the lighthouse") drives the story, and the place inside it ("the
    # lighthouse") is what a progress bar and an image prompt can name.
    destination = story_engine.clean_destination(req.destination, setting, objects)
    goal_text = story_engine.goal_phrase(destination)

    session_id = uuid.uuid4().hex[:12]
    session = {
        "id": session_id,
        "interpretation": interpretation,
        "theme": req.theme,
        "background": None,
        "character_sprite": None,
        "destination": (req.destination or "").strip() or None,
        "goal_text": goal_text,
        "setting": setting,
        "band_hint": req.band,
        "level": 2,
        "score": 0,
        "streak": 0,
        "consecutive_correct": 0,
        "consecutive_wrong": 0,
        "mistakes_total": 0,
        "answered": 0,
        "topic": None,
        "band": None,
        "challenge": None,
        "quest": None,
        "history": [],
        # journey frames
        "frames": {},
        "frames_lock": threading.Lock(),
        "frames_enabled": bool(req.generate_images),
        "storyline_future": None,
        "storyline": None,
        # --- accessories -------------------------------------------------
        # `sprite_original` is the sprite as FIRST generated and is never
        # overwritten: every accessory edit is applied to it with the full
        # cumulative list, which is what stops the character drifting away
        # from itself after three or four edits.
        "sprite_original": None,
        "correct_total": 0,
        "accessories": [],
        "accessory_status": "idle",   # idle | drawing | rendering | ready | failed
        "accessory_lock": threading.Lock(),
        "accessory_sprite_seq": 0,
    }
    SESSIONS[session_id] = session

    # V2 item 2: write the bespoke quest FIRST, in the background. It takes
    # ~12s, which is almost exactly what the images below take - so by the
    # time create-world returns it is already done and /api/start-game does
    # not wait at all. (Submitting it after the images cost 14s of dead air.)
    # The topic is not chosen yet, so complexity comes from the band hint
    # alone plus a mid-weight topic. The model is always shown the FULL
    # skeleton, and story_engine picks out the stops this quest actually uses
    # - so an outline written for the wrong band is trimmed, never wasted.
    hint_band = req.band if req.band in math_engine.BANDS else "23"
    session["storyline_future"] = _POOL.submit(
        prompts.generate_storyline, interpretation,
        hint_band, destination, story_engine.JOURNEY_SKELETON, "",
        story_engine.complexity_for(hint_band, "geometry"))
    session["storyline_requested_at"] = time.time()

    if req.generate_images:
        # The opening background IS journey frame 0 - the anchor every later
        # frame is painted from. The sprite is independent, so the two run
        # side by side instead of back to back (~11s saved).
        sprite_job = _POOL.submit(
            prompts.generate_world_image, interpretation, req.theme, "character")
        frame0 = _new_frame_record(session_id, 0, story_engine.default_location(
            {"camera": "wide", "tint": "day"},
            interpretation.get("setting") or setting, goal_text))
        session["frames"][0] = frame0
        _render_frame(session, frame0)
        session["background"] = frame0["image"]
        try:
            session["character_sprite"] = sprite_job.result(timeout=90)
            session["sprite_original"] = session["character_sprite"]
        except Exception as exc:  # a missing sprite must not kill world creation
            prompts.note_error(exc)

    return {
        "session_id": session_id,
        "interpretation": interpretation,
        "background": session["background"],
        "character_sprite": session["character_sprite"],
        "destination": destination,
        "goal_text": goal_text,
        "destination_source": "child" if session["destination"] else "inferred",
        "frame": {
            "id": _frame_id(session_id, 0) if session["frames"] else None,
            "index": 0,
            "ready": bool(session["background"]),
            "frames_max": MAX_FRAMES,
            "frame_every_beats": FRAME_EVERY_BEATS,
        },
        "ai_enabled": prompts.ai_available(),
    }


# ------------------------------------------------------------------ grading

# Fraction and mixed-number forms, matched BEFORE any unit stripping. Order
# matters: the unit stripper below removes spaces, which would silently turn
# "1 1/3" into "11/3" - a plausible-looking wrong answer rather than a right
# one. Anchored and digit-required, so "90 km/h" never matches either.
_RE_TYPED_MIXED = re.compile(r"^=?\s*([+-]?\d+)\s+(\d+)\s*/\s*(\d+)\s*$")
_RE_TYPED_FRACTION = re.compile(r"^=?\s*([+-]?\d+)\s*/\s*(\d+)\s*$")


def _parse_typed_fraction(text: str) -> float | None:
    """'3/4' -> 0.75, '1 1/3' -> 1.333..., '-1 1/2' -> -1.5. Else None."""
    m = _RE_TYPED_MIXED.match(text)
    if m:
        try:
            whole, num, den = int(m.group(1)), int(m.group(2)), int(m.group(3))
        except ValueError:                       # pragma: no cover
            return None
        if den == 0:
            return None
        frac = num / den
        # "-1 1/2" means -(1 + 1/2), not -1 + 1/2.
        return (whole - frac) if (whole < 0 or m.group(1).startswith("-")) else (whole + frac)
    m = _RE_TYPED_FRACTION.match(text)
    if m:
        try:
            num, den = int(m.group(1)), int(m.group(2))
        except ValueError:                       # pragma: no cover
            return None
        return None if den == 0 else num / den
    return None


def parse_typed_number(raw) -> float | None:
    """What a child typed, as a number - or None if it isn't one.

    Deliberately forgiving about the things a six-year-old's hands do
    (spaces, a stray comma, a trailing unit, a leading '='), and deliberately
    strict about everything else: a blank box must never grade as correct.

    Fractions and mixed numbers are accepted in either written form, because
    the fraction archetypes have answers like 4/3 and a child taught to write
    that as "1 1/3" is not wrong. Both parse to the same float and the grader
    compares numerically, so neither form is privileged.
    """
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if not text:
        return None

    fraction = _parse_typed_fraction(text)
    if fraction is not None:
        return fraction

    # Longest first: "km/h" must be eaten before "km", and "hours" before "h".
    for junk in ("=", "cents", "cent", "degrees", "degree", "hours", "hour",
                 "litres", "litre", "km/h", "kmh", "km", "cm", "mm",
                 "m", "l", "h", "$", "c", ","):
        text = text.replace(junk, " ")
    text = text.replace(" ", "")
    if text in ("", "-", ".", "-."):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _grade(challenge: dict, chosen_list: list[int], chosen: set[int],
           correct_ids: set[int], typed: str | None = None) -> bool:
    """Deterministic grading. Never an LLM - a wrong answer key is fatal here."""
    mode = challenge.get("grade_mode", "set")
    stones = challenge.get("stones", [])

    if mode == "value":
        # FREE RESPONSE. `tolerance` is half a unit in the last decimal place
        # the question asked for, so 3.7 is right and 3.71 is wrong when the
        # question was to one place - but 3.7000001 is never punished.
        want = challenge.get("answer_value")
        if want is None:
            return False
        got = parse_typed_number(typed)
        if got is None:
            return False
        return abs(got - float(want)) <= float(challenge.get("tolerance", 1e-6))

    if mode == "none":
        return True                 # an interlude has nothing to get wrong

    if mode == "count":
        # COLLECT-N: pick exactly target_count items, and only collectable
        # ones. Stones flagged correct=False are traps (mushrooms, etc).
        collectable = {s["id"] for s in stones if s.get("correct", True)}
        return len(chosen) == challenge.get("target_count") and chosen <= collectable

    if mode == "groups":
        # Correct iff the child selected exactly N *complete* sandbars and
        # nothing outside them - i.e. n of d equal parts.
        groups: dict[int, set[int]] = {}
        for s in stones:
            groups.setdefault(s.get("group"), set()).add(s["id"])
        whole = {g for g, ids in groups.items() if ids and ids <= chosen}
        covered = set().union(*(groups[g] for g in whole)) if whole else set()
        return len(whole) == challenge.get("answer_group_count") and chosen == covered

    if mode == "order":
        # ORDERED-WALK. Dedupe consecutive repeats first: a child who wobbles
        # on a corner post should not fail for touching it twice.
        seq = [n for i, n in enumerate(chosen_list) if i == 0 or n != chosen_list[i - 1]]
        want = challenge.get("answer_order") or []
        if not want:
            return False
        if seq == want:
            return True
        if challenge.get("order_cyclic"):
            n = len(want)
            if len(seq) != n or set(seq) != set(want):
                return False
            for base in (want, list(reversed(want))):
                for r in range(n):
                    if seq == base[r:] + base[:r]:
                        return True
        return False

    if mode == "sum":
        vals = [s["value"] for s in stones if s["id"] in chosen]
        if not vals:
            return False
        target = challenge.get("answer_sum")
        if target is None:
            return False
        return abs(sum(vals) - target) <= challenge.get("tolerance", 1e-4)

    return chosen == correct_ids


def _get_session(session_id: str) -> dict:
    session = SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(404, "Session not found. Please create a world first.")
    return session


def _public_challenge(challenge: dict) -> dict:
    """Strip the answer key. Keep every field the renderer needs."""
    public = {k: v for k, v in challenge.items() if k not in SECRET_FIELDS}
    public["answer_order"] = None  # key present, value withheld
    public["stones"] = [
        {"id": s["id"], "label": s.get("label", ""),
         **{f: s[f] for f in STONE_VISUAL_FIELDS if f in s}}
        for s in challenge["stones"]
    ]
    return public


def _issue_beat(session: dict) -> dict | None:
    """Generate the current quest beat and cache it server-side."""
    quest = session.get("quest")
    if quest is None:
        raise HTTPException(400, "Start a game first.")
    challenge = story_engine.issue_beat(
        quest,
        session_level=session["level"],
        mistakes_total=session["mistakes_total"],
        seed=random.randrange(1 << 30),
    )
    if challenge is None:
        return None
    challenge["narrative"] = prompts.narrate_challenge(challenge, session["interpretation"])
    session["challenge"] = challenge

    public = _public_challenge(challenge)
    # The frame for THIS beat, then immediately start the next one. Requesting
    # the prefetch here - while the child has not even read the problem yet -
    # is what buys the ~11s render back.
    public["frame"] = _frame_payload(session, quest["index"])
    _prefetch_next_frame(session)
    return public


# Kept for backwards compatibility with anything still calling it.
def _issue_challenge(session: dict) -> dict:
    return _issue_beat(session)


def _collect_storyline(session: dict) -> dict | None:
    """Pick up the storyline prefetched at create-world time.

    Returns None - meaning "use an authored template" - if there was no AI,
    if the model produced something unusable, or if it is simply still
    running past its budget. Starting the game late is worse than starting
    it with a template, every time.
    """
    fut = session.get("storyline_future")
    if fut is None:
        return None
    waited = time.time() - session.get("storyline_requested_at", time.time())
    budget = min(STORYLINE_BUDGET_S - waited, STORYLINE_MAX_BLOCK_S)
    try:
        storyline = fut.result(timeout=max(0.0, budget))
    except Exception as exc:
        prompts.note_error(exc)
        session["storyline_wait_s"] = round(time.time() - session.get(
            "storyline_requested_at", time.time()), 2)
        return None
    session["storyline"] = storyline
    session["storyline_wait_s"] = round(time.time() - session.get(
        "storyline_requested_at", time.time()), 2)
    return storyline


@app.post("/api/start-game")
def start_game(req: StartGameRequest):
    session = _get_session(req.session_id)
    if req.topic not in math_engine.TOPICS:
        raise HTTPException(400, f"Unknown topic: {req.topic}")
    if req.band not in math_engine.TOPIC_BANDS.get(req.topic, []):
        raise HTTPException(400, f"Topic {req.topic} is not available for age band {req.band}")

    session["topic"] = req.topic
    session["band"] = req.band
    session["level"] = 1 if req.band == "k1" else 2
    session["consecutive_correct"] = 0
    session["consecutive_wrong"] = 0
    session["quest"] = story_engine.start_quest(
        req.topic, req.band, session["interpretation"],
        destination=session.get("destination"),
        storyline=_collect_storyline(session),
    )

    challenge = _issue_beat(session)
    quest = session["quest"]
    return {
        "challenge": challenge,
        "stats": _stats(session),
        "quest": story_engine.summary(quest),
        "opening": quest["opening"],
        "goal_text": quest["goal_text"],
        "journey_progress": story_engine.journey_progress(quest),
        "distance_remaining": story_engine.distance_remaining(quest),
        "distance_text": story_engine.distance_text(quest),
        "story_source": quest["story_source"],
        "story_hook": session["interpretation"].get("story_hook", ""),
    }


@app.post("/api/next-beat")
def next_beat(req: NextChallengeRequest):
    """Advance the quest one beat and issue its challenge.

    When the arc is finished the response carries `complete: true` plus the
    epilogue instead of a challenge.
    """
    session = _get_session(req.session_id)
    quest = session.get("quest")
    if quest is None:
        raise HTTPException(400, "Start a game first.")

    if quest["complete"]:
        return _quest_finished(session)

    # The very first beat is issued by /api/start-game; only advance once the
    # current beat has actually been answered.
    if quest["awaiting_answer"] and session.get("challenge"):
        replay = _public_challenge(session["challenge"])
        replay["frame"] = _frame_payload(session, quest["index"])
        return {
            "challenge": replay,
            "stats": _stats(session),
            "quest": story_engine.summary(quest),
            "complete": False,
            "epilogue": None,
            **_journey(quest),
        }

    if not story_engine.advance(quest):
        return _quest_finished(session)

    return {
        "challenge": _issue_beat(session),
        "stats": _stats(session),
        "quest": story_engine.summary(quest),
        "complete": False,
        "epilogue": None,
        **_journey(quest),
    }


@app.post("/api/next-challenge")
def next_challenge(req: NextChallengeRequest):
    """Legacy alias for /api/next-beat."""
    return next_beat(req)


def _journey(quest: dict) -> dict:
    """The progress block the UI renders: a goal, a bar, and how far is left."""
    return {
        "goal_text": quest.get("goal_text", ""),
        "journey_progress": story_engine.journey_progress(quest),
        "distance_remaining": story_engine.distance_remaining(quest),
        "distance_text": story_engine.distance_text(quest),
    }


def _quest_finished(session: dict) -> dict:
    quest = session["quest"]
    quest["complete"] = True
    session["challenge"] = None
    return {
        "challenge": None,
        "complete": True,
        "epilogue": story_engine.epilogue(quest),
        "quest": story_engine.summary(quest),
        "stats": _stats(session),
        "arrival_frame": _frame_payload(session, quest["index"]),
        **_journey(quest),
    }


# --------------------------------------------------------------- adaptation

_ADAPT_LINES = {
    "streak_up": "Three in a row! Here comes a tougher one. \U0001F525",
    "two_right_up": "You're on fire - stepping it up a notch!",
    "two_wrong_down": "Let's make the next one friendlier. You've got this.",
    "hold_after_slip": "Close one! Same kind of puzzle next - try it again.",
    "hold": "Nice work. Onward!",
}


def _adaptation(session: dict, quest: dict, reason: str, previous_level: int,
                helper_added: bool, optional_dropped: bool,
                was_correct: bool) -> dict:
    level = session["level"]
    direction = "up" if level > previous_level else "down" if level < previous_level else "same"

    message = _ADAPT_LINES.get(reason, _ADAPT_LINES["hold"])
    if helper_added:
        message = (f"{quest['friend']} is running over to help - the next one "
                   f"is an easier one, together.")
        direction = "helper"
    elif optional_dropped:
        message = "You're flying! We're taking the short cut to the big finish."

    obstacles = min(session["mistakes_total"], 3)
    # Only announce the obstacle change on the beat where it CHANGED - a line
    # repeated every single beat stops being information and starts being noise.
    detail = None
    if obstacles and not was_correct:
        detail = (f"The trail just grew {'another thing' if obstacles == 1 else 'more things'} "
                  f"to weave around - but the puzzles get gentler, not harder.")

    return {
        "direction": direction,
        "reason": reason,
        "message": message,
        "detail": detail,
        "level": level,
        "previous_level": previous_level,
        "math_easier": level < previous_level,
        "nav_obstacles": obstacles,
        "beats_total": len(quest["beats"]),
        "helper_added": helper_added,
        "optional_dropped": optional_dropped,
    }


@app.post("/api/answer")
def answer(req: AnswerRequest):
    session = _get_session(req.session_id)
    challenge = session.get("challenge")
    if challenge is None:
        raise HTTPException(400, "No active challenge.")
    quest = session.get("quest")

    if challenge.get("question_type") == "interlude":
        # Interludes are walked, not answered. The frontend goes straight to
        # /api/next-beat; anything posting here is confused, and silently
        # scoring it would corrupt the streak.
        raise HTTPException(400, "This beat has no question - walk it instead.")

    correct_ids = {s["id"] for s in challenge["stones"] if s["correct"]}
    chosen = set(req.stone_ids)
    graded = _grade(challenge, req.stone_ids, chosen, correct_ids, req.typed)

    # "n of d equal parts" has many right answers. When the child found one of
    # them, highlight THEIRS - highlighting a different valid set would read
    # as "you were wrong" after being told you were right.
    reveal_ids = correct_ids
    if graded and challenge.get("grade_mode") in ("groups", "count", "sum"):
        reveal_ids = chosen

    # The resolution beat cannot be failed. The quest always ends in a win -
    # the child still sees the worked answer, they just don't lose on it.
    no_fail = bool(challenge.get("no_fail"))
    was_correct = True if no_fail else graded

    session["answered"] += 1
    if was_correct:
        session["correct_total"] += 1
        session["consecutive_correct"] += 1
        session["consecutive_wrong"] = 0
        session["streak"] += 1
        session["score"] += 10 * session["level"] + 5 * max(0, session["streak"] - 1)
    else:
        session["consecutive_wrong"] += 1
        session["consecutive_correct"] = 0
        session["streak"] = 0
        session["mistakes_total"] += 1

    # Snapshot BEFORE adapt_level zeroes the counters - the arc-shape
    # adaptation below needs the streak that actually just happened.
    streak_now = session["streak"]
    wrong_now = session["consecutive_wrong"]

    previous_level = session["level"]
    new_level, reason = math_engine.adapt_level(
        session["level"], was_correct,
        session["consecutive_correct"], session["consecutive_wrong"],
    )
    session["level"] = new_level
    if new_level != previous_level:
        session["consecutive_correct"] = 0
        session["consecutive_wrong"] = 0

    # --- arc-shape adaptation -----------------------------------------
    helper_added = optional_dropped = False
    if quest is not None:
        if not was_correct and wrong_now >= 2:
            helper_added = story_engine.insert_helper_beat(quest)
        elif was_correct and streak_now >= 3:
            optional_dropped = story_engine.drop_optional_beat(quest)

    beat_result = {}
    if quest is not None:
        beat_result = story_engine.record_result(quest, challenge, was_correct)

    session["history"].append({
        "topic": challenge["topic"],
        "prompt": challenge["prompt"],
        "archetype": challenge.get("archetype"),
        "level": previous_level,
        "correct": was_correct,
    })

    char = challenge.get("character_name", "your friend")
    is_final = bool(quest and quest["index"] >= len(quest["beats"]) - 1)

    return {
        "correct": was_correct,
        "graded_correct": graded,
        "no_fail": no_fail,
        "correct_stone_ids": sorted(reveal_ids),
        "answer_value": challenge.get("answer_value"),
        # The written form, for fraction answers - revealed only AFTER
        # the child has answered, exactly like answer_value.
        "answer_text": challenge.get("answer_text"),
        "typed": req.typed,
        "answer_order": challenge.get("answer_order"),
        "explanation": challenge.get("explanation", ""),
        "feedback": prompts.feedback_line(was_correct, char),
        "beat_result": beat_result.get("text", ""),
        "progress_line": beat_result.get("progress_line", ""),
        "quest_state": beat_result.get("state", {}),
        "quest_changes": beat_result.get("changes", {}),
        "level_changed": session["level"] - previous_level,
        "adaptation": _adaptation(session, quest, reason, previous_level,
                                  helper_added, optional_dropped,
                                  was_correct) if quest else None,
        "quest": story_engine.summary(quest) if quest else None,
        "is_final_beat": is_final,
        "stats": _stats(session),
        "accessory": _accessory_offer(session),
        **(_journey(quest) if quest else {}),
    }


# ============================================================= ACCESSORIES
#
# The loop: 5 cumulative correct answers -> the child draws something ->
# their character wears it for the rest of the session, and they accumulate.
#
# THREE THINGS THIS MUST NEVER DO, in order of how badly they would hurt:
#   1. block gameplay. The edit takes ~10s; the quest carries on regardless
#      and the sprite swaps in whenever it lands.
#   2. fail visibly. Any error keeps the sprite the child already had and
#      says nothing - a six-year-old must never meet a stack trace.
#   3. vanish with no API key. The browser paints the child's OWN strokes on
#      the character the instant they finish drawing, so the reward is real
#      offline; the AI version is a polish pass on top, not the feature.


def _accessories_earned(session: dict) -> int:
    """How many accessories this child has unlocked so far, ever."""
    if ACCESSORY_EVERY <= 0:
        return 0
    return min(session["correct_total"] // ACCESSORY_EVERY, MAX_ACCESSORIES)


def _accessory_offer(session: dict) -> dict:
    """Is one due right now, and how far off is the next one?"""
    earned = _accessories_earned(session)
    claimed = len(session["accessories"])
    due = earned > claimed and session["accessory_status"] != "rendering"
    to_go = 0
    if claimed < MAX_ACCESSORIES and ACCESSORY_EVERY > 0:
        to_go = max(0, (claimed + 1) * ACCESSORY_EVERY - session["correct_total"])
    return {
        "due": bool(due),
        "index": claimed + 1,
        "earned": earned,
        "claimed": claimed,
        "max": MAX_ACCESSORIES,
        "every": ACCESSORY_EVERY,
        "correct_total": session["correct_total"],
        "to_go": to_go,
        "slots": list(prompts.ACCESSORY_SLOTS.keys()),
        "status": session["accessory_status"],
    }


def _render_accessory(session: dict, accessory: dict) -> None:
    """Background: describe the doodle, then put it on the character.

    Always edits the ORIGINAL sprite with the FULL cumulative accessory list.
    Chaining edit-on-edit drifts the character away from itself after three
    or four goes; re-editing the original costs exactly the same and keeps
    the child's character recognisably theirs. See prompts.py.
    """
    try:
        desc = prompts.describe_accessory(accessory.get("image_data_url"),
                                          accessory.get("slot"))
        accessory["description"] = desc or accessory.get("slot") or "an accessory"
        sprite = prompts.generate_accessorised_sprite(
            session["interpretation"], session.get("sprite_original"),
            session["accessories"])
        if sprite:
            session["character_sprite"] = sprite
            session["accessory_sprite_seq"] += 1
            session["accessory_status"] = "ready"
        else:
            # Keep whatever sprite the child already had. The browser is
            # still drawing their own strokes on top, so they lose nothing.
            session["accessory_status"] = "failed"
    except Exception as exc:                    # never reaches the child
        prompts.note_error(exc)
        session["accessory_status"] = "failed"


@app.post("/api/accessory")
def add_accessory(req: AccessoryRequest):
    """Claim an earned accessory. Returns IMMEDIATELY - never blocks play."""
    session = _get_session(req.session_id)

    if req.skipped:
        # A child who does not want to draw keeps playing and is offered the
        # next one at the next milestone. Nothing is recorded.
        return {"ok": True, "skipped": True, "accessory": _accessory_offer(session)}

    if _accessories_earned(session) <= len(session["accessories"]):
        raise HTTPException(400, "No accessory has been earned yet.")
    if len(session["accessories"]) >= MAX_ACCESSORIES:
        raise HTTPException(400, "That is every accessory for this session.")
    if not req.image_data_url:
        raise HTTPException(400, "Draw something first.")

    slot = req.slot if req.slot in prompts.ACCESSORY_SLOTS else prompts.DEFAULT_ACCESSORY_SLOT
    accessory = {
        "index": len(session["accessories"]) + 1,
        "slot": slot,
        "image_data_url": req.image_data_url,
        "description": None,
    }
    with session["accessory_lock"]:
        session["accessories"].append(accessory)
        session["accessory_status"] = "rendering" if prompts.ai_available() else "offline"

    if prompts.ai_available() and session.get("sprite_original"):
        _POOL.submit(_render_accessory, session, accessory)

    return {
        "ok": True,
        "skipped": False,
        # The browser already has the strokes and is already drawing them.
        # This is only "is a nicer version coming?".
        "rendering": session["accessory_status"] == "rendering",
        "accessory": _accessory_offer(session),
    }


@app.get("/api/accessory/{session_id}")
def get_accessory(session_id: str):
    """Poll for the accessorised sprite. `sprite` is null until it is ready."""
    session = _get_session(session_id)
    ready = session["accessory_status"] == "ready"
    return {
        "status": session["accessory_status"],
        "seq": session["accessory_sprite_seq"],
        "sprite": session["character_sprite"] if ready else None,
        "worn": [{"index": a["index"], "slot": a["slot"],
                  "description": a.get("description")}
                 for a in session["accessories"]],
        "accessory": _accessory_offer(session),
    }


def _stats(session: dict) -> dict:
    total = session["answered"]
    correct = sum(1 for h in session["history"] if h["correct"])
    return {
        "score": session["score"],
        "level": session["level"],
        "streak": session["streak"],
        "answered": total,
        "correct": correct,
        "accuracy": round(correct / total * 100) if total else 0,
        "mistakes_total": session["mistakes_total"],
    }


@app.get("/api/session/{session_id}")
def get_session(session_id: str):
    session = _get_session(session_id)
    quest = session.get("quest")
    with session["frames_lock"]:
        frames = [
            {"id": r["id"], "index": r["index"], "status": r["status"],
             "image_url": f"/api/frame/{r['id']}/image" if r["image"] else None,
             "gen_seconds": r["gen_seconds"], "scene_brief": r["scene_brief"],
             "seeded_from": r.get("seeded_from")}
            for r in sorted(session["frames"].values(), key=lambda r: r["index"])
        ]
    return {
        "interpretation": session["interpretation"],
        "stats": _stats(session),
        "history": session["history"],
        "quest": story_engine.summary(quest) if quest else None,
        "goal_text": session.get("goal_text", ""),
        "destination_source": "child" if session.get("destination") else "inferred",
        "frames": frames,
        "frames_max": MAX_FRAMES,
        "frame_every_beats": FRAME_EVERY_BEATS,
        "storyline_source": (quest or {}).get("story_source", "template"),
        "storyline_wait_s": session.get("storyline_wait_s"),
        **(_journey(quest) if quest else {}),
    }


# ------------------------------------------------------------ static files

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(FRONTEND_DIR / "index.html")
