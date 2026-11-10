# HTTP proxy carousel for the GeeLark OSS upload

**Date:** 2026-11-10
**Repo:** `geelark-publisher`
**Status:** plan only. Implementation has not started.

## Goal

When GeeLark's signed `uploadUrl` does not accept a direct TCP connection from the publisher container, `upload_video_to_storage()` retries the same PUT through an operator-configured list of HTTP proxies. The list uses the same URL shape as Video Farm `YTDLP_PROXIES`. An empty list keeps today's single direct PUT.

## Out of scope

- Video Farm code, `YTDLP_PROXIES`, and any change on the Video Farm host.
- Cloud-phone proxy rotation: `GEELARK_PROXY_AUTO_ROTATE`, `GEELARK_PROXY_PORT_MIN`, `GEELARK_PROXY_PORT_MAX`, `GEELARK_PROXY_ROTATE_ATTEMPTS`, `GEELARK_PROXY_FAIL_ABORT_THRESHOLD`, and `rotate_geelark_proxy_port()`.
- `get_upload_url()` and the GeeLark `POST /open/v1/upload/getUrl` contract. Do not request a new `uploadUrl` between attempts.
- Deploy, server `.env` edits, container restarts, and resending a Video Farm job.
- Sticky rotation, success counters, and "rotate every N uploads". Every PUT starts at direct, then walks the list from the first entry.
- Adding a scheme to bare `host:port` values, or a second single-proxy env var.

## Decisions

- New env `GEELARK_UPLOAD_PROXIES`: comma-separated HTTP proxy URLs. Default `''`. Empty or whitespace-only means the current direct PUT only.
- Parse like Video Farm `list_proxies`: split on comma, strip each item, drop empties, keep first-seen order, drop later duplicates. There is no `YTDLP_PROXY`-style fallback.
- On a non-empty list, try **direct first**, then each proxy in list order, and stop on the first HTTP 200.
- Direct and each proxy use `timeout=(10, GEELARK_UPLOAD_TIMEOUT_SEC)`. `10` is the connect timeout (`UPLOAD_CONNECT_TIMEOUT_SEC` in `publisher/utils.py`, not a new env). The read timeout stays `GEELARK_UPLOAD_TIMEOUT_SEC` (default 180) so a live PUT can finish. A dead connect must not sit for 180 seconds.
- When the list is empty, the call stays `requests.put(upload_url, data=f, timeout=timeout)` with `timeout` as that integer and **no** `proxies` argument.
- When the list is non-empty, the direct attempt passes `proxies={"http": None, "https": None}` so an ambient `HTTP_PROXY` / `HTTPS_PROXY` cannot disguise itself as direct. Each proxy attempt passes `proxies={"http": url, "https": url}`.
- No extra HTTP headers on any attempt. The signed URL is already `http://...`; both proxy dict keys are set so a later `https://` upload URL still goes through the proxy.
- Reuse one `uploadUrl` for every attempt. Rewind the file to byte 0 before each attempt. `requests.put(data=f)` consumes the handle.
- Log with the existing `print` style in this function. Each failed attempt prints `direct` or `host:port` plus `ConnectTimeout` / `Timeout` / `RequestException` / `HTTP <status>`. A proxy success prints `host:port` and `ok`. A direct success prints nothing (same as today).
- Never log or raise `user`, password, or the raw proxy URL. `requests` exception strings often contain `user:pass@host`. Log `type(exc).__name__` only. `urllib.parse.urlparse`: label is `hostname:port` when the port is present, otherwise `hostname`. If there is no hostname, label `proxy#<1-based index>` and still do not print the raw string.
- If every attempt fails, raise `Exception` whose text starts with `Ошибка загрузки видео в хранилище:` and then lists attempt labels plus status codes or exception class names. If the last failure was an HTTP response, append that status and `response.text[:500]`, matching today's body cap. The message must not contain credentials.
- Do not commit real proxy URLs. `.env.example` gets a commented placeholder only.
- Operator note is already in `docs/upload-cost-optimization.md`. Do not add `docs/upload-proxy-carousel.md` or a section in `docs/json-ingest.md`.

No open product questions.

## Files to change

| File | Change |
|---|---|
| `config/settings.py` | Next to `GEELARK_UPLOAD_TIMEOUT_SEC`, add `GEELARK_UPLOAD_PROXIES = os.environ.get('GEELARK_UPLOAD_PROXIES', '')`. One comment: this is the OSS PUT list, not the phone proxy. |
| `publisher/utils.py` | Add `UPLOAD_CONNECT_TIMEOUT_SEC = 10`, `geelark_upload_proxy_urls()`, `upload_proxy_label()`, and the carousel inside `upload_video_to_storage()`. |
| `publisher/test_upload_proxy.py` | New Django `SimpleTestCase` module. Mock `publisher.utils.requests.put`. No sockets. |
| `.env.example` | Commented placeholder beside `GEELARK_UPLOAD_TIMEOUT_SEC`. Example host `proxy.example`, not a real proxy. |

`docs/upload-cost-optimization.md` already has the five-line operator note under "PUT в OSS, если прямой адрес молчит". Leave that section as written.

## Files not to touch

