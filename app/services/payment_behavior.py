"""PaymentBehaviorService — deterministic payment metrics, no LLM math."""

from __future__ import annotations
from datetime import datetime, timedelta
import statistics
from app.models.schemas import PaymentBehavior
from app.repositories.voucher import ReceiptRepository


class PaymentBehaviorService:
    def __init__(self):
        self._receipts = ReceiptRepository()

    async def analyze(self, customer_name: str) -> PaymentBehavior:
        receipts = await self._receipts.get_receipts(customer_name, limit=200)
        active = [r for r in receipts if not r.is_cancelled and r.date]

        if not active:
            return PaymentBehavior()

        dates: list[datetime] = []
        for r in active:
            try:
                dates.append(datetime.fromisoformat(str(r.date).replace(" ", "T").split(".")[0]))
            except ValueError:
                pass

        dates.sort()
        last_payment = dates[-1].isoformat() if dates else None

        intervals: list[float] = []
        for i in range(1, len(dates)):
            intervals.append((dates[i] - dates[i - 1]).days)

        avg = statistics.mean(intervals) if intervals else None
        med = statistics.median(intervals) if intervals else None

        # Typical window: rough qualitative bucket
        window: str | None = None
        if med is not None:
            if med <= 7:
                window = "weekly"
            elif med <= 16:
                window = "fortnightly"
            elif med <= 35:
                window = "monthly"
            else:
                window = f"every ~{int(med)} days"

        # Overdue frequency: fraction of intervals > 30 days
        overdue_freq = (sum(1 for i in intervals if i > 30) / len(intervals)) if intervals else None

        return PaymentBehavior(
            last_payment_date=last_payment,
            average_interval_days=round(avg, 1) if avg is not None else None,
            median_interval_days=round(med, 1) if med is not None else None,
            typical_payment_window=window,
            overdue_frequency=round(overdue_freq, 2) if overdue_freq is not None else None,
            total_payments=len(active),
        )
