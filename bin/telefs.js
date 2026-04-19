#!/usr/bin/env node

/**
 * TeleFS Node.js Shim
 * This script invokes the Python core of TeleFS.
 */

const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const projectRoot = path.join(__dirname, '..');
const venvPython = path.join(projectRoot, '.venv', 'bin', 'python');
const args = ['-m', 'telefs.cli', ...process.argv.slice(2)];

// Priority: TELEFS_PYTHON -> Local venv -> System python3 -> System python
let pythonBin = process.env.TELEFS_PYTHON;
if (!pythonBin) {
    pythonBin = fs.existsSync(venvPython) ? venvPython : 'python3';
}

let pythonProcess;

function startProcess(bin) {
    return spawn(bin, args, {
        stdio: 'inherit',
        env: {
            ...process.env,
            PYTHONPATH: projectRoot
        }
    });
}

pythonProcess = startProcess(pythonBin);

pythonProcess.on('error', (err) => {
    if (pythonBin === 'python3') {
        // Fallback to python if python3 fails
        pythonBin = 'python';
        pythonProcess = startProcess(pythonBin);
    } else {
        console.error(`\n[TeleFS Error] Failed to start Python process: ${err.message}`);
        console.error('Make sure Python 3 is installed and available in your PATH.\n');
        process.exit(1);
    }
});

pythonProcess.on('exit', (code) => {
    process.exit(code || 0);
});

// Handle termination signals
process.on('SIGINT', () => {
    if (pythonProcess) pythonProcess.kill('SIGINT');
});

process.on('SIGTERM', () => {
    if (pythonProcess) pythonProcess.kill('SIGTERM');
});

