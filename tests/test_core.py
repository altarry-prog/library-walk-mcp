import copy
import json
import secrets
import unittest
from unittest.mock import patch
import httpx
from planner import ROOT, PlanRequest, load_catalog, make_plan, search_catalog
from providers import NaruClient
from import_bucheon_csv import COLUMNS, normalize_rows


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.c = load_catalog()
        self.r = json.loads((ROOT / 'data' / 'example_request.json').read_text())

    def plan(self):
        return make_plan(self.c, self.r)

    def test_base_matches_same_facility_without_booking(self):
        out = self.plan()
        self.assertEqual(len(out['plans']), 1)
        p = out['plans'][0]
        self.assertEqual(p['library']['id'], 'MOCK-LIB-A')
        self.assertEqual(p['total_fee_krw'], 5000)
        self.assertEqual(p['space']['proposed_start'], '14:00')
        self.assertTrue(out['synthetic'])
        self.assertFalse(out['booking_performed'])
        self.assertFalse(out['live_api_called'])

    def test_topic_changes_results(self):
        self.c['books'][1]['topics'] = ['추리']
        self.c['programs'][1]['topics'] = ['추리']
        self.r.update(topic='추리', needs=[])
        self.assertEqual(self.plan()['plans'][0]['library']['id'], 'MOCK-LIB-B')

    def test_time_window_changes_results(self):
        self.r.update(start_time='14:00', needs=[])
        self.assertEqual(self.plan()['plans'][0]['library']['id'], 'MOCK-LIB-B')

    def test_capacity_change_excludes_small_space(self):
        self.r['needs'] = []
        self.assertEqual(len(self.plan()['plans']), 2)
        self.r['group_size'] = 5
        self.assertEqual(len(self.plan()['plans']), 1)

    def test_program_remaining_seats(self):
        self.r['group_size'] = 7
        self.assertFalse(self.plan()['plans'])

    def test_budget_hard_limit(self):
        self.r['budget_krw'] = 4999
        self.assertFalse(self.plan()['plans'])

    def test_amenity_false_excluded(self):
        self.c['spaces'][0]['amenities']['계단 없는 접근'] = False
        self.assertFalse(self.plan()['plans'])

    def test_unknown_fields_kept_not_promised(self):
        self.r['allow_unknown'] = True
        p = next(p for p in self.plan()['plans'] if p['library']['id'] == 'MOCK-LIB-C')
        self.assertIsNone(p['total_fee_krw'])
        self.assertFalse(p['budget_confirmed_from_data'])
        self.assertEqual(p['space']['slot_evidence'], 'requested_not_verified')
        self.assertEqual(p['condition_status'], 'needs_confirmation')
        self.assertTrue(p['pending_checks'])

    def test_empty_slots_not_unknown(self):
        self.c['spaces'][0]['slots'] = []
        self.assertFalse(self.plan()['plans'])

    def test_closed_slot_excluded(self):
        self.c['spaces'][0]['slots'][0]['status'] = 'closed'
        self.assertFalse(self.plan()['plans'])

    def test_not_enough_continuous_time(self):
        self.r['space_minutes'] = 181
        self.assertFalse(self.plan()['plans'])

    def test_closed_program_excluded(self):
        self.c['programs'][0]['registration_status'] = 'closed'
        self.assertFalse(self.plan()['plans'])

    def test_capacity_is_not_remaining_seats(self):
        self.c['programs'][0]['capacity'] = 999
        self.c['programs'][0]['remaining_seats'] = None
        self.assertFalse(self.plan()['plans'])
        self.r['allow_unknown'] = True
        p = self.plan()['plans'][0]
        self.assertTrue(any('남은 자리' in x for x in p['pending_checks']))

    def test_date_range_not_daily_occurrence(self):
        self.c['programs'][0].update(date=None, period_start='2026-11-01', period_end='2026-11-30')
        self.assertFalse(self.plan()['plans'])

    def test_missing_program_time_not_invented(self):
        self.c['programs'][0]['start'] = None
        self.r['allow_unknown'] = True
        self.assertTrue(all(p['program']['id'] != 'MOCK-PROGRAM-A' for p in self.plan()['plans']))

    def test_provider_failure_not_empty_result(self):
        self.c['source_status']['spaces'] = 'unavailable'
        self.assertEqual(self.plan()['status'], 'source_unavailable')
        self.assertEqual(search_catalog(self.c, 'spaces')['status'], 'source_unavailable')

    def test_unknown_cost_not_free(self):
        self.c['spaces'][0]['session_fee_krw'] = None
        self.r.update(allow_unknown=True, budget_krw=0)
        p = self.plan()['plans'][0]
        self.assertIsNone(p['total_fee_krw'])
        self.assertEqual(p['known_fee_krw'], 0)
        self.assertFalse(p['budget_confirmed_from_data'])

    def test_hours_and_closure(self):
        self.c['libraries'][0]['hours']['end'] = '14:00'
        self.assertFalse(self.plan()['plans'])
        self.c['libraries'][0]['hours']['end'] = '18:00'
        self.c['libraries'][0]['closed_dates'] = ['2026-11-07']
        self.assertFalse(self.plan()['plans'])

    def test_previous_day_not_current(self):
        p = self.plan()['plans'][0]
        self.assertEqual(p['book']['availability']['status'], 'previous_day_available')
        self.assertTrue(any('하루 전' in x for x in p['warnings']))

    def test_input_validation(self):
        changes = [{'date': '2026-02-30'}, {'group_size': True}, {'group_size': 0}, {'budget_krw': -1},
                   {'start_time': '25:00'}, {'end_time': '12:00'}, {'limit': 100}, {'member_id': 'fictional'}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                make_plan(self.c, {**self.r, **change})

    def test_past_date(self):
        self.r['date'] = '2026-11-03'
        with self.assertRaises(ValueError):
            self.plan()

    def test_unknown_region_is_not_national_fallback(self):
        self.r['region'] = 'NOT-LOADED'
        self.assertFalse(self.plan()['plans'])

    def test_no_network_from_planner(self):
        with patch('socket.create_connection', side_effect=AssertionError('network forbidden')):
            self.assertTrue(self.plan()['plans'])


class NaruTests(unittest.TestCase):
    def setUp(self):
        self.key = secrets.token_urlsafe(32)
        self.seen = []
        def handler(req):
            self.seen.append(req)
            if req.url.path.endswith('srchBooks'):
                body = {'docs': [{'doc': {'isbn13': '0000000000001', 'bookname': '가상 응답 도서'}}]}
            elif req.url.path.endswith('libSrchByBook'):
                body = {'libs': [{'lib': {'libCode': '000001', 'libName': '가상 응답 도서관'}}]}
            else:
                body = {'result': {'hasBook': 'Y', 'loanAvailable': 'N'}}
            return httpx.Response(200, json={'response': body})
        self.transport = httpx.MockTransport(handler)
        self.client = NaruClient(enabled=True, approved=True, key=self.key, transport=self.transport)

    def test_disabled_by_default(self):
        client = NaruClient(key=self.key, transport=self.transport)
        self.assertEqual(client.search_books('우주')['status'], 'disabled')
        self.assertFalse(self.seen)

    def test_both_approval_gates_needed(self):
        c = NaruClient(enabled=True, approved=False, key=self.key, transport=self.transport)
        self.assertEqual(c.search_books('우주')['status'], 'disabled')

    def test_search_whitelist_no_key_leak(self):
        out = self.client.search_books('우주')
        self.assertEqual(out['items'][0]['title'], '가상 응답 도서')
        self.assertNotIn(self.key, json.dumps(out))
        self.assertEqual(self.seen[0].url.host, 'data4library.kr')

    def test_cache_and_process_cap(self):
        self.client._max_calls = 1
        first = self.client.search_books('우주')
        second = self.client.search_books('우주')
        self.assertEqual(first['queried_at'], second['queried_at'])
        self.assertTrue(second['cache_hit'])
        self.assertEqual(self.client.search_books('역사')['status'], 'local_limit_reached')
        self.assertEqual(len(self.seen), 1)

    def test_status_is_previous_day(self):
        out = self.client.book_status('0000000000001', '000001')
        self.assertTrue(out['has_book'])
        self.assertFalse(out['loan_available_previous_day'])
        self.assertIsNone(out['actual_upstream_update_time'])
        self.assertIn('하루 전', out['warning'])

    def test_holdings_normalization(self):
        out = self.client.holding_libraries('0000000000001', '00')
        self.assertEqual(out['items'][0]['library_code'], '000001')
        self.assertFalse(out['reservation_performed'])

    def test_error_does_not_expose_key(self):
        def fail(req):
            raise httpx.ReadTimeout('upstream failure', request=req)
        c = NaruClient(enabled=True, approved=True, key=self.key, transport=httpx.MockTransport(fail))
        out = c.search_books('우주')
        self.assertEqual(out['status'], 'provider_error')
        self.assertNotIn(self.key, json.dumps(out))

    def test_no_redirect_following(self):
        c = NaruClient(enabled=True, approved=True, key=self.key,
                       transport=httpx.MockTransport(lambda req: httpx.Response(302, headers={'Location': 'https://example.invalid'})))
        self.assertEqual(c.search_books('우주')['status'], 'provider_error')

    def test_invalid_parameters_before_network(self):
        with self.assertRaises(ValueError):
            self.client.holding_libraries('bad', 'bad')
        self.assertFalse(self.seen)


class ImportTests(unittest.TestCase):
    def row(self):
        return dict(zip(COLUMNS, ['가상 문화강좌', '가상 도서관', '가족', '2026-10-01', '2026-11-01', '2026-11-07', '2026-11-28', '20']))

    def test_csv_does_not_invent_times_vacancies_or_daily_sessions(self):
        out = normalize_rows([self.row()], {}, '2026-11-04', synthetic=True)
        p = out['programs'][0]
        self.assertIsNone(p['date'])
        self.assertIsNone(p['start'])
        self.assertIsNone(p['remaining_seats'])
        self.assertEqual(p['capacity'], 20)
        self.assertFalse(out['human_reviewed'])
        self.assertIsNone(p['library_id'])

    def test_one_day_still_no_time_invention(self):
        row = self.row()
        row['교육종료일'] = row['교육시작일']
        p = normalize_rows([row], {}, '2026-11-04', synthetic=True)['programs'][0]
        self.assertEqual(p['date'], '2026-11-07')
        self.assertIsNone(p['start'])

    def test_bad_columns_rejected(self):
        with self.assertRaises(ValueError):
            normalize_rows([{'title': 'fictional'}], {}, '2026-11-04')

    def test_bad_dates_rejected(self):
        row = self.row()
        row['교육시작일'] = '2026-02-30'
        with self.assertRaises(ValueError):
            normalize_rows([row], {}, '2026-11-04')


if __name__ == '__main__':
    unittest.main()
