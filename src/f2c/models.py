"""Validated representation of the source order image.

Everything monetary is `Decimal`. Floats are never used for money anywhere in
this package - the reconciliation in `f2c.extract.validate` depends on exact
two-decimal arithmetic.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

CENTS = Decimal("0.01")

PAYMENT_METHODS = ("Bank Transfer", "Credit Card", "SEPA Direct Debit")

#: spec 2.10.4 - Fakturama payment-code dropdown mapping
PAYMENT_CODE_MAP = {
    "Bank Transfer": "Credit transfer",
    "Credit Card": "Credit card",
    "SEPA Direct Debit": "SEPA direct debit",
}


def money(value) -> Decimal:
    """Quantise to 2dp, half-up (commercial rounding, spec 3.9)."""
    return Decimal(str(value)).quantize(CENTS, rounding=ROUND_HALF_UP)


class Address(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = ""             # company / recipient line
    street: str = ""
    zip: str = ""
    city: str = ""
    country: str = ""

    def same_place_as(self, other: "Address") -> bool:
        """Billing == delivery test used by spec 2.8."""
        return (
            self.street.casefold() == other.street.casefold()
            and self.zip == other.zip
            and self.city.casefold() == other.city.casefold()
            and self.country.casefold() == other.country.casefold()
        )

    def is_empty(self) -> bool:
        return not any([self.street, self.zip, self.city])


class Item(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    sku: str
    description: str
    qty: Decimal
    unit: str = "pcs"
    unit_net: Decimal              # unit net price
    discount_pct: Decimal = Decimal("0")
    vat_pct: Decimal
    line_net: Decimal              # "source total" from the image

    @field_validator("qty", "unit_net", "discount_pct", "vat_pct", "line_net", mode="before")
    @classmethod
    def _to_decimal(cls, v):
        if isinstance(v, Decimal):
            return v
        if isinstance(v, str):
            v = v.replace("%", "").replace(",", "").strip()
        return Decimal(str(v))

    # --- derived values -----------------------------------------------------
    @property
    def computed_line_net(self) -> Decimal:
        """spec 3.16: qty x unit net x (1 - discount/100)."""
        return money(self.qty * self.unit_net * (Decimal(1) - self.discount_pct / Decimal(100)))

    @property
    def product_gross_price(self) -> Decimal:
        """spec 3.9: unit net x (1 + VAT/100), 2dp. Line discount NOT applied."""
        return money(self.unit_net * (Decimal(1) + self.vat_pct / Decimal(100)))

    @property
    def vat_name(self) -> str:
        """spec 3.4/3.6: 'VAT ' + percentage, e.g. 'VAT 19%'."""
        return "VAT %s%%" % _fmt_pct(self.vat_pct)


class OrderDoc(BaseModel):
    """The complete, validated extraction of one order image."""

    model_config = ConfigDict(str_strip_whitespace=True)

    # order header (spec 1.2 / 1.5 / 1.6)
    order_date: date
    external_ref: str
    customer_id_source: str = ""     # the image's own customer id - informational
    currency: str = "EUR"

    # debtor (spec 2.x)
    company: str
    contact_first: str = ""
    contact_last: str = ""
    salutation: str = ""             # empty -> leave "---" (spec 2.6)
    alias: str = ""
    email: str = ""
    phone: str = ""
    billing: Address
    delivery: Address

    # payment (spec 2.10 / 5.2 / 5.3)
    payment_method: str
    paid_status: str                 # "PAID" | anything else
    payment_date: Optional[date] = None

    items: List[Item] = Field(default_factory=list)

    # source totals (spec 4.3)
    net_total: Decimal
    vat_total: Decimal
    gross_total: Decimal

    @field_validator("net_total", "vat_total", "gross_total", mode="before")
    @classmethod
    def _to_decimal(cls, v):
        if isinstance(v, Decimal):
            return v
        if isinstance(v, str):
            v = v.replace("EUR", "").replace(",", "").strip()
        return Decimal(str(v))

    @field_validator("paid_status", mode="before")
    @classmethod
    def _norm_status(cls, v):
        return str(v).strip().upper()

    @property
    def is_paid(self) -> bool:
        return self.paid_status == "PAID"

    @property
    def payment_code(self) -> str:
        """Fakturama payment-code dropdown value (spec 2.10.4)."""
        return PAYMENT_CODE_MAP.get(self.payment_method, "")

    @property
    def delivery_same_as_billing(self) -> bool:
        return self.billing.same_place_as(self.delivery)

    @property
    def contact_name(self) -> str:
        return (self.contact_first + " " + self.contact_last).strip()

    def distinct_vat_rates(self):
        seen, out = set(), []
        for it in self.items:
            if it.vat_pct not in seen:
                seen.add(it.vat_pct)
                out.append(it.vat_pct)
        return out


def _fmt_pct(p: Decimal) -> str:
    """19 -> '19'; 7.5 -> '7.5' (no trailing zeros)."""
    s = format(Decimal(p).normalize(), "f")
    return s


class DebtorMatch(BaseModel):
    """One row of Fakturama's 'Select the address' dialog (spec 2.3)."""

    company: str = ""
    first_name: str = ""
    name: str = ""
    zip: str = ""
    city: str = ""
    row_index: int = -1
    raw: str = ""

    def is_exact_for(self, doc: "OrderDoc") -> bool:
        """spec 2.3: exact only when Company, First Name, Name, ZIP and City all match."""
        def eq(a: str, b: str) -> bool:
            return a.strip().casefold() == b.strip().casefold()

        return (
            eq(self.company, doc.company)
            and eq(self.first_name, doc.contact_first)
            and eq(self.name, doc.contact_last)
            and self.zip.strip() == doc.billing.zip.strip()
            and eq(self.city, doc.billing.city)
        )


class ProductMatch(BaseModel):
    """One row of Fakturama's 'Select a product' dialog (spec 3.3)."""

    item_number: str = ""
    name: str = ""
    row_index: int = -1
    raw: str = ""

    def is_exact_for(self, item: Item) -> bool:
        return self.item_number.strip().casefold() == item.sku.strip().casefold()


class VatRow(BaseModel):
    """One row of Data > VATs (spec 3.5)."""

    name: str = ""
    value: str = ""
    code: str = ""      # 'VAT code (E-Invoice)'
    row_index: int = -1
    raw: str = ""

    def is_reusable_for(self, item: Item) -> bool:
        """spec 3.5: name == 'VAT <pct>%', value == pct, code == S (Standard rate)."""
        if self.name.strip().casefold() != item.vat_name.casefold():
            return False
        if not _pct_equal(self.value, item.vat_pct):
            return False
        return "standard rate" in self.code.casefold() or self.code.strip().upper() == "S"


def _pct_equal(raw: str, pct: Decimal) -> bool:
    try:
        cleaned = str(raw).replace("%", "").replace(",", ".").strip()
        return Decimal(cleaned) == Decimal(pct)
    except Exception:
        return False
