"""Analyst views preserve perspective, disclosure grain and metric provenance."""
from copy import deepcopy
from decimal import Decimal
import json
import subprocess
import sys

import pytest

from datawiserai import CompanyRelationships
from test_company_relationships import client_api, payload


@pytest.fixture
def body():
    source=payload()
    focus=source['company']
    original=source['relationships'][0]
    original['evidence'].append({
        'evidenceId':'evidence2','text':'Full contract evidence: '+('x'*3000),
        'pageLabel':'12','sectionPath':'Contracts','location':{'start':10,'end':3024},
        'sourceUrl':'https://www.sec.gov/example#contract','sourceTag':'contract-section',
    })
    original['metrics'].append({
        'metricId':'metric2','evidenceId':'evidence2','metricType':'contract_value',
        'value':'120000000.0123456789','rawValue':'about $120 million','valueQualifier':'approx',
        'basisParty':'counterparty','basisCompanyId':focus['companyId'],'basisDescription':'supplier contract',
        'currencyCode':'USD','periodStart':'2026-01-01','periodEnd':'2026-12-31','periodLabel':'FY 2026',
    })
    def row(identifier,other_id,kind,*,incoming=False,status='current',directness='direct'):
        other={'companyId':other_id,'name':other_id+' name'}
        inverse={'customer':'supplier','supplier':'customer','competitor':'competitor','partner':'partner'}
        return {
            'relationshipId':identifier,'reportingCompany':other if incoming else focus,
            'counterparty':focus if incoming else other,
            'relationshipType':inverse[kind] if incoming else kind,'viewRelationshipType':kind,
            'status':status,'directness':directness,'relationshipSubtype':'commercial',
            'scopeKey':identifier,'productService':'Cloud services','filingDate':'2026-11-01',
            'relationshipStartDate':'2024-01-01',
            'source':{'documentId':'doc_'+identifier,'url':'https://example.test/'+identifier,'form':'10-K'},
            'evidence':[{'evidenceId':'e_'+identifier,'text':'Disclosure for '+identifier}], 'metrics':[],
        }
    source['relationships'].extend([
        row('incoming_supplier','Supplier3','supplier',incoming=True),
        row('historical_supplier','Supplier3','supplier',status='historical'),
        row('competitor','Competitor4','competitor',status='unknown',directness='indirect'),
        row('partner','Partner5','partner',incoming=True,status='planned',directness='unspecified'),
        row('old_customer','Customer6','customer',status='terminated'),
    ])
    return source


def ids(result):return [row.relationship_id for row in result]


def test_role_helpers_follow_focused_company_and_preserve_multiple_assertions(body):
    result=CompanyRelationships._from_dict(body)
    assert ids(result.suppliers())==['incoming_supplier','historical_supplier']
    assert ids(result.suppliers(status='current'))==['incoming_supplier']
    assert ids(result.customers())==['relation1','old_customer']
    assert ids(result.customers(status='current'))==['relation1']
    assert ids(result.competitors(directness='indirect'))==['competitor']
    assert ids(result.partners(status='planned',directness='unspecified'))==['partner']
    assert len(result)==6


def test_filters_are_local_chainable_and_keep_the_original_response(tmp_path,body):
    client,api=client_api(tmp_path);api.body=deepcopy(body)
    with client:
        result=client.company_relationships('Private2')
        original=deepcopy(result.raw)
        selected=result.filter(role='supplier').filter(status='current',directness='direct')
        assert ids(selected)==['incoming_supplier']
        assert selected.raw==result.raw==original
        assert selected.selection==result.selection==body['selection']
        assert len(result)==6 and result.company==selected.company
        assert result.filter()==result
        assert len(result.customers().suppliers())==0
        selected.to_dataframe();selected.evidence_dataframe();selected.metrics_dataframe()
        assert len(api.calls)==1


@pytest.mark.parametrize('criteria',[
    {'role':'suppliers'},{'role':'Supplier'},{'role':[]},
    {'status':'active'},{'status':False},{'directness':'DIRECT'},{'directness':{}},
])
def test_invalid_filter_criteria_fail_explicitly(body,criteria):
    with pytest.raises(ValueError):CompanyRelationships._from_dict(body).filter(**criteria)


def test_unspecified_role_can_be_selected_when_published():
    source=payload();source['selection']['includeUnspecified']=True
    source['relationships'][0].update(relationshipType='unspecified',viewRelationshipType='unspecified')
    result=CompanyRelationships._from_dict(source)
    assert len(result.filter(role='unspecified'))==1 and len(result.suppliers())==0


