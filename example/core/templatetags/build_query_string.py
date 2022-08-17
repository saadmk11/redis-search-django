from django import template

register = template.Library()


@register.simple_tag
def query_string(request, **kwargs):
    """Build URL query string from request.GET."""
    params = request.GET.copy()
    params.pop("page", None)

    if params:
        return "&" + params.urlencode()
    return ""
