from baseball.tests.api_settings import *  # noqa: F403

# Disposable in-memory test DB; never migrate or modify the running database.
MIGRATION_MODULES = {"baseball": None, "accounts": None}
EXTERNAL_DATA_SYNC_INTERVAL_SECONDS = 600
