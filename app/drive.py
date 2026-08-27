from pathlib import Path

from .config import Settings
from .models import RecordType


DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]


class DriveStorage:
    def __init__(self, settings: Settings):
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError as error:
            raise RuntimeError("Install Google API dependencies first.") from error
        credentials = None
        if settings.google_drive_oauth_token_file.exists():
            credentials = Credentials.from_authorized_user_file(settings.google_drive_oauth_token_file, DRIVE_SCOPES)
        if not credentials or not credentials.valid or not credentials.has_scopes(DRIVE_SCOPES):
            if credentials and credentials.expired and credentials.refresh_token and credentials.has_scopes(DRIVE_SCOPES):
                credentials.refresh(Request())
            else:
                if not settings.google_drive_oauth_credentials_file.exists():
                    raise RuntimeError(
                        f"Google Drive OAuth credentials file not found: {settings.google_drive_oauth_credentials_file}"
                    )
                credentials = InstalledAppFlow.from_client_secrets_file(
                    settings.google_drive_oauth_credentials_file, DRIVE_SCOPES
                ).run_local_server(port=0)
            settings.google_drive_oauth_token_file.write_text(credentials.to_json(), encoding="utf-8")
        self.service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        self.root_id = settings.google_drive_root_folder_id
        if not self.root_id:
            raise RuntimeError("GOOGLE_DRIVE_ROOT_FOLDER_ID is not configured.")
        self.media_upload = MediaFileUpload
        self._folders: dict[tuple[str, str], str] = {}

    def _folder(self, name: str, parent_id: str) -> str:
        cache_key = (parent_id, name)
        if cache_key in self._folders:
            return self._folders[cache_key]
        query = (
            "trashed = false and mimeType = 'application/vnd.google-apps.folder' "
            f"and name = '{name.replace(chr(39), chr(92) + chr(39))}' and '{parent_id}' in parents"
        )
        matches = self.service.files().list(q=query, fields="files(id)", pageSize=1).execute().get("files", [])
        folder_id = matches[0]["id"] if matches else self.service.files().create(
            body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
            fields="id",
        ).execute()["id"]
        self._folders[cache_key] = folder_id
        return folder_id

    def upload(self, image_path: Path, timestamp, record_type, filename: str) -> str:
        type_name = "TIMEIN" if record_type == RecordType.TIME_IN else "TIMEOUT"
        type_id = self._folder(type_name, self.root_id)
        year_id = self._folder(timestamp.strftime("%Y"), type_id)
        month_id = self._folder(timestamp.strftime("%m-%B"), year_id)
        date_id = self._folder(timestamp.strftime("%Y-%m-%d"), month_id)
        uploaded = self.service.files().create(
            body={"name": filename, "parents": [date_id]},
            media_body=self.media_upload(str(image_path), mimetype="image/jpeg", resumable=True),
            fields="id,webViewLink",
        ).execute()
        return uploaded.get("webViewLink") or f"https://drive.google.com/open?id={uploaded['id']}"
