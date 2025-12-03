"""
Utility functions and classes

This module provides utility functions and helper classes.
"""

import os
import hashlib
import json
import logging
import re
import time
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# Try to import zoneinfo (Python 3.9+)
try:
    from zoneinfo import ZoneInfo
    _HAS_ZONEINFO = True
except ImportError:
    _HAS_ZONEINFO = False
    try:
        import pytz
        _HAS_PYTZ = True
    except ImportError:
        _HAS_PYTZ = False

# Cache for timezone to avoid repeated lookups
_timezone_cache: Optional[Any] = None
_timezone_lock = threading.Lock()


def get_timezone() -> Any:
    """
    Get the configured timezone from environment variable.
    
    This function reads the TIMEZONE environment variable to determine the timezone
    to use for all datetime operations in powermem. The timezone is cached after first
    access for performance.
    
    Configuration:
        Set TIMEZONE in your .env file or environment variables:
        - TIMEZONE=Asia/Shanghai (for China Standard Time)
        - TIMEZONE=America/New_York (for US Eastern Time)
        - TIMEZONE=Europe/London (for UK Time)
        - TIMEZONE=UTC (default, if not specified)
        
        Common timezone names:
        - Asia/Shanghai, Asia/Tokyo, Asia/Hong_Kong
        - America/New_York, America/Los_Angeles, America/Chicago
        - Europe/London, Europe/Paris, Europe/Berlin
        - UTC (Coordinated Universal Time)
    
    Returns:
        Timezone object (ZoneInfo or pytz timezone) for the configured timezone, 
        or UTC if not configured or invalid timezone specified
        
    Note:
        The timezone is cached globally. To reset the cache (e.g., after changing
        the TIMEZONE environment variable), call reset_timezone_cache().
    """
    global _timezone_cache
    
    if _timezone_cache is not None:
        return _timezone_cache
    
    with _timezone_lock:
        if _timezone_cache is not None:
            return _timezone_cache
        
        # Try to get timezone from environment variable
        timezone_str = os.getenv('TIMEZONE', 'UTC')
        
        try:
            if _HAS_ZONEINFO:
                _timezone_cache = ZoneInfo(timezone_str)
            elif _HAS_PYTZ:
                _timezone_cache = pytz.timezone(timezone_str)
            else:
                logger.warning("No timezone library available, using UTC")
                _timezone_cache = timezone.utc
        except Exception as e:
            logger.warning(f"Invalid timezone '{timezone_str}', falling back to UTC: {e}")
            try:
                if _HAS_ZONEINFO:
                    _timezone_cache = ZoneInfo('UTC')
                elif _HAS_PYTZ:
                    _timezone_cache = pytz.UTC
                else:
                    _timezone_cache = timezone.utc
            except Exception:
                _timezone_cache = timezone.utc
        
        return _timezone_cache


def get_current_datetime() -> datetime:
    """
    Get current datetime in the configured timezone.
    
    This function is used throughout powermem to get the current time in the
    configured timezone. It replaces datetime.utcnow() to support timezone
    configuration via the TIMEZONE environment variable.
    
    This function respects the TIMEZONE environment variable set in .env file.
    If TIMEZONE is not set, it defaults to UTC.
    
    Returns:
        datetime object in the configured timezone (timezone-aware)
        
    Example:
        # In .env file:
        # TIMEZONE=Asia/Shanghai
        
        from powermem.utils.utils import get_current_datetime
        now = get_current_datetime()  # Returns datetime in Asia/Shanghai timezone
        
        # The returned datetime is timezone-aware:
        # datetime.datetime(2025, 1, 15, 14, 30, 0, tzinfo=zoneinfo.ZoneInfo(key='Asia/Shanghai'))
        
    Note:
        All timestamps in powermem (created_at, updated_at, etc.) are generated
        using this function to ensure consistency with the configured timezone.
    """
    tz = get_timezone()
    # datetime.now() works with both ZoneInfo and pytz timezones
    return datetime.now(tz)


def reset_timezone_cache():
    """
    Reset the timezone cache. Useful for testing or when timezone changes.
    """
    global _timezone_cache
    with _timezone_lock:
        _timezone_cache = None


