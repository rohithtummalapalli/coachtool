from __future__ import annotations

from django.conf import settings
from django.db import models


class MCPServer(models.Model):
    name = models.CharField(max_length=120, unique=True)
    base_url = models.URLField(max_length=500)
    auth_type = models.CharField(max_length=40, blank=True, default="none")
    is_enabled = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_mcp_servers",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class MCPTool(models.Model):
    server = models.ForeignKey(MCPServer, on_delete=models.CASCADE, related_name="tools")
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    is_enabled = models.BooleanField(default=True)
    input_schema = models.JSONField(default=dict, blank=True)
    rate_limit_per_minute = models.PositiveIntegerField(default=60)
    timeout_seconds = models.PositiveIntegerField(default=30)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["server__name", "name"]
        unique_together = ("server", "name")

    def __str__(self) -> str:
        return f"{self.server.name}:{self.name}"


class MCPAuditLog(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mcp_audit_logs",
    )
    server = models.ForeignKey(MCPServer, on_delete=models.SET_NULL, null=True, blank=True)
    tool = models.ForeignKey(MCPTool, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=120)
    status = models.CharField(max_length=40, default="success")
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.action} [{self.status}]"
