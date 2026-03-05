Help me plan and start a new feature for Castor.

Ask me to describe the feature, then:

1. Identify which apps are affected (core, environments, ifc_processor, documents, chat, writeback, embeddings)
2. Read the relevant docs (architecture.md plus app-specific docs)
3. Generate appropriate context dump: cd src and uv run manage.py dump_context --skeleton --full-apps AFFECTED_APP --docs RELEVANT_DOCS --compact
4. Propose a plan: which files to create/modify, in what order, following service layer pattern
5. Ask if I want to create a feature branch: git checkout -b feature/FEATURE_NAME
6. Start implementing the first file, explaining the approach before showing code