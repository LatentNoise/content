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
