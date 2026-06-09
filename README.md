# LTL Quote

Digital freight network and intelligent LTL rating engine for Frappe/ERPNext.

## Platform Layers

| Layer | Capability |
|-------|------------|
| **Rate Aggregation Engine** | Parallel multi-carrier LTL rate fetch (price, transit, accessorials) |
| **Carrier Network** | Pluggable carrier adapters (Mock, REST API, project44, BlueGrace-ready) |
| **Decision Engine** | Cheapest / Fastest / Best Value recommendations |
| **Booking & Execution** | Book shipments, generate BOL, dispatch to carrier |
| **Visibility** | Tracking events, ETA, exception alerts |

## Quick Start

1. Install on site: `bench --site <site> install-app ltl_quote`
2. Open **LTL Quote** workspace
3. Create **LTL Quote Request** → **Fetch Rates** → **Book Selected Quote**
4. Track via **LTL Shipment** → **Refresh Tracking**

## API

```bash
# Aggregate rates
curl -X POST 'https://<site>/api/method/ltl_quote.api.quote.get_ltl_rates' \
  -H 'Authorization: token <api_key>:<api_secret>' \
  -d 'origin_zip=90210&destination_zip=10001&total_weight=500&freight_class=70&accessorials=["LIFTGATE"]'

# Book shipment
curl -X POST 'https://<site>/api/method/ltl_quote.api.quote.book_shipment' \
  -d 'quote_request=LTL-QR-2026-00001&quote_row_idx=0'

# Refresh tracking
curl -X POST 'https://<site>/api/method/ltl_quote.api.quote.track_shipment' \
  -d 'shipment=LTL-SHP-2026-00001'
```

## Carrier Integrations

Configure carriers under **LTL Carrier**. Set `Connector Type` to `Mock` for development (6 simulated carriers). Implement new adapters in `ltl_quote/carrier_network/adapters/` and register in `registry.py`.

## Settings

**LTL Platform Settings** controls timeouts, decision weights, mock mode, and tracking poll behavior.
