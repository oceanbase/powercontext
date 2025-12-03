"""
AI Customer Service Bot with PowerMem + LangGraph + OceanBase

This example demonstrates how to build an AI Customer Service Bot using
PowerMem for intelligent memory management, LangGraph for stateful conversation
workflows, and OceanBase as the database backend.

Features:
- Stateful conversation management with LangGraph
- Persistent memory of customer information, orders, and preferences
- Intelligent fact extraction from conversations
- Multi-step workflow handling (order lookup, issue resolution, etc.)
- Context-aware responses based on customer history
- OceanBase database backend for scalable storage

Setup:
1. Install dependencies: pip install langgraph>=1.0.0 langchain langchain-openai python-dotenv
2. Copy .env.example to .env and configure OceanBase
3. Run: python customer_service_bot.py
"""

import os
import sys
from typing import Dict, Any, List, TypedDict, Annotated
from dotenv import load_dotenv

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from powermem import Memory, auto_config

# LangGraph and LangChain imports
try:
    from langgraph.graph import StateGraph, END, START
except ImportError as e:
    print("=" * 80)
    print("ERROR: langgraph package is not installed")
    print("=" * 80)
    print("\nPlease install the required dependencies:")
    print("  pip install langgraph>=1.0.0 langchain langchain-core langchain-openai")
    print("\nOr install all dependencies at once:")
    print("  pip install langgraph>=1.0.0 langchain langchain-core langchain-openai langchain-community")
    print(f"\nImport error details: {e}")
    print("=" * 80)
    sys.exit(1)

try:
    from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
except ImportError as e:
    print("=" * 80)
    print("ERROR: langchain-core package is not installed")
    print("=" * 80)
    print("\nPlease install:")
    print("  pip install langchain-core")
    print(f"\nImport error details: {e}")
    print("=" * 80)
    sys.exit(1)

# Try newer import first, fallback to older
try:
    from langchain_openai import ChatOpenAI
except ImportError:
    try:
        from langchain.chat_models import ChatOpenAI
    except ImportError:
        ChatOpenAI = None
        print("Warning: ChatOpenAI not available. LLM features will be limited.")


def load_oceanbase_config():
    """
    Load OceanBase configuration from environment variables.
    
    Uses the auto_config() utility function to automatically load from .env.
    """
    # Try to load from powermem.env from multiple possible locations
    possible_paths = [
        os.path.join(os.path.dirname(__file__), '..', '..', '.env'),  # project root
    ]
    
    loaded = False
    for env_path in possible_paths:
        if os.path.exists(env_path):
            load_dotenv(env_path, override=True)
            print(f"Loaded config from: {env_path}")
            loaded = True
            break
    
    if not loaded:
        # Try to load from any .env file
        load_dotenv()
    
    # Automatically load config from environment variables
    config = auto_config()
    
    return config


# Define the state schema for LangGraph
class CustomerServiceState(TypedDict):
    """State schema for the customer service bot."""
    messages: Annotated[List[BaseMessage], "The conversation messages"]
    customer_id: str
    intent: str  # "order_inquiry", "issue_resolution", "general", etc.
    order_number: str
    issue_type: str
    context: Dict[str, Any]  # Additional context from PowerMem
    resolved: bool


