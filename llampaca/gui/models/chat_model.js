export class ChatModel {
    async getConversations() {
        const r = await fetch(`/api/conversations?_t=${Date.now()}`);
        if (!r.ok) throw new Error(await r.text());
        return await r.json();
    }

    async getConversation(id) {
        const r = await fetch(`/api/conversations/${id}?_t=${Date.now()}`);
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

    async addMessage(convId, role, content, signal) {
        const response = await fetch(`/api/conversations/${convId}/messages`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ role, content }),
            signal   // AbortSignal: lets the caller stop reading the stream
        });
        if (!response.ok) throw new Error(await response.text());
        return response.body; // Returns readable stream for SSE parsing
    }

    // Ask the backend to cancel the in-flight generation (the Stop button).
    async cancel() {
        const r = await fetch('/api/cancel', { method: 'POST' });
        if (!r.ok) throw new Error(await r.text());
        return await r.json();
    }

    // Upload a file to be staged for the conversation's next message.
    // The raw bytes are the request body; the filename rides in a header
    // (URL-encoded) because the server drops the query string. Resolves to
    // {kind:"inject"|"rag", filename, ...} or throws the server's error text.
    async uploadAttachment(convId, file) {
        const response = await fetch(`/api/conversations/${convId}/attach`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/octet-stream',
                'X-Attachment-Filename': encodeURIComponent(file.name)
            },
            body: file
        });
        if (!response.ok) {
            // The server sends {error: "..."} for both 4xx and 5xx.
            let message = `Upload fallito (${response.status})`;
            try {
                const err = await response.json();
                if (err && err.error) message = err.error;
            } catch (_) { /* non-JSON body: keep the generic message */ }
            throw new Error(message);
        }
        return await response.json();
    }
}
