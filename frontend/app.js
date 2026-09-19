/* ── Doodle Quest ────────────────────────────────────────────────────
   Frontend: drawing canvas, voice input, read-aloud, and the arrow-key
   adventure loop.

   Rendering contract (all fields OPTIONAL — the backend is a moving
   target, so every reader below has a defined fallback):

     challenge = {
       question_type: single_choice | multi_select | collect_count | ordered_path,
       grade_mode:    set | count | order | sum | groups,
       prompt, narrative, target_count,
       stones: [{ id, label, value, group, tint, shape, dots, x_pct, y_pct }],
       props:  [{ kind, x_pct, y_pct, scale, label }],
       play_area: { top_pct, bottom_pct } | null,
       beat:   { index, total, title, camera, tint, intro, on_success },
     }

   V2 additions — ALSO all optional, and read from several possible
   places because the backend contract is still being settled:

     goal / journey   goal_text | goal | destination            (string)
                      journey_progress  0..1  (or 0..100)       (number)
                      distance_remaining | distance_text        (num/str)
                      …looked for on quest, challenge, challenge.beat
                      and quest.state, in that order.

     story frames     a new painted background every ~2 beats. Accepted as
                      EITHER inline data (frame.image / image_data_url /
                      b64_json / url …) OR an id (frame_id / frame.id)
                      fetched from GET /api/frame/{id}. Neither present →
                      the current background simply stays. Nothing here
                      ever blocks gameplay.
   ─────────────────────────────────────────────────────────────────── */

const $ = (id) => document.getElementById(id);
const API = '';
const TAU = Math.PI * 2;

const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);
const lerp = (a, b, t) => a + (b - a) * t;
const num = (v, d) => (typeof v === 'number' && isFinite(v) ? v : d);

/* The V2 payload shape is still being settled, so most readers below ask
   "whichever of these exists first" rather than naming one field. */
function firstStr(...vals) {
  for (const v of vals) {
    if (typeof v === 'string' && v.trim()) return v.trim();
  }
  return '';
}
function firstNum(...vals) {
  for (const v of vals) {
    if (typeof v === 'number' && isFinite(v)) return v;
    if (typeof v === 'string' && v.trim() && isFinite(Number(v))) return Number(v);
  }
  return null;
}
// Safe property read — `obj` is very often undefined or a string here.
function field(obj, key) {
  return (obj && typeof obj === 'object') ? obj[key] : undefined;
}

const state = {
  sessionId: null,
  interpretation: null,
  description: '',          // raw text the child typed/spoke — keeps adjectives
  destination: '',          // OPTIONAL "where is this adventure going?"
  goalText: '',             // the goal the server settled on (may differ)
  background: null,
  characterSprite: null,
  theme: 'storybook',
  topics: [],
  band: 'k1',
  challenge: null,
  quest: null,              // { title, ... } summary from the story engine
  selected: [],             // ORDERED — ordered_path depends on this
  locked: false,
  muted: localStorage.getItem('dq-muted') === '1',
  stats: { score: 0, streak: 0, level: 1, accuracy: 0 },
};

function show(screenId) {
  document.querySelectorAll('.screen').forEach((s) => s.classList.remove('active'));
  $(screenId).classList.add('active');
}
document.querySelectorAll('[data-goto]').forEach((b) =>
  b.addEventListener('click', () => show(b.dataset.goto))
);

/* ═══════════════════════ 1. DRAWING CANVAS ═══════════════════════ */

const dc = $('draw-canvas');
const dctx = dc.getContext('2d');
const PALETTE = ['#4a3b52', '#ff8fb8', '#ffd24a', '#5fd0ae', '#6fc0ff', '#a98cff', '#ff9a6c', '#8bd45f'];

let brush = { color: PALETTE[0], size: 6, erasing: false };
let drawing = false, undoStack = [];

function initCanvas() {
  dctx.fillStyle = '#fff';
  dctx.fillRect(0, 0, dc.width, dc.height);
  dctx.lineCap = dctx.lineJoin = 'round';

  PALETTE.forEach((color, i) => {
    const sw = document.createElement('div');
    sw.className = 'swatch' + (i === 0 ? ' sel' : '');
    sw.style.background = color;
    sw.addEventListener('click', () => {
      brush.color = color;
      brush.erasing = false;
      $('btn-eraser').classList.remove('rec');
      document.querySelectorAll('.swatch').forEach((s) => s.classList.remove('sel'));
      sw.classList.add('sel');
    });
    $('swatches').appendChild(sw);
  });
}

function pushUndo() {
  undoStack.push(dctx.getImageData(0, 0, dc.width, dc.height));
  if (undoStack.length > 25) undoStack.shift();
}

// Map a pointer event to canvas-space coords (canvas is CSS-scaled).
function pos(e) {
  const r = dc.getBoundingClientRect();
  return {
    x: (e.clientX - r.left) * (dc.width / r.width),
    y: (e.clientY - r.top) * (dc.height / r.height),
  };
}

dc.addEventListener('pointerdown', (e) => {
  drawing = true;
  pushUndo();
  dc.setPointerCapture(e.pointerId);
  const p = pos(e);
  dctx.beginPath();
  dctx.moveTo(p.x, p.y);
  dctx.strokeStyle = brush.erasing ? '#fff' : brush.color;
  dctx.lineWidth = brush.erasing ? brush.size * 3 : brush.size;
  dctx.lineTo(p.x + 0.1, p.y);
  dctx.stroke();
});

dc.addEventListener('pointermove', (e) => {
  if (!drawing) return;
  const p = pos(e);
  dctx.strokeStyle = brush.erasing ? '#fff' : brush.color;
  dctx.lineWidth = brush.erasing ? brush.size * 3 : brush.size;
  dctx.lineTo(p.x, p.y);
  dctx.stroke();
});

['pointerup', 'pointerleave', 'pointercancel'].forEach((ev) =>
  dc.addEventListener(ev, () => { drawing = false; })
);

$('brush-size').addEventListener('input', (e) => { brush.size = +e.target.value; });
$('btn-eraser').addEventListener('click', (e) => {
  brush.erasing = !brush.erasing;
  e.target.classList.toggle('rec', brush.erasing);
});
$('btn-undo').addEventListener('click', () => {
  const img = undoStack.pop();
  if (img) dctx.putImageData(img, 0, 0);
});
$('btn-clear').addEventListener('click', () => {
  pushUndo();
  dctx.fillStyle = '#fff';
  dctx.fillRect(0, 0, dc.width, dc.height);
});

function canvasIsBlank() {
  const d = dctx.getImageData(0, 0, dc.width, dc.height).data;
  for (let i = 0; i < d.length; i += 40) {
    if (d[i] !== 255 || d[i + 1] !== 255 || d[i + 2] !== 255) return false;
  }
  return true;
}

/* ═══════════════════════ 2. VOICE INPUT ═══════════════════════ */

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let recog = null;

if (SR) {
  recog = new SR();
  recog.continuous = false;
  recog.interimResults = true;
  recog.lang = 'en-US';

  recog.addEventListener('result', (e) => {
    const text = Array.from(e.results).map((r) => r[0].transcript).join('');
    $('desc-input').value = text;
    $('mic-status').textContent = e.results[0].isFinal ? '✓ Got it!' : 'Listening…';
  });
  recog.addEventListener('end', () => {
    $('btn-mic').classList.remove('rec');
    $('btn-mic').textContent = '🎤 Speak instead';
  });
  recog.addEventListener('error', (e) => {
    $('mic-status').textContent = e.error === 'not-allowed'
      ? 'Microphone blocked — you can type instead!' : 'Could not hear that — try typing!';
  });

  $('btn-mic').addEventListener('click', () => {
    const btn = $('btn-mic');
    if (btn.classList.contains('rec')) { recog.stop(); return; }
    btn.classList.add('rec');
    btn.textContent = '⏹ Stop';
    $('mic-status').textContent = 'Listening…';
    recog.start();
  });
} else {
  $('btn-mic').disabled = true;
  $('btn-mic').textContent = '🎤 Voice not supported here';
}

/* ═════════════ 3. READ-ALOUD + LITTLE SOUNDS ═════════════
   Ages 4-6 often cannot read the prompt, so everything important is
   spoken. Muteable, because on the tenth repeat it is not charming. */

function speak(text) {
  if (state.muted || !text || !('speechSynthesis' in window)) return;
  try {
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(String(text).replace(/[*_#]/g, ''));
    u.rate = 0.92; u.pitch = 1.2; u.volume = 1;
    window.speechSynthesis.speak(u);
  } catch (e) { /* speech is a nice-to-have, never fatal */ }
}
function hushSpeech() {
  try { if ('speechSynthesis' in window) window.speechSynthesis.cancel(); } catch (e) { /* ignore */ }
}

let audioCtx = null;
function blip(freq, dur = 0.12, type = 'sine', gain = 0.06) {
  if (state.muted) return;
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    audioCtx = audioCtx || new AC();
    const o = audioCtx.createOscillator(), g = audioCtx.createGain();
    o.type = type; o.frequency.value = freq;
    g.gain.setValueAtTime(gain, audioCtx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + dur);
    o.connect(g); g.connect(audioCtx.destination);
    o.start(); o.stop(audioCtx.currentTime + dur);
  } catch (e) { /* ignore */ }
}
const sfxPick = () => blip(720, 0.1, 'sine', 0.05);
const sfxNope = () => { blip(300, 0.1, 'triangle', 0.05); setTimeout(() => blip(230, 0.14, 'triangle', 0.04), 90); };
const sfxWin = () => { [660, 830, 990].forEach((f, i) => setTimeout(() => blip(f, 0.16, 'sine', 0.05), i * 90)); };

function syncMuteButton() {
  const b = $('btn-mute');
  b.textContent = state.muted ? '🔇' : '🔊';
  b.classList.toggle('off', state.muted);
  b.title = state.muted ? 'Read-aloud is OFF — click to turn on' : 'Read-aloud is ON — click to mute';
}
$('btn-mute').addEventListener('click', () => {
  state.muted = !state.muted;
  localStorage.setItem('dq-muted', state.muted ? '1' : '0');
  if (state.muted) hushSpeech();
  syncMuteButton();
});
$('btn-replay').addEventListener('click', () => readChallengeAloud());

function readChallengeAloud() {
  const c = state.challenge;
  if (!c) return;
  const bits = [c.narrative, c.prompt].filter(Boolean);
  speak(bits.join('. '));
}

/* ═══════════════════════ 4. THEMES & SETUP ═══════════════════════ */

const THEME_LABELS = {
  storybook: '📖 Storybook', candy: '🍭 Candy', forest: '🌲 Forest',
  ocean: '🌊 Ocean', space: '🚀 Space',
};

async function loadTopics() {
  try {
    const res = await fetch(`${API}/api/topics`);
    const data = await res.json();
    state.topics = data.topics || [];

    (data.themes || []).forEach((t, i) => {
      const chip = document.createElement('div');
      chip.className = 'theme-chip' + (i === 0 ? ' sel' : '');
      chip.textContent = THEME_LABELS[t] || t;
      chip.addEventListener('click', () => {
        state.theme = t;
        document.querySelectorAll('.theme-chip').forEach((c) => c.classList.remove('sel'));
        chip.classList.add('sel');
      });
      $('theme-row').appendChild(chip);
    });

    const health = await (await fetch(`${API}/api/health`)).json();
    $('ai-status').textContent = health.ai_enabled
      ? '✨ AI art & storytelling: ON'
      : '💡 Offline mode — your hero is hand-drawn by the game itself';
  } catch (e) {
    $('ai-status').textContent = '⚠️ Could not reach the game server.';
  }
}

/* ═══════════════════════ 5. BRING TO LIFE ═══════════════════════ */

const LOADING_LINES = [
  'Waking up your character…', 'Painting the sky…', 'Planting the trees…',
  'Filling the river with sparkles…', 'Hiding some math treasures…', 'Almost ready!',
];

/* ── the destination box (OPTIONAL) ──────────────────────────────────
   A child who says "I want to get to the treasure cave" gets a quest
   that actually goes there. A child who says nothing gets one anyway —
   which is why this can never be required. */
const DESTINATION_EXAMPLES = [
  'cross the river',
  'reach the top of the mountain',
  'find the treasure cave',
  'get home before dark',
];

function readDestination() {
  const box = $('dest-input');
  const v = box && typeof box.value === 'string' ? box.value.trim() : '';
  state.destination = v.slice(0, 120);
  return state.destination;
}

function initDestination() {
  const row = $('dest-examples');
  if (!row) return;
  row.innerHTML = '';
  DESTINATION_EXAMPLES.forEach((d) => {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'dest-chip';
    chip.textContent = d;
    chip.addEventListener('click', () => {
      const box = $('dest-input');
      if (box) box.value = d;
      state.destination = d;
    });
    row.appendChild(chip);
  });
  const box = $('dest-input');
  if (box && box.addEventListener) box.addEventListener('input', readDestination);
}

$('btn-bring-to-life').addEventListener('click', async () => {
  const desc = $('desc-input').value.trim();
  const hasDrawing = !canvasIsBlank();
  if (!hasDrawing && !desc) {
    alert('Draw something or tell us about your world first! 🎨');
    return;
  }
  state.description = desc;
  const destination = readDestination();

  show('screen-loading');
  let i = 0;
  const ticker = setInterval(() => {
    i = (i + 1) % LOADING_LINES.length;
    $('loading-sub').textContent = LOADING_LINES[i];
  }, 2200);

  try {
    const res = await fetch(`${API}/api/create-world`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        image_data_url: hasDrawing ? dc.toDataURL('image/png') : null,
        text_description: desc || null,
        destination: destination || null,
        theme: state.theme,
        generate_images: $('toggle-images').checked,
      }),
    });
    if (!res.ok) throw new Error(`Server said ${res.status}`);
    const data = await res.json();

    state.sessionId = data.session_id;
    state.interpretation = data.interpretation || {};
    state.background = data.background;
    state.characterSprite = data.character_sprite;
    state.goalText = firstStr(data.goal_text, data.goal, data.destination, state.destination);
    heroLook = describeHero();

    // The opening background is story frame 0 — same pipeline as every later
    // frame, so it preloads and fades in instead of popping.
    const opening = adoptOpeningFrame(data);
    if (opening) {
      $('reveal-bg').src = opening;
      $('reveal-bg').hidden = false;
    } else {
      $('reveal-bg').hidden = true;
    }
    adoptSprite(data.character_sprite || data.sprite || null);

    const name = state.interpretation.character_name || 'your hero';
    $('reveal-name').textContent = `Meet ${name}! 🌟`;
    $('reveal-hook').textContent = state.interpretation.story_hook || '';

    clearInterval(ticker);
    renderTopics();
    show('screen-topic');
    speak(`Meet ${name}! ${state.interpretation.story_hook || ''}`);
  } catch (err) {
    clearInterval(ticker);
    alert(`Oh no! Something went wrong: ${err.message}`);
    show('screen-create');
  }
});

/* ═══════════════════════ 6. TOPIC PICKER ═══════════════════════ */

const TOPIC_EMOJI = {
  addition: '➕', subtraction: '➖', multiplication: '✖️', fractions: '🍕',
  decimals: '💧', geometry: '🔷', algebra: '🔑', speed_distance_time: '🏃',
};
const BAND_ORDER = ['k1', '23', '45'];
const BAND_LABEL = { k1: '🐣 Ages 4–6', 23: '🌱 Ages 7–8', 45: '🚀 Ages 9–10' };

function renderTopics() {
  const tabs = $('band-tabs');
  tabs.innerHTML = '';
  BAND_ORDER.forEach((b) => {
    const tab = document.createElement('button');
    tab.className = 'band-tab' + (b === state.band ? ' sel' : '');
    tab.textContent = BAND_LABEL[b];
    tab.addEventListener('click', () => { state.band = b; renderTopics(); });
    tabs.appendChild(tab);
  });

  const grid = $('topic-grid');
  grid.innerHTML = '';
  state.topics.forEach((t) => {
    const available = (t.bands || []).some((b) => b.id === state.band);
    const card = document.createElement('div');
    card.className = 'topic-card' + (available ? '' : ' locked');
    card.innerHTML =
      `<span class="emoji">${TOPIC_EMOJI[t.id] || '✨'}</span>` +
      `<div class="name">${t.label}</div>` +
      `<div class="lvl">${available ? 'Tap to play!' : 'For older explorers'}</div>`;
    if (available) card.addEventListener('click', () => startGame(t.id));
    grid.appendChild(card);
  });
}

/* ═══════════════════════ 7. GAME WORLD ═══════════════════════ */

const gc = $('game-canvas');
const gctx = gc.getContext('2d');
const W = gc.width, H = gc.height;

let bgImage = null, spriteImage = null;
let bgOutgoing = null;               // the frame we are fading OUT of
let bgFade = 1;                      // 0 = all outgoing, 1 = all current
const keys = {};
let stones = [];
let sandbars = [];
let props = [];
let particles = [];
let ambient = [];
let ropeGlow = 0;
let tick = 0;                        // global animation clock (frames)
let heroLook = null;                 // parsed creature description
let running = false;

let hero = { x: 90, y: 0, w: 58, h: 58, vx: 0, vy: 0, face: 1, bob: 0, sq: 0, bounce: 0 };

const HERO_SPEED = 4.6;

/* play area — the vertical band stones + hero live in. */
let play = { top: 0.46 * H, bottom: 0.94 * H };

/* ── camera: one background image, many scenes ───────────────────── */
const CAMERAS = {
  wide:      { x: 0.00, y: 0.00, w: 1.00, h: 1.00 },
  left_bank: { x: 0.00, y: 0.16, w: 0.56, h: 0.74 },
  midstream: { x: 0.23, y: 0.22, w: 0.54, h: 0.70 },
  far_bank:  { x: 0.44, y: 0.16, w: 0.56, h: 0.74 },
  high:      { x: 0.14, y: 0.00, w: 0.72, h: 0.64 },
};
let cam = { ...CAMERAS.wide };
let camTarget = { ...CAMERAS.wide };

function setCamera(name) {
  camTarget = { ...(CAMERAS[name] || CAMERAS.wide) };
}
function easeCamera() {
  cam.x = lerp(cam.x, camTarget.x, 0.055);
  cam.y = lerp(cam.y, camTarget.y, 0.055);
  cam.w = lerp(cam.w, camTarget.w, 0.055);
  cam.h = lerp(cam.h, camTarget.h, 0.055);
}

/* ── time of day ─────────────────────────────────────────────────── */
const TINT_OVERLAY = {
  day: null,
  dusk: 'rgba(255,170,90,.18)',
  night: 'rgba(40,50,120,.32)',
};
let timeOfDay = 'day';

const SKY_RAMPS = {
  day:   ['#cfeaff', '#eaf7ff', '#dff6ec'],
  dusk:  ['#ffc9a3', '#ffdfc4', '#f7d7e6'],
  night: ['#2b3570', '#4a5596', '#6d6fa8'],
};

/* ═══════════ 7b. STORY FRAMES — crossfade, never block ═══════════
   Every couple of beats the backend paints a NEW background that
   continues the last one. Three rules, in priority order:

     1. Gameplay never waits. If the next frame is not ready we keep
        playing on the current one and swap in whenever it lands —
        possibly several seconds into the beat, possibly never.
     2. No hard cut and no flash: the image is fully decoded BEFORE the
        crossfade starts, and the outgoing frame stays on screen under
        it for the whole fade.
     3. The API shape is not settled, so we accept inline image data OR
        a frame id to fetch, and shrug if neither is there. */

const FRAME_FADE_FRAMES = 52;        // ≈0.85s at 60fps
const FRAME_RETRY_MS = 1100;
const FRAME_MAX_TRIES = 12;

let bgKey = null;                    // identity of the frame on screen
let bgRank = -1;                     // its position in the story (frame index)
let frameTarget = -1;                // newest rank a beat has declared CURRENT
let frameAuto = 0;                   // rank counter for payloads with no index
let frameEpoch = 0;                  // bumps on resetFrames — kills old loads
const framePending = Object.create(null);   // frame key → rank, in flight

function pendingHas(k) { return k != null && framePending[k] !== undefined; }
function pendingClear(k) { if (k != null) delete framePending[k]; }

/* Frames are ORDERED, and only ever move forward. A frame is dropped if a
   later one is already showing, or if a later one has since been declared
   current — otherwise a slow frame 2 landing after frame 3 would rewind the
   story in front of the child. */
function rankFor(r) {
  if (r === null || r === undefined) return ++frameAuto;
  frameAuto = Math.max(frameAuto, r);
  return r;
}
function frameStale(rank, epoch) {
  if (epoch !== frameEpoch) return true;
  return num(rank, 0) < Math.max(bgRank, frameTarget);
}

