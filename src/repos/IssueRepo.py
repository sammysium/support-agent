
from src.exceptions import ChatNotFound
from src.persistence.Database import SessionDep
from src.persistence.models import Issue, IssueStatus
from fastapi import Depends

from typing import Annotated
from sqlmodel import select

class DatabaseRepo:
    def __init__(self, session: SessionDep):
        self.session = session

    def save_chat(self, issue: Issue) -> Issue:
        self.session.add(issue)
        self.session.commit()
        self.session.refresh(issue)
        return issue

    def update_issue_status(self, chat_id: str, new_status: IssueStatus) -> Issue:
        issue = self.get_chat(chat_id=chat_id)
        if issue is None:
            raise ChatNotFound(chat_id = chat_id)
        issue.issue_status = new_status
        return self.save_chat(issue)

    def update_chat(self, chat_id: str, updates: dict) -> Issue:
        """Merge updates into the chat's updates, e.g. {"search": {...}} or {"pick": {...}}."""
        issue = self.get_chat(chat_id=chat_id)
        if issue is None:
            raise ChatNotFound(chat_id = chat_id)
        # a new dict, not an in-place change, or SQLAlchemy doesn't see the JSON column changed
        issue.updates = {**(issue.updates or {}), **updates}
        return self.save_chat(issue)

    def get_chat(self, chat_id: str) -> Issue | None:
        statement = select(Issue).where(Issue.chat_id == chat_id).order_by(Issue.created_at)
        results = list(self.session.exec(statement))
        if len(results) >0:
            return results[0]
        return None


DatabaseRepoDep = Annotated[DatabaseRepo, Depends(DatabaseRepo)]


