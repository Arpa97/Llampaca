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

    const scrollToBottom = () => {
        nextTick(() => {
            if (messagesContainer.value) {
                messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
            }
        });
    };

    const sendMessage = async () => {
        if (!userInput.value.trim()) return;
        
        let convId = activeConversationId.value;
        const promptText = userInput.value;
        userInput.value = '';

        const now = new Date();
        const timeStr = now.toTimeString().split(' ')[0];

        // Automatic on-demand conversation creation if none is active
        if (convId === null) {
            try {
                const nextNum = conversations.value.length + 1;
                const newConv = await model.addConversation(`Conversazione ${nextNum}`);
                conversations.value = await model.getConversations();
                activeConversationId.value = newConv.id;
                convId = newConv.id;
            } catch (err) {
                console.error("Errore creazione automatica conversazione:", err);
                return;
            }
        }

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

    // Load list at mount
    loadConversations();

    return {
        conversations,
        activeConversationId,
        userInput,
        messagesContainer,
        contextBudget,
        pendingConfirmation,
        getActiveMessages: activeMessages,
        sendMessage,
        startNewConversation,
        deleteConversation,
        resolveConfirmation,
        scrollToBottom
    };
}
