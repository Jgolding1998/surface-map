import json


def artifacts(revision=1):
    entries = [
        {"request": {"method": "GET", "url": "https://demo.invalid/api/orders/1042?page=1"}, "response": {"status": 200, "content": {"mimeType": "application/json", "text": json.dumps({"id": 1042, "status": "ready", "total": 48.5})}}},
        {"request": {"method": "POST", "url": "https://demo.invalid/api/orders", "postData": {"mimeType": "application/json", "text": json.dumps({"items": [{"sku": "DEMO-01", "quantity": 2}]})}}, "response": {"status": 201, "content": {"mimeType": "application/json", "text": json.dumps({"id": 1043})}}},
        {"request": {"method": "GET", "url": "https://demo.invalid/api/inventory"}, "response": {"status": 200}},
    ]
    if revision == 2:
        entries = entries[:2]
        entries[0]["response"]["status"] = 206
        entries.append({"request": {"method": "GET", "url": "https://demo.invalid/api/reports"}, "response": {"status": 200}})
    return [{"kind": "har", "text": json.dumps({"log": {"entries": entries}})}, {"kind": "html", "text": '<a href="/dashboard">Home</a><form action="/api/search" method="get"><input name="q"><input name="token" value="not-exported"></form><script src="/assets/app.js"></script><link rel="stylesheet" href="/assets/app.css">'}, {"kind": "javascript", "text": "fetch('/api/notifications');\nfetch('/api/messages', {method: 'POST'});"}]
