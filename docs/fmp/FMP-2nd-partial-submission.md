# Castor — Bi-Directional LLM Assistant

## 2nd Partial Submission — Pilot Demonstration, AI Process & Adoption Analysis

**Group 4**: Pavla Hornická, Maria Makri, Erez Bader, Carlo Cogni, Islam Mohamed Sabra Ahmed
**Module 10** — Final Master's Project
**MSc in Artificial Intelligence for Architecture & Construction** — Zigurat Institute of Technology
**May 2026**

---

## 0. Submission Overview and Scope Evolution

The mandatory pilot demonstration video (§1.1) is submitted as a separate file. It shows Castor's two foundational paths in operation: a natural-language **Ask** query that returns cited evidence from the project's IFC model and accompanying technical documents, and a **Modify** proposal that escalates through risk tiers under live validation, ending in an approved write to the IFC file and a corresponding Git commit.

Since the 1st partial, the operational scope has broadened. The read-and-write core converged faster than the milestone plan anticipated, freeing capacity to extend Castor across the BIM lifecycle. Three new surfaces share the same architectural substrate (semantic retrieval, locally hosted language models, project-scoped versioning, role-based access):

- **Explore** — direct IFC navigation and entity inspection, complementing the natural-language Ask path.
- **Facilities** — a 7D Facilities Management module covering Asset Register, Work Orders, an Occupant Portal and an Export Reconciliation step that writes facility changes back into the IFC file.
- **Schedule** — 4D scaffolding now in place, with 5D cost integration as the next planned milestone.

This expansion is best read as efficient reinvestment of the 1st partial's primitives, not a change of project. The same Risk-Stratified Autonomous Action (RSAA) tier model and Retrieval-Augmented Verification (RAV) pattern continue to govern every proposed change, regardless of which surface initiates it.

## §1.2 AI Process Description

### (a) Operational logic

Three AI-mediated surfaces share one substrate: **Ask** (read), **Modify** (write) and **Facilities Intent** (work-order generation from natural-language requests). All three follow the principle introduced in the 1st partial — *Minimal Authority*: the language model is granted the smallest decision space the task requires.

The model is responsible for what only language can do — understanding intent, classifying the kind of operation requested, extracting parameters, generating multi-step plans and, in the most permissive tier, drafting code. Everything else (checking that property names exist, validating types, executing the change, version-controlling the file, recording the audit trail) is performed by deterministic, tested code. Every modification is staged as a proposal; the user always sees a diff preview and explicitly approves before anything is written. Approval is the change-management primitive, carried into §1.3.

### (b) Workflow sequence, constraints and assumptions

The Modify path is organised as a four-tier ladder. The system always attempts the safest tier first and escalates only on validated failure.

**Tier 0 — Feasibility.** Before any tier executes, the system verifies that the user's intent is actually actionable. A valid request must specify *what* to change, *which* entity to change it on, and *what value* to set. Vague requests are rejected here with an explanation rather than escalated. Tier 0 protects the rest of the pipeline from cascade failures driven by ambiguous prompts and protects the user from silently incorrect outcomes.

**Tier 1 (GREEN).** Single, certified property changes. The model emits a structured intent; a validator checks the proposal against the IFC property-set standard, combines the model's confidence with a deterministic groundedness score, and only then presents a diff preview. Approval triggers a tested handler that performs the write, commits to the project's Git history and synchronises the database index.

**Tier 2 (ORANGE).** Multi-step plans. The model generates an ordered list of operations against a fixed schema. Each step is independently validated; the user reviews the full plan; the executor then walks the steps in order, reusing the Tier 1 handlers.

**Tier 3 (RED).** Custom code, reserved for changes outside predefined patterns. The model drafts code; a second model pass scores how well that code matches the original request (aligned, partial or misaligned); the code runs in a sandbox on a temporary copy of the file under strict execution constraints, and only replaces the original on success.

**Facilities AI** (new since the 1st partial) follows the same logic. A short classifier decides whether a natural-language request describes a single work order, a batch or no actionable work at all; a planner drafts the work-order list; the user confirms; a state-machine service then creates each order with role-gated transitions. An Export Reconciliation step replays accumulated facility changes back into the IFC file at export time, closing the loop between operations and the model.

