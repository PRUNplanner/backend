"""
Load-test scenarios for perf/run.sh (run with Locust against gunicorn).

Two kinds of simulated users, weighted like real traffic:
- Visitor: anonymous, browses game data and planets.
- Planner: logs in as a seeded user, reads and edits plans and empires.

PERF_TARGETS points at the targets.json written by `seed_perf --targets-out`.
"""

import json
import os
import random
from pathlib import Path

# locust is installed by uvx at run time (perf/run.sh), not in the project environment
from locust import HttpUser, between, task  # ty:ignore[unresolved-import]

TARGETS = json.loads(Path(os.environ['PERF_TARGETS']).read_text())
PLANETS: list[str] = TARGETS['planets']
PLANET_NAMES: list[str] = TARGETS['planet_names']
MATERIALS: list[str] = TARGETS['materials']
USER_COUNT: int = TARGETS['users']
USERNAME_PREFIX: str = TARGETS['username_prefix']
PASSWORD: str = TARGETS['password']

EDITABLE_PLAN_FIELDS = (
    'plan_name',
    'planet_natural_id',
    'plan_permits_used',
    'plan_cogc',
    'plan_corphq',
    'plan_data',
)


class Visitor(HttpUser):
    """Anonymous traffic: the game data the app loads on start, plus planet lookups."""

    weight = 1
    wait_time = between(1, 3)

    @task(3)
    def game_data(self) -> None:
        for path in ('/data/materials/', '/data/recipes/', '/data/buildings/', '/data/exchanges/'):
            self.client.get(path)

    @task(4)
    def planet(self) -> None:
        planet = random.choice(PLANETS)
        self.client.get(f'/data/planet/{planet}/', name='/data/planet/[id]/')
        self.client.get(f'/analytics/planet_insights/{planet}/', name='/analytics/planet_insights/[id]/')

    @task(2)
    def planet_search(self) -> None:
        term = random.choice(PLANET_NAMES or PLANETS)[:5]
        self.client.get(f'/data/planets/{term}/', name='/data/planets/[term]/')

    @task(1)
    def planets_multiple(self) -> None:
        self.client.post('/data/planets/multiple/', json=random.sample(PLANETS, min(10, len(PLANETS))))

    @task(1)
    def market(self) -> None:
        self.client.get(f'/data/cxpc/{random.choice(MATERIALS)}/', name='/data/cxpc/[ticker]/')
        self.client.get('/analytics/planning_insights/materials/')


class Planner(HttpUser):
    """A logged-in user working on their plans and empires."""

    weight = 3
    wait_time = between(1, 4)

    plan_ids: list[str]
    empire_ids: list[str]

    def on_start(self) -> None:
        self.plan_ids = []
        self.empire_ids = []
        self.login()
        # what the app loads after login
        self.client.get('/user/profile/')
        self.client.get('/user/preferences/')
        self.load_plans()
        self.load_empires()
        self.client.get('/planning/cx/')

    def login(self) -> None:
        username = f'{USERNAME_PREFIX}{random.randrange(USER_COUNT)}'
        response = self.client.post('/user/login/', json={'username': username, 'password': PASSWORD})
        response.raise_for_status()
        self.client.headers['Authorization'] = f'Bearer {response.json()["access"]}'

    def load_plans(self) -> None:
        response = self.client.get('/planning/plan/')
        if response.ok:
            self.plan_ids = [plan['uuid'] for plan in response.json()]

    def load_empires(self) -> None:
        response = self.client.get('/planning/empire/')
        if response.ok:
            self.empire_ids = [empire['uuid'] for empire in response.json()]

    @task(5)
    def plan_list(self) -> None:
        self.load_plans()

    @task(5)
    def plan_detail(self) -> None:
        if self.plan_ids:
            self.client.get(f'/planning/plan/{random.choice(self.plan_ids)}/', name='/planning/plan/[id]/')

    @task(3)
    def empires(self) -> None:
        self.load_empires()
        if self.empire_ids:
            empire_id = random.choice(self.empire_ids)
            self.client.get(f'/planning/empire/{empire_id}/', name='/planning/empire/[id]/')
            self.client.get(f'/planning/empire/{empire_id}/plans/', name='/planning/empire/[id]/plans/')

    @task(2)
    def save_plan(self) -> None:
        """Saving a plan invalidates the user's planning caches, so the next reads hit the database."""
        if not self.plan_ids:
            return
        plan_id = random.choice(self.plan_ids)
        response = self.client.get(f'/planning/plan/{plan_id}/', name='/planning/plan/[id]/')
        if not response.ok:
            return
        plan = response.json()
        body = {field: plan[field] for field in EDITABLE_PLAN_FIELDS}
        self.client.put(f'/planning/plan/{plan_id}/', json=body, name='/planning/plan/[id]/ PUT')

    @task(1)
    def create_and_delete_plan(self) -> None:
        body = {
            'plan_name': 'Load test plan',
            'planet_natural_id': random.choice(PLANETS),
            'plan_permits_used': 1,
            'plan_corphq': False,
            'plan_data': {'experts': [], 'workforce': [], 'infrastructure': [], 'buildings': []},
        }
        response = self.client.post('/planning/plan/', json=body)
        if response.status_code == 201:
            self.client.delete(f'/planning/plan/{response.json()["uuid"]}/', name='/planning/plan/[id]/ DELETE')