def test_assertion_frame_has_correct_related_company_for_both_directions(body):
    result=CompanyRelationships._from_dict(body)
    frame=result.to_dataframe().set_index('relationship_id')
    incoming=frame.loc['relation1']
    assert incoming['company_id']==incoming['counterparty_company_id']=='Private2'
    assert incoming['related_company_id']==incoming['reporting_company_id']=='Reporter1'
    assert incoming['related_company_name']=='Reporter'
    assert incoming['relationship_type']=='supplier' and incoming['view_relationship_type']=='customer'
    outgoing=frame.loc['historical_supplier']
    assert outgoing['company_id']==outgoing['reporting_company_id']=='Private2'
    assert outgoing['related_company_id']==outgoing['counterparty_company_id']=='Supplier3'
    assert outgoing['document_id']=='doc_historical_supplier' and outgoing['source_form']=='10-K'
    suppliers=result.suppliers().to_dataframe()
    assert suppliers['related_company_id'].tolist()==['Supplier3','Supplier3']
    assert suppliers['relationship_id'].is_unique


def test_evidence_frame_preserves_passages_locations_and_source_links(body):
    result=CompanyRelationships._from_dict(body).customers(status='current')
    frame=result.evidence_dataframe()
    assert len(frame)==2
    passage=frame.set_index('evidence_id').loc['evidence2']
    assert passage['text']==body['relationships'][0]['evidence'][1]['text']
    assert passage['page_label']=='12' and passage['section_path']=='Contracts'
    assert passage['evidence_source_url']=='https://www.sec.gov/example#contract'
    assert passage['source_url']=='https://www.sec.gov/example'
    assert passage['source_tag']=='contract-section'
    assert passage['document_id']=='document1' and passage['related_company_id']=='Reporter1'
    passage['location']['start']=999
    assert result.relationships[0].evidence[1].location['start']==10
    assert body['relationships'][0]['evidence'][1]['location']['start']==10


def test_metric_frame_keeps_decimal_precision_original_basis_and_evidence_join(body):
    result=CompanyRelationships._from_dict(body).customers(status='current')
    metrics=result.metrics_dataframe()
    assert len(metrics)==2
    share,amount=metrics.iloc[0],metrics.iloc[1]
    assert share['value']==Decimal('0.1234567890') and isinstance(share['value'],Decimal)
    assert share['basis_company_id']=='Reporter1' and share['basis_party']=='reporting_company'
    assert amount['value']==Decimal('120000000.0123456789') and isinstance(amount['value'],Decimal)
    assert amount['basis_company_id']=='Private2' and amount['basis_party']=='counterparty'
    assert amount['currency_code']=='USD' and amount['period_label']=='FY 2026'
    assert amount['raw_value']=='about $120 million' and amount['value_qualifier']=='approx'
    joined=metrics.merge(result.evidence_dataframe(),on=['relationship_id','evidence_id'],validate='many_to_one')
    assert len(joined)==2 and joined['text'].tolist()==[e.text for e in result.relationships[0].evidence]


@pytest.mark.parametrize('method',['to_dataframe','evidence_dataframe','metrics_dataframe'])
def test_empty_views_retain_columns(body,method):
    result=CompanyRelationships._from_dict(body)
    empty=result.filter(role='supplier',status='planned')
    assert getattr(empty,method)().empty
    assert list(getattr(empty,method)().columns)==list(getattr(result,method)().columns)
    source=deepcopy(body);source['relationships']=[]
    assert list(getattr(CompanyRelationships._from_dict(source),method)().columns)==list(getattr(result,method)().columns)


def test_reference_and_relationship_imports_and_filters_do_not_require_pandas():
    script='''
import importlib.abc,json,sys
class NoPandas(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname=='pandas' or fullname.startswith('pandas.'):
            raise AssertionError('pandas was imported outside a DataFrame call')
sys.meta_path.insert(0,NoPandas())
from datawiserai import CompanyRelationships,CompanyReference
result=CompanyRelationships._from_dict(json.loads(sys.argv[1]))
assert len(result.customers(status='current'))==1
assert not result.suppliers()
ref=CompanyReference._from_dict({'schemaVersion':'company-reference-v1','company':{'companyId':'Private2'},'securities':[]})
assert ref.securities==()
'''
    result=subprocess.run([sys.executable,'-c',script,json.dumps(payload())],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
