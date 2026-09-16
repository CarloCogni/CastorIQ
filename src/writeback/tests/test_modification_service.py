# writeback/tests/test_modification_service.py
"""``propose_in_session`` owns the chat bookkeeping for both transports.

The pipeline is stubbed at ``ModificationService.propose``; the messages, the
supersede, the link and the session title are real.
"""

from unittest.mock import patch

import pytest

from chat.models import ChatSession, Message
from ifc_processor.tests.factories import IFCFileFactory
from writeback.models import ModificationProposal
from writeback.services.errors import ModificationError, NoChangeError
from writeback.services.modification_service import ModificationService
from writeback.tests.factories import ModificationProposalFactory


@pytest.fixture
def session():
    ifc_file = IFCFileFactory()
    return ChatSession.objects.create(
        project=ifc_file.project,
        user=ifc_file.project.owner,
        mode=ChatSession.Mode.MODIFY,
        title="New Modification",
    )


def _messages(session):
    return [(m.role, m.content) for m in session.messages.order_by("created_at")]


@pytest.mark.django_db
def test_success_records_both_messages_links_the_proposal_and_titles_the_session(session):
    project, user = session.project, session.user
    from ifc_processor.models import IFCFile

    ifc_file = IFCFile.objects.get(project=project)
    prior = ModificationProposalFactory(ifc_file=ifc_file, created_by=user)
    prior.message = Message.objects.create(
        session=session, role=Message.Role.ASSISTANT, content="old"
    )
    prior.save()
    new = ModificationProposalFactory(ifc_file=ifc_file, created_by=user, explanation="Sets it.")
    svc = ModificationService(project, user=user)

    with patch.object(ModificationService, "propose", return_value=new) as propose:
        proposal, superseded = svc.propose_in_session(
            session, "Set fire rating to EI120", user, conflict_ids="c1, c2", skip_guardian=True
        )

    assert proposal == new and superseded == [str(prior.id)]
    assert propose.call_args.kwargs["skip_guardian"] is True
    prior.refresh_from_db()
    assert prior.status == ModificationProposal.Status.SUPERSEDED
    new.refresh_from_db()
    assert new.message.content == "Sets it." and new.linked_conflict_ids == ["c1", "c2"]
    session.refresh_from_db()
    assert session.title == "Set fire rating to EI120"
    assert _messages(session)[-2:] == [
        ("user", "Set fire rating to EI120"),
        ("assistant", "Sets it."),
    ]


@pytest.mark.django_db
def test_no_change_and_rejection_are_recorded_as_the_assistants_answer_and_re_raised(session):
    svc = ModificationService(session.project, user=session.user)

    with patch.object(ModificationService, "propose", side_effect=NoChangeError("Already so")):
        with pytest.raises(NoChangeError):
            svc.propose_in_session(session, "x", session.user)
    with patch.object(
        ModificationService, "propose", side_effect=ModificationError("Declined: no")
    ):
        with pytest.raises(ModificationError):
            svc.propose_in_session(session, "y", session.user)

    assert _messages(session) == [
        ("user", "x"),
        ("assistant", "ℹ️ Already so"),
        ("user", "y"),
        ("assistant", "⚠️ Declined: no"),
    ]
