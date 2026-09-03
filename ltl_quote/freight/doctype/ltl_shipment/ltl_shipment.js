// Copyright (c) 2026, LTL Quote and contributors
// For license information, please see license.txt

function resolve_bol_url(doc) {
	if (!doc) return "";
	const url = String(doc.bol_document_url || "").trim();
	if (url) {
		if (url.startsWith("http://") || url.startsWith("https://")) return url;
		return window.location.origin + (url.startsWith("/") ? url : `/${url}`);
	}
	const attach = String(doc.bol_document || "").trim();
	if (!attach) return "";
	if (attach.startsWith("http://") || attach.startsWith("https://")) return attach;
	return window.location.origin + (attach.startsWith("/") ? attach : `/${attach}`);
}

frappe.core = frappe.core || {};
frappe.core.utils = frappe.core.utils || {};

frappe.core.utils.update_dayton_carrier_bol = function (frm) {
	frappe.call({
		method: "update_electronic_bol",
		doc: frm.doc,
		freeze: true,
		callback(r) {
			if (r.message?.success) {
				frappe.show_alert({
					message: __("Updated BOL saved."),
					indicator: "green",
				});
				frm.reload_doc();
				return;
			}

			if (r.message?.status === "info") {
				frappe.msgprint({
					title: __("Dayton BOL Pending"),
					indicator: "orange",
					message: r.message.message || __("The BOL document is not ready yet."),
				});
				return;
			}

			frappe.msgprint({
				title: __("Dayton BOL Update Failed"),
				indicator: "red",
				message: r.message?.error || r.message?.message || __("Unknown error"),
			});
		},
	});
};

