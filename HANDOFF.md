# Doodle Quest — Handoff

**A kid draws or describes a character and world → AI turns it into a playable
2D storybook math adventure.** Ages 4–10.

Last updated: 2026-09-19, mid-hackathon.

---

## 1. Run it

```bash
cd backend
python3 -m pip install -r requirements.txt
cd .. && ./run.sh            # → http://localhost:8000
```

The API key lives in `backend/.env` (gitignored, **not** in the repo — ask the
team for it). Without a key the game still runs fully in offline mode with
procedural art; that path is deliberately maintained, don't let it rot.

**When editing CSS/JS, hard-refresh with `Cmd+Shift+R`** — normal reload serves
cached assets and you'll think your change didn't work.

---

## 2. Architecture

Three layers, in order of how much we trust them:

```
math_engine.py    deterministic math + layout        ALWAYS correct
story_engine.py   deterministic quest arc            ALWAYS available
prompts.py        OpenAI embellishment               optional, degrades
```

```
hackmit/
├── run.sh
├── backend/
│   ├── app.py            FastAPI routes, session store, grading, frame prefetch
│   ├── math_engine.py    25 challenge archetypes, 8 topics × 3 age bands
│   ├── story_engine.py   quest arcs, beats, journey progress, adaptation
│   ├── prompts.py        OpenAI: vision, image gen, storyline, narration
│   └── test_engine.py    ~83k assertions — RUN THIS BEFORE YOU COMMIT
└── frontend/
    ├── index.html        storybook layout: running head, art plate, answer tray
    ├── style.css         pastel design system
    └── app.js            canvas world, clickable answers, page turns, drawing
```

### The single most important design rule

**The math is generated in Python. The LLM never computes anything.**
`math_engine.py` produces the numbers and the answer key; `prompts.py` only
wraps them in prose. An LLM asked to "make a fractions problem" will eventually
emit a wrong answer key, which is fatal in a learning tool. **Do not move math
generation into a prompt.**

Corollary: answer keys never reach the browser. `SECRET_FIELDS` and the
per-stone `correct`/`value` strip happens in `_public_challenge()`. If you add
a field that encodes the answer, add it to `SECRET_FIELDS`.

---

## 3. Current state

### Works and is verified
- Drawing canvas, text description, voice-to-text input
- Vision reads a child's drawing (`child, boat, river, tree, cloud` from stick figures)
- AI background + **transparent** character sprite (~10s each)
- **Story frames** — a new background every ~2 beats, chained via `images.edit`
  so the world visibly progresses; prefetched so latency is hidden
- Bespoke AI storyline from character + setting + age + destination
- 5–7 beat quest arcs with a goal, journey progress, causally chained state
- Adaptive difficulty: 2 right → level up, 1 wrong → level down; helper beats
  splice in for struggling kids; resolution beat always runs
- **Clickable answer cards** (the storybook redesign) — walking is now optional
- Page-turn transitions between chapters
- ~83k backend assertions, 332 + 93 frontend assertions, 105 real-pixel
  geometry assertions in headless Chrome

### NOT verified — do this first
**Nobody has played the current build with AI art on.** The clickable-storybook
redesign was verified structurally and in headless screenshots of the
*procedural fallback* world only. The on-canvas stones for `path` / `treasure` /
`island` modes still composite onto the generated painting, and nobody has
judged whether that looks right.

**Go play it, with art generation on, and screenshot what looks wrong.**

---

## 4. Environment gotchas (these cost us real hours)

| Symptom | Cause | Fix |
|---|---|---|
| "AI is offline" but key is set | `openai<1.109` passes a `proxies` kwarg `httpx>=0.28` removed | `openai>=1.109` (pinned in requirements.txt) |
| `Decompressor.decompress() got an unexpected keyword 'output_buffer_limit'` | Anaconda's zstandard vs httpx zstd | We force `Accept-Encoding: gzip, deflate` in `get_client()` — **don't remove** |
| `'max_tokens' is not supported` | gpt-5.6 renamed it | Use `max_completion_tokens` |
| `temperature does not support 0.9` | gpt-5.6 only allows default | Don't pass `temperature` |
| Storyline takes 23s | model reasons before writing prose | `reasoning_effort="low"` → 10.7s, no quality loss |
| Everything 429s | account has no credits | Add credits at platform.openai.com → Billing |

