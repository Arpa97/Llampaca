import ChatView from './components/ChatView.js';
import ModelsView from './components/ModelsView.js';
import McpView from './components/McpView.js';
import SettingsView from './components/SettingsView.js';
import WikiView from './components/WikiView.js';
import ToolsView from './components/ToolsView.js';
import SkillsView from './components/SkillsView.js';
import ClientsView from './components/ClientsView.js';

const { createApp, ref, computed, onMounted } = Vue;

createApp({
    components: {
        ChatView,
        ModelsView,
        McpView,
        SettingsView,
        WikiView,
        ToolsView,
        SkillsView,
        ClientsView
    },
    setup() {
        const currentTab = ref('chat');
        const toastMessage = ref('');
        const toastType = ref('success');

        window.showToast = (msg, type = 'success') => {
            toastMessage.value = msg;
            toastType.value = type;
            setTimeout(() => {
                toastMessage.value = '';
            }, 3500);
        };

        // --- Blocco di stato della sidebar -------------------------------
        // Prima il footer diceva "Server locale attivo" e basta: una stringa
        // fissa che non dipendeva da niente. Ora legge la configurazione reale
        // (la stessa che usa la tab Impostazioni) e mostra il modello attivo,
        // la porta e la finestra di contesto, così l'informazione più utile
        // dell'app — cosa sta girando ADESSO — è sempre a schermo.
        const activeModel = ref('');
        const serverPort = ref('—');
        const contextSize = ref(0);

        // Il nome file GGUF è lungo (es. "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf").
        // Nella colonna da 232px togliamo estensione e suffisso di quantizzazione:
        // il nome resta riconoscibile e la riga non va a capo.
        const activeModelShort = computed(() => {
            if (!activeModel.value) return 'nessun modello';
            return activeModel.value
                .replace(/\.gguf$/i, '')
                .replace(/-(q\d[^-]*|f16|f32|bf16)$/i, '');
        });

        // Contesto in forma compatta: 8192 → "8k".
        const contextLabel = computed(() => {
            if (!contextSize.value) return '—';
            return contextSize.value >= 1000
                ? `${Math.round(contextSize.value / 1024)}k`
                : String(contextSize.value);
        });

        // Il nome completo resta disponibile nel tooltip del blocco.
        const statusTitle = computed(() =>
            activeModel.value
                ? `Modello attivo: ${activeModel.value}\nPorta: ${serverPort.value}\nContesto: ${contextSize.value} token`
                : 'Nessun modello configurato'
        );

        let initialTabDetermined = false;

        const loadStatus = async () => {
            try {
                const rStatus = await fetch(`/api/server/status?_t=${Date.now()}`);
                if (rStatus.ok) {
                    const statusData = await rStatus.json();
                    activeModel.value = statusData.active_model || '';
                    if (!initialTabDetermined) {
                        initialTabDetermined = true;
                        if (statusData.active_model) {
                            currentTab.value = 'chat';
                        } else {
                            currentTab.value = 'settings';
                        }
                    }
                }
                const r = await fetch(`/api/settings?_t=${Date.now()}`);
                if (r.ok) {
                    const cfg = await r.json();
                    serverPort.value = cfg.server_port ?? '—';
                    contextSize.value = cfg.context_size || 0;
                }
            } catch (e) {
                // Nessun rumore in UI: il blocco di stato resta sui placeholder.
                console.error('Errore nel caricamento dello stato del server:', e);
            }
        };

        const checkModelsAndNavigate = async () => {
            try {
                const res = await fetch(`/api/models?_t=${Date.now()}`);
                if (!res.ok) return;
                const data = await res.json();
                const modelsList = Array.isArray(data) ? data : (data.installed || []);
                const installedCount = modelsList.filter(m => m.installed && (m.kind === 'chat' || !m.kind)).length;
                if (installedCount === 0) {
                    currentTab.value = 'models';
                    setTimeout(() => {
                        window.showToast("Nessun modello installato. Scarica un modello per iniziare a chattare.", "warning");
                    }, 400);
                }
            } catch (e) {
                console.error('Errore nel controllo modelli iniziali:', e);
            }
        };

        onMounted(() => {
            loadStatus();
            checkModelsAndNavigate();
            window.addEventListener('llampaca:status-changed', loadStatus);
            setInterval(loadStatus, 3000);
        });

        // La tab Impostazioni e Modelli salvano e riavviano il server: si riallineano da lì.
        window.refreshServerStatus = () => {
            loadStatus();
            window.dispatchEvent(new CustomEvent('llampaca:status-changed'));
        };

        return {
            currentTab,
            toastMessage,
            toastType,
            activeModelShort,
            serverPort,
            contextLabel,
            statusTitle
        };
    }
}).mount('#app');
