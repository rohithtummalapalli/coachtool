import logging
import os

import requests
from django.contrib.auth.models import Group
from django.contrib.auth import authenticate
from django.shortcuts import get_object_or_404
from rest_framework import generics, viewsets
from rest_framework.permissions import AllowAny
from rest_framework.permissions import IsAuthenticated
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import FavoriteThread, MembershipRole, Organization, OrganizationMembership, User
from .permissions import IsOrgAdminOrStaff, IsStaffUser
from .survey_cache import set_user_survey_dataframe
from .serializers import (
    GroupSerializer,
    MembershipSerializer,
    OrganizationSerializer,
    UserSerializer,
    UserWriteSerializer,
)

logger = logging.getLogger(__name__)


class IsInternalRequest(BasePermission):
    def has_permission(self, request, view):
        expected = os.getenv("CHAINLIT_INTERNAL_API_TOKEN", "").strip()
        provided = (request.headers.get("X-Internal-Token") or "").strip()
        if expected:
            return provided == expected

        remote_addr = (request.META.get("REMOTE_ADDR") or "").strip()
        return remote_addr in {"127.0.0.1", "::1", "localhost"}


def get_survey_token() -> str | None:
    """Obtain a survey API token from static key or OAuth2 client credentials."""
    if survey_key := os.getenv("SURVEY_API_KEY"):
        return survey_key

    try:
        token_url = os.environ["SURVEY_TOKEN_URL"]
        client_id = os.environ["SURVEY_CLIENT_ID"]
        client_secret = os.environ["SURVEY_CLIENT_SECRET"]
    except KeyError:
        logger.info("Survey OAuth environment variables not set.")
        return None

    scope = os.getenv("SURVEY_SCOPE")
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if scope:
        data["scope"] = scope

    try:
        resp = requests.post(token_url, data=data, timeout=10)
        resp.raise_for_status()
        token_json = resp.json()
        return token_json.get("access_token")
    except requests.RequestException as exc:
        logger.error("Survey token request failed: %s", exc)
        return None
    except Exception as exc:
        logger.error("Error processing survey token response: %s", exc)
        return None


def fetch_user_survey_data(user: User):
    survey_url = os.getenv("SURVEY_API_URL") or os.getenv("SURVEY_URL")
    if not survey_url:
        logger.info("SURVEY_API_URL/SURVEY_URL not set, skipping survey data fetch.")
        return []

    organization = getattr(user, "organization", None)
    payload = {
        "jahr": getattr(organization, "year", None),
        "untgscod": getattr(organization, "company_id", None),
        "unitcode": user.team_name or "",
    }

    headers = {"Content-Type": "application/json"}
    token = get_survey_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = requests.post(survey_url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        try:
            data = resp.json()
            logger.info("Survey API fetched data for user=%s", user.email)
            #print(f"[Survey API] fetched data for user={user.email}: {data}", flush=True)
            set_user_survey_dataframe(str(user.id), data)
            return data
        except ValueError:
            logger.warning("Survey API returned non-JSON response for user %s", user.email)
            return []
    except requests.RequestException as exc:
        logger.warning("Survey API fetch failed for user %s: %s", user.email, exc)
        return []


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.prefetch_related("groups").all().order_by("-created_at")
    permission_classes = [IsStaffUser]

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return UserWriteSerializer
        return UserSerializer


class GroupViewSet(viewsets.ModelViewSet):
    queryset = Group.objects.all().order_by("name")
    serializer_class = GroupSerializer
    permission_classes = [IsStaffUser]


class OrganizationViewSet(viewsets.ModelViewSet):
    serializer_class = OrganizationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        queryset = Organization.objects.all().order_by("company_name")
        if user.is_staff:
            return queryset
        return queryset.filter(memberships__user=user, memberships__is_active=True).distinct()


class MembershipViewSet(viewsets.ModelViewSet):
    serializer_class = MembershipSerializer
    permission_classes = [IsOrgAdminOrStaff]

    def get_queryset(self):
        user = self.request.user
        queryset = OrganizationMembership.objects.select_related("organization", "user").all()
        if user.is_staff:
            return queryset.order_by("-created_at")
        return queryset.filter(user=user, is_active=True).order_by("-created_at")


class MeAPIView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = UserSerializer

    def get(self, request, *args, **kwargs):
        user_data = UserSerializer(request.user).data
        memberships = OrganizationMembership.objects.select_related("organization").filter(
            user=request.user, is_active=True
        )
        membership_data = MembershipSerializer(memberships, many=True).data
        is_org_admin = memberships.filter(
            role__in=[MembershipRole.OWNER, MembershipRole.ADMIN]
        ).exists()
        return Response(
            {
                "user": user_data,
                "memberships": membership_data,
                "is_org_admin": is_org_admin,
            }
        )


class ChainlitLoginAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        identifier = str(
            request.data.get("email", "") or request.data.get("username", "")
        ).strip()
        password = str(request.data.get("password", ""))
        if not identifier or not password:
            return Response({"detail": "Username/email and password are required."}, status=400)

        user = authenticate(request, email=identifier.lower(), password=password)
        if not user:
            user = authenticate(request, username=identifier, password=password)
        if not user or not user.is_active:
            return Response({"detail": "Invalid credentials."}, status=401)

        survey_data = fetch_user_survey_data(user)
        organization = getattr(user, "organization", None)
        organization_payload = None
        if organization is not None:
            organization_payload = {
                "id": str(organization.id),
                "year": organization.year,
                "company_id": organization.company_id,
                "company_name": organization.company_name,
                "industry": organization.industry,
                "company_size": organization.company_size,
            }

        return Response(
            {
                "user": {
                    "id": str(user.id),
                    "email": user.email,
                    "username": user.username,
                    "is_staff": user.is_staff,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "organization": organization_payload,
                    "industry": organization.industry if organization is not None else "",
                    "company_size": organization.company_size if organization is not None else "",
                    "team_name": user.team_name,
                },
                "survey_data": survey_data,
            }
        )


class FavoriteThreadAPIView(APIView):
    permission_classes = [IsInternalRequest]

    def get(self, request, *args, **kwargs):
        user_id = str(request.query_params.get("user_id", "")).strip()
        if not user_id:
            return Response({"detail": "user_id is required"}, status=400)

        thread_ids = list(
            FavoriteThread.objects.filter(user_id=user_id)
            .values_list("thread_id", flat=True)
        )
        return Response({"thread_ids": thread_ids})

    def put(self, request, *args, **kwargs):
        user_id = str(request.data.get("user_id", "")).strip()
        thread_id = str(request.data.get("thread_id", "")).strip()
        is_favorite = bool(request.data.get("is_favorite"))

        if not user_id or not thread_id:
            return Response({"detail": "user_id and thread_id are required"}, status=400)

        user = get_object_or_404(User, id=user_id)

        if is_favorite:
            FavoriteThread.objects.get_or_create(user=user, thread_id=thread_id)
        else:
            FavoriteThread.objects.filter(user=user, thread_id=thread_id).delete()

        return Response({"success": True})


class SurveyDataRefreshAPIView(APIView):
    permission_classes = [IsInternalRequest]

    def get(self, request, *args, **kwargs):
        user_id = str(request.query_params.get("user_id", "")).strip()
        if not user_id:
            return Response({"detail": "user_id is required"}, status=400)

        user = get_object_or_404(User, id=user_id)
        survey_data = fetch_user_survey_data(user)
        return Response({"user_id": user_id, "survey_data": survey_data})
