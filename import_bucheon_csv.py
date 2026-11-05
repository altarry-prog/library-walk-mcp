"""Import the documented Bucheon culture-class CSV, without inventing times/seats.
Output is a REVIEW DRAFT, not an active server catalog. Requires explicit library mapping.
"""
import argparse
import csv
import hashlib
import json
from datetime import date
from pathlib import Path

COLUMNS = ['강좌명', '운영도서관', '대상', '신청기간시작일', '신청기간종료일', '교육시작일', '교육종료일', '정원']
SOURCE = 'https://www.data.go.kr/data/3075878/fileData.do'


def normalize_rows(rows, mapping, basis_date, synthetic=False):
    date.fromisoformat(basis_date)
    programs, missing = [], set()
    for i, row in enumerate(rows, 1):
        if any(k not in row for k in COLUMNS):
            raise ValueError('공식 CSV의 필수 열 8개를 확인하세요.')
        name = row['운영도서관'].strip()
        library = mapping.get(name)
        if library is None:
            missing.add(name)
        start, end = row['교육시작일'].strip(), row['교육종료일'].strip()
        for value in [start, end, row['신청기간시작일'].strip(), row['신청기간종료일'].strip()]:
            if value:
                date.fromisoformat(value)
        if start and end and start > end:
            raise ValueError('교육 날짜 범위가 거꾸로입니다.')
        capacity = row['정원'].strip()
        capacity = int(capacity) if capacity.isdecimal() else None
        programs.append({'id': f'BUCHEON-CSV-{i}', 'title': row['강좌명'].strip(),
                         'library_id': library.get('id') if library else None,
                         'unresolved_library_name': name if library is None else None,
                         'topics': [], 'audiences': None, 'eligibility_text': row['대상'].strip(),
                         'date': start if start and start == end else None,
                         'period_start': start or None, 'period_end': end or None,
                         'registration_start': row['신청기간시작일'].strip() or None,
                         'registration_end': row['신청기간종료일'].strip() or None,
                         'start': None, 'end': None, 'capacity': capacity, 'remaining_seats': None,
                         'registration_status': 'unknown', 'fee_per_person_krw': None,
                         'source': {'kind': 'synthetic' if synthetic else 'annual_file_snapshot',
                                    'source_url': None if synthetic else SOURCE, 'as_of': basis_date}})
    libraries = list({v['id']: v for v in mapping.values()}.values())
    return {'schema_version': 1, 'mode': 'mock' if synthetic else 'snapshot', 'basis_date': basis_date,
            'human_reviewed': False,
            'coverage': {'label': '부천 문화강좌 CSV 변환 초안' if not synthetic else '가상 CSV 변환 시험',
                         'national_coverage': False},
            'source_manifest': [] if synthetic else [{'source_url': SOURCE, 'basis_date': basis_date}],
            'source_status': {'books': 'not_configured', 'programs': 'ok', 'spaces': 'not_configured'},
            'libraries': libraries, 'books': [], 'programs': programs, 'spaces': [],
            'review_required': ['교육 기간을 매일 열리는 회차로 확장하지 않았습니다.',
                                '정원을 남은 자리로 바꾸지 않았습니다.',
                                '실제 시각·주제·대상·비용·시설 연결·출처를 사람이 확인해야 합니다.'],
            'unresolved_library_names': sorted(missing)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('csv_file')
    parser.add_argument('--library-map', required=True)
    parser.add_argument('--basis-date', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--synthetic', action='store_true')
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        raise SystemExit('원본 보호: 출력 파일이 이미 있습니다. 새 파일명을 사용하세요.')
    mapping = json.loads(Path(args.library_map).read_text(encoding='utf-8'))
    with Path(args.csv_file).open(encoding='utf-8-sig', newline='') as stream:
        result = normalize_rows(list(csv.DictReader(stream)), mapping, args.basis_date, args.synthetic)
    result['input_file_sha256'] = hashlib.sha256(Path(args.csv_file).read_bytes()).hexdigest()
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('변환 초안 작성. 실제 시각·빈자리·기관 연결 검토와 서버 적용은 완료하지 않았습니다.')
