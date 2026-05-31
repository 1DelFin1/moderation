"""POST /api/v1/events/product — canonical incoming events from B2B.

Canonical flow: moderation-flows.md#receive-product-events (MOD-1)

Accepts the slim-event format {product_id, seller_id, event, date} and
fetches product data from B2B for CREATED/EDITED before persisting.
"""
from __future__ import annotations

import logging
from uuid import UUID, uuid5, NAMESPACE_URL

import httpx
from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import SessionDep, ServiceKeyDep
from app.schemas import ProductEventFromB2B
from app.services.moderation_queue_service import ModerationQueueService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/events", tags=["b2b-events"])

_EVENT_MAP = {
    "CREATED": "PRODUCT_CREATED",
    "EDITED": "PRODUCT_EDITED",
    "DELETED": "PRODUCT_DELETED",
}


async def _fetch_product_from_b2b(product_id: UUID) -> dict:
    from app.core.config import settings

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{settings.B2B_URL}/api/v1/products/{product_id}",
                headers={"X-Service-Key": settings.SERVICE_KEY},
            )
            if resp.status_code >= 400:
                logger.error(
                    "B2B returned %s when fetching product %s for moderation",
                    resp.status_code,
                    product_id,
                )
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Failed to fetch product data from B2B",
                )
            data = resp.json()
            # Strip private seller fields (cost_price, reserved_quantity)
            for sku in data.get("skus", []):
                sku.pop("cost_price", None)
                sku.pop("reserved_quantity", None)
            return data
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error fetching product %s from B2B: %s", product_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="B2B service unavailable",
        )


@router.post("/product", status_code=status.HTTP_200_OK)
async def receive_product_event(
    body: ProductEventFromB2B,
    _: ServiceKeyDep,
    session: SessionDep,
):
    """Receive CREATED / EDITED / DELETED product event from B2B.

    Canonical format (MOD-1):
      {product_id, seller_id, event: CREATED|EDITED|DELETED, date}

    For CREATED/EDITED: fetches product JSON from B2B before persisting.
    Returns 200 OK (idempotent on duplicate).
    """
    event_type_internal = _EVENT_MAP.get(body.event.upper())
    if not event_type_internal:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown event type: {body.event}",
        )

    # Deterministic idempotency key from (product_id, event, date)
    idem_str = f"{body.product_id}:{body.event.upper()}:{body.date.isoformat()}"
    idempotency_key = uuid5(NAMESPACE_URL, idem_str)

    payload: dict = {
        "product_id": str(body.product_id),
        "seller_id": str(body.seller_id),
    }

    if event_type_internal in ("PRODUCT_CREATED", "PRODUCT_EDITED"):
        json_after = await _fetch_product_from_b2b(body.product_id)
        payload["json_after"] = json_after
        payload["category_id"] = (
            str(json_after["category"]["id"])
            if isinstance(json_after.get("category"), dict)
            else None
        )
        # Queue priority: default CREATED=1, EDITED computed by service
        payload["queue_priority"] = 1 if event_type_internal == "PRODUCT_CREATED" else 3

    is_duplicate = await ModerationQueueService.handle_b2b_event(
        session=session,
        event_type=event_type_internal,
        idempotency_key=idempotency_key,
        payload=payload,
    )

    if is_duplicate:
        logger.info("Duplicate product event ignored: %s product_id=%s", body.event, body.product_id)

    return {"accepted": True}
