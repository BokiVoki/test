# 프로젝트 메모리 — 희은의 개인 시스템

> 이 문서는 "항상 기억하는 뇌"예요. 새 Claude 세션은 이 파일을 자동으로 읽어요.
> 무언가 바뀌면 이 문서도 같이 업데이트해요.

## 한눈에 — 지금 뭐가 돌아가고 있나

| 시스템 | 무엇 | 어디 사는가 | 상태 |
|---|---|---|---|
| **일정 비서봇** | 텔레그램 봇. 할일/알람/리마인더/영양제 재고/생리주기/브리핑 | Railway (BOT_ROLE 미설정) + Google Sheets | ✅ 운영 중 |
| **인박스봇** | 텔레그램 봇. 링크/생각/사진/쇼핑/책을 옵시디언 볼트로 자동 정리 | Railway (BOT_ROLE=inbox) + GitHub 볼트 `BokiVoki/obsidian-vault`(Private) | ✅ 운영 중 (Books/Inbox/Shopping 폴더에 커밋 중). 남은 건 옵시디언 앱에서 볼트 열기(Obsidian Git) |
| **하루(Haru) 웹앱** | 개인 업무 할일 대시보드 (미니멀·딥그린) | Netlify + Supabase | ✅ 운영 중 (`webapp/index.html`) |

---

## 1. 일정 비서봇 (`bot/`)

- **엔트리**: `python start.py` → `BOT_ROLE` 미설정이면 `bot.main` 실행
- **저장소**: Google Sheets (`SPREADSHEET_ID`) — Archive / Todos / Reminders / Memos / Inventory / IntakeLog / Cycle 워크시트
- **LLM**: Anthropic API (`ANTHROPIC_API_KEY`) — Haiku로 자연어 파싱
- **주요 기능**
  - 투두/알람: 자연어로 추가, 반복(daily/weekly/monthly/after:N분), 재알람, 완료
  - 아카이브: 책/웹툰/영화 등 기록, `/검색` 인라인 버튼, 사진 메모(Drive)
  - 영양제 재고 + 복용 기록, 생리주기 추적 + 단계별 알림(PMS 등)
  - 브리핑: 아침/저녁/밤 (매일 고정 알림은 브리핑에서 제외됨)
  - **하루앱 주머니 던지기**: `앱 <내용>` → 하루 웹앱 주머니(Supabase todos, `bucket='inbox'`)로 바로 저장 (`bot/haru_app.py`). 환경변수: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `HARU_OWNER_ID`, (선택 `HARU_WORKSPACE`, 기본 '일')
  - **하루앱 11시 체크인**: 매일 11:00(KST) 전용 메시지로 오늘 추천(우선순위순 `haru_app.list_open`)+마감(`list_due`)을 쏨 (`send_haru_daily`, `scheduler.send_haru_daily_job`, main.py UTC 02:00). 즉시 확인: `추천`/`체크인` 텍스트. 마감만: `마감` 텍스트 또는 `/deadlines`. (브리핑엔 합치지 않음)
- **최근 수정/버그픽스**
  - `cancel_alarms` 오발동 방지 (프롬프트 강화)
  - "모든 알람 취소"에 되돌리기 버튼 추가
  - 체크인 기능 **삭제됨**
  - 날짜/요일 파싱 정확도 개선 (요일 문자열 전달)
  - 브리핑에서 `repeat=daily` 고정 항목 숨김
  - PMS 단계 알림 중복 발송 버그 → Cycle 시트 note에 `[notified:...]` 영구 기록으로 해결

## 2. 인박스봇 (`inbox_bot/`)

