"""
Graph tools prompts for memory operations

This module provides tool definitions for graph-based memory operations.
"""

import logging
from typing import Dict, Any, Optional, List
from ..templates import PromptTemplates

logger = logging.getLogger(__name__)

# Tool definitions constants
UPDATE_MEMORY_TOOL_GRAPH = {
    "type": "function",
    "function": {
        "name": "update_graph_memory",
        "description": "Update the relationship key of an existing graph memory based on new information. This function should be called when there's a need to modify an existing relationship in the knowledge graph. The update should only be performed if the new information is more recent, more accurate, or provides additional context compared to the existing information. The source and destination nodes of the relationship must remain the same as in the existing graph memory; only the relationship itself can be updated.",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "The identifier of the source node in the relationship to be updated. This should match an existing node in the graph.",
                },
                "destination": {
                    "type": "string",
                    "description": "The identifier of the destination node in the relationship to be updated. This should match an existing node in the graph.",
                },
                "relationship": {
                    "type": "string",
                    "description": "The new or updated relationship between the source and destination nodes. This should be a concise, clear description of how the two nodes are connected.",
                },
            },
            "required": ["source", "destination", "relationship"],
            "additionalProperties": False,
        },
    },
}

ADD_MEMORY_TOOL_GRAPH = {
    "type": "function",
    "function": {
        "name": "add_graph_memory",
        "description": "Add a new graph memory to the knowledge graph. This function creates a new relationship between two nodes, potentially creating new nodes if they don't exist.",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "The identifier of the source node in the new relationship. This can be an existing node or a new node to be created.",
                },
                "destination": {
                    "type": "string",
                    "description": "The identifier of the destination node in the new relationship. This can be an existing node or a new node to be created.",
                },
                "relationship": {
                    "type": "string",
                    "description": "The type of relationship between the source and destination nodes. This should be a concise, clear description of how the two nodes are connected.",
                },
                "source_type": {
                    "type": "string",
                    "description": "The type or category of the source node. This helps in classifying and organizing nodes in the graph.",
                },
                "destination_type": {
                    "type": "string",
                    "description": "The type or category of the destination node. This helps in classifying and organizing nodes in the graph.",
                },
            },
            "required": [
                "source",
                "destination",
                "relationship",
                "source_type",
                "destination_type",
            ],
            "additionalProperties": False,
        },
    },
}

DELETE_MEMORY_TOOL_GRAPH = {
    "type": "function",
    "function": {
        "name": "delete_graph_memory",
        "description": "Delete the relationship between two nodes. This function deletes the existing relationship.",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "The identifier of the source node in the relationship.",
                },
                "relationship": {
                    "type": "string",
                    "description": "The existing relationship between the source and destination nodes that needs to be deleted.",
                },
                "destination": {
                    "type": "string",
                    "description": "The identifier of the destination node in the relationship.",
                },
            },
            "required": [
                "source",
                "relationship",
                "destination",
            ],
            "additionalProperties": False,
        },
    },
}

NOOP_TOOL = {
    "type": "function",
    "function": {
        "name": "noop",
        "description": "No operation should be performed to the graph entities. This function is called when the system determines that no changes or additions are necessary based on the current input or context. It serves as a placeholder action when no other actions are required, ensuring that the system can explicitly acknowledge situations where no modifications to the graph are needed.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
}


RELATIONS_TOOL = {
    "type": "function",
    "function": {
        "name": "establish_relationships",
        "description": "Establish relationships among the entities based on the provided text.",
        "parameters": {
            "type": "object",
            "properties": {
                "entities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source": {"type": "string", "description": "The source entity of the relationship."},
                            "relationship": {
                                "type": "string",
                                "description": "The relationship between the source and destination entities.",
                            },
                            "destination": {
                                "type": "string",
                                "description": "The destination entity of the relationship.",
                            },
                        },
                        "required": [
                            "source",
                            "relationship",
                            "destination",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["entities"],
            "additionalProperties": False,
        },
    },
}


EXTRACT_ENTITIES_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_entities",
        "description": "Extract entities and their types from the text.",
        "parameters": {
            "type": "object",
            "properties": {
                "entities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "entity": {"type": "string", "description": "The name or identifier of the entity."},
                            "entity_type": {"type": "string", "description": "The type or category of the entity."},
                        },
                        "required": ["entity", "entity_type"],
                        "additionalProperties": False,
                    },
                    "description": "An array of entities with their types.",
                }
            },
            "required": ["entities"],
            "additionalProperties": False,
        },
    },
}