frappe.ui.form.on("LTL Shipment", {
	refresh(frm) {
		apply_pro_number_barcode_help(frm);
		if (!frm.is_new()) {
			load_pro_number_barcode_spec(frm);
			frm.remove_custom_button(__("View BOL"));
			const bol_url = resolve_bol_url(frm.doc);
			if (bol_url) {
				frm.add_custom_button(__("View BOL"), () => {
					window.open(bol_url, "_blank", "noopener,noreferrer");
				}).addClass("btn-primary");
			}

			frm.remove_custom_button(__("Dispatch to Carrier"));
			frm.remove_custom_button(__("Update Electronic BOL"));
			frm.remove_custom_button(__("Track Location"));
			frm.remove_custom_button(__("Generate BOL"));
			frm.remove_custom_button(__("Cancel BOL"));
			frm.remove_custom_button(__("Get BOL Image"));
			frm.remove_custom_button(__("View BOL Image"));
			frm.remove_custom_button(__("Dispatch to Carrier"), __("Actions"));
			frm.remove_custom_button(__("Update Electronic BOL"), __("Actions"));
			frm.remove_custom_button(__("Track Location"), __("Actions"));
			frm.remove_custom_button(__("Cancel BOL"), __("SMC3 Actions"));
			frm.remove_custom_button(__("Get BOL Image"), __("SMC3 Actions"));
			frm.remove_custom_button(__("View BOL Image"), __("SMC3 Actions"));
			frm.remove_custom_button(__("Get Next PRO"), __("SMC3 Actions"));
			frm.remove_custom_button(__("Get POD"), __("SMC3 Actions"));
			frm.remove_custom_button(__("Get POD"));
			frm.remove_custom_button(__("Get DR"), __("SMC3 Actions"));
			frm.remove_custom_button(__("Get DR"));
			frm.remove_custom_button(__("Origin Terminal"), __("SMC3 Actions"));
			frm.remove_custom_button(__("Destination Terminal"), __("SMC3 Actions"));
			frm.remove_custom_button(__("Terminal Info"), __("SMC3 Actions"));

			frm.add_custom_button(__("Refresh Tracking"), () => {
				frappe.call({
					method: "refresh_tracking",
					doc: frm.doc,
					freeze: true,
					callback() {
						frm.reload_doc();
					},
				});
			}, __("Visibility"));

			if (is_pickup_tracking_carrier(frm.doc.carrier) && frm.doc.status === "Booked" && frm.doc.dispatch_status !== "Acknowledged") {
				const carrier_label = pickup_carrier_label(frm.doc.carrier);
				frm.add_custom_button(__("Schedule Pickup"), () => {
					frappe.call({
						method: "dispatch_to_carrier",
						doc: frm.doc,
						freeze: true,
						freeze_message: __("Scheduling pickup with {0}...", [carrier_label]),
						callback(r) {
							if (r.message?.status === "acknowledged" || r.message?.pickup_number) {
								frappe.show_alert({
									message: __("Pickup scheduled successfully."),
									indicator: "green",
								});
							}
							frm.reload_doc();
						},
					});
				});
			} else if (frm.doc.status === "Booked" && frm.doc.dispatch_status !== "Acknowledged") {
				frm.add_custom_button(__("Dispatch to Carrier"), () => {
					frappe.call({
						method: "dispatch_to_carrier",
						doc: frm.doc,
						freeze: true,
						callback() {
							frm.reload_doc();
						},
					});
				});
			}

			if (is_pickup_tracking_carrier(frm.doc.carrier) && frm.doc.pickup_number) {
				const carrier_label = pickup_carrier_label(frm.doc.carrier);
				const pickup_method = pickup_lookup_method(frm.doc.carrier);
				frm.add_custom_button(__("View Pickup"), () => {
					frappe.call({
						method: pickup_method,
						args: { shipment: frm.doc.name },
						freeze: true,
						callback(r) {
							const pickup = r.message?.pickup || {};
							frappe.msgprint({
								title: __("{0} Pickup", [carrier_label]),
								indicator: r.message?.status === "success" ? "green" : "orange",
								message: [
									`${__("Pickup Number")}: ${pickup.pickup_number || "—"}`,
									`${__("Status")}: ${pickup.status || "—"}`,
									`${__("Ready")}: ${pickup.ready || "—"}`,
									`${__("Close")}: ${pickup.close || "—"}`,
								].join("<br>"),
							});
							frm.reload_doc();
						},
					});
				}, __("{0} Actions", [carrier_label]));

				if (frm.doc.pickup_status !== "Cancelled") {
					frm.add_custom_button(__("Cancel Pickup"), () => {
						frappe.confirm(
							__("Cancel this {0} pickup?", [carrier_label]),
							() => {
								frappe.call({
									method: "cancel_pickup",
									doc: frm.doc,
									freeze: true,
									callback(r) {
										if (r.message?.status === "success") {
											frappe.show_alert({
												message: __("Pickup cancelled."),
												indicator: "green",
											});
										}
										frm.reload_doc();
									},
								});
							}
						);
					}, __("{0} Actions", [carrier_label]));
				}
			}

			if (frm.doc.carrier === "DAYTON" && frm.doc.dayton_bol_id) {
				frm.add_custom_button(__("Generate BOL"), () => {
					frappe.core.utils.update_dayton_carrier_bol(frm);
				}).addClass("btn-primary");
			}

			if (
				is_smc3_carrier(frm.doc.carrier)
				&& frm.doc.status !== "Cancelled"
				&& frm.doc.status !== "Delivered"
			) {
				frm.add_custom_button(__("Get Next PRO"), () => {
					const run = (force) => {
						frappe.call({
							method: "assign_next_pro",
							doc: frm.doc,
							args: { force },
							freeze: true,
							freeze_message: __("Requesting next PRO from SMC3…"),
							callback(r) {
								if (r.message?.status === "success") {
									frappe.show_alert({
										message: __("PRO {0} assigned.", [r.message.pro_number || ""]),
										indicator: "green",
									});
								}
								frm.reload_doc();
							},
						});
					};
					if (frm.doc.pro_number) {
						frappe.confirm(
							__("PRO {0} is already assigned. Assign a new number?", [frm.doc.pro_number]),
							() => run(1)
						);
					} else {
						run(0);
					}
				}, __("SMC3 Actions"));
			}

			if (
				is_smc3_carrier(frm.doc.carrier)
				&& frm.doc.status !== "Cancelled"
				&& frm.doc.status !== "Delivered"
				&& (frm.doc.pro_number || frm.doc.bol_number)
			) {
				frm.add_custom_button(__("Get BOL Image"), () => {
					const pdf_url = resolve_bol_url(frm.doc);
					if (pdf_url) {
						window.open(pdf_url, "_blank", "noopener,noreferrer");
						return;
					}
					frappe.call({
						method: "ltl_quote.carrier_network.adapters.smc3.fetch_bol_image",
						args: { shipment_name: frm.doc.name },
						freeze: true,
						freeze_message: __("Fetching BOL PDF from SMC3..."),
						callback(r) {
							if (r.message?.status === "success") {
								if (r.message.file_url) {
									window.open(r.message.file_url, "_blank", "noopener,noreferrer");
								}
								frappe.show_alert({
									message: __("BOL PDF opened."),
									indicator: "green",
								});
							}
							frm.reload_doc();
						},
					});
				}, __("SMC3 Actions"));
				frm.add_custom_button(__("Cancel BOL"), () => {
					frappe.confirm(__("Cancel this SMC3 bill of lading?"), () => {
						frappe.call({
							method: "cancel_bol",
							doc: frm.doc,
							freeze: true,
							freeze_message: __("Cancelling BOL…"),
							callback(r) {
								if (r.message?.status === "success") {
									frappe.show_alert({
										message: __("BOL cancelled."),
										indicator: "green",
									});
								}
								frm.reload_doc();
							},
						});
					});
				}, __("SMC3 Actions"));
			}

			if (is_smc3_carrier(frm.doc.carrier) && frm.doc.bol_image) {
				frm.add_custom_button(__("View BOL Image"), () => {
					const image_url = resolve_bol_url({ bol_document: frm.doc.bol_image });
					if (image_url) {
						window.open(image_url, "_blank", "noopener,noreferrer");
					}
				}, __("SMC3 Actions"));
			}

			if (is_smc3_carrier(frm.doc.carrier) && (frm.doc.pro_number || frm.doc.bol_number)) {
				const $pod_btn = frm.add_custom_button(__("Get POD"), () => {
					fetch_smc3_document(frm, "POD");
				}, __("SMC3 Actions"));
				if (frm.doc.status !== "Delivered" && $pod_btn) {
					$pod_btn.removeClass("btn-primary").addClass("btn-default");
				} else if ($pod_btn) {
					$pod_btn.addClass("btn-primary");
				}
			}

			if (is_smc3_carrier(frm.doc.carrier) && frm.doc.status === "Delivered" && frm.doc.pro_number) {
				const $dr_btn = frm.add_custom_button(__("Get DR"), () => {
					fetch_smc3_delivery_receipt(frm);
				});
				if ($dr_btn) $dr_btn.addClass("btn-primary");
				frm.add_custom_button(__("Get DR"), () => {
					fetch_smc3_delivery_receipt(frm);
				}, __("SMC3 Actions"));
			}

			if (is_smc3_carrier(frm.doc.carrier)) {
				frm.add_custom_button(__("Origin Terminal"), () => {
					lookup_smc3_terminal(frm, "origin");
				}, __("SMC3 Actions"));
				frm.add_custom_button(__("Destination Terminal"), () => {
					lookup_smc3_terminal(frm, "destination");
				}, __("SMC3 Actions"));
				add_terminal_lookup_buttons(frm);
			}

			if (is_pickup_tracking_carrier(frm.doc.carrier) && frm.doc.pro_number) {
				const carrier_label = pickup_carrier_label(frm.doc.carrier);
				const is_dayton = pickup_connector_key(frm.doc.carrier) === "DAYTON";
				frm.add_custom_button(__("Track Location"), () => {
					frappe.show_alert({
						message: __("Contacting {0} network...", [carrier_label]),
						indicator: "blue",
					});

					frappe.call({
						method: is_dayton ? "fetch_dayton_tracking_updates" : "refresh_tracking",
						doc: frm.doc,
						callback(r) {
							if (!is_dayton) {
								frm.reload_doc();
								frappe.show_alert({
									message: __("Tracking details synchronized."),
									indicator: "green",
								});
								return;
							}
							if (r.message?.status === "success") {
								frm.reload_doc();
								frappe.show_alert({
									message: r.message.message,
									indicator: "green",
								});
								return;
							}

							if (r.message) {
								frappe.msgprint({
									title: __("Tracking System"),
									message: r.message.message,
									indicator: r.message.status === "error" ? "red" : "orange",
								});
							}
						},
					});
				});
			}

			if (frm.doc.carrier === "DAYTON") {
				frm.add_custom_button(__("Track Pending Shipments"), () => {
					get_dayton_customer_code(frm).then((customer_code) => {
						frappe.call({
							method: "ltl_quote.carrier_network.adapters.dayton.fetch_dayton_pending_shipments",
							args: { customer_code },
							freeze: true,
							freeze_message: __("Fetching pending shipments from Dayton..."),
							callback(r) {
								if (r.message?.status === "error") {
									frappe.msgprint({
										title: __("Dayton Tracking"),
										indicator: "red",
										message: r.message.text || __("Failed to fetch pending shipments."),
									});
									return;
								}
								if (r.message?.results) {
									render_dayton_tracking_dashboard(
										frm,
										"Dayton Pending Shipments Scan",
										r.message.results
									);
								} else {
									render_dayton_tracking_dashboard(frm, "Dayton Pending Shipments Scan", []);
								}
							},
						});
					});
				}, __("Dayton Actions"));

				frm.add_custom_button(__("Track By Date Range"), () => {
					frappe.prompt(
						[
							{
								label: __("Start Date"),
								fieldname: "start_date",
								fieldtype: "Date",
								reqd: 1,
								default: frappe.datetime.add_days(frappe.datetime.get_today(), -1),
							},
							{
								label: __("End Date"),
								fieldname: "end_date",
								fieldtype: "Date",
								reqd: 1,
								default: frappe.datetime.get_today(),
							},
						],
						(values) => {
							get_dayton_customer_code(frm).then((customer_code) => {
								frappe.call({
									method: "ltl_quote.carrier_network.adapters.dayton.fetch_dayton_tracking_by_date",
									args: {
										start_date: values.start_date,
										end_date: values.end_date,
										customer_code,
									},
									freeze: true,
									freeze_message: __("Querying historical tracking window..."),
									callback(r) {
										if (r.message?.status === "error") {
											frappe.msgprint({
												title: __("Dayton Tracking"),
												indicator: "red",
												message: r.message.text || __("Failed to fetch tracking data."),
											});
											return;
										}
										if (r.message?.results) {
											render_dayton_tracking_dashboard(
												frm,
												"Historical Shipments Tracking",
												r.message.results
											);
										} else {
											render_dayton_tracking_dashboard(frm, "Historical Shipments Tracking", []);
										}
									},
								});
							});
						},
						__("Specify Tracking Range"),
						__("Fetch Data")
					);
				}, __("Dayton Actions"));
			}

			if (frm.doc.carrier === "ARCB" && frm.doc.status === "Booked") {
				let btn = frm.add_custom_button(__("Generate BOL"), function () {
					frappe.call({
						method: "ltl_quote.api.shipment.attach_arcbest_bol_to_shipment",
						args: {
							shipment_id: frm.doc.name,
						},
						freeze: true,
						freeze_message: __("Downloading PDF from ArcBest and attaching to panel..."),
						callback(r) {
							if (r.message && r.message.status === "success") {
								frappe.show_alert({
									message: __("BOL Attached Successfully!"),
									indicator: "green",
								});
								frm.reload_doc();
							}
						},
					});
				});

				if (btn) {
					btn
						.removeClass("btn-default")
						.css({
							"background-color": "#111111",
							color: "#ffffff",
							"font-weight": "bold",
							border: "1px solid #000000",
						});
				}
			}
		}
	},
});

