import frappe

# Curated shipper-facing accessorials grouped by stage. Each internal code is
# validated against the LTL Accessorial master before being shown in the UI.
STANDARD_ACCESSORIALS = {
	"pickup": [
		("LIFTGATE", "Liftgate Pickup"),
		("INSIDE_DELIVERY", "Inside Pickup"),
	],
	"delivery": [
		("LIFTGATE", "Liftgate Delivery"),
		("INSIDE_DELIVERY", "Inside Delivery"),
		("RESIDENTIAL", "Residential Delivery"),
		("APPOINTMENT", "Notify Before Delivery"),
	],
	"load": [
		("LIMITED_ACCESS", "Limited Access"),
		("HAZMAT", "Hazmat Handling"),
		("APPOINTMENT", "Delivery Appointment"),
	],
}


@frappe.whitelist()
def get_accessorial_options() -> dict:
	"""Return curated accessorials grouped for origin (pickup), destination
	(delivery), and load-based sections, sourced from LTL Accessorial master data."""
	# Map of code -> master name for the codes we care about (single query).
	codes = {code for group in STANDARD_ACCESSORIALS.values() for code, _ in group}
	rows = frappe.get_all(
		"LTL Accessorial",
		filters={"accessorial_code": ["in", list(codes)]},
		fields=["accessorial_code", "accessorial_name"],
	)
	available = {r.accessorial_code: r.accessorial_name for r in rows}

	result: dict[str, list[dict]] = {}
	for group, entries in STANDARD_ACCESSORIALS.items():
		result[group] = [
			{"code": code, "label": label, "master_name": available.get(code) or label}
			for code, label in entries
			if code in available
		]
	return result


@frappe.whitelist()
def get_recent_quote_requests(limit: int = 10, origin_zip: str = None, destination_zip: str = None) -> list[dict]:
	"""Return the most recent LTL Quote Requests, optionally filtered by the
	origin and/or destination ZIP the user has entered on the dashboard."""
	filters = {}
	if origin_zip and str(origin_zip).strip():
		filters["origin_zip"] = str(origin_zip).strip()
	if destination_zip and str(destination_zip).strip():
		filters["destination_zip"] = str(destination_zip).strip()

	return frappe.get_list(
		"LTL Quote Request",
		filters=filters,
		fields=[
			"name",
			"origin_city",
			"origin_state",
			"origin_zip",
			"destination_city",
			"destination_state",
			"destination_zip",
			"total_weight",
			"creation",
			"status",
		],
		order_by="creation desc",
		limit_page_length=int(limit or 10),
		ignore_permissions=False,
	)