**Cross-cutting constraints.** Geometry is never modified at any tier. Inference is local-only, preserving the 1st partial's data-sovereignty commitment. Every model call has a wall-clock cap and a maximum output budget. The IFC file remains the source of truth; the database is its queryable index.

### (c) Intermediate calculations and model reasoning

A few internal mechanisms make the tiers above defensible. **Confidence is not trusted alone**: the model's self-reported confidence is combined with a deterministic match score against the property-set standard, so a single weak signal cannot pass. **Past successes are reused**: validated Tier 2 and Tier 3 outcomes are harvested into a small library of examples retrieved at proposal time, biasing the classifier toward patterns that have already worked. **Retrieval-Augmented Verification (RAV)**, introduced in the 1st partial, runs in parallel with every proposal — it queries the project's documents for clauses related to the proposed change and labels each retrieved excerpt as confirming, conflicting or irrelevant. RAV is advisory by design: it gives the user a document-backed second opinion without blocking the workflow.

## §1.3 Adoption Process Analysis

### (a) Integration pathways

**IFC interoperability.** Castor reads and writes standard IFC files through open-source libraries, so it works alongside any tool that conforms to IFC2x3 or IFC4. Every approved modification becomes a commit in the project's Git history with a human-readable diff, allowing rollback at any point.

**Identity and roles.** Access is enforced through two layers: a project-level membership tier (owner, editor, viewer) plus nine functional roles (building owner, facilities manager, maintenance engineer, contractor, subcontractor, tenant, occupant, auditor, consultant) with validity windows tied to contract durations. The model is compatible with single-sign-on through standard authentication backends.

**Language-model connectivity.** A tiered strategy is documented for production: a local model is the live default and preserves data sovereignty; a "bring-your-own-key" cloud option and a fully managed inference tier are deferred. Adding a tier is a configuration change, not a refactor.

**4D and 5D extension.** The Schedule surface is already in place. Linking facility assets and a forthcoming bill-of-quantities entity to schedule activities will deliver 5D cost integration. External building-management-system and IoT integration is specified in the design but not yet built.

### (b) Change management

**Approval flow as the change-management primitive.** Every AI-driven mutation routes through a diff preview and explicit human approval; the four colour-coded tiers (rejected, green, orange, red) communicate risk visually before the user clicks. This maps directly to ISO 19650 information-management expectations: every change carries an author, an approver and a recorded justification.

**Role-aware interface.** Tenants and occupants see only a portal — an intake form and the status of their open requests. Contractors see permits and the work assigned to them. Facilities managers see the full operations surface. Designers and engineers see the Ask, Modify and Explore tools. Onboarding a new user is, in essence, choosing a role.

**Help on every page.** Every meaningful tab ships with a "?" button that opens a five-section explainer (what it is, lifecycle, how to use it, who can do what, caveats). The convention is enforced as a merge gate so help text cannot drift behind the feature.

**Real-time feedback.** Pipeline progress and operational state changes are streamed live to the user; every mutating action returns a confirmation. Silent state changes are not allowed.

### (c) Scalability and maintenance

**Project isolation** keeps blast radius bounded — each project has its own version history, its own embedded vector index and its own real-time channels.

**Index refresh** runs incrementally on document ingest; a periodic batch reindex command is on the roadmap. A shared vector space means cross-domain queries (model + documents) need only one embedding model.

**Test maturity.** 434 automated tests pass across the four delivered facilities milestones; per-application test runs and a code-style gate are mandatory before any merge.

**Long-term governance.** Every approved AI-assisted change is a Git commit, so the project history is the auditable record of who decided what, when and on what evidence. Rollback is a single revert.

**Open gaps acknowledged honestly.** No observability layer is yet wired in. Horizontal scaling has not been load-tested. The reindex is not yet exposed to operators. External BMS and IoT integration is deferred. These are explicit candidates for the 3rd partial submission.

## Closing Reflection

The 2nd partial closes the feasibility loop on Castor's read, write and facilities-management core: each path is operational, tested end-to-end against real IFC files, and routed through the same governance pattern. The 3rd partial submission (12 July 2026) will move the next layer from roadmap to results — 4D and 5D integration through the Schedule surface, the remaining facilities milestones, the cloud-LLM tier and an observability layer.
