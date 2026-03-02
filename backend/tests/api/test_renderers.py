import decimal
import uuid

import orjson
import pytest
from api.renderers import OrjsonRenderer


class TestOrjsonRenderer:
    @pytest.fixture
    def renderer(self):
        return OrjsonRenderer()

    @pytest.mark.parametrize(
        'input_data, expected_output',
        [
            (None, b''),
            ({'status': 'success'}, {'status': 'success'}),
            ({1: 'int-key'}, {'1': 'int-key'}),
            ({'val': decimal.Decimal('10.50')}, {'val': 10.5}),
            (
                {'id': uuid.UUID('12345678-1234-5678-1234-567812345678')},
                {'id': '12345678-1234-5678-1234-567812345678'},
            ),
        ],
    )
    def test_renderer_successful_cases(self, renderer, input_data, expected_output):
        result = renderer.render(input_data)

        if input_data is None:
            assert result == b''
        else:
            assert orjson.loads(result) == expected_output
            assert isinstance(result, bytes)

    def test_render_unsupported_type_raises_error(self, renderer):

        class Unserializable:
            pass

        with pytest.raises(TypeError, match='Type is not JSON serializable: Unserializable'):
            renderer.render({'item': Unserializable()})

    def test_render_complex_nesting(self, renderer):
        data = {'prices': [decimal.Decimal('1.99')], 'meta': {'uid': uuid.uuid4(), 'tags': None}}
        result = renderer.render(data)
        decoded = orjson.loads(result)

        assert decoded['prices'][0] == 1.99
        assert isinstance(decoded['meta']['uid'], str)
