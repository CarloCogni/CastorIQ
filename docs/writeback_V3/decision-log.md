# Writeback V3 — Decision log

Dated entries, newest last. Each gives the decision, the alternative rejected, and the rationale
in a sentence or two. The full argument is in the frozen brainstorm,
[`../brainstorming/modify_pipeline_V3.md`](../brainstorming/modify_pipeline_V3.md), cited by
section. Entries marked *review* come from the 2026-09-14 design review against the Zen of
Python, and entries marked *review 2* from the code-grounded review the same day; where a later
entry replaces an earlier one, the earlier one is kept and marked **superseded** so the trail
stays readable.

**2026-09-11 — One path: generated code. Tiers dropped.** Rejected: keeping the three-tier
escalation with a better router. Targeting and hand-off drift were the churn clusters of V2's
history, and both live between tiers. Minimal authority becomes maximal verification; the FMP
report must re-argue that rather than swap it silently. (§0)

**2026-09-11 — The ring stays.** Git, journal and sandbox, fingerprint, round-trip diff,
Guardian, database sync, benchmark all survive; how far each survives is a per-component
decision, not a blanket one. Rejected: a clean-slate rewrite. (§0, §3) *The journal's fate was
revised by the review; see below.*

**2026-09-11 — Shape is select → mutate → verify.** Rejected: spec → code → verify (its
assertions survive as the allowed-set diff check) and a constrained api call plan (kept as the
fallback if the bake-off fails). (§2, §4) **Superseded** in call count only: see "One generation
call" below. The two functions, the allowed set and the target evidence survive.

**2026-09-11 — Modify switches to a coder model; Ask keeps its own.** Rejected: one model for
both. Settings already split provider and model by purpose, so this is a default change. The
default is decided by the bake-off, not by a table. (§0, §1)

**2026-09-11 — Every model call has one small job, one small output, and a deterministic check
behind it.** This is the rule the rest follows from. (§0)

**2026-09-14 — The database is a lookup, not a selector.** Rejected: the model emits a
FilterEngine spec. V2's targeting failed because the request was compressed into seven keys and
vocabulary was guessed; in V3 the model writes the selection and the database supplies exact
strings. (§0, §2.5)

**2026-09-14 — Selection is kept honest by four measures.** Grounding; a small helper library;
deterministic sanity checks on the returned targets; target evidence on the card. Rejected:
trusting the model's selection and checking only the diff. (§2.5) **Superseded** on the sanity
checks: see "Grounding matches once, the sanity check goes" below. Three measures remain.

**2026-09-14 — Corpus expectations are rewritten before the bake-off.** The router lines die with
the tiers; each prompt gets a targets line and a diff line. Rejected: running the bake-off on
understanding scores that no longer mean anything. (§3)

**2026-09-14 — Risks get mitigations, not acceptance.** (§5) *Several mitigations were simplified
by the review; the risks stand.*

**2026-09-14 — The cheat-sheet stays, narrowed.** Rejected: removing it, because no local model
knows the api from memory. (§1) **Superseded** on the mechanism: see "The api sheet is a
checked-in file" below.

**2026-09-14 — Guardian toggle, per request, stateless.** Rejected: a remembered per-project
default. Guardian advises and never blocks, so skipping it changes speed, not safety; the skip is
recorded on the proposal. (§5.4)

**2026-09-14 — 8 GB floor, 12 GB default.** 7B coder on 8 GB, 14B on 12 GB, no CPU offload.
Rejected: designing for 12 GB and letting 8 GB spill into system memory. The 8 GB row is run in
the bake-off so the claim is measured. (§0)

**2026-09-14 — Checkpoint: show always, block on triggers.** (§2.3) **Superseded**: see "The
card is the checkpoint" below.

**2026-09-14 — The card is the diff plus the blind explanation; code collapsed.** Rejected:
showing the code by default with an acknowledgement gate. The gate moves to flagged diff rows.
(§0, §5.1)

**2026-09-14 — Multi-intent requests are one commit.** Rejected: one commit per intent. (§0)

**2026-09-14 — The Ask model writes the blind explanation.** (§0) **Superseded**: see "The
explainer defaults to the coder model" below.

**2026-09-14 — Eight helpers to start.** Growth only by the evidence rule. Rejected: designing
the full set upfront. (§0, §5.3) *Review: the eight are thin wrappers over ifcopenshell.util and
the evidence rule is a docstring sentence, not a requirement.*

**2026-09-14 — Corpus expectations are generated, then hand-reviewed.** (§0) **Superseded**: see
"Corpus expectations are GlobalId-free" below.

**2026-09-14 — Cheat-sheet retrieval by a hand-written keyword → module map.** (§0)
**Superseded**: see "The api sheet is a checked-in file" below.

