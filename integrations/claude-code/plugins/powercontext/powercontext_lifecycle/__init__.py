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

# Generated from src/powercontext_integration_lifecycle/__init__.py; do not edit.
# Edit the source and run `make integration-lifecycle` instead.

"""Standard-library helpers that the PowerContext host integrations share.

Claude Code, Codex, WorkBuddy, and Hermes install their plugin directories without the PowerContext package, so each
plugin ships generated copies of these modules. Claude Code and Codex ship the whole package as
``powercontext_lifecycle``; WorkBuddy and Hermes ship single modules where they already import them. Edit the modules
in ``src/powercontext_integration_lifecycle/`` and run ``make integration-lifecycle``; ``make check`` rejects a copy
that is missing or has drifted.

Claude Code runs its hooks with the ``python3`` on ``PATH``, often the macOS system Python 3.9, and Hermes installs no
dependency for its plugin. A module may therefore import only the standard library and must run on Python 3.9. A
single-module copy cannot reach its siblings, so a module may not import them either. The package sits outside
``powercontext`` so that importing it loads nothing from the SDK.
"""