# Structured versions (with strict=True)
UPDATE_MEMORY_STRUCT_TOOL_GRAPH = {
    "type": "function",
    "function": {
        "name": "update_graph_memory",
        "description": "Update the relationship key of an existing graph memory based on new information. This function should be called when there's a need to modify an existing relationship in the knowledge graph. The update should only be performed if the new information is more recent, more accurate, or provides additional context compared to the existing information. The source and destination nodes of the relationship must remain the same as in the existing graph memory; only the relationship itself can be updated.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "The identifier of the source node in the relationship to be updated. This should match an existing node in the graph.",
                },
                "destination": {
                    "type": "string",
                    "description": "The identifier of the destination node in the relationship to be updated. This should match an existing node in the graph.",
                },
                "relationship": {
                    "type": "string",
                    "description": "The new or updated relationship between the source and destination nodes. This should be a concise, clear description of how the two nodes are connected.",
                },
            },
            "required": ["source", "destination", "relationship"],
            "additionalProperties": False,
        },
    },
}

ADD_MEMORY_STRUCT_TOOL_GRAPH = {
    "type": "function",
    "function": {
        "name": "add_graph_memory",
        "description": "Add a new graph memory to the knowledge graph. This function creates a new relationship between two nodes, potentially creating new nodes if they don't exist.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "The identifier of the source node in the new relationship. This can be an existing node or a new node to be created.",
                },
                "destination": {
                    "type": "string",
                    "description": "The identifier of the destination node in the new relationship. This can be an existing node or a new node to be created.",
                },
                "relationship": {
                    "type": "string",
                    "description": "The type of relationship between the source and destination nodes. This should be a concise, clear description of how the two nodes are connected.",
                },
                "source_type": {
                    "type": "string",
                    "description": "The type or category of the source node. This helps in classifying and organizing nodes in the graph.",
                },
                "destination_type": {
                    "type": "string",
                    "description": "The type or category of the destination node. This helps in classifying and organizing nodes in the graph.",
                },
            },
            "required": [
                "source",
                "destination",
                "relationship",
                "source_type",
                "destination_type",
            ],
            "additionalProperties": False,
        },
    },
}

DELETE_MEMORY_STRUCT_TOOL_GRAPH = {
    "type": "function",
    "function": {
        "name": "delete_graph_memory",
        "description": "Delete the relationship between two nodes. This function deletes the existing relationship.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "The identifier of the source node in the relationship.",
                },
                "relationship": {
                    "type": "string",
                    "description": "The existing relationship between the source and destination nodes that needs to be deleted.",
                },
                "destination": {
                    "type": "string",
                    "description": "The identifier of the destination node in the relationship.",
                },
            },
            "required": [
                "source",
                "relationship",
                "destination",
            ],
            "additionalProperties": False,
        },
    },
}

NOOP_STRUCT_TOOL = {
    "type": "function",
    "function": {
        "name": "noop",
        "description": "No operation should be performed to the graph entities. This function is called when the system determines that no changes or additions are necessary based on the current input or context. It serves as a placeholder action when no other actions are required, ensuring that the system can explicitly acknowledge situations where no modifications to the graph are needed.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
}

RELATIONS_STRUCT_TOOL = {
    "type": "function",
    "function": {
        "name": "establish_relationships",
        "description": "Establish relationships among the entities based on the provided text.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "entities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source": {
                                "type": "string",
                                "description": "The source entity of the relationship.",
                            },
                            "relationship": {
                                "type": "string",
                                "description": "The relationship between the source and destination entities.",
                            },
                            "destination": {
                                "type": "string",
                                "description": "The destination entity of the relationship.",
                            },
                        },
                        "required": [
                            "source",
                            "relationship",
                            "destination",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["entities"],
            "additionalProperties": False,
        },
    },
}


