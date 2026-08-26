export class SkillsModel {
    async getSkills() {
        const r = await fetch(`/api/skills?_t=${Date.now()}`);
        if (!r.ok) throw new Error(await r.text());
        return await r.json();
    }

    async getSkill(name) {
        const r = await fetch(`/api/skills/${encodeURIComponent(name)}?_t=${Date.now()}`);
        if (!r.ok) throw new Error(await r.text());
        return await r.json();
    }

    async saveSkill(name, content, url = '') {
        const r = await fetch('/api/skills', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: name,
                content: content,
                url: url
            })
        });
        if (!r.ok) {
            const err = await r.json().catch(() => ({ error: 'Errore durante il salvataggio' }));
            throw new Error(err.error || 'Errore durante il salvataggio');
        }
        return await r.json();
    }

    async deleteSkill(name) {
        const r = await fetch(`/api/skills/${encodeURIComponent(name)}`, {
            method: 'DELETE'
        });
        if (!r.ok) {
            const err = await r.json().catch(() => ({ error: 'Errore eliminazione skill' }));
            throw new Error(err.error || 'Errore eliminazione skill');
        }
        return await r.json();
    }
}
