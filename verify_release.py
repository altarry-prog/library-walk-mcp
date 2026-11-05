"""Reproduce local checks and build a secrets-free release. No live data APIs."""
from __future__ import annotations
import ast
import csv
import hashlib
import importlib.metadata as md
import io
import json
import re
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from planner import load_catalog, make_plan, PlanRequest

ROOT = Path(__file__).resolve().parent
VERSION = '0.2'
CHECKS = []


def check(label, condition):
    if not condition:
        raise AssertionError(label)
    CHECKS.append(label)
    print('PASS', label)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def execute(args, logfile, timeout=120):
    run = subprocess.run(args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
    (ROOT / logfile).write_text(run.stdout, encoding='utf-8')
    if run.returncode:
        print(run.stdout[-6000:])
        raise SystemExit(f'FAILED: {logfile}')
    return run.stdout


runtime = ['server.py', 'planner.py', 'providers.py', 'import_bucheon_csv.py', 'probe_mcp.py',
           'make_demo_data.py', 'verify_server.py', 'verify_release.py', 'tests/test_core.py',
           'tests/test_configuration.py', 'requirements.txt', 'requirements.lock.txt',
           'data/demo_catalog.json', 'data/example_request.json']
code_before = {name: digest(ROOT / name) for name in runtime}
for name in runtime:
    if name.endswith('.py'):
        ast.parse((ROOT / name).read_text(encoding='utf-8'))
check('Python syntax', True)
for line in (ROOT / 'requirements.lock.txt').read_text().splitlines():
    name, version = line.split('==')
    check('dependency ' + name, md.version(name) == version)
subprocess.run([sys.executable, '-m', 'pip', 'check'], cwd=ROOT, check=True)
unit_log = execute([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], 'docs/test_core_current.log')
unit_count = int(re.search(r'Ran (\d+) tests', unit_log).group(1))
check('unit tests', bool(re.search(r'\nOK\s*$', unit_log)))
execute([sys.executable, 'verify_server.py'], 'docs/test_mcp_current.log')
protocol = json.loads((ROOT / 'docs/server-verification.json').read_text())
check('HTTP and SSE local protocol', protocol['checks_passed'] == 26)
check('tested source hashes unchanged', code_before == {name: digest(ROOT / name) for name in runtime})

observed = json.loads((ROOT / 'docs/mcp_tools_observed.json').read_text())
tool_names = {tool['name'] for tool in observed}
prompt = (ROOT / 'docs/03_Custom_지시문.txt').read_text(encoding='utf-8')
check('prompt names match actual tools', len(tool_names) == 6 and all(name in prompt for name in tool_names))
prompt_json = next(json.loads(line) for line in prompt.splitlines() if line.startswith('{"request":'))
request = PlanRequest.model_validate(prompt_json['request'])
first = make_plan(load_catalog(), request)
check('prompt example matches executable fixture', first['plans'][0]['library']['id'] == 'MOCK-LIB-A' and first['plans'][0]['total_fee_krw'] == 5000)
base = request.model_dump(mode='json')
cases = {
    'F01': base,
    'F02': {**base, 'start_time': '14:00', 'needs': []},
    'F03': {**base, 'budget_krw': 0, 'allow_unknown': True},
    'F04': {**base, 'group_size': 7},
}
answers = {key: make_plan(load_catalog(), value) for key, value in cases.items()}
check('F02 changed conditions', answers['F02']['plans'][0]['library']['id'] == 'MOCK-LIB-B')
check('F03 unknown fee not free', answers['F03']['plans'][0]['total_fee_krw'] is None)
check('F04 no invented match', answers['F04']['plans'] == [])
(ROOT / 'docs/구조화입력_시험.json').write_text(json.dumps({'scope': '구조화한 입력의 로컬 시험. 자연어 모델 시험이 아닙니다.', 'cases': cases}, ensure_ascii=False, indent=2), encoding='utf-8')
examples = (ROOT / 'docs/02_Agent_기본정보.md').read_text(encoding='utf-8')
check('four UI examples', len(re.findall(r'^\d\. ', examples, flags=re.M)) == 4)
rows = list(csv.reader(io.StringIO((ROOT / 'docs/플랫폼시험기록_빈양식.csv').read_text(encoding='utf-8-sig'))))
check('platform records remain unexecuted', len(rows) == 7 and all(len(row) == len(rows[0]) and row[1] == '미실행' for row in rows[1:]))
for name in ['data/approved_catalog.template.json', 'data/library_map.template.json', 'data/provider_registry.json']:
    json.loads((ROOT / name).read_text(encoding='utf-8'))
try:
    load_catalog(ROOT / 'data/approved_catalog.template.json')
    empty_rejected = False
except ValueError:
    empty_rejected = True
check('unreviewed real-data template cannot activate', empty_rejected)
registry = json.loads((ROOT / 'data/provider_registry.json').read_text())
check('registry does not claim live verification', all(p['live_verified'] is False and p['default_enabled'] is False for p in registry['providers']))
check('no credential assignments in example config', not re.search(r'^(?:MCP_ACCESS_TOKEN|NARU_KEY)\s*=', (ROOT / '.env.example').read_text(), flags=re.M))
check('Docker runtime source files present', all((ROOT / name).is_file() for name in ['server.py', 'planner.py', 'providers.py', 'requirements.lock.txt']))
check('compose uses host credential reference', '${MCP_ACCESS_TOKEN:?' in (ROOT / 'compose.yaml').read_text())

third = ['# 외부 패키지 메타데이터', '', '설치된 배포판의 메타데이터입니다. 전체 라이선스 검토·법률 검토·취약점 검사를 대신하지 않습니다.', '', '| 패키지 | 설치 판 | 라이선스 메타데이터 |', '| --- | --- | --- |']
for name in ['mcp', 'uvicorn', 'httpx', 'python-dotenv', 'tzdata']:
    meta = md.metadata(name)
    license_text = meta.get('License-Expression') or meta.get('License') or '메타데이터에서 확인 못 함'
    license_text = ' '.join(license_text.split())[:160].replace('|', '/')
    third.append(f'| {name} | {md.version(name)} | {license_text} |')
third += ['', '전이 의존성 전체 판은 requirements.lock.txt에 고정했습니다. 실제 배포 전 각 배포판의 LICENSE와 호스팅·API 이용조건을 함께 확인합니다.', 'Docker 기반 이미지와 Caddy의 라이선스·지원 정책·이미지 고정도 배포 단계에서 확인해야 합니다.']
(ROOT / 'THIRD_PARTY.md').write_text('\n'.join(third) + '\n', encoding='utf-8')
now = datetime.now(ZoneInfo('Asia/Seoul')).isoformat(timespec='seconds')
verification = f'''# 0.2 검증 기록

확인 시각: {now}. Python {sys.version.split()[0]}, 이 작업의 로컬 Linux 환경.

## 실제 실행

- 현재 코드판의 단위·구성 시험 **{unit_count}개 시험 메서드 통과**. 일부 메서드 안에 여러 조건이 있습니다. 실제 이용 정확도 수치가 아닙니다.
- 로컬 Streamable HTTP/SSE 접속·인증·도구 점검 **{protocol['checks_passed']}개 통과**.
- 구문·고정 의존성·지시문 예시·도구 이름·빈 자료 양식·시험기록 양식·설정의 정적 대조 통과.
- 시험 전후 실행 코드·시험 코드·주요 자료의 SHA-256이 같은지 확인했습니다. 이 판의 해시는 MANIFEST_0.2.json에 있습니다.
- 대표 F01~F04의 구조화한 입력을 실제 로직에 넣어 대조했습니다. 자연어 질문에 대한 Solar Pro 4 답을 시험한 것이 아닙니다.
- 정보나루는 모의 응답만 사용했습니다. 실제 정보나루·행사·공간 API·모델 호출은 없습니다.

## 아직 실행하지 않은 것

- Docker 이미지 빌드·컨테이너 실행·Caddy 공개 HTTPS·DNS·외부 접속.
- 실제 FabriX MCP 등록·인증 호환성·Agent 유형·Solar Pro 4 도구 사용·Store 배포·심사위원 열람.
- 실제 CSV 전체 검증·실제 API 응답·공간 빈 시간 조회·전국 자료 적재.
- 운영 환경 침투 시험·기관 보안 승인·현장 효과 측정·최종 제출.

Docker 구성은 파일 간 정적 대조만 했습니다. 배포 성공·정부 환경 호환성·보안 인증을 뜻하지 않습니다. 외부 HTTPS용 probe_mcp.py는 주소 검증 함수를 시험했으나 실제 공개 서버에 실행하지 않았습니다.

## 읽을 기록

- docs/test_core_current.log
- docs/test_mcp_current.log
- docs/server-verification.json
- docs/mcp_tools_observed.json
- docs/실제_MCP_모의응답.json

플랫폼시험기록_빈양식.csv는 모든 행이 미실행 상태입니다. 파일·지시문 작성 완료와 실제 플랫폼 적용을 구분합니다.
'''
(ROOT / 'VERIFICATION.md').write_text(verification, encoding='utf-8')

files = ['README.md', 'VERIFICATION.md', 'THIRD_PARTY.md', 'server.py', 'planner.py', 'providers.py',
         'import_bucheon_csv.py', 'make_demo_data.py', 'verify_server.py', 'probe_mcp.py', 'verify_release.py',
         'requirements.txt', 'requirements.lock.txt', 'Dockerfile', 'compose.yaml', 'Caddyfile',
         '.env.example', '.gitignore', '.dockerignore', 'tests/test_core.py', 'tests/test_configuration.py',
         'data/demo_catalog.json', 'data/example_request.json', 'data/approved_catalog.template.json',
         'data/library_map.template.json', 'data/provider_registry.json',
         'docs/01_FabriX_연결안내.md', 'docs/02_Agent_기본정보.md', 'docs/03_Custom_지시문.txt',
         'docs/04_시범판_실행과제출.md', 'docs/05_전국확장_상세설계.md', 'docs/06_자료확보와승인.md',
         'docs/07_변경내역.md', 'docs/공개자료_확인메모.md', 'docs/플랫폼시험기록_빈양식.csv',
         'docs/test_core_current.log', 'docs/test_mcp_current.log', 'docs/server-verification.json',
         'docs/mcp_tools_observed.json', 'docs/실제_MCP_모의응답.json', 'docs/구조화입력_시험.json']
check('archive allowlist excludes credentials and environments', all(Path(name).name != '.env' and not any(part in ('.venv', '.tmp', '__pycache__') for part in Path(name).parts) for name in files))
check('all deliverables exist', all((ROOT / name).is_file() for name in files))
manifest = {'version': VERSION, 'checked_at_kst': now, 'status': 'local_tested_fabrix_application_pending',
            'unit_test_methods_passed': unit_count, 'local_protocol_checks_passed': protocol['checks_passed'],
            'local_and_static_checks': CHECKS,
            'source_hashes_at_test': code_before,
            'reference_repository': {'commit_short': 'bb4da9b', 'commit_time_kst': '2026. 11. 4. 17:11', 'modified': False},
            'external_apis_called': False, 'fabrix_connected': False, 'public_https_deployed': False,
            'files': [{'path': name, 'bytes': (ROOT / name).stat().st_size, 'sha256': digest(ROOT / name)} for name in files]}
manifest_path = ROOT / 'MANIFEST_0.2.json'
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
files.append('MANIFEST_0.2.json')
archive = ROOT.parent / '책이지_문화생활_FabriX실행준비와_전국설계_v0.2_2026-11-04.zip'
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
    for name in files:
        z.write(ROOT / name, 'library-life-fabrix/' + name)
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    assert len(z.namelist()) == len(files)
    assert all(z.read('library-life-fabrix/' + name) == (ROOT / name).read_bytes() for name in files)
print('RELEASE_VERIFIED', unit_count, 'unit methods;', protocol['checks_passed'], 'local protocol checks')
print('ARCHIVE_INTEGRITY_PASS', len(files), 'files;', archive.stat().st_size, 'bytes')
print('SHA256', digest(archive))
print('BUNDLE', archive.name)
