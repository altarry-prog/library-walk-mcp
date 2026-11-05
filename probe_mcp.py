"""Operator-run deployment probe: initialize, list tools, get_data_status only.
Use only an HTTPS address you administer and are authorized to access.
Credentials are read from the environment, never command-line arguments or files.
"""
import argparse
import asyncio
import json
import logging
import os
from urllib.parse import urlsplit
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.sse import sse_client

logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)


def validate_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('본인이 관리하는 HTTPS MCP 주소만 입력하세요. 인증정보·쿼리·조각 식별자는 주소에 넣지 않습니다.')
    return value


async def probe(url, transport, token):
    headers = {'Authorization': 'Bearer ' + token}
    context = streamablehttp_client(url, headers=headers, timeout=10) if transport == 'streamable-http' else sse_client(url, headers=headers, timeout=10)
    async with asyncio.timeout(30):
        async with context as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()
                names = {t.name for t in tools.tools}
                if 'get_data_status' not in names:
                    raise ValueError('예상한 도구를 제공하지 않는 서버입니다.')
                result = await session.call_tool('get_data_status', {})
                if result.isError:
                    raise ValueError('자료 상태 확인 실패')
                status = result.structuredContent or json.loads(result.content[0].text)
                return {'status': 'probe_pass', 'transport': transport,
                        'server_name': initialized.serverInfo.name, 'tools': sorted(names),
                        'catalog_mode': status.get('catalog_mode'), 'synthetic': status.get('synthetic'),
                        'naru_enabled': status.get('naru', {}).get('enabled'),
                        'scope': '연결·도구 목록·자료 상태만 확인. 실제 데이터 조회·예약·모델 호출 없음.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--transport', choices=['streamable-http', 'sse'], default='streamable-http')
    args = parser.parse_args()
    try:
        url = validate_url(args.url)
        token = os.environ.get('MCP_ACCESS_TOKEN', '')
        if len(token) < 32:
            raise ValueError('호스트 환경변수 MCP_ACCESS_TOKEN 설정을 확인하세요.')
        print(json.dumps(asyncio.run(probe(url, args.transport, token)), ensure_ascii=False, indent=2))
    except Exception as error:
        print('PROBE_FAILED:', type(error).__name__, '(민감정보 보호를 위해 원본 오류·헤더·주소를 출력하지 않습니다.)')
        raise SystemExit(1)
