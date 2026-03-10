const fs = require('fs');
const path = require('path');

async function main() {
    const handlerPath = process.env.HANDLER || 'handler.handler';
    const [moduleName, funcName] = handlerPath.split('.');

    const moduleFile = path.join('/var/task', `${moduleName}.js`);
    if (!fs.existsSync(moduleFile)) {
        console.log(JSON.stringify({
            status: 'error',
            error: `Handler module not found: ${moduleFile}`,
        }));
        process.exit(1);
    }

    const mod = require(moduleFile);
    const handlerFunc = mod[funcName];
    if (typeof handlerFunc !== 'function') {
        console.log(JSON.stringify({
            status: 'error',
            error: `Handler function '${funcName}' not found in ${moduleName}`,
        }));
        process.exit(1);
    }

    let event = {};
    const eventFile = '/var/task/_event.json';
    if (fs.existsSync(eventFile)) {
        event = JSON.parse(fs.readFileSync(eventFile, 'utf-8'));
    }

    const context = {
        function_name: process.env.FUNCTION_NAME || 'unknown',
        memory_limit_mb: process.env.MEMORY_LIMIT || '128',
    };

    try {
        const result = await handlerFunc(event, context);
        console.log(JSON.stringify({ status: 'success', output: result }));
    } catch (e) {
        console.log(JSON.stringify({
            status: 'error',
            error: e.message,
            trace: e.stack,
        }));
        process.exit(1);
    }
}

main();
