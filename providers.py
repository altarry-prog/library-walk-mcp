"""Optional InfoNaru client. Disabled unless both explicit gates and a key are set.
No arbitrary URLs, no redirects, no key rotation, no raw upstream error bodies.
"""
from __future__ import annotations
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import httpx

logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
KST = ZoneInfo('Asia/Seoul')


class NaruClient:
    BASE = 'https://data4library.kr/api/'

    def __init__(self, *, enabled=False, approved=False, key='', max_calls=5, ttl_seconds=600, transport=None):
        self.enabled = bool(enabled and approved and key)
        self._key = key
        self._max_calls = max(1, min(int(max_calls), 50))
        self._ttl = max(1, int(ttl_seconds))
        self._cache = {}
        self._lock = threading.Lock()
        self.calls = 0
        self._transport = transport

    @classmethod
    def from_env(cls):
        return cls(enabled=os.getenv('ALLOW_LIVE_CALLS', 'false').lower() == 'true',
                   approved=os.getenv('EXTERNAL_LINK_APPROVED', 'false').lower() == 'true',
                   key=os.getenv('NARU_KEY', ''), max_calls=int(os.getenv('MAX_NARU_CALLS_PER_PROCESS', '5')))

    def status(self):
        return {'enabled': self.enabled, 'process_call_count': self.calls,
                'process_call_limit': self._max_calls,
                'warning': '프로세스 재시작 시 초기화되는 보조 제한입니다. 제공기관 한도나 공동 사용량을 보장하지 않습니다.'}

    def _get(self, operation: str, params: dict):
        if not self.enabled:
            return {'status': 'disabled', 'reason': '외부 연계 승인·실호출 설정·서버 인증정보가 모두 필요합니다.'}
        key = (operation, tuple(sorted(params.items())))
        with self._lock:
            now = time.monotonic()
            cached = self._cache.get(key)
            if cached and now - cached[0] < self._ttl:
                return {'status': 'ok', 'response': cached[1], 'queried_at': cached[2], 'cache_hit': True}
            if self.calls >= self._max_calls:
                return {'status': 'local_limit_reached', 'reason': '보조 호출 한도에 도달했습니다. 키 교체로 우회하지 않습니다.'}
            self.calls += 1
            try:
                # Keep authKey inside this process. Never return or log request URLs.
                with httpx.Client(timeout=8, follow_redirects=False, transport=self._transport) as client:
                    response = client.get(self.BASE + operation,
                                          params={**params, 'authKey': self._key, 'format': 'json'})
                    response.raise_for_status()
                    if len(response.content) > 2_000_000:
                        return {'status': 'provider_error', 'reason': '허용 크기를 넘는 제공기관 응답입니다.'}
                    body = response.json()
                if not isinstance(body, dict):
                    return {'status': 'provider_error', 'reason': '제공기관 응답이 객체 형식이 아닙니다.'}
                data = body.get('response')
                if not isinstance(data, dict) or data.get('error'):
                    return {'status': 'provider_error', 'reason': '제공기관 응답 형식을 확인하지 못했습니다.'}
                stamp = datetime.now(KST).isoformat(timespec='seconds')
                self._cache[key] = (now, data, stamp)
                return {'status': 'ok', 'response': data, 'queried_at': stamp, 'cache_hit': False}
            except (httpx.HTTPError, ValueError, TypeError):
                return {'status': 'provider_error', 'reason': '자료 제공기관 응답에 실패했습니다. 인증값·원본 오류 내용은 노출하지 않습니다.'}

    @staticmethod
    def _text(value, limit=400):
        return str(value or '')[:limit]

    @staticmethod
    def _meta(reply):
        return {'provider': '도서관 정보나루', 'queried_at': reply['queried_at'], 'cache_hit': reply['cache_hit'],
                'source_url': 'https://data4library.kr/apiUtilization', 'reservation_performed': False}

    def search_books(self, keyword: str, limit=3):
        if not isinstance(keyword, str) or not keyword.strip() or len(keyword) > 40 or type(limit) is not int or not 1 <= limit <= 5:
            raise ValueError('검색어와 건수를 확인하세요.')
        reply = self._get('srchBooks', {'keyword': keyword.strip(), 'pageNo': 1, 'pageSize': limit})
        if reply['status'] != 'ok':
            return reply
        docs = reply['response'].get('docs', [])
        if not isinstance(docs, list):
            return {'status': 'provider_error', 'reason': '도서 목록 형식이 다릅니다.'}
        items = []
        for row in docs[:limit]:
            d = row.get('doc', {}) if isinstance(row, dict) else {}
            items.append({'isbn13': self._text(d.get('isbn13'), 30), 'title': self._text(d.get('bookname')),
                          'authors': self._text(d.get('authors')), 'publisher': self._text(d.get('publisher'))})
        return {'status': 'ok', 'items': items, **self._meta(reply),
                'note': '서지 검색 결과입니다. 해당 지역의 소장이나 대출 가능 여부는 아직 확인하지 않았습니다.'}

    def holding_libraries(self, isbn13: str, region_code: str, limit=5):
        if not isinstance(isbn13, str) or not isinstance(region_code, str) or not re.fullmatch(r'\d{13}', isbn13) or not re.fullmatch(r'\d{2,10}', region_code) or type(limit) is not int or not 1 <= limit <= 5:
            raise ValueError('ISBN·공식 지역번호·건수를 확인하세요.')
        reply = self._get('libSrchByBook', {'isbn': isbn13, 'region': region_code, 'pageNo': 1, 'pageSize': limit})
        if reply['status'] != 'ok':
            return reply
        libs = reply['response'].get('libs', [])
        if not isinstance(libs, list):
            return {'status': 'provider_error', 'reason': '도서관 목록 형식이 다릅니다.'}
        result = []
        for row in libs[:limit]:
            d = row.get('lib', {}) if isinstance(row, dict) else {}
            result.append({'library_code': self._text(d.get('libCode'), 30), 'name': self._text(d.get('libName')),
                           'public_address': self._text(d.get('address'))})
        return {'status': 'ok', 'items': result, **self._meta(reply),
                'note': '소장 도서관 후보입니다. 프로그램·공간 자료와 자동으로 합쳐지지 않으며 신청 가능 확정도 아닙니다.'}

    def book_status(self, isbn13: str, library_code: str):
        if not isinstance(isbn13, str) or not isinstance(library_code, str) or not re.fullmatch(r'\d{13}', isbn13) or not re.fullmatch(r'\d{1,15}', library_code):
            raise ValueError('ISBN과 도서관 번호를 확인하세요.')
        reply = self._get('bookExist', {'isbn13': isbn13, 'libCode': library_code})
        if reply['status'] != 'ok':
            return reply
        data = reply['response'].get('result')
        if not isinstance(data, dict):
            return {'status': 'provider_error', 'reason': '소장·상태 응답 형식이 다릅니다.'}
        yn = lambda value: True if value == 'Y' else False if value == 'N' else None
        basis = datetime.fromisoformat(reply['queried_at']).date() - timedelta(days=1)
        return {'status': 'ok', 'isbn13': isbn13, 'library_code': library_code,
                'has_book': yn(data.get('hasBook')), 'loan_available_previous_day': yn(data.get('loanAvailable')),
                'basis_date_by_provider_documentation': basis.isoformat(), 'actual_upstream_update_time': None,
                **self._meta(reply), 'warning': '조회일의 하루 전 기준입니다. 현재 상태·최신 수집 완료·실제 예약을 보장하지 않습니다.'}
