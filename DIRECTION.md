# Ninja Image Hub — Direction v0.4

## Self-authored brief, grounded in the existing project
- Audience: one self-hosting operator managing multiple image-producing accounts, queues, assets, and an AI agent.
- Core daily job: identify backlog/failed work, adjust safe capacity, recover accounts, and download images.
- Tone: precise / calm / operational. Do not pretend sample throughput is real.
- Existing contract: preserve `/api/*`, `/v1/*`, login, accounts, job history, gallery and SQLite migration; no destructive DB reset.

## Audit of the v0.3 interface
Keep: account lifecycle, image studio, jobs, gallery, two-factor concurrency model, private downloads, strong dark theme and green accent. Improve: operational controls hidden in `.env`, insufficient time-range analytics, no per-account dispatch pause, no measured RAM, generic decorative overview, absence of settings audit. Never hide provider limitations or represent a manual plan label as verified.

## Three directions evaluated
1. **Operations cockpit** (chosen): compact dark slate control plane with green used for ready/healthy, typography based on Space Grotesk + DM Sans, real data as the visual richness, status vocabulary and two-column control forms. Works with the existing stack and does not manufacture artwork.
2. **White editorial ledger** (not chosen): lighter, strongly typographic, but excessively low contrast for long monitoring sessions and inconsistent with current recognizable brand.
3. **High-gloss 3D signal room** (not chosen): visual spectacle adds loading and rendering cost and distracts from failure triage and data precision.

These are design concepts, not three fully implemented UI variants. The shipped direction is the Operations cockpit.

## Tokens and interaction
- Ground `#080f1c`, navigation `#0a1424`, surface `#0e1b2d`, hairline `#21334b`, text `#e9f0fc`, secondary `#8ba0b9`, primary green `#b9f85c`.
- 8–16px border radii; 12–14px UI copy, 20–32px data, strong mono-like tabular figures.
- Navigation first, work first. Analytics uses stored output counts instead of fake sparkline statistics.
- Motion: small hover feedback; bar height transition when selecting analytics period; no autoplay decorative visuals. `prefers-reduced-motion` disables transitions.
- Keyboard: native buttons/forms, focus-visible, no div-only pseudo-controls. Destructive actions require confirmation.
- Real data never replaced by demo content in the server; demo screenshots are labeled and generated in an isolated offline browser fixture.

## Supplied skill usage
- `web-designer`: application focus, compact tables, design tokens, responsive/reduced-motion rules, avoidance of generic KPI-only dashboard, capability states.
- `app-designer`: contrast and composition references for dense mobile settings, content-first labels and states.
- `motion-primitives-main`: audited the upstream React components and MIT license. React TSX components are **not directly mounted into this existing vanilla-JS frontend**; a forced conversion would increase bundle, dependencies and risk. We retained the same interaction goals using a few native CSS transitions, not copied motion-primitives components. To actually adopt those components, first migrate the frontend to React with a separate verified regression suite.
- Icons: remote linked assets from the Lucide open-source library; no project-authored SVG path. Unicode fallback works when the CDN is offline. CDN version and availability are external dependencies.

## Responsive check
- Offline Chromium mock content at 1440×950 and 390×844; no `pageerror` and no document overflow at 390px. Does not validate real gateway login or live API flows in Chromium.
