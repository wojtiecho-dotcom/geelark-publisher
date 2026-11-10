# Оптимизация минут GeeLark / прокси при публикации

**Статус:** поверх `feature/scheduled-phone-timeout` (2026-08)  
**Репо:** `geelark-publisher` (`publisher/session_runner.py` + helpers в `utils.py`)  
**Вне scope:** сжатие/вес роликов Video Farm **не трогаем**.

## Что уже сделал upstream

Ветка `feature/scheduled-phone-timeout` (+ youtube-reliability):

- телефоны **не** стартуют в начале сессии;
- после create API → `submitted` + sync статусов GeeLark;
- `geelark_watchdog` отменяет зависшие RPA и гасит телефоны;
- YouTube title из колонки F, `profile_number`, proxy rotate, `completed_with_errors`.

Наш PR **не откатывает** это: не стартуем/не стопаем телефоны в воркере (иначе конфликт с RPA/watchdog).

## Что добавляем

| Изменение | Зачем |
|-----------|--------|
| `session_runner`: prepare → publish | параллель OSS = `GEELARK_MAX_PARALLEL` (default 2); телефон не нужен на download/PUT |
| `geelark_dispatch`: глобальный cap | одновременно не больше 2 телефонов/прокси; остальные `prepared` ждут watchdog |
| Дедуп storage по `video_url` | один PUT на YT+TT одного файла |
| Удаление temp сразу после PUT | меньше диска / orphan |
| Timeout на PUT | против «висит upload» |
| Метрики `t_*_ms`, `file_size_bytes`, `resource_url` | ловить затыки; p50/p95 в status API |
| SLA wall-clock на prepare/publish | abort зависшей задачи |
| `cost_guard`: stop → 1× rotate; abort 29996/20116 | не крутить прокси на живом телефоне; не жечь пачку как 14.08 |
| Fix `Content-Type` в `check_phone_status` | `'application/json'` без пробелов |

## Поток

```text
prepare (parallel ≤ GEELARK_MAX_PARALLEL): download → PUT storage → delete local mp4
publish: status=prepared (телефон ещё не стартует)
dispatch (≤ GEELARK_MAX_PARALLEL in-flight RPA): create RPA → submitted; остальные очередь
watchdog/sync: processing → success|error; сразу гасим телефон (если нет соседней due-сети) + idle reaper
29996: abort хвоста телефона → stop phone → одна смена порта; ≥3 в сессии → abort остальных
20116: не стартуем остальные сети на этом телефоне
ссылка публикации: не GeeLark shareLink; VF harvest `#vf_{jobId}` + ig-stats
```

HTTP URL из Video Farm (колонка C) принимается; `video_url` max_length=2048.

Прямая отправка без Excel: `POST /api/ingest/` (`docs/json-ingest.md`). `dryRun` делает HEAD URL и не стартует телефоны.

## Конфиг

См. `.env.example`: `GEELARK_MAX_PARALLEL`, `GEELARK_UPLOAD_TIMEOUT_SEC`,
`GEELARK_DOWNLOAD_TIMEOUT_SEC`, `GEELARK_TASK_SLA_SEC`,
`GEELARK_TASK_TIMEOUT_MINUTES=8`, `GEELARK_PROXY_ROTATE_ATTEMPTS=1`,
`GEELARK_PROXY_FAIL_ABORT_THRESHOLD=3`.

## PUT в OSS, если прямой адрес молчит

`GEELARK_UPLOAD_PROXIES` — HTTP-прокси через запятую, та же форма, что `YTDLP_PROXIES`.
Пусто: как сейчас, один прямой PUT.
Если список задан: сначала прямой PUT с connect timeout 10 с, затем прокси по порядку до HTTP 200.
В лог пишется только `host:port`, без логина и пароля. `GEELARK_PROXY_*` (прокси телефона) не меняется.
Реальные URL только в серверном `.env`, в git их нет.
