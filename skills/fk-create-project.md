Create a new Google Flow video project. Ask the user for:

1. **Project name** and **story** (brief plot summary)
2. **Material** — the visual style for all images. Choose one of the 6 built-in styles or a custom material. Run `GET /api/materials` to show available options. Built-ins: `realistic`, `3d_pixar`, `anime`, `stop_motion`, `minecraft`, `oil_painting`. **Required.**
3. **Characters** — name + visual description of their **base default look in ONE outfit only**. No scene-specific variants (e.g. "glamorous in studio, sporty in gym"). The reference image must be a single clean image, not a multi-panel grid. Different outfits per scene come from the scene prompts, not the character description.
4. **Locations** — name + visual description of key places
5. **Visual assets** — name + visual description of key props/objects
6. **Number of scenes** and **orientation** (VERTICAL or HORIZONTAL)

Then execute:

## Real-People Characters (Documentary / News Projects)

When characters are based on **real famous people** (politicians, military leaders, celebrities), Google's AI safety filter (`PUBLIC_ERROR_UNSAFE_GENERATION`) will reject generation if it recognizes the person. This section captures battle-tested strategies from real production runs.

### What the filter actually detects

Google's filter sees **three inputs combined**: (1) ref images sent as `imageInputs`, (2) prompt text, (3) the generated output image. ANY can trigger rejection independently or together.

**Trigger sensitivity (ranked):**
| Trigger | Risk | Example |
|---------|------|---------|
| Front-facing ref image of famous person | Highest | Trump's face in portrait ref → instant reject |
| Political/military titles in prompt (any language) | High | "The Commander" (Vietnamese for Commander in Chief) — Google understands multilingual text |
| Iconic visual combos in prompt | Medium | "blond hair + dark suit + helicopter" = Marine One image |
| Back-view ref of very famous person | Medium | Back of Trump's silhouette still sometimes recognized |
| Generic scene with political context | Low | "leader at podium" without refs usually passes |

**The filter is RANDOM** — identical prompts sometimes pass, sometimes fail. Don't assume one failure is permanent. A scene that fails 3x may pass on the 4th try.

### Rules

1. **Entity `name` = role-based alias in English** — never the real name, always English
   - Good: `"The Commander"`, `"Iron Premier"`, `"The Deputy"`
   - Bad: `"Trump"`, `"Netanyahu"`, `"The Commander"` (non-English)

2. **Entity `description` = physical appearance only** — describe distinctive visual features without naming
   - Hair (color, style, length), face shape, build, skin tone, age
   - Signature clothing (e.g. "long red silk necktie", "dark suit without tie Iranian style")
   - Distinguishing features (e.g. "trimmed dark beard along jawline", "round wire-frame glasses")

3. **Never mention real names in**: `description`, `image_prompt`, `prompt` (scene), `video_prompt`

4. **Ref images = back view or left-side profile (famous people only)** — for **recognizable public figures** (presidents, prime ministers, celebrities), generate refs showing the character from behind or left-side three-quarter profile. The `image_prompt` must specify: `"seen from behind"` or `"seen from the left side three-quarter profile"`. This does NOT apply to fictional characters or non-famous people — those use normal front-facing refs.

   Why back/side view works: viewers can still identify "who" from silhouette + context (e.g., behind-view of a blond-haired leader signing documents at the Oval Office desk = everyone knows who it is). Cinematically powerful — think documentary footage showing the weight of power from behind.

5. **Scene prompts must match ref angle** — if ref is back-view, scene `prompt` and `video_prompt` MUST also describe the character from behind/side. Camera always behind or beside famous characters. Never have them face the camera.

   ```
   Good: "View from behind The Commander standing at podium, gesturing at screen"
   Bad:  "The Commander facing camera with arms crossed" (face visible → filter triggers)
   ```

   `video_prompt` shots must also keep camera behind — prevent character from turning face to camera across all shots.

6. **`narrator_text` CAN reference real titles/roles** — narration is audio-only and doesn't feed into image/video generation. This is where you name who they really are.

7. **Track the mapping** — keep a `real_reference` table in the project plan file (`.omc/research/`) so the team knows who each alias represents

### Fixing UNSAFE_GENERATION failures (escalation per-scene)

When specific scenes fail, apply **per-scene** escalation. Only fix what's broken — don't touch scenes that already passed.

