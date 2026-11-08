#!/usr/bin/env node
'use strict';

const { spawn } = require('node:child_process');
const { release, constants } = require('node:os');
const { dirname, join } = require('node:path');

const guidance = 'See https://github.com/usemont/mont-cli for other distributions.';
function fail(message) {
  console.error(message);
  process.exit(1);
}

let suffix;
if (process.platform === 'darwin') {
  if (process.arch !== 'arm64') {
    fail(`Mont npm requires Apple Silicon on macOS. ${guidance}`);
  }
  const version = /^(\d+)\./.exec(release());
  if (!version || Number(version[1]) < 23) {
    fail(`Mont npm requires macOS 14 or newer. ${guidance}`);
  }
  suffix = 'darwin-arm64';
} else if (process.platform === 'win32') {
  if (process.arch !== 'x64') fail(`Mont npm requires Windows x64. ${guidance}`);
  suffix = 'win32-x64';
} else if (process.platform === 'linux') {
  if (process.arch !== 'x64' && process.arch !== 'arm64') {
    fail(`Mont npm supports Linux x64 and arm64 only. ${guidance}`);
  }
  const glibc = process.report.getReport().header.glibcVersionRuntime;
  const version = /^(\d+)\.(\d+)(?:\.|$)/.exec(glibc || '');
  if (!version || Number(version[1]) < 2 ||
      (Number(version[1]) === 2 && Number(version[2]) < 36)) {
    fail(`Mont npm requires Linux GNU with glibc 2.36 or newer; musl is unsupported. ${guidance}`);
  }
  suffix = `linux-${process.arch}`;
} else {
  fail(`Unsupported Mont npm target: ${process.platform}-${process.arch}. ${guidance}`);
}

const packageName = `@usemont/cli-${suffix}`;
const { version } = require('./package.json');
const reinstall = `Reinstall @usemont/cli@${version} with npm and optional dependencies enabled (do not use --omit=optional or --no-optional).`;
let packagePath;
let payload;
try {
  packagePath = require.resolve(`${packageName}/package.json`);
  payload = require(packagePath);
} catch (error) {
  fail(`Cannot load optional dependency ${packageName}@${version}: ${error.message}\n${reinstall}`);
}
if (payload.version !== version) {
  fail(`Mont requires ${packageName}@${version}, but found ${payload.version}.\n${reinstall}`);
}

const executable = join(dirname(packagePath), 'bin', process.platform === 'win32' ? 'mont.exe' : 'mont');
const child = spawn(executable, process.argv.slice(2), { stdio: 'inherit', shell: false });
const signals = process.platform === 'win32' ? ['SIGINT', 'SIGTERM'] : ['SIGINT', 'SIGTERM', 'SIGHUP'];
let interrupted;
let finished = false;
let failed = false;
function forward(signal) {
  interrupted = signal;
  child.kill(signal);
}
const handlers = new Map(signals.map(signal => [signal, () => forward(signal)]));
for (const [signal, handler] of handlers) process.on(signal, handler);
process.once('exit', () => {
  if (!finished) child.kill();
});
child.once('error', error => {
  failed = true;
  console.error(`Cannot launch Mont: ${error.message}`);
});
child.once('close', (code, signal) => {
  finished = true;
  for (const [name, handler] of handlers) process.removeListener(name, handler);
  const termination = interrupted || signal;
  if (termination) {
    process.kill(process.pid, termination);
    process.exitCode = 128 + constants.signals[termination];
  } else {
    process.exitCode = failed ? 1 : code;
  }
});
