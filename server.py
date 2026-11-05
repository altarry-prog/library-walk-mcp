"""Authenticated read-only MCP server. Default: mock catalog, live APIs OFF."""
from __future__ import annotations
import hmac
import os
import re
from collections import deque
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from time import monotonic
import anyio
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse
from planner import ROOT, NOTICE, PlanRequest, load_catalog, make_plan, search_catalog
from providers import NaruClient

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
LIVE_READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)


class BearerGate:
    """Static bearer support, NOT an OAuth server. No request/secret logging."""
    def __init__(self, app, token: str, requests_per_minute=60, max_body=512000):
        self.app, self.token = app, token.encode('utf-8')
        self.window = deque()
        self.rate = requests_per_minute
        self.max_body = max_body

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        if scope.get('path') == '/health' and scope.get('method') == 'GET':
            return await self.app(scope, receive, send)
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


def create_app():
    token = os.getenv('MCP_ACCESS_TOKEN', '')
    if not re.fullmatch(r'[\x21-\x7e]{32,256}', token):
        raise ValueError('MCP_ACCESS_TOKEN을 공백 없는 ASCII 32~256자의 충분히 긴 임의 값으로 별도 설정하세요. 값은 출력하지 않습니다.')
    candidate = Path(os.getenv('CATALOG_PATH', 'data/demo_catalog.json'))
    path = (candidate if candidate.is_absolute() else ROOT / candidate).resolve()
    if not path.is_relative_to(ROOT / 'data'):
        raise ValueError('CATALOG_PATH는 이 패키지의 data 폴더 안이어야 합니다.')
    catalog = load_catalog(path)
    naru = NaruClient.from_env()
    if catalog['mode'] == 'mock' and naru.enabled:
        raise ValueError('모의자료 서버에서는 실호출을 켤 수 없습니다. 검토한 실제 자료와 별도 환경에서 시험하세요.')
    hosts = [x.strip() for x in os.getenv('MCP_ALLOWED_HOSTS', 'localhost:*,127.0.0.1:*').split(',') if x.strip()]
    origins = [x.strip() for x in os.getenv('MCP_ALLOWED_ORIGINS', '').split(',') if x.strip()]
    if not hosts or '*' in hosts:
        raise ValueError('접속을 허용할 호스트를 명시하세요. 전체 와일드카드는 허용하지 않습니다.')
    rate = int(os.getenv('REQUESTS_PER_MINUTE', '60'))
    if not 1 <= rate <= 600:
        raise ValueError('요청 제한은 분당 1~600 범위로 지정하세요.')
    mcp = FastMCP('library-life-readonly', instructions=NOTICE + ' get_data_status로 모드·범위를 먼저 확인하세요. 모의자료를 실자료로 표현하지 마세요.',
                  stateless_http=True, json_response=True, log_level='WARNING', max_request_body_size=512000,
                  transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                                                allowed_hosts=hosts, allowed_origins=origins))

    @mcp.custom_route('/health', methods=['GET'])
    async def health(request: Request):
        return JSONResponse({'status': 'ok', 'service': 'library-life-readonly', 'mode': catalog['mode'],
                             'booking_enabled': False})

    @mcp.tool(annotations=READ_ONLY)
    def get_data_status() -> dict:
        """먼저 호출. 자료 범위·모의 여부·연결 상태만 반환. 외부 API 호출 없음."""
        return {'catalog_mode': catalog['mode'], 'synthetic': catalog['mode'] == 'mock',
                'coverage': catalog['coverage'], 'basis_date': catalog['basis_date'],
                'server_time_kst': datetime.now(ZoneInfo('Asia/Seoul')).isoformat(timespec='seconds'),
                'loaded_records': {k: len(catalog[k]) for k in ('books', 'programs', 'spaces')},
                'source_status': catalog.get('source_status', {}), 'naru': naru.status(),
                'live_program_connector': 'not_implemented', 'live_space_slot_connector': 'not_implemented',
                'planning_scope': '같은 시설의 프로그램 후 공간 이용. 전국 실시간 통합이 아님.', 'notice': NOTICE}

    @mcp.tool(annotations=READ_ONLY)
    def search_resources(kind: str, region: str = '', topic: str = '', limit: int = 10) -> dict:
        """등록된 고정자료 검색. kind는 books/programs/spaces. 실제 외부 검색과 다름."""
        return search_catalog(catalog, kind, region, topic, limit)

    @mcp.tool(annotations=READ_ONLY)
    def make_visit_plan(request: PlanRequest) -> dict:
        """구조화한 조건으로 같은 시설의 책·프로그램·공간 후보를 대조. 신청·예약하지 않음.
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

    transport = os.getenv('MCP_TRANSPORT', 'streamable-http')
    if transport not in ('streamable-http', 'sse'):
        raise ValueError('MCP_TRANSPORT는 streamable-http 또는 sse입니다.')
    app = mcp.streamable_http_app() if transport == 'streamable-http' else mcp.sse_app()
    return BearerGate(app, token, rate)


if __name__ == '__main__':
    # Access logs disabled: do not put credentials, query strings or user prompts into logs.
    uvicorn.run(create_app(), host=os.getenv('HOST', '127.0.0.1'), port=int(os.getenv('PORT', '8000')),
                log_level='warning', access_log=False, proxy_headers=False)