class CustomerServiceBot:
    """
    AI Customer Service Bot using PowerMem, LangGraph, and OceanBase.
    
    This bot provides:
    - Order status inquiries
    - Issue resolution assistance
    - Product information
    - Customer preference tracking
    - Multi-step conversation workflows
    """
    
    def __init__(self, customer_id: str = "customer_001"):
        """
        Initialize the customer service bot.
        
        Args:
            customer_id: Unique identifier for the customer
        """
        self.customer_id = customer_id
        
        # Load OceanBase configuration
        print("Loading OceanBase configuration...")
        config = load_oceanbase_config()
        
        # Ensure OceanBase is configured
        if config.get('vector_store', {}).get('provider') != 'oceanbase':
            print("⚠️  Warning: Database provider is not OceanBase.")
            print("   Please configure DATABASE_PROVIDER=oceanbase in .env")
        
        # Initialize PowerMem with OceanBase
        print("Initializing PowerMem with OceanBase...")
        self.memory = create_memory(config=config)
        print("✓ PowerMem initialized with OceanBase!")
        
        # Initialize LLM
        llm_config_dict = config.get('llm', {})
        llm_provider = llm_config_dict.get('provider', '').lower()
        llm_inner_config = llm_config_dict.get('config', {})
        llm_api_key = llm_inner_config.get('api_key') or os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
        llm_base_url = llm_inner_config.get('dashscope_base_url') or llm_inner_config.get('openai_base_url') or os.getenv("LLM_BASE_URL")
        llm_model = llm_inner_config.get('model', 'gpt-3.5-turbo')
        llm_temperature = llm_inner_config.get('temperature', 0.7)
        
        self.llm = None
        if ChatOpenAI and llm_api_key:
            try:
                llm_kwargs = {
                    "model": llm_model,
                    "temperature": llm_temperature
                }
                
                if llm_provider == 'qwen' and llm_base_url:
                    if '/compatible-mode' not in llm_base_url and '/api/v1' in llm_base_url:
                        compatible_url = llm_base_url.replace('/api/v1', '/compatible-mode/v1')
                        llm_kwargs["base_url"] = compatible_url
                        print(f"  Using DashScope compatible mode: {compatible_url}")
                    else:
                        llm_kwargs["base_url"] = llm_base_url
                elif llm_base_url:
                    llm_kwargs["base_url"] = llm_base_url
                
                if llm_api_key:
                    llm_kwargs["api_key"] = llm_api_key
                
                self.llm = ChatOpenAI(**llm_kwargs)
                print(f"✓ LLM initialized: {llm_model} (provider: {llm_provider or 'openai'})")
            except Exception as e:
                print(f"Warning: Failed to initialize ChatOpenAI: {e}")
                self.llm = None
        
        if not self.llm:
            print("Warning: No LLM available. Using mock responses.")
            print("  Note: Conversations will still be saved to PowerMem database.")
        
        # Build the LangGraph workflow
        self.graph = self._build_graph()
    
    def _build_graph(self) -> StateGraph:
        """Build the LangGraph state graph for customer service workflow."""
        workflow = StateGraph(CustomerServiceState)
        
        # Add nodes
        workflow.add_node("load_context", self._load_customer_context)
        workflow.add_node("classify_intent", self._classify_intent)
        workflow.add_node("handle_order_inquiry", self._handle_order_inquiry)
        workflow.add_node("handle_issue_resolution", self._handle_issue_resolution)
        workflow.add_node("handle_general", self._handle_general)
        workflow.add_node("save_conversation", self._save_conversation)
        
        # Set entry point using START constant (LangGraph 1.0+ best practice)
        workflow.add_edge(START, "load_context")
        
        # Add edges
        workflow.add_edge("load_context", "classify_intent")
        
        # Conditional routing based on intent
        workflow.add_conditional_edges(
            "classify_intent",
            self._route_intent,
            {
                "order_inquiry": "handle_order_inquiry",
                "issue_resolution": "handle_issue_resolution",
                "general": "handle_general",
            }
        )
        
        # All handlers go to save_conversation
        workflow.add_edge("handle_order_inquiry", "save_conversation")
        workflow.add_edge("handle_issue_resolution", "save_conversation")
        workflow.add_edge("handle_general", "save_conversation")
        
        # Save conversation then end
        workflow.add_edge("save_conversation", END)
        
        return workflow.compile()
    
    def _load_customer_context(self, state: CustomerServiceState) -> CustomerServiceState:
        """Load relevant customer context from PowerMem."""
        print(f"[Node: load_context] Loading context for customer {state['customer_id']}")
        
        # Get the latest user message
        last_message = state["messages"][-1] if state["messages"] else None
        query = last_message.content if last_message and hasattr(last_message, 'content') else ""
        
        # Search PowerMem for relevant customer memories
        try:
            results = self.memory.search(
                query=query,
                user_id=state["customer_id"],
                limit=5
            )
            
            memories = results.get('results', [])
            context_info = {
                "recent_memories": [mem.get('memory', '') for mem in memories],
                "total_memories": len(memories)
            }
            
            # Also search for specific information
            orders = self.memory.search("order purchase transaction", user_id=state["customer_id"], limit=3)
            preferences = self.memory.search("preference like favorite", user_id=state["customer_id"], limit=3)
            
            context_info["order_mentions"] = [mem.get('memory', '') for mem in orders.get('results', [])]
            context_info["preference_mentions"] = [mem.get('memory', '') for mem in preferences.get('results', [])]
            
        except Exception as e:
            print(f"Warning: Failed to search PowerMem: {e}")
            context_info = {"error": str(e)}
        
        state["context"] = context_info
        return state
    
    def _classify_intent(self, state: CustomerServiceState) -> CustomerServiceState:
        """Classify the customer's intent from their message."""
        print("[Node: classify_intent] Classifying intent...")
        
        last_message = state["messages"][-1] if state["messages"] else None
        user_input = last_message.content if last_message and hasattr(last_message, 'content') else ""
        
        # Simple keyword-based classification (can be enhanced with LLM)
        user_lower = user_input.lower()
        
        if any(word in user_lower for word in ["order", "purchase", "track", "status", "delivery", "shipment"]):
            intent = "order_inquiry"
        elif any(word in user_lower for word in ["problem", "issue", "broken", "wrong", "refund", "return", "complaint"]):
            intent = "issue_resolution"
        else:
            intent = "general"
        
        state["intent"] = intent
        print(f"  Classified intent: {intent}")
        return state
    
    def _route_intent(self, state: CustomerServiceState) -> str:
        """Route to the appropriate handler based on intent."""
        return state.get("intent", "general")
    
    def _handle_order_inquiry(self, state: CustomerServiceState) -> CustomerServiceState:
        """Handle order inquiry requests."""
        print("[Node: handle_order_inquiry] Processing order inquiry...")
        
        last_message = state["messages"][-1] if state["messages"] else None
        user_input = last_message.content if last_message and hasattr(last_message, 'content') else ""
        
        # Extract order number if mentioned
        import re
        order_match = re.search(r'order[#\s]*([A-Z0-9-]+)', user_input, re.IGNORECASE)
        if order_match:
            state["order_number"] = order_match.group(1)
        
        # Generate response using LLM or mock
        context_str = "\n".join(state["context"].get("order_mentions", []))
        
        if self.llm:
            try:
                prompt = f"""You are a helpful customer service representative. The customer is asking about their order.

Customer context from previous conversations:
{context_str}

Customer message: {user_input}

Please provide a helpful response about the order status. If an order number was mentioned ({state.get('order_number', 'N/A')}), acknowledge it.
Be friendly and professional."""
                
                response = self.llm.invoke(prompt)
                response_text = response.content if hasattr(response, 'content') else str(response)
            except Exception as e:
                print(f"Error calling LLM: {e}")
                response_text = f"I can help you with your order inquiry. I found some previous order information in your history. How can I assist you today?"
        else:
            response_text = f"I can help you with your order inquiry. I found some previous order information in your history. How can I assist you today?"
        
        # Add AI response to messages
        state["messages"].append(AIMessage(content=response_text))
        return state
    
    def _handle_issue_resolution(self, state: CustomerServiceState) -> CustomerServiceState:
        """Handle issue resolution requests."""
        print("[Node: handle_issue_resolution] Processing issue resolution...")
        
        last_message = state["messages"][-1] if state["messages"] else None
        user_input = last_message.content if last_message and hasattr(last_message, 'content') else ""
        
        # Classify issue type
        user_lower = user_input.lower()
        if any(word in user_lower for word in ["refund", "money", "payment"]):
            issue_type = "refund"
        elif any(word in user_lower for word in ["return", "send back"]):
            issue_type = "return"
        elif any(word in user_lower for word in ["broken", "damaged", "defective"]):
            issue_type = "defect"
        else:
            issue_type = "general_issue"
        
        state["issue_type"] = issue_type
        
        # Generate response
        if self.llm:
            try:
                prompt = f"""You are a helpful customer service representative. The customer has an issue that needs resolution.

Customer message: {user_input}
Issue type: {issue_type}

Please provide a helpful, empathetic response. Offer to help resolve the issue and ask for any additional information needed.
Be understanding and professional."""
                
                response = self.llm.invoke(prompt)
                response_text = response.content if hasattr(response, 'content') else str(response)
            except Exception as e:
                print(f"Error calling LLM: {e}")
                response_text = f"I understand you're experiencing an issue. I'm here to help resolve it. Can you provide more details about what happened?"
        else:
            response_text = f"I understand you're experiencing an issue. I'm here to help resolve it. Can you provide more details about what happened?"
        
        state["messages"].append(AIMessage(content=response_text))
        return state
    
    def _handle_general(self, state: CustomerServiceState) -> CustomerServiceState:
        """Handle general inquiries."""
        print("[Node: handle_general] Processing general inquiry...")
        
        last_message = state["messages"][-1] if state["messages"] else None
        user_input = last_message.content if last_message and hasattr(last_message, 'content') else ""
        
        # Get customer context
        context_str = "\n".join(state["context"].get("recent_memories", []))
        preferences_str = "\n".join(state["context"].get("preference_mentions", []))
        
        # Generate response
        if self.llm:
            try:
                prompt = f"""You are a helpful customer service representative. The customer has a general inquiry.

Customer context from previous conversations:
{context_str}

Customer preferences:
{preferences_str}

Customer message: {user_input}

Please provide a helpful, personalized response based on the customer's history and preferences."""
                
                response = self.llm.invoke(prompt)
                response_text = response.content if hasattr(response, 'content') else str(response)
            except Exception as e:
                print(f"Error calling LLM: {e}")
                response_text = f"Thank you for your message. How can I assist you today?"
        else:
            response_text = f"Thank you for your message. How can I assist you today?"
        
        state["messages"].append(AIMessage(content=response_text))
        return state
    
    def _save_conversation(self, state: CustomerServiceState) -> CustomerServiceState:
        """Save the conversation to PowerMem."""
        print("[Node: save_conversation] Saving conversation to PowerMem...")
        
        # Get the last user message and AI response
        messages = state["messages"]
        if len(messages) >= 2:
            user_msg = messages[-2] if isinstance(messages[-2], HumanMessage) else None
            ai_msg = messages[-1] if isinstance(messages[-1], AIMessage) else None
            
            if user_msg and ai_msg:
                try:
                    # Save to PowerMem with intelligent fact extraction
                    self.memory.add(
                        messages=[
                            {"role": "user", "content": user_msg.content if hasattr(user_msg, 'content') else str(user_msg)},
                            {"role": "assistant", "content": ai_msg.content if hasattr(ai_msg, 'content') else str(ai_msg)}
                        ],
                        user_id=state["customer_id"],
                        infer=True,  # Enable intelligent fact extraction
                        metadata={
                            "intent": state.get("intent", "general"),
                            "order_number": state.get("order_number", ""),
                            "issue_type": state.get("issue_type", ""),
                            "category": "customer_service"
                        }
                    )
                    print("  ✓ Conversation saved to PowerMem")
                except Exception as e:
                    print(f"Warning: Failed to save to PowerMem: {e}")
        
        state["resolved"] = True
        return state
    
    def chat(self, user_input: str) -> str:
        """
        Process user input and return bot response.
        
        Args:
            user_input: Customer's message
            
        Returns:
            Bot's response
        """
        # Initialize state
        initial_state: CustomerServiceState = {
            "messages": [HumanMessage(content=user_input)],
            "customer_id": self.customer_id,
            "intent": "",
            "order_number": "",
            "issue_type": "",
            "context": {},
            "resolved": False
        }
        
        # Run the graph
        try:
            final_state = self.graph.invoke(initial_state)
            
            # Get the last AI message
            messages = final_state.get("messages", [])
            if messages:
                last_message = messages[-1]
                if isinstance(last_message, AIMessage):
                    return last_message.content if hasattr(last_message, 'content') else str(last_message)
            
            return "I apologize, but I couldn't generate a response. Please try again."
        except Exception as e:
            error_msg = str(e)
            print(f"Error in graph execution: {error_msg}")
            return f"I apologize, but I encountered an error: {error_msg}. Please try again."
    
    def get_customer_summary(self) -> Dict[str, Any]:
        """
        Get a summary of the customer's stored information.
        
        Returns:
            Dictionary containing customer summary
        """
        try:
            # Get all memories for this customer
            all_memories = self.memory.get_all(user_id=self.customer_id)
            memories = all_memories.get('results', [])
            
            # Search for specific types of information
            orders = self.memory.search("order purchase transaction", user_id=self.customer_id, limit=10)
            issues = self.memory.search("problem issue complaint", user_id=self.customer_id, limit=10)
            preferences = self.memory.search("preference like favorite", user_id=self.customer_id, limit=10)
            
            return {
                "total_memories": len(memories),
                "order_mentions": len(orders.get('results', [])),
                "issue_mentions": len(issues.get('results', [])),
                "preference_mentions": len(preferences.get('results', [])),
                "recent_memories": [
                    {
                        "memory": mem.get('memory', ''),
                        "metadata": mem.get('metadata', {})
                    }
                    for mem in memories[:5]
                ]
            }
        except Exception as e:
            return {"error": str(e)}


