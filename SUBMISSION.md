# Do-IT-oodle — submission notes

> Draw anything. Tell us what you want to learn. Your drawing becomes the game.

---

## How we built it

**The stack:** Python + FastAPI on the back end, vanilla JavaScript and a
`<canvas>` on the front end. No build step, no framework — it runs from one
process.

**Three layers, in order of how much we trust them:**

1. **`math_engine.py` — the maths. Always correct.** Every number and every
   answer is generated in Python. 41 question concepts (triangle angle sum,
   fraction division, speed-distance-time, coin totals…) rendered as multiple
   choice or free response.
2. **`story_engine.py` — the quest. Always available.** Builds a 5–9 stop
   journey with a goal, escalating obstacles and a finale, and adapts as the
   child plays.
3. **`prompts.py` — the AI. Optional, degrades gracefully.** Vision reads the
   child's drawing; Muse paints the world; a text model writes the story.

**The one rule we never broke: the AI never does the maths.** An LLM asked to
"make a fractions problem" will eventually produce a wrong answer key, which is
fatal in a learning tool. So Python generates `7 × 8 = 56`, and the model only
*re-words* it into the scene — "The gate wants 7 bundles of 8 rods. How many
rods?" We then **verify the rewrite and throw it away** unless it contains
exactly the same numbers and doesn't leak the answer. 81% of rewrites pass; the
rest silently fall back to the plain question.

**Difficulty comes from real curriculum, not guesswork.** We mined a bank of
773 grade 1–5 questions and classified each into a concept and the grades it
appears in. That drives a 13-rung difficulty ladder where a 7-year-old's
hardest question and a 9-year-old's easiest sit on the *same rung* — so an
older child never gets asked to count to three, and a five-year-old never meets
triangle angle sums.

**The art is two generations composited at runtime.** Muse Image 1.0 paints the
background (explicitly with no characters in it) and, separately, the
character. Muse has no transparent-background mode, so we cut the sprite out
ourselves with a flood fill from the image edges — which keeps the character's
white eyes and highlights instead of deleting them. The sprite is drawn
feet-anchored over the painted world with a contact shadow and a shared
time-of-day tint, which is what makes two separately-generated images look like
one scene. A new background is generated every couple of beats by *editing* the
previous one, so the world visibly travels.

**Gameplay:** the child roams in four directions over the illustration and hits
something they can't pass — a chasm, a rope bridge, a boat, a locked gate.
**The question is the key.** Answer it and the crossing opens, then they
perform it on the keyboard. Obstacles are picked from the world they drew (a
boat on a river, ledges on a cliff), escalate toward the finale, and never
repeat twice in a row.

**It works offline.** With no API key at all, the game is fully playable with
procedural art and authored prose. That path is tested as hard as the online
one.

**Testing:** 168,000+ assertions run with and without API keys, plus 59
real-pixel and input-simulation checks in headless Chrome — proving all four
directions move the character, that the question box never covers the art, and
that every obstacle actually renders differently.

---

## Open item before submitting

The write-up prompt asks how the project **strengthens human connection**, and
right now there is no honest answer: Do-IT-oodle is entirely single-player.

Cheapest credible fix (~1 hour, reuses shipped code): **invert the accessory
system.** Instead of the child drawing their own reward, they get a share link;
a family member opens it on their phone, draws a wizard hat, and next time the
child plays their character is wearing it — "Grandma sent you this." The child
can draw back. Asynchronous, genuinely between two people, and it makes a far
better demo video than a solo maths game.

Not built yet — decide before submitting.