- `rotate_geelark_proxy_port()` and every `GEELARK_PROXY_*` setting.
- `get_upload_url()`, `upload_local_file_to_geelark_storage()` (it already calls `upload_video_to_storage`), `publisher/gallery_phone.py`.
- `docker-compose.yml`. The `app` and `watchdog` services already load `.env` via `env_file`.
- `docs/json-ingest.md`, Video Farm, secrets, and any live `.env`.

## Behavior

`upload_local_file_to_geelark_storage()` still calls `get_upload_url('mp4')` once, then `upload_video_to_storage(path, urls['uploadUrl'])`.

Empty `GEELARK_UPLOAD_PROXIES`:

1. Open the file.
2. One PUT, integer timeout, no `proxies` kwarg, no extra headers.
3. Non-200 raises the current message: `Ошибка загрузки видео в хранилище: {status} - {body}`.

Non-empty list:

1. Open the file once.
2. Direct PUT with `timeout=(10, GEELARK_UPLOAD_TIMEOUT_SEC)` and `proxies={"http": None, "https": None}`.
3. HTTP 200 returns immediately. Proxies are not called.
4. `requests.RequestException` or any status other than 200: print the safe label, `seek(0)`, try the next entry.
5. Proxy PUT uses the same timeout tuple and `proxies={"http": url, "https": url}`.
6. First proxy HTTP 200 returns. Later proxies are not called.
7. After the last failure, raise one exception. No credentials.

Edge cases:

- `'  ,  , '` parses to `[]` and uses the empty-list PUT.
- Duplicate URLs are tried once, in first-seen order.
- A proxy URL without a scheme is not rewritten. It is still attempted; a failure moves on. The log label must not echo the raw value when `urlparse` has no hostname.
- Direct HTTP 500, then proxy HTTP 403, then the next proxy HTTP 200: three PUTs, then success.
- Connect timeout on direct does not wait 180 seconds. The read budget is only for a connection that already opened.
- A proxy that accepts TCP and then stalls can still use the full read timeout. That is the residual case; today's failure is a connect timeout to `oss-eu-central-1.aliyuncs.com`.
- The signed URL might reject a later PUT if an earlier attempt uploaded a partial body. Do not call `getUrl` again. Direct connect timeouts send no body. Document this as a residual risk, not a second feature.
- Phone-proxy settings and logs stay unchanged.

## Verification

Existing runner, no new framework. From `/Users/sviridovnikita/IdeaProjects/geelark-publisher`:

```bash
python manage.py test publisher.test_upload_proxy
```

Then the publisher suite, so the new tests are not the only green file:

```bash
python manage.py test publisher
```

Tests in `publisher/test_upload_proxy.py` (`django.test.SimpleTestCase`, `override_settings`, `@patch("publisher.utils.requests.put")`):

1. **Parse.** `''`, `'   '`, and `',,,'` yield `[]`. `' http://user:secret@10.1.2.3:8000, http://10.4.5.6:8000 ,http://user:secret@10.1.2.3:8000'` yields those two URLs in that order, credentials still present on the URL that will be passed to `requests`. `upload_proxy_label` of the first URL is `10.1.2.3:8000` and does not contain `secret` or `user`.
2. **Empty list.** One `put`. `timeout` is the int from `GEELARK_UPLOAD_TIMEOUT_SEC`. `'proxies'` is absent. No `headers` kwarg.
3. **Direct short-fail, then proxy success.** Setting is `http://user:secret@10.1.2.3:8000`. First call raises `requests.exceptions.ConnectTimeout`. Second returns status 200. Assert two calls; first `timeout == (10, 180)` (override the read timeout to 180 in the test); first `proxies == {"http": None, "https": None}`; second `proxies` is both schemes set to the full proxy URL; second timeout is the same tuple. The side effect reads the file object on both calls and both reads return the same temp-file bytes (`b"clip"`). That fails if the code forgets `seek(0)`.
4. **Stop on direct 200.** Proxies are configured, the first response is 200, `put` is called once, and the proxy URL is not in that call.
5. **Non-200 walks the list.** Direct returns 500, first proxy returns 403, second returns 200. Three calls, third URL is the second proxy, no fourth call.
6. **All fail, credentials stay out.** Direct raises `ConnectTimeout`, the only proxy returns 403. The exception text contains `10.1.2.3:8000` and does not contain `secret` or `user:secret`. Patch `builtins.print` and assert the same for every printed line.

Use a temp file. Do not bind sockets and do not call GeeLark or the OSS host.

## Risks

- Logging `str(exc)` or the proxy URL leaks the password into `logs/`. The tests above are the guard.
- Forgetting `seek(0)` uploads an empty body on the second attempt. OSS may answer non-200, or a 200 on an empty object. The byte-read assertion is the guard.
- A partial PUT on a one-shot signed URL can make the next proxy fail even though the proxy path works. No automatic `getUrl` retry in this change.
- `timeout=(10, read)` fixes a refused or black-holed connect. It does not shorten a stall after TCP is up.
- Prod still needs `GEELARK_UPLOAD_PROXIES` in the server `.env` (same value as Video Farm `YTDLP_PROXIES`, copied on the server, never into git) and a publisher recreate. That deploy is not part of this implementation.

## Implementer stop

Code, the new test module, the commented `.env.example` line, and a green `python manage.py test publisher.test_upload_proxy`. Do not deploy, commit, or push unless a later instruction says so.
