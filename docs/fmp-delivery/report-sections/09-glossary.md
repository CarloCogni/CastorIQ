# Glossary

Acronyms and technical terms used in the memory, listed alphabetically. Definitions are the ones this memory works with; the appendices carry fuller technical treatment where relevant.

**AECO**  Architecture, Engineering, Construction and Operations. The industry sector this project addresses.

**AGPL-3.0**  GNU Affero General Public License, version 3. Copyleft licence requiring that modifications hosted as a network service be released under the same terms.

**BIM**  Building Information Modelling. Structured, machine-readable representation of a built asset.

**Blind explanation**  One sentence written by a model that reads the generated code and the measured diff without seeing the user's request; the user compares it with what they asked.

**BYOK**  Bring Your Own Key. User-supplied API credential for optional cloud model access, as an opt-out of the local-first default.

**CDE**  Common Data Environment. Shared file and workflow platform for a project team; typically vendor-hosted.

**DCMA 14-point**  Defence Contract Management Agency schedule health assessment, applied to Primavera P6 schedules.

**GDPR**  General Data Protection Regulation. European legislation on personal data handling.

**GLM-OCR**  GLM-based Optical Character Recognition model used as the fallback text extractor for scanned and image-dominant PDFs.

**Guardian**  The user-facing name of the RAV layer on the Modify card.

**IFC**  Industry Foundation Classes. Open data model for BIM, standardised as ISO 16739.

**ISO 19650**  International standard on information management using BIM through the whole lifecycle of a built asset.

**LLM**  Large Language Model.

**Maximal verification**  This project's write-path principle: the model is given full authority to write the change as code, and what the code did to a copy of the file is measured and gated before a human approves it.

**MRR**  Mean Reciprocal Rank. Retrieval quality metric.

**OCR**  Optical Character Recognition.

**RAG**  Retrieval-Augmented Generation. Pattern in which retrieved document excerpts are supplied to an LLM at query time to ground its output.

**RAV**  Retrieval-Augmented Verification. This project's document-grounded second opinion layer, run on every Modify proposal.

**RSAA**  Risk-Stratified Autonomous Action. The four-tier minimal-authority governance ladder of the first write-path design (February to September 2026), replaced on 2026-09-15 by maximal verification.

**Scope gate**  The deterministic check that a change touched nothing outside the selected entities and no geometry; a violation sends the code back to the model, at most twice.

**TEK17**  Norwegian building technical regulation used as the fire-rating notation reference in project pilot testing.

**VRAM**  Video RAM. GPU memory available for model inference.
