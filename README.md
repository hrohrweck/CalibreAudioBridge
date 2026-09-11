# CalibreAudioBridge

Turn your Calibre library into a listening library — automatically.

CalibreAudioBridge is a scheduled job that scans a [Calibre](https://calibre-ebook.com)
library for books without audio variants and generates **two audio versions per book**
using open-source TTS and configurable (local-first) LLMs:

1. **Audiobook** (M4B) — full text, cleaned and optimized for listening by an LLM
   (spoken-form numbers/abbreviations, OCR fixes, footnote stripping), then
   synthesized chapter-by-chapter with chapters, cover art and metadata.
2. **Audio summary** (MP3) — an LLM-generated summary (map-reduce over chunks,
   sized for local model context windows), synthesized as a single file.

Both are attached to the *same* book record in Calibre as new formats, with
processing status tracked in custom columns. Fully local by default:
[Ollama](https://ollama.com)/llama.cpp/LM Studio for the LLM passes and
[Kokoro](https://huggingface.co/hexgrad/Kokoro-82M)/[Piper](https://github.com/rhasspy/piper)/[LuxTTS](https://github.com/ysharma3501/LuxTTS)
for narration.

## Quickstart

### 1 — Prerequisites

```
brew install calibre ffmpeg         # macOS (calibre >= 6.x)
```

Verify the tools are on PATH:
```
calibredb --version
ebook-convert --version
ffmpeg -version
```

You also need a local LLM server. The simplest option is [Ollama](https://ollama.com):
```
brew install ollama
ollama pull qwen2.5:14b
```

### 2 — Install CalibreAudioBridge

```
pip install git+https://github.com/hrohrweck/CalibreAudioBridge.git
# or: clone + uv sync --extra kokoro
```

Install the default TTS engine (Kokoro, EN + DE):
```
pip install "calibreaudiobridge[kokoro]"
```

### 3 — Configure

```
mkdir -p ~/.config/calibreaudiobridge
cp config.example.toml ~/.config/calibreaudiobridge/config.toml
```

Edit the three mandatory fields:
```toml
[calibre]
library_path = "/Users/you/Calibre Library"

[[llm.models]]
name = "qwen2.5:14b"
base_url = "http://localhost:11434/v1"
api_key = "ollama"
max_context_tokens = 32768
```

### 4 — Bootstrap custom columns (once, with Calibre closed)

```
cab bootstrap
```

### 5 — Dry run

```
cab scan             # see which books would be processed (read-only)
```

### 6 — Generate

```
cab run              # process up to 3 books (default) within the 4h budget
cab generate 42      # force-generate variants for book id 42
cab status           # show last runs, job states, calibre access mode
cab retry            # requeue failed jobs
```

### Scheduling (cron)

```
# Run nightly at 03:00
0 3 * * *  cab run --config ~/.config/calibreaudiobridge/config.toml >> ~/Library/Logs/cab.log 2>&1
```

See [`docs/scheduling.md`](docs/scheduling.md) for launchd and advanced options.

## Safety principles

- Writes to your library go **only** through `calibredb` (never direct sqlite).
- Works whether Calibre is open (via its Content Server) or closed.
- Idempotent and resumable: interrupted runs restart at the failed book; completed
  books are never re-processed.
- No DRM circumvention — protected books are detected and skipped.

## Documentation

| | |
|---|---|
| [docs/calibre-setup.md](docs/calibre-setup.md) | Access modes, column bootstrap, variant mapping |
| [docs/scheduling.md](docs/scheduling.md) | cron + launchd, exit codes |
| [docs/engines.md](docs/engines.md) | Kokoro, Piper, LuxTTS — install & config |
| [PLAN.md](PLAN.md) | Full architecture and implementation decisions |
| [config.example.toml](config.example.toml) | Annotated configuration reference |

## Personal-use note

Generated audio is for personal listening of books you own. Don't distribute the
output; book and model licenses apply.
