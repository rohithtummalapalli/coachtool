from rest_framework import viewsets
from rest_framework.permissions import IsAdminUser, IsAuthenticated

from mcp_admin.models import MCPAuditLog, MCPServer, MCPTool
from mcp_admin.serializers import MCPAuditLogSerializer, MCPServerSerializer, MCPToolSerializer


class MCPServerViewSet(viewsets.ModelViewSet):
    queryset = MCPServer.objects.prefetch_related("tools").all().order_by("name")
    serializer_class = MCPServerSerializer
    permission_classes = [IsAdminUser]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class MCPToolViewSet(viewsets.ModelViewSet):
    queryset = MCPTool.objects.select_related("server").all().order_by("server__name", "name")
    serializer_class = MCPToolSerializer
    permission_classes = [IsAdminUser]


class MCPAuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = MCPAuditLog.objects.select_related("actor", "server", "tool").all().order_by("-created_at")
    serializer_class = MCPAuditLogSerializer
    permission_classes = [IsAuthenticated]
