# fived/tests/test_models_f2.py
"""5D-F2 versioned preparation snapshot model tests.

Persistence foundation only — no rates/cost/BOQ/EVM/writeback.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from environments.tests.factories import ProjectFactory
from fived.models import (
    CONTRACT_VERSION_F2,
    FiveDDataModel,
    FiveDModelRow,
)
from fived.services.snapshot_service import BOUNDARY_SNAPSHOT_F2
from fived.tests.factories import (
    FiveDDataModelFactory,
    FiveDModelRowFactory,
    FiveDModelVersionFactory,
)

FORBIDDEN_ROW_FIELDS = (
    "unit_rate",
    "extended_cost",
    "estimated_cost",
    "total_cost",
    "coverage_pct",
    "boq_ready",
    "qs_approved",
    "five_d_ready",
    "evm",
    "task_id",
    "wbs_id",
)


@pytest.mark.django_db
def test_data_model_created_for_project():
    """FiveDDataModel can be created for a project."""
    project = ProjectFactory()
    model = FiveDDataModelFactory(project=project, name="Pilot Prep")
    assert model.pk is not None
    assert model.project_id == project.pk
    assert model.status == FiveDDataModel.Status.DRAFT


@pytest.mark.django_db
def test_contract_version_defaults_to_f2():
    """Contract version defaults to fived-snapshot-f2-v1."""
    model = FiveDDataModelFactory()
    assert model.contract_version == CONTRACT_VERSION_F2
    assert CONTRACT_VERSION_F2 == "fived-snapshot-f2-v1"


@pytest.mark.django_db
def test_version_unique_data_model_and_label():
    """Duplicate data_model + version_label is rejected."""
    data_model = FiveDDataModelFactory()
    FiveDModelVersionFactory(data_model=data_model, version_label="v1")
    with pytest.raises(IntegrityError), transaction.atomic():
        FiveDModelVersionFactory(data_model=data_model, version_label="v1")


@pytest.mark.django_db
def test_multiple_versions_allowed_with_different_labels():
    """Multiple versions allowed for same model with different labels."""
    data_model = FiveDDataModelFactory()
    v1 = FiveDModelVersionFactory(data_model=data_model, version_label="v1")
    v2 = FiveDModelVersionFactory(data_model=data_model, version_label="v2")
    assert v1.pk != v2.pk
    assert data_model.versions.count() == 2


@pytest.mark.django_db
def test_row_unique_version_and_source_row_key():
    """Duplicate version + source_row_key is rejected."""
    version = FiveDModelVersionFactory()
    FiveDModelRowFactory(version=version, source_row_key="same-key")
    with pytest.raises(IntegrityError), transaction.atomic():
        FiveDModelRowFactory(version=version, source_row_key="same-key")


@pytest.mark.django_db
def test_same_source_row_key_allowed_in_different_versions():
    """Same source_row_key may exist on different versions."""
    data_model = FiveDDataModelFactory()
    v1 = FiveDModelVersionFactory(data_model=data_model, version_label="a")
    v2 = FiveDModelVersionFactory(data_model=data_model, version_label="b")
    r1 = FiveDModelRowFactory(version=v1, source_row_key="shared-key")
    r2 = FiveDModelRowFactory(version=v2, source_row_key="shared-key")
    assert r1.source_row_key == r2.source_row_key
    assert r1.version_id != r2.version_id


@pytest.mark.django_db
def test_no_cost_rate_evm_fields_on_row():
    """FiveDModelRow must not expose cost/rate/EVM field names."""
    names = {f.name for f in FiveDModelRow._meta.get_fields()}
    for forbidden in FORBIDDEN_ROW_FIELDS:
        assert forbidden not in names
        assert not any(forbidden in n for n in names)


@pytest.mark.django_db
def test_boundary_snapshot_includes_stage1_flags():
    """Boundary snapshot defaults/creation include no BOQ/cost/EVM/writeback flags."""
    version = FiveDModelVersionFactory(boundary_snapshot=dict(BOUNDARY_SNAPSHOT_F2))
    boundary = version.boundary_snapshot
    assert boundary["not_boq"] is True
    assert boundary["not_qs_certified"] is True
    assert boundary["not_cost_estimate"] is True
    assert boundary["not_evm"] is True
    assert boundary["not_5d_readiness_claim"] is True
    assert boundary["not_writeback"] is True
    assert boundary["not_modify_proposal"] is True
    assert boundary["no_rates"] is True
    assert boundary["no_unit_costs"] is True
    assert boundary["snapshot_stage"] == "stage_1_preparation_snapshot"
