# Writeback V3 — How it works

Writeback is the part of Castor that changes an IFC building model from a sentence typed by a
user, with human approval and full traceability. This page explains V3 in plain language. The
contract with acceptance criteria is [`spec.md`](spec.md); the delta from V2 is
[`what-changed.md`](what-changed.md).

## The principle: from minimal authority to maximal verification

V2 was built on **Minimal Authority**: the language model was never given more power than the
request needed. Simple property edits went through pre-coded handlers, the model only classified
and filled slots; only entity creation or deletion let the model write code. That produced three
tiers, a router, and a chain of five to eight small model calls per request. The failures lived
between the calls: each one was small and on schema, but nothing checked that step four still
meant what step one understood.

V3 keeps one path and moves the safety to the other end. The model always writes a small piece
of IfcOpenShell code, and **what the code did to the file is checked deterministically**.
Authority is maximal; verification is maximal too. The human reviews the result of the code, not
the model's description of its intent.

## Pipeline at a glance

```
user message
   │
   ▼
ground    (no LLM)    facts from the database, as exact strings: every storey, every
   │                  space with its storey, how many of each type there are, which
   │                  property sets exist on the types the request names. Plus the
   │                  api sheet and the eight helper signatures.
   ▼
generate  (LLM #1)    the model writes two functions in one block: select(model)
   │                  returns the target entities; modify(model, targets) changes
   │                  those and only those. Or it answers with one REJECT: line and
   │                  a reason (a greeting, a question, geometry, nothing to change),
   │                  which ends the request in one call.
   ▼
run       (no LLM)    a separate process opens a scratch copy of the file, snapshots
   │                  it, runs select then modify, snapshots again, and returns the
   │                  targets and the diff. The scratch copy is kept.
   ▼
verify    (no LLM)    scope: anything changed outside the targets, or any geometry,
   │                  is a violation → back to the model with the error, max 2 tries.
   │                  flags: a value or a property name not in the request, an entity added
   │                  or removed.
   │       (LLM #2)   a model reads the code and the diff WITHOUT the request and
   │                  writes one sentence on what the change does.
   │       (LLM #3,   Guardian searches the project documents for anything that
   │        optional) confirms or contradicts the change and says so on the card.
   ▼
       ── human approval: request, sentence, targets, diff, flagged rows ticked ──
   ▼
approve               fingerprint check → the scratch copy replaces the original
                      atomically → git commit → index updated from the diff
```

Two model calls, three with Guardian; a repair adds one, at most two, and Guardian's document
search adds one embedding query. V2 spent five to eight. Every example below is written
against the benchmark fixture, `Ifc4_SampleHouse.ifc`: two storeys, four spaces, five walls,
three doors.

## A request, step by step

**"Set the fire rating of all walls on the ground floor to EI60."**

1. **Ground.** The database says this file has storeys *Ground Floor* and *Roof*; four spaces
   (*1 - Living room*, *2 - Bedroom*, *3 - Entrance hall*, *4 - Roof*); three IfcWall and two
   IfcWallStandardCase; walls carry *Pset_WallCommon* with *Reference*, *IsExternal*,
   *LoadBearing*, *ExtendToStructure* and *ThermalTransmittance*. All of that goes into the
   prompt as it is. The one thing grounding matched was the word "walls" against the type names,
   which is why the wall pset is listed; "ground floor" is left to the model, which reads two
   storey names and picks one.

2. **Generate.** The model writes `select`: take the elements contained in the storey
   *Ground Floor*, keep the walls, return them. And `modify`: for each target, set *FireRating*
   in *Pset_WallCommon* to *EI60*.

3. **Run.** The pipeline copies the file; a child process opens the copy, snapshots it, runs `select` (5 walls, since
   IfcWallStandardCase is a kind of IfcWall), runs `modify` on those five, snapshots again,
   diffs, and writes the scratch copy. It returns the five GlobalIds and the diff: five property
   changes, all on the five walls, no geometry moved, no entity added or removed.

4. **Verify.** The scope check passes. The value "EI60" is in the request, so no row is flagged.
   Then the explainer model, shown the code and the diff but not the request, writes:

   > Adds Pset_WallCommon.FireRating = "EI60" to five wall elements that did not have the
   > property before.

   Guardian, unless switched off for this request, searches the project documents for "wall
   fire rating EI60" and puts its verdict on the card. The card shows the request, that sentence,
   the five walls by name and storey with their *IsExternal* value, the diff aggregated as one
   row, *FireRating: added EI60 on 5 walls*, and the Guardian verdict. The code is one click
   away, collapsed.

5. **Approve.** The user approves. The fingerprint of the original is checked against the one
   taken when the proposal was made. It matches, so the scratch copy replaces the original
   atomically, git commits with the code in the commit body, and the database index is refreshed
   for the five walls from the diff. If Guardian had been switched off, the proposal records
   that it was.

