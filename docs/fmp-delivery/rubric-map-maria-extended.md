# rubric-map-maria-extended.md

Status: ✅ done, 🔶 in progress, ⬜ open. Updated 2026-09-03.
Owner: Maria Makri
Defense Focus: Protocol design, Write-Back system validation, and architectural data integrity.

## 3. Technical implementation (2.0)

**Evidence:** 
* You evaluated Castor's local LLM orchestration and LangGraph architecture by testing pipeline compatibility across different local models [cite: 3].
* You documented that the Modify pipeline failed on the Qwen3 14b model but executed successfully on Llama 3.1 8b [cite: 3].
* You verified the internal Castor IFC viewer rendering against independent tools to ensure spatial hierarchy and geometry displayed correctly.

**Gaps to close before 27 Sep:**
* ⬜ Document model-choice sensitivity between the Performance tier and Standard tier in the setup guide.
* ⬜ Review and patch the conflict-to-prompt generation layer to fix auto-generated prompt failures. Your expertise in Python and custom script development makes you the ideal owner for this backend fix.
* ⬜ Investigate the internal Castor viewer rendering layer to support IFC files that currently only render correctly in Solibri Model Checker.

## 5. Evaluation & validation (1.75)

**Evidence:** 
* You established Solibri Model Checker as the independent ground-truth methodology for all IFC spatial and property validation.
* You designed the round-trip integrity protocol for Write-Back operations to prove Castor securely modifies models via natural language proposals.
* You designed the external blind review protocol to eliminate confirmation bias during the evaluation phase.

**Gaps to close before 27 Sep (October Defense Preparation):**
* 🔶 Execute the round-trip integrity protocol utilizing IfcOpenShell [cite: 3]. You must verify that geometry, GlobalIds, and modified properties survive intact in Solibri after a Castor commit.
* 🔶 Execute the external blind review protocol with independent domain expert Petru Conduraru. This single addition moves the grade more than any amount of extra presentation slides.
* ⬜ Implement an Excel export feature on the Semantic Conflicts sub-tab to allow offline review of findings.
