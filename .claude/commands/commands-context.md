¿Generate a context dump for the area I'm about to work on. Ask me which subsystem I'm focusing on, then run the appropriate dump_context preset:

- writeback → `cd src && uv run manage.py dump_context --preset writeback --compact`
- rag/chat → `cd src && uv run manage.py dump_context --preset rag --compact`
- ifc → `cd src && uv run manage.py dump_context --preset ifc --compact`
- models → `cd src && uv run manage.py dump_context --preset models --compact`
- frontend → `cd src && uv run manage.py dump_context --preset frontend --compact`
- overview → `cd src && uv run manage.py dump_context --preset overview --compact`
- debug → `cd src && uv run manage.py dump_context --preset debug`

Read the output file and summarize what you see. Then ask me what I want to work on.