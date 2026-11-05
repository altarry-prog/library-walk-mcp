"""Generate deliberately fictional fixtures; never fetches external data."""
import json
from pathlib import Path
root = Path(__file__).resolve().parent / 'data'
root.mkdir(exist_ok=True)
source = {'kind': 'synthetic', 'name': '직접 만든 가상 시험자료', 'source_url': None, 'as_of': '2026-11-04'}
libs = [{'id': f'MOCK-LIB-{x}', 'name': f'가상도서관 {x}', 'region': 'DEMO',
         'hours': {'start': '09:00', 'end': '18:00'}, 'closed_dates': []} for x in 'ABC']
books = [{'id': f'MOCK-BOOK-{x}', 'title': f'가상도서 {x}: 우주 이야기',
          'library_id': f'MOCK-LIB-{x}', 'topics': ['우주'],
          'availability': {'status': 'previous_day_available' if x != 'C' else 'unknown', 'as_of': '2026-11-03'},
          'source': source} for x in 'ABC']
programs = []
for x, start, end, fee in [('A', '13:00', '14:00', 0), ('B', '14:00', '15:00', 3000), ('C', '15:00', '16:00', 0)]:
    programs.append({'id': f'MOCK-PROGRAM-{x}', 'title': f'가상 프로그램 {x}: 우주 체험',
                     'library_id': f'MOCK-LIB-{x}', 'topics': ['우주'], 'audiences': ['가족', '아동'],
                     'date': '2026-11-07', 'start': start, 'end': end,
                     'registration_status': 'open' if x != 'C' else 'unknown',
                     'remaining_seats': 6 if x != 'C' else None,
                     'capacity': 20, 'fee_per_person_krw': fee, 'source': source})
spaces = []
for x, capacity, fee, step_free in [('A', 6, 5000, True), ('B', 4, 1000, False), ('C', None, None, None)]:
    spaces.append({'id': f'MOCK-SPACE-{x}', 'title': f'가상 모임공간 {x}', 'library_id': f'MOCK-LIB-{x}',
                   'capacity': capacity, 'amenities': {'계단 없는 접근': step_free, '함께 대화 가능': True},
                   'pricing_basis': 'fixed_session', 'session_fee_krw': fee,
                   'slots': [{'date': '2026-11-07', 'start': '14:00', 'end': '17:00', 'status': 'available'}] if x != 'C' else None,
                   'source': source})
obj = {'schema_version': 1, 'mode': 'mock', 'basis_date': '2026-11-04',
       'coverage': {'label': '전부 허구인 가상 시범권역', 'region': 'DEMO', 'national_coverage': False},
       'source_status': {'books': 'ok', 'programs': 'ok', 'spaces': 'ok'},
       'libraries': libs, 'books': books, 'programs': programs, 'spaces': spaces}
request = {'region': 'DEMO', 'date': '2026-11-07', 'start_time': '13:00', 'end_time': '17:00',
           'topic': '우주', 'audience': '가족', 'group_size': 4, 'space_minutes': 60,
           'budget_krw': 20000, 'needs': ['계단 없는 접근'], 'allow_unknown': False, 'limit': 3}
for filename, value in [('demo_catalog.json', obj), ('example_request.json', request)]:
    (root / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
