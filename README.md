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

> **Status: planning.** No code yet — the full design and milestone plan lives in
> [PLAN.md](PLAN.md). See §16 there for open questions before implementation starts.

## Safety principles

- Writes to your library go **only** through `calibredb` (never direct sqlite).
- Works whether Calibre is open (via its Content Server) or closed.
- Idempotent and resumable: interrupted runs continue at the exact chunk; completed
  books are never re-processed.
- No DRM circumvention — protected books are detected and skipped.

## Personal-use note

Generated audio is for personal listening of books you own. Don't distribute the
output; book and model licenses apply.
