// Copyright (c) 2026 OceanBase.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// Replay discovery serialization with the pinned daemon's actual Go entities.
// Run from its checkout so its unmodified module supplies the imported types.
package main

import (
	"encoding/json"
	"os"

	"github.com/langgenius/dify-plugin-daemon/pkg/entities/plugin_entities"
)

func main() {
	var tools []plugin_entities.ToolDeclaration
	if err := json.NewDecoder(os.Stdin).Decode(&tools); err != nil {
		panic(err)
	}
	if err := json.NewEncoder(os.Stdout).Encode(tools); err != nil {
		panic(err)
	}
}
