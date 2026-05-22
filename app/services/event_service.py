import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx

logger = logging.getLogger(__name__)


async def send_result_to_b2b(
    product_id: UUID,
    event_type: str,  # "MODERATED" | "BLOCKED"
    moderator_id: UUID,
    moderator_comment: str | None,
    hard_block: bool,
    blocking_reason_ids: list[UUID] | None = None,
    occurred_at: datetime | None = None,
) -> None:
    from app.core.config import settings

    if occurred_at is None:
        occurred_at = datetime.now(timezone.utc)

    payload = {
        "idempotency_key": str(uuid4()),
        "product_id": str(product_id),
        "event_type": event_type,
        "moderator_id": str(moderator_id),
        "moderator_comment": moderator_comment,
        "blocking_reason_id": str(blocking_reason_ids[0]) if blocking_reason_ids else None,
        "hard_block": hard_block,
        "occurred_at": occurred_at.isoformat(),
    }

    url = settings.B2B_URL + "/api/v1/moderation/events"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                json=payload,
                headers={"X-Service-Key": settings.SERVICE_KEY},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Failed to send moderation result to B2B: %s", exc)
