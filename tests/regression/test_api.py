#!/usr/bin/env python3
"""
powermem 0.3.0 API Server Basic Functionality Test Script

Tests basic functionality of all API endpoints, including:
- System endpoints
- Memory management endpoints
- Search endpoints
- User profile endpoints
- Agent management endpoints
"""

import requests
import json
import time
import os
import subprocess
from typing import Dict, Any, Optional
from datetime import datetime


class APITester:
    """API Test Class"""
    
    def __init__(self, base_url: str = "http://localhost:8000", api_key: str = "key1"):
        """
        Initialize tester
        
        Args:
            base_url: API server base URL
            api_key: API key
        """
        self.base_url = base_url.rstrip('/')
        self.api_base = f"{self.base_url}/api/v1"
        self.api_key = api_key
        self.headers = {
            "X-API-Key": api_key,
            "Content-Type": "application/json"
        }
        self.headers_without_auth = {
            "Content-Type": "application/json"
        }
        self.test_results = {
            "passed": 0,
            "failed": 0,
            "total": 0,
            "details": []
        }
        # Store data IDs created during testing for subsequent tests
        self.test_data = {
            "memory_ids": [],
            "user_id": "test-user-123",
            "agent_id": "test-agent-456",
            "run_id": "test-run-789"
        }
    
    def print_response(self, response: requests.Response, test_name: str = ""):
        """
        Print response content
        
        Args:
            response: Response object
            test_name: Test name (optional)
        """
        print(f"\n{'─' * 60}")
        if test_name:
            print(f"Response for: {test_name}")
        print(f"Status Code: {response.status_code}")
        print(f"Response Headers:")
        for key, value in response.headers.items():
            print(f"  {key}: {value}")
        
        print(f"\nResponse Body:")
        try:
            result = response.json()
            print(json.dumps(result, indent=2, ensure_ascii=False))
        except json.JSONDecodeError:
            # If not JSON, output text content (limited length)
            text = response.text
            if len(text) > 1000:
                print(text[:1000] + f"\n... (truncated, total length: {len(text)} characters)")
            else:
                print(text)
        except Exception as e:
            print(f"Error parsing response: {e}")
            print(f"Raw text: {response.text[:500]}")
        print(f"{'─' * 60}\n")
    
    def log_result(self, test_name: str, passed: bool, message: str = "", response: Optional[Dict] = None):
        """
        Log test result
        
        Args:
            test_name: Test name
            passed: Whether passed
            message: Test message
            response: Response data
        """
        self.test_results["total"] += 1
        if passed:
            self.test_results["passed"] += 1
            status = "✓ PASS"
        else:
            self.test_results["failed"] += 1
            status = "✗ FAIL"
        
        result = {
            "test": test_name,
            "status": status,
            "message": message,
            "response": response
        }
        self.test_results["details"].append(result)
        print(f"{status}: {test_name} - {message}")
    
    def make_request(self, method: str, endpoint: str, data: Optional[Dict] = None, 
                     headers: Optional[Dict] = None, params: Optional[Dict] = None, 
                     print_response: bool = True) -> requests.Response:
        """
        Send HTTP request (with API Key)
        
        Args:
            method: HTTP method
            endpoint: API endpoint
            data: Request body data
            headers: Request headers
            params: URL parameters
            print_response: Whether to print response content (default True)
            
        Returns:
            Response object
        """
        url = f"{self.api_base}{endpoint}"
        request_headers = self.headers.copy()
        if headers:
            request_headers.update(headers)
        
        try:
            if method.upper() == "GET":
                response = requests.get(url, headers=request_headers, params=params, timeout=40)
            elif method.upper() == "POST":
                response = requests.post(url, headers=request_headers, json=data, params=params, timeout=40)
            elif method.upper() == "PUT":
                response = requests.put(url, headers=request_headers, json=data, params=params, timeout=40)
            elif method.upper() == "DELETE":
                response = requests.delete(url, headers=request_headers, json=data, params=params, timeout=40)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            # Print response content
            if print_response:
                self.print_response(response, f"{method.upper()} {endpoint}")
            
            return response
        except requests.exceptions.RequestException as e:
            print(f"Request error: {e}")
            raise
    
    def check_response_without_auth(self, response: requests.Response, test_name: str, 
                                     success_check_func=None):
        """
        Check response without API Key
        
        Args:
            response: Response object
            test_name: Test name
            success_check_func: Optional function to check 200 response content (when auth is disabled)
        """
        expect_auth = getattr(self, 'expect_auth_required', True)
        
        if expect_auth:
            # Expect 401
            if response.status_code == 401:
                self.log_result(test_name, True, "Returned 401 unauthorized (as expected)")
            else:
                self.log_result(test_name, False, f"Should return 401, actually returned: {response.status_code}")
        else:
            # Expect 200 (auth disabled)
            # 422 is always considered failure (validation error), regardless of auth status
            if response.status_code == 200:
                if success_check_func:
                    try:
                        result = response.json()
                        if success_check_func(result):
                            self.log_result(test_name, True, "Returned 200 (auth disabled, as expected)", result)
                        else:
                            self.log_result(test_name, False, f"Returned 200 but response format incorrect: {result}")
                    except:
                        self.log_result(test_name, True, "Returned 200 (auth disabled, as expected)")
                else:
                    self.log_result(test_name, True, "Returned 200 (auth disabled, as expected)")
            elif response.status_code == 422:
                # 422 is validation error - always considered failure
                self.log_result(test_name, False, f"Returned 422 validation error (unexpected failure)")
            elif response.status_code == 500:
                # 500 could be server error or auth middleware issue
                try:
                    error_text = response.text[:200]  # Only take first 200 characters
                    self.log_result(test_name, False, f"Returned 500 server error: {error_text}")
                except:
                    self.log_result(test_name, False, f"Returned 500 server error")
            else:
                self.log_result(test_name, False, f"Should return 200 (auth disabled), actually returned: {response.status_code}")
    
    def make_request_without_auth(self, method: str, endpoint: str, data: Optional[Dict] = None, 
                                  headers: Optional[Dict] = None, params: Optional[Dict] = None,
                                  print_response: bool = True) -> requests.Response:
        """
        Send HTTP request (without API Key)
        
        Args:
            method: HTTP method
            endpoint: API endpoint
            data: Request body data
            headers: Request headers
            params: URL parameters
            print_response: Whether to print response content (default True)
            
        Returns:
            Response object
        """
        url = f"{self.api_base}{endpoint}"
        request_headers = self.headers_without_auth.copy()
        if headers:
            request_headers.update(headers)
        
        try:
            if method.upper() == "GET":
                response = requests.get(url, headers=request_headers, params=params, timeout=40)
            elif method.upper() == "POST":
                response = requests.post(url, headers=request_headers, json=data, params=params, timeout=40)
            elif method.upper() == "PUT":
                response = requests.put(url, headers=request_headers, json=data, params=params, timeout=40)
            elif method.upper() == "DELETE":
                response = requests.delete(url, headers=request_headers, json=data, params=params, timeout=40)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            # Print response content
            if print_response:
                self.print_response(response, f"{method.upper()} {endpoint} (without auth)")
            
            return response
        except requests.exceptions.RequestException as e:
            print(f"Request error: {e}")
            raise
    
    # ==================== Module 1: System Endpoints ====================
    
    def test_health_check_with_auth(self):
        """Test health check endpoint (with API Key)"""
        print("\n=== Testing Health Check Endpoint (with API Key) ===")
        
        try:
            response = self.make_request("GET", "/system/health")
            if response.status_code == 200:
                data = response.json()
                if data.get("success") and data.get("data", {}).get("status") == "healthy":
                    self.log_result("Health Check-with API Key", True, "Returned 200, status is healthy", data)
                else:
                    self.log_result("Health Check-with API Key", False, f"Response format incorrect: {data}")
            else:
                self.log_result("Health Check-with API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("Health Check-with API Key", False, f"Exception: {str(e)}")
    
    def test_health_check_without_auth(self):
        """Test health check endpoint (without API Key)"""
        print("\n=== Testing Health Check Endpoint (without API Key) ===")
        
        try:
            response = requests.get(f"{self.api_base}/system/health", timeout=10)
            self.print_response(response, "Health Check-without API Key")
            if response.status_code == 200:
                data = response.json()
                if data.get("success") and data.get("data", {}).get("status") == "healthy":
                    self.log_result("Health Check-without API Key", True, "Returned 200, status is healthy", data)
                else:
                    self.log_result("Health Check-without API Key", False, f"Response format incorrect: {data}")
            else:
                self.log_result("Health Check-without API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("Health Check-without API Key", False, f"Exception: {str(e)}")
    
    def test_system_status_with_auth(self):
        """Test system status endpoint (with API Key)"""
        print("\n=== Testing System Status Endpoint (with API Key) ===")
        
        try:
            response = self.make_request("GET", "/system/status")
            if response.status_code == 200:
                data = response.json()
                if data.get("success") and "data" in data:
                    self.log_result("System Status-with API Key", True, "Returned 200, contains system info", data)
                else:
                    self.log_result("System Status-with API Key", False, f"Response format incorrect: {data}")
            else:
                self.log_result("System Status-with API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("System Status-with API Key", False, f"Exception: {str(e)}")
    
    def test_system_status_without_auth(self):
        """Test system status endpoint (without API Key)"""
        print("\n=== Testing System Status Endpoint (without API Key) ===")
        
        try:
            response = self.make_request_without_auth("GET", "/system/status")
            self.check_response_without_auth(
                response, 
                "System Status-without API Key",
                success_check_func=lambda r: r.get("success") and "data" in r
            )
        except Exception as e:
            self.log_result("System Status-without API Key", False, f"Exception: {str(e)}")
    
    def test_system_metrics_with_auth(self):
        """Test system metrics endpoint (with API Key)"""
        print("\n=== Testing System Metrics Endpoint (with API Key) ===")
        
        try:
            response = self.make_request("GET", "/system/metrics")
            if response.status_code == 200:
                content = response.text
                if "powermem_api_requests_total" in content:
                    self.log_result("System Metrics-with API Key", True, "Returned 200, Prometheus format metrics")
                else:
                    self.log_result("System Metrics-with API Key", False, "Expected metrics not found in response")
            else:
                self.log_result("System Metrics-with API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("System Metrics-with API Key", False, f"Exception: {str(e)}")
    
    def test_system_metrics_without_auth(self):
        """Test system metrics endpoint (without API Key)"""
        print("\n=== Testing System Metrics Endpoint (without API Key) ===")
        
        try:
            response = self.make_request_without_auth("GET", "/system/metrics")
            self.check_response_without_auth(
                response,
                "System Metrics-without API Key",
                success_check_func=lambda r: "powermem_api_requests_total" in response.text if hasattr(response, 'text') else True
            )
        except Exception as e:
            self.log_result("System Metrics-without API Key", False, f"Exception: {str(e)}")
    
    def test_system_delete_all_memories_with_auth(self):
        """Test delete all memories endpoint (with API Key)"""
        print("\n=== Testing Delete All Memories Endpoint (with API Key) ===")
        
        # Test 1: Delete all memories
        try:
            response = self.make_request("DELETE", "/system/delete-all-memories")
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    self.log_result("Delete All Memories-All-with API Key", True, "Returned 200, deletion successful", data)
                else:
                    self.log_result("Delete All Memories-All-with API Key", False, f"Response format incorrect: {data}")
            else:
                self.log_result("Delete All Memories-All-with API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("Delete All Memories-All-with API Key", False, f"Exception: {str(e)}")
        
        # Test 2: Delete all memories for specific agent
        try:
            params = {"agent_id": self.test_data["agent_id"]}
            response = self.make_request("DELETE", "/system/delete-all-memories", params=params)
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    self.log_result("Delete All Memories-By Agent-with API Key", True, "Returned 200, deletion successful", data)
                else:
                    self.log_result("Delete All Memories-By Agent-with API Key", False, f"Response format incorrect: {data}")
            else:
                self.log_result("Delete All Memories-By Agent-with API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("Delete All Memories-By Agent-with API Key", False, f"Exception: {str(e)}")
        
        # Test 3: Delete all memories for specific user
        try:
            params = {"user_id": self.test_data["user_id"]}
            response = self.make_request("DELETE", "/system/delete-all-memories", params=params)
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    self.log_result("Delete All Memories-By User-with API Key", True, "Returned 200, deletion successful", data)
                else:
                    self.log_result("Delete All Memories-By User-with API Key", False, f"Response format incorrect: {data}")
            else:
                self.log_result("Delete All Memories-By User-with API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("Delete All Memories-By User-with API Key", False, f"Exception: {str(e)}")
    
    def test_system_delete_all_memories_without_auth(self):
        """Test delete all memories endpoint (without API Key)"""
        print("\n=== Testing Delete All Memories Endpoint (without API Key) ===")
        
        # Test 1: Delete all memories
        try:
            response = self.make_request_without_auth("DELETE", "/system/delete-all-memories")
            self.check_response_without_auth(
                response,
                "Delete All Memories-All-without API Key",
                success_check_func=lambda r: r.get("success") if isinstance(r, dict) else True
            )
        except Exception as e:
            self.log_result("Delete All Memories-All-without API Key", False, f"Exception: {str(e)}")
        
        # Test 2: Delete all memories for specific agent
        try:
            params = {"agent_id": self.test_data["agent_id"]}
            response = self.make_request_without_auth("DELETE", "/system/delete-all-memories", params=params)
            self.check_response_without_auth(
                response,
                "Delete All Memories-By Agent-without API Key",
                success_check_func=lambda r: r.get("success") if isinstance(r, dict) else True
            )
        except Exception as e:
            self.log_result("Delete All Memories-By Agent-without API Key", False, f"Exception: {str(e)}")
        
        # Test 3: Delete all memories for specific user
        try:
            params = {"user_id": self.test_data["user_id"]}
            response = self.make_request_without_auth("DELETE", "/system/delete-all-memories", params=params)
            self.check_response_without_auth(
                response,
                "Delete All Memories-By User-without API Key",
                success_check_func=lambda r: r.get("success") if isinstance(r, dict) else True
            )
        except Exception as e:
            self.log_result("Delete All Memories-By User-without API Key", False, f"Exception: {str(e)}")
    
    # ==================== Module 2: Memory Management Endpoints ====================
    
    def test_create_memory_with_auth(self):
        """Test create memory endpoint (with API Key)"""
        print("\n=== Testing Create Memory Endpoint (with API Key) ===")
        
        # Test 1: Minimum parameters (content only)
        try:
            data = {
                "content": "user123 likes sleep."
            }
            response = self.make_request("POST", "/memories", data=data)
            if response.status_code == 200:
                result = response.json()
                if result.get("success") and "data" in result:
                    memories = result.get("data", [])
                    if isinstance(memories, list):
                        # API may return empty array if no memories were created (duplicates or no facts extracted)
                        # This is still a valid 200 response
                        for mem in memories:
                            if "memory_id" in mem:
                                self.test_data["memory_ids"].append(mem["memory_id"])
                        if len(memories) > 0:
                            self.log_result("Create Memory-Min Params-with API Key", True, f"Created successfully, returned {len(memories)} memories", result)
                        else:
                            self.log_result("Create Memory-Min Params-with API Key", True, "Returned 200 with empty array (no memories created, valid response)", result)
                    else:
                        self.log_result("Create Memory-Min Params-with API Key", False, "Response data is not a list")
                else:
                    self.log_result("Create Memory-Min Params-with API Key", False, f"Response format incorrect: {result}")
            else:
                self.log_result("Create Memory-Min Params-with API Key", False, f"Returned status code: {response.status_code}, response: {response.text}")
        except Exception as e:
            self.log_result("Create Memory-Min Params-with API Key", False, f"Exception: {str(e)}")
        
        # Test 2: Full parameters
        try:
            data = {
                "content": "User likes coffee and likes to drink coffee in the morning.",
                "user_id": self.test_data["user_id"],
                "agent_id": self.test_data["agent_id"],
                "run_id": self.test_data["run_id"],
                "metadata": {
                    "source": "conversation",
                    "importance": "high"
                },
                "filters": {
                    "category": "preference",
                    "topic": "beverage"
                },
                "scope": "user",
                "memory_type": "preference",
                "infer": True
            }
            response = self.make_request("POST", "/memories", data=data)
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    memories = result.get("data", [])
                    for mem in memories:
                        if "memory_id" in mem:
                            self.test_data["memory_ids"].append(mem["memory_id"])
                    self.log_result("Create Memory-Full Params-with API Key", True, f"Created successfully, returned {len(memories)} memories", result)
                else:
                    self.log_result("Create Memory-Full Params-with API Key", False, f"Response format incorrect: {result}")
            else:
                self.log_result("Create Memory-Full Params-with API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("Create Memory-Full Params-with API Key", False, f"Exception: {str(e)}")
        
        # Test 3: No content (should fail)
        try:
            data = {
                "user_id": self.test_data["user_id"]
            }
            response = self.make_request("POST", "/memories", data=data)
            if response.status_code == 422:
                self.log_result("Create Memory-No Content-with API Key", True, "Returned 422 validation error (as expected)")
            else:
                self.log_result("Create Memory-No Content-with API Key", False, f"Should return 422, actually returned: {response.status_code}")
        except Exception as e:
            self.log_result("Create Memory-No Content-with API Key", False, f"Exception: {str(e)}")
    
    def test_create_memory_without_auth(self):
        """Test create memory endpoint (without API Key)"""
        print("\n=== Testing Create Memory Endpoint (without API Key) ===")
        
        # Test 1: Minimum parameters (content only)
        try:
            data = {
                "content": "User likes coffee."
            }
            response = self.make_request_without_auth("POST", "/memories", data=data)
            if response.status_code == 200:
                result = response.json()
                if result.get("success") and "data" in result:
                    memories = result.get("data", [])
                    if isinstance(memories, list):
                        # API may return empty array if no memories were created (duplicates or no facts extracted)
                        # This is still a valid 200 response
                        for mem in memories:
                            if "memory_id" in mem:
                                if mem["memory_id"] not in self.test_data["memory_ids"]:
                                    self.test_data["memory_ids"].append(mem["memory_id"])
                        self.check_response_without_auth(
                            response,
                            "Create Memory-Min Params-without API Key",
                            success_check_func=lambda r: r.get("success") and "data" in r if isinstance(r, dict) else True
                        )
                    else:
                        self.log_result("Create Memory-Min Params-without API Key", False, "Response data is not a list")
                else:
                    self.check_response_without_auth(
                        response,
                        "Create Memory-Min Params-without API Key",
                        success_check_func=lambda r: r.get("success") and "data" in r if isinstance(r, dict) else True
                    )
            else:
                self.check_response_without_auth(
                    response,
                    "Create Memory-Min Params-without API Key",
                    success_check_func=lambda r: r.get("success") and "data" in r if isinstance(r, dict) else True
                )
        except Exception as e:
            self.log_result("Create Memory-Min Params-without API Key", False, f"Exception: {str(e)}")
        
        # Test 2: Full parameters
        try:
            data = {
                "content": "User likes coffee and likes to drink coffee in the morning.",
                "user_id": self.test_data["user_id"],
                "agent_id": self.test_data["agent_id"],
                "run_id": self.test_data["run_id"],
                "metadata": {
                    "source": "conversation",
                    "importance": "high"
                },
                "filters": {
                    "category": "preference",
                    "topic": "beverage"
                },
                "scope": "user",
                "memory_type": "preference",
                "infer": True
            }
            response = self.make_request_without_auth("POST", "/memories", data=data)
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    memories = result.get("data", [])
                    for mem in memories:
                        if "memory_id" in mem:
                            if mem["memory_id"] not in self.test_data["memory_ids"]:
                                self.test_data["memory_ids"].append(mem["memory_id"])
                    self.check_response_without_auth(
                        response,
                        "Create Memory-Full Params-without API Key",
                        success_check_func=lambda r: r.get("success") if isinstance(r, dict) else True
                    )
                else:
                    self.check_response_without_auth(
                        response,
                        "Create Memory-Full Params-without API Key",
                        success_check_func=lambda r: r.get("success") if isinstance(r, dict) else True
                    )
            else:
                self.check_response_without_auth(
                    response,
                    "Create Memory-Full Params-without API Key",
                    success_check_func=lambda r: r.get("success") if isinstance(r, dict) else True
                )
        except Exception as e:
            self.log_result("Create Memory-Full Params-without API Key", False, f"Exception: {str(e)}")
        
        # Test 3: No content (should return 422 validation error)
        try:
            data = {
                "user_id": self.test_data["user_id"]
            }
            response = self.make_request_without_auth("POST", "/memories", data=data)
            # 422 is expected for missing required field - this is the correct validation behavior
            if response.status_code == 422:
                self.log_result("Create Memory-No Content-without API Key", True, "Returned 422 validation error (as expected)")
            else:
                self.log_result("Create Memory-No Content-without API Key", False, f"Should return 422 for missing content, actually returned: {response.status_code}")
        except Exception as e:
            self.log_result("Create Memory-No Content-without API Key", False, f"Exception: {str(e)}")
    
    def test_batch_create_memories_with_auth(self):
        """Test batch create memories endpoint (with API Key)"""
        print("\n=== Testing Batch Create Memories Endpoint (with API Key) ===")
        
        try:
            data = {
                "memories": [
                    {
                        "content": "User likes Python programming",
                        "metadata": {"topic": "programming"},
                        "filters": {"category": "skill"},
                        "scope": "user",
                        "memory_type": "skill"
                    },
                    {
                        "content": "User lives in Beijing",
                        "metadata": {"topic": "location"},
                        "filters": {"category": "personal"},
                        "scope": "user",
                        "memory_type": "fact"
                    }
                ],
                "user_id": self.test_data["user_id"],
                "agent_id": self.test_data["agent_id"],
                "run_id": self.test_data["run_id"],
                "infer": True
            }
            response = self.make_request("POST", "/memories/batch", data=data)
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    batch_data = result.get("data", {})
                    memories = batch_data.get("memories", [])
                    for mem in memories:
                        if "memory_id" in mem:
                            self.test_data["memory_ids"].append(mem["memory_id"])
                    created_count = batch_data.get("created_count", 0)
                    self.log_result("Batch Create Memories-with API Key", True, 
                                  f"Created successfully, created_count={created_count}", result)
                else:
                    self.log_result("Batch Create Memories-with API Key", False, f"Response format incorrect: {result}")
            else:
                self.log_result("Batch Create Memories-with API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("Batch Create Memories-with API Key", False, f"Exception: {str(e)}")
    
    def test_batch_create_memories_without_auth(self):
        """Test batch create memories endpoint (without API Key)"""
        print("\n=== Testing Batch Create Memories Endpoint (without API Key) ===")
        
        try:
            data = {
                "memories": [
                    {
                        "content": "User likes Python programming",
                        "metadata": {"topic": "programming"},
                        "filters": {"category": "skill"},
                        "scope": "user",
                        "memory_type": "skill"
                    },
                    {
                        "content": "User lives in Beijing",
                        "metadata": {"topic": "location"},
                        "filters": {"category": "personal"},
                        "scope": "user",
                        "memory_type": "fact"
                    }
                ],
                "user_id": self.test_data["user_id"],
                "agent_id": self.test_data["agent_id"],
                "run_id": self.test_data["run_id"],
                "infer": True
            }
            response = self.make_request_without_auth("POST", "/memories/batch", data=data)
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    batch_data = result.get("data", {})
                    memories = batch_data.get("memories", [])
                    for mem in memories:
                        if "memory_id" in mem:
                            if mem["memory_id"] not in self.test_data["memory_ids"]:
                                self.test_data["memory_ids"].append(mem["memory_id"])
            self.check_response_without_auth(
                response,
                "Batch Create Memories-without API Key",
                success_check_func=lambda r: r.get("success") and "data" in r if isinstance(r, dict) else True
            )
        except Exception as e:
            self.log_result("Batch Create Memories-without API Key", False, f"Exception: {str(e)}")
    
    def test_list_memories_with_auth(self):
        """Test list memories endpoint (with API Key)"""
        print("\n=== Testing List Memories Endpoint (with API Key) ===")
        
        # Test 1: Default pagination
        try:
            response = self.make_request("GET", "/memories")
            if response.status_code == 200:
                result = response.json()
                if result.get("success") and "data" in result:
                    data = result["data"]
                    total = data.get("total", 0)
                    self.log_result("List Memories-Default Pagination-with API Key", True, f"Returned successfully, total={total}", result)
                else:
                    self.log_result("List Memories-Default Pagination-with API Key", False, f"Response format incorrect: {result}")
            else:
                self.log_result("List Memories-Default Pagination-with API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("List Memories-Default Pagination-with API Key", False, f"Exception: {str(e)}")
        
        # Test 2: Custom pagination
        try:
            params = {"limit": 10, "offset": 0}
            response = self.make_request("GET", "/memories", params=params)
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    data = result["data"]
                    limit = data.get("limit", 0)
                    if limit == 10:
                        self.log_result("List Memories-Custom Pagination-with API Key", True, f"Pagination parameters effective, limit={limit}", result)
                    else:
                        self.log_result("List Memories-Custom Pagination-with API Key", False, f"Pagination parameters not effective, limit={limit}")
                else:
                    self.log_result("List Memories-Custom Pagination-with API Key", False, f"Response format incorrect: {result}")
            else:
                self.log_result("List Memories-Custom Pagination-with API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("List Memories-Custom Pagination-with API Key", False, f"Exception: {str(e)}")
        
        # Test 3: Filter by user
        try:
            params = {"user_id": self.test_data["user_id"], "limit": 20, "offset": 0}
            response = self.make_request("GET", "/memories", params=params)
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    data = result.get("data", {})
                    # Extract memory_id (take first from returned memory list for subsequent single memory test)
                    try:
                        if isinstance(data, dict) and "memories" in data:
                            memories = data.get("memories", [])
                            if memories and len(memories) > 0:
                                memory_id = memories[0].get("memory_id")
                                if memory_id:
                                    # Save to separate variable for single memory test
                                    self.test_data["filtered_memory_id"] = memory_id
                                    print(f"Extracted memory_id (filtered by user): {memory_id}")
                    except Exception as e:
                        print(f"Error extracting memory_id: {e}")
                    self.log_result("List Memories-Filter by User-with API Key", True, "Filter successful", result)
                else:
                    self.log_result("List Memories-Filter by User-with API Key", False, f"Response format incorrect: {result}")
            else:
                self.log_result("List Memories-Filter by User-with API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("List Memories-Filter by User-with API Key", False, f"Exception: {str(e)}")
    
    def test_list_memories_without_auth(self):
        """Test list memories endpoint (without API Key)"""
        print("\n=== Testing List Memories Endpoint (without API Key) ===")
        
        # Test 1: Default pagination
        try:
            response = self.make_request_without_auth("GET", "/memories")
            if response.status_code == 200:
                result = response.json()
                if result.get("success") and "data" in result:
                    data = result["data"]
                    total = data.get("total", 0)
                    self.log_result("List Memories-Default Pagination-without API Key", True, f"Returned successfully, total={total}", result)
                    
                    # Extract memory_id
                    try:
                        memories = data.get("memories", []) or data.get("items", [])
                        if isinstance(data, list):
                            memories = data
                        
                        for mem in memories:
                            if isinstance(mem, dict) and "memory_id" in mem:
                                memory_id = mem["memory_id"]
                                if memory_id not in self.test_data["memory_ids"]:
                                    self.test_data["memory_ids"].append(memory_id)
                    except:
                        pass  # If parsing fails, ignore
                else:
                    self.check_response_without_auth(
                        response,
                        "List Memories-Default Pagination-without API Key",
                        success_check_func=lambda r: r.get("success") and "data" in r if isinstance(r, dict) else True
                    )
            else:
                self.check_response_without_auth(
                    response,
                    "List Memories-Default Pagination-without API Key",
                    success_check_func=lambda r: r.get("success") and "data" in r if isinstance(r, dict) else True
                )
        except Exception as e:
            self.log_result("List Memories-Default Pagination-without API Key", False, f"Exception: {str(e)}")
        
        # Test 2: Custom pagination
        try:
            params = {"limit": 10, "offset": 0}
            response = self.make_request_without_auth("GET", "/memories", params=params)
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    data = result["data"]
                    limit = data.get("limit", 0)
                    if limit == 10:
                        self.log_result("List Memories-Custom Pagination-without API Key", True, f"Pagination parameters effective, limit={limit}", result)
                    else:
                        self.log_result("List Memories-Custom Pagination-without API Key", False, f"Pagination parameters not effective, limit={limit}")
                else:
                    self.check_response_without_auth(
                        response,
                        "List Memories-Custom Pagination-without API Key",
                        success_check_func=lambda r: r.get("success") if isinstance(r, dict) else True
                    )
            else:
                self.check_response_without_auth(
                    response,
                    "List Memories-Custom Pagination-without API Key",
                    success_check_func=lambda r: r.get("success") if isinstance(r, dict) else True
                )
        except Exception as e:
            self.log_result("List Memories-Custom Pagination-without API Key", False, f"Exception: {str(e)}")
        
        # Test 3: Filter by user
        try:
            params = {"user_id": self.test_data["user_id"], "limit": 20, "offset": 0}
            response = self.make_request_without_auth("GET", "/memories", params=params)
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    data = result.get("data", {})
                    # Extract memory_id (take first from returned memory list for subsequent single memory test)
                    try:
                        if isinstance(data, dict) and "memories" in data:
                            memories = data.get("memories", [])
                            if memories and len(memories) > 0:
                                memory_id = memories[0].get("memory_id")
                                if memory_id:
                                    # Save to separate variable for single memory test
                                    self.test_data["filtered_memory_id"] = memory_id
                                    print(f"Extracted memory_id (filtered by user-without API Key): {memory_id}")
                    except Exception as e:
                        print(f"Error extracting memory_id: {e}")
                    self.log_result("List Memories-Filter by User-without API Key", True, "Filter successful", result)
                else:
                    self.check_response_without_auth(
                        response,
                        "List Memories-Filter by User-without API Key",
                        success_check_func=lambda r: r.get("success") if isinstance(r, dict) else True
                    )
            else:
                self.check_response_without_auth(
                    response,
                    "List Memories-Filter by User-without API Key",
                    success_check_func=lambda r: r.get("success") if isinstance(r, dict) else True
                )
        except Exception as e:
            self.log_result("List Memories-Filter by User-without API Key", False, f"Exception: {str(e)}")
    
    def test_get_memory_with_auth(self):
        """Test get single memory endpoint (with API Key)"""
        print("\n=== Testing Get Single Memory Endpoint (with API Key) ===")
        
        # Prefer using memory_id extracted from "filter by user" test
        memory_id = None
        if "filtered_memory_id" in self.test_data and self.test_data["filtered_memory_id"]:
            memory_id = self.test_data["filtered_memory_id"]
            print(f"Using memory_id extracted from filter by user test: {memory_id}")
        elif self.test_data["memory_ids"]:
            memory_id = self.test_data["memory_ids"][0]
            print(f"Using first memory_id from memory_ids list: {memory_id}")
        else:
            self.log_result("Get Single Memory-with API Key", False, "No available memory_id, skipping test")
            return
        
        try:
            params = {
                "user_id": self.test_data["user_id"],
                "agent_id": self.test_data["agent_id"]
            }
            response = self.make_request("GET", f"/memories/{memory_id}", params=params)
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    self.log_result("Get Single Memory-with API Key", True, f"Retrieved successfully, memory_id={memory_id}", result)
                else:
                    self.log_result("Get Single Memory-with API Key", False, f"Response format incorrect: {result}")
            else:
                self.log_result("Get Single Memory-with API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("Get Single Memory-with API Key", False, f"Exception: {str(e)}")
        
        # Test non-existent ID
        try:
            response = self.make_request("GET", "/memories/999999999999999999")
            if response.status_code == 404:
                self.log_result("Get Single Memory-Non-existent ID-with API Key", True, "Returned 404 (as expected)")
            else:
                self.log_result("Get Single Memory-Non-existent ID-with API Key", False, f"Should return 404, actually returned: {response.status_code}")
        except Exception as e:
            self.log_result("Get Single Memory-Non-existent ID-with API Key", False, f"Exception: {str(e)}")
    
    def test_get_memory_without_auth(self):
        """Test get single memory endpoint (without API Key)"""
        print("\n=== Testing Get Single Memory Endpoint (without API Key) ===")
        
        # Test 1: Use existing memory_id
        if not self.test_data["memory_ids"]:
            self.log_result("Get Single Memory-without API Key", False, "No available memory_id, skipping test")
        else:
            memory_id = self.test_data["memory_ids"][0]
            try:
                response = self.make_request_without_auth("GET", f"/memories/{memory_id}")
                self.check_response_without_auth(
                    response,
                    "Get Single Memory-without API Key",
                    success_check_func=lambda r: r.get("success") if isinstance(r, dict) else True
                )
            except Exception as e:
                self.log_result("Get Single Memory-without API Key", False, f"Exception: {str(e)}")
        
        # Test 2: Test non-existent ID
        try:
            response = self.make_request_without_auth("GET", "/memories/99999999999")
            if response.status_code == 404:
                self.log_result("Get Single Memory-Non-existent ID-without API Key", True, "Returned 404 (as expected)")
            else:
                self.log_result("Get Single Memory-Non-existent ID-without API Key", False, f"Should return 404, actually returned: {response.status_code}")
        except Exception as e:
            self.log_result("Get Single Memory-Non-existent ID-without API Key", False, f"Exception: {str(e)}")
    
    def test_update_memory_with_auth(self):
        """Test update memory endpoint (with API Key)"""
        print("\n=== Testing Update Memory Endpoint (with API Key) ===")
        
        # Prefer getting latest memory_id from server to ensure using existing ID
        memory_id = None
        try:
            params = {"user_id": self.test_data["user_id"], "limit": 1, "offset": 0}
            get_response = self.make_request("GET", "/memories", params=params, print_response=False)
            if get_response.status_code == 200:
                get_result = get_response.json()
                if get_result.get("success"):
                    data = get_result.get("data", {})
                    memories = data.get("memories", []) or data.get("items", [])
                    if isinstance(data, list):
                        memories = data
                    if memories and len(memories) > 0:
                        memory_id = memories[0].get("memory_id") or memories[0].get("id")
                        if memory_id and memory_id not in self.test_data["memory_ids"]:
                            self.test_data["memory_ids"].append(memory_id)
        except Exception as e:
            print(f"Error getting memories: {e}")
        
        # If getting from server failed, try using ID from list
        if not memory_id:
            if not self.test_data["memory_ids"]:
                self.log_result("Update Memory-with API Key", False, "No available memory_id, skipping test")
                return
            memory_id = self.test_data["memory_ids"][0]
        
        try:
            data = {
                "content": "User likes latte coffee.",
                "user_id": self.test_data["user_id"],
                "agent_id": self.test_data["agent_id"]
            }
            response = self.make_request("PUT", f"/memories/{memory_id}", data=data)
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    self.log_result("Update Memory-with API Key", True, f"Updated successfully, memory_id={memory_id}", result)
                else:
                    self.log_result("Update Memory-with API Key", False, f"Response format incorrect: {result}")
            elif response.status_code == 404:
                # If 404 returned, memory_id doesn't exist, remove from list and try creating new memory
                if memory_id in self.test_data["memory_ids"]:
                    self.test_data["memory_ids"].remove(memory_id)
                
                # Try creating a new memory for update
                try:
                    create_data = {
                        "content": "User likes coffee.",
                        "user_id": self.test_data["user_id"],
                        "agent_id": self.test_data["agent_id"]
                    }
                    create_response = self.make_request("POST", "/memories", data=create_data, print_response=False)
                    if create_response.status_code == 200:
                        create_result = create_response.json()
                        if create_result.get("success"):
                            memories = create_result.get("data", [])
                            if isinstance(memories, list) and len(memories) > 0:
                                new_memory_id = memories[0].get("memory_id")
                                if new_memory_id:
                                    # Retry update with newly created memory_id
                                    response2 = self.make_request("PUT", f"/memories/{new_memory_id}", data=data)
                                    if response2.status_code == 200:
                                        result2 = response2.json()
                                        if result2.get("success"):
                                            self.log_result("Update Memory-with API Key", True, f"Updated successfully (using newly created memory_id), memory_id={new_memory_id}", result2)
                                            return
                except Exception as e:
                    print(f"Error creating new memory: {e}")
                
                self.log_result("Update Memory-with API Key", False, f"Returned status code: {response.status_code}, memory_id={memory_id} doesn't exist and cannot create new memory")
            else:
                self.log_result("Update Memory-with API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("Update Memory-with API Key", False, f"Exception: {str(e)}")
    
    def test_update_memory_without_auth(self):
        """Test update memory endpoint (without API Key)"""
        print("\n=== Testing Update Memory Endpoint (without API Key) ===")
        
        if not self.test_data["memory_ids"]:
            self.log_result("Update Memory-without API Key", False, "No available memory_id, skipping test")
            return
        
        memory_id = self.test_data["memory_ids"][0]
        try:
            data = {
                "content": "Updated memory content: User likes latte coffee"
            }
            response = self.make_request_without_auth("PUT", f"/memories/{memory_id}", data=data)
            self.check_response_without_auth(
                response,
                "Update Memory-without API Key",
                success_check_func=lambda r: r.get("success") if isinstance(r, dict) else True
            )
        except Exception as e:
            self.log_result("Update Memory-without API Key", False, f"Exception: {str(e)}")
    
    def test_batch_update_memories_with_auth(self):
        """Test batch update memories endpoint (with API Key)"""
        print("\n=== Testing Batch Update Memories Endpoint (with API Key) ===")
        
        # Get valid memory_ids from server to ensure using existing IDs
        memory_ids_to_update = []
        try:
            params = {"user_id": self.test_data["user_id"], "limit": 10, "offset": 0}
            get_response = self.make_request("GET", "/memories", params=params, print_response=False)
            if get_response.status_code == 200:
                get_result = get_response.json()
                if get_result.get("success"):
                    data = get_result.get("data", {})
                    memories = data.get("memories", []) or data.get("items", [])
                    if isinstance(data, list):
                        memories = data
                    # Get at least 2 memory_ids for batch update
                    for mem in memories[:2]:
                        memory_id = mem.get("memory_id") or mem.get("id")
                        if memory_id:
                            memory_ids_to_update.append(memory_id)
                            if memory_id not in self.test_data["memory_ids"]:
                                self.test_data["memory_ids"].append(memory_id)
        except Exception as e:
            print(f"Error getting memories: {e}")
        
        # If less than 2 memory_ids from server, try creating new memories
        while len(memory_ids_to_update) < 2:
            try:
                create_data = {
                    "content": f"Memory for batch update {len(memory_ids_to_update) + 1}",
                    "user_id": self.test_data["user_id"],
                    "agent_id": self.test_data["agent_id"]
                }
                create_response = self.make_request("POST", "/memories", data=create_data, print_response=False)
                if create_response.status_code == 200:
                    create_result = create_response.json()
                    if create_result.get("success"):
                        memories = create_result.get("data", [])
                        if isinstance(memories, list) and len(memories) > 0:
                            memory_id = memories[0].get("memory_id")
                            if memory_id:
                                memory_ids_to_update.append(memory_id)
                                if memory_id not in self.test_data["memory_ids"]:
                                    self.test_data["memory_ids"].append(memory_id)
                else:
                    break  # If creation failed, break loop
            except Exception as e:
                print(f"Error creating memory: {e}")
                break
        
        if len(memory_ids_to_update) < 2:
            self.log_result("Batch Update Memories-with API Key", False, f"Cannot get enough memory_ids (need 2, got {len(memory_ids_to_update)}), skipping test")
            return
        
        try:
            # Use memory_ids from server or newly created for batch update
            data = {
                "updates": [
                    {
                        "memory_id": memory_ids_to_update[0],
                        "content": "Content 1 for batch update",
                        "metadata": {"updated": True}
                    },
                    {
                        "memory_id": memory_ids_to_update[1],
                        "content": "Content 2 for batch update",
                        "metadata": {"updated": True}
                    }
                ],
                "user_id": self.test_data["user_id"],
                "agent_id": self.test_data["agent_id"]
            }
            response = self.make_request("PUT", "/memories/batch", data=data)
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    batch_data = result.get("data", {})
                    updated_count = batch_data.get("updated_count", 0)
                    failed_count = batch_data.get("failed_count", 0)
                    if updated_count > 0:
                        self.log_result("Batch Update Memories-with API Key", True, 
                                      f"Updated successfully, updated_count={updated_count}, failed_count={failed_count}", result)
                    else:
                        # If all failed, check if it's ID non-existence issue
                        failed = batch_data.get("failed", [])
                        if failed:
                            error_msg = f"Batch update failed, updated_count={updated_count}, failed_count={failed_count}"
                            self.log_result("Batch Update Memories-with API Key", False, error_msg, result)
                        else:
                            self.log_result("Batch Update Memories-with API Key", False, f"Update failed, updated_count={updated_count}", result)
                else:
                    self.log_result("Batch Update Memories-with API Key", False, f"Response format incorrect: {result}")
            else:
                self.log_result("Batch Update Memories-with API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("Batch Update Memories-with API Key", False, f"Exception: {str(e)}")
    
    def test_batch_update_memories_without_auth(self):
        """Test batch update memories endpoint (without API Key)"""
        print("\n=== Testing Batch Update Memories Endpoint (without API Key) ===")
        
        # Get valid memory_ids from server to ensure using existing IDs
        memory_ids_to_update = []
        try:
            params = {"user_id": self.test_data["user_id"], "limit": 10, "offset": 0}
            get_response = self.make_request_without_auth("GET", "/memories", params=params, print_response=False)
            if get_response.status_code == 200:
                get_result = get_response.json()
                if get_result.get("success"):
                    data = get_result.get("data", {})
                    memories = data.get("memories", []) or data.get("items", [])
                    if isinstance(data, list):
                        memories = data
                    # Get at least 2 memory_ids for batch update
                    for mem in memories[:2]:
                        memory_id = mem.get("memory_id") or mem.get("id")
                        if memory_id:
                            memory_ids_to_update.append(memory_id)
                            if memory_id not in self.test_data["memory_ids"]:
                                self.test_data["memory_ids"].append(memory_id)
        except Exception as e:
            print(f"Error getting memories: {e}")
        
        # If less than 2 memory_ids from server, try creating new memories
        while len(memory_ids_to_update) < 2:
            try:
                create_data = {
                    "content": f"Memory for batch update {len(memory_ids_to_update) + 1} (without auth)",
                    "user_id": self.test_data["user_id"],
                    "agent_id": self.test_data["agent_id"]
                }
                create_response = self.make_request_without_auth("POST", "/memories", data=create_data, print_response=False)
                if create_response.status_code == 200:
                    create_result = create_response.json()
                    if create_result.get("success"):
                        memories = create_result.get("data", [])
                        if isinstance(memories, list) and len(memories) > 0:
                            memory_id = memories[0].get("memory_id")
                            if memory_id:
                                memory_ids_to_update.append(memory_id)
                                if memory_id not in self.test_data["memory_ids"]:
                                    self.test_data["memory_ids"].append(memory_id)
                else:
                    break  # If creation failed, break loop
            except Exception as e:
                print(f"Error creating memory: {e}")
                break
        
        if len(memory_ids_to_update) < 2:
            self.log_result("Batch Update Memories-without API Key", False, f"Cannot get enough memory_ids (need 2, got {len(memory_ids_to_update)}), skipping test")
            return
        
        try:
            # Use memory_ids from server or newly created for batch update
            data = {
                "updates": [
                    {
                        "memory_id": memory_ids_to_update[0],
                        "content": "Batch updated content 1 (without auth)"
                    },
                    {
                        "memory_id": memory_ids_to_update[1],
                        "content": "Batch updated content 2 (without auth)"
                    }
                ]
            }
            response = self.make_request_without_auth("PUT", "/memories/batch", data=data)
            self.check_response_without_auth(
                response,
                "Batch Update Memories-without API Key",
                success_check_func=lambda r: r.get("success") if isinstance(r, dict) else True
            )
        except Exception as e:
            self.log_result("Batch Update Memories-without API Key", False, f"Exception: {str(e)}")
    
    def test_delete_memory_with_auth(self):
        """Test delete memory endpoint (with API Key)"""
        print("\n=== Testing Delete Memory Endpoint (with API Key) ===")
        
        if not self.test_data["memory_ids"]:
            self.log_result("Delete Memory-with API Key", False, "No available memory_id, skipping test")
            return
        
        memory_id_to_delete = self.test_data["memory_ids"][-1]
        try:
            params = {
                "user_id": self.test_data["user_id"],
                "agent_id": self.test_data["agent_id"]
            }
            response = self.make_request("DELETE", f"/memories/{memory_id_to_delete}", params=params)
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    if memory_id_to_delete in self.test_data["memory_ids"]:
                        self.test_data["memory_ids"].remove(memory_id_to_delete)
                    self.log_result("Delete Memory-with API Key", True, f"Deleted successfully, memory_id={memory_id_to_delete}", result)
                else:
                    self.log_result("Delete Memory-with API Key", False, f"Response format incorrect: {result}")
            else:
                self.log_result("Delete Memory-with API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("Delete Memory-with API Key", False, f"Exception: {str(e)}")
    
    def test_delete_memory_without_auth(self):
        """Test delete memory endpoint (without API Key)"""
        print("\n=== Testing Delete Memory Endpoint (without API Key) ===")
        
        if not self.test_data["memory_ids"]:
            self.log_result("Delete Memory-without API Key", False, "No available memory_id, skipping test")
            return
        
        memory_id = self.test_data["memory_ids"][0]
        try:
            response = self.make_request_without_auth("DELETE", f"/memories/{memory_id}")
            # If deletion successful, remove deleted ID from list to avoid using it in subsequent tests
            if response.status_code == 200:
                try:
                    result = response.json()
                    if result.get("success"):
                        if memory_id in self.test_data["memory_ids"]:
                            self.test_data["memory_ids"].remove(memory_id)
                except:
                    pass  # If parsing fails, ignore
            self.check_response_without_auth(
                response,
                "Delete Memory-without API Key",
                success_check_func=lambda r: r.get("success") if isinstance(r, dict) else True
            )
        except Exception as e:
            self.log_result("Delete Memory-without API Key", False, f"Exception: {str(e)}")
    
    def test_batch_delete_memories_with_auth(self):
        """Test batch delete memories endpoint (with API Key)"""
        print("\n=== Testing Batch Delete Memories Endpoint (with API Key) ===")
        
        if len(self.test_data["memory_ids"]) < 2:
            self.log_result("Batch Delete Memories-with API Key", False, "Insufficient memory_ids, skipping test")
            return
        
        ids_to_delete = self.test_data["memory_ids"][-2:]
        try:
            data = {
                "memory_ids": ids_to_delete,
                "user_id": self.test_data["user_id"],
                "agent_id": self.test_data["agent_id"]
            }
            response = self.make_request("DELETE", "/memories/batch", data=data)
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    batch_data = result.get("data", {})
                    deleted_count = batch_data.get("deleted_count", 0)
                    for mem_id in ids_to_delete:
                        if mem_id in self.test_data["memory_ids"]:
                            self.test_data["memory_ids"].remove(mem_id)
                    self.log_result("Batch Delete Memories-with API Key", True, 
                                  f"Deleted successfully, deleted_count={deleted_count}", result)
                else:
                    self.log_result("Batch Delete Memories-with API Key", False, f"Response format incorrect: {result}")
            else:
                self.log_result("Batch Delete Memories-with API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("Batch Delete Memories-with API Key", False, f"Exception: {str(e)}")
    
    def test_batch_delete_memories_without_auth(self):
        """Test batch delete memories endpoint (without API Key)"""
        print("\n=== Testing Batch Delete Memories Endpoint (without API Key) ===")
        
        # Use real memory_ids (obtained from previous tests)
        if len(self.test_data["memory_ids"]) < 2:
            self.log_result("Batch Delete Memories-without API Key", False, "Not enough memory_ids (need at least 2), skipping test")
            return
        
        try:
            # Use last 2 from memory_ids (avoid conflict with single delete)
            memory_ids_to_delete = self.test_data["memory_ids"][-2:]
            data = {
                "memory_ids": memory_ids_to_delete
            }
            response = self.make_request_without_auth("DELETE", "/memories/batch", data=data)
            self.check_response_without_auth(
                response,
                "Batch Delete Memories-without API Key",
                success_check_func=lambda r: r.get("success") if isinstance(r, dict) else True
            )
            
            # If deletion successful, remove deleted IDs from list
            if response.status_code == 200:
                try:
                    result = response.json()
                    if result.get("success"):
                        batch_data = result.get("data", {})
                        deleted_count = batch_data.get("deleted_count", 0)
                        if deleted_count > 0:
                            # Remove deleted IDs from list
                            for mem_id in memory_ids_to_delete:
                                if mem_id in self.test_data["memory_ids"]:
                                    self.test_data["memory_ids"].remove(mem_id)
                except:
                    pass  # If parsing fails, ignore
        except Exception as e:
            self.log_result("Batch Delete Memories-without API Key", False, f"Exception: {str(e)}")
    
    # ==================== Module 3: Search Endpoints ====================
    
    def test_search_memories_with_auth(self):
        """Test search memories endpoint (with API Key)"""
        print("\n=== Testing Search Memories Endpoint (with API Key) ===")
        
        try:
            data = {
                "query": "User likes what",
                "user_id": self.test_data["user_id"],
                "agent_id": self.test_data["agent_id"],
                "run_id": self.test_data["run_id"],
                "filters": {
                    "category": "preference",
                    "topic": "beverage"
                },
                "limit": 10
            }
            response = self.make_request("POST", "/memories/search", data=data)
            if response.status_code == 200:
                result = response.json()
                if result.get("success") and "data" in result:
                    search_data = result["data"]
                    results = search_data.get("results", [])
                    total = search_data.get("total", 0)
                    self.log_result("Search Memories-with API Key", True, 
                                  f"Search successful, found {total} results", result)
                else:
                    self.log_result("Search Memories-with API Key", False, f"Response format incorrect: {result}")
            else:
                self.log_result("Search Memories-with API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("Search Memories-with API Key", False, f"Exception: {str(e)}")
    
    def test_search_memories_without_auth(self):
        """Test search memories endpoint (without API Key)"""
        print("\n=== Testing Search Memories Endpoint (without API Key) ===")
        
        try:
            data = {
                "query": "User likes what"
            }
            response = self.make_request_without_auth("POST", "/memories/search", data=data)
            self.check_response_without_auth(
                response,
                "Search Memories-without API Key",
                success_check_func=lambda r: r.get("success") and "data" in r if isinstance(r, dict) else True
            )
        except Exception as e:
            self.log_result("Search Memories-without API Key", False, f"Exception: {str(e)}")
    
    # ==================== Module 4: User Profile Endpoints ====================
    
    def test_update_user_profile_with_auth(self):
        """Test add messages and extract user profile endpoint (with API Key)"""
        print("\n=== Testing Add Messages and Extract User Profile Endpoint (with API Key) ===")
        
        # Test 1: Basic message with default profile_type="content"
        try:
            data = {
                "messages": [
                    {"role": "user", "content": "Hi, I am a senior software engineer from Beijing. I focus on AI and machine learning."},
                    {"role": "assistant", "content": "Nice to meet you! That sounds interesting."}
                ],
                "agent_id": self.test_data["agent_id"],
                "run_id": self.test_data["run_id"],
                "profile_type": "content",
                "infer": True
            }
            response = self.make_request("POST", f"/users/{self.test_data['user_id']}/profile", data=data)
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    data_result = result.get("data", {})
                    profile_extracted = data_result.get("profile_extracted", False)
                    self.log_result("Add Messages and Extract Profile-Content-with API Key", True, 
                                  f"Profile extracted: {profile_extracted}", result)
                else:
                    self.log_result("Add Messages and Extract Profile-Content-with API Key", False, f"Response format incorrect: {result}")
            else:
                self.log_result("Add Messages and Extract Profile-Content-with API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("Add Messages and Extract Profile-Content-with API Key", False, f"Exception: {str(e)}")
        
        # Test 2: Extract structured topics
        try:
            data = {
                "messages": [
                    {"role": "user", "content": "I am Alice, 28 years old, working as a data scientist in Shanghai. I like Python programming and machine learning."}
                ],
                "profile_type": "topics",
                "custom_topics": '{"basic_info": {"name": "User name", "age": "User age", "location": "User location"}, "professional": {"occupation": "User job", "skills": "User skills"}}',
                "strict_mode": False,
                "infer": True
            }
            response = self.make_request("POST", f"/users/{self.test_data['user_id']}/profile", data=data)
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    data_result = result.get("data", {})
                    profile_extracted = data_result.get("profile_extracted", False)
                    topics = data_result.get("topics", {})
                    self.log_result("Add Messages and Extract Profile-Topics-with API Key", True, 
                                  f"Profile extracted: {profile_extracted}, topics extracted: {bool(topics)}", result)
                else:
                    self.log_result("Add Messages and Extract Profile-Topics-with API Key", False, f"Response format incorrect: {result}")
            else:
                self.log_result("Add Messages and Extract Profile-Topics-with API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("Add Messages and Extract Profile-Topics-with API Key", False, f"Exception: {str(e)}")
        
        # Test 3: Missing required messages field (should fail)
        try:
            data = {
                "agent_id": self.test_data["agent_id"]
            }
            response = self.make_request("POST", f"/users/{self.test_data['user_id']}/profile", data=data)
            if response.status_code == 422:
                self.log_result("Add Messages and Extract Profile-Missing Messages-with API Key", True, "Returned 422 validation error (as expected)")
            else:
                self.log_result("Add Messages and Extract Profile-Missing Messages-with API Key", False, f"Should return 422, actually returned: {response.status_code}")
        except Exception as e:
            self.log_result("Add Messages and Extract Profile-Missing Messages-with API Key", False, f"Exception: {str(e)}")
    
    def test_update_user_profile_without_auth(self):
        """Test add messages and extract user profile endpoint (without API Key)"""
        print("\n=== Testing Add Messages and Extract User Profile Endpoint (without API Key) ===")
        
        try:
            data = {
                "messages": [
                    {"role": "user", "content": "I am a senior software engineer, focused on AI and machine learning."}
                ],
                "profile_type": "content",
                "infer": True
            }
            response = self.make_request_without_auth("POST", f"/users/{self.test_data['user_id']}/profile", data=data)
            self.check_response_without_auth(
                response,
                "Add Messages and Extract Profile-without API Key",
                success_check_func=lambda r: r.get("success") if isinstance(r, dict) else True
            )
        except Exception as e:
            self.log_result("Add Messages and Extract Profile-without API Key", False, f"Exception: {str(e)}")
    
    def test_get_user_profile_with_auth(self):
        """Test get user profile endpoint (with API Key)"""
        print("\n=== Testing Get User Profile Endpoint (with API Key) ===")
        
        try:
            response = self.make_request("GET", f"/users/{self.test_data['user_id']}/profile")
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    self.log_result("Get User Profile-with API Key", True, "Retrieved successfully", result)
                else:
                    self.log_result("Get User Profile-with API Key", False, f"Response format incorrect: {result}")
            else:
                self.log_result("Get User Profile-with API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("Get User Profile-with API Key", False, f"Exception: {str(e)}")
    
    def test_get_user_profile_without_auth(self):
        """Test get user profile endpoint (without API Key)"""
        print("\n=== Testing Get User Profile Endpoint (without API Key) ===")
        
        try:
            response = self.make_request_without_auth("GET", f"/users/{self.test_data['user_id']}/profile")
            self.check_response_without_auth(
                response,
                "Get User Profile-without API Key",
                success_check_func=lambda r: r.get("success") if isinstance(r, dict) else True
            )
        except Exception as e:
            self.log_result("Get User Profile-without API Key", False, f"Exception: {str(e)}")
    
    def test_delete_user_profile_with_auth(self):
        """Test delete user profile endpoint (with API Key)"""
        print("\n=== Testing Delete User Profile Endpoint (with API Key) ===")
        
        # Test 1: Normal deletion (user_id with existing profile)
        try:
            response = self.make_request("DELETE", f"/users/{self.test_data['user_id']}/profile")
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    data = result.get("data", {})
                    if data.get("deleted") is True:
                        self.log_result("Delete User Profile-Normal-with API Key", True, "Deleted successfully", result)
                    else:
                        self.log_result("Delete User Profile-Normal-with API Key", False, f"Response format incorrect: {result}")
                else:
                    self.log_result("Delete User Profile-Normal-with API Key", False, f"Response format incorrect: {result}")
            else:
                self.log_result("Delete User Profile-Normal-with API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("Delete User Profile-Normal-with API Key", False, f"Exception: {str(e)}")
        
        # Test 2: User doesn't exist
        try:
            response = self.make_request("DELETE", "/users/unknown-user-999999/profile")
            if response.status_code == 404:
                self.log_result("Delete User Profile-User Not Found-with API Key", True, "Returned 404 (as expected)")
            else:
                self.log_result("Delete User Profile-User Not Found-with API Key", False, f"Should return 404, actually returned: {response.status_code}")
        except Exception as e:
            self.log_result("Delete User Profile-User Not Found-with API Key", False, f"Exception: {str(e)}")
        
        # Test 3: User has no profile (delete already deleted profile, should return 404)
        try:
            # Since test 1 already deleted profile, deleting again should return 404
            response = self.make_request("DELETE", f"/users/{self.test_data['user_id']}/profile")
            if response.status_code == 404:
                self.log_result("Delete User Profile-No Profile-with API Key", True, "Returned 404 (as expected)")
            else:
                self.log_result("Delete User Profile-No Profile-with API Key", False, f"Should return 404, actually returned: {response.status_code}")
        except Exception as e:
            self.log_result("Delete User Profile-No Profile-with API Key", False, f"Exception: {str(e)}")
        
        # Test 4: Query after deletion (query same user again, should return 404)
        try:
            response = self.make_request("GET", f"/users/{self.test_data['user_id']}/profile")
            if response.status_code == 404:
                self.log_result("Delete User Profile-Query After Delete-with API Key", True, "Returned 404 (as expected)")
            else:
                self.log_result("Delete User Profile-Query After Delete-with API Key", False, f"Should return 404, actually returned: {response.status_code}")
        except Exception as e:
            self.log_result("Delete User Profile-Query After Delete-with API Key", False, f"Exception: {str(e)}")
    
    def test_delete_user_profile_without_auth(self):
        """Test delete user profile endpoint (without API Key)"""
        print("\n=== Testing Delete User Profile Endpoint (without API Key) ===")
        
        try:
            response = self.make_request_without_auth("DELETE", f"/users/{self.test_data['user_id']}/profile")
            self.check_response_without_auth(
                response,
                "Delete User Profile-without API Key",
                success_check_func=lambda r: r.get("success") if isinstance(r, dict) else True
            )
        except Exception as e:
            self.log_result("Delete User Profile-without API Key", False, f"Exception: {str(e)}")
    
    def test_get_user_memories_with_auth(self):
        """Test get user memories endpoint (with API Key)"""
        print("\n=== Testing Get User Memories Endpoint (with API Key) ===")
        
        try:
            params = {"limit": 20, "offset": 0}
            response = self.make_request("GET", f"/users/{self.test_data['user_id']}/memories", params=params)
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    data = result.get("data", {})
                    total = data.get("total", 0)
                    self.log_result("Get User Memories-with API Key", True, f"Retrieved successfully, total={total}", result)
                else:
                    self.log_result("Get User Memories-with API Key", False, f"Response format incorrect: {result}")
            else:
                self.log_result("Get User Memories-with API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("Get User Memories-with API Key", False, f"Exception: {str(e)}")
    
    def test_get_user_memories_without_auth(self):
        """Test get user memories endpoint (without API Key)"""
        print("\n=== Testing Get User Memories Endpoint (without API Key) ===")
        
        try:
            response = self.make_request_without_auth("GET", f"/users/{self.test_data['user_id']}/memories")
            self.check_response_without_auth(
                response,
                "Get User Memories-without API Key",
                success_check_func=lambda r: r.get("success") and "data" in r if isinstance(r, dict) else True
            )
        except Exception as e:
            self.log_result("Get User Memories-without API Key", False, f"Exception: {str(e)}")
    
    def test_delete_user_memories_with_auth(self):
        """Test delete user memories endpoint (with API Key)"""
        print("\n=== Testing Delete User Memories Endpoint (with API Key) ===")
        
        try:
            response = self.make_request("DELETE", f"/users/{self.test_data['user_id']}/memories")
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    data = result.get("data", {})
                    deleted_count = data.get("deleted_count", 0)
                    self.log_result("Delete User Memories-with API Key", True, 
                                  f"Deleted successfully, deleted_count={deleted_count}", result)
                else:
                    self.log_result("Delete User Memories-with API Key", False, f"Response format incorrect: {result}")
            else:
                self.log_result("Delete User Memories-with API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("Delete User Memories-with API Key", False, f"Exception: {str(e)}")
    
    def test_delete_user_memories_without_auth(self):
        """Test delete user memories endpoint (without API Key)"""
        print("\n=== Testing Delete User Memories Endpoint (without API Key) ===")
        
        try:
            response = self.make_request_without_auth("DELETE", f"/users/{self.test_data['user_id']}/memories")
            self.check_response_without_auth(
                response,
                "Delete User Memories-without API Key",
                success_check_func=lambda r: r.get("success") if isinstance(r, dict) else True
            )
        except Exception as e:
            self.log_result("Delete User Memories-without API Key", False, f"Exception: {str(e)}")
    
    # ==================== Module 5: Agent Management Endpoints ====================
    
    def test_create_agent_memory_with_auth(self):
        """Test create agent memory endpoint (with API Key)"""
        print("\n=== Testing Create Agent Memory Endpoint (with API Key) ===")
        
        try:
            data = {
                "content": "Alice likes coffee",
                "user_id": self.test_data["user_id"],
                "run_id": self.test_data["run_id"]
            }
            response = self.make_request("POST", f"/agents/{self.test_data['agent_id']}/memories", data=data)
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    mem_data = result.get("data", {})
                    if "memory_id" in mem_data:
                        self.test_data["memory_ids"].append(mem_data["memory_id"])
                    self.log_result("Create Agent Memory-with API Key", True, "Created successfully", result)
                else:
                    self.log_result("Create Agent Memory-with API Key", False, f"Response format incorrect: {result}")
            else:
                self.log_result("Create Agent Memory-with API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("Create Agent Memory-with API Key", False, f"Exception: {str(e)}")
    
    def test_create_agent_memory_without_auth(self):
        """Test create agent memory endpoint (without API Key)"""
        print("\n=== Testing Create Agent Memory Endpoint (without API Key) ===")
        
        try:
            data = {
                "content": "Alice likes coffee"
            }
            response = self.make_request_without_auth("POST", f"/agents/{self.test_data['agent_id']}/memories", data=data)
            self.check_response_without_auth(
                response,
                "Create Agent Memory-without API Key",
                success_check_func=lambda r: r.get("success") if isinstance(r, dict) else True
            )
        except Exception as e:
            self.log_result("Create Agent Memory-without API Key", False, f"Exception: {str(e)}")
    
    def test_get_agent_memories_with_auth(self):
        """Test get agent memories endpoint (with API Key)"""
        print("\n=== Testing Get Agent Memories Endpoint (with API Key) ===")
        
        try:
            params = {"limit": 20, "offset": 0}
            response = self.make_request("GET", f"/agents/{self.test_data['agent_id']}/memories", params=params)
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    data = result.get("data", {})
                    total = data.get("total", 0)
                    self.log_result("Get Agent Memories-with API Key", True, f"Retrieved successfully, total={total}", result)
                else:
                    self.log_result("Get Agent Memories-with API Key", False, f"Response format incorrect: {result}")
            else:
                self.log_result("Get Agent Memories-with API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("Get Agent Memories-with API Key", False, f"Exception: {str(e)}")
    
    def test_get_agent_memories_without_auth(self):
        """Test get agent memories endpoint (without API Key)"""
        print("\n=== Testing Get Agent Memories Endpoint (without API Key) ===")
        
        try:
            response = self.make_request_without_auth("GET", f"/agents/{self.test_data['agent_id']}/memories")
            self.check_response_without_auth(
                response,
                "Get Agent Memories-without API Key",
                success_check_func=lambda r: r.get("success") and "data" in r if isinstance(r, dict) else True
            )
        except Exception as e:
            self.log_result("Get Agent Memories-without API Key", False, f"Exception: {str(e)}")
    
    def test_share_agent_memories_with_auth(self):
        """Test share agent memories endpoint (with API Key)"""
        print("\n=== Testing Share Agent Memories Endpoint (with API Key) ===")
        
        try:
            data = {
                "target_agent_id": "test-agent-789"
            }
            response = self.make_request("POST", f"/agents/{self.test_data['agent_id']}/memories/share", data=data)
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    share_data = result.get("data", {})
                    shared_count = share_data.get("shared_count", 0)
                    self.log_result("Share Agent Memories-with API Key", True, 
                                  f"Shared successfully, shared_count={shared_count}", result)
                else:
                    self.log_result("Share Agent Memories-with API Key", False, f"Response format incorrect: {result}")
            else:
                self.log_result("Share Agent Memories-with API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("Share Agent Memories-with API Key", False, f"Exception: {str(e)}")
    
    def test_share_agent_memories_without_auth(self):
        """Test share agent memories endpoint (without API Key)"""
        print("\n=== Testing Share Agent Memories Endpoint (without API Key) ===")
        
        try:
            data = {
                "target_agent_id": "test-agent-789"
            }
            response = self.make_request_without_auth("POST", f"/agents/{self.test_data['agent_id']}/memories/share", data=data)
            self.check_response_without_auth(
                response,
                "Share Agent Memories-without API Key",
                success_check_func=lambda r: r.get("success") if isinstance(r, dict) else True
            )
        except Exception as e:
            self.log_result("Share Agent Memories-without API Key", False, f"Exception: {str(e)}")
    
    def test_get_shared_memories_with_auth(self):
        """Test get shared memories endpoint (with API Key)"""
        print("\n=== Testing Get Shared Memories Endpoint (with API Key) ===")
        
        try:
            params = {"limit": 20, "offset": 0}
            response = self.make_request("GET", "/agents/test-agent-789/memories/share", params=params)
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    data = result.get("data", {})
                    total = data.get("total", 0)
                    self.log_result("Get Shared Memories-with API Key", True, f"Retrieved successfully, total={total}", result)
                else:
                    self.log_result("Get Shared Memories-with API Key", False, f"Response format incorrect: {result}")
            else:
                self.log_result("Get Shared Memories-with API Key", False, f"Returned status code: {response.status_code}")
        except Exception as e:
            self.log_result("Get Shared Memories-with API Key", False, f"Exception: {str(e)}")
    
    def test_get_shared_memories_without_auth(self):
        """Test get shared memories endpoint (without API Key)"""
        print("\n=== Testing Get Shared Memories Endpoint (without API Key) ===")
        
        try:
            response = self.make_request_without_auth("GET", "/agents/test-agent-789/memories/share")
            self.check_response_without_auth(
                response,
                "Get Shared Memories-without API Key",
                success_check_func=lambda r: r.get("success") and "data" in r if isinstance(r, dict) else True
            )
        except Exception as e:
            self.log_result("Get Shared Memories-without API Key", False, f"Exception: {str(e)}")
    
    # ==================== Module 6: Error Scenario Tests ====================
    
    def test_auth_errors(self):
        """Test authentication error scenarios"""
        print("\n=== Testing Authentication Error Scenarios ===")
        
        # Test 1: No API Key
        try:
            response = requests.get(f"{self.api_base}/memories", timeout=10)
            self.print_response(response, "Auth Error-No API Key")
            if response.status_code == 401:
                self.log_result("Auth Error-No API Key", True, "Returned 401 unauthorized (as expected)")
            else:
                self.log_result("Auth Error-No API Key", False, f"Should return 401, actually returned: {response.status_code}")
        except Exception as e:
            self.log_result("Auth Error-No API Key", False, f"Exception: {str(e)}")
        
        # Test 2: Invalid API Key
        try:
            headers = {"X-API-Key": "invalid-key", "Content-Type": "application/json"}
            response = requests.get(f"{self.api_base}/memories", headers=headers, timeout=10)
            self.print_response(response, "Auth Error-Invalid API Key")
            if response.status_code == 401:
                self.log_result("Auth Error-Invalid API Key", True, "Returned 401 unauthorized (as expected)")
            else:
                self.log_result("Auth Error-Invalid API Key", False, f"Should return 401, actually returned: {response.status_code}")
        except Exception as e:
            self.log_result("Auth Error-Invalid API Key", False, f"Exception: {str(e)}")
    
    def test_validation_errors(self):
        """Test validation error scenarios"""
        print("\n=== Testing Validation Error Scenarios ===")
        
        # Test 1: Missing required fields
        try:
            data = {"user_id": self.test_data["user_id"]}
            response = self.make_request("POST", "/memories", data=data)
            if response.status_code == 422:
                self.log_result("Validation Error-Missing Required Fields", True, "Returned 422 validation error (as expected)")
            else:
                self.log_result("Validation Error-Missing Required Fields", False, f"Should return 422, actually returned: {response.status_code}")
        except Exception as e:
            self.log_result("Validation Error-Missing Required Fields", False, f"Exception: {str(e)}")
        
        # Test 2: Limit exceeded
        try:
            params = {"limit": 2000}
            response = self.make_request("GET", "/memories", params=params)
            if response.status_code == 422:
                self.log_result("Validation Error-Limit Exceeded", True, "Returned 422 validation error (as expected)")
            else:
                self.log_result("Validation Error-Limit Exceeded", False, f"Should return 422, actually returned: {response.status_code}")
        except Exception as e:
            self.log_result("Validation Error-Limit Exceeded", False, f"Exception: {str(e)}")
    
    # ==================== Run All Tests ====================
    
    def restart_server(self):
        """
        Restart server: execute make server-stop and make server-start
        """
        print("\nRestarting server...")
        
        # Find Makefile location (usually in project root)
        possible_makefile_paths = [
            os.getcwd(),
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            os.path.dirname(os.path.dirname(__file__)),
        ]
        
        makefile_dir = None
        for path in possible_makefile_paths:
            makefile_path = os.path.join(path, 'Makefile')
            if os.path.exists(makefile_path):
                makefile_dir = path
                break
        
        if not makefile_dir:
            print("Warning: Makefile not found, skipping server restart")
            print("Please manually execute: make server-stop && make server-start")
            return False
        
        try:
            # Execute make server-stop
            print(f"Executing: make server-stop (in directory: {makefile_dir})")
            result_stop = subprocess.run(
                ['make', 'server-stop'],
                cwd=makefile_dir,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result_stop.returncode != 0:
                print(f"Warning: make server-stop returned non-zero exit code: {result_stop.returncode}")
                if result_stop.stderr:
                    print(f"Error message: {result_stop.stderr}")
            else:
                print("✓ make server-stop executed successfully")
                if result_stop.stdout:
                    print(f"Output: {result_stop.stdout.strip()}")
            
            # Wait for server to stop
            print("Waiting for server to stop...")
            time.sleep(2)
            
            # Execute make server-start
            print(f"Executing: make server-start (in directory: {makefile_dir})")
            result_start = subprocess.run(
                ['make', 'server-start'],
                cwd=makefile_dir,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result_start.returncode != 0:
                print(f"Warning: make server-start returned non-zero exit code: {result_start.returncode}")
                if result_start.stderr:
                    print(f"Error message: {result_start.stderr}")
                return False
            else:
                print("✓ make server-start executed successfully")
                if result_start.stdout:
                    print(f"Output: {result_start.stdout.strip()}")
            
            # Wait for server to start, using retry mechanism
            print("Waiting for server to start...")
            max_retries = 10  # Maximum 10 retries
            retry_interval = 3  # 3 seconds between retries
            
            for attempt in range(max_retries):
                time.sleep(retry_interval)
                try:
                    response = requests.get(f"{self.base_url}/api/v1/system/health", timeout=5)
                    if response.status_code == 200:
                        print(f"✓ Server started successfully and responding (attempt {attempt + 1}/{max_retries})")
                        return True
                    else:
                        print(f"Waiting for server to start... (attempt {attempt + 1}/{max_retries}, status code: {response.status_code})")
                except requests.exceptions.ConnectionError:
                    print(f"Waiting for server to start... (attempt {attempt + 1}/{max_retries}, connection refused)")
                except requests.exceptions.RequestException as e:
                    print(f"Waiting for server to start... (attempt {attempt + 1}/{max_retries}, error: {e})")
            
            print(f"Error: Server failed to start within {max_retries * retry_interval} seconds")
            print("Please manually check server status")
            return False
            
        except subprocess.TimeoutExpired:
            print("Error: make command execution timeout")
            return False
        except Exception as e:
            print(f"Error: Error executing make command: {e}")
            return False
    
    def load_env_config(self):
        """
        Load configuration from .env file
        
        Returns:
            tuple: (auth_enabled, api_keys, env_file_path)
        """
        auth_enabled = True  # Default value
        api_keys = ""  # Default value
        
        # Try multiple possible .env file paths
        possible_paths = [
            os.path.join(os.getcwd(), '.env'),
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env'),
            os.path.join(os.path.dirname(__file__), '.env'),
        ]
        
        env_file_path = None
        for path in possible_paths:
            if os.path.exists(path):
                env_file_path = path
                break
        
        if env_file_path:
            try:
                with open(env_file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        # Skip comments and empty lines
                        if not line or line.startswith('#'):
                            continue
                        
                        if '=' in line:
                            key, value = line.split('=', 1)
                            key = key.strip()
                            value = value.strip().strip('"').strip("'")
                            
                            if key.upper() == 'POWERMEM_SERVER_AUTH_ENABLED':
                                auth_enabled = value.lower() in ('true', '1', 'yes', 'on', 'enabled')
                            elif key.upper() == 'POWERMEM_AUTH_ENABLED':
                                # Also check for old format (backward compatibility)
                                auth_enabled = value.lower() in ('true', '1', 'yes', 'on', 'enabled')
                            elif key.upper() == 'POWERMEM_SERVER_API_KEYS':
                                api_keys = value
            except Exception as e:
                print(f"Error reading .env file: {e}")
                print("Using default configuration")
        
        return auth_enabled, api_keys, env_file_path
    
    def update_env_file(self, auth_enabled: bool, api_keys: str = None):
        """
        Update configuration in .env file
        
        Args:
            auth_enabled: Whether to enable authentication
            api_keys: API key list (optional, if None then not updated)
        """
        # Try multiple possible .env file paths
        possible_paths = [
            os.path.join(os.getcwd(), '.env'),
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env'),
            os.path.join(os.path.dirname(__file__), '.env'),
        ]
        
        env_file_path = None
        for path in possible_paths:
            if os.path.exists(path):
                env_file_path = path
                break
        
        if not env_file_path:
            print("Warning: .env file not found, cannot update configuration")
            return False
        
        try:
            # Read existing content
            lines = []
            auth_enabled_found = False
            api_keys_found = False
            
            with open(env_file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    stripped = line.strip()
                    # Skip comments and empty lines, but keep them
                    if not stripped or stripped.startswith('#'):
                        lines.append(line)
                        continue
                    
                    if '=' in stripped:
                        key, value = stripped.split('=', 1)
                        key_stripped = key.strip()
                        
                        if key_stripped.upper() == 'POWERMEM_SERVER_AUTH_ENABLED':
                            # Update this line
                            lines.append(f"POWERMEM_SERVER_AUTH_ENABLED={str(auth_enabled).lower()}\n")
                            auth_enabled_found = True
                        elif key_stripped.upper() == 'POWERMEM_SERVER_API_KEYS' and api_keys is not None:
                            # Update this line
                            lines.append(f"POWERMEM_SERVER_API_KEYS={api_keys}\n")
                            api_keys_found = True
                        else:
                            # Keep original line
                            lines.append(line)
                    else:
                        # Keep original line
                        lines.append(line)
            
            # If configuration item doesn't exist, add to end of file
            # Ensure file ends with newline
            if lines and not lines[-1].endswith('\n'):
                lines[-1] = lines[-1] + '\n'
            
            if not auth_enabled_found:
                if lines and lines[-1].strip():  # If last line is not empty
                    lines.append(f"\nPOWERMEM_SERVER_AUTH_ENABLED={str(auth_enabled).lower()}\n")
                else:
                    lines.append(f"POWERMEM_SERVER_AUTH_ENABLED={str(auth_enabled).lower()}\n")
            
            if api_keys is not None and not api_keys_found:
                lines.append(f"POWERMEM_SERVER_API_KEYS={api_keys}\n")
            
            # Write back to file
            with open(env_file_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            
            print(f"Updated {env_file_path}:")
            print(f"  POWERMEM_SERVER_AUTH_ENABLED = {auth_enabled}")
            if api_keys is not None:
                print(f"  POWERMEM_SERVER_API_KEYS = {api_keys}")
            
            return True
        except Exception as e:
            print(f"Error updating .env file: {e}")
            return False
    
    def run_tests_without_auth(self, expect_auth_required: bool = True):
        """
        Run all tests without API Key
        
        Args:
            expect_auth_required: If True, expect 401 (auth required); If False, expect 200 (auth disabled)
        """
        print("\n" + "=" * 60)
        print("Module 1: Tests without API Key")
        if expect_auth_required:
            print("Expected behavior: Return 401 (authentication enabled)")
        else:
            print("Expected behavior: Return 200 (authentication disabled)")
        print("=" * 60)
        
        # Save expected state to instance variable for test functions
        self.expect_auth_required = expect_auth_required
        
        # Module 1: System endpoints
        self.test_health_check_without_auth()
        self.test_system_status_without_auth()
        self.test_system_metrics_without_auth()
        self.test_system_delete_all_memories_without_auth()
        
        # Module 2: Memory management endpoints
        self.test_create_memory_without_auth()
        self.test_batch_create_memories_without_auth()
        self.test_list_memories_without_auth()
        self.test_get_memory_without_auth()
        self.test_update_memory_without_auth()
        self.test_batch_update_memories_without_auth()
        self.test_delete_memory_without_auth()
        self.test_batch_delete_memories_without_auth()
        
        # Module 3: Search endpoints
        self.test_search_memories_without_auth()
        
        # Module 4: User profile endpoints
        self.test_update_user_profile_without_auth()
        self.test_get_user_profile_without_auth()
        self.test_delete_user_profile_without_auth()
        self.test_get_user_memories_without_auth()
        self.test_delete_user_memories_without_auth()
        
        # Module 5: Agent management endpoints
        self.test_create_agent_memory_without_auth()
        self.test_get_agent_memories_without_auth()
        self.test_share_agent_memories_without_auth()
        self.test_get_shared_memories_without_auth()
        self.test_system_delete_all_memories_without_auth()
    
    def run_tests_with_auth(self):
        """Run all tests with API Key"""
        print("\n" + "=" * 60)
        print("Module 2: Tests with API Key")
        print("=" * 60)
        
        # Module 1: System endpoints
        self.test_health_check_with_auth()
        self.test_system_status_with_auth()
        self.test_system_metrics_with_auth()
        self.test_system_delete_all_memories_with_auth()
        
        # Module 2: Memory management endpoints
        self.test_create_memory_with_auth()
        self.test_batch_create_memories_with_auth()
        self.test_list_memories_with_auth()
        self.test_get_memory_with_auth()
        self.test_update_memory_with_auth()
        self.test_batch_update_memories_with_auth()
        self.test_delete_memory_with_auth()
        self.test_batch_delete_memories_with_auth()
        
        # Module 3: Search endpoints
        # self.test_create_memory_with_auth()
        self.test_search_memories_with_auth()
        
        # Module 4: User profile endpoints
        self.test_update_user_profile_with_auth()
        self.test_get_user_profile_with_auth()
        self.test_delete_user_profile_with_auth()
        self.test_get_user_memories_with_auth()
        self.test_delete_user_memories_with_auth()
        
        # Module 5: Agent management endpoints
        self.test_create_agent_memory_with_auth()
        self.test_get_agent_memories_with_auth()
        self.test_share_agent_memories_with_auth()
        self.test_get_shared_memories_with_auth()
        
        # Module 6: Error scenario tests (these tests require API Key)
        self.test_auth_errors()
        self.test_validation_errors()
    
    def run_all_tests(self):
        """Run all tests"""
        
        print("=" * 60)
        print("powermem 0.3.0 API Server Basic Functionality Test")
        print("=" * 60)
        print(f"Base URL: {self.base_url}")
        print(f"API Base: {self.api_base}")
        print(f"API Key: {self.api_key}")
        print(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)
        
        start_time = time.time()
        
        # ==================== Module 1: Tests without API Key ====================
        print("\n" + ">" * 60)
        print("Starting Module 1: Tests without API Key")
        print(">" * 60)
        
        # First update .env file, set POWERMEM_SERVER_AUTH_ENABLED to false
        print("\nUpdating .env file: POWERMEM_SERVER_AUTH_ENABLED=false")
        self.update_env_file(auth_enabled=False)
        
        # Restart server to apply new configuration
        if not self.restart_server():
            print("Error: Server restart failed, cannot continue testing")
            print("Please manually check server status and ensure server is running")
            self.log_result("Server Restart", False, "Server restart failed, test terminated")
            return self.test_results
        
        # Execute tests without API Key (when auth_enabled=false, expect 200)
        self.run_tests_without_auth(expect_auth_required=False)
        
        
        # ==================== Module 2: Tests with API Key ====================
        print("\n" + ">" * 60)
        print("Starting Module 2: Tests with API Key")
        print(">" * 60)
        
        # Update .env file, set POWERMEM_SERVER_AUTH_ENABLED to true
        # Always set POWERMEM_SERVER_API_KEYS to key1,key2,key3 for testing
        print("\nUpdating .env file: POWERMEM_SERVER_AUTH_ENABLED=true, POWERMEM_SERVER_API_KEYS=key1,key2,key3")
        test_api_keys = "key1,key2,key3"
        self.update_env_file(auth_enabled=True, api_keys=test_api_keys)
        
        # Restart server to apply new configuration
        if not self.restart_server():
            print("Error: Server restart failed, cannot continue testing")
            print("Please manually check server status and ensure server is running")
            self.log_result("Server Restart", False, "Server restart failed, test terminated")
            return self.test_results
        
        # Execute tests with API Key
        self.run_tests_with_auth()
        
        end_time = time.time()
        duration = end_time - start_time
        
        # Print test summary
        print("\n" + "=" * 60)
        print("Test Summary")
        print("=" * 60)
        print(f"Total tests: {self.test_results['total']}")
        print(f"Passed: {self.test_results['passed']} (green)")
        print(f"Failed: {self.test_results['failed']} (red)")
        print(f"Success rate: {self.test_results['passed'] / self.test_results['total'] * 100:.2f}%" if self.test_results['total'] > 0 else "N/A")
        print(f"Duration: {duration:.2f} seconds")
        print(f"End time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)
        
        # Print failed test details
        failed_tests = [r for r in self.test_results['details'] if '✗' in r['status']]
        if failed_tests:
            print("\nFailed tests:")
            for test in failed_tests:
                print(f"  - {test['test']}: {test['message']}")
        
        return self.test_results


def main():
    """Main function"""
    import argparse
    
    parser = argparse.ArgumentParser(description='powermem API Server Basic Functionality Test')
    parser.add_argument('--url', type=str, default='http://localhost:8000',
                       help='API server base URL (default: http://localhost:8000)')
    parser.add_argument('--api-key', type=str, default='key1',
                       help='API key (default: key1)')
    parser.add_argument('--output', type=str, default='results.json',
                       help='Test result output file (JSON format)')
    
    args = parser.parse_args()
    
    # Create tester and run tests
    tester = APITester(base_url=args.url, api_key=args.api_key)
    results = tester.run_all_tests()
    
    # If output file specified, save results
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\nTest results saved to: {args.output}")
    
    # Return exit code: 1 if failures, 0 otherwise
    exit_code = 1 if results['failed'] > 0 else 0
    return exit_code


if __name__ == "__main__":
    exit(main())


# ==================== Pytest compatibility ====================
# Dynamically create pytest-compatible test functions for all APITester test methods
# pytest cannot collect test classes with __init__, so we create standalone functions

def _create_pytest_wrapper(method_name):
    """Create a pytest-compatible wrapper function for an APITester method"""
    def wrapper():
        tester = APITester()
        method = getattr(tester, method_name)
        method()
    wrapper.__name__ = method_name
    wrapper.__doc__ = f"Pytest wrapper for {method_name}"
    return wrapper

# Get all test methods from APITester class
_test_methods = [name for name in dir(APITester) if name.startswith('test_') and callable(getattr(APITester, name))]

# Dynamically create pytest-compatible functions for each test method
for method_name in _test_methods:
    globals()[method_name] = _create_pytest_wrapper(method_name)
