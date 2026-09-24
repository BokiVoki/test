"""하루앱 아이폰(웹) 푸시 알림 발송.

아이폰은 사파리에서 그냥 열어둔 탭으로는 웹 푸시를 못 받고, 반드시 하루앱을
"홈 화면에 추가"로 설치한 뒤 그 아이콘으로 열어서 알림을 켜야 수신 가능하다
(iOS 16.4+ 제약, 우회 불가). 구독 등록/해제는 웹앱(index.html)이 담당하고,
여기서는 등록된 구독으로 실제 메시지만 보낸다.

환경변수:
  VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY  웹앱 JS와 공유하는 키 쌍 (1회 생성, 고정값)
  VAPID_SUBJECT   (선택) mailto: 주소, 기본 mailto:lolcv1294@gmail.com
  (SUPABASE_URL / SUPABASE_SERVICE_KEY / HARU_OWNER_ID 는 haru_app과 공유)
"""
import json
import os

import requests

try:
    from pywebpush import webpush, WebPushException
except ImportError:  # requirements.txt 반영 전 로컬 테스트 대비
    webpush = None
    WebPushException = Exception

from . import haru_app  # SUPABASE_URL / SERVICE_KEY / OWNER_ID 재사용

VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_SUBJECT = os.getenv("VAPID_SUBJECT", "mailto:lolcv1294@gmail.com")


def is_configured() -> bool:
    return bool(webpush and VAPID_PRIVATE_KEY and haru_app.is_configured())


def _list_subscriptions() -> list[dict]:
    if not haru_app.is_configured():
        return []
    resp = requests.get(
        f"{haru_app.SUPABASE_URL}/rest/v1/push_subscriptions",
        headers={
            "apikey": haru_app.SERVICE_KEY,
            "Authorization": f"Bearer {haru_app.SERVICE_KEY}",
        },
        params=[
            ("select", "endpoint,p256dh,auth"),
            ("owner", f"eq.{haru_app.OWNER_ID}"),
        ],
        timeout=15,
    )
    if resp.status_code >= 300:
        return []
    data = resp.json()
    return data if isinstance(data, list) else []


def _delete_subscription(endpoint: str) -> None:
    try:
        requests.delete(
            f"{haru_app.SUPABASE_URL}/rest/v1/push_subscriptions",
            headers={
                "apikey": haru_app.SERVICE_KEY,
                "Authorization": f"Bearer {haru_app.SERVICE_KEY}",
            },
            params=[("endpoint", f"eq.{endpoint}")],
            timeout=15,
        )
    except Exception:
        pass


def send(title: str, body: str, url: str = "./") -> int:
    """등록된 모든 기기(아이폰 홈 화면 앱 포함)에 알림을 보낸다.

    반환값: 성공적으로 보낸 기기 수. 만료된 구독(410/404)은 조용히 DB에서 지움.
    구독이 하나도 없으면 0을 반환(예외 아님).
    """
    if not is_configured():
        raise RuntimeError(
            "웹 푸시가 아직 설정 안 됐어요. Railway에 VAPID_PRIVATE_KEY/"
            "VAPID_PUBLIC_KEY(+SUPABASE 환경변수)를 넣어주세요."
        )
    subs = _list_subscriptions()
    sent = 0
    payload = json.dumps({"title": title, "body": body, "url": url})
    for sub in subs:
        subscription_info = {
            "endpoint": sub["endpoint"],
            "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
        }
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_SUBJECT},
            )
            sent += 1
        except WebPushException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (404, 410):
                _delete_subscription(sub["endpoint"])
            # 그 외 실패(일시적 오류 등)는 다른 기기 발송을 막지 않게 조용히 건너뜀
        except Exception:
            pass
    return sent
