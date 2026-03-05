# Prompting Guide

Prompt templates for working with LLMs on Castor. Copy, adapt, and paste. Each template is designed to produce code that
follows the project's conventions without having to re-explain them every time.

When using **Claude Code**, most of this is handled by CLAUDE.md automatically. These templates are for when you're
working in a regular chat session (Claude, ChatGPT, or any other LLM).

---

## General Development Prompt

The foundation template. Paste this at the end of any development request.

```
I'm continuing development on Castor, a bi-directional LLM assistant for IFC models.

Current code/context is attached/below.

Next task: [Describe what you want to build]

---
DEVELOPER NOTES:
- Clean code (Uncle Bob), Zen of Python, DRY
- Negative Space Programming: design by omission, guard clauses over nesting,
  remove boilerplate with context managers and base classes
- Docstrings on every module, class, and public method
- File header comment: # app/path/to/file.py
- Views and Forms are dumb — business logic in services/ layer
- Logging (never print), all in English
- Type hints on signatures and return types
- select_related / prefetch_related on every queryset

DELIVERY FORMAT:
- Explain the problem and solution BEFORE writing code
- Code in focused chunks (not full files) — I patch manually in PyCharm
- For modifications: show exact location with 3-5 lines of surrounding context
- For HTML: reference surrounding elements precisely, never "add this somewhere"
- Ask before touching more than 3 files
---

[paste CODE/CONTEXT here]
```

---

## Service Creation

When building a new service module from scratch.

```
Create a new service: [app]/services/[name].py

Purpose: [What this service does and why it exists]

It should:
- [Responsibility 1]
- [Responsibility 2]
- [Responsibility 3]

Called from: [view / management command / other service]
Dependencies: [other services, models, external libs]

Follow the existing service pattern in the project:
- Module docstring explaining role in the system
- Class with docstring explaining what it represents
- Public methods with docstrings (what, not how)
- Guard clauses, type hints, logging
- File header: # [app]/services/[name].py
```

---

## Model Addition

When adding new Django models.

```
Add a new model to [app]/models.py: [ModelName]

Purpose: [What this model represents in the domain]

Fields:
- [field_name]: [type] — [purpose]
- [field_name]: [type] — [purpose]

Relationships:
- FK to [Model] (on_delete=[CASCADE/PROTECT/SET_NULL])

Follow existing patterns:
- Inherit from UUIDModel
- class Meta with verbose_name, verbose_name_plural, ordering, indexes
- __str__ returning something meaningful
- Docstring on the class
- Consider: does this need an admin registration?

Show me: model class, migration command, admin registration if needed.
```

---

## Bug Investigation

When something isn't working and you need help diagnosing.

```
I'm seeing [describe the symptom].

Expected behavior: [what should happen]
Actual behavior: [what happens instead]

Relevant code attached below.

Before suggesting a fix:
1. Explain what you think is causing it
2. Show me exactly where the issue is
3. Then propose the minimal change to fix it

Don't refactor or improve anything else — just fix the bug.

[paste relevant code / traceback]
```

---

## Template / HTMX Work

When modifying Django templates with HTMX interactivity.

```
Modify the template: [app]/templates/[app]/[filename].html

Current behavior: [what it does now]
Desired behavior: [what it should do]

Context:
- This template extends [base template]
- The relevant section is near [describe location: after the sidebar, inside the tab content, etc.]
- Related view: [view class/function]

For HTMX changes:
- Which element triggers the request?
- What URL does it hit?
- What gets swapped, and where?

Show the exact HTML change with surrounding context (5+ lines before and after).
Use Bootstrap 5 utilities, dark theme, Castor blue (#3b82f6).
```

---

## IFC / Write-Back Work

When working on anything in the writeback pipeline.

```
I'm working on the write-back system.

Task: [What needs to change or be built]

Relevant tier: [1 GREEN / 2 ORANGE / 3 RED / unsure]

Before writing code, confirm:
1. Which tier handles this?
2. Which existing services are involved?
3. What's the validation flow?

Key constraints:
- IFC file is source of truth (DB is index)
- Minimal authority: LLM never exercises more power than needed
- Guardian advises, never blocks
- All changes go through Git commit

[paste relevant service code / intent examples]
```

---

## Code Review Request

When you want the LLM to review code you've written.

```
Review this code for:
- Convention violations (missing docstrings, type hints, file headers)
- N+1 query risks
- Business logic leaking into views/forms
- Unnecessary complexity (could be simpler?)
- Guard clause opportunities (nested conditionals to flatten)
- Missing error handling or logging

Be specific: file path, line reference, what to change and why.
Don't rewrite working code just to rewrite it.

[paste code]
```

---

## Refactoring

When code works but needs structural improvement.

```
This code works but needs refactoring.

File: [path]
Problem: [what's wrong structurally — too long, mixed concerns, etc.]

Constraints:
- Don't change external behavior
- Keep changes to this file unless absolutely necessary
- Propose the plan before writing code
- If it touches more than 3 files, stop and ask

[paste code]
```

---

## Context Dump Usage

When feeding the LLM a dump_context output for broad awareness.

```
I'm attaching a context dump of the Castor project.

[overview / writeback / rag / ifc / models] preset was used.

My question: [specific question or task]

Use the context to understand the codebase structure, then focus your
answer on the specific task. Don't summarize the context back to me.

[paste dump_context output]
```

---

## Tips

**Be specific about scope.** "Fix the sidebar" is vague. "The active project highlight in 
    `environments/components/sidebar.html` doesn't update when switching projects via HTMX" is actionable.

**Attach only what's relevant.** A 5,000-line context dump for a CSS fix wastes tokens and dilutes focus. 
    Use targeted `dump_context --grep` or paste just the relevant files.

**One task per prompt.** "Add a model, create the service, build the view, and write the template" will produce mediocre
    results on all four. Break it up.

**Let the LLM explain first.** The "explain before coding" instruction isn't just politeness — it catches 
    misunderstandings before they become wrong code. If the explanation is off, correct it before any code is written.


