// Data-access layer for the personal wiki ("Profilo"): the same
// ~/.llampaca/wiki/*.md pages the model reads/writes and that /remember
// stores. Thin wrappers over the /api/wiki endpoints.
export class WikiModel {
    // List of pages: { pages: [{name, description}], max_chars }
    async getPages() {
        const r = await fetch(`/api/wiki?_t=${Date.now()}`);
        if (!r.ok) throw new Error(await r.text());
        return await r.json();
    }

    // Full content of one page: { name, content }
    async getPage(name) {
        const r = await fetch(`/api/wiki/${encodeURIComponent(name)}?_t=${Date.now()}`);
        if (!r.ok) throw new Error(await this._error(r));
        return await r.json();
    }

    // Create or overwrite a page. Returns { name } with the slugified name
    // actually written (may differ from what was typed).
    async savePage(name, content) {
        const r = await fetch('/api/wiki', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, content })
        });
        if (!r.ok) throw new Error(await this._error(r));
        return await r.json();
    }

    async deletePage(name) {
        const r = await fetch(`/api/wiki/${encodeURIComponent(name)}`, { method: 'DELETE' });
        if (!r.ok) throw new Error(await this._error(r));
        return await r.json();
    }

    // The wiki endpoints return {error: "..."} for 4xx/5xx; surface it.
    async _error(response) {
        try {
            const err = await response.json();
            if (err && err.error) return err.error;
        } catch (_) { /* fall through */ }
        return `Errore (${response.status})`;
    }
}
