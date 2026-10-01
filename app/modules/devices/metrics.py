"""Métricas del panel familiar.

Todas son conteos y marcas de tiempo verificables. No se infieren emociones ni estados
de salud, y no se expone el contenido de las conversaciones ni el estado de seguridad.
"""

from collections import Counter
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.models import (
    ActivityFeedback,
    Contact,
    Device,
    DeviceEvent,
    FamilyAccess,
    Invitation,
    Message,
    Profile,
    Proposal,
)
from app.modules.devices.schemas import (
    CameraStatus,
    DayPoint,
    DeviceStatus,
    FamilyOverview,
    TimelineEntry,
    TodayMetrics,
    WeekMetrics,
)

ONLINE_WINDOW = timedelta(seconds=60)
SERIES_DAYS = 7
TIMELINE_LIMIT = 20
WAKE_KINDS = ("wake_button", "wake_name")
DEFAULT_PERSON_NAME = "Tu familiar"
EVENT_LABELS = {
    "wake_button": "Llamó al asistente con el botón",
    "wake_name": "Llamó al asistente por su nombre",
    "camera_on": "Encendió la cámara",
    "camera_off": "Apagó la cámara",
}


def _count(db: Session, model: type, *conditions: object) -> int:
    return db.execute(select(func.count()).select_from(model).where(*conditions)).scalar_one()


def _device_status(device: Device | None, now: datetime) -> DeviceStatus:
    if device is None:
        return DeviceStatus(name=None, online=False, last_seen_at=None)
    online = device.last_seen_at is not None and now - device.last_seen_at <= ONLINE_WINDOW
    return DeviceStatus(name=device.name, online=online, last_seen_at=device.last_seen_at)


def _today(db: Session, owner_id, day_start: datetime) -> TodayMetrics:
    user_messages = (Message.owner_id == owner_id, Message.role == "user")
    events = (DeviceEvent.owner_id == owner_id, DeviceEvent.created_at >= day_start)
    presence_minutes = db.execute(
        select(func.count(func.distinct(func.date_trunc("minute", DeviceEvent.created_at)))).where(
            *events, DeviceEvent.kind == "presence"
        )
    ).scalar_one()
    return TodayMetrics(
        interactions=_count(db, Message, *user_messages, Message.created_at >= day_start),
        calls_by_button=_count(db, DeviceEvent, *events, DeviceEvent.kind == "wake_button"),
        calls_by_name=_count(db, DeviceEvent, *events, DeviceEvent.kind == "wake_name"),
        presence_minutes=presence_minutes,
        last_interaction_at=db.execute(
            select(func.max(Message.created_at)).where(*user_messages)
        ).scalar_one(),
        last_presence_at=db.execute(
            select(func.max(DeviceEvent.created_at)).where(
                DeviceEvent.owner_id == owner_id, DeviceEvent.kind == "presence"
            )
        ).scalar_one(),
    )


def _week(db: Session, owner_id, since: datetime) -> WeekMetrics:
    return WeekMetrics(
        activities_approved=_count(
            db, Proposal, Proposal.owner_id == owner_id, Proposal.approved_at >= since
        ),
        activities_done=_count(
            db,
            ActivityFeedback,
            ActivityFeedback.owner_id == owner_id,
            ActivityFeedback.happened.is_(True),
            ActivityFeedback.created_at >= since,
        ),
        invitations_sent=_count(
            db, Invitation, Invitation.owner_id == owner_id, Invitation.sent_at >= since
        ),
        invitations_accepted=_count(
            db,
            Invitation,
            Invitation.owner_id == owner_id,
            Invitation.status == "accepted",
            Invitation.responded_at >= since,
        ),
    )


