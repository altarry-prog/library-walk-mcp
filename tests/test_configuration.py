import copy
import json
import os
import secrets
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from starlette.responses import JSONResponse
from planner import ROOT, load_catalog
from providers import NaruClient
from server import BearerGate, create_app
from probe_mcp import validate_url


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.env = {'MCP_ACCESS_TOKEN': secrets.token_urlsafe(40), 'NARU_KEY': '',
                    'ALLOW_LIVE_CALLS': 'false', 'EXTERNAL_LINK_APPROVED': 'false',
                    'MCP_ALLOWED_HOSTS': 'localhost:*,127.0.0.1:*', 'CATALOG_PATH': 'data/demo_catalog.json'}

    def test_missing_weak_or_nonascii_token_rejected(self):
        for value in ['', 'short', '가' * 40, 'a' * 40 + '\n']:
            with self.subTest(length=len(value)), patch.dict(os.environ, {**self.env, 'MCP_ACCESS_TOKEN': value}, clear=True):
                with self.assertRaises(ValueError):
                    create_app()

    def test_local_configuration_builds(self):
        with patch.dict(os.environ, self.env, clear=True):
            self.assertIsInstance(create_app(), BearerGate)

    def test_catalog_outside_data_rejected(self):
        with patch.dict(os.environ, {**self.env, 'CATALOG_PATH': 'server.py'}, clear=True):
            with self.assertRaises(ValueError):
                create_app()

    def test_mock_and_live_cannot_mix(self):
        values = {**self.env, 'NARU_KEY': secrets.token_urlsafe(40),
                  'ALLOW_LIVE_CALLS': 'true', 'EXTERNAL_LINK_APPROVED': 'true'}
        with patch.dict(os.environ, values, clear=True):
            with self.assertRaises(ValueError):
                create_app()

    def test_blank_or_wildcard_hosts_rejected(self):
        for host in ['', '*']:
            with patch.dict(os.environ, {**self.env, 'MCP_ALLOWED_HOSTS': host}, clear=True):
                with self.assertRaises(ValueError):
                    create_app()

    def test_invalid_rate_rejected(self):
        for value in ['0', '601']:
            with patch.dict(os.environ, {**self.env, 'REQUESTS_PER_MINUTE': value}, clear=True):
                with self.assertRaises(ValueError):
                    create_app()

    def test_bad_transport_rejected(self):
        with patch.dict(os.environ, {**self.env, 'MCP_TRANSPORT': 'unsupported'}, clear=True):
            with self.assertRaises(ValueError):
                create_app()

    def test_unreviewed_snapshot_rejected(self):
        data = copy.deepcopy(load_catalog())
        data.update(mode='snapshot', human_reviewed=False)
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as temp:
            path = Path(temp) / 'catalog.json'
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                load_catalog(path)

    def test_probe_url_does_not_allow_secret_query_or_plain_http(self):
        for url in ['http://example.invalid/mcp', 'https://example.invalid/mcp?secret=placeholder',
                    'https://placeholder@example.invalid/mcp', 'https://example.invalid/mcp#fragment']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)
        self.assertEqual(validate_url('https://example.invalid/mcp'), 'https://example.invalid/mcp')

    def test_bad_provider_payload_handled(self):
        client = NaruClient(enabled=True, approved=True, key=secrets.token_urlsafe(40),
                            transport=httpx.MockTransport(lambda req: httpx.Response(200, json=[])))
        self.assertEqual(client.search_books('우주')['status'], 'provider_error')

    def test_naru_key_from_environment_only(self):
        key = secrets.token_urlsafe(40)
        with patch.dict(os.environ, {'NARU_KEY': key, 'ALLOW_LIVE_CALLS': 'true', 'EXTERNAL_LINK_APPROVED': 'true'}, clear=True):
            self.assertTrue(NaruClient.from_env().enabled)

    def test_korean_timezone_without_system_timezone_files(self):
        import zoneinfo
        original = zoneinfo.TZPATH
        try:
            zoneinfo.reset_tzpath([])
            zoneinfo.ZoneInfo.clear_cache()
            from datetime import datetime
            self.assertEqual(datetime(2026, 11, 4, tzinfo=zoneinfo.ZoneInfo('Asia/Seoul')).utcoffset().total_seconds(), 32400)
        finally:
            zoneinfo.reset_tzpath(original)
            zoneinfo.ZoneInfo.clear_cache()


class MiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_rate_limit_and_health_bypass(self):
        async def app(scope, receive, send):
            await JSONResponse({'ok': True})(scope, receive, send)
        token = secrets.token_urlsafe(40)
        gate = BearerGate(app, token, requests_per_minute=1)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate), base_url='http://local.invalid') as client:
            headers = {'Authorization': 'Bearer ' + token}
            self.assertEqual((await client.get('/x', headers=headers)).status_code, 200)
            self.assertEqual((await client.get('/x', headers=headers)).status_code, 429)
            self.assertEqual((await client.get('/health')).status_code, 200)

    async def test_unauthorized_does_not_consume_authorized_quota(self):
        async def app(scope, receive, send):
            await JSONResponse({'ok': True})(scope, receive, send)
        token = secrets.token_urlsafe(40)
        gate = BearerGate(app, token, requests_per_minute=1)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate), base_url='http://local.invalid') as client:
            self.assertEqual((await client.get('/x')).status_code, 401)
            self.assertEqual((await client.get('/x', headers={'Authorization': 'Bearer ' + token})).status_code, 200)


if __name__ == '__main__':
    unittest.main()
