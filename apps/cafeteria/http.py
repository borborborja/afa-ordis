from functools import wraps

from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme


def atomic_write(view):
    """Keep state changes atomic without opening write transactions for GET pages."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.method == "POST":
            with transaction.atomic():
                return view(request, *args, **kwargs)
        return view(request, *args, **kwargs)
    return wrapped


def positive_pk(value):
    try:
        number = int(value)
        return number if 0 < number <= 9223372036854775807 else None
    except (ValueError, TypeError):
        return None


def local_redirect(request, fallback):
    target = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(target, {request.get_host()}, require_https=request.is_secure()):
        target = reverse(fallback)
    return redirect(target)


def csv_cell(value):
    """Prevent spreadsheet software from evaluating user-supplied formulas."""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        return "'" + value
    if isinstance(value, str) and value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value
