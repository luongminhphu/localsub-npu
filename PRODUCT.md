# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One person: the owner, on their own Windows 11 laptop with an Intel Core Ultra NPU (Intel AI Boost) and
Arc iGPU. They make subtitles for foreign-language films, series and TV shows (Japanese, Korean, Chinese,
English) to watch them in Vietnamese, and review and fix the result in the app before keeping it.

## Product Purpose

LocalSub NPU turns a video or audio file into timed subtitles and, optionally, a translation into
Vietnamese or English, entirely on the laptop. Success: a whole episode (or a queue of episodes)
processed unattended into subtitles that are readable at viewing speed, need little manual fixing, and
never leave the machine.

## Positioning

Fully offline, NPU-first subtitling: Whisper speech recognition and a local Qwen translation model run
through OpenVINO on the NPU, falling back to GPU/CPU with the reason recorded. No cloud service, account
or API key once the models are downloaded; media never leaves the machine.

## Operating Context

- Windows 11 desktop app (Tauri 2 shell, React UI in WebView2) driving a Python/OpenVINO worker; runs
  long jobs (model load, recognition, translation) while the user does other things, often a queue of
  several files.
- Typical loop: choose one or more videos, pick spoken language, recognition and translation models
  and devices, generate, then review cues in the editor (timings, source text, translation), split,
  merge or delete lines, and export SRT/VTT/TXT plus a `.localsub.json` project.
- Side views the owner uses to tune the machine: live CPU/GPU/NPU load, job history with per-stage
  timings, an AI analysis tab with a read-only MCP server for AI assistants, and a model manager.
- Interface language Vietnamese by default, English available.

## Capabilities and Constraints

- Spoken languages: auto-detect, English, Japanese, Korean, Chinese, Vietnamese. Translation targets:
  Vietnamese and English only.
- Models are pre-converted OpenVINO IR from a single catalog (`apps/inference/app/catalog.py`):
  Whisper variants for recognition, Qwen 2.5/3/3.5 INT4 for translation; some run on GPU/CPU only.
- Cue rules follow subtitle reading limits per script (Latin, CJK, Hangul); timings are never
  rewritten by translation.
- Must work fully offline after model download; the UI may not load remote assets (CSP `'self'`).
- Personal tool: no distribution planned beyond the owner's machine, so no onboarding, licensing,
  accounts or telemetry are needed. The Windows installer exists for the owner's own installs.

## Brand Commitments

- Name: LocalSub NPU.
- Plain, factual Vietnamese copy that states numbers, devices and reasons (e.g. why a job fell back from
  NPU to GPU) rather than marketing language.

## Evidence on Hand

- Real outputs live only on the owner's machine (`%LOCALAPPDATA%\com.luongminhphu.localsubnpu\output`);
  the repository holds sample/preview data only (`apps/desktop/src/lib/worker.ts` preview worker).
- No testimonials, users, benchmarks or reviews exist beyond the owner's own job history; do not
  invent any.

## Product Principles

1. Privacy is the product: nothing about the media, subtitles or usage leaves the machine.
2. Unattended by default: long jobs and queues finish without supervision, recover from device
   failures, and never lose finished work or unsaved edits.
3. Show the machine's truth: which device ran each stage, how long it took, and why it fell back.
4. Subtitles must be watchable, not just transcribed: reading speed, line length and faithful,
   natural translation matter more than raw speed.
5. One owner, one laptop: optimize for the owner's hardware and workflow over generality.
