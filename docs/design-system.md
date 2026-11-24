# Desktop design system (0.15.0)

The desktop UI was redesigned with the **UI/UX Pro Max** skill, vendored in
`.claude/skills/ui-ux-pro-max` (from `uipro-cli` v2.2.3, MIT; see its `LICENSE.md`). Claude Code
loads it automatically in this repository. Query it from the repo root, for example:

```bash
python3 .claude/skills/ui-ux-pro-max/scripts/search.py "productivity dashboard desktop tool dark mode" --design-system -p "LocalSub NPU"
python3 .claude/skills/ui-ux-pro-max/scripts/search.py "progress feedback long task" --domain ux
```

## What was taken from the skill, and what was not

| Skill output | Used | Notes |
|---|---|---|
| Colors: *Developer Tool / IDE* (slate dark) + *Productivity Tool* (teal focus, orange action) | Yes | Teal for selection and primary actions, orange only for the one main action (Generate subtitles) |
| Typography: Fira Sans / Fira Code (dashboard pairing) | Yes | Bundled with `@fontsource` (offline, CSP `'self'`), Latin + Vietnamese subsets only |
| Style: micro-interactions (150–200 ms) | Yes | Hover/press/switch transitions; disabled under `prefers-reduced-motion` |
| UX rules: progress indicators, keyboard order, `role=alert` errors, visible labels, 4.5:1 contrast, reduced motion | Yes | See checklist below |
| Pattern (landing-page section layouts, e.g. "Video-First Hero") | No | The skill's patterns are for marketing pages; a desktop tool needs an app shell |
| Google Fonts `@import` | No | The app runs offline; fonts are bundled |

## Tokens

