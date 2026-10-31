# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Build historical fixtures without later additive migration revisions."""


def remove_memory_processing_revision(directory, manifest):
    remove_context_usage_revision(directory, manifest)
    revision = "v1_5_0_005"
    resources = manifest["revision_resources"].pop(revision)
    for resource in (*resources, f"versions/{revision}_memory_processing.py", f"mysql/versions/{revision}.py"):
        (directory / resource).unlink()
    del manifest["sqlite_fingerprints"][revision]
    del manifest["domain_readiness"][revision]
    manifest["mysql_state_layouts"].remove(f"schemas/mysql-{revision}.json")
    for table in ("records", "sources", "artifacts"):
        manifest["affected_objects"].remove(f"pc_memory_processing_{table}")


def remove_context_usage_revision(directory, manifest):
    revision = "v1_5_0_006"
    if revision not in manifest["revision_resources"]:
        return
    resources = manifest["revision_resources"].pop(revision)
    for resource in (*resources, f"versions/{revision}_context_usage.py", f"mysql/versions/{revision}.py"):
        (directory / resource).unlink()
    del manifest["sqlite_fingerprints"][revision]
    del manifest["domain_readiness"][revision]
    manifest["mysql_state_layouts"].remove(f"schemas/mysql-{revision}.json")
    for table in ("pc_context_usage_records", "pc_context_usage_feedback"):
        manifest["affected_objects"].remove(table)
