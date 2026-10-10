#!/bin/sh
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

set -eu

# Diagnostic only: the final step's reward decides the trial.
if grep -q 'rebuilt' /workspace/README.md && ! grep -q 'rebuit' /workspace/README.md; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi

# Harbor keeps the container for the recall session, so leave it only the corrected README: notes the agent wrote
# into the workspace would otherwise stand in for memory of the conversation. The reward above is already written;
# a file the reset could not remove is reported in the verifier output and the README is rewritten regardless.
find /workspace -mindepth 1 -delete || echo 'workspace reset incomplete' >&2
printf '# Search service\n\nResults are cached per query; the cache is cleared when the index is rebuilt.\n' > /workspace/README.md
