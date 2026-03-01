from rest_framework.permissions import BasePermission, SAFE_METHODS

from .models import MembershipRole, OrganizationMembership


class IsStaffUser(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)


class IsSuperuserOrReadOnly(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return True
        return request.user.is_superuser


class IsOrgAdminOrStaff(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_staff:
            return True
        org_id = request.data.get("organization") or request.query_params.get("organization")
        if not org_id:
            return request.method in SAFE_METHODS
        return OrganizationMembership.objects.filter(
            organization_id=org_id,
            user=user,
            is_active=True,
            role__in=[MembershipRole.OWNER, MembershipRole.ADMIN],
        ).exists()
