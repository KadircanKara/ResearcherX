from pydantic import BaseModel


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    avatar_color: str
    model_config = {"from_attributes": True}
