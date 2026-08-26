export class GgufModel {
    async getModels() {
        const r = await fetch(`/api/models?_t=${Date.now()}`);
        if (!r.ok) throw new Error(await r.text());
        return await r.json();
    }

    // kind: "chat" (default) sets the chat/LLM default model; "embedding" sets
    // the default embedding model. The two are stored independently server-side.
    async setDefaultModel(name, kind = 'chat') {
        const r = await fetch('/api/models/default', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model_name: name, kind: kind })
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

    async downloadModel(repoId, filename, inputVal, mmprojFilename = null) {
        const r = await fetch('/api/models/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ repo_id: repoId, filename: filename, input_val: inputVal, mmproj_filename: mmprojFilename })
        });
        if (!r.ok) throw new Error(await r.text());
        return await r.json();
    }

    async searchHfModels(query, page = 1) {
        const r = await fetch(`/api/models/search?q=${encodeURIComponent(query || '')}&page=${page}&_t=${Date.now()}`);
        if (!r.ok) throw new Error(await r.text());
        return await r.json();
    }
}
