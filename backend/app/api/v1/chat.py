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
from app.services.conversation_service import ConversationService, retitle_citations

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
    await project_service.require_member(db, project_id, user.id, "member")
    conv = await _conv_svc.create_conversation(db, project_id, user.id, payload.content)
    return ConversationOut.model_validate(conv)


@router.get("/projects/{project_id}/conversations", response_model=list[ConversationOut])
async def list_conversations(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> list[ConversationOut]:
    await project_service.require_member(db, project_id, user.id, "member")
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
    await project_service.require_member(db, project_id, user.id, "member")
    conv = await _conv_svc.get_conversation(db, conversation_id)
    if conv is None or conv.project_id != project_id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Citation titles are resolved against the papers table on every read, not
    # served from the snapshot stored with the message. Renaming a paper in the
    # Papers tab has to reach every chip and hover card that points at it, in
    # every past conversation — see retitle_citations for why that is a read
    # concern and not a backfill.
    detail = ConversationDetailOut.model_validate(conv)
    titles = await _conv_svc.current_paper_titles(db, project_id)
    for message in detail.messages:
        message.citations = retitle_citations(message.citations, titles)
    return detail


@router.delete("/projects/{project_id}/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    project_id: str,
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> Response:
    await project_service.require_member(db, project_id, user.id, "member")
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
    await project_service.require_member(db, project_id, user.id, "member")

    # Verify conversation exists and belongs to this project before streaming.
    conv = await _conv_svc.get_conversation(db, conversation_id)
    if conv is None or conv.project_id != project_id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    try:
        mentions = await _conv_svc.validate_mentions(db, project_id, payload.mentioned_paper_ids)
    except ValueError:
        raise HTTPException(status_code=400, detail="Unknown paper in mentions") from None

    # Persist the user message BEFORE starting the SSE stream.
    # ChatService.respond expects the message already in the conversation.
    await _conv_svc.save_message(db, conversation_id, "user", payload.content, mentions=mentions)

    async def event_stream() -> AsyncGenerator[dict, None]:
        async for event in chat_service.respond(conversation_id, payload.content, mentions):
            yield event

    return EventSourceResponse(event_stream())
