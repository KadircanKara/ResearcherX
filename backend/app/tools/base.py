from typing import Protocol, TypeVar

from pydantic import BaseModel

I = TypeVar("I", bound=BaseModel, contravariant=True)
O = TypeVar("O", bound=BaseModel, covariant=True)


class Tool(Protocol[I, O]):
    name: str

    async def __call__(self, inp: I) -> O: ...
