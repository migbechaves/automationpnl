import threading
from pathlib import Path

from .config import Settings
from .models import RecordType


DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


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
        self.media_upload = MediaFileUpload
        self._folders: dict[tuple[str, str], str] = {}
        # The underlying HTTP client isn't safe to call from multiple threads at once,
        # and multiple employees can now be processed concurrently (see app/bot.py),
        # so every Drive call below is serialized through this one lock. Reentrant
        # because upload() holds it while _folder() re-acquires it internally.
        self._lock = threading.RLock()
        # With drive.file scope the app can only see files/folders it created itself,
        # so unless a pre-owned folder ID is configured, find-or-create one under "My
        # Drive" by name. Because the app creates it, the app retains access to it
        # (and everything nested under it) permanently, even across token refreshes.
        self.root_id = settings.google_drive_root_folder_id or self._folder(
            settings.google_drive_root_folder_name, "root"
        )

    def _folder(self, name: str, parent_id: str) -> str:
        with self._lock:
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

    def upload(self, image_paths, timestamp, record_type, folder_label: str) -> str:
        """Upload every photo of one time-in/out submission into its own folder
        under .../TYPE/YEAR/MONTH/DATE/, and return that folder's shareable link.

        The Sheet's Image cell points at the folder rather than a single file, so
        an album (several photos in one submission) is all reachable from one
        click and a lone photo still lands somewhere tidy.
        """
        with self._lock:
            type_name = "TIMEIN" if record_type == RecordType.TIME_IN else "TIMEOUT"
            type_id = self._folder(type_name, self.root_id)
            year_id = self._folder(timestamp.strftime("%Y"), type_id)
            month_id = self._folder(timestamp.strftime("%m-%B"), year_id)
            date_id = self._folder(timestamp.strftime("%Y-%m-%d"), month_id)
            folder = self.service.files().create(
                body={
                    "name": folder_label,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [date_id],
                },
                fields="id,webViewLink",
            ).execute()
            for index, image_path in enumerate(image_paths, start=1):
                self.service.files().create(
                    body={"name": f"{folder_label}_{index}.jpg", "parents": [folder["id"]]},
                    media_body=self.media_upload(str(image_path), mimetype="image/jpeg", resumable=True),
                    fields="id",
                ).execute()
            return folder.get("webViewLink") or f"https://drive.google.com/drive/folders/{folder['id']}"
