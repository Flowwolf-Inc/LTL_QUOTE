// Copyright (c) 2026, LTL Quote and contributors
// For license information, please see license.txt

const SYNC_CONNECTORS = ["Dayton", "ArcBest API", "TForce", "SMC3"];

frappe.ui.form.on("LTL Carrier", {
	refresh(frm) {
		if (frm.is_new() || !SYNC_CONNECTORS.includes(frm.doc.connector_type)) {
			return;
		}

		frm.add_custom_button(__("Sync Accessorials from API"), () => sync_accessorials(frm));

		if (frm.doc.connector_type === "SMC3") {
			frm.add_custom_button(__("Sync Barcode Requirements"), () => sync_barcode_requirements(frm));
			frm.add_custom_button(__("Sync Dispatch Messages"), () => sync_dispatch_response_messages(frm));
			frm.add_custom_button(__("Register Status Webhook"), () => register_status_callback(frm));
			if (window.ltl_smc3_credentials && typeof window.ltl_smc3_credentials.bind === "function") {
				window.ltl_smc3_credentials.bind(frm);
			}
		}
	},
});

function sync_accessorials(frm) {
	frm.call({
		method: "sync_accessorials",
		doc: frm.doc,
		freeze: true,
		freeze_message: __("Fetching accessorials from carrier..."),
		callback(r) {
			if (r.exc || !r.message) {
				return;
			}

			frm.refresh_field("accessorial_mappings");
			frappe.show_alert(
				{
					message: r.message.message || __("Accessorial mappings updated"),
					indicator: r.message.added || r.message.updated ? "green" : "blue",
				},
				7
			);
		},
	});
}

function sync_barcode_requirements(frm) {
	frm.call({
		method: "sync_barcode_requirements",
		doc: frm.doc,
		freeze: true,
		freeze_message: __("Fetching barcode requirements from SMC3..."),
		callback(r) {
			if (r.exc || !r.message) {
				return;
			}
			frm.refresh_field("smc3_network_carriers");
			frappe.show_alert(
				{
					message: r.message.message || __("Barcode requirements updated"),
					indicator: r.message.updated ? "green" : "blue",
				},
				7
			);
		},
	});
}

function sync_dispatch_response_messages(frm) {
	frm.call({
		method: "sync_dispatch_response_messages",
		doc: frm.doc,
		freeze: true,
		freeze_message: __("Fetching dispatch response messages from SMC3..."),
		callback(r) {
			if (r.exc || !r.message) {
				return;
			}
			frappe.show_alert(
				{
					message: r.message.message || __("Dispatch messages updated"),
					indicator: r.message.created || r.message.updated ? "green" : "blue",
				},
				7
			);
		},
	});
}

function default_status_callback_url(frm) {
	try {
		const notes = JSON.parse(frm.doc.notes || "{}");
		const stored =
			(notes.status_callback_url ||
				(notes.status_webhook && notes.status_webhook.endpoint) ||
				"") + "";
		if (stored.trim()) {
			return stored.trim();
		}
		const base = (notes.public_base_url || "").replace(/\/+$/, "");
		if (base) {
			return `${base}/api/method/ltl_quote.api.webhooks.smc3_status_update`;
		}
	} catch (e) {
		/* notes is not JSON */
	}
	return `${window.location.origin}/api/method/ltl_quote.api.webhooks.smc3_status_update`;
}

function default_status_callback_date(frm) {
	try {
		const notes = JSON.parse(frm.doc.notes || "{}");
		const stored = notes.status_webhook && notes.status_webhook.effectiveDate;
		const digits = String(stored || "").replace(/\D/g, "");
		if (digits.length >= 8) {
			return digits.slice(0, 8);
		}
	} catch (e) {
		/* notes is not JSON */
	}
	return String(frappe.datetime.now_date() || "").replace(/-/g, "");
}

function register_status_callback(frm) {
	frappe.prompt(
		[
			{
				fieldname: "endpoint",
				label: __("Public callback URL"),
				fieldtype: "Data",
				reqd: 1,
				default: default_status_callback_url(frm),
				description: __(
					"HTTPS URL SMC3 can reach. Localhost will not receive Status push notifications."
				),
			},
			{
				fieldname: "effective_date",
				label: __("Effective Date (YYYYMMDD)"),
				fieldtype: "Data",
				reqd: 1,
				default: default_status_callback_date(frm),
			},
		],
		(values) => {
			frm.call({
				method: "register_status_callback",
				doc: frm.doc,
				args: values,
				freeze: true,
				freeze_message: __("Registering SMC3 Status callback..."),
				callback(r) {
					if (r.exc || !r.message) {
						return;
					}
					frm.reload_doc();
					frappe.show_alert(
						{
							message: r.message.message || __("Status callback registered"),
							indicator: r.message.ok ? "green" : "blue",
						},
						8
					);
				},
			});
		},
		__("Register SMC3 Status Webhook"),
		__("Register")
	);
}
