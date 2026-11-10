import os
import tempfile
from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase, override_settings

from publisher.utils import (
    geelark_upload_proxy_urls,
    upload_proxy_label,
    upload_video_to_storage,
)

UPLOAD_URL = 'http://upload.example/put'
FIRST_PROXY = 'http://user:secret@10.1.2.3:8000'
SECOND_PROXY = 'http://10.4.5.6:8000'


def _clip_path():
    fd, path = tempfile.mkstemp(suffix='.mp4')
    os.write(fd, b'clip')
    os.close(fd)
    return path


class UploadProxyCarouselTests(SimpleTestCase):
    def test_parse_strips_empties_and_hides_credentials_in_label(self):
        for raw in ('', '   ', ',,,', '  ,  , '):
            with override_settings(GEELARK_UPLOAD_PROXIES=raw):
                self.assertEqual(geelark_upload_proxy_urls(), [])

        raw = (
            ' http://user:secret@10.1.2.3:8000, http://10.4.5.6:8000 ,'
            'http://user:secret@10.1.2.3:8000'
        )
        with override_settings(GEELARK_UPLOAD_PROXIES=raw):
            urls = geelark_upload_proxy_urls()
        self.assertEqual(urls, [FIRST_PROXY, SECOND_PROXY])
        self.assertIn('user:secret', urls[0])
        label = upload_proxy_label(urls[0])
        self.assertEqual(label, '10.1.2.3:8000')
        self.assertNotIn('secret', label)
        self.assertNotIn('user', label)

    @override_settings(GEELARK_UPLOAD_PROXIES='', GEELARK_UPLOAD_TIMEOUT_SEC=180)
    @patch('publisher.utils.requests.put')
    def test_empty_list_is_one_direct_put(self, mock_put):
        mock_put.return_value = Mock(status_code=200, text='ok')
        path = _clip_path()
        try:
            upload_video_to_storage(path, UPLOAD_URL)
        finally:
            os.remove(path)

        self.assertEqual(mock_put.call_count, 1)
        kwargs = mock_put.call_args.kwargs
        self.assertEqual(kwargs['timeout'], 180)
        self.assertNotIn('proxies', kwargs)
        self.assertNotIn('headers', kwargs)

    @override_settings(
        GEELARK_UPLOAD_PROXIES=FIRST_PROXY,
        GEELARK_UPLOAD_TIMEOUT_SEC=180,
    )
    @patch('publisher.utils.requests.put')
    def test_direct_connect_timeout_then_proxy_success(self, mock_put):
        reads = []

        def side_effect(url, data=None, **kwargs):
            reads.append(data.read())
            if len(reads) == 1:
                raise requests.exceptions.ConnectTimeout(
                    'connect timed out user:secret@10.1.2.3:8000'
                )
            return Mock(status_code=200, text='ok')

        mock_put.side_effect = side_effect
        path = _clip_path()
        try:
            upload_video_to_storage(path, UPLOAD_URL)
        finally:
            os.remove(path)

        self.assertEqual(reads, [b'clip', b'clip'])
        self.assertEqual(mock_put.call_count, 2)
        first = mock_put.call_args_list[0].kwargs
        second = mock_put.call_args_list[1].kwargs
        self.assertEqual(first['timeout'], (10, 180))
        self.assertEqual(first['proxies'], {'http': None, 'https': None})
        self.assertEqual(second['timeout'], (10, 180))
        self.assertEqual(second['proxies'], {'http': FIRST_PROXY, 'https': FIRST_PROXY})
        self.assertNotIn('headers', first)
        self.assertNotIn('headers', second)

    @override_settings(
        GEELARK_UPLOAD_PROXIES=FIRST_PROXY,
        GEELARK_UPLOAD_TIMEOUT_SEC=180,
    )
    @patch('publisher.utils.requests.put')
    def test_stop_on_direct_200(self, mock_put):
        mock_put.return_value = Mock(status_code=200, text='ok')
        path = _clip_path()
        try:
            upload_video_to_storage(path, UPLOAD_URL)
        finally:
            os.remove(path)

        self.assertEqual(mock_put.call_count, 1)
        self.assertNotIn(FIRST_PROXY, str(mock_put.call_args))
        self.assertEqual(
            mock_put.call_args.kwargs['proxies'],
            {'http': None, 'https': None},
        )

    @override_settings(
        GEELARK_UPLOAD_PROXIES=f'{FIRST_PROXY},{SECOND_PROXY}',
        GEELARK_UPLOAD_TIMEOUT_SEC=180,
    )
    @patch('publisher.utils.requests.put')
    def test_non_200_walks_the_list_and_stops_on_200(self, mock_put):
        codes = [500, 403, 200]

        def side_effect(url, data=None, **kwargs):
            data.read()
            code = codes[mock_put.call_count - 1]
            return Mock(status_code=code, text=f'status-{code}')

        mock_put.side_effect = side_effect
        path = _clip_path()
        try:
            upload_video_to_storage(path, UPLOAD_URL)
        finally:
            os.remove(path)

        self.assertEqual(mock_put.call_count, 3)
        third = mock_put.call_args_list[2].kwargs['proxies']
        self.assertEqual(third, {'http': SECOND_PROXY, 'https': SECOND_PROXY})

    @override_settings(
        GEELARK_UPLOAD_PROXIES=FIRST_PROXY,
        GEELARK_UPLOAD_TIMEOUT_SEC=180,
    )
    @patch('builtins.print')
    @patch('publisher.utils.requests.put')
    def test_all_fail_keeps_credentials_out_of_errors(self, mock_put, mock_print):
        body = ('A' * 500) + 'TAIL'

        def side_effect(url, data=None, **kwargs):
            data.read()
            if mock_put.call_count == 1:
                raise requests.exceptions.ConnectTimeout(
                    'HTTPSConnectionPool user:secret@10.1.2.3:8000'
                )
            return Mock(status_code=403, text=body)

        mock_put.side_effect = side_effect
        path = _clip_path()
        try:
            with self.assertRaises(Exception) as ctx:
                upload_video_to_storage(path, UPLOAD_URL)
        finally:
            os.remove(path)

        message = str(ctx.exception)
        self.assertTrue(message.startswith('Ошибка загрузки видео в хранилище:'))
        self.assertIn('10.1.2.3:8000', message)
        self.assertIn('ConnectTimeout', message)
        self.assertIn('403', message)
        self.assertIn('A' * 500, message)
        self.assertNotIn('TAIL', message)
        self.assertNotIn('secret', message)
        self.assertNotIn('user:secret', message)
        self.assertGreaterEqual(len(mock_print.call_args_list), 1)
        saw_proxy = False
        for call in mock_print.call_args_list:
            line = ' '.join(str(arg) for arg in call.args)
            self.assertNotIn('secret', line)
            self.assertNotIn('user:secret', line)
            if '10.1.2.3:8000' in line:
                saw_proxy = True
        self.assertTrue(saw_proxy)
