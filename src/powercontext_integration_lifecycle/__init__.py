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

"""Standard-library helpers that the PowerContext host integrations share.

Claude Code, Codex, WorkBuddy, and Hermes install their plugin directories without the PowerContext package, so each
plugin ships a generated copy of these modules where it already imports them. Edit them here and run
``make integration-lifecycle``; ``make check`` rejects a copy that is missing or has drifted.

Claude Code runs its hooks with the ``python3`` on ``PATH``, often the macOS system Python 3.9, and Hermes installs no
dependency for its plugin. A module may therefore import only the standard library and must run on Python 3.9. The
copies do not keep this package's layout, so a module may not import its siblings either. The package sits outside
``powercontext`` so that importing it loads nothing from the SDK.
"""
