# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class LTLQuoteRequest(Document):
	def validate(self):
		self._normalize_zip_codes()
		self._enrich_location_fields()

	def _normalize_zip_codes(self):
		for field in ("origin_zip", "destination_zip"):
			if self.get(field):
				self.set(field, str(self.get(field)).strip()[:10])

	def _enrich_location_fields(self):
		from ltl_quote.utils.location import enrich_location_fields

		enrich_location_fields(self, "origin")
		enrich_location_fields(self, "destination")

	def before_save(self):
		if self.is_new() and not self.requested_on:
			self.requested_on = now_datetime()

	@frappe.whitelist()
	def fetch_rates(self):
		"""Aggregate LTL rates from all enabled carriers."""
		from ltl_quote.rate_engine.aggregator import RateAggregator

		aggregator = RateAggregator(self)
		result = aggregator.aggregate()
		# raw_quotes holds CarrierRateQuote dataclasses (not JSON serializable) and
		# is only consumed by internal callers, so drop it from the client response.
		result.pop("raw_quotes", None)
		return result

	@frappe.whitelist()
	def book_selected_quote(self, row_idx=None):
		"""Book shipment using selected carrier quote line."""
		from ltl_quote.booking.executor import ShipmentExecutor

		if row_idx is not None:
			self.selected_carrier_quote = str(row_idx)

		executor = ShipmentExecutor(self)
		return executor.book()
