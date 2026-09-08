const { ref, onMounted } = Vue;

export default {
    template: `
        <div class="view">
            <div class="view-header">
                <div class="view-header-text">
                    <h1 class="view-title">Client IDE & Integrazioni</h1>
                    <div class="view-subtitle">Guida passo-passo per collegare Llampaca a VS Code, Continue.dev, Cline, Roo Code e Claude Desktop</div>
                </div>
                <div class="view-header-actions">
                    <button class="btn btn-secondary" @click="loadData" :disabled="isLoading">
                        <svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14"><path d="M17.65 6.35C16.2 4.9 14.21 4 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"/></svg>
                        Aggiorna stato
                    </button>
                </div>
            </div>

            <div class="view-body" style="gap: 28px;">
                <!-- SEZIONE 1: Autocompletamento e Chat in VS Code (Continue.dev) -->
                <div class="info-block" style="border-left: 4px solid var(--amber-glow); padding-left: 20px;">
                    <div class="section-head" style="margin-bottom: 14px;">
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <span class="model-badge badge-mcp" style="background: var(--amber-glow); color: var(--amber-ink); font-weight: 700;">CONSIGLIATO</span>
                            <h3 class="section-title" style="font-size: 16px;">⚡ 1. Autocompletamento & Chat di Codice in VS Code</h3>
                        </div>
                        <p class="section-desc" style="margin-top: 6px;">
                            VS Code non include una chat IA nativa senza estensioni. Per avere l'autocompletamento in tempo reale (Ghost text grigio mentre digiti) e la chat di codice in VS Code usa <strong>Continue.dev</strong> (l'alternativa gratuita ed open source a Copilot).
                        </p>
                    </div>

                    <div class="card" style="padding: 16px; background: var(--bg-hover); border-color: var(--border-color); display: flex; flex-direction: column; gap: 12px;">
                        <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap; justify-content: space-between;">
                            <div style="font-size: 13px; color: var(--dusk-blue);">
                                <strong>Passo 1:</strong> Installa l'estensione <strong>Continue</strong> dal marketplace di VS Code (<span class="mono">Cmd+Shift+X</span> &rarr; cerca <span class="mono">Continue</span>).
                            </div>
                            <div style="font-size: 13px; color: var(--dusk-blue);">
                                <strong>Passo 2:</strong> Incolla lo snippet nella lista <span class="mono">models:</span> del file <span class="mono">~/.continue/config.yaml</span>.
                            </div>
                        </div>

                        <div style="display: flex; gap: 10px; align-items: center; margin-top: 4px; flex-wrap: wrap;">
                            <button class="btn btn-primary" @click="copySnippet(snippets.continue)">
                                <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/></svg>
                                Copia Configurazione per Continue.dev (YAML)
                            </button>
                            <span style="font-size: 11px; color: var(--text-muted);">
                                Endpoint: <span class="mono" style="color: var(--dusk-blue); font-weight: 600;">{{ snippets.endpoint_url || 'http://127.0.0.1:8080/v1' }}</span>
                            </span>
                        </div>
                    </div>
                </div>

                <!-- SEZIONE 2: Strumenti Avanzati MCP (Cline, Roo Code, Claude Desktop) -->
                <div class="info-block">
                    <div class="section-head" style="margin-bottom: 16px;">
                        <h3 class="section-title">🔌 2. Strumenti Avanzati MCP (Model Context Protocol)</h3>
                        <p class="section-desc">
                            Questa funzione abilita Llampaca come <strong>Server di Strumenti (MCP)</strong>. Consente ad estensioni avanzate come <strong>Cline</strong>, <strong>Roo Code</strong> o a <strong>Claude Desktop</strong> di accedere ai file del tuo progetto, eseguire comandi shell e leggere la tua memoria wiki.
                        </p>
                    </div>

                    <div class="registry-grid" style="grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));">
                        <!-- VS Code MCP Card -->
                        <div class="card" style="padding: 18px; display: flex; flex-direction: column; justify-content: space-between; gap: 14px;">
                            <div>
                                <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px;">
                                    <div style="display: flex; align-items: center; gap: 10px;">
                                        <div style="width: 32px; height: 32px; border-radius: 6px; background: var(--bg-hover); display: flex; align-items: center; justify-content: center; color: var(--dusk-blue);">
                                            <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M23.15 2.587L18.21.21a1.494 1.494 0 0 0-1.705.29l-9.46 8.63-4.12-3.12a.999.999 0 0 0-1.276.06L.32 7.27a.999.999 0 0 0-.02 1.442l3.44 3.29-3.44 3.29a.999.999 0 0 0 .02 1.442l1.329 1.18a.999.999 0 0 0 1.276.06l4.12-3.12 9.46 8.63c.47.43 1.16.54 1.705.29l4.94-2.377A1.5 1.5 0 0 0 24 20.06V3.94a1.5 1.5 0 0 0-.85-1.353z"/></svg>
                                        </div>
                                        <div>
                                            <strong style="font-size: 15px; color: var(--dusk-blue);">VS Code MCP Server</strong>
                                            <div style="font-size: 11px; color: var(--text-muted);">Per estensioni MCP (Cline / Roo Code)</div>
                                        </div>
                                    </div>
                                    <span class="model-badge" :class="status.vscode?.mcp_connected ? 'badge-mcp' : ''" :style="{ background: status.vscode?.mcp_connected ? '#d4edda' : '', color: status.vscode?.mcp_connected ? '#155724' : '' }">
                                        {{ status.vscode?.mcp_connected ? '● Server Registrato' : '○ Non Registrato' }}
                                    </span>
                                </div>
                                <p style="font-size: 12px; color: var(--text-muted); line-height: 1.5; margin: 0;">
                                    Registra il server MCP di Llampaca nei file di configurazione di VS Code (<span class="mono">mcp.json</span> e <span class="mono">settings.json</span>).
                                </p>
                            </div>
                            <div style="display: flex; gap: 8px; align-items: center; margin-top: 6px;">
                                <button v-if="!status.vscode?.mcp_connected" class="btn btn-secondary" style="flex: 1;" @click="toggleMcp('vscode', 'install')" :disabled="isUpdating">
                                    Abilita Server MCP in VS Code
                                </button>
                                <button v-else class="btn btn-danger" style="flex: 1;" @click="toggleMcp('vscode', 'remove')" :disabled="isUpdating">
                                    Rimuovi Server MCP
                                </button>
                            </div>
                        </div>

                        <!-- Claude Desktop Card -->
                        <div class="card" style="padding: 18px; display: flex; flex-direction: column; justify-content: space-between; gap: 14px;">
                            <div>
                                <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px;">
                                    <div style="display: flex; align-items: center; gap: 10px;">
                                        <div style="width: 32px; height: 32px; border-radius: 6px; background: var(--bg-hover); display: flex; align-items: center; justify-content: center; color: #d97706;">
                                            <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>
                                        </div>
                                        <div>
                                            <strong style="font-size: 15px; color: var(--dusk-blue);">Claude Desktop MCP</strong>
                                            <div style="font-size: 11px; color: var(--text-muted);">Anthropic Claude App</div>
                                        </div>
                                    </div>
                                    <span class="model-badge" :class="status.claude_desktop?.mcp_connected ? 'badge-mcp' : ''" :style="{ background: status.claude_desktop?.mcp_connected ? '#d4edda' : '', color: status.claude_desktop?.mcp_connected ? '#155724' : '' }">
                                        {{ status.claude_desktop?.mcp_connected ? '● Server Registrato' : '○ Non Registrato' }}
                                    </span>
                                </div>
                                <p style="font-size: 12px; color: var(--text-muted); line-height: 1.5; margin: 0;">
                                    Registra il server MCP di Llampaca nel file <span class="mono">claude_desktop_config.json</span>.
                                </p>
                            </div>
                            <div style="display: flex; gap: 8px; align-items: center; margin-top: 6px;">
                                <button v-if="!status.claude_desktop?.mcp_connected" class="btn btn-secondary" style="flex: 1;" @click="toggleMcp('claude_desktop', 'install')" :disabled="isUpdating">
                                    Abilita Server MCP in Claude
                                </button>
                                <button v-else class="btn btn-danger" style="flex: 1;" @click="toggleMcp('claude_desktop', 'remove')" :disabled="isUpdating">
                                    Rimuovi Server MCP
                                </button>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- SEZIONE 3: Snippet per Altri Client (Cline, Cursor, Python) -->
                <div class="info-block">
                    <div class="section-head" style="margin-bottom: 16px;">
                        <h3 class="section-title">📋 3. Snippet di Configurazione per Altri Client</h3>
                        <p class="section-desc">Snippet pronti da copiare per connettere Continue.dev, Cline, Roo Code, Cursor o script Python all'endpoint locale di Llampaca.</p>
                    </div>

                    <!-- Client Selector Tabs -->
                    <div style="display: flex; gap: 8px; margin-bottom: 14px; flex-wrap: wrap;">
                        <button class="btn" :class="activeSnippetTab === 'continue' ? 'btn-primary' : 'btn-secondary'" @click="activeSnippetTab = 'continue'">
                            Continue.dev (YAML)
                        </button>
                        <button class="btn" :class="activeSnippetTab === 'cline_roo' ? 'btn-primary' : 'btn-secondary'" @click="activeSnippetTab = 'cline_roo'">
                            Cline / Roo Code
                        </button>
                        <button class="btn" :class="activeSnippetTab === 'mcp' ? 'btn-primary' : 'btn-secondary'" @click="activeSnippetTab = 'mcp'">
                            MCP JSON Generic
                        </button>
                        <button class="btn" :class="activeSnippetTab === 'python' ? 'btn-primary' : 'btn-secondary'" @click="activeSnippetTab = 'python'">
                            Python (OpenAI SDK)
                        </button>
                    </div>

                    <!-- Code Snippet Box -->
                    <div class="card" style="padding: 16px; background: #1c2b3a; border-color: #2c3e50; color: #eef2f6;">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; padding-bottom: 8px; border-bottom: 1px solid #2c3e50;">
                            <div style="font-size: 12px; color: var(--amber-glow); font-family: var(--font-mono);">
                                {{ snippetTitles[activeSnippetTab] }}
                            </div>
                            <button class="btn btn-primary btn-sm" @click="copySnippet(snippets[activeSnippetTab])">
                                <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor"><path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/></svg>
                                Copia Configurazione
                            </button>
                        </div>
                        <pre class="code-block" style="margin: 0; background: transparent; padding: 0; color: #a9b7c6; font-size: 12px; line-height: 1.5; overflow-x: auto;"><code>{{ snippets[activeSnippetTab] || 'Caricamento snippet in corso...' }}</code></pre>
                    </div>
                </div>

                <!-- Endpoint Live Info Bar -->
                <div class="info-block" style="background: var(--bg-hover); padding: 14px 18px; border-radius: 8px; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px;">
                    <div style="display: flex; align-items: center; gap: 12px;">
                        <span style="display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: #28a745;"></span>
                        <div style="font-size: 13px; color: var(--dusk-blue);">
                            <strong>Endpoint API Locale:</strong> <span class="mono">{{ snippets.endpoint_url || 'http://127.0.0.1:8080/v1' }}</span>
                        </div>
                    </div>
                    <div style="font-size: 12px; color: var(--text-muted);">
                        Modello Attivo: <span class="mono" style="color: var(--amber-ink); font-weight: 600;">{{ snippets.active_model || '—' }}</span>
                    </div>
                </div>
            </div>
        </div>
    `,
    setup() {
        const status = ref({});
        const snippets = ref({});
        const isLoading = ref(false);
        const isUpdating = ref(false);
        const activeSnippetTab = ref('cline_roo');

        const snippetTitles = {
            continue: '~/.continue/config.yaml (Snippet modello YAML per Continue.dev)',
            cline_roo: 'Settings per Cline / Roo Code (OpenAI Compatible)',
            mcp: 'Configurazione MCP server generica per editor',
            python: 'Codice Python con SDK ufficiale OpenAI'
        };

        const loadData = async () => {
            isLoading.value = true;
            try {
                const [rStatus, rSnippets] = await Promise.all([
                    fetch(`/api/clients/status?_t=${Date.now()}`),
                    fetch(`/api/clients/snippets?_t=${Date.now()}`)
                ]);

                if (rStatus.ok) {
                    status.value = await rStatus.json();
                }
                if (rSnippets.ok) {
                    snippets.value = await rSnippets.json();
                }
            } catch (e) {
                if (window.showToast) window.showToast('Errore durante il caricamento dello stato dei client.', 'danger');
            } finally {
                isLoading.value = false;
            }
        };

        const toggleMcp = async (target, action) => {
            isUpdating.value = true;
            try {
                const res = await fetch('/api/clients/mcp/setup', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ target, action })
                });

                if (!res.ok) {
                    const err = await res.json();
                    throw new Error(err.message || 'Operazione fallita.');
                }

                const label = target === 'vscode' ? 'VS Code' : 'Claude Desktop';
                const actionText = action === 'install' ? 'registrato' : 'rimosso';
                if (window.showToast) {
                    window.showToast(`MCP Server di Llampaca ${actionText} con successo in ${label}!`);
                }
                await loadData();
            } catch (e) {
                if (window.showToast) {
                    window.showToast(`Errore: ${e.message}`, 'danger');
                }
            } finally {
                isUpdating.value = false;
            }
        };

        const copySnippet = (text) => {
            if (!text) return;
            navigator.clipboard.writeText(text).then(() => {
                if (window.showToast) {
                    window.showToast('Configurazione copiata negli appunti!');
                }
            }).catch(() => {
                if (window.showToast) {
                    window.showToast('Impossibile copiare il testo.', 'danger');
                }
            });
        };

        onMounted(loadData);

        return {
            status,
            snippets,
            isLoading,
            isUpdating,
            activeSnippetTab,
            snippetTitles,
            loadData,
            toggleMcp,
            copySnippet
        };
    }
};
