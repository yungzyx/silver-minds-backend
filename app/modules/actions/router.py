"""Rutas de propuestas, invitaciones, actividades y seguimiento."""

import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentProfile, DbSession
from app.core.errors import NotFoundError
from app.modules.actions import proposals as proposal_repo
from app.modules.actions import service
from app.modules.actions.schemas import (
    ActivityList,
    ActivityOut,
    FeedbackIn,
    FeedbackOut,
    InvitationList,
    InvitationOut,
    InvitationResponse,
    InvitationView,
    ProposalApprove,
    ProposalEdit,
    ProposalList,
    ProposalOut,
    ProposalStatus,
    ReminderList,
    ReminderOut,
)

router = APIRouter()
PROPOSALS = ["propuestas"]
INVITATIONS = ["invitaciones"]
ACTIVITIES = ["actividades"]


@router.get("/proposals", response_model=ProposalList, tags=PROPOSALS, summary="Listar")
def list_proposals(
    db: DbSession,
    profile: CurrentProfile,
    status_filter: ProposalStatus | None = Query(None, alias="status"),
) -> ProposalList:
    found = proposal_repo.list_proposals(db, profile.id, status_filter)
    items = [ProposalOut.model_validate(p) for p in found]
    return ProposalList(items=items, total=len(items))


@router.get(
    "/proposals/{proposal_id}", response_model=ProposalOut, tags=PROPOSALS, summary="Detalle"
)
def get_proposal(proposal_id: uuid.UUID, db: DbSession, profile: CurrentProfile) -> ProposalOut:
    proposal = proposal_repo.get_proposal(db, profile.id, proposal_id)
    if proposal is None:
        raise NotFoundError("La propuesta no existe.")
    return ProposalOut.model_validate(proposal)


@router.patch(
    "/proposals/{proposal_id}",
    response_model=ProposalOut,
    tags=PROPOSALS,
    summary="Editar contenido, destinatario o fecha",
    description="Editar una propuesta aprobada la devuelve a borrador y exige nueva aprobación.",
)
def edit_proposal(
    proposal_id: uuid.UUID, changes: ProposalEdit, db: DbSession, profile: CurrentProfile
) -> ProposalOut:
    return ProposalOut.model_validate(service.edit_proposal(db, profile.id, proposal_id, changes))


@router.post(
    "/proposals/{proposal_id}/approve",
    response_model=ProposalOut,
    tags=PROPOSALS,
    summary="Aprobar",
    description=(
        "Exige la `version` revisada. Si hay un contacto, encola la invitación. "
        "Responde `409 actions_paused` mientras haya una ruta de apoyo activa."
    ),
)
def approve_proposal(
    proposal_id: uuid.UUID, body: ProposalApprove, db: DbSession, profile: CurrentProfile
) -> ProposalOut:
    proposal = service.approve_proposal(db, profile.id, proposal_id, body.version)
    return ProposalOut.model_validate(proposal)


@router.post(
    "/proposals/{proposal_id}/reject",
    response_model=ProposalOut,
    tags=PROPOSALS,
    summary="Rechazar",
)
def reject_proposal(proposal_id: uuid.UUID, db: DbSession, profile: CurrentProfile) -> ProposalOut:
    return ProposalOut.model_validate(service.reject_proposal(db, profile.id, proposal_id))


@router.post(
    "/proposals/{proposal_id}/cancel",
    response_model=ProposalOut,
    tags=PROPOSALS,
    summary="Cancelar una propuesta aprobada",
)
def cancel_proposal(proposal_id: uuid.UUID, db: DbSession, profile: CurrentProfile) -> ProposalOut:
    return ProposalOut.model_validate(service.cancel_proposal(db, profile.id, proposal_id))


@router.post(
    "/proposals/{proposal_id}/feedback",
    response_model=FeedbackOut,
    status_code=status.HTTP_201_CREATED,
    tags=ACTIVITIES,
    summary="Contar cómo resultó",
)
def add_feedback(
    proposal_id: uuid.UUID, data: FeedbackIn, db: DbSession, profile: CurrentProfile
) -> FeedbackOut:
    return FeedbackOut.model_validate(service.add_feedback(db, profile.id, proposal_id, data))


@router.get("/invitations", response_model=InvitationList, tags=INVITATIONS, summary="Listar")
def list_invitations(db: DbSession, profile: CurrentProfile) -> InvitationList:
    items = [InvitationOut.model_validate(i) for i in service.list_invitations(db, profile.id)]
    return InvitationList(items=items, total=len(items))


@router.get(
    "/invitation-responses/{token}",
    response_model=InvitationView,
    tags=INVITATIONS,
    summary="Ver la invitación (público, solo lectura)",
    description="Abrir el enlace no modifica estado.",
)
def view_invitation(token: str, db: DbSession) -> InvitationView:
    return service.invitation_view(db, token)


@router.post(
    "/invitation-responses/{token}",
    response_model=InvitationView,
    tags=INVITATIONS,
    summary="Aceptar o rechazar (público)",
)
def respond_invitation(token: str, body: InvitationResponse, db: DbSession) -> InvitationView:
    service.respond_invitation(db, token, body.response)
    return service.invitation_view(db, token)


@router.get(
    "/activities", response_model=ActivityList, tags=ACTIVITIES, summary="Actividades vigentes"
)
def list_activities(db: DbSession, profile: CurrentProfile) -> ActivityList:
    items = [ActivityOut.model_validate(a) for a in service.list_activities(db, profile.country)]
    return ActivityList(items=items, total=len(items))


@router.get(
    "/activities/{activity_id}", response_model=ActivityOut, tags=ACTIVITIES, summary="Detalle"
)
def get_activity(activity_id: uuid.UUID, db: DbSession, profile: CurrentProfile) -> ActivityOut:
    return ActivityOut.model_validate(service.get_activity(db, profile.country, activity_id))


@router.get(
    "/reminders",
    response_model=ReminderList,
    tags=ACTIVITIES,
    summary="Recordatorios y seguimientos",
)
def list_reminders(db: DbSession, profile: CurrentProfile) -> ReminderList:
    items = [ReminderOut.model_validate(r) for r in service.list_reminders(db, profile.id)]
    return ReminderList(items=items, total=len(items))
