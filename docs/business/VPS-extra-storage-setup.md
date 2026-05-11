# VPS Extra Storage — Hetzner Cloud Volume Setup

> Companion to `vps-deployment.md`. Plan for putting the paid Hetzner Cloud Volume to use on `castoriq.io`.
> Status: **not yet executed** — reference doc to come back to.

---

## Why this doc exists

The CCX13 has 80 GB of NVMe. Per `vps-deployment.md`, that one disk has to hold:

- OS, Docker, Castor image (~5–10 GB)
- `postgres_data` (Postgres 16 + pgvector — small now, grows with embeddings)
- `media_volume` → `/app/media` — per-project IFC files, uploaded documents, **per-project Git repos**. **Unbounded.**
- `ollama_models` (~1 GB)
- `static_volume` (small)
- **`/var/backups/castor`** — `scripts/backup.sh` keeps **14 days × (pg dump + media tarball)** locally before pushing to the Storage Box. With even 10 GB of media, that's ~140 GB of local backups alone.

The 80 GB disk runs out long before the user count does. A separate Hetzner Cloud Volume was purchased to fix this.

### About the "Synology NAS via Samba" pattern

Different shape, same goal. That setup uses an off-site NAS reached over the WAN via SMB/CIFS — slower, more failure-prone, only worth it when the NAS already exists. A **Hetzner Cloud Volume** is network-attached **block storage** in the same datacenter, mounted as ext4. From the OS's perspective it looks like a local disk — no SMB, no auth, no WAN latency. Right tool for "offload bulky/growing data from the app server" without the operational pain of remote NAS.

---

## What goes on the volume

**On the volume:**

1. **`MEDIA_ROOT`** — live IFC files, documents, per-project Git repos (the unbounded thing)
2. **`BACKUP_DIR`** — local 14-day backup retention (the other unbounded thing)

**Stays on the boot NVMe:**

- **Postgres data.** pgvector similarity search is latency-sensitive. Hetzner Cloud Volumes do ~10k IOPS at ~1–2 ms; local NVMe is ~100k IOPS at sub-ms. At beta scale Postgres is single-digit GB and benefits more from speed than from extra capacity. Revisit if Postgres data exceeds ~30 GB.
- **Ollama models** (~1 GB, rarely changes), **static_volume** (small), OS, Docker images.

**Unchanged:**

- The **Hetzner Storage Box** (€4/mo, 1 TB) stays as the **off-host backup destination**. The volume sits in the same datacenter as the VPS — it is not a substitute for off-host backup. Three copies, two media, one offsite still holds: live (volume) → local retention (volume, fast restore) → Storage Box (offsite).

### Why not Postgres on the volume

Beta workload is dominated by embedding similarity queries that hit pgvector indexes. Adding 1–2 ms per disk op compounds across an Ask session. Postgres also doesn't have the capacity problem media does — it's small now and grows slowly. Moving it adds an extra failure mode (volume detach) for no current benefit.

---

## Layout on the volume

```
/mnt/castor-data/
├── media/        ← bind-mounted into web + nginx as /app/media
└── backups/      ← BACKUP_DIR for scripts/backup.sh
```

One mount point, two clearly-named subdirectories. No Docker named-volume indirection — bind mounts are easier to inspect (`du -sh /mnt/castor-data/*`), back up, and reason about.

---

## Sizing

Hetzner Cloud Volumes cost **~€0.04/GB/month** and resize live (no downtime).

| Size | Price | Headroom guidance |
|---|---|---|
| 40 GB | ~€1.60/mo | tight — fits ~50 small projects, resize sooner |
| 100 GB | ~€4/mo | comfortable for the 30-user beta with typical 50–500 MB IFCs |
| 200 GB+ | ~€8+/mo | plenty of headroom; no resize for the foreseeable |

Undersizing is recoverable in minutes — start small if unsure.

---

## Migration steps

All commands run on the VPS as root. Single maintenance window — entire stack briefly down. Estimate **10–30 min** depending on current `MEDIA_ROOT` size.

1. **Attach the volume** in the Hetzner Cloud Console to the CCX13 server. It appears as `/dev/sdb` (verify with `lsblk`).
2. **Format and mount:**
   ```bash
   mkfs.ext4 -L castor-data /dev/sdb
   mkdir -p /mnt/castor-data/{media,backups}
   echo 'LABEL=castor-data /mnt/castor-data ext4 defaults,nofail,discard 0 2' >> /etc/fstab
   mount -a
   ```