def demo_conversation():
    """Demonstrate a customer service conversation."""
    print("=" * 80)
    print("AI Customer Service Bot Demo")
    print("Framework: LangGraph")
    print("Database: OceanBase")
    print("=" * 80)
    print()
    
    # Initialize bot for a customer
    customer_id = "customer_alice_001"
    bot = CustomerServiceBot(customer_id=customer_id)
    
    print(f"Bot initialized for customer: {customer_id}")
    print("Starting conversation...\n")
    print("-" * 80)
    
    # Simulate a conversation
    conversation_turns = [
        {
            "user": "Hello, I'd like to check the status of my order #ORD-12345",
            "description": "Customer asks about order status"
        },
        {
            "user": "I ordered a laptop last week. When will it arrive?",
            "description": "Customer provides more order details"
        },
        {
            "user": "I have a problem with my recent order. The product arrived damaged.",
            "description": "Customer reports an issue"
        },
        {
            "user": "Can I get a refund for the damaged item?",
            "description": "Customer requests refund"
        },
        {
            "user": "What are your return policies?",
            "description": "Customer asks general question"
        }
    ]
    
    for i, turn in enumerate(conversation_turns, 1):
        print(f"\n[Turn {i}] {turn['description']}")
        print(f"Customer: {turn['user']}")
        print()
        
        response = bot.chat(turn['user'])
        print(f"Bot: {response}")
        print("-" * 80)
    
    # Show customer summary
    print("\n" + "=" * 80)
    print("Customer Information Summary")
    print("=" * 80)
    summary = bot.get_customer_summary()
    print(f"Total memories stored: {summary.get('total_memories', 0)}")
    print(f"Order mentions: {summary.get('order_mentions', 0)}")
    print(f"Issue mentions: {summary.get('issue_mentions', 0)}")
    print(f"Preference mentions: {summary.get('preference_mentions', 0)}")
    print("\nRecent memories:")
    for mem in summary.get('recent_memories', [])[:3]:
        print(f"  - {mem.get('memory', '')[:100]}...")
    
    print("\n" + "=" * 80)
    print("Demo completed!")
    print("=" * 80)


