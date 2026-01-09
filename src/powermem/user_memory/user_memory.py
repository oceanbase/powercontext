"""
User Memory module for managing user profiles and events

This module provides high-level interface for creating and maintaining user profiles
and events extracted from conversations.
"""

import logging
from typing import Any, Dict, Optional, List

from .storage.factory import UserProfileStoreFactory
from ..core.memory import Memory
from ..prompts.user_profile_prompts import (
    get_user_profile_extraction_prompt,
    get_user_profile_topics_extraction_prompt,
)
from ..utils.utils import remove_code_blocks, parse_json_from_text, parse_conversation_text

logger = logging.getLogger(__name__)


class UserMemory:
    """
    High-level manager for creating and maintaining user profiles and events.
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any] | Any] = None,
        storage_type: Optional[str] = None,
        llm_provider: Optional[str] = None,
        embedding_provider: Optional[str] = None,
        agent_id: Optional[str] = None,
    ):
        """
        Initializes the UserMemory layer.

        Args:
            ... see Memory.__init__() for more details
        """
        # Initialize Memory instance internally
        self.memory = Memory(
            config=config,
            storage_type=storage_type,
            llm_provider=llm_provider,
            embedding_provider=embedding_provider,
            agent_id=agent_id,
        )
        
        # Initialize UserProfileStore using factory based on storage_type
        provider = self.memory.storage_type.lower()
        vector_store = self.memory.storage.vector_store
        
        # Build config for UserProfileStore based on provider
        if provider == "sqlite":
            # SQLite uses database_path
            profile_store_config = {
                "table_name": "user_profiles",
                "database_path": getattr(vector_store, 'db_path', ":memory:"),
            }
        else:
            # OceanBase and other providers use connection_args
            if hasattr(vector_store, 'connection_args'):
                connection_args = vector_store.connection_args
            else:
                # Fallback to default connection args
                connection_args = {}
            
            profile_store_config = {
                "table_name": "user_profiles",
                "connection_args": connection_args,
                "host": connection_args.get("host"),
                "port": connection_args.get("port"),
                "user": connection_args.get("user"),
                "password": connection_args.get("password"),
                "db_name": connection_args.get("db_name"),
            }
        
        # Use factory to create UserProfileStore based on storage_type
        self.profile_store = UserProfileStoreFactory.create(provider, profile_store_config)
        
        logger.info("UserMemory initialized")

    def add(
        self,
        messages,
        user_id: str,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        filters: Optional[Dict[str, Any]] = None,
        scope: Optional[str] = None,
        memory_type: Optional[str] = None,
        prompt: Optional[str] = None,
        infer: bool = True,
        profile_type: str = "content",
        custom_topics: Optional[str] = None,
        strict_mode: bool = False,
    ) -> Dict[str, Any]:
        """
        Add messages and extract user profile information.

        This method executes two steps:
        1. Store messages event (calls memory.add())
        2. Extract profile information (uses LLM to extract user profile from messages)

        Args:
            messages: Conversation messages (str, dict, or list[dict])
            user_id: User identifier
            agent_id: Optional agent identifier
            run_id: Optional run identifier
            metadata: Optional metadata
            filters: Optional filters
            scope: Optional scope
            memory_type: Optional memory type
            prompt: Optional prompt
            infer: Whether to enable intelligent memory processing
            profile_type: Type of profile extraction, either "content" (non-structured) or "topics" (structured). Default: "content"
            custom_topics: Optional custom topics JSON string for structured extraction. Only used when profile_type="topics".
                Format should be a JSON string:
                {
                    "main_topic": {
                        "sub_topic1": "description1",
                        "sub_topic2": "description2"
                    }
                }
                - All keys must be in snake_case (lowercase, underscores, no spaces)
                - Descriptions are for reference only and should NOT be used as keys in the output
            strict_mode: If True, only output topics from the provided list. Only used when profile_type="topics". Default: False

        Returns:
            Dict[str, Any]: A dictionary containing the add operation results with the following structure:
                ... see memory.add() for more details
                - "profile_extracted" (bool): Whether profile information was extracted
                - "profile_content" (str, optional): Profile content text (when profile_type="content")
                - "topics" (dict, optional): Structured topics dictionary (when profile_type="topics")
        """
        try:
             # Step 1: Store messages event
            logger.info(f"Step 1: Storing messages event for user_id: {user_id}")
            memory_result = self.memory.add(
                messages=messages,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                metadata=metadata,
                filters=filters,
                scope=scope,
                memory_type=memory_type,
                prompt=prompt,
                infer=infer,
            )
            
            # Step 2: Extract profile information
            logger.info(f"Step 2: Extracting profile information for user_id: {user_id}, profile_type: {profile_type}")

            if profile_type == "topics":
                # Extract structured topics
                extracted_data = self._extract_topics(
                    messages=messages,
                    user_id=user_id,
                    custom_topics=custom_topics,
                    strict_mode=strict_mode,
                )
                result_key = "topics"
            else:
                # Extract non-structured profile content (default behavior)
                extracted_data = self._extract_profile(
                    messages=messages,
                    user_id=user_id,
                )
                result_key = "profile_content"

            # Save profile and build result (common logic for both types)
            return self._save_profile_and_build_result(
                memory_result=memory_result,
                extracted_data=extracted_data,
                result_key=result_key,
                user_id=user_id,
            )
        except Exception as e:
            logger.error(f"Error adding messages: {e}")
            raise

    def _save_profile_and_build_result(
        self,
        memory_result: Dict[str, Any],
        extracted_data: Any,
        result_key: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """
        Save extracted profile data and build result dictionary.

        Args:
            memory_result: Result from memory.add() operation
            extracted_data: Extracted profile data (topics dict or profile_content str)
            result_key: Key to use in result dict ("topics" or "profile_content")
            user_id: User identifier

        Returns:
            Combined result dictionary with profile extraction results
        """
        if extracted_data:
            # Prepare save_profile arguments
            save_kwargs = {
                "user_id": user_id,
            }
            if result_key == "topics":
                save_kwargs["topics"] = extracted_data
            else:
                save_kwargs["profile_content"] = extracted_data

            # Save profile to UserProfileStore
            profile_id = self.profile_store.save_profile(**save_kwargs)
            logger.info(f"Profile {result_key} saved for user_id: {user_id}, profile_id: {profile_id}")
        else:
            logger.debug(f"No profile {result_key} extracted for user_id: {user_id}")

        # Build and return combined result
        result = memory_result.copy()
        result["profile_extracted"] = bool(extracted_data)
        if extracted_data:
            result[result_key] = extracted_data

        return result


    def _get_existing_profile_data(
        self,
        user_id: str,
        data_key: str,
    ) -> Optional[Any]:
        """
        Get existing profile data (profile_content or topics) from storage.

        Args:
            user_id: User identifier
            data_key: Key to retrieve ("profile_content" or "topics")

        Returns:
            Existing profile data or None if not found
        """
        try:
            profile = self.profile_store.get_profile_by_user_id(user_id)
            if profile and profile.get(data_key):
                data = profile[data_key]
                logger.debug(f"Found existing {data_key} for user_id: {user_id}, will update based on new conversation")
                return data
        except Exception as e:
            logger.warning(f"Error retrieving existing {data_key}: {e}, will extract new {data_key}")
        return None

    def _call_llm_for_extraction(
        self,
        system_prompt: str,
        user_message: str,
    ) -> str:
        """
        Call LLM to extract profile information.

        Args:
            system_prompt: System prompt for LLM
            user_message: User message for LLM

        Returns:
            LLM response text
        """
        response = self.memory.llm.generate_response(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        return remove_code_blocks(response).strip()

    def _extract_profile(
        self,
        messages: Any,
        user_id: str,
    ) -> str:
        """
        Extract user profile information from conversation using LLM.
        First retrieves existing profile if available, then asks LLM to update it based on new conversation.

        Args:
            messages: Conversation messages (str, dict, or list[dict])
            user_id: User identifier

        Returns:
            Extracted profile content as text string, or empty string if no profile found
        """
        # Parse conversation into text format
        conversation_text = parse_conversation_text(messages)
        if not conversation_text or not conversation_text.strip():
            logger.debug("Empty conversation, skipping profile extraction")
            return ""
        
        # Get existing profile if available
        existing_profile = self._get_existing_profile_data(
            user_id=user_id,
            data_key="profile_content",
        )
        
        # Generate system prompt and user message
        system_prompt, user_message = get_user_profile_extraction_prompt(
            conversation_text,
            existing_profile=existing_profile
        )

        # Call LLM to extract profile
        try:
            profile_content = self._call_llm_for_extraction(system_prompt, user_message)

            # Return empty string if response is empty or indicates no profile
            if not profile_content or profile_content.lower() in ["", "none", "no profile information", "no relevant information"]:
                return ""
            
            return profile_content
            
        except Exception as e:
            logger.error(f"Error extracting profile: {e}")
            raise

    def _extract_topics(
        self,
        messages: Any,
        user_id: str,
        custom_topics: Optional[str] = None,
        strict_mode: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Extract structured user profile topics from conversation using LLM.
        First retrieves existing topics if available, then asks LLM to update them based on new conversation.

        Args:
            messages: Conversation messages (str, dict, or list[dict])
            user_id: User identifier
            custom_topics: Optional custom topics JSON string. Format: {"main_topic": {"sub_topic": "description", ...}}
            strict_mode: If True, only output topics from the provided list

        Returns:
            Extracted topics as dictionary, or None if no topics found
        """
        # Parse conversation into text format
        conversation_text = parse_conversation_text(messages)
        if not conversation_text or not conversation_text.strip():
            logger.debug("Empty conversation, skipping topic extraction")
            return None

        # Get existing topics if available
        existing_topics = self._get_existing_profile_data(
            user_id=user_id,
            data_key="topics",
        )

        # Generate system prompt and user message
        system_prompt, user_message = get_user_profile_topics_extraction_prompt(
            conversation_text,
            existing_topics=existing_topics,
            custom_topics=custom_topics,
            strict_mode=strict_mode,
        )

        # Call LLM to extract topics
        try:
            topics_text = self._call_llm_for_extraction(system_prompt, user_message)

            # Return None if response is empty or indicates no topics
            if not topics_text or topics_text.lower() in ["", "none", "no profile information", "no relevant information", "{}"]:
                return None

            topics = parse_json_from_text(topics_text, expected_type=dict)
            if topics is None:
                raise ValueError(f"Invalid JSON format in topics response: {topics_text}")
            
            # Convert numeric values to strings recursively
            def convert_numbers_to_strings(obj):
                """Recursively convert numeric values to strings in dict/list structures."""
                if isinstance(obj, dict):
                    return {k: convert_numbers_to_strings(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [convert_numbers_to_strings(item) for item in obj]
                elif isinstance(obj, (int, float)):
                    return str(obj)
                else:
                    return obj
            
            topics = convert_numbers_to_strings(topics)
            return topics

        except Exception as e:
            logger.error(f"Error extracting topics: {e}")
            raise

    def search(
        self,
        query: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 30,
        threshold: Optional[float] = None,
        add_profile: bool = False,
    ) -> Dict[str, Any]:
        """
        Search for memories, optionally including user profile information.

        Args:
            ... see memory.search() for more details
            - add_profile: If True, include user profile content in results

        Returns:
            ... see memory.search() for more details
            - "profile_content" (str, optional): Profile content text if add_profile is True and user_id is provided
        """

        # Call memory.search()
        search_result = self.memory.search(
            query=query,
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            filters=filters,
            limit=limit,
            threshold=threshold,
        )
        
        # Add profile if requested and user_id is provided
        if add_profile and user_id:
            profile = self.profile_store.get_profile_by_user_id(user_id)
            if profile:
                if profile.get("profile_content"):
                    search_result["profile_content"] = profile["profile_content"]
                if profile.get("topics"):
                    search_result["topics"] = profile["topics"]
        
        return search_result

    def get(
        self,
        memory_id: int,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get a specific memory by ID.
        
        See memory.get() for details.
        """
        return self.memory.get(memory_id, user_id, agent_id)

    def update(
        self,
        memory_id: int,
        content: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Update an existing memory.
        
        See memory.update() for details.
        """
        return self.memory.update(memory_id, content, user_id, agent_id, metadata)

    def delete(
        self,
        memory_id: int,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        delete_profile: bool = False,
    ) -> bool:
        """
        Delete a memory.
        
        Args:
            memory_id: Memory ID to delete
            user_id: Optional user identifier
            agent_id: Optional agent identifier
            delete_profile: If True, also delete the corresponding user profile
        
        Returns:
            True if deleted successfully, False otherwise
        """
        result = self.memory.delete(memory_id, user_id, agent_id)
        
        # Delete profile if requested and user_id is provided
        if delete_profile and user_id:
            try:
                profile = self.profile_store.get_profile_by_user_id(user_id)
                if profile and profile.get("id"):
                    self.profile_store.delete_profile(profile["id"])
                    logger.info(f"Deleted profile for user_id: {user_id}, agent_id: {agent_id}")
            except Exception as e:
                logger.warning(f"Failed to delete profile for user_id: {user_id}, agent_id: {agent_id}: {e}")
        
        return result

    def delete_all(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        delete_profile: bool = False,
    ) -> bool:
        """
        Delete all memories for given identifiers.
        
        Args:
            user_id: Optional user identifier
            agent_id: Optional agent identifier
            run_id: Optional run identifier
            delete_profile: If True, also delete the corresponding user profile
        
        Returns:
            True if deleted successfully, False otherwise
        """
        result = self.memory.delete_all(user_id, agent_id, run_id)
        
        # Delete profile if requested and user_id is provided
        if delete_profile and user_id:
            try:
                profile = self.profile_store.get_profile_by_user_id(user_id)
                if profile and profile.get("id"):
                    self.profile_store.delete_profile(profile["id"])
                    logger.info(f"Deleted profile for user_id: {user_id}")
            except Exception as e:
                logger.warning(f"Failed to delete profile for user_id: {user_id}: {e}")

        return result

    def get_all(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Get all memories with optional filtering.
        
        See memory.get_all() for details.
        """
        return self.memory.get_all(user_id, agent_id, run_id, limit, offset, filters)

    def reset(self):
        """
        Reset the memory store.
        
        See memory.reset() for details.
        """
        return self.memory.reset()

    def profile(
        self,
        user_id: str,
    ) -> Dict[str, Any]:
        """
        Get user profile information.

        Args:
            user_id: User identifier

        Returns:
            Profile dictionary with the following keys:
            - "id" (int): Profile ID
            - "user_id" (str): User identifier
            - "profile_content" (str): Profile content text
            - "topics" (dict): Structured topics dictionary (filtered if main_topic or sub_topic parameter is provided)
            - "created_at" (str): Creation timestamp in ISO format
            - "updated_at" (str): Last update timestamp in ISO format
            or empty dict if not found
        """

        return self.profile_store.get_profile_by_user_id(user_id)

    def profile_list(
        self,
        user_id: Optional[str] = None,
        main_topic: Optional[List[str]] = None,
        sub_topic: Optional[List[str]] = None,
        topic_value: Optional[List[str]] = None,
        limit: Optional[int] = 100,
        offset: Optional[int] = 0,
    ) -> List[Dict[str, Any]]:
        """
        Get user profile information.

        Args:
            user_id: User identifier
            main_topic: Optional list of main topic names to filter
            sub_topic: Optional list of sub topic paths to filter. Each path should be in the format
                   "main_topic.sub_topic", e.g., ["basic_information.user_name"].
                   If provided, only returns profiles that contain the specified main topics or sub topics.
            topic_value: Optional list of topic values to filter by exact match.
            limit: Optional limit on the number of profiles to return (default: 100)
            offset: Optional offset for pagination (default: 0)

        Returns:
            List of profile dictionaries, each with the following keys:
            - "id" (int): Profile ID
            - "user_id" (str): User identifier
            - "profile_content" (str): Profile content text
            - "topics" (dict): Structured topics dictionary (filtered if main_topic or sub_topic parameter is provided)
            - "created_at" (str): Creation timestamp in ISO format
            - "updated_at" (str): Last update timestamp in ISO format
            Returns empty list if no profiles found
        """

        return self.profile_store.get_profile(user_id, main_topic, sub_topic, topic_value, limit, offset)


    def delete_profile(
        self,
        user_id: str,
    ) -> bool:
        """
        Delete user profile by user_id.

        Args:
            user_id: User identifier (required)

        Returns:
            True if profile was deleted successfully, False if profile not found
        """
        try:
            profile = self.profile_store.get_profile_by_user_id(user_id)

            if profile and profile.get("id"):
                # Delete profile using profile_id
                result = self.profile_store.delete_profile(profile["id"])
                if result:
                    logger.info(f"Deleted profile for user_id: {user_id}")
                return result
            else:
                logger.debug(f"Profile not found for user_id: {user_id}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to delete profile for user_id: {user_id}: {e}")
            raise

