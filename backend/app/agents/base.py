from typing import Protocol, TypeVar

from pydantic import BaseModel

I = TypeVar("I", bound=BaseModel, contravariant=True)
O = TypeVar("O", bound=BaseModel, covariant=True)


class Agent(Protocol[I, O]):
    name: str

    async def run(self, inp: I) -> O: ...
