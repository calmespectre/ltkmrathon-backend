import os
import base64
from fastapi import FastAPI, Request, status, HTTPException
from pydantic import BaseModel
import requests
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="KCB STK Push API", version="1.0.0")

KCB_CLIENT_ID = os.getenv("KCB_CLIENT_ID")
KCB_CLIENT_SECRET = os.getenv("KCB_CLIENT_SECRET")
KCB_API_KEY = os.getenv("KCB_API_KEY")
KCB_BASE_URL = os.getenv("KCB_BASE_URL")
KCB_CALLBACK_URL = os.getenv("KCB_CALLBACK_URL")

db_payment_records = {}


class STKPushRequest(BaseModel):
    phoneNumber: str
    amount: int


def get_kcb_oauth_token():
    if not KCB_CLIENT_ID or not KCB_CLIENT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Missing configuration keys.")
    credentials = f"{KCB_CLIENT_ID}:{KCB_CLIENT_SECRET}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    headers = {"Authorization": f"Basic {encoded_credentials}",
               "Content-Type": "application/x-www-form-urlencoded"}
    token_url = f"{KCB_BASE_URL}/oauth/v1/generate?grant_type=client_credentials"
    try:
        response = requests.get(token_url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json().get("access_token")
    except requests.exceptions.RequestException:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail="Gateway authentication failure.")


@app.post("/api/v1/stk-push", status_code=status.HTTP_200_OK)
async def initiate_stk_push(request_data: STKPushRequest):
    access_token = get_kcb_oauth_token()
    headers = {"Authorization": f"Bearer {access_token}",
               "Content-Type": "application/json", "apikey": KCB_API_KEY}
    kcb_payload = {
        "PhoneNumber": request_data.phoneNumber,
        "Amount": request_data.amount,
        "CallbackURL": KCB_CALLBACK_URL,
        "TransactionDesc": "Web Checkout Payment",
        "AccountReference": "BusinessRef"
    }
    stk_url = f"{KCB_BASE_URL}/mpesa/v1/stkpush/processrequest"
    try:
        response = requests.post(
            stk_url, json=kcb_payload, headers=headers, timeout=10)
        res_data = response.json()

        # Extract CheckoutRequestID from KCB response
        checkout_id = res_data.get("CheckoutRequestID")
        if checkout_id:
            db_payment_records[checkout_id] = {"status": "PENDING"}

        return res_data
    except requests.exceptions.RequestException:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail="Transaction request broadcast failed.")


@app.get("/api/v1/check-payment/{checkout_id}")
def check_payment_status(checkout_id: str):
    record = db_payment_records.get(checkout_id)
    if not record:
        return {"status": "NOT_FOUND"}
    return record


@app.post("/api/v1/kcb-webhook", status_code=status.HTTP_200_OK)
async def kcb_callback_webhook(request: Request):
    try:
        callback_data = await request.json()
        print(callback_data)

        # Parse standard Safaricom/KCB response data structure
        stk_callback = callback_data.get("Body", {}).get("stkCallback", {})
        checkout_id = stk_callback.get("CheckoutRequestID")
        result_code = stk_callback.get("ResultCode")

        if checkout_id in db_payment_records:
            if str(result_code) == "0":
                db_payment_records[checkout_id] = {"status": "SUCCESS"}
            else:
                db_payment_records[checkout_id] = {"status": "FAILED"}

        return {"status": "Success", "detail": "Callback verified"}
    except Exception:
        return {"status": "Error", "detail": "Malformed JSON structure"}