def generate_memory_id(content: str, user_id: Optional[str] = None) -> str:
    """
    Generate a unique memory ID based on content and user.
    
    Args:
        content: Memory content
        user_id: User ID
        
    Returns:
        Unique memory ID
    """
    data = f"{content}:{user_id}:{get_current_datetime().isoformat()}"
    return hashlib.md5(data.encode()).hexdigest()


def validate_memory_data(data: Dict[str, Any]) -> bool:
    """
    Validate memory data structure.
    
    Args:
        data: Memory data to validate
        
    Returns:
        True if valid, False otherwise
    """
    required_fields = ["content"]
    
    for field in required_fields:
        if field not in data:
            logger.error(f"Missing required field: {field}")
            return False
    
    if not isinstance(data["content"], str) or not data["content"].strip():
        logger.error("Content must be a non-empty string")
        return False
    
    return True


def sanitize_content(content: str) -> str:
    """
    Sanitize memory content.
    
    Args:
        content: Content to sanitize
        
    Returns:
        Sanitized content
    """
    # Remove excessive whitespace
    content = " ".join(content.split())
    
    # Remove control characters
    content = "".join(char for char in content if ord(char) >= 32 or char in "\n\t")
    
    return content.strip()


def format_memory_for_display(memory: Dict[str, Any]) -> str:
    """
    Format memory for display.
    
    Args:
        memory: Memory data
        
    Returns:
        Formatted memory string
    """
    content = memory.get("content", "")
    created_at = memory.get("created_at", "")
    metadata = memory.get("metadata", {})
    
    formatted = f"Content: {content}\n"
    if created_at:
        formatted += f"Created: {created_at}\n"
    if metadata:
        formatted += f"Metadata: {json.dumps(metadata, indent=2)}\n"
    
    return formatted


def merge_memories(memories: List[Dict[str, Any]]) -> str:
    """
    Merge multiple memories into a single string.
    
    Args:
        memories: List of memory data
        
    Returns:
        Merged memory content
    """
    if not memories:
        return ""
    
    merged_content = []
    for memory in memories:
        content = memory.get("content", "")
        if content:
            merged_content.append(content)
    
    return "\n\n".join(merged_content)


def calculate_similarity(text1: str, text2: str) -> float:
    """
    Calculate similarity between two texts.
    
    Args:
        text1: First text
        text2: Second text
        
    Returns:
        Similarity score between 0 and 1
    """
    # Simple word-based similarity
    words1 = set(text1.lower().split())
    words2 = set(text2.lower().split())
    
    if not words1 and not words2:
        return 1.0
    
    if not words1 or not words2:
        return 0.0
    
    intersection = words1.intersection(words2)
    union = words1.union(words2)
    
    return len(intersection) / len(union)


def extract_keywords(text: str, max_keywords: int = 10) -> List[str]:
    """
    Extract keywords from text.
    
    Args:
        text: Text to extract keywords from
        max_keywords: Maximum number of keywords
        
    Returns:
        List of keywords
    """
    # Simple keyword extraction
    words = text.lower().split()
    
    # Remove common stop words
    stop_words = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "is", "are", "was", "were", "be", "been", "have",
        "has", "had", "do", "does", "did", "will", "would", "could", "should"
    }
    
    keywords = [word for word in words if word not in stop_words and len(word) > 2]
    
    # Count frequency
    word_count = {}
    for word in keywords:
        word_count[word] = word_count.get(word, 0) + 1
    
    # Sort by frequency
    sorted_keywords = sorted(word_count.items(), key=lambda x: x[1], reverse=True)
    
    return [word for word, count in sorted_keywords[:max_keywords]]


def format_timestamp(timestamp: datetime) -> str:
    """
    Format timestamp for display.
    
    Args:
        timestamp: Timestamp to format
        
    Returns:
        Formatted timestamp string with timezone information
    """
    # If timestamp is timezone-naive, assume it's in the configured timezone
    if timestamp.tzinfo is None:
        tz = get_timezone()
        timestamp = timestamp.replace(tzinfo=tz)
    
    # Format with timezone name
    timezone_name = timestamp.tzinfo.tzname(timestamp) if timestamp.tzinfo else "UTC"
    return timestamp.strftime(f"%Y-%m-%d %H:%M:%S {timezone_name}")