def interactive_mode():
    """Run the bot in interactive mode for user input."""
    print("=" * 80)
    print("AI Customer Service Bot - Interactive Mode")
    print("Framework: LangGraph")
    print("Database: OceanBase")
    print("=" * 80)
    print("Type 'quit' or 'exit' to end the conversation")
    print("Type 'summary' to see your customer information summary")
    print("-" * 80)
    print()
    
    # Get customer ID
    customer_id = input("Enter customer ID (or press Enter for default): ").strip()
    if not customer_id:
        customer_id = "customer_interactive_001"
    
    # Initialize bot
    bot = CustomerServiceBot(customer_id=customer_id)
    print(f"\nBot initialized for customer: {customer_id}")
    print("How can I help you today?\n")
    
    # Conversation loop
    while True:
        user_input = input("You: ").strip()
        
        if not user_input:
            continue
        
        if user_input.lower() in ['quit', 'exit', 'q']:
            print("\nThank you for using our Customer Service Bot. Have a great day!")
            break
        
        if user_input.lower() == 'summary':
            summary = bot.get_customer_summary()
            print("\n--- Customer Summary ---")
            print(f"Total memories: {summary.get('total_memories', 0)}")
            print(f"Order mentions: {summary.get('order_mentions', 0)}")
            print(f"Issue mentions: {summary.get('issue_mentions', 0)}")
            print(f"Preference mentions: {summary.get('preference_mentions', 0)}")
            print("\nRecent memories:")
            for mem in summary.get('recent_memories', [])[:5]:
                print(f"  - {mem.get('memory', '')}")
            print()
            continue
        
        # Get bot response
        print("Bot: ", end="")
        response = bot.chat(user_input)
        print(response)
        print()


def main():
    """Main function to run the customer service bot."""
    import argparse
    
    parser = argparse.ArgumentParser(description="AI Customer Service Bot with PowerMem + LangGraph (OceanBase)")
    parser.add_argument(
        '--mode',
        choices=['demo', 'interactive'],
        default='demo',
        help='Run mode: demo (predefined conversation) or interactive (user input)'
    )
    parser.add_argument(
        '--customer-id',
        type=str,
        default=None,
        help='Customer ID for the conversation'
    )
    
    args = parser.parse_args()
    
    if args.mode == 'demo':
        demo_conversation()
    elif args.mode == 'interactive':
        if args.customer_id:
            # Override default customer ID in interactive mode
            import sys
            sys.argv = [sys.argv[0], '--mode', 'interactive']
        interactive_mode()
    else:
        print(f"Unknown mode: {args.mode}")
        print("Use --mode demo or --mode interactive")


if __name__ == "__main__":
    main()

