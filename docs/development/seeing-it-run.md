# Seeing it run, before anything is published

Two environments, because there are two loops with very different costs, and
running the slow one for the fast job is what makes a project feel heavy.

Neither publishes anything. No image reaches a registry, no commit reaches
GitHub, and the production release in the cluster is never touched.

## `make dev` — the everyday loop

Four processes from the working tree on your own machine: the engine under
`uvicorn --reload`, and the three surfaces under Streamlit, which reloads on
save by itself. Edit a file, refresh the browser.

```bash
make dev                              # token mode: sign-in, sessions, quotas
make dev AUTH=none                    # the self-hosted contract instead
make dev OPERATOR_EMAILS=you@here     # who gets the operator privilege
```

|          |                                  |
| -------- | -------------------------------- |
| engine   | <http://localhost:8000/docs>     |
| Studio   | <http://localhost:8502>          |
| Console  | <http://localhost:8503>          |
| HomeTube | <http://localhost:8501>          |

**Signing in works with no mail server.** `CONTENT_MAILER_URL` is empty, so the
engine writes the link to its own log (ADR 0031) and `make dev` lifts it out
and prints it on its own line. Ask for a link on any surface, then look at the
terminal.

Two settings differ from production, and only two. The session cookie is not
`Secure`, because localhost is not HTTPS and a `Secure` cookie would be set and
then never sent back. And its domain is empty, so the browser files it under
`localhost` — cookies ignore the port, so one sign-in covers all four.

Nothing is containerised: four ordinary Python processes, the engine from
`apps/backend/.venv` and the surfaces from `.venv-ui`. Docker does not need to
be installed, let alone running.

All four bind to the loopback only. Streamlit listens on every interface by
default, which on a laptop means the LAN can reach a development build;
`ADDRESS=0.0.0.0 make dev` opts back in when you want to look at it from a
phone.

Data lives in `playground/dev-data/`, separate from the compose stack's, so
neither can surprise the other. Logs are in `playground/dev-data/logs/`.

Ctrl-C stops all four, and `make dev-stop` does it from anywhere — a stack
started from another terminal has no Ctrl-C to press.

## `make k8s-test` — the end-to-end check

The same code in the cluster, as a **second release** in its own namespace, on
its own volumes, on LAN-only hostnames. This is what proves the things a laptop
cannot: the ingress, a session cookie shared across a parent domain by four
real hostnames, the per-user volumes, and the layout migration on a real disk.

```bash
make k8s-test                 # build all four, move them, deploy
make k8s-test ONLY=studio     # just the one that changed
make k8s-test SKIP_BUILD=1    # redeploy what is already on the node
make k8s-test-link            # the sign-in link, out of the engine's pod log
make k8s-test-down            # remove the release and its volumes
```

|          |                                                |
| -------- | ---------------------------------------------- |
| engine   | <http://api.content-test.k3s.lab/docs>         |
| Studio   | <http://studio.content-test.k3s.lab>           |
| Console  | <http://console.content-test.k3s.lab>          |
| HomeTube | <http://hometube.content-test.k3s.lab>         |

Those names need one line in `/etc/hosts` pointing at the cluster node; the
values file says which.

### How an image gets there without a registry

Built on an amd64 machine over SSH (`DOCKER_HOST=ssh://…`), because the node is
amd64 and a laptop may not be: cross-building Python images under emulation
costs minutes each, while a native build with a warm layer cache costs seconds
when only application code changed.

Moved with `docker save | ssh node ctr images import`, straight through, never
landing on a disk on the way. Measured at roughly four seconds per image on a
gigabit LAN. No registry means nothing on the cluster has to be reconfigured
and nothing has to be restarted — `k3s` reads its registry configuration only
at startup, and restarting it restarts every pod on the node.

`pullPolicy: Never` in the test values is what makes the node use the image
that was just imported. Without it containerd would go looking for a tag that
exists on no registry, and the pod would sit in `ErrImagePull` beside a perfect
local copy.

### What it deliberately does not have

**No public hostname.** No `extraHosts`, so nginx and the Cloudflare tunnel —
which only know the production names — cannot reach it. A test environment on
the open internet is a production environment nobody is watching.

**No mailer.** The sign-in link goes to the pod log, which is what
`make k8s-test-link` reads. A test environment that sends real email eventually
sends it to real people.

**No version of its own.** The images carry the working tree, and the version
string is whatever the tree currently declares — it is bumped at release time,
not here. Read the image tag (`:test`), not the version, to know what is
running.

## Which one to use

Use `make dev` unless the thing you are looking at is one of the four the
cluster alone can show. Layout, wording, a banner, a form, an error message and
the whole shape of an interaction are identical in both, and one of them
answers in two seconds.