const LTL_PRO_BARCODE_SHORT = "GS1-128 / UCC-128 compliant carrier tracking barcode.";
const LTL_PRO_BARCODE_SPEC =
	"The bar code symbology used for the Bill of Lading and the SCAC/PRO shall comply with UCC/EAN-128 (GS1-128) standards. " +
	"All GS1-128 barcodes contain an Application Identifier (AI) that defines the meaning of the encoded data. " +
	"For the SCAC/PRO, the AI used is always 9012K. Encode the 4-character SCAC immediately followed by the PRO number.";

function barcode_spec_from_payload(data) {
	const symbology = String((data && data.symbology) || "").trim();
	const printing = String((data && data.printing_requirements) || "").trim();
	const parts = [];
	if (symbology) parts.push(symbology);
	if (printing) parts.push(printing);
	return parts.join("\n\n") || LTL_PRO_BARCODE_SPEC;
}

function teardown_pro_barcode_popover($root) {
	($root || $(document))
		.find(".ltl-pro-barcode-info")
		.each(function () {
			const $el = $(this);
			if ($el.data("bs.popover") || $el.data("popover")) {
				try {
					$el.popover("dispose");
				} catch (e) {
					try {
						$el.popover("destroy");
					} catch (e2) {
						/* ignore */
					}
				}
			}
			$el.remove();
		});
}

