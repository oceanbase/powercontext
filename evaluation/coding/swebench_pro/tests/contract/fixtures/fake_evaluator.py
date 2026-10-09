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

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--raw_sample_path", required=True)
parser.add_argument("--patch_path", required=True)
parser.add_argument("--output_dir", required=True)
parser.add_argument("--dockerhub_username", required=True)
parser.add_argument("--scripts_dir", required=True)
parser.add_argument("--num_workers", required=True)
parser.add_argument("--use_local_docker", action="store_true")
parser.add_argument("--docker_platform", required=True)
parser.add_argument("--redo", action="store_true")
parser.add_argument("--block_network", action="store_true")
args = parser.parse_args()

output_dir = Path(args.output_dir)
output_dir.mkdir(parents=True, exist_ok=True)
invocation = vars(args)
(output_dir / "invocation.json").write_text(json.dumps(invocation, sort_keys=True))
default_id = json.loads(Path(args.raw_sample_path).read_text().splitlines()[0]).get(
    "instance_id", "instance_flipt-io__flipt-518ec324b66a07fdd95464a5e9ca5fe7681ad8f9"
)
payload = json.loads(os.environ.get("FAKE_EVAL_RESULT", json.dumps({default_id: True})))
(output_dir / "eval_results.json").write_text(json.dumps(payload))
if "FAKE_EVAL_OUTPUT" in os.environ:
    prediction = json.loads(Path(args.patch_path).read_text())[0]
    instance_dir = output_dir / prediction["instance_id"]
    instance_dir.mkdir(parents=True, exist_ok=True)
    prefix = prediction["prefix"]
    (instance_dir / f"{prefix}_output.json").write_text(os.environ["FAKE_EVAL_OUTPUT"])
    (instance_dir / f"{prefix}_stdout.log").write_text(os.environ.get("FAKE_EVAL_STDOUT", ""))
    (instance_dir / f"{prefix}_stderr.log").write_text(os.environ.get("FAKE_EVAL_STDERR", ""))
print("FAKE OFFICIAL EVALUATOR")
