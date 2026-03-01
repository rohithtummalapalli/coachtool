from django.contrib.auth.models import Group
from rest_framework import serializers

from .models import MembershipRole, Organization, OrganizationMembership, User


class GroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = Group
        fields = ("id", "name")


class UserSerializer(serializers.ModelSerializer):
    groups = GroupSerializer(many=True, read_only=True)
    organization_name = serializers.CharField(source="organization.company_name", read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "username",
            "first_name",
            "last_name",
            "organization",
            "organization_name",
            "team_name",
            "is_active",
            "is_staff",
            "is_email_verified",
            "groups",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")


class UserWriteSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, min_length=8)

    class Meta:
        model = User
        fields = (
            "email",
            "username",
            "first_name",
            "last_name",
            "organization",
            "team_name",
            "is_active",
            "is_staff",
            "is_email_verified",
            "password",
        )

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        user = User.objects.create(**validated_data)
        if password:
            user.set_password(password)
            user.save(update_fields=["password"])
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for key, value in validated_data.items():
            setattr(instance, key, value)
        if password:
            instance.set_password(password)
        instance.save()
        return instance


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = (
            "id",
            "year",
            "company_id",
            "company_name",
            "industry",
            "company_size",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")


class MembershipSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)
    organization_name = serializers.CharField(source="organization.company_name", read_only=True)
    role = serializers.ChoiceField(choices=MembershipRole.choices)

    class Meta:
        model = OrganizationMembership
        fields = (
            "id",
            "organization",
            "organization_name",
            "user",
            "user_email",
            "role",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")
