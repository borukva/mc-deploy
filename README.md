# mc-deploy

Реліз у мод-репі → джар автоматично на Pterodactyl-сервері.

Деплой чіпляється до події **«GitHub Release опубліковано»** (як саме реп робить
релізи — байдуже) і деплоїть **асет релізу байт-у-байт**, без перезбирання:
аплоад нового джара → видалення старого джара цього мода → рестарт сервера →
очікування чистого буту в лозі (~3 хв). Немає чистого буту — джоба червона,
сервер лишається як є до ручного втручання.

Дизайн і ухвалені рішення: `docs/design.md`.

## Підключити свій реп (2 кроки)

**1.** Додай у свій реп файл `.github/workflows/deploy.yml`:

```yaml
name: Deploy
on:
  release:
    types: [published]
  workflow_dispatch:
    inputs:
      tag:
        description: "Тег релізу (порожньо = останній)"
        required: false

jobs:
  deploy:
    uses: borukva/mc-deploy/.github/workflows/deploy.yml@v1
    with:
      targets: |
        мій-мод-*.jar -> аліас-сервера
      tag: ${{ inputs.tag || github.event.release.tag_name }}
    secrets:
      PTERO_URL: ${{ secrets.PTERO_URL }}
      PTERO_TOKEN: ${{ secrets.PTERO_TOKEN }}
```

Опціональний інпут `boot_ok` — маркер чистого буту в лозі (дефолт `Done (`;
напр. abyss ставить `Abyss initialized`).

**2.** Якщо твого сервера ще немає в [`servers.json`](servers.json) — додай
рядок `"аліас": "<короткий id з URL панелі>"` PR-ом.

Секрети приїдуть самі: cron щодня, або одразу — **Actions → Sync secrets →
Run workflow** у цьому репі. Наявність `deploy.yml` у репі і є фактом
«підключений» — жодного реєстру.

Якщо реп ще не робить релізів — скопіюй `release.yml` з
[mindbattle](https://github.com/Borukva/mindbattle) (тег `v*` → збірка →
реліз із джаром).

## Правила `targets`

- Рядок = `<глоб асета> -> <аліас сервера>`. Коментарі після `#`.
- **Перший глоб, що зматчив, забирає асет** → специфічніше пиши вище:
  `mindbattle-pvp-*.jar` над `mindbattle-*.jar`.
- Кожне правило має зматчити **рівно один** асет релізу, інакше джоба падає.
- `-sources.jar` / `-javadoc.jar` / `-dev.jar` ігноруються.
- Видаляються з сервера лише старі джари, які матчаться правилами твого
  репу, — джари інших модів на тому ж сервері недоторкані.

## Відкат / редеплой

Старі релізи зберігають джари: у своєму репі **Actions → Deploy →
Run workflow** → впиши тег попереднього релізу.

## Секрети

Канон — Actions-секрети цього репу: `PTERO_URL`, `PTERO_TOKEN` (панель),
`REPO_ADMIN_TOKEN` (PAT для роздачі). Ротація = оновити тут → Run workflow
«Sync secrets». Логи деплою публічні, тому deploy.py свідомо не друкує
серверну консоль — лише імена джарів і вердикт буту.

## Що цей реп НЕ деплоїть

Кімнати, моделі й конфіги abyss — то `abyss/tools/remote.py`
(`push-rooms`/`push-models`): там source of truth — прод-сервер, не git.
