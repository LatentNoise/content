# Helm chart — Content

Deploys the Content engine and its three UIs on a Kubernetes cluster.

## Deploying

```bash
helm upgrade --install content ./deploy/charts/content \
  -n content --create-namespace \
  -f my-values.yaml
```

**A deployment brings its own values file**, which belongs to its operator's
infrastructure repository and not to this public chart. Most of what it needs to
say is one line:

```yaml
domain: example.com   # api., studio., console., hometube.example.com
https: true
```

Every address follows from those two values — see *One domain* below.

Check it: `kubectl -n content get pods,svc,ingress,pvc` ·
`kubectl -n content logs deploy/content -f`

Change version: `helm upgrade content ./deploy/charts/content -n content --reuse-values --set image.tag=0.7.2`
Roll back: `helm rollback content -n content`

## What the chart deploys

| Object | Role |
|---|---|
| `Deployment content` | the engine, **1 replica, `Recreate` strategy** |
| `Deployment content-worker` | optional, runs the jobs (ADR 0032) |
| `Service content` | the stable internal address, which does the load balancing |
| `Ingress content` | the HTTP entry point, by hostname |
| `PVC content-data` | `/data`: SQLite, jobs, artifacts, cache |
| `PVC content-output` | `/output`: the delivery library |
| `ConfigMap content-config` | all non-secret configuration |
| `Deployment/Service/Ingress …-studio/-console/-hometube` | the three Streamlit UIs |
| `ServiceMonitor` | **disabled** — Content exposes no `/metrics` today |

## The three choices that are not up for debate, and why

1. **`replicaCount: 1`.** SQLite is both the source of truth *and* the job
   queue (ADR 0006), with **local** POSIX locks, and Litestream assumes a
   single writer. To go faster, raise `CONTENT_MAX_CONCURRENT_JOBS` rather
   than the pod count: on one node, two pods share the same CPU.
2. **`strategy: Recreate`.** A rolling update would put two pods on the same
   volume and the same database, even if only for a few seconds.
3. **An explicit version tag, never `latest`.** A mutable tag forces
   `imagePullPolicy: Always`: a restart becomes an undecided deployment, and
   two pods started at two different moments can run two versions.

## Four surfaces, four Services, four Ingresses — and why these are not replicas

The two ideas have nothing to do with each other, and the distinction is worth
stating because it is a common trap.

**A replica is the SAME container started several times** — same image, same
role, same port. You run several for load or fault tolerance, and **Kubernetes
puts them behind ONE `Service`, which spreads traffic on its own**. A
Deployment with 3 replicas is still 1 Service and 1 Ingress.

**Here there are four different applications**, each with its own image, role
and audience:

| Surface | Image | Port | For whom |
|---|---|---|---|
| **engine** | `content` | 8000 | the API: clients, SDKs, the other UIs |
| **Studio** | `content-studio` | 8501 | the general creation UI |
| **Console** | `content-console` | 8501 | operations: observe and steer |
| **HomeTube** | `content-hometube` | 8501 | someone who wants to download a video |

A `Service` points at a set of **identical** pods, so it cannot serve four
applications. Hence four Services, necessarily. And since each must be
reachable directly, four Ingresses, one hostname per surface.

**Adding a surface is one entry in `uis`** and nothing else: the template
treats them generically, since they are all Streamlit on 8501 with the
`/_stcore/health` probe.

### One domain, and every address derived from it

The four hostnames share a parent because a session cookie set on that parent
is sent by the browser to all of its subdomains. That is what makes one
sign-in work across the four surfaces without merging them or serving them
under paths (ADR 0033).

So the parent *is* the configuration. From `domain` and `https` the chart
derives:

| Derived | From `domain: example.com`, `https: true` |
|---|---|
| Ingress hosts | `api.`, `studio.`, `console.`, `hometube.example.com` |
| `CONTENT_PUBLIC_API_URL` on each UI | `https://api.example.com` |
| `CONTENT_PUBLIC_BASE_URL` — the address in every sign-in email | `https://api.example.com` |
| `CONTENT_SESSION_COOKIE_DOMAIN` | `.example.com` |
| `CONTENT_SESSION_COOKIE_SECURE` | `true` — a browser never sends a Secure cookie over http |
| `CONTENT_SURFACES` — how the UIs find each other (ADR 0038) | each enabled surface's URL |
| `CONTENT_ALLOWED_REDIRECT_ORIGINS` | the same URLs |
| `CONTENT_SIGN_IN_DEFAULT_TARGET` | Studio |

These were seven separate settings, and in one production deployment two of
them disagreed: the UIs drew their Sign in button on a LAN name while the
engine's emails used the public one. One fact cannot disagree with itself.

**Anything derived can still be stated.** A surface's `subdomain`, its
`ingress.host` or `publicUrl`, a `config` key — the explicit value wins. A
common case: `uis.hometube.subdomain: hometube-app`, when `hometube.<domain>`
already serves something else.

**And a broken override is caught.** With `CONTENT_AUTH_MODE=token`, the engine
refuses to start on addresses that cannot sign anyone in — a surface outside
the cookie's domain, an http address beside a Secure cookie — and says which.

What stays outside the chart, and must agree by hand: the names your DNS,
tunnel or reverse proxy send to the cluster.

## Separating the API from the worker (`worker.enabled`)

Same image, same code, one environment variable (ADR 0032). With
`worker.enabled=true`, the main deployment stops running jobs and only answers
requests, while a second deployment does the heavy work. The API then never
waits behind a transcode.

```bash
helm upgrade content ./deploy/charts/content -n content --reuse-values \
  --set worker.enabled=true
```

⛔ **What this does not add: CPU.** Every pod on a node shares the same cores,
so throughput is still set by `maxConcurrentJobs`. And **every pod must stay
on ONE node**: the data volume is ReadWriteOnce and node-bound, and a SQLite
file reached over a network share corrupts.

⚠️ Before enabling Litestream continuous backup *at the same time* as the
worker, check its behaviour with more than one writing process.

## Secrets

Never in git. Out of band:

```bash
kubectl -n content create secret generic content-secrets \
  --from-literal=CONTENT_ANTHROPIC_API_KEY=...
helm upgrade content ./deploy/charts/content -n content --reuse-values \
  --set existingSecret=content-secrets
```

## What is not wired up

- **Ollama** — `CONTENT_OLLAMA_URL` is empty by default, so summaries do not
  work. Point it at an Ollama reachable from inside the cluster.
- **YouTube cookies** (`CONTENT_CREDENTIALS`) — neutralised: no file is
  mounted. Add a Secret and a `volumeMount` if authenticated sources become
  necessary.
- **`/input`** — docker-compose mounts a read-only folder of local sources;
  there is no equivalent here, so only URL sources and uploads work.
- **Litestream** — continuous backup to object storage is not in this chart,
  since it needs a bucket and credentials of its own.
- **TLS** — `ingress.tls.enabled: false` by default. Terminate TLS at your
  ingress controller or at whatever fronts the cluster.
