from pathlib import Path

from app.bot import create_application
from app.config import Settings
from app.dryrun_bot import create_dryrun_application
from app.service import configured_service


if __name__ == "__main__":
    settings = Settings.from_env()
    if settings.app_mode == "dryrun":
        application = create_dryrun_application(settings, Path(settings.dryrun_storage_dir))
    elif settings.app_mode == "production":
        application = create_application(settings, configured_service(settings))
    else:
        raise RuntimeError("APP_MODE must be either 'dryrun' or 'production'.")
    application.run_polling(allowed_updates=["message"])
