import copy

from api.mixins import JSONSafeSerializerMixin
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from planning.models import PlanningCX, PlanningEmpire
from planning.signup_defaults import SIGNUP_CX_DATA, SIGNUP_CX_NAME, SIGNUP_EMPIRE
from rest_framework import serializers
from user.models import User, UserAPIKey
from user.services.verification_service import VerificationService


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'prun_username', 'fio_apikey']


class UserAPIKeySerializer(serializers.ModelSerializer):
    class Meta:
        model = UserAPIKey
        fields = ['id', 'name', 'prefix', 'created', 'last_used']
        read_only_fields = ['prefix', 'created', 'last_used']


class UserAPIKeyCreateSerializer(serializers.ModelSerializer):
    key = serializers.CharField(read_only=True, max_length=50)

    class Meta:
        model = UserAPIKey
        fields = ['id', 'name', 'key']


class CodeGenericResponseSerializer(serializers.Serializer):
    detail = serializers.CharField()


class VerifyCodeSerializer(serializers.Serializer):
    code = serializers.CharField(min_length=8, max_length=8)


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.CharField(write_only=True, min_length=8, max_length=8)
    new_password = serializers.CharField(write_only=True, style={'input_type': 'password'})

    def validate_new_password(self, value):
        validate_password(value)
        return value

    def validate(self, attrs):
        user, code_obj = VerificationService.get_valid_code_or_raise(attrs['email'], attrs['code'])

        attrs['user_obj'] = user
        attrs['code_obj'] = code_obj

        return attrs

    def save(self, **kwargs):
        user = self.validated_data['user_obj']
        code_obj = self.validated_data['code_obj']
        new_password = self.validated_data['new_password']

        return VerificationService.execute_password_reset(user=user, code_obj=code_obj, new_password=new_password)


class UserRegisterSerializer(serializers.ModelSerializer):
    username = serializers.CharField(required=True, min_length=3)
    password = serializers.CharField(write_only=True, min_length=8, validators=[validate_password])
    email = serializers.EmailField(required=False, allow_null=True, allow_blank=True)

    # "captcha" stuff
    planet_id = serializers.CharField(write_only=True)
    planet_input = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ('username', 'password', 'email', 'planet_id', 'planet_input')

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError('This username is already taken.')
        return value

    def validate_email(self, value):
        # Don't reveal whether an email is already registered (account enumeration):
        # silently drop it so registration still succeeds, just without that email attached.
        if value and User.objects.filter(email__iexact=value).exists():
            return None
        return value

    def validate(self, attrs):
        # validate planet captcha
        PLANET_CHECKLIST = {
            'OT-580b': 'montem',
            'KW-688c': 'etherwind',
            'ZV-759c': 'deimos',
            'ZV-896b': 'harmonia',
            'FK-794b': 'boucher',
            'UV-351c': 'umbra',
            'RC-040b': 'malahat',
            'OT-442b': 'danakil',
            'KW-020c': 'milliways',
        }

        p_id = attrs.get('planet_id')
        p_input = attrs.get('planet_input').strip().lower()

        if PLANET_CHECKLIST.get(p_id) != p_input:
            raise serializers.ValidationError({'planet_input': 'The planet name does not match the provided ID.'})

        return attrs

    def create(self, validated_data):
        # remove planet captcha fields
        validated_data.pop('planet_id')
        validated_data.pop('planet_input')

        with transaction.atomic():
            user = User.objects.create_user(**validated_data)

            # create a cx and an empire, cx first and assign its cx
            cx = PlanningCX.objects.create(user=user, cx_name=SIGNUP_CX_NAME, cx_data=copy.deepcopy(SIGNUP_CX_DATA))
            PlanningEmpire.objects.create(user=user, cx=cx, **SIGNUP_EMPIRE)

        return user


def deep_merge(base, overrides):
    result = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and key in result and isinstance(result[key], dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


LAYOUT_NAVIGATION_STYLES = ['full', 'collapsed']

DEFAULT_PREFERENCES = {
    'locale': 'en_US',
    'default_empire_uuid': None,
    'default_cx_uuid': None,
    'default_buy_items_from_cx': True,
    'burn_days_red': 5,
    'burn_days_yellow': 10,
    'burn_resupply_days': 18,
    'burn_origin': 'Antares Station Warehouse',
    'supply_cart_days': 20,
    'layout_navigation_style': 'full',
    'plan_overrides': {},
}


class PlanOverrideSerializer(serializers.Serializer):
    includeCM = serializers.BooleanField(source='include_cm', default=False)
    visitationMaterialExclusions = serializers.ListField(
        source='visitation_material_exclusions', child=serializers.CharField(), default=list
    )
    autoOptimizeHabs = serializers.BooleanField(source='auto_optimize_habs', default=True)


class UserPreferenceSerializer(JSONSafeSerializerMixin, serializers.Serializer):
    # Frontend JSON: camelCase, variable name
    # Backend JSON: snake_case, source
    locale = serializers.CharField(allow_null=True, required=False)
    defaultEmpireUuid = serializers.UUIDField(source='default_empire_uuid', allow_null=True, required=False)
    defaultCXUuid = serializers.UUIDField(source='default_cx_uuid', allow_null=True, required=False)
    defaultBuyItemsFromCX = serializers.BooleanField(source='default_buy_items_from_cx', required=False)
    burnDaysRed = serializers.IntegerField(source='burn_days_red', min_value=0, default=5, required=False)
    burnDaysYellow = serializers.IntegerField(source='burn_days_yellow', min_value=0, default=10, required=False)
    burnResupplyDays = serializers.IntegerField(source='burn_resupply_days', min_value=0, default=18, required=False)
    burnOrigin = serializers.CharField(source='burn_origin', required=False)
    supplyCartDays = serializers.IntegerField(source='supply_cart_days', min_value=0, default=20, required=False)
    layoutNavigationStyle = serializers.ChoiceField(
        source='layout_navigation_style', choices=LAYOUT_NAVIGATION_STYLES, required=False
    )

    planOverrides = serializers.DictField(source='plan_overrides', child=PlanOverrideSerializer(), required=False)

    def to_representation(self, instance):
        data_from_db = instance if isinstance(instance, dict) else {}
        merged = deep_merge(DEFAULT_PREFERENCES, data_from_db)
        # ChoiceField / allow_null only guard writes, fix up values stored before they existed
        if merged['plan_overrides'] is None:
            merged['plan_overrides'] = {}
        if merged['layout_navigation_style'] not in LAYOUT_NAVIGATION_STYLES:
            merged['layout_navigation_style'] = 'full'
        return super().to_representation(merged)


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'prun_username', 'fio_apikey', 'is_email_verified']

    def update(self, instance, validated_data):
        # update fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()

        return instance


class UserChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True, validators=[validate_password])

    def validate_old_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError('Old password is not correct')
        return value
