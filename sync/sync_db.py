"""Database logic for syncing the firehose."""

from typing import Optional

from lib.db.models.raw import FirehoseSubscriptionStateCursorModel


def load_cursor_state_from_db(service_name: str) -> Optional[FirehoseSubscriptionStateCursorModel]:
    return None