**2026-09-14 — Build order: evidence first, UI second.** Rejected: building everything at once
with thirteen days to the FMP. (§6)

---

### Review entries, 2026-09-14

**review — One generation call, not two.** The model writes `select()` and `modify()` in one
fenced block; the harness runs them in order and passes the selection to `modify`. Rejected: two
calls with a blocking checkpoint between them. The checkpoint needed request classification
(creation, "no explicit noun") that V3 had deleted, and a stateful child-process protocol to
avoid a second file open. Cost: a wrong selection wastes one run of a few seconds. Gain: one call,
one child, one open, one repair loop, no checkpoint policy.

**review — The card is the checkpoint.** Targets with evidence sit above the diff; flagged rows
need a tick. Rejected: blocking before mutation on zero targets, more than N, creation or
deletion, no explicit noun. Zero targets is a repair then a failure. Revisit only if the bake-off
shows a material share of runs wasted on wrong selections.

**review — Approval swaps the scratch file; the code is not run again.** The propose-time copy
is kept and swapped in after the fingerprint check. Rejected: re-run at approval with a
diff-equality gate. It cannot work for creation requests, since every run mints fresh GlobalIds,
and it kept an execution path alive to defend against non-determinism the fingerprint already
excludes for everything but uuid and time. Rebase is dropped with it: a changed file means
re-propose.

**review — The proposal row is the journal.** Code, targets, diff, fingerprint, scratch path,
Guardian skip. Rejected: keeping `MutationJournal` and `JournalExecutor` untouched. With one
operation the journal is a list of one twelve-field record wrapping a string; "untouched" meant
carrying about 1,400 lines of which V3 uses sixty. The journal, its executor, its builder and the
journal diff renderer are deleted with the tiers. `FilterEngine` is deleted, not demoted:
grounding is three ORM queries.

**review — Grounding is a lookup: full lists, no vectors, no near-miss turn.** The index has a
handful of storeys and at most a few hundred spaces; inject them all and let the model match
"first floor" against three strings. Rejected: pgvector name matching plus a held-back near-miss
list for a special repair prompt. That was a fourth open-coded cosine query and a second grounding
mode for data a human can read in one screen.

**review — The api sheet is a checked-in file.** **Superseded** on the mechanism: see "The api
sheet is hand-written" below. Generated by a management command from the
pinned ifcopenshell, committed, reviewed like code. Signatures only, seven modules, about a
thousand tokens. Rejected: a keyword → module map (the deleted triage classifier, reborn as a
dict), introspection at startup (an environment-dependent prompt nobody can read in review), and
an escape hatch that pulled modules in during repair.

**review — One flag rule on the diff.** A row is flagged when its new value is not in the request
text, unless boolean or removal; every added or removed entity is a flagged row. Rejected: the
property-count rule, which needed an intent count nothing in V3 computes, and the
operation-shape rule, which was a keyword scan. Both motivating cases fall out of the one rule.

**review — Population change is a flagged row, never a violation.** Rejected: "allowed only for
creation or deletion requests", which needed a request kind before the diff exists.

**review — Code comes back as a fenced block, not JSON.** Rejected: a JSON schema on the code
path. Code inside a JSON string forces escaped newlines that small models mangle, and the
structured-call boundary exists for stages V3 deletes.

**review — The explainer defaults to the coder model.** The Ask model is an override.
**Superseded** on the override: see "No explainer setting" below. Rejected:
the Ask model by default, which costs two model swaps per request on 8 and 12 GB for a
one-sentence output. The bake-off scores the sentence; if it is poor the default flips, one
setting.

**review — Corpus expectations are GlobalId-free and hand-written.** `targets:` names type,
count and container; `diff:` names pset, property, value and count; the runner resolves ids
through the index. Rejected: generating GlobalId sets with the Claude row and hand-reviewing
ninety cases. The fixture has two storeys, four spaces and about two dozen products; the
forty-seven-thousand lines are geometry.

**review — Snapshot cache only after measurement.** Rejected: committing to a per-fingerprint
cache and an open item about where to put it before timing the snapshot on the largest file.
**Superseded**: see "No conditional requirements" below; the row is gone, not conditional.

**review — At most one new setting.** **Superseded**: see "No explainer setting" below. The
existing Modify model default changes; an optional explainer model is added. Rejected: four new settings including a per-graphics-memory default
table the app cannot act on, since it does not detect memory, and a site setting for a threshold
that no longer exists.

---

### Review 2 entries, 2026-09-14 (code-grounded)

Every "kept", "existing" and "untouched" claim in the spec was checked against `src/`.

**review 2 — Guardian stays at proposal time.** The V3 docs had moved it after approval; the
code runs it inside propose, and that is where it belongs, because a verdict that arrives after
the decision is advice nobody can act on. Rejected: Guardian in the approval chain.

