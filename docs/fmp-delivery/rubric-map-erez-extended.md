# rubric-map-erez-extended.md

Status: ✅ done, 🔶 in progress, ⬜ open. Updated 2026-09-03.
Owner: Erez Bader
Defense Focus: Frontend stability, RAG ingestion constraints, and user interface experience.

## 3. Technical implementation (2.0)

**Evidence:** 
* Erez tested ingestion constraints between formatted PDF briefs and clean text files. He identified 400 Bad Request errors due to token overflow in the embedding space.
* He validated the Retrieval-Augmented Generation engine against language hallucinations and context leaking using Hebrew versus English queries.
* He tested browser memory limits and WebGL rendering stability during heavy IFC loads on the frontend.

**Gaps to close before 27 Sep:**
* ✅ Confirmed Hebrew is not supported by the PostgreSQL and pgvector database build [cite: 3]. This is documented as a system constraint.
* ⬜ Implement a pre-processor to strip PDF formatting to prevent context window overflow during document ingestion.
* ⬜ Implement Strict Grounding in the system prompt to prioritize retrieved chunks over the internal model weights of the selected Ollama model [cite: 3].
* ⬜ Resolve WebGL context geometries failing to clear during model switching to prevent memory leaks in the viewer.

## 4. Design thinking & interdisciplinary integration (1.75)

**Evidence:** 
* Erez conducted user interface visibility testing on the AEC-native frontend, specifically evaluating the Bootstrap 5 property table responsiveness for long ArchiCAD names [cite: 3].
* He tested the Modify Agent's intent detection for entity recognition to ensure it accurately bridges natural language to IFC schema classes.
* He evaluated compliance calculation guardrails and tested the limitations of the agent handling multi-step engineering logic.

**Gaps to close before 27 Sep:**
* ✅ Implemented text wrapping and hover tooltips for long property names in the Django Templates interface [cite: 3].
* 🔶 Add row-level checkboxes in the Validation table for selective user commits.
* ⬜ Enhance Fuzzy Search and Intent Detection to fix rigid entity matching requirements.
* ✅ Confirmed the Modify agent's rejection of multi-step engineering logic is an intentional architectural guardrail to prevent the AI from guessing calculations.
