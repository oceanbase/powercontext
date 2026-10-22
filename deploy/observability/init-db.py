"""Initialize only the isolated reference database before starting concurrent roles."""

from __future__ import annotations

import asyncio

from sqlalchemy.engine import make_url

from powercontext.builtin.runtime.composition import open_builtin_runtime
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.server.authz import PrincipalRef
from powercontext.server.authz.composition import open_builtin_access_control
from powercontext.server.settings import ServerSettings


async def main() -> None:
    settings = ServerSettings()
    if settings.database.kind != "oceanbase":
        message = "Reference initialization requires its isolated OceanBase database"
        raise RuntimeError(message)
    url = make_url(settings.database.url.get_secret_value())
    if url.host != "reference-oceanbase" or url.database != "powercontext":
        message = "Reference initialization refuses a database outside the isolated demo"
        raise RuntimeError(message)
    principal = PrincipalRef(type="service", id="server-token", description="PowerContext static bearer")
    config = BuiltinConfig(database=settings.database, runtime=settings.runtime, inference=settings.inference)
    async with (
        open_builtin_access_control(config.database, bootstrap_administrators=(principal,)),
        open_builtin_runtime(config),
    ):
        pass


if __name__ == "__main__":
    asyncio.run(main())
