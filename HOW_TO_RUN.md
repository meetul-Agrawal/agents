# How to Run

## 1. Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env — set NVIDIA_API_KEY and NIM_MODEL
```

## 2. Start server

```bash
cd C:\Users\meetu\OneDrive\Documents\agents\customerRep
uvicorn app.main:app --reload --port 8080
```

## 3. Connect via WebSocket

```
ws://localhost:8080/ws/chat?customer_id=<ledger_guid>&session_id=<optional>
```

`customer_id` = `ledgerGuid` from the `ledgers` collection.

Send JSON: `{"message": "What is my outstanding balance?"}`

Receive JSON: `{"event": "response", "message": "...", "case_id": null, ...}`

## 4. REST endpoints

- `GET /api/health`
- `GET /api/customers/{customer_id}/profile`
- `GET /api/customers/{customer_id}/cases`
- `GET /api/customers/{customer_id}/approvals`
- `POST /api/approvals/{approval_id}/decision?decision=APPROVED`

## 5. Run tests

```bash
cd C:\Users\meetu\OneDrive\Documents\agents\customerRep
python -m tests.test_receipts
python -m tests.test_cases
```

## Environment variables

| Variable             | Description                                    |
| -------------------- | ---------------------------------------------- |
| `NVIDIA_API_KEY`   | NIM API key from build.nvidia.com              |
| `NIM_BASE_URL`     | `https://integrate.api.nvidia.com/v1`        |
| `NIM_MODEL`        | e.g.`nvidia/llama-3.1-nemotron-70b-instruct` |
| `MONGODB_URI`      | `mongodb://localhost:27017/`                 |
| `MONGODB_DATABASE` | `sf_tenant_6a33b5b2091da2fb4a7c3de4`         |
| `LOG_LEVEL`        | `INFO`                                       |

## Finding a customer_id

```python
import pymongo
client = pymongo.MongoClient("mongodb://localhost:27017/")
db = client["sf_tenant_6a33b5b2091da2fb4a7c3de4"]
# Customers with receipts
r = db.vouchers.find_one({"voucherCategory": "Receipt"})
party_name = r["partyLedgerName"]
ledger = db.ledgers.find_one({"ledgerName": party_name})
print(ledger["ledgerGuid"])  # use this as customer_id
```
