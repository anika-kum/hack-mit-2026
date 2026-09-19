# 🎨 Doodle Quest

**Draw anything. Tell us what you want to learn. Your drawing becomes the game.**

An immersive math game for kids aged 4–10. A child draws a character and a world
on a blank canvas (or just describes it out loud), picks a math topic, and their
doodle becomes a playable 2D adventure where math problems are embedded as
challenges in the world itself.

Instead of:
> What is 3/4 × 8?  A) 4  B) 6  C) 8

you get:
> 🐱 **Mochi needs to cross the river!** There are 8 stepping stones,
> but only ¾ are safe. Walk onto the safe stones to cross!

---

## Quick start

```bash
cd backend
python3 -m pip install -r requirements.txt
cd .. && ./run.sh
```

Then open **http://localhost:8000**

### Enable the AI features (optional but recommended for demo)

```bash
cp backend/.env.example backend/.env
# edit backend/.env and paste your OpenAI key
```

Without a key the game runs in **offline mode**: fully playable, with a
hand-drawn canvas world and built-in storytelling instead of generated art.
This is deliberate — the demo never hard-fails if the venue wifi dies or the
API rate-limits mid-presentation.

---

## How it works

```
Child draws / speaks / types
          │
          ▼
  POST /api/create-world ──► vision model reads the drawing
          │                  → objects, relationships, character name, story hook
          │                  → gpt-image-1 paints a polished background + sprite
          ▼
  Pick topic + age band
          │
          ▼
  POST /api/start-game  ──► math_engine generates a challenge (pure Python)
          │                  → prompts.narrate_challenge wraps it in story
          ▼
  Play: arrow keys to walk, step on stones to answer
          │
          ▼
  POST /api/answer      ──► scored, difficulty adapts, next challenge issued
```

**Key design decision:** the math itself is generated *deterministically in
Python*, never by the LLM. The LLM only writes the story wrapper around it.
This guarantees every answer key is correct and every challenge generates
instantly — an LLM asked to "make a fractions problem" will eventually produce
a wrong answer, which is unacceptable in a learning tool.

---

## Features

**Input** — draw with a full paint canvas (8 colors, brush sizes, eraser, undo),
*or* type a description, *or* use voice-to-text via the Web Speech API.

**Math modules** — 8 topics across 3 age bands:

| Topic | Ages 4–6 | Ages 7–8 | Ages 9–10 |
|---|:-:|:-:|:-:|
| Addition / Subtraction | ✓ | ✓ | ✓ |
| Shapes & Geometry | ✓ | ✓ | ✓ |
| Multiplication | | ✓ | ✓ |
| Fractions | | ✓ | ✓ |
| Decimals | | | ✓ |
| Basic Algebra | | | ✓ |
| Speed–Distance–Time | | | ✓ |

**Adaptive difficulty** — 5 levels per topic/band. Two correct in a row levels
up; one wrong levels down. Number ranges, operand sizes and problem complexity
all scale with level. *Verified:* a perfect player climbs `28+24` → `175+76`
within 8 questions.

**Adaptive obstacles** — every mistake permanently adds a decoy stone to future
challenges (capped at +4), so a struggling child faces a denser, more
deliberate world while a confident one gets a clean path.

**Gameplay** — arrow-key movement on a canvas, walk onto a stone to select it.
Single-answer questions auto-submit so young kids never need the space bar;
multi-select (fractions) lets you step on/off stones and press Space to commit.

**Aesthetic** — pastel cutesy throughout, 5 selectable world themes (storybook,
candy, forest, ocean, space), bouncy animations, particle bursts, and
encouraging feedback that never says "wrong."

---

## Project layout

```
hackmit/
├── run.sh                  one-command launcher
├── backend/
│   ├── app.py              FastAPI routes + session state + static serving
│   ├── math_engine.py      deterministic adaptive question generator
│   ├── prompts.py          OpenAI vision / image-gen / narration + fallbacks
│   ├── requirements.txt
│   └── .env.example
└── frontend/
    ├── index.html          5 screens: title, create, loading, topic, game
    ├── style.css           pastel design system
    └── app.js              paint canvas, voice input, game loop
```

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | server + AI availability |
| `GET /api/topics` | topic/band catalog and themes |
| `POST /api/create-world` | interpret drawing/text → session + generated art |
| `POST /api/start-game` | choose topic + band → first challenge |
| `POST /api/answer` | grade, score, adapt difficulty |
| `POST /api/next-challenge` | issue the next adapted challenge |
| `GET /api/session/{id}` | stats + full answer history |

Answer keys are stripped server-side before challenges are sent to the client,
so the answers can't be read out of the network tab.

---

## Known gaps / next up

- Sessions are in-memory — a server restart drops progress. Fine for demo;
  swap `SESSIONS` for Redis to persist.
- Image generation takes ~10–20s. The loading screen covers it, but consider
  generating the background while the child picks their topic.
- Only the background and character sprite are generated today; the plan's
  "sequence of images" (multiple scenes per topic) is the natural next step.
- No audio yet — sound effects and read-aloud narration would help pre-readers
  in the 4–6 band significantly.