EXTRACT_ENTITIES_STRUCT_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_entities",
        "description": "Extract entities and their types from the text.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "entities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "entity": {"type": "string", "description": "The name or identifier of the entity."},
                            "entity_type": {"type": "string", "description": "The type or category of the entity."},
                        },
                        "required": ["entity", "entity_type"],
                        "additionalProperties": False,
                    },
                    "description": "An array of entities with their types.",
                }
            },
            "required": ["entities"],
            "additionalProperties": False,
        },
    },
}


class GraphToolsPrompts(PromptTemplates):
    """
    Tool definitions for graph-based memory operations.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize graph tools prompts.
        
        Args:
            config: Configuration dictionary
        """
        super().__init__(config)
    
    def get_tool(self, tool_name: str, structured: bool = False) -> Dict[str, Any]:
        """
        Get a specific tool definition.
        
        Args:
            tool_name: Name of the tool
            structured: Whether to return structured version (with strict=True)
            
        Returns:
            Tool definition dictionary
        """
        tool_map = {
            "update_memory": UPDATE_MEMORY_STRUCT_TOOL_GRAPH if structured else UPDATE_MEMORY_TOOL_GRAPH,
            "add_memory": ADD_MEMORY_STRUCT_TOOL_GRAPH if structured else ADD_MEMORY_TOOL_GRAPH,
            "delete_memory": DELETE_MEMORY_STRUCT_TOOL_GRAPH if structured else DELETE_MEMORY_TOOL_GRAPH,
            "noop": NOOP_STRUCT_TOOL if structured else NOOP_TOOL,
            "establish_relationships": RELATIONS_STRUCT_TOOL if structured else RELATIONS_TOOL,
            "extract_entities": EXTRACT_ENTITIES_STRUCT_TOOL if structured else EXTRACT_ENTITIES_TOOL,
        }
        return tool_map.get(tool_name, {})
    
    def get_tools(self, tool_names: List[str], structured: bool = False) -> List[Dict[str, Any]]:
        """
        Get multiple tool definitions.
        
        Args:
            tool_names: List of tool names
            structured: Whether to return structured versions
            
        Returns:
            List of tool definition dictionaries
        """
        return [self.get_tool(name, structured) for name in tool_names if self.get_tool(name, structured)]
    
    def get_all_tools(self, structured: bool = False) -> List[Dict[str, Any]]:
        """
        Get all tool definitions.
        
        Args:
            structured: Whether to return structured versions
            
        Returns:
            List of all tool definition dictionaries
        """
        tool_names = ["update_memory", "add_memory", "delete_memory", "noop", "establish_relationships", "extract_entities"]
        return self.get_tools(tool_names, structured)
    
    def get_update_tool(self, structured: bool = False) -> Dict[str, Any]:
        """Get update memory tool."""
        return UPDATE_MEMORY_STRUCT_TOOL_GRAPH if structured else UPDATE_MEMORY_TOOL_GRAPH
    
    def get_add_tool(self, structured: bool = False) -> Dict[str, Any]:
        """Get add memory tool."""
        return ADD_MEMORY_STRUCT_TOOL_GRAPH if structured else ADD_MEMORY_TOOL_GRAPH
    
    def get_delete_tool(self, structured: bool = False) -> Dict[str, Any]:
        """Get delete memory tool."""
        return DELETE_MEMORY_STRUCT_TOOL_GRAPH if structured else DELETE_MEMORY_TOOL_GRAPH
    
    def get_noop_tool(self, structured: bool = False) -> Dict[str, Any]:
        """Get noop tool."""
        return NOOP_STRUCT_TOOL if structured else NOOP_TOOL
    
    def get_relations_tool(self, structured: bool = False) -> Dict[str, Any]:
        """Get establish relationships tool."""
        return RELATIONS_STRUCT_TOOL if structured else RELATIONS_TOOL
    
    def get_extract_entities_tool(self, structured: bool = False) -> Dict[str, Any]:
        """Get extract entities tool."""
        return EXTRACT_ENTITIES_STRUCT_TOOL if structured else EXTRACT_ENTITIES_TOOL
