# Choosing a tool from its description

This small routing rehearsal tests whether the advertised tools fit 24 natural
requests, including nine requests outside their scope. It does not invoke tools
or measure whether a normal agent session will remember to use an installed MCP.

On October 30, 2026, three fresh text-only `claude-opus-5-5` sessions each selected
the expected choice for 24/24 requests. The expected choices were withheld.
The [exact prompt](tool_choice_prompt.txt), [labelled requests](tool_choice.json)
and [recorded choices](tool_choice_results.json) are included. These deliberately
clear synthetic requests are a description check, not an adoption benchmark.

To repeat, give the prompt to a fresh session without expected choices. Save its
JSON object with a `choices` array, then score it:

```sh
python -m evals.tool_choice choices.json
```

The [MCP setup guide](../docs/MCP.md) includes an optional project instruction
for when to use Surface Map. Registration makes tools available; the agent still
decides which tools fit the task. The protocol's
[model-controlled tool design](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)
does not guarantee invocation.
