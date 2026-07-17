export class ChatModel {
    async getConversations() {
        const r = await fetch('/api/conversations');
        if (!r.ok) throw new Error(await r.text());
        return await r.json();
    }

    async getConversation(id) {
        const r = await fetch(`/api/conversations/${id}`);
        if (!r.ok) throw new Error(await r.text());
        return await r.json();
    }

    async addConversation(title) {
        const r = await fetch('/api/conversations', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title })
        });
        if (!r.ok) throw new Error(await r.text());
        return await r.json();
    }

    async deleteConversation(id) {
        const r = await fetch(`/api/conversations/${id}`, {
            method: 'DELETE'
        });
        if (!r.ok) throw new Error(await r.text());
        return await r.json();
    }

    async addMessage(convId, role, content) {
        const response = await fetch(`/api/conversations/${convId}/messages`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ role, content })
        });
        if (!response.ok) throw new Error(await response.text());
        return response.body; // Returns readable stream for SSE parsing
    }
}