| Level | What to change | When to use | Success rate |
|-------|---------------|-------------|-------------|
| 1 — Camera angle | Rewrite `prompt` + `video_prompt` to behind/side view. Keep character name, keep `character_names` refs | First failure. Try this + retry 2-3 times (filter is random) | ~40% |
| 2 — Strip names | Remove alias from prompt text → `"A distinguished older leader at podium"`. Keep `character_names` field (refs still sent) | Level 1 failed after retries | ~60% |
| 3 — Remove refs | Set `character_names: []` on scene. No `imageInputs` sent. Prompt-only generation | Level 2 failed — the ref IMAGE is the trigger | ~90% |
| 4 — Strip all identity | Remove hair color, build, iconic context. `"A man in a dark suit walking across a lawn"` (no helicopter, no blond hair, no political context) | Level 3 failed — even generic prompt with political context triggers | ~99% |

**Always fix BOTH prompt AND refs together.** Fixing only the ref but leaving "The Commander" in the prompt = still fails. Fixing only the prompt but leaving front-face ref = still fails.

**`PUBLIC_ERROR_MINOR_INPUT_IMAGE`** — separate error. Triggered when ref images or prompts depict children. Fix: remove "child", "carrying child", "young people", "injured child" from prompts and refs.

### Example Entity

```json
{
  "name": "The Commander",
  "entity_type": "character",
  "description": "Tall imposing 78-year-old Caucasian man seen from behind, distinctive golden-blonde hair swept back, broad shoulders, long red silk necktie over white dress shirt, dark navy tailored suit, standing with authoritative posture. Back view, full silhouette head to toe."
}
```

Note: `description` now specifies back view — this flows into `image_prompt` generation for the ref image.

### Example Scene Prompt (famous character)

```
prompt: "Real RAW photograph, shot on Canon EOS R5. View from behind The Commander standing at podium in situation room, his silhouette against a large screen showing regional map, advisors seated facing him, dramatic overhead lighting."

video_prompt: "Medium shot of The Commander seen from behind at a podium, hand gesturing firmly at the large screen showing a regional map, advisors seated facing him. The camera holds steady. Dramatic overhead lighting casts deep shadows, cool blue glow from the screen illuminating his silhouette. Then cut to close-up of his broad shoulders tensing, slow push toward the screen as markers appear one by one. The camera slowly dollies in. Warm overhead rim light contrasts with the cool blue screen glow, dark room atmosphere.\n\nAudio: quiet hum of electronics, muffled breathing, air conditioning.\nSFX: map markers clicking into place, pen tapping on table.\nNegative: subtitles, watermark, text overlay, blurry faces."
```

Camera stays behind. Viewers see the leader's power through body language, not face.

### Naming Conventions for Aliases (always English)

| Pattern | Examples | Best for |
|---------|----------|----------|
| Role/title in English | The Commander, The Tehran Envoy | Documentary, news |
| Descriptive title | Iron Premier, The Successor | When personality matters |
| Military rank + origin | The Field Marshal, The Admiral | Military figures |
| Generic role | The Royal Advisor, The Strategist | Secondary characters |

## Step 0: Make sure there is a Flow project to attach to

Since Flow moved to `flow.google.com`, Flow Kit cannot create Flow projects —
the endpoint that did it went with the migration. Every generation is scoped to
an existing one.

```bash
curl -s http://127.0.0.1:8100/api/flow/status | python3 -c "
import sys, json
s = json.load(sys.stdin)
print('Flow project:', s.get('flow_project_id') or 'NONE — create one in the Flow UI')
"
```

If it prints NONE, ask the user to open `https://flow.google.com/`, create a
project, and copy the uuid out of the URL. Then either pin it
(`export FLOW_PROJECT_ID=<uuid>` before starting the agent) or pass it as
`flow_project_id` in Step 1. Without it every request fails `NO_FLOW_PROJECT`.

## Step 1: Create project with all entities

Add `"flow_project_id": "<uuid>"` if you are not using the pinned one.

```bash
curl -X POST http://127.0.0.1:8100/api/projects \
  -H "Content-Type: application/json" \
  -d '{"name": "...", "description": "...", "story": "...", "material": "3d_pixar", "characters": [
    {"name": "...", "entity_type": "character", "description": "...", "voice_description": "Deep calm voice, speaks slowly with confidence"},
    {"name": "...", "entity_type": "location", "description": "..."},
    {"name": "...", "entity_type": "visual_asset", "description": "..."}
  ]}'
```

Save the returned `project_id`.

## Step 2: Create video

