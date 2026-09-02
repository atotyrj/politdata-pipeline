# PolitData: режими імпорту, трансформації та публікації

Дата плану: 2026-09-02.

## Мета

Один і той самий перевірений pipeline має підтримувати три способи запуску:

1. повне завантаження з повною логічною заміною RAW та всіх похідних даних;
2. ручне інкрементальне оновлення;
3. автоматичне інкрементальне оновлення у GitHub Actions.

Ручний та автоматичний інкрементальні режими не повинні мати окремої
бізнес-логіки. GitHub Actions лише відновлює останній успішний стан, запускає ту
саму CLI-команду, публікує результат і зберігає новий стан.

## Обов'язкові принципи

- Кожний запуск має унікальний `run_id` і працює у власному staging-просторі.
- Поточний валідований набір не змінюється, доки новий не пройшов усі QA.
- Публікація виконується атомарним перемиканням покажчика `latest`, а не
  очищенням робочих каталогів на початку запуску.
- Час останнього запуску не є єдиним критерієм новизни. Порівнюються стабільні
  ідентифікатори, склад набору та семантичні хеші вмісту. Це дозволяє знаходити
  запізнілі публікації, виправлення і вилучення.
- Report discovery має власний графік обходу організацій і не залежить від того,
  чи змінилася картка організації. Інакше новий звіт незміненої організації буде
  пропущено.
- Чинна policy вибору звітів, ручні analytical overrides та перенесення окремих
  payment-записів між аналітичними таблицями застосовуються в усіх режимах.
- Excel є похідним публічним продуктом, а не джерелом стану pipeline.
- Повний режим ніколи не запускається за розкладом без окремого ручного
  підтвердження.

## Спільний контракт запуску

Цільовий CLI-інтерфейс:

```text
politdata run --mode full-replace --confirm-full-replace
politdata run --mode incremental
```

Автоматичний режим викликає другу команду. Додаткові параметри (`--limit`,
`--resume`, `--publish`, `--dry-run`) змінюють обсяг або спосіб виконання, але не
семантику обробки даних.

Кожний успішний запуск створює manifest покоління щонайменше з такими полями:

```text
schema_version
generation_id
run_id
mode
started_at_utc
completed_at_utc
source_watermark
organization_state_hash
report_discovery_state_hash
report_detail_state_hash
selected_reports_manifest_hash
code_revision
row_counts
artifact_checksums
artifact_locations
status
```

`latest.json` містить лише посилання на останнє успішне незмінне покоління.
Невдалий або перерваний запуск не має права змінити цей покажчик.

## Режим 1: повне завантаження і заміна

Призначення: створення нового baseline, відновлення після несумісної зміни
схеми або контрольна повна звірка.

Послідовність:

1. Preflight перевіряє доступність API, вільне місце, версію схеми та відсутність
   іншого активного writer-а.
2. У `runs/<run_id>/` завантажується повний перелік організацій, усі їх картки,
   усі списки звітів і всі потрібні report details.
3. З нуля будуються manifests та застосовується чинна policy вибору звітів.
   Валідні ручні overrides переносяться; невалідні потрапляють у окремий QA-звіт.
4. З нуля виконуються normalization, references, enrichment та аналітичні
   Excel-вивантаження.
5. Перевіряються схеми, ключі, кількість рядків, суми, дублікати, зв'язки,
   coverage і Excel-пакет.
6. RAW і processed частини одного `generation_id` публікуються разом. Лише після
   цього `latest.json` атомарно перемикається на нове покоління.
7. Попереднє успішне покоління зберігається для rollback згідно з retention
   policy; staging невдалого запуску позначається як failed і не публікується.

Таким чином «повна заміна» означає заміну активного покоління, а не небезпечне
видалення чинних даних до завершення нового завантаження.

## Режим 2: ручне інкрементальне оновлення

Призначення: запуск оператором після перевірки preflight або для контрольного
оновлення поза розкладом.

Послідовність:

1. Відновити останній успішний generation manifest і ingestion checkpoints.
2. Оновити discovery організацій; картки завантажувати для нових, змінених та
   планово перевірюваних організацій.
3. Незалежно від змін карток обійти due-чергу report discovery. Один прохід може
   бути лімітованим, але черга має зберігатися і рівномірно покривати всі
   організації.
4. Порівняти report IDs і хеші списків. Завантажити details лише для нових,
   змінених, раніше невдалих або явно прострочених кандидатів.
5. Атомарно оновити report manifests, повторно застосувати policy вибору і
   зберегти валідні ручні overrides.
