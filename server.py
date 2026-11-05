"""Read-only MCP server for 「도서관 산책」. Default data is mock; live provider calls are OFF.

Two entry points share this module:
- ``python server.py``  : local strict mode (loopback, Bearer token required, allowed hosts required).
- ``python main.py``    : FabriX hosted mode (0.0.0.0, PORT env, platform-managed auth unless a token is set).
"""
from __future__ import annotations
import hmac
import os
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import monotonic
from zoneinfo import ZoneInfo
import anyio
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse
from planner import ROOT, NOTICE, PlanRequest, load_catalog, make_plan, search_catalog
from providers import NaruClient

SERVER_NAME = 'library-walk-readonly'
TRANSPORTS = ('streamable-http', 'sse', 'stdio')
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
LIVE_READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)
TOKEN_PATTERN = re.compile(r'[\x21-\x7e]{32,256}')


class BearerGate:
    """Static bearer check (token=None skips the check for platform-managed auth), rate and body limits.
    Not an OAuth server. Never logs requests or secrets."""
    def __init__(self, app, token: str | None, requests_per_minute=60, max_body=512000):
        self.app = app
        self.token = token.encode('utf-8') if token else None
        self.window = deque()
        self.rate = requests_per_minute
        self.max_body = max_body

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        if scope.get('path') == '/health' and scope.get('method') == 'GET':
            return await self.app(scope, receive, send)
        if self.token is not None:
            headers = dict(scope.get('headers', []))
            auth = headers.get(b'authorization', b'')
            if not hmac.compare_digest(auth, b'Bearer ' + self.token):
                return await JSONResponse({'error': 'unauthorized'}, status_code=401,
                                          headers={'WWW-Authenticate': 'Bearer'})(scope, receive, send)
        now = monotonic()
        while self.window and now - self.window[0] >= 60:
            self.window.popleft()
        if len(self.window) >= self.rate:
            return await JSONResponse({'error': 'rate_limited'}, status_code=429,
                                      headers={'Retry-After': '60'})(scope, receive, send)
        self.window.append(now)
        if scope.get('method') in ('POST', 'PUT', 'PATCH'):
            messages, count = [], 0
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                count += len(message.get('body', b''))
                if count > self.max_body:
                    return await JSONResponse({'error': 'body_too_large'}, status_code=413)(scope, receive, send)
                messages.append(message)
                if not message.get('more_body', False):
                    break
            iterator = iter(messages)
            async def replay():
                try:
                    return next(iterator)
                except StopIteration:
                    return await receive()
            return await self.app(scope, replay, send)
        return await self.app(scope, receive, send)


@dataclass
class Runtime:
    mcp: FastMCP
    app: BearerGate | None
    transport: str
    auth_mode: str
    host_check: str


def _token(hosted: bool) -> str | None:
    token = os.getenv('MCP_ACCESS_TOKEN', '')
    if token:
        if not TOKEN_PATTERN.fullmatch(token):
            raise ValueError('MCP_ACCESS_TOKEN을 공백 없는 ASCII 32~256자의 충분히 긴 임의 값으로 별도 설정하세요. 값은 출력하지 않습니다.')
        return token
    if hosted:
        return None  # platform-managed: FabriX controls who can reach the hosted server
    raise ValueError('로컬 실행에는 MCP_ACCESS_TOKEN이 필요합니다. 호스팅 실행은 main.py를 사용합니다.')


def _catalog():
    candidate = Path(os.getenv('CATALOG_PATH', 'data/demo_catalog.json'))
    path = (candidate if candidate.is_absolute() else ROOT / candidate).resolve()
    if not path.is_relative_to(ROOT / 'data'):
        raise ValueError('CATALOG_PATH는 이 패키지의 data 폴더 안이어야 합니다.')
    return load_catalog(path)


def _transport_security(hosted: bool):
    raw = os.getenv('MCP_ALLOWED_HOSTS')
    if raw is None:
        raw = '' if hosted else 'localhost:*,127.0.0.1:*'
    hosts = [x.strip() for x in raw.split(',') if x.strip()]
    origins = [x.strip() for x in os.getenv('MCP_ALLOWED_ORIGINS', '').split(',') if x.strip()]
    if '*' in hosts:
        raise ValueError('전체 와일드카드 호스트는 허용하지 않습니다. 실제 호스트 이름을 적거나 비워 두세요.')
    if hosts:
        return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts,
                                         allowed_origins=origins), 'allowlist'
    if not hosted:
        raise ValueError('접속을 허용할 호스트를 명시하세요.')
    # Hosted: the platform terminates ingress and the internal Host header is not known in advance.
    return TransportSecuritySettings(enable_dns_rebinding_protection=False), 'platform_managed'


