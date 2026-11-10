import os
import urllib.parse
import requests
import openpyxl
import time
import uuid
import random
from config import settings
from django.conf import settings as django_settings
from .models import UploadSession, refresh_session_status
from datetime import datetime, timedelta
from django.utils import timezone
from django.utils.timezone import make_aware
from django.core.exceptions import ValidationError


def session_worker(session_id):
    """Совместимость: делегирует в session_runner (prepare → publish API, без старта телефонов)."""
    from .session_runner import session_worker as _run

    _run(session_id)

def parse_excel_file(file):
    """
    Парсит Excel файл и возвращает список словарей с данными.
    ВСЕ ПОЛЯ ОБЯЗАТЕЛЬНЫ ДЛЯ ЗАПОЛНЕНИЯ!
    Если хоть одно поле пустое - строка пропускается с ошибкой.

    Структура Excel:
    A: Номер профиля (обязательно) - может быть в формате "1/605043047633256588" или просто "605043047633256588"
    B: Название соц. сети (обязательно)
    C: Ссылка на видео (обязательно)
    D: Комментарий к видео (обязательно) РАНЬШЕ БЫЛО НАЗВАНИЕ, ТЕПЕРЬ ТОЛЬКО КОММЕНТАРИЙ
    E: Время выкладывания (обязательно)
    """
    print(">>> parse_excel_file вызвана")
    wb = openpyxl.load_workbook(file)
    ws = wb.active

    rows_data = []
    errors = []

    # Маппинг колонок (индекс -> английское название)
    column_map = {
        1: 'profile_id',
        2: 'social_network',
        3: 'video_url',
        #4: 'title', Больше неактивно
        4: 'comment',
        5: 'publish_time',
        6: 'youtube_title',
    }

    # Проходим по строкам (начинаем с 2, т.к. 1-я строка - заголовки)
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not any(row):
            continue

        row_dict = {}
        row_errors = []

        # Парсим все поля
        for col_idx, field_name in column_map.items():
            # Старые таблицы могут содержать только первые пять колонок.
            value = row[col_idx - 1] if len(row) >= col_idx else None

            # Пропускаем проверку на пустоту для publish_time (обработаем позже рандомом)
            if field_name in ('publish_time', 'youtube_title'):
                row_dict[field_name] = value if value is not None else None
                continue

            if value is None or (isinstance(value, str) and not value.strip()):
                row_errors.append(f"Поле '{field_name}' обязательно для заполнения")
                row_dict[field_name] = None
            else:
                if isinstance(value, str):
                    value = value.strip()
                row_dict[field_name] = value

        if row_errors:
            errors.append({
                'row': row_idx,
                'errors': row_errors,
                'data': row_dict
            })
            continue

        # Из "30 / 605043047633256588" сохраняем номер телефона 30
        # отдельно от технического ID GeeLark.
        print(f"row dict profile id -> {row_dict['profile_id']}")
        profile_number, profile_id = split_profile_reference(row_dict['profile_id'])
        if not profile_id:
            errors.append({
                'row': row_idx,
                'errors': [f'Номер профиля должен содержать только цифры. Получено: {profile_id}'],
                'data': row_dict
            })
            continue

        social_network = convert_social_networks(row_dict['social_network'])
        if not social_network:
            errors.append({
                'row': row_idx,
                'errors': [f"Неподдерживаемая соцсеть: {row_dict['social_network']}"],
                'data': row_dict
            })
            continue

        video_url = row_dict['video_url']
        if not validate_video_url(video_url):
            errors.append({
                'row': row_idx,
                'errors': [
                    f"Невалидная ссылка на видео (нужен Яндекс.Диск или HTTP/HTTPS URL): {video_url}"
                ],
                'data': row_dict
            })
            continue

        try:
            print(f"Перед вызовом parse_publish_time, значение publish_time: {row_dict['publish_time']}")
            publish_time = parse_publish_time(row_dict['publish_time'])
            if publish_time < datetime.now(): #timezone.now():
                errors.append({
                    'row': row_idx,
                    'errors': [
                        f"Время публикации уже прошло: {publish_time.strftime('%d.%m.%Y %H:%M')}. Текущее время: {datetime.now().strftime('%d.%m.%Y %H:%M')}"],
                    'data': row_dict
                })
                continue
        except Exception as e:
            errors.append({
                'row': row_idx,
                'errors': [f"Ошибка парсинга времени: {str(e)}"],
                'data': row_dict
            })
            continue

        comment = row_dict['comment']
        if not comment or not str(comment).strip():
            errors.append({
                'row': row_idx,
                'errors': ["comment не может быть пустым"],
                'data': row_dict
            })
            continue

        youtube_title = row_dict.get('youtube_title') or comment
        youtube_title = str(youtube_title).strip()
        if social_network == 'YouTube' and len(youtube_title) > 100:
            errors.append({
                'row': row_idx,
                'errors': [
                    f"Заголовок YouTube должен быть не длиннее 100 символов. Получено: {len(youtube_title)}. "
                    "Укажите короткий заголовок в колонке F."
                ],
                'data': row_dict
            })
            continue

        # Всё ок
        rows_data.append({
            'profile_number': profile_number,
            'profile_id': profile_id,
            'social_network': social_network,
            'video_url': video_url,
            'title': youtube_title if social_network == 'YouTube' else str(comment).strip()[:255],
            'comment': str(comment).strip(),
            'publish_time': publish_time,
            'raw_row': row_idx,
        })

    #if errors and not rows_data:
    #    error_messages = [f"Строка {err['row']}: {', '.join(err['errors'])}" for err in errors[:10]]
    #    raise ValidationError("Нет валидных строк в файле:\n" + "\n".join(error_messages))
    #
    #if errors:
    #    print(f"Внимание: {len(errors)} строк пропущено")
    if errors and not rows_data:
        print(f"ВНИМАНИЕ: Нет валидных строк. {len(errors)} строк с ошибками")

    if errors:
        print(f"Внимание: {len(errors)} строк пропущено")

    return rows_data, errors


