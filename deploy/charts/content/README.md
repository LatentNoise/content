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

## Pourquoi deux Deployments, deux Services, et (par défaut) deux Ingress

C'est la question qui revient : *« je pensais qu'on aurait un seul ingress, un seul service, et juste plusieurs répliques. »* Les deux notions n'ont rien à voir, et voici la différence.

**Une réplique, c'est le MÊME conteneur lancé plusieurs fois.** Même image, même rôle, même port. On en met plusieurs pour encaisser la charge ou survivre à la perte d'un pod. **Kubernetes les met derrière un seul `Service`, qui répartit tout seul.** Un `Deployment` à 3 répliques = un seul Service, un seul Ingress.

**Content et Studio ne sont pas des répliques l'un de l'autre : ce sont deux applications différentes.**

| | Content | Studio |
|---|---|---|
| Image | `ghcr.io/latentnoise/content` | `ghcr.io/latentnoise/content-studio` |
| Ce que c'est | le moteur : l'API, les jobs, le stockage | une UI Streamlit qui *appelle* cette API |
| Port | 8000 | 8501 |
| Sans l'autre | fonctionne (c'est une API) | ne sert à rien |

Deux images, deux ports, deux rôles → **deux `Deployment` et deux `Service`, nécessairement**. Un Service pointe vers un ensemble de pods identiques ; il ne peut pas servir deux applications sur deux ports différents.

**En revanche, le nombre d'Ingress est un choix, et il y en a deux.**

### Option A — deux noms de domaine (le défaut actuel)

`content.k3s.lab` → le moteur · `studio.k3s.lab` → l'UI. Chaque application a son nom. Pratique en LAN, chaque brique se teste séparément.

### Option B — **un seul nom de domaine** (`ingress.singleHost: true`)

`content.k3s.lab/` → l'UI · `content.k3s.lab/api` → le moteur. **Un seul Ingress, un seul nom, un seul certificat.**

🔑 **Et ça marche sans reconfigurer quoi que ce soit, parce que l'API de Content vit déjà sous `/api/v1/…`** — il n'y a pas de collision de chemins, rien à réécrire, aucun `baseUrlPath` à régler côté Streamlit.

**C'est la forme à choisir pour une mise en ligne publique** : une seule URL à donner, un seul nom dans le tunnel Cloudflare, un seul certificat. Bascule :

```bash
helm upgrade content ./deploy/charts/content -n content --reuse-values --set ingress.singleHost=true
```

*(L'Ingress de Studio disparaît automatiquement dans ce mode — le template le sait.)*

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
- **Litestream** — la sauvegarde continue vers S3 n'est pas dans ce chart (elle demande un bucket et des identifiants). Le manifest de référence est dans `~/Independence/Services/Content/k8s/02-content.yaml`.
- **TLS** — `ingress.tls.enabled: false` : en LAN, en HTTP. Le tunnel Cloudflare terminera le TLS quand il sera en place.
