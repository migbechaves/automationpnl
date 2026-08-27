import os
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/drive"]

oauth_credentials_file = "oauth_credentials.json"
token_file = "token.json"

folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID") or os.getenv("GOOGLE_DRIVE_ROOT_FOLDER_ID")
if not folder_id:
    raise ValueError("GOOGLE_DRIVE_FOLDER_ID is missing from .env")

creds = None

# Reuse saved login from previous runs, if it exists
if os.path.exists(token_file):
    creds = Credentials.from_authorized_user_file(token_file, SCOPES)

# If no valid saved login, prompt for one (only needed the first time)
if not creds or not creds.valid:
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file(oauth_credentials_file, SCOPES)
        creds = flow.run_local_server(port=0)

    with open(token_file, "w") as token:
        token.write(creds.to_json())

drive_service = build("drive", "v3", credentials=creds)

file_path = "test_image.jpg"
if not os.path.exists(file_path):
    raise FileNotFoundError(f"{file_path} was not found.")

file_metadata = {
    "name": "TEST_UPLOAD.jpg",
    "parents": [folder_id]
}

media = MediaFileUpload(file_path, mimetype="image/jpeg", resumable=True)

uploaded_file = drive_service.files().create(
    body=file_metadata,
    media_body=media,
    fields="id, name, webViewLink"
).execute()

print()
print("================================")
print("GOOGLE DRIVE TEST SUCCESSFUL!")
print("================================")
print()
print("File Name:", uploaded_file.get("name"))
print("File ID:", uploaded_file.get("id"))
print("Drive Link:", uploaded_file.get("webViewLink"))