/* Field names an image could plausibly arrive under. Inline picture data
   comes FIRST: when the beat already carries the bytes there is no reason
   to go back to the network for them. */
const FRAME_SRC_KEYS = [
  'image_data_url', 'data_url', 'dataUrl', 'image', 'png', 'b64_json', 'b64',
  'background', 'data', 'image_url', 'imageUrl', 'background_url', 'url', 'src',
];
const FRAME_ID_KEYS = ['frame_id', 'frameId', 'background_id', 'image_id', 'scene_id'];
const FRAME_NEXT_ID_KEYS = ['next_id', 'nextId', 'next_frame_id'];
const FRAME_RANK_KEYS = ['index', 'frame_index', 'frameIndex'];

function asImageSrc(v) {
  if (typeof v !== 'string') return null;
  const s = v.trim();
  if (!s) return null;
  if (/^data:image\//i.test(s)) return s;
  if (/^https?:\/\//i.test(s)) return s;
  if (/^\/[^/]/.test(s)) return API + s;            // server-relative path
  // Bare base64 (what a raw b64_json field looks like). Long + base64-ish.
  if (s.length > 256 && /^[A-Za-z0-9+/=\s]+$/.test(s)) return `data:image/png;base64,${s.replace(/\s+/g, '')}`;
  return null;
}

// Pull a usable image src out of whatever object we were handed.
function frameSrcFrom(obj) {
  if (!obj || typeof obj !== 'object') return null;
  for (const k of FRAME_SRC_KEYS) {
    const direct = asImageSrc(obj[k]);
    if (direct) return direct;
    // one level of nesting: { image: { url: … } }
    if (obj[k] && typeof obj[k] === 'object') {
      for (const k2 of FRAME_SRC_KEYS) {
        const nested = asImageSrc(obj[k][k2]);
        if (nested) return nested;
      }
    }
  }
  return null;
}

function idish(v) {
  if (typeof v === 'string' && v.trim()) return v.trim();
  if (typeof v === 'number' && isFinite(v)) return String(v);
  return null;
}

function frameIdFrom(obj) {
  if (!obj || typeof obj !== 'object') return null;
  for (const k of FRAME_ID_KEYS) {
    const v = idish(obj[k]);
    if (v) return v;
  }
  // `id` only counts inside something that is itself a frame object.
  return idish(obj.id);
}

function frameRankFrom(obj) {
  if (!obj || typeof obj !== 'object') return null;
  return firstNum.apply(null, FRAME_RANK_KEYS.map((k) => obj[k]));
}

// Everywhere a frame could be hiding on a response.
function frameHolders(challenge) {
  const beat = field(challenge, 'beat');
  return [
    field(challenge, 'frame'), field(challenge, 'arrival_frame'),
    field(challenge, 'story_frame'), field(challenge, 'scene'),
    field(beat, 'frame'), field(beat, 'story_frame'), field(beat, 'scene'),
    challenge, beat,
  ].filter((h) => h && typeof h === 'object');
}

function frameDescriptor(challenge) {
  if (!challenge || typeof challenge !== 'object') return null;
  for (const h of frameHolders(challenge)) {
    const src = frameSrcFrom(h);
    const id = frameIdFrom(h);
    if (src || id) {
      return {
        src, id, rank: frameRankFrom(h),
        key: firstStr(id, src ? src.slice(0, 64) : '') || 'frame',
      };
    }
  }
  return null;
}

/* The frame the painter is STILL WORKING ON. Polling it is what lets a
   picture slide in halfway through a question instead of the child having
   to finish one first. */
function nextFrameDescriptor(challenge) {
  if (!challenge || typeof challenge !== 'object') return null;
  for (const h of frameHolders(challenge)) {
    let id = null;
    for (const k of FRAME_NEXT_ID_KEYS) { id = id || idish(h[k]); }
    if (!id) continue;
    const status = h.next_status;
    if (typeof status === 'string' && /error|failed/i.test(status)) continue;
    const url = typeof h.next_url === 'string' && h.next_url.trim() ? h.next_url.trim() : null;
    return {
      id, key: id, url,
      rank: firstNum(h.wanted_index, h.next_index, h.wantedIndex),
    };
  }
  return null;
}

function resetFrames() {
  frameEpoch++;
  Object.keys(framePending).forEach(pendingClear);
  bgKey = null;
  bgRank = -1;
  frameTarget = -1;
  frameAuto = 0;
  bgOutgoing = null;
  bgImage = null;
  bgFade = 1;
}

function imgReady(img) {
  return !!(img && img.complete && img.naturalWidth > 0);
}

// Swap the decoded image in and start the fade. The frame we were showing
// keeps drawing underneath until the fade finishes.
function commitFrame(img, key, rank) {
  bgOutgoing = imgReady(bgImage) ? bgImage : null;
  bgImage = img;
  if (key != null) bgKey = key;
  bgRank = num(rank, bgRank);
  bgFade = 0;
}

/* Preload + decode, THEN fade. A load failure is silent on purpose: the
   child keeps playing on the picture they already have. */
function crossfadeTo(src, key, rank) {
  if (!src) return null;
  const r = rankFor(rank);
  const epoch = frameEpoch;
  if (key != null) framePending[key] = r;
  let img;
  try { img = new Image(); } catch (e) { pendingClear(key); return null; }
  const go = () => {
    pendingClear(key);
    if (frameStale(r, epoch) || !imgReady(img)) return;
    commitFrame(img, key, r);
  };
  img.onload = () => {
    if (typeof img.decode === 'function') {
      try { img.decode().then(go, go); } catch (e) { go(); }
    } else go();
  };
  img.onerror = () => { pendingClear(key); };
  try { img.src = src; } catch (e) { pendingClear(key); return img; }
  // Cached images can already be complete before onload is wired up.
  if (imgReady(img)) go();
  return img;
}

/* GET /api/frame/{id}. The endpoint may answer with JSON carrying the
   image, JSON saying "not yet" ({ready:false} / {status:"pending"}), raw
   image bytes, or 404/409 while the painter is still working — all of
   those mean "keep playing, ask again shortly". */
const FRAME_RETRY_STATUS = [202, 204, 404, 409, 425, 429, 503];

async function fetchFrameById(id, key, rank, epoch, tries, url) {
  tries = tries || 0;
  if (frameStale(rank, epoch) || tries >= FRAME_MAX_TRIES) { pendingClear(key); return; }
  const target = url || `${API}/api/frame/${encodeURIComponent(id)}`;
  let retry = false;
  try {
    const res = await fetch(target);
    const status = num(res && res.status, 200);
    if (FRAME_RETRY_STATUS.indexOf(status) >= 0) {
      retry = true;
    } else if (res && res.ok === false) {
      retry = false;                                   // a real error: give up quietly
    } else {
      let ct = '';
      try { ct = (res.headers && res.headers.get && res.headers.get('content-type')) || ''; } catch (e) { ct = ''; }
      if (ct && ct.indexOf('image/') === 0) {
        pendingClear(key);                             // endpoint serves bytes
        if (!frameStale(rank, epoch)) crossfadeTo(target, key, rank);
        return;
      }
      let data = null;
      try { data = await res.json(); } catch (e) { data = null; }
      const src = frameSrcFrom(data);
      if (src) {
        pendingClear(key);
        if (!frameStale(rank, epoch)) {
          crossfadeTo(src, key, firstNum(frameRankFrom(data), rank));
        }
        return;
      }
      // JSON but no picture yet — {ready:false}, {status:"pending"}, {}
      retry = true;
    }
  } catch (e) {
    retry = true;
  }
  if (!retry || frameStale(rank, epoch)) { pendingClear(key); return; }
  setTimeout(() => fetchFrameById(id, key, rank, epoch, tries + 1, url),
             FRAME_RETRY_MS * (1 + tries * 0.4));
}

function startFrameLoad(d, rank) {
  const r = rankFor(rank);
  if (frameStale(r, frameEpoch)) return false;
  if (d.src) { crossfadeTo(d.src, d.key, r); return true; }
  if (d.id) {
    framePending[d.key] = r;
    fetchFrameById(d.id, d.key, r, frameEpoch, 0, d.url);
    return true;
  }
  return false;
}

/* The world the child just made IS story frame 0 — the anchor every later
   frame is painted from. It normally arrives with its bytes inline, so use
   those and label them with the real frame id; that way the first beat,
   which names the same frame, recognises it instead of refetching. */
function adoptOpeningFrame(data) {
  resetFrames();
  if (!data || typeof data !== 'object') return null;
  const holder = field(data, 'frame');
  const key = firstStr(frameIdFrom(holder), 'world:0');
  const rank = num(firstNum(frameRankFrom(holder)), 0);
  frameTarget = rank;
  const src = frameSrcFrom(data) || asImageSrc(data.background);
  if (src) { crossfadeTo(src, key, rank); return src; }
  // No bytes inline — fall back to fetching frame 0 by id, if we have one.
  const id = frameIdFrom(holder);
  if (id) startFrameLoad({ id, key, rank }, rank);
  return null;
}

/* Called once per beat. Two jobs:
     · make sure the frame this beat says is CURRENT is on screen, and
     · start polling the one the painter has not finished yet, so it can
       slide in mid-question rather than waiting for the next one.
   Cheap and side-effect-free when nothing changed. */
function applyFrame(challenge) {
  let started = false;

  const f = frameDescriptor(challenge);
  if (f) {
    const r = rankFor(f.rank);
    frameTarget = Math.max(frameTarget, r);       // only CURRENT moves the target
    if (f.key !== bgKey && !pendingHas(f.key)) started = startFrameLoad(f, r) || started;
  }

  const nx = nextFrameDescriptor(challenge);
  if (nx && nx.key !== bgKey && !pendingHas(nx.key)) {
    const nr = nx.rank === null || nx.rank === undefined
      ? Math.max(frameTarget, bgRank) + 1 : nx.rank;
    started = startFrameLoad(nx, nr) || started;
  }
  return started;                                  // false → keep what we have
}

/* The hero sprite can also be refreshed mid-quest. Decode first, then swap —
   a half-loaded sprite would pop the procedural creature for one frame. */
function adoptSprite(src) {
  const url = asImageSrc(src);
  if (!url) { spriteImage = null; return null; }
  let img;
  try { img = new Image(); } catch (e) { return null; }
  const go = () => { if (imgReady(img)) spriteImage = img; };
  img.onload = () => {
    if (typeof img.decode === 'function') {
      try { img.decode().then(go, go); } catch (e) { go(); }
    } else go();
  };
  img.onerror = () => { /* keep whatever we had — often the procedural hero */ };
  try { img.src = url; } catch (e) { return img; }
  if (imgReady(img)) go();
  return img;
}

/* A frame may ride on the envelope rather than on the challenge. Only look
   when the envelope actually advertises one — blindly scanning every
   response would happily chase an unrelated `id` field. */
function applyFrameFromResponse(data) {
  if (!data || typeof data !== 'object') return false;
  const advertises = data.frame || data.arrival_frame || data.story_frame || data.scene ||
    FRAME_ID_KEYS.some((k) => data[k] != null) ||
    FRAME_NEXT_ID_KEYS.some((k) => data[k] != null) ||
    typeof data.background === 'string';
  if (!advertises) return false;
  try { return applyFrame(data); } catch (e) { return false; }
}

/* ═══════════ 7c. THE JOURNEY — goal + progress trail ═══════════
   "Questions are stepping stones toward a destination." A child should be
   able to glance up and see where they're going and how far they've come,
   without reading a word of it if they can't. */

const TRAIL_SPAN = 86;               // % of the track the walker travels
const TRAIL_WALK_EMOJI = '🐾';

const journey = { progress: 0, goal: '', remaining: '', baseDistance: null, arrived: false };

// Journey fields sent on the response ENVELOPE rather than inside `quest`.
// Always reassigned (possibly to null) by the newest response, so a stale
// value can never pin the trail in place.
const JOURNEY_KEYS = [
  'goal_text', 'goal_label', 'quest_goal', 'destination_text', 'destination', 'goal',
  'journey_progress', 'progress', 'journey_pct', 'progress_pct',
  'distance_remaining', 'steps_remaining', 'distance_total', 'journey_total',
  'distance_text', 'distance_remaining_text', 'remaining_text', 'journey_text',
];
let journeyExtra = null;

function captureJourney(data) {
  if (!data || typeof data !== 'object') { journeyExtra = null; return; }
  const found = {};
  let any = false;
  JOURNEY_KEYS.forEach((k) => {
    if (data[k] !== undefined && data[k] !== null) { found[k] = data[k]; any = true; }
  });
  journeyExtra = any ? found : null;
}

function resetJourney() {
  journey.progress = 0;
  journey.goal = '';
  journey.remaining = '';
  journey.baseDistance = null;
  journey.arrived = false;
  journeyExtra = null;
}

// Every place a goal / progress number could legitimately turn up,
// freshest first.
function journeySources() {
  const c = state.challenge || {};
  const q = state.quest || {};
  return [
    journeyExtra,
    q, field(q, 'state'), field(q, 'journey'), field(q, 'goal'),
    c, field(c, 'beat'), field(c, 'journey'),
  ].filter((o) => o && typeof o === 'object');
}

function pickField(key) {
  for (const o of journeySources()) {
    if (o[key] !== undefined && o[key] !== null) return o[key];
  }
  return undefined;
}

function readGoalText() {
  const direct = firstStr(
    pickField('goal_text'), pickField('goal_label'), pickField('quest_goal'),
    pickField('destination_text'), pickField('destination'),
  );
  if (direct) return direct;
  // `goal` may itself be an object ({ text: … }) rather than a string.
  const g = pickField('goal');
  if (typeof g === 'string') return g.trim();
  const nested = firstStr(field(g, 'text'), field(g, 'label'), field(g, 'name'));
  if (nested) return nested;
  return firstStr(state.goalText, state.destination);
}

function readProgress() {
  let p = firstNum(
    pickField('journey_progress'), pickField('journey_pct'), pickField('progress_pct'),
  );
  if (p === null) {
    // A field called plainly `progress` is only trustworthy as a FRACTION.
    // An integer there could just as easily mean "beat 3", and reading that
    // as 3% would march the hero backwards down the trail.
    const loose = firstNum(pickField('progress'));
    if (loose !== null && loose >= 0 && loose <= 1) p = loose;
  }
  if (p !== null) {
    if (p > 1 && p <= 100) p /= 100;               // sent as a percentage
    return clamp(p, 0, 1);
  }

  // No progress field: derive it from how much distance is left.
  const dist = firstNum(pickField('distance_remaining'), pickField('steps_remaining'));
  if (dist !== null) {
    const total = firstNum(pickField('distance_total'), pickField('journey_total'));
    const base = total !== null && total > 0 ? total
      : (journey.baseDistance === null ? Math.max(dist, 1) : journey.baseDistance);
    journey.baseDistance = base;
    return clamp(1 - dist / base, 0, 1);
  }

  // Last resort: the chapter we're on. Beat 0 is the start line, the final
  // beat IS the arrival, so the walker reaches the flag exactly on cue.
  const beat = field(state.challenge, 'beat');
  const total = num(field(beat, 'total'), 0);
  const idx = num(field(beat, 'index'), 0);
  if (total > 1) return clamp(idx / (total - 1), 0, 1);
  return journey.progress;
}

function readRemainingText() {
  const txt = firstStr(
    pickField('distance_text'), pickField('distance_remaining_text'),
    pickField('remaining_text'), pickField('journey_text'),
  );
  if (txt) return txt;
  const dist = firstNum(pickField('distance_remaining'), pickField('steps_remaining'));
  if (dist !== null && dist > 0 && dist <= 99) {
    const n = Math.round(dist);
    return n === 1 ? '1 step to go' : `${n} steps to go`;
  }
  const beat = field(state.challenge, 'beat');
  const total = num(field(beat, 'total'), 0);
  const idx = num(field(beat, 'index'), 0);
  const left = total - idx - 1;
  if (total > 1 && left > 0) return left === 1 ? '1 stop to go' : `${left} stops to go`;
  return '';
}

function renderTrailDots(count, progress) {
  const box = $('trail-dots');
  if (!box) return;
  const n = clamp(Math.round(count) || 6, 3, 12);
  box.innerHTML = '';
  for (let i = 0; i < n; i++) {
    const at = n === 1 ? 1 : i / (n - 1);
    const dot = document.createElement('i');
    dot.className = 'trail-dot' + (at <= progress + 1e-6 ? ' lit' : '');
    dot.style.left = `${at * TRAIL_SPAN}%`;
    box.appendChild(dot);
  }
}

function updateJourney() {
  const row = $('quest-trail');
  if (!row) return;

  const goal = readGoalText();
  const progress = clamp(num(readProgress(), 0), 0, 1);
  const remaining = readRemainingText();

  // Nothing to say and nowhere to go → stay out of the child's way.
  if (!goal && progress <= 0 && !remaining) { row.hidden = true; return; }
  row.hidden = false;

  journey.goal = goal;
  journey.progress = progress;
  journey.remaining = remaining;
  journey.arrived = progress >= 0.999;

  const label = $('trail-goal');
  if (label) label.textContent = goal || 'Follow the trail!';

  const fill = $('trail-fill');
  if (fill) fill.style.width = `${progress * TRAIL_SPAN}%`;

  const walker = $('trail-walker');
  if (walker) {
    walker.style.left = `${progress * TRAIL_SPAN}%`;
    walker.textContent = journey.arrived ? '🎉' : (heroEmoji() || TRAIL_WALK_EMOJI);
  }

  const pin = $('trail-pin');
  if (pin) pin.textContent = journey.arrived ? '🏁' : '🚩';

  const rem = $('trail-remaining');
  if (rem) {
    const text = journey.arrived ? 'You made it! 🎉' : remaining;
    rem.textContent = text;
    rem.hidden = !text;
  }

  const beat = field(state.challenge, 'beat');
  renderTrailDots(num(field(beat, 'total'), 6), progress);
  if (row.classList) row.classList.toggle('arrived', journey.arrived);
}

/* ═══════════════════════ 8. CHALLENGE ═══════════════════════ */

async function startGame(topic) {
  try {
    const res = await fetch(`${API}/api/start-game`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: state.sessionId, topic, band: state.band }),
    });
    if (!res.ok) {
      let detail = 'could not start';
      try { detail = (await res.json()).detail || detail; } catch (e) { /* ignore */ }
      throw new Error(detail);
    }
    const data = await res.json();
    lastBeatKey = null;
    resetJourney();
    state.quest = data.quest || null;
    state.goalText = firstStr(
      data.goal_text, data.goal, data.destination,
      field(data.quest, 'goal_text'), field(data.quest, 'destination'),
      state.goalText, state.destination,
    );
    captureJourney(data);
    applyFrameFromResponse(data);
    applyChallenge(data.challenge, data.stats);
    show('screen-game');
    if (!running) { running = true; requestAnimationFrame(loop); }
    // The quest opening sets the scene before the first puzzle.
    if (data.opening) {
      setTimeout(() => showNarration(data.opening, questTitle() || 'Your quest', 5200), 420);
    }
  } catch (err) {
    alert(`Could not start that adventure: ${err.message}`);
  }
}

function questTitle() {
  const q = state.quest;
  return (q && (q.title || q.quest_title)) || null;
}

