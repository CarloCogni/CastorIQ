# Server Setup — Hetzner CCX13 Base Hardening

> Step-by-step runbook for taking a freshly provisioned Hetzner CCX13 from "I just
> SSH'd in as root" to "ready for the application stack." Tick each `- [ ]` as you
> go so a coffee break doesn't lose you the thread.
>
> **Scope:** OS baseline, user accounts, SSH, firewall, fail2ban, automatic
> security patches, swap, Docker, plus pointing the Namecheap DNS records at the
> box (zero-cost to do now, lets propagation finish in the background). Nothing
> application-specific — Postgres, Ollama, nginx, Let's Encrypt, the Castor
> compose stack, backups, and monitoring all live in a follow-up doc.
>
> **Why these choices:** see `vps-deployment.md`. This doc is purely *how*; that
> doc is *why*.
>
> **Source of truth for milestone state:** the M0 checklist in `live-roadmap.md`.
> Re-tick those boxes as you complete the corresponding phase here.

---

## Pre-flight assumptions

- Image: **Ubuntu 24.04 LTS** (Hetzner default for new CCX13s as of 2026-05).
- You are logged in via the Hetzner console or `ssh root@<vps_ip>` with your SSH
  public key already installed by the provisioning step.
- You have `<vps_ip>` (the public IPv4 from the Hetzner panel) and a chosen
  `<username>` for the non-root sudo account ready.
- DNS isn't strictly required for the box-side phases, but Phase 1 sets the
  Namecheap A records now so propagation finishes while you work through the
  rest. nginx + SSL still come in the follow-up.

Throughout this doc, replace `<vps_ip>` and `<username>` with your real values.

---

## Phase 0 — Sanity check & system update

Patch first, install the small utility belt we'll need in later phases.

```bash
apt update && apt upgrade -y
apt install -y curl ca-certificates ufw fail2ban unattended-upgrades \
               htop ncdu vim git rsync gnupg lsb-release
```

Set a recognisable hostname and a sensible timezone (logs become readable when
the box clock matches yours).

```bash
hostnamectl set-hostname castoriq-prod
timedatectl set-timezone Europe/Madrid
```

- [X] `apt upgrade` finished without errors
- [X] `hostname` returns `castoriq-prod`
- [X] `timedatectl` shows `Europe/Madrid` and `System clock synchronized: yes`
- [X] If the kernel was upgraded, `reboot` now and SSH back in before continuing

---

## Phase 1 — DNS records at Namecheap

Point the domain at the box now so propagation (typically 5–60 min) finishes in
the background while you work through the rest of this doc. No commands run on
the server for this phase — it's all in the Namecheap dashboard.

In Namecheap → **Domain List** → `castoriq.io` → **Manage** → **Advanced DNS**:

| Type     | Host  | Value      | TTL |
|----------|-------|------------|-----|
| A Record | `@`   | `<vps_ip>` | 1 h |
| A Record | `www` | `<vps_ip>` | 1 h |

Delete any default Namecheap parking records (e.g. `URL Redirect Record` on `@`,
or a `CNAME www → parkingpage…`) — they conflict with the new A records and
silently override them.

Verify from your laptop after a few minutes (use a public resolver to bypass any
cache on your ISP):

```bash
dig +short A castoriq.io @1.1.1.1
dig +short A www.castoriq.io @1.1.1.1
```

Both should return `<vps_ip>`. If they're empty, give it another 10 min.

- [X] `A @ → <vps_ip>` set in Namecheap, TTL 1 h
- [X] `A www → <vps_ip>` set in Namecheap, TTL 1 h
- [X] Default Namecheap parking / redirect records removed
- [X] `dig` returns `<vps_ip>` for both `castoriq.io` and `www.castoriq.io` from a public resolver

Don't wait for propagation to complete before moving on — Phases 2–8 are all
box-side and don't depend on DNS. Re-check `dig` during Phase 9 verification.

---

## Phase 2 — Non-root sudo user

Daily-driver account that isn't root. SSH key gets copied across so you can log
in immediately as the new user.

```bash
adduser <username>                     # set a real password — sudo will ask for it
usermod -aG sudo <username>
rsync --archive --chown=<username>:<username> /root/.ssh /home/<username>/
```

**Verify before doing anything else** — open a *new* terminal (keep the root one
open as a safety net) and confirm:

```bash
ssh <username>@<vps_ip>
sudo whoami      # must print: root
```

