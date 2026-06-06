from typing import Protocol, TypeVar

from pydantic import BaseModel

InT = TypeVar("InT", bound=BaseModel, contravariant=True)
OutT = TypeVar("OutT", bound=BaseModel, covariant=True)


class Agent(Protocol[InT, OutT]):
    name: str

    async def run(self, inp: InT) -> OutT: ...
