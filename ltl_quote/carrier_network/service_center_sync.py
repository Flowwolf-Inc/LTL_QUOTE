"""Sync Dayton GET /api/ServiceCenters into Dayton Service Center DocType."""

from __future__ import annotations

import frappe

from ltl_quote.carrier_network.adapters.dayton import DaytonCarrierAdapter
from ltl_quote.carrier_network.service_centers import clear_service_center_cache


def sync_dayton_service_centers() -> dict:
	"""Fetch ServiceCenters from Dayton and upsert into Dayton Service Center."""
	adapter = DaytonCarrierAdapter()
	centers = adapter.get_service_centers()
	if not centers:
		return {
			"status": "error",
			"synced_count": 0,
			"message": "No service centers returned from Dayton (check credentials or API availability).",
		}

	synced_count = 0
	seen: set[str] = set()
	for center in centers:
		center_id = str(center.get("id") or "").strip().upper()
		if not center_id or center_id in seen:
			continue
		seen.add(center_id)

		center_name = str(center.get("name") or center_id).strip()
		values = {
			"center_id": center_id,
			"center_number": center.get("number"),
			"center_name": center_name,
			"address1": center.get("address1") or "",
			"address2": center.get("address2") or "",
			"city": center.get("city") or "",
			"state": center.get("state") or "",
			"zip": center.get("zip") or "",
			"phone": center.get("phone") or "",
			"toll_free": center.get("toll_free") or "",
			"fax": center.get("fax") or "",
			"lat": center.get("lat"),
			"lng": center.get("lng"),
		}

		if frappe.db.exists("Dayton Service Center", center_id):
			frappe.db.set_value("Dayton Service Center", center_id, values, update_modified=True)
		else:
			doc = frappe.get_doc({"doctype": "Dayton Service Center", **values})
			doc.insert(ignore_permissions=True)
		synced_count += 1

	frappe.db.commit()
	clear_service_center_cache()
	return {
		"status": "success",
		"synced_count": synced_count,
		"message": f"Successfully synced {synced_count} service centers.",
	}
