# Production Pipeline — *"Castor vs. The Jargon Beast"*

> Companion to `docs/business/castor-screenplay.md`.
> Step-by-step instructions for turning the existing logo into a finished 2:30 animated short via AI tools.

---

## Starting point

What you already have:

- **`src/static/images/castor-logo-nobg.png`** — a stylized geometric beaver head in profile, blues and purples, low-poly faceted style.

**Honest read on the existing asset:** it's a **brand mark**, not a character. The geometric low-poly style is gorgeous as a logomark but **not animatable** — you cannot reliably puppet faceted geometric planes through 20+ shots of expression and motion in any AI tool.

**The right move:** design the mascot as a **sibling asset** that inherits the logo's identity (palette, beaver-ness, slight mystery in the silhouette) but lives in Pixar-cartoon territory where animation tools excel. The logo stays the logo. The mascot is new. Brands do this routinely — Mailchimp's logo and "Freddie" the chimp are siblings, not the same asset.

---

## Tools, in order

| Phase | Tool | Why |
|---|---|---|
| **1. Character design** | **Nano-Banana** (Gemini 2.5 Flash Image) via [aistudio.google.com](https://aistudio.google.com) | Best-in-class as of 2026 for "here's a reference image, give me consistent variations." Free tier is enough for design exploration. |
| **2. Scene stills** | Nano-Banana again | Reuses the locked character reference shot-to-shot. |
| **3. Animation (still → video)** | **Kling 2.x** *primary*, **Runway Gen-4** as quality fallback. Worth testing **Motionfly.co** as well. | Kling currently has the best character-consistency on image-to-video. Runway is more cinematic but pricier. Motionfly is more template-driven. |
| **4. Editing** | DaVinci Resolve (free) or CapCut (free) | Stitch the clips, add intertitles, music, SFX. |

---

## Phase 1 — Build the character bible

> **This phase is what saves the whole project.** Don't skip it. Generating shots one-at-a-time straight from a single hero image is the most common failure mode — by shot 8 the character has visibly drifted. The character bible is unglamorous, slow work, but it's what makes 21 shots look like the same beaver.

Open [aistudio.google.com](https://aistudio.google.com), pick the **Gemini 2.5 Flash Image** model, sign in with Google. Free tier works.

### Step 1.1 — Hero shot (the master reference)

Upload `src/static/images/castor-logo-nobg.png`. Prompt:

> *"Inspired by the geometric beaver in this logo, design a full-body **Pixar-style cartoon mascot**. Chubby plush proportions, big round head, large expressive eyes, prominent bucked teeth, a wide flat tail. Keep the **blue and purple palette** from the logo — primarily Castor blue `#3b82f6`. Add a tiny yellow construction hard hat with a Castor-blue stripe, and a Castor-blue scarf. Confident, friendly expression. Three-quarter front view, full body, standing pose, clean white background. Soft cartoon shading, no harsh shadows."*

Generate 6–8 variations. Pick your favorite. **Save it as `castor-mascot-master.png`.** This is your bible image.

### Step 1.2 — Turnaround sheet

Upload `castor-mascot-master.png` as reference. Prompt:

> *"Using this exact character as the reference, generate a 5-pose **character turnaround sheet**: front, three-quarter front, side profile, three-quarter back, back. Identical character design — same proportions, same hat, same scarf, same colors, same expression. Lined up in a row on a clean white background. No text labels."*

Iterate 3–5 times until the character is recognizably the same across all five angles.

### Step 1.3 — Expression sheet

> *"Same character, same pose (three-quarter front, head and shoulders crop), six expressions: 1) neutral calm, 2) confident smile, 3) wide-eyed surprised, 4) focused/concentrating with eyebrow raised, 5) smug wink, 6) triumphant thumbs-up. Six panels, identical character design. No text labels."*

### Step 1.4 — Action pose sheet

> *"Same character, full-body action poses: 1) walking confidently, 2) chomping on a glowing data shard, 3) building a dam with logs, 4) tipping his hard hat to camera, 5) holding a tiny chisel, 6) giving a thumbs-up to camera. Six panels, identical character. No text labels."*

### Step 1.5 — Split each sheet into single-pose files

**This is critical for downstream consistency.** Sheets are good for human review and for ensuring internal consistency at generation time — but when you later use one as a reference for a scene generation, the model has to guess which of the 6 panels to pull style from. Always work from single-pose files.

Two ways to split:

**Option A — let Nano-Banana do it:**

Upload the sheet. Prompt:

> *"Extract each of these 6 poses as its own separate image on a clean white background. Identical character design preserved exactly. No text labels, no borders, no panel numbers."*

**Option B — crop manually** in any image editor (Photoshop, GIMP, Photopea, even PowerPoint). Faster and more precise.

**Rules for the single-pose files:**

- **Clean white background.** No backdrop, no shadow contamination.
- **No text labels in the image.** AI models sometimes try to reproduce embedded text in derivative scenes — you'll get garbled "fronT vieW" text floating in your office shots. Don't take the risk.
- **Descriptive filenames** carry the labeling job. Suggested structure:

```
character-bible/
├── castor-mascot-master.png        ← THE hero reference. Lock this.
├── turnaround/
│   ├── castor-front.png
│   ├── castor-three-quarter-front.png
│   ├── castor-side.png
│   ├── castor-three-quarter-back.png
│   └── castor-back.png
├── expressions/
│   ├── castor-neutral.png
│   ├── castor-confident.png
│   ├── castor-surprised.png
│   ├── castor-focused.png
│   ├── castor-smug-wink.png
│   └── castor-triumphant.png
└── actions/
    ├── castor-walking.png
    ├── castor-chomping.png
    ├── castor-dam-building.png
    ├── castor-hat-tip.png
    ├── castor-chisel.png
    └── castor-thumbs-up.png
```

You now have ~18 single-pose reference images that depict the **same beaver** from every angle in every relevant pose. This is the character bible.

---

## Phase 2 — Generate the 21 scene stills

For each shot in the screenplay:

1. Pick the most relevant pose image from `character-bible/`.
2. Upload **`castor-mascot-master.png` + the matching pose image** as references (Nano-Banana accepts multiple references).
3. Prompt with the shot description from the screenplay.
4. Generate 4–6 takes per shot, pick best.
5. Save as `shot-stills/shot-09-castor-walks-in.png` (descriptive filename).

**Example — Shot 9 (Castor walks into the chaotic office):**

References attached: `castor-mascot-master.png` + `castor-walking.png`.

Prompt:

> *"Using these two reference images of the character, generate a **wide shot**: this beaver mascot walking calmly through the front door of a chaotic architecture office. The office is in disarray — papers flying, blueprints on the floor, an orange-red tornado of glowing acronyms (`IFCSPACE`, `Pset`, `QTO`, `BIM`, `IFCWALL`) swirling in the background. Three architects huddled behind an overturned drafting table, looking terrified. Castor is calm, unhurried, hat low over eyes, blue scarf trailing behind him. Pixar-meets-8-bit cartoon style, soft cartoon shading, bright friendly palette except for the angry orange-red tornado. No text in the image."*

Repeat for all 21 shots.

**Volume:** ~21 stills × 5 takes ≈ 100 generations.
**Cost estimate:** ~€4–€10 via the Gemini API at ~€0.04/image, or 1–2 days inside the AI Studio free tier.

---

## Phase 3 — Animate the stills

Image-to-video, one clip per shot. For each still, generate a 3–8 second clip.

### Recommended test before committing

**Pick 3 representative shots** and animate the same still in **both Kling 2.x and Runway Gen-4**. Character drift varies wildly between tools, and your specific mascot design will favor one. Spend a week. Cancel the one that loses.

| Tool | URL | Approx. cost | Strengths |
|---|---|---|---|
| **Kling 2.x** | klingai.com | ~€15/mo (Pro tier) | Best character consistency on image-to-video as of 2026. Good motion fidelity. |
| **Runway Gen-4** | runwayml.com | ~€15–€90/mo | More cinematic, better camera moves, but pricier and slower iteration. |
| **Motionfly.co** | motionfly.co | varies | Template-driven, less character-focused — test for the wide office shots, not the close-ups on Castor. |
| **Luma Dream Machine** | lumalabs.ai | ~€10/mo | Cheap and fast, decent quality, useful for B-roll/transitions. |

### Workflow per shot

1. Upload the still (e.g., `shot-09-castor-walks-in.png`).
2. Describe the **motion** (not the scene — the still already defines the scene):

   > *"Beaver walks forward into frame from the door, slow confident gait. He tips his hat to camera. One eyebrow raises. Tornado swirls in background. Static camera."*

3. Generate, regenerate if character drift is too strong (3–5 takes per shot is normal).
4. Pick best take, export as `shot-clips/shot-09-castor-walks-in.mp4`.

**Clip length per shot:** match the screenplay beat sheet — most shots are 4–8 seconds. The chomp montage shots (Shot 11) can be shorter (~3s each) and stitched fast in the edit.

---

## Phase 4 — Edit

### Software

- **DaVinci Resolve** (free, professional-grade) — recommended if you have any video-editing intuition.
- **CapCut** (free, beginner-friendly) — fine if Resolve feels heavy.

### Assemble the timeline

1. Drop all 21 clips into the timeline in screenplay order.
2. Add **intertitle cards** between shots where the screenplay specifies them:
   - Black background, white serif text (Playfair Display or Linotype Didot — both free or cheap).
   - 0.2s fade-in, ~1.5s on screen, 0.2s fade-out.
   - Resolve and CapCut both have title templates that do this in one click.
3. Drop in the **chiptune track** as the main audio bed. Royalty-free sources:
   - Pixabay Music ([pixabay.com/music](https://pixabay.com/music))
   - Free Music Archive ([freemusicarchive.org](https://freemusicarchive.org))
   - Incompetech (Kevin MacLeod, search "chiptune" or "8-bit")
4. Layer **SFX** from a royalty-free pack:
   - Chomp, burp, ding (Mario-coin style), hat-tip whoosh, door creak, commit-stamp clunk, tornado roar
   - Sources: Zapsplat (free with account), Freesound.org
5. Record the **single VO line** from the Client (Shot 14):
   > *"Castor… can you also change something?"*
   - Hire on Fiverr (~€30–€80) or record yourself if you have a usable mic and English-speaking voice.

### Export

- **Master:** 1920×1080, H.264, 30fps, ~10–20 Mbps bitrate.
- **Vertical cut for social:** Re-crop to 1080×1920, 9:16. Most modern editors do this with one click using auto-reframing.

---

## Cost ceiling (full pipeline)

| Item | Estimate |
|---|---|
| Google AI Studio (Nano-Banana, free tier or API) | €0–€10 |
| Kling Pro (1 month) | ~€15 |
| Runway Standard (1 month, for the test) | ~€15 |
| Royalty-free music + SFX | €0 |
| VO recording (single line) | €30–€80 |
| Editing software | €0 |
| **Total** | **~€60–€120** |

Cheaper than commissioning a 30-second clip from a freelance animator on Fiverr (~€300+).

---

## Sequence at a glance

```
src/static/images/castor-logo-nobg.png       ← what you have today
        ↓ Nano-Banana, hero generation
character-bible/castor-mascot-master.png     ← THE bible image (lock this)
        ↓ Nano-Banana, turnaround / expression / action sheets
        ↓ split into single-pose files
character-bible/turnaround/, expressions/, actions/   ← ~18 single-pose references
        ↓ Nano-Banana, per-shot generation
shot-stills/ (~21 stills)
        ↓ Kling / Runway, image-to-video
shot-clips/ (~21 short videos)
        ↓ DaVinci Resolve / CapCut, intertitles + music + SFX + VO
castor-vs-jargon-beast.mp4                   ← final cut
```

---

## Hard rules (don't break these)

1. **Never skip the character bible (Phase 1).** Drift is the #1 killer of AI-animated mascot work.
2. **Always work from single-pose reference files, not sheets**, when generating downstream shots.
3. **No text labels baked into reference images.** AI models try to reproduce them. Filenames do the labeling.
4. **Test 2 animation tools on the same still before buying a full month** of either.
5. **Carry the logo's palette and attitude, not its faceted geometry.** Don't fight the medium.
6. **Don't generate audio with AI.** Royalty-free chiptune + a single human VO line will sound infinitely better than AI-generated music in 2026.

---

## When something goes wrong

- **Character drifts between shots →** regenerate that shot with stricter reference adherence (attach 2-3 reference images, not 1, and add "preserve exact character design from references" to the prompt).
- **Jargon-tornado looks too consistent across shots →** good. Embrace the morph. The chaos is the point.
- **The IFC cube changes color or shape →** lock it the same way you locked Castor. Generate a master cube reference image early.
- **Animation clip is too short to cover the beat →** stitch two short clips of the same shot back-to-back with a subtle cross-dissolve, or use the first half + reverse the second half (works for ambient motion like the tornado).
- **Final cut is over 2:30 →** ruthlessly trim Shot 5 (chaos montage) and Shot 11 (chomp montage). Both are designed to be elastic.
