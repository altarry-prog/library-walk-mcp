"""Read-only, deterministic candidate planning. No network or booking functions."""
from __future__ import annotations
import json
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parent
NOTICE = '시연·업무지원용입니다. 공식 서비스가 아니며 실제 신청·예약은 없습니다.'


def minutes(value: str) -> int:
    if not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d|24:00', value):
        raise ValueError('시각은 HH:MM 형식이어야 합니다.')
    h, m = map(int, value.split(':'))
    return h * 60 + m


def hhmm(value: int) -> str:
    return f'{value // 60:02d}:{value % 60:02d}'


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    region: str = Field(min_length=1, max_length=80)
    date: date
    start_time: str
    end_time: str
    topic: str = Field(min_length=1, max_length=40)
    audience: str = Field(min_length=1, max_length=20)
    group_size: int = Field(ge=1, le=100, strict=True)
    space_minutes: int = Field(ge=15, le=240, strict=True)
    budget_krw: int = Field(ge=0, le=10000000, strict=True)
    needs: list[str] = Field(default_factory=list, max_length=8)
    allow_unknown: bool = Field(default=False, strict=True)
    limit: int = Field(default=3, ge=1, le=5, strict=True)

    @model_validator(mode='after')
    def check_window(self):
        if minutes(self.start_time) >= minutes(self.end_time):
            raise ValueError('같은 날짜에서 시작 시각은 종료 시각보다 빨라야 합니다.')
        if any(not isinstance(n, str) or not n or len(n) > 40 for n in self.needs):
            raise ValueError('시설 조건을 확인하세요.')
        return self


def load_catalog(path: Path | None = None) -> dict:
    path = path or ROOT / 'data' / 'demo_catalog.json'
    obj = json.loads(path.read_text(encoding='utf-8'))
    if obj.get('schema_version') != 1 or obj.get('mode') not in ('mock', 'snapshot'):
        raise ValueError('지원하는 자료 판·모드를 확인하세요.')
    if not obj.get('basis_date') or not obj.get('coverage'):
        raise ValueError('자료 기준일과 적용 범위가 필요합니다.')
    date.fromisoformat(obj['basis_date'])
    for kind in ('libraries', 'books', 'programs', 'spaces'):
        if not isinstance(obj.get(kind), list):
            raise ValueError('자료 배열이 필요합니다.')
        ids = [r.get('id') for r in obj[kind]]
        if any(not i for i in ids) or len(ids) != len(set(ids)):
            raise ValueError('자료 ID 누락 또는 중복입니다.')
    if obj['mode'] == 'snapshot':
        if obj.get('human_reviewed') is not True or not obj.get('source_manifest'):
            raise ValueError('실제 고정자료는 출처표와 사람 대조 확인이 필요합니다.')
        if not obj['libraries']:
            raise ValueError('빈 양식을 실제 자료로 사용할 수 없습니다.')
    return obj


def envelope(catalog: dict) -> dict:
    return {
        'mode': catalog['mode'],
        'synthetic': catalog['mode'] == 'mock',
        'coverage': catalog['coverage'],
        'data_basis_date': catalog['basis_date'],
        'notice': NOTICE,
        'booking_performed': False,
        'live_api_called': False,
    }


def search_catalog(catalog: dict, kind: str, region: str = '', topic: str = '', limit: int = 10) -> dict:
    if kind not in ('books', 'programs', 'spaces') or not 1 <= limit <= 20:
        raise ValueError('검색 종류 또는 건수를 확인하세요.')
    result = envelope(catalog)
    if catalog.get('source_status', {}).get(kind, 'unknown') != 'ok':
        return {**result, 'status': 'source_unavailable', 'items': [], 'reason': '자료 연결 상태 미확인 또는 장애. 자료가 없다는 뜻이 아닙니다.'}
    libraries = {r['id']: r for r in catalog['libraries']}
    rows = []
    for row in catalog[kind]:
        lib = libraries.get(row.get('library_id'), {})
        if region and lib.get('region') != region:
            continue
        if topic and topic not in row.get('topics', []) and topic not in row.get('title', ''):
            continue
        rows.append(row)
    return {**result, 'status': 'ok', 'items': rows[:limit], 'matched_records': len(rows)}


