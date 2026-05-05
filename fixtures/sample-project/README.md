# Sample project fixture

The Castor beta auto-provisions a sample project for every approved user
(M4.2 management command, wired into the `Approve` admin action in M4.3).
Everyone gets the same curated IFC + a couple of short specs so they can
exercise Ask and Modify within seconds of logging in.

The actual binary fixtures aren't checked into git. Drop them in here
before deploying:

```
fixtures/sample-project/
├── README.md                 (this file — checked in)
├── PROVENANCE.md             (download URLs, hashes, licenses — checked in)
├── building.ifc              (NOT in git — drop in manually)
└── docs/
    ├── fire-strategy.pdf     (NOT in git)
    ├── thermal-spec.pdf      (NOT in git)
    └── acoustic-notes.pdf    (NOT in git)
```

The provisioning command in `core/management/commands/provision_sample_project.py`
detects missing files and prints the path it expected.

See `PROVENANCE.md` for recommended sources and licensing notes.
