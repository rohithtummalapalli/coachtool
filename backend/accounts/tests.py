from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase

from .models import MembershipRole, Organization, OrganizationMembership

User = get_user_model()


class UserModelTests(TestCase):
    def test_create_user_with_email_normalizes_email(self):
        user = User.objects.create_user(
            email="TEST@Example.COM",
            username="test_user",
            password="strong-pass-123",
        )
        self.assertEqual(user.email, "test@example.com")

    def test_create_superuser_flags(self):
        user = User.objects.create_superuser(
            email="admin@example.com",
            username="admin_user",
            password="strong-pass-123",
        )
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)


class MembershipModelTests(TestCase):
    def test_membership_unique_per_org_user(self):
        user = User.objects.create_user(
            email="member@example.com",
            username="member_user",
            password="strong-pass-123",
        )
        org = Organization.objects.create(name="Acme Inc")
        OrganizationMembership.objects.create(
            organization=org, user=user, role=MembershipRole.ADMIN
        )
        with self.assertRaises(IntegrityError):
            OrganizationMembership.objects.create(
                organization=org, user=user, role=MembershipRole.MEMBER
            )
