import json
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor

import requests
from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()

class MemoryADD:
    def __init__(self, data_path=None, batch_size=2, is_graph=False):
        api_base_url = os.getenv("API_BASE_URL")
        if api_base_url:
            self.api_base_url = api_base_url
        else:
            raise ValueError("api_base_url is not set")
        self.batch_size = batch_size
        self.data_path = data_path
        self.data = None
        self.is_graph = is_graph
        self.server_execution_time = 0.0  # Track server-side execution time
        self.request_count = 0  # Track request count
        self.request_times = []  # Record latency for each request
        self._lock = threading.Lock()  # Thread lock to protect shared data
        if data_path:
            self.load_data()

    def load_data(self):
        with open(self.data_path, "r") as f:
            self.data = json.load(f)
        return self.data

    def add_memory(self, user_id, message, metadata, retries=3):
        for attempt in range(retries):
            try:
                payload = {
                    "messages": message,
                    "user_id": user_id,
                    "metadata": metadata or {}
                }
                # Record server-side request time
                start_time = time.time()
                response = requests.post(
                    f"{self.api_base_url}/memories",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                end_time = time.time()
                
                if response.status_code == 200:
                    # Accumulate server-side execution time and request count
                    request_time = end_time - start_time
                    with self._lock:
                        self.server_execution_time += request_time
                        self.request_count += 1
                        self.request_times.append(request_time)
                    return response.json()
                else:
                    raise Exception(f"API call failed with status {response.status_code}: {response.text}")
            except Exception as e:
                if attempt < retries - 1:
                    time.sleep(1)  # Wait before retrying
                    continue
                else:
                    raise e

    def add_memories_for_speaker(self, speaker, messages, timestamp):
        for i in range(0, len(messages), self.batch_size):
            batch_messages = messages[i: i + self.batch_size]
            self.add_memory(speaker, batch_messages, metadata={"timestamp": timestamp})

    def process_conversation(self, item, idx, key):
        conversation = item["conversation"]
        speaker_a = conversation["speaker_a"]
        speaker_b = conversation["speaker_b"]

        speaker_a_user_id = f"{speaker_a}_{idx}"
        speaker_b_user_id = f"{speaker_b}_{idx}"

        date_time_key = key + "_date_time"
        timestamp = conversation[date_time_key]
        chats = conversation[key]

        messages = []
        messages_reverse = []
        for chat in chats:
            if chat["speaker"] == speaker_a:
                messages.append({"role": "user", "content": f"{speaker_a}: {chat['text']}"})
                messages_reverse.append({"role": "assistant", "content": f"{speaker_a}: {chat['text']}"})
            elif chat["speaker"] == speaker_b:
                messages.append({"role": "assistant", "content": f"{speaker_b}: {chat['text']}"})
                messages_reverse.append({"role": "user", "content": f"{speaker_b}: {chat['text']}"})
            else:
                raise ValueError(f"Unknown speaker: {chat['speaker']}")

        # add memories for the two users on different threads
        self.add_memories_for_speaker(speaker_a_user_id, messages, timestamp)
        self.add_memories_for_speaker(speaker_b_user_id, messages_reverse, timestamp)

    def process_all_conversations(self, max_workers=10):
        if not self.data:
            raise ValueError("No data loaded. Please set data_path and call load_data() first.")

        # First delete all user memories
        print("Deleting existing memories...")
        for idx, item in enumerate(self.data):
            conversation = item["conversation"]
            speaker_a = conversation["speaker_a"]
            speaker_b = conversation["speaker_b"]
            speaker_a_user_id = f"{speaker_a}_{idx}"
            speaker_b_user_id = f"{speaker_b}_{idx}"
            
            self.delete_all_memories(speaker_a_user_id)
            self.delete_all_memories(speaker_b_user_id)

        # Collect all tasks that need to be processed
        all_tasks = []
        for idx, item in enumerate(self.data):
            conversation = item["conversation"]
            
            # Create tasks for each key of each conversation
            for key in conversation.keys():
                if key in ["speaker_a", "speaker_b"] or "date" in key or "timestamp" in key:
                    continue
                all_tasks.append((item, idx, key))

        print(f"Processing {len(all_tasks)} tasks with {max_workers} workers...")
        
        # Use a single ThreadPoolExecutor to process all tasks
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(self.process_conversation, item, idx, key) 
                     for item, idx, key in all_tasks]

            for future in tqdm(futures, desc="Processing conversations", total=len(futures)):
                future.result()

    def delete_all_memories(self, user_id, retries=3):
        for attempt in range(retries):
            try:
                response = requests.delete(
                    f"{self.api_base_url}/memories",
                    params={"user_id": user_id},
                )

                if response.status_code == 200:
                    return response.json()
                else:
                    raise Exception(f"Delete API call failed with status {response.status_code}: {response.text}")

            except Exception as e:
                if attempt < retries - 1:
                    time.sleep(1)
                    continue
                else:
                    raise e