- **엔트리**: `python start.py` + `BOT_ROLE=inbox` → `inbox_bot.main`
- **개념**: 명령어 없이 뭐든 던지면 자동으로 옵시디언 볼트(GitHub repo)에 `.md` 노트로 정리. 명령어일 때만 다른 처리.
- **동작**
  - 링크 → 본문 fetch + Claude(Sonnet) 요약 + "왜 저장" 한 줄. 인스타 등 못 읽는 사이트는 안내 문구
  - 생각/텍스트 → 아이디어 노트
  - 사진 → 볼트 `Inbox/attachments/`에 직접 커밋 + `![[...]]` 임베드
  - 쇼핑("살까/얼마") → `Shopping/` 위시리스트 (비전으로 상품/가격 읽음)
  - 책("읽어볼까/책추천") → `Books/` 읽고싶은 책 (표지 제목/저자 읽음, 여러 권이면 목록)
  - `✍️ 내 생각` 버튼 → 사용자 말에서 태그 재추출, 캡션 우선
  - 명령어: `/today` `/find` `/shopping` `/books`
  - 비전 모델: `INBOX_VISION_MODEL` (기본 sonnet, opus로 올릴 수 있음)
- **연결 상태**: ✅ 이미 연결됨. 볼트 = `BokiVoki/obsidian-vault`(Private), Railway 두 번째 서비스(`BOT_ROLE=inbox`)에서 커밋 중. env: `INBOX_BOT_TOKEN`, `VAULT_REPO`, `GITHUB_TOKEN`, `BOT_ROLE=inbox`, (공유: `ANTHROPIC_API_KEY`, `TELEGRAM_USER_ID`)
  - **남은 것**: 옵시디언 앱에서 그 볼트를 열어 보기 (GitHub Desktop으로 clone → 폴더를 볼트로 열기 → 자동 업데이트는 **Obsidian Git** 플러그인)

## 3. 하루(Haru) 웹앱 (`webapp/index.html`)

- **스택**: 단일 HTML 파일 + Supabase (DB+Auth) + Netlify 호스팅. 빌드 없음.
- **디자인**: 미니멀·샤프, 딥 포레스트 그린 단일 포인트(`#2C6A46`/dark `#5FB088`), 엑셀st 표, 라이트/다크.
- **탭**: 대시보드 / 주머니·프로젝트 / 캘린더 / 메모 / 완료함 / 순서도
- **전체 검색**: 헤더 아래 검색바(`#q`) — 모든 탭/워크스페이스 걸쳐 할일(제목·하위항목·프로젝트)+메모 검색. **띄어쓰기 무시**(`norm()`=공백 제거+소문자). 결과 클릭 시 해당 위치로 이동. (`runSearch`, `#searchView`)
  - **대시보드**: 빠른 담기 + 오늘의 집중(자동 우선순위 + 가용시간) + 마감 임박 + 오늘 흐름 토글. 진짜 할일만. 빠른담기 옆 **오늘** 버튼=오늘의 집중 바로 담기(Shift+Enter도). 행 hover **🔁**=고정업무(none→매일→매주(요일)→매월(일)→none), 프로젝트 행 hover **오늘** 토글, 하위 항목 hover **＋날짜**(`subdue`, sub.due).
  - **주머니·프로젝트**: 주머니(inbox) 분류(오늘/프로젝트/나중/메모/버림) + 프로젝트 아코디언
  - **캘린더**: 월 그리드, 드래그로 마감일 변경
  - **메모**: 애플 메모st, 폴더, 할일 연결(📝)
  - **완료함**: 체크=완료(찍 긋고 그 자리 유지 + 완료시점 `doneat` 기록) → "완료함으로" 버튼/일괄정리로 `archived=true` → 완료함 탭에서 완료시점 표시 + 되돌리기. 반복 항목은 예외(lastdone).
  - **프로젝트**: 헤더 ⠿ 핸들 드래그로 순서 변경(→ `settings.projOrder[ws]`), ✕로 삭제(할일은 주머니로 이동, 삭제 아님). 프로젝트 안 **할일 행 ⠿ 드래그로 순서 변경**(→ `todos.sort`, `reorderTodo`), **하위 항목 ⠿ 드래그로 순서 변경**(subs 배열 재정렬, `reorderSub`).
  - **하위 항목(서브태스크)**: 할일 제목 옆 `▸ n/m` 칩(또는 hover 시 `＋하위`) → 토글로 체크리스트 펼침. 오늘의 집중·프로젝트 뷰에서만. `todos.subs jsonb`(`[{t,d}]`)에 저장. 기존 할일은 hover `⤵하위로` → 대상 할일 탭하면 그 밑으로 이동(원본 삭제, 자식의 subs도 함께 흡수, 되돌리기 지원).
  - **순서도(flow) — 단계 보드**: 대상(프로젝트 or 하위항목 있는 할일) 선택 → 카드를 **단계 칼럼(미배치·1·2…·＋새단계)으로 드래그앤드롭**. 같은 칼럼=같은 순서(병렬 가능), 미배치=잡일. 상태색 done/next(다음=앞 단계 다 끝남)/blocked, 상위 마감일 표시, "다음 할 일" 하이라이트. **의존성 화살표 방식은 폐기**(탭-탭 불편+순환 이슈 → 단계 방식으로 교체, mermaid도 제거). 하위 단계=`subs[].stage`, 프로젝트 단계=`todos.dep`(숫자 재활용). 대시보드 오늘 흐름=`#todayFlow`(토글 `#flowToggle`, pref `showTodayFlow`). `renderFlow`/`renderFlowBoard`/`renderTodayFlow`. (대시보드/프로젝트 순서 자동반영은 2차 예정)
  - **DB 추가**: `todos.doneat text`, `todos.archived bool`, `todos.subs jsonb`(각 sub `{t,d,id,due,stage}`), `todos.dep jsonb`(프로젝트 순서도 단계 숫자), `settings(owner uuid pk, data jsonb)` 테이블(RLS `owner=auth.uid()`). 코드는 `hasArchive`/`hasSubs`/`hasDep` 플래그로 컬럼 없어도 안 깨지게 방어.
