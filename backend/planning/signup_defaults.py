"""What every signup gets (`UserRegisterSerializer.create`); the admin dashboard compares against it."""

from planning.models import PlanningFactionChoices

SIGNUP_CX_NAME = 'My Exchange Preference'
SIGNUP_CX_DATA = {
    'cx_empire': [{'type': 'BOTH', 'exchange': 'UNIVERSE_30D'}],
    'cx_planets': [],
    'ticker_empire': [],
    'ticker_planets': [],
}
SIGNUP_EMPIRE = {
    'empire_name': 'My Empire',
    'empire_faction': PlanningFactionChoices.NONE,
    'empire_permits_used': 1,
    'empire_permits_total': 2,
}