function bind_pro_barcode_popover($icon, spec) {
	if (!$icon || !$icon.length || typeof $icon.popover !== "function") return;
	const html = frappe.utils.escape_html(spec || LTL_PRO_BARCODE_SPEC).replace(/\n/g, "<br>");
	$icon.popover({
		trigger: "hover focus",
		placement: "auto",
		container: "body",
		html: true,
		title: __("Barcode specification"),
		content: html,
	});
}

function apply_pro_number_barcode_help(frm, spec) {
	frm.set_df_property("pro_number", "description", __(LTL_PRO_BARCODE_SHORT));
	const field = frm.get_field("pro_number");
	if (!field || !field.$wrapper) return;
	const $wrapper = field.$wrapper;
	teardown_pro_barcode_popover($wrapper);
	const $label = $wrapper.find("label.control-label").first();
	if (!$label.length) return;
	const $icon = $(
		`<i class="fa fa-info-circle ltl-pro-barcode-info text-muted" tabindex="0" role="img" aria-label="${__(
			"Barcode specification"
		)}" style="margin-left:6px;cursor:help;"></i>`
	);
	$label.append($icon);
	bind_pro_barcode_popover($icon, spec || frm._ltl_barcode_spec || LTL_PRO_BARCODE_SPEC);
}

function load_pro_number_barcode_spec(frm) {
	frm.call("get_barcode_spec")
		.then((r) => {
			const spec = barcode_spec_from_payload(r.message);
			frm._ltl_barcode_spec = spec;
			apply_pro_number_barcode_help(frm, spec);
		})
		.catch(() => {
			apply_pro_number_barcode_help(frm, LTL_PRO_BARCODE_SPEC);
		});
}