3. **Stop the stack:** `docker compose -f docker/docker-compose.prod.yml down`
4. **Copy existing data across:**
   ```bash
   rsync -aH --info=progress2 \
     /var/lib/docker/volumes/castor_media_volume/_data/ \
     /mnt/castor-data/media/

   # Only if backups have already started accumulating locally:
   rsync -aH --info=progress2 \
     /var/backups/castor/ /mnt/castor-data/backups/

   # Match the web container's uid/gid (1000:1000 by default in the Castor image):
   chown -R 1000:1000 /mnt/castor-data/media
   ```
5. **Edit `docker/docker-compose.prod.yml`** — replace the `media_volume` named volume with a bind mount:
   - In the `web` service, change `media_volume:/app/media` → `/mnt/castor-data/media:/app/media`
   - In the `nginx` service, change `media_volume:/app/media:ro` → `/mnt/castor-data/media:/app/media:ro`
   - Remove `media_volume:` from the bottom `volumes:` block
   - Leave `postgres_data`, `static_volume`, and (if present) `ollama_models` untouched
6. **Update the backup cron** to set `BACKUP_DIR=/mnt/castor-data/backups` (in the crontab line or in a wrapper script — `scripts/backup.sh` already reads it from env, no script change needed).
7. **Bring the stack back up:** `docker compose -f docker/docker-compose.prod.yml up -d`
8. **Verify** (next section).
9. **After 7 days of clean operation**, delete the old Docker named volume: `docker volume rm castor_media_volume`. Don't do this on day one — it's the rollback path.

### No code changes in the Django app

`MEDIA_ROOT=/app/media` stays the same inside the container; the bind mount is invisible to Django. Same for `scripts/backup.sh` — it already honours `BACKUP_DIR` from the environment.

---

## Verification

End-to-end, in order:

1. `docker compose ps` — all three services healthy.
2. `curl https://castoriq.io/healthz/` — returns 200, free disk space reflects new layout.
3. Upload a test IFC via the UI. Confirm the file lands at `/mnt/castor-data/media/projects/<id>/ifc/<file>` on the host (not in the old Docker volume).
4. Open the test project in the UI, confirm IFC parses and Ask returns chunks (sanity check that Postgres + pgvector still see the embeddings — they should, the DB didn't move).
5. Run the backup once manually:
   ```bash
   BACKUP_DIR=/mnt/castor-data/backups bash scripts/backup.sh
   ```
   Confirm both artefacts appear under `/mnt/castor-data/backups/` and that rclone push to the Storage Box succeeds.
6. **Restore drill** (M6 pre-flight item anyway): untar the just-created `media-*.tar.gz` into a throwaway directory, confirm per-project Git repos and IFCs are intact.
7. `df -h /mnt/castor-data` and `df -h /` — confirm media + backups have moved off the boot disk.

---

## Files touched

| Path | What changes |
|---|---|
| `docker/docker-compose.prod.yml` | `web` and `nginx` mounts switch from `media_volume` named volume to `/mnt/castor-data/media` bind mount; `media_volume:` entry removed from the bottom `volumes:` block |
| Crontab entry for nightly backup | `BACKUP_DIR=/mnt/castor-data/backups` prepended |
| `docs/business/vps-deployment.md` | Topology diagram adds the volume; persistent-volumes section reflects new layout; backups section notes new `BACKUP_DIR`; cost table adds the volume line |

No changes to `scripts/backup.sh`, `src/config/settings/*.py`, or any model — `MEDIA_ROOT` is unchanged inside the container.

---

## What this does NOT do

- **Does not move Postgres.** Stays on NVMe (see "Why not Postgres on the volume" above).
- **Does not replace the Hetzner Storage Box.** Storage Box remains the offsite backup destination.
- **Does not change the backup script or schedule.** Same `scripts/backup.sh`, same nightly cron — only the destination directory changes.
- **Does not change anything in the Django app.** Pure infra change.

---

## Cost impact

Add to `vps-deployment.md` cost table:

| Item | Monthly |
|---|---|
| Hetzner Cloud Volume (e.g. 100 GB) | ~€4 |

All-in goes from ~€30/mo infra → ~€34/mo (at 100 GB). Negligible at this stage.

---

## Rollback

If anything misbehaves after migration:

1. `docker compose down`
2. Revert the `docker-compose.prod.yml` edits (back to `media_volume:` named volume).
3. `docker compose up -d` — the old `castor_media_volume` is still intact (don't delete it for at least 7 days).
4. New uploads since the migration will be on the volume — `rsync` them back if needed: `rsync -aH /mnt/castor-data/media/ /var/lib/docker/volumes/castor_media_volume/_data/`.
5. Detach the volume in the Hetzner Console and (optionally) delete it.
