import { ChatModel } from '../models/chat_model.js';

const { ref, watch, nextTick } = Vue;

export function useChatController() {
    const model = new ChatModel();
    const conversations = ref([]);
    const activeConversationId = ref(null);
    const activeMessages = ref([]);
    const userInput = ref('');
    const messagesContainer = ref(null);
    const contextBudget = ref(null);
    const pendingConfirmation = ref(null);
    // Files uploaded for the next message but not yet sent. Each entry:
    // { name, status: 'uploading'|'done'|'error', kind: 'inject'|'rag'|null,
    //   detail: string }. Cleared once the message that carries them is sent
    // (the backend merges them into that turn) and on conversation switch.
    const attachments = ref([]);
    const isDragging = ref(false);
    // The conversation the staged files belong to. Used to clear the chips
    // when the user navigates to a DIFFERENT conversation, without clobbering
    // chips that were just added to a conversation created on the fly by the
    // attach flow itself (which also changes activeConversationId).
    const attachmentsConvId = ref(null);

    // Load initial list of conversations from database
    const loadConversations = async () => {
        try {
            conversations.value = await model.getConversations();
            if (conversations.value.length && activeConversationId.value === null) {
                activeConversationId.value = conversations.value[0].id;
            }
        } catch (err) {
            console.error("Errore caricamento conversazioni:", err);
        }
    };

    // Watch activeConversationId and load full history for the selected conversation
    watch(activeConversationId, async (newVal) => {
        // Staged files belong to a single conversation (the backend keys its
        // pending list by conversation id); leaving for a DIFFERENT one
        // abandons its chips so they can't be sent with the wrong message.
        // The `!==` guard keeps chips that the attach flow just added to a
        // conversation it created on the fly (which triggers this same watch).
        if (attachmentsConvId.value !== newVal) {
            attachments.value = [];
        }
        if (!newVal) {
            activeMessages.value = [];
            return;
        }
        try {
            const detail = await model.getConversation(newVal);
            activeMessages.value = detail ? detail.messages : [];
            scrollToBottom();
        } catch (err) {
            console.error("Errore caricamento dettaglio conversazione:", err);
        }
    }, { immediate: true });

    // Create a conversation on demand if none is active, returning its id.
    // Shared by sendMessage and the attach flow (you can drop a file before
    // typing anything, which must land in a real conversation).
    const ensureConversation = async () => {
        if (activeConversationId.value !== null) return activeConversationId.value;
        const nextNum = conversations.value.length + 1;
        const newConv = await model.addConversation(`Conversazione ${nextNum}`);
        conversations.value = await model.getConversations();
        activeConversationId.value = newConv.id;
        return newConv.id;
    };

    const scrollToBottom = () => {
        nextTick(() => {
            if (messagesContainer.value) {
                messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
            }
        });
    };

    const sendMessage = async () => {
        // Allow sending with only attachments (e.g. "riassumi" typed later),
        // but never a completely empty turn.
        if (!userInput.value.trim() && !attachments.value.length) return;

        let convId = activeConversationId.value;
        const promptText = userInput.value;
        userInput.value = '';

        const now = new Date();
        const timeStr = now.toTimeString().split(' ')[0];

        // Automatic on-demand conversation creation if none is active
        if (convId === null) {
            try {
                convId = await ensureConversation();
            } catch (err) {
                console.error("Errore creazione automatica conversazione:", err);
                return;
            }
        }

        // The staged files are consumed by the backend the moment this
        // message is posted (it merges them into this user turn), so clear
        // their chips now — they belong to the message we are about to send.
        attachments.value = [];

        // 1. Immediately append user message to UI
        activeMessages.value.push({
            role: 'user',
            content: promptText,
            timestamp: timeStr
        });
        scrollToBottom();

        // 2. Append a placeholder assistant message that will stream the content
        const assistantIndex = activeMessages.value.push({
            role: 'agent',
            content: '',
            thought: 'Penso...',
            timestamp: ''
        }) - 1;

        try {
            // Initiate send to backend API
            const stream = await model.addMessage(convId, 'user', promptText);
            const reader = stream.getReader();
            const decoder = new TextDecoder("utf-8");
            let buffer = "";

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split("\n");
                buffer = lines.pop(); // Keep last incomplete line in buffer

                for (const line of lines) {
                    if (line.trim().startsWith("data: ")) {
                        try {
                            const payload = JSON.parse(line.trim().slice(6));
                            const kind = payload.kind;
                            const data = payload.data;

                            if (kind === "text") {
                                // Accumulate streaming text
                                if (activeMessages.value[assistantIndex].thought) {
                                    activeMessages.value[assistantIndex].thought = ''; // clear thought
                                }
                                activeMessages.value[assistantIndex].content += data;
                                scrollToBottom();
                            } else if (kind === "tool_call") {
                                activeMessages.value[assistantIndex].thought = `Uso lo strumento: ${data.name}...`;
                                console.log(`[tool] ${data.name}(${JSON.stringify(data.arguments)})`);
                                scrollToBottom();
                            } else if (kind === "tool_result") {
                                activeMessages.value[assistantIndex].thought = `Elaboro il risultato di: ${data.name}...`;
                                let preview = data.result.replace(/\n/g, " ");
                                if (preview.length > 100) preview = preview.slice(0, 100) + "...";
                                console.log(`[risultato] ${preview}`);
                                scrollToBottom();
                            } else if (kind === "context_status") {
                                contextBudget.value = data;
                            } else if (kind === "title_updated") {
                                const conv = conversations.value.find(c => c.id === convId);
                                if (conv) conv.title = data.title;
                            } else if (kind === "tool_confirm_request") {
                                pendingConfirmation.value = data;
                                scrollToBottom();
                            } else if (kind === "warning") {
                                console.warn(`[avviso] ${data}`);
                            } else if (kind === "error") {
                                console.error(`[errore] ${data}`);
                                activeMessages.value[assistantIndex].thought = '';
                                activeMessages.value[assistantIndex].content += `\n❌ **[Errore di sistema, vedi console]**`;
                                scrollToBottom();
                            } else if (kind === "done") {
                                activeMessages.value[assistantIndex].thought = '';
                                activeMessages.value[assistantIndex].timestamp = new Date().toTimeString().split(' ')[0];
                                // Reload conversations list to update sidebar titles if needed
                                conversations.value = await model.getConversations();
                            }
                        } catch (e) {
                            console.error("Errore parsing SSE line:", e, line);
                        }
                    }
                }
            }
        } catch (err) {
            activeMessages.value[assistantIndex].content = `Errore di connessione: ${err.message}`;
            activeMessages.value[assistantIndex].timestamp = 'Errore';
        }
    };

    const startNewConversation = async () => {
        try {
            const nextNum = conversations.value.length + 1;
            const newConv = await model.addConversation(`Conversazione ${nextNum}`);
            conversations.value = await model.getConversations();
            activeConversationId.value = newConv.id;
        } catch (err) {
            console.error("Errore creazione conversazione:", err);
        }
    };

    const deleteConversation = async (id) => {
        try {
            await model.deleteConversation(id);
            conversations.value = await model.getConversations();
            if (activeConversationId.value === id) {
                activeConversationId.value = conversations.value.length ? conversations.value[0].id : null;
            }
        } catch (err) {
            console.error("Errore eliminazione conversazione:", err);
        }
    };

    const resolveConfirmation = async (allow) => {
        if (!pendingConfirmation.value) return;
        const confirmId = pendingConfirmation.value.confirm_id;
        pendingConfirmation.value = null; // Hide UI immediately
        
        try {
            await fetch('/api/confirm', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ confirm_id: confirmId, allow: allow })
            });
        } catch (e) {
            console.error("Failed to send confirmation", e);
        }
    };

    // Upload one file: shows an "uploading" chip immediately, then flips it
    // to done/error based on the backend's response. Each file is staged for
    // the current conversation's next message.
    const uploadOne = async (file, convId) => {
        const chip = {
            name: file.name,
            status: 'uploading',
            kind: null,
            detail: ''
        };
        attachments.value.push(chip);
        try {
            const res = await model.uploadAttachment(convId, file);
            chip.status = 'done';
            chip.kind = res.kind;
            if (res.kind === 'rag') {
                chip.detail = `indicizzato (${res.chunks} passaggi)`;
            } else {
                chip.detail = `${res.budget_used_pct}% del budget`;
            }
        } catch (err) {
            chip.status = 'error';
            chip.detail = err.message;
            console.error(`Errore upload '${file.name}':`, err);
        }
    };

    // Entry point for both the 📎 button and drag-and-drop: ensure a
    // conversation exists, then upload every chosen file (sequentially, so
    // the cumulative-budget check on the backend is deterministic).
    const attachFiles = async (fileList) => {
        const files = Array.from(fileList || []);
        if (!files.length) return;
        let convId;
        try {
            convId = await ensureConversation();
        } catch (err) {
            console.error("Impossibile creare la conversazione per l'allegato:", err);
            return;
        }
        // Tag the chips with their conversation BEFORE the first upload so the
        // activeConversationId watcher (which may have fired when a new
        // conversation was created above) does not wipe them.
        attachmentsConvId.value = convId;
        for (const file of files) {
            await uploadOne(file, convId);
        }
    };

    // Remove a not-yet-sent chip. This only drops it from the UI; the
    // backend clears its whole pending list when the next message is sent,
    // and a chip removed here simply won't have a matching send.
    const removeAttachment = (index) => {
        attachments.value.splice(index, 1);
    };

    // --- Drag and drop over the chat area -----------------------------
    const onDragOver = (e) => {
        e.preventDefault();
        isDragging.value = true;
    };
    const onDragLeave = (e) => {
        e.preventDefault();
        isDragging.value = false;
    };
    const onDrop = (e) => {
        e.preventDefault();
        isDragging.value = false;
        if (e.dataTransfer && e.dataTransfer.files) {
            attachFiles(e.dataTransfer.files);
        }
    };

    // Load list at mount
    loadConversations();

    return {
        conversations,
        activeConversationId,
        userInput,
        messagesContainer,
        contextBudget,
        pendingConfirmation,
        attachments,
        isDragging,
        getActiveMessages: activeMessages,
        sendMessage,
        startNewConversation,
        deleteConversation,
        resolveConfirmation,
        scrollToBottom,
        attachFiles,
        removeAttachment,
        onDragOver,
        onDragLeave,
        onDrop
    };
}