function get_dayton_customer_code(frm) {
	if (frm.doc.customer_code) {
		return Promise.resolve(frm.doc.customer_code);
	}
	if (frm.doc.carrier) {
		return frappe.db
			.get_value("LTL Carrier", frm.doc.carrier, "account_number")
			.then((r) => r.message?.account_number || "0055666");
	}
	return Promise.resolve("0055666");
}

function render_dayton_tracking_dashboard(frm, title, results) {
	frm.dashboard.clear_headline();
	frm.dashboard.reset();

	if (!results || results.length === 0) {
		frm.dashboard.set_headline_alert(
			__("No active transit tracking events returned from Dayton Freight currently."),
			"orange"
		);
		return;
	}

	let html_content = `
		<div class="dayton-tracking-container" style="padding: 15px; background: var(--card-bg); border: 1px solid var(--border-color); border-radius: var(--border-radius-md); margin-bottom: 20px;">
			<h6 class="text-muted text-uppercase tracking-wide" style="margin-bottom: 12px; font-size: 11px; font-weight: 600; color: var(--text-muted);">
				<i class="fa fa-truck" style="margin-right: 6px;"></i> ${__(title)}
			</h6>
			<div class="row">
	`;

	results.forEach((row) => {
		const activity = row.status ? row.status.activity : row.event || "Unknown Status";
		const code = row.status ? row.status.activityCode : "N/A";

		let indicator_color = "blue";
		if (["DELIVERED", "DLV"].includes(code)) indicator_color = "green";
		if (["DELAY", "EXC"].includes(code)) indicator_color = "red";
		if (["ETOFD", "OFD"].includes(code)) indicator_color = "orange";

		html_content += `
				<div class="col-sm-4" style="margin-bottom: 10px;">
					<div style="padding: 10px; background: var(--control-bg); border-left: 4px solid var(--text-${indicator_color}); border-radius: 4px;">
						<div style="font-size: 11px; color: var(--text-muted);">PRO #${row.pro}</div>
						<div style="font-weight: 600; font-size: 13px; margin: 2px 0;">${activity}</div>
						<span class="indicator ${indicator_color}" style="font-size: 10px;">Code: ${code}</span>
					</div>
				</div>
		`;
	});

	html_content += `
			</div>
		</div>
	`;

	frm.dashboard.add_section(html_content);
	frm.dashboard.show();

	frappe.utils.scroll_to(frm.dashboard.wrapper);
}

