"""JSON schema handed to the model as a tool definition.

Declaring the shape as a tool (rather than asking for "JSON in a code block")
is what makes the extraction deterministic enough to reconcile afterwards.
Every numeric field is typed `string` on purpose: the model must transcribe the
digits it sees, and we parse them into `Decimal` ourselves. Letting JSON produce
a float would silently destroy cents.
"""
from __future__ import annotations

_ADDRESS = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "recipient / company line of the address"},
        "street": {"type": "string"},
        "zip": {"type": "string"},
        "city": {"type": "string"},
        "country": {"type": "string"},
    },
    "required": ["name", "street", "zip", "city", "country"],
}

ORDER_TOOL = {
    "name": "record_order",
    "description": (
        "Record every field of the supplied sales-order image. Transcribe values "
        "exactly as printed. Never compute, infer or correct a value - if a total "
        "looks wrong, still report what is printed."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "order_date": {"type": "string", "description": "ISO yyyy-mm-dd"},
            "external_ref": {"type": "string", "description": "External reference / order ref"},
            "customer_id_source": {
                "type": "string",
                "description": "customer id printed on the image, or empty string",
            },
            "currency": {"type": "string"},
            "company": {"type": "string"},
            "contact_first": {"type": "string", "description": "contact given name only"},
            "contact_last": {"type": "string", "description": "contact family name only"},
            "salutation": {
                "type": "string",
                "description": "empty string when the image supplies none",
            },
            "alias": {"type": "string", "description": "customer alias, or empty string"},
            "email": {"type": "string"},
            "phone": {"type": "string"},
            "billing": _ADDRESS,
            "delivery": _ADDRESS,
            "payment_method": {
                "type": "string",
                "enum": ["Bank Transfer", "Credit Card", "SEPA Direct Debit"],
            },
            "paid_status": {"type": "string", "enum": ["PAID", "UNPAID"]},
            "payment_date": {
                "type": "string",
                "description": "ISO yyyy-mm-dd, or empty string when the status is not PAID",
            },
            "items": {
                "type": "array",
                "description": (
                    "one entry per printed item row, in source order; skip blank filler rows"
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "sku": {"type": "string"},
                        "description": {"type": "string"},
                        "qty": {"type": "string"},
                        "unit": {"type": "string"},
                        "unit_net": {
                            "type": "string",
                            "description": "unit NET price, digits only, e.g. 250.00",
                        },
                        "discount_pct": {
                            "type": "string",
                            "description": "bare number, e.g. 10 for 10 percent; 0 when blank",
                        },
                        "vat_pct": {
                            "type": "string",
                            "description": "bare number, e.g. 19 for 19 percent",
                        },
                        "line_net": {"type": "string", "description": "the printed line total"},
                    },
                    "required": [
                        "sku",
                        "description",
                        "qty",
                        "unit",
                        "unit_net",
                        "discount_pct",
                        "vat_pct",
                        "line_net",
                    ],
                },
            },
            "net_total": {"type": "string"},
            "vat_total": {"type": "string"},
            "gross_total": {"type": "string"},
        },
        "required": [
            "order_date",
            "external_ref",
            "currency",
            "company",
            "contact_first",
            "contact_last",
            "alias",
            "email",
            "phone",
            "billing",
            "delivery",
            "payment_method",
            "paid_status",
            "items",
            "net_total",
            "vat_total",
            "gross_total",
        ],
    },
}

SYSTEM_PROMPT = "\n".join(
    [
        "You transcribe sales-order documents for an accounting automation.",
        "Accuracy of digits matters more than anything else.",
        "Rules:",
        "1. Transcribe, never calculate. Report printed totals even if they look inconsistent.",
        "2. Split the contact name into given name and family name.",
        "3. Percentages are bare numbers: 19, not 19 percent sign.",
        "4. Money is a plain decimal string with a dot separator and no currency symbol.",
        "5. A blank or dash discount cell means 0.",
        "6. Ignore empty filler rows in the items table.",
        "7. Dates become ISO yyyy-mm-dd.",
        "8. Use an empty string for any field the document does not supply.",
        "   Do not invent values.",
        "Call the record_order tool exactly once.",
    ]
)
