import { type Ratel, type RatelConfig, type RatelPlatformRuntime, ratel } from "../index.js";
import { referenceAdapter } from "../testkit/reference-adapter.js";

type Equal<A, B> =
  (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false;
const assertType = <T extends true>(_proof?: T): void => {};

// `ratel({ platform })` gives a defined `r.platform`, on the core and on each adapted view.
const withPlatform = ratel({ platform: true });
assertType<Equal<typeof withPlatform.platform, RatelPlatformRuntime>>();
assertType<Equal<ReturnType<typeof withPlatform.adaptTo>["platform"], RatelPlatformRuntime>>();
const adaptedWithPlatform = withPlatform.adaptTo(referenceAdapter());
assertType<Equal<typeof adaptedWithPlatform.platform, RatelPlatformRuntime>>();
void withPlatform.platform.skills.list();

const withPlatformObject = ratel({ platform: { apiKey: "rtl_k", toolPicker: "precise" } });
assertType<Equal<typeof withPlatformObject.platform, RatelPlatformRuntime>>();

// `ratel()` without `platform` gives `undefined` in the type.
const local = ratel();
assertType<Equal<typeof local.platform, undefined>>();
const localWithConfig = ratel({ recallTopK: 3 });
assertType<Equal<typeof localWithConfig.platform, undefined>>();
const adaptedLocal = localWithConfig.adaptTo(referenceAdapter());
assertType<Equal<typeof adaptedLocal.platform, undefined>>();

// A config of the wide type gives the wide slot.
declare const config: RatelConfig;
const wide: Ratel = ratel(config);
assertType<Equal<typeof wide.platform, RatelPlatformRuntime | undefined>>();

// The platform option takes `true` or an object, and nothing else.
// @ts-expect-error `platform: false` is not a valid option: omit it
ratel({ platform: false });
