import pytest
from django.core import mail
from model_bakery import baker
from user.models import User, VerificationCode

pytestmark = pytest.mark.django_db

LEGACY_HASH = '$2b$12$abcdefghijklmnopqrstuuJ9C8ZpU0d8m9cC3c1m3zZ0o0b8y4dW2'


class TestCreateLegacyUser:
    def test_creates_the_user_as_migrated(self) -> None:
        user = User.objects.create_legacy_user(
            id=4711, username='veteran', hashed_password=LEGACY_HASH, email='vet@example.com', is_email_verified=True
        )

        user = User.objects.get(id=4711)
        assert user.username == 'veteran'
        assert user.password == LEGACY_HASH  # kept as-is, upgraded on the first login
        assert user.email == 'vet@example.com'
        assert user.date_joined is None  # migrated accounts are not signups

    def test_updates_an_existing_user_without_asking_for_email_verification(self) -> None:
        baker.make('user.User', id=4711, username='old-name', email='old@example.com', is_email_verified=True)

        User.objects.create_legacy_user(
            id=4711, username='veteran', hashed_password=LEGACY_HASH, email='vet@example.com', is_email_verified=True
        )

        assert User.objects.count() == 1
        user = User.objects.get(id=4711)
        assert user.username == 'veteran'
        # a migration is not an email change: the address stays verified and no verification goes out
        assert user.is_email_verified
        assert not VerificationCode.objects.exists()
        assert mail.outbox == []

    def test_a_taken_email_is_renamed_instead_of_failing(self) -> None:
        baker.make('user.User', email='vet@example.com', is_email_verified=True)  # unverified would queue a task

        User.objects.create_legacy_user(
            id=4711, username='veteran', hashed_password=LEGACY_HASH, email='vet@example.com'
        )

        email = User.objects.get(id=4711).email
        assert email.startswith('migrated_')
        assert email.endswith('_vet@example.com')
