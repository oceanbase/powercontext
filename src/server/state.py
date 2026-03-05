"""
Server state management
"""

from datetime import datetime, timezone

# Track server start time
SERVER_START_TIME = datetime.now(timezone.utc)