- **우선순위 점수**: `중요도*25 + 마감임박도(지남100/오늘·내일60/3일내30) + 빠른완수(≤20분)10`
- **Supabase**
  - Project URL: `https://mfgiesampazjzgfliuje.supabase.co`
  - Publishable key(공개용, 앱에 하드코딩 OK): `sb_publishable_QBlLIrJ8coqH3I-Ij-Q7SA_OUB7KVwV`
  - ⚠️ secret key는 절대 코드/문서에 넣지 말 것
  - 테이블: `todos`, `memos` (RLS `owner=auth.uid()`)
  - Auth: email+password (Confirm email 꺼둠)
- **호스팅**: Netlify. 자동배포는 이 repo `webapp/` 폴더 연결 (아래 참고).
- **데이터**: 현재 빈 상태로 시작. 예전 러버블 CSV(todos 300개)는 원하면 Supabase Table Editor로 Import 가능.

---

## 배포 / 인프라 메모

- **Railway**: 봇 2개(일정봇 / 인박스봇). Procfile `worker: python start.py`, `BOT_ROLE`로 구분.
- **Netlify**: 하루 웹앱. `webapp/index.html`을 publish. GitHub 연결 시 push→자동배포.
- **Google**: `GOOGLE_CREDENTIALS_JSON`(서비스계정), `GOOGLE_DRIVE_FOLDER_ID`(사진), `SPREADSHEET_ID`.
- **개발 브랜치**: `claude/content-archive-bot-xv3nx` (봇 개발용).

## 자주 하는 작업 (how-to)

- **웹앱 고치기**: `webapp/index.html` 수정 → push → Netlife 자동 반영 (GitHub 연결 후)
- **봇 고치기**: `bot/` 또는 `inbox_bot/` 수정 → push → Railway 자동 재배포
- **메모리 갱신**: 뭔가 구조가 바뀌면 이 `CLAUDE.md`도 같이 고칠 것

## 다음 할 일 (백로그)

1. 하루 웹앱 GitHub↔Netlify 자동배포 연결 (진행 중)
2. 인박스봇 볼트/Railway 연결
3. ~~텔레그램 → 하루 웹앱 주머니로 던지기 연동~~ ✅ 완료 (`앱 <내용>`, `bot/haru_app.py`) — Railway 환경변수만 넣으면 작동
4. 예전 CSV 300개 Supabase로 옮기기 (원할 때)
5. 메모 → 옵시디언 실제 내보내기
