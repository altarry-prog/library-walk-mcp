"""Local HTTP/SSE integration. Generated tokens stay in memory; no provider API calls."""
from __future__ import annotations
import asyncio
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.sse import sse_client

ROOT = Path(__file__).resolve().parent
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
CHECKS = []


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    CHECKS.append(name)
    print('PASS', name)


def payload(result):
    assert not result.isError
    return result.structuredContent if result.structuredContent is not None else json.loads(result.content[0].text)


async def exercise(base, token, transport):
    headers = {'Authorization': 'Bearer ' + token}
    ctx = streamablehttp_client(base + '/mcp', headers=headers) if transport == 'streamable-http' else sse_client(base + '/sse', headers=headers)
    async with ctx as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            initialized = await session.initialize()
            check(transport + ': initialize', initialized.serverInfo.name == 'library-life-readonly')
            tools = await session.list_tools()
            expected = {'get_data_status', 'search_resources', 'make_visit_plan', 'search_books_live',
                        'find_holding_libraries_live', 'check_book_status_live'}
            check(transport + ': tools/list 6개', {t.name for t in tools.tools} == expected)
            check(transport + ': 읽기 전용 힌트', all(t.annotations.readOnlyHint for t in tools.tools))
            status = payload(await session.call_tool('get_data_status', {}))
            check(transport + ': 모의 모드·실호출 꺼짐', status['synthetic'] is True and status['naru']['enabled'] is False)
            request = json.loads((ROOT / 'data' / 'example_request.json').read_text())
            result = payload(await session.call_tool('make_visit_plan', {'request': request}))
            check(transport + ': 가상 계획 호출', result['plans'][0]['library']['id'] == 'MOCK-LIB-A' and result['booking_performed'] is False)
            disabled = payload(await session.call_tool('search_books_live', {'keyword': '우주'}))
            check(transport + ': 실호출 차단', disabled['status'] == 'disabled')
            resource = payload(await session.call_tool('search_resources', {'kind': 'spaces'}))
            check(transport + ': 자료 검색', len(resource['items']) == 3 and resource['synthetic'] is True)
            if transport == 'streamable-http':
                (ROOT / 'docs' / 'mcp_tools_observed.json').write_text(
                    json.dumps([t.model_dump(mode='json', exclude_none=True) for t in tools.tools], ensure_ascii=False, indent=2), encoding='utf-8')
                (ROOT / 'docs' / '실제_MCP_모의응답.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')


def run(transport):
    token = secrets.token_urlsafe(40)
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    env = dict(os.environ, MCP_ACCESS_TOKEN=token, MCP_TRANSPORT=transport, PORT=str(port), HOST='127.0.0.1',
               MCP_ALLOWED_HOSTS='localhost:*,127.0.0.1:*', MCP_ALLOWED_ORIGINS='', REQUESTS_PER_MINUTE='60',
               ALLOW_LIVE_CALLS='false', EXTERNAL_LINK_APPROVED='false', NARU_KEY='',
               CATALOG_PATH=str(ROOT / 'data' / 'demo_catalog.json'))
    (ROOT / '.tmp').mkdir(exist_ok=True)
    logfile = ROOT / '.tmp' / ('server-' + transport + '.log')
    with logfile.open('w', encoding='utf-8') as log:
        process = subprocess.Popen([sys.executable, 'server.py'], cwd=ROOT, env=env, stdout=log, stderr=log)
        try:
            ready = False
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError('서버 조기 종료. .tmp 로그에서 설정·구동 오류를 확인하세요.')
                try:
                    health = httpx.get(base + '/health', timeout=1)
                    if health.status_code == 200:
                        ready = True
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            check(transport + ': health', ready and health.json()['mode'] == 'mock')
            route = '/mcp' if transport == 'streamable-http' else '/sse'
            response = httpx.get(base + route, timeout=2)
            check(transport + ': 인증 없는 접근 거절', response.status_code == 401)
            response = httpx.get(base + route, headers={'Authorization': 'Bearer ' + secrets.token_urlsafe(40)}, timeout=2)
            check(transport + ': 틀린 인증 거절', response.status_code == 401)
            response = httpx.post(base + route, content=b'x' * 512001,
                                  headers={'Authorization': 'Bearer ' + token}, timeout=3)
            check(transport + ': 과대 요청 거절', response.status_code == 413)
            response = httpx.get(base + route,
                                 headers={'Authorization': 'Bearer ' + token, 'Host': 'unapproved.invalid'}, timeout=2)
            check(transport + ': 허용하지 않은 Host 거절', response.status_code == 421)
            asyncio.run(exercise(base, token, transport))
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    check(transport + ': 인증값 로그 미출력', token not in logfile.read_text(encoding='utf-8'))


if __name__ == '__main__':
    run('streamable-http')
    run('sse')
    record = {'checked_at_kst': datetime.now(ZoneInfo('Asia/Seoul')).isoformat(timespec='seconds'),
              'checks_passed': len(CHECKS), 'checks': CHECKS,
              'scope': '로컬 루프백 HTTP/SSE, 합성자료, 실행 중 생성한 시험 인증값',
              'not_verified': ['FabriX 실제 연결', '외부 HTTPS 배포', '실제 데이터 API', '기관·대회 승인', '운영 환경 보안 인증']}
    (ROOT / 'docs' / 'server-verification.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print('LOCAL_MCP_VERIFICATION_PASS', len(CHECKS))
