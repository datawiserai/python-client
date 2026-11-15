"""Explore company disclosures and reference data using explicit data requests.

Install datawiserai[pandas], set DATAWISER_API_KEY, then run:
    python company_relationships.py --company-id REAL_COMPANY_ID --status current

This fetches relationships and company reference (two potentially metered data
responses). Using --ticker instead adds one security-reference request to resolve
the company ID. All filtering and DataFrame conversion then happen locally.
"""
import argparse
import os

import datawiserai as dw


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    identity=parser.add_mutually_exclusive_group(required=True)
    identity.add_argument('--company-id',help='Exact, case-sensitive Datawiser company ID')
    identity.add_argument('--ticker',help='Resolve a listed company through security reference first')
    parser.add_argument('--status',choices=('current','historical','planned','terminated','unknown'))
    parser.add_argument('--base-url',default='https://api.datawiser.ai/v1')
    args=parser.parse_args(argv)
    key=os.environ.get('DATAWISER_API_KEY')
    if not key:parser.error('Set DATAWISER_API_KEY in the environment')
    with dw.Client(api_key=key,base_url=args.base_url) as client:
        company_id=args.company_id
        if args.ticker:
            company_id=client.reference(args.ticker).company_id
            if not company_id:parser.error('Security reference did not provide a company ID')
        relationships=client.company_relationships(company_id)
        suppliers=relationships.suppliers(status=args.status)
        customers=relationships.customers(status=args.status)
        print(f'{relationships.company.name} ({relationships.company_id}): {len(relationships)} published assertions')
        columns=['related_company_id','related_company_name','status','directness','filing_date']
        print(f'Suppliers: {len(suppliers)} assertions')
        print(suppliers.to_dataframe()[columns].head(10).to_string(index=False))
        print(f'Customers: {len(customers)} assertions')
        print(customers.to_dataframe()[columns].head(10).to_string(index=False))
        print(f'Supplier evidence passages: {len(suppliers.evidence_dataframe())}')
        print(f'Supplier accepted metrics: {len(suppliers.metrics_dataframe())}')
        reference=client.company_reference(company_id)
        print(f'Company reference: {reference.company_name or reference.company_id}; LEI={reference.lei}')
        print(reference.to_dataframe().head(10).to_string(index=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
