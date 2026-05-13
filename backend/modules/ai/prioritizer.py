"""Lead prioritization service — fetches overdue leads, scores via AI."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from backend.core.config import settings
from backend.shared.ai.budget_tracker import BudgetTracker
from backend.shared.ai.cache import AICache, lead_cache_key, overdue_list_cache_key
from backend.modules.ai.chatter import clean_chatter_body, detect_signals
from backend.shared.ai.client import OpenAIClient
from backend.shared.ai.exceptions import AIFeatureDisabledError, AIInvalidResponseError, BudgetExceededError
from backend.modules.ai.prompts import build_lead_prioritization_prompt, get_system_prompt
from backend.modules.ai.schemas import ChatterMessage, LeadContext, LeadPriority
from backend.shared.odoo.client import OdooClient
from backend.modules.crm.domain import BASE_DOMAIN, get_critical_stage_ids

_OVERDUE_CACHE_TTL = 600  # 10 min for the aggregated list


def _completeness_score(lead: LeadContext) -> int:
    score = 0
    if lead.has_phone:
        score += 1
    if lead.has_mobile:
        score += 1
    if lead.has_email:
        score += 1
    if lead.salesperson_name:
        score += 1
    if lead.team_name:
        score += 1
    return score


def _parse_ai_response(content: str, lead_id: int, model: str, cost: float, cached: bool) -> LeadPriority:
    """Parse JSON from AI response into LeadPriority."""
    try:
        data = json.loads(content.strip())
        score = int(data["score"])
        tier = data["tier"]
        reasoning = str(data.get("reasoning", ""))[:200]
        action = str(data.get("recommended_action", ""))[:100]
        key_signal = str(data.get("key_signal", ""))[:150]
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        raise AIInvalidResponseError(f"Could not parse AI response for lead {lead_id}: {exc}") from exc

    # Derive tier from score if not valid
    valid_tiers = {"critical", "high", "medium", "low", "dead"}
    if tier not in valid_tiers:
        if score >= 90:
            tier = "critical"
        elif score >= 70:
            tier = "high"
        elif score >= 50:
            tier = "medium"
        elif score >= 30:
            tier = "low"
        else:
            tier = "dead"

    return LeadPriority(
        lead_id=lead_id,
        score=max(0, min(100, score)),
        tier=tier,
        reasoning=reasoning,
        recommended_action=action,
        key_signal=key_signal,
        cached=cached,
        cost_usd=cost,
        generated_at=datetime.now(timezone.utc),
        model_used=model,
    )


class LeadPrioritizer:
    """AI-powered lead prioritization service."""

    def __init__(
        self,
        odoo_client: OdooClient,
        ai_client: OpenAIClient,
        budget_tracker: BudgetTracker,
        cache: AICache,
    ) -> None:
        self._odoo = odoo_client
        self._ai = ai_client
        self._budget = budget_tracker
        self._cache = cache
        # Short-lived in-memory cache for the prioritized list — no disk persistence needed
        from cachetools import TTLCache as _TTL
        import threading as _threading
        _oc = _TTL(maxsize=10, ttl=_OVERDUE_CACHE_TTL)
        _lock = _threading.Lock()
        self._overdue_cache_raw = _oc
        self._overdue_cache_lock = _lock

    async def prioritize_single(self, lead: LeadContext, locale: str = "en") -> LeadPriority:
        """Score one lead. Uses per-lead cache. Enforces budget."""
        if not settings.AI_FEATURE_LEAD_PRIORITIZATION:
            raise AIFeatureDisabledError("Lead prioritization feature is disabled")

        completeness = _completeness_score(lead)
        chatter_hash = hashlib.md5(
            "".join(m.body_text for m in lead.recent_messages).encode()
        ).hexdigest()[:8]
        cache_key = lead_cache_key(
            lead.lead_id,
            lead.stage_id,
            lead.last_activity_date,
            completeness,
            chatter_hash,
            locale,
        )

        cached_result = self._cache.get(cache_key)
        if cached_result is not None:
            logger.debug(f"AI cache hit for lead {lead.lead_id}")
            result = cached_result.copy() if isinstance(cached_result, dict) else cached_result
            if isinstance(result, LeadPriority):
                return result.model_copy(update={"cached": True, "cost_usd": 0.0})
            return result

        self._budget.enforce_budget()

        if self._budget.is_near_budget():
            logger.warning(
                f"AI budget warning: {self._budget.current_month_spend():.4f} / "
                f"{self._budget.monthly_budget:.2f} USD used"
            )

        messages = [
            {"role": "system", "content": get_system_prompt(locale)},
            {"role": "user", "content": build_lead_prioritization_prompt(lead, locale)},
        ]

        model = settings.AI_MODEL
        try:
            response = await self._ai.chat_completion(
                messages=messages,
                model=model,
                temperature=0.3,
                max_tokens=200,
                response_format={"type": "json_object"},
            )
        except BudgetExceededError:
            raise
        except Exception as exc:
            logger.error(f"AI call failed for lead {lead.lead_id}: {exc}")
            raise

        result = _parse_ai_response(
            content=response.content,
            lead_id=lead.lead_id,
            model=response.model,
            cost=response.cost_usd,
            cached=False,
        )

        self._cache.set(cache_key, result)
        return result

    async def prioritize_batch(
        self,
        leads: list[LeadContext],
        max_concurrent: int = 5,
        locale: str = "en",
    ) -> list[LeadPriority]:
        """Score multiple leads concurrently. Stops if budget is hit."""
        semaphore = asyncio.Semaphore(max_concurrent)
        results: list[LeadPriority] = []

        async def score_one(lead: LeadContext) -> Optional[LeadPriority]:
            async with semaphore:
                try:
                    return await self.prioritize_single(lead, locale)
                except BudgetExceededError:
                    logger.warning(f"Budget exceeded — skipping lead {lead.lead_id} and remaining")
                    return None
                except Exception as exc:
                    logger.error(f"Failed to score lead {lead.lead_id}: {exc}")
                    return None

        tasks = [score_one(lead) for lead in leads]
        raw = await asyncio.gather(*tasks)

        budget_hit = False
        for item in raw:
            if item is None:
                budget_hit = True
                continue
            results.append(item)

        if budget_hit:
            logger.warning("Batch prioritization stopped early due to budget exhaustion")

        results.sort(key=lambda r: r.score, reverse=True)
        return results

    async def prioritize_overdue(self, limit: int = 50, locale: str = "en") -> list[LeadPriority]:
        """Fetch overdue leads from Odoo and prioritize them."""
        list_key = overdue_list_cache_key(limit, locale)
        with self._overdue_cache_lock:
            cached_list = self._overdue_cache_raw.get(list_key)
        if cached_list is not None:
            logger.debug(f"Overdue priority list cache hit (limit={limit}, locale={locale})")
            return cached_list  # type: ignore[return-value]

        leads = await self._fetch_overdue_leads(limit)
        if not leads:
            return []

        prioritized = await self.prioritize_batch(leads, locale=locale)
        with self._overdue_cache_lock:
            self._overdue_cache_raw[list_key] = prioritized
        return prioritized

    async def _fetch_overdue_leads(self, limit: int) -> list[LeadContext]:
        domain = BASE_DOMAIN + [["activity_state", "=", "overdue"]]
        critical_ids = get_critical_stage_ids()
        now = datetime.now(timezone.utc)

        try:
            rows = await self._odoo.execute_kw(
                "crm.lead",
                "search_read",
                args=[domain],
                kwargs={
                    "fields": [
                        "id",
                        "name",
                        "stage_id",
                        "user_id",
                        "team_id",
                        "create_date",
                        "activity_date_deadline",
                        "phone",
                        "mobile",
                        "email_from",
                        "activity_state",
                    ],
                    "limit": limit,
                },
            )
        except Exception as exc:
            logger.error(f"Failed to fetch overdue leads from Odoo: {exc!r}")
            return []

        if not rows:
            return []

        # Fetch chatter for all leads concurrently
        leads: list[LeadContext] = list(
            await asyncio.gather(
                *[self._build_lead_context(row, critical_ids, now) for row in rows]
            )
        )

        # Critical stages first, then oldest (most overdue) first
        leads.sort(key=lambda l: (not l.is_critical_stage, -l.days_in_stage))
        return leads

    async def _build_lead_context(
        self, row: dict, critical_ids: list, now: datetime
    ) -> LeadContext:
        """Build LeadContext for one Odoo row, fetching chatter concurrently."""
        lead_id = row["id"]

        try:
            messages_raw = await self._odoo.fetch_recent_messages(lead_id, limit=3)
        except Exception as exc:
            logger.warning(f"Could not fetch chatter for lead {lead_id}: {exc!r}")
            messages_raw = []

        messages = [
            ChatterMessage(
                date=_parse_odoo_dt(m.get("date")) or now,
                author=m["author_id"][1] if m.get("author_id") else "System",
                body_text=clean_chatter_body(m.get("body") or ""),
                message_type=m.get("message_type") or "notification",
            )
            for m in messages_raw
        ]

        signals = detect_signals(messages)
        last_msg_date = messages[0].date if messages else None
        days_since = (now - last_msg_date).days if last_msg_date else None

        stage = row.get("stage_id")
        user = row.get("user_id")
        team = row.get("team_id")
        stage_id = stage[0] if stage else 0
        stage_name = stage[1] if stage else "No Stage"

        create_dt = _parse_odoo_dt(row.get("create_date"))
        last_activity_dt = _parse_odoo_dt(row.get("activity_date_deadline"))
        days_in_stage = (now - create_dt).days if create_dt else 0

        return LeadContext(
            lead_id=lead_id,
            name=row.get("name") or f"Lead #{lead_id}",
            stage_id=stage_id,
            stage_name=stage_name,
            salesperson_name=user[1] if user else None,
            team_name=team[1] if team else None,
            create_date=create_dt or now,
            last_activity_date=last_activity_dt,
            days_in_stage=days_in_stage,
            is_critical_stage=stage_id in critical_ids,
            has_phone=bool(row.get("phone")),
            has_mobile=bool(row.get("mobile")),
            has_email=bool(row.get("email_from")),
            activity_state=row.get("activity_state") or "none",
            recent_messages=messages,
            has_site_visit=signals["has_site_visit"],
            has_phone_attempt=signals["has_phone_attempt"],
            last_message_date=last_msg_date,
            days_since_last_message=days_since,
        )


def _parse_odoo_dt(value: object) -> Optional[datetime]:
    if not value or value is False:
        return None
    try:
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace(" ", "T")).replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    return None
