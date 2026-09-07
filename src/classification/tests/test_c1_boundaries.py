# classification/tests/test_c1_boundaries.py
"""C1 boundary smoke — other domains remain untouched by registry models."""

from __future__ import annotations

import pytest

from classification.models import ClassificationSchema
from environments.tests.factories import ProjectFactory
from facilities.models import Classification as FacilityClassification
from facilities.models import ClassificationReference
from takeoff.services.quantity_prep_export import CONTRACT_VERSION_V1, EXPORT_KIND


@pytest.mark.django_db
def test_facilities_classification_still_works_independently():
    """FM Classification* remains usable; C1 does not migrate or replace it."""
    project = ProjectFactory()
    system = FacilityClassification.objects.create(
        project=project,
        name="Uniclass 2015",
        edition="demo",
    )
    ref = ClassificationReference.objects.create(
        classification=system,
        code="Ss_25_10_30",
        name="Demo ref",
    )
    assert FacilityClassification.objects.filter(pk=system.pk).exists()
    assert ref.code == "Ss_25_10_30"
    # Platform classification app is a separate registry — no FK to FM tables.
    assert not hasattr(ClassificationSchema, "facility_classification")


def test_qty_prep_export_v1_contract_unchanged():
    """C1 must not alter qty-prep-export-v1 constants."""
    assert CONTRACT_VERSION_V1 == "qty-prep-export-v1"
    assert EXPORT_KIND == "quantity_preparation_model"
