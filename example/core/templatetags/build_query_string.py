import json

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag
def query_string(request, **kwargs):
    """Build URL query string from request.GET."""
    params = request.GET.copy()
    params.pop("page", None)

    if params:
        return "&" + params.urlencode()
    return ""


@register.filter
def pretty_json(value):
    return mark_safe(json.dumps(value, indent=2, default=str))
