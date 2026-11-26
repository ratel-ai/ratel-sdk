import { type Ratel, type RatelCloudRuntime, type RatelConfig, ratel } from "../index.js";
import { referenceAdapter } from "../testkit/reference-adapter.js";

type Equal<A, B> =
  (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false;
const assertType = <T extends true>(_proof?: T): void => {};

// `ratel({ cloud })` gives a defined `r.cloud`, on the core and on each adapted view.
const withCloud = ratel({ cloud: true });
assertType<Equal<typeof withCloud.cloud, RatelCloudRuntime>>();
assertType<Equal<ReturnType<typeof withCloud.adaptTo>["cloud"], RatelCloudRuntime>>();
const adaptedWithCloud = withCloud.adaptTo(referenceAdapter());
assertType<Equal<typeof adaptedWithCloud.cloud, RatelCloudRuntime>>();
void withCloud.cloud.skills.list();

const withCloudObject = ratel({ cloud: { apiKey: "rtl_k", toolPicker: "precise" } });
assertType<Equal<typeof withCloudObject.cloud, RatelCloudRuntime>>();

// `ratel()` without `cloud` gives `undefined` in the type.
const local = ratel();
assertType<Equal<typeof local.cloud, undefined>>();
const localWithConfig = ratel({ recallTopK: 3 });
assertType<Equal<typeof localWithConfig.cloud, undefined>>();
const adaptedLocal = localWithConfig.adaptTo(referenceAdapter());
assertType<Equal<typeof adaptedLocal.cloud, undefined>>();

// A config of the wide type gives the wide slot.
declare const config: RatelConfig;
const wide: Ratel = ratel(config);
assertType<Equal<typeof wide.cloud, RatelCloudRuntime | undefined>>();

// The cloud option takes `true` or an object, and nothing else.
// @ts-expect-error `cloud: false` is not a valid option: omit it
ratel({ cloud: false });
