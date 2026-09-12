# Privacy policy

This plugin sends data to the services configured by your Dify administrator. The PowerContext tool plugin communicates with
the configured PowerContext Server. The Agent strategy also invokes the model and business tools selected in Dify. There is
no additional analytics endpoint in this plugin's code.

Automatic capture can include the textual user request, visible model response, tool names, tool arguments, tool results,
run identifiers, app identifier, completion status and sequence numbers. Identity bindings also use a deployment namespace
and user or business identity; the binding key hashes these values. This hash is a routing identifier, not anonymization or
authorization. The configured explicit Scope, if any, is shared by every invocation using that credential.

The bridge redacts credential-like fields and the configured PC token, discards arbitrary metadata and truncates oversized
events. Hidden reasoning parts and known reasoning tags are excluded by the automatic hosts. Binary attachments are not
intentionally copied. Free text may still contain personal information or sensitive values that automatic redaction cannot
recognize. Explicit memory-write tools send the content chosen by their caller.

Dify stores provider credentials using its credential mechanism. PC tool transport uses HTTPS by default outside loopback;
un-encrypted private-network HTTP requires explicit administrator opt-in. Captured Sources and derived Memory are retained
according to the Server operator's storage and retention policies. The plugin does not automatically expire or purge data.
Retiring an entry removes it from active memory; it does not erase the original captured Source.

Administrators control the destination, credentials, Scope bindings, tool selection and access policy. Agent V2 configuration
can disable automatic capture. For legacy Agent, use explicit memory tools without the automatic strategy when capture is
not wanted. Contact the operator of your Dify/PowerContext deployment for access, export or deletion requests. No universal
data-residency or deletion commitment is made on behalf of a self-hosted Server operator.

The code is distributed under Apache-2.0. See LICENSE.
