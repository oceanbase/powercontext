# PowerContext memory for dsh-pet

This host-only adapter adds optional long-term memory to a dsh-pet instance in DSH Web.
It connects to an existing PowerContext Server. Use DSH `0.2.2-rc.1`, dsh-pet `0.4.8`,
and the adapter and Server from the same PowerContext release or checkout.

See the [DSH integration guide](https://github.com/oceanbase/powercontext/blob/master/docs/en/docs/integrations/dsh.md)
for setup, diagnostics, privacy, and how to disable the adapter or discard pending dialogue.

From the repository root, run `make js-test` for transport, retry, and archive checks.
With the pinned `dsh` CLI on PATH, run `make dsh-pet-runtime-test` to install the package into
an isolated profile and exercise real dsh-pet chat and PowerContext Server processing.
The test uses a deterministic local model fixture; its three-arm replay reports request bytes
and latency for native recent history, the same window with PowerContext, and full history.
It does not measure model quality or billed provider cost.
