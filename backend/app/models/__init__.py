"""v2 핵심 테이블(7개 + jobs·transcripts·meeting_views·notices·mail_outbox·meeting_speakers·meeting_holds). Alembic autogenerate 가 모두 보도록 여기서 한꺼번에 불러온다."""
from app.models.account import Account
from app.models.action_item import ActionItem
from app.models.event import Event, append_event
from app.models.hold import MeetingHold
from app.models.job import Job
from app.models.mail import MailOutbox
from app.models.meeting import Meeting, MeetingParticipant
from app.models.notice import MeetingView, Notice
from app.models.source_document import SourceDocument
from app.models.speaker import MeetingSpeaker
from app.models.tenant import Tenant
from app.models.transcript import Transcript

__all__ = [
    "Account",
    "ActionItem",
    "Event",
    "Job",
    "MailOutbox",
    "Meeting",
    "MeetingHold",
    "MeetingParticipant",
    "MeetingSpeaker",
    "MeetingView",
    "Notice",
    "SourceDocument",
    "Tenant",
    "Transcript",
    "append_event",
]