def parse_timestamp(timestamp_str: str) -> Optional[datetime]:
    """
    Parse timestamp string.
    
    Args:
        timestamp_str: Timestamp string to parse
        
    Returns:
        Parsed datetime object or None if invalid
    """
    try:
        return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except ValueError:
        logger.error(f"Failed to parse timestamp: {timestamp_str}")
        return None

def extract_json(text):
    """
    Extracts JSON content from a string, removing enclosing triple backticks and optional 'json' tag if present.
    If no code block is found, returns the text as-is.
    """
    text = text.strip()
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        json_str = match.group(1)
    else:
        json_str = text  # assume it's raw JSON
    return json_str

def format_entities(entities):
    if not entities:
        return ""

    formatted_lines = []
    for entity in entities:
        simplified = f"{entity['source']} -- {entity['relationship']} -- {entity['destination']}"
        formatted_lines.append(simplified)

    return "\n".join(formatted_lines)

def remove_code_blocks(content: str) -> str:
    """
    Removes enclosing code block markers ```[language] and ``` from a given string.

    Remarks:
    - The function uses a regex pattern to match code blocks that may start with ``` followed by an optional language tag (letters or numbers) and end with ```.
    - If a code block is detected, it returns only the inner content, stripping out the markers.
    - If no code block markers are found, the original content is returned as-is.
    """
    pattern = r"^```[a-zA-Z0-9]*\n([\s\S]*?)\n```$"
    match = re.match(pattern, content.strip())
    return match.group(1).strip() if match else content.strip()


def get_image_description(image_obj: Any, llm: Any, vision_details: Any) -> str:
    """
    - image_obj can be a URL string, or a prebuilt multimodal message (list/dict).
    - vision_details can be "auto" or a dict; when dict we use detail = dict.get("detail", "auto").
    """
    detail = vision_details
    if isinstance(vision_details, dict):
        detail = vision_details.get("detail", "auto")
    if detail is None:
        detail = "auto"

    if isinstance(image_obj, str):
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "A user is providing an image. Provide a high level description of the image and do not include any additional text.",
                    },
                    {"type": "image_url", "image_url": {"url": image_obj, "detail": detail}},
                ],
            },
        ]
    else:
        messages = [image_obj]

    return llm.generate_response(messages=messages)


def _process_content_item(item: Dict[str, Any], role: str, llm: Any, vision_details: Any, audio_llm: Any) -> Optional[str]:
    """
    Process a single content item and return processed text content.
    
    Args:
        item: Content item dict
        role: Message role
        llm: LLM instance for image description
        vision_details: Vision details setting
        audio_llm: Audio LLM instance for transcription
        
    Returns:
        Processed text content or None if item should be skipped
    """
    if not isinstance(item, dict):
        return None
    
    item_type = item.get("type")
    
    if item_type == "text":
        text_content = item.get("text", "")
        return text_content if text_content else None
    
    elif item_type == "image_url":
        image_url = item.get("image_url", {}).get("url")
        if image_url:
            try:
                description = get_image_description(image_url, llm, vision_details)
                return description if description else None
            except Exception as e:
                raise Exception(f"Error while processing image {image_url}: {e}")
        return None
    
    elif item_type == "audio":
        if audio_llm is not None:
            audio_content = item.get("content", {})
            audio_url = audio_content.get("audio") if isinstance(audio_content, dict) else None
            if audio_url:
                try:
                    transcribed_text = audio_llm.transcribe(audio_url=audio_url)
                    return transcribed_text if transcribed_text else None
                except Exception as e:
                    logger.error(f"Error while transcribing audio {audio_url}: {e}")
                    raise Exception(f"Error while transcribing audio {audio_url}: {e}")
        else:
            logger.warning(f"Audio item found but audio_llm is not configured: {item}")
        return None
    
    else:
        logger.warning(f"Unknown content type: {item_type}")
        return None


