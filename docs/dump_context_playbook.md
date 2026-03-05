# dump_context Playbook — Commands & Use Cases

> Drop this file into `docs/` or keep it alongside the command for quick reference.
> All commands assume you're in `src/` (where `manage.py` lives).
> Make sure venv is activated or use `uv run manage.py dump_context`
> **Pro tip:** Add `--compact` to any command below to save ~30-40% tokens.

---

## 📦 THE `--compact` FLAG — Use It Everywhere

Before diving into commands, know this: `--compact` is your best friend. It applies four optimizations at once with zero information loss for the LLM:

| Optimization | What it strips | Savings |
|---|---|---|
| Compact tree | `│ ├── └──` connectors → 2-space indent | ~21% on tree |
| Strip comments | `# comments` and docstrings from Python code | ~25% on code |
| Collapse blanks | Multiple blank lines → single | ~10% on code |
| Short headers | `======` banners → `--- file.py ---` | ~66% on headers |

**Combined: a 25k dump drops to ~18k.** Same information, fewer tokens.

```bash
# See the savings on your actual project
python manage.py dump_context --estimate --preset overview
python manage.py dump_context --estimate --preset overview --compact
```

From here on, every example works with or without `--compact`. Add it when you need to squeeze more into the context window.

---

## 🗺️ ORIENTATION — "I need to understand the project"

### 1. Project tree only
```bash
python manage.py dump_context --tree-only
python manage.py dump_context --tree-only --compact          # leaner tree
```
**When:** Starting a brand new LLM chat and you want it to understand how the project is organized before giving it any code. Also great for onboarding a human.
**Output:** Directory tree with line counts and token estimates per file. Zero code. Very cheap on tokens.

### 2. Tree + all docs (the briefing)
```bash
python manage.py dump_context --tree --docs all --compact
```
**When:** You want the LLM to have full architectural context — structure + all design docs — without any source code. Perfect first message in a planning/design session.
**Tokens:** ~4-6k with compact. Leaves tons of room for conversation.

### 3. Full structural map (the skeleton overview)
```bash
python manage.py dump_context --tree --skeleton --docs architecture --compact
```
**When:** You need the LLM to see every class, every method signature, every field — but not implementation details. The `architecture.md` doc ties it all together.
**Tokens:** ~10-18k with compact. Good for "where should I put this new feature?" conversations.

### 4. Overview preset (shortcut for #3)
```bash
python manage.py dump_context --preset overview
python manage.py dump_context --preset overview --compact    # recommended
```
**When:** Same as above but faster. Equivalent to `--tree --skeleton --docs all`.
**Tip:** If you always use compact, add `"compact": true` to the overview preset in `.dump_presets.json` and skip the flag.

---

## 🔧 FEATURE WORK — "I'm building something in a specific app"

### 5. Full code for one app, skeleton for the rest
```bash
python manage.py dump_context --skeleton --apps writeback --full-apps writeback --compact
```
**When:** You're actively coding in writeback and need the LLM to see full implementation there, but only the API surface of everything else. The **power move** for focused work.
**Tokens:** ~10-14k with compact. Best balance of depth vs. breadth.

### 6. Writeback preset (shortcut for #5 + docs)
```bash
python manage.py dump_context --preset writeback --compact
```
**When:** Same as above but also pulls in `docs/writeback/` and `docs/guardian.md` automatically. One command, perfect context for any writeback task.

### 7. Full code for one app only
```bash
python manage.py dump_context --apps writeback --compact
```
**When:** You only care about one app and don't need context from the rest. Quick, focused, low tokens.

### 8. Two apps, full code for the one you're editing
```bash
python manage.py dump_context --skeleton --apps writeback chat --full-apps writeback --compact
```
**When:** Your change touches writeback but also needs to understand chat's interface. Full code where you're working, skeleton for the dependency.

### 9. RAG pipeline work
```bash
python manage.py dump_context --preset rag --compact
```
**When:** Working on embeddings, document processing, or chat. Pulls full code for `embeddings/`, skeletons for `documents/` and `chat/`, plus the RAG pipeline doc.

### 10. IFC processor work
```bash
python manage.py dump_context --preset ifc --compact
```
**When:** Working on parsing, entity extraction, or validation. Full `ifc_processor/` code + the processor doc.