**review 2 — Grounding matches once, the sanity check goes.** G-1 claimed "no matching" while
matching in three places, and V-2 "reused grounding hits" that a lookup never produces. Now
grounding does one named substring match (request words against type names, to pick pset lists)
and nothing else; the type-and-container sanity check is deleted because the card shows both.
Rejected: a keyword matcher under another name.

**review 2 — One repair rule.** Zero targets is one more error string through the same two-repair
loop. Rejected: a special second-zero prompt and rejection text; the grounding lists are already
in every prompt.

**review 2 — The api sheet is hand-written.** A forty-line constant plus one test that every
named function resolves in the installed ifcopenshell. Rejected: a generator, a management
command and an idempotency test for a string a reviewer can read.

**review 2 — No explainer setting.** Adding one meant a new site field, a third purpose in a
resolver that raises on anything but ask/modify, per-user override plumbing and a bake-off
dimension, for one sentence. If the coder's prose is poor, the call is pointed at the Ask model
in one line, then.

**review 2 — Proposal columns are new and explicit.** Seven columns. Rejected: reusing
`changes` (list-defaulted, holds a dict), `diff_preview` (text holding JSON), the code
acknowledgement columns or `confidence` (documented 0–1, stores 80) with a second meaning.

**review 2 — Approval is one POST.** The ticked flagged-row keys travel with the approve request
and are compared server-side to the keys recomputed from the stored diff. Rejected: the V2
two-request dance with a separate acknowledge endpoint and optimistic page script.

**review 2 — No conditional requirements.** The snapshot-cache row is deleted rather than marked
conditional; P-1 measures, and a row is written if the number is bad. A conditional row is a
commitment in disguise and a third sidecar-file convention.

**review 2 — Git is adapted, not untouched.** The commit method hard-codes a tier label and a
fixed body and has three callers. It takes a subject and a body.

**review 2 — The index refresh is promoted, not reused as is.** The V2 routine is a private method
on the execution service that trusts a GlobalId list and does not regenerate embeddings. It
becomes a public function in `ifc_processor`; the embedding limitation is stated, not hidden.
"No file open after the child returns" applies to the card only; approval opens the file once.

**review 2 — Examples and corpus are written against the real fixture.** The overview used a
house with a *Level 1 (+0.00)*, a corridor, 47 walls and 14 doors; the fixture has *Ground
Floor* and *Roof*, four spaces, five walls, three doors and no space boundaries. The worked
examples now use the fixture, and the "doors in a space" case is named as one it cannot answer.

**review 2 — The line count is labelled honestly.** The named modules are about 6.5k source
lines, 7.5k with the pipeline orchestrator, and about 5.6k lines of tests on top. "Services and
their tests" did not add up to 7.5k.

**review 2 — Step 0 is the skill rewrite.** `.claude/skills/writeback-ops.md` describes a V1
pipeline naming a class that does not exist, and CLAUDE.md sends every implementer there first.
Rewriting it precedes the first line of V3 code.

**review 2 — The helper library lives in `ifc_processor/services/` and is whitelisted.** The
sandbox child runs without Django and restricts imports; a helper module under `writeback/`
would not import.

**Next revisit trigger:** the bake-off result. If no local coder reaches an acceptable
targets-match score on the 12 GB row, the constrained api call plan (§4) is promoted from
fallback to design.

---

### Build entries, 2026-09-15

**build — One extra proposal column.** `explainer_model` joins the seven columns of A-1 so the card can
name the model that wrote the blind explanation (U-1) without storing it in a V2 column. Rejected:
reusing `intent_json`.

**build — The scratch name carries a short uuid, not the proposal id.** The child runs before the row
exists, so `.<stem>.proposal-<uuid8>.ifc` is minted by the pipeline and stored on the row; R-2's
lifetime rules are unchanged.

**build — A `REJECT:` line is a first-class model answer.** A greeting, a question, a geometry request
or a request with no target or value ends in one call with the model's reason ("Declined: …"), not in
three zero-target attempts. It is not a failure kind and takes no repair; the benchmark's reject column
measures over-refusal as well as under-refusal. Rejected: routing rejections through C-4, which cost
three model calls per greeting.

**build — The diff tracks relationship-derived values.** Container, materials, classifications, groups
and type object are snapshotted as attributes of every IfcObject, so a storey move, a material or
classification assignment or a zone membership is a diff row on the object and not an invisible
change. Direct containment only: an element hosted in a moved wall keeps its own storey relation, so
the move is one row on the wall rather than a violation on every window in it.

**build — The geometry hash is over the world placement matrix.** Re-parenting an element to another
storey while it stays where it is (what `spatial.assign_container` does) is not a geometry change;
any move still is. Rejected: hashing the placement tree, which made every containment change a
scope violation.

