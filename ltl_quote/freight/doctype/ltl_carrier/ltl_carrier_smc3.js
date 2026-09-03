// Copyright (c) 2026, LTL Quote and contributors
// For license information, please see license.txt

window.ltl_smc3_credentials = {
	bind(frm) {
		if (!frm || frm.is_new() || frm.doc.connector_type !== "SMC3") {
			return;
		}
		frm.remove_custom_button(__("Fetch SMC3 Credential Requirements"));
		frm.add_custom_button(__("Fetch SMC3 Credential Requirements"), () => {
			window.ltl_smc3_credentials.fetch_requirements(frm);
		});
	},

	fetch_requirements(frm) {
		pick_smc3_network_scac(frm, (scac) => {
			if (!scac) return;
			frappe.call({
				method: "ltl_quote.api.smc3_credentials.get_carrier_requirements",
				args: { scac, carrier: frm.doc.name },
				freeze: true,
				freeze_message: __("Fetching credential requirements for {0}…", [scac]),
				callback(r) {
					const result = r.message || {};
					if (show_smc3_credentials_error(result, __("Could not load credential requirements."))) {
						return;
					}
					open_smc3_credentials_dialog(frm, scac, result.fields || []);
				},
			});
		});
	},
};

frappe.ui.form.on("LTL Carrier", {
	refresh(frm) {
		window.ltl_smc3_credentials.bind(frm);
	},
});

function pick_smc3_network_scac(frm, callback) {
	const rows = (frm.doc.smc3_network_carriers || []).filter((row) => String(row.scac || "").trim());
	const selected = selected_smc3_network_scac(frm);
	if (selected) {
		callback(selected);
		return;
	}
	if (rows.length === 1) {
		callback(String(rows[0].scac).trim().toUpperCase());
		return;
	}
	const options = rows.map((row) => String(row.scac).trim().toUpperCase()).filter(Boolean);
	if (!options.length) {
		const fallback = String(frm.doc.scac || "").trim().toUpperCase();
		if (fallback && fallback !== "SMC3" && fallback !== "SMC") {
			callback(fallback);
			return;
		}
		frappe.msgprint({
			title: __("SMC3 Credentials"),
			indicator: "orange",
			message: __("Add a network carrier SCAC, then fetch credential requirements."),
		});
		return;
	}
	frappe.prompt(
		[
			{
				fieldname: "scac",
				label: __("Network Carrier SCAC"),
				fieldtype: "Select",
				options: options.join("\n"),
				reqd: 1,
				default: options[0],
			},
		],
		(values) => callback(String(values.scac || "").trim().toUpperCase()),
		__("Select Carrier"),
		__("Continue")
	);
}

function selected_smc3_network_scac(frm) {
	const grid = frm.get_field("smc3_network_carriers") && frm.get_field("smc3_network_carriers").grid;
	if (!grid || typeof grid.get_selected_children !== "function") {
		return "";
	}
	const selected = grid.get_selected_children() || [];
	if (selected.length !== 1) {
		return "";
	}
	return String(selected[0].scac || "").trim().toUpperCase();
}

function open_smc3_credentials_dialog(frm, scac, fields) {
	if (!fields.length) {
		frappe.msgprint({
			title: __("SMC3 Credentials"),
			indicator: "blue",
			message: __("{0} did not require any additional credential fields.", [scac]),
		});
		return;
	}
	const dialog = new frappe.ui.Dialog({
		title: __("SMC3 Credentials — {0}", [scac]),
		fields: fields.map((field, idx) => credential_dialog_field(field, idx)),
		primary_action_label: __("Save to SMC3 Vault"),
		primary_action(values) {
			const smc_attributes = fields
				.map((field, idx) => ({
					name: field.name,
					value: values[field_key(field, idx)] || "",
				}))
				.filter((row, idx) => row.value || fields[idx].required);
			dialog.hide();
			frappe.call({
				method: "ltl_quote.api.smc3_credentials.save_carrier_credentials",
				args: {
					scac,
					smc_attributes,
					carrier: frm.doc.name,
				},
				freeze: true,
				freeze_message: __("Saving {0} credentials to the SMC3 vault…", [scac]),
				callback(r) {
					const result = r.message || {};
					if (show_smc3_credentials_error(result, __("Could not save carrier credentials."))) {
						return;
					}
					frappe.show_alert(
						{
							message: result.message || __("Credentials stored for {0}.", [scac]),
							indicator: "green",
						},
						8
					);
				},
			});
		},
	});
	dialog.show();
}

function credential_dialog_field(field, idx) {
	const key = field_key(field, idx);
	const secure = Boolean(field.secure) || String(field.type || "").toLowerCase() === "password";
	return {
		fieldname: key,
		label: field.label || field.name,
		fieldtype: secure ? "Password" : "Data",
		reqd: field.required ? 1 : 0,
		description: field.description && field.description !== field.label ? field.description : "",
	};
}

function field_key(field, idx) {
	const name = String((field && field.name) || "").trim();
	const slug = name.replace(/[^a-zA-Z0-9_]/g, "_");
	return slug || `attr_${idx}`;
}

function show_smc3_credentials_error(result, fallback) {
	if (result && (result.status === "success" || result.ok)) {
		return false;
	}
	const status = (result && result.message_status) || {};
	const code = String(status.code || "").trim();
	frappe.msgprint({
		title: code === "10000401" ? __("SMC3 Authentication Error") : __("SMC3 Carrier Credentials"),
		indicator: "red",
		message: (result && result.message) || fallback,
	});
	return true;
}
