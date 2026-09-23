# FraudGraph Investigator — investigation console (`ui/`)

A static React/TypeScript console that lets a judge inspect what the backend
agent concluded for each benchmark case: verdict, evidence, retrieved memory,
tool provenance, initial vs. final actions, the SAR, validation, and whether the
case was written to TigerGraph and read back.

## Architecture and the static-data boundary

```
backend repo (this repository)                      ui/
  cases/HHG-001.json … HHG-020.json      ──┐
  runs/latest/batch_summary.json           ├─ npm run sync-data ─►  public/data/cases/*.json
  runs/latest/traces/HHG-xxx.trace.json  ──┘   (validate + copy)     public/data/traces/*.trace.json
                                                                     public/data/batch_summary.json
                                                                     public/data/data-manifest.json
                                                                            │
                                                         vite build ───────►  dist/ (static site)
```

- The browser **only** fetches files under `public/data/`. It never calls
  TigerGraph, an LLM, or any backend service, and it needs no credentials.
- The console does not compute fraud outcomes, policy decisions, SAR filing,
  affected episodes or graph-write success. It validates and displays what the
  backend wrote, and shows missing or invalid data as diagnostics.
- Runtime validation uses Zod contracts in `src/contracts/`. The same contracts
  are bundled on the fly (esbuild) by `scripts/sync-data.mjs`, so sync time and
  browser checks can never drift apart.

### Data modes

| Mode | When | What the viewer sees |
|---|---|---|
| `official` | All 20 answer files, the batch summary and every final write/validation field are present and valid | No demo banner; a slim "Official backend results" strip |
| `fixtures` | `npm run sync-data -- --fixtures` | A permanent striped **DEMO DATA** banner; every fixture ID starts with `DEMO-` |
| `partial` | Some official files missing or invalid | A red **PARTIAL DATA** banner with expandable diagnostics; only what passed is presented as trustworthy |

The browser re-validates everything it loads. If the manifest says `official`
but a file fails in the browser, the page drops to `partial`. Any `DEMO-` case
ID or `DEMO-` run ID always forces the fixture banner.

## Prerequisites

- Node.js 20.11+ (tested with Node 22) and npm 10+.

## Install and develop

```bash
cd ui
npm ci
npm run sync-data -- --fixtures   # or an official sync, see below
npm run dev                       # http://localhost:5173
```

## Synchronizing data

```bash
npm run sync-data -- --fixtures                 # interface fixtures from ui/fixtures/
npm run sync-data                               # official, non-strict: partial data allowed
npm run sync-data -- --strict                   # official, must be complete and valid
npm run sync-data -- --strict --require-traces  # traces become mandatory too
npm run sync-data -- --source ../other-checkout # read backend artifacts from another repo root
npm run sync-data -- --reference-evidence path/to/evidence_package_HHG-019.json
```

- Runs from `ui/`. The default source is the repository root (`..`).
- Before copying, it deletes the old `public/data/cases/`, `public/data/traces/`
  and `batch_summary.json`, so stale or duplicate files never survive a resync.
  It writes nothing outside `public/data/`, and it copies the original bytes
  without changing any source file.
- Only files named `<CASE-ID>.json` / `<CASE-ID>.trace.json` are accepted.
  Official syncs accept `HHG-` IDs; fixture syncs accept `DEMO-` IDs.
- Checks JSON syntax, the answer, trace and summary contracts, filename vs.
  `case_id`, duplicate case IDs, summary totals vs. rows, summary rows vs.
  answers, and answer vs. trace (write state, graph case ID, final probability,
  retrieved vs. cited prior cases, evidence after the cutoff).
- **Strict mode** exits 1, leaving `public/data/` untouched, unless all of these
  hold: HHG-001…HHG-020 are present and contract-valid,
  `runs/latest/batch_summary.json` is present and consistent, every answer has
  `written_to_graph: true`, every trace that exists reports
  `graph_write.read_back_ok: true` and `validation.passed: true`, and no batch
  row reports `validation_passed: false`. Missing traces are warnings unless
  `--require-traces` is set.