function pickup_connector_key(carrier_code) {
	const code = String(carrier_code || "").toUpperCase();
	if (code === "DAYTON") return "DAYTON";
	if (["TFORCE", "TFF"].includes(code) || code.includes("TFORCE")) return "TFORCE";
	if (["ARCB", "ARCBEST", "ABF", "ABFS"].includes(code) || code.includes("ARC")) return "ARCB";
	if (code === "SMC3" || code.includes("SMC3")) return "SMC3";
	return code;
}

function is_tforce_carrier(carrier_code) {
	return pickup_connector_key(carrier_code) === "TFORCE";
}

function is_arcbest_carrier(carrier_code) {
	return pickup_connector_key(carrier_code) === "ARCB";
}

function is_pickup_tracking_carrier(carrier_code) {
	return ["DAYTON", "TFORCE", "ARCB", "SMC3"].includes(pickup_connector_key(carrier_code));
}

function is_smc3_carrier(carrier_code) {
	const code = String(carrier_code || "").toUpperCase();
	return code === "SMC3" || code.includes("SMC3");
}

function pickup_carrier_label(carrier_code) {
	const key = pickup_connector_key(carrier_code);
	if (key === "TFORCE") return "TForce";
	if (key === "ARCB") return "ArcBest";
	if (key === "DAYTON") return "Dayton";
	if (key === "SMC3") return "SMC3";
	return key || "Carrier";
}

function pickup_lookup_method(carrier_code) {
	const key = pickup_connector_key(carrier_code);
	if (key === "TFORCE") return "ltl_quote.api.shipping.get_tforce_pickup";
	if (key === "ARCB") return "ltl_quote.api.shipping.get_arcbest_pickup";
	if (key === "SMC3") return "ltl_quote.api.shipping.get_smc3_pickup";
	return "ltl_quote.api.shipping.get_dayton_pickup";
}

function fetch_smc3_pod(frm, opts) {
	fetch_smc3_document(frm, "POD", opts);
}

function fetch_smc3_delivery_receipt(frm) {
	if ((frm.doc.status || "") !== "Delivered" || !String(frm.doc.pro_number || "").trim()) {
		frappe.msgprint({
			title: __("Delivery Receipt"),
			indicator: "orange",
			message: __("Get DR is only available for Delivered shipments that have a PRO number."),
		});
		return;
	}
	frappe.call({
		method: "ltl_quote.api.smc3.get_smc3_document",
		args: {
			shipment: frm.doc.name,
			scac: frm.doc.carrier,
			pro_number: frm.doc.pro_number,
			document_type: "DR",
			file_type: "PDF",
		},
		freeze: true,
		freeze_message: __("Fetching delivery receipt PDF from SMC3…"),
		callback(r) {
			open_smc3_document_result(r.message || {}, "DR", {
				on_success: () => frm.reload_doc(),
			});
		},
	});
}

function fetch_smc3_document(frm, document_type, opts) {
	opts = opts || {};
	document_type = String(document_type || "POD").toUpperCase();
	if (document_type === "DR") {
		fetch_smc3_delivery_receipt(frm);
		return;
	}
	const labels = {
		POD: { title: __("Proof of Delivery"), fetching: __("Fetching POD PDF from SMC3…") },
	};
	const copy = labels[document_type] || labels.POD;
	if ((frm.doc.status || "") !== "Delivered") {
		frappe.msgprint({
			title: copy.title,
			indicator: "orange",
			message: __("POD is only available for Delivered shipments"),
		});
		return;
	}
	frappe.call({
		method: "ltl_quote.api.smc3.get_smc3_document",
		args: {
			shipment: frm.doc.name,
			scac: frm.doc.carrier,
			pro_number: frm.doc.pro_number,
			document_type,
			file_type: "PDF",
		},
		freeze: true,
		freeze_message: copy.fetching,
		callback(r) {
			open_smc3_document_result(r.message || {}, document_type, {
				on_success: opts.on_success || (() => frm.reload_doc()),
			});
		},
	});
}

