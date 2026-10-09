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

"""The explicitly selected DSH tools; new Server operations are not added implicitly."""

TOOLS = {
    "pc_search": (
        "search_memory",
        "Find relevant Atomic Memory with exact artifact references and state versions.",
        "检索相关历史记忆及精确引用。",
    ),
    "pc_memory_list": (
        "list_memory_entries",
        "Inventory Atomic Memory with pagination; include forgotten, merged and retired records for an explicit audit.",
        "盘点记忆，可包含停用条目。",
    ),
    "pc_memory_get": (
        "get_memory_entry",
        "Read an exact Atomic Memory artifact or a full legacy historical citation. Current content includes its real"
        " content ETag for correction; historical reads have no current write ETag. Choose one identity.",
        "按精确制品引用读取原子记忆，或按完整旧引用读取历史；当前内容附带修订所需的 ETag。",
    ),
    "pc_memory_state": (
        "get_atomic_memory_state",
        "Read the current Atomic Memory reference, lifecycle and state_version before an explicit lifecycle change.",
        "读取原子记忆当前引用、生命周期及状态版本。",
    ),
    "pc_remember": (
        "remember_memory",
        "Save curated Memory on an explicit user request or trusted application condition.",
        "按明确保存意图写入整理后的记忆。",
    ),
    "pc_memory_revise": (
        "revise_memory_entry",
        "Correct Atomic Memory using its exact current artifact and real content ETag from pc_memory_get as if_match."
        " Supply complete kind/text. On conflict read again and verify intent. Legacy citation writes are unsupported.",
        "用精确原子记忆引用和当前内容 ETag 纠正记忆，冲突需重新读取并核对意图。",
    ),
    "pc_memory_retire": (
        "retire_memory_entry",
        "Forget Atomic Memory using its exact current artifact and state_version from search, list or pc_memory_state."
        " This sets recoverable forgotten state and preserves history. Legacy citation writes are unsupported.",
        "用精确原子记忆引用和状态版本设置可恢复的遗忘状态，保留历史。",
    ),
    "pc_prepare_context": (
        "prepare_context",
        "Prepare historical context for a focused question; this does not save Memory.",
        "为具体问题准备历史上下文，不写入记忆。",
    ),
    "pc_capture_source": (
        "capture_content_source",
        "Explicitly capture evidence with a stable source_id; acceptance is not Memory extraction.",
        "以稳定标识显式采集证据，接收不代表已提取记忆。",
    ),
    "pc_handoff_activate": (
        "activate_handoff",
        "Activate a Handoff from a boundary Source; generated and ignored are distinct outcomes.",
        "按边界证据激活交接，保留生成与忽略状态。",
    ),
    "pc_handoff_prepare": (
        "prepare_handoff",
        "Prepare a temporary Handoff draft from exact evidence.",
        "按精确证据准备临时交接草稿。",
    ),
    "pc_handoff_finalize": (
        "finalize_handoff",
        "Validate a draft and return the complete transferable PreparedHandoff.",
        "校验草稿并返回完整临时交接对象。",
    ),
    "pc_handoff_commit": (
        "commit_handoff",
        "Persist a complete PreparedHandoff only for an explicitly requested milestone.",
        "仅按明确的持久化意图提交完整交接。",
    ),
    "pc_handoff_continue": (
        "continue_handoff",
        "Read a complete prepared Handoff or an exact/latest persisted revision.",
        "读取完整临时交接或指定持久版本。",
    ),
    "pc_experience_generate": (
        "generate_experience",
        "Generate an Experience candidate from exact evidence; approval is external.",
        "按精确证据生成经验候选，审批在插件外完成。",
    ),
    "pc_experience_get": (
        "get_experience",
        "Read approved Experience using an exact Artifact reference, not a candidate_id.",
        "按精确产物引用读取经验，不使用候选标识代替。",
    ),
    "pc_skill_generate": (
        "generate_skill",
        "Generate a Skill candidate; this does not install a Dify workspace Skill.",
        "生成技能候选，不会自动安装 Dify 工作区技能。",
    ),
    "pc_skill_get": ("get_skill", "Read a Skill Artifact using its exact reference.", "按精确引用读取技能产物。"),
    "pc_review_list": (
        "list_artifact_candidates",
        "Inspect candidates with stable status/family/cursor pagination; no approval.",
        "分页查看候选，保持筛选与游标，不执行审批。",
    ),
    "pc_review_get": (
        "get_artifact_candidate",
        "Inspect one candidate, its evidence and version; no approval.",
        "查看候选内容、证据和版本，不执行审批。",
    ),
}
