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
