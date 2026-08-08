# ADR 0003 — `source.type` (how it is supplied) ≠ `resource_type` (what it is)

Status: accepted (2026-07-17)

## Context

A URL may reference a video, a PDF, a web page, a playlist, an archive or unknown content. HomeTube conflates the two (a URL there is always a YouTube-like video or playlist). Content must accept sources whose nature is only known after analysis.

## Decision

- `SourceDescriptor.type` is the **supply mode**, the discriminator of the union: `url`, `file`, `upload`, `text` (+ `collection`, `connector` reserved).
- `ResourceAnalysis.resource_type` is the **detected nature**: `video`, `audio`, `pdf`, `webpage`, `collection`, `unknown`…
- The two share neither field nor enum; client `hints` (`hints.resource_type`) are not guaranteed and are checked by the analysis.

## Consequences

- Adding a supply mode (connector) does not affect the taxonomy of natures, and vice versa.
- Feasibility is decided on the detected nature, never on the supply mode.

## Alternatives considered

- **A single semantic `type` field** (`"youtube_video"`, `"pdf_url"`): combinatorial explosion of modes × natures, and a contract that lies as soon as a URL surprises us.
- **Detection left to the client**: contradicts the product (the engine analyzes) and makes the contract dishonest.
