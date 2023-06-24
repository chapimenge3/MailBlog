import datetime
import json
import logging
import os

import boto3

USER_TABLE = os.getenv("USER_TABLE")
FROM_EMAIL = os.getenv("FROM_EMAIL")
DOMAIN = os.getenv("DOMAIN")


logger = logging.getLogger()
logger.setLevel(logging.INFO)

client = boto3.client("dynamodb")
ses = boto3.client("ses")
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(USER_TABLE)

headers = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Credentials": True,
}


def load_body(event: dict):
    """Load body from API Gateway event

    Args:
        event (dict): API Gateway event

    Returns:
        dict: body

    Raises:
        ValueError: if body is not a valid json
    """
    try:
        body = event["body"]
        if isinstance(body, str):
            body = json.loads(body)
        return body
    except (KeyError, json.JSONDecodeError):
        raise ValueError("Invalid body")


def get_user(email: str, *args, **kwargs):
    """Get user from dynamodb

    Args:
        email (str): user email

    Returns:
        dict: dynamodb item
    """
    try:
        response = table.get_item(Key={"email": email}, *args, **kwargs)
        return response.get("Item")
    except Exception as e:
        logger.error(e)
        raise e


def add_user(item: str, *args, **kwargs):
    """Add user to dynamodb

    Args:
        item (str): item to add

    Returns:
        dict: dynamodb item
    """
    try:
        response = table.put_item(Item=item, *args, **kwargs)
        return response.get("Item")
    except Exception as e:
        logger.error(e)
        raise e


def send_email(email: str, subject: str, body: str):
    """Send email using SES

    Args:
        email (str): email address to send email to
        subject (str): email subject
        body (str): email body

    Returns:
        dict: ses response
    """
    try:
        data = {
            "Source": FROM_EMAIL,
            "Destination": {"ToAddresses": [email]},
            "Message": {"Subject": {"Data": subject}, "Body": body},
        }
        response = ses.send_email(**data)
        return response
    except Exception as e:
        logger.error(e)
        raise e


def subscribe(event: dict, _):
    """Subscribe user to mailblog

    Args:
        event (dict): API Gateway event

    Returns:
        dict: api gateway response
    """
    try:
        body = load_body(event)
        email = body.get("email")
        if not email:
            return {
                "statusCode": 400,
                "headers": headers,
                "body": json.dumps({"message": "Email is required"}),
            }
        user = get_user(email)
        if user and user.get("verified"):
            return {
                "statusCode": 400,
                "headers": headers,
                "body": json.dumps({"message": "User already subscribed"}),
            }

        item = {
            "email": email,
            "token": os.urandom(16).hex(),
            "verified": False,
            "created_at": datetime.datetime.now().isoformat(),
            "updated_at": datetime.datetime.now().isoformat(),
        }
        user = add_user(item)

        verify_url = f"{DOMAIN}/verify?email={email}&token={item['token']}"
        unsubscribe_url = f"{DOMAIN}/unsubscribe?email={email}&token={item['token']}"

        with open("templates/verify.html") as f:
            html = f.read()
        html = html.replace("{{verify_url}}", verify_url).replace(
            "{{unsubscribe_url}}", unsubscribe_url
        )

        with open("templates/verify.txt") as f:
            text = f.read()
        text = text.replace("{{verify_url}}", verify_url)

        body = {"Html": {"Data": html}, "Text": {"Data": text}}
        send_email(email, "Verify your email", body)
        return {
            "statusCode": 200,
            "headers": headers,
            "body": json.dumps({"message": "User subscribed successfully"}),
        }

    except Exception as e:
        logger.error(e)
        logger.info(event)
        return {
            "statusCode": 500,
            "headers": headers,
            "body": json.dumps({"message": "Internal server error"}),
        }


def unsubscribe(event: dict, _):
    """Unsubscribe user from mailblog

    Args:
        event (dict): API Gateway event

    Returns:
        dict: api gateway response
    """
    try:
        query_params = event["queryStringParameters"]
        email = query_params.get("email")
        token = query_params.get("token")
        if not email or not token:
            return {
                "statusCode": 400,
                "headers": headers,
                "body": json.dumps({"message": "Email and token are required"}),
            }
        user = get_user(email)
        if not user or user.get("token") != token:
            # respond should not expose if user exists or not for security reasons
            return {
                "statusCode": 400,
                "headers": headers,
                "body": json.dumps({"message": "Failed to unsubscribe"}),
            }

        user["verified"] = False
        user["updated_at"] = datetime.datetime.now().isoformat()
        table.put_item(Item=user)
        return {
            "statusCode": 200,
            "headers": headers,
            "body": json.dumps({"message": "You are successfully unsubscribed"}),
        }

    except Exception as e:
        logger.error(e)
        logger.info(event)
        return {
            "statusCode": 500,
            "headers": headers,
            "body": json.dumps({"message": "Internal server error"}),
        }


def verify(event: dict, _):
    """Verify user email

    Args:
        event (dict): API Gateway event

    Returns:
        dict: api gateway response
    """
    try:
        query_params = event["queryStringParameters"]
        email = query_params.get("email")
        token = query_params.get("token")
        if not email or not token:
            return {
                "statusCode": 400,
                "headers": headers,
                "body": json.dumps({"message": "Email and token are required"}),
            }
        user = get_user(email)
        if not user or user.get("token") != token:
            # respond should not expose if user exists or not for security reasons
            return {
                "statusCode": 400,
                "headers": headers,
                "body": json.dumps({"message": "User verification failed"}),
            }
        user["verified"] = True
        user["updated_at"] = datetime.datetime.now().isoformat()
        table.put_item(Item=user)

        with open("templates/verified.html", "r") as f:
            html = f.read()

        unsubscribe_url = f"{DOMAIN}/unsubscribe?email={email}&token={token}"
        html = html.replace("{{unsubscribe_url}}", unsubscribe_url)

        with open("templates/verified.txt", "r") as f:
            text = f.read()

        text = text.replace("{{unsubscribe_url}}", unsubscribe_url)

        body = {"Html": {"Data": html}, "Text": {"Data": text}}
        send_email(email, "Email verified", body)

        return {
            "statusCode": 200,
            "headers": headers,
            "body": json.dumps({"message": "User verified successfully"}),
        }
    except Exception as e:
        logger.error(e)
        logger.info(event)
        return {
            "statusCode": 500,
            "headers": headers,
            "body": json.dumps({"message": "Internal server error"}),
        }


def lambda_handler(event: dict, _):
    """Handler for subscription and un-subscription requests

    endpoints:
        - POST /subscription
        - GET /unsubscription
        - GET /verify

    Args:
        event (dict): API Gateway event
    """
    try:
        http_method = event["httpMethod"]
        path = event["path"]
        if http_method == "POST" and path == "/subscribe":
            return subscribe(event, _)
        elif http_method == "GET" and path == "/unsubscribe":
            return unsubscribe(event, _)
        elif http_method == "GET" and path == "/verify":
            return verify(event, _)
        else:
            logger.info(event)
            return {
                "statusCode": 405,
                "headers": headers,
                "body": json.dumps({"message": "Method not allowed"}),
            }
    except Exception as e:
        logger.error(e)
        logger.info(event)
        return {
            "statusCode": 500,
            "headers": headers,
            "body": json.dumps({"message": "Internal server error"}),
        }