- With no backend `cases/` at all, the non-fixture sync exits 1 and changes nothing.
- `--reference-evidence` compares the matching answer and trace with an
  evidence package (for example Aryan's Gate 2 HHG-019 package): entity IDs
  it never observed, prior cases it never retrieved, a different candidate
  episode or exposure, and trigger mismatches. The package is used as a
  reference only. It is never displayed as an answer or a trace.

`npm run validate-data` runs the same checks as a dry run, without copying
anything. By default it checks `public/data/`; pass `--source <root>` to check a
backend checkout instead.

### Replacing fixtures with the final backend drop

1. The backend writes `cases/HHG-001.json … HHG-020.json`,
   `runs/latest/batch_summary.json` and, ideally,
   `runs/latest/traces/HHG-xxx.trace.json`.
2. `cd ui && npm run build:official`, which runs a strict sync and then the
   production build. Fix whatever it reports in the backend; never edit the
   copies in `public/data/`.
3. Commit the updated `ui/public/data/` if you deploy from committed files
   (Vercel does), or run the Pages workflow with `data: official`.

No frontend code changes are required when the real files arrive.

## Data locations and contracts

| File | Contract | Notes |
|---|---|---|
| `public/data/cases/<id>.json` | `src/contracts/answer.ts` | The frozen challenge schema. Strict: unknown keys, unknown enum values and numeric IDs are contract errors. A lenient projection still renders invalid files so their diagnostics can be shown. |
| `public/data/traces/<id>.trace.json` | `src/contracts/trace.ts` | A presentation projection of real execution. Unknown step names, node types, roles and `via` values render generically and are reported. |
| `public/data/batch_summary.json` | `src/contracts/summary.ts` | Run-level totals. If absent, the Overview derives a clearly labeled temporary view from the answers and leaves backend validation and trigger metadata unknown. |
| `public/data/data-manifest.json` | `src/contracts/manifest.ts` | Written only by the sync script: mode, counts, errors, warnings, and file lists. |

Semantic answer rules checked in the browser (diagnostic only): probability
in [0, 1]; a legitimate verdict has no affected transactions, zero exposure and
no SAR; `first_suspicious_txn_id` is in `affected_txn_ids`;
`written_to_graph: true` requires `graph_case_id`; `sar.file` agrees with
`FILE_REPORT`; a filed SAR has subjects, a positive amount, two ordered dates and
a narrative; with no evidence request, initial = final and `what_changed` is
`nothing`. Warnings cover sentence counts and the README approval-route table.

### Missing trace behavior

A case without a trace still renders every answer-backed tab: header,
Evidence (answer items and cited prior cases), Actions, SAR and Raw JSON. The
Investigation timeline, probability chart, signals, retrieval scores, rule
provenance and Graph tab each say that the trace was not provided, or that it
failed validation. Nothing is reconstructed from the answer file. Graph memory
is shown as **"Written · read-back unverified"** because only a trace can
report the read-back.

Graph-memory states: `verified` (answer written and the trace read-back succeeded),
`readback_failed`, `written_unverified`, `not_written` (red alert), and
`conflict` (answer and trace disagree).

## Screens

- `/#/`: Overview with the KPI strip, verdict and pattern distribution, run
  provenance, grouped diagnostics, and a sortable case board with search and
  filters. Rows link to the case.
- `/#/case/:caseId`: the case header (IDs, trigger, verdict, probability,
  pattern, status, exposure, SAR, validation, memory, graph case ID,
  previous/next) and six tabs, **Investigation · Evidence · Graph · Actions ·
  SAR · Raw JSON**. The URL keeps the tab and selected node
  (`?tab=graph&node=<id>`), so links are shareable.
- `/#/how-it-works`: pipeline, challenge checklist, honesty rules, technology,
  team, and dataset facts (from the challenge README, labeled as such).
- Unknown routes and case IDs show a not-found page with a link back.

The SAR tab renders a filed report as a formal document. **Print / save PDF**
uses print CSS that shows only the report.

## Test, typecheck, lint, build

```bash
npm run lint            # ESLint, zero warnings allowed
npm run typecheck       # tsc strict, noUncheckedIndexedAccess
npm test -- --run       # Vitest + React Testing Library + Node tests for the sync pipeline
npm run build           # tsc -b && vite build of the currently synchronized data
npm run build:official  # strict sync, then build
npm run preview         # serve dist/ at http://localhost:4173
npm audit --omit=dev
```

## Deployment

The build uses `base: "./"` and `HashRouter`, so the same `dist/` works at a
domain root and under a subpath, and deep links such as
`/#/case/HHG-019?tab=graph` work without server rewrites.

**Vercel:** import the repository, set **Root Directory** to `ui`, and keep
the defaults from `ui/vercel.json` (`npm ci`, `npm run build`, output `dist`).
Vercel builds whatever is committed in `ui/public/data/`.

**GitHub Pages:** enable Pages with source "GitHub Actions", then run the
`ui-pages` workflow (`.github/workflows/ui-pages.yml`) by hand. Choose
`committed` to publish the committed data, or `official` to run a strict sync
in CI first (the build fails unless the official artifacts are complete).

## Interface fixtures

`ui/fixtures/` is generated by `npm run fixtures:generate`. It holds six
invented cases (DEMO-001…DEMO-006) covering: fraud with an evidence request,
changed actions and a filed SAR; legitimate with zero exposure; uncertain with
analyst escalation and no trace; an undocumented pattern with a failed graph
write and unknown trace vocabulary; card testing with a ~230-node graph and a
failed read-back; and a valid answer with a deliberately malformed trace. They
are not investigation outcomes. Never copy them into the backend's `cases/` or
`runs/` directories.

## Known limitations

- The trace format is defined by this frontend's contract. Until the backend
  emits `runs/latest/traces/*.trace.json`, official cases render without the
  timeline, signals, retrieval scores or graph.
- A "referenced" policy document is matched by name: an evidence `ref` has to
  contain the document's anchor (for example `R6`).
- Browser validation mirrors the backend contract but does not replace the
  backend validator. Dataset-dependent rules (IDs exist, exposure recomputes,
  prior cases closed before the anchor) run only in the backend.
