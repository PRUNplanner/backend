import pytest
from model_bakery import baker
from user.models import User, VerificationeCodeChoices


@pytest.fixture
def user(db) -> User:
    user: User = baker.make('user.User', is_email_verified=False)
    # update(): an email set through save() would already get a verification code from the signal
    User.objects.filter(pk=user.pk).update(email='pilot@example.com')
    user.refresh_from_db()
    return user


@pytest.fixture
def user_factory(**kwargs):
    return lambda **kwargs: baker.make('user.User', **kwargs)


@pytest.fixture
def verification_code_factory(db):
    def _make_code(user, purpose=VerificationeCodeChoices.EMAIL_VERIFICATION, **kwargs):

        kwargs.setdefault('is_used', False)
        return baker.make('user.VerificationCode', user=user, purpose=purpose, **kwargs)

    return _make_code
