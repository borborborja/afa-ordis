from .models import Role, user_has_role


def role_flags(request):
    user = request.user
    return {
        "can_manage_meals": user_has_role(user, Role.ADMIN, Role.MANAGER),
        "can_administer": user_has_role(user, Role.ADMIN),
    }