def make_plan(catalog: dict, request: PlanRequest | dict) -> dict:
    r = request if isinstance(request, PlanRequest) else PlanRequest.model_validate(request)
    result = envelope(catalog)
    result.update({'request': r.model_dump(mode='json'), 'plans': [], 'ranking': '미확인 조건이 적은 순, 확인된 비용이 낮은 순, 시작 시각순. 성공확률 점수가 아닙니다.'})
    unavailable = [k for k in ('books', 'programs', 'spaces') if catalog.get('source_status', {}).get(k) != 'ok']
    if unavailable:
        return {**result, 'status': 'source_unavailable', 'unavailable_sources': unavailable,
                'message': '필수 출처 일부를 확인하지 못해 통합 계획을 만들지 않았습니다. 자원이 없다는 뜻이 아닙니다.'}
    today = date.fromisoformat(catalog['basis_date']) if catalog['mode'] == 'mock' else datetime.now(ZoneInfo('Asia/Seoul')).date()
    if r.date < today:
        raise ValueError('기준일보다 이전 날짜의 방문 계획은 지원하지 않습니다.')
    day, window_start, window_end = r.date.isoformat(), minutes(r.start_time), minutes(r.end_time)
    issues = Counter()
    libraries = {x['id']: x for x in catalog['libraries']}
    for program in catalog['programs']:
        lib = libraries.get(program.get('library_id'))
        if not lib or lib.get('region') != r.region:
            issues['지역 또는 시설 연결 불일치'] += 1
            continue
        if day in lib.get('closed_dates', []):
            issues['등록된 휴관일'] += 1
            continue
        if r.topic not in program.get('topics', []):
            issues['프로그램 주제 불일치'] += 1
            continue
        # Periods without a verified occurrence/time are NOT expanded into daily sessions.
        if program.get('date') != day:
            issues['해당 날짜의 실제 회차 미확인'] += 1
            continue
        if not program.get('start') or not program.get('end'):
            issues['프로그램 시각 미확인'] += 1
            continue
        try:
            ps, pe = minutes(program['start']), minutes(program['end'])
        except ValueError:
            issues['프로그램 시각 형식 오류'] += 1
            continue
        if ps >= pe or ps < window_start or pe > window_end:
            issues['프로그램 시간 조건 불일치'] += 1
            continue
        pending = []
        audiences = program.get('audiences')
        if audiences is None:
            pending.append('프로그램 대상 미확인')
        elif r.audience not in audiences and '무관' not in audiences:
            issues['프로그램 대상 불일치'] += 1
            continue
        if program.get('registration_status') in ('closed', 'full'):
            issues['접수 종료 또는 마감'] += 1
            continue
        if program.get('registration_status') != 'open':
            pending.append('프로그램 접수 상태 미확인')
        remaining = program.get('remaining_seats')
        if remaining is None:
            pending.append('프로그램 남은 자리 미확인(정원과 다름)')
        elif remaining < r.group_size:
            issues['프로그램 남은 자리 부족'] += 1
            continue
        books = [b for b in catalog['books'] if b.get('library_id') == lib['id'] and r.topic in b.get('topics', [])
                 and b.get('availability', {}).get('status') != 'previous_day_unavailable']
        if not books:
            issues['같은 시설의 주제 도서 후보 없음'] += 1
            continue
        books.sort(key=lambda b: (b.get('availability', {}).get('status') != 'previous_day_available', b['id']))
        book = books[0]
        if book.get('availability', {}).get('status') != 'previous_day_available':
            pending.append('도서 소장·대출 상태 미확인')
        for space in catalog['spaces']:
            if space.get('library_id') != lib['id']:
                continue
            missing = list(pending)
            capacity = space.get('capacity')
            if capacity is None:
                missing.append('공간 수용 인원 미확인')
            elif capacity < r.group_size:
                issues['공간 수용 인원 부족'] += 1
                continue
            rejected = False
            for need in r.needs:
                value = space.get('amenities', {}).get(need)
                if value is False:
                    issues['필요한 시설 조건 불충족'] += 1
                    rejected = True
                    break
                if value is not True:
                    missing.append(f'시설 조건 미확인: {need}')
            if rejected:
                continue
            slots = space.get('slots')
            chosen = None
            if slots is None:
                if pe + r.space_minutes <= window_end:
                    chosen = (pe, pe + r.space_minutes, 'requested_not_verified')
                    missing.append('공간 빈 시간 미확인. 제시 시간은 희망 시간입니다.')
            else:
                # An empty list is known to contain no usable slots, not an unknown schedule.
                for slot in sorted(slots, key=lambda s: (s.get('date', ''), s.get('start', ''))):
                    if slot.get('date') != day or slot.get('status') not in ('available', 'unknown'):
                        continue
                    try:
                        ss, se = minutes(slot['start']), minutes(slot['end'])
                    except (KeyError, ValueError):
                        continue
                    start = max(pe, ss, window_start)
                    if start + r.space_minutes <= min(se, window_end):
                        chosen = (start, start + r.space_minutes, slot['status'])
                        if slot['status'] == 'unknown':
                            missing.append('공간 시간대 상태 미확인')
                        break
            if chosen is None:
                issues['프로그램 뒤 공간 시간 확보 불가'] += 1
                continue
            hours = lib.get('hours')
            if not hours:
                missing.append('시설 운영시간 미확인')
            elif ps < minutes(hours['start']) or chosen[1] > minutes(hours['end']):
                issues['시설 운영시간과 충돌'] += 1
                continue
            pp = program.get('fee_per_person_krw')
            sf = space.get('session_fee_krw') if space.get('pricing_basis') == 'fixed_session' else None
            if pp is not None and (type(pp) is not int or pp < 0):
                pp = None
            if sf is not None and (type(sf) is not int or sf < 0):
                sf = None
            known_fee = (pp * r.group_size if pp is not None else 0) + (sf if sf is not None else 0)
            if known_fee > r.budget_krw:
                issues['확인된 비용만으로 예산 초과'] += 1
                continue
            if pp is None:
                missing.append('프로그램 비용 미확인')
            if sf is None:
                missing.append('공간 비용·요금 방식 미확인')
            if missing and not r.allow_unknown:
                issues['필수 조건 미확인 후보 제외'] += 1
                continue
            missing = list(dict.fromkeys(missing))
            result['plans'].append({
                'library': {'id': lib['id'], 'name': lib['name']},
                'book': book,
                'program': program,
                'space': {'id': space['id'], 'title': space['title'], 'source': space.get('source'),
                          'proposed_start': hhmm(chosen[0]), 'proposed_end': hhmm(chosen[1]),
                          'slot_evidence': chosen[2]},
                'known_fee_krw': known_fee,
                'total_fee_krw': known_fee if pp is not None and sf is not None else None,
                'budget_confirmed_from_data': pp is not None and sf is not None,
                'condition_status': 'needs_confirmation' if missing else 'matches_catalog_conditions',
                'pending_checks': missing,
                'warnings': ['같은 시설 안의 프로그램 후 공간 이용만 대조했습니다. 이동시간은 계산하지 않았습니다.',
                             '도서 상태는 하루 전 기준 자료이며 현재 이용 가능 여부와 실제 신청·예약은 별도 확인해야 합니다.',
                             '세부 연령·거주지·회원·예약자격은 자동으로 확인하지 않았습니다.'],
                'synthetic': catalog['mode'] == 'mock',
            })
    result['plans'].sort(key=lambda p: (len(p['pending_checks']), p['total_fee_krw'] is None,
                                      p['known_fee_krw'], p['program']['start'], p['space']['id']))
    result['candidate_count'] = len(result['plans'])
    result['plans'] = result['plans'][:r.limit]
    result['status'] = 'candidates_found' if result['plans'] else 'no_matching_plan_in_loaded_data'
    result['exclusion_summary'] = dict(issues)
    result['message'] = '등록된 자료 범위의 후보입니다. 전국 조회 완료나 예약 가능 확정이 아닙니다.'
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', default=str(ROOT / 'data' / 'example_request.json'))
    args = parser.parse_args()
    req = json.loads(Path(args.request).read_text(encoding='utf-8'))
    print(json.dumps(make_plan(load_catalog(), req), ensure_ascii=False, indent=2))