---

## 📊 DATA MODEL — "I need to understand the schema"

### 11. All models.py files
```bash
python manage.py dump_context --models-only
python manage.py dump_context --models-only --compact
```
**When:** You need the LLM to understand your data model across all apps. Highest information density in Django — models define everything.
**Tokens:** ~3-5k (compact strips docstrings from models too, but fields and relationships stay intact).

### 12. Models preset (models + architecture doc)
```bash
python manage.py dump_context --preset models --compact
```
**When:** Models + the architecture doc that explains relationships. Good for "help me add a new model" or "what's the relationship between X and Y?"

### 13. Models + specific app's full code

The natural instinct is `--models-only --full-apps writeback`, but these flags conflict. `--models-only` filters first 
and only grabs files named `models.py` — so `--full-apps` has nothing extra to include. You'd get every `models.py` 
but none of writeback's views, services, or templates.

**Option A: skeleton + full-apps (recommended)**
```bash
python manage.py dump_context --skeleton --full-apps writeback --compact
```
This gives you every model's fields and relationships as skeletons (which still show every field, FK, and Meta class), 
plus full writeback code. Covers 95% of use cases.

**Option B: two separate dumps**
```bash
python manage.py dump_context --models-only --compact -o models.txt
python manage.py dump_context --apps writeback --compact -o writeback.txt
```
Then paste both files into the LLM. The first has every model in full detail (docstrings, methods, everything),
the second has all of writeback. More tokens, but full model code everywhere.

The `-o` flag sets a custom output filename. Without it, the command auto-generates a timestamped name 
like `2026-02-19__14-30-45__dump-context__compact__models.txt`. With `-o models.txt`, you get a clean predictable 
name — easier to find and paste. Both files still go into the `_output/` folder.

---

## 🐛 DEBUGGING — "Something broke, help me fix it"

### 14. Files changed recently (git diff)
```bash
python manage.py dump_context --diff HEAD~3 --compact
```
**When:** "I made changes in the last 3 commits and something broke." Only includes files you touched — 
plus staged and unstaged changes. Minimal, surgical context.

### 15. Diff + docs for broader context
```bash
python manage.py dump_context --diff HEAD~5 --docs all --tree --compact
```
**When:** You want to show what changed AND give the LLM project context to reason about it. 
The tree helps it understand where the changed files sit.

### 16. Diff from a branch point
```bash
python manage.py dump_context --diff main --compact
```
**When:** You're on a feature branch and want to dump everything that differs from main. 
Perfect for "review my feature branch" sessions.

### 17. Debug preset
```bash
python manage.py dump_context --preset debug
```
**When:** Quick debug dump. Equivalent to `--diff HEAD~5 --docs all --tree --compact`. 
Compact is baked into this preset by default.

---

## 🔍 TARGETED SEARCH — "I need everything related to X"

### 18. Grep for a concept
```bash
python manage.py dump_context --grep ModificationProposal --compact
```
**When:** You need every Python file that mentions `ModificationProposal`. 
Great for understanding how a model/class is used across the codebase.

### 19. Grep + relevant docs
```bash
python manage.py dump_context --grep "RAV\|guardian\|verification" --docs guardian --compact
```
**When:** You want all code files mentioning RAV + the guardian doc. Focused research context.

**Note:** `--grep` only *includes* matching files — non-matching files won't appear, even in skeleton mode. 
Pair it with `--docs` for the broader context.

### 20. Grep with regex
```bash
python manage.py dump_context --grep "class.*Service" --compact
```
**When:** Find all service classes across the project. Regex makes this very flexible.

### 21. Grep for a specific property/field
```bash
python manage.py dump_context --grep "FireRating\|fire_rating" --compact
```
**When:** Tracing how a specific IFC property flows through the system — from parser to validator to UI.

---

## 📖 DOCS ONLY — "I need design context, not code"

### 22. All docs, nothing else
```bash
python manage.py dump_context --docs-only --compact
```
**When:** Architecture review, planning session, or design discussion. All docs, zero code, zero tree.
**Tip:** Add `--tree` if you also want the project tree for orientation.

### 23. Specific docs only
```bash
python manage.py dump_context --docs-only --docs architecture ifc-processor --compact
```
**When:** You need exactly these docs and nothing else. No `.md` extension needed, it fuzzy-matches.