def _series(db: Session, owner_id, now: datetime, zone: ZoneInfo) -> list[DayPoint]:
    today = now.astimezone(zone).date()
    first_day = today - timedelta(days=SERIES_DAYS - 1)
    since = datetime.combine(first_day, time.min, tzinfo=zone)
    messages = db.execute(
        select(Message.created_at).where(
            Message.owner_id == owner_id, Message.role == "user", Message.created_at >= since
        )
    ).scalars()
    calls = db.execute(
        select(DeviceEvent.created_at).where(
            DeviceEvent.owner_id == owner_id,
            DeviceEvent.kind.in_(WAKE_KINDS),
            DeviceEvent.created_at >= since,
        )
    ).scalars()
    per_day_messages = Counter(stamp.astimezone(zone).date() for stamp in messages)
    per_day_calls = Counter(stamp.astimezone(zone).date() for stamp in calls)
    days = [first_day + timedelta(days=offset) for offset in range(SERIES_DAYS)]
    return [
        DayPoint(day=day, interactions=per_day_messages[day], calls=per_day_calls[day])
        for day in days
    ]


def _timeline(db: Session, access: FamilyAccess, since: datetime) -> list[TimelineEntry]:
    owner_id = access.owner_id
    entries = [
        TimelineEntry(at=at, kind=kind, label=EVENT_LABELS[kind])
        for at, kind in db.execute(
            select(DeviceEvent.created_at, DeviceEvent.kind)
            .where(
                DeviceEvent.owner_id == owner_id,
                DeviceEvent.kind.in_(tuple(EVENT_LABELS)),
                DeviceEvent.created_at >= since,
            )
            .order_by(DeviceEvent.created_at.desc())
            .limit(TIMELINE_LIMIT)
        )
    ]
    # Solo el hecho de aprobar: el contenido de una propuesta no se comparte.
    entries += [
        TimelineEntry(at=at, kind="activity_approved", label="Aprobó una actividad")
        for at in db.execute(
            select(Proposal.approved_at)
            .where(Proposal.owner_id == owner_id, Proposal.approved_at >= since)
            .order_by(Proposal.approved_at.desc())
            .limit(TIMELINE_LIMIT)
        ).scalars()
    ]
    # Las invitaciones dirigidas a quien mira: ya las recibió por correo.
    entries += [
        TimelineEntry(at=at, kind="invitation", label=f"Te envió una invitación: {title}")
        for at, title in db.execute(
            select(Invitation.sent_at, Proposal.title)
            .join(Proposal, Proposal.id == Invitation.proposal_id)
            .where(
                Invitation.owner_id == owner_id,
                Invitation.contact_id == access.contact_id,
                Invitation.sent_at >= since,
            )
            .order_by(Invitation.sent_at.desc())
            .limit(TIMELINE_LIMIT)
        )
    ]
    return sorted(entries, key=lambda entry: entry.at, reverse=True)[:TIMELINE_LIMIT]


def family_overview(
    db: Session, access: FamilyAccess, now: datetime | None = None
) -> FamilyOverview:
    now = now or utcnow()
    profile = db.get(Profile, access.owner_id)
    contact = db.get(Contact, access.contact_id)
    zone = ZoneInfo(profile.timezone)
    day_start = datetime.combine(now.astimezone(zone).date(), time.min, tzinfo=zone)
    week_start = now - timedelta(days=7)
    device = db.execute(
        select(Device)
        .where(Device.owner_id == access.owner_id, Device.status == "active")
        .order_by(Device.last_seen_at.desc().nulls_last())
        .limit(1)
    ).scalar_one_or_none()
    return FamilyOverview(
        person_name=profile.preferred_name or DEFAULT_PERSON_NAME,
        viewer_name=contact.name,
        generated_at=now,
        device=_device_status(device, now),
        camera=CameraStatus(
            allowed=access.can_view_camera,
            sharing=bool(device and device.camera_sharing),
        ),
        today=_today(db, access.owner_id, day_start),
        week=_week(db, access.owner_id, week_start),
        series=_series(db, access.owner_id, now, zone),
        timeline=_timeline(db, access, week_start),
    )
