# Introduction and problem statement

The AECO sector is undergoing significant digital transformation, yet a critical synchronisation failure persists at the heart of most projects. BIM has become the standard for structured project data, with IFC serving as the open interoperability format standardised as ISO 16739. In parallel, a project's regulatory and contractual obligations remain encoded in unstructured technical documents. These two information domains evolve on separate timelines, are maintained by different disciplines, and are edited in different tools. We refer to this as the Split Reality problem.

When an engineer updates a fire-rating requirement in a report from EI30 to EI60, no automated mechanism propagates that change to the corresponding IfcDoor or IfcWall entities. Conversely, when a designer modifies a wall assembly in the model, the thermal report does not update itself. Love and Li (2000) measured direct rework costs of 2.4 and 3.15 percent of contract value on two audited projects, and FMI and PlanGrid (2018) attribute 48 percent of all rework on US jobsites to poor project data and miscommunication. The split extends beyond design coordination to operations and site verification. Each situation is the same problem: two authoritative information domains that must agree at every point in a project's lifecycle, and that no existing tool holds together at that resolution.

## 1.1 State of practice

Current industry practice addresses this gap through largely manual or uni-directional mechanisms. Commercial clash-detection platforms perform model-to-model checks but do not reason over unstructured text. NLP-based tools for building code compliance checking parse prescriptive requirements but remain read-only: they flag non-compliance without proposing corrective model modifications. Commercial CDEs manage file versioning but treat models and documents as opaque files with no semantic linkage. Recent research demonstrates LLM-based multi-agent frameworks that generate and edit BIM models from natural language (Du et al., 2024), and protocols that let LLMs manipulate IFC data through predefined tools and dynamic code generation (Nithyanantham et al., 2025). Both remain uni-directional from instruction to model, do not cross-reference technical documents, and check a change against the model's stated intent rather than by measuring what the executed code did to the file.

## 1.2 Gap identification

Three specific gaps justify CastorIQ's approach and structure the rest of this memory:

- **Gap 1.** No production-ready tool semantically links IFC entity properties to natural-language requirements in associated technical documents. Addressed by the unified vector store of Section 3.1.

- **Gap 2.** Existing compliance-checking research stops at detection. No validated workflow exists for LLM-proposed IFC modifications whose effect on the file is measured before a human approves it. Addressed by the write-back architecture of Section 3.2 and evaluated in Section 5.

- **Gap 3.** The industry lacks lightweight, privacy-respecting solutions that operate on locally hosted models. Most emerging tools depend on proprietary cloud APIs, which raises data-sovereignty concerns for projects under contractual confidentiality. Addressed by local-first deployment with an optional bring-your-own-key (BYOK) path.