### 24. Docs + tree (the planning combo)
```bash
python manage.py dump_context --docs-only --tree --docs all --compact
```
**When:** Full design context — tree for structure, docs for substance. Still no code.

### 25. Writeback subsystem docs
```bash
python manage.py dump_context --docs-only --docs writeback
```
**When:** Pulls everything under `docs/writeback/`. Folder names work as selectors.
```

Then bump old 25 to 26 and so on through the rest of the numbering.

Also add `docs_only` to the **Available preset keys** table:
```
| `docs_only` | bool | `true` |
```

And update the **TOKEN BUDGET CHEAT SHEET** — replace the `--tree-only --docs all` row with:
```
| `--docs-only` | ~3-8k | ~2.5-6k | Design discussions |
| `--docs-only --tree` | ~6-10k | ~5-8k | Planning with structure |

### 26. Docs + models (the design session combo)
```bash
python manage.py dump_context --models-only --docs architecture rag-pipeline guardian --compact
```
**When:** Planning a feature that touches RAG and Guardian. Data model + relevant design docs. No implementation noise.

---

## 📏 ESTIMATION — "How big would this dump be?"

### 26. Estimate before dumping
```bash
python manage.py dump_context --estimate --apps writeback
python manage.py dump_context --estimate --apps writeback --compact
```
**When:** Compare both to see how much `--compact` saves for your app. No file written.

### 27. Estimate a preset
```bash
python manage.py dump_context --estimate --preset overview
python manage.py dump_context --estimate --preset overview --compact
```
**When:** Checking if a preset fits your context window. Run both to see the difference.

### 28. Estimate full project
```bash
python manage.py dump_context --estimate
```
**When:** Baseline measurement. "How big is my whole project in tokens?"

---

## ⚡ SPECIFIC FILE TYPES — "I only need certain files"

### 29. Python only
```bash
python manage.py dump_context --types py --compact
```
**When:** Skip templates, CSS, JSON. Just Python.

### 30. Views and serializers only
```bash
python manage.py dump_context --files views.py serializers.py urls.py --compact
```
**When:** API review. Just the request-handling layer.

