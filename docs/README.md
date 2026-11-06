# Documentation index

Current documentation for Ninja Image Hub. Historical versioned documents were removed so this folder describes **the current state**, not old releases.

## Getting started

| Document | Contents |
|---|---|
| [../README.md](../README.md) | Project overview, screenshots, multi-account model, full option and API reference |
| [../START_HERE_FA.md](../START_HERE_FA.md) | Persian quickstart: install order, doctor, gateway, first account, first image, troubleshooting table |
| [FIRST_RUN_FA.md](FIRST_RUN_FA.md) | Deep first-run: doctor checks, gateway + browser install, manual ChatGPT login, first image, backup |
| [LOGIN_FA.md](LOGIN_FA.md) | **Signing in to ChatGPT — three methods** (remote page, visible window, automatic login) with screenshots and troubleshooting |

## Operating the system

| Document | Contents |
|---|---|
| [OPERATIONS_FA.md](OPERATIONS_FA.md) | Capacity and sizing (including the 10-account recipe), failure modes, on-demand workers, `/api/health`, forbidden operations |
| [DEPLOY_LINUX_FA.md](DEPLOY_LINUX_FA.md) | Linux server deployment: systemd, one Xvfb per worker, SSH tunnel, headless-server login, reuse in other automations |

## How it works

| Document | Contents |
|---|---|
| [ARCHITECTURE_FA.md](ARCHITECTURE_FA.md) | Module layout, `ctx` composition, data flow, route map |
| [FILE_PIPELINE_FA.md](FILE_PIPELINE_FA.md) | Reception, atomic storage, previews, ZIP export, size budgets |
| [ANALYTICS_FA.md](ANALYTICS_FA.md) | What each metric means, measured vs projected numbers |

## Evidence

| File | Contents |
|---|---|
| [TRANSFER_BENCHMARK.json](TRANSFER_BENCHMARK.json) | Storage-stage memory benchmark used by the file-pipeline note |
| [../REPORT_FA.md](../REPORT_FA.md) | Current release report: what was broken, what was fixed, test evidence, known gaps |
| [images/](images/) | Screenshots used by the README (real dashboard, gallery, operations panel) |

## Development

| Document | Contents |
|---|---|
| [../AGENTS.md](../AGENTS.md) | Development contract: invariants, dispatch/file/security rules, required checks |
| [../MANIFEST_SHA256.json](../MANIFEST_SHA256.json) | SHA-256 seal of every tracked file (excludes `.env`, `data/`, `vendor/`) |
