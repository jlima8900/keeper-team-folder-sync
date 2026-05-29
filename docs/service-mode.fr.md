# Exécution planifiée via le mode service de Commander

🇫🇷 **Français** · 🇬🇧 [English version](service-mode.md)

> **⚠️ Statut : modèle documenté, NON testé en réel avec cet outil.**
> Ce guide s'appuie sur la documentation officielle Keeper
> ([Service Mode REST API](https://docs.keeper.io/keeperpam/commander-cli/service-mode-rest-api))
> et sur un test antérieur, distinct, de déploiement en mode service.
> **L'intégration de *cet* outil avec le mode service n'a pas été exécutée de
> bout en bout.** À considérer comme une conception à valider, pas une recette
> vérifiée. Même avertissement que le README : preuve de concept personnelle,
> pas un produit officiel/soutenu par Keeper.

## Pourquoi le mode service pour la planification

La section [Planification](../README.fr.md#planification) du README utilise cron
+ la connexion « fail-closed » de l'outil. Sa faiblesse : **la connexion
persistante expire**, donc une tâche cron non supervisée finit par exiger qu'un
humain relance `keeper login`.

Le **mode service** exécute Commander comme un processus long-vivant déjà
authentifié, exposant une API REST. Un seul processus détient la session ; le
planificateur se contente d'envoyer (POST) des commandes. Cela ne rend pas
l'authentification éternelle — si le processus meurt ou la session est révoquée,
il faut se réauthentifier — mais supprime la connexion à chaque exécution.

## Fonctionnement de l'API REST (d'après la doc)

```bash
# Créer le service : limiter les commandes autorisées à CE que l'outil émet.
service-create -p 9090 -f json \
  -c 'enterprise-info,sync-down,ls,tree,mkdir,share-folder,record-add,run-batch'
# Le démarrer (utilise la config chiffrée en cache ; sans paramètres ensuite) :
service-start
```

`service-create` affiche une **clé API**. Appel :

```bash
# v1 — synchrone :
curl -X POST 'http://localhost:9090/api/v1/executecommand' \
  -H 'Content-Type: application/json' -H 'api-key: <CLE_API>' \
  --data '{"command": "run-batch /opt/keeper/plan.batch"}'

# v2 — asynchrone (renvoie request_id ; interroger /api/v2/result/<request_id>) :
curl -X POST 'http://localhost:9090/api/v2/executecommand-async' \
  -H 'Content-Type: application/json' -H 'api-key: <CLE_API>' \
  --data '{"command": "run-batch /opt/keeper/plan.batch"}'
```

Forme de réponse : `{"command": "...", "data": <...>, "status": "success"}`.

## Authentification de l'hôte du service

- **VM / hôte natif :** établir une fois la connexion persistante ou biométrique :
  - `this-device persistent-login on` (enregistrer l'appareil, régler le délai), ou
  - `biometric register`.
- **Conteneurs (test K8s antérieur) :** la config de connexion persistante **ne
  fonctionne pas** en conteneur — le conteneur présente une empreinte d'appareil
  différente de l'hôte. Passer `--user / --password / --server` directement pour
  qu'à chaque démarrage un nouvel appareil s'enregistre. (Identifiants via un
  secret monté / variable d'env, jamais inscrits dans l'image.)
- Certaines commandes de configuration ne fonctionnent pas quand les identifiants
  sont dans le trousseau de l'OS — utiliser `keeper shell --config-file <fichier>`
  pour la configuration initiale.

## Modèle recommandé avec cet outil

Séparer la **décision** (quoi changer, relu par un humain) de l'**exécution**
(non supervisée, sur le service) :

1. **Poste opérateur** (sa propre connexion — *pas* la config du service, pour
   éviter la concurrence sur le rafraîchissement persistant mono-appareil) :
   ```bash
   python3 gen_team_folder_batch.py --fetch-teams --node "<Noeud>" \
       --include "<Dept>" --prefix "Team-" --permissions full \
       --seed login --seed-login svc@example.com --out plan.batch
   ```
   Relire `plan.batch`.
2. **Copier `plan.batch`** vers un chemin lisible par l'hôte du service (ex. `/opt/keeper/plan.batch`).
3. **Planificateur** (cron / timer systemd) : POST de la commande run-batch au service :
   ```bash
   curl -fsS -X POST 'http://localhost:9090/api/v1/executecommand' \
     -H 'Content-Type: application/json' -H "api-key: $KEEPER_SVC_API_KEY" \
     --data '{"command": "run-batch /opt/keeper/plan.batch"}'
   ```

Le batch généré est idempotent côté Keeper (`mkdir` sur un dossier existant = un
avertissement sans danger ; accès/enregistrements non dupliqués), donc relancer
est sûr. Pour un vrai suivi d'état (le marquage `absent`, le rapport `+0/+0/+0`),
le mode `--sync` (bibliothèque) reste plus riche qu'un simple `run-batch` — le
mode service échange cela contre une session toujours active.

## Pourquoi ce n'est pas du « prêt à brancher »

`--sync` lit l'état du coffre depuis des structures en mémoire
(`params.enterprise`, `shared_folder_cache`) qui ne correspondent pas 1:1 à la
sortie des commandes REST, et exécute via `cli.do_command` en intra-processus —
une session différente de celle du service. Un mode natif
`--service-url`/`--api-key` (POST des commandes générées vers l'API REST, analyse
de `enterprise-info --format json` pour les UID d'équipe) est possible mais non
développé. En attendant, le modèle **générer → POST `run-batch`** ci-dessus est
la voie pragmatique.

## Liste de contrôle sécurité (options de service-create)

| Préoccupation | Option |
|---|---|
| Moindre privilège | Liste blanche `-c` — uniquement les commandes ci-dessus ; en ajouter via `service-config-add` |
| Durée des jetons | `-te 24h` (ex. `30m` / `7d`) ; utiliser **des clés API différentes par cas d'usage** |
| Exposition réseau | Liste blanche `-aip` / liste noire `-dip` d'IP clientes ; lier à localhost si possible |
| Transport | `-crtf` / `-crtp` pour TLS |
| Abus | Limite de débit `-rl` (ex. `100/hour`) |
| Confidentialité des réponses | Clé de chiffrement `-ek` (AES-256-GCM) |

Stocker la clé API comme tout secret (ex. coffre Keeper / KSM), l'injecter via une
variable d'env, ne jamais la committer.

## Avant de s'appuyer dessus en production

- Valider la boucle **générer → POST `run-batch`** de bout en bout sur un
  locataire de test (c'est la partie non testée).
- Confirmer que la liste de commandes autorisées est suffisante et minimale.
- Décider si le suivi d'état de `--sync` est nécessaire ; si oui, un mode REST
  natif est requis (non développé).
