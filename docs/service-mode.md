# Running on a schedule via Commander Service Mode

🇬🇧 **English** · 🇫🇷 [Version française](service-mode.fr.md)

> **⚠️ Status: documented pattern, NOT live-tested with this tool.**
> This guide is grounded in the official Keeper docs
> ([Service Mode REST API](https://docs.keeper.io/keeperpam/commander-cli/service-mode-rest-api))
> plus a prior, separate service-mode deployment test. The **integration of
> *this* tool with service mode has not been exercised end-to-end.** Treat it as
> a design to validate, not a verified recipe. Same disclaimer as the README:
> personal PoC, not an official/supported Keeper product.

## Why service mode for scheduling

The README's [Scheduling](../README.md#scheduling) section uses cron + the
tool's fail-closed library login. Its weakness: **persistent login expires**, so
an unattended cron job eventually needs a human to re-run `keeper login`.

**Service mode** runs Commander as a long-lived, already-authenticated process
exposing a REST API. One process holds the session; the scheduler just POSTs
commands. It does not make auth eternal — if the process dies or the session is
revoked, you re-auth — but it removes the per-run login.

## How the REST API works (from the docs)

```bash
# Create the service: scope allowed commands to ONLY what this tool emits.
service-create -p 9090 -f json \
  -c 'enterprise-info,sync-down,ls,tree,mkdir,share-folder,record-add,run-batch'
# Start it (uses the cached encrypted config; no params on subsequent starts):
service-start
```

`service-create` prints an **API key**. Call it:

```bash
# v1 — synchronous:
curl -X POST 'http://localhost:9090/api/v1/executecommand' \
  -H 'Content-Type: application/json' -H 'api-key: <API_KEY>' \
  --data '{"command": "run-batch /opt/keeper/plan.batch"}'

# v2 — async (returns request_id; poll /api/v2/result/<request_id>):
curl -X POST 'http://localhost:9090/api/v2/executecommand-async' \
  -H 'Content-Type: application/json' -H 'api-key: <API_KEY>' \
  --data '{"command": "run-batch /opt/keeper/plan.batch"}'
```

Response shape: `{"command": "...", "data": <...>, "status": "success"}`.

## Authentication for the service host

- **VM / bare host:** establish persistent login or biometric once:
  - `this-device persistent-login on` (register device, set timeout), or
  - `biometric register`.
- **Containers (from a prior K8s test):** persistent-login config does **not**
  work in containers — the container presents a different device fingerprint
  than the host. Pass `--user / --password / --server` directly so each start
  auto-registers a fresh device. (Credentials via a mounted secret / env, never
  baked into the image.)
- Some setup commands don't work when credentials live in the OS keychain — use
  `keeper shell --config-file <file>` for the initial configuration.

## Recommended pattern with this tool

Keep **decision** (what to change, reviewed by a human) separate from
**execution** (unattended, on the service):

1. **Operator workstation** (its own login — *not* the service's config, to
   avoid the single-device / persistent-login refresh race):
   ```bash
   python3 gen_team_folder_batch.py --fetch-teams --node "<Node>" \
       --include "<Dept>" --prefix "Team-" --permissions full \
       --seed login --seed-login svc@example.com --out plan.batch
   ```
   Review `plan.batch`.
2. **Copy `plan.batch`** to a path the service host can read (e.g. `/opt/keeper/plan.batch`).
3. **Scheduler** (cron/systemd timer) POSTs the run-batch command to the service:
   ```bash
   curl -fsS -X POST 'http://localhost:9090/api/v1/executecommand' \
     -H 'Content-Type: application/json' -H "api-key: $KEEPER_SVC_API_KEY" \
     --data '{"command": "run-batch /opt/keeper/plan.batch"}'
   ```

The generated batch is idempotent at the Keeper level (`mkdir` on an existing
folder is a harmless warning; grants/records are not duplicated), so re-running
is safe. For true state tracking (the `absent` flagging, `+0/+0/+0` reporting),
the library `--sync` mode is still richer than a plain `run-batch` — service
mode trades that for an always-warm session.

## Why this isn't a drop-in

`--sync` reads vault state from in-memory library structures
(`params.enterprise`, `shared_folder_cache`) that don't map 1:1 to REST command
output, and it executes via `cli.do_command` in-process — a different session
from the service. A native `--service-url`/`--api-key` mode (POST generated
commands to the REST API, parse `enterprise-info --format json` for team UIDs)
is possible but not built. Until then, the **generate → POST `run-batch`**
pattern above is the pragmatic path.

## Security checklist (service-create options)

| Concern | Option |
|---|---|
| Least privilege | `-c` allow-list — only the commands above; add later with `service-config-add` |
| Token lifetime | `-te 24h` (e.g. `30m` / `7d`); use **different API keys per use case** |
| Network exposure | `-aip` allow-list / `-dip` deny-list of client IPs; bind to localhost where possible |
| Transport | `-crtf` / `-crtp` for TLS |
| Abuse | `-rl` rate limit (e.g. `100/hour`) |
| Response confidentiality | `-ek` AES-256-GCM response encryption key |

Store the API key like any secret (e.g. in the Keeper vault / KSM), inject as an
env var, never commit it.

## Before relying on this in production

- Validate the **generate → POST `run-batch`** loop end-to-end on a test tenant
  (this is the untested part).
- Confirm the allowed-command list is sufficient and minimal for your flags.
- Decide whether you need `--sync`'s state tracking; if so, a native REST mode is
  required (not yet built).
