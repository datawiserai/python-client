"""The example makes only the explicitly documented data requests."""
import importlib.util
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from requests import Response

from test_company_relationships import client_api, payload as relationships
from test_company_reference import payload as reference

spec=importlib.util.spec_from_file_location('company_example',Path(__file__).parents[1]/'examples/company_relationships.py')
example=importlib.util.module_from_spec(spec);spec.loader.exec_module(example)


@pytest.mark.parametrize('ticker',[False,True])
def test_example_uses_two_data_requests_plus_optional_ticker_resolution(tmp_path,monkeypatch,ticker):
    client,api=client_api(tmp_path)
    send=api.send
    def respond(request,**kwargs):
        path=urlsplit(request.url).path
        if path.endswith('/reference/NVDA'):
            api.calls.append(request)
            response=Response();response.request=request;response.url=request.url;response.status_code=200
            response._content=json.dumps({'ticker':'NVDA','securityId':'SEC1','companyId':'Private2'}).encode()
            return response
        api.body=reference() if '/company-reference/' in path else relationships()
        return send(request,**kwargs)
    monkeypatch.setattr(api,'send',respond)
    monkeypatch.setattr(example.dw,'Client',lambda **kwargs:client)
    monkeypatch.setenv('DATAWISER_API_KEY','synthetic-example-key')
    identifier=['--ticker','NVDA'] if ticker else ['--company-id','Private2']
    assert example.main([*identifier,'--status','current'])==0
    paths=[urlsplit(request.url).path for request in api.calls]
    expected=['/v1/company-relationships/Private2','/v1/company-reference/Private2']
    assert paths==(['/v1/reference/NVDA'] if ticker else [])+expected


def test_example_requires_a_key_before_constructing_the_client(monkeypatch):
    monkeypatch.delenv('DATAWISER_API_KEY',raising=False)
    monkeypatch.setattr(example.dw,'Client',lambda **kwargs:pytest.fail('Constructed an unauthenticated client'))
    with pytest.raises(SystemExit) as error:example.main(['--company-id','Private2'])
    assert error.value.code==2