If someone had changed the file between step 4 and step 5, the fingerprint would no longer
match, and the approval would be refused with "file changed, please re-propose". The code is
never run a second time: what the user approved is the file that gets swapped in.

## A harder request: why grounding and helpers matter

**"Set the fire rating of the internal doors to EI30."**

V2 had no notion of "internal": its resolver looked for doors with *internal* in the name, found
none, tried twice more, then either gave up or grabbed all three doors.

V3's grounding lists three IfcDoor and, because "doors" matched the type name, the door pset
*Pset_DoorCommon* with *IsExternal*. The select function takes the doors and keeps those whose
*IsExternal* is false, using a helper, *by pset value*, and returns the two internal single
doors. The card lists them by name and storey with that value. The diff confirms exactly those
two changed.

A space-based request, "the furniture in the living room", works the same way through the
*elements in space* helper, since this file places its twelve furniture items inside
*1 - Living room*. Its doors, like most doors in IFC, sit in the storey, not in a space, so
"the doors in the entrance hall" is not a request this fixture can answer, and the corpus does
not pretend otherwise.

The helper library is deliberately small: eight names, each a few lines over functions
ifcopenshell already ships, covering storey, space, type, name, property value, material,
decomposition and container. The model may also write raw IfcOpenShell when no helper fits. A
new helper is added only when the benchmark shows the same raw traversal failing twice.

## What catches a wrong-but-plausible result

The scope check only asks *which entities* changed. One rule, with no model, asks *what*
changed: **every row in the diff must be something the request said.** A new value that is not in
the request (booleans and removals excepted) is flagged; so is a property whose name nothing in
the request mentions, by its words or a listed synonym (a U-value written when a fire rating was
asked for). An entity that appears or disappears is flagged. That
is what catches a second property copied from the api sheet, a "remove" executed as "set to
empty", and a creation the user did not ask for.

Flagged rows go to the top of the card in a warning colour, and approval requires ticking each
one. Normal rows need nothing. The blind explanation is the catch for whatever the rule cannot
express, such as the right walls with the wrong operation.

## The ring around the code

Everything below existed in V2 and survives. Each one answers a different question.

- **Git.** Every IFC file lives in a per-project git repository. Every approved change is a
  commit; every version can be restored. *What did this file look like before?*
- **Proposal record.** One row per proposal: which file, its fingerprint when proposed, the code,
  the targets, the diff, and where the scratch copy is. This replaces V2's journal. *What exactly
  was approved?*
- **Fingerprint.** A hash of the file's bytes, taken at proposal time and checked at approval. A
  mismatch refuses the approval. *Is this still the file I proposed against?*
- **Round-trip diff.** A snapshot of every entity's class, identity, geometry hash, and
  properties, taken before and after the code runs. In V2 this only scored the benchmark; in V3
  it is the centre of the pipeline: it enforces scope, it feeds the card, the commit, the index
  update and Guardian. *Did the code do only what it said?*
- **Sandbox.** Generated code runs in a separate process on a copy of the file, with a timeout
  and a short list of allowed imports. It is a speed bump, not a jail; the diff and the human are
  the real gates. *Can the code touch anything but the copy?*
- **Guardian.** When a proposal is made, a document search asks whether the project's technical
  documents confirm or contradict the change, and the answer is on the card before the human
  decides. It advises, it never blocks, and in V3 the user can skip it per request when they know
  the documents are silent. *Does the documentation agree?*
- **Failure record.** Every decline, three-strikes rejection and failed approval is stored as a
  structured failure record with the last code the model wrote, and shown in the chat as a card
  with a retry path. *What went wrong, and how often?*
- **Benchmark.** Ninety-eight prompts (95 scored, three advisory) against the sample house above, each with the expected
  targets and the expected diff written in plain words. It scores whether the right entities were
  selected, whether the diff matches expectation, and whether nothing else in the file moved. It
  is also how the coder model is chosen. *Does this work, measurably, on this hardware?*

## What a user can and cannot ask for

- **Yes:** set, add or remove properties; change names, descriptions, tags; add or remove
  property sets; assign materials and classifications; create and delete entities; change
  spatial containment and other relationships. Several of these in one sentence, committed
  together.
- **No:** geometry. Moving, resizing or reshaping anything is out of scope by design, and the
  diff treats any geometry change as a violation.

## Hardware and models

Castor runs all inference locally. Modify uses a code-tuned model; Ask keeps its own prose-tuned
model. The one-sentence explanation is written by the Modify model, so a request pays no model
swap. The bake-off scored that sentence ten of ten on a ten-case sample, so it stays there; there
is no setting for it.

| Graphics memory | Modify model | Note |
|---|---|---|
| 8 GB (floor) | 7B coder | the target for users with limited hardware; measured, not assumed |
| 12 GB (default) | 14B coder | proposed default, settled by the benchmark bake-off |
| 24 GB | 30B coder | best local quality |

Spilling a bigger model into system memory is deliberately not done: it turns a thirty-second
generation into minutes.
