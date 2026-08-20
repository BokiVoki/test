# 프로젝트 메모리 — 희은의 개인 시스템

> 이 문서는 "항상 기억하는 뇌"예요. 새 Claude 세션은 이 파일을 자동으로 읽어요.
> 무언가 바뀌면 이 문서도 같이 업데이트해요.

## 한눈에 — 지금 뭐가 돌아가고 있나

| 시스템 | 무엇 | 어디 사는가 | 상태 |
|---|---|---|---|
| **일정 비서봇** | 텔레그램 봇. 할일/알람/리마인더/영양제 재고/생리주기/브리핑 | Railway (BOT_ROLE 미설정) + Google Sheets | ✅ 운영 중 |
| **인박스봇** | 텔레그램 봇. 링크/생각/사진/쇼핑/책을 옵시디언 볼트로 자동 정리 | Railway (BOT_ROLE=inbox) + GitHub 볼트 `BokiVoki/obsidian-vault`(Private) | ✅ 운영 중 (Books/Inbox/Shopping 폴더에 커밋 중). 남은 건 옵시디언 앱에서 볼트 열기(Obsidian Git) |
| **하루(Haru) 웹앱** | 개인 업무 할일 대시보드 (미니멀·딥그린) + 그림공부(작가별 그림 태그 상속) | **Cloudflare** + Supabase, 그림은 옵시디언에도 미러링 | ✅ 운영 중 (`webapp/index.html`, `haru.lolcv1294.workers.dev`) |

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
  - **하루앱 주머니 던지기**: `앱 <내용> [마감]` → 하루 웹앱 주머니(Supabase todos, `bucket='inbox'`)로 바로 저장 (`bot/haru_app.py`). 끝에 붙은 날짜(오늘/내일/모레/글피, 요일, `M/D`·`M.D`·`M-D`, `M월 D일`)를 `parse_pocket_due`로 떼어 due로 저장. 환경변수: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `HARU_OWNER_ID`, (선택 `HARU_WORKSPACE`, 기본 '일')
  - **구글 캘린더 일정**: 끝에 **`구캘`=캘린더만**, **`구할`=캘린더+하루앱 둘 다**(`_create_schedule(also_app)`), 앞에 `일정 `=캘린더만. 예: `출장 8/6-8/8 구캘`, `회의 8/6 구할` (`bot/gcal.py`, `parse_schedule`). 기간 `8/6-8/8`·`8월6일-8월8일`, 단일 `8/6`/`8월 3일`/오늘·내일, 시간 `8/6 14:00`(1시간). 서비스계정(GOOGLE_CREDENTIALS_JSON) 재사용 — **셋업**: Calendar API 사용설정 + 내 캘린더를 서비스계정 client_email에 '일정 변경'으로 공유 + env `GOOGLE_CALENDAR_ID`(기본캘린더면 내 gmail). **캘린더 + 하루앱 동시**: 성공 시 `haru_app.add_to_pocket(bucket='inbox', due=시작일, endd=종료일)`로 하루앱 주머니+캘린더에도 표시(기간이면 제목에 `~` + `todos.endd`로 캘린더에 시작~종료 쫙). 실패/미연결이면 답장에 이유 표시.
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
  - 사진 → 볼트 `Inbox/attachments/`에 직접 커밋 + `![[...]]` 임베드. 저장 메시지에 **📝 글 인식** 버튼(`ocr_callback`, callback `o:{폴더}:{stamp}`) → 눌러야 실행(자동 아님): 노트 이미지를 볼트에서 `read_binary`로 다시 읽어 `capture.read_photo_text`(비전)로 글 추출 → 노트에 `## 📄 인식한 글` 추가 + **읽은 글에서 뽑은 태그/허브를 frontmatter에 합치고 그래프 링크(`관련: [[허브]]`)까지 옵시디언용 자동 정리**(`merge_tags_into_note`, 답장에 🏷/🗂 표시) + **읽은 글을 사용자에게 확인용으로 전송**(4096자 제한, 3500 넘으면 잘림). `_stampcb`/`_find_note` 공통.
  - 쇼핑("살까/얼마") → `Shopping/` 위시리스트 (비전으로 상품/가격 읽음)
  - 책("읽어볼까/책추천") → `Books/` 읽고싶은 책 (표지 제목/저자 읽음, 여러 권이면 목록)
  - `✍️ 내 생각` 버튼 → 사용자 말에서 태그 재추출, 캡션 우선
  - **삭제**: 각 저장 메시지에 `🗑 삭제` 버튼 — **재시작에도 안전하게** callback_data에 `d:{폴더코드}:{타임스탬프}`를 담음(`_del_button`/`delete_callback`, `list_folder`로 stamp 매칭 → 노트+`![[img]]` 첨부 삭제). 일괄: `삭제`/`삭제 N`/`최근삭제 N` 텍스트 → 인박스 최근 N개 삭제(`_delete_recent`). `vault.read_note`/`list_folder`/`delete_note`.
  - **사진 여러 장(앨범)**: `media_group_id`로 **file_id만 즉시 버퍼**(`_album_buf`, 핸들러는 업로드 안 함=빠름) → 4초 debounce 후 `_flush_album`이 다운로드+업로드하고 **한 노트에 전부 임베드**(`![[img]]` 여러 줄, 첫 캡션 사용). job_queue 없으면 개별 저장로 폴백. (예전에 핸들러에서 바로 업로드하다 느려서 앨범이 쪼개지던 것 → file_id 버퍼링으로 해결)
  - 명령어: `/today` `/find` `/shopping` `/books`
  - 비전 모델: `INBOX_VISION_MODEL` (기본 sonnet, opus로 올릴 수 있음)
  - **파일명 형식**: `제목 YYMMDD-HHMM.md` (제목 앞으로, 밑줄 X=띄어쓰기, 날짜는 뒤에 짧게) — 옵시디언 그래프에서 노드가 날짜 대신 **제목으로 읽히게** (`capture._slugify`는 공백 유지+금지문자만 제거, `capture._note_path`). 삭제/글인식 콜백은 파일명 뒤 `YYMMDD-HHMM` 스탬프로 노트 매칭(`_stampcb`/`_find_note`, `stamp in name`), 옛 형식(`YYYY-MM-DD_HHMM` 앞)도 호환. 최신순 정렬은 `vault._recency_key`가 파일명에서 날짜 뽑아 유지.
  - **허브를 실제 파일로**: `관련: [[허브]]`는 원래 유령 노드(파일 없음)라 옵시디언 그래프에서 색칠이 안 됨 → 노트 저장 시 `_save_note`(모든 write_note 래퍼)가 `_ensure_hub_notes`로 `Hubs/{허브}.md`(frontmatter `tags:[허브]`)를 자동 생성(`_hub_seen` 캐시). **`이름정리` 명령**이 기존 노트도 리네임 + 허브 백필(`_backfill_hubs`). **그래프 색칠은 옵시디언 앱에서 Groups `path:Hubs` → 초록**(코드 아님).
  - **태그 vs 허브 기준**: 태그(2~4개)=이 노트 하나의 짧은 속성/종류 라벨(예: 감상·인터뷰·유튜브·창의력), 허브(1개)=여러 노트가 재사용하는 넓은 카테고리(예: 글쓰기·브랜딩·자기계발) — 노트 요약이면 안 됨. **옵시디언 태그는 공백이 있으면 무효**라 프롬프트에 "공백 없는 한 단어"를 명시(모든 tags 프롬프트: `summarize`/`derive_tags`/`read_photo_text`/`parse_book`/`parse_shopping`) + 코드 안전장치 `capture._clean_tags`(공백 제거·중복 제거)를 모든 tags_yaml 생성 지점과 `merge_tags_into_note`에 적용(기존에 이미 오염된 태그도 다음 저장 때 자동 정리됨). **`태그정리` 명령**이 기존 노트 전체를 한 번에 정리(`_clean_note_tags`, 바뀔 것만 저장해서 빠름).