function open_smc3_pod_result(result, opts) {
	open_smc3_document_result(result, "POD", opts);
}

function open_smc3_document_result(result, document_type, opts) {
	opts = opts || {};
	document_type = String(document_type || "POD").toUpperCase();
	const is_dr = document_type === "DR";
	if (result.status !== "success") {
		frappe.msgprint({
			title: is_dr ? __("Get DR Failed") : __("Get POD Failed"),
			indicator: "red",
			message:
				result.message ||
				(is_dr
					? __("Could not retrieve the delivery receipt.")
					: __("Could not retrieve the proof of delivery.")),
		});
		return;
	}
	const title = is_dr ? __("Delivery Receipt") : __("Proof of Delivery");
	const opened = open_smc3_pdf(result, title);
	frappe.show_alert({
		message:
			result.message ||
			(opened
				? is_dr
					? __("Delivery receipt attached and opened.")
					: __("POD attached and opened.")
				: is_dr
					? __("Delivery receipt attached.")
					: result.pod_name
						? __("POD attached as {0}.", [result.pod_name])
						: __("POD PDF opened.")),
		indicator: "green",
	});
	if (typeof opts.on_success === "function") {
		opts.on_success(result);
	}
}

function open_smc3_pdf(result, title) {
	const url = String(result.file_url || "").trim() || smc3_pdf_blob_url(result.document_binary);
	if (!url) {
		if (result.pod_name) {
			frappe.set_route("Form", "LTL POD", result.pod_name);
			return false;
		}
		return false;
	}
	window.open(url, "_blank", "noopener,noreferrer");
	const dialog = new frappe.ui.Dialog({
		title,
		size: "extra-large",
		fields: [{ fieldtype: "HTML", fieldname: "preview" }],
	});
	dialog.fields_dict.preview.$wrapper.html(
		`<iframe src="${frappe.utils.escape_html(url)}" style="width:100%;height:75vh;border:0;border-radius:8px;background:#fff;"></iframe>`
	);
	dialog.show();
	return true;
}

function smc3_pdf_blob_url(document_binary) {
	const raw = String(document_binary || "").trim();
	if (!raw) return "";
	try {
		let b64 = raw;
		if (b64.indexOf(",") >= 0 && b64.toLowerCase().indexOf("data:") === 0) {
			b64 = b64.split(",", 2)[1] || "";
		}
		const binary = atob(b64.replace(/\s/g, ""));
		const bytes = new Uint8Array(binary.length);
		for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
		return URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
	} catch (e) {
		return "";
	}
}

function shipment_origin_zip(doc) {
	return String((doc && (doc.origin_zip || doc.bol_shipper_postal_code)) || "").trim();
}

function shipment_destination_zip(doc) {
	return String((doc && (doc.destination_zip || doc.bol_consignee_postal_code)) || "").trim();
}

function add_terminal_lookup_buttons(frm) {
	attach_terminal_lookup_button(frm, "bol_shipper_postal_code", "origin", __("Origin Terminal"));
	attach_terminal_lookup_button(frm, "bol_consignee_postal_code", "destination", __("Destination Terminal"));
}

function attach_terminal_lookup_button(frm, fieldname, lane, label) {
	const field = frm.get_field(fieldname);
	if (!field || !field.$wrapper) return;
	const $wrapper = field.$wrapper;
	$wrapper.find(".ltl-terminal-lookup-btn").remove();
	const $label = $wrapper.find("label.control-label").first();
	const $icon = $(
		`<i class="fa fa-building-o ltl-terminal-lookup-btn text-muted" tabindex="0" role="button" title="${frappe.utils.escape_html(
			label
		)}" aria-label="${frappe.utils.escape_html(label)}" style="margin-left:6px;cursor:pointer;color:var(--primary-color, #7c5cfc);"></i>`
	);
	$icon.on("click", (e) => {
		e.preventDefault();
		e.stopPropagation();
		lookup_smc3_terminal(frm, lane);
	});
	if ($label.length) {
		$label.append($icon);
	} else {
		$wrapper.append($icon);
	}
}

