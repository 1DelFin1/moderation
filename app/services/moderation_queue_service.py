from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.moderation import (
    ProductBlockingReasonModel,
    TicketModel,
    TicketHistoryModel,
)
from app.schemas import (
    BlockingReasonCreate,
    BlockingReasonUpdate,
    FieldReportSchema,
)

logger = logging.getLogger(__name__)

CLAIM_TTL_MINUTES = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ModerationQueueService:

    # ------------------------------------------------------------------
    # Blocking reasons
    # ------------------------------------------------------------------

    @classmethod
    async def get_blocking_reasons(
        cls,
        session: AsyncSession,
        hard_block: bool | None = None,
        is_active: bool = True,
    ) -> list[ProductBlockingReasonModel]:
        stmt = select(ProductBlockingReasonModel)
        stmt = stmt.where(ProductBlockingReasonModel.is_active.is_(is_active))
        if hard_block is not None:
            stmt = stmt.where(ProductBlockingReasonModel.hard_block.is_(hard_block))
        result = await session.scalars(stmt)
        return list(result.all())

    @classmethod
    async def create_blocking_reason(
        cls,
        session: AsyncSession,
        data: BlockingReasonCreate,
    ) -> ProductBlockingReasonModel:
        reason = ProductBlockingReasonModel(
            id=uuid4(),
            code=data.code,
            title=data.title,
            description=data.description,
            hard_block=data.hard_block,
            is_active=True,
        )
        session.add(reason)
        await session.commit()
        await session.refresh(reason)
        return reason

    @classmethod
    async def update_blocking_reason(
        cls,
        session: AsyncSession,
        reason_id: UUID,
        data: BlockingReasonUpdate,
    ) -> ProductBlockingReasonModel:
        stmt = select(ProductBlockingReasonModel).where(ProductBlockingReasonModel.id == reason_id)
        reason = await session.scalar(stmt)
        if reason is None:
            raise HTTPException(status_code=404, detail="Blocking reason not found")

        if data.title is not None:
            reason.title = data.title
        if data.description is not None:
            reason.description = data.description
        if data.is_active is not None:
            reason.is_active = data.is_active

        session.add(reason)
        await session.commit()
        await session.refresh(reason)
        return reason

    @classmethod
    async def delete_blocking_reason(
        cls,
        session: AsyncSession,
        reason_id: UUID,
    ) -> None:
        stmt = select(ProductBlockingReasonModel).where(ProductBlockingReasonModel.id == reason_id)
        reason = await session.scalar(stmt)
        if reason is None:
            raise HTTPException(status_code=404, detail="Blocking reason not found")
        reason.is_active = False
        session.add(reason)
        await session.commit()

    # ------------------------------------------------------------------
    # Queue (PENDING tickets)
    # ------------------------------------------------------------------

    @classmethod
    async def get_queue(
        cls,
        session: AsyncSession,
        limit: int,
        offset: int,
        queue_priority: int | None = None,
        category_id: UUID | None = None,
        seller_id: UUID | None = None,
    ) -> tuple[list[TicketModel], int]:
        stmt = select(TicketModel).where(TicketModel.status == "PENDING")
        count_stmt = select(func.count(TicketModel.id)).where(TicketModel.status == "PENDING")

        if queue_priority is not None:
            stmt = stmt.where(TicketModel.queue_priority == queue_priority)
            count_stmt = count_stmt.where(TicketModel.queue_priority == queue_priority)
        if category_id is not None:
            stmt = stmt.where(TicketModel.category_id == category_id)
            count_stmt = count_stmt.where(TicketModel.category_id == category_id)
        if seller_id is not None:
            stmt = stmt.where(TicketModel.seller_id == seller_id)
            count_stmt = count_stmt.where(TicketModel.seller_id == seller_id)

        total = await session.scalar(count_stmt) or 0
        stmt = stmt.order_by(
            TicketModel.queue_priority.asc(),
            TicketModel.created_at.asc(),
        ).offset(offset).limit(limit)

        result = await session.scalars(stmt)
        return list(result.all()), total

    @classmethod
    async def claim_next(
        cls,
        session: AsyncSession,
        moderator_id: UUID,
        queue_priority: int | None = None,
        category_ids: list[UUID] | None = None,
    ) -> TicketModel | None:
        # Reject if moderator already has an active IN_REVIEW ticket
        existing = await session.scalar(
            select(TicketModel).where(
                TicketModel.assigned_moderator_id == moderator_id,
                TicketModel.status == "IN_REVIEW",
            )
        )
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail={"code": "ALREADY_IN_REVIEW", "message": "You already have a ticket in review"},
            )

        stmt = (
            select(TicketModel)
            .where(TicketModel.status == "PENDING")
            .order_by(TicketModel.queue_priority.asc(), TicketModel.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if queue_priority is not None:
            stmt = stmt.where(TicketModel.queue_priority == queue_priority)
        if category_ids:
            stmt = stmt.where(TicketModel.category_id.in_(category_ids))

        ticket = await session.scalar(stmt)
        if ticket is None:
            return None

        now = _now()
        ticket.status = "IN_REVIEW"
        ticket.assigned_moderator_id = moderator_id
        ticket.claimed_at = now
        ticket.claim_expires_at = now + timedelta(minutes=CLAIM_TTL_MINUTES)
        ticket.updated_at = now

        history_entry = TicketHistoryModel(
            id=uuid4(),
            ticket_id=ticket.id,
            at=now,
            action="CLAIMED",
            moderator_id=moderator_id,
        )
        session.add(ticket)
        session.add(history_entry)
        await session.commit()

        # Reload with history
        stmt2 = (
            select(TicketModel)
            .where(TicketModel.id == ticket.id)
            .options(selectinload(TicketModel.history))
        )
        return await session.scalar(stmt2)

    # ------------------------------------------------------------------
    # Tickets (list / get)
    # ------------------------------------------------------------------

    @classmethod
    async def list_tickets(
        cls,
        session: AsyncSession,
        limit: int,
        offset: int,
        status: str | None = None,
        moderator_id: UUID | None = None,
        product_id: UUID | None = None,
        seller_id: UUID | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[list[TicketModel], int]:
        stmt = select(TicketModel)
        count_stmt = select(func.count(TicketModel.id))

        filters = [TicketModel.status.notin_(["CLOSED", "CANCELLED"])]
        if status is not None:
            filters.append(TicketModel.status == status.upper())
        if moderator_id is not None:
            filters.append(TicketModel.assigned_moderator_id == moderator_id)
        if product_id is not None:
            filters.append(TicketModel.product_id == product_id)
        if seller_id is not None:
            filters.append(TicketModel.seller_id == seller_id)
        if created_from is not None:
            filters.append(TicketModel.created_at >= created_from)
        if created_to is not None:
            filters.append(TicketModel.created_at <= created_to)

        if filters:
            stmt = stmt.where(*filters)
            count_stmt = count_stmt.where(*filters)

        total = await session.scalar(count_stmt) or 0
        stmt = stmt.order_by(
            TicketModel.queue_priority.asc(),
            TicketModel.created_at.asc(),
        ).offset(offset).limit(limit)

        result = await session.scalars(stmt)
        return list(result.all()), total

    @classmethod
    async def get_by_id(
        cls,
        session: AsyncSession,
        ticket_id: UUID,
    ) -> TicketModel:
        stmt = (
            select(TicketModel)
            .where(TicketModel.id == ticket_id)
            .options(selectinload(TicketModel.history))
        )
        ticket = await session.scalar(stmt)
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket not found")

        # Fetch blocking reasons by stored IDs and attach as transient attribute
        blocking_reasons: list[ProductBlockingReasonModel] = []
        if ticket.blocking_reason_ids:
            stmt_reasons = select(ProductBlockingReasonModel).where(
                ProductBlockingReasonModel.id.in_(ticket.blocking_reason_ids)
            )
            result = await session.scalars(stmt_reasons)
            blocking_reasons = list(result.all())
        ticket._blocking_reasons = blocking_reasons

        return ticket

    @classmethod
    async def get_by_product_id(
        cls,
        session: AsyncSession,
        product_id: UUID,
    ) -> TicketModel:
        """Fetch the active (non-cancelled) ticket for a product. Used by canonical product endpoints."""
        stmt = (
            select(TicketModel)
            .where(
                TicketModel.product_id == product_id,
                TicketModel.status.not_in(["CANCELLED", "CLOSED"]),
            )
            .order_by(TicketModel.created_at.desc())
            .limit(1)
            .options(selectinload(TicketModel.history))
        )
        ticket = await session.scalar(stmt)
        if ticket is None:
            raise HTTPException(status_code=404, detail="Product not found in moderation queue")
        return ticket

    # ------------------------------------------------------------------
    # Ticket actions
    # ------------------------------------------------------------------

    @classmethod
    async def release(
        cls,
        session: AsyncSession,
        ticket_id: UUID,
        moderator_id: UUID,
        is_admin: bool = False,
    ) -> TicketModel:
        ticket = await cls.get_by_id(session, ticket_id)

        if ticket.status != "IN_REVIEW":
            raise HTTPException(status_code=409, detail="Ticket is not IN_REVIEW")

        if not is_admin and ticket.assigned_moderator_id != moderator_id:
            raise HTTPException(status_code=409, detail="Not assigned to you")

        now = _now()
        ticket.status = "PENDING"
        ticket.assigned_moderator_id = None
        ticket.claimed_at = None
        ticket.claim_expires_at = None
        ticket.updated_at = now

        history_entry = TicketHistoryModel(
            id=uuid4(),
            ticket_id=ticket.id,
            at=now,
            action="RELEASED",
            moderator_id=moderator_id,
        )
        session.add(ticket)
        session.add(history_entry)
        await session.commit()

        stmt = (
            select(TicketModel)
            .where(TicketModel.id == ticket.id)
            .options(selectinload(TicketModel.history))
        )
        return await session.scalar(stmt)

    @classmethod
    async def approve(
        cls,
        session: AsyncSession,
        ticket_id: UUID,
        moderator_id: UUID,
        comment: str | None = None,
        is_admin: bool = False,
    ) -> TicketModel:
        ticket = await cls.get_by_id(session, ticket_id)

        if ticket.status != "IN_REVIEW":
            raise HTTPException(status_code=409, detail="Ticket is not IN_REVIEW")

        if not is_admin and ticket.assigned_moderator_id != moderator_id:
            raise HTTPException(status_code=409, detail="Not assigned to you")

        now = _now()
        ticket.status = "APPROVED"
        ticket.decision_at = now
        ticket.decision_comment = comment
        ticket.updated_at = now

        history_entry = TicketHistoryModel(
            id=uuid4(),
            ticket_id=ticket.id,
            at=now,
            action="APPROVED",
            moderator_id=moderator_id,
            comment=comment,
        )
        session.add(ticket)
        session.add(history_entry)
        await session.commit()

        # Send event to B2B
        from app.services.event_service import send_result_to_b2b
        await send_result_to_b2b(
            product_id=ticket.product_id,
            event_type="MODERATED",
            moderator_id=moderator_id,
            moderator_comment=comment,
            hard_block=False,
            occurred_at=now,
        )

        stmt = (
            select(TicketModel)
            .where(TicketModel.id == ticket.id)
            .options(selectinload(TicketModel.history))
        )
        return await session.scalar(stmt)

    @classmethod
    async def block(
        cls,
        session: AsyncSession,
        ticket_id: UUID,
        moderator_id: UUID,
        blocking_reason_ids: list[UUID],
        comment: str | None = None,
        field_reports: list[FieldReportSchema] | None = None,
        is_admin: bool = False,
    ) -> TicketModel:
        ticket = await cls.get_by_id(session, ticket_id)

        if ticket.status != "IN_REVIEW":
            raise HTTPException(status_code=409, detail="Ticket is not IN_REVIEW")

        if not is_admin and ticket.assigned_moderator_id != moderator_id:
            raise HTTPException(status_code=409, detail="Not assigned to you")

        # Load blocking reasons
        stmt = select(ProductBlockingReasonModel).where(
            ProductBlockingReasonModel.id.in_(blocking_reason_ids)
        )
        reasons_result = await session.scalars(stmt)
        reasons = list(reasons_result.all())

        if len(reasons) != len(blocking_reason_ids):
            raise HTTPException(status_code=400, detail="One or more blocking reasons not found")

        any_hard_block = any(r.hard_block for r in reasons)
        new_status = "HARD_BLOCKED" if any_hard_block else "BLOCKED"
        action = "HARD_BLOCKED" if any_hard_block else "BLOCKED"

        now = _now()
        ticket.status = new_status
        ticket.decision_at = now
        ticket.decision_comment = comment
        ticket.updated_at = now
        ticket.field_reports = [fr.model_dump() for fr in field_reports] if field_reports else []
        ticket.blocking_reason_ids = list(blocking_reason_ids) if blocking_reason_ids else []

        history_entry = TicketHistoryModel(
            id=uuid4(),
            ticket_id=ticket.id,
            at=now,
            action=action,
            moderator_id=moderator_id,
            comment=comment,
        )
        session.add(ticket)
        session.add(history_entry)
        await session.commit()

        from app.services.event_service import send_result_to_b2b
        await send_result_to_b2b(
            product_id=ticket.product_id,
            event_type="BLOCKED",
            moderator_id=moderator_id,
            moderator_comment=comment,
            blocking_reason_ids=[r.id for r in reasons],
            hard_block=any_hard_block,
            occurred_at=now,
        )

        stmt = (
            select(TicketModel)
            .where(TicketModel.id == ticket.id)
            .options(selectinload(TicketModel.history))
        )
        return await session.scalar(stmt)

    # ------------------------------------------------------------------
    # B2B event intake
    # ------------------------------------------------------------------

    @classmethod
    async def handle_b2b_event(
        cls,
        session: AsyncSession,
        event_type: str,
        idempotency_key: UUID,
        payload: dict,
    ) -> bool:
        """Returns True if the event was a duplicate (already processed), False otherwise."""
        event_type = event_type.upper()

        # Idempotency check
        existing = await session.scalar(
            select(TicketModel).where(TicketModel.idempotency_key == idempotency_key)
        )
        if existing is not None:
            logger.info("Duplicate idempotency_key %s, skipping", idempotency_key)
            return True

        now = _now()

        if event_type == "PRODUCT_CREATED":
            product_id = UUID(str(payload["product_id"]))
            seller_id = UUID(str(payload["seller_id"]))
            category_id = UUID(str(payload["category_id"])) if payload.get("category_id") else None
            queue_priority = int(payload.get("queue_priority", 3))
            json_after = payload.get("json_after", {})

            ticket = TicketModel(
                id=uuid4(),
                product_id=product_id,
                seller_id=seller_id,
                category_id=category_id,
                kind="CREATE",
                status="PENDING",
                queue_priority=queue_priority,
                json_before=None,
                json_after=json_after,
                idempotency_key=idempotency_key,
                created_at=now,
                updated_at=now,
            )
            history_entry = TicketHistoryModel(
                id=uuid4(),
                ticket_id=ticket.id,
                at=now,
                action="CREATED",
            )
            session.add(ticket)
            session.add(history_entry)
            await session.commit()

        elif event_type == "PRODUCT_EDITED":
            product_id = UUID(str(payload["product_id"]))
            seller_id = UUID(str(payload["seller_id"]))
            category_id = UUID(str(payload["category_id"])) if payload.get("category_id") else None
            queue_priority = int(payload.get("queue_priority", 3))
            json_before = payload.get("json_before", {})
            json_after = payload.get("json_after", {})

            ticket = TicketModel(
                id=uuid4(),
                product_id=product_id,
                seller_id=seller_id,
                category_id=category_id,
                kind="EDIT",
                status="PENDING",
                queue_priority=queue_priority,
                json_before=json_before,
                json_after=json_after,
                idempotency_key=idempotency_key,
                created_at=now,
                updated_at=now,
            )
            history_entry = TicketHistoryModel(
                id=uuid4(),
                ticket_id=ticket.id,
                at=now,
                action="CREATED",
            )
            session.add(ticket)
            session.add(history_entry)
            await session.commit()

        elif event_type == "PRODUCT_DELETED":
            product_id = UUID(str(payload["product_id"]))
            # Cancel all open tickets for this product
            open_statuses = ["PENDING", "IN_REVIEW"]
            stmt = select(TicketModel).where(
                TicketModel.product_id == product_id,
                TicketModel.status.in_(open_statuses),
            )
            result = await session.scalars(stmt)
            tickets = list(result.all())
            for t in tickets:
                t.status = "CANCELLED"
                t.updated_at = now
                cancel_entry = TicketHistoryModel(
                    id=uuid4(),
                    ticket_id=t.id,
                    at=now,
                    action="AUTO_RETURNED",
                )
                session.add(t)
                session.add(cancel_entry)

            # Create a sentinel row so duplicate DELETE events are detected via idempotency_key
            _zero_uuid = UUID("00000000-0000-0000-0000-000000000000")
            sentinel = TicketModel(
                id=uuid4(),
                product_id=product_id,
                seller_id=_zero_uuid,  # placeholder — no seller in DELETE payload
                category_id=None,
                kind="CREATE",
                status="CLOSED",
                queue_priority=4,
                json_before=None,
                json_after={},
                idempotency_key=idempotency_key,
                created_at=now,
                updated_at=now,
            )
            session.add(sentinel)
            await session.commit()

        else:
            logger.warning("Unknown event_type: %s", event_type)

        return False

    # ------------------------------------------------------------------
    # APScheduler: auto-return expired claims
    # ------------------------------------------------------------------

    @classmethod
    async def auto_return_expired(cls, session: AsyncSession) -> int:
        now = _now()
        stmt = select(TicketModel).where(
            TicketModel.status == "IN_REVIEW",
            TicketModel.claim_expires_at < now,
        ).with_for_update(skip_locked=True)

        result = await session.scalars(stmt)
        tickets = list(result.all())

        for ticket in tickets:
            ticket.status = "PENDING"
            ticket.assigned_moderator_id = None
            ticket.claimed_at = None
            ticket.claim_expires_at = None
            ticket.updated_at = now

            history_entry = TicketHistoryModel(
                id=uuid4(),
                ticket_id=ticket.id,
                at=now,
                action="AUTO_RETURNED",
            )
            session.add(ticket)
            session.add(history_entry)

        if tickets:
            await session.commit()
            logger.info("Auto-returned %d expired tickets", len(tickets))

        return len(tickets)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    @classmethod
    async def get_stats_overview(
        cls,
        session: AsyncSession,
        period: str = "today",
    ) -> dict:
        from datetime import date, timedelta as td

        now = _now()
        if period == "today":
            since = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        elif period == "week":
            since = now - td(days=7)
        elif period == "month":
            since = now - td(days=30)
        else:
            since = None

        # Instantaneous counts (no period filter)
        instant_stmt = select(TicketModel.status, func.count(TicketModel.id)).where(
            TicketModel.status.in_(["PENDING", "IN_REVIEW"])
        ).group_by(TicketModel.status)
        instant_result = await session.execute(instant_stmt)
        instant_counts: dict[str, int] = {}
        for status_val, count in instant_result:
            instant_counts[status_val] = count

        # Period-filtered counts for decided statuses
        decided_filters = [TicketModel.status.in_(["APPROVED", "BLOCKED", "HARD_BLOCKED"])]
        if since:
            decided_filters.append(TicketModel.decision_at >= since)
        decided_stmt = select(TicketModel.status, func.count(TicketModel.id)).where(
            *decided_filters
        ).group_by(TicketModel.status)
        decided_result = await session.execute(decided_stmt)
        decided_counts: dict[str, int] = {}
        for status_val, count in decided_result:
            decided_counts[status_val] = count

        # Pending by priority
        stmt2 = (
            select(TicketModel.queue_priority, func.count(TicketModel.id))
            .where(TicketModel.status == "PENDING")
            .group_by(TicketModel.queue_priority)
        )
        result2 = await session.execute(stmt2)
        pending_by_priority: dict[str, int] = {}
        for prio, cnt in result2:
            pending_by_priority[str(prio)] = cnt

        # Avg review time for decided tickets in period
        avg_time = None
        try:
            time_filter = []
            if since:
                time_filter.append(TicketModel.decision_at >= since)

            stmt3 = select(
                func.avg(
                    func.extract("epoch", TicketModel.decision_at) -
                    func.extract("epoch", TicketModel.created_at)
                )
            ).where(
                TicketModel.decision_at.isnot(None),
                *time_filter,
            )
            avg_seconds = await session.scalar(stmt3)
            if avg_seconds is not None:
                avg_time = int(avg_seconds)
        except Exception:
            pass

        return {
            "pending_count": instant_counts.get("PENDING", 0),
            "in_review_count": instant_counts.get("IN_REVIEW", 0),
            "approved_count": decided_counts.get("APPROVED", 0),
            "blocked_count": decided_counts.get("BLOCKED", 0),
            "hard_blocked_count": decided_counts.get("HARD_BLOCKED", 0),
            "avg_review_time_seconds": avg_time,
            "pending_by_priority": pending_by_priority or None,
        }

    @classmethod
    async def get_moderator_stats(
        cls,
        session: AsyncSession,
        period: str = "today",
    ) -> list[dict]:
        from datetime import timedelta as td
        from app.models.moderators import ModeratorModel

        now = _now()
        if period == "today":
            since = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        elif period == "week":
            since = now - td(days=7)
        elif period == "month":
            since = now - td(days=30)
        else:
            since = None

        time_filter = []
        if since:
            time_filter.append(TicketModel.decision_at >= since)

        stmt = (
            select(
                TicketModel.assigned_moderator_id,
                TicketModel.status,
                func.count(TicketModel.id),
            )
            .where(
                TicketModel.assigned_moderator_id.isnot(None),
                TicketModel.status.in_(["APPROVED", "BLOCKED", "HARD_BLOCKED"]),
                *time_filter,
            )
            .group_by(TicketModel.assigned_moderator_id, TicketModel.status)
        )
        result = await session.execute(stmt)

        aggregated: dict[UUID, dict] = {}
        for mod_id, status, count in result:
            if mod_id not in aggregated:
                aggregated[mod_id] = {
                    "moderator_id": mod_id,
                    "moderator_name": None,
                    "decisions_count": 0,
                    "approved_count": 0,
                    "blocked_count": 0,
                    "hard_blocked_count": 0,
                    "avg_review_time_seconds": None,
                    "released_count": 0,
                }
            aggregated[mod_id]["decisions_count"] += count
            if status == "APPROVED":
                aggregated[mod_id]["approved_count"] = count
            elif status == "BLOCKED":
                aggregated[mod_id]["blocked_count"] = count
            elif status == "HARD_BLOCKED":
                aggregated[mod_id]["hard_blocked_count"] = count

        # Enrich with moderator names
        if aggregated:
            mod_stmt = select(ModeratorModel).where(ModeratorModel.id.in_(list(aggregated.keys())))
            mod_result = await session.scalars(mod_stmt)
            for mod in mod_result:
                if mod.id in aggregated:
                    name_parts = [mod.first_name]
                    if mod.last_name:
                        name_parts.append(mod.last_name)
                    aggregated[mod.id]["moderator_name"] = " ".join(name_parts)

        return list(aggregated.values())
