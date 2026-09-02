# PolitData Pipeline

Local development project for downloading, cleaning, enriching, transforming, validating and analyzing data from the NACP PolitData registry.

## Structure

- `notebooks/exploration/` - API and data exploration
- `notebooks/development/` - transformation experiments
- `src/politdata/` - stable reusable Python code
- `tests/` - automated tests
- `config/` - configuration
- `data/raw/` - raw downloaded data
- `data/interim/` - intermediate data
- `data/processed/` - processed data
- `logs/` - local logs

## Safe incremental operation

The project deliberately separates online RAW ingestion from downstream
processing. The command-line interface below never contacts the source API and
never starts a RAW scan.

Install the local package once in the activated virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
```

Before any future online run, inspect only the local readiness state:

```powershell
politdata preflight --json
```

`preflight` makes zero network requests and writes nothing. It reports whether
the committed organization baseline and ingestion state files are readable, and
whether a previous change set is still marked `running`. It also reports how
many organization report-list checks are currently due.

The first online command is intentionally narrow and requires a hard limit:

```powershell
politdata ingest --organization-limit 5 --json
```

It fetches at most five candidate organization cards, saves a factual
change-set, then runs the existing changed-only downstream pipeline. Use
`--skip-downstream` to stop after the change-set. Without `--report-limit` it
does not refresh report lists or fetch report details.

To also refresh report lists and download a bounded number of newly selected
report details, opt in explicitly:

```powershell
politdata ingest --organization-limit 5 --report-limit 10 --json
```

Report-list discovery has an independent persisted due queue. It therefore
checks organizations whose cards did not change and cannot miss a newly
published report merely because organization metadata stayed the same. By
default its batch limit equals `--organization-limit`; set it independently when
needed:

```powershell
politdata ingest --organization-limit 5 --report-discovery-limit 25 --report-limit 10 --json
```

Successful report-list checks are scheduled again after seven days by default.
Use `--report-refresh-interval-days` to change that interval. Failed checks use
a persisted exponential retry delay. Incremental manifest assembly reads only
the snapshots successfully refreshed in the current batch.

## Unified run lifecycle

The shared orchestrator adds a single-writer lock, atomic run journal and common
configuration for manual and future scheduled operation. Its incremental mode
delegates to the same bounded ingestion pipeline:

```powershell
politdata run --mode incremental --organization-limit 5 --report-discovery-limit 25 --report-limit 10 --json
```

Inspect a full-replace plan without network requests or writes:

```powershell
politdata run --mode full-replace --dry-run --json
```

The full-replace lifecycle already provides isolated staging, an explicit
confirmation guard, QA-gated immutable generation promotion and an atomic
`latest.json` pointer. The command intentionally refuses an actual full run
until the complete RAW-to-output stage runner is connected; it must never
publish a partial rebuild.

After a committed ingestion run has created a change set, inspect it first:

```powershell
politdata status --change-set data/interim/change_sets/current.json --json
```

Run (or safely resume) only its changed-only downstream work:

```powershell
politdata downstream --change-set data/interim/change_sets/current.json --json
```

`downstream` skips a change set with no organization or report changes. For a
partially completed change set it resumes pending or failed stages, but refuses
an ambiguous stage marked `running`; reconcile that state before retrying.

Do not replace `current.json` merely to test the command. Use a separate
validation path for an isolated no-change control run. GitHub scheduling and
public publication remain a later deployment step, after online ingestion is
put behind an explicit, reviewed command.

The implementation roadmap for full replacement, manual incremental updates,
scheduled GitHub updates, persistent state and public artifacts is documented in
[`docs/ingestion_modes_and_github_plan.md`](docs/ingestion_modes_and_github_plan.md).