**build — Population rows are typed and limited to objects.** `added_objects` / `removed_objects`
carry the IFC class of every IfcObject that appeared or vanished; property sets and relationship
entities that come and go with a change are not listed, so "add a pset" is one property row, not a
flagged "entity added". Known limit: a pset added with no properties is invisible to the diff
(corpus case 7.2 is advisory for that reason).

**build — The card is one Django partial.** `serialize_proposal` feeds `proposal_card.html`, rendered
server-side for both the persisted page and the live WebSocket/HTTP answer (the response carries the
HTML). Rejected: a second, client-side rendering of the same dict.

**build — Modify's Ollama model is `MODIFY_MODEL`; Ask keeps `OLLAMA_MODEL`.** `SiteLLMConfig.resolve`
honours an Ollama tag for Modify, and a user's per-user Ollama pick applies to Ask only, so a prose
model cannot silently replace the coder. No new setting (U-4).

**build — The helpers, `ifcopenshell`, `api` and `element` are bound in the sandbox namespace.** The
7B coder copies the shape of the prompt and drops import lines; three section-1 smoke runs went from
0/6 to 4/6 once the names were in scope. The import whitelist still gates everything else.

**build — One worked example, with placeholder names.** Spec C-2 allows one mixed example. Without it
the coder invented `entity.get_pset(...)`; with a concrete example it copied the example's value
(`LoadBearing = True`) into a request about fire ratings, and a boolean is exempt from the flag rule,
so only the blind explanation exposed it. The example now uses `<storey name>`, `<Pset name>`,
`<Property>`, `<value>`: copied placeholders are visibly wrong and flagged.

**build — Every model call has a token cap and a wall-clock cap.** `num_predict` (1536 for the code
call, 160 for the explanation) and `safe_invoke` (240 s / 90 s). The first full 7B bake-off row lost
30 minutes to one looping generation that the streaming client timeout never cuts. Rejected: relying
on `OLLAMA_REQUEST_TIMEOUT`, which is a per-read timeout and does not fire while tokens keep arriving.


---

### Review 3 entries, 2026-09-15 (code review of the built pipeline)

Every module was read end to end and each suspected defect was reproduced on the sample house
before it was fixed.

**review 3 — Population is objects only.** `added_global_ids` / `removed_global_ids` hold every
`IfcRoot`, so one `pset.add_pset` added two GlobalIds (the pset and its relationship) that reached
the commit counters and the index refresh, which then upserted an `IfcPropertySet` as an entity.
The commit and the refresh now read `added_objects` / `removed_objects`; the refresh writes only
classes a full parse would index and links the direct spatial container. Rejected: filtering in
the refresh alone, which left the commit counters wrong.

**review 3 — Occurrences snapshot their own psets.** `get_psets` inherits from the type by
default, so editing a type's pset made every occurrence report a change outside the selection:
three failed attempts, then a rejection, for a request the spec allows. The snapshot now reads
with `should_inherit=False`; a type edit is one row on the type. Rejected: allowing inherited
rows when the type is selected, which needed a second rule the card cannot show.

