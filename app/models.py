from typing import Literal

from pydantic import BaseModel


class Ticket(BaseModel):
    subject: str
    body: str


class SupportResolution(BaseModel):
    answer: str
    citations: list[int]  # knowledge_chunks.id values actually used
    confidence: float  # 0.0-1.0, model's own self-reported estimate — not a calibrated probability
    action: Literal["resolve", "escalate", "need_info"]
    escalation_reason: str | None = None
