# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

import frappe
import requests
from frappe import _
from frappe.utils.file_manager import save_file


@frappe.whitelist()
def attach_arcbest_bol_to_shipment(shipment_id):
	"""
	Download the PDF from ArcBest's document URL and attach it
	directly to the LTL Shipment record's attachment sidebar.
	"""
	doc = frappe.get_doc("LTL Shipment", shipment_id)

	if not doc.bol_document_url:
		frappe.throw(_("No BOL Document URL found on this shipment record."))

	try:
		response = requests.get(doc.bol_document_url, timeout=20)
		response.raise_for_status()

		file_name = f"ArcBest_BOL_{doc.bol_number or doc.name}.pdf"

		saved_file = save_file(
			fname=file_name,
			content=response.content,
			dt="LTL Shipment",
			dn=doc.name,
			is_private=1,
			decode=False,
			df="bol_document",
		)

		doc.db_set("bol_document", saved_file.file_url)
		doc.add_comment(
			text=(
				"<b>System Attachment</b>: Electronic BOL PDF successfully fetched "
				"and linked to the attachment sidepanel."
			)
		)

		return {
			"status": "success",
			"message": _("BOL successfully attached to shipment record."),
			"file_url": saved_file.file_url,
		}

	except Exception as e:
		frappe.log_error(title="ArcBest File Attachment Pipeline Failure", message=frappe.get_traceback())
		frappe.throw(_("Failed to attach file from carrier: {0}").format(str(e)))
