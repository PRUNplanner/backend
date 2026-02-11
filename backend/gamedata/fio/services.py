from collections.abc import Generator
from contextlib import contextmanager
from typing import Literal, TypeVar

import httpx
import structlog
from pydantic import TypeAdapter, ValidationError

from gamedata.fio.schemas import (
    FIOBuildingSchema,
    FIOExchangeCXPC,
    FIOExchangeFullSChema,
    FIOExchangeSchema,
    FIOMaterialSchema,
    FIOPlanetSchema,
    FIORecipeSchema,
    FIOUserShipSiteSchema,
    FIOUserSiteSchema,
    FIOUserSiteWarehouseSchema,
    FIOUserStorageSchema,
)

type Endpoint = Literal[
    'allrecipes',
    'allmaterials',
    'allbuildings',
    'allexchange',
    'fullexchange',
    'cxpc',
    'allplanets',
    'planet',
    'user_storage',
    'user_sites',
    'user_sites_warehouses',
    'user_ships',
]


class FIOURL:
    FIO_BASE_URL: str = 'https://rest.fnar.net/'

    endpoint_url: dict[Endpoint, str] = {
        'allrecipes': FIO_BASE_URL + 'recipes/allrecipes',
        'allmaterials': FIO_BASE_URL + 'material/allmaterials',
        'allbuildings': FIO_BASE_URL + 'building/allbuildings',
        'allexchange': FIO_BASE_URL + 'exchange/all',
        'cxpc': FIO_BASE_URL + 'exchange/cxpc/',
        'fullexchange': FIO_BASE_URL + 'exchange/full',
        'allplanets': FIO_BASE_URL + 'planet/allplanets/full',
        'planet': FIO_BASE_URL + 'planet/',
        'user_storage': FIO_BASE_URL + 'storage/',
        'user_sites': FIO_BASE_URL + 'sites/',
        'user_sites_warehouses': FIO_BASE_URL + 'sites/warehouses/',
        'user_ships': FIO_BASE_URL + 'ship/ships/',
    }

    endpoint_timeouts: dict[Endpoint, int] = {
        'allrecipes': 10,
        'allmaterials': 5,
        'allbuildings': 10,
        'allexchange': 5,
        'fullexchange': 10,
        'allplanets': 10,
        'planet': 3,
        'cxpc': 3,
        'user_storage': 3,
        'user_sites': 3,
        'user_sites_warehouses': 3,
        'user_ships': 3,
    }

    @staticmethod
    def get_url(endpoint: Endpoint) -> str:
        return FIOURL.endpoint_url[endpoint]

    @staticmethod
    def get_timeout(endpoint: Endpoint) -> int:
        return FIOURL.endpoint_timeouts[endpoint]


logger = structlog.get_logger(__name__)


class FIOService:
    def __init__(self) -> None:
        self.client = httpx.Client()

    def close(self) -> None:
        self.client.close()

    def _get_auth_headers(self, apikey: str | None) -> dict[str, str]:
        headers = {'X-FIO-Application': 'PRUNplanner'}

        if apikey:
            clean_key = apikey.strip().replace('\t', '').replace('\n', '').replace(' ', '')
            headers['Authorization'] = clean_key
        return headers

    TSchema = TypeVar('TSchema')

    def _json_to_pydantic(self, raw_bytes: bytes, typed: type[TSchema]) -> TSchema:
        try:
            return TypeAdapter(typed).validate_json(raw_bytes)
        except ValidationError as val_error:
            logger.error('fio_serialization_failed', schema=str(typed), exc_info=val_error)
            raise val_error

    def _execute_request(self, url: str, endpoint: Endpoint, header: dict[str, str]) -> httpx.Response:
        log = logger.bind(method='GET', url=url, endpoint=endpoint)
        log.info('fio_request_started')

        try:
            response = self.client.get(
                url,
                timeout=FIOURL.get_timeout(endpoint),
                headers=header,
            )
            log.info(
                'fio_request_completed', status_code=response.status_code, duration=response.elapsed.total_seconds()
            )
            response.raise_for_status()
            return response

        except Exception as e:
            log.error('fio_request_failed', exc_info=e)
            raise e

    def _fetch(self, endpoint: Endpoint, schema: type[TSchema], path_suffix: str = '', apikey: str | None = None):
        url = f'{FIOURL.get_url(endpoint)}{path_suffix}'
        header = self._get_auth_headers(apikey)

        response = self._execute_request(url, endpoint, header)
        return self._json_to_pydantic(response.content, schema)

    def get_all_materials(self) -> list[FIOMaterialSchema]:
        return self._fetch(endpoint='allmaterials', schema=list[FIOMaterialSchema])

    def get_all_buildings(self) -> list[FIOBuildingSchema]:
        return self._fetch(endpoint='allbuildings', schema=list[FIOBuildingSchema])

    def get_planet(self, planet_natural_id: str) -> FIOPlanetSchema:
        return self._fetch(endpoint='planet', schema=FIOPlanetSchema, path_suffix=planet_natural_id)

    def get_all_planets(self) -> list[FIOPlanetSchema]:
        return self._fetch(endpoint='allplanets', schema=list[FIOPlanetSchema])

    def get_all_exchanges(self) -> list[FIOExchangeSchema]:
        return self._fetch(endpoint='allexchange', schema=list[FIOExchangeSchema])

    def get_full_exchanges(self) -> list[FIOExchangeFullSChema]:
        return self._fetch(endpoint='fullexchange', schema=list[FIOExchangeFullSChema])

    def get_cxpc(self, ticker, exchange_code) -> list[FIOExchangeCXPC]:
        return self._fetch(endpoint='cxpc', schema=list[FIOExchangeCXPC], path_suffix=f'{ticker}.{exchange_code}')

    def get_all_recipes(self) -> list[FIORecipeSchema]:
        return self._fetch(endpoint='allrecipes', schema=list[FIORecipeSchema])

    def get_user_storage(self, prun_username: str, fio_apikey: str) -> list[FIOUserStorageSchema]:
        return self._fetch(
            endpoint='user_storage', path_suffix=prun_username, schema=list[FIOUserStorageSchema], apikey=fio_apikey
        )

    def get_user_sites(self, prun_username: str, fio_apikey: str) -> list[FIOUserSiteSchema]:
        return self._fetch(
            endpoint='user_sites', path_suffix=prun_username, schema=list[FIOUserSiteSchema], apikey=fio_apikey
        )

    def get_user_sites_warehouses(self, prun_username: str, fio_apikey: str) -> list[FIOUserSiteWarehouseSchema]:
        return self._fetch(
            endpoint='user_sites_warehouses',
            path_suffix=prun_username,
            schema=list[FIOUserSiteWarehouseSchema],
            apikey=fio_apikey,
        )

    def get_user_ships(self, prun_username: str, fio_apikey: str) -> list[FIOUserShipSiteSchema]:
        return self._fetch(
            endpoint='user_ships', path_suffix=prun_username, schema=list[FIOUserShipSiteSchema], apikey=fio_apikey
        )


@contextmanager
def get_fio_service() -> Generator[FIOService, None, None]:
    service = FIOService()
    try:
        yield service
    finally:
        service.close()
