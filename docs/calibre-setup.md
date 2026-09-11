# Calibre setup

## Access modes

CalibreAudioBridge writes to your library **only through `calibredb`** — never
via direct sqlite access. Calibre allows exactly one writer, and calibredb
refuses direct library access while the calibre GUI (or `calibre-server`) is
running. The tool therefore resolves an access mode at startup:

1. If `calibre.server_url` is configured, it connects through calibre's
   **Content Server** (works while the GUI is open).
2. Otherwise it uses the **library path directly** (requires calibre to be
   closed for writes; a locked library results in exit code 3).

For the server mode, enable in calibre:
*Preferences → Sharing over the net → Start Content Server*, and
*Preferences → Sharing over the net → Advanced → Allow local writes*.

## One-time bootstrap: custom columns

```
cab bootstrap
```

Creates four custom columns (must run once with calibre closed / no server URL):

| Column | Type | Purpose |
|---|---|---|
| `#cab_audiobook_status` | enumeration | pending / processing / done / failed / skipped_* |
| `#cab_summary_status` | enumeration | same |
| `#cab_last_run` | datetime | last generation attempt |
| `#cab_details` | comments | JSON diagnostics (engine, models, errors) |

## Variant ↔ format mapping

Calibre stores one format per extension per book, so the two variants map to:

* Audiobook → **M4B** (chapters, cover, AAC)
* Audio summary → **MP3**

Both attach to the same book record via `calibredb add_format`.

## Safety checklist

- Back up your library (or at least `metadata.db`) before the first production run.
- Run `cab scan` first — it is read-only and shows exactly what would be processed.
- Books whose extraction fails due to DRM are marked `skipped_drm` and never
  retried; CalibreAudioBridge does not circumvent DRM.
- Thinner-than-expected extractions (e.g. scanned PDFs) are marked
  `skipped_no_text`.
