# keeper-team-folder-sync

![CI](https://github.com/jlima8900/keeper-team-folder-sync/actions/workflows/ci.yml/badge.svg)

🇫🇷 **Français** · 🇬🇧 [English version](README.md)

> **⚠️ Avertissement — preuve de concept personnelle.**
> Ceci est une **preuve de concept personnelle** réalisée à titre individuel. Ce
> **N'EST PAS un produit officiel Keeper Security** et n'est **ni pris en charge,
> ni approuvé, ni révisé, ni maintenu par Keeper Security, Inc.** L'outil utilise
> le CLI public `keepercommander` mais s'appuie sur des API internes/non
> documentées et peut cesser de fonctionner à tout moment. À utiliser à vos
> risques, d'abord sur des locataires de test/démo. « Keeper », « Keeper
> Commander » et les marques associées appartiennent à Keeper Security, Inc.

Provisionner et **synchroniser régulièrement** des dossiers partagés Keeper à
partir des équipes synchronisées par SCIM, via Keeper Commander. Un dossier
partagé par équipe, l'accès accordé à l'équipe, avec en option un enregistrement
de départ. Conçu pour s'exécuter de façon répétée (ex. planifié) comme un
**réconciliateur additif** — par défaut il ne supprime jamais rien.

---

## À lire avant toute utilisation en production

Cet outil est **validé uniquement comme utilitaire interne SE/démo** — voir la
[matrice de validation](#matrice-de-validation) pour le détail exact de ce qui a
été exécuté en conditions réelles. Deux lacunes structurelles pour une remise à
une équipe en production non supervisée :

1. **Validé sur un seul locataire de démo / une version de Commander / une machine.** Jamais exécuté sur un locataire de production client.
2. **Dépend d'API Keeper Commander internes et non documentées** ([Fragilité](#fragilité)) ; épinglé à `keepercommander==18.0.3`.

## Pourquoi cet outil (les points non évidents)

- **Sécurité anti-verrouillage.** Il n'appelle **jamais** le binaire `keeper` en
  mode non interactif (cela soumettrait une entrée vide comme mot de passe maître
  → verrouillage du compte). Il **reprend la session de connexion persistante via
  la bibliothèque `keepercommander`** avec une classe `LoginUi` **« fail-closed »**
  qui lève une exception à chaque étape interactive, en laissant `params.password`
  vide. La session est soit reprise **silencieusement**, soit la commande
  **s'arrête** — jamais d'invite, jamais de mot de passe vide soumis.
- **Accès accordé par UID d'équipe, pas par nom.** SCIM peut créer des **équipes
  de même nom dans des nœuds différents** (un nom = plusieurs UID). `share-folder
  -e "<nom>"` trouve alors plusieurs correspondances et **ignore silencieusement
  l'accès**. L'outil accorde par `team_uid`, ce qui est non ambigu.
- **Aucune suppression par défaut.** Les dossiers et enregistrements ne sont
  **jamais** supprimés. Une équipe disparue est marquée `absent` dans l'état et
  laissée intacte. La seule suppression possible est la révocation d'**accès**
  hors périmètre, et uniquement via l'option explicite `--prune-grants`.

## Prérequis

- **Python 3.9+** (CI : 3.9 / 3.11 / 3.13).
- **`keepercommander==18.0.3`** (épinglé — voir [Fragilité](#fragilité)).
- Un compte **enterprise-admin** Keeper avec **Share Admin** (pour lire toutes les équipes et gérer tous les dossiers partagés).
- Une session de **connexion persistante** active pour ce compte (`keeper login` une fois).

## Installation

```bash
python3 -m pip install -r requirements.txt   # keepercommander==18.0.3
keeper login                                  # établir la connexion persistante une fois
```

## Démarrage rapide (synchronisation avec état — recommandé)

```bash
# Aperçu seul — ne modifie rien :
python3 gen_team_folder_batch.py --sync --state ~/keeper-team-sync.json \
    --node "Engineering" --prefix "Team-" --permissions full \
    --seed login --seed-login svc@example.com --include "Departaments" --dry-run

# Appliquer :
python3 gen_team_folder_batch.py --sync --state ~/keeper-team-sync.json \
    --node "Engineering" --prefix "Team-" --permissions full \
    --seed login --seed-login svc@example.com --include "Departaments"
```

## Référence complète des commandes

Source de vérité : `gen_team_folder_batch.py --help`. Toutes les options :

| Option | Défaut | Rôle |
|---|---|---|
| `teams_csv` (positionnel) | — | Source hors-ligne : CSV de `enterprise-info --teams --format csv`. Exclusif avec `--fetch-teams`. |
| `--fetch-teams` | off | Lire les équipes en direct via la connexion persistante (sans risque de verrouillage). |
| `--sync` | off | Réconciliation avec état (additive, sans suppression). Implique une lecture en direct ; requiert `--state`. |
| `--state FILE` | — | Fichier d'état JSON pour `--sync`, indexé par `team_uid`. |
| `--config PATH` | `~/.keeper/config.json` | Config Keeper utilisée pour reprendre la connexion persistante. |
| `--existing CSV` | — | CSV `list-sf` pour l'idempotence hors-ligne (ignorer les dossiers existants). `--fetch-teams`/`--sync` l'obtiennent automatiquement du coffre. |
| `--node "NAME\|ID"` | tous les nœuds | **Optionnel.** Limiter à un nœud (nom ou `node_id` numérique). Omis → tous les nœuds (les noms identiques entre nœuds fusionnent). Un nom inconnu affiche la liste des nœuds. |
| `--prune-grants` | off | **Opt-in.** Révoque les accès d'équipe hors périmètre (ex. l'équipe de l'autre nœud quand `--node` est fixé). Révoque **uniquement des accès** — jamais dossiers/enregistrements. |
| `--include REGEX` | — | Garder les équipes dont le nom correspond (insensible à la casse). |
| `--exclude REGEX` | — | Exclure les équipes dont le nom correspond (insensible à la casse). |
| `--prefix STR` | `""` | Préfixe du nom de dossier, ex. `"Team-"`. |
| `--permissions` | `edit-only` | Permissions par défaut des membres : `full` · `edit-share` · `edit-only` · `view-only`. |
| `--seed {login,note}` | aucun | Garantir un enregistrement de départ par dossier (login, ou note encryptedNotes). |
| `--seed-title TITLE` | `"<dossier> - Shared Login"` / `"… - Welcome"` | Titre de l'enregistrement de départ. |
| `--seed-text TEXT` | `"Team vault provisioned."` | Corps de la note pour `--seed note`. |
| `--seed-login USER` | — | Valeur `login=` pour `--seed login` (le mot de passe est toujours généré via `$GEN`). |
| `--root-record TITLE` | — | Crée un enregistrement login à la **racine** du coffre (sans dossier). |
| `--root-login USER` | — | Valeur `login=` pour `--root-record`. |
| `--out FILE` | `provision-team-folders.batch` | Chemin du fichier batch généré (mode génération). |
| `--dry-run` | off | Aperçu seul ; n'écrit rien, ne modifie rien. |
| `--execute` | off | Exécute le plan immédiatement via la connexion persistante (avec garde-fou « canary » sur le premier enregistrement login). |

## Exemples (tous les modes)

```bash
# 1. SYNC, aperçu (aucune modification, pas d'écriture d'état) :
python3 gen_team_folder_batch.py --sync --state ~/keeper-team-sync.json \
    --node "Engineering" --include "Departaments" --prefix "Team-" \
    --permissions full --seed login --seed-login svc@example.com --dry-run

# 2. SYNC, appliquer (idempotent ; planifiable sans risque) :
python3 gen_team_folder_batch.py --sync --state ~/keeper-team-sync.json \
    --node "Engineering" --include "Departaments" --prefix "Team-" \
    --permissions full --seed login --seed-login svc@example.com

# 3. SYNC avec élagage opt-in (révoque les accès de l'autre nœud ; prévisualiser !) :
python3 gen_team_folder_batch.py --sync --state ~/keeper-team-sync.json \
    --node "Engineering" --include "Departaments" --prefix "Team-" \
    --prune-grants --dry-run

# 4. Ponctuel : lire en direct, GÉNÉRER un fichier batch à relire :
python3 gen_team_folder_batch.py --fetch-teams --node "Engineering" \
    --include "Departaments" --prefix "Team-" --permissions full \
    --seed login --seed-login svc@example.com --out plan.batch
#   puis dans `keeper shell` :  run-batch plan.batch

# 5. Ponctuel : lire en direct, EXÉCUTER immédiatement (avec canary) :
python3 gen_team_folder_batch.py --fetch-teams --node "Engineering" \
    --include "Departaments" --prefix "Team-" --seed login \
    --seed-login svc@example.com --execute

# 6. HORS-LIGNE depuis un export CSV (sans connexion) :
#   dans `keeper shell` :  enterprise-info --teams --format csv --output teams.csv
python3 gen_team_folder_batch.py teams.csv --existing existing-sf.csv \
    --prefix "Team-" --permissions full --seed login \
    --seed-login svc@example.com --out plan.batch

# 7. Déposer une NOTE au lieu d'un enregistrement login :
python3 gen_team_folder_batch.py --fetch-teams --node "Engineering" \
    --include "Departaments" --prefix "Team-" \
    --seed note --seed-text "Stocker ici les identifiants partagés de l'équipe." --execute

# 8. Créer un enregistrement login unique à la RACINE du coffre :
python3 gen_team_folder_batch.py --root-record "Shared Root Login" \
    --root-login svc@example.com --execute

# 9. Chemin de config personnalisé :
python3 gen_team_folder_batch.py --sync --state ~/keeper-team-sync.json \
    --config /chemin/vers/config.json --include "Departaments" --prefix "Team-"
```

### Ce qu'affiche une exécution

```
Persistent login OK as admin@example.com.
--node 'Engineering' -> [<node_id>]
9 unique team name(s) in node 'Engineering'; 0 with duplicate UIDs ...
Filtered 26 -> 9 team(s).
SYNC PLAN: +0 folder(s), +0 grant(s), +0 record(s), -0 grant(s) [prune]. Absent (KEPT, never deleted): 0.
State written to ~/keeper-team-sync.json (run #2).
```

- `+0/+0/+0` à la réexécution = **rien n'a changé** (idempotent).
- `Absent (KEPT, …)` = équipes de l'état qui ne sont plus retournées par la requête ; **signalées, jamais supprimées**.
- `-N grant(s) [prune]` n'est non nul qu'avec `--prune-grants`.

### Forme du fichier d'état

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

## Planification

```cron
# La connexion persistante doit être valide ; replanifier `keeper login` régulièrement.
0 7 * * *  /usr/bin/python3 /chemin/vers/gen_team_folder_batch.py --sync \
  --state ~/keeper-team-sync.json --node "Engineering" --prefix "Team-" \
  --permissions full --seed login --seed-login svc@example.com \
  --include "Departaments" >> ~/keeper-sync.log 2>&1
```

> **Session toujours active plutôt qu'une connexion à chaque exécution :** voir
> [Planification en mode service](docs/service-mode.fr.md) — un modèle documenté
> (non encore testé en réel avec cet outil) pour s'exécuter via l'API REST de Commander.

## Tests

```bash
python3 -m unittest discover -s tests -v   # 23 tests de fonctions pures, sans coffre
```

Couvre l'analyse CSV, le filtrage include/exclude, la génération des
enregistrements/titres, le constructeur de plan (accès par UID, déduplication,
réparation des dossiers existants, un seul enregistrement), et une assertion
qu'**aucune commande destructive n'est jamais émise**.

## Matrice de validation

État honnête — ce qui a réellement été exécuté, et comment.

| Capacité | État |
|---|---|
| Reprise « fail-closed » de la connexion persistante | ✅ **vérifié en réel** (reprise silencieuse / arrêt sans invite) |
| `--fetch-teams` (lecture équipes + dossiers existants) | ✅ vérifié en réel |
| Accès **par UID** appliqué à un dossier | ✅ vérifié en réel (9 dossiers réparés, 0→2 accès) |
| Chemin d'accès `--execute` | ✅ vérifié en réel (18 accès) |
| `--sync` lecture / plan / no-op idempotent / écriture d'état | ✅ vérifié en réel (+0/+0/+0) |
| `--node` résolution + périmètre | ✅ vérifié en réel (dry-run ; résout le node_id, liste les nœuds en cas d'erreur) |
| Création d'enregistrement login + mot de passe `$GEN` | ✅ vérifié en réel |
| Nettoyage des doublons (`rm -f` → Corbeille) | ✅ vérifié en réel |
| Logique des fonctions pures (analyse, filtre, plan, seed) | ✅ 23 tests unitaires |
| CI Python 3.9 / 3.11 / 3.13 | ✅ vert |
| `--sync` **créant de nouveaux** dossiers/accès/enregistrements | ✅ vérifié en réel (dossier `SYNCTEST-` : +1 dossier, +2 accès, +1 enregistrement) |
| Révocation réelle via `--prune-grants` | ✅ vérifié en réel (un accès inter-nœud révoqué sur le dossier de test, 2→1) |
| Création réelle de `--seed note` (encryptedNotes) | ✅ vérifié en réel |
| Chemin de la commande `run-batch` (générer → `keeper shell` `run-batch`) | ✅ vérifié en réel — **la commande est `run-batch`, pas `batch`** |
| Branche « canary » de `--execute` dans l'outil | ✅ vérifié en réel (canary déclenché, mot de passe `$GEN` 12 caractères) |
| Multi-locataire / autres versions Commander / passage à l'échelle | ❌ non testé (throttling déjà observé à ~18 équipes) |
| Validation sécurité du modèle `--seed login` partagé | ❌ nécessite une revue produit/sécurité |

**Validé sur :** un locataire **démo** éditeur (pas un locataire de production client), `keepercommander==18.0.3`, Python 3.13, macOS.

## Fragilité

Dépend d'API Commander internes qui **ne constituent pas un contrat stable** :
`get_params_from_config`, `auth.login_steps.LoginUi`, `params.shared_folder_cache`,
`params.enterprise`, `params.folder_cache`, `cli.do_command`. **Revalider sur un
locataire réel avant de monter la version de `keepercommander`.**

## Dépannage & récupération

| Symptôme | Cause / solution |
|---|---|
| `Persistent login not active (… required). Run keeper login.` | Session expirée. Lancer `keeper login`, puis relancer. L'outil **fail-closed** — il n'invite pas et ne risque pas de verrouillage. |
| `--node 'X' matched no node.` | Le message liste tous les nœuds disponibles — en copier un exactement, ou passer le `node_id` numérique. |
| `Throttled (attempt 1/3), retrying in 60 seconds` | Limitation côté serveur ; le client réessaie. Durée plus longue sur grands locataires (échelle non testée). |
| `Multiple matches were found for team "<name>"` | Ne devrait pas arriver — l'outil accorde par `team_uid`. Si observé, c'est une version ancienne qui accordait par nom ; mettre à jour. |
| Création par erreur | **Enregistrements** → **Corbeille** : `trash list`, `trash restore <uid>`. **Dossiers** : supprimer dans l'UI du coffre ou `rmdir`/`rm` dans `keeper shell` (l'outil ne les supprime jamais). |
| Doublons après exécutions répétées | N'arrive pas en `--sync` (il vérifie d'abord un enregistrement au titre attendu). Les modes ponctuels `--execute`/batch ne sont **pas** conscients de l'état — préférer `--sync` pour les répétitions. |

## Notes de sécurité

- Déposer un enregistrement login partagé (`--seed login`, un identifiant partagé
  par dossier d'équipe) est un choix de modèle délibéré — confirmer qu'il
  correspond à la politique du client.
- Le compte opérateur nécessite enterprise admin / Share Admin.
- Le JSON `--state` contient des UID d'équipe et de dossier (pas de secrets) mais est ignoré par git de toute façon.
