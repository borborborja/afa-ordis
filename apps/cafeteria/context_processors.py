from .models import Family, PortalSettings, Role, user_has_role


def role_flags(request):
    user = request.user
    portal = PortalSettings.objects.first()
    family_options = []
    active_family = None
    if user.is_authenticated and not user_has_role(user, Role.ADMIN, Role.MANAGER, Role.TEACHER):
        family_options = list(Family.objects.filter(memberships__user=user, active=True))
        active_family_id = request.session.get("cafeteria_active_family_id")
        active_family = next((family for family in family_options if family.id == active_family_id), None)
        if active_family is None and family_options:
            active_family = family_options[0]
            request.session["cafeteria_active_family_id"] = active_family.id
    return {
        "can_manage_meals": user_has_role(user, Role.ADMIN, Role.MANAGER),
        "can_administer": user_has_role(user, Role.ADMIN),
        "is_management_user": user_has_role(user, Role.ADMIN, Role.MANAGER),
        "is_teacher_user": user_has_role(user, Role.TEACHER),
        "is_family_user": bool(family_options),
        "family_options": family_options,
        "active_family": active_family,
        "school_menu_url": portal.school_menu_url if portal else "https://agora.xtec.cat/esc-mariapages-ordis/",
    }
