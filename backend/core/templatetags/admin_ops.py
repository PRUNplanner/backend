from django import template
from django.template.context import Context

from core.admin_charts import NEUTRAL, STATUS, Fact, Summary
from core.services.task_health import DayCell

register = template.Library()


@register.simple_tag(takes_context=True)
def admin_summary(context: Context) -> Summary | None:
    """The changelist strip of `SummaryStripMixin` admins; None hides it."""
    cl = context.get('cl')
    model_admin = getattr(cl, 'model_admin', None)
    if model_admin is None or not hasattr(model_admin, 'cached_summary'):
        return None
    return model_admin.cached_summary(context['request'])


@register.simple_tag(takes_context=True)
def admin_header(context: Context) -> list[Fact] | None:
    """The change-page header of `ChangeHeaderMixin` admins; nothing on add pages."""
    adminform, original = context.get('adminform'), context.get('original')
    model_admin = getattr(adminform, 'model_admin', None)
    if original is None or model_admin is None or not hasattr(model_admin, 'safe_header'):
        return None
    return model_admin.safe_header(context['request'], original)


@register.filter
def tracker_colour(cell: DayCell) -> str:
    if cell.fail:
        return STATUS['critical']
    if cell.ok:
        return STATUS['good']
    return NEUTRAL


@register.filter
def status_colour(key: str) -> str:
    return STATUS.get(key, NEUTRAL)


TASK_STATE_VARIANTS = {'paused': 'default', 'ok': 'success', 'overdue': 'warning', 'failing': 'danger'}


@register.filter
def state_variant(state: str) -> str:
    return TASK_STATE_VARIANTS.get(state, 'default')
