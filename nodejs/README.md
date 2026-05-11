# teenyfactories (JavaScript)

Placeholder for the JavaScript port of [TeenyFactories](https://github.com/teenyfactories/core). **Not yet implemented.**

Use the Python implementation today:

```bash
pip install --pre teenyfactories
```

## Why this package exists

To reserve the `teenyfactories` name on npm so the eventual JS port can publish under it without scramble. Every exported member throws `NotImplementedError` if invoked.

```js
const tf = require('teenyfactories');
tf.onState('documents', 'loaded');
// throws: teenyfactories.onState() is not yet implemented. Use the Python implementation...
```

## Planned API surface

When the JS port lands it will mirror the Python API in camelCase:

| Python | JavaScript |
|---|---|
| `tf.on_state` / `tf.on_message` / `tf.send_message` | `tf.onState` / `tf.onMessage` / `tf.sendMessage` |
| `tf.collection(name).set` / `.add` / `.get` / `.get_all` | `tf.collection(name).set` / `.add` / `.get` / `.getAll` |
| `tf.call_llm` / `tf.embed` | `tf.callLlm` / `tf.embed` |
| `tf.add_mcp_server` / `tf.add_mcp_tool` | `tf.addMcpServer` / `tf.addMcpTool` |
| `tf.on_schedule` | `tf.onSchedule` |
| `tf.log_info` / etc. | `tf.logInfo` / etc. |
| `tf.run_pending` | `tf.runPending` |

The protocol underneath (what gets written to `factory_data`, what `NOTIFY` channels are named, how messages are framed) is identical across languages. JS and Python agents will interoperate against the same Postgres backend.

## Versioning

Published as semver pre-releases under the `dev` dist-tag: `0.1.0-dev.YYYYMMDD`. Default `npm install teenyfactories` will NOT pick these up — you need `npm install teenyfactories@dev` to opt in, and even then you'll get the placeholder shell.

## License

MIT.
