# SSH Lockout Recovery — Runbook

> When `ssh` to the production VPS fails and you need to get back in. Mechanical
> steps, no detective work. Sibling docs: `server-setup.md` (the *why* behind
> the policy), `vps-deployment.md` (infra reference).
>
> Throughout: replace `<vps_ip>`, `<ssh_port>`, `<username>`, `<your_ip>` with
> the real values. Real values intentionally NOT recorded here.

---

## Step 0 — Hetzner Cloud Console is your last resort, and it cannot fail

The Hetzner web console attaches to the VM's serial/VGA console via the
hypervisor. It bypasses ufw, iptables, fail2ban, and sshd entirely. Even if
sshd were uninstalled, the Console would still work. Use it whenever
network-level recovery is needed.

URL: https://console.hetzner.cloud → project → server → **Console** button.
Log in as `<username>`.

---

## Step 1 — Symptom fingerprint

The first message tells you which layer is failing.

| Client message | Meaning | First place to look |
|---|---|---|
| `Connection timed out` | Packets are being dropped before sshd answers (firewall, fail2ban iptables rule, upstream filter) | `iptables`, `ufw`, `fail2ban-client status sshd`, Hetzner Cloud Firewall (web panel) |
| `Connection refused` | TCP RST — nothing is listening on that port on the server | `ss -tlnp \| grep sshd`. Common cause: edited `Port` in sshd_config but `ssh.socket` is still active. See Step 3. |
| `Permission denied (publickey)` | TCP + sshd are fine; key auth failed | Wrong key, wrong user, or `~/.ssh` permissions on the server |
| Hangs after banner / `kex_exchange_identification` | sshd accepted but couldn't complete handshake — usually `MaxStartups` exhaustion under bot flood on port 22 | Move SSH off 22 (`server-setup.md` Phase 3) |

`Test-NetConnection <vps_ip> -Port <ssh_port>` from PowerShell only proves the
TCP handshake — it says nothing about whether sshd will actually authenticate.
Don't trust it as a full health check.

---

## Step 2 — Triage from your laptop (no server access needed)

```bash
# Server alive? (ICMP usually allowed even when 22 is filtered)
ping <vps_ip>

# TCP layer reachable on the SSH port?
powershell -Command "Test-NetConnection <vps_ip> -Port <ssh_port>"

# What public IP are we egressing from? (in case the ISP rotated)
curl -s https://ifconfig.me
```

Interpretation:
- ping ✅ + Test-NetConnection ❌ → block is port-specific (fail2ban, ufw, Hetzner edge filter, ISP filtering this port).
- ping ✅ + Test-NetConnection ✅ + `ssh` still hangs/times out → SSH-protocol layer issue, classically `MaxStartups` on a flooded port 22.
- ping ❌ → server or routing problem, go straight to Step 0 (Console).

---

## Step 3 — Diagnose from the Hetzner Console

Once in via the Console:

```bash
# Is sshd running and on the expected port?
sudo systemctl status ssh.service
sudo ss -tlnp | grep sshd            # MUST show <ssh_port>; if 22, see "socket activation gotcha"

# Anyone banned right now?
sudo fail2ban-client status sshd

# Any rule that mentions our IP specifically?
sudo iptables -S | grep <your_ip>    # (use the IP from `curl ifconfig.me` above)

# What's the firewall actually doing?
sudo ufw status verbose

# Recent SSH activity, especially from our IP
sudo journalctl -u ssh.service --since "1 hour ago" | tail -50
sudo grep <your_ip> /var/log/auth.log | tail -20
```

---

## Step 4 — Three classes of cause, three fixes

### A) fail2ban has banned you

Symptom: your IP appears under "Banned IP list" in `fail2ban-client status sshd`,
or `iptables -S` shows it in the `f2b-sshd` chain.

```bash
sudo fail2ban-client unban <your_ip>
# Or, if uncertain which IP is banned:
sudo fail2ban-client unban --all
```

