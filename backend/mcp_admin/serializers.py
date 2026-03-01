from rest_framework import serializers

from mcp_admin.models import MCPAuditLog, MCPServer, MCPTool


class MCPToolSerializer(serializers.ModelSerializer):
    class Meta:
        model = MCPTool
        fields = [
            "id",
            "server",
            "name",
            "description",
            "is_enabled",
            "input_schema",
            "rate_limit_per_minute",
            "timeout_seconds",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class MCPServerSerializer(serializers.ModelSerializer):
    tools = MCPToolSerializer(many=True, read_only=True)

    class Meta:
        model = MCPServer
        fields = [
            "id",
            "name",
            "base_url",
            "auth_type",
            "is_enabled",
            "metadata",
            "tools",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "tools"]


class MCPAuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = MCPAuditLog
        fields = [
            "id",
            "actor",
            "server",
            "tool",
            "action",
            "status",
            "details",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]
