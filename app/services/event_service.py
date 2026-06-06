import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx

logger = logging.getLogger(__name__)


async def send_result_to_b2b(
    product_id: UUID,
    status: str,  # "MODERATED" | "BLOCKED"
    hard_block: bool,
    blocking_reason: dict | None = None,  # {id, title, comment}
    field_reports: list[dict] | None = None,  # [{field_name, sku_id, comment}]
    occurred_at: datetime | None = None,
) -> None:
    from app.core.config import settings

    if occurred_at is None:
        occurred_at = datetime.now(timezone.utc)

    payload: dict = {
        "idempotency_key": str(uuid4()),
        "product_id": str(product_id),
        "status": status,
    }

    if status == "BLOCKED":
        payload["hard_block"] = hard_block
        payload["blocking_reason"] = blocking_reason or {}
        payload["field_reports"] = field_reports or []

    url = settings.B2B_URL + "/api/v1/events/moderation"

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
