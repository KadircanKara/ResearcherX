"""Chat conversations router."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.identity import get_current_user
from app.db.models import User
from app.db.session import get_session
from app.schemas.chat import ChatRequest, ConversationDetailOut, ConversationOut
from app.services import project_service
from app.services.chat_service import ChatService
from app.services.conversation_service import ConversationService

router = APIRouter(tags=["chat"])

_conv_svc = ConversationService()
chat_service = ChatService()


@router.post(
    "/projects/{project_id}/conversations",
    response_model=ConversationOut,
    status_code=201,
)
async def create_conversation(
    project_id: str,
    payload: ChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ConversationOut:
    await project_service.require_member(db, project_id, user.id, "viewer")
    conv = await _conv_svc.create_conversation(db, project_id, user.id, payload.content)
    return ConversationOut.model_validate(conv)


@router.get("/projects/{project_id}/conversations", response_model=list[ConversationOut])
async def list_conversations(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> list[ConversationOut]:
    await project_service.require_member(db, project_id, user.id, "viewer")
    convs = await _conv_svc.list_conversations(db, project_id)
    return [ConversationOut.model_validate(c) for c in convs]


@router.get(
    "/projects/{project_id}/conversations/{conversation_id}",
    response_model=ConversationDetailOut,
)
async def get_conversation(
    project_id: str,
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ConversationDetailOut:
    await project_service.require_member(db, project_id, user.id, "viewer")
    conv = await _conv_svc.get_conversation(db, conversation_id)
    if conv is None or conv.project_id != project_id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return ConversationDetailOut.model_validate(conv)


@router.delete("/projects/{project_id}/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    project_id: str,
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> Response:
    await project_service.require_member(db, project_id, user.id, "editor")
    conv = await _conv_svc.get_conversation(db, conversation_id)
    if conv is None or conv.project_id != project_id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await db.delete(conv)
    await db.commit()
    return Response(status_code=204)


@router.post("/projects/{project_id}/conversations/{conversation_id}/messages")
async def send_message(
    project_id: str,
    conversation_id: str,
    payload: ChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> EventSourceResponse:
    await project_service.require_member(db, project_id, user.id, "viewer")

    # Verify conversation exists and belongs to this project before streaming.
    conv = await _conv_svc.get_conversation(db, conversation_id)
    if conv is None or conv.project_id != project_id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Persist the user message BEFORE starting the SSE stream.
    # ChatService.respond expects the message already in the conversation.
    await _conv_svc.save_message(db, conversation_id, "user", payload.content)

    async def event_stream() -> AsyncGenerator[dict, None]:
        async for event in chat_service.respond(conversation_id, payload.content):
            yield event

    return EventSourceResponse(event_stream())