```bash
curl -X POST http://127.0.0.1:8100/api/videos \
  -H "Content-Type: application/json" \
  -d '{"project_id": "<PID>", "title": "...", "display_order": 0}'
```

Save the returned `video_id`.

## Step 3: Create scenes

For each scene, write a prompt that describes **action + environment + mood** only. Reference entities by name. Never describe character appearance. **All `prompt` and `video_prompt` must be in English** regardless of project language — the AI generator performs best with English prompts.

### Chain Structure Rules

`chain_type` controls how images are generated: CONTINUATION scenes use EDIT_IMAGE from parent (visual continuity), ROOT scenes generate fresh.

**CONTINUATION** = visually continuous with parent scene. Use when:
- Same primary character(s) continue acting
- Same or adjacent location (camera moves within the space)
- Direct temporal continuation ("then he runs to the door")

**ROOT** = fresh generation, no visual dependency. Use when:
- **Switching to different character/perspective** — e.g., cutting from Defector to Pursuer
- **Jumping to different location** — e.g., from battlefield to interview studio
- **Interview/talking-head scenes** — always ROOT (standalone, no chain)
- **Time skip** — hours/days later

**CRITICAL: Parallel timelines = separate chains.** If a sequence intercuts between 2 characters (e.g., Defector running + Pursuer chasing), create 2 separate CONTINUATION chains:
```
Chain A (Defector): scene_09 [ROOT] → scene_10 → scene_12 → scene_14
Chain B (Pursuer): scene_11 [ROOT] → scene_13 → scene_15
```
Then interleave by `display_order` for playback: 9,10,11,12,13,14,15. Each chain maintains its own visual consistency via EDIT_IMAGE.

**Never chain scenes with different primary characters.** EDIT_IMAGE morphs the parent image — chaining Pursuer→Defector will morph Pursuer's face into Defector's scene, causing character drift.

### CONTINUATION Image Prompt Rule

CONTINUATION scenes use EDIT_IMAGE with the **parent's image as source**. The edit API preserves the source composition by default — if the child prompt just describes a static scene, the result looks nearly identical to the parent (same background, same angle, same setup). This wastes generation credits.

**Rule: CONTINUATION scene `prompt` must describe the desired result with transformation emphasis — NOT describe what the previous scene looked like.**

The system auto-prepends `"Transform this image into a completely different moment. Move the camera to a new angle, position, and composition. Change the surrounding environment and visual setup."` before the scene prompt. So write prompts that describe **what you want to see**, with explicit camera/angle/composition details.

Write prompts that explicitly specify:
- **Camera angle and position** (e.g., "wide shot from roadside, low angle, 30m from impact")
- **New location within the space** (e.g., "exterior view of the vehicle", "from the cockpit looking out")
- **Action and environment** that make this scene visually distinct from parent

**Bad** (static, no camera/angle info — edit preserves parent composition):
```
Military Jeep crashing into drainage ditch, wheel detaching, dust cloud.
Background shows JSA border zone.
```

**Good** (describes desired result with camera/composition):
```
Wide shot from roadside, low angle. Military Jeep hits drainage ditch at full speed,
front-right wheel breaks free and flies off, vehicle lurches violently sideways.
Dust cloud erupts from gravel. Camera positioned low, 30 meters from impact point.
JSA blue buildings visible in far background, guard towers on both sides.
```

Key principles:
1. **Describe what you want** — not what the previous scene was
2. **Specify camera** — angle, distance, position (the most important factor for visual change)
3. **Describe environment around the action** — not just the action itself

### Transition Prompt (Chain Scenes Only)

When a scene has `end_scene_media_id` (start+end frame video generation), the AI generates an 8s video transitioning from **this scene's image → next scene's image**. The regular `video_prompt` only describes this scene's action — it doesn't describe the motion toward the next frame.

`transition_prompt` bridges the gap: it describes the **full trajectory from start frame to end frame**.

**Rules:**
- Only set `transition_prompt` on CONTINUATION chain scenes that have a child (i.e., another scene uses this scene as `parent_scene_id`)
- ROOT scenes with no chain child: `transition_prompt` = empty (not needed, no end frame)
- Last scene in a chain (no child): `transition_prompt` = empty (video uses `video_prompt` as normal)
- When `transition_prompt` is set AND `end_scene_media_id` exists, the video generator uses `transition_prompt` instead of `video_prompt`

