# Chart Helm — Content

Déploie le moteur Content (et, en option, l'UI Studio) sur un cluster Kubernetes.

## Déployer

```bash
helm upgrade --install content ./deploy/charts/content \
  -n content --create-namespace \
  -f deploy/charts/content/values-home.yaml
```

Vérifier : `kubectl -n content get pods,svc,ingress,pvc` · `kubectl -n content logs deploy/content -f`

Changer de version : `helm upgrade content ./deploy/charts/content -n content --reuse-values --set image.tag=0.7.2`
Revenir en arrière : `helm rollback content -n content`

## Ce que le chart déploie

| Objet | Rôle |
|---|---|
| `Deployment content` | le moteur, **1 réplica, stratégie `Recreate`** |
| `Service content` | l'IP stable interne — c'est lui qui fait le load balancing, rien à installer |
| `Ingress content` | l'entrée HTTP par nom d'hôte (Traefik) |
| `PVC content-data` | `/data` : SQLite, jobs, artefacts, cache |
| `PVC content-output` | `/output` : la bibliothèque de livraison |
| `ConfigMap content-config` | toute la configuration non secrète |
| `Deployment/Service/Ingress …-studio` | l'UI Streamlit, optionnelle |
| `ServiceMonitor` | **désactivé** — Content n'expose pas de `/metrics` aujourd'hui |

## Les trois choix qui ne se discutent pas, et pourquoi

1. **`replicaCount: 1`.** SQLite est la source de vérité *et* la file de travaux (ADR 0006), avec des verrous POSIX **locaux** ; Litestream suppose un seul writer. Pour aller plus vite : monter `CONTENT_MAX_CONCURRENT_JOBS`, pas le nombre de pods — sur un seul nœud, deux pods se partagent le même CPU.
2. **`strategy: Recreate`.** Un rolling update ferait cohabiter deux pods sur le même volume et la même base, même quelques secondes.
3. **Un tag de version explicite, jamais `latest`.** Un tag mutable force `imagePullPolicy: Always` : un simple redémarrage devient un déploiement non décidé, et deux pods démarrés à deux moments peuvent porter deux versions.

## Quatre surfaces, quatre Services, quatre Ingress — et pourquoi ce ne sont pas des répliques

*« Je pensais qu'on aurait un seul ingress, un seul service, et juste plusieurs répliques. »* Les deux notions n'ont rien à voir.

**Une réplique, c'est le MÊME conteneur lancé plusieurs fois** — même image, même rôle, même port. On en met plusieurs pour la charge ou la tolérance de panne, et **Kubernetes les met derrière UN seul `Service`, qui répartit tout seul**. Un Deployment à 3 répliques = 1 Service, 1 Ingress.

**Ici, ce sont quatre applications différentes**, chacune avec son image, son rôle et son public :

| Surface | Image | Port | Pour qui |
|---|---|---|---|
| **moteur** | `content` | 8000 | l'API — les clients, les SDK, les autres UI |
| **Studio** | `content-studio` | 8501 | l'opérateur, en création |
| **Console** | `content-console` | 8501 | l'opérateur, en exploitation |
| **HomeTube** | `content-hometube` | 8501 | quelqu'un qui veut télécharger une vidéo |

Un `Service` pointe vers un ensemble de pods **identiques** : il ne peut pas servir quatre applications. Donc **quatre Services**, nécessairement. Et comme chacune doit être atteignable directement, **quatre Ingress** — un nom de domaine par surface.

**Ajouter une surface = une entrée dans `uis`**, rien d'autre : le template les traite génériquement (elles sont toutes en Streamlit sur 8501, healthcheck `/_stcore/health`).

### Les URLs des surfaces sœurs, injectées automatiquement

Chaque UI reçoit `CONTENT_<AUTRE_SURFACE>_URL` pour **les surfaces effectivement activées et exposées** — jamais un lien en dur, parce qu'un opérateur qui auto-héberge n'aura ni les mêmes surfaces ni les mêmes URLs.

⚠️ **Ces variables ne sont pas encore lues par les applications.** Le déploiement est prêt, le code non : voir `~/Independence/Services/Content/2026-09-11 La navigation entre les surfaces…`, qui dit aussi **quels liens ne pas ouvrir** (la Console n'a pas d'authentification — ADR 0024 — et ne doit jamais être atteignable depuis une surface publique).

## Les secrets

Jamais dans git. Hors bande :

```bash
kubectl -n content create secret generic content-secrets \
  --from-literal=CONTENT_ANTHROPIC_API_KEY=...
helm upgrade content ./deploy/charts/content -n content --reuse-values \
  --set existingSecret=content-secrets
```

## Ce qui n'est pas branché (état au 11/09/2026)

- **Ollama** — `CONTENT_OLLAMA_URL` est vide : les résumés ne fonctionneront pas. Ni `192.168.21.30:11434` ni `.85` ne répondaient depuis le cluster. À brancher quand le port sera joignable.
- **Les cookies YouTube** (`CONTENT_CREDENTIALS`) — neutralisés : aucun fichier n'est monté. À ajouter via un Secret + un `volumeMount` si les sources authentifiées deviennent nécessaires.
- **`/input`** — le docker-compose monte un dossier de sources locales en lecture seule ; sans équivalent ici, seules les sources par URL et les envois de fichiers fonctionnent.
### Séparer l'API du worker (`worker.enabled`)

Même image, même code, une variable d'environnement (ADR 0032). Avec
`worker.enabled=true`, le déploiement principal ne fait plus tourner les
travaux et se contente de répondre aux requêtes, tandis qu'un second
déploiement fait le travail lourd. L'API n'attend plus derrière un transcodage.

```bash
helm upgrade content deploy/charts/content --reuse-values --set worker.enabled=true
```

⛔ **Ce que ça n'ajoute pas : du CPU.** Tous les pods d'un nœud se partagent les
mêmes cœurs ; le débit reste réglé par `maxConcurrentJobs`. Et **tous les pods
doivent rester sur UN SEUL nœud** : le volume de données est ReadWriteOnce et
lié au nœud, et un fichier SQLite atteint par le réseau se corrompt.

⚠️ Avant d'activer la sauvegarde continue Litestream *en même temps* que le
worker, vérifier son comportement avec plus d'un processus écrivain.

- **Litestream** — la sauvegarde continue vers S3 n'est pas dans ce chart (elle demande un bucket et des identifiants). Le manifest de référence est dans `~/Independence/Services/Content/k8s/02-content.yaml`.
- **TLS** — `ingress.tls.enabled: false` : en LAN, en HTTP. Le tunnel Cloudflare terminera le TLS quand il sera en place.
