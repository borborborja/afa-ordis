from .interface_mode import (
    can_switch_to_family_interface,
    family_options_for_user,
    has_selected_family_interface,
    has_staff_portal_role,
)
from .models import EconomicSettings, PortalSettings, Role, UserProfile, user_has_role


def role_flags(request):
    user = request.user
    portal = PortalSettings.objects.first()
    family_options = []
    active_family = None
    can_switch_interface = False
    is_family_user = False
    if user.is_authenticated:
        family_options = family_options_for_user(user)
        can_switch_interface = can_switch_to_family_interface(user, family_options)
        is_family_user = bool(family_options) and (
            not has_staff_portal_role(user)
            or has_selected_family_interface(request, family_options)
        )
    if is_family_user:
        active_family_id = request.session.get("cafeteria_active_family_id")
        active_family = next((family for family in family_options if family.id == active_family_id), None)
        if active_family is None and family_options:
            active_family = family_options[0]
            request.session["cafeteria_active_family_id"] = active_family.id
    navigation_state = {}
    can_submit_economic_expenses = False
    if user.is_authenticated:
        try:
            navigation_state = user.profile.navigation_state or {}
            economic_settings = EconomicSettings.objects.first()
            can_submit_economic_expenses = user_has_role(user, Role.ADMIN) or bool(
                (economic_settings and economic_settings.allow_all_users_expense_submissions)
                or user.profile.can_submit_expenses
            )
        except UserProfile.DoesNotExist:
            navigation_state = {}
    return {
        "can_review_health": user_has_role(user, Role.ADMIN, Role.MANAGER),
        "is_kitchen": user_has_role(user, Role.KITCHEN),
        "can_manage_meals": user_has_role(user, Role.ADMIN, Role.MANAGER),
        "can_administer": user_has_role(user, Role.ADMIN),
        "is_management_user": user_has_role(user, Role.ADMIN, Role.MANAGER),
        "is_teacher_user": user_has_role(user, Role.TEACHER),
        "is_family_user": is_family_user,
        "can_switch_interface": can_switch_interface,
        "family_options": family_options,
        "active_family": active_family,
        "navigation_state": navigation_state,
        "school_menu_url": portal.school_menu_url if portal else "https://agora.xtec.cat/esc-mariapages-ordis/",
        "can_submit_economic_expenses": can_submit_economic_expenses,
    }
