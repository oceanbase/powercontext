"""
AI Healthcare Support Bot with PowerMem + LangChain

This example demonstrates how to build an AI Healthcare Support Bot using
PowerMem for intelligent memory management and LangChain for conversation handling.

Features:
- Persistent memory of patient information, symptoms, and medical history
- Intelligent fact extraction from conversations
- Context-aware responses based on patient history
- Multi-turn conversation support
- Privacy-aware patient data management
- OceanBase database backend for scalable storage

Setup:
1. Install dependencies: pip install langchain langchain-community langchain-openai python-dotenv
2. Copy .env.example to .env and configure OceanBase
3. Run: python healthcare_support_bot.py
"""

import os
import sys
from typing import Dict, Any, List
from dotenv import load_dotenv

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from powermem import create_memory, auto_config

# LangChain imports
try:
    from langchain.memory import ConversationBufferMemory
    from langchain.chains import ConversationChain
    from langchain.prompts import PromptTemplate
    # Try newer import first, fallback to older
    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        try:
            from langchain.chat_models import ChatOpenAI
        except ImportError:
            ChatOpenAI = None
    
    try:
        from langchain_community.llms import OpenAI
    except ImportError:
        try:
            from langchain.llms import OpenAI
        except ImportError:
            OpenAI = None
except ImportError as e:
    print("Please install langchain dependencies:")
    print("  pip install langchain langchain-community langchain-openai")
    print(f"Error: {e}")
    sys.exit(1)


