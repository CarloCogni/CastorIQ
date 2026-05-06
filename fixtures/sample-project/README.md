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
├── README.md                 (this file — checked in)
├── PROVENANCE.md             (sources, hashes, licenses — checked in)
├── architectural.ifc         (NOT in git — drop in manually)
├── structural.ifc            (NOT in git — drop in manually)
└── docs/
    ├── fire-strategy.pdf     (NOT in git)
    ├── thermal-spec.pdf      (NOT in git)
    └── acoustic-notes.pdf    (NOT in git)
```

Filenames are matched literally by `provision_sample_project` — keep
exactly `architectural.ifc` and `structural.ifc` (lowercase, no spaces).
PDFs in `docs/` are picked up by glob, so any name is fine.

The command detects missing files and prints all the paths it expected.

See `PROVENANCE.md` for recommended sources and licensing notes.
