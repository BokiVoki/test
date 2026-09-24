# 하루 위젯 (잠금화면/홈 화면 전용 미니 앱)

앱 전체를 새로 만든 게 아니라, **위젯만 담당하는 아주 작은 앱**이에요. 할일·루틴 관리는
그대로 하루 웹앱에서 하고, 이 앱은 그중 "오늘 체크할 루틴 + 투두"를 잠금화면/홈 화면에
띄우고 체크할 수 있게 해줘요.

이 폴더엔 Xcode 프로젝트 파일(.xcodeproj)이 없어요 — Xcode 프로젝트는 맥에서 직접
만들어야 해서(리눅스 서버에선 못 만듦), 여기 있는 Swift 소스 파일들을 그 프로젝트 안에
붙여넣는 방식이에요. 아래 순서대로 하면 돼요.

## 1. Xcode에서 새 프로젝트 만들기

1. Xcode 열기 → **File → New → Project**
2. **iOS → App** 선택
3. Product Name: `HaruWidget` (원하는 이름 아무거나 OK)
4. Interface: **SwiftUI**, Language: **Swift**
5. 저장 위치는 아무 데나 (이 저장소 폴더 말고 다른 곳에 만들어도 됨 — Swift 파일만
   복사해 넣을 거라서)

## 2. 위젯 익스텐션(Widget Extension) 타겟 추가하기

1. 방금 만든 프로젝트에서 **File → New → Target**
2. **Widget Extension** 선택
3. Product Name: `HaruWidgetExtension`
4. **"Include Configuration App Intent" 체크 해제** (설정 화면 필요 없음 — 위젯 하나만
   고정으로 씀)
5. **"Include Live Activity" 체크 해제**
6. Activate 물어보면 Activate

이제 프로젝트에 타겟이 2개 생겼을 거예요 — `HaruWidget`(메인 앱)과
`HaruWidgetExtension`(위젯).

## 3. 이 폴더의 Swift 파일들을 프로젝트에 넣기

Xcode 프로젝트 탐색기(왼쪽 파일 목록)에 드래그 앤 드롭으로 넣으면 돼요. **"Copy items
if needed" 체크**하고, 아래처럼 타겟 멤버십(Target Membership)을 맞춰주세요:

- **`HaruWidgetShared/` 안의 두 파일**(`WidgetModels.swift`, `WidgetAPI.swift`)
  → **두 타겟 모두 체크** (`HaruWidget` 앱 타겟 + `HaruWidgetExtension` 위젯 타겟).
  파일 선택 후 오른쪽 File Inspector에서 Target Membership 체크박스 두 개 다 켜기.
- **`HaruWidgetExtension/` 안의 파일들**(`CheckItemIntent.swift`, `Provider.swift`,
  `HaruWidgetView.swift`, `HaruWidget.swift`, `HaruWidgetBundle.swift`)
  → **`HaruWidgetExtension` 타겟만** 체크.
  ⚠️ Xcode가 위젯 타겟 만들 때 자동으로 만들어준 예시 Swift 파일(비슷한 이름)이
  이미 있을 텐데, 그건 지우고 이 파일들로 교체하세요.
- **`HaruWidgetApp/` 안의 파일들**(`HaruWidgetApp.swift`, `ContentView.swift`)
  → **`HaruWidget` 메인 앱 타겟만** 체크.
  ⚠️ 마찬가지로 Xcode가 처음에 만들어준 `HaruWidgetApp.swift`/`ContentView.swift`가
  이미 있을 텐데, 그것들을 이 파일들로 덮어쓰세요(같은 이름이라 그냥 교체하면 됨).

## 4. 위젯 iOS 최소 버전 확인

App Intents 기반 인터랙티브 위젯(잠금화면에서 바로 체크)은 **iOS 17 이상**이 필요해요.
프로젝트 설정(HaruWidgetExtension 타겟 → General → Minimum Deployments)에서 iOS 17
이상으로 맞춰주세요.

## 5. 빌드 & 실행

1. 아이폰을 맥에 USB(또는 같은 네트워크)로 연결
2. Xcode 상단에서 실행 대상을 **본인 아이폰**으로 선택
3. 상단 스킴을 `HaruWidget`(메인 앱)으로 두고 ▶ 실행
   - 처음엔 "신뢰할 수 없는 개발자" 경고가 뜰 수 있어요 → 아이폰 설정 →
     일반 → VPN 및 기기 관리에서 본인 Apple ID 신뢰 허용
   - **무료 Apple ID로 빌드하면 인증서가 7일마다 만료**돼요 — 1주일 지나면 Xcode에서
     다시 실행해서 재설치해야 함. ($99/년 유료 개발자 계정이면 이 제약 없음.)
4. 앱이 설치되면 아이폰에서 **잠금화면 길게 누르기 → 사용자화 → 위젯 추가** (또는
   홈 화면 길게 누르기 → + → "하루" 검색)로 위젯을 추가

## 6. 동작 확인

- 위젯에 루틴/투두가 하나도 안 보이면: 하루 웹앱 → 캘린더 탭 → 루틴/투두에서
  항목을 먼저 몇 개 추가해두세요(오늘 체크할 게 있어야 위젯에 뜸)
- 체크 버튼(동그라미) 누르면 바로 반영되고, 그 옆 텍스트를 누르면 이 미니 앱의
  빠른 추가 화면이 열려요

## 참고: 백엔드

위젯은 Supabase Edge Function `widget-data`를 통해서만 데이터를 읽고 씁니다
(하루 웹앱과 같은 Supabase 프로젝트). 서비스 키를 앱에 직접 넣지 않고, 이 함수
하나에만 통하는 별도 토큰(`WidgetAPI.swift`의 `token` 상수)으로 인증해요 — 이
토큰이 노출돼도 DB 전체가 아니라 이 좁은 API(루틴/투두 읽기·체크·투두 추가)만
가능해서 블라스트 반경이 작습니다.