def load_oceanbase_config():
    """
    Load OceanBase configuration from environment variables.
    
    Uses the auto_config() utility function to automatically load from .env.
    """
    # Try to load from powermem.env from multiple possible locations
    possible_paths = [
        os.path.join(os.path.dirname(__file__), '..', 'configs', 'powermem.env'),  # examples/.env
        os.path.join(os.path.dirname(__file__), '..', '..', 'configs', 'powermem.env'),  # .env
        os.path.join(os.path.dirname(__file__), '..', '..', 'powermem.env'),  # project root
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


class HealthcarePowerMemMemory(ConversationBufferMemory):
    """
    Custom memory class that integrates PowerMem with LangChain for healthcare support.
    
    This class extends ConversationBufferMemory to:
    - Store patient conversations in PowerMem
    - Extract medical facts (symptoms, medications, history) automatically
    - Retrieve relevant patient context for personalized responses
    - Maintain privacy by isolating patient data by user_id
    """
    
    def __init__(self, powermem_instance, user_id: str, **kwargs):
        super().__init__(**kwargs)
        # Use object.__setattr__ to bypass Pydantic validation for custom attributes
        object.__setattr__(self, 'powermem', powermem_instance)
        object.__setattr__(self, 'user_id', user_id)
    
    def save_context(self, inputs: Dict[str, Any], outputs: Dict[str, str]):
        """Save conversation context to both LangChain buffer and PowerMem."""
        super().save_context(inputs, outputs)
        
        # Extract user input and assistant response
        user_input = inputs.get('input', '') if isinstance(inputs, dict) else str(inputs)
        assistant_output = outputs.get('output', '') if isinstance(outputs, dict) else str(outputs)
        
        # Save to PowerMem with intelligent fact extraction
        messages = [
            {"role": "user", "content": user_input},
            {"role": "assistant", "content": assistant_output}
        ]
        
        try:
            self.powermem.add(
                messages=messages,
                user_id=self.user_id,
                infer=True,  # Enable intelligent fact extraction
                metadata={
                    "category": "healthcare",
                    "conversation_type": "patient_support"
                }
            )
        except Exception as e:
            print(f"Warning: Failed to save to PowerMem: {e}")
    
    def load_memory_variables(self, inputs: Dict[str, Any]) -> Dict[str, str]:
        """Load relevant patient context from PowerMem."""
        # Extract query from inputs
        query = inputs.get('input', '') if isinstance(inputs, dict) else str(inputs)
        
        # Search PowerMem for relevant patient memories
        try:
            results = self.powermem.search(
                query=query,
                user_id=self.user_id,
                limit=5  # Get top 5 relevant memories
            )
            
            # Format memories for LangChain context
            memories = results.get('results', [])
            if memories:
                memory_text = "\n".join([
                    f"- {mem.get('memory', '')}" for mem in memories
                ])
            else:
                memory_text = "No previous patient history found."
            
        except Exception as e:
            print(f"Warning: Failed to search PowerMem: {e}")
            memory_text = "Unable to retrieve patient history."
        
        # Get conversation history from buffer
        history = self.buffer if hasattr(self, 'buffer') else ""
        
        # Combine patient context with history
        # Format: Patient context first, then conversation history
        combined_history = f"""Patient Context (from previous conversations):
{memory_text}

Conversation History:
{history}"""
        
        return {
            "history": combined_history
        }


class HealthcareSupportBot:
    """
    AI Healthcare Support Bot using PowerMem and LangChain.
    
    This bot provides:
    - Symptom assessment and guidance
    - Medication reminders and information
    - Appointment scheduling assistance
    - General health information
    - Patient history tracking
    """
    
    def __init__(self, patient_id: str = "patient_001", use_openai: bool = True):
        """
        Initialize the healthcare support bot.
        
        Args:
            patient_id: Unique identifier for the patient
            use_openai: Whether to use OpenAI (True) or other LLM provider
        """
        self.patient_id = patient_id
        
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
        
        # Initialize LangChain memory with PowerMem integration
        self.langchain_memory = HealthcarePowerMemMemory(
            powermem_instance=self.memory,
            user_id=patient_id
        )
        
        # Initialize LLM
        # Check for configured LLM provider from config
        llm_config_dict = config.get('llm', {})
        llm_provider = llm_config_dict.get('provider', '').lower()
        llm_inner_config = llm_config_dict.get('config', {})
        llm_api_key = llm_inner_config.get('api_key') or os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
        # For Qwen, use dashscope_base_url; for OpenAI-compatible, use base_url
        llm_base_url = llm_inner_config.get('dashscope_base_url') or llm_inner_config.get('openai_base_url') or os.getenv("LLM_BASE_URL")
        llm_model = llm_inner_config.get('model', 'gpt-3.5-turbo')
        llm_temperature = llm_inner_config.get('temperature', 0.7)
        
        self.llm = None
        if ChatOpenAI and (llm_api_key or use_openai):
            try:
                # Support OpenAI-compatible APIs (including Qwen via DashScope)
                llm_kwargs = {
                    "model": llm_model,
                    "temperature": llm_temperature
                }
                
                # For Qwen/DashScope, configure API endpoint
                if llm_provider == 'qwen' and llm_base_url:
                    # DashScope may need compatible mode or direct API endpoint
                    # Try compatible mode first, fallback to original if it fails
                    if '/compatible-mode' not in llm_base_url and '/api/v1' in llm_base_url:
                        # Try compatible mode endpoint
                        compatible_url = llm_base_url.replace('/api/v1', '/compatible-mode/v1')
                        llm_kwargs["base_url"] = compatible_url
                        print(f"  Using DashScope compatible mode: {compatible_url}")
                    else:
                        llm_kwargs["base_url"] = llm_base_url
                elif llm_base_url:
                    llm_kwargs["base_url"] = llm_base_url
                
                if llm_api_key:
                    llm_kwargs["api_key"] = llm_api_key
                
                # For DashScope, also set default_headers if needed
                if llm_provider == 'qwen':
                    llm_kwargs.setdefault("default_headers", {})
                    # DashScope uses Authorization header with "Bearer" prefix
                    if llm_api_key and not llm_api_key.startswith("Bearer "):
                        # The api_key parameter should handle this, but we ensure it's set
                        pass
                
                self.llm = ChatOpenAI(**llm_kwargs)
                print(f"✓ LLM initialized: {llm_model} (provider: {llm_provider or 'openai'})")
            except Exception as e:
                print(f"Warning: Failed to initialize ChatOpenAI: {e}")
                print(f"  Attempting fallback configuration...")
                # Try with original base_url if compatible mode failed
                try:
                    if llm_provider == 'qwen' and llm_base_url:
                        llm_kwargs = {
                            "model": llm_model,
                            "temperature": llm_temperature,
                            "base_url": llm_base_url,
                            "api_key": llm_api_key
                        }
                        self.llm = ChatOpenAI(**llm_kwargs)
                        print(f"✓ LLM initialized with fallback config: {llm_model}")
                    else:
                        self.llm = None
                except Exception as e2:
                    print(f"Warning: Fallback also failed: {e2}")
                    self.llm = None
        elif OpenAI:
            # Fallback to OpenAI class (works with other providers too)
            try:
                llm_kwargs = {"temperature": llm_temperature}
                if llm_base_url:
                    llm_kwargs["base_url"] = llm_base_url
                if llm_api_key:
                    llm_kwargs["openai_api_key"] = llm_api_key
                self.llm = OpenAI(**llm_kwargs)
            except Exception as e:
                print(f"Warning: Failed to initialize OpenAI: {e}")
                self.llm = None
        
        if not self.llm:
            print("Warning: No LLM available. Using mock responses.")
            print("  Note: Conversations will still be saved to PowerMem database.")
        
        # Create healthcare-specific prompt template
        # Note: patient_context is now included in history by load_memory_variables
        self.prompt = PromptTemplate(
            input_variables=["history", "input"],
            template="""You are a helpful and empathetic AI Healthcare Support Assistant. 
Your role is to provide general health information, symptom guidance, and support to patients.

IMPORTANT DISCLAIMERS:
- You are NOT a replacement for professional medical advice
- Always recommend consulting with healthcare professionals for serious symptoms
- Never diagnose medical conditions
- Provide general information and guidance only

{history}

Current Patient Input: {input}

Please provide a helpful, empathetic, and informative response. If the patient mentions serious symptoms 
(chest pain, difficulty breathing, severe pain, etc.), strongly recommend immediate medical attention.

AI Assistant Response:"""
        )
        
        # Create conversation chain
        if self.llm:
            self.chain = ConversationChain(
                llm=self.llm,
                memory=self.langchain_memory,
                prompt=self.prompt,
                verbose=False
            )
        else:
            self.chain = None
    
    def chat(self, user_input: str) -> str:
        """
        Process user input and return bot response.
        
        Args:
            user_input: Patient's message
            
        Returns:
            Bot's response
        """
        if not self.chain:
            # Mock response if no LLM available
            response = "I'm a healthcare support bot. Please configure an LLM API key to use this feature."
            # Still save to PowerMem even without LLM
            try:
                self.langchain_memory.save_context(
                    inputs={'input': user_input},
                    outputs={'output': response}
                )
            except Exception as e:
                print(f"Warning: Failed to save context: {e}")
            return response
        
        try:
            response = self.chain.run(input=user_input)
            # Note: save_context is automatically called by LangChain's ConversationChain
            return response
        except Exception as e:
            error_msg = str(e)
            # Provide more helpful error messages
            if "404" in error_msg:
                error_response = (
                    "I apologize, but I encountered an API error (404). "
                    "This may be due to incorrect API endpoint configuration. "
                    "However, your conversation has been saved to the database. "
                    "Please check your LLM API configuration."
                )
            else:
                error_response = f"I apologize, but I encountered an error: {error_msg}. Please try again."
            
            # Always try to save the error context to PowerMem
            # This ensures conversations are preserved even when LLM fails
            try:
                self.langchain_memory.save_context(
                    inputs={'input': user_input},
                    outputs={'output': error_response}
                )
                # Print confirmation that memory was saved despite LLM error
                print(f"  ✓ Conversation saved to PowerMem despite LLM error")
            except Exception as save_error:
                print(f"Warning: Failed to save error context: {save_error}")
            return error_response
    
    def get_patient_summary(self) -> Dict[str, Any]:
        """
        Get a summary of the patient's stored information.
        
        Returns:
            Dictionary containing patient summary
        """
        try:
            # Get all memories for this patient
            all_memories = self.memory.get_all(user_id=self.patient_id)
            memories = all_memories.get('results', [])
            
            # Search for specific types of information
            symptoms = self.memory.search("symptoms pain discomfort", user_id=self.patient_id, limit=10)
            medications = self.memory.search("medication medicine prescription", user_id=self.patient_id, limit=10)
            history = self.memory.search("medical history condition diagnosis", user_id=self.patient_id, limit=10)
            
            return {
                "total_memories": len(memories),
                "symptom_mentions": len(symptoms.get('results', [])),
                "medication_mentions": len(medications.get('results', [])),
                "history_mentions": len(history.get('results', [])),
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
    """Demonstrate a healthcare support conversation."""
    print("=" * 80)
    print("AI Healthcare Support Bot Demo")
    print("Database: OceanBase")
    print("=" * 80)
    print()
    
    # Initialize bot for a patient
    patient_id = "patient_alice_001"
    bot = HealthcareSupportBot(patient_id=patient_id)
    
    print(f"Bot initialized for patient: {patient_id}")
    print("Starting conversation...\n")
    print("-" * 80)
    
    # Simulate a conversation
    conversation_turns = [
        {
            "user": "Hello, I'm Alice. I've been experiencing headaches for the past few days.",
            "description": "Patient introduces herself and mentions symptoms"
        },
        {
            "user": "The headaches usually happen in the afternoon, and they're moderate in intensity.",
            "description": "Patient provides more symptom details"
        },
        {
            "user": "I'm currently taking ibuprofen 200mg twice daily for the pain.",
            "description": "Patient mentions current medication"
        },
        {
            "user": "I have a history of migraines, but these feel different. Should I be concerned?",
            "description": "Patient shares medical history and asks for guidance"
        },
        {
            "user": "What are some general tips for managing headaches?",
            "description": "Patient asks for general health information"
        },
        {
            "user": "I also want to schedule a follow-up appointment. Can you help?",
            "description": "Patient requests appointment assistance"
        }
    ]
    
    for i, turn in enumerate(conversation_turns, 1):
        print(f"\n[Turn {i}] {turn['description']}")
        print(f"Patient: {turn['user']}")
        print()
        
        response = bot.chat(turn['user'])
        print(f"Bot: {response}")
        print("-" * 80)
    
    # Show patient summary
    print("\n" + "=" * 80)
    print("Patient Information Summary")
    print("=" * 80)
    summary = bot.get_patient_summary()
    print(f"Total memories stored: {summary.get('total_memories', 0)}")
    print(f"Symptom mentions: {summary.get('symptom_mentions', 0)}")
    print(f"Medication mentions: {summary.get('medication_mentions', 0)}")
    print(f"History mentions: {summary.get('history_mentions', 0)}")
    print("\nRecent memories:")
    for mem in summary.get('recent_memories', [])[:3]:
        print(f"  - {mem.get('memory', '')[:100]}...")
    
    print("\n" + "=" * 80)
    print("Demo completed!")
    print("=" * 80)


def interactive_mode():
    """Run the bot in interactive mode for user input."""
    print("=" * 80)
    print("AI Healthcare Support Bot - Interactive Mode")
    print("Database: OceanBase")
    print("=" * 80)
    print("Type 'quit' or 'exit' to end the conversation")
    print("Type 'summary' to see your patient information summary")
    print("-" * 80)
    print()
    
    # Get patient ID
    patient_id = input("Enter patient ID (or press Enter for default): ").strip()
    if not patient_id:
        patient_id = "patient_interactive_001"
    
    # Initialize bot
    bot = HealthcareSupportBot(patient_id=patient_id)
    print(f"\nBot initialized for patient: {patient_id}")
    print("How can I help you today?\n")
    
    # Conversation loop
    while True:
        user_input = input("You: ").strip()
        
        if not user_input:
            continue
        
        if user_input.lower() in ['quit', 'exit', 'q']:
            print("\nThank you for using the Healthcare Support Bot. Take care!")
            break
        
        if user_input.lower() == 'summary':
            summary = bot.get_patient_summary()
            print("\n--- Patient Summary ---")
            print(f"Total memories: {summary.get('total_memories', 0)}")
            print(f"Symptom mentions: {summary.get('symptom_mentions', 0)}")
            print(f"Medication mentions: {summary.get('medication_mentions', 0)}")
            print(f"History mentions: {summary.get('history_mentions', 0)}")
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
    """Main function to run the healthcare support bot."""
    import argparse
    
    parser = argparse.ArgumentParser(description="AI Healthcare Support Bot with PowerMem + LangChain (OceanBase)")
    parser.add_argument(
        '--mode',
        choices=['demo', 'interactive'],
        default='demo',
        help='Run mode: demo (predefined conversation) or interactive (user input)'
    )
    parser.add_argument(
        '--patient-id',
        type=str,
        default=None,
        help='Patient ID for the conversation'
    )
    
    args = parser.parse_args()
    
    if args.mode == 'demo':
        demo_conversation()
    elif args.mode == 'interactive':
        interactive_mode()
    else:
        print(f"Unknown mode: {args.mode}")
        print("Use --mode demo or --mode interactive")


if __name__ == "__main__":
    main()

