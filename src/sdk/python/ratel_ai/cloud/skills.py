"""The managed-catalog write surface (``/skills``, Bearer project key)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from .transport import CloudHttpClient
from .types import (
    CloudSkill,
    ImportReport,
    ListSkillsResult,
    NewSkillInput,
    SkillStatus,
    UpdateSkillInput,
)

__all__ = ["SkillsClient"]


class SkillsClient:
    """Create, edit, publish, archive and import managed skills.

    `ratel_ai.cloud.testing.MockCloud` serves the same wire contract for tests.
    Reading the resolved published catalog is the job of the runtime loader,
    not of this client.
    """

    def __init__(self, http: CloudHttpClient) -> None:
        """Create the client on a shared `CloudHttpClient`."""
        self._http = http

    async def list(
        self, *, status: SkillStatus | None = None, end_user_id: str | None = None
    ) -> ListSkillsResult:
        """List the managed skills.

        Args:
            status: Only skills in this status.
            end_user_id: Only the skills scoped to this opaque end user.

        Returns:
            The ``GET /skills`` body.
        """
        return cast(
            ListSkillsResult,
            await self._http.json(
                "GET", "/skills", query={"status": status, "endUserId": end_user_id}
            ),
        )

    async def get(self, skill_id: str) -> CloudSkill:
        """Read one skill.

        Args:
            skill_id: The skill id.

        Returns:
            The skill.
        """
        return await self._skill("GET", f"/skills/{skill_id}")

    async def create(self, skill: NewSkillInput) -> CloudSkill:
        """Create a skill. It is a draft unless ``status`` says otherwise.

        Args:
            skill: The new skill, with wire keys.

        Returns:
            The created skill.
        """
        return await self._skill("POST", "/skills", body=skill)

    async def update(self, skill_id: str, changes: UpdateSkillInput) -> CloudSkill:
        """Edit fields.

        Put ``expectedVersion`` (the version you last read) in ``changes`` to guard
        the edit against concurrent writes: a stale value raises ``conflict``
        (reason ``version_conflict``). Without it, the last write wins.

        Args:
            skill_id: The skill id.
            changes: The fields to change, with wire keys.

        Returns:
            The updated skill.
        """
        return await self._skill("PATCH", f"/skills/{skill_id}", body=changes)

    async def publish(self, skill_id: str, *, expected_version: int | None = None) -> CloudSkill:
        """Publish a skill.

        Args:
            skill_id: The skill id.
            expected_version: Optional version guard, as for `update`.

        Returns:
            The published skill.
        """
        return await self._skill(
            "POST", f"/skills/{skill_id}/publish", body=_guard(expected_version)
        )

    async def archive(self, skill_id: str, *, expected_version: int | None = None) -> CloudSkill:
        """Archive a skill.

        Args:
            skill_id: The skill id.
            expected_version: Optional version guard, as for `update`.

        Returns:
            The archived skill.
        """
        return await self._skill(
            "POST", f"/skills/{skill_id}/archive", body=_guard(expected_version)
        )

    async def import_(self, skills: Sequence[NewSkillInput]) -> ImportReport:
        """Upsert skills by name in bulk, to onboard an existing skill set.

        The server reports what changed. Nothing is archived: removal is an
        explicit `archive`. (The TypeScript name is ``import``, a Python keyword.)

        Args:
            skills: The skills, with wire keys.

        Returns:
            The created, updated and unchanged names.
        """
        return cast(
            ImportReport,
            await self._http.json("POST", "/skills/import", body={"skills": list(skills)}),
        )

    async def _skill(self, method: str, path: str, body: object = None) -> CloudSkill:
        response = await self._http.json(method, path, body=body)
        return cast(CloudSkill, response["skill"])


def _guard(expected_version: int | None) -> dict[str, int]:
    return {} if expected_version is None else {"expectedVersion": expected_version}
