/**
 * teenyfactories (JavaScript) — placeholder.
 *
 * The JS port is planned but not yet implemented. Every exported member
 * throws NotImplemented when called, with a pointer to the Python
 * implementation that's live today.
 *
 * Use the Python implementation:
 *
 *     pip install --pre teenyfactories
 *
 * See https://github.com/teenyfactories/core for the project overview.
 */

'use strict';

const HOMEPAGE = 'https://github.com/teenyfactories/core';
const PLACEHOLDER_MESSAGE =
  'teenyfactories (JS) is a placeholder. The JavaScript port is planned but not yet implemented. ' +
  'For the working implementation today: pip install --pre teenyfactories. See ' + HOMEPAGE + ' for the project overview.';

class NotImplementedError extends Error {
  constructor(name) {
    super('teenyfactories.' + name + '() is not yet implemented. ' + PLACEHOLDER_MESSAGE);
    this.name = 'NotImplementedError';
  }
}

const notImplemented = (name) => () => { throw new NotImplementedError(name); };

// The eventual API surface, mirrored from the Python implementation in
// camelCase. Each export throws when invoked — depending on this package
// today gets you a clear error pointing at the Python lib.
module.exports = {
  // Pub / sub
  onState:           notImplemented('onState'),
  onMessage:         notImplemented('onMessage'),
  sendMessage:       notImplemented('sendMessage'),
  runPending:        notImplemented('runPending'),

  // Data collections
  collection:        notImplemented('collection'),

  // LLM
  callLlm:           notImplemented('callLlm'),
  embed:             notImplemented('embed'),

  // MCP tool registration
  addMcpServer:      notImplemented('addMcpServer'),
  addMcpTool:        notImplemented('addMcpTool'),

  // Scheduling
  onSchedule:        notImplemented('onSchedule'),

  // Logging
  logDebug:          notImplemented('logDebug'),
  logInfo:           notImplemented('logInfo'),
  logWarn:           notImplemented('logWarn'),
  logError:          notImplemented('logError'),
  logPersona:        notImplemented('logPersona'),

  // Time / IDs
  getTimestamp:      notImplemented('getTimestamp'),
  getTimestampUtc:   notImplemented('getTimestampUtc'),
  generateUniqueId:  notImplemented('generateUniqueId'),

  // Util
  sleep:             notImplemented('sleep'),

  // Markers
  __placeholder__:   true,
  __version__:       require('./package.json').version,
  NotImplementedError,
};
