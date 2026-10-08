# Break It: Tool Design

## Challenge 1: Test the Function, Not the Protocol

Write a unit test that calls `qemu_type("nope", "x")` directly and asserts the error
mentions `qemu_boot`. Now remove the `@tool` wrapper (use `@mcp.tool()`). Does your
test still pass? Does the agent still see the message?

**Hint:** The direct test passes either way, because it sees the raw `KeyError`. Only a
test that goes through MCP, as this demo does over stdio, catches the hidden message.
qemu-mcp's 200+ unit tests missed this for exactly that reason.

## Challenge 2: Catch Everything

Change `EXPECTED` to `Exception`. Then introduce a bug: misspell an attribute in
`qemu_type`. What does the agent see now?

**Hint:** The agent sees `'VM' object has no attribute 'qmpp'` and will try to "fix" it
by calling the tool differently. Converting only *expected* failures keeps the line
clear: those are for the agent, and everything else is a bug for you.

## Challenge 3: A Blocking Wait

Make `qemu_wait_serial` loop until the marker appears, with no timeout. Ask it to wait
for `never`.

**Hint:** The tool call never returns, so the agent's turn never ends. MCP clients
eventually time out the request and report a generic failure, and the VM is left
running with nobody to stop it. Every wait needs a timeout and a result that says what
was seen.

## Challenge 4: Silent Success

Make `qemu_boot` return just `"ok"`. Run the agent loop by reasoning as the agent: what
do you call next, and with what name?

**Hint:** You know the VM's name only because you chose it. The pid, the next step and
the fact that serial is being captured were all in the old result. Every word a result
omits is something the agent must guess or spend a call discovering.

## Challenge 5: Unpinned Majors

In a scratch venv, install `mcp<2` and run this file. Then read the `mcp` 2.0 changelog
for what moved.

**Hint:** `from mcp.server.mcpserver import MCPServer` doesn't exist in 1.x, and
`mcp.server.fastmcp` doesn't exist in 2.x. A dependency spec like `mcp>=1.2` is a promise
you can't keep. Pin to the major version you tested (`mcp>=2,<3`), and upgrade on
purpose.

→ [Next: Module 08](../08-lifecycle/README.md)