- **연결 상태**: ✅ 이미 연결됨. 볼트 = `BokiVoki/obsidian-vault`(Private), Railway 두 번째 서비스(`BOT_ROLE=inbox`)에서 커밋 중. env: `INBOX_BOT_TOKEN`, `VAULT_REPO`, `GITHUB_TOKEN`, `BOT_ROLE=inbox`, (공유: `ANTHROPIC_API_KEY`, `TELEGRAM_USER_ID`)
  - **남은 것**: 옵시디언 앱에서 그 볼트를 열어 보기 (GitHub Desktop으로 clone → 폴더를 볼트로 열기 → 자동 업데이트는 **Obsidian Git** 플러그인)

## 3. 하루(Haru) 웹앱 (`webapp/index.html`)

- **스택**: 단일 HTML 파일 + Supabase (DB+Auth) + Netlify 호스팅. 빌드 없음.
- **디자인**: 미니멀·샤프, 딥 포레스트 그린 단일 포인트(`#2C6A46`/dark `#5FB088`), 엑셀st 표, 라이트/다크.
- **탭**: 대시보드 / 주머니·프로젝트 / 캘린더 / 메모 / 완료함 / 순서도
- **탭 순서 변경**: 상단 탭 자체를 드래그로 재정렬(localStorage `tabOrder`, `applyTabOrder`).
- **모바일 전체화면(아이폰 메모st)**: 680px 이하에서 그림공부/메모 둘 다 "목록 vs 상세" 중 하나만 보임 — 뭔가 선택하면(작가/그림/태그/미분류, 또는 메모) 사이드바·목록이 숨고 내용이 화면 전체를 채우는 고정 오버레이로 뜸(`art-mob-detail`/`mob-edit` 클래스, `renderArtMain`/`#mBackBtn`에서 토글). 각 화면 위 **‹ 목록** 링크(`art-back-list`)나 **‹ 메모 목록** 버튼(`#mBackBtn`)으로 되돌아감. 데스크톱(680px 초과)에선 원래대로 사이드바+본문 동시에 보임(CSS 미디어쿼리 안에서만 의미있는 클래스라 무해).
- **되돌리기**: 토스트 "되돌리기" + **Cmd/Ctrl+Z**(입력창 focus 아닐 때). `undoHist` 스택, `doUndo()`. (되돌리기 옵션 있는 액션만 — 삭제/완료함/nest/프로젝트삭제 등)
- **프로젝트 이름 변경**: 헤더 hover **✎**(`proj-rename`) → 해당 ws 모든 할일 `project` 갱신 + projOrder/flowShownProj/flowSel 키 이동.
- **메모 정렬**: 최신순(`memos.updated` text, `putMemo`가 `new Date().toISOString()` 기록). 새 메모/수정 메모가 위로(목록 새로고침 시). 컬럼 없으면 `hasMemoUpd`로 방어.
- **전체 검색**: 헤더 아래 검색바(`#q`) — 모든 탭/워크스페이스 걸쳐 할일(제목·하위항목·프로젝트)+메모 검색. **띄어쓰기 무시**(`norm()`=공백 제거+소문자). 결과 클릭 시 해당 위치로 이동. (`runSearch`, `#searchView`)
  - **대시보드**: 빠른 담기 + 오늘의 집중(자동 우선순위 + 가용시간 · **오늘 찍음 or 오늘 마감 자동 포함**, 밀린 건 마감 섹션에만) + 마감 임박 + 오늘 흐름 토글. 진짜 할일만. 빠른담기 옆 **오늘** 버튼=오늘의 집중 바로 담기(Shift+Enter도). 행 hover **🔁**=고정업무(none→매일→매주(요일)→매월(일)→none), 프로젝트 행 hover **오늘** 토글, 하위 항목 hover **＋날짜**(`subdue`, sub.due) · **오늘**(`subtoday`, sub.today — 하위 항목도 오늘의 집중에 개별 지정 가능, 마감 안 잡아도 됨). **오늘 지정은 자정 지나면 자동 리셋**(`dailyResetToday`, 매일 첫 로드 때 `settings.todayResetDate`가 오늘과 다르면 부모·하위 `today` 플래그 전부 끄고 오늘 날짜로 갱신 — 컬럼 추가 없이 settings에 리셋한 날짜만 기록해서 판별). **오늘의 집중 포함 조건**: 부모가 `today`거나 마감이 오늘이거나, **하위 항목 중 하나라도 `today`거나 마감이 오늘**이면(완료 제외) 그 부모가 통째로 올라옴.
  - **주머니·프로젝트**: 주머니 분류(오늘/프로젝트/나중/메모/버림) + 프로젝트 아코디언. **주머니 표시 기준은 `bucket==='inbox'`가 아니라 `!project`**(프로젝트가 없으면 무조건 주머니) — "오늘" 버튼으로 바로 오늘의 집중에 던진 항목(`bucket='active'`, project 없음)이 다음날 `dailyResetToday`로 오늘 지정이 풀리면 프로젝트도 없고 bucket도 inbox가 아니라서 주머니/프로젝트 어디에도 안 보이고 마감임박에만 뜨는 유령 상태가 되던 버그 → 모든 할일이 항상 주머니 또는 프로젝트 중 하나에는 걸리게 통일. 주머니 행에도 체크박스(완료) 추가.
  - **캘린더**: 월 그리드, 드래그로 마감일 변경. **기간 일정**(`todos.endd`)이면 시작~종료 모든 날에 바 표시(시작일=제목, 이후=`.evcont` 연속 바). 구캘 일정이 여기 뜸.
  - **메모**: 애플 메모st, 폴더, 할일 연결(📝). **리스트 자동표기**(노션/애플메모st): `-`(또는 `*`)+스페이스 → `• `, 불릿/번호 줄에서 엔터 → 다음 줄 자동 이어짐(`1.`→`2.`), 빈 불릿/번호에서 엔터 → 리스트 종료. `#mbody` keydown 핸들러(한글 조합 중·@팝업 중엔 양보). **연결은 메모 본문에서 `@`**로 → 할일 검색(제목·**하위항목**·**프로젝트** 다, 띄어쓰기 무시)해서 클릭하면 연결. 매치는 **부모/하위 각각 별도의 한 줄짜리 행**으로 나옴(하위 매치는 `↳ 하위텍스트` + 아래 부모 이름) — 줄 전체가 탭 영역이라 모바일에서도 안 놓침, 클릭하면 그 하위항목(`sub.memoId`, `normSubs`로 정규화)에 연결됨(부모 title이 아니라). 연결되면 `@부모title ↳ 하위텍스트` 형태로 삽입, `m.link`도 같은 형식. 드롭다운은 **캐럿을 안 쫓고 화면 하단(키보드 바로 위)에 고정 도킹**(`position:fixed`, `window.visualViewport`로 키보드 높이만큼 띄움) — 커서 위치·스크롤·모바일 키보드 뭐든 상관없이 항상 같은 자리에 보임(`positionMention`, 이전의 캐럿 추적 방식은 위치가 튀는 문제가 있어서 폐기). 연결된 할일 행엔 📝(메모로 점프)만 표시(할일 행에 링크 버튼은 안 붙임) — **연결된 하위항목도 그 줄에 📝 표시**(`subRowHtml`, `submemo` act). `syncLinks()`가 부모/하위 양쪽 다 제목·텍스트 바뀌면 `m.link` 자동 갱신. 메모 편집기 `연결 해제`(`data-unlink`, 부모+모든 하위의 memoId를 같이 정리). (주머니 "메모"는 할일→메모 변환이라 다름)
    - **접기 토글**: `>`+스페이스 → `▾ `(펼침 상태 마커). 엔터 치면 그 아래에 들여쓰기(+2칸)된 자식 줄이 시작되고(`▾`/`▸` 줄 자체의 엔터 처리), 빈 자식 줄에서 엔터 → 들여쓰기 종료(리스트 자동표기와 동일한 "빈 항목 → 종료" 패턴). 자식 줄이 아닌 그냥 들여써진 일반 줄도 엔터 시 같은 들여쓰기로 자동 이어짐(토글 전용은 아니고 범용 들여쓰기 유지). 읽기뷰(`renderMemoRead`→`renderMemoLines`)는 들여쓰기 깊이로 자식 범위를 재귀적으로 묶어서(`▾`/`▸` 줄보다 들여쓰기가 더 깊은 연속 줄들, 중간 빈 줄 포함) `.mtoggle-body`로 감싸고 접힘(`▸`)이면 `hidden` 클래스. 행 전체(`data-act="mread-toggle"`)가 탭 영역이라 화살표만 정확히 눌러야 하는 문제 없음, 클릭하면 본문의 `▸`/`▾` 글자 자체를 토글해서 저장(체크박스 토글과 같은 방식) — 그래서 접힘 상태도 메모 본문에 그대로 저장되고 다음에 열어도 유지됨. 중첩 토글(토글 안에 토글)도 재귀 렌더링이라 지원됨. **들여쓰기 안에 여러 줄 붙여넣기**: 기본 붙여넣기는 첫 줄만 커서 위치 들여쓰기를 따라가고 나머지 줄은 들여쓰기 0으로 떨어져서 토글 밖으로 끊기던 문제 → `#mbody` paste 핸들러가 붙여넣는 줄 전체(첫 줄 제외)에 현재 줄의 들여쓰기를 그대로 붙여서 삽입(들여쓰기 없는 줄에 붙여넣을 땐 기본 동작 그대로 둠). **출처별 줄바꿈 문자 정규화**: 맥 메모/일부 웹페이지 등에서 복붙하면 줄바꿈이 `\n`이 아니라 `\r\n`/`\r`/유니코드 줄바꿈일 수 있어서 `indexOf('\n')` 체크와 `split('\n')`이 다 어긋나 첫 문단만 들여써지고 나머지가 통째로 토글 밖으로 새던 버그 → paste 핸들러 맨 앞에서 전부 `\n`으로 정규화.
    - **체크리스트**: `[]`/`[ ]`+스페이스 → `☐ `(리스트 자동표기와 같은 keydown 핸들러 확장), 엔터로 다음 줄도 `☐ ` 이어짐(항상 미체크로 시작), 빈 체크박스에서 엔터 → 종료. **글리프(☐/☑) 탭/클릭으로 완료 토글**(`#mbody` click 리스너, `ta.selectionStart`로 클릭 위치의 줄·글리프 오프셋 계산해 그 근처(±2자)일 때만 토글 — 본문 클릭은 그냥 커서 이동).
    - **사진 첨부**(`memos.images` jsonb, `hasMemoImg` 프로브): 제목 아래 **📎 사진** 버튼(파일선택, `multiple`), **`#mbody`에 붙여넣기(Cmd/Ctrl+V)**, **`#mbody`로 드래그앤드롭** 셋 다 여러 장 한번에 가능(그림공부와 같은 `uploadArtImage`/`art` 스토리지 버킷 재사용). **마커 방식**(`addMemoImages`): 사진 넣으면 커서 위치(또는 끝)에 `📷 사진 ✕` 텍스트 줄이 실제로 삽입돼서 본문 순서대로 섞여 보임.
    - **읽기뷰(렌더링) ↔ 편집뷰(raw textarea) 전환**: 안 만지고 있을 땐 `#mbodyRead`가 본문을 렌더링해서 보여줌 — `📷 사진 ✕` 마커는 **진짜 `<img>` 사진**으로, `☐/☑` 줄은 탭 가능한 체크박스로 바뀜(`renderMemoRead`). **탭하면** `#mbody`(진짜 textarea, raw 마커 텍스트) 로 전환해서 평소처럼 편집(`startMemoEdit`) — 리스트 자동표기·@멘션·**한글 입력**은 이 raw textarea에서 그대로 동작(안 건드림, 안전). **blur 시**(다른 데 탭/포커스 이동, 180ms 디바운스로 @멘션 클릭과 안 꼬이게) 다시 읽기뷰로 렌더링(`endMemoEdit`). 읽기뷰에서 사진 탭=새 탭으로 보기, 사진 위 ✕(진짜 버튼)=그 사진+마커 줄 삭제, 체크박스 탭=완료 토글 — 전부 `data-act`(`mread-img-view`/`mread-img-del`/`mread-ck-toggle`)로 처리, 결과는 `m.body`/`m.images`에 그대로 반영되고 raw textarea 값도 동기화됨. **새 메모 만들면 바로 편집모드**로 열림(제목부터 바로 쓸 수 있게).
    - **삭제**: 편집기 상단 **🗑 삭제** 버튼(`#mDelBtn`) — 되돌리기 토스트 지원, 연결된 할일 있으면 memoId도 같이 정리(되돌리면 복원).
  - **완료함**: 체크=완료(찍 긋고 그 자리 유지 + 완료시점 `doneat` 기록) → "완료함으로" 버튼/일괄정리로 `archived=true` → 완료함 탭에서 완료시점 표시 + 되돌리기. 반복 항목은 예외(lastdone).
  - **프로젝트**: 헤더 ⠿ 핸들 드래그로 순서 변경(→ `settings.projOrder[ws]`), ✕로 삭제(할일은 주머니로 이동, 삭제 아님). 프로젝트 안 **할일 행 ⠿ 드래그로 순서 변경**(→ `todos.sort`, `reorderTodo`), **하위 항목 ⠿ 드래그로 순서 변경**(subs 배열 재정렬, `reorderSub`).
  - **⤵하위로(nest) 연결 모드 주의**: 모드 진입 후 아무 데나 클릭하면 취소됨(예전엔 유효 대상 아니면 무시→전체 먹통 버그였음, 수정됨), Esc로도 취소.
  - **하위 항목(서브태스크)**: 할일 제목 옆 `▸ n/m` 칩(또는 hover 시 `＋하위`) → 토글로 체크리스트 펼침. 오늘의 집중·프로젝트 뷰에서만. `todos.subs jsonb`(`[{t,d}]`)에 저장. 기존 할일은 hover `⤵하위로` → 대상 할일 탭하면 그 밑으로 이동(원본 삭제, 자식의 subs도 함께 흡수, 되돌리기 지원).
  - **순서도(flow) — 단계 보드**: 대상(프로젝트 or 하위항목 있는 할일) 선택 → 카드를 **단계 칼럼(미배치·1·2…·＋새단계)으로 드래그앤드롭**. 같은 칼럼=같은 순서(병렬 가능), 미배치=잡일. 상태색 done/next(다음=앞 단계 다 끝남)/blocked, 상위 마감일 표시, "다음 할 일" 하이라이트. **의존성 화살표 방식은 폐기**(탭-탭 불편+순환 이슈 → 단계 방식으로 교체, mermaid도 제거). 하위 단계=`subs[].stage`, 프로젝트 단계=`todos.dep`(숫자 재활용). 대시보드 오늘 흐름=`#todayFlow`(토글 `#flowToggle`, pref `showTodayFlow`). `renderFlow`/`renderFlowBoard`/`renderTodayFlow`.
    - **순서도 → 프로젝트 리스트 반영**: 프로젝트 할일 정렬 = 단계 우선(미배치=뒤) → 그다음 `sort`. 행에 단계 배지(`.stbadge`).
    - **상위 마감 → 하위 자동 분배**(`flowDistribute`, 순서도 헤더 `📅 하위 마감 자동 분배` 버튼, todo-source+상위 due 있을 때): 오늘~상위마감을 단계 수로 나눠 각 단계 하위 항목에 마감 배정(마지막 단계=상위마감, 미배치는 제외).
    - **보드 개선**: 카드에 마감 표시(`.fcdue`), **단계-대-단계**를 화살표 SVG 곡선 1개로 연결(`drawFlowLines`, 칼럼 gap 32px, 화살촉 marker). 인라인 순서도 두 종류: 프로젝트 섹션 헤더 **🔀**(`proj-flow`, `flowShownProj`, `.projflow`)=프로젝트 할일 순서도, 하위 있는 할일 행 **🔀**(`row-flow`, `flowShownTodo`, `.rowflow`/`.flowsubrow`)=그 할일 하위 순서도. 둘 다 읽기전용. 프로젝트 행은 제목 옆에 하위토글이 먼저.
  - **DB 추가**: `todos.doneat text`, `todos.archived bool`, `todos.subs jsonb`(각 sub `{t,d,id,due,stage}`), `todos.dep jsonb`(프로젝트 순서도 단계 숫자), `todos.endd text`(기간 일정 종료일), `settings(owner uuid pk, data jsonb)` 테이블(RLS `owner=auth.uid()`). 코드는 `hasArchive`/`hasSubs`/`hasDep`/`hasEnd` 플래그로 컬럼 없어도 안 깨지게 방어.
