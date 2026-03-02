import pytest
from model_bakery import baker
from user.models import VerificationeCodeChoices


@pytest.fixture
def user(db):
    return baker.make('user.User', is_email_verified=False)


@pytest.fixture
def verification_code_factory(db):
    def _make_code(user, purpose=VerificationeCodeChoices.EMAIL_VERIFICATION, **kwargs):

        kwargs.setdefault('is_used', False)
        return baker.make('user.VerificationCode', user=user, purpose=purpose, **kwargs)

    return _make_code
