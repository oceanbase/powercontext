"""Print interpreter identity for the Unicode compatibility matrix."""

import hashlib
import platform
import sys
import sysconfig

print("executable:", sys.executable)
print("version:", sys.version.replace("\n", " "))
print("version_info:", sys.version_info[:])
print("implementation:", sys.implementation.name)
print("platform:", platform.platform())
print("sysconfig_platform:", sysconfig.get_platform())
print("maxunicode:", sys.maxunicode)
try:
    import unicodedata

    print("unicodedata_version:", unicodedata.unidata_version)
except Exception as exc:  # pragma: no cover - defensive
    print("unicodedata_error:", type(exc).__name__, str(exc))
print("byteorder:", sys.byteorder)
print("abi_flags:", getattr(sys, "abiflags", None))
with open(sys.executable, "rb") as handle:
    print("exe_sha256:", hashlib.sha256(handle.read()).hexdigest())