**review 3 — The pset recipe is `add_pset` then `edit_pset`.** The prompt told the model to find
a pset with `get_pset` and fetch it by its `id`; when only the type carries the pset (doors'
"Construction", furniture's "Identity Data" on the fixture) that id is the type's pset and the
edit changes every sibling. `add_pset` is find-or-create on the entity itself (verified: called
twice, same id, nothing created). Rejected: a ninth helper for "own pset", which the api already
provides.

**review 3 — A pset added empty is one row.** A `pset.add_pset` with no properties was invisible
to the rows and a card with zero rows; it is now one row named after the pset (before none,
after the pset name) and follows the flag rule. `is_empty` judges population on objects, so a
relationship or pset that comes and goes is never a change on its own. Corpus case 7.2 can leave
advisory.

**review 3 — Cancellation owns the scratch.** Every emit after the copy is made can raise
`CancellationError`; only the sandbox call was guarded, so a Stop during verify leaked the copy,
and a Stop during Guardian left a PENDING row with no message that `supersede_pending` could never
find. The attempt now deletes the copy on any exception after the copy exists, and a cancellation
during Guardian rejects the row with the reason "cancelled". `ProposalConsumer.disconnect` sets
the cancel event, as the scan consumer does. Rejected: linking the message before Guardian, which
moved persistence into the transport.

**review 3 — An empty explanation, not a sentinel.** "Explanation unavailable." is truthy, so a
failed explainer became the git subject, the commit message and the chat message. The stored
explanation is now empty on failure; the card and the chat show the placeholder, the commit falls
back to the request. The explainer also cuts at the first sentence boundary, which the old
`_first_sentence` never did.

**review 3 — One context size for every Modify call.** The code call passed `num_ctx`; the
explainer and Guardian did not, so Ollama reloaded the same model with a different runner two or
three times per request. Both now pass the one constant (G-3, U-4). Guardian's purpose is now
explicitly `modify`; it was the default before.

**review 3 — Model timeouts are visible failures.** V3 added a 240 s wall-clock cap, and its
`TimeoutError` (like a provider that is down) was unhandled on the HTTP path: a 500 with no chat
message. The generator turns those into a `ModificationError` (classified `LLM_TIMEOUT` /
`LLM_UNREACHABLE`, retryable); the typed budget and BYOK errors pass through as before.

**review 3 — Viewers read, editors write, on both transports.** `ModifyView` gated on membership
only while the consumer required `can_modify`. Every Modify POST now requires EDITOR or OWNER.

**review 3 — The sandbox budget grows with the file.** 30 s covered open, two snapshots, the
code and the write; a 100 MiB model would have timed out before the code ran. The budget is a
base plus a per-MiB share. With zero targets the child skips `modify`, the second snapshot and
the write. Rejected: a Django setting, because the sandbox module must stay Django-free.

**review 3 — Helper docstrings are prompt text.** `elements_in_storey` said "spaces included"
while excluding spaces and including openings. The helpers now exclude feature elements and their
first lines say so. The grounding match is capped at six types, whole-stem matches first.

**review 3 — Recorded, not changed.** The flag rule treats a list-valued removal (materials
detached) as flagged and a scalar removal (`→ None`) as not; the bake-off already noted a
container removal passing unflagged. A rule change is a spec decision for the next revision, not
a build fix.


---

### Review 4 entries, 2026-09-15 (code review after the build)

Every V3 module was read end to end once more, the UI, the benchmark and the residual V2 surface
were swept, and each suspected defect was reproduced on the sample house or traced to its lines
before it was fixed. Tests: 930 passing before, the suite green after.

**review 4 — Approval claims the row and locks the file.** The approve view read PENDING, then
executed with no lock; a double-click sent two POSTs, the loser's `os.replace` failed and its
cleanup rolled git back over the winner's commit while the row said APPLIED. Now the view claims
the row in one guarded UPDATE (pending → approved, stamping the flagged-row acknowledgement), a
second POST gets 409, and execution locks the file row and the proposal row from the fingerprint
check through the commit, reading the status from the locked row. The failure-path writes run
after the lock is released so a rollback cannot erase the FAILED status. The page disables both
buttons while a POST is in flight. Rejected: a per-process mutex, which does not survive two
workers. `APPROVED` was a dead status; it is the claim now.

**review 4 — Relationship values on type objects, own only.** The snapshot tracked container,
materials, classifications, groups and type object on `IfcObject` only, and read materials with
`should_inherit=True`. A material or classification assigned to a wall type produced zero rows
anywhere (reproduced) and the object-only `is_empty` answered "already so"; an occurrence with no
material of its own would have reported the type's change as a violation. Both are now snapshotted
on every `IfcObject` and `IfcTypeObject`, own only, which is the rule review 3 already set for
psets. Not on the project context: registering a classification system associates it with the
`IfcProject`, and tracking that made every "classify …" request a scope violation on the project.
Rejected: allowing inherited rows when the type is selected, the same second rule review 3 refused.

**review 4 — The refresh follows a type to its occurrences.** A type object is not an indexed
class, so a type pset edit refreshed nothing and left `IFCElementType` and every occurrence's
inherited keys stale until a reprocess. A touched type now refreshes its row and its occurrences.

**review 4 — Benchmark denominators keep refused change cases.** A change case that ended in a
rejection left `targets_match` and `diff_match` at None and dropped out of both denominators; the
7B row's 16/36 was 16/64, Claude's 38/46 was 38/64. Such a case now scores false on the columns
its expectation names. Harness errors stay out and are listed on their own. Rejected: leaving the
rule and footnoting the report, because the FMP report cites the columns as rates.

**review 4 — A model that cannot answer is a harness error.** A timeout or an unreachable
provider raised a plain `ModificationError`, which the reject column counted as a correct refusal:
a dead provider scored 27/27. `ModelUnavailableError` (still a `ModificationError` for the
transports) is scored as an error by the runner and classified `LLM_TIMEOUT` / `LLM_UNREACHABLE`
by its own patterns. A three-strikes rejection on a reject case stays a pass, as C-4 requires.

**review 4 — Integrity re-reads the written copy.** The column re-ran `scope_error` on the diff
the pipeline had already gated on with the same call, so it was true by construction; the bake-off's
39/39 and 50/50 measured nothing. The runner now snapshots the original and the scratch copy from
disk and scores `IfcDiff.unexpected` on that, which also gives the second implementation of the
rule a live consumer and a test pinning it to `scope_error`.

**review 4 — Diff-match is equality; p90 is the nearest rank; `--repeat` has rules.** `wanted in
text` let `EI60` pass on `REI60` and `EI600` while the corpus expects both values on the same
property; scalars now compare equal, list items by containment. p90 used banker's rounding on
`n-1` and returned the max for any n ≤ 5. `--repeat` re-ran cases that had already failed, re-ran
advisory cases, and let a provider blip in run two replace a clean run one; the first failing run
is kept, nothing else is repeated, an error never replaces a scored run, and the artifact records
`runs`.

**review 4 — Guardian follows the user and has a wall clock.** `GuardianService()` was built
without the user, so BYOK overrides, the token budget and the call log lost the Modify call's
rules; and its model call was the only one in the Modify path without `safe_invoke`. Both fixed;
a timeout is a FAILED verdict, advisory as before.

**review 4 — `getattr` and `hasattr` are ordinary builtins.** The regex ban on `getattr` and the
missing `hasattr` cost a repair attempt each on common idioms (three Claude rejections in the
bake-off) while `.__class__` access was unrestricted, so the ban bought nothing. Decided with the
owner; recorded in the bake-off as "not taken" before, taken now.

**review 4 — The card shows the request once and the explanation once; History renders the
stored rows.** The card never rendered `card.request` (U-1) while telling the user to compare;
the explanation was printed by the chat bubble and again by the card, on both transports; the
History tab's diff panel read the V2 `changes` key and never appeared on a V3 commit. The commit
rows now carry their card label. The page script also escapes every user, server and model string
before it becomes markup, reports a non-JSON answer by status, honours the 422 payload, and
dispatches the fallback toast on `document.body`, where the bridge listens.

**review 4 — Recorded, not changed.** An object removed outside the selection is a flagged row,
never a violation (V-1/V-2 as decided); tightening it needs the decomposition, because
`root.remove_product` cascades to openings. The flag rule's substring test is weak for one- or
two-digit values. Container tracking is by name, so a move between two storeys with the same name
is invisible. The corpus `reject:` lines carry no substrings, so the reason-matching machinery is
dead against the shipped corpus. These four are spec questions for the next revision.

**review 4 — The cleanup pass, same day.** Everything V2-shaped that the review had listed as
"not now" was done once the owner asked for it. One propose flow: `ModificationService.
propose_in_session` owns the chat messages, the supersede, the link and the session title for
the HTTP view and the WebSocket consumer alike (they had duplicated forty lines). The staff
dashboards lost their tier axis: the Modify funnel is now per ISO week, proposal acceptance is
overall plus a split by Guardian verdict, and the failure taxonomy lists the top error types;
`FailureRecord.tier` stays nullable for old rows and left the admin filters. The classifier
lost the intent/tier branches no V3 caller reached. The Modify page lost the dead sidebar
helper, the dead pending-proposals query and the unread `conflict_ids` on approve; viewers see a
note instead of the composer and no Approve/Reject on the cards. The card reads the index once
per page instead of once per proposal. A recorded Guardian skip is stored as `unknown` with a
reason, so history and admin never show "checking…" for it; a settled row whose check never
ran reads the same. A target the index does not know is labelled "(not in the index)", never
"(new entity)" (targets are read before `modify` runs, so none is new). The sandbox child names
a lone non-rooted entity from `select` instead of iterating its attributes; the sandbox
docstring says what the import whitelist really gates. `safe_invoke`'s docstring stopped
claiming a daemon thread, and the worker closes its own database connections. `explanation`
is `blank=True` (migration 0013). The History tab has its `?` help modal. `CLAUDE.md` points
writeback work at `docs/writeback_V3/` and states the local-first rule as the code implements
it (Ollama by default, cloud only when selected); the testing skill, `docs/testing.md`,
`docs/conventions.md`, `docs/data-models.md`, `docs/metacastor/d3-failure-memory.md` and
`docs/specs/writeback/pipeline-architecture.md` describe V3 instead of the deleted classes.


---

### Review 5 entries, 2026-09-15 (third code review of the built pipeline)

Every V3 module was read end to end again, each suspected defect was reproduced on the sample
house or traced to its lines, and the suites were green before (643) and after. Nothing in the
main path changed; every entry below is an edge path with a repro and a fix of a few lines, or a
spec question recorded for the next revision. No refactor, no rename, no new helper.

**review 5 — `add_pset` is not "the entity's own pset".** Review 3 chose `add_pset` then
`edit_pset` on that claim; `add_pset` is find-or-create *by name through `IsDefinedBy`* and returns
a pset shared by several entities as is. On the fixture, 20 curtain-wall members share six psets:
the prompt's own recipe on one of them produced 20 rows, 19 outside the selection, three failed
attempts and a rejection for a request the spec allows. The recipe is now `add_pset`, then
`pset.unshare_pset` when `get_elements_by_pset` holds more than one entity, then `edit_pset`;
the sheet says so and a test runs the prompt's example on the shared member (one row, no
violation). No corpus case hits a shared pset (walls, doors, windows, slabs, spaces have their
own), so the bake-off numbers did not move for that reason; the prompt changed, so the 7B row was
re-run before it is cited: 41/95, targets 19/64, diff 18/66, integrity 46/46 measured (evaluation
record, "Re-run after review 5"). Rejected: a ninth helper (`castor_select` is read-only) and an
unconditional unshare (it raises on a pset that is not shared and would orphan copies).

