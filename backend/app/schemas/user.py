from datetime import datetime

from pydantic import BaseModel


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    avatar_color: str
    model_config = {"from_attributes": True}


class PaperUsage(BaseModel):
    used: int
    limit: int  # 0 = no limit


class TurnUsage(BaseModel):
    used: int
    limit: int  # 0 = no limit
    resets_at: datetime


class UsageOut(BaseModel):
    papers: PaperUsage
    chat_turns_today: TurnUsage