function lookup_smc3_terminal(frm, lane) {
	const is_dest = String(lane || "").toLowerCase().indexOf("dest") >= 0;
	const zip = is_dest ? shipment_destination_zip(frm.doc) : shipment_origin_zip(frm.doc);
	const title = is_dest ? __("Destination Terminal") : __("Origin Terminal");
	const run = (postal_code) => {
		if (!String(postal_code || "").trim()) {
			frappe.msgprint({
				title,
				indicator: "orange",
				message: __("A {0} ZIP is required to look up the carrier terminal.", [
					is_dest ? __("destination") : __("origin"),
				]),
			});
			return;
		}
		frappe.call({
			method: "ltl_quote.api.smc3.get_carrier_terminal_info",
			args: {
				scac: frm.doc.carrier,
				postal_code,
				shipment: frm.doc.name,
				lane: is_dest ? "destination" : "origin",
			},
			freeze: true,
			freeze_message: __("Looking up {0}…", [title]),
			callback(r) {
				show_smc3_terminal_dialog(title, r.message || {});
			},
		});
	};
	if (zip) {
		run(zip);
		return;
	}
	if (!frm.doc.quote_request) {
		run("");
		return;
	}
	frappe.db
		.get_value("LTL Quote Request", frm.doc.quote_request, ["origin_zip", "destination_zip"])
		.then((r) => {
			const data = (r && r.message) || {};
			run(is_dest ? data.destination_zip : data.origin_zip);
		})
		.catch(() => run(""));
}

function show_smc3_terminal_dialog(title, result) {
	const terminals = Array.isArray(result.terminals) ? result.terminals : [];
	if (!terminals.length) {
		frappe.msgprint({
			title,
			indicator: "orange",
			message: __("No terminal locations were returned for ZIP {0}.", [result.postal_code || "—"]),
		});
		return;
	}
	const row = (label, value) =>
		`<div style="display:grid;grid-template-columns:160px 1fr;gap:8px;font-size:13px;line-height:1.55;margin-bottom:4px;">
			<span style="color:var(--text-muted);">${frappe.utils.escape_html(label)}</span>
			<strong>${frappe.utils.escape_html(value || "—")}</strong>
		</div>`;
	const cards = terminals
		.map((item) => {
			const name = item.name || __("Carrier Terminal");
			return `
				<div style="border:1px solid var(--border-color);border-radius:10px;padding:14px 16px;margin-bottom:12px;background:var(--card-bg);">
					<div style="display:flex;justify-content:space-between;gap:8px;align-items:center;margin-bottom:10px;">
						<strong style="font-size:14px;">${frappe.utils.escape_html(name)}</strong>
						<span class="indicator-pill blue" style="font-size:11px;">${__("SCAC")}: ${frappe.utils.escape_html(item.scac || "—")}</span>
					</div>
					${row(__("Terminal Name"), name)}
					${row(__("Address"), item.address)}
					${row(__("City"), item.city)}
					${row(__("State"), item.state)}
					${row(__("ZIP"), item.zip)}
					${row(__("Phone Number"), item.phone)}
					${row(__("Contact Manager"), item.contact)}
				</div>`;
		})
		.join("");
	const dialog = new frappe.ui.Dialog({
		title,
		size: "large",
		fields: [{ fieldtype: "HTML", fieldname: "terminal_info" }],
	});
	dialog.fields_dict.terminal_info.$wrapper.html(
		`<div style="margin-bottom:10px;color:var(--text-muted);font-size:12px;">
			${__("ZIP")} ${frappe.utils.escape_html(result.postal_code || "—")}
			· ${__("SCAC")} ${frappe.utils.escape_html(result.scac || "—")}
		</div>${cards}`
	);
	dialog.show();
}