All colours are CSS custom properties on `:root` in `apps/desktop/src/styles.css`; components never
use raw hex values. Dark is the default; light applies with `prefers-color-scheme: light` or
`<html data-theme="light">` (the rail's System / Light / Dark switch, saved in `localStorage`).

| Token | Dark | Light | Use |
|---|---|---|---|
| `--bg` | `#0b1220` | `#f0fdfa` | Window background |
| `--surface` / `--surface-2` / `--surface-3` | `#111a2c` / `#17233a` / `#1e2b45` | `#ffffff` / `#f4f8fb` / `#e8eef5` | Cards, inputs, hover |
| `--text` / `--text-muted` / `--text-faint` | `#f1f5f9` / `#9fb0c8` / `#8193ad` | `#0f172a` / `#475569` / `#5b6b80` | Body / hints / labels |
| `--primary` / `--on-primary` | `#14b8a6` / `#042f2e` | `#0f766e` / `#ffffff` | Selection, primary buttons, focus |
| `--cta` / `--on-cta` | `#f97316` / `#1c0a00` | same | Generate subtitles only |
| `--success` / `--warning` / `--danger` / `--info` | light tints | dark shades | Status text and soft backgrounds |
| `--series-cpu` / `--series-gpu` / `--series-npu` | blue / amber / green | darker variants | Load chart lines and swatches |

Measured contrast (WCAG): every text/background pair is at least 4.5:1 in both themes (lowest:
light `--warning` on white 4.9:1, dark `--text-faint` on `--surface-2` 5.0:1); primary button 5.5–5.8:1,
CTA 6.9:1, danger button 4.8:1.

Spacing uses a 4 px scale (`--space-1` … `--space-5` = 4, 8, 12, 16, 24 px); radii 6/10/14 px.
Root font size is 14 px (dense desktop tool); the smallest rendered text is 12 px.

## Layout

- **Rail** (216 px, icon-only under 900 px, top bar under 600 px): brand, vertical `tablist`
  (↑/↓, ←/→, Home/End), theme and language switches, environment chip.
- **Top bar**: view title and one-line purpose, job stepper (icons for pending/running/done/failed,
  progress and ETA), the single orange CTA or Cancel. Result, error and log banners sit under it so
  progress is visible from every view.
- **Run view** (0.17.0): Media (with the queue), Configure and Runtime cards in an auto-fit grid. A
  disabled Generate button shows its first unmet condition beside it; the top bar shows `File n/N`
  while a queue runs, and a finished queue gets a per-file summary banner.
- **Subtitles view**: the cue editor at full width with Undo/Redo (snapshot stack, Ctrl+Z / Ctrl+Y;
  one step per typing run), an issues-only filter, a cue jump box and Alt+↑/↓ between cues. Start/end
  times stack in one column, text boxes grow to their content (`field-sizing: content`), and
  split/merge/delete are labelled icon buttons that announce how to undo them.
- Changes from the first Impeccable critique (24/40): light `--bg` is neutral `#f3f5f8`, light
  `--success`/`--warning` darkened to `#166534`/`#854d0e` so status pills reach 5.7–6.3:1, and analysis
  findings dropped the 4 px side accent (severity stays on the labelled pill).

## Pre-delivery checklist (from the skill)

- [x] No emoji as icons: SVG icons from `lucide-react` (pinned 1.66.0), `aria-hidden`, with text or `aria-label`
- [x] Pointer cursor and 150–200 ms hover/press feedback on every control
- [x] Visible focus ring (`--focus`) on buttons, tabs, inputs, chips and switches; skip link to main content
- [x] Labels above inputs; errors use `role="alert"` next to the field
- [x] Contrast ≥ 4.5:1 in light and dark; status never shown by colour alone (icon or text too)
- [x] `prefers-reduced-motion` disables transitions and spinners
- [x] No horizontal page scroll at 560, 860 and 1280 px wide

## Impeccable skill

`.claude/skills/impeccable` is [Impeccable](https://impeccable.style) (pbakaus/impeccable, tag
`skill-v4.8.2`, Apache-2.0; see its `LICENSE` and `NOTICE.md`), copied from the repo's `.claude/skills`
build. In Claude Code run `/impeccable` (for example `/impeccable audit apps/desktop/src/App.tsx` or
`/impeccable polish`). On first use its launcher downloads the native engine from the project's GitHub
releases and checks it against `scripts/engine-checksums.txt`; the binary lands in the gitignored
`scripts/bin/` (or `~/.impeccable`). The per-edit design-detector hooks are **not** enabled: turn them
on per machine with `/impeccable hooks on` (writes `.claude/settings.local.json`), or skip them and
let the skill run one detector pass at the end of a session.

## Second critique (26/40) changes, 0.18.0

- The Subtitles editor is the home view; Run (Media/queue, Configure, Runtime) is a right-hand drawer
  of it (top-bar toggle, Ctrl+B, Esc), open while there is nothing to edit and closed when editing
  starts. The Monitor tab is now "Hiệu năng / Performance" so it no longer clashes with "Tải về".
- Queue items keep the settings they were queued with; the top bar adds a remaining-time estimate;
  a finished queue updates the window title, raises a Web Notification when allowed, and its
  summary (`role=status`) and History rows offer "Mở để sửa". Result banners show only on the
  editor and can be dismissed.
- Polish: Runtime key/values in one column, zero-count severity pills without opacity (outlined,
  muted text), editor header names the file, jump label no longer wraps.

## Free refinement pass, 0.19.0

- Reading speed per cue: the editor shows `duration · chars/s` under each cue's times and warns
  (`too_fast`, a filterable warning) above 17 cps Latin, 12 Hangul, 9 Chinese, 7 Japanese
  (`maxCpsFor` / `readingSpeed` in `lib/cues.ts`).
- The stepper is a segmented track (3 px bar per step: primary running, success done, danger failed)
  and is hidden while idle; the empty editor shows a three-step guide.
- Switch help folds into a `details` disclosure; thin themed scrollbars, primary caret, tabular
  numbers in tables; light `--bg` `#e9edf2` so white cards separate; `h2` 16 px; the environment chip
  is plain text.

## Third critique (28/40) changes, 0.22.0

- Overnight queue: with files queued, Cancel is a menu (skip this file / stop after this file /
  stop everything); the top bar keeps position and remaining time in non-shrinking spans and only
  truncates the file name; the queue list shows the running file with its settings; Performance shows
  the files still queued.
- Device truth: the queue summary says "Speech NPU · Translation GPU" and shows a fallback as a
  warning line with its reason; History shows the reason under the device cell, readable error text
  with a link to Models for model errors, and an RTF legend ("NĐ" and "chạy chồng" are spelled out).
- Hierarchy: once the file in the editor was just generated, Generate drops to secondary so Export
  leads; the empty state's "open project" shows only while the drawer (which has its own) is closed;
  the drawer toggle sits beside the title so the job bar no longer moves it.
- Polish: MCP client picker shows its selection (generic card button rule no longer overrides it),
  load tiles are hairline-separated columns instead of cards in a card, danger button colours are
  tokens (`--on-danger`, `--danger-solid-hover`), the stepper wraps under 600 px (no horizontal
  scroll at 390 px), queue remove buttons sit beside their file.
