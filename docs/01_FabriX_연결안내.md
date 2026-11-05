# FabriX 연결 안내: 서버 호스팅판 (0.3)

**확인 범위:** 11. 5. 사용자의 FabriX 화면(MCP Server 생성 ①·② 전체, Agent 생성 ①~④)을 읽고 맞춘 안내입니다. ② 화면 아래쪽에는 Executable File·Port Number(기본 8080)·Personal Access Token 칸이 있었습니다. 연결 테스트·배포·등록 신청·모델 답은 아직 실행하지 않았습니다. 호스팅 도움말 본문은 확인하지 못했습니다.

## 1. 전체 흐름

1. 이 저장소를 GitHub 공개 저장소 `library-walk-mcp`에 올립니다(비밀값 없음).
2. FabriX: AI Lab → Tools → MCP Server → 「MCP Server 생성」 → 생성 방식 **서버 호스팅**.
3. ① 기본 정보: `02_Agent_기본정보.md` 끝의 MCP Server 글(이름·설명·사용 방법).
4. ② Repository 연결: 아래 입력표 → 「연결 테스트」 → ③ 서버 배포 → ④ Tool 확인 및 테스트 → ⑤ 등록 신청.
5. 승인 뒤 Agent 「도서관 산책」 → ④ Tools 설정 → 「MCP Server 추가」 → 이 서버 선택.

같은 Lab 안에서 Agent와 MCP Server를 만듭니다. 「연결 테스트 후에는 생성 방식을 변경할 수 없습니다」는 안내가 있으므로 생성 방식(서버 호스팅)을 먼저 확정합니다.

## 2. ② Repository 연결 입력표

| 항목 | 넣을 값 | 비고 |
| --- | --- | --- |
| 시스템 인증 방식 | **「인증 사용하지 않음」**(기본 권장) | 서버가 환경변수로 같은 값을 받을 수 있다고 확인되면 「직접 입력」 파라미터 `Authorization`, 값 `Bearer {긴 임의 값}`으로 바꿀 수 있음. 값은 저장소·채팅에 넣지 않음 |
| 사용자 인증 방식 | **미사용** | 사용자별 Key를 쓰지 않음 |
| Repository URL | `https://github.com/{계정}/library-walk-mcp` | 공개 저장소 |
| Branch | `main` | |
| Executable File | `./main.py` | 11. 5. 화면 확인: 「실행 파일명과 상대 경로」 |
| Port Number | `8080` | 화면 기본값 8080. 서버 기본 포트도 8080(`PORT` 환경변수가 있으면 그 값) |
| Personal Access Token | GitHub에서 발급한 토큰 | **FabriX 화면에만 입력.** 저장소·채팅·문서에 넣지 않음. 새 저장소 읽기 권한(Contents: Read)만 있는 fine-grained 토큰 권장 |
| 전송 방식(칸이 있으면) | Streamable HTTP, 경로 `/mcp` | SSE면 `MCP_TRANSPORT=sse`, 경로 `/sse`. stdio도 지원 |
| 환경변수(칸이 있으면) | 없어도 동작 | 필요 시 `MCP_TRANSPORT`, `MCP_ACCESS_TOKEN`, `MCP_ALLOWED_HOSTS` |

「시스템 인증 방식」이 FabriX→서버 인증인지 저장소 접근 인증인지는 화면 문구만으로 확정하지 못했습니다. 공개 저장소이므로 저장소 접근 인증은 필요하지 않습니다. 「직접 입력」의 파라미터·값이 필수(*)로 표시되어 「인증 사용하지 않음」을 고를 수 없으면 멈추고 화면을 확인합니다.

## 3. ④ Tool 확인 및 테스트에서 볼 것

- 도구 6개가 보이는지. `get_data_status`를 실행해 `catalog_mode: mock`, `synthetic: true`, `hosting_mode: fabrix_hosted`, `auth_mode`가 나오는지.
- `search_books_live`를 실행하면 `status: disabled`가 나와야 합니다(정보나루 꺼짐).
- `make_visit_plan`에 `data/example_request.json`의 내용을 `request`로 넣으면 `MOCK-LIB-A` 코스와 `booking_performed: false`가 나와야 합니다.

## 4. Agent에 붙인 뒤 첫 대화

1. 「도구로 현재 연결된 자료의 상태를 확인해 줘.」 → 가상 자료·DEMO·외부 실호출 꺼짐을 설명해야 합니다.
2. 예시 질문 1 → 가상도서관 A, 1코스 책 2권, 2코스 13~14시, 3코스 14~15시(희망 시간), 확인된 비용 5,000원. 이 값은 가상 시험자료의 계산값입니다.
3. 예시 질문 3 → 미확인 비용을 무료로, 희망 시간을 예약 가능으로 바꾸지 않아야 합니다.

## 5. 자주 생기는 실패

| 현상 | 확인할 것 |
| --- | --- |
| 연결 테스트 실패 | 저장소 공개 여부, URL 오타, Branch 이름, FabriX가 GitHub에 접근할 수 있는 망 정책 |
| 빌드 실패 | 플랫폼이 쓰는 방식(Dockerfile / requirements.txt)과 Python 버전. 로그를 캡처 |
| 서버가 떴는데 도구가 안 보임 | 전송 방식·경로(`/mcp` 또는 `/sse`)와 플랫폼 기대값 불일치 |
| 401 | 서버에 토큰이 설정됐는데 FabriX가 헤더를 보내지 않음. 둘을 맞추거나 토큰을 빼고 플랫폼 관리로 |
| 421 | `MCP_ALLOWED_HOSTS`를 설정했는데 플랫폼 내부 Host와 다름. 설정을 비우면 점검 생략 |
| 413·429 | 요청 크기·빈도 제한. 정상 보호 동작 |
| `disabled` | 정보나루 도구가 꺼진 상태. 장애가 아님 |

## 6. 예비 경로: 외부 서버 연결

FabriX 호스팅이 불가하면 「외부 서버 연결」로 메타 정보만 등록하고 운영자가 관리하는 HTTPS 서버를 둡니다. 그때는 `server.py` 엄격 모드(토큰·허용 호스트 필수)와 `probe_mcp.py`를 사용합니다. 이 경로는 공개 HTTPS·DNS·TLS·호스팅 정책 확인이 추가로 필요합니다.
