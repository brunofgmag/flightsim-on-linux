import { createRequire } from 'node:module';
import { resolve } from 'node:path';

const MODULE_MISSING = 3;
const BAD_USAGE = 4;
const POLL_MS = 3000;
const AIRCRAFT_REQUEST = 1;

const [port, modules] = process.argv.slice(2);
if (!/^\d+$/.test(port ?? '') || !modules) {
  console.error('usage: node aircraft-loaded.mjs <SimConnect port> <folder that holds node_modules>');
  process.exit(BAD_USAGE);
}

let simconnect;
try {
  simconnect = createRequire(resolve(modules) + '/')('node-simconnect');
} catch {
  process.exit(MODULE_MISSING);
}

let session;
try {
  session = await simconnect.open('flightsim-on-linux', simconnect.Protocol.SunRise, { host: '127.0.0.1', port: Number(port) });
} catch {
  process.exit(1);
}

const { handle } = session;
let current = null;

handle.on('systemState', state => {
  const aircraft = state.dataString;
  if (state.requestID === AIRCRAFT_REQUEST && aircraft && aircraft !== current) {
    current = aircraft;
    console.log(aircraft);
  }
});
handle.on('close', () => process.exit(0));
handle.on('error', () => process.exit(1));

console.log('#open');
const poll = () => handle.requestSystemState(AIRCRAFT_REQUEST, 'AircraftLoaded');
poll();
setInterval(poll, POLL_MS);