def split_profile_reference(value):
    """Разделяет запись Excel «номер телефона / ID GeeLark»."""
    raw_value = str(value).strip()
    if '/' not in raw_value:
        return '', raw_value

    profile_number, profile_id = raw_value.split('/', 1)
    return profile_number.strip(), profile_id.strip()


def validate_profile_id(value):
    """Оставлено для совместимости: возвращает технический ID GeeLark."""
    return split_profile_reference(value)[1]


def parse_publish_time(value):
    """
    Парсит время публикации.
    Поддерживает:
    - time объект из Excel (10:10:00)
    - строка с временем "10:10:00" или "10:10"
    - None или пустая строка — тогда генерируется рандомное время > сейчас

    Время привязывается к сегодняшней дате.
    """
    print(">>> parse_publish_time вызвана")
    today = datetime.now().date()
    now = datetime.now()
    print(f'зашло value - {value}')
    # Если значения нет - генерируем рандомное время в будущем
    if value is None or value == "":
        print(f'value is None or value == "" ')
        random_minutes = random.randint(1, 180)  # от 1 до 180 минут вперёд
        random_dt = now + timedelta(minutes=random_minutes)
        return random_dt

    # Проверяем, есть ли у объекта атрибуты hour и minute (это time object)
    if hasattr(value, 'hour') and hasattr(value, 'minute'):
        dt = datetime.combine(today, value)
        return dt #make_aware(dt)

    # Если строка с временем
    if isinstance(value, str):
        value = value.strip()

        # Формат "10:10:00" или "10:10"
        if ':' in value:
            parts = value.split(':')
            hour = int(parts[0])
            minute = int(parts[1])
            second = int(parts[2]) if len(parts) > 2 else 0
            dt = datetime.combine(today, time(hour, minute, second))
            return dt #make_aware(dt)

    raise ValueError(f"Не удалось распарсить время: {value}. Используйте формат ЧЧ:ММ:СС")


def convert_social_networks(network_name):
    """
    Функция конвертации наименований социальных сетей
    """
    network_map = {
        'инстаграм': 'Instagram',
        'instagram': 'Instagram',
        'inst': 'Instagram',
        'ig': 'Instagram',

        'ютуб': 'YouTube',
        'youtube': 'YouTube',
        'yt': 'YouTube',
        'yT': 'YouTube',
        'Ютуб': 'YouTube',

        'тикток': 'TikTok',
        'tiktok': 'TikTok',
        'tt': 'TikTok',
        'TikTok': 'TikTok',
        'ТикТок': 'TikTok',
    }

    normalized = network_name.lower().strip()
    return network_map.get(normalized)


def validate_yandex_disk_url(url):
    """Проверяет, что ссылка ведет на Яндекс Диск."""
    return is_yandex_disk_url(url)


def is_yandex_disk_url(url):
    """True, если URL — публичная ссылка Яндекс.Диска."""
    if not url:
        return False
    text = str(url).strip().lower()
    return 'disk.yandex.ru' in text or 'yadi.sk' in text


def is_direct_http_video_url(url):
    """
    True для прямой HTTP(S) ссылки на файл (например Video Farm signed URL).
    Яндекс.Диск сюда не входит — для него отдельный путь через API.
    """
    if not url:
        return False
    text = str(url).strip()
    if is_yandex_disk_url(text):
        return False
    lower = text.lower()
    return lower.startswith('https://') or lower.startswith('http://')


def validate_video_url(url):
    """Принимает Яндекс.Диск или прямой HTTP(S) URL."""
    return is_yandex_disk_url(url) or is_direct_http_video_url(url)


def get_yandex_direct_download_url(public_url):
    """
    Получает прямую ссылку для скачивания с Яндекс Диска.
    Нужно для скачивания видео.
    """

    params = {
        'public_key': public_url
    }
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.get(settings.YANDEX_URL, params=params, timeout=30)
            response.raise_for_status()

            data = response.json()

            # Прямая ссылка на скачивание
            download_url = data.get('file')

            if not download_url:
                raise Exception("Не удалось получить прямую ссылку на файл")

            return download_url

        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                raise Exception(f"Ошибка при запросе к Яндекс Диску после {max_retries} попыток: {str(e)}")

            print(f"Попытка {attempt + 1} не удалась, повтор через 2 секунды...")
            import time
            time.sleep(2)


