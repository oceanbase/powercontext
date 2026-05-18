"""Skill distillation and merging.

Extracts reusable procedural skills (operation guides with steps and pitfalls)
from conversations and merges semantically similar skills.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from ..prompts.skill_prompts import SKILL_DISTILL_PROMPT, SKILL_MERGE_PROMPT
from ..utils.utils import strip_think_tags

logger = logging.getLogger(__name__)


class SkillManager:
    """Distill and merge procedural skills via LLM."""

    def __init__(self, llm):
        """
        Args:
            llm: An LLM instance that exposes ``generate_response(messages=...)``.
        """
        self.llm = llm

    # -- Distillation --

    def distill(
        self,
        messages: List[Dict[str, str]],
        today: str,
    ) -> List[Dict[str, Any]]:
        """Extract reusable procedural skills from a conversation (sync).

        Returns:
            List of ``{"title": str, "description": str, "tags": list,
                        "procedure": {"prerequisites": list, "steps": list, "pitfalls": list}}``.
        """
        user_content = self._build_distill_input(messages)
        if user_content is None:
            return []

        try:
            response = self.llm.generate_response(
                messages=[
                    {"role": "system", "content": SKILL_DISTILL_PROMPT.replace("{today}", today)},
                    {"role": "user", "content": user_content},
                ],
            )
            return self._parse_skills(response)
        except Exception as e:
            logger.warning("SkillManager.distill failed: %s", e)
            return []

    async def adistill(
        self,
        messages: List[Dict[str, str]],
        today: str,
    ) -> List[Dict[str, Any]]:
        """Async variant of :meth:`distill`."""
        import asyncio

        user_content = self._build_distill_input(messages)
        if user_content is None:
            return []

        try:
            response = await asyncio.to_thread(
                self.llm.generate_response,
                messages=[
                    {"role": "system", "content": SKILL_DISTILL_PROMPT.replace("{today}", today)},
                    {"role": "user", "content": user_content},
                ],
            )
            return self._parse_skills(response)
        except Exception as e:
            logger.warning("SkillManager.adistill failed: %s", e)
            return []

    # -- Merging --

    def merge(self, existing: str, new: str) -> Dict[str, Any]:
        """Judge whether two skills should be merged or kept separate (sync).

        Returns:
            ``{"action": "merge", "title": str, "description": str, "procedure": dict}``
            or ``{"action": "skip"}``.
            Falls back to ``{"action": "skip"}`` on failure.
        """
        try:
            response = self.llm.generate_response(
                messages=[
                    {"role": "system", "content": SKILL_MERGE_PROMPT},
                    {"role": "user", "content": f"Skill A:\n{existing}\n\nSkill B:\n{new}"},
                ],
            )
            return self._parse_merge(response)
        except Exception as e:
            logger.warning("SkillManager.merge failed: %s", e)
            return {"action": "skip"}

    async def amerge(self, existing: str, new: str) -> Dict[str, Any]:
        """Async variant of :meth:`merge`."""
        import asyncio

        try:
            response = await asyncio.to_thread(
                self.llm.generate_response,
                messages=[
                    {"role": "system", "content": SKILL_MERGE_PROMPT},
                    {"role": "user", "content": f"Skill A:\n{existing}\n\nSkill B:\n{new}"},
                ],
            )
            return self._parse_merge(response)
        except Exception as e:
            logger.warning("SkillManager.amerge failed: %s", e)
            return {"action": "skip"}

    # -- Internal helpers --

    @staticmethod
    def _build_distill_input(messages: List[Dict[str, str]]) -> Optional[str]:
        """Build the user-content string for the distillation prompt."""
        if not messages:
            return None
        lines = []
        for m in messages:
            role = m.get("role", "")
            content = m.get("content", "")
            if role and content and role != "system":
                lines.append(f"{role}: {content}")
        return "\n".join(lines) if lines else None

    @staticmethod
    def _parse_skills(response: str) -> List[Dict[str, Any]]:
        """Parse LLM response into a list of skill dicts."""
        stripped = strip_think_tags(response).strip()
        json_match = re.search(r"\{[\s\S]*\}", stripped)
        if not json_match:
            return []
        try:
            data = json.loads(json_match.group(0))
            skills = data.get("skills", [])
            results = []
            for skill in skills:
                if not isinstance(skill, dict):
                    continue
                title = skill.get("title", "")
                description = skill.get("description", "")
                procedure = skill.get("procedure")
                if not description or not isinstance(procedure, dict):
                    continue
                # Normalize tags
                raw_tags = skill.get("tags", [])
                if isinstance(raw_tags, str):
                    tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
                elif isinstance(raw_tags, list):
                    tags = raw_tags
                else:
                    tags = []
                results.append({
                    "title": title if isinstance(title, str) else "",
                    "description": description,
                    "tags": tags,
                    "procedure": procedure,
                })
            return results
        except (json.JSONDecodeError, AttributeError):
            return []

    @staticmethod
    def _parse_merge(response: str) -> Dict[str, Any]:
        """Parse LLM merge response."""
        stripped = strip_think_tags(response).strip()
        json_match = re.search(r"\{[\s\S]*\}", stripped)
        if json_match:
            try:
                data = json.loads(json_match.group(0))
                action = data.get("action", "skip")
                if action == "merge":
                    title = data.get("title", "")
                    description = data.get("description", "")
                    procedure = data.get("procedure")
                    if description and isinstance(procedure, dict):
                        return {
                            "action": "merge",
                            "title": title,
                            "description": description,
                            "procedure": procedure,
                        }
                return {"action": "skip"}
            except (json.JSONDecodeError, AttributeError):
                pass
        return {"action": "skip"}
