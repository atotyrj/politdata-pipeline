# GitHub Releases як сховище поколінь PolitData

## Контракт

Одне валідоване покоління PolitData відповідає одному GitHub Release з тегом
`politdata-data-<generation_id>`. Публікація має дві фази:

1. adapter перевіряє `generation_manifest.json` і всі оголошені SHA-256;
2. створює draft release та завантажує всі assets;
3. звіряє SHA-256, повернутий GitHub для кожного asset;
4. лише після окремого `publish_latest` додає покажчик покоління, прибирає
   `draft` і позначає release як latest.

Збій на будь-якому кроці завантаження видаляє незавершений draft release. Уже
активне покоління при цьому не змінюється.

## Склад release

- `generation_manifest.json` — manifest та checksums артефактів;
- `generation_bundle_index.json` — перелік ZIP-частин, їх SHA-256 і файлів;
- `generation-<section>-NNNN.zip` — restorable RAW, interim, processed та
  outputs, згруповані за верхнім каталогом;
- `generation_pointer.json` — незмінний покажчик на конкретне покоління;
- `public_artifacts.json` — каталог прямих посилань лише на Excel;
- кожний `outputs/*.xlsx` — окремий asset для зручного завантаження.

RAW та службовий state не отримують окремих публічних посилань у каталозі.
Водночас вони входять у checksum-verified ZIP-частини, тому чистий runner може
відновити повний state для наступного incremental запуску.

## Обмеження і захист

Adapter використовує безпечний робочий поріг 1.9 GB на один asset при
абсолютному ліміті GitHub 2 GiB. ZIP створюються без стиснення: це не економить
місце, зате не потребує великих додаткових CPU/RAM і робить розбиття
передбачуваним. Якщо один окремий файл перевищує поріг, запуск завершується
явною помилкою. Файл не обрізається і покоління не стає latest.

Відновлення повторно перевіряє:

- digest кожного завантаженого release asset;
- digest ZIP-частини з bundle index;
- безпечність шляхів усередині ZIP;
- checksum кожного артефакту з generation manifest;
- відповідність `generation_id` і hash manifest покажчику latest.

## CLI

У всіх generation-командах локальний backend лишається default. Для GitHub
додаються однакові параметри:

```text
--generation-store github-releases
--github-repository atotyrj/politdata-pipeline
--github-target-commitish main
```

Команди `restore`, `retention` і `rollback` працюють через той самий storage
contract. Retention не може видалити latest generation. Rollback перевіряє
цільове покоління та використовує compare-and-swap guard
`--expected-current`, перш ніж змінити latest release.

## Автентифікація

Adapter читає токен лише з `GITHUB_TOKEN`. Токен не передається прапорцем і не
записується у manifests або логи.

У GitHub Actions слід використовувати автоматичний `${{ github.token }}` з
мінімальним дозволом workflow:

```yaml
permissions:
  contents: write
```

Додавати personal access token до Secrets для публікації у цьому самому
репозиторії не потрібно. CI workflow лишається з `contents: read`; право запису
отримає тільки майбутній окремий workflow оновлення даних.

## Ручний rehearsal

Workflow **GitHub Releases storage rehearsal** запускається лише вручну через
`workflow_dispatch`. Він:

1. створює крихітне синтетичне покоління з RAW, interim, processed та валідним
   Excel fixture;
2. публікує його як draft через справжній GitHub API;
3. завантажує ZIP-assets назад на чистий runner;
4. перевіряє всі checksums і наявність окремого Excel asset;
5. ніколи не викликає `publish_latest`.

Параметр `delete_after_verification` за замовчуванням має значення `false`, тому
draft залишається для ручного огляду. Значення `true` явно дозволяє видалити
синтетичний release і tag після успішної перевірки.

## Щотижневе оновлення

Workflow **PolitData weekly incremental update** запускається щопонеділка о
03:37 у часовому поясі `Europe/Kyiv` (із автоматичним урахуванням літнього часу),
а також може бути запущений вручну. Він серіалізований тим самим writer-lock
group, що й rehearsal, і виконує такий цикл:

1. відновлює checksum-verified latest generation;
2. лімітовано перевіряє картки організацій, списки звітів і нові report details;
3. запускає changed-only normalization, references та enrichment;
4. при фактичних змінах повторно генерує 18 аналітичних Excel і запускає QA;
5. створює новий immutable release та перемикає latest лише після успіху.

Якщо змін немає, новий release не створюється. Якщо будь-який етап завершується
помилкою, попередній latest release лишається активним і придатним до rollback.

Початкове production-покоління можна скласти з уже завантажених локальних даних
через `scripts/assemble_existing_baseline.py`, без повторного RAW ingestion, а
потім явно опублікувати `scripts/publish_existing_generation.py`.
