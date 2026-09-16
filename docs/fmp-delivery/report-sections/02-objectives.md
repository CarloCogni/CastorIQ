# {yellow}Objectives and research questions{/yellow}

{yellow}The central claim is that a bi-directional, document-grounded, human-in-the-loop system can materially close the Split Reality gap without compromising professional accountability or data sovereignty. It decomposes into three research questions with measurable success criteria.{/yellow}

{yellow}**RQ1.** Can IFC entity properties and unstructured project documents be co-located in a single semantic substrate at sufficient quality to support cross-domain retrieval? Success criterion: questions with one right answer (counts, storey names, schema) answered correctly over public IFC models the system has never seen.{/yellow}

{yellow}**RQ2.** Can LLM-proposed IFC modifications be made safe enough for professional use, auditable and reversible? Success criteria: no approved change touches anything outside the entities the user was shown; every proposal is measured correct, repaired or rejected, never applied on the model's own report; every accepted change is a Git commit that a single revert rolls back. The pass rate of a local model on a benchmark is reported as the ceiling of that model, not as a criterion.{/yellow}

{yellow}**RQ3.** Can the pipeline run on consumer-grade hardware without cloud dependency and without loss of interoperability? Success criterion: end-to-end operation with 8 GB of graphics memory, IFC4 as the native schema and IFC2x3 uploads converted in-pipeline.{/yellow}

{yellow}A fourth question emerged during pilot testing: whether an independent verification layer (RAV) can cross-check proposals against project documents without inheriting the agreement bias of the same model family. The conflict scanner was measured, diagnosed and improved (Section 5.2.2) and the per-proposal check was tested by hand (Section 5.4); the residual is the principal open item of Section 6.{/yellow}