- [X] Created `<username>` with a password
- [X] Added `<username>` to `sudo` group
- [X] Copied `/root/.ssh` into the new home with correct ownership
- [X] Logged in from a fresh terminal as `<username>` and `sudo` works
- [X] Old root terminal still open (don't close it until Phase 3 verification passes)

---

## Phase 3 — SSH hardening

Lock SSH down: no root login, no passwords, keys only. Drop a small override file
in `sshd_config.d/` rather than editing the main config — survives package
upgrades cleanly.

```bash
cat <<'EOF' > /etc/ssh/sshd_config.d/10-castor.conf
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
KbdInteractiveAuthentication no
EOF

sshd -t                          # validates config, must print nothing
systemctl restart ssh
```

**Verify** from a fresh terminal:

```bash
ssh <username>@<vps_ip>          # should succeed
ssh root@<vps_ip>                # should be refused: "Permission denied"
```

- [X] `/etc/ssh/sshd_config.d/10-castor.conf` written
- [X] `sshd -t` exits cleanly
- [X] `ssh root@<vps_ip>` is refused
- [X] `ssh <username>@<vps_ip>` still works
- [X] Only **after** the above pass: close the root console

---

## Phase 4 — UFW firewall

Default-deny inbound, allow outbound, open exactly the three ports we'll need
(SSH, HTTP, HTTPS — HTTP/HTTPS are pre-opened so the nginx + certbot work in the
follow-up doc just works).

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow http
ufw allow https
ufw enable                       # answer "y" to the SSH-cutoff warning
ufw status verbose
```

- [X] `ufw status verbose` shows `Status: active`
- [X] Allow rules present for `OpenSSH`, `80/tcp`, `443/tcp`
- [X] Default policy: `deny (incoming), allow (outgoing)`

---

## Phase 5 — fail2ban

Brute-force protection on SSH. We installed it in Phase 0; now configure and
enable.

```bash
cat <<'EOF' > /etc/fail2ban/jail.local
[DEFAULT]
bantime  = 1h
findtime = 10m
maxretry = 5

[sshd]
enabled  = true
EOF

systemctl enable --now fail2ban
fail2ban-client status sshd
```

- [X] `/etc/fail2ban/jail.local` created
- [X] `systemctl is-active fail2ban` returns `active`
- [X] `fail2ban-client status sshd` lists the jail (currently 0 banned, that's fine)

---

## Phase 6 — Unattended security upgrades

Auto-apply security patches so the box doesn't rot between deploys.

```bash
dpkg-reconfigure --priority=low unattended-upgrades   # answer "Yes"
```

If you prefer to skip the interactive prompt, the equivalent non-interactive
version:

```bash
cat <<'EOF' > /etc/apt/apt.conf.d/20auto-upgrades
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
```

Confirm the dry-run picks up something to do (or cleanly reports nothing
pending):

```bash
unattended-upgrade --dry-run --debug | tail -n 20
```

- [X] `/etc/apt/apt.conf.d/20auto-upgrades` exists with both periodic lines
- [X] `unattended-upgrade --dry-run` runs without errors
- [X] `systemctl is-active unattended-upgrades` returns `active` (or `static`)

---

## Phase 7 — 4 GB swap file

Cushion for Postgres + Ollama spikes. CCX13 has 16 GB RAM, but embedding inference
+ a misbehaving Python process can squeeze it during indexing.

```bash
fallocate -l 4G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile

echo '/swapfile none swap sw 0 0' >> /etc/fstab
echo 'vm.swappiness=10'  > /etc/sysctl.d/99-swap.conf
sysctl --system
```

- [X] `swapon --show` lists `/swapfile` at 4G
- [X] `free -h` shows the swap line populated
- [X] `/etc/fstab` contains the swap entry (survives reboot)
- [X] `cat /proc/sys/vm/swappiness` returns `10`

---

## Phase 8 — Docker Engine + Compose plugin

Install from the **official Docker apt repository**, not Ubuntu's `docker.io`
package — that one ships an older engine and lacks the `docker compose` v2
plugin we'll rely on.

```bash
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | tee /etc/apt/sources.list.d/docker.list > /dev/null

apt update
apt install -y docker-ce docker-ce-cli containerd.io \
               docker-buildx-plugin docker-compose-plugin

usermod -aG docker <username>     # so the daily-driver user can run docker without sudo
```

Log out and back in as `<username>` for the group change to take effect, then
smoke-test:

```bash
docker run --rm hello-world
docker compose version
```

- [X] `docker --version` reports a Docker CE build (e.g. `Docker version 26.x`)
- [X] `docker compose version` reports a v2 build (e.g. `Docker Compose version v2.x`)
- [X] `docker run --rm hello-world` succeeds without `sudo` (after re-login)
- [X] `<username>` is in the `docker` group (`groups` shows it)

---

## Phase 9 — Final verification

Run the full sweep before declaring the box ready for the application stack.

```bash
# DNS (from your laptop, not the box)
dig +short A castoriq.io @1.1.1.1        # MUST return <vps_ip>
dig +short A www.castoriq.io @1.1.1.1    # MUST return <vps_ip>

# SSH posture
ssh root@<vps_ip>                # MUST be refused
ssh <username>@<vps_ip>          # MUST succeed

# On the box:
ufw status verbose                       # active, 3 allow rules
systemctl is-active fail2ban             # active
fail2ban-client status sshd              # jail listed
systemctl is-active unattended-upgrades  # active or static
swapon --show                            # /swapfile, 4G
free -h                                  # swap row populated
docker run --rm hello-world              # succeeds
docker compose version                   # v2.x
```

- [X] All commands above return the expected output
- [X] Every checkbox in Phases 0–8 is ticked
- [ ] M0 boxes in `live-roadmap.md` re-ticked to match
- [X] Hetzner snapshot taken (cheap insurance before the application stack lands on top)

---

## What's NOT in this doc

The next runbook covers the application layer — none of these are touched here:

- nginx as the SSL-terminating reverse proxy + Let's Encrypt via certbot
- Postgres 16 + pgvector container
- Ollama container (`mxbai-embed-large` for embeddings; optionally an LLM)
- The Castor `docker-compose.prod.yml` stack (Daphne, persistent volumes)
- Hetzner Storage Box + nightly backup cron (`pg_dump` + `MEDIA_ROOT` tarball)
- Sentry SDK, UptimeRobot ping on `/healthz/`
- Mailgun SMTP credentials and SPF/DKIM/DMARC records

Architecture and rationale for all of the above already live in
`vps-deployment.md` — read that next for the bigger picture before starting the
application setup.
