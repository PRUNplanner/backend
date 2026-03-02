import pytest
from django.utils import timezone
from gamedata.models import GamePlanetCOGCStatusChoices
from gamedata.services.planet_search import GamePlanetSearchService, SearchRequestType
from model_bakery import baker


@pytest.mark.django_db
class TestGamePlanetSearchService:
    def test_search_by_term_and_id(self):
        baker.make('gamedata.GamePlanet', planet_natural_id='MORIA', planet_name='Deep')
        assert len(GamePlanetSearchService.search_by_planet_natural_id(['MORIA'])) == 1
        assert len(GamePlanetSearchService.search_by_term('Deep')) == 1
        assert len(GamePlanetSearchService.search_by_term('')) == 0

    @pytest.mark.parametrize('scenario', ['complex_env', 'infra_and_materials', 'cogc_active'])
    def test_complex_search_scenarios(self, scenario):

        req: SearchRequestType = {k: False for k in SearchRequestType.__annotations__}  # type: ignore
        req['materials'], req['cogc_programs'] = [], []

        if scenario == 'complex_env':
            req.update(
                {
                    'environment_rocky': True,
                    'environment_low_gravity': True,
                    'environment_high_pressure': True,
                    'environment_low_temperature': True,
                }
            )
            baker.make(
                'gamedata.GamePlanet', surface=True, gravity_type='LOW', pressure_type='HIGH', temperature_type='LOW'
            )

        elif scenario == 'infra_and_materials':
            req.update(
                {
                    'materials': ['FE'],
                    'must_be_fertile': True,
                    'must_have_localmarket': True,
                    'must_have_chamberofcommerce': True,
                    'must_have_warehouse': True,
                    'must_have_administrationcenter': True,
                    'must_have_shipyard': True,
                }
            )
            p = baker.make(
                'gamedata.GamePlanet',
                fertility_type=True,
                has_localmarket=True,
                has_chamberofcommerce=True,
                has_warehouse=True,
                has_administrationcenter=True,
                has_shipyard=True,
            )

            baker.make('gamedata.GameMaterial', material_id='mat_fe', ticker='FE')

            baker.make('gamedata.GamePlanetResource', planet=p, material_id='mat_fe')

        elif scenario == 'cogc_active':
            req.update({'cogc_programs': ['EDUCATION'], 'environment_gaseous': True})
            p = baker.make('gamedata.GamePlanet', surface=False, cogc_program_status=GamePlanetCOGCStatusChoices.Active)
            now_ms = int(timezone.now().timestamp() * 1000)
            baker.make(
                'gamedata.GamePlanetCOGCProgram',
                planet=p,
                program_type='EDUCATION',
                start_epochms=now_ms - 100,
                end_epochms=now_ms + 100,
            )

        results = GamePlanetSearchService.search(req)
        assert len(results) >= 1

    def test_env_alternates(self):
        """Tests the missing environment branches (high gravity, low pressure, high temp)."""
        req: SearchRequestType = {k: False for k in SearchRequestType.__annotations__}  # type: ignore
        req.update(
            {
                'materials': [],
                'cogc_programs': [],
                'environment_high_gravity': True,
                'environment_low_pressure': True,
                'environment_high_temperature': True,
            }
        )
        baker.make('gamedata.GamePlanet', gravity_type='HIGH', pressure_type='LOW', temperature_type='HIGH')

        assert len(GamePlanetSearchService.search(req)) == 1
