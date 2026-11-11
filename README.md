# LATCHA website

A scroll-driven landing page for **LATCHA**, a speciality matcha and premium soft-serve café.
One cup travels through four scenes as you scroll, then the page opens into the menu, a float builder and a small chat helper.
It is a single `index.html` with no framework and no build step.

**Live preview:** https://rahuljakula.github.io/latcha-preview/

Built by **Rahul J Akula** (Hyderabad) for **Rajasree Yarlagadda**, founder of LATCHA.

> This is a design preview. The brand name, logo lettering and menu belong to LATCHA. See [Content and rights](#content-and-rights).

---

## What is on the page

**Scroll story** (the first 620 vh of the page)

| Scene | What you see |
|---|---|
| 1. Hero | Curtain lifts, the large LATCHA logo lettering rises letter by letter, an Ube Cloud cup floats and swings with sparkles around it. |
| 2. Ingredients | Five badges (Uji matcha, Rosy cloud, Vanilla soft serve, Ube, Strawberry crush) pop in around a still picture of a matcha and vanilla swirl. |
| 3. Reviews | Three review cards in frosted glass beside a picture of three LATCHA drinks. |
| 4. The matcha table | An open tin of matcha powder with its lid beside it and a scoop on top, next to a LATCHA cup that turns while matcha is poured in and mixes. |

**After the story**

- Pour video section with a rotating "Matcha · Soft serve · Floats" badge
- Instagram-style profile card and post grid (`@siplatcha`)
- Full menu: Classic Iced Matcha Lattes (11), Cloud Lattes (6), Premium Soft Serves (8), Matcha Floats (8). Sweetness and calories are shown. **No prices on purpose.**
- **Build your float:** pick a latte, a soft serve and extra matcha, and see the matching float
- Latcha 3.0 section, and a footer with the founder line
- **Latchi**, a cup mascot in the corner. Click it to chat. It answers questions about the menu, sweetness, calories, best sellers, soft serve, delivery, extra matcha, oat milk and opening hours. It never quotes prices.

Also included: a full phone layout (below 760 px wide), glass panels, 3D tilt cards (desktop), a sparkle cursor trail, and `prefers-reduced-motion` support.

---

## Run it

No install needed. Open `index.html` by double-clicking it, or serve the folder so the videos load reliably:

```bash
python3 -m http.server 8000
# then open http://localhost:8000
```

It loads fonts from Google Fonts, so it needs an internet connection for the typefaces.

## Project layout

```
index.html        the whole site (HTML + CSS + JS, inline)
assets/
  logo*.png       the LATCHA logo (badge, square, and transparent lettering for the hero)
  cup-*.png       transparent cut-outs: hero cup, swirl cup, three-cup picture
  tin-open.png    open matcha tin with lid and scoop
  menu/           33 menu cut-outs: classic-1..11, cloud-1..6, soft-1..8, float-1..8
  gen-*.jpg       generated photos (setup, pour, ube, soft serve, float)
  photo-*.jpg     photos from the LATCHA Instagram and the founder's team (used for the preview)
  pour.mp4        pour video (section after the story)
  pourcup-opaque.mp4  video of the cup being filled, on a green backdrop
  pourcup-static.png  still used if the video cannot load
tools/cutout.swift   Vision-based background remover (macOS)
```

## How it works

- **One animation loop.** A single `requestAnimationFrame` loop drives the whole scroll story. Scroll progress `p` (0 to 1 over the story) maps to a timeline `t = 2.6 + p × 10` seconds and is smoothed each frame (`sm += (p − sm) × 0.075`). The first 2.6 s play on load.
- **Scenes** are timed windows in that timeline: cup drop, move to ingredients (2.5–3.8 s), badges (3.55 s+), move to reviews (5.3–6.7 s), wipe to the table (8.3–9.75 s), final copy (9.55–10.6 s).
- **A stage that fits every screen.** The scene lives in a `container-type: size` box and everything uses `cqh` and `cqw` units, so cups land in the same place at any size. On phones (≤ 760 px) the stage becomes portrait and each scene uses a separate position set (`K1`, `FX1`, `ING1` in the script).
- **Transparent video on every browser.** The cup-pouring clip is an ordinary video with a green backdrop. The page draws each frame to a canvas and removes the green on the fly, because iPhones and some in-app browsers ignore transparent video. If autoplay is blocked, a **Tap to play** button appears over the cup.
- **Editing the menu.** The items are four arrays near the top of the script: `CLASSIC`, `CLOUD`, `SOFT`, `FLOAT`. Each entry is `[name, description, kcal, sweetness, bestSeller?]`. The matching picture is `assets/menu/<group>-<n>.png`.
- **Latchi's answers** are keyword rules in the `ask()` function. Add a line there to teach it something new.

## Swapping in real photos

Replace a file in `assets/` with one of the same name and the page picks it up. The most useful ones:

| File | Used for |
|---|---|
| `cup-ube.png` | Hero cup (transparent PNG, about 600 × 1100) |
| `cup-matchaswirl.png` | Scene 2 picture |
| `cups-trio.png` | Scene 3 picture |
| `tin-open.png` | Scene 4 tin |
| `pourcup-opaque.mp4` | Scene 4 pouring cup (keep a flat green backdrop, about `#41bd46`) |
| `gen-setup.jpg` | Blurred backdrop and the table scene (16:9) |
| `menu/*.png` | Menu pictures |
| `logo.png`, `logo-sq.png`, `logo-word.png` | Logo |

## Rebuilding the media

The generated pictures were made with an image model, then cut out and processed locally. To repeat the steps:

```bash
# 1. Cut a cup out of a studio photo (single image) or a catalogue sheet (cols x rows)
swift tools/cutout.swift cup-photo.png 1 1 1 assets/cup-new 1100
swift tools/cutout.swift menu-sheet.png 4 3 11 assets/menu/classic 420

# 2. Make the cup-pouring video web-ready (H.264, no sound, small)
ffmpeg -i pour-source.mp4 -t 3.2 -vf "scale=800:450,format=yuv420p" \
  -c:v libx264 -crf 22 -movflags +faststart -an assets/pourcup-opaque.mp4
```

Generate the cup on a flat green backdrop (`#41bd46`) so the on-page keying works.

## Browser support

Developed and checked in desktop Chrome and at phone width. The iPhone and Safari video issues found along the way are fixed (see "Transparent video on every browser"), but Safari and a real iPhone have only been checked by the author on one phone. If a video cannot play, the page shows a still picture of the same cup. The scroll story needs `container-type: size`, which all current browsers support.

## Deploying

The site is static. It is published with GitHub Pages from the `main` branch root. Any static host (Netlify, Vercel, Cloudflare Pages) works by pointing it at this folder.

## Content and rights

- Most pictures in `assets/` are **AI-generated** (cups, tin, table, pour, menu cut-outs). They are stand-ins and should be replaced with LATCHA's own photography before launch.
- Some pictures (`photo-*.jpg`, the logo, the three-cup picture, the swirl-cup picture) are LATCHA's own Instagram images or photos taken at LATCHA, used here for this preview only.
- The LATCHA name, logo, menu and copy belong to LATCHA. Do not reuse them without the owner's permission.
- Fonts: Bagel Fat One, Gloock, Nunito Sans, Pinyon Script (Google Fonts, open licences).

## Contact

**Rahul J Akula** · Hyderabad
