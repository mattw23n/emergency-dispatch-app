import boto3
import json
import os
from dotenv import load_dotenv

load_dotenv()

sns = boto3.client(
    "sns",
    region_name=os.getenv("AWS_DEFAULT_REGION"),
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY")
)
TOPIC_ARN = os.getenv("TOPIC_ARN")

def send_notification(patient_id: str, subject: str, message: str):
    # Format the message for email/SMS readability
    formatted_message = (
        f"Subject: {subject}\n\n"
        f"Message:\n{message}"
    )

    try:
        # Publish to SNS topic with filtering by patient_id
        response = sns.publish(
            TopicArn=TOPIC_ARN,
            Message=formatted_message,
            Subject=subject,
            MessageAttributes={
                "patient_id": {
                    "DataType": "String",
                    "StringValue": patient_id
                }
            }
        )

        print(f"Notification sent for patient {patient_id} (MessageId: {response['MessageId']})")
        return response["MessageId"]

    except Exception as e:
        print(f"Error sending SNS notification: {e}")
        raise