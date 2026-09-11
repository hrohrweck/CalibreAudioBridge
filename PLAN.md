# CalibreAudioBridge — Implementation Plan

**Status:** Draft v1 — for approval before implementation
**Date:** 2026-09-11
**Repo:** https://github.com/hrohrweck/CalibreAudioBridge

---

## 1. Mission

A scheduled (cron) tool that scans a Calibre library, finds books without audio
variants, and generates **two audio versions per book**:

1. **Audiobook** — full text, cleaned/optimized for listening by an LLM, then
   synthesized with an open-source TTS engine.
2. **Audio summary** — an LLM-generated summary of the book, synthesized with
   the same TTS layer.

Both artifacts are attached to the *same* book record in Calibre as new formats
(M4B audiobook, MP3 summary), with processing status tracked in custom columns.
LLMs are configurable with a strong preference for local models (Ollama,
llama.cpp, LM Studio); books are processed in chunks sized to each model's
context window.

Core properties: **local-first, idempotent, resumable, library-safe.**

---

## 2. Verified research findings that shape this plan

### 2.1 LuxTTS (user's suggested engine) — verified against primary sources

| Property | Finding |
|---|---|
| What it is | Open-source research project: [ysharma3501/LuxTTS](https://github.com/ysharma3501/LuxTTS) (5.4k stars), distilled ZipVoice, 48 kHz vocoder. luxtts.com is a landing page; several of its claims (multilingual, streaming, style prompts) are **not present in the code**. |
| License | Apache-2.0 (LICENSE + [HF model card](https://huggingface.co/YatharthS/LuxTTS)) — commercial OK |
| Locality | Fully local after weights download; runs on CUDA, **Apple Silicon MPS**, or CPU (ONNX int8). < 1 GB VRAM. |
| API | Python class only (`LuxTTS.encode_prompt()`, `LuxTTS.generate_speech()`). No PyPI package (git clone install), no HTTP server, no CLI. |
| Languages | **English only** — HF card `language: ["en"]`, default `lang: "en-us"`. German not supported. |
| Long text | **No built-in chunking.** `generate()` tokenizes the entire input in one inference call; behavior on multi-thousand-word inputs unverified. All chunking must be implemented by us (we need it anyway). |
| Voices | Zero-shot **voice cloning via reference audio only** (≥3 s wav/mp3). No fixed voice catalog. |
| Maturity | Created Jan 2026, 26 commits, **no tags/releases**, single maintainer, last commit Jun 2026. Research-grade. |

**Verdict:** usable as a local OSS engine for *English* books with experimental
status, but not a sane default (English-only, no releases, cloning-only voices).
The chunking layer we must build for it is engine-agnostic and reusable.

### 2.2 TTS engine comparison (long-form, local, Python)

| Engine | License | Languages | Voices | CPU | Fit |
|---|---|---|---|---|---|
| **Kokoro-82M** | Apache-2.0 | EN, DE + 6 more | ~50 fixed voices | fast (MLX/CoreML on macOS) | **Default engine** — best quality-per-cost, pip-installable |
| **Piper** | MIT (orig.) / GPL (piper1-gpl fork) | EN, DE + many | many fixed voices | very fast | **Fallback** — lowest resource, least expressive |
| **LuxTTS** | Apache-2.0 | EN only | cloning only | OK (ONNX) | Adapter included per user preference, EN books only |
| Coqui XTTS-v2 | MPL-2.0 code / **non-commercial model** | 17 incl. DE | cloning | slow (GPU pref.) | Rejected: license + weight |

### 2.3 Calibre automation — verified against manual.calibre-ebook.com + calibre source

1. **`calibredb --with-library <path>` refuses to run while the Calibre GUI or
   `calibre-server` is running** (single-instance lock; exit message recommends
   the Content Server). Source:
   [`src/calibre/db/cli/main.py` L164–175](https://github.com/kovidgoyal/calibre/blob/6eb13bdd70521287fcb40d1d25a3083292cffbdd/src/calibre/db/cli/main.py#L164-L175).
2. The same `--with-library` flag also accepts a **Content Server URL**
   (`http://localhost:8080/#library_id`) — safe while Calibre runs, requires
   *Preferences → Sharing over the net → Advanced → Allow local writes*.
3. `calibredb add_format <id> <file>` attaches a file as a format; **an existing
   format with the same extension is overwritten by default** (`--dont-replace`
   to keep). Calibre keeps **exactly one format per extension per book** — this
   constrains our variant mapping (→ D2).
4. Custom columns can be **created** via `calibredb add_custom_column` — but it
   is `no_remote = True`: **direct library access only, Calibre must be closed**
   (one-time bootstrap). Setting values (`set_custom`, `set_metadata`) works in
   both modes.
5. Text extraction: `ebook-convert book.epub out.txt` (extension decides).
   Running `ebook-convert book.epub <folder-without-extension>` emits an **OEB
   folder = one HTML file per chapter** → machine-readable chapter structure for
   free. `--chapter-mark rule` inserts chapter markers in plain TXT.
   DRM'd MOBI/AZW3 fail conversion (detect + skip); scanned PDFs yield poor text.
6. **Never write `metadata.db` directly** — single-writer design, in-memory
   caches, corruption is a known painful event (`restore_database` loses custom
   settings). Read via `calibredb list --for-machine` (JSON), write via
   `calibredb` only.

---

## 3. Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Pluggable `TTSEngine` abstraction; Kokoro-82M is the default engine.** Adapters: `kokoro` (default), `piper` (low-resource fallback), `luxtts` (English books only, voice-cloning, experimental). Per-language routing table in config. | LuxTTS is EN-only research-grade (§2.1); Kokoro covers EN+DE with fixed narrator voices and pip install. The user's LuxTTS preference is honored via the adapter + config, without making the whole pipeline depend on it. |
| D2 | **Variant → format mapping: audiobook → `M4B`, summary → `MP3`**, both attached to the same book record. | Calibre stores one format per extension (§2.3 #3). Two distinct extensions = two coexisting audio variants on one book, playable in calibre & synced to devices. |
| D3 | **Python 3.11+, managed with uv.** CLI: `typer`. HTTP: `httpx`. External binaries: `calibredb`, `ebook-convert`, `ffmpeg` (subprocess, never assumed on PATH — probed at startup). | Calibre's own tooling and the TTS/LLM ecosystem are Python-native; `calibredb`/`ebook-convert` are the only safe write path (§2.3 #6). |
| D4 | **LLM access exclusively via OpenAI-compatible chat API** (works with Ollama `http://localhost:11434/v1`, llama.cpp server, LM Studio, vLLM; hosted OpenAI/Groq/… as optional last-resort fallback). Configurable **ordered model chain** with per-model `max_context_tokens`. | One client implementation covers every local runtime; the chain gives graceful degradation; explicit context config is the input to the chunker. |
| D5 | **Calibre access mode resolved at runtime:** probe Content Server URL → fall back to direct library path. Column creation is a one-time `cab bootstrap` run while Calibre is closed. | §2.3 #1/#2/#4. Exactly one writer in both modes — this is the property that keeps the library safe. |
| D6 | **Book-length strategy:** chapter-aware chunking sized from model context; audiobook preprocessing is stateless per chunk (with drift guard); summarization is map-reduce; TTS is per-sentence with a content-hash cache. | Local model context windows (often 4k–32k) cannot hold a book; each stage has a different optimal chunk granularity (see §6–§7). |
| D7 | **Idempotency & resume:** per-variant status in custom columns (source of truth Calibre-side) + local SQLite ledger (hashes, chunk-level TTS cache). Re-runs skip completed work; interrupted books resume at the failed chunk. | Cron implies unattended repeated runs; TTS hours must not be wasted. |
| D8 | **Cron-safe run loop:** single-instance `flock`, `--time-budget` (default 4 h), `--max-books` (default 3), structured logs, documented exit codes. | A 100k-word book can take hours on CPU; the daily run must stop gracefully and resume tomorrow. |
| D9 | **No DRM circumvention, ever.** DRM'd formats are detected (conversion failure) and marked `skipped_drm` with a log entry. Personal-use licensing note in README. | Legal hard line. |

---

## 4. Architecture

```
                    ┌────────────────────────────── cab run ──────────────────────────────┐
                    │                                                                     │
 calibre library    │  ┌──────────┐   ┌────────────┐   ┌───────────────────────────┐      │
 ─────────────────> │  │ scanner  │──>│  queue     │──>│ orchestrator (per book)   │      │
  calibredb (list/  │  └──────────┘   └────────────┘   │  state machine, resume    │      │
  JSON)             │        │                          └─────┬───────────┬────────┘      │
                    │        v                                v           v               │
                    │  ┌──────────┐   ┌────────────────┐  ┌────────────────────────┐      │
                    │  │ extract  │──>│  LLM pipeline  |  | variant A: audiobook   |      │
                    │  │ ebook-   │   |  - preprocess  |  |  preprocess -> TTS ->  |      │
                    │  │ convert  │   |  - summarize   |  |  mux M4B               |      │
                    │  │ txt+OEB  │   └───────┬────────┘  └────────────────────────┘      │
                    │  └──────────┘           |           ┌────────────────────────┐      │
                    │                          └────────> │ variant B: summary     │      │
                    │                                     |  map-reduce -> TTS ->  |      │
                    │                                     |  mux MP3               |      │
                    │                                     └───────────┬────────────┘      │
                    │  ┌─────────────┐   ┌─────────────┐               v                   │
                    │  | state DB    |<--| TTS cache   |<-------- ffmpeg (mux, loudnorm) │
                    │  | (sqlite)    |   | hash->wav   |   ┌────────────────────────┐    │
                    │  └─────────────┘   └─────────────┘   │ attach: add_format,    │    │
                    │                                      | set_custom status      │    │
                    │                                      └────────────────────────┘    │
                    └─────────────────────────────────────────────────────────────────────┘
```

**Components (`src/calibreaudiobridge/`)**

| Module | Responsibility |
|---|---|
| `cli.py` | `typer` app: `bootstrap`, `scan`, `run`, `generate`, `status`, `retry` |
| `config.py` | Layered config: defaults ← TOML file ← env (`CAB_*`) ← CLI flags; validated (pydantic) |
| `calibre/client.py` | `calibredb` wrapper: mode probe (server URL → local path), `list`, `add_format`, `set_custom`, `custom_columns`; subprocess + JSON, never text parsing |
| `calibre/scanner.py` | Candidate selection (see §5.3) |
| `calibre/extract.py` | `ebook-convert` → plain TXT (rule-based cleanup) + OEB folder → chapter list (titles + text) |
| `calibre/columns.py` | Bootstrap: verify/create custom columns (direct mode only) |
| `llm/client.py` | OpenAI-compatible chat client: model chain, context accounting, retries/backoff, token+cost log |
| `llm/chunking.py` | Chapter→paragraph→sentence splitter; token-budget packer (tiktoken, fallback chars/4) |
| `llm/preprocess.py` | Audiobook text optimization passes (§6.2) |
| `llm/summarize.py` | Map-reduce summarization to a target listening length (§6.3) |
| `tts/base.py` | `TTSEngine` protocol: `synthesize(text, voice, lang) -> AudioSegment`; `voices(lang)` |
| `tts/kokoro.py`, `tts/piper.py`, `tts/luxtts.py` | Adapters (D1) |
| `tts/cache.py` | Content-addressed cache: `sha256(engine, voice, params, text)` → wav path |
| `audio/mux.py` | ffmpeg: concat chapter wavs, loudness normalize, encode MP3/M4B, ffmetadata chapters, cover embed, ID3/MP4 tags |
| `pipeline/orchestrator.py` | Per-book state machine: extract → variants → attach → mark; drives resume |
| `pipeline/runner.py` | Run loop: queue, time budget, book cap, exit codes |
| `state.py` | SQLite ledger schema + access (§9.1) |
| `lock.py` | `flock` single-instance guard |

**Per-book data flow (happy path)**

1. `scan` → book is a candidate (no complete M4B-audiobook or MP3-summary, §5.3).
2. Reserve: `set_custom #cab_audiobook_status=processing` (and/or summary).
3. Extract best text source (EPUB > AZW3 > MOBI > TXT > PDF) → TXT + chapter map.
4. Variant A (audiobook): rule-based cleanup → LLM per-chunk preprocess → TTS
   per sentence (cache-checked) → per-chapter wav → mux M4B.
5. Variant B (summary): map-reduce summary (target minutes of audio) → TTS →
   mux MP3.
6. Attach: `add_format <id> book.m4b`, `add_format <id> summary.mp3`;
   `set_custom` statuses → `done` + timestamp + model/voice info.
7. Ledger updated; artifacts cleaned from staging.

---

## 5. Calibre integration design

### 5.1 Access mode resolution (every invocation)

```
probe():
  1. If config.calibre.server_url set:
       try calibredb list --with-library "<server_url>/#-"  → ok: MODE=server
  2. Else / on failure: try --with-library "<library_path>"
       - locked ("Another calibre program ... is running"):
           MODE = blocked → exit code 3 (actionable: start server or close GUI)
       - ok: MODE=local
```

All subsequent calls reuse the resolved mode. `add_custom_column` additionally
requires `MODE=local` (§2.3 #4).

### 5.2 Custom columns (created by `cab bootstrap`, one time, Calibre closed)

| Column (label → lookup name) | Type | Values / meaning |
|---|---|---|
| `cab_audiobook_status` → `#cab_audiobook_status` | enumeration | `pending`, `processing`, `done`, `failed`, `skipped_drm`, `skipped_no_text`, `skipped_manual` |
| `cab_summary_status` → `#cab_summary_status` | enumeration | same set |
| `cab_last_run` → `#cab_last_run` | datetime | last generation attempt (either variant) |
| `cab_details` → `#cab_details` | comments | JSON: engine, voices, llm models, durations, error message |

Enumeration values are fixed at bootstrap; the JSON comments column avoids
column proliferation for diagnostics.

### 5.3 Candidate selection

```
candidate(book):
  text_formats    = formats ∩ {EPUB, AZW3, MOBI, TXT, PDF}      # preference order
  has audiobook   = M4B in formats  OR #cab_audiobook_status == done
  has summary     = MP3 in formats  OR #cab_summary_status   == done
  eligible        = text_formats ≠ ∅
                    AND NOT (#*_status ∈ {skipped_*})
                    AND (NOT has audiobook OR NOT has summary)
  source_format   = first of EPUB > AZW3 > MOBI > TXT > PDF
```

- PDF-only books are eligible but flagged; extraction quality gate (§6.1) may
  mark `skipped_no_text`.
- Opt-in/opt-out: a calibre tag (configurable, default none) can restrict
  processing to tagged books (`restrict_tag` in config) — default processes all.
- `cab scan` prints the candidate table (dry-run) without touching anything.

### 5.4 Attach semantics

- `add_format <id> <file.m4b|mp3>` — **default overwrite** is intentional: a
  regeneration replaces the old artifact (idempotent end state).
- Only after successful attach do we set status `done` (attach is the
  transaction point per variant).
- Book metadata (title/author/cover) is read from calibre and embedded into the
  audio files; we never modify the book's own metadata fields other than our
  `#cab_*` columns.

---

## 6. LLM pipeline

### 6.1 Extraction & text hygiene (rule-based, deterministic — no LLM)

1. `ebook-convert source.epub staging/book.txt` (+ OEB folder for chapters).
2. Rule-based cleanup: page-number lines, repeated headers/footers, footnote
   markers `\[\d+\]`, illustration captions, excess blank lines; Unicode
   normalization (NFKC, smart quotes → spoken-friendly where safe).
3. Quality gate: extracted chars/words vs. calibre's reported book size; if
   suspiciously thin (< configurable threshold, e.g. < 200 words) → status
   `skipped_no_text`, log, continue. Detects scanned PDFs.

### 6.2 Audiobook text optimization (variant A)

Goal: same text, optimized for ears — *not* a rewrite. Two stages:

**Stage 1 — rule-based (always on, deterministic):** number/abbreviation
expansion via `num2words` (locale from book language: `en`/`de`/…), unit
normalization ("3 km" → "three kilometers"/"drei Kilometer"), URL/email
spelling-out, collapse of typographic artifacts OCR'd books bring.

**Stage 2 — LLM per-chunk pass (configurable, on by default):**

- Chunk = chapter if it fits, else sentence-packed to
  `budget = max_context_tokens − prompt_tokens − output_reserve`
  (defaults: output_reserve 2048; prompt measured at runtime).
- Prompt contract: *"Return the SAME text with only these edits: expand
  abbreviations, dates, currencies and numbers to natural spoken form; fix OCR
  artifacts and hyphenation; remove footnote/figure/sidenote references; keep
  language, meaning and length; change nothing else."* Language of the book is
  stated in the prompt (from calibre `languages` field).
- **Drift guard (per chunk):** output length must be within 0.7–1.3× input
  length and sentence count within 0.8–1.2×; otherwise retry once with stricter
  instruction, then fall back to Stage-1 text for that chunk (logged). This
  makes hallucinated rewrites impossible to ship silently.
- Chunks are independent → LLM calls run with bounded concurrency
  (`llm.concurrency`, default 2 — local models serve sequentially anyway).

### 6.3 Summarization (variant B)

Map-reduce, sized to a **target listening duration** (`summary.target_minutes`,
default 30 → ≈ 4,500 words at 150 wpm → ≈ 6k output tokens):

1. **Map:** summarize each chunk (~2–4 % of chunk length) — includes a rolling
   one-paragraph "story so far" context built from previous chunk summaries to
   maintain continuity.
2. **Reduce:** merge batched chunk summaries (batches sized to context) into
   section summaries; final pass produces the target-length summary in the
   book's language, structured as: premise → key developments → characters →
   conclusion (no spoilers suppression for now; it's a personal library tool).
3. Token math is computed before every call; if even map chunks don't fit, the
   model chain advances to the next configured model (larger context), else the
   chunker subdivides further (min chunk = 1 sentence).

### 6.4 Model chain & failure policy

```toml
[[llm.models]]
name      = "qwen2.5:14b"            # Ollama
base_url  = "http://localhost:11434/v1"
api_key   = "ollama"                  # placeholder most servers accept
max_context_tokens = 32768
role      = "primary"                 # preprocess + summarize

[[llm.models]]
name      = "gpt-4o-mini"             # optional remote last resort
base_url  = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
max_context_tokens = 128000
role      = "fallback"
```

- Context-length / rate-limit / connection errors advance to the next model in
  the chain for the *current chunk*; a model that fails N times (default 3) is
  benched for the rest of the run.
- **Degradation:** audiobook variant falls back to Stage-1-only text if all
  LLMs fail (still a valid audiobook, logged loudly). Summary variant cannot
  degrade → status `failed`, retriable via `cab retry --failed`.
- Every call logged: model, prompt/completion tokens, latency, per-run totals
  in the ledger (cost transparency for hosted fallbacks).

---

## 7. TTS layer

### 7.1 Engine abstraction

```python
class TTSEngine(Protocol):
    name: str
    def voices(self, lang: str) -> list[str]: ...
    def synthesize(self, text: str, *, voice: str, lang: str,
                   speed: float = 1.0) -> "np.ndarray":  # 24/48 kHz mono
        ...
```

Engines are loaded lazily (only the one in use is imported/model-loaded).

### 7.2 Adapters

| Adapter | Notes |
|---|---|
| `kokoro` (default) | `pip install kokoro`; voices like `af_heart` (EN), `df_...`/`ef_...` (DE); runs on CPU (CoreML/MLX where available); 24 kHz output upsampled uniformly in mux. |
| `piper` | Original [rhasspy/piper](https://github.com/rhasspy/piper) (MIT) is archived; use the maintained `piper1-gpl` fork. **Invoked as an external binary / separate process — no code linkage, so its GPL does not extend to CalibreAudioBridge.** Voice models downloaded per language; lowest CPU footprint. |
| `luxtts` | Git-install (no PyPI); **routed only to `lang=eng` books**; requires a narrator reference wav (`tts.luxtts.reference_wav` in config); MPS on macOS; wraps our chunker because upstream has none. Experimental — failures fall back to the next engine in the chain. |

**Language routing** (`tts.routing` in config):

```toml
[tts.routing]
eng = ["kokoro", "piper"]     # try kokoro, fall back to piper
deu = ["kokoro", "piper"]
default = ["kokoro", "piper", "luxtts"]
```

### 7.3 Long-text synthesis & caching

- Synthesis unit = **sentence** (split respecting quotes/abbreviations in
  en/de); sentences batched to ~300–800 chars per engine call where the engine
  benefits (Kokoro), serialized per chapter.
- Inter-sentence silence: 250 ms, paragraph 600 ms (configurable) inserted at
  mux time.
- **Cache key:** `sha256(engine ∥ voice ∥ speed ∥ lang ∥ text)` → wav in
  `state_dir/tts-cache/`. A resumed book re-synthesizes nothing; a config
  change (new voice) cleanly misses the cache.
- Retry: per-sentence retry ×3 (transient engine errors); a sentence that
  persistently fails is replaced with 1 s of silence + logged warning
  (audiobook must not die at sentence 4,302 of 9,000).

---

## 8. Audio packaging (ffmpeg)

Per chapter: sentence wavs → concat → `loudnorm` (one-pass, I/O −16 LUFS,
target consistent across chapters) → chapter file.

**Audiobook (M4B):** AAC 64 kbps mono (configurable), chapters via ffmetadata
(titles from OEB/TOC, offsets computed from chapter durations), embedded cover
(calibre cover.jpg), tags: title `"<book> (Audiobook)"`, artist=author,
album=book title, comment=`generated by CalibreAudioBridge; engine=…;
llm=…; date=…`.

**Summary (MP3):** libmp3lame 96 kbps mono, single chapter, same tags with
title `"<book> (Summary)"`, comment includes `target_minutes` and models used.

Filenames: `calibredb add_format` copies the file into the library and renames
per library convention — we only stage `<title> - <author>.{m4b,mp3` in the
work dir.

---

## 9. State, scheduling, operations

### 9.1 Ledger (SQLite, `<state_dir>/ledger.db`, default `~/.local/state/calibreaudiobridge/`)

```sql
runs      (id, started, finished, exit_code, books_done, books_failed, tokens_in, tokens_out)
jobs      (book_id, variant, status, attempts, source_hash, config_hash,
           last_chunk, started, finished, error)          -- PK(book_id, variant)
tts_cache (hash, path, bytes, created)
```

`config_hash` = hash of the pipeline-relevant config subset → changing voice or
model invalidates prior `done` markers only if the user asks
(`cab regenerate <id>`), never automatically.

### 9.2 Run loop

```
flock(state_dir/lock)                       # single instance, D8
resolve calibre mode (§5.1)                 # exit 3 if blocked
candidates = scan()
for book in candidates (ordered by last run, oldest first):
    if budget exhausted or max_books hit: break
    for variant in (audiobook, summary):
        if already done: skip               # idempotent
        process with chunk-level resume     # orchestrator
exit 0 (all done) | 1 (partial failures) | 2 (env error) | 3 (calibre blocked)
```

### 9.3 Scheduling (docs + examples)

- **cron** (as requested): `0 3 * * *  /usr/local/bin/cab run --time-budget 4h >> ~/.local/state/calibreaudiobridge/cron.log 2>&1`
- **launchd** example for macOS persistence (docs only).
- Logs: JSON-lines to stderr + rotating file; `cab status` renders ledger +
  last run summary.

---

## 10. Configuration reference (`config.example.toml`)

```toml
[calibre]
library_path = "/Users/you/Calibre Library"
server_url   = "http://localhost:8080"     # optional; probed first
restrict_tag = ""                          # only process books with this tag

[llm]
concurrency = 2
output_reserve_tokens = 2048
# models: see §6.4 — ordered chain, local first

[preprocess]
enabled = true            # LLM stage; stage-1 rules always run
drift_min_ratio = 0.7
drift_max_ratio = 1.3

[summary]
target_minutes = 30

[tts]
default_engine = "kokoro"
speed = 1.0
sentence_pause_ms = 250
paragraph_pause_ms = 600

[tts.voices]
eng = "af_heart"
deu = "df_anna"            # final names verified in M3

[tts.routing]
eng = ["kokoro", "piper"]
deu = ["kokoro", "piper"]
default = ["kokoro", "piper"]

[tts.luxtts]
reference_wav = "~/.config/calibreaudiobridge/narrator.wav"

[audio]
m4b_bitrate = "64k"
mp3_bitrate = "96k"
loudnorm_target = "-16 LUFS"

[pipeline]
time_budget = "4h"
max_books_per_run = 3
work_dir = "~/.local/state/calibreaudiobridge"
```

---

## 11. Project layout & tooling

```
CalibreAudioBridge/
├── pyproject.toml            # uv, ruff, pytest, mypy config
├── README.md  PLAN.md  LICENSE (open question Q2)
├── config.example.toml
├── src/calibreaudiobridge/   # modules per §4
├── tests/
│   ├── fixtures/library/     # 3-book CC0 calibre library (EPUB/TXT/PDF-only)
│   ├── fixtures/epubs/       # tiny EPUBs: chapters, unicode, numbers, OCR junk
│   ├── unit/                 # chunker, guards, scanner, config, state, mux args
│   └── e2e/                  # mock calibredb + mock LLM + real Kokoro-CPU
└── docs/ (scheduling, calibre-setup, engines)
```

Tooling: `uv` (lock + venv), `ruff` (lint+format), `mypy --strict` on `src/`,
`pytest`. CI (GitHub Actions): lint + unit on push; e2e job optional
(linux, CPU Kokoro).

---

## 12. Milestones & acceptance criteria

| M | Scope | Acceptance criteria (all verified by tests or recorded runs) |
|---|---|---|
| **M0 — Scaffold** | pyproject, uv, cli skeleton, config layering+validation, logging, lock, state DB | `cab --help`, `cab status`, `cab scan --dry-run` run; flock blocks a second concurrent `cab run` (test); config layering unit tests green |
| **M1 — Calibre read path** | client (mode probe, list JSON), scanner, extract (TXT + OEB chapters), column bootstrap | `cab scan` against fixture library returns exactly the expected candidates; `cab bootstrap` creates the 4 columns on a scratch library (calibre closed); extraction returns chapters for fixture EPUB; unit tests with a fake `calibredb` script |
| **M2 — LLM core** | client (chain, retries, accounting), chunker, preprocess + drift guard, map-reduce summary | Unit tests with a mock OpenAI server: chunker respects token budget ±5 % and never splits mid-sentence unless forced; drift guard rejects a 2× rewrite; summary of fixture book hits target length ±30 %; optional live-Ollama integration test (skipped if absent) |
| **M3 — TTS layer** | `TTSEngine`, kokoro + piper adapters, sentence chunking, cache | Fixture paragraph synthesized on CPU by both engines; cache: second run 0 engine calls (asserted); contract test every adapter must pass; per-sentence failure → silence substitution (test) |
| **M4 — Audio + attach** | mux (m4b chapters/cover/tags, mp3), add_format, set_custom, attach transaction | E2E on fixture library (mock LLM, real Kokoro): M4B with correct chapter count/timings and MP3 visible in `calibredb list`; statuses `done`; kill-before-attach leaves statuses `processing`, ledger allows resume |
| **M5 — Orchestrator + ops** | run loop, budget, max-books, resume mid-book, exit codes, retry, cron/launchd docs | Interrupted book resumes at recorded chunk (test with seeded ledger); time-budget honored (fake clock); second full run processes 0 new work; exit codes per §9.2 (test) |
| **M6 — LuxTTS + polish** | luxtts adapter (EN routing, reference voice), config.example.toml, README quickstart, `v0.1.0` tag | LuxTTS adapter passes contract test with reference wav (skipped gracefully if repo unavailable); docs complete; fresh-machine quickstart verified on the author's Mac |

Dependency order: M0 → M1 → M2 → M3 → M4 → M5 → M6. M2 and M3 are independent
and can be built in parallel after M1.

---

## 13. Testing strategy

- **Unit (fast, no network):** chunker (token budgets, sentence/paragraph/chapter
  boundaries, CJK-safe basics), drift guard, scanner filter matrix, config
  layering/invalid-config errors, ledger state machine, ffmpeg arg construction,
  cache keys.
- **Contract tests:** one suite every `TTSEngine` adapter must pass (voices,
  synthesize, silence-on-failure) against a stub model — catches adapter drift.
- **Integration:** fake `calibredb` bash script (recorded JSON responses) so the
  Calibre path is testable without Calibre; mock OpenAI-compatible HTTP server
  (pytest + httpx mock transport) with fault injection (500s, truncation,
  context-limit errors).
- **E2E:** fixture library + mock LLM + real Kokoro on CPU; asserts artifacts,
  chapters, statuses, idempotency (second run = no-op).
- **Fixtures:** 3 tiny CC0/self-written EPUBs (multi-chapter; one with heavy
  numbers/abbreviations; one deliberately thin PDF for the quality gate).

---

## 14. Risks & mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| LuxTTS immaturity (no releases, EN-only, unmaintained) | audiobook generation fails for EN books | Adapter isolated behind protocol; chain fallback to Kokoro/Piper (D1); contract tests; clearly labeled experimental |
| Local LLM context too small / slow | preprocessing takes very long | chunker sizes to configured context; concurrency; degradation to rule-based Stage 1; model chain with bigger-context fallback |
| LLM rewrites instead of edits (hallucinated audiobook text) | corrupted book text | strict prompt contract + length/sentence drift guard + fallback to deterministic text (§6.2) |
| Long-book runtime on CPU (hours) | cron overlap, heat | chunk cache, resume, time budget, max-books, flock (D7/D8) |
| Calibre library corruption | user data loss (unacceptable) | writes only via `calibredb`; single-writer modes (D5); no direct sqlite; recommend library backup before first production run (documented) |
| DRM / scanned PDFs | wasted cycles, broken audio | conversion-failure detection → `skipped_drm`; text quality gate → `skipped_no_text` |
| Format-per-extension limit in calibre | two variants collide | fixed by D2 mapping (M4B + MP3) |
| macOS sleep kills overnight run | partial work | resume makes this harmless (documented); launchd `StartCalendarInterval` alternative documented |
| Non-commercial model licenses (e.g. XTTS) | legal exposure | Only permissively-licensed models in scope (§2.2); Piper's GPL applies to the engine binary only — invoked as a separate process, never linked (§7.2) |

---

## 15. Out of scope for v1

GUI/web interface, calibre plugin packaging, multi-narrator/dialogue voicing,
podcast/RSS feeds, auto language detection (calibre `languages` field is
trusted), distributed/multi-host processing, DRM circumvention (permanent
exclusion), ebook text *rewriting* beyond listenability edits.

---

## 16. Open questions (need your call before/at M0)

| # | Question | Default if no answer |
|---|---|---|
| Q1 | Given LuxTTS is English-only & research-grade (§2.1): keep **Kokoro as default** with LuxTTS as EN-only option — OK? | Yes (D1) |
| Q2 | Repo license? | none shipped until answered (candidates: MIT / Apache-2.0) |
| Q3 | Default summary length 30 min — OK? | Yes |
| Q4 | Process **all** eligible books, or only books tagged e.g. `tts`? | all (`restrict_tag` empty) |
| Q5 | Do you need German (or other) narration? | Config ships EN+DE voices; routing table covers both |
