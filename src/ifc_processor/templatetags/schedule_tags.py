# ifc_processor/templatetags/schedule_tags.py
"""Custom template filters for the dynamic schedule templates."""

from django import template

register = template.Library()


@register.filter
def zip_with_row(columns: list, row: list) -> zip:
    """Zip a ScheduleColumn list with a row value list for use in templates.

    Usage: {% for col, val in result.columns|zip_with_row:row %}
    """
    return zip(columns, row)
