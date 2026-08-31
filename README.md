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
whether a previous change set is still marked `running`.

The first online command is intentionally narrow and requires a hard limit:

```powershell
politdata ingest --organization-limit 5 --json
```

It fetches at most five candidate organization cards, saves a factual
change-set, then runs the existing changed-only downstream pipeline. Use
`--skip-downstream` to stop after the change-set. It does not yet refresh report
lists or fetch new report details: that will be enabled only after the report
selection policy is moved out of the exploratory notebook into a tested module.

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
