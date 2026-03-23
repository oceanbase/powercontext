#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PowerMem CLI Regression Test Suite (pytest version)

Automated test script based on test report, covering:
- Basic operations (--help, --version, config init/show/validate/test)
- Data operations (add, update, delete, delete-all)
- Core commands (list, search, get)
- Other commands (stats, manage backup/restore/cleanup/migrate, shell)

Usage:
    pytest tests/regression/test_powermem_cli.py -v
    pytest tests/regression/test_powermem_cli.py -v -k "test_help"  # Run specific tests
"""

import subprocess
import json
import os
import sys
import time
import tempfile
import shutil
import re
import pytest
from typing import Tuple, List, Optional


# ==================== Configuration ====================

# Environment file path (same level as tests directory)
ENV_FILE = ".env"


# ==================== Fixtures ====================

@pytest.fixture(scope="session")
def cli_runner():
    """CLI runner fixture"""
    return CLIRunner(ENV_FILE)


@pytest.fixture(scope="module")
def test_data():
    """Test data fixture"""
    return {
        "memory_ids": [],
        "user_id": "cli_test_user",
        "agent_id": "cli_test_agent",
        "run_id": "cli_test_run",
    }


@pytest.fixture(scope="module")
def backup_dir():
    """Temporary backup directory fixture"""
    dir_path = tempfile.mkdtemp(prefix="pmem_test_backup_")
    yield dir_path
    # Cleanup
    if os.path.exists(dir_path):
        shutil.rmtree(dir_path)


@pytest.fixture(scope="session", autouse=True)
def cleanup_all_memories(cli_runner):
    """Delete all memories before and after test session"""
    # Delete all memories before tests start
    print("\n[SETUP] Deleting all memories before tests...")
    cli_runner.pmem("memory delete-all --confirm", input_text="y\n")
    
    yield
    
    # Delete all memories after tests complete
    print("\n[TEARDOWN] Deleting all memories after tests...")
    cli_runner.pmem("memory delete-all --confirm", input_text="y\n")


@pytest.fixture(scope="module", autouse=True)
def cleanup_test_data(cli_runner, test_data):
    """Cleanup test data after module tests complete"""
    yield
    # Clean up all memories for test user
    cli_runner.pmem(
        f"memory delete-all --user-id {test_data['user_id']} --confirm",
        input_text="y\n"
    )


# ==================== CLI Runner ====================

class CLIRunner:
    """CLI runner for executing commands"""
    
    def __init__(self, env_file: str = ".env"):
        self.env_file = env_file
    
    def run_command(self, cmd: str, timeout: int = 60, input_text: str = None) -> Tuple[int, str, str]:
        """
        Execute command and return results
        
        Returns:
            (return_code, stdout, stderr)
        """
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                input=input_text
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "Command timed out"
        except Exception as e:
            return -1, "", str(e)
    
    def pmem(self, args: str, timeout: int = 60, input_text: str = None) -> Tuple[int, str, str]:
        """Execute pmem command"""
        cmd = f"pmem -f {self.env_file} {args}"
        return self.run_command(cmd, timeout, input_text)


# ==================== Assertion Helper Functions ====================

def assert_success(rc: int, stdout: str, stderr: str, *expected_patterns):
    """
    Assert command execution succeeded
    
    Args:
        rc: Return code
        stdout: Standard output
        stderr: Standard error
        *expected_patterns: Expected patterns in output (variadic args, case-insensitive)
    """
    assert rc == 0, f"Command failed with return code: {rc}\nstdout: {stdout}\nstderr: {stderr}"
    
    if expected_patterns:
        combined = (stdout + stderr).lower()
        for pattern in expected_patterns:
            assert pattern.lower() in combined, \
                f"Expected pattern not found in output: {pattern}\nstdout: {stdout}\nstderr: {stderr}"


def assert_failure(rc: int, stdout: str, stderr: str, *expected_errors):
    """
    Assert command execution failed
    
    Args:
        rc: Return code
        stdout: Standard output
        stderr: Standard error
        *expected_errors: Expected error messages (variadic args, case-insensitive)
    """
    assert rc != 0, f"Command should have failed but succeeded\nstdout: {stdout}\nstderr: {stderr}"
    
    if expected_errors:
        combined = (stdout + stderr).lower()
        for error in expected_errors:
            assert error.lower() in combined, \
                f"Expected error message not found: {error}\nstdout: {stdout}\nstderr: {stderr}"


def assert_contains(rc: int, stdout: str, stderr: str, expected_patterns: List[str]):
    """
    Assert output contains all expected patterns (case-insensitive)
    
    Args:
        rc: Return code
        stdout: Standard output
        stderr: Standard error
        expected_patterns: List of expected patterns in output
    """
    combined = (stdout + stderr).lower()
    missing = []
    for pattern in expected_patterns:
        if pattern.lower() not in combined:
            missing.append(pattern)
    
    assert not missing, \
        f"Missing expected patterns in output: {missing}\nstdout: {stdout}\nstderr: {stderr}"


def extract_memory_id(stdout: str) -> Optional[str]:
    """Extract memory_id from output"""
    match = re.search(r'ID[=:-]?\s*(\d+)', stdout)
    if match:
        return match.group(1)
    return None


# ==================== Basic Operations Tests ====================

class TestHelpAndVersion:
    """Test --help and --version commands"""
    
    def test_pmem_help(self, cli_runner):
        """pmem --help should display help information with main commands"""
        rc, out, err = cli_runner.pmem("--help")
        assert_contains(rc, out, err, ["PowerMem CLI", "Examples", "Options", "Commands"])
    
    def test_pmem_version(self, cli_runner):
        """pmem --version should display version number"""
        rc, out, err = cli_runner.pmem("--version")
        assert_success(rc, out, err, "version")
    
    def test_powermem_cli_help(self, cli_runner):
        """powermem-cli --help should display help information"""
        rc, out, err = cli_runner.run_command("powermem-cli --help")
        assert_contains(rc, out, err, ["PowerMem CLI", "Examples", "Options", "Commands"])
    
    def test_powermem_cli_version(self, cli_runner):
        """powermem-cli --version should display version number"""
        rc, out, err = cli_runner.run_command("powermem-cli --version")
        assert_success(rc, out, err, "version")


class TestConfigShow:
    """Test config show command"""
    
    def test_config_show_basic(self, cli_runner):
        """config show should display configuration information"""
        rc, out, err = cli_runner.pmem("config show")
        assert_contains(rc, out, err, ["PowerMem Configuration", "DATABASE", "LLM", "EMBEDDING", "AGENT", "INTELLIGENT MEMORY", "PERFORMANCE", "SECURITY", "TELEMETRY", "AUDIT", "LOGGING", "GRAPH STORE", "SPARSE EMBEDDING", "QUERY REWRITE", "POWERMEM HTTP API SERVER"])
    
    def test_config_show_section_llm(self, cli_runner):
        """config show --section llm should display LLM configuration"""
        rc, out, err = cli_runner.pmem("config show --section llm")
        assert_contains(rc, out, err, ["LLM"])
    
    def test_config_show_section_embedder(self, cli_runner):
        """config show --section embedder should display Embedder configuration"""
        rc, out, err = cli_runner.pmem("config show --section embedder")
        assert_contains(rc, out, err, ["EMBEDDING"])
    
    def test_config_show_section_vector_store(self, cli_runner):
        """config show --section vector_store should display vector store configuration"""
        rc, out, err = cli_runner.pmem("config show --section vector_store")
        assert_contains(rc, out, err, ["DATABASE"])
    
    def test_config_show_section_all(self, cli_runner):
        """config show --section all should display all configuration"""
        rc, out, err = cli_runner.pmem("config show --section all")
        assert_contains(rc, out, err, ["LLM", "DATABASE", "EMBEDDING", "AGENT", "INTELLIGENT MEMORY", "PERFORMANCE", "SECURITY", "TELEMETRY", "AUDIT", "LOGGING", "GRAPH STORE", "SPARSE EMBEDDING", "QUERY REWRITE", "POWERMEM HTTP API SERVER"])
    
    def test_config_show_invalid_section(self, cli_runner):
        """config show --section invalid should fail"""
        rc, out, err = cli_runner.pmem("config show --section invalid_section")
        assert_failure(rc, out, err)


class TestConfigValidate:
    """Test config validate command"""
    
    def test_config_validate_basic(self, cli_runner):
        """config validate should validate configuration"""
        rc, out, err = cli_runner.pmem("config validate")
        assert_contains(rc, out, err, ["Validating", "configuration", "valid"])
    
    def test_config_validate_with_file(self, cli_runner):
        """config validate -f should validate specified config file"""
        rc, out, err = cli_runner.pmem(f"config validate -f {ENV_FILE}")
        assert_contains(rc, out, err, ["Validating", "configuration", "valid"])


class TestConfigTest:
    """Test config test command"""
    
    def test_config_test_basic(self, cli_runner):
        """config test should test connectivity of all components"""
        rc, out, err = cli_runner.pmem("config test")
        assert_contains(rc, out, err, ["Testing", "Database", "LLM", "Embedder"])
    
    def test_config_test_database(self, cli_runner):
        """config test --component database should test database connection"""
        rc, out, err = cli_runner.pmem("config test --component database")
        assert_contains(rc, out, err, ["Database", "Testing"])
    
    def test_config_test_llm(self, cli_runner):
        """config test --component llm should test LLM connection"""
        rc, out, err = cli_runner.pmem("config test --component llm")
        assert_contains(rc, out, err, ["LLM", "Connected"])
    
    def test_config_test_embedder(self, cli_runner):
        """config test --component embedder should test Embedder connection"""
        rc, out, err = cli_runner.pmem("config test --component embedder")
        assert_contains(rc, out, err, ["Embedder", "Connected", "dims"])
    
    def test_config_test_all(self, cli_runner):
        """config test --component all should test all components"""
        rc, out, err = cli_runner.pmem("config test --component all")
        assert_contains(rc, out, err, ["Testing", "Database", "LLM", "Embedder"])


# ==================== Data Operations Tests ====================

class TestMemoryAdd:
    """Test memory add command"""
    
    def test_memory_add_basic(self, cli_runner, test_data):
        """memory add should successfully add memory with output format [SUCCESS] Memory ADD: ID=xxx"""
        rc, out, err = cli_runner.pmem(
            f'memory add "CLI1 is my best friend" --user-id {test_data["user_id"]} '
            f'--agent-id {test_data["agent_id"]} --run-id {test_data["run_id"]}'
        )
        assert_success(rc, out, err)
        assert_contains(rc, out, err, ["[SUCCESS]", "Memory ADD", "ID="])
        memory_id = extract_memory_id(out)
        assert memory_id is not None, f"Failed to extract memory_id from output\nstdout: {out}"
        test_data["memory_ids"].append(memory_id)
    
    def test_memory_add_with_metadata(self, cli_runner, test_data):
        """memory add --metadata should successfully add memory with metadata, output format [SUCCESS] Memory ADD: ID=xxx"""
        rc, out, err = cli_runner.pmem(
            f'memory add "CLI2 is my best friend" --user-id {test_data["user_id"]} '
            f'--agent-id {test_data["agent_id"]} --run-id {test_data["run_id"]} '
            f'--metadata \'{{"category":"test"}}\''
        )
        assert_success(rc, out, err)
        assert_contains(rc, out, err, ["[SUCCESS]", "Memory ADD", "ID="])
        memory_id = extract_memory_id(out)
        assert memory_id is not None, f"Failed to extract memory_id from output\nstdout: {out}"
        test_data["memory_ids"].append(memory_id)
    
    def test_memory_add_no_infer(self, cli_runner, test_data):
        """memory add --no-infer should skip inference and add directly, output format [SUCCESS] Memory ADD: ID=xxx"""
        rc, out, err = cli_runner.pmem(
            f'memory add "CLI test memory no_infer" --user-id {test_data["user_id"]} '
            f'--agent-id {test_data["agent_id"]} --run-id {test_data["run_id"]} --no-infer'
        )
        assert_success(rc, out, err)
        assert_contains(rc, out, err, ["[SUCCESS]", "Memory ADD", "ID="])
        memory_id = extract_memory_id(out)
        assert memory_id is not None, f"Failed to extract memory_id from output\nstdout: {out}"
        test_data["memory_ids"].append(memory_id)
    
    def test_memory_add_without_ids(self, cli_runner):
        """memory add without ID parameters should succeed, output format [SUCCESS] Memory ADD: ID=xxx"""
        rc, out, err = cli_runner.pmem('memory add "I like drinking coffee"')
        assert_success(rc, out, err)
        assert_contains(rc, out, err, ["[SUCCESS]", "Memory ADD", "ID="])
    
    def test_memory_add_empty_content(self, cli_runner):
        """memory add with empty content should show warning"""
        rc, out, err = cli_runner.pmem('memory add ""')
        assert_contains(rc, out, err, ["WARNING", "No memory"])
    
    def test_memory_add_with_scope_and_type(self, cli_runner, test_data):
        """memory add --scope --memory-type should succeed, output format [SUCCESS] Memory ADD: ID=xxx"""
        rc, out, err = cli_runner.pmem(
            f'memory add "I like swimming" --user-id {test_data["user_id"]} '
            f'--scope private --memory-type working'
        )
        assert_success(rc, out, err)
        assert_contains(rc, out, err, ["[SUCCESS]", "Memory ADD", "ID="])


class TestMemoryUpdate:
    """Test memory update command"""
    
    def test_memory_update_basic(self, cli_runner, test_data):
        """memory update should successfully update memory"""
        rc, out, err = cli_runner.pmem(f'memory update {test_data["memory_ids"][0]} "I like drinking tea"')
        assert_success(rc, out, err)
        assert_contains(rc, out, err, ["[SUCCESS]", "Memory updated", "ID="])
    
    def test_memory_update_with_metadata(self, cli_runner, test_data):
        """memory update --metadata should successfully update metadata"""
        rc, out, err = cli_runner.pmem(
            f'memory update {test_data["memory_ids"][0]} "I like drinking coffee" --metadata \'{{"updated":true}}\''
        )
        assert_success(rc, out, err)
        assert_contains(rc, out, err, ["[SUCCESS]", "Memory updated", "ID="])
    
    def test_memory_update_nonexistent(self, cli_runner):
        """memory update with non-existent ID should fail"""
        rc, out, err = cli_runner.pmem('memory update 999999999999 "content"')
        assert_contains(rc, out, err, ["not found", "ERROR", "denied"])
    
    def test_memory_update_missing_args(self, cli_runner):
        """memory update with missing arguments should fail"""
        rc, out, err = cli_runner.pmem("memory update")
        assert_failure(rc, out, err)
        assert_contains(rc, out, err, ["Missing", "Error", "MEMORY_ID"])



class TestMemoryDelete:
    """Test memory delete command"""
    
    def test_memory_delete_basic(self, cli_runner, test_data):
        """memory delete --yes should successfully delete memory"""
        # First add a memory for deletion
        rc, out, err = cli_runner.pmem(
            f'memory add "Memory to be deleted" --user-id {test_data["user_id"]} '
            f'--agent-id {test_data["agent_id"]} --no-infer'
        )
        assert_contains(rc, out, err, ["[SUCCESS]", "Memory ADD", "ID="])
        delete_id = extract_memory_id(out)
        assert delete_id is not None, f"Failed to extract memory_id from output\nstdout: {out}"
        
        rc, out, err = cli_runner.pmem(f"memory delete {delete_id} --yes")
        assert_success(rc, out, err)
    
    def test_memory_delete_nonexistent(self, cli_runner):
        """memory delete with non-existent ID should fail"""
        rc, out, err = cli_runner.pmem("memory delete 999999999999 --yes")
        assert_contains(rc, out, err, ["not found", "ERROR", "denied"])
    
    def test_memory_delete_missing_id(self, cli_runner):
        """memory delete with missing ID should fail"""
        rc, out, err = cli_runner.pmem("memory delete --yes")
        assert_failure(rc, out, err, "Missing", "MEMORY_ID")


class TestMemoryDeleteAll:
    """Test memory delete-all command"""
    
    def test_memory_delete_all_nonexistent_user(self, cli_runner):
        """memory delete-all for non-existent user should succeed (delete 0 records)"""
        rc, out, err = cli_runner.pmem(
            "memory delete-all --user-id nonexistent_user_xyz --confirm",
            input_text="y\n"
        )
        assert_success(rc, out, err, "[SUCCESS] All matching memories deleted")
    
    def test_memory_delete_all_missing_confirm(self, cli_runner):
        """memory delete-all without --confirm should fail"""
        rc, out, err = cli_runner.pmem("memory delete-all --user-id test")
        assert_failure(rc, out, err, "Add --confirm flag to proceed")


# ==================== Core Commands Tests ====================

class TestMemoryList:
    """Test memory list command"""
    
    def test_memory_list_basic(self, cli_runner):
        """memory list should return memory list"""
        rc, out, err = cli_runner.pmem("memory list")
        assert_success(rc, out, err, "Found", "ID", "User ID", "Agent ID", "Content")
    
    def test_memory_list_by_user_id(self, cli_runner, test_data):
        """memory list --user-id should filter by user"""
        rc, out, err = cli_runner.pmem(f"memory list --user-id {test_data['user_id']}")
        assert_success(rc, out, err, "Found", "ID", "User ID", "Agent ID", "Content")
        assert_contains(rc, out, err, test_data['user_id'])
    
    def test_memory_list_by_agent_id(self, cli_runner, test_data):
        """memory list --agent-id should filter by agent"""
        rc, out, err = cli_runner.pmem(f"memory list --agent-id {test_data['agent_id']}")
        assert_success(rc, out, err)
        assert_contains(rc, out, err, test_data['agent_id'])
    
    def test_memory_list_with_limit(self, cli_runner):
        """memory list --limit should limit number of results"""
        rc, out, err = cli_runner.pmem("memory list --limit 5")
        assert_success(rc, out, err)
        assert_contains(rc, out, err, ["Showing", "5", "memories"])
    
    def test_memory_list_with_offset(self, cli_runner):
        """memory list --offset --limit should support pagination"""
        rc, out, err = cli_runner.pmem("memory list --offset 0 --limit 5")
        assert_success(rc, out, err, "Showing", "5", "memories", "offset", "0")
    
    def test_memory_list_with_sort(self, cli_runner):
        """memory list --sort-by --order should support sorting"""
        rc, out, err = cli_runner.pmem("memory list --sort-by created_at --order asc --limit 5")
        assert_success(rc, out, err, "Showing", "5", "memories")
    
    def test_memory_list_limit_zero(self, cli_runner):
        """memory list --limit 0 should return empty"""
        rc, out, err = cli_runner.pmem("memory list --limit 0")
        assert_contains(rc, out, err, ["No memories found"])
    
    def test_memory_list_combined(self, cli_runner, test_data):
        """memory list with combined parameters should work properly"""
        rc, out, err = cli_runner.pmem(
            f"memory list --user-id {test_data['user_id']} --limit 5 "
            f"--offset 0 --sort-by updated_at --order desc"
        )
        assert_success(rc, out, err, "Showing", "memories", "offset", "0", test_data['user_id'])


class TestMemorySearch:
    """Test memory search command"""
    
    def test_memory_search_basic(self, cli_runner):
        """memory search should return search results"""
        rc, out, err = cli_runner.pmem('memory search "CLI"')
        assert_success(rc, out, err)
    
    def test_memory_search_by_user_id(self, cli_runner, test_data):
        """memory search --user-id should filter by user"""
        rc, out, err = cli_runner.pmem(f'memory search "test" --user-id {test_data["user_id"]}')
        assert_success(rc, out, err)
    
    def test_memory_search_with_limit(self, cli_runner):
        """memory search --limit should limit number of results"""
        rc, out, err = cli_runner.pmem('memory search "test" --limit 3')
        assert_success(rc, out, err, "Found", "3", "results")
    
    def test_memory_search_with_threshold(self, cli_runner):
        """memory search --threshold should filter by similarity"""
        rc, out, err = cli_runner.pmem('memory search "test" --threshold 0.1')
        assert_success(rc, out, err)
    
    def test_memory_search_json_output(self, cli_runner):
        """memory search --json should return JSON format"""
        rc, out, err = cli_runner.pmem('memory search "test" --json')
        assert_success(rc, out, err, "memory", "metadata")
    
    def test_memory_search_empty(self, cli_runner):
        """memory search with empty content should return no results"""
        rc, out, err = cli_runner.pmem('memory search ""')
        assert_contains(rc, out, err, ["No results found"])
    
    def test_memory_search_combined(self, cli_runner, test_data):
        """memory search with combined parameters should work properly"""
        rc, out, err = cli_runner.pmem(
            f'memory search "test" --user-id {test_data["user_id"]} --limit 5 --json'
        )
        assert_success(rc, out, err, "memory", "metadata")
    
    def test_memory_search_with_filter(self, cli_runner):
        """memory search -f should support JSON filtering"""
        rc, out, err = cli_runner.pmem('memory search "test" -f \'{"category":"pref"}\'')
        assert_success(rc, out, err, "Found", "results")
    
    def test_memory_search_invalid_filter(self, cli_runner):
        """memory search with invalid filter JSON should fail"""
        rc, out, err = cli_runner.pmem('memory search "test" -f \'invalid json\'')
        assert_failure(rc, out, err, "Invalid filters JSON")


class TestMemoryGet:
    """Test memory get command"""
    
    def test_memory_get_basic(self, cli_runner, test_data):
        """memory get should return specified memory"""
        if not test_data["memory_ids"]:
            pytest.skip("No available memory_id")
        
        memory_id = test_data["memory_ids"][0]
        rc, out, err = cli_runner.pmem(f"memory get {memory_id}")
        assert_success(rc, out, err, "ID", "Metadata")
    
    def test_memory_get_json(self, cli_runner, test_data):
        """memory get --json should return JSON format"""
        if not test_data["memory_ids"]:
            pytest.skip("No available memory_id")
        
        memory_id = test_data["memory_ids"][0]
        rc, out, err = cli_runner.pmem(f"memory get {memory_id} --json")
        assert_success(rc, out, err, "id", "metadata")
    
    def test_memory_get_with_user_id(self, cli_runner, test_data):
        """memory get --user-id should verify user permissions"""
        if not test_data["memory_ids"]:
            pytest.skip("No available memory_id")
        
        memory_id = test_data["memory_ids"][0]
        rc, out, err = cli_runner.pmem(
            f"memory get {memory_id} --user-id no_exist_user"
        )
        assert_contains(rc, out, err, ["Memory not found", "ERROR"])
    
    def test_memory_get_nonexistent(self, cli_runner):
        """memory get with non-existent ID should fail"""
        rc, out, err = cli_runner.pmem("memory get 999999999999")
        assert_contains(rc, out, err, ["Memory not found", "ERROR"])
    
    def test_memory_get_missing_id(self, cli_runner):
        """memory get with missing ID should fail"""
        rc, out, err = cli_runner.pmem("memory get")
        assert_failure(rc, out, err, "Missing", "MEMORY_ID")


# ==================== Stats Tests ====================

class TestStats:
    """Test stats command"""
    
    def test_stats_basic(self, cli_runner):
        """stats should return statistics information"""
        rc, out, err = cli_runner.pmem("stats")
        assert_contains(rc, out, err, ["PowerMem Statistics", "Total Memories", "By Type", "Age Distribution", "Avg Importance", "Recent Growth"])
    
    def test_stats_by_user_id(self, cli_runner, test_data):
        """stats --user-id should filter by user"""
        rc, out, err = cli_runner.pmem(f"stats --user-id {test_data['user_id']}")
        assert_contains(rc, out, err, ["PowerMem Statistics", "Total Memories", "By Type", "Age Distribution", "Avg Importance", "Recent Growth"])
    
    def test_stats_by_agent_id(self, cli_runner, test_data):
        """stats --agent-id should filter by agent"""
        rc, out, err = cli_runner.pmem(f"stats --agent-id {test_data['agent_id']}")
        assert_contains(rc, out, err, ["PowerMem Statistics", "Total Memories", "By Type", "Age Distribution", "Avg Importance", "Recent Growth"])
    
    def test_stats_detailed(self, cli_runner):
        """stats --detailed should display detailed statistics"""
        rc, out, err = cli_runner.pmem("stats --detailed")
        assert_contains(rc, out, err, ["PowerMem Statistics", "Total Memories", "By Type", "Age Distribution", "Avg Importance", "Recent Growth"])
    
    def test_stats_json(self, cli_runner):
        """stats --json should return JSON format"""
        rc, out, err = cli_runner.pmem("stats --json")
        assert_success(rc, out, err, "total_memories", "by_type", "age_distribution", "avg_importance", "growth_trend")
    
    def test_stats_combined(self, cli_runner, test_data):
        """stats with combined parameters should work properly"""
        rc, out, err = cli_runner.pmem(
            f"stats --user-id {test_data['user_id']} --detailed --json"
        )
        assert_success(rc, out, err, "total_memories", "by_type", "age_distribution", "avg_importance", "growth_trend", "filters")
    
    def test_stats_nonexistent_user(self, cli_runner):
        """stats for non-existent user should return empty statistics"""
        rc, out, err = cli_runner.pmem("stats --user-id nonexistent_user_xyz")
        assert_contains(rc, out, err, ["PowerMem Statistics", "Total Memories", "Age Distribution"])


# ==================== Manage Backup Tests ====================

class TestManageBackup:
    """Test manage backup command"""
    
    def test_manage_backup_basic(self, cli_runner, backup_dir):
        """manage backup should successfully create backup file"""
        backup_file = os.path.join(backup_dir, "backup.json")
        rc, out, err = cli_runner.pmem(f"manage backup --output {backup_file}")
        assert_success(rc, out, err, "[SUCCESS] Backup completed", "memories", "File size")
        assert os.path.exists(backup_file), "Backup file was not created"
    
    def test_manage_backup_by_user_id(self, cli_runner, backup_dir, test_data):
        """manage backup --user-id should filter by user"""
        backup_file = os.path.join(backup_dir, "user_backup.json")
        rc, out, err = cli_runner.pmem(
            f"manage backup --user-id {test_data['user_id']} --output {backup_file}"
        )
        assert_success(rc, out, err, "[SUCCESS] Backup completed", "memories", "File size")
    
    def test_manage_backup_with_limit(self, cli_runner, backup_dir):
        """manage backup --limit should limit number of exported records"""
        backup_file = os.path.join(backup_dir, "limit_backup.json")
        rc, out, err = cli_runner.pmem(f"manage backup --limit 2 --output {backup_file}")
        assert_success(rc, out, err, "[SUCCESS] Backup completed", "memories", "File size")
    
    def test_manage_backup_json_output(self, cli_runner, backup_dir):
        """manage backup --json should return JSON format status"""
        backup_file = os.path.join(backup_dir, "backup_json.json")
        rc, out, err = cli_runner.pmem(f"manage backup --output {backup_file} --json")
        assert_success(rc, out, err, "status", "file", "count", "size_bytes")
    
    def test_manage_backup_auto_create_dir(self, cli_runner, backup_dir):
        """manage backup should automatically create directory"""
        new_dir_backup = os.path.join(backup_dir, "new_dir", "backup.json")
        rc, out, err = cli_runner.pmem(f"manage backup --output {new_dir_backup}")
        assert_success(rc, out, err, "[SUCCESS] Backup completed", "memories", "File size", "new_dir")


# ==================== Manage Restore Tests ====================

class TestManageRestore:
    """Test manage restore command"""
    
    @pytest.fixture(autouse=True)
    def setup_backup_file(self, cli_runner, backup_dir):
        """Create backup file for testing"""
        self.backup_file = os.path.join(backup_dir, "restore_test.json")
        cli_runner.pmem(f"manage backup --output {self.backup_file}")
    
    def test_manage_restore_dry_run(self, cli_runner):
        """manage restore --dry-run should preview restore"""
        if not os.path.exists(self.backup_file):
            pytest.skip("Backup file does not exist")
        
        rc, out, err = cli_runner.pmem(f"manage restore --input {self.backup_file} --dry-run")
        assert_contains(rc, out, err, ["DRY RUN", "Would restore", "Sample memories"])
    
    def test_manage_restore_dry_run_json(self, cli_runner):
        """manage restore --dry-run --json should return JSON format"""
        if not os.path.exists(self.backup_file):
            pytest.skip("Backup file does not exist")
        
        rc, out, err = cli_runner.pmem(
            f"manage restore --input {self.backup_file} --dry-run --json"
        )
        assert_success(rc, out, err, "dry_run", "would_restore")
    
    def test_manage_restore_with_user_id(self, cli_runner):
        """manage restore --user-id should override user ID"""
        if not os.path.exists(self.backup_file):
            pytest.skip("Backup file does not exist")
        
        rc, out, err = cli_runner.pmem(
            f"manage restore --input {self.backup_file} --user-id new_test_user --dry-run"
        )
        assert_success(rc, out, err, "DRY RUN", "Would restore")
    
    def test_manage_restore_missing_input(self, cli_runner):
        """manage restore without --input should fail"""
        rc, out, err = cli_runner.pmem("manage restore --dry-run")
        assert_failure(rc, out, err, "Error:", "Missing")
    
    def test_manage_restore_nonexistent_file(self, cli_runner):
        """manage restore with non-existent file should fail"""
        rc, out, err = cli_runner.pmem(
            "manage restore --input /nonexistent/file.json --dry-run"
        )
        assert_failure(rc, out, err, "Error:", "not exist", "Invalid value", "Path")
    
    def test_manage_restore_empty_file(self, cli_runner, backup_dir):
        """manage restore with empty file should fail"""
        empty_file = os.path.join(backup_dir, "empty.json")
        with open(empty_file, 'w') as f:
            f.write("")
        
        rc, out, err = cli_runner.pmem(f"manage restore --input {empty_file} --dry-run")
        assert_failure(rc, out, err, "Invalid JSON")
    
    def test_manage_restore_invalid_json(self, cli_runner, backup_dir):
        """manage restore with invalid JSON should fail"""
        bad_file = os.path.join(backup_dir, "bad.json")
        with open(bad_file, 'w') as f:
            f.write("not json")
        
        rc, out, err = cli_runner.pmem(f"manage restore --input {bad_file} --dry-run")
        assert_failure(rc, out, err, "Invalid JSON")


# ==================== Manage Cleanup Tests ====================

class TestManageCleanup:
    """Test manage cleanup command"""
    
    def test_manage_cleanup_dry_run(self, cli_runner):
        """manage cleanup --dry-run should preview cleanup"""
        rc, out, err = cli_runner.pmem("manage cleanup --dry-run")
        assert_contains(rc, out, err, ["DRY RUN", "Analysis Results"])
    
    def test_manage_cleanup_with_threshold(self, cli_runner):
        """manage cleanup --threshold should set deletion threshold"""
        rc, out, err = cli_runner.pmem("manage cleanup --threshold 0.2 --dry-run")
        assert_contains(rc, out, err, ["DRY RUN", "Analysis Results", "0.2"])
    
    def test_manage_cleanup_with_archive_threshold(self, cli_runner):
        """manage cleanup --archive-threshold should set archive threshold"""
        rc, out, err = cli_runner.pmem("manage cleanup --archive-threshold 0.3 --dry-run")
        assert_contains(rc, out, err, ["DRY RUN", "Analysis Results", "0.3", "0.1"])
    
    def test_manage_cleanup_by_user_id(self, cli_runner, test_data):
        """manage cleanup --user-id should filter by user"""
        rc, out, err = cli_runner.pmem(
            f"manage cleanup --user-id {test_data['user_id']} --dry-run"
        )
        assert_contains(rc, out, err, ["DRY RUN", "Analysis Results"])
    
    def test_manage_cleanup_json(self, cli_runner):
        """manage cleanup --json should return JSON format"""
        rc, out, err = cli_runner.pmem("manage cleanup --dry-run --json")
        assert_success(rc, out, err, "dry_run", "would_delete", "would_archive", "total_scanned")
    
    def test_manage_cleanup_combined(self, cli_runner, test_data):
        """manage cleanup with combined parameters should work properly"""
        rc, out, err = cli_runner.pmem(
            f"manage cleanup --user-id {test_data['user_id']} --threshold 0.15 "
            f"--archive-threshold 0.35 --dry-run --json"
        )
        assert_success(rc, out, err, "dry_run", "would_delete", "would_archive", "total_scanned", "0.15", "0.35")
    
    def test_manage_cleanup_nonexistent_user(self, cli_runner):
        """manage cleanup for non-existent user should return no memories"""
        rc, out, err = cli_runner.pmem(
            "manage cleanup --user-id nonexistent_user_xyz --dry-run"
        )
        assert_contains(rc, out, err, ["No memories found", "Analyzing"])


# ==================== Manage Migrate Tests (Commented Out) ====================
# 
# Note: migrate functionality is not needed for testing in this phase, 
# code is preserved for future use. Uncomment below to enable.
#
# class TestManageMigrate:
#     """Test manage migrate command"""
#     
#     def test_manage_migrate_dry_run(self, cli_runner):
#         """manage migrate --dry-run should preview migration"""
#         rc, out, err = cli_runner.pmem("manage migrate --target-store 0 --dry-run")
#         assert_contains(rc, out, err, ["DRY RUN", "Migration", "Preview"])
#     
#     def test_manage_migrate_source_target(self, cli_runner):
#         """manage migrate --source-store --target-store should specify source and target"""
#         rc, out, err = cli_runner.pmem(
#             "manage migrate --source-store 0 --target-store 1 --dry-run"
#         )
#         assert_contains(rc, out, err, ["Migration"])
#     
#     def test_manage_migrate_delete_source(self, cli_runner):
#         """manage migrate --delete-source should mark source for deletion"""
#         rc, out, err = cli_runner.pmem(
#             "manage migrate --target-store 1 --delete-source --dry-run"
#         )
#         assert_contains(rc, out, err, ["Delete Source"])
#     
#     def test_manage_migrate_json(self, cli_runner):
#         """manage migrate --json should return JSON format"""
#         rc, out, err = cli_runner.pmem("manage migrate --target-store 0 --dry-run --json")
#         assert_success(rc, out, err)
#     
#     def test_manage_migrate_missing_target(self, cli_runner):
#         """manage migrate without --target-store should fail"""
#         rc, out, err = cli_runner.pmem("manage migrate --dry-run")
#         assert_failure(rc, out, err, "target")


# ==================== Shell Tests ====================

class TestShell:
    """Test shell (interactive mode) command"""
    
    def test_shell_help(self, cli_runner):
        """shell help command should display help"""
        rc, out, err = cli_runner.pmem("shell", timeout=10, input_text="help\nexit\n")
        assert_contains(rc, out, err, ["Interactive", "PowerMem"])
    
    def test_shell_list(self, cli_runner):
        """shell list command should work properly"""
        rc, out, err = cli_runner.pmem("shell", timeout=15, input_text="list -l 1\nexit\n")
        assert_success(rc, out, err, "Found", "memories")
    
    def test_shell_exit(self, cli_runner):
        """shell exit should exit normally"""
        rc, out, err = cli_runner.pmem("shell", timeout=10, input_text="exit\n")
        assert_contains(rc, out, err, ["Goodbye"])
    
    def test_shell_quit(self, cli_runner):
        """shell quit should exit normally"""
        rc, out, err = cli_runner.pmem("shell", timeout=10, input_text="quit\n")
        assert_contains(rc, out, err, ["Goodbye"])


# ==================== Main Entry Point ====================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
