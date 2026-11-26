"""The type of `Ratel.platform`: set with `platform`, `None` without.

The port of ratel-platform.type-test.ts.
"""

from typing import Optional

from ratel_ai import PlatformOptions, Ratel, RatelPlatformRuntime, ratel

with_platform = ratel(platform=True)
handle: RatelPlatformRuntime = with_platform.platform
handle.status()

options: PlatformOptions = {"api_key": "rtl_k", "tool_picker": "precise", "intent_graph": True}
with_options: Ratel[RatelPlatformRuntime] = ratel(platform=options)

local = ratel()
nothing: None = local.platform
local_with_config: Ratel[None] = ratel(recall_top_k=3)


def wide(flag: bool) -> Optional[RatelPlatformRuntime]:
    return ratel(platform=flag).platform
