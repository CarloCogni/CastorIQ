# Sample project fixture

The Castor beta auto-provisions a sample project for every approved user
(`provision_sample_project` management command, wired into the `Approve`
admin action). Everyone gets the same curated pair of IFC files plus a
few short specs so they can exercise Ask + Modify within seconds of
logging in. Two IFC files (architectural and structural) so Ask can
cross-cut between disciplines, and Modify can be exercised on either.

The actual binary fixtures aren't checked into git. Drop them in here
before deploying:

```
fixtures/sample-project/
├── README.md                              (checked in)
├── PROVENANCE.md                          (checked in)
├── render_pdfs.py                         (checked in — md → pdf one-shot)
├── architectural.ifc                      (NOT in git — drop in manually)
├── structural.ifc                         (NOT in git — drop in manually)
└── docs/
    ├── architectural-design-brief.md      (checked in — editable source)
    ├── architectural-design-brief.pdf     (checked in — rendered artefact)
    ├── structural-design-statement.md     (checked in — editable source)
    └── structural-design-statement.pdf    (checked in — rendered artefact)
```

Filenames are matched literally by `provision_sample_project` — keep
exactly `architectural.ifc` and `structural.ifc` (lowercase, no spaces).
PDFs in `docs/` are picked up by glob, so any name is fine.

The command detects missing files and prints all the paths it expected.

To re-render the design-doc PDFs after editing the `.md` sources:

```bash
cd fixtures/sample-project && uv run python render_pdfs.py
```

The script reads each `.md`, parses its YAML frontmatter for the
project header, renders MD→HTML via the `markdown` lib, wraps in a
letterhead-ish template, and writes the PDF next to the source via
PyMuPDF's `Story` + `DocumentWriter` API. Both `markdown` and
`pymupdf` are already in `pyproject.toml`.

See `PROVENANCE.md` for recommended sources and licensing notes.