### Models (verified current 2026-09-19)
- Image: `gpt-image-2.5-flare` at `quality="low"` — ~15× cheaper and much faster
  than high; for watercolor art the difference is near-invisible
- Text + vision: `gpt-5.6-luna`
- ⚠️ **`gpt-image-1` is deprecated — shuts down 2026-12-01.** Don't build on it.

All overridable via env: `OPENAI_IMAGE_MODEL`, `OPENAI_TEXT_MODEL`,
`OPENAI_VISION_MODEL`, `OPENAI_IMAGE_QUALITY`, `OPENAI_REASONING_EFFORT`,
`DQ_MAX_FRAMES`, `DQ_FRAME_EVERY_BEATS`.

### Cost
~$0.02–0.05 per full session; images are ~95% of it. $10 ≈ 200+ playthroughs.

---

## 5. Product decisions already made (don't silently undo these)

1. **Mistakes add navigational obstacles, not harder math.** The brief said
   "more obstacles on mistakes"; a struggling child getting *harder* math is a
   failure spiral. So mistakes add scenery to walk around while the math level
   goes *down* and a helper beat splices in. This was an explicit product call.
2. **Resolution beats always report success** regardless of the answer, so every
   quest ends in a win. The honest result is still recorded in `graded_correct`.
3. **Fractions are grouped sandbars, not random stones.** The original version
   picked safe stones at random with no visual cue — a child who correctly
   computed "6 of 8" still had a 1-in-28 chance of being marked right. Now it's
   "step on 3 of these 4 sandbars," which *is* the concept. Don't regress this.
4. **Backgrounds contain no characters.** The hero is composited on top as a
   transparent sprite; if the hero is also painted into the scenery you see two
   of them. `_scenery_objects()` / `strip_hero()` enforce this.
5. **The child's spatial layout is preserved.** The vision model returns an
   explicit `layout` (`"tree on the far left"`) and the image prompt forbids
   mirroring. Before this fix, drawings came back flipped.

---

## 6. Open items / next steps

**Highest value first:**

1. **Play it with AI art on and screenshot problems.** Everything below is
   guesswork until someone does this.
2. **Pre-warm a demo session before going on stage.** World creation takes ~13s
   of live API calls. Doing that in front of judges on venue wifi is the single
   most likely way this fails. Record a backup video too.
3. **`narrate_challenge` adds ~1.7s to every question.** Now that beats have
   bespoke AI-written intros it's arguably redundant — cutting it would make
   transitions near-instant. ~15 min of work, noticeable feel improvement.
4. **Unresolved feedback: "I don't like how it copies."** A teammate said this
   about the generated world but we never pinned down whether they wanted the
   art to follow the drawing *less* literally. We currently made it follow the
   layout *more* faithfully. Worth clarifying before changing.
5. A failed frame is never retried — the journey holds on the last good frame.
   Deliberate (no loading screens), but a mid-quest API blip freezes scenery.
6. The storyline is generated at create-world, *before* the child picks an age
   band, so it's written for a default band. Only vocabulary is affected; math
   difficulty is entirely `math_engine`'s. Frontend could pass its default band.
7. Frame drift past ~4 chained edits is untested (`DQ_MAX_FRAMES=6` caps it).
8. Sessions are in-memory — a restart drops progress. Fine for demo; swap
   `SESSIONS` for Redis to scale.

---

## 7. Testing

```bash
python3 backend/test_engine.py                      # ~83k assertions
OPENAI_API_KEY= python3 backend/test_engine.py      # offline path must also pass
```

Frontend has no browser test runner, but there are harnesses in `frontend/`
driven by `jsc` (`JavaScriptCore.framework/Helpers/jsc`) and `osascript -l
JavaScript`. `jsc` drains promise microtasks; `osascript` does not, so async
paths only really run under `jsc`. Headless Chrome is available at
`/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` and was used for
real `getBoundingClientRect()` geometry checks — that's how we proved the
narration card stopped covering the answers.

**Note:** there is no `node`/`npm` on the original dev machine, which is why the
stack is FastAPI + vanilla JS with no build step.

---

## 8. Security

The OpenAI key was pasted in plaintext into a chat transcript during
development. **It should be rotated** at platform.openai.com when convenient.
`.env` is gitignored — keep it that way, and don't paste the key into
Slack/Discord/commits.
