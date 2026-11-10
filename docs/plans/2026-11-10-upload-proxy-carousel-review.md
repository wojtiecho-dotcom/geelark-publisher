# Review: HTTP proxy carousel for the GeeLark OSS upload

**Оценка:** 10/10
**План:** `docs/plans/2026-11-10-upload-proxy-carousel.md`
**Дата:** 2026-11-10
**Репозиторий:** `geelark-publisher`

Изменение совпадает с планом. Блокирующих дефектов нет.

## Что совпадает с планом

- `GEELARK_UPLOAD_PROXIES` добавлен рядом с `GEELARK_UPLOAD_TIMEOUT_SEC`, значение по умолчанию `''`. Комментарий отделяет список OSS PUT от прокси телефона.
- Пустая строка, пробелы и строка из одних запятых разбираются в `[]`. Тогда один прямой `requests.put`: таймаут — целое `GEELARK_UPLOAD_TIMEOUT_SEC`, аргументов `proxies` и `headers` нет. Не-200 сохраняет прежний текст `Ошибка загрузки видео в хранилище: {status} - {body}`.
- Непустой список: сначала прямой PUT с `proxies={"http": None, "https": None}` и `timeout=(10, GEELARK_UPLOAD_TIMEOUT_SEC)`, затем каждый URL по порядку. Остановка на первом HTTP 200. Поздние дубликаты отбрасываются, схема у голого `host:port` не дописывается.
- Один `uploadUrl` на все попытки. Файл открывается один раз, перед каждой попыткой карусели — `seek(0)`. `get_upload_url()` повторно не вызывается.
- В лог и в итоговое исключение попадает `direct` или `hostname:port` (иначе `proxy#N`), имя класса исключения или `HTTP <status>`. Успех прямого PUT ничего не печатает. Успех прокси печатает `host:port` и `ok`. `str(exc)` и сырой URL с логином не печатаются.
- Если все попытки неудачны, исключение начинается с `Ошибка загрузки видео в хранилище:`. Для последнего HTTP-ответа дописываются статус и `response.text[:500]`.
- `.env.example` содержит только закомментированный пример с хостом `proxy.example`. Реальных прокси-URL в diff нет. Тестовые адреса `10.1.2.3` и `10.4.5.6` взяты из плана.
- `GEELARK_PROXY_*`, `rotate_geelark_proxy_port()`, `get_upload_url()` и `upload_local_file_to_geelark_storage()` не менялись.
- Пять строк оператора в `docs/upload-cost-optimization.md` под «PUT в OSS, если прямой адрес молчит» описывают то же зафиксированное поведение. Отдельный `docs/upload-proxy-carousel.md` и секция в `docs/json-ingest.md` не добавлялись.
- `publisher/test_upload_proxy.py` — `SimpleTestCase`, `override_settings`, мок `publisher.utils.requests.put`. Покрыты разбор списка, пустой список, connect timeout и успех прокси с повторным чтением `b"clip"`, остановка на прямом 200, обход 500 → 403 → 200 и отсутствие `secret` / `user:secret` в исключении и в `print`.

## Дефекты

Нет.

Остаточный риск частичного PUT на одноразовый signed URL и полный read timeout после уже открытого TCP в плане приняты и в код как отдельная фича не вынесены.

## Тесты

Команда плана: `python manage.py test publisher` из `/Users/sviridovnikita/IdeaProjects/geelark-publisher` (интерпретатор `.venv`, Django 4.2.30).

`DB_NAME` указывает на временный sqlite: в `config/settings.py` путь по умолчанию — `/app/data/db.sqlite3`, этого каталога на машине нет. Набор тестов тот же.

Результат: **93 tests, OK**, 0.050s. System check без замечаний. В выводе карусели видны только `direct`, `10.1.2.3:8000` и `10.4.5.6:8000`, без логина и пароля.

## Рекомендации

Нет.
