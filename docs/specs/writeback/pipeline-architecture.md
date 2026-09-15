# Writeback Pipeline — Architecture Summary

This is a concept doc, not an inventory of files. The contract is
[`../../writeback_V3/spec.md`](../../writeback_V3/spec.md); the plain-language walk-through is
[`../../writeback_V3/overview.md`](../../writeback_V3/overview.md). The V2 pipeline this page used
to describe (triage, slots, resolver, tier router, journal) is kept as history under
[`../../writeback/`](../../writeback/).

## Design goal

The model always writes the change as a small piece of IfcOpenShell code, and **what the code did
to a copy of the file is measured** before a human sees it. Authority is maximal; verification is
maximal too. Two model calls per request, one deterministic gate, one human decision.

## Stages

```
user message
   │
   ▼
ground    (no LLM)   storeys, spaces, entity counts and the property sets of the
   │                 types the request names, read from the index as exact strings
   ▼
generate  (LLM #1)   one fenced block: select(model) -> targets, modify(model, targets);
   │                 a REJECT: line for greetings, questions, geometry
   ▼
run       (no LLM)   a child process on a scratch copy: snapshot → select → modify →
   │                 snapshot → diff → write; the copy is kept
   ▼
verify    (no LLM)   scope: anything changed outside the selection, or any geometry, is
   │                 one error string fed back with the code (at most two repairs);
   │                 the one flag rule marks values that are not in the request and
   │                 every entity added or removed
   │      (LLM #2)   the blind explanation: code + diff, never the request
   │      (LLM #3)   Guardian, optional, searches the documents; advisory
   ▼
   ── the card: request, sentence, targets with evidence, aggregated rows, flags ──
   ▼
approve   (no LLM)   claim the row → lock the file → fingerprint check → the scratch
                     copy replaces the original → git commit with the code → index
                     refresh from the diff. The code never runs twice.
```

## What holds the line

- **The scope check** on the measured diff: entities outside the selection, geometry, schema.
- **The flag rule** on the aggregated rows: a value not in the request, an added or removed
  entity; each flagged row needs a tick in the one approval POST.
- **The blind explanation**: a sentence the user compares with what they asked.
- **The fingerprint and the file lock**: a changed file refuses the approval; two approvals on
  one file cannot interleave.
- **The benchmark**: targets match, diff match, integrity re-read from the written copy, and the
  reject column, per model, on the sample house.

## What it does not do

Geometry, multi-file requests, a checkpoint between selection and mutation, a second run of the
code at approval, a rebase onto a changed file. See the spec's non-goals.
