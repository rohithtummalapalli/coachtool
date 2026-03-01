from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    ChainlitLoginAPIView,
    GroupViewSet,
    MeAPIView,
    MembershipViewSet,
    OrganizationViewSet,
    UserViewSet,
)

router = DefaultRouter()
router.register("users", UserViewSet, basename="user")
router.register("groups", GroupViewSet, basename="group")
router.register("organizations", OrganizationViewSet, basename="organization")
router.register("memberships", MembershipViewSet, basename="membership")

urlpatterns = [
    path("me/", MeAPIView.as_view(), name="me"),
    path("chainlit-login/", ChainlitLoginAPIView.as_view(), name="chainlit-login"),
    path("", include(router.urls)),
]
