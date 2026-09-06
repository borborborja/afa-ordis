"""Session-scoped selection of the portal interface.

An administrator who is also linked to a family can inspect the ordinary
family experience without changing their account roles or permissions.
"""

from .models import Family, Role, user_has_role


FAMILY_INTERFACE_MODE_SESSION_KEY = "cafeteria_interface_mode"
FAMILY_INTERFACE_MODE = "family"
STAFF_INTERFACE_MODE = "staff"


def family_options_for_user(user):
    if not user.is_authenticated:
        return []
    return list(Family.objects.filter(memberships__user=user, active=True))


def has_staff_portal_role(user):
    """Roles whose default navigation is the operational portal."""
    return user_has_role(user, Role.ADMIN, Role.MANAGER, Role.TEACHER)


def can_switch_to_family_interface(user, family_options=None):
    """Only administrators linked to a family receive this optional switch."""
    if family_options is None:
        family_options = family_options_for_user(user)
    return bool(family_options) and user_has_role(user, Role.ADMIN)


def has_selected_family_interface(request, family_options=None):
    """Whether an eligible administrator selected the family interface."""
    return (
        request.session.get(FAMILY_INTERFACE_MODE_SESSION_KEY) == FAMILY_INTERFACE_MODE
        and can_switch_to_family_interface(request.user, family_options)
    )
