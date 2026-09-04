# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Shared identity limits that remain safe for utf8mb4 relational indexes."""

MAX_SCOPE_ID_LENGTH = 256
MAX_SCOPE_TITLE_LENGTH = 256
MAX_SCOPE_SUMMARY_LENGTH = 2_000
MAX_SCOPE_EXTERNAL_REFERENCE_KIND_LENGTH = 128
MAX_SCOPE_EXTERNAL_REFERENCE_VALUE_LENGTH = 2_000
MAX_SCOPE_IDEMPOTENCY_KEY_LENGTH = 256
MAX_SCOPE_BINDING_INTEGRATION_LENGTH = 128
MAX_SCOPE_BINDING_KIND_LENGTH = 64
MAX_SCOPE_BINDING_EXTERNAL_ID_LENGTH = 256
MAX_SOURCE_ID_LENGTH = 256
MAX_SOURCE_TYPE_LENGTH = 128
MAX_SOURCE_OBSERVATION_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_FAMILY_LENGTH = 128
MAX_ARTIFACT_ID_LENGTH = 128
MAX_BINDING_NAME_LENGTH = 128
MAX_EXTERNAL_SKILL_NAME_LENGTH = 128
MAX_EXTERNAL_SKILL_DESCRIPTION_LENGTH = 2_000
MAX_EXTERNAL_SKILL_HOST_ID_LENGTH = 128
MAX_EXTERNAL_SKILL_LOCATOR_LENGTH = 2_000
