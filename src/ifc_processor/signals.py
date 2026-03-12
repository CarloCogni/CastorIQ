# ifc_processor/signals.py
"""Signal handlers for the ifc_processor app."""

import logging

from django.db.models.signals import post_delete
from django.dispatch import receiver

from ifc_processor.models import IFCFile

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=IFCFile)
def delete_ifc_file(sender, instance, **kwargs):
    """Delete the physical file from storage when an IFCFile record is removed."""
    if instance.file:
        try:
            instance.file.delete(save=False)
            logger.info("Deleted IFC file: %s", instance.file.name)
        except Exception:
            logger.exception("Failed to delete IFC file: %s", instance.file.name)