// The quest engine's endpoint is /api/next-beat; /api/next-challenge is kept
// as a legacy alias. Try the new one, fall back if this server predates it.
async function postJSON(paths, body) {
  let lastErr = null;
  for (const p of paths) {
    try {
      const res = await fetch(`${API}${p}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (res.status === 404 || res.status === 405) { lastErr = new Error('404'); continue; }
      return await res.json();
    } catch (e) { lastErr = e; }
  }
  throw lastErr || new Error('request failed');
}

async function nextChallenge() {
  try {
    const data = await postJSON(['/api/next-beat', '/api/next-challenge'], { session_id: state.sessionId });
    if (data.quest) state.quest = data.quest;
    captureJourney(data);
    applyFrameFromResponse(data);
    if (data.complete || !data.challenge) { finishQuest(data); return; }
    applyChallenge(data.challenge, data.stats);
  } catch (e) {
    showToast('Lost the connection!', 'Check the server and try Skip.', 'bad');
    state.locked = false;
  }
}

// End of the arc: the epilogue, then back to the map.
function finishQuest(data) {
  state.locked = true;
  updateStats(data.stats);
  // The hero arrived. Walk them the last inch to the flag.
  if (data.quest) state.quest = data.quest;
  journey.progress = 1;
  try {
    const row = $('quest-trail');
    if (row && !row.hidden) {
      const fill = $('trail-fill');
      if (fill) fill.style.width = `${TRAIL_SPAN}%`;
      const walker = $('trail-walker');
      if (walker) { walker.style.left = `${TRAIL_SPAN}%`; walker.textContent = '🎉'; }
      const pin = $('trail-pin');
      if (pin) pin.textContent = '🏁';
      const rem = $('trail-remaining');
      if (rem) { rem.textContent = 'You made it! 🎉'; rem.hidden = false; }
      renderTrailDots(num(field(field(state.challenge, 'beat'), 'total'), 6), 1);
      if (row.classList) row.classList.add('arrived');
      journey.arrived = true;
    }
  } catch (e) { /* the ending is not worth a crash */ }
  const ep = data.epilogue || 'And that was the end of the adventure.';
  showToast('Quest complete! 🎉', questTitle() || '', 'good');
  sfxWin();
  setTimeout(() => showNarration(ep, 'The End', 7000), 900);
  setTimeout(() => { show('screen-topic'); running = false; }, 8200);
}

/* Tapping is the way in. Walking still works wherever walking is the
   actual puzzle (paths, perimeters, scattered treasure), and is offered
   as the second sentence rather than the first. */
const MODE_HINTS = {
  single_choice: '👆 Tap the answer you think is right',
  multi_select: '👆 Tap every answer you want · tap again to un-pick · then <b>Lock it in ✓</b>',
  collect_count: '👆 Tap the treasures to collect them — or walk over them with <kbd>←</kbd><kbd>↑</kbd><kbd>↓</kbd><kbd>→</kbd>',
  ordered_path: '👆 Tap the stones <b>in order</b> — or walk the path yourself! A wrong one just bounces back.',
};

let lastBeatKey = null;

function applyChallenge(challenge, stats) {
  if (!challenge) {
    showToast('Hmm, no puzzle arrived', 'Tap Skip to try again.', 'bad');
    return;
  }
  state.challenge = challenge;
  state.selected = [];
  state.locked = false;
  prevTouched = new Set();
  orderMisses = 0;
  orderUnenforced = false;
  if (stats) updateStats(stats);

  /* ── play area ── */
  const pa = challenge.play_area || {};
  play.top = clamp(num(pa.top_pct, 0.46), 0.05, 0.9) * H;
  play.bottom = clamp(num(pa.bottom_pct, 0.94), 0.15, 1) * H;
  if (play.bottom - play.top < 90) play.bottom = Math.min(H, play.top + 90);

  /* ── beat / chapter ── */
  applyBeat(challenge.beat);

  /* ── the story picture for this beat, and the map trail ──
     applyFrame is fire-and-forget: if the painter is still working the
     child plays this whole beat on the previous frame and the new one
     slides in whenever it lands. */
  try { applyFrame(challenge); } catch (e) { /* a frame is never worth a crash */ }
  const newSprite = challenge.character_sprite || challenge.sprite ||
    field(challenge.beat, 'character_sprite');
  if (newSprite) { try { adoptSprite(newSprite); } catch (e) { /* keep the old hero */ } }
  try { updateJourney(); } catch (e) { /* ditto */ }

  /* ── ★ THE PROBLEM — always rendered, in its own loud banner ★ ── */
  const promptText = challenge.prompt || challenge.question || '…';
  $('problem-text').textContent = promptText;

  /* ── story narration lives in its own bar, side by side ── */
  $('story-text').textContent = challenge.narrative || 'Let’s go!';
  $('story-avatar').textContent = TOPIC_EMOJI[challenge.topic] || heroEmoji();

  /* ── controls hint depends on the interaction mode ── */
  const qt = questionType();
  $('controls-hint').innerHTML = MODE_HINTS[qt] || MODE_HINTS.single_choice;

  $('selected-row').innerHTML = '';
  layoutStones(challenge.stones || []);
  layoutProps(challenge.props);
  renderAnswerTray();          // ★ the answers are DOM cards, not painted blobs
  renderSelected();

  // Far enough in that the (much larger) hero is never clipped by the
  // left edge of the plate, and never standing on the first stone.
  hero.x = 52;
  hero.y = clamp(play.bottom - hero.h - 14, play.top, H - hero.h);
  hero.face = 1;

  readChallengeAloud();
}

function questionType() {
  const c = state.challenge || {};
  const known = ['single_choice', 'multi_select', 'collect_count', 'ordered_path'];
  if (known.includes(c.question_type)) return c.question_type;
  // unknown / missing → infer from grade_mode, else degrade to multi_select
  if (c.grade_mode === 'order') return 'ordered_path';
  if (c.grade_mode === 'count') return 'collect_count';
  if (c.grade_mode === 'groups') return 'multi_select';
  if (num(c.target_count, 1) === 1) return 'single_choice';
  return 'multi_select';
}

/* ── beats: "Chapter 3 of 6: The Broken Bridge" ── */
function applyBeat(beat) {
  const row = $('chapter-row');
  if (!beat || typeof beat !== 'object') {
    row.hidden = true;
    setCamera('wide');
    setTimeOfDay('day');
    lastBeatKey = null;
    return;
  }
  if (beat.quest_title && !state.quest) state.quest = { title: beat.quest_title };

  const total = Math.max(1, num(beat.total, 1));
  const idx = clamp(num(beat.index, 0), 0, Math.max(0, total));
  const shown = Math.min(total, idx + 1);   // story_engine's index is 0-based
  const title = beat.title ? `: ${beat.title}` : '';

  row.hidden = false;
  $('beat-chip').textContent = `Chapter ${shown} of ${total}${title}`;
  $('beat-fill').style.width = `${(shown / total) * 100}%`;

  setCamera(beat.camera);
  setTimeOfDay(beat.tint);

  const key = `${idx}|${beat.title || ''}`;
  if (key !== lastBeatKey) {
    const hadPage = lastBeatKey !== null;
    lastBeatKey = key;
    // A new chapter is a new PAGE. Mid-beat picture swaps keep the canvas
    // crossfade; only a beat change turns the leaf.
    if (hadPage) playPageTurn();
    if (beat.intro) showNarration(beat.intro, `Chapter ${shown}`, 3600);
  }
}

/* ── the page turn ───────────────────────────────────────────────────
   A cream leaf sweeps across the illustration and the words settle in
   behind it. Restarting a CSS animation needs the class removed, a
   reflow, then the class added again — hence the deliberate layout read.
   Purely decorative: every step is optional and swallowed. */
let pageTurnTimer = null;
function playPageTurn() {
  try {
    const turn = $('page-turn');
    const book = $('storybook');
    if (!turn || !turn.classList) return;
    turn.classList.remove('turn');
    if (book && book.classList) book.classList.remove('page-changing');
    void turn.offsetWidth;                       // force the reflow
    turn.hidden = false;
    turn.classList.add('turn');
    if (book && book.classList) book.classList.add('page-changing');
    clearTimeout(pageTurnTimer);
    pageTurnTimer = setTimeout(() => {
      try {
        turn.classList.remove('turn');
        turn.hidden = true;
        if (book && book.classList) book.classList.remove('page-changing');
      } catch (e) { /* ignore */ }
    }, 900);
  } catch (e) { /* a transition is never worth a crash */ }
}

function setTimeOfDay(t) {
  const next = ['day', 'dusk', 'night'].includes(t) ? t : 'day';
  if (next !== timeOfDay) { timeOfDay = next; seedAmbient(); }
}

let narrationTimer = null;
function showNarration(text, badge, ms = 3200) {
  const card = $('narration-card');
  $('narration-text').textContent = text;
  $('narration-badge').textContent = badge || 'Story';
  card.hidden = false;
  requestAnimationFrame(() => card.classList.add('show'));
  clearTimeout(narrationTimer);
  narrationTimer = setTimeout(() => {
    card.classList.remove('show');
    setTimeout(() => { card.hidden = true; }, 450);
  }, ms);
  speak(text);
}

function updateStats(stats) {
  if (!stats) return;
  state.stats = stats;
  $('hud-score').textContent = num(stats.score, 0);
  $('hud-streak').textContent = num(stats.streak, 0);
  $('hud-level').textContent = num(stats.level, 1);
  $('hud-acc').textContent = stats.answered ? `${stats.accuracy}%` : '—';
}

/* ═══════════════════════ 9. LAYOUT ═══════════════════════ */

const TINT_HEX = {
  mint: '#b6f0d8', lemon: '#fff2bf', sky: '#cfe8ff', lav: '#e0d7ff',
  peach: '#ffe0cc', pink: '#ffd3e2', sage: '#d8eec4', coral: '#ffd5cc',
};
const TINT_EDGE = {
  mint: '#5fd0ae', lemon: '#ffd24a', sky: '#6fc0ff', lav: '#a98cff',
  peach: '#ff9a6c', pink: '#ff8fb8', sage: '#8bd45f', coral: '#ff8f7a',
};

function baseStone(s, i) {
  return {
    id: num(s.id, i),
    label: s.label == null ? '' : String(s.label),
    value: s.value,
    group: s.group,
    tint: s.tint,
    shape: s.shape,
    dots: num(s.dots, 0),
    x: 0, y: 0, r: 40,
    picked: false, reveal: null, pop: 0, shake: 0,
    touchLock: false, orderIndex: 0, small: false,
    wobble: Math.random() * TAU,
  };
}

// Grouped layout: stones sharing a `group` sit together on their own sandbar,
// with clear gaps between. This is what makes "3 of these 4 sandbars"
// readable at a glance instead of a guessing game.
function layoutGroupedStones(list) {
  const groups = [...new Set(list.map((s) => s.group))];
  const gCount = Math.max(1, groups.length);
  const bandW = (W - 190) / gCount;
  const cy = (play.top + play.bottom) / 2;

  stones = [];
  sandbars = [];
  groups.forEach((g, gi) => {
    const members = list.filter((s) => s.group === g);
    const cx = 95 + bandW * gi + bandW / 2;
    const spread = Math.min(46, bandW / (members.length + 0.6));

    members.forEach((s, mi) => {
      const st = baseStone(s, stones.length);
      st.x = cx + (mi - (members.length - 1) / 2) * spread * 1.35;
      st.y = cy;
      st.r = 32;
      stones.push(st);
    });
    const t = members[0] && members[0].tint;
    sandbars.push({
      x: cx, y: cy + 8, group: g,
      w: Math.max(96, members.length * spread * 1.35 + 58),
      tint: TINT_HEX[t] || '#f2e7d6',
      edge: TINT_EDGE[t] || '#d8c3a8',
    });
  });
}

// Many small unlabeled pickups scattered across the play area.
function layoutScatter(list) {
  sandbars = [];
  const n = list.length;
  const cols = Math.ceil(Math.sqrt(n * 1.9)) || 1;
  const rows = Math.ceil(n / cols);
  const padX = 150, padY = 26;
  const cw = (W - padX * 2) / Math.max(1, cols);
  const ch = (play.bottom - play.top - padY * 2) / Math.max(1, rows);

  stones = list.map((s, i) => {
    const st = baseStone(s, i);
    const c = i % cols, r = Math.floor(i / cols);
    st.x = padX + cw * (c + 0.5) + (Math.random() - 0.5) * cw * 0.45;
    st.y = play.top + padY + ch * (r + 0.5) + (Math.random() - 0.5) * ch * 0.45;
    st.r = 19;
    st.small = true;
    return st;
  });
}

// Lay stones out in 1-2 rows across the play area.
function layoutRows(list) {
  sandbars = [];
  const n = list.length;
  const twoRows = n > 6;
  const perRow = twoRows ? Math.ceil(n / 2) : n;
  const rows = twoRows ? 2 : 1;
  const r = clamp(46 - Math.max(0, perRow - 4) * 3.4, 26, 46);
  const midY = (play.top + play.bottom) / 2;
  const rowGap = Math.min(120, (play.bottom - play.top) * 0.42);
  const startY = rows === 1 ? midY : midY - rowGap / 2;

  stones = list.map((s, i) => {
    const st = baseStone(s, i);
    const row = Math.floor(i / perRow);
    const col = i % perRow;
    const countInRow = row === rows - 1 ? n - perRow * row : perRow;
    // The hero is drawn large and starts on the left, so the row begins
    // clear of it rather than underneath it.
    const span = W - 300;
    const spacing = countInRow > 1 ? span / (countInRow - 1) : 0;
    st.x = countInRow === 1 ? W / 2 : 200 + col * spacing;
    st.y = startY + row * rowGap;
    st.r = r;
    return st;
  });
}

function layoutStones(list) {
  list = Array.isArray(list) ? list : [];
  if (!list.length) { stones = []; sandbars = []; return; }

  const qt = questionType();
  const hasExplicit = list.some((s) => s.x_pct != null || s.y_pct != null);
  const groupVals = new Set(list.map((s) => s.group).filter((g) => g != null));

  // Sandbars are ONLY for real "n of d equal parts" fraction puzzles.
  // Other modes also use `group`, but to mean something else entirely —
  // berry colour, for instance — and drawing islands under those would
  // tell the child a lie about what they are picking.
  const isPartition = (state.challenge || {}).grade_mode === 'groups' && groupVals.size > 1;

  if (isPartition) layoutGroupedStones(list);
  else if (qt === 'collect_count') layoutScatter(list);
  else layoutRows(list);

  // Explicit placement always wins over the automatic layout.
  if (hasExplicit) {
    const byId = new Map(stones.map((s) => [s.id, s]));
    list.forEach((src, i) => {
      const st = byId.get(num(src.id, i)) || stones[i];
      if (!st) return;
      if (src.x_pct != null) st.x = clamp(num(src.x_pct, 0.5), 0, 1) * W;
      if (src.y_pct != null) st.y = clamp(num(src.y_pct, 0.7), 0, 1) * H;
    });
    // sandbars follow their stones
    sandbars.forEach((b) => {
      const mem = stones.filter((s) => s.group === b.group);
      if (!mem.length) return;
      const xs = mem.map((s) => s.x);
      b.x = (Math.min(...xs) + Math.max(...xs)) / 2;
      b.y = mem.reduce((a, s) => a + s.y, 0) / mem.length + 8;
      b.w = Math.max(96, (Math.max(...xs) - Math.min(...xs)) + mem[0].r * 3.4);
    });
  }

  // An unlabelled stone with no pips is a collectible — a berry, a gem, a
  // tile. Draw it small and in its own colour rather than as a big blank
  // disc. (Sandbar stones stay stepping-stone sized: you stand ON those.)
  if (!isPartition) {
    stones.forEach((s) => {
      if (!s.label && !s.dots) { s.small = true; s.r = Math.min(s.r, 22); }
    });
  }

  // Keep every stone on screen and inside the play band. This can MOVE
  // stones (some payloads place a row below their own play_area), so it has
  // to happen before radii are fitted, not after.
  stones.forEach((s) => {
    s.x = clamp(s.x, s.r + 12, W - s.r - 12);
    s.y = clamp(s.y, play.top + s.r * 0.6, play.bottom - s.r * 0.5);
  });

  fitRadii();
}

// Explicit x_pct/y_pct placements can pack stones far tighter than the
// automatic layouts do. Shrink each stone so neighbours never overlap —
// overlapping hit circles make pickup ambiguous as well as ugly.
function fitRadii() {
  if (stones.length < 2) return;
  stones.forEach((s) => {
    let nearest = Infinity;
    stones.forEach((o) => {
      if (o === s) return;
      const d = Math.hypot(o.x - s.x, o.y - s.y);
      if (d < nearest) nearest = d;
    });
    // On a number line the POSITION carries the value, so crowded points
    // must shrink rather than be nudged apart — moving one would change the
    // number it stands for.
    if (isFinite(nearest)) s.r = clamp(Math.min(s.r, nearest * 0.46), 6, s.r);
    s.labelAbove = false;
    s.labelRow = 0;
  });

  // A number line can pack labelled points ~23px apart. No digits fit inside
  // a circle that small, so label them above the point — the way a real
  // number line does — staggering neighbours so the tags don't collide.
  const tight = stones.filter((s) => s.r < 18 && s.label);
  tight.sort((a, b) => a.x - b.x).forEach((s, i) => {
    s.labelAbove = true;
    s.labelRow = i % 2;
  });
}

function layoutProps(list) {
  props = (Array.isArray(list) ? list : []).map((p, i) => ({
    kind: typeof p.kind === 'string' ? p.kind : 'stone',
    x: clamp(num(p.x_pct, ((i + 1) / (list.length + 1))), 0, 1) * W,
    y: clamp(num(p.y_pct, 0.62), 0, 1) * H,
    scale: clamp(num(p.scale, 1), 0.2, 4),
    label: p.label == null ? '' : String(p.label),
    seed: Math.random() * TAU,
  }));
}

/* ═══════════ 9b. ★ THE ANSWER TRAY — clickable storybook cards ★ ═══
   Walking to every option was tedious, and painting the options onto the
   watercolour made them look like ping-pong balls glued to a painting.
   So the answers are DOM now: designed cards in their own tray, under
   the illustration, where they can never cover the art or the hero.

   The canvas still owns the answers wherever the SPACE is the puzzle —
   an ordered path, a perimeter walk, scattered treasure, fraction
   sandbars — and those stay walkable. Everywhere else the canvas is
   purely the picture. */

/* Which stones (if any) still belong on the painting. */
function canvasStoneMode() {
  const c = state.challenge || {};
  if (!stones.length) return 'none';
  if (c.grade_mode === 'groups' && sandbars.length > 1) return 'islands';
  const qt = questionType();
  if (qt === 'ordered_path') return 'path';
  if (qt === 'collect_count') return 'treasure';
  return 'none';
}
function groupsMode() {
  return (state.challenge || {}).grade_mode === 'groups' && sandbars.length > 1;
}
function stoneById(id) {
  for (let i = 0; i < stones.length; i++) if (stones[i].id === id) return stones[i];
  return null;
}

/* ── what a card should SHOW ──────────────────────────────────────
   Never blank, and never a bare lonely "0" that reads as a broken or
   empty button. Zero is a real answer in maths, so it gets drawn as an
   empty dish beside the digit: unmistakably "none", deliberately. */
const ZERO_RE = /^[-+]?0+(?:\.0+)?$/;

function answerFace(s) {
  const raw = s && s.label != null ? String(s.label).trim() : '';
  const dots = num(s && s.dots, 0);
  if (dots > 0) return { kind: 'dots', dots: clamp(Math.round(dots), 1, 12), text: raw };
  if (raw && ZERO_RE.test(raw)) return { kind: 'zero', text: raw };
  if (raw) return { kind: 'text', text: raw };
  if (s && typeof s.shape === 'string' && s.shape) return { kind: 'shape', shape: s.shape };
  return { kind: 'token' };
}

// The short version, for the "you picked…" pills.
function faceText(s) {
  const f = answerFace(s);
  if (f.kind === 'dots') return f.text || '•'.repeat(Math.min(f.dots, 9));
  if (f.kind === 'zero') return '0';
  if (f.kind === 'text') return f.text;
  if (f.kind === 'shape') return f.shape;
  return '✦';
}

const SHAPE_SIDES = { triangle: 3, square: 4, pentagon: 5, hexagon: 6 };

function shapeSvg(shape, edge, fill) {
  const c = 32, r = 26;
  const n = SHAPE_SIDES[shape];
  let body;
  if (shape === 'circle') {
    body = `<circle cx="${c}" cy="${c}" r="${r}" fill="${fill}" stroke="${edge}" stroke-width="4"/>`;
  } else if (shape === 'rectangle') {
    body = `<rect x="4" y="16" width="56" height="32" rx="5" fill="${fill}" stroke="${edge}" stroke-width="4"/>`;
  } else if (n) {
    const rot = shape === 'square' ? -Math.PI / 4 : -Math.PI / 2;
    const pts = [];
    for (let i = 0; i < n; i++) {
      const a = rot + (i / n) * TAU;
      pts.push(`${(c + Math.cos(a) * r).toFixed(1)},${(c + Math.sin(a) * r).toFixed(1)}`);
    }
    body = `<polygon points="${pts.join(' ')}" fill="${fill}" stroke="${edge}" stroke-width="4" stroke-linejoin="round"/>`;
    // corner pips: a four-year-old cannot read "pentagon" but can count 5
    for (let i = 0; i < n; i++) {
      const a = rot + (i / n) * TAU;
      body += `<circle cx="${(c + Math.cos(a) * r).toFixed(1)}" cy="${(c + Math.sin(a) * r).toFixed(1)}" r="4.6" fill="#fffaf1" stroke="${edge}" stroke-width="2"/>`;
    }
  } else {
    body = `<circle cx="${c}" cy="${c}" r="${r}" fill="${fill}" stroke="${edge}" stroke-width="4"/>`;
  }
  return `<svg class="card-shape" viewBox="0 0 64 64" aria-hidden="true">${body}</svg>`;
}

function gemSvg(edge, fill) {
  return `<svg class="card-token" viewBox="0 0 48 48" aria-hidden="true">` +
    `<polygon points="24,4 42,17 35,44 13,44 6,17" fill="${fill}" stroke="${edge}" stroke-width="3.5" stroke-linejoin="round"/>` +
    `<polygon points="24,4 35,44 13,44" fill="rgba(255,255,255,.45)"/>` +
    `<path d="M6 17 L42 17" stroke="rgba(255,255,255,.75)" stroke-width="2.5" fill="none"/>` +
    `</svg>`;
}

function faceHtml(face, s) {
  const edge = TINT_EDGE[s && s.tint] || '#c9a874';
  const fill = TINT_HEX[s && s.tint] || '#ffe9bd';
  if (face.kind === 'dots') {
    const cols = face.dots <= 3 ? face.dots : Math.ceil(Math.sqrt(face.dots));
    let pips = '';
    for (let i = 0; i < face.dots; i++) pips += '<i></i>';
    const numeral = face.text && !ZERO_RE.test(face.text)
      ? `<span class="card-sub">${escapeHtml(face.text)}</span>` : '';
    return `<span class="card-pips" style="--cols:${cols}" aria-hidden="true">${pips}</span>${numeral}`;
  }
  if (face.kind === 'zero') {
    return `<span class="card-zero"><i class="dish" aria-hidden="true"></i>` +
           `<span class="card-num">0</span></span><span class="card-sub">zero</span>`;
  }
  if (face.kind === 'shape') {
    return shapeSvg(face.shape, edge, fill) +
      `<span class="card-sub">${escapeHtml(face.shape)}</span>`;
  }
  if (face.kind === 'token') return gemSvg(edge, fill);
  const long = face.text.length > 4 ? ' long' : '';
  return `<span class="card-num${long}">${escapeHtml(face.text)}</span>`;
}

/* ── building the tray ───────────────────────────────────────────── */

let answerCards = [];          // [{ id, group, el, order }]

/* In fraction mode the unit of choice is a whole sandbar, not a pebble —
   one card per group, showing how many are on it. Everywhere else it is
   one card per stone. */
function trayEntries() {
  if (groupsMode()) {
    const seen = [];
    const out = [];
    stones.forEach((s) => {
      if (seen.indexOf(s.group) >= 0) return;
      seen.push(s.group);
      const members = stones.filter((m) => m.group === s.group);
      out.push({
        stone: s, group: s.group,
        face: { kind: 'dots', dots: clamp(members.length, 1, 12), text: '' },
      });
    });
    return out;
  }
  return stones.map((s) => ({ stone: s, group: undefined, face: answerFace(s) }));
}

function renderAnswerTray() {
  const tray = $('answer-tray');
  if (!tray) return;
  answerCards = [];
  tray.innerHTML = '';
  let entries = [];
  try { entries = trayEntries(); } catch (e) { entries = []; }
  if (!entries.length) { tray.hidden = true; return; }
  tray.hidden = false;
  tray.className = 'answer-tray' + (entries.length > 6 ? ' dense' : '');

  entries.forEach((entry) => {
    const s = entry.stone;
    const card = document.createElement('button');
    card.className = 'answer-card';
    card.type = 'button';
    try {
      card.setAttribute('data-stone-id', String(s.id));
      card.setAttribute('aria-label', faceText(s));
      if (card.style) {
        card.style.setProperty
          ? card.style.setProperty('--card-edge', TINT_EDGE[s.tint] || '#e0c49a')
          : (card.style.borderColor = TINT_EDGE[s.tint] || '#e0c49a');
      }
    } catch (e) { /* attributes are cosmetic */ }
    card.innerHTML = `<span class="card-order" hidden></span>` + faceHtml(entry.face, s);
    card.addEventListener('click', () => onAnswerClick(s.id, entry.group));
    tray.appendChild(card);
    answerCards.push({ id: s.id, group: entry.group, el: card, face: entry.face });
  });
  syncAnswerTray();
}

function groupPicked(g) {
  return stones.some((s) => s.group === g && s.picked);
}

/* Repaint the tray's STATE without rebuilding it, so the pop animations
   and the browser's focus ring both survive a selection. */
function syncAnswerTray() {
  const tray = $('answer-tray');
  if (tray && tray.classList) tray.classList.toggle('locked', !!state.locked);
  const ordered = questionType() === 'ordered_path';
  answerCards.forEach((c) => {
    const s = stoneById(c.id);
    if (!s || !c.el || !c.el.classList) return;
    const picked = c.group !== undefined ? groupPicked(c.group) : !!s.picked;
    c.el.classList.toggle('picked', picked);
    c.el.classList.toggle('good', s.reveal === 'good');
    c.el.classList.toggle('bad', s.reveal === 'bad');
    // once the server has revealed the answer the tray is settled
    c.el.classList.toggle('done', !!s.reveal && !picked && s.reveal !== 'good');

    let n = 0;
    if (ordered) n = s.orderIndex > 0 ? s.orderIndex : state.selected.indexOf(s.id) + 1;
    else if (picked && targetCount() > 1) n = state.selected.indexOf(s.id) + 1;
    const badge = c.el.children && c.el.children[0];
    if (badge) {
      const show = n > 0 && (ordered ? (s.orderIndex > 0 || picked) : picked);
      badge.textContent = show ? String(n) : '';
      badge.hidden = !show;
    }
  });
}

/* Flash a card that was tapped out of turn. */
function shakeCard(id) {
  const c = answerCards.filter((a) => a.id === id)[0];
  if (!c || !c.el || !c.el.classList) return;
  c.el.classList.remove('wrongorder');
  void c.el.offsetWidth;
  c.el.classList.add('wrongorder');
  setTimeout(() => { try { c.el.classList.remove('wrongorder'); } catch (e) { /* ignore */ } }, 500);
}

/* Confetti belongs where the child is looking. When the answers are DOM
   cards there is no stone on the canvas to burst from, so the hero
   celebrates instead. */
function pickBurst(s, color, n) {
  if (canvasStoneMode() !== 'none' && s) burst(s.x, s.y, color, n);
  else {
    burst(hero.x + hero.w / 2, hero.y + hero.h * 0.2, color, n);
    hero.bounce = 1;
  }
}

/* ── the click ───────────────────────────────────────────────────── */
function onAnswerClick(id, group) {
  if (state.locked) return;
  const s = stoneById(id);
  if (!s) return;
  const qt = questionType();

  if (group !== undefined) { toggleGroup(group); return; }
  if (qt === 'ordered_path') { if (!orderedPick(s)) shakeCard(s.id); return; }

  if (qt === 'single_choice') {
    stones.forEach((o) => { o.picked = false; });
    state.selected = [];
    s.picked = true; s.pop = 1;
    selAdd(s.id);
    pickBurst(s, '#5fd0ae');
    sfxPick();
    renderSelected();
    setTimeout(submitAnswer, 340);
    return;
  }

  // multi_select / collect_count / sum — a tap toggles, so a child can
  // always change their mind without walking anywhere.
  if (s.picked) {
    s.picked = false;
    selRemove(s.id);
    blip(360, 0.08, 'sine', 0.04);
  } else {
    s.picked = true; s.pop = 1;
    selAdd(s.id);
    pickBurst(s, qt === 'collect_count' ? '#ffd24a' : '#5fd0ae', qt === 'collect_count' ? 10 : 11);
    sfxPick();
  }
  renderSelected();
}

/* ═══════════════════════ 10. INPUT ═══════════════════════ */

window.addEventListener('keydown', (e) => {
  if (!$('screen-game').classList.contains('active')) return;
  keys[e.key] = true;
  if ([' ', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) e.preventDefault();
  if (e.key === ' ' && !state.locked) submitAnswer();
});
window.addEventListener('keyup', (e) => { keys[e.key] = false; });

$('btn-skip').addEventListener('click', () => {
  if (state.locked) return;
  state.locked = true;
  hushSpeech();
  nextChallenge();
});

/* "Lock it in" is the same element as the old Space hint, now a real
   button — a five-year-old should never have to find the space bar. */
$('space-hint').addEventListener('click', () => {
  if (state.locked) return;
  if (!state.selected.length) { blip(320, 0.1, 'triangle', 0.04); return; }
  submitAnswer();
});

/* ═══════════════════════ 11. PICKUPS ═══════════════════════ */

let prevTouched = new Set();

function selAdd(id) { if (!state.selected.includes(id)) state.selected.push(id); }
function selRemove(id) {
  const i = state.selected.indexOf(id);
  if (i >= 0) state.selected.splice(i, 1);
}

function targetCount() {
  const c = state.challenge || {};
  const t = num(c.target_count, 0);
  return t > 0 ? t : (questionType() === 'single_choice' ? 1 : stones.length);
}

// In grouped (fraction) mode the unit of choice is a whole sandbar, not a
// stone: stepping onto any stone lights the entire sandbar. Stepping off and
// back on toggles it, so a child can freely change their mind.
// Shared by the walker and the tray: one sandbar lights or unlights whole.
function toggleGroup(g) {
  const members = stones.filter((s) => s.group === g);
  if (!members.length) return false;
  const turningOn = !members[0].picked;
  members.forEach((s) => {
    s.picked = turningOn;
    if (turningOn) { selAdd(s.id); s.pop = 1; } else selRemove(s.id);
  });
  if (turningOn) {
    members.forEach((s) => pickBurst(s, '#5fd0ae', 6));
    sfxPick();
  } else {
    blip(360, 0.08, 'sine', 0.04);
  }
  renderSelected();
  return true;
}

function checkGroupPickups() {
  const touched = new Set();
  stones.forEach((s) => {
    const d = Math.hypot(hero.x + hero.w / 2 - s.x, hero.y + hero.h / 2 - s.y);
    if (d < s.r + 18) touched.add(s.group);
  });

  touched.forEach((g) => {
    if (prevTouched.has(g)) return;           // already standing here
    toggleGroup(g);
  });
  prevTouched = touched;
}

// Collect-count: walking over a treasure collects it for good. No take-backs
// needed — the counter shows exactly how many you have.
function checkCollectPickups() {
  let changed = false;
  stones.forEach((s) => {
    if (s.picked) return;
    const d = Math.hypot(hero.x + hero.w / 2 - s.x, hero.y + hero.h / 2 - s.y);
    if (d < s.r + 20) {
      s.picked = true; s.pop = 1;
      selAdd(s.id);
      burst(s.x, s.y, '#ffd24a', 10);
      sfxPick();
      changed = true;
    }
  });
  if (changed) renderSelected();
}

/* Ordered path.

   The answer key (`answer_order`) is deliberately withheld by the server,
   so the ONLY safe client-side check is one we can derive with certainty.
   Guessing "ascending" is not safe: the count-back puzzle ("Step back by
   3") is descending, and the walk-the-perimeter puzzle is cyclic by stone
   id — enforcing ascending there would soft-block the correct answer
   forever. So: enforce only when the direction is explicit, the labels are
   numeric, and the values are unambiguous. Otherwise just record the order
   the child walked and let the server grade it. */
function orderedDirection() {
  const c = state.challenge || {};
  if (c.order_hint === 'desc') return -1;
  if (c.order_hint === 'asc') return 1;
  const p = `${c.prompt || ''} ${c.narrative || ''}`.toLowerCase();
  if (/step back|count back|\bbackwards?\b|largest to smallest|biggest to smallest|highest to lowest|take \d+ away/.test(p)) return -1;
  if (/smallest to largest|lowest to highest|count up|counting up|in order from small/.test(p)) return 1;
  return 0;   // unknown → do not enforce
}

let orderMisses = 0;
let orderUnenforced = false;

function orderedSequence() {
  if (orderUnenforced) return null;
  const dir = orderedDirection();
  if (!dir) return null;
  const vals = stones.map((s) => (typeof s.value === 'number' ? s.value : parseFloat(s.label)));
  if (vals.some((v) => !isFinite(v))) return null;          // unlabelled / cyclic path
  if (new Set(vals).size !== vals.length) return null;      // ties → ambiguous
  return stones
    .map((s, i) => ({ id: s.id, v: vals[i] }))
    .sort((a, b) => (a.v - b.v) * dir)
    .map((o) => o.id);
}

/* One step of the path, whether it arrived by foot or by fingertip.
   Returns false when the step was out of turn (the caller decides whether
   to bounce the hero or shake the card). */
function orderedPick(s) {
  if (!s || s.picked) return true;
  const seq = orderedSequence();
  const expected = seq ? seq[state.selected.length] : undefined;
  if (expected !== undefined && s.id !== expected) {
    // soft rejection — no penalty, retry instantly
    s.shake = 1;
    sfxNope();
    orderMisses++;
    // Safety valve: if our inferred direction were ever wrong the child
    // would be stuck forever. After three misses we stop enforcing and
    // let the server be the judge.
    if (orderMisses >= 3) {
      orderUnenforced = true;
      showNarration('Take them in any order you like — I’ll check at the end!', 'Hint', 2600);
    }
    return false;
  }
  s.picked = true; s.pop = 1;
  selAdd(s.id);
  pickBurst(s, '#a98cff', 9);
  blip(540 + state.selected.length * 70, 0.12, 'sine', 0.05);
  ropeGlow = 1;
  renderSelected();
  if (state.selected.length >= Math.min(stones.length, targetCount())) {
    setTimeout(submitAnswer, 320);
  }
  return true;
}

function checkOrderedPickups() {
  stones.forEach((s) => {
    const d = Math.hypot(hero.x + hero.w / 2 - s.x, hero.y + hero.h / 2 - s.y);
    const near = d < s.r + 16;
    if (!near) { s.touchLock = false; return; }
    if (s.touchLock || s.picked) return;
    s.touchLock = true;
    if (!orderedPick(s)) bounceHeroAway(s);
  });
}

function bounceHeroAway(s) {
  let dx = hero.x + hero.w / 2 - s.x;
  let dy = hero.y + hero.h / 2 - s.y;
  let m = Math.hypot(dx, dy);
  if (m < 0.01) {
    // Standing dead-centre on the stone: there is no "away" to push toward,
    // so back the hero off the way they came in. Without this the child gets
    // chimed at forever and never moves.
    dx = -(hero.face || 1);
    dy = 0.4;
    m = Math.hypot(dx, dy);
  }
  hero.x = clamp(hero.x + (dx / m) * (s.r + hero.w * 0.75), 0, W - hero.w);
  hero.y = clamp(hero.y + (dy / m) * (s.r * 0.7), play.top - 30, play.bottom - hero.h);
  hero.bounce = 1;
}

/* Walking only picks things up where the stones are actually ON the
   painting. When the answers live in the tray the hero is free to roam
   the scene without accidentally answering the question. */
function checkPickups() {
  if (state.locked || !state.challenge || !stones.length) return;
  const mode = canvasStoneMode();
  if (mode === 'none') return;

  if (mode === 'islands') { checkGroupPickups(); return; }
  if (mode === 'path') { checkOrderedPickups(); return; }
  if (mode === 'treasure') { checkCollectPickups(); return; }
}

function renderSelected() {
  const row = $('selected-row');
  row.innerHTML = '';
  const ordered = questionType() === 'ordered_path';
  state.selected.forEach((id, i) => {
    const s = stones.find((st) => st.id === id);
    if (!s) return;
    const pill = document.createElement('span');
    pill.className = 'sel-pill';
    pill.innerHTML = (ordered ? `<i class="ord">${i + 1}</i>` : '') +
      `<span>${escapeHtml(faceText(s))}</span>`;
    row.appendChild(pill);
  });
  updateProgressHud();
  try { syncAnswerTray(); } catch (e) { /* the tray is never worth a crash */ }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// Live "3 of 6 ⭐" counter + the pulsing SPACE hint.
function updateProgressHud() {
  const prog = $('problem-progress');
  const hint = $('space-hint');
  const target = targetCount();
  const have = state.selected.length;
  const qt = questionType();

  if (target > 1) {
    prog.hidden = false;
    prog.textContent = `${have} of ${target} ⭐`;
    prog.classList.toggle('full', have === target);
  } else {
    prog.hidden = true;
    prog.classList.remove('full');
  }

  const needsSpace = qt !== 'single_choice';
  const ready = have === target && target > 0;
  hint.hidden = !needsSpace;
  hint.classList.toggle('pulse', needsSpace && ready);
}

/* ═══════════════════════ 12. ANSWER ═══════════════════════ */

async function submitAnswer() {
  if (state.locked || state.selected.length === 0) return;
  state.locked = true;
  hushSpeech();

  try {
    const res = await fetch(`${API}/api/answer`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: state.sessionId,
        stone_ids: state.selected.slice(),   // ORDER PRESERVED for order mode
      }),
    });
    const data = await res.json();
    updateStats(data.stats);

    // The answer moves the quest along — walk the trail forward right away
    // rather than waiting for the next beat to arrive.
    if (data.quest) state.quest = data.quest;
    else if (data.quest_state && state.quest) state.quest = { ...state.quest, state: data.quest_state };
    captureJourney(data);
    try { updateJourney(); } catch (e) { /* ignore */ }
    applyFrameFromResponse(data);

    const correctSet = new Set(data.correct_stone_ids || []);
    stones.forEach((s) => { s.reveal = correctSet.has(s.id) ? 'good' : 'bad'; });

    // For an ordered path the server reveals the real sequence on the way
    // out — show the right numbers on the stones so the child SEES the path.
    if (Array.isArray(data.answer_order)) {
      data.answer_order.forEach((id, i) => {
        const s = stones.find((st) => st.id === id);
        if (s) s.orderIndex = i + 1;
      });
    }

    // Repaint the tray so the cards themselves show right / wrong.
    try { syncAnswerTray(); } catch (e) { /* ignore */ }

    if (data.correct) {
      stones.filter((s) => s.reveal === 'good').forEach((s) => pickBurst(s, '#ffd24a', 18));
      sfxWin();
    } else {
      sfxNope();
    }

    let extra = data.correct ? '' : (data.explanation || '');
    // Make the adaptation visible — "no adaptivity" was a real complaint,
    // and the engine explains itself now.
    const adapt = data.adaptation;
    if (adapt && adapt.message) extra += (extra ? ' ' : '') + adapt.message;
    else if (data.level_changed > 0) extra += ' 🎉 Level up!';
    else if (data.level_changed < 0) extra += ' 💛 Let’s slow down a little.';

    showToast(data.feedback || (data.correct ? 'Yes!' : 'Not quite!'), extra, data.correct ? 'good' : 'bad');
    speak(`${data.feedback || ''}. ${extra}`);

    // `beat.on_success` is empty at issue time — the story engine fills the
    // real resolution in on the answer response as `beat_result`.
    const beat = state.challenge && state.challenge.beat;
    const outro = data.beat_result || (beat && beat.on_success) || '';
    if (data.correct && outro) {
      setTimeout(() => showNarration(outro, data.is_final_beat ? 'At last' : 'And then…', 3400), 900);
      setTimeout(nextChallenge, 4600);
    } else {
      setTimeout(nextChallenge, data.correct ? 2200 : 3600);
    }
  } catch (e) {
    showToast('Hmm, we lost the connection!', 'Try again in a moment.', 'bad');
    state.locked = false;
  }
}

function showToast(msg, sub, kind) {
  const t = $('toast');
  t.className = `toast ${kind}`;
  t.innerHTML = escapeHtml(msg) + (sub ? `<small>${escapeHtml(sub)}</small>` : '');
  t.hidden = false;
  requestAnimationFrame(() => t.classList.add('show'));
  setTimeout(() => t.classList.remove('show'), kind === 'good' ? 1900 : 3200);
}

/* ═══════════════════════ 13. PARTICLES ═══════════════════════ */

function burst(x, y, color, n = 11) {
  for (let i = 0; i < n; i++) {
    particles.push({
      x, y, color,
      vx: (Math.random() - 0.5) * 6,
      vy: -Math.random() * 5 - 1.5,
      r: 2.5 + Math.random() * 3,
      life: 1,
    });
  }
}

function seedAmbient() {
  const n = timeOfDay === 'night' ? 80 : 46;
  ambient = [];
  for (let i = 0; i < n; i++) {
    ambient.push({
      x: Math.random() * W,
      y: Math.random() * H,
      r: 1 + Math.random() * 2.4,
      sp: 0.12 + Math.random() * 0.5,
      ph: Math.random() * TAU,
      drift: 0.2 + Math.random() * 0.8,
    });
  }
}

function drawAmbient() {
  const t = tick / 60;
  gctx.save();
  ambient.forEach((p) => {
    if (timeOfDay === 'night') {
      // drifting stars, mostly in the upper sky
      p.x += p.drift * 0.16;
      if (p.x > W + 4) p.x = -4;
      const tw = 0.45 + 0.55 * Math.sin(t * 2.2 + p.ph);
      gctx.globalAlpha = (p.y < H * 0.55 ? 0.95 : 0.35) * tw;
      gctx.fillStyle = '#fffdf2';
      gctx.beginPath(); gctx.arc(p.x, p.y, p.r, 0, TAU); gctx.fill();
    } else if (timeOfDay === 'dusk') {
      // fireflies: slow wander + blink
      p.x += Math.sin(t * p.sp + p.ph) * 0.7;
      p.y += Math.cos(t * p.sp * 0.7 + p.ph) * 0.45;
      const blink = Math.max(0, Math.sin(t * 1.7 + p.ph));
      gctx.globalAlpha = 0.25 + blink * 0.7;
      gctx.fillStyle = '#ffe98a';
      gctx.shadowColor = '#ffd24a'; gctx.shadowBlur = 12;
      gctx.beginPath(); gctx.arc(p.x, p.y, p.r * 1.3, 0, TAU); gctx.fill();
      gctx.shadowBlur = 0;
    } else {
      // pollen: lazy upward float
      p.y -= p.sp * 0.45;
      p.x += Math.sin(t * 0.7 + p.ph) * 0.45;
      if (p.y < -6) { p.y = H + 6; p.x = Math.random() * W; }
      gctx.globalAlpha = 0.16 + 0.22 * (0.5 + 0.5 * Math.sin(t + p.ph));
      gctx.fillStyle = '#fffbe4';
      gctx.beginPath(); gctx.arc(p.x, p.y, p.r, 0, TAU); gctx.fill();
    }
  });
  gctx.restore();
  gctx.globalAlpha = 1;
}

function drawParticles() {
  particles = particles.filter((p) => p.life > 0);
  particles.forEach((p) => {
    p.x += p.vx; p.y += p.vy; p.vy += 0.26; p.life -= 0.022;
    gctx.globalAlpha = Math.max(0, p.life);
    gctx.fillStyle = p.color;
    gctx.beginPath(); gctx.arc(p.x, p.y, p.r, 0, TAU); gctx.fill();
  });
  gctx.globalAlpha = 1;
}

/* ═══════════════════════ 14. CANVAS HELPERS ═══════════════════════ */

function rrect(x, y, w, h, r) {
  const rr = Math.min(r, Math.abs(w) / 2, Math.abs(h) / 2);
  gctx.beginPath();
  gctx.moveTo(x + rr, y);
  gctx.arcTo(x + w, y, x + w, y + h, rr);
  gctx.arcTo(x + w, y + h, x, y + h, rr);
  gctx.arcTo(x, y + h, x, y, rr);
  gctx.arcTo(x, y, x + w, y, rr);
  gctx.closePath();
}

function polyPath(cx, cy, r, n, rot = -Math.PI / 2) {
  gctx.beginPath();
  for (let i = 0; i < n; i++) {
    const a = rot + (i / n) * TAU;
    const x = cx + Math.cos(a) * r, y = cy + Math.sin(a) * r;
    if (i === 0) gctx.moveTo(x, y); else gctx.lineTo(x, y);
  }
  gctx.closePath();
}

function fillStroke(fill, stroke, lw) {
  if (fill) { gctx.fillStyle = fill; gctx.fill(); }
  if (stroke) { gctx.strokeStyle = stroke; gctx.lineWidth = lw || 3; gctx.stroke(); }
}

function circle(x, y, r, fill, stroke, lw) {
  gctx.beginPath(); gctx.arc(x, y, r, 0, TAU);
  fillStroke(fill, stroke, lw);
}

function ellipse(x, y, rx, ry, fill, stroke, lw, rot = 0) {
  gctx.beginPath(); gctx.ellipse(x, y, Math.abs(rx), Math.abs(ry), rot, 0, TAU);
  fillStroke(fill, stroke, lw);
}

/* ── painterly helpers ───────────────────────────────────────────────
   Flat fills are what made the props read as UI rectangles dropped onto
   a watercolour. Everything hand-drawn now gets a soft vertical light
   ramp, a warm contact shadow and, where it belongs, a glow. */
function vGrad(y0, y1, top, bottom) {
  try {
    const g = gctx.createLinearGradient(0, y0, 0, y1);
    g.addColorStop(0, top);
    g.addColorStop(1, bottom);
    return g;
  } catch (e) { return bottom; }
}
function dGrad(x0, y0, x1, y1, a, b) {
  try {
    const g = gctx.createLinearGradient(x0, y0, x1, y1);
    g.addColorStop(0, a);
    g.addColorStop(1, b);
    return g;
  } catch (e) { return b; }
}
function glowAt(x, y, r, inner, outer) {
  if (!(r > 0)) return;
  try {
    const g = gctx.createRadialGradient(x, y, Math.max(0.5, r * 0.06), x, y, r);
    g.addColorStop(0, inner);
    g.addColorStop(1, outer);
    gctx.fillStyle = g;
  } catch (e) { gctx.fillStyle = outer; }
  gctx.beginPath(); gctx.arc(x, y, r, 0, TAU); gctx.fill();
}
// the little dark oval that glues an object to the ground
function groundShadow(x, y, rx, ry, a) {
  gctx.save();
  gctx.globalAlpha = a === undefined ? 0.16 : a;
  ellipse(x, y, rx, ry, '#4a3b52', null);
  gctx.restore();
  gctx.globalAlpha = 1;
}
// a soft sheen, for glass / glaze / wet things
function sheen(x, y, rx, ry, rot, a) {
  gctx.save();
  gctx.globalAlpha = a === undefined ? 0.5 : a;
  ellipse(x, y, rx, ry, '#fffdf4', null, 0, rot || 0);
  gctx.restore();
  gctx.globalAlpha = 1;
}

/* ═══════════════════════ 15. THE WORLD ═══════════════════════ */

// One painted frame, cropped through the eased camera rect.
// Returns false if the image is unusable, so the caller can fall back.
function drawFrameLayer(img) {
  if (!imgReady(img)) return false;
  const nw = img.naturalWidth, nh = img.naturalHeight;
  const sx = clamp(cam.x, 0, 0.98) * nw;
  const sy = clamp(cam.y, 0, 0.98) * nh;
  const sw = clamp(cam.w, 0.02, 1 - cam.x) * nw;
  const sh = clamp(cam.h, 0.02, 1 - cam.y) * nh;
  try {
    gctx.drawImage(img, sx, sy, sw, sh, 0, 0, W, H);
  } catch (e) {
    return false;
  }
  return true;
}

/* The crossfade. The OUTGOING frame (or the procedural world, when this is
   the very first painting to arrive) is drawn at full strength underneath
   and the new one fades up over it — so there is never a blank frame, a
   flash, or a visible cut. */
function drawBackdrop() {
  const fade = clamp(num(bgFade, 1), 0, 1);
  let painted = false;

  if (!imgReady(bgImage)) {
    // Nothing new decoded yet — keep whatever the child was already looking at.
    painted = drawFrameLayer(bgOutgoing);
    if (!painted) drawProceduralWorld();
  } else if (fade >= 1) {
    painted = drawFrameLayer(bgImage);
    if (!painted) drawProceduralWorld();
  } else {
    if (!drawFrameLayer(bgOutgoing)) drawProceduralWorld();
    gctx.save();
    gctx.globalAlpha = fade;
    painted = drawFrameLayer(bgImage);
    gctx.restore();
    gctx.globalAlpha = 1;
  }

  // A gentle wash so the hand-drawn props read on top of a painted photo.
  if (painted) {
    gctx.fillStyle = 'rgba(255,255,255,.10)';
    gctx.fillRect(0, 0, W, H);
  }
}

// A layered, parallax storybook scene built entirely from vectors.
function drawProceduralWorld() {
  const t = tick / 60;
  const px = cam.x * 1.0;             // 0..1 camera pan
  const zoom = 1 / clamp(cam.w, 0.2, 1);
  const ramp = SKY_RAMPS[timeOfDay] || SKY_RAMPS.day;

  // ── sky ──
  const sky = gctx.createLinearGradient(0, 0, 0, H);
  sky.addColorStop(0, ramp[0]);
  sky.addColorStop(0.58, ramp[1]);
  sky.addColorStop(1, ramp[2]);
  gctx.fillStyle = sky;
  gctx.fillRect(0, 0, W, H);

  // ── sun / moon ──
  const cx = W - 130 - px * 90;
  const cy = 96 + cam.y * 40;
  if (timeOfDay === 'night') {
    circle(cx, cy, 40, '#fdf6d8', null);
    gctx.globalCompositeOperation = 'destination-out';
    circle(cx + 15, cy - 11, 34, '#000', null);
    gctx.globalCompositeOperation = 'source-over';
  } else {
    gctx.globalAlpha = 0.35;
    circle(cx, cy, 68, timeOfDay === 'dusk' ? '#ffd0a0' : '#fff6cd', null);
    gctx.globalAlpha = 1;
    circle(cx, cy, 44, timeOfDay === 'dusk' ? '#ffcf8f' : '#fff2bf', null);
  }

  // ── drifting clouds, two parallax layers ──
  drawClouds(0.55, 0.30, t * 7, px * 40, timeOfDay === 'night' ? 'rgba(255,255,255,.18)' : 'rgba(255,255,255,.55)');
  drawClouds(0.85, 0.17, t * 13, px * 80, timeOfDay === 'night' ? 'rgba(255,255,255,.26)' : 'rgba(255,255,255,.85)');

  // ── far hills ──
  const horizon = play.top - 58;
  hillBand(horizon + 8, 54, 190, shade('#b9d9ea', '#c69bb5', '#41508f'), px * 55, zoom);
  hillBand(horizon + 34, 42, 140, shade('#a8dcc8', '#e0a98f', '#3c4a7e'), px * 95, zoom);

  // ── mid scenery: trees along the hill line ──
  const treeY = horizon + 52;
  for (let i = 0; i < 7; i++) {
    const tx = ((i * 167 + 60 - px * 150) % (W + 220)) - 110;
    miniTree(tx, treeY + (i % 3) * 9, 0.62 + (i % 3) * 0.12);
  }

  // ── ground ──
  const groundTop = play.top + 6;
  const g = gctx.createLinearGradient(0, groundTop, 0, H);
  g.addColorStop(0, shade('#a9e2c2', '#e8bd9c', '#4d5f96'));
  g.addColorStop(1, shade('#8ed0ab', '#d8a184', '#3e4d7d'));
  gctx.fillStyle = g;
  gctx.beginPath();
  gctx.moveTo(0, H);
  for (let x = 0; x <= W; x += 16) gctx.lineTo(x, groundTop + Math.sin(x / 190 + 1.2) * 12);
  gctx.lineTo(W, H);
  gctx.closePath();
  gctx.fill();

  // ── the river: animated water with a travelling highlight ──
  drawWater(play.bottom + 6, Math.max(26, H - play.bottom - 2), t);

  // ── grass tufts ──
  gctx.strokeStyle = shade('rgba(92,168,128,.75)', 'rgba(150,105,80,.7)', 'rgba(60,80,130,.7)');
  gctx.lineWidth = 2.4;
  gctx.lineCap = 'round';
  for (let i = 0; i < 46; i++) {
    const gx = ((i * 137 + 20 - px * 130) % (W + 60)) - 30;
    const gy = groundTop + 14 + ((i * 53) % 40);
    const sway = Math.sin(t * 1.4 + i) * 3;
    gctx.beginPath();
    gctx.moveTo(gx, gy);
    gctx.quadraticCurveTo(gx + sway, gy - 9, gx + sway * 1.8, gy - 15);
    gctx.stroke();
  }
}

// pick a colour for day / dusk / night
function shade(day, dusk, night) {
  return timeOfDay === 'night' ? night : timeOfDay === 'dusk' ? dusk : day;
}

function drawClouds(yFrac, scale, offset, pan, color) {
  gctx.fillStyle = color;
  for (let i = 0; i < 4; i++) {
    const base = i * 320;
    const x = ((base + offset - pan) % (W + 400)) - 200;
    const y = H * yFrac * 0.30 + (i % 2) * 34 + 26;
    const s = scale * (0.8 + (i % 3) * 0.2);
    gctx.beginPath();
    gctx.arc(x, y, 54 * s, 0, TAU);
    gctx.arc(x + 46 * s, y - 14 * s, 42 * s, 0, TAU);
    gctx.arc(x + 88 * s, y + 4 * s, 34 * s, 0, TAU);
    gctx.arc(x + 40 * s, y + 18 * s, 38 * s, 0, TAU);
    gctx.fill();
  }
}

function hillBand(baseY, amp, wavelen, color, pan, zoom) {
  gctx.fillStyle = color;
  gctx.beginPath();
  gctx.moveTo(0, H);
  for (let x = 0; x <= W; x += 14) {
    gctx.lineTo(x, baseY + Math.sin((x + pan) / wavelen) * amp * zoom * 0.6);
  }
  gctx.lineTo(W, H);
  gctx.closePath();
  gctx.fill();
}

function miniTree(x, y, s) {
  gctx.fillStyle = shade('#b98c6c', '#9c6f52', '#4a4468');
  gctx.fillRect(x - 4 * s, y, 8 * s, 34 * s);
  const leaf = shade('#8fd4a4', '#d79b7e', '#415680');
  circle(x, y - 6 * s, 24 * s, leaf, null);
  circle(x - 17 * s, y + 8 * s, 17 * s, leaf, null);
  circle(x + 17 * s, y + 8 * s, 17 * s, leaf, null);
}

function drawWater(y, h, t) {
  if (h <= 4) return;
  const wg = gctx.createLinearGradient(0, y, 0, y + h);
  wg.addColorStop(0, shade('#8fd7f2', '#e2a9c6', '#33427f'));
  wg.addColorStop(1, shade('#5fbfe2', '#c98aae', '#26315f'));
  gctx.fillStyle = wg;
  gctx.fillRect(0, y, W, h);

  // travelling specular highlight
  gctx.save();
  gctx.beginPath(); gctx.rect(0, y, W, h); gctx.clip();
  gctx.strokeStyle = 'rgba(255,255,255,.5)';
  gctx.lineWidth = 3;
  for (let row = 0; row < 3; row++) {
    const ry = y + 8 + row * (h / 3.2);
    gctx.beginPath();
    for (let x = 0; x <= W; x += 10) {
      const yy = ry + Math.sin(x / 42 + t * (1.4 + row * 0.3) + row) * 3.2;
      if (x === 0) gctx.moveTo(x, yy); else gctx.lineTo(x, yy);
    }
    gctx.globalAlpha = 0.20 + 0.16 * Math.sin(t * 1.1 + row);
    gctx.stroke();
  }
  // a bright glint sliding across
  const glintX = ((t * 70) % (W + 300)) - 150;
  const grad = gctx.createLinearGradient(glintX - 90, 0, glintX + 90, 0);
  grad.addColorStop(0, 'rgba(255,255,255,0)');
  grad.addColorStop(0.5, 'rgba(255,255,255,.28)');
  grad.addColorStop(1, 'rgba(255,255,255,0)');
  gctx.globalAlpha = 1;
  gctx.fillStyle = grad;
  gctx.fillRect(glintX - 90, y, 180, h);
  gctx.restore();
  gctx.globalAlpha = 1;
}

function drawVignette() {
  const v = gctx.createRadialGradient(W / 2, H / 2, H * 0.38, W / 2, H / 2, H * 0.86);
  v.addColorStop(0, 'rgba(0,0,0,0)');
  v.addColorStop(1, timeOfDay === 'night' ? 'rgba(12,16,42,.42)' : 'rgba(92,70,100,.20)');
  gctx.fillStyle = v;
  gctx.fillRect(0, 0, W, H);
}

/* ═══════════════════════ 16. PROPS ═══════════════════════ */

const PROP_DRAW = {
  stone(x, y, s) {
    groundShadow(x, y + 14 * s, 27 * s, 8 * s, 0.18);
    gctx.beginPath();
    gctx.moveTo(x - 26 * s, y + 12 * s);
    gctx.quadraticCurveTo(x - 31 * s, y - 13 * s, x - 5 * s, y - 19 * s);
    gctx.quadraticCurveTo(x + 27 * s, y - 21 * s, x + 26 * s, y + 12 * s);
    gctx.closePath();
    fillStroke(vGrad(y - 20 * s, y + 13 * s, '#efeaf5', '#c3bad2'), '#a79bba', 2.6 * s);
    // a mossy sunlit cap rather than a flat grey disc
    gctx.save();
    gctx.globalAlpha = 0.55;
    gctx.beginPath();
    gctx.moveTo(x - 17 * s, y - 10 * s);
    gctx.quadraticCurveTo(x - 2 * s, y - 21 * s, x + 15 * s, y - 12 * s);
    gctx.quadraticCurveTo(x - 1 * s, y - 15 * s, x - 17 * s, y - 10 * s);
    gctx.closePath();
    fillStroke('#fffdf6', null);
    gctx.restore();
    gctx.globalAlpha = 1;
  },
  plank(x, y, s) {
    groundShadow(x, y + 14 * s, 44 * s, 6 * s, 0.14);
    rrect(x - 44 * s, y - 11 * s, 88 * s, 22 * s, 8 * s);
    fillStroke(vGrad(y - 11 * s, y + 11 * s, '#f0d2a8', '#c69465'), '#a3754a', 2.6 * s);
    // grain, drawn with the wood rather than across it
    gctx.save();
    gctx.strokeStyle = 'rgba(140,100,62,.35)'; gctx.lineWidth = 1.5 * s; gctx.lineCap = 'round';
    [-4, 2].forEach((o, i) => {
      gctx.beginPath();
      gctx.moveTo(x - 36 * s, y + o * s);
      gctx.quadraticCurveTo(x, y + (o + (i ? -3 : 3)) * s, x + 36 * s, y + o * s);
      gctx.stroke();
    });
    gctx.restore();
    sheen(x - 4 * s, y - 6 * s, 30 * s, 2.6 * s, 0, 0.35);
  },
  rope(x, y, s) {
    gctx.strokeStyle = '#d8b07a';
    gctx.lineWidth = 6 * s; gctx.lineCap = 'round';
    gctx.beginPath();
    gctx.moveTo(x - 46 * s, y - 14 * s);
    gctx.quadraticCurveTo(x, y + 22 * s, x + 46 * s, y - 14 * s);
    gctx.stroke();
    gctx.strokeStyle = '#b98c5e'; gctx.lineWidth = 2 * s;
    for (let i = -3; i <= 3; i++) {
      const tt = (i + 3) / 6;
      const bx = lerp(x - 46 * s, x + 46 * s, tt);
      const by = lerp(y - 14 * s, y - 14 * s, tt) + Math.sin(tt * Math.PI) * 18 * s;
      gctx.beginPath(); gctx.moveTo(bx, by - 4 * s); gctx.lineTo(bx, by + 4 * s); gctx.stroke();
    }
  },
  /* A paper lantern, not a yellow rounded rectangle with a dot in it:
     a warm halo that breathes, a curved paper belly, a real flame. */
  lantern(x, y, s) {
    const flick = 0.84 + 0.16 * Math.sin(tick / 9 + x * 0.05);
    // halo, two layers so the falloff is soft rather than a hard disc
    glowAt(x, y - 2 * s, 66 * s, `rgba(255,206,122,${0.30 * flick})`, 'rgba(255,206,122,0)');
    glowAt(x, y - 2 * s, 30 * s, `rgba(255,238,190,${0.48 * flick})`, 'rgba(255,238,190,0)');

    // hook + hanging ring
    gctx.strokeStyle = '#8a6c46'; gctx.lineWidth = 2.4 * s; gctx.lineCap = 'round';
    gctx.beginPath(); gctx.moveTo(x, y - 42 * s); gctx.lineTo(x, y - 31 * s); gctx.stroke();
    gctx.beginPath(); gctx.arc(x, y - 27 * s, 8 * s, Math.PI * 1.08, Math.PI * 1.92); gctx.stroke();

    // cap
    gctx.beginPath();
    gctx.moveTo(x - 15 * s, y - 21 * s);
    gctx.quadraticCurveTo(x, y - 33 * s, x + 15 * s, y - 21 * s);
    gctx.closePath();
    fillStroke(vGrad(y - 33 * s, y - 21 * s, '#d19a5c', '#a8703c'), '#8a5a32', 2 * s);

    // the paper belly — bowed, never boxy
    gctx.beginPath();
    gctx.moveTo(x - 13 * s, y - 20 * s);
    gctx.bezierCurveTo(x - 21 * s, y - 6 * s, x - 19 * s, y + 7 * s, x - 10 * s, y + 12 * s);
    gctx.lineTo(x + 10 * s, y + 12 * s);
    gctx.bezierCurveTo(x + 19 * s, y + 7 * s, x + 21 * s, y - 6 * s, x + 13 * s, y - 20 * s);
    gctx.closePath();
    fillStroke(dGrad(x - 16 * s, y - 20 * s, x + 16 * s, y + 12 * s, '#fff7d8', '#ffce7d'),
               '#c98a4c', 2.2 * s);

    // ribs
    gctx.save();
    gctx.strokeStyle = 'rgba(201,138,76,.38)'; gctx.lineWidth = 1.3 * s;
    [-7, 7].forEach((o) => {
      gctx.beginPath();
      gctx.moveTo(x + o * s, y - 19 * s);
      gctx.quadraticCurveTo(x + o * 1.5 * s, y - 4 * s, x + o * 1.1 * s, y + 11 * s);
      gctx.stroke();
    });
    gctx.restore();

    // the flame inside
    glowAt(x, y - 2 * s, 12 * s, `rgba(255,186,84,${0.85 * flick})`, 'rgba(255,186,84,0)');
    gctx.beginPath();
    gctx.moveTo(x, y - 11 * s);
    gctx.quadraticCurveTo(x + 5 * s, y - 2 * s, x, y + 4 * s);
    gctx.quadraticCurveTo(x - 5 * s, y - 2 * s, x, y - 11 * s);
    gctx.closePath();
    fillStroke(vGrad(y - 11 * s, y + 4 * s, '#fff6d0', '#ffab3d'), null);

    // base
    rrect(x - 12 * s, y + 10 * s, 24 * s, 7 * s, 3.4 * s);
    fillStroke(vGrad(y + 10 * s, y + 17 * s, '#c9884a', '#8a5a32'), '#7a4f2c', 1.8 * s);
    sheen(x - 6 * s, y - 7 * s, 3 * s, 7 * s, -0.22, 0.55);
  },

  basket(x, y, s) {
    groundShadow(x, y + 18 * s, 29 * s, 7 * s, 0.18);
    // handle behind the rim
    gctx.strokeStyle = '#a3754a'; gctx.lineWidth = 3.4 * s; gctx.lineCap = 'round';
    gctx.beginPath(); gctx.arc(x, y - 11 * s, 20 * s, Math.PI * 1.04, Math.PI * 1.96); gctx.stroke();
    // the body, bellied out like real wicker
    gctx.beginPath();
    gctx.moveTo(x - 28 * s, y - 11 * s);
    gctx.bezierCurveTo(x - 31 * s, y + 4 * s, x - 26 * s, y + 13 * s, x - 19 * s, y + 16 * s);
    gctx.lineTo(x + 19 * s, y + 16 * s);
    gctx.bezierCurveTo(x + 26 * s, y + 13 * s, x + 31 * s, y + 4 * s, x + 28 * s, y - 11 * s);
    gctx.closePath();
    fillStroke(vGrad(y - 12 * s, y + 16 * s, '#f0cd9a', '#c08f58'), '#a3754a', 2.6 * s);
    // weave
    gctx.save();
    gctx.strokeStyle = 'rgba(140,100,62,.34)'; gctx.lineWidth = 1.5 * s;
    [-3, 5, 12].forEach((o, i) => {
      const halfW = (27 - i * 2.6) * s;
      gctx.beginPath();
      gctx.moveTo(x - halfW, y + o * s);
      gctx.quadraticCurveTo(x, y + (o + 2.6) * s, x + halfW, y + o * s);
      gctx.stroke();
    });
    gctx.restore();
    // rim
    ellipse(x, y - 11 * s, 28 * s, 6 * s, vGrad(y - 17 * s, y - 5 * s, '#f6dcb2', '#cf9e66'), '#a3754a', 2.4 * s);
    sheen(x - 10 * s, y + 1 * s, 6 * s, 9 * s, -0.25, 0.3);
  },

  berry(x, y, s) {
    groundShadow(x, y + 11 * s, 9 * s, 3 * s, 0.12);
    circle(x, y, 11 * s, dGrad(x - 11 * s, y - 11 * s, x + 9 * s, y + 11 * s, '#ff9fb6', '#e0577c'),
           '#c33f64', 2.2 * s);
    sheen(x - 3.6 * s, y - 4 * s, 3.4 * s, 2.4 * s, -0.5, 0.78);
    // a leaf with a spine
    gctx.beginPath();
    gctx.moveTo(x + 2 * s, y - 10 * s);
    gctx.quadraticCurveTo(x + 16 * s, y - 21 * s, x + 4 * s, y - 15 * s);
    gctx.closePath();
    fillStroke(dGrad(x, y - 21 * s, x + 16 * s, y - 10 * s, '#a6e08c', '#5fae4d'), '#4f9440', 1.1 * s);
  },

  gem(x, y, s) {
    const tw = 0.7 + 0.3 * Math.sin(tick / 22 + x * 0.04);
    glowAt(x, y, 26 * s, `rgba(150,226,255,${0.26 * tw})`, 'rgba(150,226,255,0)');
    gctx.save(); gctx.translate(x, y);
    gctx.beginPath();
    gctx.moveTo(0, -15 * s); gctx.lineTo(13 * s, -4 * s); gctx.lineTo(8 * s, 14 * s);
    gctx.lineTo(-8 * s, 14 * s); gctx.lineTo(-13 * s, -4 * s);
    gctx.closePath();
    fillStroke(dGrad(-13 * s, -15 * s, 13 * s, 14 * s, '#d6f5ff', '#5ec4ea'), '#3b9ccc', 2.2 * s);
    // the lit facet
    gctx.beginPath();
    gctx.moveTo(0, -15 * s); gctx.lineTo(13 * s, -4 * s); gctx.lineTo(0, 14 * s);
    gctx.closePath();
    gctx.globalAlpha = 0.45; fillStroke('#ffffff', null); gctx.globalAlpha = 1;
    gctx.strokeStyle = 'rgba(255,255,255,.85)'; gctx.lineWidth = 1.4 * s;
    gctx.beginPath(); gctx.moveTo(0, -15 * s); gctx.lineTo(0, 14 * s);
    gctx.moveTo(-13 * s, -4 * s); gctx.lineTo(13 * s, -4 * s); gctx.stroke();
    gctx.restore();
    // a single travelling sparkle
    gctx.globalAlpha = tw;
    circle(x - 4 * s, y - 8 * s, 1.9 * s, '#ffffff', null);
    gctx.globalAlpha = 1;
  },

  sack(x, y, s, label) {
    groundShadow(x, y + 22 * s, 25 * s, 7 * s, 0.18);
    gctx.beginPath();
    gctx.moveTo(x - 8 * s, y - 18 * s);
    gctx.bezierCurveTo(x - 32 * s, y + 1 * s, x - 26 * s, y + 16 * s, x - 17 * s, y + 20 * s);
    gctx.lineTo(x + 17 * s, y + 20 * s);
    gctx.bezierCurveTo(x + 26 * s, y + 16 * s, x + 32 * s, y + 1 * s, x + 8 * s, y - 18 * s);
    gctx.closePath();
    fillStroke(vGrad(y - 18 * s, y + 20 * s, '#f3e2c4', '#cbb28c'), '#a89070', 2.6 * s);
    // the cinch, with cloth folds fanning out below it
    gctx.save();
    gctx.strokeStyle = 'rgba(168,144,112,.55)'; gctx.lineWidth = 1.5 * s;
    [-10, 0, 10].forEach((o) => {
      gctx.beginPath();
      gctx.moveTo(x + o * 0.45 * s, y - 10 * s);
      gctx.quadraticCurveTo(x + o * 0.9 * s, y + 4 * s, x + o * s, y + 17 * s);
      gctx.stroke();
    });
    gctx.restore();
    gctx.strokeStyle = '#a8703c'; gctx.lineWidth = 3.4 * s; gctx.lineCap = 'round';
    gctx.beginPath(); gctx.moveTo(x - 9 * s, y - 13 * s); gctx.lineTo(x + 9 * s, y - 13 * s); gctx.stroke();
    sheen(x - 9 * s, y + 2 * s, 4 * s, 9 * s, -0.2, 0.32);
    if (label) {
      gctx.fillStyle = '#6b5340';
      gctx.font = `800 ${13 * s}px 'Baloo 2', sans-serif`;
      gctx.textAlign = 'center'; gctx.textBaseline = 'middle';
      gctx.fillText(String(label), x, y + 5 * s);
    }
  },
  signpost(x, y, s, label) {
    groundShadow(x, y + 34 * s, 16 * s, 5 * s, 0.16);
    gctx.fillStyle = vGrad(y - 10 * s, y + 34 * s, '#d4a879', '#a3754a');
    gctx.fillRect(x - 4 * s, y - 10 * s, 8 * s, 44 * s);
    // the board, tilted just enough to look nailed on by hand
    gctx.save();
    gctx.translate(x, y - 20 * s);
    gctx.rotate(-0.035);
    rrect(-40 * s, -14 * s, 80 * s, 28 * s, 8 * s);
    fillStroke(vGrad(-14 * s, 14 * s, '#fff6e2', '#f0dcb8'), '#b9895a', 2.6 * s);
    gctx.strokeStyle = 'rgba(185,137,90,.4)'; gctx.lineWidth = 1.2 * s;
    gctx.beginPath(); gctx.moveTo(-34 * s, -6 * s); gctx.lineTo(34 * s, -6 * s); gctx.stroke();
    if (label) {
      gctx.fillStyle = '#6b5340';
      gctx.font = `800 ${14 * s}px 'Baloo 2', sans-serif`;
      gctx.textAlign = 'center'; gctx.textBaseline = 'middle';
      gctx.fillText(String(label), 0, 2 * s);
    }
    gctx.restore();
    // two nail heads
    gctx.fillStyle = 'rgba(122,79,44,.7)';
    [-1, 1].forEach((sg) => {
      gctx.beginPath(); gctx.arc(x + sg * 30 * s, y - 20 * s, 1.8 * s, 0, TAU); gctx.fill();
    });
  },
  gate(x, y, s) {
    gctx.fillStyle = '#cbb6e0';
    gctx.fillRect(x - 40 * s, y - 40 * s, 13 * s, 78 * s);
    gctx.fillRect(x + 27 * s, y - 40 * s, 13 * s, 78 * s);
    gctx.beginPath();
    gctx.arc(x, y - 40 * s, 40 * s, Math.PI, 0);
    gctx.lineWidth = 13 * s; gctx.strokeStyle = '#cbb6e0'; gctx.stroke();
    gctx.strokeStyle = '#a98cff'; gctx.lineWidth = 3 * s;
    gctx.beginPath(); gctx.arc(x, y - 40 * s, 46 * s, Math.PI, 0); gctx.stroke();
  },
  pie_gate(x, y, s, label) {
    const slices = Math.max(2, parseInt(label, 10) || 4);
    const R = 34 * s;
    circle(x, y, R, '#ffe6b8', '#e0a85e', 3.4 * s);
    gctx.strokeStyle = '#e0a85e'; gctx.lineWidth = 2.6 * s;
    for (let i = 0; i < slices; i++) {
      const a = (i / slices) * TAU - Math.PI / 2;
      gctx.beginPath(); gctx.moveTo(x, y);
      gctx.lineTo(x + Math.cos(a) * R, y + Math.sin(a) * R);
      gctx.stroke();
    }
    circle(x, y, 4 * s, '#e0a85e', null);
  },
  raft(x, y, s) {
    ellipse(x, y + 16 * s, 46 * s, 8 * s, 'rgba(60,90,130,.22)', null);
    rrect(x - 46 * s, y - 12 * s, 92 * s, 24 * s, 9 * s);
    fillStroke('#d9ab77', '#a97c4e', 3 * s);
    gctx.strokeStyle = 'rgba(169,124,78,.75)'; gctx.lineWidth = 2 * s;
    [-23, 0, 23].forEach((o) => {
      gctx.beginPath(); gctx.moveTo(x + o * s, y - 11 * s); gctx.lineTo(x + o * s, y + 11 * s); gctx.stroke();
    });
  },
  clock(x, y, s) {
    circle(x, y, 28 * s, '#fffaf0', '#a98cff', 4 * s);
    gctx.strokeStyle = '#7d6b86'; gctx.lineWidth = 3 * s; gctx.lineCap = 'round';
    const t = tick / 60;
    gctx.beginPath(); gctx.moveTo(x, y);
    gctx.lineTo(x + Math.cos(t * 0.5 - Math.PI / 2) * 16 * s, y + Math.sin(t * 0.5 - Math.PI / 2) * 16 * s);
    gctx.stroke();
    gctx.lineWidth = 2.2 * s;
    gctx.beginPath(); gctx.moveTo(x, y);
    gctx.lineTo(x + Math.cos(t * 2 - Math.PI / 2) * 22 * s, y + Math.sin(t * 2 - Math.PI / 2) * 22 * s);
    gctx.stroke();
    circle(x, y, 3 * s, '#7d6b86', null);
  },
  fence_post(x, y, s) {
    groundShadow(x, y + 26 * s, 11 * s, 4 * s, 0.16);
    gctx.beginPath();
    gctx.moveTo(x - 7 * s, y + 26 * s);
    gctx.lineTo(x - 7 * s, y - 16 * s);
    gctx.lineTo(x, y - 26 * s);
    gctx.lineTo(x + 7 * s, y - 16 * s);
    gctx.lineTo(x + 7 * s, y + 26 * s);
    gctx.closePath();
    fillStroke(dGrad(x - 7 * s, y, x + 7 * s, y, '#f0d2a8', '#b9885a'), '#966a42', 2.6 * s);
    gctx.save();
    gctx.strokeStyle = 'rgba(140,100,62,.3)'; gctx.lineWidth = 1.2 * s;
    gctx.beginPath(); gctx.moveTo(x - 1.5 * s, y - 13 * s); gctx.lineTo(x - 1.5 * s, y + 22 * s);
    gctx.moveTo(x + 3 * s, y - 11 * s); gctx.lineTo(x + 3 * s, y + 20 * s);
    gctx.stroke();
    gctx.restore();
  },
  tile(x, y, s) {
    gctx.save(); gctx.translate(x, y); gctx.scale(1, 0.55);
    polyPath(0, 0, 26 * s, 4, 0);
    fillStroke(dGrad(-26 * s, -26 * s, 26 * s, 26 * s, '#f4eefc', '#d6c7ea'), '#b09cd0', 2.6 * s);
    gctx.restore();
    gctx.save();
    gctx.globalAlpha = 0.5;
    gctx.translate(x, y - 3 * s); gctx.scale(1, 0.55);
    polyPath(0, 0, 15 * s, 4, 0);
    fillStroke('#ffffff', null);
    gctx.restore();
    gctx.globalAlpha = 1;
  },
  droplet(x, y, s) {
    gctx.beginPath();
    gctx.moveTo(x, y - 18 * s);
    gctx.quadraticCurveTo(x + 15 * s, y + 2 * s, x + 10 * s, y + 10 * s);
    gctx.arc(x, y + 6 * s, 12 * s, 0.35, Math.PI - 0.35);
    gctx.quadraticCurveTo(x - 15 * s, y + 2 * s, x, y - 18 * s);
    gctx.closePath();
    fillStroke(vGrad(y - 18 * s, y + 18 * s, '#d4f1ff', '#72c2e8'), '#4aa3cf', 2.4 * s);
    sheen(x - 3.5 * s, y + 2 * s, 3.2 * s, 5 * s, -0.3, 0.8);
  },
  tick(x, y, s) {
    circle(x, y, 17 * s, '#b6f0d8', '#5fd0ae', 3 * s);
    gctx.strokeStyle = '#3aa580'; gctx.lineWidth = 4 * s; gctx.lineCap = 'round';
    gctx.beginPath();
    gctx.moveTo(x - 7 * s, y); gctx.lineTo(x - 2 * s, y + 6 * s); gctx.lineTo(x + 8 * s, y - 6 * s);
    gctx.stroke();
  },
  npc(x, y, s, label, seed) {
    const species = NPC_CYCLE[Math.floor((seed || 0) * 3.9) % NPC_CYCLE.length];
    drawCreature(x, y, 42 * s, {
      species,
      color: TINT_HEX[['mint', 'lav', 'peach', 'sky'][Math.floor((seed || 0) * 2.1) % 4]] || '#e0d7ff',
      accent: '#a98cff',
      t: tick / 60, face: -1, bounce: Math.sin(tick / 26 + (seed || 0)) * 0.05,
    });
    if (label) {
      gctx.fillStyle = '#4a3b52';
      gctx.font = `700 ${12 * s}px 'Baloo 2', sans-serif`;
      gctx.textAlign = 'center';
      gctx.fillText(label, x, y - 36 * s);
    }
  },
  tree(x, y, s) {
    ellipse(x, y + 34 * s, 28 * s, 8 * s, 'rgba(74,59,82,.16)', null);
    gctx.fillStyle = shade('#b98c6c', '#9c6f52', '#5a5378');
    gctx.fillRect(x - 7 * s, y - 4 * s, 14 * s, 38 * s);
    const leaf = shade('#8fd4a4', '#d79b7e', '#4a5f8c');
    const sway = Math.sin(tick / 70) * 3 * s;
    circle(x + sway, y - 22 * s, 30 * s, leaf, shade('#6cb98a', '#b97f66', '#3b4c73'), 3 * s);
    circle(x - 24 * s + sway, y - 4 * s, 21 * s, leaf, null);
    circle(x + 24 * s + sway, y - 4 * s, 21 * s, leaf, null);
  },
  bridge(x, y, s) {
    gctx.strokeStyle = '#b98c5e'; gctx.lineWidth = 12 * s; gctx.lineCap = 'round';
    gctx.beginPath();
    gctx.moveTo(x - 70 * s, y + 16 * s);
    gctx.quadraticCurveTo(x, y - 34 * s, x + 70 * s, y + 16 * s);
    gctx.stroke();
    gctx.strokeStyle = '#e0bb8e'; gctx.lineWidth = 7 * s;
    gctx.beginPath();
    gctx.moveTo(x - 70 * s, y + 14 * s);
    gctx.quadraticCurveTo(x, y - 36 * s, x + 70 * s, y + 14 * s);
    gctx.stroke();
    gctx.strokeStyle = '#b98c5e'; gctx.lineWidth = 3 * s;
    for (let i = -2; i <= 2; i++) {
      const tt = (i + 2) / 4;
      const bx = lerp(x - 62 * s, x + 62 * s, tt);
      const by = y + 12 * s - Math.sin(tt * Math.PI) * 40 * s;
      gctx.beginPath(); gctx.moveTo(bx, by); gctx.lineTo(bx, by + 20 * s); gctx.stroke();
    }
  },
};
const NPC_CYCLE = ['bunny', 'bird', 'frog', 'mouse'];

// Unknown kinds degrade to a friendly labelled mound instead of throwing.
function drawPropFallback(x, y, s, label) {
  ellipse(x, y + 12 * s, 24 * s, 7 * s, 'rgba(74,59,82,.14)', null);
  gctx.beginPath();
  gctx.arc(x, y + 8 * s, 22 * s, Math.PI, 0);
  gctx.closePath();
  fillStroke('#e8e0f0', '#c3b4d6', 3 * s);
  if (label) {
    gctx.fillStyle = '#7d6b86';
    gctx.font = `700 ${12 * s}px 'Baloo 2', sans-serif`;
    gctx.textAlign = 'center'; gctx.textBaseline = 'middle';
    gctx.fillText(label, x, y - 4 * s);
  }
}

function drawProp(p) {
  const fn = PROP_DRAW[p.kind];
  gctx.save();
  gctx.textAlign = 'center';
  gctx.textBaseline = 'middle';
  gctx.lineJoin = 'round';
  try {
    if (fn) fn(p.x, p.y, p.scale, p.label, p.seed);
    else drawPropFallback(p.x, p.y, p.scale, p.label || p.kind);
  } catch (e) {
    /* one bad prop must never kill the frame */
  }
  gctx.restore();
}

/* ═══════════════════════ 17. STONES ═══════════════════════ */

// One sandbar = one equal part of the fraction. Drawing them as distinct
// islands is what turns "3/4 of 8" into something a 7-year-old can see.
function drawSandbar(b) {
  const lit = stones.some((s) => s.group === b.group && s.picked);
  gctx.save();
  ellipse(b.x, b.y + 40, b.w / 2 + 5, 44, 'rgba(74,59,82,.12)', null);
  ellipse(b.x, b.y + 34, b.w / 2, 40, b.tint, lit ? '#5fd0ae' : b.edge, lit ? 6 : 3);
  // little pebbles for texture
  gctx.fillStyle = 'rgba(255,255,255,.5)';
  for (let i = 0; i < 6; i++) {
    const a = (i / 6) * TAU + b.x;
    gctx.beginPath();
    gctx.arc(b.x + Math.cos(a) * b.w * 0.3, b.y + 36 + Math.sin(a) * 18, 2.6, 0, TAU);
    gctx.fill();
  }
  gctx.restore();
}

function shapePath(shape, x, y, r) {
  switch (shape) {
    case 'triangle': polyPath(x, y, r * 1.12, 3); return 3;
    case 'square': polyPath(x, y, r * 1.06, 4, -Math.PI / 4); return 4;
    case 'rectangle': rrect(x - r * 1.24, y - r * 0.74, r * 2.48, r * 1.48, 7); return 4;
    case 'pentagon': polyPath(x, y, r * 1.06, 5); return 5;
    case 'hexagon': polyPath(x, y, r * 1.04, 6); return 6;
    case 'circle': gctx.beginPath(); gctx.arc(x, y, r, 0, TAU); return 0;
    default: gctx.beginPath(); gctx.arc(x, y, r, 0, TAU); return -1;
  }
}

// Corner pips: a four-year-old cannot read "Pentagon" but can count 5 dots.
function drawCorners(shape, x, y, r) {
  const n = { triangle: 3, square: 4, pentagon: 5, hexagon: 6 }[shape];
  if (!n) return;
  const rr = shape === 'square' ? r * 1.06 : r * (shape === 'triangle' ? 1.12 : 1.05);
  const rot = shape === 'square' ? -Math.PI / 4 : -Math.PI / 2;
  gctx.fillStyle = '#ffffff';
  gctx.strokeStyle = '#7d6b86';
  gctx.lineWidth = 2;
  for (let i = 0; i < n; i++) {
    const a = rot + (i / n) * TAU;
    gctx.beginPath();
    gctx.arc(x + Math.cos(a) * rr, y + Math.sin(a) * rr, 4.2, 0, TAU);
    gctx.fill(); gctx.stroke();
  }
}

// Pips instead of a numeral, for children who cannot read digits yet.
function drawDots(n, x, y, r) {
  n = Math.min(n, 12);
  const cols = n <= 3 ? n : Math.ceil(Math.sqrt(n));
  const rows = Math.ceil(n / cols);
  const gap = Math.min(r * 0.62, (r * 1.35) / Math.max(cols, rows));
  gctx.fillStyle = '#4a3b52';
  for (let i = 0; i < n; i++) {
    const c = i % cols, rw = Math.floor(i / cols);
    const inRow = rw === rows - 1 ? n - cols * rw : cols;
    const dx = (c - (inRow - 1) / 2) * gap;
    const dy = (rw - (rows - 1) / 2) * gap;
    gctx.beginPath();
    gctx.arc(x + dx, y + dy, Math.max(3, r * 0.115), 0, TAU);
    gctx.fill();
  }
}

function drawStone(s) {
  s.wobble += 0.04;
  if (s.pop > 0) s.pop = Math.max(0, s.pop - 0.045);
  if (s.shake > 0) s.shake = Math.max(0, s.shake - 0.05);

  const lift = Math.sin(s.wobble) * 3;
  const shakeX = s.shake ? Math.sin(s.shake * 34) * s.shake * 7 : 0;
  const x = s.x + shakeX;
  const y = s.y + lift;
  const popScale = 1 + (s.pop || 0) * 0.22;
  const r = s.r * popScale;

  // shadow — tighter when the stone is lifted
  groundShadow(x, s.y + s.r * 0.82, s.r * 0.82 - lift * 0.5, Math.max(5, s.r * 0.26), 0.17);

  /* Colours. The unlit default used to be a cold grey-lilac disc, which
     is exactly what made these read as ping-pong balls glued to a
     watercolour. Untinted stones are warm river stone now, and every
     fill is a light ramp rather than a flat wash. */
  const tintFill = TINT_HEX[s.tint];
  const tintEdge = TINT_EDGE[s.tint];
  let fill = tintFill || '#f0e2c9';
  let fillLo = tintFill ? darken(tintFill, 0.86) : '#cfb68f';
  let stroke = tintEdge || '#a3855e';
  if (s.reveal === 'good') { fill = '#c8f4e0'; fillLo = '#8fdcbe'; stroke = '#3aa580'; }
  else if (s.reveal === 'bad') { fill = '#ffdcc9'; fillLo = '#f5b394'; stroke = '#e0764a'; }
  else if (s.picked) { fill = '#fff3c9'; fillLo = '#f2cf72'; stroke = '#d89b1e'; }

  // pickup ring
  if (s.pop > 0) {
    gctx.globalAlpha = s.pop * 0.7;
    circle(x, y, r + (1 - s.pop) * 34, null, stroke, 4);
    gctx.globalAlpha = 1;
  }

  // body
  gctx.save();
  if (s.picked || s.reveal === 'good') { gctx.shadowColor = stroke; gctx.shadowBlur = 18; }
  const sides = shapePath(s.shape, x, y, r);
  fillStroke(vGrad(y - r, y + r, fill, fillLo), stroke, s.picked || s.reveal ? 5.5 : 3.4);
  gctx.restore();

  // a soft rim-light along the top edge, so it sits in the scene's light
  gctx.save();
  gctx.globalAlpha = 0.5;
  gctx.strokeStyle = '#fffdf4';
  gctx.lineWidth = Math.max(1.4, r * 0.09);
  gctx.beginPath();
  gctx.arc(x, y, r * 0.86, Math.PI * 1.12, Math.PI * 1.88);
  gctx.stroke();
  gctx.restore();
  gctx.globalAlpha = 1;

  // glossy top highlight
  gctx.globalAlpha = 0.4;
  ellipse(x - r * 0.22, y - r * 0.44, r * 0.4, r * 0.18, '#ffffff', null, 0, -0.35);
  gctx.globalAlpha = 1;

  if (sides > 0) drawCorners(s.shape, x, y, r);

  // contents: pips > label > nothing (a collectible pickup)
  if (s.labelAbove && s.label) {
    const ty = y - r - 13 - s.labelRow * 18;
    gctx.font = "700 14px 'Baloo 2', sans-serif";
    gctx.textAlign = 'center'; gctx.textBaseline = 'middle';
    const tw = gctx.measureText(s.label).width + 12;
    rrect(x - tw / 2, ty - 10, tw, 20, 9);
    fillStroke(s.picked ? '#fff2bf' : 'rgba(255,250,244,.95)', stroke, 2.5);
    gctx.strokeStyle = stroke; gctx.lineWidth = 2;
    gctx.beginPath(); gctx.moveTo(x, ty + 10); gctx.lineTo(x, y - r - 1); gctx.stroke();
    gctx.fillStyle = '#4a3b52';
    gctx.fillText(s.label, x, ty);
  } else if (s.dots > 0) {
    drawDots(s.dots, x, y, r);
  } else if (s.label) {
    gctx.fillStyle = '#4a3b52';
    const len = String(s.label).length;
    const size = clamp(r * (len > 5 ? 0.42 : len > 3 ? 0.54 : 0.66), 11, 30);
    gctx.font = `700 ${size}px 'Baloo 2', sans-serif`;
    gctx.textAlign = 'center';
    gctx.textBaseline = 'middle';
    gctx.fillText(s.label, x, y);
  } else if (s.small) {
    // Unlabelled collectible — a sparkle so it reads as treasure. It keeps
    // its OWN tint, because prompts like "pick 2 red and 1 blue berries"
    // are unanswerable if every berry is the same colour.
    gctx.fillStyle = s.picked ? '#ffb800' : (tintEdge || '#ffd24a');
    gctx.beginPath();
    for (let i = 0; i < 8; i++) {
      const a = (i / 8) * TAU - Math.PI / 2;
      const rad = i % 2 ? r * 0.28 : r * 0.62;
      const px = x + Math.cos(a) * rad, py = y + Math.sin(a) * rad;
      if (i === 0) gctx.moveTo(px, py); else gctx.lineTo(px, py);
    }
    gctx.closePath(); gctx.fill();
  }

  // Order badge: the child's own path while playing, and after answering
  // the TRUE sequence the server revealed — so a wrong path teaches.
  if (questionType() === 'ordered_path') {
    const revealed = s.orderIndex > 0;
    const n = revealed ? s.orderIndex : state.selected.indexOf(s.id) + 1;
    if (n > 0 && (revealed || s.picked)) {
      circle(x + r * 0.72, y - r * 0.72, 13, revealed ? '#3aa580' : '#a98cff', '#fff', 3);
      gctx.fillStyle = '#fff';
      gctx.font = "800 14px 'Baloo 2', sans-serif";
      gctx.textAlign = 'center'; gctx.textBaseline = 'middle';
      gctx.fillText(String(n), x + r * 0.72, y - r * 0.72);
    }
  }

  if (s.reveal === 'good') { gctx.fillStyle = '#3aa580'; gctx.font = '22px sans-serif'; gctx.fillText('✓', x, y - r - 14); }
  if (s.reveal === 'bad' && s.picked) { gctx.fillStyle = '#ff7a4a'; gctx.font = '22px sans-serif'; gctx.fillText('✗', x, y - r - 14); }
}

// A glowing rope threaded through the stones already stepped on.
function drawRopeTrail() {
  if (questionType() !== 'ordered_path' || state.selected.length < 1) return;
  const pts = state.selected.map((id) => stones.find((s) => s.id === id)).filter(Boolean);
  if (pts.length < 2) return;
  ropeGlow = Math.max(0, ropeGlow - 0.012);

  gctx.save();
  gctx.lineCap = 'round'; gctx.lineJoin = 'round';
  gctx.shadowColor = '#a98cff';
  gctx.shadowBlur = 14 + ropeGlow * 20;
  gctx.strokeStyle = 'rgba(169,140,255,.85)';
  gctx.lineWidth = 9;
  gctx.beginPath();
  gctx.moveTo(pts[0].x, pts[0].y);
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1], b = pts[i];
    gctx.quadraticCurveTo((a.x + b.x) / 2, (a.y + b.y) / 2 + 22, b.x, b.y);
  }
  gctx.stroke();

  gctx.shadowBlur = 0;
  gctx.strokeStyle = 'rgba(255,255,255,.75)';
  gctx.lineWidth = 3;
  gctx.setLineDash([10, 14]);
  gctx.lineDashOffset = -tick * 0.9;
  gctx.stroke();
  gctx.setLineDash([]);
  gctx.restore();
}

/* ═══════════════ 18. THE HERO — procedural creatures ═══════════════
   AI art may be unavailable, so the fallback sprite is often ALL the
   child sees. "fat cat" and "cute dog" must therefore actually look
   like a fat cat and a cute dog. */

const SPECIES = {
  cat:      { body: 'round', ears: 'pointy', tail: 'curl',   color: '#ffb8d4', accent: '#ff8fb8', muzzle: 1, whiskers: 1 },
  dog:      { body: 'round', ears: 'floppy', tail: 'wag',    color: '#e8c49a', accent: '#c08a56', muzzle: 1, nose: 1 },
  bunny:    { body: 'egg',   ears: 'long',   tail: 'puff',   color: '#fdf4ff', accent: '#d9c8ee', muzzle: 1 },
  bird:     { body: 'egg',   ears: 'tuft',   tail: 'fan',    color: '#ffd88a', accent: '#f2a93b', beak: 1, wings: 1 },
  dragon:   { body: 'round', ears: 'horns',  tail: 'spike',  color: '#9be8b8', accent: '#3fae7c', wings: 1, spikes: 1, nostrils: 1 },
  robot:    { body: 'box',   ears: 'antenna', tail: 'none',  color: '#d6e6f7', accent: '#7ba7d0', visor: 1, bolts: 1 },
  fish:     { body: 'fish',  ears: 'none',   tail: 'fin',    color: '#87d9ff', accent: '#3aa8e0', fins: 1 },
  bear:     { body: 'round', ears: 'round',  tail: 'none',   color: '#caa07c', accent: '#966c48', muzzle: 1, nose: 1 },
  unicorn:  { body: 'round', ears: 'pointy', tail: 'rainbow', color: '#fff2fb', accent: '#e2b6ff', horn: 1, mane: 1, muzzle: 1 },
  fox:      { body: 'round', ears: 'pointy', tail: 'bushy',  color: '#ffb27a', accent: '#e2743a', muzzle: 1, nose: 1, bib: 1 },
  dinosaur: { body: 'round', ears: 'none',   tail: 'spike',  color: '#a8e6a1', accent: '#5aab52', spikes: 1, nostrils: 1 },
  mouse:    { body: 'round', ears: 'biground', tail: 'thin', color: '#ded8e8', accent: '#a79bb8', muzzle: 1, nose: 1 },
  frog:     { body: 'wide',  ears: 'none',   tail: 'none',   color: '#b6e86a', accent: '#75ad2c', eyesTop: 1, wideMouth: 1 },
  turtle:   { body: 'wide',  ears: 'none',   tail: 'stub',   color: '#c9e8a8', accent: '#7aa85e', shell: 1 },
};

const SPECIES_WORDS = {
  cat: ['cat', 'kitten', 'kitty', 'feline'],
  dog: ['dog', 'puppy', 'pup', 'doggy', 'hound'],
  bunny: ['bunny', 'rabbit', 'hare'],
  bird: ['bird', 'birdie', 'parrot', 'owl', 'chick', 'penguin', 'duck'],
  dragon: ['dragon', 'wyvern'],
  robot: ['robot', 'bot', 'android', 'droid'],
  fish: ['fish', 'shark', 'whale', 'dolphin', 'goldfish'],
  bear: ['bear', 'panda', 'cub'],
  unicorn: ['unicorn', 'pony', 'horse'],
  fox: ['fox'],
  dinosaur: ['dinosaur', 'dino', 'rex', 'raptor', 'trex'],
  mouse: ['mouse', 'mice', 'rat', 'hamster'],
  frog: ['frog', 'toad'],
  turtle: ['turtle', 'tortoise'],
};

const COLOR_WORDS = {
  pink: '#ffb8d4', red: '#ff8f85', orange: '#ffb27a', yellow: '#ffdf85',
  gold: '#ffd24a', golden: '#ffd24a', green: '#a8e6a1', blue: '#9dd4ff',
  purple: '#cdb6ff', violet: '#cdb6ff', white: '#fdfaff', black: '#8d85a0',
  grey: '#d2ccdd', gray: '#d2ccdd', brown: '#caa07c', silver: '#dfe6ee',
  rainbow: '#ffc7e8', teal: '#9fe6da', cream: '#fff0da',
};

const BIG_WORDS = ['fat', 'chubby', 'big', 'huge', 'giant', 'large', 'round', 'fluffy', 'chonky'];
const SMALL_WORDS = ['tiny', 'small', 'little', 'baby', 'mini', 'teeny', 'itty'];

function describeHero() {
  const interp = state.interpretation || {};
  const objs = Array.isArray(interp.objects) ? interp.objects : [];
  const hay = [state.description, interp.main_character, objs[0], objs.join(' '), interp.setting]
    .filter(Boolean).join(' ').toLowerCase();

  let species = 'cat';
  let best = Infinity;
  Object.keys(SPECIES_WORDS).forEach((sp) => {
    SPECIES_WORDS[sp].forEach((w) => {
      const i = hay.indexOf(w);
      if (i >= 0 && i < best) { best = i; species = sp; }
    });
  });

  const base = SPECIES[species];
  let color = base.color, accent = base.accent;
  Object.keys(COLOR_WORDS).forEach((w) => {
    if (new RegExp(`\\b${w}\\b`).test(hay)) { color = COLOR_WORDS[w]; accent = darken(color, 0.78); }
  });

  let size = 1;
  if (BIG_WORDS.some((w) => new RegExp(`\\b${w}`).test(hay))) size = 1.34;
  if (SMALL_WORDS.some((w) => new RegExp(`\\b${w}`).test(hay))) size = 0.76;

  return { species, color, accent, size };
}

function darken(hex, f) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex);
  if (!m) return hex;
  const v = parseInt(m[1], 16);
  const r = Math.round(((v >> 16) & 255) * f);
  const g = Math.round(((v >> 8) & 255) * f);
  const b = Math.round((v & 255) * f);
  return `#${((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1)}`;
}

function heroEmoji() {
  const sp = (heroLook && heroLook.species) || 'cat';
  return {
    cat: '🐱', dog: '🐶', bunny: '🐰', bird: '🐦', dragon: '🐲', robot: '🤖',
    fish: '🐟', bear: '🐻', unicorn: '🦄', fox: '🦊', dinosaur: '🦖',
    mouse: '🐭', frog: '🐸', turtle: '🐢',
  }[sp] || '🐱';
}

/* Draws a creature centred at (x,y), roughly `size` px tall. */
function drawCreature(x, y, size, o) {
  const sp = SPECIES[o.species] || SPECIES.cat;
  const color = o.color || sp.color;
  const accent = o.accent || sp.accent;
  const k = size / 58;                       // 58px is the reference height
  const fat = o.fat || 1;
  const t = o.t || 0;

  gctx.save();
  gctx.translate(x, y);
  gctx.scale((o.face || 1) * (o.sx || 1), o.sy || 1);
  gctx.lineJoin = 'round';
  gctx.lineCap = 'round';

  const bw = 24 * k * fat;                   // body half-width
  const bh = 22 * k * (sp.body === 'wide' ? 0.86 : sp.body === 'egg' ? 1.12 : 1);

  /* ── behind-the-body parts ── */
  if (sp.wings) {
    gctx.save();
    const flap = Math.sin(t * 6) * 0.3;
    gctx.rotate(flap * 0.12);
    ellipse(-bw * 0.9, -bh * 0.35, bw * 0.75, bh * 0.55, accent, darken(accent, 0.85), 2.4 * k, -0.5);
    ellipse(bw * 0.9, -bh * 0.35, bw * 0.75, bh * 0.55, accent, darken(accent, 0.85), 2.4 * k, 0.5);
    gctx.restore();
  }

  // tail
  gctx.strokeStyle = accent; gctx.fillStyle = accent;
  gctx.lineWidth = 6 * k;
  switch (sp.tail) {
    case 'curl':
      gctx.beginPath();
      gctx.moveTo(-bw * 0.85, bh * 0.35);
      gctx.quadraticCurveTo(-bw * 1.9, bh * 0.2 + Math.sin(t * 3) * 5 * k, -bw * 1.5, -bh * 0.55);
      gctx.stroke();
      break;
    case 'wag':
      gctx.beginPath();
      gctx.moveTo(-bw * 0.85, bh * 0.2);
      gctx.quadraticCurveTo(-bw * 1.6, bh * 0.1, -bw * 1.4 + Math.sin(t * 9) * 7 * k, -bh * 0.5);
      gctx.stroke();
      break;
    case 'puff':
      circle(-bw * 0.95, bh * 0.45, 8 * k, '#fff', accent, 2.4 * k);
      break;
    case 'bushy':
      gctx.beginPath();
      gctx.moveTo(-bw * 0.8, bh * 0.3);
      gctx.quadraticCurveTo(-bw * 2.1, bh * 0.4, -bw * 1.7, -bh * 0.5);
      gctx.quadraticCurveTo(-bw * 1.2, bh * 0.0, -bw * 0.8, bh * 0.3);
      gctx.closePath();
      fillStroke(accent, darken(accent, 0.8), 2.4 * k);
      circle(-bw * 1.72, -bh * 0.46, 6 * k, '#fff9f2', null);
      break;
    case 'spike':
      gctx.beginPath();
      gctx.moveTo(-bw * 0.8, bh * 0.25);
      gctx.quadraticCurveTo(-bw * 2.2, bh * 0.1, -bw * 1.9, -bh * 0.7);
      gctx.lineWidth = 9 * k;
      gctx.stroke();
      break;
    case 'fan':
      gctx.beginPath();
      gctx.moveTo(-bw * 0.7, 0);
      gctx.lineTo(-bw * 1.8, -bh * 0.5);
      gctx.lineTo(-bw * 1.75, bh * 0.45);
      gctx.closePath();
      fillStroke(accent, darken(accent, 0.8), 2.4 * k);
      break;
    case 'fin':
      gctx.beginPath();
      gctx.moveTo(-bw * 0.6, 0);
      gctx.lineTo(-bw * 1.7, -bh * 0.75);
      gctx.quadraticCurveTo(-bw * 1.3, 0, -bw * 1.7, bh * 0.75);
      gctx.closePath();
      fillStroke(accent, darken(accent, 0.8), 2.4 * k);
      break;
    case 'rainbow': {
      const cols = ['#ff9ec4', '#ffd24a', '#8bd45f', '#6fc0ff', '#c0a2ff'];
      cols.forEach((c, i) => {
        gctx.strokeStyle = c; gctx.lineWidth = 4 * k;
        gctx.beginPath();
        gctx.moveTo(-bw * 0.85, bh * 0.2 + (i - 2) * 3 * k);
        gctx.quadraticCurveTo(-bw * 1.8, -bh * 0.2 + (i - 2) * 5 * k, -bw * 1.6, -bh * 0.8);
        gctx.stroke();
      });
      break;
    }
    case 'thin':
      gctx.lineWidth = 3 * k;
      gctx.beginPath();
      gctx.moveTo(-bw * 0.8, bh * 0.4);
      gctx.quadraticCurveTo(-bw * 2, bh * 0.5, -bw * 1.6, -bh * 0.2);
      gctx.stroke();
      break;
    case 'stub':
      circle(-bw * 0.95, bh * 0.4, 5 * k, accent, null);
      break;
    default: break;
  }

  /* ── ears / head gear behind head ── */
  const headY = -bh * 0.62;
  gctx.strokeStyle = accent; gctx.lineWidth = 2.8 * k;
  if (sp.ears === 'long') {          // bunny
    ellipse(-9 * k, headY - 26 * k, 6.5 * k, 20 * k, color, accent, 2.8 * k, -0.15);
    ellipse(9 * k, headY - 26 * k, 6.5 * k, 20 * k, color, accent, 2.8 * k, 0.15);
    ellipse(-9 * k, headY - 26 * k, 3 * k, 13 * k, '#ffd8e8', null, 0, -0.15);
    ellipse(9 * k, headY - 26 * k, 3 * k, 13 * k, '#ffd8e8', null, 0, 0.15);
  } else if (sp.ears === 'biground') {
    circle(-15 * k, headY - 12 * k, 11 * k, color, accent, 2.8 * k);
    circle(15 * k, headY - 12 * k, 11 * k, color, accent, 2.8 * k);
    circle(-15 * k, headY - 12 * k, 6 * k, '#ffd8e8', null);
    circle(15 * k, headY - 12 * k, 6 * k, '#ffd8e8', null);
  } else if (sp.ears === 'round') {
    circle(-14 * k, headY - 11 * k, 8 * k, color, accent, 2.8 * k);
    circle(14 * k, headY - 11 * k, 8 * k, color, accent, 2.8 * k);
  } else if (sp.ears === 'floppy') {
    ellipse(-16 * k, headY - 1 * k, 7 * k, 15 * k, accent, darken(accent, 0.82), 2.4 * k, -0.35);
    ellipse(16 * k, headY - 1 * k, 7 * k, 15 * k, accent, darken(accent, 0.82), 2.4 * k, 0.35);
  } else if (sp.ears === 'pointy') {
    gctx.beginPath();
    gctx.moveTo(-16 * k, headY - 3 * k); gctx.lineTo(-9 * k, headY - 22 * k); gctx.lineTo(-1 * k, headY - 5 * k);
    gctx.closePath(); fillStroke(color, accent, 2.8 * k);
    gctx.beginPath();
    gctx.moveTo(16 * k, headY - 3 * k); gctx.lineTo(9 * k, headY - 22 * k); gctx.lineTo(1 * k, headY - 5 * k);
    gctx.closePath(); fillStroke(color, accent, 2.8 * k);
  } else if (sp.ears === 'horns') {
    gctx.beginPath();
    gctx.moveTo(-11 * k, headY - 8 * k);
    gctx.quadraticCurveTo(-20 * k, headY - 24 * k, -6 * k, headY - 18 * k);
    gctx.closePath(); fillStroke('#fff0c4', '#d8b46a', 2.4 * k);
    gctx.beginPath();
    gctx.moveTo(11 * k, headY - 8 * k);
    gctx.quadraticCurveTo(20 * k, headY - 24 * k, 6 * k, headY - 18 * k);
    gctx.closePath(); fillStroke('#fff0c4', '#d8b46a', 2.4 * k);
  } else if (sp.ears === 'antenna') {
    gctx.strokeStyle = '#8ea7bd'; gctx.lineWidth = 3 * k;
    gctx.beginPath(); gctx.moveTo(0, headY - 14 * k); gctx.lineTo(0, headY - 26 * k); gctx.stroke();
    circle(0, headY - 29 * k, 5 * k, '#ff8fb8', '#e2648f', 2 * k);
  } else if (sp.ears === 'tuft') {
    gctx.strokeStyle = accent; gctx.lineWidth = 3 * k;
    [-6, 0, 6].forEach((ox) => {
      gctx.beginPath();
      gctx.moveTo(ox * k, headY - 12 * k);
      gctx.lineTo(ox * k + 3 * k, headY - 22 * k);
      gctx.stroke();
    });
  }

  if (sp.mane) {
    const cols = ['#ff9ec4', '#ffd24a', '#8bd45f', '#6fc0ff'];
    cols.forEach((c, i) => {
      gctx.strokeStyle = c; gctx.lineWidth = 5 * k;
      gctx.beginPath();
      gctx.moveTo(-6 * k + i * 2 * k, headY - 12 * k);
      gctx.quadraticCurveTo(-22 * k, headY - 2 * k + i * 4 * k, -14 * k, headY + 16 * k + i * 3 * k);
      gctx.stroke();
    });
  }
  if (sp.horn) {
    gctx.beginPath();
    gctx.moveTo(-5 * k, headY - 13 * k);
    gctx.lineTo(0, headY - 34 * k);
    gctx.lineTo(5 * k, headY - 13 * k);
    gctx.closePath();
    fillStroke('#ffe9a8', '#e0b45e', 2.4 * k);
  }

  /* ── body ── */
  if (sp.body === 'box') {
    rrect(-bw, -bh * 0.5, bw * 2, bh * 1.6, 9 * k);
    fillStroke(color, accent, 3 * k);
  } else if (sp.body === 'fish') {
    ellipse(0, 0, bw * 1.15, bh * 0.82, color, accent, 3 * k);
  } else if (sp.body === 'wide') {
    ellipse(0, bh * 0.2, bw * 1.16, bh * 0.85, color, accent, 3 * k);
  } else if (sp.body === 'egg') {
    ellipse(0, bh * 0.15, bw * 0.92, bh * 1.02, color, accent, 3 * k);
  } else {
    ellipse(0, bh * 0.18, bw * 0.98, bh * 0.94, color, accent, 3 * k);
  }

  if (sp.shell) {
    gctx.beginPath();
    gctx.arc(0, bh * 0.2, bw * 0.95, Math.PI, 0);
    gctx.closePath();
    fillStroke('#c08a56', '#8d6236', 3 * k);
    gctx.strokeStyle = '#8d6236'; gctx.lineWidth = 2 * k;
    [-0.45, 0, 0.45].forEach((f) => {
      gctx.beginPath();
      gctx.moveTo(bw * f, bh * 0.2);
      gctx.lineTo(bw * f * 0.7, bh * 0.2 - bw * 0.8);
      gctx.stroke();
    });
  }
  if (sp.bib) {
    ellipse(0, bh * 0.55, bw * 0.55, bh * 0.4, '#fff8f0', null);
  }
  if (sp.fins) {
    gctx.beginPath();
    gctx.moveTo(-2 * k, -bh * 0.78);
    gctx.lineTo(10 * k, -bh * 1.4);
    gctx.lineTo(16 * k, -bh * 0.6);
    gctx.closePath();
    fillStroke(accent, darken(accent, 0.8), 2.4 * k);
  }
  if (sp.spikes) {
    gctx.fillStyle = darken(accent, 0.9);
    for (let i = 0; i < 4; i++) {
      const sxp = -bw * 0.55 + i * bw * 0.38;
      gctx.beginPath();
      gctx.moveTo(sxp - 5 * k, -bh * 0.66);
      gctx.lineTo(sxp, -bh * 1.02);
      gctx.lineTo(sxp + 5 * k, -bh * 0.66);
      gctx.closePath(); gctx.fill();
    }
  }
  if (sp.bolts) {
    gctx.fillStyle = '#8ea7bd';
    [[-bw * 0.6, bh * 0.85], [bw * 0.6, bh * 0.85]].forEach(([bxp, byp]) => {
      gctx.beginPath(); gctx.arc(bxp, byp, 3.4 * k, 0, TAU); gctx.fill();
    });
  }

  /* ── head ── */
  const hr = 17 * k * (sp.body === 'fish' ? 0.0 : 1);
  if (hr > 0) {
    if (sp.body === 'box') { rrect(-hr, headY - hr, hr * 2, hr * 2, 7 * k); fillStroke(color, accent, 3 * k); }
    else circle(0, headY, hr, color, accent, 3 * k);
  }

  /* ── face ── */
  const fy = hr > 0 ? headY : -bh * 0.18;
  const eyeY = sp.eyesTop ? fy - bh * 0.62 : fy - 2 * k;
  const eyeX = 6.4 * k;

  if (sp.visor) {
    rrect(-13 * k, fy - 7 * k, 26 * k, 13 * k, 6 * k);
    fillStroke('#42506b', '#2b3549', 2.4 * k);
    gctx.fillStyle = '#7fe8ff';
    [-5, 5].forEach((ox) => { gctx.beginPath(); gctx.arc(ox * k, fy, 3 * k, 0, TAU); gctx.fill(); });
  } else if (sp.eyesTop) {
    // frog: eyes perched on top of the head
    circle(-9 * k, eyeY, 8 * k, color, accent, 2.4 * k);
    circle(9 * k, eyeY, 8 * k, color, accent, 2.4 * k);
    circle(-9 * k, eyeY, 4 * k, '#fff', null);
    circle(9 * k, eyeY, 4 * k, '#fff', null);
    circle(-9 * k, eyeY, 2.2 * k, '#332b3d', null);
    circle(9 * k, eyeY, 2.2 * k, '#332b3d', null);
  } else {
    const blink = (Math.sin(t * 0.9) > 0.985) ? 0.12 : 1;
    gctx.fillStyle = '#3a3044';
    ellipse(-eyeX, eyeY, 3.3 * k, 3.6 * k * blink, '#3a3044', null);
    ellipse(eyeX, eyeY, 3.3 * k, 3.6 * k * blink, '#3a3044', null);
    if (blink > 0.5) {
      circle(-eyeX + 1.1 * k, eyeY - 1.2 * k, 1.2 * k, '#fff', null);
      circle(eyeX + 1.1 * k, eyeY - 1.2 * k, 1.2 * k, '#fff', null);
    }
  }

  if (sp.muzzle) ellipse(0, fy + 6.5 * k, 8 * k, 5.5 * k, 'rgba(255,255,255,.55)', null);
  if (sp.beak) {
    gctx.beginPath();
    gctx.moveTo(4 * k, fy + 1 * k);
    gctx.lineTo(17 * k, fy + 5 * k);
    gctx.lineTo(4 * k, fy + 9 * k);
    gctx.closePath();
    fillStroke('#ffb347', '#e08a1f', 2 * k);
  }
  if (sp.nose) {
    gctx.beginPath();
    gctx.moveTo(-3.2 * k, fy + 4 * k); gctx.lineTo(3.2 * k, fy + 4 * k); gctx.lineTo(0, fy + 7.4 * k);
    gctx.closePath(); fillStroke('#4a3b52', null);
  }
  if (sp.nostrils) {
    gctx.fillStyle = darken(accent, 0.7);
    circle(-4 * k, fy + 6 * k, 1.7 * k, darken(accent, 0.7), null);
    circle(4 * k, fy + 6 * k, 1.7 * k, darken(accent, 0.7), null);
  }
  if (!sp.beak && !sp.visor) {
    gctx.strokeStyle = '#4a3b52'; gctx.lineWidth = 1.9 * k;
    gctx.beginPath();
    if (sp.wideMouth) {
      gctx.arc(0, fy + 1 * k, 12 * k, 0.25, Math.PI - 0.25);
    } else {
      gctx.arc(0, fy + 7 * k, 4.6 * k, 0.22, Math.PI - 0.22);
    }
    gctx.stroke();
  }
  if (sp.whiskers) {
    gctx.strokeStyle = 'rgba(74,59,82,.5)'; gctx.lineWidth = 1.4 * k;
    [-1, 1].forEach((sgn) => {
      [-2, 2].forEach((dy) => {
        gctx.beginPath();
        gctx.moveTo(sgn * 9 * k, fy + 5 * k + dy * k);
        gctx.lineTo(sgn * 20 * k, fy + 3 * k + dy * 1.6 * k);
        gctx.stroke();
      });
    });
  }

  // cheeks
  gctx.fillStyle = 'rgba(255,143,184,.42)';
  circle(-12 * k, fy + 5 * k, 4 * k, 'rgba(255,143,184,.42)', null);
  circle(12 * k, fy + 5 * k, 4 * k, 'rgba(255,143,184,.42)', null);

  gctx.restore();
}

/* The backgrounds are now painted with NOBODY in them and the hero arrives
   as a transparent cutout, so the sprite has to be composited into the
   scene rather than pasted over it:

     · sized relative to the play band, not to the collision box
     · aspect ratio preserved — a stretched character reads as a mistake
     · feet planted on the ground plane (the bottom of the collision box),
       which is also where the contact shadow goes
     · the shadow lifts and fades with the walk bounce

   The collision box (hero.w/h) is deliberately left alone: it is what the
   pickup maths uses, and changing it would change the game. */
/* Scale. The hero used to be drawn at roughly the size of its own 58px
   collision box, which on a 960×540 plate read as a thumbnail in the
   corner — easy to miss entirely. It is the hero of the scene, so it is
   now drawn at ~2.5× that, filling most of the play band, while the
   collision box (hero.w/h) is deliberately left alone: it is what the
   pickup maths uses, and changing it would change the game. */
const SPRITE_BAND_FRACTION = 0.74;   // of the play band's height
const SPRITE_MAX_W = 260;
const HERO_MIN_SCALE = 1.6;          // × the collision box
const HERO_MAX_SCALE = 3.1;
const CREATURE_FOOT_FRACTION = 0.38; // how far below its centre a creature's feet sit

// How tall whoever is standing there should be drawn.
function heroDrawHeight() {
  const band = Math.max(80, play.bottom - play.top);
  return clamp(band * SPRITE_BAND_FRACTION, hero.h * HERO_MIN_SCALE, hero.h * HERO_MAX_SCALE);
}

function spriteMetrics() {
  if (!imgReady(spriteImage)) return null;
  const nw = spriteImage.naturalWidth;
  const nh = spriteImage.naturalHeight;
  if (!(nw > 0) || !(nh > 0)) return null;
  let h = heroDrawHeight();
  let w = h * (nw / nh);
  if (w > SPRITE_MAX_W) { w = SPRITE_MAX_W; h = w * (nh / nw); }   // never stretch: refit
  return { w, h };
}

function drawHero() {
  const moving = Math.hypot(hero.vx, hero.vy) > 0.2;
  const bounce = Math.sin(hero.bob) * (moving ? 5 : 1.6);
  const cx = hero.x + hero.w / 2;
  const cy = hero.y + hero.h / 2 + bounce;
  const groundY = hero.y + hero.h + 4;        // where the feet meet the world

  // squash & stretch: stretch on the way up, squash on the way down
  const phase = Math.cos(hero.bob);
  hero.sq = lerp(hero.sq, moving ? phase * 0.09 : 0, 0.25);
  const sx = 1 - hero.sq;
  const sy = 1 + hero.sq;

  const spr = spriteMetrics();
  const creatureH = heroDrawHeight();

  // Contact shadow, sized to whoever is actually standing there. It shrinks
  // and fades as the hero lifts off the ground.
  const lift = clamp((bounce + 6) / 12, 0, 1);
  const footprint = spr ? spr.w : creatureH * 0.86;
  const shadowRx = footprint * 0.34 * (1 - lift * 0.3);
  const shadowRy = Math.max(6, footprint * 0.11) * (1 - lift * 0.3);
  gctx.globalAlpha = 0.07 + 0.09 * (1 - lift);
  ellipse(cx, groundY + 1, shadowRx * 1.45, shadowRy * 1.35, '#4a3b52', null);
  gctx.globalAlpha = 0.10 + 0.14 * (1 - lift);
  ellipse(cx, groundY, shadowRx, shadowRy, '#4a3b52', null);
  gctx.globalAlpha = 1;

  if (hero.bounce > 0) hero.bounce = Math.max(0, hero.bounce - 0.06);

  if (spr) {
    // A painted sprite wants far less cartoon squash than a vector one.
    const psx = 1 + (sx - 1) * 0.45;
    const psy = 1 + (sy - 1) * 0.45;
    gctx.save();
    // Anchor at the feet so the character never floats or sinks, whatever
    // shape the generator hands back.
    gctx.translate(cx, groundY + bounce * 0.6);
    gctx.scale((hero.face || 1) * psx, psy);
    try {
      gctx.drawImage(spriteImage, -spr.w / 2, -spr.h, spr.w, spr.h);
    } catch (e) {
      /* a broken sprite falls through to the procedural hero next frame */
    }
    gctx.restore();
  } else {
    // The procedural creature is anchored at its FEET too, so scaling it
    // up plants it on the ground plane instead of floating it.
    const look = heroLook || (heroLook = describeHero());
    drawCreature(cx, groundY - creatureH * CREATURE_FOOT_FRACTION + bounce * 0.6, creatureH, {
      species: look.species,
      color: look.color,
      accent: look.accent,
      fat: look.size,
      face: hero.face,
      sx, sy,
      t: tick / 60,
    });
  }

  const name = state.interpretation && state.interpretation.character_name;
  if (name) {
    // A little painted name ribbon, sitting above whoever is actually
    // drawn — not above the invisible collision box.
    const drawnH = spr ? spr.h : creatureH * 1.15;
    const tagY = clamp(groundY - drawnH - 26, 8, H - 34);
    gctx.save();
    gctx.font = "800 15px 'Baloo 2', sans-serif";
    gctx.textAlign = 'center';
    gctx.textBaseline = 'middle';
    const w = gctx.measureText(name).width + 26;
    const hgt = 25;
    gctx.shadowColor = 'rgba(74,59,82,.28)';
    gctx.shadowBlur = 9;
    gctx.shadowOffsetY = 3;
    rrect(cx - w / 2, tagY, w, hgt, 12);
    fillStroke('rgba(255,250,241,.96)', 'rgba(201,168,116,.9)', 2.4);
    gctx.shadowBlur = 0; gctx.shadowOffsetY = 0;
    // the little tail that points down at the hero
    gctx.beginPath();
    gctx.moveTo(cx - 6, tagY + hgt - 1);
    gctx.lineTo(cx, tagY + hgt + 6);
    gctx.lineTo(cx + 6, tagY + hgt - 1);
    gctx.closePath();
    fillStroke('rgba(255,250,241,.96)', null);
    gctx.fillStyle = '#6b5340';
    gctx.fillText(name, cx, tagY + hgt / 2);
    gctx.restore();
  }
}

/* ═══════════════════════ 19. LOOP ═══════════════════════ */

function update() {
  tick++;
  easeCamera();

  // Advance the story-frame crossfade. Once it finishes we can let go of the
  // outgoing frame so it isn't held in memory for the rest of the quest.
  if (bgFade < 1) {
    bgFade = Math.min(1, bgFade + 1 / FRAME_FADE_FRAMES);
    if (bgFade >= 1) bgOutgoing = null;
  }

  hero.vx = ((keys.ArrowRight ? 1 : 0) - (keys.ArrowLeft ? 1 : 0)) * HERO_SPEED;
  hero.vy = ((keys.ArrowDown ? 1 : 0) - (keys.ArrowUp ? 1 : 0)) * HERO_SPEED;
  if (hero.vx !== 0) hero.face = Math.sign(hero.vx);
  hero.x = clamp(hero.x + hero.vx, 0, W - hero.w);
  hero.y = clamp(hero.y + hero.vy, play.top - 40, Math.min(H - hero.h - 6, play.bottom - hero.h * 0.6));
  if (hero.vx || hero.vy) hero.bob += 0.24; else hero.bob += 0.035;

  checkPickups();
}

function draw() {
  drawBackdrop();
  drawAmbient();

  props.forEach(drawProp);

  /* ★ The answers are DOM cards now. The canvas only paints them where
     the SPACE is the puzzle — a path to walk, a perimeter to trace,
     treasure scattered across the ground, fraction islands in a river.
     Everywhere else the picture stays a picture. */
  if (canvasStoneMode() !== 'none') {
    sandbars.forEach(drawSandbar);
    drawRopeTrail();
    stones.forEach(drawStone);
  }

  drawHero();
  drawParticles();

  const overlay = TINT_OVERLAY[timeOfDay];
  if (overlay) { gctx.fillStyle = overlay; gctx.fillRect(0, 0, W, H); }
  drawVignette();
}

// A thrown exception used to kill the game permanently. Now it logs once
// and the world keeps turning.
let renderErrorLogged = false;
function loop() {
  if (!$('screen-game').classList.contains('active')) { running = false; return; }
  try {
    update();
    draw();
  } catch (err) {
    if (!renderErrorLogged) {
      renderErrorLogged = true;
      console.error('[Doodle Quest] render error (further ones suppressed):', err);
    }
  }
  requestAnimationFrame(loop);
}

/* ── boot ── */
initCanvas();
initDestination();
loadTopics();
syncMuteButton();
seedAmbient();
heroLook = describeHero();
