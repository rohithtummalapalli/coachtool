from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import AuditLog, Organization, OrganizationMembership, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("email",)
    list_display = (
        "email",
        "username",
        "first_name",
        "last_name",
        "organization",
        "team_name",
        "is_staff",
        "is_active",
        "is_email_verified",
        "last_login",
    )
    list_filter = ("is_staff", "is_superuser", "is_active", "is_email_verified", "groups", "organization")
    search_fields = ("email", "username", "first_name", "last_name", "organization__company_name", "team_name")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            "Personal info",
            {
                "fields": (
                    "username",
                    "first_name",
                    "last_name",
                    "organization",
                    "team_name",
                    "is_email_verified",
                )
            },
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined", "created_at", "updated_at")}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "username",
                    "first_name",
                    "last_name",
                    "organization",
                    "team_name",
                    "password1",
                    "password2",
                    "is_staff",
                    "is_superuser",
                ),
            },
        ),
    )
    readonly_fields = ("created_at", "updated_at")


class OrganizationMembershipInline(admin.TabularInline):
    model = OrganizationMembership
    extra = 0
    autocomplete_fields = ("user",)


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = (
        "company_id",
        "company_name",
        "year",
        "industry",
        "company_size",
        "created_at",
        "member_count",
    )
    search_fields = ("company_id", "company_name", "industry", "company_size")
    inlines = [OrganizationMembershipInline]

    @admin.display(description="Members")
    def member_count(self, obj: Organization) -> int:
        return obj.memberships.count()


@admin.register(OrganizationMembership)
class OrganizationMembershipAdmin(admin.ModelAdmin):
    list_display = ("organization", "user", "role", "is_active", "created_at")
    list_filter = ("role", "is_active", "organization")
    search_fields = ("organization__company_name", "user__email", "user__username")
    autocomplete_fields = ("organization", "user")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "actor", "organization", "target_model", "target_id")
    list_filter = ("action", "organization", "created_at")
    search_fields = ("action", "target_model", "target_id", "actor__email")
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
        "actor",
        "organization",
        "action",
        "target_model",
        "target_id",
        "metadata",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
