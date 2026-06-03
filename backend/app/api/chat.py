"""Conversational analytics API: persisted conversations + SSE streaming."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models
from app.api.auth import get_current_user, get_current_workspace_id
from app.database import SessionLocal, get_db
from app.services import analytics_tools, gemini

router = APIRouter()


class CreateConversationBody(BaseModel):
    title: Optional[str] = None


class SendMessageBody(BaseModel):
    content: str


def _serialize_conversation(c: models.Conversation) -> Dict[str, Any]:
    return {
        "id": c.id,
        "title": c.title or "New conversation",
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


def _serialize_message(m: models.Message) -> Dict[str, Any]:
    return {
        "id": m.id,
        "conversation_id": m.conversation_id,
        "role": m.role,
        "content": m.content,
        "tool_name": m.tool_name,
        "tool_call_id": m.tool_call_id,
        "tool_payload": m.tool_payload,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


@router.post("/conversations")
def create_conversation(
    body: CreateConversationBody = CreateConversationBody(),
    user_id: str = Depends(get_current_user),
    workspace_id: int = Depends(get_current_workspace_id),
    db: Session = Depends(get_db),
):
    conv = models.Conversation(
        workspace_id=workspace_id, user_id=user_id, title=body.title
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return _serialize_conversation(conv)


@router.get("/conversations")
def list_conversations(
    user_id: str = Depends(get_current_user),
    workspace_id: int = Depends(get_current_workspace_id),
    db: Session = Depends(get_db),
):
    # Show: chats I authored (private) + chats anyone in the workspace shared.
    rows = (
        db.query(models.Conversation)
        .filter(
            models.Conversation.workspace_id == workspace_id,
            (
                (models.Conversation.user_id == user_id)
                | (models.Conversation.visibility == "workspace")
            ),
        )
        .order_by(models.Conversation.updated_at.desc())
        .all()
    )
    return [_serialize_conversation(c) for c in rows]


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: int,
    user_id: str = Depends(get_current_user),
    workspace_id: int = Depends(get_current_workspace_id),
    db: Session = Depends(get_db),
):
    conv = (
        db.query(models.Conversation)
        .filter(
            models.Conversation.id == conversation_id,
            models.Conversation.workspace_id == workspace_id,
        )
        .first()
    )
    if conv and conv.visibility == "private" and conv.user_id != user_id:
        conv = None
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = (
        db.query(models.Message)
        .filter(models.Message.conversation_id == conversation_id)
        .order_by(models.Message.id.asc())
        .all()
    )
    return {
        "conversation": _serialize_conversation(conv),
        "messages": [_serialize_message(m) for m in messages],
    }


@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: int,
    user_id: str = Depends(get_current_user),
    workspace_id: int = Depends(get_current_workspace_id),
    db: Session = Depends(get_db),
):
    conv = (
        db.query(models.Conversation)
        .filter(
            models.Conversation.id == conversation_id,
            models.Conversation.workspace_id == workspace_id,
            models.Conversation.user_id == user_id,  # only author can delete
        )
        .first()
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    db.query(models.Message).filter(models.Message.conversation_id == conversation_id).delete()
    db.delete(conv)
    db.commit()
    return {"status": "deleted", "id": conversation_id}


def _load_history(db: Session, conversation_id: int) -> List[Dict[str, Any]]:
    rows = (
        db.query(models.Message)
        .filter(models.Message.conversation_id == conversation_id)
        .order_by(models.Message.id.asc())
        .all()
    )
    return [
        {
            "role": m.role,
            "content": m.content or "",
            "tool_name": m.tool_name,
            "tool_call_id": m.tool_call_id,
            "tool_payload": m.tool_payload,
        }
        for m in rows
    ]


def _sse(event: Dict[str, Any]) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


@router.post("/conversations/{conversation_id}/messages")
def send_message(
    conversation_id: int,
    body: SendMessageBody,
    user_id: str = Depends(get_current_user),
    workspace_id: int = Depends(get_current_workspace_id),
):
    """Persist the user message, stream Gemini's response over SSE, and persist
    the assistant + tool messages as they arrive.

    We open our own DB session inside the generator because StreamingResponse
    keeps yielding after the request handler returns, and the FastAPI
    dependency-injected session would be closed by then.
    """

    def _generate():
        db = SessionLocal()
        try:
            conv = (
                db.query(models.Conversation)
                .filter(
                    models.Conversation.id == conversation_id,
                    models.Conversation.workspace_id == workspace_id,
                )
                .first()
            )
            if conv and conv.visibility == "private" and conv.user_id != user_id:
                conv = None
            if not conv:
                yield _sse({"type": "error", "message": "Conversation not found"})
                return

            # Persist user message
            user_msg = models.Message(
                conversation_id=conversation_id,
                role="user",
                content=body.content,
            )
            db.add(user_msg)

            # Auto-title from first user message
            if not conv.title:
                conv.title = (body.content[:60] + "…") if len(body.content) > 60 else body.content
            db.commit()
            yield _sse({"type": "user_message", "id": user_msg.id})

            history = _load_history(db, conversation_id)

            def _executor(name: str, args: Dict[str, Any]) -> Any:
                return analytics_tools.call_tool(name, db, workspace_id, args)

            assistant_text_buffer: List[str] = []

            for event in gemini.stream_chat(
                history=history,
                tool_declarations=analytics_tools.TOOL_DECLARATIONS,
                tool_executor=_executor,
            ):
                etype = event.get("type")

                if etype == "token":
                    assistant_text_buffer.append(event.get("text", ""))

                elif etype == "tool_call":
                    # Flush any pending assistant text before the tool call
                    if assistant_text_buffer:
                        text = "".join(assistant_text_buffer)
                        assistant_text_buffer = []
                        msg = models.Message(
                            conversation_id=conversation_id,
                            role="assistant",
                            content=text,
                        )
                        db.add(msg)
                        db.commit()

                    call_msg = models.Message(
                        conversation_id=conversation_id,
                        role="assistant",
                        content="",
                        tool_name=event.get("name"),
                        tool_call_id=event.get("call_id"),
                        tool_payload=event.get("args"),
                    )
                    db.add(call_msg)
                    db.commit()

                elif etype == "tool_result":
                    result_msg = models.Message(
                        conversation_id=conversation_id,
                        role="tool",
                        content="",
                        tool_name=event.get("name"),
                        tool_call_id=event.get("call_id"),
                        tool_payload=event.get("result"),
                    )
                    db.add(result_msg)
                    db.commit()

                elif etype == "done":
                    if assistant_text_buffer:
                        text = "".join(assistant_text_buffer)
                        assistant_text_buffer = []
                        msg = models.Message(
                            conversation_id=conversation_id,
                            role="assistant",
                            content=text,
                        )
                        db.add(msg)
                        db.commit()

                yield _sse(event)

        except Exception as exc:  # noqa: BLE001
            yield _sse({"type": "error", "message": str(exc)})
        finally:
            db.close()

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
