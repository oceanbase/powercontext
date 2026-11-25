# CI package download cache

The service in `scripts/ci_package_cache.py` shares Debian and Python package
downloads across independent CI containers. It stores original distribution
files, while clients still create fresh environments and execute their real
installers. It does not share installed environments, test results or sessions.

Build from the repository root:

```sh
docker build -f deploy/ci-package-cache/Dockerfile -t powercontext-package-cache .
```

Deploy the resulting image with a persistent volume mounted at `/cache` and
port 8080 reachable from the nested task containers. Preserve the volume when
replacing the service. The process needs write access to that directory. Use
the image digest in the deployment and retain the source revision with the
deployment record.

Set `PC_CI_PACKAGE_CACHE_ENDPOINT` on internal runner templates to the reachable
service base URL. The shared Harbor workflow selects its `/pypi/simple/` index
and Debian repository routes. Explicit task mirror overrides take precedence;
unconfigured runners retain their existing mirror behavior. The endpoint is
infrastructure configuration, not a repository-specific URL in each job.

| Route | Behavior |
| --- | --- |
| `/pypi/simple/<project>/` | Refresh package metadata and route file links through the service, preserving hashes and compatibility/yanked attributes. |
| `/pypi/packages/...` | Persist complete wheels, source distributions and distribution metadata. |
| `/debian/...`, `/debian-security/...` | Preserve signed repository bytes; persist package files and content-addressed indexes while refreshing mutable metadata. |
| `/healthz` | Service readiness. |

An immutable object has one fill owner; concurrent requests for that object
wait for the same result. Other objects can download concurrently. Aliyun is
the first upstream for package files and Debian repositories, with official
fallback owned by the same fill operation. Python package metadata uses the
official Simple index first and Aliyun as fallback, so a successful but stale
mirror index does not hide a newly released version. File links still use the
shared cache and prefer Aliyun for the large distribution files.
Incomplete transfers are not published. The guarantee applies while the
object remains in this persistent store; deleting the store requires new fills.

HTTP cache endpoints are trusted only for the configured host by the internal
installers. Upstream HTTPS and the installers' package hash and repository
signature verification remain enabled. Do not route an arbitrary package URL
through this service without a supported repository route.

The service completes a cold fill before responding, so cache-backed pip and
uv installers use a 180-second socket read budget. This accommodates a shared
fill and origin fallback without changing Harbor's 360-second setup deadline
or any test deadline. Explicit `PIP_DEFAULT_TIMEOUT` / `PIP_TIMEOUT` and
`UV_HTTP_TIMEOUT` runner settings take precedence. Direct mirror installations
retain their existing socket timeout behavior.

Validate from fresh nested containers with empty client caches: simultaneous
installs must share artifact fills, subsequent installs and a service restart
must reuse them, and new upstream versions must remain visible. Index requests
are distinct from immutable artifact fetches. Inspect the service's structured
events alongside the real installer logs and the complete Harbor evaluation.
