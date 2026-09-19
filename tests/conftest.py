from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: The extraction the sample order image is expected to produce. Used as a
#: fixture everywhere so the pure logic can be tested without a model call.
SAMPLE_RAW = {
    "order_date": "2026-07-14",
    "external_ref": "WEB-2026-0714-A17",
    "customer_id_source": "CUST-1007",
    "currency": "EUR",
    "company": "Northstar Office GmbH",
    "contact_first": "Marta",
    "contact_last": "Klein",
    "salutation": "",
    "alias": "NORTHSTAR-BERLIN",
    "email": "marta.klein@example.test",
    "phone": "+49 30 5550 1420",
    "billing": {
        "name": "Northstar Office GmbH",
        "street": "Friedrichstrasse 88",
        "zip": "10117",
        "city": "Berlin",
        "country": "Germany",
    },
    "delivery": {
        "name": "Northstar Office Warehouse",
        "street": "Beusselstrasse 44",
        "zip": "10553",
        "city": "Berlin",
        "country": "Germany",
    },
    "payment_method": "Bank Transfer",
    "paid_status": "PAID",
    "payment_date": "2026-07-18",
    "items": [
        {
            "sku": "CHR-ERG-01",
            "description": "Ergonomic Desk Chair",
            "qty": "2",
            "unit": "pcs",
            "unit_net": "250.00",
            "discount_pct": "10",
            "vat_pct": "19",
            "line_net": "450.00",
        },
        {
            "sku": "MAT-DESK-02",
            "description": "Anti-Fatigue Desk Mat",
            "qty": "3",
            "unit": "pcs",
            "unit_net": "40.00",
            "discount_pct": "0",
            "vat_pct": "19",
            "line_net": "120.00",
        },
    ],
    "net_total": "570.00",
    "vat_total": "108.30",
    "gross_total": "678.30",
}


@pytest.fixture
def raw_sample():
    return json.loads(json.dumps(SAMPLE_RAW))


@pytest.fixture
def sample_doc(raw_sample):
    from f2c.extract.pipeline import normalise

    return normalise(raw_sample)


@pytest.fixture
def repo_root():
    return REPO