**review 5 — Every failure after the claim ends as FAILED.** `execute()` caught only what
`_apply` returned; an exception from the fingerprint read (original file gone) or from
`ensure_repo` / `snapshot` (repo unwritable) escaped as a 500 and left the row APPROVED, which no
view can approve or reject again. The locked section now routes any exception through the same
failure path (FAILED, scratch deleted, git rolled back where a snapshot exists).

**review 5 — Stop reaches a running propose.** Channels dispatches frames one at a time and the
consumer awaited the whole pipeline inside `receive_json`, so a `cancel` frame waited until the
run had finished and a card appeared for a run the user had stopped; only closing the tab
cancelled. The propose now runs as an asyncio task, a second propose while one runs is refused,
and the card render sits inside the handler's guard so a serialiser error is an error frame, not
a closed socket. The help modal's sentence about Stop is true now.

**review 5 — Reject is one guarded UPDATE, like approve.** A reject that raced an approve in
another tab blocked on the row lock and then wrote REJECTED over APPLIED from its stale instance:
file modified and committed, row rejected. Reject now matches `status = PENDING` in the UPDATE and
answers 409 when nothing matched.

**review 5 — The sweep keeps claimed rows.** It kept PENDING scratch files only, so a sweep in
the window between the claim and the swap deleted the reviewed copy. APPROVED rows are kept.

