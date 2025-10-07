from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from send_sns import send_notification

app = FastAPI(title="Notification Service")

class Notification(BaseModel):
    patient_id: str
    subject: str
    message: str

@app.post("/notify")
def notify(req: Notification):
    """
    Publishes a notification to the SNS topic for a specific patient.
    The SNS topic handles routing (email/SMS) based on subscription filter policies.
    """
    try:
        msg_id = send_notification(
            patient_id=req.patient_id,
            subject=req.subject,
            message=req.message
        )
        return {
            "status": "sent",
            "message_id": msg_id,
            "patient_id": req.patient_id
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error sending notification: {str(e)}")
