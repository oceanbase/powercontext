"""
Configuration factory for creating and managing agent memory configurations.

This module provides factory classes for creating and managing different
types of configurations for the agent memory system.
"""

from typing import Dict, Any
import logging

from powermem.configs import (
    MemoryConfig, 
    AgentMemoryConfig, 
    MultiAgentMemoryConfig,
    MultiUserConfig,
    HybridConfig
)

logger = logging.getLogger(__name__)


class ConfigFactory:
    """
    Factory class for creating and managing agent memory configurations.
    
    This factory provides a unified interface for creating different types
    of configurations for the agent memory system.
    """
    
    @classmethod
    def create_agent_memory_config(
        cls,
        mode: str = "multi_agent",
        enabled: bool = True,
        **kwargs
    ) -> AgentMemoryConfig:
        """
        Create an agent memory configuration.
        
        Args:
            mode: Management mode (multi_agent, multi_user, hybrid)
            enabled: Whether to enable agent memory management
            **kwargs: Additional configuration parameters
            
        Returns:
            AgentMemoryConfig instance
        """
        config = AgentMemoryConfig(
            mode=mode,
            enabled=enabled
        )
        
        # Apply additional parameters
        for key, value in kwargs.items():
            if hasattr(config, key):
                setattr(config, key, value)
        
        logger.info(f"Created agent memory config with mode: {mode}")
        return config
    
    @classmethod
    def create_multi_agent_config(
        cls,
        enabled: bool = True,
        default_scope: str = "private",
        enable_collaboration: bool = True,
        **kwargs
    ) -> MultiAgentMemoryConfig:
        """
        Create a multi-agent memory configuration.
        
        Args:
            enabled: Whether to enable multi-agent memory management
            default_scope: Default memory scope
            enable_collaboration: Whether to enable collaboration
            **kwargs: Additional configuration parameters
            
        Returns:
            MultiAgentMemoryConfig instance
        """
        from powermem.configs import MemoryScope, CollaborationLevel
        
        config = MultiAgentMemoryConfig(
            enabled=enabled,
            default_scope=MemoryScope(default_scope),
            default_collaboration_level=(
                CollaborationLevel.COLLABORATIVE if enable_collaboration 
                else CollaborationLevel.ISOLATED
            )
        )
        
        # Apply additional parameters
        for key, value in kwargs.items():
            if hasattr(config, key):
                setattr(config, key, value)
        
        logger.info("Created multi-agent memory config")
        return config
    
    @classmethod
    def create_multi_user_config(
        cls,
        user_isolation: bool = True,
        cross_user_sharing: bool = False,
        **kwargs
    ) -> MultiUserConfig:
        """
        Create a multi-user memory configuration.
        
        Args:
            user_isolation: Whether to enable user isolation
            cross_user_sharing: Whether to allow cross-user sharing
            **kwargs: Additional configuration parameters
            
        Returns:
            MultiUserConfig instance
        """
        config = MultiUserConfig(
            user_isolation=user_isolation,
            cross_user_sharing=cross_user_sharing
        )
        
        # Apply additional parameters
        for key, value in kwargs.items():
            if hasattr(config, key):
                setattr(config, key, value)
        
        logger.info("Created multi-user memory config")
        return config
    
    @classmethod
    def create_hybrid_config(
        cls,
        enabled: bool = True,
        primary_mode: str = "multi_user",
        fallback_mode: str = "multi_agent",
        auto_switch_threshold: float = 0.8,
        **kwargs
    ) -> HybridConfig:
        """
        Create a hybrid memory configuration.
        
        Args:
            enabled: Whether to enable hybrid mode
            primary_mode: Primary management mode
            fallback_mode: Fallback management mode
            auto_switch_threshold: Threshold for automatic mode switching
            **kwargs: Additional configuration parameters
            
        Returns:
            HybridConfig instance
        """
        config = HybridConfig(
            enabled=enabled,
            primary_mode=primary_mode,
            fallback_mode=fallback_mode,
            auto_switch_threshold=auto_switch_threshold
        )
        
        # Apply additional parameters
        for key, value in kwargs.items():
            if hasattr(config, key):
                setattr(config, key, value)
        
        logger.info("Created hybrid memory config")
        return config
    
    @classmethod
    def create_full_memory_config(
        cls,
        agent_memory_mode: str = "multi_agent",
        agent_memory_enabled: bool = True,
        **kwargs
    ) -> MemoryConfig:
        """
        Create a complete memory configuration with agent memory settings.
        
        Args:
            agent_memory_mode: Agent memory management mode
            agent_memory_enabled: Whether to enable agent memory management
            **kwargs: Additional configuration parameters
            
        Returns:
            MemoryConfig instance with agent memory configuration
        """
        # Create base memory config
        config = MemoryConfig()
        
        # Create agent memory config
        agent_config = cls.create_agent_memory_config(
            mode=agent_memory_mode,
            enabled=agent_memory_enabled
        )
        
        # Set agent memory config
        config.agent_memory = agent_config
        
        # Apply additional parameters
        for key, value in kwargs.items():
            if hasattr(config, key):
                setattr(config, key, value)
        
        logger.info(f"Created full memory config with agent memory mode: {agent_memory_mode}")
        return config
    
    @classmethod
    def update_config_from_dict(
        cls,
        config: MemoryConfig,
        config_dict: Dict[str, Any]
    ) -> MemoryConfig:
        """
        Update a memory configuration from a dictionary.
        
        Args:
            config: Memory configuration to update
            config_dict: Dictionary containing configuration updates
            
        Returns:
            Updated MemoryConfig instance
        """
        for key, value in config_dict.items():
            if hasattr(config, key):
                setattr(config, key, value)
            elif hasattr(config, 'agent_memory') and hasattr(config.agent_memory, key):
                setattr(config.agent_memory, key, value)
        
        logger.info("Updated memory config from dictionary")
        return config
    
    @classmethod
    def validate_config(cls, config: MemoryConfig) -> Dict[str, Any]:
        """
        Validate a memory configuration.
        
        Args:
            config: Memory configuration to validate
            
        Returns:
            Dictionary containing validation results
        """
        validation_results = {
            "valid": True,
            "errors": [],
            "warnings": []
        }
        
        try:
            # Validate agent memory config if present
            if hasattr(config, 'agent_memory') and config.agent_memory.enabled:
                agent_config = config.agent_memory
                
                # Check mode validity
                valid_modes = ["multi_agent", "multi_user", "hybrid"]
                if agent_config.mode not in valid_modes:
                    validation_results["errors"].append(
                        f"Invalid agent memory mode: {agent_config.mode}"
                    )
                    validation_results["valid"] = False
                
                # Check hybrid config if mode is hybrid
                if agent_config.mode == "hybrid":
                    if not agent_config.hybrid_config.enabled:
                        validation_results["warnings"].append(
                            "Hybrid mode is selected but hybrid config is not enabled"
                        )
            
            logger.info("Memory config validation completed")
            
        except Exception as e:
            validation_results["errors"].append(f"Validation error: {str(e)}")
            validation_results["valid"] = False
            logger.error(f"Memory config validation failed: {e}", exc_info=True)
        
        return validation_results
    
    @classmethod
    def get_config_summary(cls, config: MemoryConfig) -> Dict[str, Any]:
        """
        Get a summary of a memory configuration.
        
        Args:
            config: Memory configuration to summarize
            
        Returns:
            Dictionary containing configuration summary
        """
        summary = {
            "base_config": {
                "version": config.version,
                "has_vector_store": config.vector_store is not None,
                "has_llm": config.llm is not None,
                "has_embedder": config.embedder is not None,
            }
        }
        
        # Add agent memory config summary if present
        if hasattr(config, 'agent_memory'):
            agent_config = config.agent_memory
            summary["agent_memory"] = {
                "enabled": agent_config.enabled,
                "mode": agent_config.mode,
                "has_multi_agent_config": agent_config.multi_agent_config is not None,
                "has_multi_user_config": agent_config.multi_user_config is not None,
                "has_hybrid_config": agent_config.hybrid_config is not None,
            }
        
        return summary
