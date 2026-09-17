# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AccessorialItem:
	code: str
	quantity: int = 1
	service_group: str = ""


@dataclass
class ShipmentRequest:
	origin_zip: str
	destination_zip: str
	total_weight: float
	freight_class: str
	length: float = 0
	width: float = 0
	height: float = 0
	pieces: int = 1
	accessorials: list[AccessorialItem] = field(default_factory=list)
	origin_city: str = ""
	origin_state: str = ""
	destination_city: str = ""
	destination_state: str = ""
	items: list[dict] = field(default_factory=list)
	payment_terms: str = "Prepaid"
	payment_payer: str = "Shipper"

	def first_handling_dimensions(self) -> tuple[float, float, float]:
		"""Return the first usable L × W × H in inches from header or line items."""
		from ltl_quote.api.payload import default_handling_dimensions

		length = float(self.length or 0)
		width = float(self.width or 0)
		height = float(self.height or 0)
		if length > 0 and width > 0 and height > 0:
			return length, width, height
		for item in self.items or []:
			if not isinstance(item, dict):
				continue
			length = float(item.get("length") or 0)
			width = float(item.get("width") or 0)
			height = float(item.get("height") or 0)
			if length > 0 and width > 0 and height > 0:
				return length, width, height
		return default_handling_dimensions(self.length, self.width, self.height)

	def cube_cubic_feet(self, pieces: int | None = None, length=None, width=None, height=None) -> float:
		"""Handling-unit cube in cubic feet: (L × W × H × pieces) / 1728."""
		if length is None or width is None or height is None:
			length, width, height = self.first_handling_dimensions()
		count = max(int(pieces if pieces is not None else self.pieces or 1), 1)
		length = float(length or 0)
		width = float(width or 0)
		height = float(height or 0)
		if length <= 0 or width <= 0 or height <= 0:
			return 0.0
		return (length * width * height * count) / 1728.0

	@property
	def accessorial_codes(self) -> list[str]:
		"""Unique normalized codes — backward-compatible for flag-based adapters."""
		seen: set[str] = set()
		ordered: list[str] = []
		for item in self.accessorials:
			code = (item.code or "").upper()
			if code and code not in seen:
				seen.add(code)
				ordered.append(code)
		return ordered

	def expanded_accessorial_codes(self) -> list[str]:
		"""Codes repeated by quantity for per-unit pricing."""
		expanded: list[str] = []
		for item in self.accessorials:
			code = (item.code or "").upper()
			if not code:
				continue
			expanded.extend([code] * max(int(item.quantity or 1), 1))
		return expanded


@dataclass
class CarrierRateQuote:
	carrier_code: str
	carrier_name: str
	total_charge: float
	transit_days: int
	linehaul_charge: float = 0
	fuel_surcharge: float = 0
	accessorial_charge: float = 0
	currency: str = "USD"
	carrier_quote_id: str = ""
	service_level: str = "Standard"
	reliability_score: float = 0
	accessorial_breakdown: dict[str, float] = field(default_factory=dict)
	raw_response: dict[str, Any] = field(default_factory=dict)
	error: str | None = None
	rate_source: str = ""
	quoted_scac: str = ""
	connector_carrier: str = ""
	estimated_delivery_date: str | None = None


class BaseCarrierAdapter(ABC):
	"""Base class for carrier API connectors."""

	def __init__(self, carrier_doc):
		self.carrier = carrier_doc

	@property
	def carrier_code(self) -> str:
		return self.carrier.carrier_code

	@abstractmethod
	def get_rates(self, request: ShipmentRequest) -> CarrierRateQuote:
		"""Fetch LTL rate quote from carrier API."""
		pass

	@abstractmethod
	def book_shipment(self, quote_data: dict) -> dict:
		"""Book shipment with carrier."""
		pass

	@abstractmethod
	def get_tracking(self, pro_number: str) -> list[dict]:
		"""Fetch tracking events from carrier."""
		pass

	def dispatch_shipment(self, shipment_data: dict) -> dict:
		"""Send pickup/dispatch request to carrier."""
		return {"status": "acknowledged", "message": "Dispatch not implemented for this connector"}

	def get_proof_of_delivery(self, pro_number: str) -> dict:
		"""Fetch signed delivery document when supported by the carrier connector."""
		return {"pod_available": False, "message": "Proof of delivery is not supported for this carrier."}
