# Work Timing Alerts n8n Setup

1. Import `work_timing_overdue_alert_webhook.json` into n8n.
2. Set n8n environment variables:
   - `APP_INTERNAL_TOKEN`
   - `WHATSAPP_PHONE_NUMBER_ID`
   - `META_GRAPH_VERSION` (optional, default `v19.0`)
   - `WORK_TIMING_TEMPLATE_GIRI` (optional, default `work_timing_overdue_giri_v1`)
   - `WORK_TIMING_TEMPLATE_MD` (optional, default `work_timing_overdue_md_v1`)
3. Create a Header Auth credential in n8n (example name: `Whatsapp`):
   - Name: `Authorization`
   - Value: `Bearer <META_WHATSAPP_ACCESS_TOKEN>`
   - In the `Send WhatsApp Template` node, select this credential.
4. Create Meta templates using `work_timing_whatsapp_templates.json`.
5. Activate workflow and copy webhook production URL.
6. Configure Flask app env:
   - `WORK_TIMING_ALERT_MODE=webhook`
   - `WORK_TIMING_WEBHOOK_URL=<n8n webhook production URL>`
   - `WORK_TIMING_WEBHOOK_TOKEN=<same value as APP_INTERNAL_TOKEN>`
   - `WORK_TIMING_GIRI_CONTACT=<E.164 phone>`
   - `WORK_TIMING_MD_CONTACT=<E.164 phone>`
7. Keep scheduler running: `flask work-timing-check` every 5 minutes.
