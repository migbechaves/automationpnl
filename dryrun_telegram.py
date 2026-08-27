from pathlib import Path

from app.config import Settings
from app.dryrun_bot import create_dryrun_application


if __name__ == "__main__":
    settings = Settings.from_env()
    storage_root = Path(settings.dryrun_storage_dir)
    application = create_dryrun_application(settings, storage_root)
    application.run_polling(allowed_updates=["message"])