**review 5 — The page's reject reads the answer.** `_post` returns the JSON for every status and
`reject()` never looked at it: a 409 / 410 / 403 painted the card rejected. It branches like
`approve()` now.

**review 5 — Benchmark: "already so" on a change case is a failed case.** The no-change branch
scored targets only, so such a case left the diff denominator (one per row in the bake-off: the
cited 14/65 and 39/65 are 14/66 and 39/66) and could pass the targets column for a run that
changed nothing. It scores false on both columns, like a rejection. A harness error is now outside
every denominator (it counted as a failure in the pass, change and reject ratios); latency is
sampled over scored cases (a timeout's 240 s is not a request); `--repeat` retries an errored
first run and lets a scored run replace it. The evaluation record and the spec carry `/66`.

**review 5 — Recorded, not changed.** A Guardian whose constructor raises leaves
`verification_status = pending` on a pending row (the card reads "Checking…" until the row
settles). Supersede runs before the pipeline, so a request that dies on a budget error has already
superseded the previous proposal (spec R-2 as decided). History groups commits by file id after
ordering by file name (two files with one name interleave). The 422 payload lists missing keys
only; after a 409 / 410 the card re-enables Approve; typed LLM errors persist a chat message on
HTTP but not on WebSocket; `render_card` has no `user_permission` in its context; `detail.verdict`
is inserted unescaped (an enum value); the flag checkboxes carry no `name`. Benchmark: the token
columns sum every call log row in the window, not this run's; `_apply_model` repoints the site
config for the run and a kill leaves it there; a corpus/index count mismatch is charged to the
model; a `- Attribute xN` diff line would parse as an entity removal; entity rows match the class
exactly while targets expand subclasses; `diff_runs` shows an error as a regression; the package
docstring still says integrity is expected at 100%. No live V2 reference remains (twenty deleted
names grepped; every module imports); `tier2_writer` and `validators` belong to facilities and the
migrations, not to writeback.


---

### Review 6 entries, 2026-09-15 (first live door case)

*change fire rating on all doors to 120* on the sample house, 7B coder: three doors selected,
`Pset_DoorCommon.ThermalTransmittance` set to 120 on all three, nothing flagged. The brainstorm is
[`../brainstorming/modify_missing_property.md`](../brainstorming/modify_missing_property.md).

**review 6 — Grounding lists the standard properties not yet set on each pset.** The pset line
showed only the names the index held (`IsExternal, Reference, ThermalTransmittance`), the prompt
said names are exact strings, and the coder wrote the value into the nearest listed property. Each
standard pset now carries a `not yet set:` suffix from the schema catalogue (`schema_data.lookup.
properties_of`, unused until now), capped at 16 so every `Pset_*Common` is complete, and the
prompt says such a property may be added and that the value goes to the property the request
names. `psets_for` now orders a type's own psets before inherited ones: the fallback for a type
with no indexed psets is capped at ten, and in catalogue order `Pset_WindowCommon` was twelfth
behind IfcElement-wide psets, so the block cost 650 tokens and still lacked the one that matters. Rejected: a deterministic resolver stating "Requested property: Pset_DoorCommon.FireRating"
in the prompt, because it is a request-to-catalogue matcher (G-1 allows one match, and it is not
this one) and a wrong resolution would steer the model with more authority than a list.

**review 6 — The property name joins the flag rule.** The rule tested the value only, so a right
value on the wrong property passed by construction. A property or attribute row is now also
flagged when nothing in the request names it: the squashed name, a camel word of four letters or
more (plural stripped), or a listed synonym. Same `flagged` bool, same tick; the row carries the
reason and the badge shows it. Rejected: a repair with one error string, which fits C-4 but turns
a false positive on an unlisted phrasing into two wasted calls and a rejection of a legitimate
request; a flag costs one tick. Also rejected: a value-type or value-format check against the
schema, because `pset.edit_pset` already casts through the template (`FireRating: 120` becomes
`IfcLabel('120')`) and "120" against "EI120" is a document convention, Guardian's domain.

**review 6 — The sheet's notation is executable.** *Create a new IfcZone called "Acoustic Zone
1"* on the 7B ended in `AttributeError: IfcZone has no attribute 'ContainsElements'`. The bake-off
record already held the pattern: on sections 10 and 12 the 7B failed 10.1 (declined: "creating
IfcZone entities is not allowed"), 10.2 and 12.1 (`NameError: name 'spatial'`), 10.5 (`select`
returned `[]`, `model.create_entity`, then `spatial.assign_container` on the zone), 12.2 and 12.3,
while Claude passed all five creation cases and two of three containment cases from the same
prompt. The sheet wrote every call as `spatial.assign_container(products=..., relating_structure=
...)` and said once at the top that calls go through `ifcopenshell.api.run("module.function",
model, ...)`; the 7B copies the notation, and `spatial` was not a name in the sandbox. The child
now binds every module the sheet names as a model-bound proxy (`code_sandbox.API_MODULES`; an
explicit model as first argument is dropped; `type` doubles as the builtin so `type(x)` keeps
working), the sheet's header states the convention, and the prompt's example is written in it.
Rejected: keeping the long form and teaching it harder, because the 7B copies what it sees and
this is the same lesson as binding the helpers (0/6 → 4/6 on section 1).

**review 6 — Creation has a recipe; `model.create_entity` is forbidden.** The prompt's one
abstract sentence ("select() returns a list holding the entity they attach to") produced `[]`
from `select` (zero targets skips `modify`: three attempts, a rejection) or a raw
`model.create_entity`, which mints no GlobalId, so the diff could not have seen the zone even
when the code ran. The prompt now says: `select()` returns the project (or the storey of a new
space), `modify()` calls `root.create_entity(ifc_class=..., name=...)`, a space is aggregated
under its storey, elements are grouped into a zone, zones are never placed in storeys; the
decline rule reads "names nothing to change or create" instead of "no identifiable target".
`model.create_entity` is a forbidden pattern whose text names `root.create_entity`, one error
string through C-4. The sheet's `group.add_group` line no longer claims to make a "zone-like
group": in ifcopenshell 0.8 it has no `ifc_class`. Rejected: allowing zero targets for a creation
(the harness cannot tell "found nothing" from "creating", and R-1 skips `modify` on zero targets
by design).

**review 6 — A live failure keeps its code.** Every `FailureRecord` had `ifc_context = {}`, so
the code behind today's failure could only be inferred from the bake-off artifact. The
three-strikes path now stores the last block in `ifc_context["code"]`. Rejected: a new column;
the JSON field was on the model, empty.

**review 6 — A new object's attachment is measured.** The first live creation the 7B got through
("Acoustic Zone 1", project selected) also ran `aggregate.assign_object` for the zone under every
storey, so the zone ended up decomposing the Roof; the card showed "IfcZone added" and nothing
else, the approve went through, and the benchmark's integrity column would have scored it clean.
The snapshot tracked container, materials, classifications, groups and type object, not
aggregation, and a new object has no "before" to diff against. Two additions: `Parent` (the
object an occurrence directly decomposes, by name) joins the relationship values, so re-parenting
an existing space is one row on the space, charged to the child as a container move is; and the
snapshot records what each object hangs from (decomposition parent, else direct container) so
`unexpected` and `scope_error` report a new object attached to a pre-existing entity outside the
selection, with a repair text that says what `select()` must return. Rejected: tracking a
parent's children list (a storey's fifty spaces as one row, and a legitimate re-parenting charged
to both storeys), and trusting the prompt sentence "zones are never placed in a storey", which
the 7B had just ignored.
