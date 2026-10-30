from datetime import datetime

import orjson
import structlog
from django.db import transaction
from planning.models import PlanningPlan, PlanningPlanVersion, PlanVersionKind
from user.models import User

logger = structlog.get_logger(__name__)

# unnamed saved versions per plan; named ones stay until unnamed
KEEP_UNNAMED = 10
PLAN_NAME_MAX_LENGTH = 200

SNAPSHOT_FIELDS = (
    'plan_name',
    'planet_natural_id',
    'plan_permits_used',
    'plan_cogc',
    'plan_corphq',
    'plan_data',
    'schema_version',
)


def _data_bytes(plan_data: object) -> int:
    return len(orjson.dumps(plan_data))


def _insert(
    plan: PlanningPlan, kind: PlanVersionKind, *, name: str = '', source: PlanningPlanVersion | None = None
) -> PlanningPlanVersion:
    return PlanningPlanVersion.objects.create(
        plan_id=plan.pk,
        user_id=plan.user_id,  # ty: ignore[unresolved-attribute]
        kind=kind,
        name=name,
        source_version=source,
        data_bytes=_data_bytes(plan.plan_data),
        **{field: getattr(plan, field) for field in SNAPSHOT_FIELDS},
    )


def _log_created(version: PlanningPlanVersion, pruned: int = 0) -> None:
    logger.info(
        'plan_version_created',
        plan_uuid=str(version.plan_id),  # ty: ignore[unresolved-attribute]
        kind=version.kind,
        named=bool(version.name),
        data_bytes=version.data_bytes,
        pruned=pruned,
    )


def _prune(plan: PlanningPlan) -> int:
    stale = list(
        PlanningPlanVersion.objects.filter(plan=plan, name='')
        .order_by('-created_at')
        .values_list('pk', flat=True)[KEEP_UNNAMED:]
    )
    if stale:
        # only(): the self FK rules out a fast delete, so the rows get loaded, and they must not bring plan_data
        PlanningPlanVersion.objects.filter(pk__in=stale).only('pk').delete()
    return len(stale)


def record_save(
    plan: PlanningPlan,
    *,
    before: PlanningPlan,
    name: str = '',
    restored_from: PlanningPlanVersion | None = None,
    kind: PlanVersionKind = PlanVersionKind.SAVE,
    keep_draft: bool = False,
) -> PlanningPlanVersion:
    """
    Call inside the save's transaction, holding the plan's row lock: a failure here rolls the save back.
    keep_draft is for writes that aren't the user's: their unsaved edits stay, and the moved modified_at sends them
    through the conflict dialog.
    """
    if not PlanningPlanVersion.objects.filter(plan=plan).exists():
        _log_created(_insert(before, PlanVersionKind.BASELINE))

    if restored_from is not None:
        kind = PlanVersionKind.RESTORE
    version = _insert(plan, kind, name=name, source=restored_from)
    if not keep_draft:
        delete_draft(plan)
    _log_created(version, pruned=_prune(plan))
    return version


def record_created(plan: PlanningPlan) -> PlanningPlanVersion:
    version = _insert(plan, PlanVersionKind.SAVE)
    _log_created(version)
    return version


def rename_version(version: PlanningPlanVersion, name: str) -> PlanningPlanVersion:
    version.name = name
    version.save(update_fields=['name', 'updated_at'])
    logger.info('plan_version_renamed', plan_uuid=str(version.plan_id), named=bool(name))  # ty: ignore[unresolved-attribute]
    return version


def clone_version(version: PlanningPlanVersion, user: User) -> PlanningPlan:
    label = version.name or f'{version.created_at:%Y-%m-%d}'
    suffix = f' ({label})'
    snapshot = {field: getattr(version, field) for field in SNAPSHOT_FIELDS}
    snapshot['plan_name'] = f'{version.plan_name[: PLAN_NAME_MAX_LENGTH - len(suffix)]}{suffix}'

    with transaction.atomic():
        plan = PlanningPlan.objects.create(user=user, **snapshot)
        record_created(plan)
    logger.info('plan_version_cloned', plan_uuid=str(version.plan_id))  # ty: ignore[unresolved-attribute]
    return plan


@transaction.atomic
def restore_version(version: PlanningPlanVersion) -> PlanningPlan:
    """Admin only: writes the plan, so modified_at moves and open editors get the conflict dialog on their next save."""
    before = PlanningPlan.objects.select_for_update().get(pk=version.plan_id)  # ty: ignore[unresolved-attribute]
    plan = PlanningPlan.objects.get(pk=before.pk)
    for field in SNAPSHOT_FIELDS:
        setattr(plan, field, getattr(version, field))
    plan.save()

    # a save may have pruned it while this waited for the lock; its content is already in hand
    source = PlanningPlanVersion.objects.filter(pk=version.pk).first()
    record_save(plan, before=before, restored_from=source, kind=PlanVersionKind.RESTORE, keep_draft=True)
    logger.info('plan_version_restored_admin', plan_uuid=str(plan.pk), version_uuid=str(version.pk))
    return plan


def save_draft(plan: PlanningPlan, fields: dict[str, object], base_modified_at: datetime) -> PlanningPlanVersion:
    """Call holding the plan's row lock. Never saves the plan: modified_at, conflicts and caches stay untouched."""
    draft, _ = PlanningPlanVersion.drafts.update_or_create(
        plan=plan,
        defaults={
            **fields,
            'user_id': plan.user_id,  # ty: ignore[unresolved-attribute]
            'kind': PlanVersionKind.DRAFT,
            'schema_version': plan.schema_version,
            'base_modified_at': base_modified_at,
            'data_bytes': _data_bytes(fields['plan_data']),
        },
    )
    return draft


def delete_draft(plan: PlanningPlan) -> None:
    PlanningPlanVersion.drafts.filter(plan=plan).only('pk').delete()


def move_versions(plan: PlanningPlan) -> None:
    """After a plan changes owner: versions cascade from their user, so the old owner's deletion would take them."""
    PlanningPlanVersion.all_objects.filter(plan=plan).update(user_id=plan.user_id)  # ty: ignore[unresolved-attribute]
