export class McpModel {
    async getActiveIntegrations() {
        const r = await fetch(`/api/mcp?_t=${Date.now()}`);
        if (!r.ok) throw new Error(await r.text());
        return await r.json();
    }

    async searchRegistry(query, page = 1) {
        const r = await fetch(`/api/mcp/search?q=${encodeURIComponent(query || '')}&page=${page}&_t=${Date.now()}`);
        if (!r.ok) throw new Error(await r.text());
        return await r.json();
    }

    async getConfigSchema(name) {
        const r = await fetch(`/api/mcp/config-schema?name=${encodeURIComponent(name)}&_t=${Date.now()}`);
        if (!r.ok) throw new Error(await r.text());
        return await r.json();
    }

    async installIntegration(name, command, args, env) {
        const r = await fetch('/api/mcp/install', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, command, args, env })
        });
        if (!r.ok) throw new Error(await r.text());
        return await r.json();
    }

    async uninstallIntegration(name) {
        const r = await fetch(`/api/mcp/uninstall/${encodeURIComponent(name)}`, {
            method: 'DELETE'
        });
        if (!r.ok) throw new Error(await r.text());
        return await r.json();
    }
}
