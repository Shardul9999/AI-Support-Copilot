from typing import Literal

from pydantic import BaseModel

class Ticket(BaseModel):
    subject: str
    body: str

class SupportResolution(BaseModel):
    answer: str
    citations: list[int]
    confidence: float
    action: Literal["resolve", "escalate", "need_info"]
    escalation_reason: str | None = None