def parse_vision_messages(messages: List[Dict[str, Any]], llm: Any = None, vision_details: Any = "auto", audio_llm: Any = None) -> List[Dict[str, Any]]:
    """

    Assumes input is already a list of message dicts with 'role' and 'content' fields.
    - Keep system messages unchanged.
    - If message.content is a list (multimodal blocks), call get_image_description and replace content with returned text.
    - If message.content is a dict with type == "image_url", call get_image_description(url, ...) and replace content with returned text.
    - If message.content contains type == "audio", use audio_llm to transcribe audio to text.
    - Otherwise keep the original message (regular text).
    - When llm is None, behave as pass-through for all messages.
    """
    returned_messages: List[Dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict) or "role" not in msg or "content" not in msg:
            continue

        if msg["role"] == "system":
            returned_messages.append(msg)
            continue

        # If LLM not provided, passthrough without image description
        if llm is None:
            returned_messages.append(msg)
            continue

        content = msg["content"]
        role = msg["role"]
        
        # Normalize content to list format for unified processing
        items_to_process = []
        if isinstance(content, list):
            items_to_process = content
        elif isinstance(content, dict):
            # Handle single dict as image_url or audio
            if content.get("type") in ("image_url", "audio"):
                items_to_process = [content]
            else:
                # Unknown dict format, passthrough
                returned_messages.append(msg)
                continue
        else:
            # Regular text or other content, passthrough
            returned_messages.append(msg)
            continue
        
        # Process each item
        for item in items_to_process:
            processed_content = _process_content_item(item, role, llm, vision_details, audio_llm)
            if processed_content:
                returned_messages.append({"role": role, "content": processed_content})

    return returned_messages


def load_config_from_env() -> Dict[str, Any]:
    """
    Load configuration from environment variables.
    
    .. deprecated:: 0.1.0
       This function is now in :mod:`mem.config_loader`.
       Please use ``from powermem import load_config_from_env`` instead.
    
    This is kept for backward compatibility.
    For the actual implementation, see :mod:`mem.config_loader`.
    
    Returns:
        Configuration dictionary built from environment variables
    """
    # Import here to avoid circular import
    from ..config_loader import load_config_from_env as _load_config_from_env
    return _load_config_from_env()


