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

Use stable, descriptive kebab-case names **without a version**: the release
URL already carries the tag, and keeping names stable means the README embeds
never change across releases:

```text
demo-hometube.gif
screenshot-studio.png
screenshot-console.png
```

Once attached to a published release, each file is addressable as:

```text
https://github.com/LatentNoise/content/releases/download/<tag>/<name>
```

and that URL is what the README embeds (see the placeholder comment near the
top of the root README).

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