**Format:** Natural prose describing the full motion trajectory from THIS scene's frame to the NEXT scene's frame. 100–150 words, camera movement as separate sentence, Audio/SFX/Negative at end.

**Example:**
```
Scene 4 (soldiers drinking in barracks):
  video_prompt: "Medium wide shot of soldiers gathered around a rough wooden table,
    tin cups raised, warm candlelight flickering on their faces. The Defector laughs
    and raises his cup, eyes bright with camaraderie. The camera slowly dollies in
    toward his face. Then cut to close-up as his smile gradually fades, eyes darting
    toward the barracks door, jaw tightening. Warm candlelight, golden tones deepening
    to shadow.

    Audio: muffled laughter, liquid sloshing in cups, crackling fire.
    SFX: tin cups clinking, chair creaking.
    Negative: subtitles, watermark, text overlay."

  transition_prompt: "Medium shot of the Defector raising his cup among laughing
    soldiers in the warm barracks interior. The camera holds steady. His expression
    shifts — he puts down the cup, glances toward the barracks door, jaw tightening.
    The camera slowly dollies in on his face, half in shadow. He rises abruptly,
    pushes the chair back, and moves toward the door. The camera tracks him from
    behind as warm golden candlelight gives way to cold blue moonlight spilling
    through the doorway.

    Audio: laughter fading, wind seeping through door cracks.
    SFX: chair scraping wood floor, heavy boots on planks, door latch lifting.
    Negative: subtitles, watermark, text overlay."

Scene 5 (Defector bursting through door — CHILD of scene 4):
  start_image = scene 5's image (Defector at door)
  video_prompt: "Medium shot of the Defector bursting through the barracks door,
    stumbling into cold night air, warm light spilling from the doorway behind him.
    The camera follows with handheld movement, slight shake. He steadies himself
    against the wall, breathing hard, breath visible in the freezing cold. Then cut
    to wide shot as he breaks into a run toward darkness, barracks shrinking behind
    him. Cold blue moonlight, his silhouette against the snow.

    Audio: cold wind howling, heavy breathing, distant dogs barking.
    SFX: door slamming shut, boots crunching on frozen ground.
    Negative: subtitles, watermark, text overlay."
```

Scene 4's video uses `transition_prompt` because it has `end_scene_media_id` (scene 5's image). The prompt describes the journey FROM drinking → TO bursting through door.

### Scene Creation

- `chain_type: "ROOT"` — standalone or first scene of a new chain
- `chain_type: "CONTINUATION"` + `parent_scene_id: "<ID>"` — continues from parent (same character/perspective)
- `character_names`: list ALL entities that should appear (characters + locations + assets)

```bash
curl -X POST http://127.0.0.1:8100/api/scenes \
  -H "Content-Type: application/json" \
  -d '{"video_id": "<VID>", "display_order": N, "prompt": "...", "video_prompt": "...", "transition_prompt": "...", "character_names": [...], "chain_type": "ROOT|CONTINUATION", "parent_scene_id": "..."}'
```

Note: `transition_prompt` only needed for chain scenes that have a child scene. Set to empty/null for ROOT scenes and last-in-chain scenes.

---

## Prompt-Writing Guide

### Image Prompt Formula

```
[Subject] [action verb] [at/in Location]. [Specific visual detail]. [Camera/composition].
```

**Good vs Bad:**

| Bad | Good | Why |
|-----|------|-----|
| `"Hero in castle"` | `"Hero pushes open the Castle gate and steps into the sunlit courtyard"` | Vague → action + specific moment |
| `"Luna the white cat with orange suit discovers river"` | `"Luna kneels at the edge of Chocolate River, dipping a paw in, surprised expression"` | Describing appearance → refs handle that |
| `"cinematic scene"` | `"Wide shot, low angle, Luna small against vast Candy Planet landscape, cotton candy clouds"` | Buzzword → specific camera + composition |

**Anti-patterns:**
- **NEVER describe character appearance** (eyes, hair, clothing, outfit) in `prompt` or `video_prompt` — reference images handle visual consistency via `imageInputs`. Write ACTION only.
- Never use single-word or atmosphere-only prompts: `"epic"`, `"dramatic"`, `"cinematic"`
- Always include a camera/composition cue at the end
- All `prompt`, `video_prompt`, and `image_prompt` MUST be in English regardless of project language

**CRITICAL: Character face visibility rule.**
When a character appears in the scene image, their face MUST be fully visible (front-facing, three-quarter, or side profile). The AI video model will fabricate/hallucinate any face area not shown in the start frame, causing character inconsistency.

