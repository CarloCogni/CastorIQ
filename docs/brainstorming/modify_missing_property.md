# Modify: a missing requested property — brainstorming

**Date:** 2026-09-15 · **Status:** decided (A + C) · **Owner:** Carlo
**Spec rows changed:** [G-1, V-3](../writeback_V3/spec.md) · **Decision log:** review 6 entries

## The failure

Sample house, `qwen2.5-coder:7b`: *change fire rating on all doors to 120*. The three doors were
selected; the diff wrote `Pset_DoorCommon.ThermalTransmittance` to 120 (one door had none, two
had 3.7021). The blind explanation said so truthfully. No row was flagged, because the flag rule
asks whether the **value** is in the request and "120" is.

Cause, checked in `src/`: grounding lists only the property names the index holds for a matched
type. The doors carry `IsExternal, Reference, ThermalTransmittance`; FireRating is not in the
prompt, the prompt says names are exact strings, and the coder substituted the closest listed
property. The standard pset catalogue (`ifc_processor/schema_data`) knows `Pset_DoorCommon.
FireRating: IfcLabel` and had no production caller.

## Options

**A — grounding lists the standard properties not yet set on each pset.**
`- Pset_DoorCommon: IsExternal, Reference, ThermalTransmittance · not yet set: AcousticRating,
FireRating, …`. Non-standard psets get no suffix. One prompt sentence: a property shown as not
yet set may be added; change the property the request names, never a different one.
Fails when: the list is cut by the cap (16 per pset, so every `Pset_*Common` is complete); the
property is outside the catalogue; the model ignores the list. Budget: worst case about 2k
tokens on a type-rich file, under 1k on the sample house.

**B — a deterministic resolver in the prompt** ("Requested property: Pset_DoorCommon.FireRating,
not set"). Rejected: a request-to-catalogue matcher, which G-1 forbids, needing a parser to pick
the property words out of the sentence; a wrong resolution steers the model with more authority
than today.

**C — the property name joins the flag rule.** A property or attribute row is flagged when the
name of what it changed is not in the request: squashed name as a substring of the squashed
request, a camel word of at least four letters as a substring of a request word (plural
stripped), or a listed synonym (u-value → ThermalTransmittance, move / storey / floor →
Container, zone → Groups, rename → Name …). Same `flagged` bool, same tick; the badge names the
reason. Fails as a false flag on phrasings the synonym list misses: one tick, never a block.
A repair instead of a flag was considered and rejected: a false positive would cost two model
calls and end a legitimate request in a rejection.

**D — a value-type check against the schema.** Rejected: `pset.edit_pset` already casts through
the template (`FireRating: 120` → `IfcLabel('120')`, verified), and a format rule ("120" vs
"EI120") is a validator by another name. Guardian is where document conventions surface.

## Challenged assumptions

- *A bigger model fixes it.* No: every model is told to use only the names it is shown; the 14B
  also offloads on 8 GB (135 s per call).
- *The value rule is enough.* It was written for copied placeholders and unit conversions; a
  right value on the wrong property is invisible to it by construction.
- *Listing more names invites invention.* The model invented nothing; it substituted because the
  list was closed. A "not yet set" name is an exact string.
- *A relevance check is a validator or a router.* It decides nothing about the flow, runs on the
  measured diff, never blocks, and reuses the mechanism V-3 already has.

## Decision

A + C. No new model call, no new setting, no repair kind, no matcher in grounding. V-3 stays one
sentence: a row is flagged when something about it, its value or its property, is not in the
request. Corpus case 18.6 pins the repro; the 7B door cases are re-run with
`benchmark_writeback --filter 1,18,20`.