- **우선순위 점수**: `중요도*25 + 마감임박도(지남100/오늘·내일60/3일내30) + 빠른완수(≤20분)10`
- **Supabase**
  - Project URL: `https://mfgiesampazjzgfliuje.supabase.co`
  - Publishable key(공개용, 앱에 하드코딩 OK): `sb_publishable_QBlLIrJ8coqH3I-Ij-Q7SA_OUB7KVwV`
  - ⚠️ secret key는 절대 코드/문서에 넣지 말 것
  - 테이블: `todos`, `memos` (RLS `owner=auth.uid()`)
  - Auth: email+password (Confirm email 꺼둠)
- **호스팅**: Cloudflare (`haru.lolcv1294.workers.dev`). 자동배포는 이 repo `main` 브랜치 연결 (아래 배포 메모 참고).
- **데이터**: 현재 빈 상태로 시작. 예전 러버블 CSV(todos 300개)는 원하면 Supabase Table Editor로 Import 가능.

---

## 4. 그림공부 (하루앱 새 탭 + 옵시디언 미러링)

- **뭐냐**: 작가별로 그림(작품)을 쌓아 공부하는 기능. 작가 태그를 달면 그 작가의 모든 그림에 자동 상속(합산)되고, 태그를 누르면 무드보드처럼 관련 그림이 다 모여 보임.
- **어디**: 하루앱(`webapp/index.html`) 탭 `그림공부` (`data-tab="art"`). Supabase가 원본, 옵시디언은 봇이 주기적으로 미러링(한쪽 방향, 옵시디언→하루앱 반영은 안 됨).
- **태그 상속(합산) 원리**: 저장할 땐 작가 태그와 그림 태그를 따로 저장, **보여줄 때만 합쳐서** 표시(`effTags`, JS)/`art_sync._effective_tags`(파이썬) — 그래서 작가 태그를 나중에 바꿔도 소급 적용됨. 자기 태그(✕로 지울 수 있음) vs 상속 태그(그림 화면엔 뜨지만 못 지움, 작가 쪽에서 지워야 함)로 구분해서 보여줌.
- **데이터 모델(Supabase)**: `art_artists`(id,owner,name,tags jsonb,note,created,updated), `art_works`(id,owner,artist_id,title,year,genre,medium,image_url,source_url,tags jsonb,note,favorite,created,updated). id는 프론트에서 생성(`'ar'+uid()`/`'aw'+uid()`)해서 `upsert`로 저장 — todos/memos와 같은 방식. `hasArt` 프로브 플래그로 테이블 없어도 안 깨짐.
- **이미지**: Supabase Storage 버킷 `art`(public read, 본인 폴더에만 업로드 — `storage.foldername(name)[1]=auth.uid()`). 새 그림 폼에서 **파일 선택 · 링크 붙여넣기 · 클립보드 붙여넣기(Cmd/Ctrl+V, `wireImageDropzone`은 아니고 document paste 델리게이션) · 드래그앤드롭**(`#artImgDropzone`, `wireImageDropzone`) 네 가지 다 가능, 다 같은 이미지 링크 인풋(`#artImgLink`)에 채워짐.
- **작가 선택**: 새 그림 폼의 작가 입력은 `<input list>` + `<datalist>`(네이티브 자동완성) — 기존 작가면 그대로 매칭, 없는 이름이면 저장 시 새 작가로 자동 생성(`resolveArtistByName`). **작가·제목 둘 다 비워도 저장 가능**(작가 없으면 미분류, 제목 없으면 "제목 없음") — 사이드바 **"🖼 그림 바로 추가"** 버튼으로 작가 선택 없이 어디서든 그림부터 툭 던질 수 있음(주머니처럼). 미배정 그림은 사이드바 **"🗂 미분류 그림"** 칸(`data-id="__unassigned__"`)에 모이고, 그림 상세화면에서 **작가 필드**(`#artFArtist`, change 이벤트)로 언제든 나중에 배정 가능.
- **UI 구조**: 왼쪽 사이드바(그림 바로 추가 · 작가 검색·목록·미분류 그림 · 전체 태그클라우드) + 메인(작가 화면=태그·공부메모·그림 그리드 / 그림 화면=작가 포함 필드·효과태그·공부메모 / 태그 클릭 시 무드보드 그리드 / 미분류 그리드). 삭제는 실행취소 토스트 방식(다른 삭제와 동일), 작가 삭제 시 그 작가의 그림도 같이 삭제(되돌리기 가능).
- **옵시디언 미러링**(`inbox_bot/art_sync.py`): `Artists/{작가}.md`(frontmatter tags=작가 태그) + `Artworks/{작가} - {제목}.md`(frontmatter tags=효과 태그(합산), `아티스트: [[작가]]` 위키링크로 백링크 연결 — 파일 경로에 타임스탬프 없이 이름 기반 고정이라 재동기화해도 같은 파일을 덮어씀(중복 생성 안 됨), 내용 바뀐 노트만 커밋. 텔레그램 **`그림동기화`** 명령으로 즉시 실행, 또는 20분마다 자동(`_periodic_art_sync`, 조용히·바뀐 게 있을 때만 로그). **env**: `SUPABASE_URL`/`SUPABASE_SERVICE_KEY`/`HARU_OWNER_ID`를 인박스봇 Railway 서비스에도 넣어야 함(일정봇과 같은 값).
- **옛 볼트 노트 가져오기**(`import_artists_from_vault`, 텔레그램 **`작가공부 가져오기`**): 니들보스에서 옮긴 `작가 공부` 폴더(작가별 노트, `# 이름` + `#artist #스타일태그` + 자유 텍스트 형식)를 하루앱 `art_artists`로 1회성 이전. **하위 폴더까지 재귀 탐색**(`_walk_md`, `vault.list_dir`) — 실제 구조가 `작가 공부/고전/…`, `작가 공부/다다,포스트모더니즘,팝/…`처럼 카테고리 폴더 안에 작가 노트가 있어서, **하위 폴더명(쉼표로 여러 개면 각각 분리)도 태그로 흡수**함. `#artist` 태그 없는 파일(README 등)은 자동 제외, 이미 같은 이름 작가 있으면 건너뜀(재실행 안전). **이미지는 안 옮김**(볼트가 Private라 그 안 이미지 URL은 웹앱에서 로그인 없인 못 읽음 → 텍스트만 이전, 대표 이미지는 하루앱에서 수동으로 재업로드 필요).
- **아이패드 손글씨**: 처음엔 옵시디언 Excalidraw 플러그인으로 가려다가, **하루앱 안에 직접 캔버스**로 최종 결정. 그림 상세화면 **"✏️ 손으로 공부하기"** 버튼 → 전체화면 캔버스 모달(`#artSketchModal`) → Pointer Events로 애플펜슬 **압력 감지**(`e.pressure`로 선 굵기), 색상 3종(검정/빨강/파랑)+지우개+실행취소(캔버스 스냅샷 스택, 최대 25단계)+전체지우기. 저장하면 PNG로 캡처(`canvas.toBlob`) → `art` 스토리지 버킷 업로드 → `art_works.sketch_url`에 저장, 그림 화면에 썸네일로 보임, 다시 누르면 기존 그림 위에 이어서 그릴 수 있음(`openSketchPad`가 기존 sketch_url을 캔버스에 먼저 그려줌). `hasSketch` 프로브 플래그로 컬럼 없어도 안 깨짐(SQL 마이그레이션 `haru_art_sketch.sql` 필요).

---

## 배포 / 인프라 메모

- **Railway**: 봇 2개(일정봇 / 인박스봇). Procfile `worker: python start.py`, `BOT_ROLE`로 구분.
- **Cloudflare (하루 웹앱 호스팅)**: `haru.lolcv1294.workers.dev`. `main` 브랜치 push → 자동배포. `wrangler.toml`(repo 루트)이 `webapp/` 폴더를 정적 사이트(Workers Static Assets)로 배포(`npx wrangler deploy`, assets-only). **Netlify에서 이사함**(무료 크레딧 소진으로 production deploy 멈춰서). ⚠️ 예전 Netlify는 개발 브랜치를 봤지만 Cloudflare는 `main`을 봄 → 웹앱 배포하려면 `main`에도 push해야 함.
- **Google**: `GOOGLE_CREDENTIALS_JSON`(서비스계정), `GOOGLE_DRIVE_FOLDER_ID`(사진), `SPREADSHEET_ID`.
- **개발 브랜치**: `claude/content-archive-bot-xv3nx` (봇 개발용). 웹앱 변경은 `main`에도 fast-forward push해야 Cloudflare가 배포함.

## 자주 하는 작업 (how-to)

- **웹앱 고치기**: `webapp/index.html` 수정 → 개발 브랜치 + `main` 둘 다 push → Cloudflare 자동 반영 (`haru.lolcv1294.workers.dev`)
- **봇 고치기**: `bot/` 또는 `inbox_bot/` 수정 → push → Railway 자동 재배포
- **메모리 갱신**: 뭔가 구조가 바뀌면 이 `CLAUDE.md`도 같이 고칠 것

## 다음 할 일 (백로그)

1. ~~하루 웹앱 GitHub↔Netlify 자동배포 연결~~ ✅ Cloudflare로 이사 완료 (`main` push→자동배포)
2. 인박스봇 볼트/Railway 연결
3. ~~텔레그램 → 하루 웹앱 주머니로 던지기 연동~~ ✅ 완료 (`앱 <내용>`, `bot/haru_app.py`) — Railway 환경변수만 넣으면 작동
4. 예전 CSV 300개 Supabase로 옮기기 (원할 때)
5. ~~메모 → 옵시디언 실제 내보내기~~ ❌ 보류 결정 — 메모는 하루앱에서만 관리하기로 함(옵시디언 연동 안 함)
6. 월별 업무 로그 탭(하루앱): 완료함+캘린더 자동 요약 + 막힌 것/기억할 만한 것 등 짧은 수동 입력 — 아이디어만, 아직 미착수
