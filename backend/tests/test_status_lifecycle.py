"""
API status lifecycle: 保存并生成 (submit_review→待验证) → 激活发布
(activate→已发布) → 停用 (deactivate→已停用), and list visibility:
pending_review shows, pending_first_doc stays hidden.
"""

from __future__ import annotations

import uuid


def _make_api(db, *, status):
    from app.models.api_definition import ApiDefinition, ApiDefinitionStatus
    api = ApiDefinition(
        id=uuid.uuid4(),
        name="lifecycle_test",
        api_code=f"life-{uuid.uuid4().hex[:6]}",
        description="",
        status=status.value if isinstance(status, ApiDefinitionStatus) else status,
        version=1,
        processor_type="mock",
        model_name="mock",
        response_schema={"type": "object", "properties": {}},
    )
    db.add(api)
    db.commit()
    return api


def test_submit_review_then_activate_then_deactivate(db_session):
    from app.models.api_definition import ApiDefinitionStatus
    from app.schemas.api_definition import UpdateApiStatusRequest
    from app.services import api_definition_service as svc

    api = _make_api(db_session, status=ApiDefinitionStatus.pending_first_doc)

    r1 = svc.update_api_status(db_session, api.id, UpdateApiStatusRequest(action="submit_review"))
    assert r1.status == ApiDefinitionStatus.pending_review.value

    r2 = svc.update_api_status(db_session, api.id, UpdateApiStatusRequest(action="activate"))
    assert r2.status == ApiDefinitionStatus.active.value

    r3 = svc.update_api_status(db_session, api.id, UpdateApiStatusRequest(action="deactivate"))
    assert r3.status == ApiDefinitionStatus.deprecated.value

    # deprecated can be re-activated
    r4 = svc.update_api_status(db_session, api.id, UpdateApiStatusRequest(action="activate"))
    assert r4.status == ApiDefinitionStatus.active.value


def test_invalid_action_rejected(db_session):
    from app.core.exceptions import ValidationError
    from app.models.api_definition import ApiDefinitionStatus
    from app.schemas.api_definition import UpdateApiStatusRequest
    from app.services import api_definition_service as svc
    import pytest

    api = _make_api(db_session, status=ApiDefinitionStatus.draft)
    with pytest.raises(ValidationError):
        svc.update_api_status(db_session, api.id, UpdateApiStatusRequest(action="bogus"))


def test_list_shows_pending_review_hides_pending_first_doc(db_session):
    from app.models.api_definition import ApiDefinitionStatus
    from app.services import api_definition_service as svc

    review = _make_api(db_session, status=ApiDefinitionStatus.pending_review)
    placeholder = _make_api(db_session, status=ApiDefinitionStatus.pending_first_doc)

    page = svc.list_api_definitions(db_session, page=1, page_size=100, include_pending=False)
    ids = {i.id for i in page.items}
    assert review.id in ids, "待验证 API must appear in the default list"
    assert placeholder.id not in ids, "pending_first_doc placeholder must stay hidden"