6. Сформувати factual change-set з inserts, updates і deletions.
7. Передати change-set у вже наявний changed-only ланцюжок: normalization,
   fragment promotion, dependency planning, references, enrichment і QA.
8. Якщо фактичних змін немає, не переписувати normalized/enriched datasets та
   Excel. Зберегти лише журнал успішної перевірки і checkpoints.
9. Якщо зміни є, створити нове незмінне покоління, перевірити його й атомарно
   оновити `latest.json`.

Кожний крок ідемпотентний: повторний запуск після збою або завершує незакінчену
стадію, або підтверджує той самий результат без дублювання даних.

## Режим 3: автоматичне інкрементальне оновлення

Призначення: регулярне оновлення без участі оператора.

GitHub Actions workflow:

1. Запускається за `schedule` та вручну через `workflow_dispatch`.
2. Використовує `concurrency` з одним writer-ом, щоб два запуски не публікували
   стан одночасно.
3. Встановлює зафіксовані залежності, запускає unit-тести та read-only preflight.
4. Завантажує останній generation manifest і checkpoints зі сховища стану.
5. Викликає `politdata run --mode incremental --publish`.
6. Завантажує нове покоління у versioned storage, перевіряє checksums і після
   цього атомарно оновлює `latest.json`.
7. Публікує машинний run summary: кількість нових/змінених/вилучених
   організацій і звітів, QA-результати, посилання на дані та Excel.
8. У разі помилки залишає попереднє покоління активним і надсилає повідомлення.

Scheduled workflow не містить власної логіки трансформації та не запускає
`full-replace`. Окремий workflow для повного оновлення має використовувати GitHub
Environment із ручним approval.

## Розміщення в GitHub

У GitHub-репозиторії зберігаються:

- код, конфігурація, схеми, тести та документація;
- малі тестові fixtures;
- workflows;
- versioned metadata контрактів, але не робочі секрети.

Великі `data/raw`, `data/interim`, `data/processed` і `outputs` уже виключені з
Git. Їх не слід додавати до історії репозиторію. Ephemeral filesystem GitHub
runner-а також не може бути єдиним місцем збереження checkpoints.

Рекомендована схема публікації:

- versioned object storage зберігає RAW, processed, checkpoints і manifests;
- GitHub Releases на першому етапі може публікувати перевірені Excel та компактні
  CSV/Parquet для користувачів;
- `latest.json` дає стабільні посилання на актуальне покоління;
- GitHub Pages надалі може показувати каталог наборів, дати оновлення, checksums
  і посилання для завантаження;
- GitHub Actions artifacts використовуються лише для діагностики запусків, а не
  як постійне канонічне сховище.

Секрети доступу зберігаються у GitHub Secrets або, краще, замінюються короткими
OIDC-обліковими даними. У логи не виводяться токени чи повні приватні payloads.

## Поточний стан

У репозиторії вже є change-set, resumable changed-only downstream, атомарне
оновлення manifests, збереження analytical overrides, checkpoints report
discovery/detail, preflight і лімітований online ingestion.

Report discovery вже відокремлено від змін карток організацій. Persisted
due-черга повторно планує успішні перевірки, надає пріоритет новим організаціям,
зберігає retry/backoff для помилок і дозволяє незалежно обмежувати розмір batch.
Інкрементальна збірка manifest читає лише snapshots організацій, успішно
перевірених у поточному запуску; семантично незмінні manifests не переписуються.

Єдиний full-replace runner уже підключено до CLI: він у відокремленому staging
завантажує повний RAW, перебудовує manifests, normalization, references,
enrichment, QA та Excel-пакет, а promotion виконується лише після успіху. Поки
немає конкретного remote state/publish backend-а. Git remote налаштовано на
приватний `atotyrj/politdata-pipeline`; базовий `.github/workflows/ci.yml`
успішно запускає offline tests на чистому GitHub runner.

Канонічні Arrow-схеми всіх normalized datasets збережено як versioned package
contract `normalized_v1.json`. Тому чистий runner може створити валідний
нульовий `properties/paper` без доступу до попереднього Parquet baseline.
Materializer також атомарно приводить фрагменти до цих схем і зупиняється при
появі невідомого API-поля замість його тихого видалення.

## План реалізації

### Етап 1. Уніфікований orchestrator і контракти

- [x] Додати `RunMode`, `RunConfig`, `GenerationManifest` і одну точку входу
  `run_pipeline`.
- [x] Відокремити immutable generations і `latest` від orchestrator через
  `GenerationStore`; локальні шляхи реалізовано окремим filesystem adapter-ом.
