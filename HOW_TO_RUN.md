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

## 3. Example Customer (Saibaba Enterprises)

- **Party Ledger Name**: `Indore, Saibaba Enterprises`
- **Customer ID (`ledgerGuid`)**: `ae5b5772-2b16-4f41-b083-c3c4abd0d885-00001a4d`
- **Contact**: `saibaba01enterprises@gmail.com` | `9893559251`

---

## 4. Connect via WebSocket

### WebSocket URL for Saibaba:
```
ws://localhost:8080/ws/chat?customer_id=ae5b5772-2b16-4f41-b083-c3c4abd0d885-00001a4d
```

### Example Interaction:
**Send JSON:**
```json
{"message": "What is my outstanding balance?"}
```

**Receive JSON:**
```json
{
  "event": "response",
  "message": "Your current ledger balance is ...",
  "case_id": null,
  "approval_id": null
}
```

---

## 5. REST Endpoints

### Health Check
- **PowerShell**:
  ```powershell
  Invoke-RestMethod http://localhost:8080/api/health
  # or
  curl.exe http://localhost:8080/api/health
  ```
- **Response**: `{"status": "ok"}`

### Get Saibaba Customer Profile
- **PowerShell**:
  ```powershell
  Invoke-RestMethod http://localhost:8080/api/customers/ae5b5772-2b16-4f41-b083-c3c4abd0d885-00001a4d/profile
  ```

### Get Saibaba Cases
- **PowerShell**:
  ```powershell
  Invoke-RestMethod http://localhost:8080/api/customers/ae5b5772-2b16-4f41-b083-c3c4abd0d885-00001a4d/cases
  ```

### Get Saibaba Approvals
- **PowerShell**:
  ```powershell
  Invoke-RestMethod http://localhost:8080/api/customers/ae5b5772-2b16-4f41-b083-c3c4abd0d885-00001a4d/approvals
  ```

### Decision on Approval (Management)
- **PowerShell**:
  ```powershell
  Invoke-RestMethod -Method Post "http://localhost:8080/api/approvals/<approval_id>/decision?decision=APPROVED"
  ```

---

## 6. Run Tests

```bash
cd C:\Users\meetu\OneDrive\Documents\agents\customerRep
python -m pytest tests/
```

---

## 7. Environment Variables

| Variable             | Description                                    |
| -------------------- | ---------------------------------------------- |
| `NVIDIA_API_KEY`     | NIM API key from build.nvidia.com              |
| `NIM_BASE_URL`       | `https://integrate.api.nvidia.com/v1`          |
| `NIM_MODEL`          | e.g. `nvidia/llama-3.1-nemotron-70b-instruct`  |
| `MONGODB_URI`        | `mongodb://localhost:27017/`                   |
| `MONGODB_DATABASE`   | `sf_tenant_6a33b5b2091da2fb4a7c3de4`           |
| `LOG_LEVEL`          | `INFO`                                         |
| `NIM_RATE_LIMIT_RPM` | `40`                                           |

---

## 8. Finding other Customer IDs

```python
import pymongo

client = pymongo.MongoClient("mongodb://localhost:27017/")
db = client["sf_tenant_6a33b5b2091da2fb4a7c3de4"]

# Find a customer by name (e.g. Saibaba)
ledger = db.ledgers.find_one({"ledgerName": {"$regex": "Saibaba", "$options": "i"}})
if ledger:
    print("Name:", ledger["ledgerName"])
    print("Customer ID (ledgerGuid):", ledger["ledgerGuid"])
```
