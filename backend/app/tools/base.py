from typing import Protocol, TypeVar

from pydantic import BaseModel

InT = TypeVar("InT", bound=BaseModel, contravariant=True)
OutT = TypeVar("OutT", bound=BaseModel, covariant=True)


class Tool(Protocol[InT, OutT]):
    name: str

    async def __call__(self, inp: InT) -> OutT: ...
