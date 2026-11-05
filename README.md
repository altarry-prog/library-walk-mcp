# 도서관 산책 읽기전용 MCP 서버 (library-walk-mcp)

「2026 공공 AI 대전환 챌린지」 세션3 출품작 **「도서관 산책」** Agent가 쓰는 읽기 전용 도구 서버입니다. FabriX의 MCP Server 「서버 호스팅」(Repository 연결 → FabriX가 직접 배포)에 올리기 위한 Python 저장소입니다. 작성 2026. 11. 5. 판 0.3.

**이 저장소는 코드와 가상 자료만 담습니다.** 인증키·개인정보·내부 주소는 없습니다. 실제 도서관의 공식 서비스가 아니며 신청·예약·결제 기능이 없습니다.

## 무엇을 하나

담당자가 말한 조건(날짜·시간·인원·예산·머물 시간·필요 시설·취향)에 맞춰, **같은 시설 안에서 읽을 책(1코스) → 들러 볼 강좌·행사(2코스) → 머물 자리(3코스)**가 시간이 이어지는 하루 코스 후보를 대조합니다. 모델(Solar Pro 4)은 말을 알아듣고 설명하고, 시간·인원·비용·시설 조건 대조는 이 서버의 코드가 합니다.

도구 6개(모두 읽기 전용):

| 도구 | 하는 일 | 기본 상태 |
| --- | --- | --- |
| `get_data_status` | 자료 모드(모의 여부)·범위·기준일·연결 상태 | 사용 |
| `search_resources` | 등록 자료 검색(`kind`: books / programs / spaces) | 사용 |
| `make_visit_plan` | `request` 객체로 조건 대조, 코스 후보 반환 | 사용 |
| `search_books_live` | 정보나루 서지 검색 | **꺼짐** |
| `find_holding_libraries_live` | 정보나루 소장관 조회 | **꺼짐** |
| `check_book_status_live` | 정보나루 하루 전 대출 상태 | **꺼짐** |

현재 자료는 `data/demo_catalog.json`의 **가상 자료**(지역 `DEMO`, 가상 일정 2026-11-07)입니다. 정원은 남은 자리가 아니고, 공간 시간은 희망 시간이며, 비용 미상은 무료가 아닙니다. 결과 JSON의 `condition_status`, `slot_evidence`, `total_fee_krw`(null 가능) 표시를 그대로 전달해야 합니다.

## 실행 방법

### FabriX 서버 호스팅(이 저장소의 목적)

FabriX ② Repository 연결 화면(11. 5. 확인)의 입력값: **Executable File `./main.py`**, **Port Number `8080`**, Branch `main`, Repository URL, 그리고 GitHub **Personal Access Token**(FabriX 화면에만 입력, 저장소·채팅에 넣지 않음). 의존성은 `requirements.txt`, 컨테이너 빌드용 `Dockerfile`(python:3.12-slim)도 있습니다. 환경변수가 하나도 없어도 뜨도록 만들었습니다.

| 환경변수 | 호스팅 기본값 | 뜻 |
| --- | --- | --- |
| `PORT` | 8080 | 수신 포트. FabriX ② 「Port Number」와 같게 둠 |
| `HOST` | 0.0.0.0 | 바인딩 주소 |
| `MCP_TRANSPORT` | `streamable-http`(경로 `/mcp`) | `sse`(경로 `/sse`), `stdio`도 가능 |
| `MCP_ACCESS_TOKEN` | 없음 → 플랫폼 관리 인증 | 설정하면 `Authorization: Bearer <값>` 필수 |
| `MCP_ALLOWED_HOSTS` | 없음 → Host 점검 생략(플랫폼 관리) | 설정하면 그 호스트만 허용. `*` 금지 |
| `REQUESTS_PER_MINUTE` | 60 | 요청 제한(1~600) |
| `ALLOW_LIVE_CALLS`, `EXTERNAL_LINK_APPROVED`, `NARU_KEY` | 꺼짐/없음 | 셋 다 있어야 정보나루 도구가 켜짐. 모의 자료와는 함께 켤 수 없음 |

`/health`는 인증 없이 `mode`, `auth_mode`만 돌려줍니다. 정보나루·모델 호출은 없습니다.

**인증에 대한 설명.** 이 서버는 OAuth 서버가 아닙니다. 호스팅 모드에서 `MCP_ACCESS_TOKEN`이 없으면 Bearer 검사를 하지 않고 FabriX가 접근을 통제한다고 봅니다(`auth_mode: platform_managed`). FabriX의 ② 「시스템 인증 방식」을 「직접 입력」으로 하고 서버에 같은 값을 환경변수로 줄 수 있으면 Bearer 검사가 켜집니다(`auth_mode: bearer`). 어느 쪽이든 `get_data_status`와 `/health`에 현재 모드가 표시됩니다. 공개 인터넷에 무인증으로 노출하는 용도로 쓰지 않습니다.

### 로컬 엄격 모드(개발·시험)

`python server.py`는 루프백 주소에서만 뜨고, `MCP_ACCESS_TOKEN`(공백 없는 ASCII 32~256자)과 `MCP_ALLOWED_HOSTS`가 없으면 시작하지 않습니다.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt     # 재현용 고정판: requirements.lock.txt (Python 3.11 이상)
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python verify_server.py
.venv/bin/python planner.py --request data/example_request.json
```

`verify_server.py`는 시험 인증값을 메모리에서 만들어 로컬 서버(엄격 모드·호스팅 모드·stdio)를 띄우고 확인한 뒤 종료합니다. 외부 호출은 없습니다.

Python은 서버·계획 코드 기준 3.10 이상, 고정 의존성 판은 3.11 이상이며 실제 시험은 3.14에서 했습니다. `probe_mcp.py`(운영자용 외부 점검 도구)만 3.11 이상이 필요합니다.

## 파일

| 파일 | 역할 |
| --- | --- |
| `main.py` | FabriX 호스팅 진입점 |
| `server.py` | MCP 서버 본체(엄격 모드 진입점 포함) |
| `planner.py` | 조건 대조·코스 생성(결정적, 네트워크 없음) |
| `providers.py` | 정보나루 어댑터(기본 꺼짐, 승인 게이트·호출 상한) |
| `data/` | 가상 자료, 예시 입력, 실제 자료용 빈 양식, 제공기관 상태표 |
| `tests/` | 단위·구성·호스팅 시험 |
| `verify_server.py` | 로컬 접속·인증·도구 통합시험 |
| `verify_hosted.py` | 시험·정적 대조·해시·업로드 묶음 생성 |
| `import_bucheon_csv.py`, `make_demo_data.py` | 공개 CSV 검토용 변환, 가상 자료 생성 |
| `probe_mcp.py` | 운영자가 배포된 HTTPS 주소를 점검할 때 쓰는 도구 |
| `docs/` | FabriX 입력 글, 지시문, 연결 안내, 시연·제출 계획, 전국 확장 설계 |

## 솔직한 범위

- 가상 자료입니다. 실제 도서관·행사·빈자리가 아닙니다.
- 같은 시설의 프로그램 뒤 공간 이용만 대조합니다. 시설 간 이동, 자동 예약, 장기 일정은 없습니다.
- 정보나루 대출 상태는 하루 전 기준입니다(도구가 켜진 경우에만).
- 문화프로그램·공간의 실시간 API 연결은 미구현이며 `docs/05_전국확장_상세설계.md`의 설계입니다.
- FabriX의 실제 빌드·배포·연결·모델 답은 이 저장소만으로 완료되지 않습니다. 확인 기록은 `VERIFICATION.md`를 봅니다.
