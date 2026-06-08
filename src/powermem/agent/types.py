from enum import Enum


class MemoryType(Enum):
    SEMANTIC = "semantic_memory"
    EPISODIC = "episodic_memory"
    PROCEDURAL = "procedural_memory"
    WORKING = "working_memory"
    SHORT_TERM = "short_term_memory"
    LONG_TERM = "long_term_memory"
    PUBLIC_SHARED = "public_shared_memory"
    PRIVATE_AGENT = "private_agent_memory"
    COLLABORATIVE = "collaborative_memory"
    GROUP_CONSENSUS = "group_consensus_memory"


class MemoryScope(Enum):
    PRIVATE = "private"
    AGENT_GROUP = "agent_group"
    USER_GROUP = "user_group"
    PUBLIC = "public"
    RESTRICTED = "restricted"


class AccessPermission(Enum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    SHARE = "share"
    ADMIN = "admin"


class PrivacyLevel(Enum):
    STANDARD = "standard"
    SENSITIVE = "sensitive"
    CONFIDENTIAL = "confidential"


class CollaborationType(Enum):
    SYNCHRONOUS = "synchronous"
    ASYNCHRONOUS = "asynchronous"


class CollaborationStatus(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    PENDING = "pending"


class CollaborationLevel(Enum):
    ISOLATED = "isolated"
    COLLABORATIVE = "collaborative"

