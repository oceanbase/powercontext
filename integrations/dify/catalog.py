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
        "Find relevant historical Memory with exact citations.",
        "检索相关历史记忆及精确引用。",
    ),
    "pc_memory_list": (
        "list_memory_entries",
        "Inventory current Memory; optionally include inactive entries.",
        "盘点记忆，可包含停用条目。",
    ),
    "pc_memory_get": ("get_memory_entry", "Read an exact Memory citation.", "按精确引用读取记忆。"),
    "pc_remember": (
        "remember_memory",
        "Save curated Memory on an explicit user request or trusted application condition.",
        "按明确保存意图写入整理后的记忆。",
    ),
    "pc_memory_revise": (
        "revise_memory_entry",
        "Correct Memory using its exact citation; conflicts require renewed intent.",
        "按精确引用纠正记忆，冲突需重新核对意图。",
    ),
    "pc_memory_retire": (
        "retire_memory_entry",
        "Retire Memory using its exact citation, preserving history.",
        "按精确引用停用记忆并保留历史。",
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
        "list_candidates",
        "Inspect candidates with stable status/family/cursor pagination; no approval.",
        "分页查看候选，保持筛选与游标，不执行审批。",
    ),
    "pc_review_get": (
        "get_candidate",
        "Inspect one candidate, its evidence and version; no approval.",
        "查看候选内容、证据和版本，不执行审批。",
    ),
}