def build_runtime(hosted: bool = False) -> Runtime:
    token = _token(hosted)
    catalog = _catalog()
    naru = NaruClient.from_env()
    if catalog['mode'] == 'mock' and naru.enabled:
        raise ValueError('모의자료 서버에서는 실호출을 켤 수 없습니다. 검토한 실제 자료와 별도 환경에서 시험하세요.')
    security, host_check = _transport_security(hosted)
    rate = int(os.getenv('REQUESTS_PER_MINUTE', '60'))
    if not 1 <= rate <= 600:
        raise ValueError('요청 제한은 분당 1~600 범위로 지정하세요.')
    transport = os.getenv('MCP_TRANSPORT', 'streamable-http')
    if transport not in TRANSPORTS:
        raise ValueError('MCP_TRANSPORT는 streamable-http, sse, stdio 중 하나입니다.')
    auth_mode = 'bearer' if token else 'platform_managed'
    mcp = FastMCP(SERVER_NAME, instructions=NOTICE + ' get_data_status로 모드·범위를 먼저 확인하세요. 모의자료를 실자료로 표현하지 마세요.',
                  stateless_http=True, json_response=True, log_level='WARNING', max_request_body_size=512000,
                  transport_security=security)

    @mcp.custom_route('/health', methods=['GET'])
    async def health(request: Request):
        return JSONResponse({'status': 'ok', 'service': SERVER_NAME, 'mode': catalog['mode'],
                             'auth_mode': auth_mode, 'booking_enabled': False})

    @mcp.tool(annotations=READ_ONLY)
    def get_data_status() -> dict:
        """먼저 호출. 자료 범위·모의 여부·연결 상태만 반환. 외부 API 호출 없음."""
        return {'catalog_mode': catalog['mode'], 'synthetic': catalog['mode'] == 'mock',
                'coverage': catalog['coverage'], 'basis_date': catalog['basis_date'],
                'server_time_kst': datetime.now(ZoneInfo('Asia/Seoul')).isoformat(timespec='seconds'),
                'loaded_records': {k: len(catalog[k]) for k in ('books', 'programs', 'spaces')},
                'source_status': catalog.get('source_status', {}), 'naru': naru.status(),
                'hosting_mode': 'fabrix_hosted' if hosted else 'local', 'auth_mode': auth_mode,
                'live_program_connector': 'not_implemented', 'live_space_slot_connector': 'not_implemented',
                'planning_scope': '같은 시설의 프로그램 후 공간 이용. 전국 실시간 통합이 아님.', 'notice': NOTICE}

    @mcp.tool(annotations=READ_ONLY)
    def search_resources(kind: str, region: str = '', topic: str = '', limit: int = 10) -> dict:
        """등록된 고정자료 검색. kind는 books/programs/spaces. 실제 외부 검색과 다름."""
        return search_catalog(catalog, kind, region, topic, limit)

    @mcp.tool(annotations=READ_ONLY)
    def make_visit_plan(request: PlanRequest) -> dict:
        """구조화한 조건으로 같은 시설의 책·프로그램·공간 후보(코스)를 대조. 신청·예약하지 않음.
        date=YYYY-MM-DD, start_time/end_time=HH:MM. 모의지역 DEMO, 주제 우주, 대상 가족.
        모르는 이용자 조건을 채우지 말고 먼저 물으세요. allow_unknown=true인 결과의 미확인 사항을 유지하세요.
        """
        return make_plan(catalog, request)

    @mcp.tool(annotations=LIVE_READ_ONLY)
    async def search_books_live(keyword: str, limit: int = 3) -> dict:
        """별도 승인·서버 설정 후에만 정보나루 서지 검색. 기본은 disabled. 소장·예약 확인 아님."""
        return await anyio.to_thread.run_sync(naru.search_books, keyword, limit)

    @mcp.tool(annotations=LIVE_READ_ONLY)
    async def find_holding_libraries_live(isbn13: str, region_code: str, limit: int = 5) -> dict:
        """별도 승인·설정 후 소장관 조회. 공식 지역번호를 확인하고 사용. 임의로 번호를 만들지 마세요."""
        return await anyio.to_thread.run_sync(naru.holding_libraries, isbn13, region_code, limit)

    @mcp.tool(annotations=LIVE_READ_ONLY)
    async def check_book_status_live(isbn13: str, library_code: str) -> dict:
        """별도 승인·설정 후 소장·하루 전 대출 상태. 현재 상태·실제 예약을 보장하지 않음."""
        return await anyio.to_thread.run_sync(naru.book_status, isbn13, library_code)

    app = None
    if transport != 'stdio':
        inner = mcp.streamable_http_app() if transport == 'streamable-http' else mcp.sse_app()
        app = BearerGate(inner, token, rate)
    return Runtime(mcp=mcp, app=app, transport=transport, auth_mode=auth_mode, host_check=host_check)


def create_app(hosted: bool = False):
    """ASGI app for HTTP transports (local strict by default). Raises for stdio."""
    runtime = build_runtime(hosted)
    if runtime.app is None:
        raise ValueError('stdio 전송에는 ASGI 앱이 없습니다. main.py 또는 server.py를 직접 실행하세요.')
    return runtime.app


def serve(runtime: Runtime, default_host: str, proxy_headers: bool, default_port: int = 8000):
    # Access logs disabled: do not put credentials, query strings or user prompts into logs.
    if runtime.transport == 'stdio':
        runtime.mcp.run(transport='stdio')
        return
    uvicorn.run(runtime.app, host=os.getenv('HOST', default_host), port=int(os.getenv('PORT', str(default_port))),
                log_level='warning', access_log=False, proxy_headers=proxy_headers)


if __name__ == '__main__':
    serve(build_runtime(hosted=False), default_host='127.0.0.1', proxy_headers=False)
