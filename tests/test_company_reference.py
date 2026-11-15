"""Company-reference decoding and real transport/cache identity separation."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from datawiserai import CompanyReference, CompanyUniverse, DatawiserAccessError, DatawiserResponseError
from test_company_relationships import client_api


def payload(*, empty=False):
    return {'schemaVersion':'company-reference-v1','company':{'companyId':'Private2','name':'Private supplier','lei':'EXAMPLE-LEI'},
            'securities':[] if empty else [{'securityId':'SEC1','name':'Common stock','primaryTicker':'ABC','securityType':'Common Stock'},
                                         {'securityId':'SEC2','name':'Second class'}]}


def test_company_reference_client_models_discovery_and_conditional_cache(tmp_path):
    client, api = client_api(tmp_path)
    api.body = payload()
    with client:
        first = client.company_reference('Private2')
        second = client.company_reference('Private2')
        assert isinstance(first, CompanyReference) and first == second
        assert first.company_id == 'Private2' and first.company_name == 'Private supplier' and first.lei == 'EXAMPLE-LEI'
        assert [s.security_id for s in first.securities] == ['SEC1','SEC2']
        assert first.securities[0].primary_ticker == 'ABC' and first.securities[1].primary_ticker is None
        assert api.calls[1].headers['If-None-Match'] == '"company-v1"'
        assert client.diagnostics['cached_304_returns'] == 1
        assert client._cache.get_data('company-reference','Private2').security_id == 'Private2'
        assert client._cache.get_data('company-relationships','Private2') is None
        universe = client.universe('company_reference')
        assert isinstance(universe, CompanyUniverse) and universe.company_ids == ['Private2']
        api.allowed = False
        with pytest.raises(DatawiserAccessError):
            client.company_reference('Private2')
        assert len(api.calls) == 4


def test_company_reference_without_a_security(tmp_path):
    client, api = client_api(tmp_path)
    api.body = payload(empty=True)
    api.body['company'].pop('lei')
    with client:
        record = client.company_reference('Private2')
        assert record.securities == () and record.lei is None
        assert client.company_reference('Private2') == record


def test_reference_company_can_have_an_unknown_name(tmp_path):
    client, api = client_api(tmp_path)
    api.body = payload(empty=True)
    api.body['company'].pop('name')
    with client:
        assert client.company_reference('Private2').company_name is None


@pytest.mark.parametrize('problem',['schema','company','duplicate','security_id','field_type'])
def test_invalid_reference_never_enters_the_cache(tmp_path, problem):
    client, api = client_api(tmp_path)
    api.body = payload()
    if problem == 'schema': api.body['schemaVersion'] = 'other-v1'
    if problem == 'company': api.body['company']['companyId'] = 'WRONG'
    if problem == 'duplicate': api.body['securities'].append(api.body['securities'][0])
    if problem == 'security_id': api.body['securities'][0]['securityId'] = ''
    if problem == 'field_type': api.body['securities'][0]['primaryTicker'] = 123
    with client, pytest.raises(DatawiserResponseError):
        client.company_reference('Private2')
    assert client._cache.get_data('company-reference','Private2') is None