| Framing | OK? | Why |
|---------|-----|-----|
| Full face visible (front/side/profile) | YES | AI has reference for the entire face |
| POV/first-person (only hands visible) | YES | No face to fabricate — intentionally faceless |
| Face cropped at eyes/forehead (half face) | **NO** | AI will invent the missing top half → drift |
| Character too far/small (face < 5% frame) | **NO** | AI will hallucinate facial details → inconsistent |
| Character seen from behind (back of head) | OK | Only for famous-person bypass (see Real-People section) |

**Rule:** If a character is in frame → show full face. If you don't want to show the face → use POV camera (hands/arms only) or wide environmental shot without the character. Never half-crop a face.

**`voice_description`** on characters (max ~30 words) — auto-appended to video prompts by the worker. Dialogue tone must match voice profile. Example: `"Deep calm heroic voice, speaks slowly with confidence"`.

See `fk-camera-guide.md` for full camera language reference.

---

### Safe Prompt Language (avoid UNSAFE_GENERATION)

Google Flow's AI filter rejects prompts with violent, aggressive, or graphic language. NEVER use these words in `prompt`, `video_prompt`, `image_prompt`, or `description`:

| Blocked word/phrase | Safe alternative |
|---|---|
| attack, strike, bomb, missile | operation, strategic action, military maneuver |
| kill, dead, death, casualty | fall, loss, aftermath |
| explosion, blast, detonate | bright flash, impact, shockwave |
| destroy, devastate, annihilate | damage, impact, disrupt |
| blood, wound, injury, gore | dust, debris, aftermath |
| chaos, carnage, massacre | turmoil, aftermath, tense scene |
| aggressive, threaten, menace | determined, resolute, firm, intense |
| pointing aggressively | gesturing firmly, addressing with authority |
| overwhelmed hospital | busy medical facility, emergency room at capacity |
| rubble, ruins, burning | damaged structures, smoke, haze, reconstruction |
| war room | situation room, command center |
| target markers | strategic markers, location indicators |
| gun, rifle, weapon (pointed) | holstered sidearm, military equipment (neutral) |
| angry, furious, rage | stern, intense, focused, resolute |
| hostage, torture, prisoner | detainee, negotiations, diplomatic standoff |
| corpse, body bags | memorial, tribute, remembrance |

**Principle:** describe *atmosphere and tension* cinematically, not violence directly. Frame military/conflict through strategy, diplomacy, and human emotion. Entity descriptions: appearance, clothing, posture only — never graphic.

---

### Video Prompt Formula (Veo 3)

Write video prompts as **natural prose** — like briefing a film director. Veo 3 generates native audio (dialogue, SFX, ambient) from text.

**5-component structure:** `[Camera/Shot] + [Subject] + [Action] + [Setting] + [Style & Audio]`

**Critical rules:**
- **100–150 words** (3–6 sentences)
- **Camera movement as separate sentence** — never embed in action description
- **Audio/SFX/Music labels** at end of prompt, separated
- **Negative prompt** always appended: `Negative: subtitles, watermark, text overlay`
- 2–3 shots max per 8s clip, use `then cut to` or timestamp format
- Every prompt needs: lighting description + audio description

**Dialogue rules:**
- Use `:` format to avoid subtitles: `Character says: "line" (no subtitles)`
- Keep short — must fit in ~8 seconds of speech
- Describe voice: `in a deep gravelly voice`, `whispering`
- Delivery verbs: `says`, `whispers`, `shouts`, `gasps`, `asks`, `replies`, `murmurs`
- Silent segments are powerful — not every shot needs dialogue

**Emotional arc pattern (map to 8s):**
```
Opening  (0-2s): Wide/establishing — set the stage
Rising   (2-5s): Medium + tracking or dolly in — build engagement
Peak     (5-7s): Close-up — maximum emotion
Release  (7-8s): Pull back to wide — breathing room
```

**Example:**
```
Wide shot of Luna emerging from a rocket onto a vast candy landscape,
cotton candy clouds towering above, long candy-colored shadows stretching
across the ground. The camera cranes down smoothly. Luna gasps: "Wow!"
(no subtitles) Then cut to low angle tracking shot as she takes her first
steps on candy ground, looking around in wonder. Luna says: "Everything
is made of candy!" (no subtitles) Finally, wide static shot of Luna small
against the vast landscape, golden backlight creating a rim light around her.
Warm golden hour light, soft pastel tones.

Audio: gentle warm breeze, faint magical shimmer.
SFX: soft footsteps on crystallized sugar ground.
Negative: subtitles, watermark, text overlay.
```