- [x] Додати блокування writer-а, run journal і атомарний `latest` pointer.
- [x] Додати ізольований full-replace staging та QA-gated promotion.
- [x] Покрити поточний контракт unit-тестами без мережі.
- Додати resume для незавершеного full-replace покоління.

Критерій готовності: обидва режими запускаються через один CLI та створюють
однаковий manifest покоління.

### Етап 2. Повний staged rebuild

- [x] Реалізувати lifecycle `full-replace` лише у новому staging-каталозі.
- [x] Підключити повний RAW-to-normalized-to-enriched stage runner до цього
  lifecycle.
- [x] Винести normalized Arrow-схеми, включно з дозволеним порожнім `paper`, у
  versioned package contract для запуску на чистій машині.
- [x] Додати мінімальний offline full-replace fixture, який із підміненими лише
  API-відповідями проходить справжні normalization, materialization,
  references, enrichment, QA та всі 18 Excel exports.
- [x] Провести offline fixture через orchestrator promotion: перевірити
  generation manifest, checksums, атомарний `latest.json` і навмисний збій
  після повної трансформації без зміни активного покоління.
- Додати повну QA-матрицю та перевірку узгодженості RAW/processed одного
  покоління.
- [x] Реалізувати promotion без destructive in-place cleanup та операторську
  checksum-verified команду rollback з compare-and-swap захистом `latest`.

Критерій готовності: штучний збій на будь-якій стадії не змінює активний набір.

### Етап 3. Завершення ручного incremental

- [x] Роз'єднати organization-card refresh і report discovery.
- [x] Додати persisted due-чергу для всіх організацій, backoff і retry.
- [x] Не сканувати всі RAW snapshots під час одного incremental batch.
- [x] Не переписувати report manifests, якщо змінився лише час перевірки.
- Виявляти не лише нові IDs, а й виправлення/вилучення за manifests і хешами.
- Підключити готовий changed-only downstream та умовну регенерацію Excel.

Критерій готовності: новий звіт організації з незміненою карткою виявляється,
no-change run не переписує дані, повторний запуск не створює дублів.

### Етап 4. Зберігання і публікація

- [x] Реалізувати storage protocol і локальний filesystem adapter; remote
  backend підключатиметься через той самий контракт після вибору провайдера.
- [x] Публікувати локальні immutable generations, checksums і `latest.json`.
- [x] Додати checksum-verified `politdata restore` для конкретного або
  актуального покоління без залежності від локальної історії запусків.
- [x] Додати read-only retention preview, hash-bound apply з обов'язковим
  expected-current guard та захистом активного покоління.
- [x] Додати атомарний rollback лише на повністю checksum-verified generation.
- [x] Додати публічний каталог `processed/` і `outputs/`, який не розкриває RAW,
  interim або абсолютні локальні шляхи.
- Узгодити остаточну кількість поколінь для production retention після вибору
  remote backend-а; локальний безпечний default — три.

Критерій готовності: нова машина може відновити state і виконати incremental,
не маючи локальної історії попередніх запусків.

### Етап 5. GitHub Actions

- [x] Push/PR workflow: інсталяція на чистому Python 3.11 runner та всі offline
  tests без online ingestion; перший run успішний.
- Додати lint/format checks після вибору відповідних інструментів.
- Scheduled workflow: incremental з concurrency, cache, timeout і summary.
- Manual full workflow: environment approval та обов'язковий dry-run/preflight.
- Release/Page publication і повідомлення про помилки.

Критерій готовності: два послідовні автоматичні запуски, другий без змін,
завершуються успішно; публічне `latest` лишається доступним.

### Етап 6. Контрольний запуск і експлуатаційний runbook

- Виконати малий end-to-end incremental rehearsal.
- Виконати контрольний full-replace на окремому target без публікації.
- Перевірити rollback, повторний запуск після навмисного збою та відновлення
  state на чистому runner-і.
- Зафіксувати runbook, частоту запусків і процедуру реагування на помилки.

## Рішення, для яких згодом потрібна участь власника

Реалізацію локальних етапів можна продовжувати без додаткових пакетів. Перед
автоматичною публікацією поколінь потрібно буде:

1. [x] створити GitHub-репозиторій, додати `origin` і перевірити перший CI run;
2. обрати постійне сховище стану/даних (рекомендовано object storage; GitHub
   Releases можна використати як початковий канал для Excel);
3. визначити бажаний розклад автоматичного incremental та retention;
4. надати GitHub Environment/Secrets або OIDC-настройки для обраного сховища.

Ці рішення не блокують завершення orchestrator-а, full-replace та локального
incremental runner-а.
