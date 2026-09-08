export class ToolsModel {
    async getTools() {
        const r = await fetch(`/api/tools?_t=${Date.now()}`);
        if (!r.ok) throw new Error(await r.text());
        return await r.json();
    }

    async saveCustomTool(name, code, requiresConfirmation) {
        const r = await fetch('/api/tools/custom', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: name,
                code: code,
                requires_confirmation: requiresConfirmation
            })
        });
        if (!r.ok) throw new Error(await r.text());
        return await r.json();
    }

    async deleteCustomTool(name) {
        const r = await fetch(`/api/tools/custom/${encodeURIComponent(name)}`, {
            method: 'DELETE'
        });
        if (!r.ok) throw new Error(await r.text());
        return await r.json();
    }
}
