from django.contrib import admin

from mcp_admin.models import MCPAuditLog, MCPServer, MCPTool


class MCPToolInline(admin.TabularInline):
    model = MCPTool
    extra = 0


@admin.register(MCPServer)
class MCPServerAdmin(admin.ModelAdmin):
    list_display = ("name", "base_url", "auth_type", "is_enabled", "updated_at")
    list_filter = ("is_enabled", "auth_type")
    search_fields = ("name", "base_url")
    inlines = [MCPToolInline]


@admin.register(MCPTool)
class MCPToolAdmin(admin.ModelAdmin):
    list_display = ("name", "server", "is_enabled", "rate_limit_per_minute", "timeout_seconds", "updated_at")
    list_filter = ("is_enabled", "server")
    search_fields = ("name", "server__name")
    autocomplete_fields = ("server",)


@admin.register(MCPAuditLog)
class MCPAuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "status", "actor", "server", "tool")
    list_filter = ("status", "action", "created_at")
    search_fields = ("action", "actor__email", "server__name", "tool__name")
    readonly_fields = ("created_at",)

    def has_add_permission(self, request):
        return False

