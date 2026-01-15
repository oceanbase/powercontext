"""
PowerMem Script Package

Provides script management tools for database upgrades, migrations, and maintenance.

Usage:
    from script import ScriptManager
    from powermem import auto_config, Memory
    
    # List available scripts
    ScriptManager.list_scripts()
    
    # View script details
    ScriptManager.info('upgrade-sparse-vector')
    
    # Execute upgrade script
    config = auto_config()
    ScriptManager.run('upgrade-sparse-vector', config)
    
    # Execute migration script
    memory = Memory(config=config)
    ScriptManager.run('migrate-sparse-vector', memory, batch_size=1000, workers=3)
"""

from script.script_manager import ScriptManager

__all__ = ['ScriptManager']
