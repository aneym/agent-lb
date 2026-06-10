# DESIGN.md — agent-lb dashboard

Visual contract for the dashboard SPA (`frontend/`). Register: **product** (tool UI; design serves the task). Theme strategy: **strict black & white monochrome** — chroma 0 everywhere, both themes. Meaning is carried by shape, fill, weight, and position, never hue.

## Theme

- Light and dark, toggled by `.dark` class (existing `useThemeStore`).
- No color anywhere in the UI: no blue primary, no green/orange/red meters, no purple charts. The only pigment on screen is ink.
- Scene: a proxy cockpit glanced at on a second monitor during agent runs; both ambient-bright (light) and late-night (dark) use are real, so both themes stay first-class.

## Colors (OKLCH, chroma 0 only)

Light theme (`:root`):

| Token                      | Value                                             | Role                                                               |
| -------------------------- | ------------------------------------------------- | ------------------------------------------------------------------ |
| `--background`             | `oklch(0.985 0 0)`                                | App canvas                                                         |
| `--card` / `--popover`     | `oklch(1 0 0)`                                    | Surfaces                                                           |
| `--foreground`             | `oklch(0.155 0 0)`                                | Ink / body text                                                    |
| `--muted`                  | `oklch(0.955 0 0)`                                | Quiet fills, meter tracks                                          |
| `--muted-foreground`       | `oklch(0.46 0 0)`                                 | Secondary text (≥4.5:1 on white)                                   |
| `--border` / `--input`     | `oklch(0.90 0 0)`                                 | Hairlines                                                          |
| `--primary`                | `oklch(0.155 0 0)`                                | Buttons, selection (ink)                                           |
| `--primary-foreground`     | `oklch(0.985 0 0)`                                | Text on primary                                                    |
| `--secondary` / `--accent` | `oklch(0.955 0 0)` / `oklch(0.94 0 0)`            | Secondary buttons, hover fills                                     |
| `--destructive`            | `oklch(0.155 0 0)`                                | Ink; danger is carried by copy, icon, and confirm dialog — not red |
| `--ring`                   | `oklch(0.155 0 0)`                                | Focus                                                              |
| `--chart-1..5`             | `0.20 / 0.42 / 0.58 / 0.72 / 0.85` (all chroma 0) | Grayscale data ramp                                                |

Dark theme (`.dark`): background `oklch(0.145 0 0)`, card `oklch(0.185 0 0)`, foreground `oklch(0.97 0 0)`, muted `oklch(0.24 0 0)`, muted-foreground `oklch(0.72 0 0)`, border/input `oklch(0.28 0 0)`, primary `oklch(0.97 0 0)` with foreground `oklch(0.145 0 0)`, chart ramp inverted `0.95 / 0.78 / 0.62 / 0.48 / 0.35`.

Donut/stacked chart segments get a 2px `--card` stroke so adjacent grays separate. Area-chart fills use ink at 6–10% alpha; lines are ink (or the grayscale ramp when series must be distinguished). When more than 3 series share a chart, differentiate with dash patterns as well as gray steps.

## Typography

- **UI family:** Geist Sans (existing self-hosted variable font). One family for headings, labels, body, buttons. No new fonts.
- **Data family:** JetBrains Mono for numerals, ids, money, logs, timestamps — always with `font-variant-numeric: tabular-nums`.
- Fixed rem scale, ratio ~1.2: 12 / 13 / 14 (base) / 16 / 20 / 24 / 30. Page titles 24, section heads 16/semibold, card labels 13/medium, data 13–14 mono.
- No letter-spacing tricks, no uppercase-tracked eyebrows on every section, no display sizes above 30px inside the app shell.

## Status vocabulary (monochrome)

Status = icon (lucide) + text label, both ink. Never color-coded.

| Status          | Icon                             | Treatment                    |
| --------------- | -------------------------------- | ---------------------------- |
| active          | filled circle (`Circle` w/ fill) | regular weight               |
| paused          | `CirclePause` (outline)          | muted-foreground             |
| rate_limited    | `Clock`                          | regular + label              |
| quota_exceeded  | `TriangleAlert`                  | semibold label               |
| reauth_required | `KeyRound`                       | semibold label               |
| deactivated     | `CircleOff`                      | muted-foreground, row dimmed |

Meters (quota windows): 4px track in `--muted`, fill in ink. Below 20% remaining the percentage turns semibold and gains a `TriangleAlert`; the bar itself stays ink.

## Components

- shadcn/ui primitives (existing `components/ui/`); restyle via tokens, do not fork variants per screen.
- Buttons: primary = ink fill; secondary = outline; destructive = outline + trash icon + confirm dialog (no red fill).
- Badges: outline style, ink text; reserve filled badges for the active selection only.
- Tables/lists: hairline row separators, generous row height (≥40px), tabular numerals right-aligned.
- Cards: single level only. Sections inside a card are separated by hairlines + section headers, never nested cards.
- Skeletons for loading; empty states explain the next action.

## Layout

- Existing app shell: top nav header + footer status bar. Content max-width ~1400px, 24px gutters.
- Accounts: master list (≥360px, full emails visible) + detail pane. Filter toolbar above the list: search, provider segmented control, status select, sort select, group-by select. Filters sync to URL params.
- Spacing scale: 4 / 8 / 12 / 16 / 24 / 32. Vary rhythm between sections (24–32) vs within (8–12).

## Motion

- 150–200 ms ease-out transitions on state changes (hover, selection, expand). No entrance choreography, no bounce.
- `prefers-reduced-motion: reduce` ⇒ crossfade or instant.
