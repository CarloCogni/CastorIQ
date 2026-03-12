# documents/signals.py
"""Signal handlers for the documents app."""

import logging

from django.db.models.signals import post_delete
from django.dispatch import receiver

from documents.models import Document

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=Document)
def delete_document_file(sender, instance, **kwargs):
    """Delete the physical file from storage when a Document record is removed."""
    if instance.file:
        try:
            instance.file.delete(save=False)
            logger.info("Deleted document file: %s", instance.file.name)
        except Exception:
            logger.exception("Failed to delete document file: %s", instance.file.name)
