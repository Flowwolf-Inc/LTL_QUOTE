// Copyright (c) 2026, LTL Quote and contributors
// For license information, please see license.txt

frappe.ui.form.on("LTL Quote Request", {
	refresh(frm) {
		if (frm.doc.docstatus === 0 && !frm.is_new()) {
			frm.add_custom_button(__("Fetch Rates"), () => fetch_rates(frm), __("Actions"));
			if (frm.doc.status === "Quoted" && frm.doc.carrier_quotes?.length) {
				frm.add_custom_button(__("Book Selected Quote"), () => book_quote(frm), __("Actions"));
			}
		}
	},
});

function fetch_rates(frm) {
	frappe.call({
		method: "fetch_rates",
		doc: frm.doc,
		freeze: true,
		freeze_message: __("Aggregating carrier rates..."),
		callback(r) {
			if (!r.exc) {
				frm.reload_doc();
				frappe.show_alert({
					message: __("Received {0} carrier quotes", [r.message?.quotes_received || 0]),
					indicator: "green",
				});
			}
		},
	});
}

function book_quote(frm) {
	const quotes = frm.doc.carrier_quotes || [];
	if (!quotes.length) {
		frappe.msgprint(__("No carrier quotes available."));
		return;
	}

	const options = quotes.map((q, i) => ({
		label: `${q.carrier_name} — ${format_currency(q.total_charge, q.currency || "USD")} (${q.transit_days} days)`,
		value: i,
	}));

	frappe.prompt(
		[
			{
				fieldname: "row_idx",
				fieldtype: "Select",
				label: __("Select Carrier Quote"),
				options: options.map((o) => o.label).join("\n"),
				reqd: 1,
			},
		],
		(values) => {
			const idx = options.findIndex((o) => o.label === values.row_idx);
			frappe.call({
				method: "book_selected_quote",
				doc: frm.doc,
				args: { row_idx: idx >= 0 ? idx : 0 },
				freeze: true,
				freeze_message: __("Booking shipment..."),
				callback(r) {
					if (!r.exc && r.message?.shipment) {
						frappe.set_route("Form", "LTL Shipment", r.message.shipment);
					}
				},
			});
		},
		__("Book Shipment"),
		__("Book")
	);
}

function format_currency(amount, currency) {
	return frappe.format(amount, { fieldtype: "Currency", options: currency });
}
