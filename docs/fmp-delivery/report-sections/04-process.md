# Development process

CastorIQ was developed across three overlapping phases, the last of which was reshaped by a measurement programme.

## 4.1 Concept and design (February 2026)

The first phase established the problem framing, the two-surface architecture (Ask for read, Modify for write) and a tiered, minimal-authority governance model for the write path, with the RAV layer as a document-grounded second opinion on every proposal. The data strategy was fixed: IFC via IfcOpenShell, documents via chunked PDF and DOCX extraction, both embedded into one vector space in PostgreSQL with pgvector. Local-only inference was a non-negotiable constraint, with Llama 3.1 8B via Ollama as the first reference model.

## 4.2 Core build and lifecycle extension (February to May 2026)

The read-write core converged faster than planned, and the freed capacity went into scope on the same substrate: Explore, a first cut of the Facilities suite, 4D scaffolding on the Schedule surface, and the start of the in-app IFC viewer. The tiered write path gained a fourth, feasibility tier, and the nine-role identity model was introduced. By the end of this phase 434 automated tests were a mandatory merge gate.

## 4.3 Completion, demonstration and measurement (May to September 2026)

The third phase completed lifecycle coverage: 5D cost on the Schedule surface, the full Facilities suite (Section 4.4) and the IFC viewer in the 4D Link workspace. All modules were demonstrated at Zigurat Student Week in June 2026 (Section 5.4). Hardening followed: GLM-OCR for scanned PDFs, BYOK, the real-time layer, an invite-only hosted beta at castoriq.io, and AGPL-3.0 licensing. Facilities gained Documents, asset edits written back into the IFC model, LLM-drafted work orders and requests, and 360 time-lapse comparison in Spaces.

The demonstration's most consistent criticism was the absence of hard numbers. The answer was a measurement programme (Section 5.2): a first write-path benchmark on 2026-08-05; a RAV baseline on 2026-08-30 that located the layer's bottleneck in retrieval; and on 2026-09-15 the retrieval fix and the replacement of the tiered write path by the pipeline of Section 3.2, each measured the day it landed. Two team members ran structured manual testing against independent ground truth throughout (Section 5.4). The automated suite now collects 2,672 tests.

## 4.4 Seven delivered surfaces

CastorIQ v1.0.0 delivers seven operational surfaces on one substrate: Ask (natural-language read over IFC and documents); Modify (the verified write path with RAV on the card); Conflicts (semantic mismatches between documents and model); History (the approved-change audit trail); Explore (structured IFC navigation without an LLM); Schedule (4D and 5D, with Primavera P6 import, earned value, DCMA 14-point health and Monte Carlo forecasting); and Facilities (7D operations across Dashboard, Assets, Work, Permits, Requests, Spaces and Documents). Lifecycle coverage from design intent to operations on one semantic substrate distinguishes CastorIQ from single-purpose BIM tools.
