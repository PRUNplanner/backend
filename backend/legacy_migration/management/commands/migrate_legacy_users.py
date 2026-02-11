from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction
from legacy_migration.models.legacy_user import LegacyUser
from user.models.user import User


class Command(BaseCommand):
    help = 'Migrate users from the legacy database to the new User + Profile models'

    def add_arguments(self, parser):
        parser.add_argument('--user', type=int, help='Migrate data of specific user id')

    def handle(self, *args: Any, **options: Any) -> None:

        user_id: int | None = options['user']

        if user_id:
            legacy_users = LegacyUser.objects.using('legacy').filter(user_id=user_id)
        else:
            legacy_users = LegacyUser.objects.using('legacy').all()

        self.stdout.write(f'Found {legacy_users.count()} legacy users')

        legacy: LegacyUser
        for legacy in legacy_users:
            # Skip if user already exists
            if User.objects.filter(username=legacy.username).exists():
                self.stdout.write(f'Skipping {legacy.username}, already exists')
                continue

            is_admin = True if legacy.level == 1 else False

            with transaction.atomic():
                user = User.objects.create_legacy_user(
                    id=legacy.user_id,
                    username=legacy.username,
                    hashed_password=legacy.hashed_password,
                    email=legacy.email,
                    is_email_verified=True if legacy.email else False,
                    is_active=True,
                    is_staff=is_admin,
                    is_superuser=is_admin,
                    prun_username=legacy.prun_username,
                    fio_apikey=legacy.fio_apikey,
                )

                self.stdout.write(
                    f'{"Created" if user else "Updated"} {"superuser" if is_admin else "user"} {user.get_username()}'
                )

        self.stdout.write(self.style.SUCCESS(f' {len(legacy_users)} Legacy users migrated successfully'))
