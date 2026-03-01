from django.urls import include, path
from rest_framework.routers import DefaultRouter

from mcp_admin.api.views import MCPAuditLogViewSet, MCPServerViewSet, MCPToolViewSet


router = DefaultRouter()
router.register("servers", MCPServerViewSet, basename="mcp-server")
router.register("tools", MCPToolViewSet, basename="mcp-tool")
router.register("audit-logs", MCPAuditLogViewSet, basename="mcp-audit-log")

urlpatterns = [
    path("", include(router.urls)),
]
