"""
push_notifications.py
Utility for sending Expo Push Notifications to REACHLO users.

Expo's push service works for both iOS and Android.
The client registers a token by calling POST /api/auth/push-token.
This utility sends to Expo's push API — no extra infrastructure needed.
"""

import logging
import httpx
from typing import Optional

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"

logger = logging.getLogger(__name__)


def send_push_notification(
    expo_push_token: Optional[str],
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> bool:
    """
    Fire-and-forget push notification via Expo's push API.
    Returns True if accepted (200), False otherwise.
    Does NOT raise — a failed push should never crash a chat/lead flow.
    """
    if not expo_push_token:
        return False
    if not expo_push_token.startswith("ExponentPushToken["):
        logger.warning("Invalid Expo push token format: %s", expo_push_token[:30])
        return False

    payload = {
        "to": expo_push_token,
        "title": title,
        "body": body,
        "sound": "default",
        "channelId": "default",  # Android notification channel
        "data": data or {},
        "priority": "high",
    }

    try:
        with httpx.Client(timeout=8.0) as client:
            response = client.post(
                EXPO_PUSH_URL,
                json=payload,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
            if response.status_code == 200:
                result = response.json()
                errors = [r for r in result.get("data", []) if r.get("status") == "error"]
                if errors:
                    logger.warning("Expo push error(s): %s", errors)
                    return False
                return True
            else:
                logger.warning("Expo push HTTP %s: %s", response.status_code, response.text[:200])
                return False
    except Exception as exc:
        logger.error("Failed to send push notification: %s", exc)
        return False


def send_new_lead_notification(seller_token: Optional[str], campaign_title: str, buyer_name: str) -> bool:
    """Notify the seller about a new lead (buyer claimed a deal)."""
    return send_push_notification(
        expo_push_token=seller_token,
        title="🎯 New Lead on Reachlo!",
        body=f"{buyer_name} is interested in \"{campaign_title}\". Open chat to respond.",
        data={"type": "NEW_LEAD", "screen": "SellerMessages"},
    )


def send_new_message_to_seller(seller_token: Optional[str], buyer_name: str, campaign_title: str, message_preview: str) -> bool:
    """Notify the seller about a new message from a buyer."""
    preview = message_preview[:60] + "..." if len(message_preview) > 60 else message_preview
    return send_push_notification(
        expo_push_token=seller_token,
        title=f"💬 New message from {buyer_name}",
        body=f"Re: {campaign_title} — {preview}",
        data={"type": "NEW_MESSAGE", "screen": "SellerMessages"},
    )


def send_new_message_to_buyer(buyer_token: Optional[str], seller_name: str, campaign_title: str, message_preview: str) -> bool:
    """Notify the buyer about a new reply from the seller."""
    preview = message_preview[:60] + "..." if len(message_preview) > 60 else message_preview
    return send_push_notification(
        expo_push_token=buyer_token,
        title=f"💬 {seller_name} replied",
        body=f"Re: {campaign_title} — {preview}",
        data={"type": "NEW_MESSAGE", "screen": "BuyerInbox"},
    )
