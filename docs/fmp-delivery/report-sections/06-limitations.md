# Limitations, ethical considerations and risk analysis

## 6.1 Four limitations and their current status

**Base models write weak IfcOpenShell code.** A 7B code model hallucinates helpers and mis-selects subtypes. Status: contained by design. Because the code runs on a copy and its effect is measured, a wrong attempt ends in a repair or a rejection, never in a wrong proposal that passes as right (46 of 46, Table 5.2). The cost is the pass rate, the ceiling of the local model rather than of the design. Grounding is the open half: exact class names are required and materials are not offered (Section 5.4).

**OCR on scanned documents.** The primary extractor silently degraded retrieval on rasterised PDFs. Status: mitigated for convenience. A free, open-source GLM-OCR layer runs locally as automatic fallback on sparse text, described by its published OmniDocBench result (Ouyang et al., 2024)[^3]. Its accuracy is not measured and is not expected to match frontier multimodal models. For precision, a user can extract the text of a scanned document with such a model, for example Gemini, save it as a text PDF and upload that instead.

{cyan}**Read-path grounding.** Testing found that entity descriptions carry properties from a fixed keyword list, so IsExternal is reported absent while the write path reads it from the same index, and that "none of the walls" can be asserted from half the walls (`erez.csv` rows 27, 42, 43, 90). Status: open; the fix is a one-line list change, re-embedding, and a coverage statement in answers.{/cyan}

**RAV verdict logic.** On the conflict scanner, measurement located the weakness the July draft reported in retrieval, and the fix raised recall from 0.20 to 0.77 on the harness (Table 5.3), though not yet on an independent corpus. The per-proposal Guardian now cites the applicable clause, but its prompt calls any different stated value a conflict, so it over-flags compliant numeric improvements and passes a non-compliant categorical value (Section 5.4). {magenta}Status: open; the fix is to derive the verdict from the comparison the clause implies.{/magenta} Until then the verdict is a retrieval aid with a correct citation; it never blocks.

[[IMAGE docs/fmp-delivery/figures/fig3-rav.png]]

Figure 3. The RAV layer in two places: the Guardian checks each proposal and reports on the card beside the measured diff; the conflict scanner checks the whole model on demand.

## 6.2 Ethical considerations and data governance

Data sovereignty is a foundational constraint. The default deployment runs on local infrastructure with open-weight models, so no project data leaves the user's environment, which matters under GDPR and ISO 19650 and for commercially sensitive models. BYOK is an explicit, user-initiated opt-out; an operator switch can disable cloud routing for a whole deployment, and provider failures hard-error instead of falling back silently. Every AI-proposed modification needs explicit human approval of a measured diff, and every approved change is a Git commit, an auditable chain of custody consistent with ISO 19650. The evaluation used public buildingSMART samples and documents written by the authors; no client data was used. AGPL-3.0 extends the governance choice to the code: self-hosting is unrestricted, and a modified network service must publish its modifications.

## 6.3 Risk analysis and open items

Five risks warrant mention. First, the Guardian verdict logic above: a check that flags compliant improvements or passes violations offers little verification value until fixed. Second, sample size: 95 prompts, 26 conflict cases and 36 Ask answers, one machine and one model per row. {magenta}Third, no load testing has been done; a proposal approved after its file changed is refused by the fingerprint check, but behaviour under many concurrent users is unqualified.{/magenta} Fourth, no expert labelling has been scored against the harness; the per-prompt protocol that would yield an agreement statistic is the highest-leverage open item before the defence. {cyan}Fifth, security testing in September 2026 found that a viewer-role account could commit a change (`erez.csv` row 60)[^5]. The editor tier is now enforced at the HTTP and WebSocket layer and tested. The same testing found that deleting a user or a document removes or anonymises the proposals, commits and conflicts that reference it (rows 63, 66, 96), which destroys audit evidence and remains open.{/cyan}