def _task_save_path(task) -> str:
    temp_dir = settings.TEMP_VIDEO_DIR
    os.makedirs(temp_dir, exist_ok=True)
    return os.path.join(temp_dir, f'task_{task.id}.mp4')


def _stream_download_to_file(url: str, save_path: str, timeout: int = 120) -> str:
    """Скачивает URL потоком на диск. Возвращает save_path."""
    response = requests.get(url, stream=True, timeout=timeout, allow_redirects=True)
    response.raise_for_status()
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    with open(save_path, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    if not os.path.isfile(save_path) or os.path.getsize(save_path) == 0:
        raise Exception(f"Скачанный файл пуст или отсутствует: {save_path}")
    return save_path


def download_from_yandex(task) -> str:
    """
    Функция загрузки видео с Яндекс-диска.
    Скачивает видео по URL во временную папку.
    Возвращает путь к скачанному файлу.
    """

    task.status = 'downloading'
    task.save()
    public_url = task.video_url
    # 1. Получаем прямую ссылку
    print(f'получаем прямую ссылку. Task ID{task.id}')
    direct_url = get_yandex_direct_download_url(public_url)
    print(f'получили прямую ссылку, начинаем скачивание.')
    save_path = _task_save_path(task)
    # 2. Скачиваем файл
    try:
        result = _stream_download_to_file(direct_url, save_path, timeout=60)
        print(f'скачали файл. path - {result}')
        return result
    except requests.exceptions.RequestException as e:
        raise Exception(f"Ошибка при скачивании видео: {str(e)}")


def download_from_http(task) -> str:
    """
    Скачивает видео по прямой HTTP(S) ссылке (Video Farm signed URL и т.п.).
    Возвращает путь к скачанному файлу.
    """
    task.status = 'downloading'
    task.save()
    url = str(task.video_url).strip()
    print(f'скачиваем HTTP видео. Task ID={task.id} url={url[:120]}')
    save_path = _task_save_path(task)
    try:
        result = _stream_download_to_file(url, save_path, timeout=180)
        print(f'скачали HTTP файл. path - {result}')
        return result
    except requests.exceptions.RequestException as e:
        raise Exception(f"Ошибка при HTTP-скачивании видео: {str(e)}")


def download_video(task) -> str:
    """Диспетчер: Яндекс.Диск или прямой HTTP(S)."""
    url = task.video_url
    if is_yandex_disk_url(url):
        return download_from_yandex(task)
    if is_direct_http_video_url(url):
        return download_from_http(task)
    raise Exception(
        f"Неподдерживаемый URL видео (нужен Яндекс.Диск или HTTP/HTTPS): {url}"
    )


def delete_video(file_path: str) -> bool:
    """Удаляет временный видеофайл"""
    if os.path.exists(file_path):
        os.remove(file_path)
        return True
    return False


def get_upload_url(file_type: str = "mp4") -> dict:
    """Шаг 1: Получаем uploadUrl и resourceUrl от Geelark"""
    api_url = "https://openapi.geelark.com/open/v1/upload/getUrl"

    trace_id = str(uuid.uuid4()).upper()
    token = settings.GEELARK_TOKEN

    headers = {
        'Content-Type': 'application/json',
        'traceId': trace_id,
        'Authorization': f'Bearer {token}'
    }

    payload = {'fileType': file_type}

    response = requests.post(api_url, json=payload, headers=headers, timeout=30)
    response.raise_for_status()

    result = response.json()

    if result.get('code') != 0:
        raise Exception(f"Ошибка получения uploadUrl: {result.get('msg')}")

    upl = result['data']['uploadUrl']
    rurl = result['data']['resourceUrl']
    print(f'uploadUrl: {upl}')
    print(f'resourceUrl: {rurl}')
    return {
        'uploadUrl': result['data']['uploadUrl'],
        'resourceUrl': result['data']['resourceUrl']
    }


UPLOAD_CONNECT_TIMEOUT_SEC = 10


def geelark_upload_proxy_urls():
    """GEELARK_UPLOAD_PROXIES: через запятую, без пустых и повторных URL."""
    raw = getattr(django_settings, 'GEELARK_UPLOAD_PROXIES', '') or ''
    urls = []
    seen = set()
    for part in str(raw).split(','):
        item = part.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        urls.append(item)
    return urls


def upload_proxy_label(url: str, index: int = 1) -> str:
    """host:port без логина и пароля. Нет hostname — proxy#N."""
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname
    if not host:
        return f'proxy#{index}'
    if parsed.port is not None:
        return f'{host}:{parsed.port}'
    return host


def _upload_failure_message(failures, last_response) -> str:
    message = 'Ошибка загрузки видео в хранилище: ' + '; '.join(failures)
    if last_response is not None:
        body = (last_response.text or '')[:500]
        message = f'{message}; {last_response.status_code} - {body}'
    return message


def upload_video_to_storage(video_path: str, upload_url: str) -> None:
    """Шаг 2: Загружаем видео в хранилище Geelark (PUT запрос). С timeout против зависаний."""
    read_timeout = int(getattr(django_settings, 'GEELARK_UPLOAD_TIMEOUT_SEC', 180))
    proxy_urls = geelark_upload_proxy_urls()
    # ВАЖНО: НИКАКИХ ДОПОЛНИТЕЛЬНЫХ ЗАГОЛОВКОВ!
    # Один uploadUrl на все попытки. Частичный PUT может испортить подпись; getUrl повторно не зовём.
    with open(video_path, 'rb') as video_file:
        if not proxy_urls:
            response = requests.put(upload_url, data=video_file, timeout=read_timeout)
            if response.status_code != 200:
                print(f"  ❌ Ошибка загрузки!")
                print(f"  Status code: {response.status_code}")
                print(f"  Response: {response.text[:500]}")
                raise Exception(
                    f"Ошибка загрузки видео в хранилище: {response.status_code} - {response.text}"
                )
            return

        timeout = (UPLOAD_CONNECT_TIMEOUT_SEC, read_timeout)
        attempts = [('direct', None)]
        for index, proxy_url in enumerate(proxy_urls, start=1):
            attempts.append((upload_proxy_label(proxy_url, index), proxy_url))

        failures = []
        last_response = None
        for label, proxy_url in attempts:
            video_file.seek(0)
            if proxy_url is None:
                request_proxies = {'http': None, 'https': None}
            else:
                request_proxies = {'http': proxy_url, 'https': proxy_url}
            try:
                response = requests.put(
                    upload_url,
                    data=video_file,
                    timeout=timeout,
                    proxies=request_proxies,
                )
            except requests.RequestException as exc:
                exc_name = type(exc).__name__
                print(f"  ❌ {label} {exc_name}")
                failures.append(f'{label} {exc_name}')
                last_response = None
                continue
            if response.status_code == 200:
                if proxy_url is not None:
                    print(f'  {label} ok')
                return
            print(f"  ❌ {label} HTTP {response.status_code}")
            failures.append(f'{label} HTTP {response.status_code}')
            last_response = response

        raise Exception(_upload_failure_message(failures, last_response))


def upload_local_file_to_geelark_storage(video_path: str) -> str:
    """getUrl + PUT + wait_for_geelark_resource → resourceUrl. Телефон не нужен."""
    urls = get_upload_url('mp4')
    upload_video_to_storage(video_path, urls['uploadUrl'])
    wait_for_geelark_resource(urls['resourceUrl'])
    return urls['resourceUrl']


def create_geelark_publish_task(
    env_id: str,
    resource_url: str,
    title: str,
    comment: str,
    publish_time,
    social_network: str,
) -> str:
    """Создаёт RPA-задачу публикации. Возвращает geelark task id."""
    schedule_timestamp = int(publish_time.timestamp()) if publish_time else int(time.time())
    network = (social_network or '').lower()

    if network == 'instagram':
        return add_instagram_task(
            env_id=env_id,
            resource_url=resource_url,
            schedule_at=schedule_timestamp,
            description=comment,
        )
    if network == 'youtube':
        youtube_title = str(title or '').strip()
        if not youtube_title:
            raise ValueError('Для YouTube нужен непустой заголовок.')
        if len(youtube_title) > 100:
            raise ValueError(
                f'Заголовок YouTube слишком длинный: {len(youtube_title)} из 100 символов.'
            )
        return add_youtube_task(
            env_id=env_id,
            resource_url=resource_url,
            schedule_at=schedule_timestamp,
            title=youtube_title,
            description=comment,
        )
    return add_tiktok_task(
        env_id=env_id,
        resource_url=resource_url,
        schedule_at=schedule_timestamp,
        description=comment,
    )


def add_publish_task(env_id: str, resource_url: str, schedule_at: int, comment: str = None, description: str = None,
                     social_network: str = None) -> str:
    """Универсальная задача для любой соцсети через /task/add"""

    api_url = "https://openapi.geelark.com/open/v1/task/add"
    trace_id = str(uuid.uuid4()).upper()
    token = settings.GEELARK_TOKEN

    headers = {
        'Content-Type': 'application/json',
        'traceId': trace_id,
        'Authorization': f'Bearer {token}'
    }

    # Формируем базовую задачу
    task_data = {
        "scheduleAt": schedule_at,
        "envId": str(env_id),
        "video": resource_url
    }

    # Добавляем описание (для всех соцсетей)
    if description and len(description) <= 4000:
        task_data["videoDesc"] = description
    elif description:
        print(f"  ⚠️ Описание слишком длинное ({len(description)} > 4000), не будет отправлено")

    # Для YouTube добавляем comment
    if social_network and social_network.lower() == 'youtube' and comment:
        if len(comment) > 100:
            raise ValueError('Заголовок YouTube не может быть длиннее 100 символов.')
        task_data["comment"] = comment

    payload = {
        "taskType": 1,
        "list": [task_data],
        "planName": f"Auto publish {social_network or 'video'} {int(time.time())}"
    }

    # Отправляем запрос
    response = requests.post(api_url, json=payload, headers=headers, timeout=60)
    response.raise_for_status()

    result = response.json()

    if result.get('code') != 0:
        raise Exception(f"Ошибка создания задачи: {result.get('msg')}")

    task_ids = result.get('data', {}).get('taskIds', [])
    if not task_ids:
        raise Exception("Не получен taskId от Geelark")

    return task_ids[0]


def add_tiktok_task(env_id: str, resource_url: str, schedule_at: int, description: str = None) -> str:
    """Создание задачи для TikTok."""
    from publisher.gallery_phone import gallery_publish_mode_enabled
    from publisher.tiktok_gallery import add_tiktok_gallery_task

    if gallery_publish_mode_enabled():
        return add_tiktok_gallery_task(
            env_id=env_id,
            resource_url=resource_url,
            schedule_at=schedule_at,
            description=description,
        )

    api_url = "https://openapi.geelark.com/open/v1/task/add"
    print(f'api url completed')
    trace_id = str(uuid.uuid4()).upper()
    token = settings.GEELARK_TOKEN
    headers = {
        'Content-Type': 'application/json',
        'traceId': trace_id,
        'Authorization': f'Bearer {token}'
    }
    # Формируем задачу строго по документации
    task_data = {
        "scheduleAt": schedule_at,
        "envId": str(env_id),
        "video": resource_url
    }
    print(f'Имеется описание description.')
    print(f'DESCRIPTION TIKTOK -> {description}')
    # Добавляем описание, ТОЛЬКО если оно есть и проходит валидацию
    if description and len(description) <= 4000:
        print(f'description подходит под параметры TikTok - направляем')
        task_data["videoDesc"] = description
    elif description:
        print(f"  ⚠️ Описание ({len(description)} > 4000), не будет отправлено")

    payload = {
        "taskType": 1,
        "list": [task_data]
    }

    print(f'description completed')
    # Добавляем planName (опционально)
    payload["planName"] = f"Auto publish {int(time.time())}"
    print(f'payload oke - {payload}')
    response = requests.post(api_url, json=payload, headers=headers, timeout=60)
    response.raise_for_status()
    print(f'response - {response}')
    result = response.json()
    print(f'result - {result}')
    if result.get('code') != 0:
        raise Exception(f"Ошибка создания задачи: {result.get('msg')}")

    task_ids = result.get('data', {}).get('taskIds', [])
    if not task_ids:
        raise Exception("Не получен taskId от Geelark")

    return task_ids[0]


def add_instagram_task(env_id: str, resource_url: str, schedule_at: int, description: str = None) -> str:
    """Создание задачи для Instagram Reels."""
    from publisher.gallery_phone import gallery_publish_mode_enabled
    from publisher.instagram_gallery import add_instagram_gallery_task

    if gallery_publish_mode_enabled():
        return add_instagram_gallery_task(
            env_id=env_id,
            resource_url=resource_url,
            schedule_at=schedule_at,
            description=description,
        )

    api_url = "https://openapi.geelark.com/open/v1/rpa/task/instagramPubReels"
    trace_id = str(uuid.uuid4()).upper()
    token = settings.GEELARK_TOKEN

    headers = {
        'Content-Type': 'application/json',
        'traceId': trace_id,
        'Authorization': f'Bearer {token}'
    }

    # Проверка на пустое описание
    if not description or not description.strip():
        description = "#reels #instagram"
        print("⚠️ Описание Instagram пустое, установлено значение по умолчанию")
    # Обрезка до лимита 2200 символов
    if len(description) > 2200:
        print(f"⚠️ Описание Instagram слишком длинное ({len(description)} > 2200), будет обрезано")
        description = description[:2200]
    print(f'DESCRIPTION INSTAGRAM - {description}')
    # По документации: video - это массив строк, даже если одно видео
    payload = {
        "scheduleAt": schedule_at,
        "id": str(env_id),
        "description": description,
        "video": [resource_url]  # Массив, даже если одно видео!
    }
    print(f'payload okey -> {payload}')
    # Добавляем опциональные поля
    #payload["name"] = f"Auto publish Instagram {int(time.time())}"

    response = requests.post(api_url, json=payload, headers=headers, timeout=60)
    print(f'response instagramm - {response}')
    response.raise_for_status()
    result = response.json()
    print(f'result? instagramm {result}')
    if result.get('code') != 0:
        raise Exception(f"Ошибка создания Instagram задачи: {result.get('msg')}")

    task_id = result.get('data', {}).get('taskId')
    if not task_id:
        raise Exception("Не получен taskId от Geelark")

    return task_id


def add_youtube_task(
    env_id: str,
    resource_url: str,
    schedule_at: int,
    title: str,
    description: str = None,
) -> str:
    """Создание задачи для YouTube Video"""
    from publisher.gallery_phone import gallery_publish_mode_enabled
    from publisher.youtube_gallery import add_youtube_gallery_task

    if gallery_publish_mode_enabled():
        return add_youtube_gallery_task(
            env_id=env_id,
            resource_url=resource_url,
            schedule_at=schedule_at,
            title=title,
        )

    api_url = "https://openapi.geelark.com/open/v1/rpa/task/youtubePubShort"
    trace_id = str(uuid.uuid4()).upper()
    token = settings.GEELARK_TOKEN

    headers = {
        'Content-Type': 'application/json',
        'traceId': trace_id,
        'Authorization': f'Bearer {token}'
    }
    safe_title = title if title and title.strip() else "Auto publish"

    payload = {
        "scheduleAt": schedule_at,
        "id": str(env_id),
        "title": safe_title[:100],
        "video": resource_url,
        "sameStyleVoice": 0,
        "originalVoice": 0
    }
    if description and str(description).strip():
        payload["description"] = str(description).strip()[:5000]

    # Добавляем опциональные поля
    #payload["name"] = f"Auto publish YouTube {int(time.time())}"

    response = requests.post(api_url, json=payload, headers=headers, timeout=60)
    response.raise_for_status()
    result = response.json()
    print(f'result?? {result}')

    if result.get('code') != 0:
        raise Exception(f"Ошибка создания YouTube задачи: {result.get('msg')}")

    task_id = result.get('data', {}).get('taskId')
    if not task_id:
        raise Exception("Не получен taskId от Geelark")

    return task_id


#def add_publish_task(env_id: str, resource_url: str, schedule_at: int, description: str = None) -> str:
#    """
#    Шаг 3: Создаем задачу на публикацию видео
#    taskType: 1 - Publish video
#    """
#    api_url = "https://openapi.geelark.com/open/v1/task/add"
#    print(f'api url completed')
#    trace_id = str(uuid.uuid4()).upper()
#    token = settings.GEELARK_TOKEN
#    headers = {
#        'Content-Type': 'application/json',
#        'traceId': trace_id,
#        'Authorization': f'Bearer {token}'
#    }
#    # Формируем задачу строго по документации
#    task_data = {
#        "scheduleAt": schedule_at,
#        "envId": str(env_id),  # <-- ПРИВОДИМ К СТРОКЕ! ЭТО ВАЖНО!
#        "video": resource_url
#    }
#
#    # Добавляем описание, ТОЛЬКО если оно есть и проходит валидацию
#    if description and len(description) <= 4000:
#        task_data["videoDesc"] = description
#    elif description:
#        print(f"  ⚠️ Описание太长 ({len(description)} > 4000), не будет отправлено")
#
#    payload = {
#        "taskType": 1,
#        "list": [task_data]
#    }
#
#    print(f'description completed')
#    # Добавляем planName (опционально)
#    payload["planName"] = f"Auto publish {int(time.time())}"
#    print(f'payload oke - {payload}')
#    response = requests.post(api_url, json=payload, headers=headers, timeout=60)
#    response.raise_for_status()
#    print(f'response - {response}')
#    result = response.json()
#    print(f'result - {result}')
#    if result.get('code') != 0:
#        raise Exception(f"Ошибка создания задачи: {result.get('msg')}")
#
#    task_ids = result.get('data', {}).get('taskIds', [])
#    if not task_ids:
#        raise Exception("Не получен taskId от Geelark")
#
#    return task_ids[0]



def wait_for_geelark_resource(resource_url: str, timeout_seconds: int = 60) -> None:
    """дёт, пока загруженное видео станет доступно GeeLark для чтения."""
    deadline = time.monotonic() + timeout_seconds
    last_error = ""

    while time.monotonic() < deadline:
        try:
            response = requests.get(
                resource_url,
                headers={"Range": "bytes=0-0"},
                stream=True,
                timeout=15,
            )
            status_code = response.status_code
            response.close()

            if status_code in (200, 206):
                print("идео подтверждено в хранилище GeeLark.")
                return

            last_error = f"HTTP {status_code}"
        except requests.RequestException as exc:
            last_error = str(exc)

        time.sleep(2)

    raise RuntimeError(
        "идео не стало доступно в хранилище GeeLark за 60 секунд"
        + (f": {last_error}" if last_error else "")
    )


def send_to_geelark(profile_id: str, video_path: str, title: str, comment: str, publish_time, social_network: str):
    """
    Полный цикл: storage upload + create task.
    Телефон не стартуем здесь (см. scheduled-phone-timeout / watchdog).
    """
    print("Загружаем видео в хранилище Geelark...")
    resource_url = upload_local_file_to_geelark_storage(video_path)
    print(f" >>> Создаем задачу публикации в Geelark для {social_network}...")
    task_id = create_geelark_publish_task(
        env_id=profile_id,
        resource_url=resource_url,
        title=title,
        comment=comment,
        publish_time=publish_time,
        social_network=social_network,
    )
    print(f"✅ Задача создана, taskId: {task_id}")
    return {
        'success': True,
        'task_id': task_id,
        'resource_url': resource_url,
    }


def query_geelark_task_statuses(task_ids):
    """Возвращает актуальные статусы задач GeeLark, сгруппированные по task ID."""
    unique_ids = list(dict.fromkeys(str(task_id) for task_id in task_ids if task_id))
    if not unique_ids:
        return {}

    api_url = "https://openapi.geelark.com/open/v1/task/query"
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {settings.GEELARK_TOKEN}',
    }
    tasks_by_id = {}

    # GeeLark принимает не более 100 ID за один запрос.
    for start in range(0, len(unique_ids), 100):
        chunk = unique_ids[start:start + 100]
        request_headers = {**headers, 'traceId': str(uuid.uuid4()).upper()}
        response = requests.post(
            api_url,
            json={'ids': chunk},
            headers=request_headers,
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()

        if result.get('code') != 0:
            raise RuntimeError(
                f"GeeLark не вернул статусы задач: {result.get('msg') or 'неизвестная ошибка'}"
            )

        for item in result.get('data', {}).get('items', []):
            task_id = str(item.get('id') or '')
            if task_id:
                tasks_by_id[task_id] = item

    return tasks_by_id


def query_geelark_task_detail(task_id: str) -> dict:
    """Task Detail: logs and screenshots after completion (GeeLark /task/detail).

    shareLink on /task/query is often empty for youtubePubShort (taskType 42)
    and TikTok task/add (taskType 1). Logs are the documented fallback.
    """
    if not task_id:
        return {}

    payload = {'id': str(task_id)}
    merged = {}
    logs = []
    for _ in range(20):
        result = _geelark_api_post('/task/detail', payload)
        if result.get('code') != 0:
            raise RuntimeError(
                f"GeeLark не вернул детали задачи: {result.get('msg') or 'неизвестная ошибка'}"
            )
        data = result.get('data') or {}
        if not isinstance(data, dict):
            return merged
        if not merged:
            merged = dict(data)
        chunk = data.get('logs') or []
        if isinstance(chunk, list):
            logs.extend(chunk)
        if data.get('logContinue') and data.get('searchAfter'):
            payload = {'id': str(task_id), 'searchAfter': data.get('searchAfter')}
            continue
        break
    merged['logs'] = logs
    return merged


def _geelark_api_post(path, payload):
    """Send a GeeLark API request without logging credentials or payloads."""
    token = settings.GEELARK_TOKEN
    if not token:
        raise RuntimeError('GeeLark API token is not configured')

    response = requests.post(
        f'https://openapi.geelark.com/open/v1{path}',
        json=payload,
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {token}',
            'traceId': str(uuid.uuid4()).upper(),
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _safe_geelark_message(payload, default):
    if not isinstance(payload, dict):
        return default
    message = payload.get('msg') or payload.get('message')
    return str(message) if message else default



def cancel_geelark_task(task_id: str) -> bool:
    """Requests cancellation of a waiting or running GeeLark automation task."""
    result = _geelark_api_post('/task/cancel', {'ids': [str(task_id)]})
    if result.get('code') != 0:
        return False
    data = result.get('data') or {}
    return bool(data.get('successAmount', 0))


def rotate_geelark_proxy_port(profile_id):
    """Move a profile proxy to the next provider port after GeeLark error 29996.

    Call this only after the cloud phone is stopped so GeeLark proxy checks
    do not keep the PAYG mobile proxy busy. One attempt by default.

    The failed publication is deliberately not retried: retrying may create a
    duplicate video.  Passwords are only passed in memory to GeeLark and are
    never returned, logged, or stored by this application.
    """
    if not settings.GEELARK_PROXY_AUTO_ROTATE:
        return {
            'changed': False,
            'message': 'Автосмена порта прокси отключена в настройках сервиса.',
        }

    port_min = settings.GEELARK_PROXY_PORT_MIN
    port_max = settings.GEELARK_PROXY_PORT_MAX
    attempts = settings.GEELARK_PROXY_ROTATE_ATTEMPTS
    if port_min < 1 or port_max > 65535 or port_min > port_max or attempts < 1:
        return {
            'changed': False,
            'message': 'Автосмена порта прокси не выполнена: неверно задан диапазон портов.',
        }

    try:
        phones_payload = _geelark_api_post('/phone/list', {'ids': [str(profile_id)]})
        if phones_payload.get('code') != 0:
            return {
                'changed': False,
                'message': 'Автосмена порта прокси не выполнена: GeeLark не вернул настройки телефона.',
            }

        phones = (phones_payload.get('data') or {}).get('items') or []
        if len(phones) != 1 or not isinstance(phones[0].get('proxy'), dict):
            return {
                'changed': False,
                'message': 'Автосмена порта прокси не выполнена: у телефона не найдено подключённое прокси.',
            }

        phone_proxy = phones[0]['proxy']
        scheme = str(phone_proxy.get('type') or '').lower()
        server = str(phone_proxy.get('server') or '')
        username = str(phone_proxy.get('username') or '')
        password = phone_proxy.get('password') or ''
        current_port = int(phone_proxy.get('port'))
        if not (scheme and server and username and password):
            return {
                'changed': False,
                'message': 'Автосмена порта прокси не выполнена: данные прокси телефона неполные.',
            }

        proxies_payload = _geelark_api_post('/proxy/list', {'page': 1, 'pageSize': 100})
        if proxies_payload.get('code') != 0:
            return {
                'changed': False,
                'message': 'Автосмена порта прокси не выполнена: GeeLark не вернул список прокси.',
            }

        proxies = (proxies_payload.get('data') or {}).get('list') or []
        matches = [
            proxy for proxy in proxies
            if str(proxy.get('scheme') or '').lower() == scheme
            and str(proxy.get('server') or '') == server
            and str(proxy.get('username') or '') == username
            and int(proxy.get('port') or 0) == current_port
        ]
        if len(matches) != 1:
            return {
                'changed': False,
                'message': 'Автосмена порта прокси не выполнена: не удалось однозначно найти это прокси в GeeLark.',
            }

        saved_proxy = matches[0]
        span = port_max - port_min + 1
        max_attempts = min(attempts, span)
        last_reason = 'GeeLark не подтвердил новый порт'
        for offset in range(1, max_attempts + 1):
            next_port = port_min + ((current_port - port_min + offset) % span)
            update_payload = _geelark_api_post('/proxy/update', {
                'list': [{
                    'id': saved_proxy['id'],
                    'scheme': scheme,
                    'server': server,
                    'port': next_port,
                    'username': username,
                    'password': saved_proxy.get('password') or password,
                }],
            })
            data = update_payload.get('data') or {}
            if data.get('successAmount', 0):
                return {
                    'changed': True,
                    'message': (
                        f'Порт прокси автоматически изменён: {current_port} → {next_port}. '
                        'Неудачная публикация автоматически не повторялась.'
                    ),
                }
            fail_details = data.get('failDetails') or []
            last_reason = _safe_geelark_message(
                fail_details[0] if fail_details else update_payload,
                last_reason,
            )

        return {
            'changed': False,
            'message': (
                f'Автосмена порта прокси не выполнена после {max_attempts} попыток: {last_reason}.'
            ),
        }
    except (requests.RequestException, TypeError, ValueError, KeyError) as exc:
        return {
            'changed': False,
            'message': f'Автосмена порта прокси не выполнена: {exc.__class__.__name__}.',
        }


def start_cloud_phone(env_id: str) -> bool:
    """
    Запускает облачный телефон по ID
    """
    api_url = "https://openapi.geelark.com/open/v1/phone/start"
    trace_id = str(uuid.uuid4()).upper()
    token = settings.GEELARK_TOKEN

    headers = {
        'Content-Type': 'application/json',
        'traceId': trace_id,
        'Authorization': f'Bearer {token}'
    }

    payload = {
        "ids": [str(env_id)]
    }

    print(f"  Запускаем облачный телефон {env_id}...")
    response = requests.post(api_url, json=payload, headers=headers, timeout=30)
    response.raise_for_status()

    result = response.json()

    if result.get('code') != 0:
        raise Exception(f"  Ошибка запуска телефона: {result.get('msg')}")

    data = result.get('data', {})
    success_amount = data.get('successAmount', 0)

    if success_amount > 0:
        print(f"  Телефон {env_id} успешно запущен")
        return True
    else:
        fail_details = data.get('failDetails', [])
        if fail_details:
            raise Exception(f"  Не удалось запустить телефон: {fail_details[0].get('msg')}")
        return False


def check_phone_status(env_id: str) -> dict:
    """
    Проверяет статус облачного телефона
    """
    api_url = "https://openapi.geelark.com/open/v1/phone/list"
    trace_id = str(uuid.uuid4()).upper()
    token = settings.GEELARK_TOKEN

    headers = {
        'Content-Type': 'application/json',
        'traceId': trace_id,
        'Authorization': f'Bearer {token}'
    }

    payload = {
        "ids": [str(env_id)]
    }

    response = requests.post(api_url, json=payload, headers=headers, timeout=30)
    response.raise_for_status()

    result = response.json()

    if result.get('code') != 0:
        raise Exception(f"  Ошибка проверки статуса: {result.get('msg')}")

    items = result.get('data', {}).get('items', [])
    if not items:
        raise Exception(f"  Телефон {env_id} не найден")

    phone = items[0]
    status = phone.get('status')  # 0 - Started, 1 - Starting, 2 - Shut down

    return {
        'id': phone.get('id'),
        'status': status,
        'status_text': ['Запущен', 'Запускается', 'Выключен'][status] if status in [0, 1, 2] else 'Неизвестно',
        'serial_name': phone.get('serialName'),
        'is_running': status == 0
    }


def stop_cloud_phone(env_id: str) -> bool:
    """
    Останавливает облачный телефон по ID
    """
    api_url = "https://openapi.geelark.com/open/v1/phone/stop"
    trace_id = str(uuid.uuid4()).upper()
    token = settings.GEELARK_TOKEN

    headers = {
        'Content-Type': 'application/json',
        'traceId': trace_id,
        'Authorization': f'Bearer {token}'
    }

    payload = {
        "ids": [str(env_id)]
    }

    print(f"  📞 Останавливаем облачный телефон {env_id}...")
    response = requests.post(api_url, json=payload, headers=headers, timeout=30)
    response.raise_for_status()

    result = response.json()

    if result.get('code') != 0:
        raise Exception(f"Ошибка остановки телефона: {result.get('msg')}")

    data = result.get('data', {})
    success_amount = data.get('successAmount', 0)

    if success_amount > 0:
        print(f"  ✅ Телефон {env_id} успешно остановлен")
        return True
    else:
        fail_details = data.get('failDetails', [])
        if fail_details:
            print(f"  ⚠️ Не удалось остановить телефон: {fail_details[0].get('msg')}")
        return False


def stop_cloud_phones(env_ids: list) -> None:
    """
    Останавливает несколько облачных телефонов
    """
    for env_id in env_ids:
        try:
            stop_cloud_phone(env_id)
        except Exception as e:
            print(f"  ❌ Ошибка при остановке телефона {env_id}: {e}")
