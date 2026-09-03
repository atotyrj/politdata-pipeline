# PolitData Pipeline

[![CI](https://github.com/atotyrj/politdata-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/atotyrj/politdata-pipeline/actions/workflows/ci.yml)

Local development project for downloading, cleaning, enriching, transforming, validating and analyzing data from the NACP PolitData registry.

## Завантажити актуальні таблиці Excel

Посилання нижче завжди ведуть на відповідний файл у
[`Latest Release`](https://github.com/atotyrj/politdata-pipeline/releases/latest).
Після успішного щотижневого оновлення вони автоматично відкриватимуть нову
версію файлу без ручної зміни README.

| Файл | Вміст |
|---|---|
| [01_party_information.xlsx](https://github.com/atotyrj/politdata-pipeline/releases/latest/download/01_party_information.xlsx) | Відомості про партію або осередок за кожним відібраним звітом: адреса, керівник, сайт і працівники. |
| [02_properties__intangible.xlsx](https://github.com/atotyrj/politdata-pipeline/releases/latest/download/02_properties__intangible.xlsx) | Нематеріальні активи та інше майно партій і осередків. |
| [03_properties__movable.xlsx](https://github.com/atotyrj/politdata-pipeline/releases/latest/download/03_properties__movable.xlsx) | Рухоме майно, крім транспортних засобів. |
| [04_properties__property_moneys.xlsx](https://github.com/atotyrj/politdata-pipeline/releases/latest/download/04_properties__property_moneys.xlsx) | Грошові активи, залишки та рух коштів, відображені у майновій частині звітів. |
| [05_properties__realty.xlsx](https://github.com/atotyrj/politdata-pipeline/releases/latest/download/05_properties__realty.xlsx) | Об'єкти нерухомості партій і осередків. |
| [06_properties__paper.xlsx](https://github.com/atotyrj/politdata-pipeline/releases/latest/download/06_properties__paper.xlsx) | Цінні папери, зазначені у відібраних звітах. |
| [07_properties__transport.xlsx](https://github.com/atotyrj/politdata-pipeline/releases/latest/download/07_properties__transport.xlsx) | Транспортні засоби партій і осередків. |
| [08_payments__other_contributions.xlsx](https://github.com/atotyrj/politdata-pipeline/releases/latest/download/08_payments__other_contributions.xlsx) | Негрошові внески майном, роботами або послугами. |
| [09_payments__monetary_contributions.xlsx](https://github.com/atotyrj/politdata-pipeline/releases/latest/download/09_payments__monetary_contributions.xlsx) | Приватні грошові внески фізичних та юридичних осіб. |
| [10_payments__state_funding.xlsx](https://github.com/atotyrj/politdata-pipeline/releases/latest/download/10_payments__state_funding.xlsx) | Надходження коштів державного фінансування. |
| [11_payments__other_incomes.xlsx](https://github.com/atotyrj/politdata-pipeline/releases/latest/download/11_payments__other_incomes.xlsx) | Інші надходження, які не належать до внесків або державного фінансування. |
| [12_payments__budget_expenses.xlsx](https://github.com/atotyrj/politdata-pipeline/releases/latest/download/12_payments__budget_expenses.xlsx) | Витрати коштів державного фінансування. |
| [13_payments__outgoing_expenses.xlsx](https://github.com/atotyrj/politdata-pipeline/releases/latest/download/13_payments__outgoing_expenses.xlsx) | Інші вихідні платежі та витрати приватних коштів, включно з аналітично перекласифікованими операціями. |
| [14_payments__return_expenses.xlsx](https://github.com/atotyrj/politdata-pipeline/releases/latest/download/14_payments__return_expenses.xlsx) | Повернення помилкових або інших надходжень. |
| [15_payments__transfer_expenses.xlsx](https://github.com/atotyrj/politdata-pipeline/releases/latest/download/15_payments__transfer_expenses.xlsx) | Перекази коштів між рахунками та внутрішньопартійні трансфери цього типу. |
| [16_obligations__obligations.xlsx](https://github.com/atotyrj/politdata-pipeline/releases/latest/download/16_obligations__obligations.xlsx) | Фінансові зобов'язання партій і осередків. |
| [17_organizations__reporting_history.xlsx](https://github.com/atotyrj/politdata-pipeline/releases/latest/download/17_organizations__reporting_history.xlsx) | Матриця наявності відібраних звітів за організаціями та звітними періодами. |
| [18_organizations__report_name_history.xlsx](https://github.com/atotyrj/politdata-pipeline/releases/latest/download/18_organizations__report_name_history.xlsx) | Хронологія назв організації у послідовних звітах і позначки їх зміни. |

Повний список поколінь і службові архіви для відновлення доступні у розділі
[`Releases`](https://github.com/atotyrj/politdata-pipeline/releases).

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

The full-replace lifecycle provides isolated staging, an explicit confirmation
guard, a complete RAW-to-output stage runner, QA-gated immutable generation
promotion and an atomic `latest.json` pointer. It never publishes a partial
rebuild.

## GitHub Releases generation storage

PolitData can use GitHub Releases as the first persistent generation backend,
without committing RAW or generated workbooks to Git history. One immutable
generation becomes one release. It is created as a draft, receives
checksum-verified ZIP parts plus individually downloadable Excel workbooks, and
is published as `latest` only after the pipeline QA gate passes.

The token is read only from `GITHUB_TOKEN`; it is never accepted as a command
line argument. On GitHub Actions the built-in job token can be used with
`contents: write`, so no personal access token needs to be copied into the
repository. For a local authenticated rehearsal, set the variable only in the
current terminal and do not save it in project files.

Publish a fully validated full-replace generation to Releases:

```powershell
politdata run --mode full-replace --confirm-full-replace --publish `
  --generation-store github-releases `
  --github-repository atotyrj/politdata-pipeline --json
```

Restore and verify the active generation on a clean machine:

```powershell
politdata restore --latest --destination data/restored/latest `
  --generation-store github-releases `
  --github-repository atotyrj/politdata-pipeline --json
```

RAW, interim, processed and output files remain inside restorable generation
archives. Only `outputs/*.xlsx` are additionally exposed as direct release
downloads and listed in `public_artifacts.json`. An individual source file that
cannot fit safely below GitHub's per-asset limit aborts publication instead of
being truncated. See
[`docs/github_releases_storage.md`](docs/github_releases_storage.md) for the
asset contract, rollback and operational safeguards.

Before enabling real data publication, the manual **GitHub Releases storage
rehearsal** workflow can exercise the actual GitHub API using a tiny synthetic
generation. It never contacts the NACP API and never changes the latest release.
By default it preserves the resulting draft for inspection; deletion is an
explicit workflow input.

## Generation maintenance

Preview which immutable generations fall outside the default three-generation
retention window. Preview is read-only:

```powershell
politdata retention --keep-latest 3 --json
```

Applying that exact policy requires the current generation ID shown by the
preview. This compare-and-swap guard prevents a stale cleanup from deleting a
generation after another run has changed `latest.json`:

```powershell
politdata retention --keep-latest 3 --expected-current GENERATION_ID --apply --json
```

Rollback never edits data in place. It checksum-verifies the selected immutable
generation and then atomically changes only `latest.json`:

```powershell
politdata rollback --generation-id TARGET_ID --expected-current CURRENT_ID --json
```

Build the public artifact catalog for all retained generations:

```powershell
politdata catalog --json
```

The default catalog includes only `processed/` analytical datasets and
`outputs/` workbooks. It excludes RAW, interim state and absolute local paths.
No retention or rollback command performs online ingestion.

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
validation path for an isolated no-change control run. GitHub CI runs the
offline test suite after every push and pull request. The production workflow
runs a bounded incremental update every Monday at 03:37 Europe/Kyiv and
publishes a new release only when source changes pass QA.

The implementation roadmap for full replacement, manual incremental updates,
scheduled GitHub updates, persistent state and public artifacts is documented in
[`docs/ingestion_modes_and_github_plan.md`](docs/ingestion_modes_and_github_plan.md).
