.claude/skills/help-modals.md
# Skill: Help modals — the "?" pill on every page

Read before creating or editing any tab / sub-tab / full-page view in Castor.

**This is a non-negotiable UX commitment per the user.** Every meaningful page must ship with a discoverable help affordance so cold users understand what the page does, how to use it, and where the edges are. The Asset Register is the canonical example — mirror it.

---

## When it applies

- **YES**: Every tab and sub-tab with non-trivial IA — lists, lifecycle, bulk ops, configuration. Examples: Assets, Work, Permits, Requests, Maintenance, Systems, Ask, Modify, Conflicts, History, Explore, Schedule.
- **NO**: Drawers or side panels reached *from* a page that already has a help modal (parent modal covers them). Admin pages (different audience).

If you're creating a new top-level page and don't know whether it qualifies: add one. Over-helping is fine; leaving users stranded is not.

---

## Pattern — copy this exactly

### 1. Page heading: add the `?` pill

Inside the page's top-bar / header row, right after the title + count badge:

```html
<div class="d-flex flex-column gap-3 facilities-scope" id="facilities-<thing>-body">
    <div class="d-flex align-items-center gap-3 flex-wrap">
        <div class="d-flex align-items-center gap-2">
            <i class="bi bi-<sub-tab-icon> fs-5" style="color: var(--castor-facilities, #14b8a6);"></i>
            <h5 class="m-0"><Page Title></h5>
            {% if page_obj %}
            <span class="badge rounded-pill" style="background-color: rgba(20,184,166,0.15); color: var(--castor-facilities, #14b8a6); border: 1px solid rgba(20,184,166,0.35);">
                {{ page_obj.paginator.count }} thing{{ page_obj.paginator.count|pluralize }}
            </span>
            {% endif %}
            <button type="button"
                    class="help-pill"
                    data-bs-toggle="modal"
                    data-bs-target="#<thing>HelpModal"
                    title="How <Thing> works"
                    aria-label="How <Thing> works">
                <i class="bi bi-question-circle"></i>
            </button>
        </div>
        <!-- … action buttons on the right … -->
    </div>
    <!-- … page body … -->

    <!-- Help modal — "How <Thing> Work" -->
    {% include "<app>/components/<thing>_help_modal.html" %}
</div>
```

**Scope class on the wrapper** — pick the tint:
- Facilities pages (teal `#14b8a6`): `.facilities-scope`
- Modify / writeback pages (purple `#8b5cf6`): `.modify-scope`
- Everything else (default blue `#3b82f6`): no scope class

### 2. The modal — five-section standard shape

File: `<app>/templates/<app>/components/<thing>_help_modal.html`

```html
<!-- <app>/components/<thing>_help_modal.html — "How <Thing> Work" -->

<div class="modal fade" id="<thing>HelpModal" tabindex="-1" aria-labelledby="<thing>HelpLabel" aria-hidden="true">
    <div class="modal-dialog modal-lg modal-dialog-centered modal-dialog-scrollable">
        <div class="modal-content border" style="background-color: var(--bs-tertiary-bg);">

            <div class="modal-header border-bottom px-4 py-3"
                 style="border-color: var(--bs-border-color) !important;">
                <h5 class="modal-title fw-semibold fs-6 mb-0" id="<thing>HelpLabel">
                    <i class="bi bi-<sub-tab-icon> me-2" style="color: var(--castor-facilities, #14b8a6);"></i>
                    How <Thing> Work
                </h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
            </div>

            <div class="modal-body px-4 py-4">

                <!-- 1. What is it? -->
                <p class="fw-semibold fs-7 mb-2">
                    <i class="bi bi-box-seam me-1" style="color: var(--castor-facilities, #14b8a6);"></i>
                    What is a <thing>?
                </p>
                <p class="text-secondary fs-7 mb-4">
                    <!-- 2-3 sentences. What role does this entity play in the user's mental model? -->
                </p>

                <!-- 2. Lifecycle / How it works -->
                <p class="fw-semibold fs-7 mb-2">
                    <i class="bi bi-arrow-repeat me-1" style="color: var(--castor-facilities, #14b8a6);"></i>
                    Lifecycle
                </p>
                <!-- Badges-with-explanations list OR numbered steps. -->

                <!-- 3. How to do X -->
                <p class="fw-semibold fs-7 mb-2">
                    <i class="bi bi-plus-circle me-1" style="color: var(--castor-facilities, #14b8a6);"></i>
                    How to do <primary action>
                </p>
                <!-- The N main actions, each with a screenshot-style badge + paragraph. -->

                <!-- 4. Who can do what (role gating) -->
                <p class="fw-semibold fs-7 mb-2">
                    <i class="bi bi-person-check me-1" style="color: var(--castor-facilities, #14b8a6);"></i>
                    Who can do what
                </p>
                <!-- Bulleted list of role → permission. -->

                <!-- 5. Caveats -->
                <p class="fw-semibold fs-7 mb-2">
                    <i class="bi bi-exclamation-circle me-1" style="color: var(--castor-accent, #8b5cf6);"></i>
                    Caveats
                </p>
                <div class="d-flex flex-column gap-1 fs-8 text-secondary">
                    <span><i class="bi bi-dot"></i>Constraint / gotcha 1.</span>
                    <span><i class="bi bi-dot"></i>Constraint / gotcha 2.</span>
                    <span><i class="bi bi-dot"></i>What this doesn't do (scope boundary).</span>
                </div>

            </div>
        </div>
    </div>
</div>
```

---

## The `.help-pill` CSS lives once, in `core/base.html`

It is already defined. **Do not redefine it per-page.** Relevant excerpt:

```css
.help-pill {
    --help-accent: var(--castor-primary);
    background: transparent;
    border: 1px dashed color-mix(in srgb, var(--help-accent) 35%, transparent);
    color: var(--help-accent);
    font-size: 0.8125rem;
    line-height: 1;
    border-radius: 999px;
    padding: 0.25rem 0.5rem;
    transition: all 0.15s ease;
    opacity: 0.85;
}
.help-pill:hover, .help-pill:focus-visible {
    background-color: color-mix(in srgb, var(--help-accent) 8%, transparent);
    border-style: solid;
    opacity: 1;
    outline: none;
}
.facilities-scope { --help-accent: var(--castor-facilities); }
.modify-scope     { --help-accent: var(--castor-accent); }
```

Pages that predate this convention may have their own per-page equivalent (`.asset-help-btn`, `.wo-help-btn`). **Leave them alone** — per `feedback_no_churn_working_templates`, don't refactor a working page for DRY. New pages use `.help-pill`.

---

## Maintenance rule — keep modals fresh

**When a feature lands on a page that has a help modal, update the modal in the same change.** Stale help text is worse than no help text. A PR that adds a new action, lifecycle stage, or permission without touching the help modal is incomplete.

---

## Reference implementations

- `facilities/templates/facilities/components/asset_register_help_modal.html` — the richest example; 5 sections, comparison table, CSV column table, caveats.
- `facilities/templates/facilities/components/work_help_modal.html` — WO lifecycle.
- `facilities/templates/facilities/components/permit_help_modal.html` — Permits.
- `facilities/templates/facilities/components/action_request_help_modal.html` — Action requests.

When in doubt, copy the Asset Register file and rewrite the copy.
