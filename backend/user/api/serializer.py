import copy
import re
from datetime import datetime

import structlog
from api.mixins import JSONSafeSerializerMixin
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from drf_spectacular.utils import extend_schema_field
from gamedata.fio.services import get_fio_service
from gamedata.services.fio_refresh import FIO_STATUSES, FioStatus, fio_connection
from planning.models import PlanningCX, PlanningEmpire
from planning.signup_defaults import SIGNUP_CX_DATA, SIGNUP_CX_NAME, SIGNUP_EMPIRE
from rest_framework import serializers
from user.models import User, UserAPIKey

logger = structlog.get_logger(__name__)


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
    # machine-readable result, see verification_service.CODE_DETAILS
    code = serializers.CharField(required=False)


class VerifyCodeSerializer(serializers.Serializer):
    code = serializers.CharField(min_length=8, max_length=8)


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    # the code is checked by the viewset, so its result keeps the {detail, code} shape
    email = serializers.EmailField()
    code = serializers.CharField(write_only=True, min_length=8, max_length=8)
    new_password = serializers.CharField(write_only=True, style={'input_type': 'password'})

    def validate_new_password(self, value):
        validate_password(value)
        return value


def normalize_email(value: str | None) -> str | None:
    """Stored form of an address: trimmed, domain lower-cased, empty as None."""
    value = (value or '').strip()
    return User.objects.normalize_email(value) if value else None


# signup "captcha": planet id -> name the user has to type
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
            raise serializers.ValidationError('This username is already taken.', code='username_taken')
        return value

    def validate_email(self, value):
        # Don't reveal whether an email is already registered (account enumeration):
        # silently drop it so registration still succeeds, just without that email attached.
        value = normalize_email(value)
        if value and User.objects.filter(email__iexact=value).exists():
            return None
        return value

    def validate(self, attrs):
        # validate planet captcha
        p_id = attrs.get('planet_id')
        p_input = attrs.get('planet_input').strip().lower()

        if PLANET_CHECKLIST.get(p_id) != p_input:
            raise serializers.ValidationError(
                {'planet_input': 'The planet name does not match the provided ID.'}, code='planet_mismatch'
            )

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
COLOR_PALETTES = ['default', 'colorblind']
MARKET_CARD_TRIGGERS = ['hover', 'click']
EMAIL_NUDGE_DISMISSALS = ['later', 'never']

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
    'color_palette': 'default',
    'market_card_trigger': 'hover',
    'plan_overrides': {},
    'plan_suggestions': True,
    'fio_repair_interval_days': 60,
    'fio_repair_due_soon_days': 7,
    'fio_repair_arrival_days': 2,
    'fio_repair_plan_links': {},
    'email_nudge_dismissal': None,
    'email_nudge_dismissed_at': None,
}


BUILDING_TICKER = re.compile(r'[A-Z0-9]{1,3}')
MAX_CONSTRUCTION_BUILT = 100
FIO_SITE_ID = re.compile(r'[0-9a-f]{32}')
MAX_FIO_REPAIR_PLAN_LINKS = 200


class PlanOverrideSerializer(serializers.Serializer):
    includeCM = serializers.BooleanField(source='include_cm', default=False)
    visitationMaterialExclusions = serializers.ListField(
        source='visitation_material_exclusions', child=serializers.CharField(), default=list
    )
    autoOptimizeHabs = serializers.BooleanField(source='auto_optimize_habs', default=True)
    constructionBuilt = serializers.DictField(
        source='construction_built', child=serializers.IntegerField(min_value=0), default=dict
    )

    def validate_constructionBuilt(self, value: dict[str, int]) -> dict[str, int]:
        if len(value) > MAX_CONSTRUCTION_BUILT:
            raise serializers.ValidationError(f'At most {MAX_CONSTRUCTION_BUILT} buildings.')
        bad = [key for key in value if not BUILDING_TICKER.fullmatch(key)]
        if bad:
            raise serializers.ValidationError(f'Invalid building tickers: {", ".join(bad)}')
        return value