### 31. Templates only
```bash
python manage.py dump_context --types html --apps writeback
```
**When:** Working on writeback UI. Just the templates. (`--compact` won't strip HTML comments, only Python.)

---

## 🧩 ADVANCED COMBOS — "I know exactly what I need"

### 32. New feature planning
```bash
python manage.py dump_context --tree --skeleton --docs architecture --compact
```
**When:** "Where should I build this and what's the data model?" Tree for structure, skeletons for API surface,
architecture doc for design philosophy.

### 33. Code review context
```bash
python manage.py dump_context --diff main --tree --docs conventions --compact
```
**When:** Before asking an LLM to review your feature branch. Shows what changed, project structure, and coding standards.

### 34. Refactoring investigation
```bash
python manage.py dump_context --grep "service\|Service" --docs conventions architecture --compact
```
**When:** "Help me refactor the service layer." All service-related code + the design docs that define how services 
should work.

### 35. Narrowest possible context
```bash
python manage.py dump_context --apps writeback --files services.py --compact
```
**When:** You need exactly one file from one app, full code. Minimal tokens for a focused question.

---

## 📋 PRESETS — All Defined in `.dump_presets.json`

Presets live in a single file: `.dump_presets.json` in your project root (next to `pyproject.toml`). 
No presets are hardcoded in Python — this file is the single source of truth. Edit it freely.

### 36. First time setup
```bash
python manage.py dump_context --init-presets
```
**When:** You don't have a `.dump_presets.json` yet, or you want to reset it to defaults. 
The command also auto-creates the file the first time you use any `--preset` or `--list-presets`, so this is optional.

### 37. List all presets
```bash
python manage.py dump_context --list-presets
```
**When:** "What presets do I have and what do they expand to?" Shows every preset from the JSON file.

### 38. Default presets (what ships out of the box)

| Preset | What it does |
|---|---|
| `writeback` | Full writeback code + skeleton for the rest + writeback & guardian docs |
| `overview` | Tree + skeleton of everything + all docs |
| `models` | All `models.py` files + architecture & data-models docs |
| `rag` | Full embeddings + skeleton for documents & chat + rag-pipeline doc |
| `ifc` | Full ifc_processor + skeleton + ifc-processor doc |
| `frontend` | Core, environments & chat templates/CSS/JS + ui-ux doc |
| `debug` | Diff HEAD~5 + all docs + tree + compact (all baked in) |

### 39. Add your own preset

Open `.dump_presets.json` and add an entry:
```json
{
  "_comment": "Keys starting with _ are ignored — use for notes",

  "tier2": {
    "apps": ["writeback"],
    "skeleton": true,
    "full_apps": ["writeback"],
    "docs": ["writeback", "guardian"],
    "grep": "tier.?2|Tier 2|operation.?plan",
    "compact": true
  }
}
```
Then:
```bash
python manage.py dump_context --preset tier2
```
**When:** You work on the same subsystem repeatedly. Save the combo once, reuse forever.

### Available preset keys

Any CLI flag works as a preset key (use `_` not `-`):

| Key | Type | Example |
|---|---|---|
| `apps` | list | `["writeback", "chat"]` |
| `skeleton` | bool | `true` |
| `full_apps` | list | `["writeback"]` |
| `models_only` | bool | `true` |
| `files` | list | `["views.py", "urls.py"]` |
| `types` | list | `["py", "html"]` |
| `tree` | bool | `true` |
| `docs` | list | `["all"]` or `["architecture", "writeback"]` |
| `diff` | string | `"HEAD~5"` or `"main"` |
| `grep` | string | `"ModificationProposal"` |
| `compact` | bool | `true` |

**CLI flags always override preset values** — so `--preset overview --compact` adds compact even if the overview preset 
doesn't include it.

---

## 💡 TOKEN BUDGET CHEAT SHEET

| Command Pattern | Normal | With `--compact` | Good For |
|---|---|---|---|
| `--tree-only` | ~1-2k | ~0.8-1.5k | Orientation only |
| `--models-only` | ~3-5k | ~2-3.5k | Data model questions |
| `--tree-only --docs all` | ~6-10k | ~5-8k | Design discussions |
| `--apps <one> --skeleton` | ~5-8k | ~4-6k | One app API surface |
| `--skeleton --full-apps <one>` | ~15-25k | ~10-18k | Focused feature work |
| `--preset overview` | ~20-30k | ~14-21k | Full project map |
| `--diff HEAD~3` | ~2-10k | ~1.5-7k | Debugging recent changes |
| `--grep <pattern>` | ~3-15k | ~2-10k | Concept tracing |
| Full dump (no flags) | ~40-80k+ | ~28-56k | Last resort |

**Rule of thumb:** Stay under 30k tokens for Claude Sonnet/Opus. That leaves 70% of the 200k window for conversation.
With `--compact`, you can fit ~40% more code into the same budget.

---

## 📁 OUTPUT FILES

All dumps go to:
```
src/core/management/commands/_output/
├── 2026-02-19__14-30-45__dump-context__compact__preset-writeback.txt
├── 2026-02-19__15-12-03__dump-context__compact__tree__skeleton__docs-all.txt
├── 2026-02-19__16-45-22__dump-context__compact__diff-HEAD3.txt
└── _latest.txt → (symlink to most recent)
```

Quick access to last dump:
```bash
cat src/core/management/commands/_output/_latest.txt | pbcopy  # macOS
cat src/core/management/commands/_output/_latest.txt | xclip   # Linux
```

Add to `.gitignore`:
```
src/core/management/commands/_output/
```

**Do NOT gitignore `.dump_presets.json`** — commit it so the whole team shares the same presets.

---

## 🎯 TL;DR — The Commands You'll Use 80% of the Time

```bash
# Starting a new LLM session for focused work
python manage.py dump_context --preset writeback --compact

# "Help me understand the whole project"
python manage.py dump_context --preset overview --compact

# "Something broke after my recent changes"
python manage.py dump_context --diff HEAD~3 --compact

# "Find everything related to X"
python manage.py dump_context --grep ModificationProposal --compact

# "How big is this dump going to be?"
python manage.py dump_context --estimate --preset writeback --compact

# "What presets do I have?"
python manage.py dump_context --list-presets
```