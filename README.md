# keeper-team-folder-sync

![CI](https://github.com/jlima8900/keeper-team-folder-sync/actions/workflows/ci.yml/badge.svg)

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

> 🇬🇧 English below · 🇫🇷 [Guide détaillé (français)](#guide-détaillé-français)

---

## ⚠️ Read this before running in production

This tool is **validated as an internal SE/demo utility**. The two items below
are the real gaps for an unattended production handover — see [Status](#status).

1. **Validated on one tenant / one version / one machine only** (see [Validated on](#validated-on)). It has **not** been run against any customer production tenant.
2. **It depends on internal, undocumented Keeper Commander APIs** (see [Fragility](#fragility)) and is pinned to `keepercommander==18.0.3`.

---

## Why this tool exists (the non-obvious parts)

- **Lockout safety.** It never calls the `keeper` binary non-interactively
  (that can submit empty stdin as your master password → account lockout).
  Instead it resumes your **persistent-login** session through the
  `keepercommander` *library* with a **fail-closed** `LoginUi` that raises on
  every interactive step, and leaves `params.password` empty. Result: the
  session is either resumed silently or the run aborts — it never prompts and
  never submits an empty password.
- **Grant by team UID, not name.** SCIM can create **same-named teams in
  different nodes** (one display name → several team UIDs). `share-folder
  -e "<name>"` then finds multiple matches and *silently skips the grant*.
  This tool grants by `team_uid`, which is unambiguous.
- **No-delete by default.** Folders and records are **never** deleted under any
  flag. A team that disappears is flagged `absent` in the state file and left
  untouched. The only removal possible is revoking out-of-scope team *grants*,
  and only when you explicitly pass `--prune-grants`.

## Prerequisites

- **Python 3.9+** (CI covers 3.9 / 3.11 / 3.13).
- **`keepercommander==18.0.3`** (pinned — see [Fragility](#fragility)).
- A Keeper **enterprise-admin** account with **Share Admin** (needed to read all
  teams and manage every shared folder).
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

`--state` is a JSON file keyed by `team_uid`; it tracks `first_seen`/`last_seen`
and `absent` flags for reporting and idempotency.

### What a run prints

```
Persistent login OK as admin@example.com.
--node 'Engineering' -> [<node_id>]
9 unique team name(s) in node 'Engineering'; 0 with duplicate UIDs ...
Filtered 26 -> 9 team(s).
SYNC PLAN: +0 folder(s), +0 grant(s), +0 record(s), -0 grant(s) [prune]. Absent (KEPT, never deleted): 0.
State written to ~/keeper-team-sync.json (run #2).
```

- `+0/+0/+0` on a re-run means **nothing changed** (idempotent — the desired state already exists).
- `Absent (KEPT, …)` lists teams in the state file no longer returned by the current query. They are **flagged, never deleted**.
- `-N grant(s) [prune]` is non-zero only when you pass `--prune-grants`.

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

## Other modes / examples

```bash
# Offline: generate a batch file from a CSV export (no live connection).
#   In keeper shell:  enterprise-info --teams --format csv --output teams.csv
python3 gen_team_folder_batch.py teams.csv --prefix "Team-" \
    --permissions full --seed login --seed-login svc@example.com --out plan.batch
#   Then in keeper shell:  batch plan.batch

# One-shot: fetch live + generate a batch file (review before running).
python3 gen_team_folder_batch.py --fetch-teams --node "Engineering" \
    --include "Departaments" --prefix "Team-" --out plan.batch

# One-shot: fetch live + apply immediately (canary-gated on the first record).
python3 gen_team_folder_batch.py --fetch-teams --node "Engineering" \
    --include "Departaments" --prefix "Team-" --seed login \
    --seed-login svc@example.com --execute

# Create a single login record in the vault ROOT.
python3 gen_team_folder_batch.py --root-record "Shared Root Login" \
    --root-login svc@example.com --execute

# Scope to one node AND revoke the other node's out-of-scope grants (opt-in).
# Preview first — this is the only removal the tool can perform.
python3 gen_team_folder_batch.py --sync --state ~/keeper-team-sync.json \
    --node "Engineering" --include "Departaments" --prefix "Team-" \
    --prune-grants --dry-run
```

`--permissions` accepts: `full` · `edit-share` · `edit-only` · `view-only`
(the default folder permissions granted to members).

## Modes & key flags

| Flag | Purpose |
|---|---|
| `--sync --state <file>` | Stateful, idempotent reconcile (recommended) |
| `--fetch-teams` | Read teams via persistent login (one-shot generate/execute) |
| `<teams.csv>` | Offline source from `enterprise-info --teams --format csv` |
| `--node "NAME\|ID"` | **Optional.** Scope to one node → no cross-node merge |
| `--prune-grants` | **Optional, opt-in.** Revoke out-of-scope grants only (never folders/records) |
| `--include` / `--exclude REGEX` | Target real teams; drop policy/test artifacts |
| `--prefix`, `--permissions`, `--seed {login,note}`, `--seed-login` | Folder naming / perms / starter record |
| `--root-record TITLE` | Create one login record in the vault root |
| `--dry-run` | Preview; write nothing |
| `--execute` | Run the one-shot plan now (canary-gated) |

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

Tests cover CSV parsing, include/exclude filtering, seed/title generation, the
plan builder (grant-by-UID, dedupe, repair-existing, single-seed), and an
assertion that **no destructive command is ever emitted**.

## Status

| Area | State |
|---|---|
| Core logic (login, grant-by-UID, dedupe, sync, no-delete, `--node`, `--prune-grants`) | ✅ live-verified |
| Pure-function unit tests | ✅ 23 passing |
| CI (GitHub Actions, py 3.9/3.11/3.13) | ✅ configured; runs once pushed to GitHub |
| Multi-tenant / multi-version validation | ❌ demo tenant + 18.0.3 only |
| Behaviour at scale (100s of teams, rate limiting) | ⚠️ untested; throttling seen at ~18 teams |
| Security sign-off on shared `--seed login` model | ⚠️ needs product/security review |

### Validated on
- Tenant: a vendor **demo** enterprise (not a customer production tenant)
- `keepercommander==18.0.3`, Python 3.13, macOS
- Verified: fetch teams, create folders, grant by UID, seed records, idempotent re-run (+0/+0/+0), `--node` scoping, `--prune-grants` dry-run, duplicate-record cleanup.

### Fragility
Depends on internal Commander APIs that are **not part of a stable contract**:
`get_params_from_config`, `auth.login_steps.LoginUi`, `params.shared_folder_cache`,
`params.enterprise`, `params.folder_cache`, `cli.do_command`. **Re-validate
against a live tenant before bumping `keepercommander`.**

## Troubleshooting & recovery

| Symptom | Cause / fix |
|---|---|
| `Persistent login not active (… required). Run keeper login.` | The session expired. Run `keeper login` in a shell, then re-run. The tool **fails closed** — it will not prompt or risk a lockout. |
| `--node 'X' matched no node.` | The message lists all available node names — copy one exactly, or pass the numeric `node_id`. |
| `Throttled (attempt 1/3), retrying in 60 seconds` | Server rate-limit; the client retries automatically. Expect longer wall-clock on large tenants (behaviour at 100s of teams is untested). |
| `Multiple matches were found for team "<name>"` | Should not occur — the tool grants by `team_uid`. If you see it, you're on an older build that granted by name; update. |
| Created a folder/record by mistake | **Records:** deleted records go to **Trash** — list with `trash list`, restore with `trash restore <uid>`. **Folders:** remove manually in the Vault UI or `rmdir`/`rm` in `keeper shell` (the tool never deletes them for you). |
| Re-ran and it created duplicate records | Shouldn't happen in `--sync` (it checks for an existing record with the seed title first). The one-shot `--execute`/batch modes are *not* state-aware — prefer `--sync` for repeat runs. |

## Security notes
- Seeding a shared `--seed login` record (one shared credential per team folder)
  is a deliberate model choice — confirm it matches the customer's policy.
- The operator account needs enterprise admin / Share Admin to manage all teams
  and folders.
- The `--state` JSON contains team and folder UIDs (not secrets) but is
  gitignored regardless.

---

## Guide détaillé (français)

Companion francophone : même contenu que ci-dessus, plus le contexte de conception.

### 1. Résumé en une phrase

Pour chaque **nom d'équipe** unique : on garantit l'existence d'un dossier partagé, on **accorde l'accès par UID d'équipe**, et on dépose **un seul** enregistrement de départ — le tout **réexécutable sans effet de bord** et **sans jamais rien supprimer**.

### 2. Sécurité — pas de verrouillage de compte

- L'outil **n'appelle jamais le binaire `keeper`** en mode non interactif. Sur une session expirée, cela soumettrait une entrée vide comme mot de passe maître → échecs répétés → **verrouillage réel du compte**.
- À la place, on **reprend la connexion persistante via la bibliothèque `keepercommander`** : une classe `LoginUi` **« fail-closed »** lève une exception à **chaque** étape interactive (mot de passe, 2FA, approbation d'appareil, SSO), et `params.password` reste **vide** (le flux ne soumet un mot de passe que s'il est non vide).
- **Conséquence :** la session est soit reprise **silencieusement**, soit la commande **s'arrête proprement**. Elle ne demande jamais de mot de passe et n'en soumet jamais un vide — sûr pour une exécution planifiée.

### 3. Le problème découvert (et corrigé)

| Symptôme | Cause |
|---|---|
| **0 accès accordé** | Des **équipes de même nom** existent (un nom = **plusieurs UID**). `share-folder -e "<nom>"` trouve plusieurs correspondances → « Multiple matches » → **accès silencieusement ignoré**. |
| **Dossiers/enregistrements en double** | Une ligne par équipe alors que le même nom existe plusieurs fois → doublons. |

> **Cause profonde = les NŒUDS.** Les UID « en double » sont en fait des **équipes distinctes dans des nœuds différents** (ex. `HQ` et `Engineering`). Fusionner par nom revient à **partager un dossier entre deux unités d'organisation** — sur-partage à éviter.

**Correctif :** déduplication par nom (un dossier par nom) + **accès accordé par `team_uid`** (non ambigu ; si un nom a 2 UID, on émet 2 lignes d'accès vers le même dossier) + **un seul** enregistrement par dossier.

### 4. Méthodologie de suivi (synchronisation régulière)

Mode `--sync` avec un **fichier d'état JSON** (`--state`) **indexé par `team_uid`** (stable). La **source de vérité** reste le coffre ; l'état sert au suivi/reporting.

| Situation | Action |
|---|---|
| Équipe **nouvelle** | Créer le dossier + accorder l'accès + 1 enregistrement |
| Équipe **déjà présente** | **Rien** si tout existe (idempotent) ; `last_seen` mis à jour |
| Accès **manquant** | Ajouter uniquement l'accès (réparation) |
| Équipe **disparue** | **`absent` + horodatée. AUCUNE suppression** |

**Idempotence :** deuxième exécution = `+0 / +0 / +0`.

### 5. Règle stricte : aucune suppression

- L'outil ne construit **que** `mkdir`, `share-folder -a grant`, `record-add`. Aucun `delete`/`rm` dans le code.
- Une équipe retirée est **conservée** ; seul `absent: true` est positionné.
- Seule exception : `--prune-grants` (opt-in) révoque des **accès** hors périmètre — **jamais** de dossiers ni d'enregistrements.

### 6. Limites connues / à valider

- **Filtrage :** beaucoup de « équipes » sont des artefacts (`Role Policies`, `Permissions`, `MIGTEST`…). **Toujours `--include`/`--exclude`**.
- **`--node` :** sans lui, les noms identiques entre nœuds fusionnent (sur-partage). Pour une séparation stricte, le passer.
- **Renommage d'équipe :** crée un nouveau dossier ; l'ancien est conservé (jamais supprimé).
- **Connexion persistante :** elle expire — replanifier `keeper login` côté opérateur.

Pour les commandes exactes, voir [Other modes / examples](#other-modes--examples) ci-dessus (`svc@example.com` est un exemple).