If this happens often, check the jail thresholds (`server-setup.md` Phase 5).
The recommended `[sshd]` policy is `maxretry=10`, `findtime=10m`,
`bantime=15m` — strict enough to block brute-forcers, lenient enough to
absorb client noise (verbose ssh, IDE retries).

### B) Bot flood on port 22 is exhausting sshd

Symptom: `Test-NetConnection -Port 22` succeeds intermittently, but `ssh`
times out at the protocol layer. `journalctl -u ssh.service` is full of
`Connection reset by authenticating user root` entries from many different
IPs, every minute.

Fix: move SSH off port 22. Follow `server-setup.md` Phase 3 (Port directive +
socket-activation disable + ufw allow new port + ufw delete OpenSSH). Bot
scans don't bother with high ports; the symptom disappears.

### C) Upstream block (Hetzner edge anti-DDoS, ISP routing)

Symptom: Console works. From your laptop, `Test-NetConnection` to the SSH
port times out, AND `iptables`/`ufw`/`fail2ban` all show nothing relevant on
the server. To confirm, run on the server while attempting `ssh` from
your laptop in another window:

```bash
sudo apt install -y tcpdump   # one-time
sudo tcpdump -ni any 'tcp port <ssh_port>' -c 20
```

- SYN packets from `<your_ip>` arrive → server is fine, the kernel/sshd is
  dropping the reply (rare; check `MaxStartups`, see B).
- Nothing arrives → packets aren't reaching the VM. Most likely Hetzner's
  edge anti-DDoS has rate-limited your IP because of repeated SSH retry
  traffic. It clears itself in 30–60 minutes of no traffic. While waiting,
  open a Hetzner support ticket if you can't afford the wait.

---

## Step 5 — The Ubuntu 24.04 socket-activation gotcha

This deserves its own callout because it has eaten ≥1 hour of recovery time
already.

On Ubuntu 24.04+, `ssh.service` is started by `ssh.socket` (socket
activation). The socket unit dictates the listening port. **Editing the
`Port` directive in `/etc/ssh/sshd_config` and running `systemctl reload ssh`
will silently keep listening on whatever port `ssh.socket` was configured
for** — usually 22. You'll see the change in the config file, you'll see
`sshd -t` pass, and `ss -tlnp | grep sshd` will still show the old port.

Fix:

```bash
sudo systemctl disable --now ssh.socket
sudo systemctl enable --now ssh.service
sudo systemctl restart ssh.service
sudo ss -tlnp | grep sshd     # now shows the configured Port
```

If you prefer to keep socket activation: `systemctl edit ssh.socket` and add
`ListenStream=` (empty, to clear inherited) followed by `ListenStream=<ssh_port>`.

---

## Step 6 — Misleading signals to ignore

- `fail2ban-client unban <ip>` returning `0` doesn't mean "failed" — it means
  "0 IPs were unbanned because that IP wasn't currently banned." Check
  `status sshd` for the actual ban list.
- `fail2ban-server` warning `'allowipv6' not defined in 'Definition'. Using
  default one: 'auto'` is harmless and present in vanilla Ubuntu installs.
- `Connection reset by authenticating user root` floods in `journalctl` are
  attackers, not you. They're noise; they're not what's locking you out
  unless paired with port-22 + `MaxStartups` exhaustion (Step 4 B).

---

## Verification after recovery

```bash
ssh -p <ssh_port> <username>@<vps_ip>           # reconnects from Git Bash
sudo journalctl -u ssh.service --since "10 min ago" | wc -l   # small (<50) on a custom port
sudo fail2ban-client status sshd                # Currently banned: 0, stays at 0
```

If all three look healthy, you're done. If lockouts recur even after Step 4 B,
escalate to a Tailscale or WireGuard VPN — closes SSH to the public internet
entirely, makes this whole runbook obsolete.
