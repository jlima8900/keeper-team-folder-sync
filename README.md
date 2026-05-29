# keeper-team-folder-sync

![CI](https://github.com/jlima8900/keeper-team-folder-sync/actions/workflows/ci.yml/badge.svg)

🇬🇧 **English** · 🇫🇷 [Version française](README.fr.md)

> **⚠️ Disclaimer — personal proof of concept.**
> This is a **personal proof-of-concept** built by an individual. It is **NOT an
> official Keeper Security product** and is **not supported, endorsed, reviewed,
> or maintained by Keeper Security, Inc.** It uses the public `keepercommander`
> CLI but relies on internal/undocumented APIs and may break at any time. Use
> entirely at your own risk, against test/demo tenants first. "Keeper",
> "Keeper Commander", and related marks belong to Keeper Security, Inc.

Provision and **regularly sync** Keeper shared folders from SCIM-synced teams,
using Keeper Commander. One shared folder per team, the team granted access,
optionally seeded with a starter record. Designed to run repeatedly (e.g. on a
schedule) as an **additive reconciler** — by default it never deletes anything.

---

## Read this before running in production

This tool is **validated as an internal SE/demo utility only** — see the
[Validation matrix](#validation-matrix) for exactly what has and has not been
exercised live. Two structural gaps for an unattended production handover:

1. **Validated on one demo tenant / one Commander version / one machine.** Not run against any customer production tenant.
2. **Depends on internal, undocumented Keeper Commander APIs** ([Fragility](#fragility)); pinned to `keepercommander==18.0.3`.

## Why this tool exists (the non-obvious parts)

- **Lockout safety.** It never calls the `keeper` binary non-interactively (that
  can submit empty stdin as your master password → account lockout). It resumes
  your **persistent-login** session through the `keepercommander` *library* with
  a **fail-closed** `LoginUi` that raises on every interactive step, leaving
  `params.password` empty. The session is either resumed silently or the run
  aborts — never a prompt, never an empty-password submission.
- **Grant by team UID, not name.** SCIM can create **same-named teams in
  different nodes** (one display name → several team UIDs). `share-folder
  -e "<name>"` then matches multiple and *silently skips the grant*. This tool
  grants by `team_uid`, which is unambiguous.
- **No-delete by default.** Folders and records are **never** deleted under any
  flag. A team that disappears is flagged `absent` in the state file and left
  untouched. The only removal possible is revoking out-of-scope team *grants*,
  and only when you explicitly pass `--prune-grants`.

## Prerequisites

- **Python 3.9+** (CI: 3.9 / 3.11 / 3.13).
- **`keepercommander==18.0.3`** (pinned — see [Fragility](#fragility)).
- A Keeper **enterprise-admin** account with **Share Admin** (to read all teams and manage every shared folder).
- An active **persistent-login** session for that account (`keeper login` once).

## Install

```bash
python3 -m pip install -r requirements.txt   # keepercommander==18.0.3
keeper login                                  # establish persistent login once
```

## Quick start (stateful sync — recommended)

```bash
# Preview only — changes nothing:
python3 gen_team_folder_batch.py --sync --state ~/keeper-team-sync.json \
    --node "Engineering" --prefix "Team-" --permissions full \
    --seed login --seed-login svc@example.com --include "Departaments" --dry-run

# Apply:
python3 gen_team_folder_batch.py --sync --state ~/keeper-team-sync.json \
    --node "Engineering" --prefix "Team-" --permissions full \
    --seed login --seed-login svc@example.com --include "Departaments"
```

## Complete command reference

Source of truth is `gen_team_folder_batch.py --help`. Every option:

| Option | Default | Purpose |
|---|---|---|
| `teams_csv` (positional) | — | Offline team source: CSV from `enterprise-info --teams --format csv`. Mutually exclusive with `--fetch-teams`. |
| `--fetch-teams` | off | Read teams live via persistent login (lockout-safe). |
| `--sync` | off | Stateful reconcile (additive, no-delete). Implies live fetch; requires `--state`. |
| `--state FILE` | — | JSON state file for `--sync`, keyed by `team_uid`. |
| `--config PATH` | `~/.keeper/config.json` | Keeper config used to resume persistent login. |
| `--existing CSV` | — | `list-sf` CSV for offline idempotency (skip folders that already exist). `--fetch-teams`/`--sync` get this automatically from the vault. |
| `--node "NAME\|ID"` | all nodes | **Optional.** Scope to one enterprise node (display name or numeric `node_id`). Omit → span all nodes (same-named teams across nodes then merge). Unknown name prints the list of available nodes. |
| `--prune-grants` | off | **Opt-in.** Revoke team grants outside the current scope (e.g. the other node's team when `--node` is set). Revokes **grants only** — never folders/records. |
| `--include REGEX` | — | Keep only teams whose name matches (case-insensitive). |
| `--exclude REGEX` | — | Drop teams whose name matches (case-insensitive). |
| `--prefix STR` | `""` | Folder name prefix, e.g. `"Team-"`. |
| `--permissions` | `edit-only` | Default member permissions: `full` · `edit-share` · `edit-only` · `view-only`. |
| `--seed {login,note}` | none | Ensure one starter record per folder (login record, or encryptedNotes note). |
| `--seed-title TITLE` | `"<folder> - Shared Login"` / `"… - Welcome"` | Title of the seeded record. |
| `--seed-text TEXT` | `"Team vault provisioned."` | Note body for `--seed note`. |
| `--seed-login USER` | — | `login=` value for `--seed login` (password is always `$GEN`-generated). |
| `--root-record TITLE` | — | Create one login record in the vault **root** (no folder). |
| `--root-login USER` | — | `login=` value for `--root-record`. |
| `--out FILE` | `provision-team-folders.batch` | Output path for the generated batch file (generate mode). |
| `--dry-run` | off | Preview only; write nothing, change nothing. |
| `--execute` | off | Run the one-shot plan now via persistent login (canary-gated on the first login record). |

## Examples (every mode)

```bash
# 1. SYNC, preview (no changes, no state write):
python3 gen_team_folder_batch.py --sync --state ~/keeper-team-sync.json \
    --node "Engineering" --include "Departaments" --prefix "Team-" \
    --permissions full --seed login --seed-login svc@example.com --dry-run

# 2. SYNC, apply (idempotent; safe to schedule):
python3 gen_team_folder_batch.py --sync --state ~/keeper-team-sync.json \
    --node "Engineering" --include "Departaments" --prefix "Team-" \
    --permissions full --seed login --seed-login svc@example.com

# 3. SYNC with opt-in pruning (revoke other-node grants; preview first!):
python3 gen_team_folder_batch.py --sync --state ~/keeper-team-sync.json \
    --node "Engineering" --include "Departaments" --prefix "Team-" \
    --prune-grants --dry-run

# 4. One-shot: fetch live, GENERATE a batch file to review:
python3 gen_team_folder_batch.py --fetch-teams --node "Engineering" \
    --include "Departaments" --prefix "Team-" --permissions full \
    --seed login --seed-login svc@example.com --out plan.batch
#   then in `keeper shell`:  batch plan.batch

# 5. One-shot: fetch live, EXECUTE immediately (canary-gated):
python3 gen_team_folder_batch.py --fetch-teams --node "Engineering" \
    --include "Departaments" --prefix "Team-" --seed login \
    --seed-login svc@example.com --execute

# 6. OFFLINE from a CSV export (no live connection):
#   in `keeper shell`:  enterprise-info --teams --format csv --output teams.csv
python3 gen_team_folder_batch.py teams.csv --existing existing-sf.csv \
    --prefix "Team-" --permissions full --seed login \
    --seed-login svc@example.com --out plan.batch

# 7. Seed a NOTE instead of a login record:
python3 gen_team_folder_batch.py --fetch-teams --node "Engineering" \
    --include "Departaments" --prefix "Team-" \
    --seed note --seed-text "Store shared team credentials here." --execute

# 8. Create a single login record in the vault ROOT:
python3 gen_team_folder_batch.py --root-record "Shared Root Login" \
    --root-login svc@example.com --execute

# 9. Custom config path (non-default Keeper config):
python3 gen_team_folder_batch.py --sync --state ~/keeper-team-sync.json \
    --config /path/to/config.json --include "Departaments" --prefix "Team-"
```

### What a run prints

```
Persistent login OK as admin@example.com.
--node 'Engineering' -> [<node_id>]
9 unique team name(s) in node 'Engineering'; 0 with duplicate UIDs ...
Filtered 26 -> 9 team(s).
SYNC PLAN: +0 folder(s), +0 grant(s), +0 record(s), -0 grant(s) [prune]. Absent (KEPT, never deleted): 0.
State written to ~/keeper-team-sync.json (run #2).
```

- `+0/+0/+0` on a re-run = **nothing changed** (idempotent).
- `Absent (KEPT, …)` = teams in the state file no longer returned by the query; **flagged, never deleted**.
- `-N grant(s) [prune]` is non-zero only with `--prune-grants`.

### State file shape

```json
{
  "version": 1,
  "tenant": "admin@example.com",
  "runs": 2,
  "teams": {
    "TEAMUID-EXAMPLE": {
      "name": "Departaments - Legal",
      "folder": "Team-Departaments - Legal",
      "folder_uid": "FOLDERUID-EXAMPLE",
      "granted": true,
      "first_seen": "2026-05-29T09:39:20+00:00",
      "last_seen":  "2026-05-29T10:12:04+00:00",
      "absent": false,
      "absent_since": null
    }
  },
  "records": { "Team-Departaments - Legal": "Team-Departaments - Legal - Shared Login" }
}
```

## Scheduling

```cron
# Persistent login must be valid; re-run `keeper login` periodically.
0 7 * * *  /usr/bin/python3 /path/to/gen_team_folder_batch.py --sync \
  --state ~/keeper-team-sync.json --node "Engineering" --prefix "Team-" \
  --permissions full --seed login --seed-login svc@example.com \
  --include "Departaments" >> ~/keeper-sync.log 2>&1
```

## Tests

```bash
python3 -m unittest discover -s tests -v   # 23 pure-function tests, no vault needed
```

Covers CSV parsing, include/exclude filtering, seed/title generation, the plan
builder (grant-by-UID, dedupe, repair-existing, single-seed), and an assertion
that **no destructive command is ever emitted**.

## Validation matrix

Honest status — what was actually exercised, and how.

| Capability | Status |
|---|---|
| Fail-closed persistent-login resume | ✅ **live-verified** (resumes silently / aborts without prompt) |
| `--fetch-teams` (read teams + existing folders) | ✅ live-verified |
| Grant **by UID** applied to a folder | ✅ live-verified (repaired 9 folders 0→2 grants) |
| `--execute` grant path | ✅ live-verified (18 grants) |
| `--sync` read / plan / idempotent no-op / state write | ✅ live-verified (+0/+0/+0) |
| `--node` resolution + scoping | ✅ live-verified (dry-run; resolves node_id, lists nodes on error) |
| Login record creation + `$GEN` password | ✅ live-verified |
| Duplicate-record cleanup (`rm -f` → Trash) | ✅ live-verified |
| Pure-function logic (parsing, filter, plan, seed) | ✅ 23 unit tests |
| CI on Python 3.9 / 3.11 / 3.13 | ✅ green |
| `--sync` **creating new** folders/grants/seeds from scratch | ⚠️ **not exercised live** (test tenant already had them; only the no-op path ran) |
| `--prune-grants` actual revoke | ⚠️ **dry-run only** — never executed live |
| `--seed note` (encryptedNotes) live creation | ⚠️ unit-tested string only; not created live |
| `batch` command path (generate → `keeper shell` `batch`) | ⚠️ not run; live mutations used `cli.do_command` per line |
| `--execute` canary branch in the tool | ⚠️ canary logic proven in a throwaway script, not via the tool's `--execute` |
| Multi-tenant / other Commander versions / scale | ❌ untested (throttling already seen at ~18 teams) |
| Security sign-off on shared `--seed login` model | ❌ needs product/security review |

**Validated on:** a vendor **demo** enterprise (not a customer production tenant), `keepercommander==18.0.3`, Python 3.13, macOS.

## Fragility

Depends on internal Commander APIs that are **not a stable contract**:
`get_params_from_config`, `auth.login_steps.LoginUi`, `params.shared_folder_cache`,
`params.enterprise`, `params.folder_cache`, `cli.do_command`. **Re-validate against
a live tenant before bumping `keepercommander`.**

## Troubleshooting & recovery

| Symptom | Cause / fix |
|---|---|
| `Persistent login not active (… required). Run keeper login.` | Session expired. Run `keeper login`, then re-run. The tool **fails closed** — it will not prompt or risk a lockout. |
| `--node 'X' matched no node.` | The message lists every available node — copy one exactly, or pass the numeric `node_id`. |
| `Throttled (attempt 1/3), retrying in 60 seconds` | Server rate-limit; the client retries. Expect longer wall-clock on large tenants (scale untested). |
| `Multiple matches were found for team "<name>"` | Should not occur — the tool grants by `team_uid`. If seen, you're on an older build that granted by name; update. |
| Created something by mistake | **Records** go to **Trash**: `trash list`, `trash restore <uid>`. **Folders**: remove in the Vault UI or `rmdir`/`rm` in `keeper shell` (the tool never deletes them for you). |
| Duplicate records after repeat runs | Won't happen in `--sync` (it checks for an existing seed-title record first). One-shot `--execute`/batch modes are **not** state-aware — prefer `--sync` for repeats. |

## Security notes

- Seeding a shared `--seed login` record (one shared credential per team folder)
  is a deliberate model choice — confirm it matches the customer's policy.
- The operator account needs enterprise admin / Share Admin.
- The `--state` JSON holds team and folder UIDs (not secrets) but is gitignored regardless.