class FIORepairPlanLinkSerializer(serializers.Serializer):
    # null: the user chose "no plan" for the site
    planUuid = serializers.UUIDField(source='plan_uuid', allow_null=True)


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
    supplyCartDays = serializers.FloatField(source='supply_cart_days', min_value=0, default=20, required=False)
    layoutNavigationStyle = serializers.ChoiceField(
        source='layout_navigation_style', choices=LAYOUT_NAVIGATION_STYLES, required=False
    )
    colorPalette = serializers.ChoiceField(source='color_palette', choices=COLOR_PALETTES, required=False)
    marketCardTrigger = serializers.ChoiceField(
        source='market_card_trigger', choices=MARKET_CARD_TRIGGERS, required=False
    )
    planSuggestions = serializers.BooleanField(source='plan_suggestions', required=False)

    planOverrides = serializers.DictField(
        source='plan_overrides', child=PlanOverrideSerializer(allow_null=True), required=False
    )

    fioRepairIntervalDays = serializers.IntegerField(
        source='fio_repair_interval_days', min_value=1, max_value=180, required=False
    )
    fioRepairDueSoonDays = serializers.IntegerField(
        source='fio_repair_due_soon_days', min_value=0, max_value=60, required=False
    )
    fioRepairArrivalDays = serializers.IntegerField(
        source='fio_repair_arrival_days', min_value=0, max_value=30, required=False
    )
    # keyed by FIO SiteId
    fioRepairPlanLinks = serializers.DictField(
        source='fio_repair_plan_links', child=FIORepairPlanLinkSerializer(allow_null=True), required=False
    )
    # "add your email" nudge, null: not dismissed
    emailNudgeDismissal = serializers.ChoiceField(
        source='email_nudge_dismissal', choices=EMAIL_NUDGE_DISMISSALS, allow_null=True, required=False
    )
    emailNudgeDismissedAt = serializers.DateTimeField(
        source='email_nudge_dismissed_at', allow_null=True, required=False
    )

    def validate_fioRepairPlanLinks(self, value: dict[str, dict | None]) -> dict[str, dict | None]:
        if len(value) > MAX_FIO_REPAIR_PLAN_LINKS:
            raise serializers.ValidationError(f'At most {MAX_FIO_REPAIR_PLAN_LINKS} sites.')
        bad = [key for key in value if not FIO_SITE_ID.fullmatch(key)]
        if bad:
            raise serializers.ValidationError(f'Invalid site ids: {", ".join(bad)}')
        # a partial PATCH makes nested fields optional too, so planUuid's presence is checked here
        if any(link is not None and 'plan_uuid' not in link for link in value.values()):
            raise serializers.ValidationError('planUuid is required.')
        return value

    def to_representation(self, instance):
        data_from_db = instance if isinstance(instance, dict) else {}
        merged = deep_merge(DEFAULT_PREFERENCES, data_from_db)
        # ChoiceField / allow_null only guard writes, fix up values stored before they existed
        for keyed in ('plan_overrides', 'fio_repair_plan_links'):
            if merged[keyed] is None:
                merged[keyed] = {}
        if merged['layout_navigation_style'] not in LAYOUT_NAVIGATION_STYLES:
            merged['layout_navigation_style'] = 'full'
        if merged['email_nudge_dismissal'] not in (*EMAIL_NUDGE_DISMISSALS, None):
            merged['email_nudge_dismissal'] = None
        if merged['color_palette'] not in COLOR_PALETTES:
            merged['color_palette'] = 'default'
        if merged['market_card_trigger'] not in MARKET_CARD_TRIGGERS:
            merged['market_card_trigger'] = 'hover'
        return super().to_representation(merged)


FIO_FIELDS = ('fio_apikey', 'prun_username')


class UserProfileSerializer(serializers.ModelSerializer):
    fio_status = serializers.SerializerMethodField()
    fio_last_refreshed_at = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id',
            'username',
            'email',
            'prun_username',
            'fio_apikey',
            'is_email_verified',
            'fio_status',
            'fio_last_refreshed_at',
        ]
        # the model's exact-match UniqueValidator would let case variants through; validate_email checks instead
        extra_kwargs = {'email': {'validators': []}}

    def validate_email(self, value: str | None) -> str | None:
        """Errors carry a code as their message."""
        value = normalize_email(value)
        if value and User.objects.filter(email__iexact=value).exclude(pk=getattr(self.instance, 'pk', None)).exists():
            raise serializers.ValidationError('email_taken', code='email_taken')
        return value

    def to_representation(self, instance: User) -> dict[str, object]:
        # one query for both fields
        self._fio = fio_connection(instance)
        return super().to_representation(instance)

    @extend_schema_field(serializers.ChoiceField(choices=FIO_STATUSES))
    def get_fio_status(self, instance: User) -> FioStatus:
        return self._fio[0]

    @extend_schema_field(serializers.DateTimeField(allow_null=True))
    def get_fio_last_refreshed_at(self, instance: User) -> datetime | None:
        return self._fio[1]

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        """Changed FIO credentials must come as a pair that FIO confirms. Errors carry a code as their message."""
        new = {f: str(attrs.get(f, getattr(self.instance, f)) or '').strip() for f in FIO_FIELDS}
        if all(new[f] == (getattr(self.instance, f) or '').strip() for f in FIO_FIELDS):
            return attrs
        if not any(new.values()):
            return attrs  # disconnect
        empty = [f for f in FIO_FIELDS if not new[f]]
        if empty:
            raise serializers.ValidationError({f: 'fio_required' for f in empty})

        with get_fio_service() as fio:
            result = fio.verify_credentials(new['prun_username'], new['fio_apikey'])
        user_id = self.instance.pk if self.instance else None
        logger.info('fio_verify_completed', result=result, user_id=user_id)
        if result == 'invalid_key':
            raise serializers.ValidationError({'fio_apikey': 'fio_invalid_key'})
        if result == 'username_mismatch':
            raise serializers.ValidationError({'prun_username': 'fio_username_mismatch'})
        if result == 'unavailable':
            # FIO down must not block saving; the refresh task reports a bad key later
            logger.warning('fio_verify_unavailable', user_id=user_id)
        return attrs

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
