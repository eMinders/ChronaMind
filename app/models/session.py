"""This file contains the session model for the application."""

from typing import (
    TYPE_CHECKING,
    Optional,
)

from sqlmodel import (
    Field,
    Relationship,
)

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.user import User


class Session(BaseModel, table=True):
    """Session model for storing chat sessions.

    Attributes:
        id: The primary key
        user_id: Foreign key to the user
        name: Name of the session (defaults to empty string)
        username: Display name copied from the user at session creation
        history_epoch: How many times the session's chat history has been cleared. A message's
            identity is (session, position, role, text), and clearing the history restarts the
            positions, so without this a repeated message after a clear would collide with the
            old claim and silently replay instead of superseding it (V2-D121 addendum).
        created_at: When the session was created
        messages: Relationship to session messages
        user: Relationship to the session owner
    """

    id: str = Field(primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    name: str = Field(default="")
    username: Optional[str] = Field(default=None)
    history_epoch: int = Field(default=0, ge=0)
    user: "User" = Relationship(back_populates="sessions")
