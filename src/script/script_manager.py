"""
PowerMem Script Manager

Provides Python API to manage and execute various maintenance and upgrade scripts.

Usage example:
    from powermem import auto_config, Memory
    from script import ScriptManager
    
    # List available scripts
    ScriptManager.list_scripts()
    
    # View script details
    ScriptManager.info('migrate-sparse-vector')
    
    # Execute upgrade script
    config = auto_config()
    ScriptManager.run('upgrade-sparse-vector', config)
    
    # Execute migration script
    memory = Memory(config=config)
    ScriptManager.run('migrate-sparse-vector', memory, batch_size=1000)
"""

import importlib
import inspect
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ScriptManager:
    """Script Manager - Used to execute maintenance and upgrade scripts"""
    
    @classmethod
    def _get_config_path(cls) -> Path:
        """Get the path to scripts_config.json relative to this module"""
        return Path(__file__).parent / "scripts_config.json"
    
    @classmethod
    def _load_config(cls) -> Dict[str, Any]:
        """Load script configuration"""
        config_path = cls._get_config_path()
        
        if not config_path.exists():
            raise FileNotFoundError(
                f"Configuration file not found: {config_path}\n"
                f"Please ensure scripts_config.json exists in the script package"
            )
        
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            return config
        except Exception as e:
            raise RuntimeError(f"Failed to load configuration file: {e}")
    
    @classmethod
    def _get_script_info(cls, script_name: str, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Get script information
        
        Args:
            script_name: Script name
            config: Optional config dict, if not provided will load from default path
            
        Returns:
            Script configuration information
            
        Raises:
            ValueError: If script does not exist
        """
        if config is None:
            config = cls._load_config()
        scripts = config.get('scripts', {})
        if script_name not in scripts:
            available = ', '.join(scripts.keys())
            raise ValueError(
                f"Unknown script: {script_name}\n"
                f"Available scripts: {available}\n"
                f"Use ScriptManager.list_scripts() to view details"
            )
        return scripts[script_name]
    
    @classmethod
    def list_scripts(cls, category: Optional[str] = None, verbose: bool = True) -> Dict[str, Dict]:
        """
        List all available scripts
        
        Args:
            category: Optional, only list scripts in the specified category
            verbose: Whether to print to console (default True)
            
        Returns:
            Script dictionary, key is script name, value is script info
        """
        config = cls._load_config()
        scripts = config.get('scripts', {})
        categories = config.get('categories', {})
        
        # Filter by category
        filtered_scripts = {}
        for script_name, script_info in scripts.items():
            if category and script_info.get('category') != category:
                continue
            filtered_scripts[script_name] = script_info
        
        if verbose:
            cls._print_scripts(filtered_scripts, categories)
        
        return filtered_scripts
    
    @classmethod
    def _print_scripts(cls, scripts: Dict[str, Dict], categories: Dict[str, str]) -> None:
        """Print script list"""
        logger.info("\n" + "=" * 70)
        logger.info("PowerMem Available Scripts")
        logger.info("=" * 70)
        
        # Organize scripts by category
        scripts_by_category: Dict[str, list] = {}
        for script_name, script_info in scripts.items():
            script_category = script_info.get('category', 'uncategorized')
            if script_category not in scripts_by_category:
                scripts_by_category[script_category] = []
            scripts_by_category[script_category].append((script_name, script_info))
        
        # Print scripts for each category
        for cat, script_list in sorted(scripts_by_category.items()):
            category_desc = categories.get(cat, cat)
            logger.info(f"\n【{category_desc}】")
            logger.info("-" * 70)
            
            for script_name, script_info in sorted(script_list):
                desc = script_info.get('description', 'No description')
                destructive = script_info.get('destructive', False)
                warning = " ⚠️  Destructive Operation" if destructive else ""
                
                # Try to get the type hint of the first parameter
                param_hint = cls._get_first_param_hint(script_name, script_info)
                param_info = f" (requires: {param_hint})" if param_hint else ""
                
                logger.info(f"  • {script_name}{warning}")
                logger.info(f"    {desc}{param_info}")
        
        logger.info("\n" + "=" * 70)
        logger.info("Usage: ScriptManager.run('script_name', param)")
        logger.info("Tip: Use ScriptManager.info('script_name') to view detailed parameters")
        logger.info("=" * 70 + "\n")
    
    @classmethod
    def _get_first_param_hint(cls, script_name: str, script_info: Dict[str, Any]) -> Optional[str]:
        """Get the type hint of the first parameter of the script"""
        try:
            module = importlib.import_module(script_info['module'])
            func = getattr(module, script_info['function'])
            sig = inspect.signature(func)
            
            # Get the first parameter
            params = list(sig.parameters.values())
            if params:
                first_param = params[0]
                if first_param.annotation != inspect.Parameter.empty:
                    annotation = first_param.annotation
                    # Handle string type annotations (e.g., 'Memory')
                    if isinstance(annotation, str):
                        return annotation
                    # Handle type objects
                    return getattr(annotation, '__name__', str(annotation))
        except Exception:
            pass
        return None
    
    @classmethod
    def info(cls, script_name: str) -> None:
        """
        Display detailed information about the script, including parameter signatures and documentation
        
        Args:
            script_name: Script name
            
        Example:
            ScriptManager.info('migrate-sparse-vector')
        """
        try:
            # Get script configuration information
            script_info = cls._get_script_info(script_name)
            
            # Load module and function
            module = importlib.import_module(script_info['module'])
            func = getattr(module, script_info['function'])
            
            # Get function signature
            sig = inspect.signature(func)
            
            # Get function documentation
            doc = inspect.getdoc(func) or "No documentation available"
            
            # Print information
            logger.info("\n" + "=" * 70)
            logger.info(f"Script: {script_name}")
            logger.info("=" * 70)
            logger.info(f"Category: {script_info.get('category', 'N/A')}")
            logger.info(f"Description: {script_info.get('description', 'N/A')}")
            
            if script_info.get('destructive', False):
                logger.info("⚠️  Warning: This is a DESTRUCTIVE operation!")
            
            logger.info("\n" + "-" * 70)
            logger.info("Parameters:")
            logger.info("-" * 70)
            
            # Parse parameter information
            for param_name, param in sig.parameters.items():
                # Parameter type
                param_type = "Any"
                if param.annotation != inspect.Parameter.empty:
                    annotation = param.annotation
                    if isinstance(annotation, str):
                        param_type = annotation
                    else:
                        param_type = getattr(annotation, '__name__', str(annotation))
                
                # Default value
                if param.default != inspect.Parameter.empty:
                    default_str = f", default={param.default}"
                else:
                    default_str = " (required)"
                
                logger.info(f"  {param_name} ({param_type}){default_str}")
            
            logger.info("\n" + "-" * 70)
            logger.info("Documentation:")
            logger.info("-" * 70)
            for line in doc.split('\n'):
                logger.info(f"  {line}")
            
            logger.info("\n" + "=" * 70)
            logger.info("Usage Example:")
            logger.info("=" * 70)
            
            # Generate usage example based on the first parameter type
            params = list(sig.parameters.values())
            if params:
                first_param = params[0]
                param_type_hint = None
                if first_param.annotation != inspect.Parameter.empty:
                    annotation = first_param.annotation
                    param_type_hint = annotation if isinstance(annotation, str) else getattr(annotation, '__name__', None)
                
                if param_type_hint and 'Memory' in param_type_hint:
                    logger.info(f"  from powermem import Memory, auto_config")
                    logger.info(f"  from script import ScriptManager")
                    logger.info(f"  ")
                    logger.info(f"  config = auto_config()")
                    logger.info(f"  memory = Memory(config=config)")
                    logger.info(f"  ScriptManager.run('{script_name}', memory)")
                else:
                    logger.info(f"  from powermem import auto_config")
                    logger.info(f"  from script import ScriptManager")
                    logger.info(f"  ")
                    logger.info(f"  config = auto_config()")
                    logger.info(f"  ScriptManager.run('{script_name}', config)")
            
            logger.info("=" * 70 + "\n")
            
        except Exception as e:
            logger.error(f"Failed to get script info: {e}", exc_info=True)
    
    @classmethod
    def run(cls, script_name: str, param: Any, **kwargs) -> bool:
        """
        Execute the specified script
        
        Args:
            script_name: Script name
            param: Script parameter (config dict/MemoryConfig for upgrade/downgrade scripts, 
                   Memory instance for migration scripts)
            **kwargs: Additional parameters to pass to the script function
            
        Returns:
            bool: Returns True on success, False on failure
            
        Raises:
            ValueError: If param is None or script does not exist
            
        Examples:
            # Upgrade/downgrade scripts (use config)
            config = auto_config()
            ScriptManager.run('upgrade-sparse-vector', config)
            
            # Migration scripts (use Memory instance)
            memory = Memory(config=config)
            ScriptManager.run('migrate-sparse-vector', memory, batch_size=1000)
        """
        if param is None:
            raise ValueError(
                "param parameter is required\n"
                "Example:\n"
                "  from powermem import auto_config, Memory\n"
                "  from script import ScriptManager\n"
                "  config = auto_config()\n"
                "  # For upgrade/downgrade:\n"
                "  ScriptManager.run('upgrade-sparse-vector', config)\n"
                "  # For migration:\n"
                "  memory = Memory(config=config)\n"
                "  ScriptManager.run('migrate-sparse-vector', memory)"
            )
        
        try:
            # Load scripts configuration (not to be confused with memory config parameter)
            scripts_config = cls._load_config()
            script_info = cls._get_script_info(script_name, scripts_config)
            
            # Display script information
            logger.info(f"\nPreparing to execute script: {script_name}")
            logger.info(f"Description: {script_info.get('description', 'No description')}")
            
            # Check if it's a destructive operation
            if script_info.get('destructive', False):
                logger.warning("\n⚠️  Warning: This is a destructive operation that may delete data!")
                confirm = input("Confirm to continue? (yes/no): ").strip().lower()
                if confirm not in ['yes', 'y']:
                    logger.info("Operation cancelled")
                    return False
            
            # Load script module and function
            module_name = script_info['module']
            function_name = script_info['function']
            
            logger.info(f"Loading module: {module_name}")
            module = importlib.import_module(module_name)
            script_func = getattr(module, function_name)
            
            # Execute script
            logger.info(f"Executing script function: {function_name}")
            result = script_func(param, **kwargs)
            
            if result:
                logger.info(f"\n✓ Script '{script_name}' executed successfully!")
            else:
                logger.error(f"\n✗ Script '{script_name}' execution failed")
            
            return result
            
        except TypeError as e:
            # Catch parameter errors and provide friendly hints
            error_msg = str(e)
            logger.error(f"\n✗ Parameter Error: {error_msg}")
            
            try:
                sig = inspect.signature(script_func)
                logger.error(f"\nExpected function signature:")
                logger.error(f"  {function_name}{sig}")
                logger.error(f"\nFor detailed parameter information, run:")
                logger.error(f"  ScriptManager.info('{script_name}')")
            except Exception:
                pass
            
            return False
            
        except Exception as e:
            logger.error(f"Error occurred while executing script: {e}", exc_info=True)
            logger.error(f"\n✗ Error: {e}")
            return False
