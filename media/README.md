# media/ — the visuals workbench (nothing here is versioned)

Screenshots, demo GIFs, recordings and brand assets for Content's README,
documentation and releases are produced **in this folder** and published **as
release attachments** — never committed. That keeps the repository small
(about half a megabyte to clone) while the visuals still have a durable,
versioned home: the release that ships them.

Only this README is tracked. Everything else in `media/` is ignored by Git
(so this folder is local to your machine — a fresh clone starts empty) and
excluded from Docker build contexts.

## Layout

Create sub-folders as needed; the convention:

```text
media/
├── screenshots/   PNG stills — hometube, studio, console…
├── gifs/          finished demo loops, ready to attach
├── video/         raw screen recordings the GIFs are cut from
└── brand/         logo exports, banner, social-preview image
```

## Naming the files that go on a release

Date-prefixed kebab-case — the convention v0.1.0 established:

```text
2026-08-09-hometube-demo.gif
2026-08-09-studio.png
2026-08-09-console.png
```

The date is the **capture vintage**: it says at a glance how old a visual is,
and replacing one means a *new* name — so an updated embed can never be
masked by a stale cache, and the old file remains addressable on the release
that shipped it.

Once attached to a published release, each file is addressable as:

```text
https://github.com/LatentNoise/content/releases/download/<tag>/<name>
```

and those URLs are what the root README embeds (the hero GIF and the
Studio/Console screenshots). When you re-capture a visual, upload the newly
dated file to the current release and update the embeds to match.

## Capture checklist

So the frames age well and leak nothing:

- run a **clean stack** (`cp .env.example .env`, default values) so no
  personal library folders, URLs or account hints appear in dropdowns;
- use a short, neutral demo source;
- same window size and theme across all captures, so the set reads as one
  product;
- keep the demo GIF short (10–20 s, one loop) and moderate in width
  (~1200 px) — it will dominate the release's download size;
- re-capture after UI changes only when a visual actually lies about the
  product; stale-but-honest beats churn.
