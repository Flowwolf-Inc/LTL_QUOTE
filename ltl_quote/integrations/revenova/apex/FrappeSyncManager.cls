/**
 * FrappeSyncManager
 * Single Apex REST endpoint for inbound Frappe → Revenova events.
 *
 * URL: /services/apexrest/FrappeSync/
 * Method: POST
 * Body: { "event_type": "carrier_quote", "source": "frappe", "doc_name": "...", "data": { ... } }
 *
 * Required Salesforce custom fields on the quote object:
 *   Frappe_Name__c            Text (unique)
 *   Frappe_Last_Source__c     Text
 *   Frappe_Skip_Outbound__c    Checkbox  — Salesforce trigger must skip Frappe callouts when true
 *   SCAC__c, Status__c, Total_Charge__c, Transit_Days__c
 *
 * Replace rtms__CustomerQuote__c with the Revenova quote object API name in this org.
 */
@RestResource(urlMapping='/FrappeSync/*')
global with sharing class FrappeSyncManager {

    public class SyncRequest {
        public String event_type;
        public String source;
        public String doc_name;
        public Map<String, Object> data;
    }

    public class SyncResponse {
        public String status;
        public String event_type;
        public String revenova_id;
        public String message;
        public String frappe_name;
    }

    @HttpPost
    global static void handlePost() {
        RestRequest req = RestContext.request;
        RestResponse res = RestContext.response;
        res.addHeader('Content-Type', 'application/json');

        SyncResponse out = new SyncResponse();
        try {
            SyncRequest body = (SyncRequest) JSON.deserialize(
                req.requestBody.toString(),
                SyncRequest.class
            );
            if (body == null || String.isBlank(body.event_type)) {
                res.statusCode = 400;
                out.status = 'error';
                out.message = 'event_type is required.';
                res.responseBody = Blob.valueOf(JSON.serialize(out));
                return;
            }

            out.event_type = body.event_type;
            out.frappe_name = body.doc_name;

            if (body.event_type == 'carrier_quote') {
                out = upsertCarrierQuote(body);
            } else if (body.event_type == 'bol') {
                // Placeholder: map Frappe BOL payloads onto the Revenova Load / Shipment object.
                out.status = 'ignored';
                out.message = 'bol handler is not implemented.';
            } else if (body.event_type == 'shipment_status') {
                // Placeholder: map Frappe tracking events onto Revenova status history.
                out.status = 'ignored';
                out.message = 'shipment_status handler is not implemented.';
            } else {
                res.statusCode = 400;
                out.status = 'error';
                out.message = 'Unsupported event_type: ' + body.event_type;
                res.responseBody = Blob.valueOf(JSON.serialize(out));
                return;
            }

            res.statusCode = 200;
            res.responseBody = Blob.valueOf(JSON.serialize(out));
        } catch (Exception ex) {
            res.statusCode = 500;
            out.status = 'error';
            out.message = ex.getMessage();
            res.responseBody = Blob.valueOf(JSON.serialize(out));
        }
    }

    /**
     * Upsert a Revenova / Salesforce quote record from Frappe Carrier Quote data.
     * Replace rtms__CustomerQuote__c with the actual Revenova object API name in this org.
     *
     * Loop prevention: Frappe_Last_Source__c = 'frappe' so a Salesforce trigger
     * must skip callouts back to Frappe when that field is set.
     */
    private static SyncResponse upsertCarrierQuote(SyncRequest body) {
        Map<String, Object> data = body.data == null
            ? new Map<String, Object>()
            : body.data;

        String revenovaId = stringValue(data.get('revenova_id'));
        String frappeName = firstNonBlank(
            stringValue(data.get('frappe_name')),
            body.doc_name
        );

        rtms__CustomerQuote__c quote;
        if (String.isNotBlank(revenovaId)) {
            List<rtms__CustomerQuote__c> found = [
                SELECT Id
                FROM rtms__CustomerQuote__c
                WHERE Id = :revenovaId
                LIMIT 1
            ];
            quote = found.isEmpty() ? new rtms__CustomerQuote__c() : found[0];
        } else if (String.isNotBlank(frappeName)) {
            List<rtms__CustomerQuote__c> found = [
                SELECT Id
                FROM rtms__CustomerQuote__c
                WHERE Frappe_Name__c = :frappeName
                LIMIT 1
            ];
            quote = found.isEmpty() ? new rtms__CustomerQuote__c() : found[0];
        } else {
            quote = new rtms__CustomerQuote__c();
        }

        quote.Frappe_Name__c = frappeName;
        quote.Frappe_Last_Source__c = 'frappe';
        quote.Frappe_Skip_Outbound__c = true;

        String carrierName = stringValue(data.get('carrier_name'));
        if (String.isNotBlank(carrierName)) {
            quote.Name = carrierName;
        }
        String scac = stringValue(data.get('quoted_scac'));
        if (String.isNotBlank(scac)) {
            quote.SCAC__c = scac;
        }
        String status = stringValue(data.get('status'));
        if (String.isNotBlank(status)) {
            quote.Status__c = status;
        }
        if (data.get('total_charge') != null) {
            quote.Total_Charge__c = Decimal.valueOf(String.valueOf(data.get('total_charge')));
        }
        if (data.get('transit_days') != null) {
            quote.Transit_Days__c = Integer.valueOf(String.valueOf(data.get('transit_days')));
        }

        upsert quote;

        SyncResponse out = new SyncResponse();
        out.status = 'success';
        out.event_type = 'carrier_quote';
        out.revenova_id = quote.Id;
        out.frappe_name = frappeName;
        out.message = 'Carrier quote upserted.';
        return out;
    }

    private static String stringValue(Object value) {
        return value == null ? null : String.valueOf(value).trim();
    }

    private static String firstNonBlank(String a, String b) {
        return String.isNotBlank(a) ? a : b;
    }
}