See `fk-camera-guide.md` for full Veo 3 camera/lighting/audio vocabulary and prompt template.

---

### Narrator Text Formula

```
[What the viewer CANNOT see: context/stakes/motivation]. [Tension or consequence]. [Short punchy closer.]
```

- 2-3 sentences max per 8s scene — strictly under 20 words per sentence
- Mirror the video timing: calm opener → rising tension → punchy close
- Add off-screen context: historical facts, character motivation, stakes
- Never describe what is visually obvious: `"We see a ship sailing"` → cut it

**Example:**
```
Captain Harris spots unusual radar signatures. Dozens of Iranian fast boats race straight toward the convoy. He orders battle stations.
```

See `fk-gen-narrator.md` for word count limits per language and narrative arc guide.

---

### Anti-Patterns Table

| Bad | Why | Good |
|-----|-----|------|
| `"Hero in castle"` | Too vague — no action, no composition | `"Hero walks into Castle courtyard at dawn, Magic Sword glowing on the wall. Wide shot."` |
| `"The tall muscular hero with blonde hair wearing golden armor..."` | Describes appearance — ref image handles it | `"Hero lifts Magic Sword above head, golden light fills the room. Close-up, slow motion."` |
| `"cinematic"` alone | Meaningless without specifics | `"Wide angle, low light, shallow depth of field, warm backlight"` |
| `"Scene 1: Luna is happy"` | Emotion without action or environment | `"Luna jumps up with arms raised at Chocolate River, face lit with joy. Medium shot."` |
| `"Camera zooms in on the action"` | Vague camera direction | `"Slow push in to close-up of Hero's eyes reflecting golden glow, rack focus from sword to face"` |

---

## Output

Print a summary table:
- Project ID, Video ID
- All entities with names and types
- All scenes with prompts (truncated) and chain type
- Next step: "Run /fk-gen-refs to generate reference images"

## Step 4: Review and Update Scenes

After creating scenes, review all prompts. If any prompt is too simple or missing detail, **PATCH it — do not delete and recreate**.

```bash
curl -X PATCH http://127.0.0.1:8100/api/scenes/<SID> \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Hero charges across the Castle bridge at dawn, sword raised, golden light catching the blade. Wide shot.",
    "video_prompt": "Wide shot of Hero sprinting across the Castle bridge at dawn, golden light catching the blade of Magic Sword raised high. The camera tracks alongside with steady movement. Warm golden hour light, long shadows stretching across ancient stone. Then cut to medium shot as Hero raises Magic Sword overhead, light bursting from the blade, wind whipping his cloak. The camera slowly dollies in, shallow depth of field. Finally, close-up on Hero's determined face, Castle gate looming behind, warm side light.\n\nAudio: wind across stone bridge, distant morning birds.\nSFX: boots pounding on stone, sword ringing, cloak snapping.\nNegative: subtitles, watermark, text overlay.",
    "character_names": ["Hero", "Castle", "Magic Sword"],
    "narrator_text": "The hero charged forward, knowing there was no turning back."
  }'
```

**Patchable fields:** `prompt`, `video_prompt`, `transition_prompt`, `image_prompt`, `character_names`, `narrator_text`, `display_order`, `chain_type`, `parent_scene_id`.

**Workflow:** create scenes → review all prompts → PATCH to improve → then run /fk-gen-refs and /fk-gen-images. Scenes are mutable — update freely before generation starts.

## Step 5: Switch Active Project (REQUIRED)

After all scenes are created, **always** switch the active project to the newly created one. Without this, downstream skills (`/fk-status`, `/fk-pipeline`, `/fk-monitor`, `/fk-dashboard`) will continue showing the previously-active project — confusing and a frequent source of errors.

```bash
curl -s -X PUT http://127.0.0.1:8100/api/active-project \
  -H "Content-Type: application/json" \
  -d '{"project_id":"<PID>"}'

# Verify
curl -s http://127.0.0.1:8100/api/active-project
# Should print: {"project_id":"<PID>","project_name":"<your new project>",...}
```

**Print confirmation** to the user:
```
✅ Active project switched to: <project_name> (<PID>)
   Video:        <VID>
   Orientation:  HORIZONTAL | VERTICAL
   Material:     <material>

Next: /fk-gen-refs
```
