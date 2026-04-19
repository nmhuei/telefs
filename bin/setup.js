#!/usr/bin/env node

const { execSync, spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const projectRoot = path.join(__dirname, '..');
const venvPath = path.join(projectRoot, '.venv');

console.log('\n🚀 TeleFS: Running post-installation setup...');

function getPython() {
    const versions = ['python3.13', 'python3.12', 'python3.11', 'python3', 'python'];
    for (const v of versions) {
        try {
            execSync(`${v} --version`, { stdio: 'ignore' });
            return v;
        } catch (e) {}
    }
    return null;
}

const python = getPython();
if (!python) {
    console.error('\n❌ Error: Python 3 was not found on your system.');
    console.error('TeleFS requires Python 3.8+ to function.');
    console.error('Please install Python and try "npm install -g @nmhuei/telefs" again.\n');
    process.exit(1);
}

console.log(`📡 Using: ${python}`);

try {
    if (!fs.existsSync(venvPath)) {
        console.log('📦 Creating virtual environment...');
        try {
            execSync(`${python} -m venv "${venvPath}"`, { cwd: projectRoot, stdio: 'pipe' });
        } catch (e) {
            console.error('\n❌ Error: Failed to create virtual environment.');
            console.error('This often happens if the "venv" module is missing.');
            console.error('Try installing it: sudo apt install python3-venv (on Ubuntu/Debian)\n');
            process.exit(1);
        }
    }

    const pip = path.join(venvPath, 'bin', 'pip');
    const requirements = path.join(projectRoot, 'requirements.txt');

    console.log('📥 Installing Python dependencies...');
    execSync(`"${pip}" install -r "${requirements}"`, { stdio: 'inherit' });

    console.log('\n✅ Setup completed successfully!');

    // Path Diagnostic
    const envPath = process.env.PATH || '';
    const npmGlobalBin = execSync('npm config get prefix', { encoding: 'utf8' }).trim();
    const expectedBinPath = path.join(npmGlobalBin, 'bin');

    if (!envPath.includes(expectedBinPath)) {
        console.log('\n⚠️  ACTION REQUIRED: System PATH configuration');
        console.log('It looks like your global NPM bin directory is not in your system PATH.');
        console.log(`Expected PATH to include: ${expectedBinPath}`);

        const userShell = (process.env.SHELL || '').toLowerCase();
        let configFile = '~/.bashrc';
        if (userShell.includes('zsh')) configFile = '~/.zshrc';
        else if (userShell.includes('fish')) configFile = '~/.config/fish/config.fish';
        else if (userShell.includes('bash')) configFile = '~/.bashrc';

        console.log(`\nTo fix this, add the following line to your ${configFile}:`);
        
        if (configFile.includes('config.fish')) {
            console.log(`\n    fish_add_path ${expectedBinPath}\n`);
        } else {
            console.log(`\n    export PATH="${expectedBinPath}:$PATH"\n`);
        }
        
        console.log(`Then run "source ${configFile}" (or restart your terminal) to use the "telefs" command.\n`);
    } else {
        console.log('\n✨ You can now run "telefs" from anywhere!\n');
    }

} catch (error) {
    console.error('\n❌ Error during setup:', error.message);
    process.exit(1);
}
