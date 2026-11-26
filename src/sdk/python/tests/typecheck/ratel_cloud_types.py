"""The type of `Ratel.cloud`: set with `cloud`, `None` without (ratel-cloud.type-test.ts)."""

from typing import Optional

from ratel_ai import CloudOptions, Ratel, RatelCloudRuntime, ratel

with_cloud = ratel(cloud=True)
handle: RatelCloudRuntime = with_cloud.cloud
handle.status()

options: CloudOptions = {"api_key": "rtl_k", "tool_picker": "precise", "intent_graph": True}
with_options: Ratel[RatelCloudRuntime] = ratel(cloud=options)

local = ratel()
nothing: None = local.cloud
local_with_config: Ratel[None] = ratel(recall_top_k=3)


def wide(flag: bool) -> Optional[RatelCloudRuntime]:
    return ratel(cloud=flag).cloud
