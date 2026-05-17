BIM QA Platform — Project Brief
Problem Statement

An MSc in AI assignment requires producing three deliverables using the Plannerly platform:

    A completed project contract document generated with AI assistance

    An AI-generated model checking ruleset

    A combined QA report showing model check results aligned with quantity/cost estimation requirements

Rather than using Plannerly directly, a custom Django web app can replicate the minimum required workflow, produce the same deliverables, and serve as a genuine software project in its own right.

The core problem Plannerly solves — and that this app must also solve — is the AI estimation illusion: AI can read a BIM model and produce a professional-looking estimate even when the underlying model data is incomplete, misnamed, or unmeasurable. The workflow must prove that model data is trustworthy before any estimate is generated. That proof is the purpose of the app.

The six-stage workflow that connects a raw model to a trusted estimate is:
Requirements → Contract → Verification → Dashboard → Approval → Estimate
Minimum Requirements (Assignment Scope)

The minimum viable app must produce the three assignment deliverables and nothing more.
1. Project / Contract Module

    Create a project with: name, purpose, discipline, element category (e.g. walls, columns, ducts, doors), assembly/classification code, and scope description

    The user defines which fields are required for estimation purposes for that element type

    AI assistance means: a text prompt generates a draft scope/contract description for the project

    Output: a printable/exportable project document showing purpose, scope, required data fields, and assumptions

2. Ruleset Generator

    For a given element category and classification code, define a list of checkable requirements

    Each requirement maps to a rule: field name, check type (exists / not empty / equals value / numeric range / matches pattern), and severity (error / warning)

    AI assistance means: plain-English input (e.g. "all structural columns must have a load-bearing classification and a cross-section area") generates a structured JSON rule

    Rules are not hardcoded — the user defines them per project and element type, so the same engine works for walls, beams, ducts, doors, or any other BIM category

3. Check Engine + QA Report

    Accept a CSV/Excel upload of BIM element properties (exported from any BIM authoring tool — no IFC parser needed)

    Run each rule against each row in the uploaded file

    Produce a results table: element ID, rule code, pass/fail, failing field, severity

    Produce a summary report: total elements checked, pass count, fail count, percentage compliant, list of issues grouped by rule, and a short narrative (AI-generated or template-based) explaining whether the model is usable for estimating

    Report must be exportable (PDF or downloadable HTML page)

Sample Data / Demo Scenario

Use this as the default project and test case for the first working build. The specific element type does not matter — what matters is proving the workflow functions end to end:
Field	Value
Project name	(any — e.g. "Office Block QA")
Discipline	(any — e.g. Architecture, Structure, MEP)
Element category	(any — e.g. walls, columns, ducts, doors)
Classification code	(any standard code — e.g. Uniclass, OmniClass, NRM)
Scope	(one discipline or one element type for the first run)
Required fields	(user-defined per project — e.g. type_name, area, volume, fire_rating, material)

A sample CSV for testing should have columns matching the required fields defined by the user, with some rows intentionally missing values to trigger failures and verify the check engine is working.
Possible Solution (To Be Polished)
Stack

    Backend: Django + Django REST Framework

    Database: SQLite for development, PostgreSQL for production

    AI calls: OpenAI API (GPT-4o or similar) for generating contract text and plain-English → rule conversion

    Frontend: Django templates + minimal JS (no heavy framework needed for MVP)

    Report export: WeasyPrint (HTML → PDF) or a downloadable HTML report

Django App Structure

text
bim_qa/
├── projects/        # Project, ContractSection, Requirement models
├── rules/           # Rule model, ruleset generator view, AI call
├── checker/         # ModelElement (CSV import), CheckResult model, check engine
├── reports/         # Report model, summary view, PDF export
└── templates/       # Shared templates + per-app templates

Data Models (Minimal)

python
# projects/models.py
Project         → id, name, purpose, discipline, scope, classification_code, element_category, created_at
ContractSection → project FK, title, body (AI-generated text), order
RequiredField   → project FK, field_name, data_type, description
# RequiredField is the key generic element — the user declares which fields
# must exist for this project's element type to be estimable

# rules/models.py
Rule → project FK, code, field_name, check_type, expected_value, severity, description
# check_type options: exists | not_empty | equals | numeric | numeric_range | regex

# checker/models.py
ModelElement → project FK, element_id, raw_data (JSONField)
CheckResult  → element FK, rule FK, passed (bool), failing_field, notes

# reports/models.py
Report → project FK, generated_at, total_elements, pass_count, fail_count, narrative (AI text)

Key Views / Pages
URL	Purpose
/projects/new/	Create a project, define element category and required fields, trigger AI contract draft
/projects/<id>/rules/	View, edit, or AI-generate rules from plain-English input
/projects/<id>/check/	Upload CSV/Excel export, run all rules, view results table
/projects/<id>/report/	View combined QA report, download PDF
AI Integration Points (Two Calls Only)

    Contract generation: Send project name, discipline, element category, scope summary, and required fields → receive a structured contract/scope document section

    Rule generation: Send a plain-English requirement and the element category → receive a JSON rule object with field, check type, and severity

These two calls cover the entire "AI assistance" requirement of the assignment, and both are fully generic — they work for any element type because the element category and required fields are passed dynamically as inputs.
How It Could Be Expanded Later

These are out of scope for the assignment but worth noting for future development:

    IFC file parsing using ifcopenshell to import model data directly instead of CSV, eliminating the manual export step and supporting any IFC-compliant BIM tool

    Multi-discipline projects so a single project holds separate rulesets for architecture, structure, MEP, and facade simultaneously, each with its own conventions and required fields

    Rule library where common rulesets for known element categories can be saved, reused, and shared across projects without rewriting from scratch

    Approval workflow with sign-off stages and a timestamped audit trail per requirement, matching the full Plannerly six-stage chain

    Estimating dashboard that feeds verified quantities into a cost dashboard via a rates API or user-maintained rate library

    Multi-model clash detection to identify duplicate or conflicting element data when multiple teams submit models for the same project

    User accounts and project permissions for team use rather than single-user local deployment

    Webhook or plugin integration to push model data directly into the app from Revit, ArchiCAD, or other authoring tools without a manual export step

Last updated: May 2026 — Assignment deadline: 31 May 2026, 15:59