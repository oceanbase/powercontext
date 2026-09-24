# PowerContext Agent

Function Calling strategy with automatic external memory for legacy Dify Workflow Agent nodes.
Install the **PowerContext** tool plugin separately. The strategy package itself has no PowerContext SDK dependency.

## Configure

1. Select **PowerContext Function Calling** in the legacy Workflow Agent node.
2. Select `prepare_context` and `capture_event` from PowerContext, with their configured provider credential, plus business tools.
3. Set the model, query and instruction. Set `context_window` if the model does not declare its context size.
4. Use runtime user identity, or supply a literal business identifier shared in the app. Provision the corresponding PC binding.

The strategy invokes the two memory callbacks automatically and excludes them from model tools. It records visible user/model
text, tool arguments/results and completion state. Model arguments cannot override configured tool form inputs or the callback
identity. The callback deadline is 10 seconds, and a failed callback is suppressed until the next run. Agent logs report degradation.

Recall defaults to 8,000 bytes, further restricted by the model input budget. Input budgeting includes tool schemas and uses
UTF-8 text bytes conservatively. The loop clears older tool results and summarizes complete older interactions while retaining
the first user request. If the current task still cannot fit, it raises a budget error before calling the model. Configure a real
model context size; this strategy does not estimate image/audio token costs. The default iteration limit is 20.

Captured evidence requires PC extraction before it becomes memory. Configure the Server scheduler or an explicit flush step.
There is no client outbox; cancellation or process termination can lose events not yet accepted by PC. Capture follows the tool
plugin's redaction and byte limits. See [PRIVACY.md](PRIVACY.md).

This strategy is for legacy Agent nodes. Native Agent V2 and standalone Agent apps use Dify's external-memory layer with the
PowerContext tool plugin. That layer requires a Dify build containing the integration; installing this strategy does not patch Dify.
