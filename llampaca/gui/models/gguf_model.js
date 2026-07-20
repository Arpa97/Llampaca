export class GgufModel {
    async getModels() {
        const r = await fetch(`/api/models?_t=${Date.now()}`);
        if (!r.ok) throw new Error(await r.text());
        return await r.json();
    }

    async setDefaultModel(name) {
        const r = await fetch('/api/models/default', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model_name: name })
        });
        if (!r.ok) throw new Error(await r.text());
        return await r.json();
    }

    async deleteModel(name) {
        const r = await fetch(`/api/models/${encodeURIComponent(name)}`, {
            method: 'DELETE'
        });
        if (!r.ok) throw new Error(await r.text());
        return await r.json();
    }
}
