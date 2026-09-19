/* ─────────────────────────────────────────────────────────────────────
   Do-IT-oodle — THE BRANDING LAYER.

   This file is the ONE place the product's identity lives. Change it here
   and the whole app follows: the title screen, the loading screen, every
   header, the browser tab, the small mark in the game HUD.

   Nothing in here may leak into the interactive story itself. The brand
   dresses the CHROME — titles, headers, buttons, loading — and stops at
   the edge of the game stage. Inside the stage the child's own world is
   the only thing on screen.

   Swapping the brand
   ------------------
   * name / motto     : plain strings below.
   * wordmark         : an array of runs; `em: true` is the emphasised part
                        (the "IT" pun). Rendered as live text so it scales
                        and stays selectable/accessible at any size.
   * assets           : PNGs under frontend/assets, served from /static.
                        Set a value to null and the app falls back to the
                        live-text wordmark — no layout breakage either way.
   * colors           : mirrored into CSS custom properties on :root at
                        boot, so style.css can use var(--brand-*).

   Asset provenance: extracted from the team's HackMIT 2026 deck.
   ───────────────────────────────────────────────────────────────────── */

window.BRAND = {
  name: 'Do-IT-oodle',

  // Live-text wordmark. Keep the capitalisation exactly: Do-IT-oodle.
  wordmark: [
    { t: 'Do-' },
    { t: 'IT', em: true },
    { t: '-oodle' },
  ],

  motto: 'Draw anything. Tell us what you want to learn. Your drawing becomes the game.',

  // Short form, for places a full sentence will not fit.
  tagline: 'Draw it. Play it. Learn it.',

  assets: {
    // Primary lockup: wordmark + paint palette.
    logo: '/static/assets/logo_full.png',
    // Wordmark only.
    wordmark: '/static/assets/wordmark.png',
    // The paint-palette mark on its own — small badges, the loading spinner.
    mark: '/static/assets/palette.png',
    // The motto as typeset in the deck. Reference only: we render the motto
    // as live text so it reflows and reads out to a screen reader.
    motto: '/static/assets/motto.png',
  },

  // Sampled from the deck. `style.css` reads these as --brand-*; the wider
  // app palette is built outward from them (see the palette note in
  // style.css) so the whole product stays in the same family.
  colors: {
    peach: '#F8D2BF',       // the deck's background
    orange: '#F0965B',      // wordmark fill
    sun: '#F4D372',         // wordmark outline
    cream: '#F8E4C8',       // the palette board
    rose: '#EA93A3',
    yellow: '#F9DC7A',
    green: '#8FC68C',
    blue: '#86C2EE',
    ink: '#33291F',
  },
};