def serialize_datetime(value: Any) -> Any:
    """
    Convert datetime objects to ISO format strings for JSON serialization.
    Recursively handles dictionaries and lists.
    
    Args:
        value: Value to serialize (can be datetime, dict, list, or primitive)
    
    Returns:
        Serialized value with datetime objects converted to ISO format strings
    """
    if isinstance(value, datetime):
        return value.isoformat()
    elif isinstance(value, dict):
        return {k: serialize_datetime(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [serialize_datetime(item) for item in value]
    return value


def convert_config_object_to_dict(obj: Any) -> Any:
    """
    Recursively convert ConfigObject instances to dictionaries.

    Args:
        obj: Object to convert (can be ConfigObject, dict, list, or primitive)

    Returns:
        Converted object with all ConfigObjects replaced by dicts
    """
    if obj is None:
        return None

    # Handle ConfigObject
    if hasattr(obj, 'to_dict'):
        obj = obj.to_dict()

    # Handle dict
    if isinstance(obj, dict):
        return {key: convert_config_object_to_dict(value) for key, value in obj.items()}

    # Handle list
    if isinstance(obj, list):
        return [convert_config_object_to_dict(item) for item in obj]

    # Return primitive types as-is
    return obj


class SnowflakeIDGenerator:
    """
    Snowflake ID generator for distributed systems.
    
    Generates unique 64-bit IDs using the Snowflake algorithm:
    - 41 bits for timestamp (milliseconds since epoch)
    - 10 bits for machine ID (5 bits datacenter + 5 bits worker)
    - 12 bits for sequence number
    
    Thread-safe implementation.
    """
    
    # Snowflake parameters
    EPOCH = 1609459200000  # 2021-01-01 00:00:00 UTC in milliseconds
    TIMESTAMP_BITS = 41
    DATACENTER_BITS = 5
    WORKER_BITS = 5
    SEQUENCE_BITS = 12
    
    MAX_DATACENTER_ID = (1 << DATACENTER_BITS) - 1  # 31
    MAX_WORKER_ID = (1 << WORKER_BITS) - 1  # 31
    MAX_SEQUENCE = (1 << SEQUENCE_BITS) - 1  # 4095
    
    # Bit shifts
    TIMESTAMP_SHIFT = SEQUENCE_BITS + WORKER_BITS + DATACENTER_BITS
    DATACENTER_SHIFT = SEQUENCE_BITS + WORKER_BITS
    WORKER_SHIFT = SEQUENCE_BITS
    
    def __init__(self, datacenter_id: int = 0, worker_id: int = 0):
        """
        Initialize Snowflake ID generator.
        
        Args:
            datacenter_id: Datacenter ID (0-31)
            worker_id: Worker ID (0-31)
            
        Raises:
            ValueError: If datacenter_id or worker_id is out of range
        """
        if datacenter_id < 0 or datacenter_id > self.MAX_DATACENTER_ID:
            raise ValueError(f"Datacenter ID must be between 0 and {self.MAX_DATACENTER_ID}")
        if worker_id < 0 or worker_id > self.MAX_WORKER_ID:
            raise ValueError(f"Worker ID must be between 0 and {self.MAX_WORKER_ID}")
        
        self.datacenter_id = datacenter_id
        self.worker_id = worker_id
        self.sequence = 0
        self.last_timestamp = -1
        self._lock = threading.Lock()
    
    def _current_timestamp(self) -> int:
        """Get current timestamp in milliseconds."""
        return int(time.time() * 1000)
    
    def _wait_next_millis(self, last_timestamp: int) -> int:
        """Wait until next millisecond."""
        timestamp = self._current_timestamp()
        while timestamp <= last_timestamp:
            timestamp = self._current_timestamp()
        return timestamp
    
    def generate(self) -> int:
        """
        Generate a new Snowflake ID.
        
        Returns:
            64-bit integer ID
            
        Raises:
            RuntimeError: If clock moves backwards or sequence overflows
        """
        with self._lock:
            timestamp = self._current_timestamp()
            
            # Handle clock backwards
            if timestamp < self.last_timestamp:
                raise RuntimeError(
                    f"Clock moved backwards. Refusing to generate ID for "
                    f"{self.last_timestamp - timestamp} milliseconds"
                )
            
            # Same millisecond, increment sequence
            if timestamp == self.last_timestamp:
                self.sequence = (self.sequence + 1) & self.MAX_SEQUENCE
                # Sequence overflow, wait for next millisecond
                if self.sequence == 0:
                    timestamp = self._wait_next_millis(self.last_timestamp)
            else:
                # New millisecond, reset sequence
                self.sequence = 0
            
            self.last_timestamp = timestamp
            
            # Generate ID
            return (
                ((timestamp - self.EPOCH) << self.TIMESTAMP_SHIFT) |
                (self.datacenter_id << self.DATACENTER_SHIFT) |
                (self.worker_id << self.WORKER_SHIFT) |
                self.sequence
            )
    
    def generate_batch(self, count: int) -> List[int]:
        """
        Generate a batch of Snowflake IDs.
        
        Args:
            count: Number of IDs to generate
            
        Returns:
            List of 64-bit integer IDs
        """
        return [self.generate() for _ in range(count)]


# Global Snowflake ID generator instance
# Default to datacenter_id=0, worker_id=0
# Can be configured via environment variables if needed
_snowflake_generator: Optional[SnowflakeIDGenerator] = None
_snowflake_lock = threading.Lock()


def get_snowflake_generator() -> SnowflakeIDGenerator:
    """
    Get or create the global Snowflake ID generator instance.
    
    Returns:
        Snowflake ID generator instance
    """
    global _snowflake_generator
    if _snowflake_generator is None:
        with _snowflake_lock:
            if _snowflake_generator is None:
                # Try to get from environment variables
                datacenter_id = int(os.getenv("SNOWFLAKE_DATACENTER_ID", "0"))
                worker_id = int(os.getenv("SNOWFLAKE_WORKER_ID", "0"))
                _snowflake_generator = SnowflakeIDGenerator(
                    datacenter_id=datacenter_id,
                    worker_id=worker_id
                )
    return _snowflake_generator


def generate_snowflake_id() -> int:
    """
    Generate a new Snowflake ID using the global generator.
    
    Returns:
        64-bit integer ID
    """
    return get_snowflake_generator().generate()
