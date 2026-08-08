# Product vision

## The problem

Fetching and transforming online or local content (downloading a video,
extracting its audio, getting a transcript, a summary…) currently requires
chaining technical tools by hand (yt-dlp, ffmpeg, a transcription model, an
LLM), knowing their options, and managing the files, errors and resumption
yourself. Every tool has its own syntax; the user's intent ("I want the summary
of this talk in French") gets lost in the implementation.

## The value proposition

Content is a **declarative resources → artifacts engine**. The user **declares**
what they supply (sources) and what they want to obtain (artifacts), with their
preferences and constraints. The engine **decides** the technical strategy, runs
it traceably, and delivers the results.

The value fits in one sentence: **the intent is stable, the implementation is
interchangeable.** The public contract never mentions a tool; providers, models
and infrastructure can evolve without breaking the clients.

## For whom

- **Today (confirmed):** a technical self-hosting/homelab user, through the HTTP
  API, a web UI, or scripts.
- **Targeted (a product hypothesis):** several kinds of client on the same API —
  web UI, CLI, SDK, agents, MCP.

## Main use cases

1. Download a video/audio from a URL, in the right format and quality.
2. Extract a transcript then a summary from a talk.
3. Process a local file that is already present (extraction, remux, subtitles).
4. Replay/diagnose a failed processing run.
5. Fetch and download the artifacts produced, with their provenance.

## How we know it creates value

- The user gets a result **without writing a single tool command**.
- The **same request** stays valid while the internals change.
- A processing run is **observable** (states, events) and **replayable**.
- Identical work is **not redone** (content-based reuse).

## General direction

Content must be able to **fully replace HomeTube** (a YouTube downloader: all of
its features + a UI dedicated to web URLs) **while remaining the general
engine**. HomeTube becomes a *use case* of the engine — a set of contract options
and a UI client — never a special code branch. Longer term, the engine goes
beyond video (documents, images, other sources and artifacts).

## Product hypotheses to validate

- **H1** — The declarative "sources → outputs" model is simpler for the user than
  driving the tools. *(To be confirmed by real UI usage.)*
- **H2** — The multi-client demand (CLI, SDK, agents) is real and not just API +
  UI. *(Unconfirmed; do not over-invest before proof.)*
- **H3** — The "beyond video" ambition has users. *(Unconfirmed; held in reserve,
  see scope.md.)*

This vision is deliberately **stable and short**. The concrete, revisable scope
is in [scope.md](scope.md); the trajectory in
[../roadmap/roadmap.md](../roadmap/roadmap.md).
