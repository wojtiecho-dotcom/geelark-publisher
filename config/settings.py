import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'change-me-before-production')

DEBUG = True

ALLOWED_HOSTS = [
    '77.222.55.79',
    'localhost',
    '127.0.0.1',
]


INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'publisher',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.environ.get('DB_NAME', os.path.join('/app/data', 'db.sqlite3')),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

LANGUAGE_CODE = 'ru-ru'

TIME_ZONE = 'Europe/Moscow'

USE_I18N = True

USE_TZ = False

STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'static')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
# Папка для временных видео
TEMP_VIDEO_DIR = os.path.join(BASE_DIR, 'media', 'temp_videos')

YANDEX_URL = 'https://cloud-api.yandex.net/v1/disk/public/resources' # API Яндекс Диска для публичных ресурсов
GEELARK_API_URL = ''
GEELARK_TOKEN = os.environ.get('GEELARK_TOKEN', '')
GEELARK_PROXY_AUTO_ROTATE = os.environ.get('GEELARK_PROXY_AUTO_ROTATE', '').lower() in {
    '1', 'true', 'yes', 'on'
}
GEELARK_PROXY_PORT_MIN = int(os.environ.get('GEELARK_PROXY_PORT_MIN', '10000'))
GEELARK_PROXY_PORT_MAX = int(os.environ.get('GEELARK_PROXY_PORT_MAX', '10999'))
GEELARK_PROXY_ROTATE_ATTEMPTS = int(os.environ.get('GEELARK_PROXY_ROTATE_ATTEMPTS', '1'))
GEELARK_PROXY_FAIL_ABORT_THRESHOLD = int(
    os.environ.get('GEELARK_PROXY_FAIL_ABORT_THRESHOLD', '3')
)

GEELARK_TASK_TIMEOUT_MINUTES = int(os.environ.get('GEELARK_TASK_TIMEOUT_MINUTES', '8'))

# Video Farm: POST shareLink for /publish/stats (Excel col C = signed final URL)
VF_SHARELINK_URL = os.environ.get('VF_SHARELINK_URL', '').strip()
VF_SHARELINK_TOKEN = os.environ.get('VF_SHARELINK_TOKEN', '').strip()
# Incoming JSON from Video Farm POST /api/ingest/; empty → reuse VF_SHARELINK_TOKEN
VF_INGEST_TOKEN = os.environ.get('VF_INGEST_TOKEN', '').strip()

# Параллельный prepare/publish и защита от зависаний (см. docs/upload-cost-optimization.md)
GEELARK_MAX_PARALLEL = int(os.environ.get('GEELARK_MAX_PARALLEL', '2'))
GEELARK_UPLOAD_TIMEOUT_SEC = int(os.environ.get('GEELARK_UPLOAD_TIMEOUT_SEC', '180'))
# OSS PUT list, not the phone proxy.
GEELARK_UPLOAD_PROXIES = os.environ.get('GEELARK_UPLOAD_PROXIES', '')
GEELARK_DOWNLOAD_TIMEOUT_SEC = int(os.environ.get('GEELARK_DOWNLOAD_TIMEOUT_SEC', '180'))
GEELARK_TASK_SLA_SEC = int(os.environ.get('GEELARK_TASK_SLA_SEC', '900'))
GEELARK_DISPATCH_LEAD_SECONDS = int(os.environ.get('GEELARK_DISPATCH_LEAD_SECONDS', '120'))
# sending без geelark_task_id дольше этого → зомби (не блокирует телефон)
GEELARK_SENDING_ZOMBIE_SECONDS = int(os.environ.get('GEELARK_SENDING_ZOMBIE_SECONDS', '600'))

# stock = штатные youtubePubShort / TikTok task/add (дефолт, путь 2099–2110).
# gallery = phone/uploadFile + свой RPA (opt-in; на проде 31.08 не выкладывал).
GEELARK_PUBLISH_MODE = (
    os.environ.get('GEELARK_PUBLISH_MODE')
    or os.environ.get('GEELARK_YOUTUBE_PUBLISH_MODE')
    or 'stock'
)
GEELARK_YOUTUBE_PUBLISH_MODE = GEELARK_PUBLISH_MODE
GEELARK_YOUTUBE_GALLERY_FLOW_ID = os.environ.get('GEELARK_YOUTUBE_GALLERY_FLOW_ID', '').strip()
GEELARK_TIKTOK_GALLERY_FLOW_ID = os.environ.get('GEELARK_TIKTOK_GALLERY_FLOW_ID', '').strip()
GEELARK_INSTAGRAM_GALLERY_FLOW_ID = os.environ.get('GEELARK_INSTAGRAM_GALLERY_FLOW_ID', '').strip()
GEELARK_PHONE_BOOT_WAIT_SEC = int(
    os.environ.get('GEELARK_PHONE_BOOT_WAIT_SEC')
    or os.environ.get('GEELARK_ADB_BOOT_WAIT_SEC', '180')
)
_verify_rpa = os.environ.get('GEELARK_VERIFY_RPA') or os.environ.get(
    'GEELARK_VERIFY_YOUTUBE_RPA', '1'
)
GEELARK_VERIFY_RPA = _verify_rpa.lower() in {'1', 'true', 'yes', 'on'}
GEELARK_VERIFY_YOUTUBE_RPA = GEELARK_VERIFY_RPA
