"""v2 핵심 테이블(7개 + jobs·transcripts·meeting_views·notices·mail_outbox·meeting_speakers·meeting_holds·meeting_closures). Alembic autogenerate 가 모두 보도록 여기서 한꺼번에 불러온다."""
from app.models.account import Account
from app.models.action_item import ActionItem
from app.models.classification import MeetingClassification
from app.models.closure import MeetingClosure
from app.models.event import Event, append_event
from app.models.guest import MeetingGuestParticipant
from app.models.hold import MeetingHold
from app.models.item_closure import ItemClosure
from app.models.job import Job
from app.models.supersession import ItemSupersession
from app.models.mail import MailOutbox
from app.models.meeting import Meeting, MeetingParticipant
from app.models.minutes import MeetingMinutes
from app.models.notice import MeetingView, Notice
from app.models.org import AccountDepartment, Department
from app.models.project import Project, ProjectMember
from app.models.source_document import SourceDocument
from app.models.speaker import MeetingSpeaker
from app.models.tenant import Tenant
from app.models.transcript import Transcript

__all__ = [
    "Account",
    "AccountDepartment",
    "ActionItem",
    "Department",
    "Event",
    "ItemSupersession",
    "ItemClosure",
    "Job",
    "MailOutbox",
    "Meeting",
    "MeetingClassification",
    "MeetingClosure",
    "MeetingGuestParticipant",
    "MeetingHold",
    "MeetingMinutes",
    "MeetingParticipant",
    "MeetingSpeaker",
    "MeetingView",
    "Notice",
    "Project",
    "ProjectMember",
    "SourceDocument",
    "Tenant",
    "Transcript",
    "append_event",
]
