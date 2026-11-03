import decimal

import orjson
from rest_framework.renderers import JSONRenderer
from rest_framework_csv.renderers import CSVRenderer


class OrjsonRenderer(JSONRenderer):
    def render(self, data, accepted_media_type=None, renderer_context=None):
        if data is None:
            return b''

        def default(obj):
            if isinstance(obj, decimal.Decimal):
                return float(obj)
            raise TypeError(f'Object of type {obj.__class__.__name__} is not JSON serializable')

        return orjson.dumps(
            data, default=default, option=orjson.OPT_NON_STR_KEYS | orjson.OPT_UTC_Z | orjson.OPT_SERIALIZE_UUID
        )


# a spreadsheet runs a cell starting with one of these as a formula; plan names are user input and reach other users
FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r')


class SafeCSVRenderer(CSVRenderer):
    """CSVRenderer whose text cells never start a formula: those get a leading ', numbers stay as they are."""

    def tablize(self, data, header=None, labels=None):
        for row in super().tablize(data, header=header, labels=labels):
            yield [f"'{cell}" if isinstance(cell, str) and cell.startswith(FORMULA_PREFIXES) else cell for cell in row]
