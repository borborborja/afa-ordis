from .models import PortalSettings, Role, user_has_role


def role_flags(request):
    user = request.user
    portal = PortalSettings.objects.first()
    return {
        "can_manage_meals": user_has_role(user, Role.ADMIN, Role.MANAGER),
        "can_administer": user_has_role(user, Role.ADMIN),
        "is_management_user": user_has_role(user, Role.ADMIN, Role.MANAGER),
        "is_teacher_user": user_has_role(user, Role.TEACHER),
        "school_menu_url": portal.school_menu_url if portal else "https://agora.xtec.cat/esc-mariapages-ordis/",
    }
